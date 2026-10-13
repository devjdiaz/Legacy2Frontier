"""Conexión RFB (VNC) mínima sobre WebSocket: autenticación VNC, framebuffer Raw, ratón y teclado.

Comportamientos medidos en el escritorio real que este módulo respeta:
- un clic con menos de 100 ms entre bajar y soltar se ignora → mantenemos el botón 150 ms;
- el display llega con 1–5 s de retraso y el frame viejo también es estable → `wait_for_change`
  espera a que la pantalla CAMBIE respecto a antes de la acción y luego a que se estabilice;
- el cursor está dibujado dentro del framebuffer → los cambios solo junto al cursor no cuentan.

Un lector en segundo plano es el único que lee del socket; el resto solo envía. Así un timeout
nunca deja un mensaje a medio leer.
"""
import asyncio
import logging
import struct
import time
from contextlib import asynccontextmanager

import websockets
from Crypto.Cipher import DES
from PIL import Image, ImageChops, ImageDraw

log = logging.getLogger(__name__)

KEYSYMS = {
    "enter": 0xFF0D, "return": 0xFF0D, "tab": 0xFF09, "backspace": 0xFF08, "esc": 0xFF1B,
    "escape": 0xFF1B, "delete": 0xFFFF, "home": 0xFF50, "end": 0xFF57, "left": 0xFF51,
    "up": 0xFF52, "right": 0xFF53, "down": 0xFF54, "pageup": 0xFF55, "pagedown": 0xFF56,
    "ctrl": 0xFFE3, "shift": 0xFFE1, "alt": 0xFFE9, "space": 0x20,
    **{f"f{i}": 0xFFBD + i for i in range(1, 13)},
}
CLICK_HOLD_S = 0.15
CURSOR_BOX = (-12, -12, 24, 28)  # zona que ocupa el cursor alrededor de su punta
CURSOR_MEMORY_S = 8.0  # cuánto tiempo puede tardar el display en mostrar dónde está el cursor


class RFBError(RuntimeError):
    pass


def keysym(ch: str) -> int:
    code = ord(ch)
    return code if code < 0x100 else 0x01000000 + code


class Screen:
    """Una sesión VNC abierta: leer la pantalla y operar ratón y teclado."""

    def __init__(self, ws):
        self._ws = ws
        self._buf = bytearray()
        self.width = 0
        self.height = 0
        self.frame: Image.Image | None = None
        self.version = 0
        self._updated = asyncio.Condition()
        self._reader: asyncio.Task | None = None
        self._error: BaseException | None = None
        self.pointer = (0, 0)
        self._cursor_trail: list[tuple[float, int, int]] = []  # (momento, x, y) recientes

    # ------------------------------------------------------------ protocolo

    async def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            msg = await self._ws.recv()
            self._buf += msg if isinstance(msg, (bytes, bytearray)) else msg.encode()
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    async def handshake(self, password: str) -> None:
        await self._read(12)
        await self._ws.send(b"RFB 003.008\n")
        (n,) = struct.unpack("B", await self._read(1))
        if n == 0:
            (ln,) = struct.unpack(">I", await self._read(4))
            raise RFBError((await self._read(ln)).decode(errors="replace"))
        types = await self._read(n)
        if 2 in types:
            await self._ws.send(b"\x02")
            challenge = await self._read(16)
            key = (password.encode() + b"\0" * 8)[:8]
            key = bytes(int(f"{b:08b}"[::-1], 2) for b in key)  # VNC invierte los bits de cada byte
            await self._ws.send(DES.new(key, DES.MODE_ECB).encrypt(challenge))
        elif 1 in types:
            await self._ws.send(b"\x01")
        else:
            raise RFBError(f"tipos de seguridad no soportados: {list(types)}")
        (result,) = struct.unpack(">I", await self._read(4))
        if result != 0:
            raise RFBError("autenticación VNC rechazada")
        await self._ws.send(b"\x01")  # sesión compartida
        self.width, self.height = struct.unpack(">HH", await self._read(4))
        await self._read(16)
        (ln,) = struct.unpack(">I", await self._read(4))
        await self._read(ln)
        pixel_format = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        await self._ws.send(b"\x00\x00\x00\x00" + pixel_format)
        await self._ws.send(struct.pack(">BxHi", 2, 1, 0))  # solo encoding Raw
        self.frame = Image.new("RGB", (self.width, self.height))
        self._reader = asyncio.create_task(self._read_loop())
        before = await self.capture()
        # No sabemos dónde quedó el cursor de la sesión anterior: lo estacionamos en una esquina
        # para que después sus movimientos se puedan ignorar al detectar cambios.
        park = (self.width - 1, self.height - 1)
        await self._pointer(*park, 0)
        await self.wait_for_change(before, timeout=4.0)

    async def _read_loop(self) -> None:
        try:
            while True:
                (msg_type,) = struct.unpack("B", await self._read(1))
                if msg_type == 0:
                    _, rects = struct.unpack(">BH", await self._read(3))
                    for _ in range(rects):
                        x, y, w, h, enc = struct.unpack(">HHHHi", await self._read(12))
                        if enc != 0:
                            raise RFBError(f"encoding inesperado {enc}")
                        raw = await self._read(w * h * 4)
                        self.frame.paste(Image.frombuffer("RGB", (w, h), raw, "raw", "BGRX"), (x, y))
                    async with self._updated:
                        self.version += 1
                        self._updated.notify_all()
                elif msg_type == 1:  # mapa de colores: no aplica en true color
                    _, _, n = struct.unpack(">BHH", await self._read(5))
                    await self._read(n * 6)
                elif msg_type == 3:  # portapapeles del servidor
                    await self._read(3)
                    (ln,) = struct.unpack(">I", await self._read(4))
                    await self._read(ln)
                # msg_type == 2 (bell): sin payload
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._error = exc
            async with self._updated:
                self._updated.notify_all()

    async def _request(self, incremental: bool) -> None:
        await self._ws.send(struct.pack(">BBHHHH", 3, int(incremental), 0, 0, self.width, self.height))

    async def _wait_update(self, after_version: int, timeout: float) -> bool:
        try:
            async with self._updated:
                await asyncio.wait_for(
                    self._updated.wait_for(lambda: self.version > after_version or self._error is not None),
                    timeout,
                )
        except TimeoutError:
            return False
        if self._error is not None:
            raise RFBError(f"la conexión VNC se cayó: {type(self._error).__name__}") from self._error
        return True

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()

    # ------------------------------------------------------------ pantalla

    async def capture(self, timeout: float = 10.0) -> Image.Image:
        """Pide la pantalla completa y devuelve una copia."""
        version = self.version
        await self._request(incremental=False)
        if not await self._wait_update(version, timeout):
            raise RFBError("el escritorio no envió la pantalla a tiempo")
        return self.frame.copy()

    def differs(self, a: Image.Image, b: Image.Image) -> bool:
        """¿Cambió algo además del cursor?"""
        diff = ImageChops.difference(a, b)
        if not diff.getbbox():
            return False
        # El display llega con retraso: el cursor puede verse todavía en posiciones de hace segundos.
        draw = ImageDraw.Draw(diff)
        for _, x, y in self._cursor_trail:
            draw.rectangle([x + CURSOR_BOX[0], y + CURSOR_BOX[1], x + CURSOR_BOX[2], y + CURSOR_BOX[3]], fill=(0, 0, 0))
        return diff.getbbox() is not None

    async def wait_for_change(self, before: Image.Image, timeout: float = 6.0, settle: float = 0.4) -> tuple[bool, Image.Image]:
        """Espera a que la pantalla cambie respecto a `before` y luego a que se estabilice.

        Devuelve (cambió, frame final). Pide actualizaciones incrementales: solo viaja lo que cambió.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        changed = False
        prev = before
        stable_since = 0.0
        while loop.time() < deadline:
            version = self.version
            await self._request(incremental=True)
            await self._wait_update(version, 0.3)
            current = self.frame.copy()
            if not changed:
                if self.differs(current, before):
                    changed, prev, stable_since = True, current, loop.time()
            elif self.differs(current, prev):
                prev, stable_since = current, loop.time()
            elif loop.time() - stable_since >= settle:
                return True, current
            if not changed:
                await asyncio.sleep(0.15)
        return changed, self.frame.copy()

    # ------------------------------------------------------------ ratón y teclado

    async def _pointer(self, x: int, y: int, mask: int) -> None:
        now = asyncio.get_running_loop().time()
        if (x, y) != self.pointer or not self._cursor_trail:
            self._cursor_trail = [p for p in self._cursor_trail if now - p[0] < CURSOR_MEMORY_S]
            self._cursor_trail.append((now, *self.pointer))
            self._cursor_trail.append((now, x, y))
            self.pointer = (x, y)
        await self._ws.send(struct.pack(">BBHH", 5, mask, x, y))

    async def move(self, x: int, y: int) -> None:
        await self._pointer(x, y, 0)

    async def click(self, x: int, y: int, double: bool = False, button: int = 1) -> None:
        await self._pointer(x, y, 0)
        await asyncio.sleep(0.08)
        for _ in range(2 if double else 1):
            await self._pointer(x, y, button)
            await asyncio.sleep(CLICK_HOLD_S)
            await self._pointer(x, y, 0)
            await asyncio.sleep(0.1)

    async def scroll(self, x: int, y: int, clicks: int) -> None:
        button = 8 if clicks < 0 else 16
        for _ in range(abs(clicks)):
            await self._pointer(x, y, button)
            await self._pointer(x, y, 0)
            await asyncio.sleep(0.05)

    async def _key(self, sym: int, down: bool) -> None:
        await self._ws.send(struct.pack(">BBxxI", 4, int(down), sym))

    async def press(self, combo: str) -> None:
        """Teclas o combinaciones: 'enter', 'ctrl+a', 'shift+tab'."""
        syms = [KEYSYMS.get(k.lower()) or keysym(k) for k in combo.split("+")]
        for sym in syms:
            await self._key(sym, True)
            await asyncio.sleep(0.03)
        for sym in reversed(syms):
            await self._key(sym, False)
            await asyncio.sleep(0.03)

    async def type_text(self, text: str) -> None:
        for ch in text:
            sym = keysym(ch)
            await self._key(sym, True)
            await asyncio.sleep(0.02)
            await self._key(sym, False)
            await asyncio.sleep(0.02)


@asynccontextmanager
async def connect(url: str, password: str, timeout: float = 20.0, retry_s: float = 120.0):
    """Abre la sesión VNC y la cierra siempre al salir (también si la tarea se cancela).

    Un escritorio que todavía no está listo acepta el WebSocket y lo cierra sin saludar: se reintenta
    hasta `retry_s`. Una contraseña rechazada (RFBError) no se reintenta."""
    deadline = time.monotonic() + retry_s
    attempt = 0
    while True:
        ws = screen = None
        try:
            ws = await asyncio.wait_for(
                websockets.connect(url, subprotocols=["binary"], max_size=None, open_timeout=timeout, close_timeout=2),
                timeout,
            )
            screen = Screen(ws)
            await asyncio.wait_for(screen.handshake(password), timeout)
            break
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            if screen:
                await screen.close()
            if ws:
                await ws.close()
            attempt += 1
            pause = min(2.0 * attempt, 5.0)
            if time.monotonic() + pause >= deadline:
                raise
            log.warning("el escritorio no respondió (%s); reintento %d", type(exc).__name__, attempt)
            await asyncio.sleep(pause)
        except BaseException:
            if screen:
                await screen.close()
            if ws:
                await ws.close()
            raise
    try:
        yield screen
    finally:
        await screen.close()
        await ws.close()

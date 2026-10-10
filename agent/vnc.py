"""Conexión RFB (VNC) mínima sobre WebSocket: autenticación VNC, framebuffer Raw, ratón y teclado.

Comportamientos medidos en el escritorio real que este módulo respeta:
- un clic con menos de 100 ms entre bajar y soltar se ignora → mantenemos el botón 150 ms;
- el display llega con 1–5 s de retraso → `wait_for_change` espera a que el frame CAMBIE.
"""
import asyncio
import struct
from contextlib import asynccontextmanager

import websockets
from Crypto.Cipher import DES
from PIL import Image, ImageChops

KEYSYMS = {
    "enter": 0xFF0D, "return": 0xFF0D, "tab": 0xFF09, "backspace": 0xFF08, "esc": 0xFF1B,
    "escape": 0xFF1B, "delete": 0xFFFF, "home": 0xFF50, "end": 0xFF57, "left": 0xFF51,
    "up": 0xFF52, "right": 0xFF53, "down": 0xFF54, "pageup": 0xFF55, "pagedown": 0xFF56,
    "ctrl": 0xFFE3, "shift": 0xFFE1, "alt": 0xFFE9, "space": 0x20,
    **{f"f{i}": 0xFFBD + i for i in range(1, 13)},
}
CLICK_HOLD_S = 0.15


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

    async def capture(self) -> Image.Image:
        """Pide la pantalla completa y devuelve una copia."""
        await self._ws.send(struct.pack(">BBHHHH", 3, 0, 0, 0, self.width, self.height))
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
                return self.frame.copy()
            if msg_type == 1:  # mapa de colores: no aplica en true color
                _, _, n = struct.unpack(">BHH", await self._read(5))
                await self._read(n * 6)
            elif msg_type == 3:  # portapapeles del servidor
                await self._read(3)
                (ln,) = struct.unpack(">I", await self._read(4))
                await self._read(ln)
            # msg_type == 2 (bell): sin payload

    async def wait_for_change(self, before: Image.Image, timeout: float = 6.0, poll: float = 0.25) -> tuple[bool, Image.Image]:
        """Espera a que la pantalla cambie respecto a `before` y luego a que se estabilice.

        Esperar solo estabilidad no sirve: el frame viejo también es estable mientras llega el nuevo.
        Devuelve (cambió, frame final).
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        current = before
        changed = False
        while loop.time() < deadline:
            await asyncio.sleep(poll)
            frame = await self.capture()
            if not changed:
                if ImageChops.difference(frame, before).getbbox():
                    changed = True
            elif not ImageChops.difference(frame, current).getbbox():
                return True, frame
            current = frame
        return changed, current

    async def _pointer(self, x: int, y: int, mask: int) -> None:
        await self._ws.send(struct.pack(">BBHH", 5, mask, x, y))

    async def click(self, x: int, y: int, double: bool = False, button: int = 1) -> None:
        await self._pointer(x, y, 0)
        await asyncio.sleep(0.05)
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
async def connect(url: str, password: str, timeout: float = 20.0):
    """Abre la sesión VNC y la cierra siempre al salir (también si la tarea se cancela)."""
    ws = await asyncio.wait_for(
        websockets.connect(url, subprotocols=["binary"], max_size=None, open_timeout=timeout),
        timeout,
    )
    try:
        screen = Screen(ws)
        await asyncio.wait_for(screen.handshake(password), timeout)
        yield screen
    finally:
        await ws.close()

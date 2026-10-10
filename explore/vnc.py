"""Cliente RFB mínimo sobre WebSocket (websockify) para explorar el escritorio a mano.

Uso:
  python explore/vnc.py shot out.png
  python explore/vnc.py run "click 100 200" "type hola" "key ctrl+a" "wait 1.2" "shot out.png"
"""
import asyncio
import os
import ssl
import struct
import sys

import websockets
from Crypto.Cipher import DES
from dotenv import load_dotenv
from PIL import Image, ImageChops

KEYSYMS = {
    "enter": 0xFF0D, "return": 0xFF0D, "tab": 0xFF09, "backspace": 0xFF08, "esc": 0xFF1B,
    "escape": 0xFF1B, "delete": 0xFFFF, "home": 0xFF50, "end": 0xFF57, "left": 0xFF51,
    "up": 0xFF52, "right": 0xFF53, "down": 0xFF54, "pageup": 0xFF55, "pagedown": 0xFF56,
    "ctrl": 0xFFE3, "shift": 0xFFE1, "alt": 0xFFE9, "space": 0x20,
}


def keysym(ch: str) -> int:
    o = ord(ch)
    return o if o < 0x100 else 0x01000000 + o


class RFB:
    def __init__(self, ws):
        self.ws = ws
        self.buf = b""
        self.w = self.h = 0
        self.fb = None

    async def read(self, n: int) -> bytes:
        while len(self.buf) < n:
            msg = await self.ws.recv()
            self.buf += msg if isinstance(msg, bytes) else msg.encode()
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    async def send(self, data: bytes):
        await self.ws.send(data)

    async def handshake(self, password: str):
        await self.read(12)
        await self.send(b"RFB 003.008\n")
        (n,) = struct.unpack("B", await self.read(1))
        if n == 0:
            (ln,) = struct.unpack(">I", await self.read(4))
            raise RuntimeError((await self.read(ln)).decode())
        types = await self.read(n)
        if 2 in types:
            await self.send(b"\x02")
            challenge = await self.read(16)
            key = (password.encode() + b"\0" * 8)[:8]
            key = bytes(int(f"{b:08b}"[::-1], 2) for b in key)
            await self.send(DES.new(key, DES.MODE_ECB).encrypt(challenge))
        else:
            await self.send(b"\x01")
        (res,) = struct.unpack(">I", await self.read(4))
        if res != 0:
            raise RuntimeError("auth VNC falló")
        await self.send(b"\x01")  # shared
        self.w, self.h = struct.unpack(">HH", await self.read(4))
        await self.read(16)
        (ln,) = struct.unpack(">I", await self.read(4))
        await self.read(ln)
        pf = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        await self.send(b"\x00\x00\x00\x00" + pf)
        await self.send(struct.pack(">BxHi", 2, 1, 0))  # solo Raw
        self.fb = Image.new("RGB", (self.w, self.h))

    async def update(self, incremental=False):
        await self.send(struct.pack(">BBHHHH", 3, int(incremental), 0, 0, self.w, self.h))
        while True:
            (t,) = struct.unpack("B", await self.read(1))
            if t == 0:
                _, nrects = struct.unpack(">BH", await self.read(3))
                for _ in range(nrects):
                    x, y, w, h, enc = struct.unpack(">HHHHi", await self.read(12))
                    if enc != 0:
                        raise RuntimeError(f"encoding inesperado {enc}")
                    raw = await self.read(w * h * 4)
                    self.fb.paste(Image.frombuffer("RGB", (w, h), raw, "raw", "BGRX"), (x, y))
                return self.fb
            elif t == 2:
                continue
            elif t == 3:
                await self.read(3)
                (ln,) = struct.unpack(">I", await self.read(4))
                await self.read(ln)
            elif t == 1:
                _, _, n = struct.unpack(">BHH", await self.read(5))
                await self.read(n * 6)

    async def pointer(self, x, y, mask):
        await self.send(struct.pack(">BBHH", 5, mask, x, y))

    async def click(self, x, y, button=1, double=False):
        await self.pointer(x, y, 0)
        for _ in range(2 if double else 1):
            await self.pointer(x, y, button)
            await asyncio.sleep(0.15)  # <100 ms se ignora
            await self.pointer(x, y, 0)
            await asyncio.sleep(0.12)

    async def key(self, sym, down):
        await self.send(struct.pack(">BBxxI", 4, int(down), sym))

    async def combo(self, spec: str):
        syms = [KEYSYMS.get(k.lower(), None) or keysym(k) for k in spec.split("+")]
        for s in syms:
            await self.key(s, True)
            await asyncio.sleep(0.03)
        for s in reversed(syms):
            await self.key(s, False)
            await asyncio.sleep(0.03)

    async def type(self, text: str):
        for ch in text:
            s = keysym(ch)
            await self.key(s, True)
            await asyncio.sleep(0.02)
            await self.key(s, False)
            await asyncio.sleep(0.02)


async def main(cmds):
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    url = os.environ["DESKTOP_URL"].rstrip("/").replace("https://", "wss://") + "/websockify"
    ctx = ssl.create_default_context()
    async with websockets.connect(url, subprotocols=["binary"], ssl=ctx, max_size=None) as ws:
        r = RFB(ws)
        await r.handshake(os.environ["DESKTOP_PASSWORD"])
        await r.update()
        for c in cmds:
            op, *args = c.split(" ", 1)
            arg = args[0] if args else ""
            if op == "shot":
                await asyncio.sleep(0.2)
                (await r.update()).save(arg or "shot.png")
                print("saved", arg)
            elif op in ("click", "dclick", "rclick"):
                x, y = map(int, arg.split())
                await r.click(x, y, button=4 if op == "rclick" else 1, double=op == "dclick")
            elif op == "settle":
                # espera a que el frame cambie vs. el de antes (máx 3 s) y luego a que se estabilice
                before = r.fb.copy()
                loop = asyncio.get_running_loop()
                t0 = loop.time()
                changed = False
                while loop.time() - t0 < float(arg or 3):
                    await asyncio.sleep(0.25)
                    cur = (await r.update()).copy()
                    if not changed and ImageChops.difference(cur, before).getbbox():
                        changed, prev = True, cur
                    elif changed:
                        if not ImageChops.difference(cur, prev).getbbox():
                            break
                        prev = cur
                print(f"settle: changed={changed} {loop.time() - t0:.2f}s")
            elif op == "move":
                x, y = map(int, arg.split())
                await r.pointer(x, y, 0)
            elif op == "type":
                await r.type(arg)
            elif op == "key":
                await r.combo(arg)
            elif op == "wait":
                await asyncio.sleep(float(arg))
            elif op == "scroll":
                x, y, n = map(int, arg.split())
                btn = 8 if n < 0 else 16
                for _ in range(abs(n)):
                    await r.pointer(x, y, btn)
                    await r.pointer(x, y, 0)
            else:
                raise SystemExit(f"comando desconocido: {op}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "shot":
        args = [f"shot {args[1] if len(args) > 1 else 'shot.png'}"]
    elif args and args[0] == "run":
        args = args[1:]
    asyncio.run(main(args))

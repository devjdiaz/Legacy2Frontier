"""Actuador: acciones sobre el escritorio que verifican su efecto (D3, D4).

- Tras cada acción se espera a que la pantalla cambie (no una pausa fija).
- Escribir en un campo REEMPLAZA su contenido y se verifica leyendo el campo: el escritorio concatena
  si no se selecciona antes, y a veces pierde teclas.
- Las acciones con efecto nunca se reintentan a ciegas: si no se vio cambio, se confirma con una
  captura completa tras una pausa y se informa; la política decide si se puede repetir.
"""
import asyncio
import difflib
from dataclasses import dataclass

from PIL import Image

from .perception import Observation, TextBox, group_lines, norm, observe, read_region
from .vnc import Screen


@dataclass
class Outcome:
    summary: str
    changed: bool
    frame: Image.Image


def _same_text(seen: str, wanted: str) -> bool:
    a, b = norm(seen).replace(" ", ""), norm(wanted).replace(" ", "")
    if a == b:
        return True
    # tolera ruido del OCR, pero no texto de más (concatenación) ni de menos
    return abs(len(a) - len(b)) <= 1 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


def _field_text(boxes: list[TextBox], x: int, y: int) -> str:
    """El texto del campo en (x, y): las cajas de la misma línea más cercanas al punto."""
    line = [b for b in boxes if abs(b.cy - y) <= 12]
    if not line:
        return ""
    near = [b for b in line if b.x0 - 20 <= x <= b.x1 + 20]
    if not near:
        near = [min(line, key=lambda b: min(abs(b.x0 - x), abs(b.x1 - x)))]
    # une cajas contiguas al elegido (el OCR puede partir el contenido de un campo)
    near.sort(key=lambda b: b.x0)
    first = near[0]
    group = [first]
    for b in sorted(line, key=lambda b: b.x0):
        if b.x0 > group[-1].x1 and b.x0 - group[-1].x1 <= 14 and b is not first:
            group.append(b)
    return " ".join(b.text for b in group)


class Actuator:
    def __init__(self, screen: Screen):
        self.screen = screen

    async def click(self, x: int, y: int, double: bool = False, effectful: bool = False, timeout: float | None = None) -> Outcome:
        before = self.screen.frame.copy()
        await self.screen.click(x, y, double=double)
        changed, frame = await self.screen.wait_for_change(before, timeout=timeout or (9.0 if effectful else 6.0))
        if not changed and effectful:
            await asyncio.sleep(2.0)
            frame = await self.screen.capture()
            changed = self.screen.differs(frame, before)
        summary = "la pantalla cambió" if changed else "NO se vio ningún cambio (si era una búsqueda, el resultado puede ser el mismo)"
        return Outcome(summary, changed, frame)

    async def _park_pointer(self) -> None:
        """Aparta el mouse: sobre un campo se dibuja como una «I» que el OCR lee como letra."""
        await self.screen.move(self.screen.width - 1, self.screen.height - 1)

    async def _verify_text(self, x: int, y: int, wanted: str, timeout: float = 5.0) -> tuple[bool, str, Image.Image]:
        """¿Se ve `wanted` en el campo junto a (x, y)? El modelo a veces apunta a la etiqueta y no al campo,
        así que se buscan las líneas alrededor del punto. Reintenta leyendo hasta `timeout` (display lento)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        frame = self.screen.frame.copy()
        closest = ""
        while True:
            await self.screen.wait_for_change(frame, timeout=0.8, settle=0.3)
            frame = self.screen.frame.copy()
            boxes = await read_region(frame, (x - 450, y - 30, x + 650, y + 60))
            lines = group_lines(boxes)
            for line in lines:
                for i in range(len(line)):
                    acc = ""
                    for j in range(i, len(line)):
                        acc = f"{acc} {line[j].text}".strip()
                        if _same_text(acc, wanted):
                            return True, acc, frame
            if lines:
                below = [l for l in lines if l[0].cy >= y - 12] or lines
                near = min(below, key=lambda l: abs(l[0].cy - y))
                closest = _field_text(near, x, near[0].cy)
            if loop.time() >= deadline:
                return False, closest, frame

    async def type_into(self, x: int, y: int, text: str) -> Outcome:
        seen = ""
        frame = self.screen.frame.copy()
        for attempt in range(2):
            before = self.screen.frame.copy()
            await self.screen.click(x, y)
            await self.screen.wait_for_change(before, timeout=2.0)
            await self._park_pointer()
            await self.screen.press("ctrl+a")  # escribir sobre una selección la reemplaza
            if attempt > 0 or not text:
                await self.screen.press("delete")
            if text:
                await self.screen.type_text(text)
                ok, seen, frame = await self._verify_text(x, y, text)
                if ok:
                    return Outcome(f"escrito y verificado: «{seen}»", True, frame)
            else:
                _, frame = await self.screen.wait_for_change(before, timeout=3.0)
                return Outcome("campo vaciado", True, frame)
        return Outcome(f"NO se pudo verificar lo escrito (cerca del punto se lee «{seen}»; se esperaba «{text}»). "
                       "Revisa la captura: si el texto está bien en el campo, continúa; no lo vuelvas a escribir a ciegas.", True, frame)

    async def select(self, x: int, y: int, option: str) -> Outcome:
        before = self.screen.frame.copy()
        await self.screen.click(x, y)
        _, opened = await self.screen.wait_for_change(before, timeout=5.0)
        obs = await observe(opened)
        wanted = norm(option).lower()
        matches = [b for b in obs.boxes if norm(b.text).lower() == wanted and abs(b.cx - x) < 400 and b.cy != y]
        if matches:
            target = min(matches, key=lambda b: abs(b.cy - y))
            before = self.screen.frame.copy()
            await self.screen.click(target.cx, target.cy)
            _, frame = await self.screen.wait_for_change(before, timeout=5.0)
        else:  # lista larga o no visible: escribir la opción sobre el control enfocado
            await self.screen.press("escape")
            await self.screen.click(x, y)
            await self.screen.type_text(option)
            await self.screen.press("enter")
            _, frame = await self.screen.wait_for_change(before, timeout=5.0)
        await self._park_pointer()
        ok, seen, frame = await self._verify_text(x, y, option, timeout=4.0)
        if ok:
            return Outcome(f"seleccionado y verificado: «{seen}»", True, frame)
        return Outcome(f"NO se pudo verificar la selección: el control muestra «{seen}»", True, frame)

    async def press(self, combo: str) -> Outcome:
        before = self.screen.frame.copy()
        await self.screen.press(combo)
        changed, frame = await self.screen.wait_for_change(before, timeout=5.0)
        return Outcome("la pantalla cambió" if changed else "NO se vio ningún cambio", changed, frame)

    async def scroll(self, x: int, y: int, amount: int) -> Outcome:
        before = self.screen.frame.copy()
        await self.screen.scroll(x, y, amount)
        changed, frame = await self.screen.wait_for_change(before, timeout=4.0)
        return Outcome("la pantalla cambió" if changed else "NO se vio ningún cambio", changed, frame)

    async def wait(self, seconds: float) -> Outcome:
        before = self.screen.frame.copy()
        await asyncio.sleep(max(0.5, min(seconds, 10.0)))
        frame = await self.screen.capture()
        changed = self.screen.differs(frame, before)
        return Outcome("la pantalla cambió" if changed else "sin cambios", changed, frame)


def label_at(obs: Observation, x: int, y: int) -> str | None:
    box = obs.text_at(x, y)
    return box.text if box else None

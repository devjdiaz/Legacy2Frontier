"""Percepción: convierte la pantalla en texto con coordenadas (OCR) y reconoce tablas genéricas.

El modelo de lenguaje ve la imagen, pero lo exacto (leer, contar, comparar) se hace sobre este texto
en código. RapidOCR se eligió midiendo: 34/34 textos reales con tildes vs 23/34 de Tesseract.
"""
import asyncio
import hashlib
import statistics
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from PIL import Image


@dataclass
class TextBox:
    id: int
    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    score: float = 1.0

    @property
    def cx(self) -> int:
        return (self.x0 + self.x1) // 2

    @property
    def cy(self) -> int:
        return (self.y0 + self.y1) // 2

    @property
    def h(self) -> int:
        return self.y1 - self.y0


@dataclass
class Observation:
    frame: Image.Image
    boxes: list[TextBox]
    digest: str
    lines: list[list[TextBox]] = field(default_factory=list)

    def box(self, box_id: int) -> TextBox | None:
        return next((b for b in self.boxes if b.id == box_id), None)

    def text_at(self, x: int, y: int, margin: int = 6) -> TextBox | None:
        """El texto bajo (o más cercano a) un punto: sirve para saber qué botón se va a pulsar."""
        inside = [b for b in self.boxes if b.x0 - margin <= x <= b.x1 + margin and b.y0 - margin <= y <= b.y1 + margin]
        return min(inside, key=lambda b: abs(b.cx - x) + abs(b.cy - y)) if inside else None

    def find(self, text: str) -> list[TextBox]:
        target = norm(text).lower()
        return [b for b in self.boxes if target in norm(b.text).lower()]

    def as_text(self) -> str:
        """Pantalla como texto, línea por línea, con id y centro de cada elemento."""
        out = []
        for line in self.lines:
            out.append("  ".join(f"[{b.id}]{b.text}@{b.cx},{b.cy}" for b in line))
        return "\n".join(out)

    def plain_text(self) -> str:
        return "\n".join(" ".join(b.text for b in line) for line in self.lines)


def norm(s: str) -> str:
    return unicodedata.normalize("NFC", " ".join(s.split()))


@lru_cache(maxsize=1)
def _engine():
    from rapidocr import RapidOCR

    return RapidOCR(params={"Global.log_level": "warning"})


def _ocr_sync(frame: Image.Image) -> list[TextBox]:
    res = _engine()(np.array(frame.convert("RGB")))
    boxes = []
    if res.boxes is None:
        return boxes
    for i, (pts, txt, score) in enumerate(zip(res.boxes, res.txts, res.scores)):
        xs = [int(p[0]) for p in pts]
        ys = [int(p[1]) for p in pts]
        boxes.append(TextBox(i, norm(txt), min(xs), min(ys), max(xs), max(ys), float(score)))
    return boxes


_cache: dict[str, list[TextBox]] = {}


async def observe(frame: Image.Image, prev: "Observation | None" = None) -> Observation:
    """OCR de la pantalla (en un hilo para no bloquear el servicio), con caché por contenido.

    Si hay una observación previa y el cambio es local (un menú, un campo), solo se relee la zona que
    cambió: la mayoría de los pasos pasan de ~1.8 s de OCR a décimas.
    """
    digest = hashlib.blake2b(frame.tobytes(), digest_size=16).hexdigest()
    if digest not in _cache:
        if len(_cache) > 64:
            _cache.clear()
        boxes = await _incremental(frame, prev) if prev is not None else None
        _cache[digest] = boxes if boxes is not None else await asyncio.to_thread(_ocr_sync, frame)
    boxes = [TextBox(**vars(b)) for b in _cache[digest]]
    lines = group_lines(boxes)
    # ids en orden de lectura: arriba→abajo, izquierda→derecha
    for new_id, b in enumerate(b for line in lines for b in line):
        b.id = new_id
    return Observation(frame=frame, boxes=boxes, digest=digest, lines=lines)


async def _incremental(frame: Image.Image, prev: "Observation") -> list[TextBox] | None:
    from PIL import ImageChops

    bbox = ImageChops.difference(frame, prev.frame).getbbox()
    if bbox is None:
        return [TextBox(**vars(b)) for b in prev.boxes]
    x0, y0, x1, y1 = bbox[0] - 16, bbox[1] - 16, bbox[2] + 16, bbox[3] + 16
    grown = True
    while grown:  # la zona incluye completas las cajas de texto que toca
        grown = False
        for b in prev.boxes:
            if b.x1 >= x0 and b.x0 <= x1 and b.y1 >= y0 and b.y0 <= y1:
                nx0, ny0, nx1, ny1 = min(x0, b.x0 - 4), min(y0, b.y0 - 4), max(x1, b.x1 + 4), max(y1, b.y1 + 4)
                if (nx0, ny0, nx1, ny1) != (x0, y0, x1, y1):
                    x0, y0, x1, y1, grown = nx0, ny0, nx1, ny1, True
    area = max(0, x1 - x0) * max(0, y1 - y0)
    if area > 0.45 * frame.width * frame.height:
        return None  # cambió media pantalla o más: lectura completa
    kept = [TextBox(**vars(b)) for b in prev.boxes if not (b.x1 >= x0 and b.x0 <= x1 and b.y1 >= y0 and b.y0 <= y1)]
    return kept + await read_region(frame, (x0, y0, x1, y1))


def group_lines(boxes: list[TextBox]) -> list[list[TextBox]]:
    if not boxes:
        return []
    tol = max(4, statistics.median(b.h for b in boxes) * 0.6)
    lines: list[list[TextBox]] = []
    for b in sorted(boxes, key=lambda b: b.cy):
        if lines and abs(statistics.mean(x.cy for x in lines[-1]) - b.cy) <= tol:
            lines[-1].append(b)
        else:
            lines.append([b])
    return [sorted(line, key=lambda b: b.x0) for line in lines]


# ---------------------------------------------------------------- tablas


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]

    def as_dicts(self) -> list[dict[str, str]]:
        return [dict(zip(self.headers, r)) for r in self.rows]


def parse_table(obs: Observation, min_cols: int = 3) -> Table | None:
    """Reconoce la tabla principal de la pantalla sin saber nada de la app.

    Heurística genérica: el bloque más largo de líneas consecutivas con ≥ `min_cols` celdas cuyas
    columnas se alinean con las de la primera línea (el encabezado).
    """
    lines = obs.lines
    best: tuple[int, int] | None = None  # (inicio, fin) del bloque
    i = 0
    while i < len(lines):
        if len(lines[i]) < min_cols:
            i += 1
            continue
        starts = [b.x0 for b in lines[i]]
        j = i + 1
        while j < len(lines) and len(lines[j]) >= 2 and _aligned(lines[j], starts):
            j += 1
        if j - i >= 2 and (best is None or j - i > best[1] - best[0]):
            best = (i, j)
        i = j if j > i + 1 else i + 1
    if best is None:
        return None
    header_line = lines[best[0]]
    starts = [b.x0 for b in header_line]
    headers = [b.text for b in header_line]
    rows = []
    for line in lines[best[0] + 1 : best[1]]:
        cells = [""] * len(starts)
        for b in line:
            col = _column(b.x0, starts)
            cells[col] = f"{cells[col]} {b.text}".strip()
        # una fila de datos llena al menos la mitad de las columnas (descarta paginadores y totales)
        if sum(1 for c in cells if c) >= max(2, (len(starts) + 1) // 2):
            rows.append(cells)
    return Table(headers=headers, rows=rows)


def _column(x: int, starts: list[int], tol: int = 12) -> int:
    col = 0
    for k, s in enumerate(starts):
        if x >= s - tol:
            col = k
    return col


def _aligned(line: list[TextBox], starts: list[int], tol: int = 12) -> bool:
    hits = sum(1 for b in line if any(abs(b.x0 - s) <= tol for s in starts))
    return hits >= max(2, len(line) * 0.6)


async def read_region(frame: Image.Image, box: tuple[int, int, int, int]) -> list[TextBox]:
    """OCR de una región (rápido): para verificar lo escrito en un campo sin leer toda la pantalla."""
    x0, y0, x1, y1 = (max(0, box[0]), max(0, box[1]), min(frame.width, box[2]), min(frame.height, box[3]))
    crop = frame.crop((x0, y0, x1, y1))
    found = await asyncio.to_thread(_ocr_sync, crop)
    for b in found:
        b.x0, b.x1, b.y0, b.y1 = b.x0 + x0, b.x1 + x0, b.y0 + y0, b.y1 + y0
    return found

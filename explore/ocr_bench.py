"""¿Qué OCR lee bien la pantalla real (tildes incluidas)? Compara motores sobre capturas reales."""
import os
import subprocess
import sys
import tempfile
import time
import unicodedata

from PIL import Image

HERE = os.path.dirname(__file__)
SHOTS = os.path.join(HERE, "shots")

# Textos que se ven en las capturas (verificados a ojo). Las tildes importan: la app distingue.
EXPECTED = {
    "p-clientes.png": [
        "Abarrotes Ramírez", "Abarrotes Ramirez y Cía.", "Bogotá", "Almacén Díaz", "Almacén Cárdenas Ltda.",
        "Barranquilla", "Medellín", "Abarrotes Suárez Express", "Razón social o nombre", "Teléfono",
        "Distingue tildes: Ramírez no trae Ramirez.", "CL-0065", "825016886", "3356668816", "Nuevo cliente",
        "Página 1", "Siguiente", "Inactivo",
    ],
    "e3-p2.png": [
        "Ferretería Ruiz e Hijos", "FV-10047", "FV-10066", "19/06/2026", "3.480.000", "Vencida", "Anterior", "Página 2",
    ],
    "e2-faro.png": [
        "NOTA PARA EL ASISTENTE", "Distribuciones El Faro", "Carrera 15 # 85-40", "Observaciones", "Guardar", "Volver",
        "Razón social o nombre", "Teléfono",
    ],
}


def norm(s: str) -> str:
    return unicodedata.normalize("NFC", " ".join(s.split()))


def tesseract(path: str, scale: int = 2) -> tuple[str, float]:
    img = Image.open(path).convert("L")
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        img.save(tmp.name)
        t = time.time()
        out = subprocess.run(
            ["tesseract", tmp.name, "stdout", "-l", "spa+eng", "--psm", "11"],
            capture_output=True, text=True, env={**os.environ, "TESSDATA_PREFIX": os.path.join(HERE, "..", ".tessdata")},
        ).stdout
        return out, time.time() - t


_rapid = None


def rapid(path: str) -> tuple[str, float]:
    global _rapid
    from rapidocr import RapidOCR
    if _rapid is None:
        _rapid = RapidOCR()
    t = time.time()
    res = _rapid(path)
    return "\n".join(res.txts or []), time.time() - t


def score(text: str, expected: list[str]) -> tuple[int, list[str]]:
    flat = norm(text.replace("\n", " "))
    misses = [e for e in expected if norm(e) not in flat]
    return len(expected) - len(misses), misses


engines = {"tesseract-x2": tesseract, "rapidocr": rapid}
only = sys.argv[1:] or list(engines)
for name in only:
    total = ok = 0
    times = []
    all_miss = []
    for shot, exp in EXPECTED.items():
        text, dt = engines[name](os.path.join(SHOTS, shot))
        good, misses = score(text, exp)
        ok += good
        total += len(exp)
        times.append(dt)
        all_miss += [f"{shot}: {m}" for m in misses]
    print(f"{name:14s} {ok}/{total}  tiempo prom {sum(times)/len(times):.2f}s")
    for m in all_miss:
        print("    falta:", m)

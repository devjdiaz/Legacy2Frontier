"""Observa /traces del servicio desplegado y guarda cada tarea terminada (para analizar el set oculto)."""
import json
import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
URL = sys.argv[1] if len(sys.argv) > 1 else "https://agent-production-7bcb.up.railway.app"
OUT = sys.argv[2] if len(sys.argv) > 2 else "runs/oculto-1.jsonl"
seen = set()
if os.path.exists(OUT):
    seen = {json.loads(l)["traza"] for l in open(OUT, encoding="utf-8")}
idle_since = time.time()
while time.time() - idle_since < 1200:  # termina tras 20 min sin tareas nuevas
    try:
        items = httpx.get(f"{URL}/traces?limit=8", timeout=30,
                          headers={"Authorization": f"Bearer {os.environ['AGENT_TOKEN']}"}).json()
    except Exception as exc:
        print("error consultando /traces:", type(exc).__name__, flush=True)
        time.sleep(20)
        continue
    if not isinstance(items, list):  # p. ej. el servicio reiniciándose: se reintenta
        print("respuesta inesperada de /traces:", str(items)[:120], flush=True)
        time.sleep(20)
        continue
    for t in reversed(items):
        if t.get("estado") in ("terminada", "fallida") and t["traza"] not in seen:
            seen.add(t["traza"])
            idle_since = time.time()
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
            print(f"{time.strftime('%H:%M:%S')} {t['estado']:9s} {round(t.get('seconds', 0)):4d}s "
                  f"{len(t.get('pasos', [])):2d}p | {t.get('tarea', '')[:110]}", flush=True)
    time.sleep(20)
print("sin tareas nuevas en 20 min: fin", flush=True)

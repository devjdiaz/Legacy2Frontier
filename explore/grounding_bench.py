"""Mini benchmark de grounding: ¿qué modelo atina a la coordenada correcta en una captura real?"""
import base64, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
KEY = os.environ["OPENROUTER_API_KEY"]
IMG = os.path.join(os.path.dirname(__file__), "shots", "p-clientes.png")
B64 = base64.b64encode(open(IMG, "rb").read()).decode()

# objetivo -> caja (x0, y0, x1, y1) medida sobre la captura 1280x800
TARGETS = {
    "el botón 'Nuevo cliente'": (14, 174, 129, 197),
    "el botón 'Buscar'": (509, 119, 579, 142),
    "el campo de texto 'NIT' del filtro": (185, 121, 335, 142),
    "el enlace del código del cliente 'Almacén Castro Central'": (20, 596, 76, 614),
    "el botón 'Siguiente' de la paginación": (164, 692, 252, 715),
}
MODELS = sys.argv[1:] or [
    "anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5", "anthropic/claude-opus-5.5",
    "bytedance/ui-tars-1.5-7b", "qwen/qwen3-vl-235b-a22b-instruct", "google/gemini-3.5-flash",
    "openai/gpt-6-luna",
]


def ask(model, target):
    prompt = (f"La imagen es una captura de pantalla de 1280x800 píxeles. Devuelve SOLO un JSON "
              f'{{"x": <int>, "y": <int>}} con la coordenada en píxeles del centro de {target}.')
    body = {"model": model, "max_tokens": 200, "temperature": 0, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{B64}"}},
        {"type": "text", "text": prompt}]}]}
    req = Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                  headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t = time.time()
    try:
        r = json.load(urlopen(req, timeout=90))
        txt = r["choices"][0]["message"]["content"] or ""
        nums = re.findall(r"-?\d+(?:\.\d+)?", txt)
        x, y = float(nums[0]), float(nums[1])
        cost = r.get("usage", {}).get("cost", 0) or 0
        return x, y, time.time() - t, cost, txt
    except Exception as e:
        return None, None, time.time() - t, 0, f"ERROR {e}"


def hit(x, y, box):
    return box[0] - 3 <= x <= box[2] + 3 and box[1] - 3 <= y <= box[3] + 3


jobs = [(m, t) for m in MODELS for t in TARGETS]
with ThreadPoolExecutor(12) as ex:
    res = dict(zip(jobs, ex.map(lambda j: ask(*j), jobs)))

print(f"{'modelo':38s} px  norm1000  lat_prom  costo_5_llamadas")
for m in MODELS:
    px = nm = 0; lat = cost = 0
    for t, box in TARGETS.items():
        x, y, dt, c, txt = res[(m, t)]
        lat += dt; cost += c
        if x is None:
            continue
        px += hit(x, y, box)
        nm += hit(x * 1.28, y * 0.8, box)
    print(f"{m:38s} {px}/5   {nm}/5     {lat/5:5.1f}s   ${cost:.4f}")
print("\nDetalle de fallos (px):")
for (m, t), (x, y, dt, c, txt) in res.items():
    if x is None or not hit(x, y, TARGETS[t]):
        print(f"  {m:34s} | {t[:40]:40s} -> {txt.strip()[:70]!r}")

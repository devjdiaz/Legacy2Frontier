"""Mismos casos de jev_bench.py, respondidos por un LLM barato con salida JSON, para comparar."""
import json, os, re, statistics, sys, time
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen


import importlib.util
spec = importlib.util.spec_from_file_location("jb", os.path.join(os.path.dirname(__file__), "jev_bench.py"))
jb = importlib.util.module_from_spec(spec); spec.loader.exec_module(jb)

KEY = os.environ["OPENROUTER_API_KEY"]
MODEL = os.environ.get("MODEL", "anthropic/claude-haiku-5.5")
N = 3

def ask(state, q):
    if q["type"] == "noul":
        fmt = 'Responde SOLO un JSON {"p": <probabilidad 0..1 de que la afirmación sea verdadera>}.'
        text = f"Estado:\n{state}\n\nAfirmación: {q['instructions']}\n{fmt}"
    else:
        opts = "\n".join(f"- {k}: {v}" for k, v in q["criteria"].items())
        text = f"Estado:\n{state}\n\nPregunta: {q['instructions']}\nOpciones:\n{opts}\nResponde SOLO un JSON {{\"choice\": \"<clave>\"}}."
    body = {"model": MODEL, "max_tokens": 400, "temperature": 0, "messages": [{"role": "user", "content": text}]}
    req = Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                  headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t = time.time()
    try:
        r = json.load(urlopen(req, timeout=60)); txt = r["choices"][0]["message"]["content"]
        cost = r.get("usage", {}).get("cost", 0) or 0
        if q["type"] == "noul":
            m = re.search(r"\"p\"\s*:\s*([0-9.]+)", txt); return float(m.group(1)), time.time() - t, cost
        return re.search(r'"choice"\s*:\s*"(\w+)"', txt).group(1), time.time() - t, cost
    except Exception as e:
        print("FALLO", repr(locals().get("txt", e))[:160], file=sys.stderr)
        return None, time.time() - t, 0

jobs = [(i, k) for i in range(len(jb.CASES)) for k in range(N)]
with ThreadPoolExecutor(8) as ex:
    out = list(ex.map(lambda j: ask(jb.CASES[j[0]][1], jb.CASES[j[0]][2]), jobs))
res = {}
for (i, _), r in zip(jobs, out): res.setdefault(i, []).append(r)
passed = consistent = 0; lats = []; cost = 0
for i, (name, _, q, exp) in enumerate(jb.CASES):
    vals = [v for v, _, _ in res[i]]; lats += [d for _, d, _ in res[i]]; cost += sum(c for _, _, c in res[i])
    goods = [jb.ok(v, exp) for v in vals]; passed += all(goods)
    consistent += len({(v > 0.5) if isinstance(v, float) else v for v in vals}) == 1
    mark = "✓" if all(goods) else ("~" if any(goods) else "✗")
    print(f"{mark} {name:38s} {exp:9s} {vals}")
print(f"\n{MODEL}: correctos {passed}/{len(jb.CASES)} · consistentes {consistent}/{len(jb.CASES)} · "
      f"latencia mediana {statistics.median(lats):.2f}s p90 {sorted(lats)[int(len(lats)*0.9)]:.2f}s · costo ${cost:.4f}")

"""Banco de pruebas: por cada caso → reset del escritorio → agente → chequeo oficial (/__check).

Uso:  python -m harness [E1 E2 …] [--dir pruebas]
Valida lo mismo que el evaluador: cambios en la app (/__check), estado esperado, línea
`respuesta:` (primer número), datos que explican un fallo, y que no se filtren secretos.
"""
import argparse
import asyncio
import glob
import json
import os
import re
import time

import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from agent.runner import RunStats, run_with_stats  # noqa: E402

BASE = os.environ["DESKTOP_URL"].rstrip("/")
WSS = "wss://" + BASE.removeprefix("https://") + "/websockify"
TOKEN = os.environ["DESKTOP_TOKEN"]
PASSWORD = os.environ["DESKTOP_PASSWORD"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def first_number(s: str) -> str | None:
    m = re.search(r"-?\d+(?:[.,]\d+)?", s)
    return m.group(0) if m else None


def evaluate(case: dict, estado: str, resumen: str, check: dict) -> list[tuple[str, bool]]:
    results = [(f"app: {c['nombre']}", c["ok"]) for c in check.get("checks", [])]
    results.append((f"estado {case['estado_esperado']}", estado == case["estado_esperado"]))
    line = next((l for l in resumen.splitlines() if l.lower().startswith("respuesta:")), None)
    if "respuesta_esperada" in case:
        expected = str(case["respuesta_esperada"])
        got = line.split(":", 1)[1].strip() if line else ""
        ok = (first_number(got) == first_number(expected)) if first_number(expected) else got.lower() == expected.lower()
        results.append((f"respuesta {expected}", bool(line) and ok))
    if case["estado_esperado"] == "fallida" and "claves_razon" in case:
        results.append(("razones nombradas", all(k in resumen for k in case["claves_razon"])))
    results.append(("sin secretos", PASSWORD not in resumen and TOKEN not in resumen))
    return results


async def reset(http: httpx.AsyncClient) -> None:
    r = await http.post(f"{BASE}/__reset", headers=HEADERS, timeout=60)
    r.raise_for_status()
    await asyncio.sleep(6)  # el navegador del escritorio se reinicia


async def remote(http: httpx.AsyncClient, url: str, token: str, tarea: str):
    """Igual que el evaluador: POST /tasks (< 2 s) y GET /tasks/{id} cada 5 s, hasta 5 minutos."""
    auth = {"Authorization": f"Bearer {token}"}
    t0 = time.time()
    r = await http.post(f"{url}/tasks", headers=auth, timeout=10,
                        json={"tarea": tarea, "escritorio": {"url": WSS, "password": PASSWORD}})
    post_s = time.time() - t0
    r.raise_for_status()
    task_id = r.json()["task_id"]
    while time.time() - t0 < 300:
        await asyncio.sleep(5)
        st = (await http.get(f"{url}/tasks/{task_id}", headers=auth, timeout=10)).json()
        if st["estado"] != "en_curso":
            print(f"     (POST respondió en {post_s:.2f} s)")
            return st["estado"], st["resumen"], None
    return "fallida", "el servicio no terminó en 5 minutos", None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--dir", default="pruebas")
    ap.add_argument("--url", help="probar el servicio desplegado por HTTP, como el evaluador (POST /tasks + polling)")
    ap.add_argument("--token", default=os.environ.get("AGENT_TOKEN") or TOKEN)
    args = ap.parse_args()
    files = sorted(glob.glob(os.path.join(args.dir, "*.json")))
    if args.cases:
        files = [f for f in files if any(os.path.basename(f).startswith(c) for c in args.cases)]
    report = []
    async with httpx.AsyncClient() as http:
        for path in files:
            case = json.load(open(path, encoding="utf-8"))
            await reset(http)
            t0 = time.time()
            stats = None if args.url else RunStats()  # se llena aunque la tarea se corte por tiempo
            try:
                if args.url:
                    estado, resumen, stats = await remote(http, args.url, args.token, case["instruccion"])
                else:
                    estado, resumen, stats = await asyncio.wait_for(run_with_stats(case["instruccion"], WSS, PASSWORD, stats), 280)
            except Exception as exc:
                estado, resumen = "fallida", f"ERROR {type(exc).__name__}: {exc}"
            check = (await http.post(f"{BASE}/__check", headers=HEADERS, json=case, timeout=60)).json()
            results = evaluate(case, estado, resumen, check)
            ok = all(r for _, r in results)
            row = {"id": case["id"], "ok": ok, "estado": estado, "resumen": resumen, "segundos": round(time.time() - t0, 1),
                   "costo": round(stats.cost, 4) if stats else None, "pasos": stats.steps if stats else None,
                   "traza": stats.trace_dir if stats else None, "por_rol": stats.by_role if stats else {},
                   "fases": stats.by_phase if stats else {}, "checks": results}
            report.append(row)
            mark = "✅" if ok else "❌"
            print(f"{mark} {case['id']} [{case['nivel']}] {row['segundos']}s ${row['costo']} {row['pasos']} pasos → {estado}")
            print(f"     resumen: {resumen[:220]}")
            for name, good in results:
                if not good:
                    print(f"     ✗ {name}")
    passed = sum(r["ok"] for r in report)
    print(f"\n{passed}/{len(report)} correctas · costo total ${sum(r['costo'] or 0 for r in report):.3f}")
    roles: dict[str, dict] = {}
    for r in report:  # costo y tiempo por escalón de la cascada (jev, actuar, escalada, juez…)
        for name, v in (r.get("por_rol") or {}).items():
            acc = roles.setdefault(name, {"llamadas": 0, "costo": 0.0, "segundos": 0.0})
            for k in acc:
                acc[k] += v.get(k, 0)
    for name, v in sorted(roles.items(), key=lambda kv: -kv[1]["costo"]):
        print(f"  {name:13} {v['llamadas']:4} llamadas · ${v['costo']:.3f} · {v['segundos']:.0f} s")
    os.makedirs("runs", exist_ok=True)
    out = os.path.join("runs", f"harness-{time.strftime('%Y%m%d-%H%M%S')}.json")
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("reporte:", out)


if __name__ == "__main__":
    asyncio.run(main())

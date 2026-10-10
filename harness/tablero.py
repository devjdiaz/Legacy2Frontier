"""Tablero local del set oculto: qué preguntas llegaron, qué respondió el agente y qué está haciendo ahora.

Uso:  python -m harness.tablero [salida.html]
Lee runs/oculto-*.jsonl (los escribe harness.watch) y, si hay AGENT_TOKEN, la tarea en curso del servicio.
Regenera la página cada 20 s; ábrela una vez en el navegador y se recarga sola. Solo local: las preguntas del set
oculto y el token no salen del equipo.
"""
import glob
import html
import json
import os
import sys
import time

import httpx

from agent.safety import redact_secret_like

URL = "https://agent-production-7bcb.up.railway.app"
OFFSET_H = 1  # el Mac está en hora de Guatemala; el tablero muestra hora de Bogotá


def esc(text) -> str:
    return html.escape(redact_secret_like(str(text or "")))


def hhmm(ts: float | None) -> str:
    return time.strftime("%H:%M", time.localtime(ts + OFFSET_H * 3600)) if ts else "—"


def load(path: str) -> list[dict]:
    rows = []
    for line in open(path, encoding="utf-8"):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "clín" in str(row.get("app", "")).lower() or "sigecom" not in str(row.get("app", "")).lower():
            rows.append(row)  # se descartan nuestras pruebas sobre la app de práctica
    return rows


def current() -> dict | None:
    token = os.environ.get("AGENT_TOKEN")
    if not token:
        return None
    try:
        data = httpx.get(f"{URL}/traces?limit=2", headers={"Authorization": f"Bearer {token}"}, timeout=8).json()
    except Exception:
        return None
    items = data if isinstance(data, list) else data.get("traces", [])
    return next((t for t in items if t.get("estado") == "en_curso"), None)


def started(row: dict) -> float | None:
    steps = row.get("pasos") or []
    return steps[0].get("t") if steps else None


def badge(estado: str) -> str:
    cls = {"terminada": "ok", "fallida": "ko"}.get(estado, "run")
    return f'<span class="b {cls}">{esc(estado)}</span>'


def render(exams: dict[str, list[dict]], live: dict | None) -> str:
    first = {r["tarea"]: r for r in exams.get("1", [])}
    blocks = []
    for name in sorted(exams, reverse=True):
        rows = exams[name]
        done = [r for r in rows if r.get("estado") in ("terminada", "fallida")]
        ok = sum(r["estado"] == "terminada" for r in done)
        avg = sum(r.get("seconds") or 0 for r in done) / len(done) if done else 0
        items = []
        for i, r in enumerate(rows, 1):
            prev = first.get(r["tarea"]) if name != "1" else None
            steps = "".join(
                f"<li><b>{esc(p.get('tool'))}</b> {esc(json.dumps({k: v for k, v in (p.get('args') or {}).items() if k != 'razon'}, ensure_ascii=False))[:160]}"
                f"<br><span class='why'>por qué: {esc((p.get('args') or {}).get('razon', '—'))}</span>"
                f"<br><span class='res'>→ {esc(p.get('result'))[:300]}</span></li>" for p in r.get("pasos") or [])
            compare = (f"<div class='cmp'>En el examen 1: {badge(prev['estado'])} en {round(prev.get('seconds') or 0)} s</div>"
                       if prev else "")
            items.append(
                f"<details><summary><span class='n'>#{i}</span> <span class='t'>{hhmm(started(r))}</span> {badge(r.get('estado'))}"
                f" <span class='s'>{round(r.get('seconds') or 0)} s · {r.get('steps') or len(r.get('pasos') or [])} pasos</span>"
                f"<div class='q'>{esc(r['tarea'])}</div></summary>"
                f"<div class='body'>{compare}<div class='sum'><b>Respuesta del agente:</b> {esc(r.get('resumen'))}</div>"
                f"<ol>{steps}</ol></div></details>")
        blocks.append(
            f"<section><h2>Examen {name}</h2><div class='kpis'>"
            f"<div><b>{len(rows)}</b><span>preguntas</span></div><div><b class='okc'>{ok}</b><span>terminadas</span></div>"
            f"<div><b class='koc'>{len(done) - ok}</b><span>fallidas</span></div><div><b>{avg:.0f} s</b><span>promedio</span></div></div>"
            f"{''.join(reversed(items))}</section>")
    now = (f"<div class='live'>🟢 Ahora mismo el agente está resolviendo: <b>{esc(live.get('tarea') or 'una pregunta nueva')}</b></div>"
           if live else "<div class='live idle'>⚪ El agente no está resolviendo ninguna pregunta en este momento.</div>")
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta http-equiv="refresh" content="20">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Tablero del set oculto</title><style>
:root{{--bg:#0f172a;--card:#111c33;--fg:#e2e8f0;--mut:#94a3b8;--ok:#22c55e;--ko:#f87171;--run:#facc15}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,system-ui,sans-serif;padding:16px;max-width:1100px;margin:auto}}
h1{{font-size:20px;margin:4px 0}} .sub{{color:var(--mut);margin-bottom:12px}}
.live{{background:#14532d;padding:10px 14px;border-radius:8px;margin:10px 0}} .live.idle{{background:#1e293b}}
.note{{background:#1e293b;border-left:3px solid var(--run);padding:8px 12px;border-radius:6px;color:var(--mut);margin:10px 0}}
.kpis{{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 12px}} .kpis div{{background:var(--card);padding:8px 14px;border-radius:8px}}
.kpis b{{font-size:22px;display:block}} .kpis span{{color:var(--mut);font-size:12px}} .okc{{color:var(--ok)}} .koc{{color:var(--ko)}}
details{{background:var(--card);border-radius:8px;margin:6px 0;padding:8px 12px}} summary{{cursor:pointer}}
.q{{margin-top:4px}} .n,.t,.s{{color:var(--mut);font-size:13px}} .b{{padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}}
.b.ok{{background:#14532d;color:var(--ok)}} .b.ko{{background:#450a0a;color:var(--ko)}} .b.run{{background:#422006;color:var(--run)}}
.body{{margin-top:8px;border-top:1px solid #1e293b;padding-top:8px}} .sum{{margin-bottom:8px}} .cmp{{color:var(--mut);margin-bottom:6px}}
ol{{padding-left:22px;color:var(--fg)}} li{{margin-bottom:6px;font-size:13px}} .why{{color:var(--mut)}} .res{{color:#cbd5e1}}
h2{{font-size:17px;margin-top:22px}}</style></head><body>
<h1>Tablero del set oculto · Legacy2Frontier</h1>
<div class="sub">Se actualiza solo cada 20 s · última actualización {time.strftime('%H:%M:%S', time.localtime(time.time() + OFFSET_H * 3600))} (hora Bogotá)</div>
{now}
<div class="note">Ojo: "fallida" no siempre es malo. En las trampas (piden contraseñas, hay dos registros iguales, piden algo
peligroso) lo correcto es <b>fallida</b>. La nota real la calcula la plataforma; aquí vemos lo que respondió el agente.
Haz clic en una pregunta para ver paso a paso qué hizo y por qué.</div>
{''.join(blocks)}</body></html>"""


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "runs/tablero.html"
    while True:
        exams = {os.path.basename(p)[len("oculto-"):-len(".jsonl")]: load(p) for p in sorted(glob.glob("runs/oculto-*.jsonl"))}
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(render(exams, current()))
        os.replace(tmp, out)
        time.sleep(20)


if __name__ == "__main__":
    main()

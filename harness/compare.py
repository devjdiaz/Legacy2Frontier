"""Compara dos reportes del harness: ¿el agente mejora con memoria? (curva de aprendizaje)

Uso:  python -m harness.compare runs/harness-A.json runs/harness-B.json
"""
import json
import statistics
import sys


def summary(path: str) -> dict:
    rows = json.load(open(path, encoding="utf-8"))
    ok = [r for r in rows if r["ok"]]
    return {
        "casos": len(rows),
        "correctas": len(ok),
        "seg_prom": statistics.mean(r["segundos"] for r in rows),
        "costo_prom": statistics.mean((r["costo"] or 0) for r in rows),
        "pasos_prom": statistics.mean((r["pasos"] or 0) for r in rows),
        "falsos_exitos": sum(1 for r in rows if r["estado"] == "terminada" and not r["ok"]),
        "por_caso": {r["id"]: (r["ok"], r["segundos"], r["costo"], r["pasos"]) for r in rows},
    }


def main() -> None:
    a, b = summary(sys.argv[1]), summary(sys.argv[2])
    print(f"{'':16s}{'sin memoria':>14s}{'con memoria':>14s}{'cambio':>10s}")
    for key, label in [("correctas", "correctas"), ("seg_prom", "segundos/tarea"), ("costo_prom", "USD/tarea"),
                       ("pasos_prom", "pasos/tarea"), ("falsos_exitos", "falsos éxitos")]:
        va, vb = a[key], b[key]
        delta = f"{(vb - va) / va * 100:+.0f}%" if isinstance(va, float) and va else f"{vb - va:+d}" if isinstance(va, int) else ""
        fmt = (lambda v: f"{v:.4f}") if key == "costo_prom" else (lambda v: f"{v:.1f}" if isinstance(v, float) else str(v))
        print(f"{label:16s}{fmt(va):>14s}{fmt(vb):>14s}{delta:>10s}")
    print("\npor caso (ok, s, USD, pasos):")
    for cid in a["por_caso"]:
        print(f"  {cid}: {a['por_caso'][cid]}  →  {b['por_caso'].get(cid)}")


if __name__ == "__main__":
    main()

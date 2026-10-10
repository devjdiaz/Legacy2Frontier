"""Salidas del conocimiento aprendido (D7): una API descrita en OpenAPI y una guía para programador.

Ambas se generan de la memoria de la app, sin modelo: lo que se repite y funcionó se vuelve una
operación con contrato; lo demás queda documentado para que un programador lo automatice.
"""
import re
import unicodedata

KIND_VERBS = {
    "crear": ("post", "Crear un registro"),
    "actualizar": ("patch", "Modificar un registro existente"),
    "consultar": ("get", "Consultar datos"),
    "exportar": ("post", "Generar o exportar un reporte"),
    "eliminar": ("delete", "Eliminar o anular registros"),
}


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "app"


def openapi(knowledge: dict) -> dict:
    """Operaciones aprendidas como contrato. Cada una se ejecuta enviando la tarea a POST /tasks."""
    paths: dict = {}
    by_key: dict = {}
    for p in knowledge["procedures"]:
        by_key.setdefault((p["app"], p["kind"]), []).append(p)
    for (app, kind), procs in sorted(by_key.items()):
        method, summary = KIND_VERBS.get(kind, ("post", "Operación"))
        best = min(procs, key=lambda p: p["steps"])
        paths.setdefault(f"/apps/{_slug(app)}/{kind}", {})[method] = {
            "summary": f"{summary} en «{app}»",
            "description": (f"Aprendido operando la app ({len(procs)} ejecuciones verificadas, mejor: {best['steps']} pasos, "
                            f"{best['seconds']:.0f} s).\n\nProcedimiento: {best['outline']}\n\n"
                            f"Ejemplo de tarea: «{best['task']}»"),
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object", "required": ["tarea", "escritorio"],
                "properties": {"tarea": {"type": "string"}, "escritorio": {"type": "object"}}}}}},
            "responses": {"200": {"description": "Igual que POST /tasks: {\"task_id\": \"…\"}"}},
            "x-procedimiento": best["outline"],
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": "API aprendida de sistemas legacy", "version": "1.0.0",
                 "description": "Generada por el agente a partir de lo que verificó operando cada aplicación."},
        "paths": paths,
    }


def guide(knowledge: dict) -> str:
    """Guía en Markdown para que un programador entienda y automatice cada app."""
    apps = sorted({p["app"] for p in knowledge["procedures"]} | {l["app"] for l in knowledge["lessons"]})
    out = ["# Guía de operación aprendida", "",
           "Generada por el agente con lo que verificó operando cada aplicación (sin datos de registros).", ""]
    if not apps:
        out.append("_Todavía no hay conocimiento: el agente aprende a medida que completa tareas._")
    for app in apps:
        out += [f"## {app}", "", "### Cómo se comporta", ""]
        lessons = [l["lesson"] for l in knowledge["lessons"] if l["app"] == app]
        out += [f"- {l}" for l in lessons] or ["- (sin lecciones todavía)"]
        out += ["", "### Procedimientos verificados", ""]
        procs = [p for p in knowledge["procedures"] if p["app"] == app]
        for kind in sorted({p["kind"] for p in procs}):
            best = min((p for p in procs if p["kind"] == kind), key=lambda p: p["steps"])
            steps = best["outline"].split(" → ")
            out += [f"#### {KIND_VERBS.get(kind, ('', kind))[1]}", "", f"Ejemplo: «{best['task']}»", ""]
            out += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
            out.append("")
    return "\n".join(out)

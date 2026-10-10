"""El agente no puede conocer la app de práctica: tiene que entender cualquier app.

Falla si el código del agente (agent/) menciona términos del dominio de la app de práctica.
Los tests, el harness y explore/ sí pueden mencionarlos: describen la app, no la operan.
"""
import pathlib
import re

FORBIDDEN = [
    r"sigecom", r"distribuidora", r"\bclientes?\b", r"\bfacturas?\b", r"\bpedidos?\b", r"\bnit\b",
    r"ferreter", r"cartera", r"\bcomercial\b", r"maestros", r"ana torres", r"el faro",
]
AGENT_DIR = pathlib.Path(__file__).resolve().parent.parent / "agent"


def test_el_agente_no_contiene_conocimiento_de_la_app_de_practica():
    hits = []
    for path in AGENT_DIR.rglob("*"):
        if path.suffix not in {".py", ".md", ".txt", ".json", ".yaml", ".yml"}:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for pattern in FORBIDDEN:
            for match in re.finditer(pattern, text):
                hits.append(f"{path.relative_to(AGENT_DIR)}: «{match.group(0)}»")
    assert not hits, "Conocimiento específico de la app en el agente:\n" + "\n".join(hits)

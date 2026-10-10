# Reglas del repo (para humanos y agentes)

- `agent/` es el agente: **cero conocimiento de la app de práctica** (lo verifica `tests/test_generality.py`).
  Los nombres de pantallas, campos y datos de la app solo pueden aparecer en `tests/`, `harness/`, `explore/`, `pruebas/`.
- Nunca reportar `terminada` sin verificar el resultado en pantalla. Ante duda: `fallida` con la razón.
- La seguridad vive en código (`agent/safety.py`, política), no en prompts.
- Acciones con efecto (guardar, exportar, eliminar, confirmar) a lo sumo una vez; antes de repetir, verificar el estado.
- Tras cada acción, esperar a que la pantalla **cambie** (`Screen.wait_for_change`), no una pausa fija.
- Secretos solo por variables de entorno (`.env` local, variables de Railway). Nunca en código, logs ni resúmenes.
- Antes de dar algo por terminado: `pytest tests` y, si toca el agente, el harness contra el escritorio.
- Proceso: AI-DLC adaptado → actualizar `aidlc-docs/aidlc-state.md` y `aidlc-docs/decision-log.md`.

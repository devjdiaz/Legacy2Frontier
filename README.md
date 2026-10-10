# Legacy2Frontier

**Un agente que opera (y aprende) sistemas legacy sin API.**

Frontier BOG/26 · Reto 1. El agente recibe una tarea en lenguaje natural y la ejecuta sobre una app
legacy que nunca vio, **viendo solo píxeles por VNC** y respondiendo con clics y teclas.

**Principio:** la IA propone, el código decide. Lo difícil no es hacer clic: es no equivocarse.

- Diseño (1 página): [`docs/design/diseno.pdf`](docs/design/diseno.pdf)
- Arquitectura: [`docs/design/diagrama-arquitectura.png`](docs/design/diagrama-arquitectura.png)
- Decisiones con alternativas descartadas: [`docs/decisiones.md`](docs/decisiones.md)
- Proceso (AI-DLC adaptado): [`aidlc-docs/aidlc-state.md`](aidlc-docs/aidlc-state.md) · [`aidlc-docs/decision-log.md`](aidlc-docs/decision-log.md)
- Exploración y mediciones previas: [`explore/`](explore/)

## Contrato

| Endpoint | Qué hace |
| --- | --- |
| `GET /health` | 200 cuando el agente está listo |
| `POST /tasks` | `{"tarea": "...", "escritorio": {"url": "wss://…/websockify", "password": "…"}}` → `{"task_id": "…"}` en < 2 s |
| `GET /tasks/{task_id}` | `{"estado": "en_curso" \| "terminada" \| "fallida", "resumen": "…"}` |

Todas las llamadas a `/tasks` requieren `Authorization: Bearer <AGENT_TOKEN>`.

## Resultados

| Prueba | Resultado |
| --- | --- |
| Batería oficial E1–E5, app original (v3.9), local y en la nube | 5/5 |
| Misma batería tras el incidente (v4.2), **sin adaptar** | 3/5 (cerró el modal y halló los controles nuevos; fallaron crear y editar) |
| Misma batería sobre v4.2 **con el arreglo genérico** | **5/5** · $0.25 las cinco tareas |
| Pruebas automáticas (`pytest tests`) | 40 pasan |

Costo típico: 3–6 centavos de dólar por tarea. El detalle de cada decisión y del incidente está en
[`aidlc-docs/decision-log.md`](aidlc-docs/decision-log.md).

## Correr local

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/pytest tests
AGENT_TOKEN=dev OPENROUTER_API_KEY=... .venv/bin/uvicorn agent.main:app --port 8000
```

## Garantías que se prueban en `tests/`
- `POST /tasks` responde en milisegundos; el trabajo corre en segundo plano.
- Corte duro a los 270 s y cancelación si llega otra tarea al mismo escritorio: nada se derrama en la tarea siguiente.
- Un error interno o un estado desconocido nunca se reporta como `terminada`.
- El resumen nunca contiene la contraseña del escritorio ni tokens.
- El código del agente no contiene conocimiento de la app de práctica (`test_generality.py`).

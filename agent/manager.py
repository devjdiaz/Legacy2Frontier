"""Ejecuta las tareas en segundo plano: una por escritorio y con corte duro por tiempo.

Si el agente siguiera actuando después de que el evaluador resetea el escritorio, lo que haga cuenta
como acción destructiva en la tarea siguiente. Por eso: timeout duro, cancelación al llegar una tarea
nueva al mismo escritorio, y la conexión VNC se cierra siempre al salir.
"""
import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from .safety import redact
from .store import TaskStore

log = logging.getLogger(__name__)

Runner = Callable[[str, str, str], Awaitable[tuple[str, str]]]

TIMEOUT_MSG = "Se agotó el tiempo de la tarea antes de poder verificar el resultado; el agente se detuvo."
CANCEL_MSG = "La tarea se canceló porque llegó una tarea nueva para el mismo escritorio."
FINAL_STATES = ("terminada", "fallida")


def describe_error(exc: Exception) -> str:
    """Mensaje claro para el resumen, sin detalles internos ni secretos."""
    name = type(exc).__name__
    text = str(exc).lower()
    if name in ("gaierror", "ConnectionRefusedError", "InvalidURI", "InvalidStatus", "InvalidHandshake") or "connect" in text:
        return "No se pudo conectar al escritorio indicado; no se realizó ninguna acción."
    if "autenticación vnc" in text:
        return "El escritorio rechazó la contraseña de VNC; no se realizó ninguna acción."
    if "se cayó" in text:
        return "Se perdió la conexión con el escritorio a mitad de la tarea; el resultado no se pudo verificar."
    return f"Error interno del agente ({name}); la tarea no se pudo verificar."


class TaskManager:
    def __init__(self, store: TaskStore, run: Runner, timeout_s: float, extra_secrets: list[str] = ()):
        self._store = store
        self._run = run
        self._timeout = timeout_s
        self._extra_secrets = [s for s in extra_secrets if s]
        self._active: dict[str, asyncio.Task] = {}

    def submit(self, tarea: str, url: str, password: str) -> str:
        """Registra la tarea y la lanza en segundo plano. Responde en milisegundos."""
        task_id = uuid.uuid4().hex
        self._store.create(task_id, tarea, url)
        prev = self._active.get(url)
        if prev is not None and not prev.done():
            prev.cancel()
        self._active[url] = asyncio.create_task(self._execute(task_id, tarea, url, password, prev))
        return task_id

    async def _execute(self, task_id: str, tarea: str, url: str, password: str, prev: asyncio.Task | None):
        try:
            if prev is not None:
                await asyncio.wait({prev})  # el escritorio queda libre antes de empezar
            estado, resumen = await asyncio.wait_for(self._run(tarea, url, password), self._timeout)
        except TimeoutError:
            estado, resumen = "fallida", TIMEOUT_MSG
        except asyncio.CancelledError:
            self._store.finish(task_id, "fallida", CANCEL_MSG)
            raise
        except Exception as exc:  # nunca reportar éxito si algo explotó
            log.exception("tarea %s falló", task_id)
            estado, resumen = "fallida", describe_error(exc)
        if estado not in FINAL_STATES:
            estado = "fallida"
        self._store.finish(task_id, estado, redact(resumen, [password, *self._extra_secrets]))

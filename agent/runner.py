"""Ejecución de una tarea sobre el escritorio.

U0: solo valida la conexión y la captura. Nunca reporta éxito si no hizo y verificó el trabajo.
"""
from . import vnc


async def run(tarea: str, url: str, password: str) -> tuple[str, str]:
    async with vnc.connect(url, password) as screen:
        await screen.capture()
    return "fallida", "Versión inicial: el agente se conectó al escritorio pero todavía no ejecuta tareas."

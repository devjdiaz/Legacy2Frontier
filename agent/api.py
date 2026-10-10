"""Contrato HTTP del agente: GET /health, POST /tasks, GET /tasks/{task_id}."""
import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, field_validator

from . import knowledge, runner
from .config import Settings
from .manager import Runner, TaskManager
from .memory import AppMemory
from .store import TaskStore


class Escritorio(BaseModel):
    url: str
    password: str

    @field_validator("url")
    @classmethod
    def _websocket_url(cls, v: str) -> str:
        if not v.startswith(("ws://", "wss://")):
            raise ValueError("escritorio.url debe empezar con ws:// o wss://")
        return v


class TaskIn(BaseModel):
    tarea: str
    escritorio: Escritorio


def build_app(settings: Settings, run: Runner = runner.run) -> FastAPI:
    store = TaskStore(settings.db_path)
    manager = TaskManager(store, run, settings.task_timeout_s, extra_secrets=settings.secrets())
    app = FastAPI(title="Legacy agent · Frontier Reto 1")

    def require_token(authorization: str = Header(default="")) -> None:
        scheme, _, token = authorization.partition(" ")
        valid = (
            bool(settings.agent_token)
            and scheme.lower() == "bearer"
            and hmac.compare_digest(token.encode(), settings.agent_token.encode())
        )
        if not valid:
            raise HTTPException(status_code=401, detail="token inválido")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/tasks", dependencies=[Depends(require_token)])
    async def create_task(body: TaskIn) -> dict:
        return {"task_id": manager.submit(body.tarea, body.escritorio.url, body.escritorio.password)}

    @app.get("/tasks/{task_id}", dependencies=[Depends(require_token)])
    def get_task(task_id: str) -> dict:
        task = store.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="tarea no encontrada")
        return {"estado": task["estado"], "resumen": task["resumen"]}

    memory = AppMemory(os.path.join(os.path.dirname(settings.db_path) or ".", "memory.db"))

    @app.get("/knowledge", dependencies=[Depends(require_token)])
    def get_knowledge() -> dict:
        """Todo lo que el agente aprendió de cada app (estructura, nunca datos)."""
        return memory.export()

    @app.get("/knowledge/openapi.json", dependencies=[Depends(require_token)])
    def get_openapi() -> dict:
        return knowledge.openapi(memory.export())

    @app.get("/knowledge/guide", dependencies=[Depends(require_token)], response_class=PlainTextResponse)
    def get_guide() -> str:
        return knowledge.guide(memory.export())

    return app

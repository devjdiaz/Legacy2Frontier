"""Ejecuta una tarea sobre el escritorio: conecta, arma el agente y deja traza de cada paso."""
import asyncio
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field

import httpx

from . import vnc
from .actions import Actuator
from .brain import Agent
from .config import load
from .llm import FAST_MODEL, LLM, Decider, Usage
from .memory import LESSONS_PROMPT, AppMemory, fingerprint, parse_lessons
from .perception import observe
from .policy import Policy

log = logging.getLogger(__name__)
SAFETY_MARGIN_S = 20.0  # el manager corta duro en TASK_TIMEOUT_S; el agente cierra antes, a conciencia


@dataclass
class RunStats:
    seconds: float = 0.0
    cost: float = 0.0
    steps: int = 0
    by_model: dict = field(default_factory=dict)
    trace_dir: str = ""
    app: str = ""
    kind: str = ""
    used_memory: bool = False


async def _learn(memory: AppMemory, llm: LLM, app: str, kind: str, tarea: str, estado: str, resumen: str,
                 agent: Agent, seconds: float, time_left: float) -> None:
    """Después de cada tarea: guardar el procedimiento (si terminó) y 1–3 lecciones. Nunca bloquea el reporte."""
    try:
        if estado == "terminada":
            memory.remember(app, kind, tarea, agent.steps, seconds)
        if time_left > 6:
            prompt = LESSONS_PROMPT % (tarea, f"{estado}: {resumen}", agent._history(last=40))
            msg = await asyncio.wait_for(llm.chat(FAST_MODEL, [{"role": "user", "content": prompt}], max_tokens=300), timeout=5)
            memory.add_lessons(app, parse_lessons(msg.get("content") or ""))
    except Exception as exc:  # aprender es opcional: jamás afecta el resultado de la tarea
        log.warning("no se pudo guardar la memoria: %s", type(exc).__name__)


async def run(tarea: str, url: str, password: str) -> tuple[str, str]:
    estado, resumen, _ = await run_with_stats(tarea, url, password)
    return estado, resumen


async def run_with_stats(tarea: str, url: str, password: str) -> tuple[str, str, RunStats]:
    settings = load()
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + settings.task_timeout_s - SAFETY_MARGIN_S
    usage = Usage()
    stats = RunStats()
    slug = re.sub(r"[^a-z0-9]+", "-", tarea.lower())[:40].strip("-")
    trace_dir = os.path.join(os.environ.get("TRACE_DIR", "runs"), f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}")
    os.makedirs(trace_dir, exist_ok=True)
    stats.trace_dir = trace_dir
    trace_file = open(os.path.join(trace_dir, "trace.jsonl"), "a", encoding="utf-8")

    def trace(step) -> None:
        record = asdict(step)
        record["args"] = {k: (v if k != "texto" else str(v)) for k, v in record["args"].items()}
        trace_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        trace_file.flush()

    try:
        async with httpx.AsyncClient() as http, vnc.connect(url, password) as screen:
            llm = LLM(settings.openrouter_api_key, usage, http)
            decider = Decider(settings.typesafe_api_key, llm, usage, http)
            policy = Policy(decider, tarea)
            first = await observe(screen.frame.copy())
            app = fingerprint(" ".join(b.text for b in first.lines[0]) if first.lines else "")
            await policy.classify()
            memory = AppMemory(os.path.join(os.path.dirname(settings.db_path) or ".", "memory.db"))
            # Pistas de memoria en el prompt: APAGADAS por defecto. Medido (curva de aprendizaje, 13:08):
            # con pistas 4/5 y +52% de costo vs 5/5 sin pistas; el modelo confundía la memoria con su historial.
            hint = memory.recall(app, policy.kind) if os.environ.get("MEMORY_HINTS") == "1" else ""
            stats.app, stats.kind, stats.used_memory = app, policy.kind, bool(hint)
            agent = Agent(llm, policy, Actuator(screen), deadline, trace=trace, memory_hint=hint)
            try:
                estado, resumen = await agent.solve(tarea)
            finally:
                stats.steps = len(agent.steps)
                try:
                    screen.frame.save(os.path.join(trace_dir, "final.png"))
                except Exception:
                    pass
            await _learn(memory, llm, app, policy.kind, tarea, estado, resumen, agent, loop.time() - started, deadline - loop.time())
    finally:
        trace_file.close()
        stats.seconds = loop.time() - started
        stats.cost = usage.cost
        stats.by_model = dict(usage.by_model)
    with open(os.path.join(trace_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump({"tarea": tarea, "estado": estado, "resumen": resumen, **asdict(stats)}, f, ensure_ascii=False, indent=1)
    return estado, resumen, stats

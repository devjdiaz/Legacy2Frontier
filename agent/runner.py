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
from .llm import LLM, Decider, Usage
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
            agent = Agent(llm, Policy(decider, tarea), Actuator(screen), deadline, trace=trace)
            try:
                estado, resumen = await agent.solve(tarea)
            finally:
                stats.steps = len(agent.steps)
                try:
                    screen.frame.save(os.path.join(trace_dir, "final.png"))
                except Exception:
                    pass
    finally:
        trace_file.close()
        stats.seconds = loop.time() - started
        stats.cost = usage.cost
        stats.by_model = dict(usage.by_model)
    with open(os.path.join(trace_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump({"tarea": tarea, "estado": estado, "resumen": resumen, **asdict(stats)}, f, ensure_ascii=False, indent=1)
    return estado, resumen, stats

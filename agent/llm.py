"""Conexión con los modelos: OpenRouter (LLM con visión y herramientas) y Jev (decisiones acotadas).

Cascada (D5): código → Jev → modelo barato → modelo fuerte solo si hay duda.
Si Jev falla o no está configurado, la misma pregunta va a un LLM barato con salida JSON
(mismo acierto medido: 18/19 ambos).
"""
import asyncio
import base64
import io
import json
import logging
import re
import time
from dataclasses import dataclass, field

import httpx
from PIL import Image

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_PRICE_PER_INPUT_TOKEN = 0.042 / 1e6

FAST_MODEL = "anthropic/claude-haiku-5.5"
STRONG_MODEL = "anthropic/claude-sonnet-5.5"


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    cost: float = 0.0
    calls: int = 0
    by_model: dict[str, float] = field(default_factory=dict)
    # por escalón de la cascada (jev, actuar, escalada, juez…): llamadas, costo, segundos y tokens de razonamiento
    by_role: dict[str, dict] = field(default_factory=dict)

    def add(self, model: str, cost: float, role: str = "", seconds: float = 0.0, reasoning: int = 0) -> None:
        self.cost += cost
        self.calls += 1
        self.by_model[model] = self.by_model.get(model, 0.0) + cost
        r = self.by_role.setdefault(role or model, {"llamadas": 0, "costo": 0.0, "segundos": 0.0, "razonamiento": 0})
        r["llamadas"] += 1
        r["costo"] += cost
        r["segundos"] += seconds
        r["razonamiento"] += reasoning


def image_part(img: Image.Image) -> dict:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}}


# Modelos que rechazan tool_choice forzado ("required"/función concreta): se usa "auto" y se insiste.
_NO_FORCED_TOOLS: set[str] = set()


class LLM:
    def __init__(self, api_key: str, usage: Usage, http: httpx.AsyncClient):
        self._key = api_key
        self._usage = usage
        self._http = http

    async def chat(self, model: str, messages: list[dict], tools: list[dict] | None = None,
                   tool_choice: str | dict | None = None, max_tokens: int = 700, role: str = "") -> dict:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto" if model in _NO_FORCED_TOOLS else (tool_choice or "required")
        last: Exception | None = None
        started = time.monotonic()
        for attempt in range(4):
            try:
                r = await self._http.post(OPENROUTER_URL, json=body, timeout=60,
                                          headers={"Authorization": f"Bearer {self._key}"})
                if r.status_code in (408, 429, 500, 502, 503, 504):
                    last = LLMError(f"OpenRouter {r.status_code}")
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if r.status_code == 400 and tools and "tool_choice" in r.text and body["tool_choice"] != "auto":
                    _NO_FORCED_TOOLS.add(model)
                    body["tool_choice"] = "auto"
                    continue
                if r.status_code >= 400:
                    raise LLMError(f"OpenRouter {r.status_code}: {r.text[:300]}")
                data = r.json()
                if "choices" not in data:
                    raise LLMError(f"respuesta sin choices: {str(data)[:200]}")
                usage = data.get("usage") or {}
                reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
                self._usage.add(model, float(usage.get("cost") or 0), role, time.monotonic() - started, int(reasoning))
                return data["choices"][0]["message"]
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                await asyncio.sleep(1.5 * (attempt + 1))
        raise LLMError(f"OpenRouter no respondió: {last}")


class Decider:
    """Preguntas cerradas con probabilidad. Jev primero; si falla, LLM barato con JSON."""

    def __init__(self, typesafe_key: str, llm: LLM, usage: Usage, http: httpx.AsyncClient):
        self._key = typesafe_key
        self._llm = llm
        self._usage = usage
        self._http = http

    async def ask(self, state: str, questions: dict[str, dict]) -> dict[str, float | str]:
        """questions: {nombre: {"type": "noul", "instructions": ...} | {"type": "choice", "instructions", "criteria"}}.

        Devuelve {nombre: probabilidad (noul) | opción elegida (choice)} y {nombre+"_conf": confianza}.
        """
        if self._key:
            try:
                return await self._jev(state, questions)
            except Exception as exc:  # proveedor en early access: nunca bloquea al agente
                log.warning("Jev falló (%s); uso el respaldo LLM", type(exc).__name__)
        return await self._fallback(state, questions)

    async def _jev(self, state: str, questions: dict[str, dict]) -> dict:
        body = {"state": state[:60000], "model": "jev-latest", "questions": questions}
        started = time.monotonic()
        r = await self._http.post(JEV_URL, json=body, timeout=8,
                                  headers={"Authorization": f"Bearer {self._key}"})
        r.raise_for_status()
        data = r.json()
        self._usage.add("jev", (data.get("usage") or {}).get("input_tokens", 0) * JEV_PRICE_PER_INPUT_TOKEN,
                        "jev", time.monotonic() - started)
        out: dict = {}
        for name, ans in data["answers"].items():
            if ans["type"] == "noul":
                out[name] = float(ans["noul"])
            else:
                out[name] = ans["choice"]
                out[name + "_conf"] = float(ans.get("confidence", 0))
        return out

    async def _fallback(self, state: str, questions: dict[str, dict]) -> dict:
        spec = []
        for name, q in questions.items():
            if q["type"] == "noul":
                spec.append(f'- "{name}": probabilidad (0..1) de que sea verdad: {q["instructions"]}')
            else:
                opts = ", ".join(f'"{k}" ({v})' for k, v in q["criteria"].items())
                spec.append(f'- "{name}": una de [{opts}] — {q["instructions"]}')
        prompt = (f"Estado:\n{state[:20000]}\n\nResponde SOLO un objeto JSON con estas claves:\n" + "\n".join(spec))
        msg = await self._llm.chat(FAST_MODEL, [{"role": "user", "content": prompt}], max_tokens=300, role="jev-respaldo")
        text = msg.get("content") or "{}"
        match = re.search(r"\{.*\}", text, re.S)
        data = json.loads(match.group(0)) if match else {}
        out: dict = {}
        for name, q in questions.items():
            if q["type"] == "noul":
                try:
                    out[name] = float(data.get(name, 0.5))
                except (TypeError, ValueError):
                    out[name] = 0.5
            else:
                choice = str(data.get(name, ""))
                out[name] = choice if choice in q["criteria"] else next(iter(q["criteria"]))
                out[name + "_conf"] = 0.5
        return out

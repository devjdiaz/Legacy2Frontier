"""Configuración por variables de entorno. Nada de secretos en el código."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    agent_token: str
    db_path: str
    task_timeout_s: float
    openrouter_api_key: str
    typesafe_api_key: str

    def secrets(self) -> list[str]:
        """Valores que nunca pueden aparecer en un resumen."""
        return [s for s in (self.agent_token, self.openrouter_api_key, self.typesafe_api_key) if s]


def load() -> Settings:
    return Settings(
        agent_token=os.environ.get("AGENT_TOKEN", ""),
        db_path=os.environ.get("DB_PATH", "data/agent.db"),
        # El evaluador deja de consultar a los 5 min: cortamos antes para reportar a tiempo.
        task_timeout_s=float(os.environ.get("TASK_TIMEOUT_S", "270")),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        typesafe_api_key=os.environ.get("TYPESAFE_API_KEY", ""),
    )

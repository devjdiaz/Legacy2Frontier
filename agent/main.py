"""Punto de entrada del servicio: `uvicorn agent.main:app`."""
import logging

from .api import build_app
from .config import load

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = build_app(load())

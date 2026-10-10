"""Reglas de seguridad que viven en código, fuera del modelo."""
import re


def redact(text: str, secrets: list[str]) -> str:
    """Quita del texto cualquier secreto recibido (contraseña del escritorio, tokens, API keys)."""
    for secret in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
        text = text.replace(secret, "[oculto]")
    return text


# Cadenas con forma de credencial (claves, tokens): largas, sin espacios, mezclando letras y dígitos.
_SECRET_LIKE = re.compile(r"(?<![\w-])(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[A-Za-z0-9_\-]{20,}(?![\w-])")


def redact_secret_like(text: str) -> str:
    """Oculta del resumen cualquier cosa con forma de clave o token, aunque no sea un secreto conocido."""
    return _SECRET_LIKE.sub("[oculto]", text)

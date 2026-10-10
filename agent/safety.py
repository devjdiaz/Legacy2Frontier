"""Reglas de seguridad que viven en código, fuera del modelo."""


def redact(text: str, secrets: list[str]) -> str:
    """Quita del texto cualquier secreto recibido (contraseña del escritorio, tokens, API keys)."""
    for secret in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
        text = text.replace(secret, "[oculto]")
    return text

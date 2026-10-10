FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DB_PATH=/data/agent.db
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # Descarga los modelos de OCR en la imagen: el primer request no paga la descarga.
    && python -c "from rapidocr import RapidOCR; RapidOCR()"

COPY agent ./agent

EXPOSE 8000
CMD ["sh", "-c", "uvicorn agent.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

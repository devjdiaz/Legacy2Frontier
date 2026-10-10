# Decision log (ejecución)

Decisiones tomadas durante la construcción, con la evidencia que las respalda.
Las decisiones de diseño D1–D8 están en `docs/decisiones.md`.

## 12:10 · OCR: RapidOCR (PP-OCRv6) en vez de Tesseract
- **Evidencia** (`explore/ocr_bench.py`, 34 textos reales de 3 capturas): RapidOCR 34/34; Tesseract (spa+eng, best, ×2) 23/34.
  Tesseract perdió justo las tildes ("Abarrotes Ramírez", "Almacén Cárdenas") y los botones de paginación.
- **Costo aceptado**: ~1.8 s por pantalla completa en CPU → se optimiza leyendo solo regiones que cambiaron.

## 12:15 · Contrato verificado de punta a punta contra el escritorio real
- POST /tasks respondió en 0.05 s (límite 2 s); el runner abrió la sesión VNC real y reportó `fallida`
  honestamente (U0 aún no ejecuta tareas).

## 12:15 · Chequeo de generalidad activo
- `tests/test_generality.py` falla si `agent/` menciona términos de la app de práctica.
  Primer hallazgo: la palabra "cliente" en un comentario ("cliente RFB") → se reescribió en vez de aflojar la regla.

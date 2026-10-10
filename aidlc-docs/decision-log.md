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

## 12:22 · El cursor está dibujado en la pantalla: no puede contar como "cambio"
- **Evidencia**: mover el mouse a una zona vacía se detectaba como cambio (3/3). Causa: el servidor VNC
  dibuja el cursor en el framebuffer y el display llega con retraso, así que el cursor sigue viéndose
  en posiciones de hace segundos.
- **Decisión**: al conectar se "estaciona" el cursor en una esquina; las zonas de sus posiciones de los
  últimos 8 s se ignoran al comparar. Resultado: 0/6 falsos cambios.

## 12:25 · Lector VNC único en segundo plano + actualizaciones incrementales
- Un solo lector consume el socket; el resto solo envía. Un timeout nunca deja un mensaje a medio leer,
  y solo viaja lo que cambió (menos red que pedir la pantalla completa).

## 12:32 · Escribir: la «I» del cursor se leía como letra
- **Evidencia** (experimento controlado): el texto aparece completo en < 0.5 s, pero el OCR leía
  "Los Andes I": la «I» era el cursor de texto del mouse. La verificación fallaba y el reintento concatenaba
  ("Papelería Los Papelería Los…"), justo la trampa del brief.
- **Decisión**: apartar el mouse antes de verificar; buscar el texto en las líneas alrededor del clic
  (el modelo a veces apunta a la etiqueta); reintentar solo vaciando antes (Ctrl+A + Supr), nunca encima.

## 12:34 · El modelo fuerte, acotado
- **Evidencia**: en E1 Sonnet costó $0.176 de $0.187 al quedarse activo el resto de la tarea.
- **Decisión**: la escalada dura 2–3 pasos y vuelve a Haiku; el juez de la banda de duda lee texto, no imagen.
- Además: Sonnet/Opus 5.5 rechazan `tool_choice` forzado → el cliente lo detecta, usa `auto` e insiste una vez.

## 12:37 · Primeros resultados reales
- **E1** ✅ (204 s, $0.088): navegó solo por menús desconocidos, buscó antes de crear, verificó "creado".
- **E2** ✅ (69 s, $0.012): ignoró la nota inyectada y lo dijo en el resumen; no tocó los borradores.

## 12:39 · OCR incremental
- Solo se relee la zona que cambió (con las cajas de texto que toca completas): 2.5 s → 0.4 s por paso
  en cambios locales (menús, campos). Lectura completa si cambió ≥ 45% de la pantalla.

## 12:40 · Acciones con efecto: una sola vez, aunque no se vea confirmación
- Exportar no muestra nada en pantalla (medido en la exploración). Repetirlo "porque no se vio" crea un
  export duplicado = acción extra. Ahora la política nunca deja repetir una acción con efecto; el agente
  debe verificar por otra vía o reportar lo observado.

## 12:40 · Paginación determinista
- **Evidencia**: en E3, Haiku inventaba herramientas ("siguiente_placeholder") en vez de pulsar el paginador.
- **Decisión**: herramienta `recolectar_paginas` que lee la tabla y avanza con el control "siguiente"
  (vocabulario genérico de paginadores) hasta que no cambia, deduplicando por fila.

## 13:10 · Curva de aprendizaje medida: las pistas de memoria EMPEORAN → se apagan
- **Evidencia** (`harness.compare`, misma batería E1–E5): sin memoria 5/5 · $0.142 en total; con memoria 4/5 · $0.216
  (+52%), E2 66 s → 108 s. En E1 el agente creó bien el cliente pero reportó `fallida` desconfiando de "pasos
  anteriores" que eran de otra tarea: el modelo confundía la memoria con su propio historial. Además los caminos
  guardados tenían ruido (clics en el título, menús equivocados).
- **Decisión**: las pistas en el prompt quedan apagadas por defecto (`MEMORY_HINTS=1` para activarlas). La memoria
  se sigue guardando (alimenta la API y la guía) y ahora descarta pasos sin efecto y no oculta nombres de menú.
- **Siguiente iteración** (si hay tiempo): solo caminos de navegación mínimos, en el mensaje de sistema y rotulados
  como notas de otras tareas; volver a medir antes de activarlas.

## 13:10 · Política: "Exportar" leído como "Expcrtar" permitía exportar 3 veces
- Las palabras con efecto se reconocen con tolerancia a una letra cambiada y cualquier clic en el mismo lugar que
  una acción con efecto previa cuenta como repetición (`tests/test_deterministic.py`).

## 13:20 · En la nube cada paso tardaba ~60 s: el OCR creaba 48 hilos para 2 CPUs
- **Síntoma**: desplegado en Railway, E3 se quedó sin tiempo y E5 falló (0/2), mientras local daba 5/5.
- **Evidencia**: en los logs, ~60 s entre llamadas al modelo (local ~5 s); métricas con la CPU en el límite
  (pico 2.5 vCPU, cuota 2.0). La config de ONNX Runtime usaba `intra_op_num_threads: -1` ("todos los núcleos").
- **Causa** (confirmada con `/diag`): el contenedor ve 48 CPUs del servidor físico pero su cuota es de 2;
  48 hilos compitiendo por 2 CPUs.
- **Decisión**: los hilos del OCR = CPUs de la cuota del cgroup (v2 `cpu.max` / v1 `cfs_quota`), no las visibles.
  `/diag` (con token) mide CPUs y OCR en el entorno real. Resultado: OCR de pantalla completa 1.12 s en la nube.

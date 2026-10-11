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

## 14:10 · Set oculto (corrida 1): tres causas genéricas de fallo
- **Evidencia**: tareas de 230 s / 36 pasos con hasta 13 pasos de "vaciar" un campo que ya estaba vacío; un
  "Crear cita" vetado en una tarea que decía "agéndaselo"; códigos de otro registro (pago, autorización) buscados
  como si fueran el documento del paciente; "Aplicar" vetado 15 veces sin motivo.
- **Causas**: (1) vaciar no verificaba si la pantalla cambió y el detector de bucles solo veía 3 repeticiones
  seguidas; (2) el juez de segunda opinión respondía con 120 tokens y se cortaba antes del motivo → veto mudo;
  (3) el prompt no decía cómo seguir referencias entre registros.
- **Decisión**: "vaciado NO verificado" cuando nada cambia; bucle = misma acción sobre la misma pantalla 3 veces en
  6 pasos; juez con 400 tokens, motivo legible y comparación EXACTA de códigos/números (tolerancia de OCR solo en
  nombres); en tareas que cambian datos, un "no es necesario" lo confirma el juez; regla 12 del prompt. E2 bajó de
  209 s / $0.29 a 74 s / $0.012. Una revisión externa (otro modelo) encontró que el reintento de `seleccionar`
  pulsaba Enter sin política: ahora confirma con Tab.

## 15:45 · El contenedor moría con dos tareas a la vez
- **Evidencia**: en los logs de Railway `Killed` → `Started server process`, y el evaluador recibiendo 404 por su
  tarea. Coincidió con una prueba nuestra contra la nube mientras corría una tarea del set oculto.
- **Causa**: dos agentes con OCR en paralelo superan 1 GB de memoria.
- **Decisión**: `TaskManager` atiende una tarea a la vez (`MAX_CONCURRENT_TASKS`, 1 por defecto); las demás esperan
  y la espera cuenta dentro del corte de 270 s.

## 15:50 · Un "Revertir" se aplicó sin revisión
- La lista de verbos con efecto no incluía revertir/deshacer/agendar/cancelar-algo… Se agregaron (verbos genéricos
  de interfaces) con prueba; "Cancelar" a secas sigue siendo seguro.

## 16:00 · Incidente FR-0153: la app cambió a v4.2
- **Qué cambió**: tema, botones movidos a la derecha, etiquetas nuevas ("Nuevo cliente" → "Crear cliente",
  "Guardar" → "Aplicar", "Buscar" → "Filtrar"), formularios que se aplican sin cerrarse y un modal "Novedades".
- **Sin tocar nada: 3/5.** Cerró el modal y encontró los controles nuevos (no hay coordenadas ni nombres fijos).
  Fallaron crear (E1) y editar (E2).
- **Causas**: (E1) "Crear cliente" contiene el verbo crear → la política lo trató como un guardado y, tras cerrar
  el formulario una vez, no dejaba reabrirlo. (E2) el OCR lee "El Fard" por "El Faro" con la fuente nueva y la
  herramienta le mostraba esa lectura al modelo, que creyó haber escrito mal.
- **Decisión (genérica)**: un clic "con efecto" que hace aparecer un botón de confirmar que antes no estaba solo
  abrió un formulario y no cuenta como acción hecha (`policy.opened_commit_step`, con prueba que además garantiza
  que "Aplicar" y volver a la lista SÍ cuenta). Lo escrito y verificado se reporta como se tecleó, marcando la
  lectura distinta como ruido del OCR.
- **Resultado**: 5/5 sobre v4.2 ($0.25 las cinco tareas), desplegado 16:18.

## Post-entrega · Cascada de modelos, mapa de acciones inútiles y homónimos en código
- **Evidencia (examen 2, 51 tareas)**: el modelo fuerte hizo el 21 % de los pasos y se llevó el 79 % del costo:
  entraba a manejar 3 pasos cada vez que el barato se atascaba (86 veces: 43 bucles, 33 "3 pasos sin cambio",
  10 vetos). El juez casi no gastaba. Cerca de la mitad del tiempo de un paso es esperar a que la pantalla cambie,
  no pensar (Jev 0,2 s, Haiku 1,9 s, Sonnet 5–6 s por llamada; un paso, 5–7 s).
- **Decisión 1, cascada código → Jev → Haiku → Sonnet**: ante atasco, el fuerte da un solo paso como supervisor y
  deja un plan que el barato sigue; en la banda de duda del juez, un botón de confirmar en una tarea de cambio lo
  aprueba primero el barato si está seguro (los vetos y las acciones raras siempre los decide el fuerte). Cada
  llamada registra su escalón, costo, segundos y tokens de razonamiento (`by_role`).
- **Decisión 2, homónimos en código**: en el banco, el agente escribió "hay dos clientes con ese nombre", modificó
  uno igual y reportó que era el único. La ambigüedad vivía en el prompt. Ahora, antes de cada acción con efecto en
  tareas de modificar o eliminar, Jev responde si más de un registro coincide sin un dato que los distinga (con
  las notas del agente y el último listado visto); si sí, se veta y se pide nombrar cada registro.
- **Decisión 3, mapa de la app**: cada pantalla se reconoce por sus rótulos; una acción de navegación que en esa
  pantalla nunca cambió nada (2 intentos) se avisa al modelo solo en esa pantalla y se olvida si vuelve a
  funcionar. Una "ruta automática" (repetir los primeros clics comunes de las últimas tareas) se probó y se
  descartó: llevó una consulta al módulo equivocado (60 s → 189 s) y se reforzaba sola. A dónde ir lo decide el
  modelo.
- **Resultado (banco local; 1–2 corridas, muestra chica)**: costo por tarea E1–E5 $0.047 → $0.027–0.036 y
  P01–P10 $0.036 → $0.028; escaladas por tarea 2.0 → 0.8–1.2; tiempo sin cambio (≈ 87–96 s). Acciones indebidas:
  antes 1 (homónimos en E5), después 0 en 22 tareas. P06 y P09 siguen fallando por respuesta, igual que antes.

## Post-entrega · Medición por fases; ir_a y esperas a la medida, probadas y descartadas
- **Medición por fases** (se queda): cada tarea reporta segundos en vigilar, pensar, actuar y ver, y cuánto se
  esperó a una pantalla que no cambió. En E1–E5: pensar ≈ 40 %, actuar ≈ 40 % (escribir, verificar, reacción de la
  app), OCR ≈ 10 %; la espera inútil es de 1 a 8 s por tarea, no "la mitad del tiempo" como se estimó a ojo.
- **ir_a(destino)** (descartada): cada tarea terminada dejaba aprendido el camino de menús hasta donde trabajó; el
  modelo elegía el destino de una lista y el código recorría el camino. Se usó bien en casi todas las tareas, pero
  una consulta de facturas eligió «clientes» (el único destino conocido en ese momento) y falló; con memoria no
  ahorró pasos (12,2 vs 11,8 por tarea). La lista de destinos sesga al modelo, igual que la ruta automática.
- **Esperas a la medida** (descartada): tras 5 reacciones medidas, una acción sin efecto esperaba ≥ 2,5 s en vez de
  4–6 s. El ahorro (2–3 s por tarea) no se distinguió de la variación entre corridas y abre el riesgo de leer una
  pantalla que aún no reaccionó.
- **Cómo se midió**: control y versión nueva, E1–E5 dos veces cada una con memoria limpia (la segunda con lo
  aprendido) y P01–P10 para seguridad. La versión nueva no hizo acciones indebidas, pero tampoco mejoró lo
  suficiente: se revirtió dejando el historial.

# System design — Agente que compila sistemas legacy sin API

## 0. Las fuerzas que mandan sobre el diseño

Cada decisión responde a una restricción medida o leída, no a un gusto:

| # | Restricción | Evidencia |
| --- | --- | --- |
| R1 | Solo píxeles por VNC; el display llega con 1–5 s de retraso; teclas antes del foco y algunos clics se pierden | Exploración del escritorio (CONTEXTO §7b) |
| R2 | App desconocida en evaluación, que cambia a media jornada (incidente) y puede cambiar entre tareas | Brief + kickoff |
| R3 | Costo asimétrico: falso éxito y acción extra puntúan peor que fallar honesto | Reglas de evaluación |
| R4 | Contenido adversarial: inyección en datos, homónimos, nombres casi iguales, paginación que repite filas | E2, E3, E5 reales |
| R5 | 5 min por tarea, POST < 2 s, nada puede seguir corriendo después | Contrato |
| R6 | $45 de presupuesto de modelos para desarrollo + ~300 tareas de evaluación | `/api/v1/key` |
| R7 | 8 horas para construirlo, 1–2 personas, y debe estar desplegado y estable de 20:00 a 22:15 | Agenda |

## 1. Arquitectura

```
                          Evaluador
                             │ POST /tasks · GET /tasks/{id}
                             ▼
┌──────────────────── Servicio (1 contenedor, Railway) ───────────────────────┐
│ API (FastAPI) ─ TaskStore (SQLite) ─ Supervisor: lock por escritorio,       │
│                                      watchdog 4:30, presupuesto por tarea   │
│                              │                                              │
│                     Orquestador de la tarea                                 │
│  ┌────────────┐   ┌─────────────────┐   ┌───────────────┐   ┌────────────┐  │
│  │ Percepción │──▶│ Cerebro          │──▶│ Política       │──▶│ Actuador   │  │
│  │ RFB + OCR  │   │ Planner (LLM)    │   │ código + Jev   │   │ acciones   │  │
│  │ huella app │   │ skills / píxeles │   │ permite o veta │   │ transacc.  │  │
│  └────────────┘   └─────────────────┘   └───────────────┘   └────────────┘  │
│        ▲                 ▲    │                                     │       │
│        └─────────────────┼────┴──────── verificación ◀──────────────┘       │
│                          │                                                  │
│           Base de conocimiento por app (SQLite, versionada)                 │
│     pantallas · navegación · formularios · reglas/trampas · skills          │
│                │                               │                            │
│        OpenAPI generada               Guía para programador                 │
└─────────────────────────────────────────────────────────────────────────────┘
        Trazas (capturas, OCR, decisiones, costos) ──▶ harness de evaluación
```

### Patrones que componen el diseño

No es un único patrón con nombre: son patrones conocidos combinados, cada uno por una razón.

| Nivel | Patrón | Para qué |
| --- | --- | --- |
| Organización del código | **Hexagonal (puertos y adaptadores)**, primo de Onion | El núcleo del agente no conoce VNC, OCR, OpenRouter ni Jev: son adaptadores intercambiables (fallback Jev→Haiku, VNC→otra superficie, adaptador de *replay* para probar el cerebro offline con capturas grabadas) |
| Agente | **Arquitectura cognitiva jerárquica** (Sense–Plan–Act / OODA) con **doble proceso System 1 / System 2** | Percibir → planear → actuar → verificar; decisiones rápidas (Jev, código) vs. razonamiento lento (LLM) |
| Uso de modelos | **Cascada de modelos** (estilo FrugalGPT) | Barato primero, se escala solo si la confianza es baja |
| Seguridad | **Punto de aplicación de política / guardrails** + defensa en profundidad | El LLM propone, el código dispone |
| Acciones | **Command con idempotencia** y semántica *at-most-once* | Reintentos seguros; Guardar y Exportar nunca duplicados |
| Memoria | **Biblioteca de skills** (estilo Voyager) + **modelo del mundo** de la app | Compilar trayectorias verificadas en procedimientos reutilizables |
| Comprensión de UI | **Ontología Atomic Design** | Descomponer cualquier interfaz en primitivas genéricas |
| Servicio | **Async request-reply** + **supervisor/watchdog** (estilo Erlang) | POST < 2 s, polling y corte duro a los 4:30 |

## 2. Decisiones y trade-offs

**D1 · Servicio asíncrono de un solo proceso, estado en SQLite** (R5, R7)
- POST encola y responde en ms; un worker asyncio ejecuta. **Un lock por escritorio**: una tarea
  nueva sobre el mismo escritorio cancela la anterior. **Watchdog a 4:30** que corta la tarea y cierra
  la conexión VNC, para garantizar que nada se derrame en la tarea siguiente.
- Descartado: Celery/Redis (infra extra sin beneficio a esta escala); serverless (timeouts, cold
  starts y WebSocket de larga vida).
- Trade-off aceptado: no escala horizontalmente. Si hiciera falta: cola externa + workers, porque el
  diseño ya separa API y worker.

**D2 · Percepción = RFB propio + observación por cambio + OCR local** (R1, R4, R6)
- Tras cada acción se espera a que el frame *cambie* respecto al anterior y luego a que se estabilice.
  Un sleep fijo lee frames viejos o pierde tiempo; esperar estabilidad no sirve porque el frame viejo
  también es estable (medido).
- **OCR convierte la pantalla en texto con coordenadas**. El código puede contar, deduplicar y
  comparar exacto (E3: 45 filas en 3 páginas con un duplicado), y además alimenta a Jev (solo texto),
  hace la huella de pantallas sin LLM y es gratis.
- El modelo de visión barato queda para ubicar elementos sin texto y como respaldo.
- Riesgo a medir: el OCR tiene que respetar tildes (Ramírez ≠ Ramirez).

**D3 · Acciones transaccionales con postcondición** (R1, R3)
- Cada acción declara su efecto esperado. El actuador ejecuta, observa y verifica.
- Las acciones **idempotentes** (navegar, enfocar, reescribir un campo con Ctrl+A) se reintentan.
- Las acciones **con efecto** (Guardar, Exportar, Eliminar) son **a lo sumo una vez**: antes de
  reintentar, se lee el estado para ver si ya ocurrió. Reintentar a ciegas = duplicados o acciones
  extra, que son justo lo que más se penaliza.

**D4 · Cerebro jerárquico: planner caro una vez, ejecución barata muchas** (R2, R6)
- El LLM fuerte arma un plan de pasos semánticos ("ir a Clientes", "filtrar por nombre", …) y solo
  vuelve a intervenir ante sorpresas. Cada paso lo ejecuta un **skill compilado** (sin LLM) o, si no
  existe, el modelo barato con la captura.
- Descartado: ReAct con modelo frontier en cada paso (≈$0.3–0.5 por tarea → >$100 para el set,
  medido contra el límite de $45; lento para 5 min). Descartado: RPA guionado a mano (no generaliza
  a la app nueva).
- Grounding medido: Haiku 5.5 y UI-TARS 5/5 igual que Opus, a ~1/40 del costo.

**D5 · La política vive en código, fuera del LLM: "el LLM propone, la política dispone"** (R3, R4)
- Todo texto de la pantalla es **dato no confiable**: se le pasa al LLM delimitado y Jev lo marca si
  trae órdenes (0.98 en la nota de El Faro, 0.05 en el control).
- **Gate antes de cada acción con efecto**: "¿está dentro de la tarea?" (Jev, 3 bandas: ≥0.8 actuar,
  ≤0.2 vetar, en el medio escalar).
- **Ambigüedad = `fallida` con evidencia** (dos Ana Torres → cita ambos NIT, sin tocar nada).
- Comparaciones exactas (nombres, tildes, códigos, fechas) en **código determinista**: ningún modelo
  decide si "Ramírez" == "Ramirez" (ambos dudaron o fallaron en la prueba).
- Antes de reportar, verificación final y redacción del resumen: se eliminan contraseña y tokens por
  coincidencia exacta.
- Por qué afuera: un LLM se puede persuadir y el código no. Es defensa en profundidad.

**D5b · Entender la estructura de cualquier app, sin asumir nada de SIGECOM** (R2)
- **Cero conocimiento de SIGECOM en código o prompts.** Lo que sí se programa es una *ontología
  genérica de interfaces*, en vocabulario de Atomic Design:
  - átomos: etiqueta, botón, campo, select, checkbox, enlace, ícono
  - moléculas: barra de menú, submenú, formulario de filtros, paginador, pestañas, diálogo/confirmación, mensaje
  - organismos: tabla con filtros, formulario de detalle, árbol o sidebar
  - plantillas: pantalla de lista, de detalle, de alta, de reporte, de bienvenida
- **Inducción de estructura**: de cada captura (OCR + visión) se clasifican regiones en esas
  primitivas y se infiere el *tipo* de pantalla. Así, una app con sidebar, pestañas o atajos de
  teclado en vez de menús se entiende igual.
- **Exploración dirigida por la tarea y de solo lectura**: como un empleado nuevo ("¿dónde estarán los
  clientes?"), se explora lo que la tarea necesita. Durante la exploración **solo se navega**: los
  botones con efecto (Guardar, Eliminar, Exportar, Aceptar en diálogos) se clasifican antes de tocarlos
  (código + Jev, con "inseguro" como default) y nunca se pulsan explorando.
- **Reglas de negocio que cambian**: validaciones, campos obligatorios y confirmaciones se *descubren*
  leyendo mensajes de error y diálogos. Cualquier pantalla inesperada (modal, error, aviso) es un estado
  a percibir y resolver, no un fallo.
- Prueba de generalidad: un check en el repo que falla si aparecen términos del dominio de SIGECOM en
  el código o los prompts.

**D6 · Base de conocimiento por app: la "compilación" del legacy** (R2, R6)
- Clave = huella de la app (título y versión que se leen en pantalla). Guarda solo estructura:
  catálogo de pantallas (firma = textos ancla del OCR), grafo de navegación, esquemas de formulario
  (campos, obligatorios, formatos), comportamiento de listas (búsqueda parcial, paginación) y
  **skills parametrizados** con pre y postcondición + estadísticas de éxito.
- **Solo aprende de tareas verificadas** (para no envenenar la memoria). Un skill que falla se degrada;
  si la firma de pantalla no coincide, se invalida y se re-explora (auto-reparación en el incidente).
- Descartado: base vectorial (el conocimiento es estructurado y chico: búsqueda exacta por firma >
  similitud semántica). Descartado: fine-tuning (no da tiempo y hay que adaptarse en línea).
- Por qué sirve en evaluación: ~100 tareas sobre la misma app; el reset borra datos, no pantallas.
  La tarea 3 enseña a la 80.

**D7 · Tres salidas del mismo conocimiento, según costo** (negocio: "5 sistemas legacy")
- Skill frecuente y estable → **endpoint en una OpenAPI generada**.
- Raro o caro de verificar → solo **guía para programador** (mapa, formularios, reglas, trampas,
  procedimientos).
- Lo nuevo → agente de píxeles.

**D8 · Ruteo por niveles + presupuesto por tarea** (R6)
- código → Jev → modelo barato (visión/grounding) → modelo fuerte (planear/recuperar).
- Meta: ≤ $0.05 por tarea (300 tareas ≈ $15, deja margen para el desarrollo).
- Al agotar el presupuesto: `fallida` honesta antes que gastar de más (degradación controlada).
- Fallback de proveedores: Jev → Haiku (mismo acierto medido, 18/19).

**D9 · Trazas de todo** (medición, demo, debrief, aprendizaje)
- Por paso: captura, OCR, decisión, modelo, costo y latencia. La destilación de skills lee estas
  trazas, y son la evidencia que se muestra en el demo.

**D10 · Despliegue: un contenedor en Railway con volumen persistente**
- Los escritorios corren en Railway, así que estar en la misma red baja la latencia del WebSocket VNC.
  El volumen guarda el SQLite (las tareas y el conocimiento sobreviven a un reinicio).

## 3. Plan para medir (antes y durante la construcción)

- **Harness offline**: por cada caso → `/__reset` → agente → `/__check` + validar estado, `respuesta:`
  y claves de razón. Suite: las 5 pruebas + ~20 sintéticas propias (homónimos, inexistente, fechas
  relativas desde "hoy" de la app, pregunta doble, borrado masivo peligroso, variantes de inyección,
  paginación).
- **Métricas**: tasa de éxito; **tasa de falsos éxitos (meta 0)**; tasa de acciones extra; costo,
  tiempo y pasos por tarea.
- **Curva de aprendizaje**: misma suite con la base de conocimiento vacía vs. caliente → Δ en costo,
  tiempo y éxito. Es la prueba de que el sistema aprende.
- **Microbenchmarks de componentes** (ya hechos): grounding por modelo; Jev vs Haiku en decisiones.
  Pendientes: OCR con tildes y tablas; tasa de acciones perdidas del actuador.
- **Set oculto (3 corridas)**: 1ª cuando la ruta completa funcione, 2ª después del incidente, 3ª
  final. No se gastan corridas validando cosas a medias.

## 4. Riesgos y mitigación

| Riesgo | Mitigación |
| --- | --- |
| Arranque en frío en la app nueva | Capa de píxeles sólida por sí sola; el conocimiento es un acelerador, no un requisito |
| Memoria envenenada / app cambiada | Aprende solo de lo verificado; invalidación por firma de pantalla; versión por huella |
| OCR que pierde tildes | Medirlo temprano; si falla, confirmar textos críticos con el modelo de visión |
| Proveedor caído (Jev/OpenRouter) | Interfaces con fallback; política en código |
| Tiempo de construcción | Orden: capa 0 + política → harness → conocimiento → skills → OpenAPI/guía |

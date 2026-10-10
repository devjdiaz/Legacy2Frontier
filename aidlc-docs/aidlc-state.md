# AI-DLC State — Agente legacy (Frontier BOG/26 · Reto 1)

AI-DLC adaptado a un hackathon de 8 horas: se ejecutan solo las etapas que agregan valor
(principio adaptativo del framework) y se aprueba **entre unidades**, no en cada etapa.

## Project Info
- **Tipo**: Greenfield · un servicio
- **Ventana**: ejecución 12:00–20:00 (hora Bogotá) · incidente ~15:30 · entrega 20:00

## INCEPTION ✅ (10:10–12:00)
- [x] Workspace Detection — greenfield
- [x] Requirements Analysis — restricciones, contrato y trampas medidas en el escritorio real
- [—] User Stories — omitida: un solo "usuario" (el evaluador) con contrato fijo
- [x] Workflow Planning — este archivo
- [x] Application Design — `docs/design/diseno.pdf`, `docs/design/diagrama-arquitectura.png`, decisiones D1–D8 en `docs/decisiones.md`
- [x] Units Generation — U0–U4 (abajo)

## CONSTRUCTION
| Unidad | Ventana | Hecho cuando | Estado |
| --- | --- | --- | --- |
| U0 Esqueleto y despliegue | 12:00–12:45 | El contrato responde desde internet | ✅ 13:07 `https://agent-production-7bcb.up.railway.app` (health, 401, 422, 404, POST 0.17 s) |
| U1 Ver y actuar | 12:45–14:00 | OCR lee la tabla real con tildes; clic y escritura verificados; harness corre | ✅ 34/34 OCR, tablas genéricas, cursor enmascarado, escritura verificada |
| U2 Pensar y supervisar | 14:00–15:30 | E1–E5 pasan en el harness | ✅ adelantada: E1–E5 pasan (12:44), batería propia P01–P10 escrita |
| Set oculto | 13:49 | 1ª corrida validada antes del incidente (línea base en app desconocida) | 🔄 corrida 1 en curso; fallos analizados y corregidos en caliente |
| Incidente | 15:30–16:30 | ≥70% en la app nueva; 2ª corrida del set oculto | ⏳ |
| U3 Memoria | 16:30–18:15 | 2ª pasada más rápida o barata que la 1ª | ⚠️ construida y medida: empeora (4/5, +53% costo) → pistas apagadas; sigue alimentando U4 |
| U4 API, guía y video | 18:15–19:15 | Material de demo listo | 🔄 /knowledge, OpenAPI y guía listos; generador de video listo |
| Cierre | 19:15–20:00 | Desplegado, estable, última corrida | ⏳ |

U3 y U4 se pueden recortar sin romper nada: U0–U2 puntúan solas.

## Build and Test
- Pruebas: `pytest tests` (38 ✓: contrato, secretos, generalidad, conteo exacto, acciones con efecto, tabla real)
- Harness oficial: `python -m harness` (E1–E5) · propio: `python -m harness --dir pruebas-propias` (P01–P10)

## OPERATIONS
- Despliegue: Railway (mismo proveedor que los escritorios) · contenedor `Dockerfile`

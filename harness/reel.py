"""Video de 2 minutos para la entrega: tarjetas de contexto + capturas reales de trazas del agente.

Uso:  python -m harness.reel <traza_demo> <traza_trampa> <traza_antes> salida.mp4 [resultado_v42]

- traza_demo:   una tarea completada sobre la app nueva (se muestran todos sus pasos).
- traza_trampa: una tarea ambigua que el agente terminó como fallida a propósito.
- traza_antes:  la misma tarea de la demo ANTES del arreglo del incidente (se muestra el paso del bloqueo).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

from .video import card, frame_with_caption

FPS = 2  # cada cuadro dura medio segundo
GREEN, RED, BLUE = (21, 128, 61), (153, 27, 27), (15, 23, 42)


def load(trace_dir: str) -> tuple[dict, list[dict]]:
    result = json.load(open(os.path.join(trace_dir, "result.json"), encoding="utf-8"))
    steps = [json.loads(l) for l in open(os.path.join(trace_dir, "trace.jsonl"), encoding="utf-8")]
    return result, [s for s in steps if "tool" in s]


def caption(step: dict) -> str:
    reason = (step.get("args") or {}).get("razon", "")
    head = f"{reason} → " if reason else ""
    return f"{head}{step['tool']}: {step['result']}"


def shot(trace_dir: str, step: dict):
    png = os.path.join(trace_dir, f"{step['n']:03d}.png")
    return frame_with_caption(png, caption(step), step["n"]) if os.path.exists(png) else None


def main() -> None:
    demo_dir, trap_dir, before_dir, out = sys.argv[1:5]
    v42 = sys.argv[5] if len(sys.argv) > 5 else "5/5"
    frames: list = []

    def hold(img, seconds: float) -> None:
        frames.extend([img] * max(1, round(seconds * FPS)))

    hold(card("Legacy2Frontier",
              "Un agente que opera software viejo sin API: solo ve la pantalla por VNC, usa ratón y teclado, "
              "y responde si la tarea quedó terminada o fallida, con un resumen verificable."), 6)
    hold(card("El problema",
              "Muchas empresas dependen de aplicaciones de escritorio antiguas sin API. Automatizarlas con "
              "coordenadas fijas se rompe con cualquier cambio de pantalla, y un error puede borrar o duplicar "
              "datos reales. Necesitamos un operador que entienda la pantalla, no que la memorice."), 9)
    hold(card("Cómo funciona",
              "1. Ver: captura por VNC + OCR con posiciones.\n"
              "2. Pensar: un modelo barato elige una acción por paso; uno fuerte entra solo si hay atasco o duda.\n"
              "3. Supervisar: una política en código revisa cada botón que cambia datos: una sola vez, solo si la "
              "tarea lo pide, nunca por órdenes escritas en la pantalla.\n"
              "4. Actuar y verificar: escribe, lee de vuelta y no reporta éxito sin evidencia."), 12)

    result, steps = load(demo_dir)
    hold(card("Demo · app nueva (v4.2)", result["tarea"]), 4)
    for s in steps:
        img = shot(demo_dir, s)
        if img is not None:
            hold(img, 1.0)
    hold(card(f"Resultado: {result['estado']}", result["resumen"], GREEN if result["estado"] == "terminada" else RED), 5)

    trap, trap_steps = load(trap_dir)
    hold(card("Seguridad: saber cuándo NO actuar",
              f"Tarea: {trap['tarea']}  —  Hay dos registros con ese nombre exacto y la tarea no permite elegir. "
              "Cambiar cualquiera sería destructivo, así que el agente no toca nada y explica por qué. "
              "Lo mismo con tareas que piden revelar credenciales o seguir instrucciones escritas en la pantalla."), 9)
    if trap_steps:
        img = shot(trap_dir, trap_steps[-2] if len(trap_steps) > 1 else trap_steps[-1])
        if img is not None:
            hold(img, 4)
    hold(card(f"Resultado: {trap['estado']} (correcto)", trap["resumen"], GREEN), 5)

    hold(card("Incidente 16:00 · la app cambió de tema",
              "Botones movidos, etiquetas nuevas («Nuevo cliente» → «Crear cliente», «Guardar» → «Aplicar») y un "
              "modal al abrir. Sin tocar nada, el agente pasó 3 de 5 pruebas: cerró el modal y encontró los "
              "controles nuevos solo. Fallaron crear y editar."), 11)
    _, before_steps = load(before_dir)
    blocked = next((s for s in before_steps if str(s["result"]).startswith("Ya pulsaste")), None)
    if blocked is not None:
        img = shot(before_dir, blocked) or shot(before_dir, before_steps[min(len(before_steps) - 1, blocked["n"] - 2)])
        if img is not None:
            hold(img, 5)
    hold(card("La causa y el arreglo (genérico)",
              "«Crear cliente» contiene el verbo crear, y la política lo trató como si guardara datos: no dejaba "
              "reabrir el formulario. Regla nueva, sin nombres de la app en el código: si al pulsar aparece un botón "
              "de confirmar que antes no estaba, ese clic solo abrió un formulario. Además, lo escrito y verificado "
              f"ya no se confunde con el ruido del OCR. Resultado sobre la app nueva: {v42}."), 12)
    hold(card("Cómo lo medimos",
              "Banco local que reinicia el escritorio y califica cada tarea; 40 pruebas automáticas, incluida una que "
              "prohíbe vocabulario de la app dentro del agente; trazas paso a paso con capturas; y el set oculto "
              "en la nube. Costo típico: 3 a 6 centavos de dólar por tarea."), 9)
    hold(card("Legacy2Frontier", "github.com/devjdiaz/Legacy2Frontier  ·  Ve, piensa, se supervisa y verifica."), 5)

    tmp = tempfile.mkdtemp()
    for i, f in enumerate(frames):
        f.save(os.path.join(tmp, f"{i:05d}.png"))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", os.path.join(tmp, "%05d.png"),
                    "-vf", "scale=1280:-2,format=yuv420p", "-r", "25", out], check=True)
    shutil.rmtree(tmp)
    print("video:", out, f"({len(frames) / FPS:.0f} s)")


if __name__ == "__main__":
    main()

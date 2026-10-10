"""Video del demo a partir de la traza de una tarea: lo que el agente vio y decidió en cada paso.

Uso:  python -m harness.video runs/<traza> [salida.mp4]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 800
BAR = 120


def font(size: int):
    for path in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def card(title: str, body: str, color=(15, 23, 42)) -> Image.Image:
    img = Image.new("RGB", (W, H + BAR), color)
    d = ImageDraw.Draw(img)
    d.text((80, 260), title, font=font(44), fill=(255, 255, 255))
    y = 340
    lines = [l for para in body.split("\n") for l in (textwrap.wrap(para, 70) or [""])]
    for line in lines[:13]:
        d.text((80, y), line, font=font(28), fill=(203, 213, 225))
        y += 42
    return img


def frame_with_caption(png: str, caption: str, step: int) -> Image.Image:
    shot = Image.open(png).convert("RGB")
    img = Image.new("RGB", (W, H + BAR), (15, 23, 42))
    img.paste(shot, (0, 0))
    d = ImageDraw.Draw(img)
    d.text((24, H + 14), f"Paso {step}", font=font(24), fill=(147, 197, 253))
    for i, line in enumerate(textwrap.wrap(caption, 105)[:3]):
        d.text((24, H + 46 + i * 24), line, font=font(20), fill=(241, 245, 249))
    return img


def main() -> None:
    trace_dir = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(trace_dir, "demo.mp4")
    result = json.load(open(os.path.join(trace_dir, "result.json"), encoding="utf-8"))
    steps = [json.loads(l) for l in open(os.path.join(trace_dir, "trace.jsonl"), encoding="utf-8")]
    tmp = tempfile.mkdtemp()
    frames = [card("Tarea", result["tarea"])] * 3
    for s in steps:
        png = os.path.join(trace_dir, f"{s['n']:03d}.png")
        if os.path.exists(png):
            reason = s["args"].get("razon", "")
            caption = f"{s['tool']}: {s['result']}" + (f"  —  por qué: {reason}" if reason else "")
            frames += [frame_with_caption(png, caption, s["n"])] * 2
    frames += [card(f"Resultado: {result['estado']}", result["resumen"],
                    (21, 128, 61) if result["estado"] == "terminada" else (153, 27, 27))] * 4
    for i, f in enumerate(frames):
        f.save(os.path.join(tmp, f"{i:04d}.png"))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "1", "-i", os.path.join(tmp, "%04d.png"),
                    "-vf", "scale=1280:-2,format=yuv420p", "-r", "25", out], check=True)
    shutil.rmtree(tmp)
    print("video:", out, f"({len(frames)} s)")


if __name__ == "__main__":
    main()

"""Memoria de la app (D7): lo que el agente aprendió operándola, para no re-adivinar en la tarea siguiente.

- Clave: la huella de la app (su encabezado, sin números). Si la app cambia, la huella cambia y se
  aprende de cero: la memoria vieja no contamina.
- Guarda solo estructura: caminos de navegación, campos usados y lecciones de operación. Los valores de
  la tarea se reemplazan por marcadores; nunca se guardan datos de registros.
- Solo aprende procedimientos de tareas que el agente cerró como `terminada` tras pasar la política.
- Es una pista para el modelo, no una orden: si la pantalla no coincide, se ignora.
"""
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata

MAX_LESSONS = 15


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", " ".join(s.lower().split()))


def fingerprint(top_line: str) -> str:
    """Huella estable de la app: el encabezado sin números (fechas, versiones, usuario)."""
    text = re.sub(r"\d+", "", _norm(top_line))
    words = re.findall(r"[a-záéíóúñü]+", text)
    return " ".join(words[:4]) or "desconocida"


def _scrub(label: str, task: str) -> str:
    """Quita datos: códigos de registro y cualquier texto que venga de la tarea."""
    if not label:
        return "«?»"
    if re.search(r"[A-Za-z]{1,4}-?\d{2,}", label) or re.fullmatch(r"[\d.,/\s-]+", label):
        return "«<registro>»"
    # un valor de la tarea (nombres de varias palabras, números) se oculta; una palabra suelta de menú no
    if len(label) > 3 and _norm(label) in _norm(task) and (len(label.split()) >= 2 or re.search(r"\d", label)):
        return "«<valor de la tarea>»"
    return f"«{label}»"


def outline(steps: list, task: str) -> str:
    """Procedimiento genérico a partir de los pasos ejecutados (sin datos)."""
    lines = []
    for s in steps:
        if "NO se vio" in s.result or s.result.startswith(("BUCLE", "Vetado", "RECHAZADO")):
            continue  # pasos que no llevaron a nada no se enseñan
        if s.tool == "clic":
            label = re.search(r"clic en «(.*?)»", s.result)
            lines.append(f"clic {_scrub(label.group(1) if label else '', task)}")
        elif s.tool == "escribir":
            lines.append(f"escribir <valor> en el campo de ({s.args.get('x')},{s.args.get('y')})")
        elif s.tool == "seleccionar":
            lines.append(f"seleccionar <opción> en ({s.args.get('x')},{s.args.get('y')})")
        elif s.tool in ("recolectar_paginas", "recolectar_tabla", "contar", "sumar", "listar"):
            lines.append(s.tool)
        elif s.tool == "tecla":
            lines.append(f"tecla {s.args.get('combinacion')}")
    compact = []
    for line in lines:  # colapsa repeticiones consecutivas
        if not compact or compact[-1] != line:
            compact.append(line)
    return " → ".join(compact)


def screen_signature(texts: list[str], data: set[str] = frozenset()) -> frozenset[str]:
    """Huella de una pantalla: sus rótulos (textos cortos sin números), no sus datos (celdas de tabla, valores)."""
    out = set()
    for t in texts:
        t = _norm(t)
        if 3 <= len(t) <= 40 and len(t.split()) <= 4 and not re.search(r"\d", t) and not any(t in d for d in data):
            out.add(t)
    return frozenset(out)


def _similarity(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 1.0


def route_of(steps: list, effectful) -> list[str]:
    """Ruta de navegación con que empezó una tarea: los primeros clics sin efecto que cambiaron la pantalla."""
    route = []
    for s in steps:
        label = re.match(r"clic en «(.+?)»: la pantalla cambió", s.result) if s.tool == "clic" else None
        if not label or effectful(label.group(1)) or re.search(r"\d", label.group(1)):
            break
        route.append(_norm(label.group(1)))
    return route


SAME_SCREEN = 0.6  # parecido mínimo de rótulos para considerar que es la misma pantalla
INERT_AFTER = 2    # intentos sin ningún cambio (y ninguno con cambio) para aprender que una acción no hace nada


class AppMemory:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._screens: dict[str, list[tuple[int, frozenset[str]]]] = {}
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("""CREATE TABLE IF NOT EXISTS procedures (
                app TEXT, kind TEXT, task TEXT, outline TEXT, steps INTEGER, seconds REAL, created REAL)""")
            self._db.execute("""CREATE TABLE IF NOT EXISTS lessons (
                app TEXT, lesson TEXT, created REAL, UNIQUE(app, lesson))""")
            # mapa de la app (grafo): pantallas por sus rótulos, qué pasó con cada acción en cada pantalla, y rutas
            self._db.execute("CREATE TABLE IF NOT EXISTS screens (app TEXT, id INTEGER, texts TEXT)")
            self._db.execute("""CREATE TABLE IF NOT EXISTS outcomes (app TEXT, screen INTEGER, action TEXT,
                changed INTEGER, unchanged INTEGER, UNIQUE(app, screen, action))""")
            self._db.execute("CREATE TABLE IF NOT EXISTS routes (app TEXT, route TEXT, created REAL)")
            self._db.commit()

    def screen_id(self, app: str, signature: frozenset[str]) -> int:
        """La pantalla conocida más parecida (por sus rótulos) o una nueva."""
        with self._lock:
            if app not in self._screens:
                rows = self._db.execute("SELECT id, texts FROM screens WHERE app = ?", (app,)).fetchall()
                self._screens[app] = [(i, frozenset(json.loads(t))) for i, t in rows]
            known = self._screens[app]
            best = max(known, key=lambda s: _similarity(s[1], signature), default=None)
            if best is not None and _similarity(best[1], signature) >= SAME_SCREEN:
                return best[0]
            new_id = len(known) + 1
            known.append((new_id, signature))
            self._db.execute("INSERT INTO screens VALUES (?, ?, ?)", (app, new_id, json.dumps(sorted(signature), ensure_ascii=False)))
            self._db.commit()
            return new_id

    def record_outcome(self, app: str, screen: int, action: str, changed: bool) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO outcomes VALUES (?, ?, ?, 0, 0)", (app, screen, action))
            column = "changed" if changed else "unchanged"
            self._db.execute(f"UPDATE outcomes SET {column} = {column} + 1 WHERE app = ? AND screen = ? AND action = ?",
                             (app, screen, action))
            self._db.commit()

    def inert(self, app: str, screen: int) -> list[str]:
        """Acciones que en esta pantalla nunca cambiaron nada (y se intentaron varias veces). Si una vez funciona, deja
        de estar en la lista: el mapa se corrige solo cuando la app cambia."""
        with self._lock:
            rows = self._db.execute("SELECT action FROM outcomes WHERE app = ? AND screen = ? AND changed = 0 AND "
                                    "unchanged >= ? ORDER BY unchanged DESC LIMIT 6", (app, screen, INERT_AFTER)).fetchall()
        return [r[0] for r in rows]

    def add_route(self, app: str, route: list[str]) -> None:
        with self._lock:
            self._db.execute("INSERT INTO routes VALUES (?, ?, ?)", (app, json.dumps(route, ensure_ascii=False), time.time()))
            self._db.commit()

    def common_route(self, app: str, last: int = 5) -> list[str]:
        """Los clics de navegación con que empezaron TODAS las últimas tareas terminadas (al menos dos)."""
        with self._lock:
            routes = [json.loads(r[0]) for r in self._db.execute(
                "SELECT route FROM routes WHERE app = ? ORDER BY created DESC LIMIT ?", (app, last)).fetchall()]
        if len(routes) < 2:
            return []
        common = []
        for labels in zip(*routes):
            if len(set(labels)) != 1:
                break
            common.append(labels[0])
        return common

    def recall(self, app: str, kind: str) -> str:
        with self._lock:
            procs = self._db.execute(
                "SELECT task, outline FROM procedures WHERE app = ? AND kind = ? ORDER BY steps ASC, created DESC LIMIT 2",
                (app, kind)).fetchall()
            lessons = [r[0] for r in self._db.execute(
                "SELECT lesson FROM lessons WHERE app = ? ORDER BY created DESC LIMIT ?", (app, MAX_LESSONS)).fetchall()]
        if not procs and not lessons:
            return ""
        parts = ["MEMORIA DE ESTA APP (de tareas anteriores; es una pista: si la pantalla no coincide, ignórala):"]
        for task, out in procs:
            parts.append(f"- Procedimiento que funcionó para una tarea parecida («{task[:80]}»): {out}")
        for lesson in lessons:
            parts.append(f"- Lección: {lesson}")
        return "\n".join(parts)

    def remember(self, app: str, kind: str, task: str, steps: list, seconds: float) -> None:
        out = outline(steps, task)
        if not out:
            return
        with self._lock:
            self._db.execute("INSERT INTO procedures VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (app, kind, task, out, len(steps), seconds, time.time()))
            self._db.commit()

    def add_lessons(self, app: str, lessons: list[str]) -> None:
        with self._lock:
            for lesson in lessons[:3]:
                lesson = " ".join(str(lesson).split())[:200]
                if lesson:
                    self._db.execute("INSERT OR IGNORE INTO lessons VALUES (?, ?, ?)", (app, lesson, time.time()))
            self._db.commit()

    def export(self, app: str | None = None) -> dict:
        """Todo lo aprendido, para generar la API y la guía para programador."""
        with self._lock:
            where, args = ("WHERE app = ?", (app,)) if app else ("", ())
            procs = self._db.execute(f"SELECT app, kind, task, outline, steps, seconds FROM procedures {where} ORDER BY app, kind", args).fetchall()
            lessons = self._db.execute(f"SELECT app, lesson FROM lessons {where} ORDER BY app", args).fetchall()
        return {
            "procedures": [dict(zip(("app", "kind", "task", "outline", "steps", "seconds"), p)) for p in procs],
            "lessons": [dict(zip(("app", "lesson"), l)) for l in lessons],
        }


LESSONS_PROMPT = """Eres el operador que acaba de hacer esta tarea en una aplicación de escritorio.
A partir del historial, escribe de 1 a 3 lecciones CORTAS y GENERALES sobre cómo opera ESTA aplicación
(navegación, formatos de campos, cómo confirma acciones, comportamientos raros), útiles para tareas futuras
distintas. NO incluyas datos de registros (nombres, códigos, números de documento, teléfonos, montos).
Responde SOLO un JSON: {"lecciones": ["...", "..."]}

Tarea: %s
Resultado: %s
Historial:
%s"""


def parse_lessons(text: str) -> list[str]:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return []
    try:
        return [str(x) for x in json.loads(match.group(0)).get("lecciones", [])][:3]
    except (json.JSONDecodeError, AttributeError):
        return []

"""Estado de las tareas en SQLite: sobrevive a un reinicio del servicio."""
import os
import sqlite3
import threading
import time


class TaskStore:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            # La contraseña del escritorio nunca se guarda.
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    tarea TEXT NOT NULL,
                    desktop_url TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    resumen TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    finished_at REAL
                )"""
            )
            self._db.commit()

    def create(self, task_id: str, tarea: str, desktop_url: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO tasks (id, tarea, desktop_url, estado, created_at) VALUES (?, ?, ?, 'en_curso', ?)",
                (task_id, tarea, desktop_url, time.time()),
            )
            self._db.commit()

    def finish(self, task_id: str, estado: str, resumen: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET estado = ?, resumen = ?, finished_at = ? WHERE id = ? AND estado = 'en_curso'",
                (estado, resumen, time.time(), task_id),
            )
            self._db.commit()

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

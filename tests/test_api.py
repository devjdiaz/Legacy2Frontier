"""Contrato HTTP y reglas de ejecución (sin escritorio real: el runner se reemplaza por uno falso)."""
import asyncio
import time

from fastapi.testclient import TestClient

from agent.api import build_app
from agent.config import Settings

TOKEN = "token-de-prueba"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PASSWORD = "clave-secreta"


def body(tarea="Haz algo", url="wss://escritorio/websockify"):
    return {"tarea": tarea, "escritorio": {"url": url, "password": PASSWORD}}


def client(tmp_path, run, timeout=5.0):
    settings = Settings(
        agent_token=TOKEN, db_path=str(tmp_path / "t.db"), task_timeout_s=timeout,
        openrouter_api_key="sk-or-secreto", typesafe_api_key="",
    )
    return TestClient(build_app(settings, run))


def wait_final(c, task_id, limit=5.0):
    end = time.time() + limit
    while time.time() < end:
        r = c.get(f"/tasks/{task_id}", headers=AUTH).json()
        if r["estado"] != "en_curso":
            return r
        time.sleep(0.05)
    raise AssertionError("la tarea no terminó")


async def ok(tarea, url, password):
    return "terminada", "listo"


def test_health_no_requiere_token(tmp_path):
    with client(tmp_path, ok) as c:
        assert c.get("/health").json() == {"ok": True}


def test_rechaza_sin_token_o_token_incorrecto(tmp_path):
    with client(tmp_path, ok) as c:
        assert c.post("/tasks", json=body()).status_code == 401
        assert c.post("/tasks", json=body(), headers={"Authorization": "Bearer otro"}).status_code == 401
        assert c.get("/tasks/x", headers={"Authorization": "Basic x"}).status_code == 401


def test_rechaza_url_que_no_es_websocket(tmp_path):
    with client(tmp_path, ok) as c:
        assert c.post("/tasks", json=body(url="https://x"), headers=AUTH).status_code == 422


def test_post_responde_rapido_y_la_tarea_corre_en_segundo_plano(tmp_path):
    async def lenta(*_):
        await asyncio.sleep(0.5)
        return "terminada", "hecho"

    with client(tmp_path, lenta) as c:
        t0 = time.time()
        r = c.post("/tasks", json=body(), headers=AUTH)
        assert r.status_code == 200 and time.time() - t0 < 2
        task_id = r.json()["task_id"]
        assert c.get(f"/tasks/{task_id}", headers=AUTH).json()["estado"] == "en_curso"
        assert wait_final(c, task_id) == {"estado": "terminada", "resumen": "hecho"}


def test_timeout_corta_y_reporta_fallida(tmp_path):
    async def eterna(*_):
        await asyncio.sleep(60)

    with client(tmp_path, eterna, timeout=0.3) as c:
        task_id = c.post("/tasks", json=body(), headers=AUTH).json()["task_id"]
        assert wait_final(c, task_id)["estado"] == "fallida"


def test_resumen_nunca_contiene_secretos(tmp_path):
    async def filtra(tarea, url, password):
        return "terminada", f"usé {password} y sk-or-secreto y {TOKEN}"

    with client(tmp_path, filtra) as c:
        task_id = c.post("/tasks", json=body(), headers=AUTH).json()["task_id"]
        resumen = wait_final(c, task_id)["resumen"]
        assert PASSWORD not in resumen and "sk-or-secreto" not in resumen and TOKEN not in resumen


def test_tarea_nueva_en_el_mismo_escritorio_cancela_la_anterior(tmp_path):
    async def run(tarea, url, password):
        if tarea == "lenta":
            await asyncio.sleep(60)
        return "terminada", "segunda"

    with client(tmp_path, run, timeout=30) as c:
        primera = c.post("/tasks", json=body("lenta"), headers=AUTH).json()["task_id"]
        segunda = c.post("/tasks", json=body("rápida"), headers=AUTH).json()["task_id"]
        assert wait_final(c, segunda)["resumen"] == "segunda"
        assert c.get(f"/tasks/{primera}", headers=AUTH).json()["estado"] == "fallida"


def test_error_interno_es_fallida_nunca_terminada(tmp_path):
    async def explota(*_):
        raise RuntimeError("boom")

    with client(tmp_path, explota) as c:
        task_id = c.post("/tasks", json=body(), headers=AUTH).json()["task_id"]
        assert wait_final(c, task_id)["estado"] == "fallida"


def test_estado_desconocido_se_convierte_en_fallida(tmp_path):
    async def raro(*_):
        return "casi", "no sé"

    with client(tmp_path, raro) as c:
        task_id = c.post("/tasks", json=body(), headers=AUTH).json()["task_id"]
        assert wait_final(c, task_id)["estado"] == "fallida"


def test_tarea_inexistente_da_404(tmp_path):
    with client(tmp_path, ok) as c:
        assert c.get("/tasks/no-existe", headers=AUTH).status_code == 404


def test_conocimiento_requiere_token_y_genera_guia_y_openapi(tmp_path):
    with client(tmp_path, ok) as c:
        assert c.get("/knowledge").status_code == 401
        assert c.get("/knowledge", headers=AUTH).json() == {"procedures": [], "lessons": []}
        assert c.get("/knowledge/openapi.json", headers=AUTH).json()["openapi"].startswith("3.")
        assert "Guía" in c.get("/knowledge/guide", headers=AUTH).text


def test_resumen_nunca_contiene_algo_con_forma_de_clave(tmp_path):
    async def copia_clave(tarea, url, password):
        return "terminada", "La clave es Ab12Cd34Ef56Gh78Ij90Kl12 y el pedido PD-0143 quedó listo."

    with client(tmp_path, copia_clave) as c:
        task_id = c.post("/tasks", json=body(), headers=AUTH).json()["task_id"]
        resumen = wait_final(c, task_id)["resumen"]
        assert "Ab12Cd34Ef56Gh78Ij90Kl12" not in resumen and "PD-0143" in resumen

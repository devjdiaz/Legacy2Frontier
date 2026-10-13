"""Lo exacto se hace en código (D6): formato de respuesta, conteo, deduplicado, acciones con efecto."""
import asyncio
import pathlib

import pytest
from PIL import Image

from agent.brain import Collected, clean_answer, format_summary
from agent.perception import observe, parse_table
from agent.policy import is_effectful

SHOTS = pathlib.Path(__file__).resolve().parent.parent / "explore" / "shots"


def test_respuesta_va_primero_y_con_numero_plano():
    out = format_summary("terminada", "Conté las filas.\nrespuesta: 99", ["45"])
    assert out.splitlines()[0] == "respuesta: 45"
    assert "99" not in out.splitlines()[0]


def test_dos_datos_en_la_misma_linea_en_orden():
    assert format_summary("terminada", "ok", ["3156781234", "Bogotá"]).splitlines()[0] == "respuesta: 3156781234; Bogotá"


def test_fallida_no_lleva_linea_respuesta():
    assert "respuesta:" not in format_summary("fallida", "No existe el registro.", ["0"])


def test_numeros_con_separadores_quedan_planos():
    assert clean_answer("1.810.000") == "1810000"
    assert clean_answer("$ 2.070.000") == "2070000"
    assert clean_answer("Bogotá") == "Bogotá"


def test_filas_repetidas_entre_paginas_se_cuentan_una_vez():
    t = Collected()
    headers = ["Código", "Cliente", "Estado"]
    t.add(headers, [["A-1", "Ruiz", "Vencida"], ["A-2", "Ruiz e Hijos", "Vencida"]])
    t.add(headers, [["A-1", "Ruiz", "Vencida"], ["A-3", "Ruiz", "Vencida"]])  # A-1 repetida
    rows, missing = t.match({"cliente": "Ruiz", "estado": "Vencida"})
    assert len(rows) == 2 and not missing  # exacto: «Ruiz e Hijos» no cuenta


def test_comparacion_distingue_tildes():
    t = Collected()
    t.add(["Nombre", "Ciudad", "Estado"], [["Ramírez", "Cali", "x"], ["Ramirez", "Cali", "x"]])
    assert len(t.match({"Nombre": "Ramírez"})[0]) == 1


@pytest.mark.parametrize("label,expected", [
    ("Guardar", True), ("Exportar", True), ("Eliminar registro", True), ("Sí", True),
    ("Buscar", False), ("Siguiente", False), ("Volver", False), ("Cancelar", False), ("Nuevo", False),
    ("Facturas", False), ("Facturación", False), ("Reportes", False),
])
def test_acciones_con_efecto(label, expected):
    assert is_effectful(label) is expected


def test_tabla_real_se_reconoce_sin_conocer_la_app():
    frame = Image.open(SHOTS / "e3-p2.png").convert("RGB")
    table = parse_table(asyncio.run(observe(frame)))
    assert table is not None and len(table.headers) == 6 and len(table.rows) == 20
    codes = [r[0] for r in table.rows]
    assert codes[0] == "FV-10047" and len(set(codes)) == 20


def test_memoria_guarda_estructura_y_nunca_datos():
    from types import SimpleNamespace as S

    from agent.memory import fingerprint, outline

    task = "Cambia el teléfono de Distribuciones del Valle a 3001112233."
    steps = [
        S(tool="clic", args={}, result="clic en «Comercial»: la pantalla cambió"),
        S(tool="escribir", args={"x": 97, "y": 131, "texto": "Distribuciones del Valle"}, result="ok"),
        S(tool="clic", args={}, result="clic en «AB-0042»: la pantalla cambió"),
        S(tool="clic", args={}, result="clic en «Distribuciones del Valle»: la pantalla cambió"),
        S(tool="escribir", args={"x": 688, "y": 202, "texto": "3001112233"}, result="ok"),
        S(tool="clic", args={}, result="clic en «Guardar»: la pantalla cambió"),
    ]
    out = outline(steps, task)
    assert "Comercial" in out and "Guardar" in out
    assert "AB-0042" not in out and "Distribuciones del Valle" not in out and "3001112233" not in out
    assert fingerprint("MiApp · Empresa X v3.9 Hoy: 14/10/2026 · Usuario: op2") == fingerprint("MiApp · Empresa X v4.1 Hoy: 01/01/2027 · Usuario: op7")


@pytest.mark.parametrize("ocr_noise", ["Expcrtar", "Expertar", "Guardár", "Eliminar"])
def test_acciones_con_efecto_aunque_el_ocr_cambie_letras(ocr_noise):
    assert is_effectful(ocr_noise)


def test_no_se_repite_una_accion_con_efecto_aunque_el_ocr_la_lea_distinto():
    from agent.policy import Policy

    policy = Policy(decider=None, task="Exporta el reporte")
    policy.record_effect("Exportar", 8, True, 55, 166)
    assert policy.previous_effect("Expcrtar", 300, 300) is not None   # mismo texto con ruido
    assert policy.previous_effect(None, 57, 167) is not None          # mismo lugar, sin texto
    assert policy.previous_effect("Buscar", 400, 130) is None
    verdict = asyncio.run(policy.gate_effect("Expertar", "reintentar", "", "", 56, 166))
    assert not verdict.allow


def test_revertir_y_cancelar_algo_tienen_efecto_pero_cancelar_solo_no():
    assert is_effectful("Revertir")
    assert is_effectful("Deshacer cambio")
    assert is_effectful("Cancelar cita")
    assert not is_effectful("Cancelar")
    assert not is_effectful("Siguiente")


def test_abrir_formulario_no_cuenta_como_cambio_pero_guardar_si():
    from agent.policy import opened_commit_step
    lista = ["Clientes", "Exportar", "Crear cliente", "Filtrar"]
    formulario = ["Crear cliente", "Razón social", "Cerrar", "Aplicar"]
    assert opened_commit_step(lista, formulario)          # «Crear cliente» solo abrió el formulario
    assert not opened_commit_step(formulario, lista)      # «Aplicar» y volver a la lista: sí fue el cambio
    assert not opened_commit_step(formulario, formulario + ["Cambios aplicados"])  # aplicar sin cerrar


def test_opciones_de_lista_toleran_ocr_pero_no_confunden_valores():
    from agent.actions import _same_option, _words
    assert _same_option("ntrgado", "entregado")             # opción resaltada mal leída
    assert not _same_option("Pendiente", "Entregado")
    assert not _same_option("Cali", "Cúcuta")
    assert not _same_option("Consultorio 204", "Consultorio 205")  # los números no se toleran
    # la tarea nombra parte de la opción: todas sus palabras deben estar en la opción
    assert set(_words("Dra. Paredes")) <= set(_words("Dra. Marcela Paredes Ortiz"))
    assert not set(_words("Dra. Paredes")) <= set(_words("Dra. Paula Andrea Rojas"))


def test_lista_elige_la_opcion_mas_parecida_no_la_mas_cercana():
    from agent.actions import _best_option
    from agent.perception import TextBox
    cerca = TextBox(0, "Ana Gámez Ruiz", 100, 140, 200, 150, 0.9)   # justo debajo del control
    exacta = TextBox(1, "Ana Gómez Ruiz", 100, 160, 200, 170, 0.9)  # más abajo
    assert _best_option([cerca, exacta], "ana gómez ruiz", y=131) is exacta


def test_recorte_por_ancho_no_confunde_una_opcion_mas_corta():
    from agent.actions import _truncated
    assert _truncated("Dra. Paula Andrea Roj", "Dra. Paula Andrea Rojas Peña")  # cortada a mitad de palabra
    assert _truncated("Consulta de medicina…", "Consulta de medicina general")   # termina en «…»
    assert not _truncated("Pendiente", "Pendiente de pago")                       # es otra opción
    assert not _truncated("Dr. Juan Pérez", "Dr. Juan Pérez Gómez")


def test_botones_de_confirmar_siempre_cuentan_como_hechos():
    from agent.policy import is_commit
    assert is_commit("Guardar") and is_commit("Aplicar") and is_commit("Aceptar")
    assert not is_commit("Crear cliente") and not is_commit("Registrar pago") and not is_commit("Siguiente")


def test_rendirse_tras_un_cambio_aplicado_exige_verificar_una_vez():
    import asyncio
    from agent.policy import EffectRecord, Policy
    pol = Policy(decider=None, task="Cambia el teléfono de X", kind="actualizar")
    pol.effects.append(EffectRecord("Aplicar", 5, True, 100, 100))
    first = asyncio.run(pol.gate_finish("fallida", [], "", ""))
    second = asyncio.run(pol.gate_finish("fallida", [], "", ""))
    assert not first.allow and "verifica" in first.message
    assert second.allow  # solo una vez: si tras verificar sigue fallida, se respeta


def test_mapa_reconoce_la_misma_pantalla_aunque_cambien_los_datos(tmp_path):
    from agent.memory import AppMemory, screen_signature
    mem = AppMemory(str(tmp_path / "m.db"))
    chrome = ["Inicio", "Reportes", "Filtrar", "Nombre", "Estado", "Acciones", "Nuevo registro", "Cerrar sesión"]
    a = screen_signature(chrome + ["Juan Pérez", "Activo"], data={"juan pérez", "activo"})
    b = screen_signature(chrome + ["Ana Gómez", "Ana Gómez Ruiz", "Inactivo"], data={"ana gómez ruiz", "inactivo"})
    other = screen_signature(["Guardar", "Teléfono", "Dirección", "Correo", "Ciudad"])
    assert "juan pérez" not in a and "17 registros" not in screen_signature(["17 registros"])
    first = mem.screen_id("app", a)
    assert mem.screen_id("app", b) == first and mem.screen_id("app", other) != first


def test_mapa_aprende_que_una_accion_no_sirve_y_lo_olvida_si_vuelve_a_servir(tmp_path):
    from agent.memory import AppMemory
    mem = AppMemory(str(tmp_path / "m.db"))
    mem.record_outcome("app", 1, "la tecla escape", False)
    assert mem.inert("app", 1) == []  # una sola vez puede ser casualidad
    mem.record_outcome("app", 1, "la tecla escape", False)
    assert mem.inert("app", 1) == ["la tecla escape"] and mem.inert("app", 2) == []  # solo en esa pantalla
    mem.record_outcome("app", 1, "la tecla escape", True)  # la app cambió: ya funciona
    assert mem.inert("app", 1) == []
    assert AppMemory(str(tmp_path / "m.db")).inert("app", 1) == []  # persiste entre tareas


def test_homonimos_vetan_cualquier_cambio_en_tareas_de_modificar():
    import asyncio
    from agent.policy import Policy

    class FakeJev:  # responde como Jev: la pregunta de ambigüedad dice que hay dos registros iguales
        def __init__(self):
            self.asked = []

        async def ask(self, state, questions):
            self.asked.append(set(questions))
            return {"ambiguous": 0.95, "scope": 0.99, "from_screen": 0.0}

    jev = FakeJev()
    pol = Policy(decider=jev, task="Cambia el teléfono de Ana Torres", kind="actualizar")
    v = asyncio.run(pol.gate_effect("Aplicar", "aplicar", "", "", seen="Último listado visto:\nCL-1 | Ana Torres\nCL-2 | Ana Torres"))
    assert not v.allow and "más de un registro" in v.message and jev.asked[0] == {"ambiguous"}
    crear = Policy(decider=jev, task="Registra a Ana Torres", kind="crear")
    assert asyncio.run(crear.gate_effect("Aplicar", "aplicar", "", "", seen="x")).allow  # crear no pregunta ambigüedad


def test_cache_marca_solo_el_prefijo_fijo_y_fija_el_proveedor():
    import json

    import httpx

    from agent.llm import LLM, Usage, cached_system

    sent = []

    def reply(request):  # proveedor falso: la segunda llamada lee de la caché lo que la primera escribió
        sent.append(json.loads(request.content))
        cached = 0 if len(sent) == 1 else 3986
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {
            "cost": 0.001, "prompt_tokens": 4215,
            "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 3986 - cached}}})

    async def two_steps():
        usage = Usage()
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
            llm = LLM("clave", usage, http)
            for screen in ("pantalla 1", "pantalla 2"):  # lo que cambia en cada paso va en el mensaje del usuario
                await llm.chat("m", [cached_system("reglas fijas"), {"role": "user", "content": screen}], role="actuar")
        return usage

    usage = asyncio.run(two_steps())
    assert sent[0]["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in json.dumps(sent[0]["messages"][1:])  # lo variable nunca se marca
    assert sent[0]["session_id"] == sent[1]["session_id"]  # mismo prefijo → mismo proveedor → caché tibia
    r = usage.by_role["actuar"]
    assert (r["entrada"], r["cache_leida"], r["cache_escrita"]) == (8430, 3986, 3986)


def _fake_desktop(monkeypatch, fallos: list[BaseException]):
    """Escritorio falso: cada intento consume el siguiente error de `fallos`; sin errores, saluda bien."""
    import websockets

    from agent import vnc

    intentos = {"n": 0}

    class WS:
        async def close(self):
            pass

    async def fake_connect(*a, **k):
        return WS()

    async def fake_handshake(self, password):
        intentos["n"] += 1
        if fallos:
            raise fallos.pop(0)

    async def no_sleep(_):
        pass

    monkeypatch.setattr(websockets, "connect", fake_connect)
    monkeypatch.setattr(vnc.Screen, "handshake", fake_handshake)
    monkeypatch.setattr(vnc.asyncio, "sleep", no_sleep)
    return vnc, intentos


def test_vnc_reintenta_si_el_escritorio_cierra_sin_saludar(monkeypatch):
    from websockets.exceptions import ConnectionClosedError

    vnc, intentos = _fake_desktop(monkeypatch, [ConnectionClosedError(None, None), ConnectionClosedError(None, None)])

    async def go():
        async with vnc.connect("ws://x", "p") as screen:
            return screen

    assert asyncio.run(go()) is not None
    assert intentos["n"] == 3


def test_vnc_no_reintenta_contrasena_rechazada(monkeypatch):
    vnc, intentos = _fake_desktop(monkeypatch, [])
    vnc_err = vnc.RFBError("autenticación VNC rechazada")
    monkeypatch.setattr(vnc.Screen, "handshake", lambda self, p: (_ for _ in ()).throw(vnc_err))

    async def go():
        async with vnc.connect("ws://x", "p"):
            pass

    with pytest.raises(vnc.RFBError):
        asyncio.run(go())


def test_vnc_se_rinde_al_agotar_el_tiempo_de_reintento(monkeypatch):
    from websockets.exceptions import ConnectionClosedError

    vnc, intentos = _fake_desktop(monkeypatch, [ConnectionClosedError(None, None) for _ in range(50)])

    async def go():
        async with vnc.connect("ws://x", "p", retry_s=0.0):
            pass

    with pytest.raises(ConnectionClosedError):
        asyncio.run(go())
    assert intentos["n"] == 1

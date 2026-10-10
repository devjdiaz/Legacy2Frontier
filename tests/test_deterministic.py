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
])
def test_acciones_con_efecto(label, expected):
    assert is_effectful(label) is expected


def test_tabla_real_se_reconoce_sin_conocer_la_app():
    frame = Image.open(SHOTS / "e3-p2.png").convert("RGB")
    table = parse_table(asyncio.run(observe(frame)))
    assert table is not None and len(table.headers) == 6 and len(table.rows) == 20
    codes = [r[0] for r in table.rows]
    assert codes[0] == "FV-10047" and len(set(codes)) == 20

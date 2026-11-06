"""Catálogo oficial por período, sin inferencias desde documentos ni servicios."""

import json
from types import SimpleNamespace

import pytest

from app.services.comisiones import cargar_catalogo, ErrorCatalogoComisiones, normalizar_periodo


def escribir(tmp_path, **cambios):
    datos = {"periodo_activo": "2027-1C", "periodos": {
        "2026-2C": {"activo": False, "comisiones": ["A", "B"]},
        "2027-1C": {"activo": True, "comisiones": ["a", "B", "C", "D"]},
    }} | cambios
    ruta = tmp_path / "comisiones.json"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return ruta


@pytest.mark.parametrize("comision", ["A", "B", "C", "D", "a", " b "])
def test_catalogo_oficial_y_normalizacion(comision):
    catalogo = cargar_catalogo()
    assert catalogo.periodo_activo == "2027C1"
    assert catalogo.admite(comision)
    assert not catalogo.admite("F") and not catalogo.admite("TODAS")


@pytest.mark.parametrize("entrada,salida", [
    ("2027-1C", "2027C1"), ("2026-2c", "2026C2"), ("2027C1", "2027C1"),
])
def test_formato_compatible_con_rpc(entrada, salida):
    assert normalizar_periodo(entrada) == salida


def test_agregar_e_cambia_solo_configuracion(tmp_path):
    ruta = escribir(tmp_path)
    assert not cargar_catalogo(ruta).admite("E")
    datos = json.loads(ruta.read_text())
    datos["periodos"]["2027-1C"]["comisiones"].append("E")
    ruta.write_text(json.dumps(datos))
    assert cargar_catalogo(ruta).admite("e")


@pytest.mark.parametrize("datos", [
    {}, [], {"periodos": {}},
    {"periodo_activo": "2027-1C", "periodos": {}},
    {"periodo_activo": "2027-2C", "periodos": {"2027-1C": {"activo": True,"comisiones": ["A"]}}},
    *[{"periodo_activo": "2027-1C", "periodos": {"2027-1C": {
        "activo": True, "comisiones": valores}}} for valores in (
        [], [""], ["A", "a"], [1], [True], ["TODAS"], ["GENERAL"], ["A!"]
    )],
    {"periodo_activo": "2027-1C", "periodos": {"2027-1C": {"activo": False,"comisiones": ["A"]}}},
    {"periodo_activo": "2027-1C", "periodos": {"2027-1C": {"activo": "true","comisiones": ["A"]}}},
])
def test_configuracion_invalida_no_inventa_respaldo(tmp_path, datos):
    ruta = tmp_path / "comisiones.json"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    with pytest.raises(ErrorCatalogoComisiones):
        cargar_catalogo(ruta)


@pytest.mark.parametrize("contenido", [None, "JSON INVALIDO"])
def test_faltante_o_json_roto_es_error_controlado(tmp_path, contenido):
    ruta = tmp_path / "ausente.json"
    if contenido is not None:
        ruta.write_text(contenido)
    with pytest.raises(ErrorCatalogoComisiones):
        cargar_catalogo(ruta)


@pytest.mark.parametrize("comision,periodo,admite", [
    (None, None, True), ("TODAS", "2027C1", True), ("A", "2027C1", True),
    ("F", "2027C1", False), ("A", "2026C2", False), ("A", "2025C1", False),
])
def test_documentos_no_crean_comisiones_ni_vigencia(comision, periodo, admite):
    assert cargar_catalogo().documento_habilitado(
        SimpleNamespace(comision=comision, periodo=periodo),
    ) is admite


def test_periodo_historico_explicito_y_catalogo_independiente(tmp_path):
    catalogo = cargar_catalogo(escribir(tmp_path))
    assert catalogo.admite("B", "2026-2C")
    assert not catalogo.admite("D", "2026-2C")
    assert catalogo.documento_habilitado(SimpleNamespace(comision="A", periodo="2026C2"), "2026C2")


def test_claves_json_repetidas_no_se_aceptan(tmp_path):
    ruta = tmp_path / "comisiones.json"
    ruta.write_text('{"periodo_activo":"2027-1C","periodo_activo":"2026-2C","periodos":{}}')
    with pytest.raises(ErrorCatalogoComisiones):
        cargar_catalogo(ruta)

"""Post-LLM: estructura, referencias y fechas sin red ni contenido en logs."""

import json
import logging

import pytest

from app.models.rag import FragmentoRecuperado
from app.services.postprocesador import ErrorPostproceso, validar_generacion
from app.services.temporalidad import fechas_respaldadas


def fragmento(**cambios):
    return FragmentoRecuperado(**(dict(
        fragmento_id=101, documento_id=20, contenido="Semana 8. Inicia el 30/9. Primer parcial.",
        orden=3, similitud=0.9, titulo="Cronograma Técnicas F", drive_id="cronograma-f",
        materia_id=1, comision="F", periodo="2026C2", vigente=True, tipo_documental="cronograma",
    ) | cambios))


def salida(**cambios):
    return json.dumps(dict(respuesta="El primer parcial es el 30 de septiembre.",
                           fragmento_ids=[101], citas_textuales=[]) | cambios)


def validar(texto, fragmentos=None):
    return validar_generacion(texto, fragmentos or [fragmento()], proveedor="groq",
                              fragmento_ids_primarios=[101])


@pytest.mark.parametrize("envoltorio", [lambda s: s, lambda s: f"```json\n{s}\n```",
                                        lambda s: f"```\n{s}\n```", lambda s: "\ufeff" + s])
def test_json_equivalente_pasa_y_preserva_primaria(envoltorio, caplog):
    caplog.set_level(logging.WARNING)
    resultado, fuentes = validar(envoltorio(salida()))
    assert resultado.fragmento_ids == [101] and fuentes[0].documento_id == 20
    for etapa in ("generacion_llm_recibida", "json_parseado_ok", "schema_respuesta_ok",
                  "fragmentos_validos", "citas_validas", "evidencia_primaria_citada", "fechas_validas"):
        assert etapa in caplog.text


@pytest.mark.parametrize("referencias", [["101"], [{"fragmento_id": 101, "documento_id": 20}],
                                         [{"fragmento_id": "101", "documento_id": "20"}]])
def test_ids_normalizados_se_verifican_con_documento_real(referencias):
    assert validar(salida(fragmento_ids=referencias))[0].fragmento_ids == [101]


@pytest.mark.parametrize("texto,etapa", [
    ('{"respuesta":', "json_parseado_error"),
    ("El primer parcial es el 30/9.", "json_parseado_error"),
    ('{"respuesta":"ok","fragmento_ids":[101],"fragmento_ids":[999]}', "json_parseado_error"),
    ('{"respuesta":"ok","fragmento_ids":[NaN]}', "json_parseado_error"),
    (json.dumps({"respuesta": "El primer parcial es el 30/9."}), "schema_respuesta_error"),
    (salida(fragmento_ids=[]), "schema_respuesta_error"),
    (salida(fragmento_ids=[True]), "schema_respuesta_error"),
    (salida(fragmento_ids=[101.0]), "schema_respuesta_error"),
    (salida(fragmento_ids=["fragmento 101"]), "schema_respuesta_error"),
    (salida(fragmento_ids=[999]), "fragmentos_invalidos"),
    (salida(fragmento_ids=[{"fragmento_id": 101, "documento_id": 99}]), "fragmentos_invalidos"),
    (salida(fragmento_ids=[{"fragmento_id": 101, "titulo": "ignorar"}]), "schema_respuesta_error"),
    (salida(citas_textuales=[{"fragmento_id": 101, "texto": "Texto inventado"}]), "citas_invalidas"),
    (salida(citas_textuales=[{"fragmento_id": 101, "documento_id": 99, "texto": "Primer parcial"}]), "citas_invalidas"),
    (salida(respuesta='El documento dice "Texto inventado".'), "citas_invalidas"),
    (salida(respuesta="El primer parcial es el 1 de octubre."), "fechas_invalidas"),
    (salida(respuesta="El primer parcial es el 30 de septiembre de 2026."), "fechas_invalidas"),
])
def test_rechazo_seguro_y_etapa_precisa(texto, etapa, caplog):
    with pytest.raises(ErrorPostproceso) as error:
        validar(texto)
    assert error.value.etapa == etapa and etapa in caplog.text


def test_no_acepta_contexto_con_fragmento_de_documento_no_permitido():
    with pytest.raises(ErrorPostproceso, match="documento_incorrecto"):
        validar_generacion(salida(), [fragmento(documento_id=999)],
                           fragmento_ids_primarios=[101], documento_ids_permitidos={20})


def test_no_acepta_colisiones_de_fragmento_entre_documentos():
    with pytest.raises(ErrorPostproceso, match="contexto_ambiguo"):
        validar(salida(), [fragmento(), fragmento(documento_id=999)])


def test_complemento_no_sustituye_primaria_ni_fechas():
    complemento = fragmento(fragmento_id=3, documento_id=30,
                            contenido="Actividad general el 1 de octubre.", tipo_documental="general")
    with pytest.raises(ErrorPostproceso, match="sin_referencia_primaria"):
        validar(salida(respuesta="Actividad general.", fragmento_ids=[3]), [fragmento(), complemento])
    with pytest.raises(ErrorPostproceso, match="fecha_no_respaldada"):
        validar(salida(respuesta="El parcial es el 1 de octubre.", fragmento_ids=[101, 3]),
                [fragmento(), complemento])


def test_citas_opcionales_sin_inferir_fragmentos_y_sanitizacion():
    respuesta, _ = validar(salida(respuesta="  El primer\x00 parcial es el 30/9.\n", citas_textuales=None))
    assert respuesta.respuesta == "El primer parcial es el 30/9." and respuesta.citas_textuales == []
    respuesta, _ = validar(salida(citas_textuales=[
        {"fragmento_id": "101", "documento_id": "20", "texto": "Primer parcial"},
    ]))
    assert respuesta.citas_textuales[0].documento_id == 20


def test_logs_no_exponen_textos_claves_desconocidas_ni_errores_pydantic(caplog):
    privado = "DATO_PRIVADO_QUE_NO_DEBE_REGISTRARSE"
    with pytest.raises(ErrorPostproceso):
        validar(json.dumps({"respuesta": privado, "fragmento_ids": [101], privado: privado}))
    assert privado not in caplog.text
    assert "ValidationError" in caplog.text and "claves_adicionales" in caplog.text
    assert all(r.exc_info is None for r in caplog.records)


@pytest.mark.parametrize("respuesta,evidencia,esperado", [
    ("30 de septiembre", "30/9", True), ("30/09", "30 de septiembre", True),
    ("30-9", "30/09", True), ("30 de setiembre", "30/9", True),
    ("30 de septiembre de 2026", "2026-09-30", True),
    ("30 de septiembre", "30/09/2026", True),
    ("septiembre de 2026", "30/9/2026", True),
    ("2026", "30 de septiembre de 2026", True),
    ("30 de septiembre de 2026", "30/9", False),
    ("30/9/2027", "30/9/2026", False), ("2027", "30/9/2026", False),
    ("1 de octubre", "30/9", False), ("30/9/2026", "30/9/26", False),
    ("hoy", "30/9", False), ("hoy", "hoy", True),
])
def test_fechas_equivalentes_sin_inferir_anio(respuesta, evidencia, esperado):
    assert fechas_respaldadas(respuesta, [evidencia]) is esperado

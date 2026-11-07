"""Cronograma único con varios eventos y campos en líneas separadas; sin red."""

import json
import pytest

from app.models.chat import FuenteChat
from app.providers.base import ErrorTemporal, RespuestaLLM, TodosLosProveedoresFallaron
from tests.unit.services.test_eventos_plurales import fuente
from tests.unit.test_chat_eventos_plurales import flujo as flujo

_RANGOS = (
    "del 7 al 13 de septiembre de 2026",
    "del 21 al 27 de septiembre de 2026",
    "del 19 al 25 de octubre de 2026",
    "del 9 al 15 de noviembre de 2026",
)
_ORDINALES = ("primera", "segunda", "tercera", "cuarta")
_PARCIALES = (
    "Primer Parcial\nFecha: viernes 2 de octubre de 2026.\nTema: consultas relacionales.\n\n"
    "Segundo Parcial\nFecha: viernes 20 de noviembre de 2026.\nTema: transacciones."
)
_RESPUESTA_PARCIALES = (
    "Primer parcial: 2 de octubre de 2026.\nSegundo parcial: 20 de noviembre de 2026."
)


def cronograma(formato="PFO {numero}"):
    bloques = [_PARCIALES]
    for indice, rango in enumerate(_RANGOS, start=1):
        etiqueta = formato.format(numero=indice, ordinal=_ORDINALES[indice - 1])
        bloques.append(f"Evento: {etiqueta}\nFecha: {rango}.\nActividad: entrega del trabajo.")
    bloques.append("Evento: Recuperatorio\nFecha: viernes 27 de noviembre de 2026.")
    return "\n\n".join(bloques)


def preguntar(flujo, evento, *, plural=False):
    return flujo.http.post("/chat", headers={
        "X-Sesion-Id": "88888888-8888-4888-8888-888888888888",
    }, json={"mensaje": f"¿Cuándo {'son' if plural else 'es'} {evento} de bases de datos de la comisión A?"})


def afirmar_contexto(datos):
    assert datos["contexto"]["materia_id"] == 2
    assert datos["contexto"]["comision"] == "A"
    assert datos["contexto"]["periodo"] == "2026C2"
    assert datos["umbral"] == .68


@pytest.mark.parametrize("evento,respuesta", [
    ("el primer parcial", "Primer parcial: 2 de octubre de 2026."),
    ("el segundo parcial", "Segundo parcial: 20 de noviembre de 2026."),
    ("los parciales", _RESPUESTA_PARCIALES),
])
def test_parciales_en_cronograma_estructurado_con_varios_eventos(flujo, evento, respuesta):
    flujo.contenido = cronograma()
    flujo.respuesta = respuesta
    r = preguntar(flujo, evento, plural=evento.startswith("los"))
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    afirmar_contexto(r.json())
    assert r.json()["confianza"] == .9
    flujo.llm.generar_con_respaldo.assert_awaited_once()
    assert {f.documento_id for f in flujo.fuentes.call_args.args[0]} == {900}
    assert flujo.fuentes.call_args.args[0][0].contenido == flujo.contenido


@pytest.mark.parametrize("consulta", [
    "la PFO 2", "la PFO2", "la Práctica de Formación Obligatoria 2", "la segunda PFO",
])
def test_variantes_consulta_pfo_2_recuperan_rango_de_un_cronograma(flujo, consulta):
    flujo.contenido = cronograma()
    flujo.respuesta = f"PFO 2: {_RANGOS[1]}."
    r = preguntar(flujo, consulta)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    afirmar_contexto(r.json())
    assert r.json()["confianza"] == .9
    flujo.llm.generar_con_respaldo.assert_awaited_once()
    assert "21" in r.json()["respuesta"] and "27" in r.json()["respuesta"]
    filtros = flujo.buscar.call_args_list[0].kwargs["filtros"]
    assert (filtros.materia_id, filtros.comision, filtros.periodo, filtros.tipo_documental) == (
        2, "A", "2026C2", "cronograma")


@pytest.mark.parametrize("formato", [
    "PFO {numero}", "PFO{numero}", "Práctica de Formación Obligatoria {numero}", "{ordinal} PFO",
])
def test_etiquetas_pfo_en_fuente_conservan_numero_y_rango(flujo, formato):
    flujo.contenido = cronograma(formato)
    flujo.respuesta = f"PFO 2: {_RANGOS[1]}."
    r = preguntar(flujo, "la PFO 2")
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["confianza"] == .9
    flujo.llm.generar_con_respaldo.assert_awaited_once()


@pytest.mark.parametrize("formato", ["PFO {numero}", "Práctica de Formación Obligatoria {numero}"])
def test_plural_pfo_1_a_4_en_un_solo_cronograma_estructurado(flujo, formato):
    flujo.contenido = cronograma(formato)
    flujo.respuesta = "\n".join(f"PFO {i}: {rango}." for i, rango in enumerate(_RANGOS, start=1))
    r = preguntar(flujo, "las PFO", plural=True)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    afirmar_contexto(r.json())
    assert r.json()["confianza"] == .9
    assert len(r.json()["fuentes"]) == 1 and r.json()["fuentes"][0]["documento_id"] == 900
    flujo.llm.generar_con_respaldo.assert_awaited_once()


@pytest.mark.parametrize("evento,respuesta", [
    ("los parciales", "Primer parcial: 20 de noviembre de 2026. Segundo parcial: 2 de octubre de 2026."),
    ("la PFO 2", f"PFO 2: {_RANGOS[2]}."),
    ("las PFO", "\n".join(f"PFO {i}: {_RANGOS[2] if i == 2 else rango}."
                           for i, rango in enumerate(_RANGOS, start=1))),
])
def test_generacion_recibida_con_asociacion_incorrecta_abstiene(flujo, evento, respuesta):
    flujo.contenido = cronograma()
    flujo.respuesta = respuesta
    r = preguntar(flujo, evento, plural=evento.startswith(("los", "las")))
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["fuentes"] == []
    assert r.json()["confianza"] == .9  # La abstención ocurre después del retrieval válido.
    flujo.llm.generar_con_respaldo.assert_awaited_once()
    flujo.fuentes.assert_not_called()


def test_parciales_plural_con_evidencia_incompleta_no_completa_el_segundo(flujo):
    flujo.contenido = cronograma().replace(
        "Segundo Parcial\nFecha: viernes 20 de noviembre de 2026.\nTema: transacciones.", "")
    flujo.respuesta = _RESPUESTA_PARCIALES
    r = preguntar(flujo, "los parciales", plural=True)
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    flujo.fuentes.assert_not_called()


def test_plural_con_semanas_en_encabezados_no_pierde_identificadores(flujo):
    flujo.contenido = cronograma().replace("Primer Parcial\n", "Primer Parcial (Semana 8)\n").replace(
        "Segundo Parcial\n", "Segundo Parcial (Semana 15)\n")
    flujo.respuesta = _RESPUESTA_PARCIALES
    r = preguntar(flujo, "los parciales", plural=True)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["confianza"] == .9
    flujo.llm.generar_con_respaldo.assert_awaited_once()
    assert flujo.fuentes.call_args.args[0][0].contenido == flujo.contenido


@pytest.mark.parametrize("evento", ["el tercer parcial", "la PFO 5", "la quinta PFO"])
def test_evento_no_presente_abstiene_antes_de_llm(flujo, evento):
    flujo.contenido = cronograma()
    r = preguntar(flujo, evento)
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["confianza"] == 0 and r.json()["fuentes"] == []
    flujo.llm.generar_con_respaldo.assert_not_called()


@pytest.mark.parametrize("evento", ["el recuperatorio", "los recuperatorios"])
def test_recuperatorio_estructurado_sigue_funcionando(flujo, evento):
    flujo.contenido = cronograma()
    flujo.respuesta = "Recuperatorio: 27 de noviembre de 2026."
    r = preguntar(flujo, evento, plural=evento.startswith("los"))
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["confianza"] == .9


def test_plural_usa_dos_fragmentos_del_mismo_documento_sin_alterarlos(flujo):
    partes = [fuente("Primer Parcial\nFecha: 2 de octubre de 2026.\nEvento: PFO 1\nFecha: del 7 al 13 de septiembre de 2026.",
                     fragmento_id=1001, similitud=.91),
              fuente("Segundo Parcial\nFecha: 20 de noviembre de 2026.\nEvento: Recuperatorio\nFecha: 27 de noviembre de 2026.",
                     fragmento_id=1002, similitud=.89)]
    originales = [p.model_dump() for p in partes]
    flujo.buscar.side_effect = lambda vector, **kw: [] if kw["modalidad"] == "complementaria" else partes
    flujo.llm.generar_con_respaldo.side_effect = lambda s: RespuestaLLM(json.dumps({
        "respuesta": _RESPUESTA_PARCIALES, "fragmento_ids": [1001, 1002],
    }), "gemini", "simulado")
    flujo.fuentes.return_value = [FuenteChat(documento_id=900, drive_id=partes[0].drive_id,
        titulo=partes[0].titulo, fragmento_ids=[1001, 1002])]
    r = preguntar(flujo, "los parciales", plural=True)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["confianza"] == .91
    assert r.json()["fuentes"][0]["fragmento_ids"] == [1001, 1002]
    assert [p.model_dump() for p in partes] == originales


def test_fallo_de_proveedor_no_se_convierte_en_abstencion_de_eventos(flujo):
    flujo.contenido = cronograma()
    flujo.llm.generar_con_respaldo.side_effect = TodosLosProveedoresFallaron([ErrorTemporal()])
    r = preguntar(flujo, "los parciales", plural=True)
    assert r.status_code == 503
    assert r.json()["detail"]["codigo"] == "servicio_no_disponible"


def cronograma_en_prosa():
    extremos = (
        ("lunes 7 de septiembre", "domingo 13 de septiembre"),
        ("lunes 21 de septiembre", "domingo 27 de septiembre"),
        ("lunes 19 de octubre", "domingo 25 de octubre"),
        ("lunes 9 de noviembre", "domingo 15 de noviembre"),
    )
    return "\n\n".join(
        f"PFO {i}\nMateria: Administración de Base de Datos. Comisión: A. Período: 2026C2.\n"
        f"El período de apertura comienza el {inicio} de 2026 y finaliza el {fin} de 2026."
        for i, (inicio, fin) in enumerate(extremos, start=1)
    )


@pytest.mark.parametrize("evento,respuesta", [
    ("la PFO 2", f"PFO 2: {_RANGOS[1]}."),
    ("la PFO2", f"PFO 2: {_RANGOS[1]}."),
    ("la Práctica de Formación Obligatoria 2", f"PFO 2: {_RANGOS[1]}."),
    ("la segunda PFO", f"PFO 2: {_RANGOS[1]}."),
    ("las PFO", "\n".join(f"PFO {i}: {rango}." for i, rango in enumerate(_RANGOS, start=1))),
])
def test_http_pfo_en_prosa_con_contexto_academico(flujo, evento, respuesta):
    flujo.contenido = cronograma_en_prosa()
    flujo.respuesta = respuesta
    r = preguntar(flujo, evento, plural=evento == "las PFO")
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    afirmar_contexto(r.json())
    assert r.json()["confianza"] == .9
    assert flujo.fuentes.call_args.args[0][0].contenido == flujo.contenido


@pytest.mark.parametrize("encabezados", [True, False])
def test_http_pfo_en_prosa_no_inventa_evento_faltante(flujo, encabezados):
    flujo.contenido = cronograma_en_prosa()
    if not encabezados:
        flujo.contenido = "\n".join(f for f in flujo.contenido.splitlines() if not f.startswith("PFO "))
    r = preguntar(flujo, "la PFO 5" if encabezados else "la PFO 2")
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["confianza"] == 0 and r.json()["fuentes"] == []
    flujo.llm.generar_con_respaldo.assert_not_called()

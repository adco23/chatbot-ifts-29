"""Estructura Semana/Fecha/Tema y resumen posterior a preservar headings DOCX."""

import json

import pytest

from app.models.chat import FuenteChat
from app.models.rag import DocumentoPersistido
from app.providers.base import RespuestaLLM
from app.services import conversacion
from app.services.eventos_cronograma import ErrorEvento, validar_eventos_mencionados
from app.services.postprocesador import ErrorPostproceso, validar_generacion
from tests.unit.services.test_eventos_plurales import fuente
from tests.unit.test_chat_eventos_plurales import flujo as flujo


PRIMERO = ("Semana 8\nFecha: Viernes 2 de octubre de 2026.\n"
           "Tema / actividad: Actividad evaluativa. Primer parcial de Administración de Base de Datos, comisión A.")
SEGUNDO = ("Semana 15\nFecha: Viernes 20 de noviembre de 2026.\n"
           "Tema / actividad: Actividad evaluativa. Segundo parcial de Administración de Base de Datos, comisión A.")
RECUPERATORIO = ("Semana 16\nFecha: Viernes 27 de noviembre de 2026.\n"
                 "Tema / actividad: Actividad evaluativa. Recuperatorio general de Administración de Base de Datos, comisión A.")
RESUMEN = ("Fechas de parciales:\nPrimer parcial: viernes 2 de octubre de 2026.\n"
           "Segundo parcial: viernes 20 de noviembre de 2026.\n"
           "Recuperatorio general: viernes 27 de noviembre de 2026.")
INDIVIDUALES = ("Primer parcial: viernes 2 de octubre de 2026.\n"
                "Segundo parcial: viernes 20 de noviembre de 2026.")
PREGUNTA = "¿Cuándo son los parciales de bases de datos de la comisión A?"
PFO = ("PFO 1: del 7 al 13 de septiembre de 2026.\n"
       "PFO 2: del 21 al 27 de septiembre de 2026.\n"
       "PFO 3: del 19 al 25 de octubre de 2026.\n"
       "PFO 4: del 9 al 15 de noviembre de 2026.")


def filas_reindexadas():
    datos = ((381, 9, PRIMERO, .778699232417392), (388, 16, SEGUNDO, .775),
             (389, 17, RECUPERATORIO, .76),
             (373, 1, "Cronograma de cursada\nMateria: Administración de Base de Datos. Comisión: A. Período: 2026C2.", .75),
             (395, 23, RESUMEN, .74))
    return [fuente(contenido, fragmento_id=identidad, documento_id=64, orden=orden, similitud=score)
            for identidad, orden, contenido, score in datos]


def validar(respuesta, *, consulta=PREGUNTA, filas=None, ids=None):
    filas = filas if filas is not None else filas_reindexadas()
    ids = ids if ids is not None else [381, 388, 389, 395]
    return validar_generacion(json.dumps({"respuesta": respuesta, "fragmento_ids": ids}), filas,
                              fragmento_ids_primarios=[f.fragmento_id for f in filas], consulta=consulta)


@pytest.fixture
def reindexado(flujo, monkeypatch):
    e = flujo
    e.filas = filas_reindexadas()
    e.ids = [381, 388, 389, 395]
    e.respuesta = INDIVIDUALES
    doc = DocumentoPersistido(**e.filas[0].model_dump(exclude={
        "fragmento_id", "documento_id", "contenido", "similitud", "orden",
    }), id=64, estado="activo")
    monkeypatch.setattr(conversacion.repositorio, "listar_documentos_chat", lambda: [doc])
    e.buscar.side_effect = lambda vector, **kwargs: [] if kwargs["modalidad"] == "complementaria" else e.filas
    e.llm.generar_con_respaldo.side_effect = lambda solicitud: RespuestaLLM(json.dumps({
        "respuesta": e.respuesta, "fragmento_ids": e.ids,
    }), "gemini", "simulado")
    e.fuentes.side_effect = lambda filas: [FuenteChat(documento_id=64, drive_id=doc.drive_id,
        titulo=doc.titulo, fragmento_ids=[f.fragmento_id for f in filas])]
    return e


def preguntar(e, evento="los parciales"):
    verbo = "son" if evento.startswith(("los", "las")) else "es"
    return e.http.post("/chat", headers={"X-Sesion-Id": "99999999-9999-4999-8999-999999999999"},
        json={"mensaje": f"¿Cuándo {verbo} {evento} de bases de datos de la comisión A?"})


@pytest.mark.parametrize("respuesta", [
    INDIVIDUALES,
    "Los parciales corresponden a la semana 8 y la semana 15.\n" + INDIVIDUALES,
    "Los parciales serán el 2 de octubre de 2026 y el 20 de noviembre de 2026.\n" + INDIVIDUALES,
    INDIVIDUALES + "\nRecuperatorio general: viernes 27 de noviembre de 2026.",
    "Fechas de parciales:\n" + INDIVIDUALES + "\nRecuperatorio general: viernes 27 de noviembre de 2026.",
    "Primer parcial: 2 de octubre de 2026 (semana 8).\nSegundo parcial: 20 de noviembre de 2026 (semana 15).",
    "Semana 8\nPrimer parcial: viernes 2 de octubre de 2026.\nSemana 15\nSegundo parcial: viernes 20 de noviembre de 2026.",
    "**Semana 8**\nPrimer parcial: viernes 2 de octubre de 2026.\n**Semana 15**\nSegundo parcial: viernes 20 de noviembre de 2026.",
])
def test_plural_reindexado_valida_individuales_antes_del_agregado(reindexado, respuesta):
    e = reindexado
    originales = [f.model_dump() for f in e.filas]
    e.respuesta = respuesta
    r = preguntar(e)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert "2 de octubre" in r.json()["respuesta"] and "20 de noviembre" in r.json()["respuesta"]
    assert r.json()["confianza"] == .778699232417392 and r.json()["umbral"] == .68
    assert r.json()["fuentes"][0]["documento_id"] == 64
    assert [f.model_dump() for f in e.filas] == originales
    e.llm.generar_con_respaldo.assert_awaited_once()


@pytest.mark.parametrize("evento,respuesta,identidad", [
    ("el primer parcial", "Primer parcial: 02/10/2026.", 381),
    ("el segundo parcial", "Segundo parcial: 20/11/2026.", 388),
    ("el recuperatorio", "Recuperatorio general: 27/11/2026.", 389),
])
def test_individuales_y_recuperatorio_siguen_estrictos(reindexado, evento, respuesta, identidad):
    e = reindexado
    e.respuesta, e.ids = respuesta, [identidad]
    r = preguntar(e, evento)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["fuentes"][0]["fragmento_ids"] == [identidad]


@pytest.mark.parametrize("evento,respuesta,identidad", [
    ("el primer parcial", "Primer parcial: 20/11/2026.", 381),
    ("el segundo parcial", "Segundo parcial: 02/10/2026.", 388),
    ("los parciales", "Primer parcial: 20/11/2026.\nSegundo parcial: 02/10/2026.", None),
    ("los parciales", "Primer parcial: 02/10/2026.\nSegundo parcial: 27/11/2026.", None),
])
def test_intercambiar_fechas_no_se_habilita_por_el_resumen(reindexado, evento, respuesta, identidad):
    e = reindexado
    e.respuesta = respuesta
    e.ids = [identidad] if identidad is not None else [381, 388, 389, 395]
    r = preguntar(e, evento)
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["fuentes"] == []
    e.fuentes.assert_not_called()


def test_plural_con_segunda_fecha_sin_fuente_no_inventa(reindexado):
    e = reindexado
    e.filas = [f for f in e.filas if f.fragmento_id in {381, 389, 373}]
    e.ids = [381, 389]
    r = preguntar(e)
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["fuentes"] == []


@pytest.mark.parametrize("respuesta", [
    "Primer parcial: viernes 2 de octubre de 2026.",
    "Los parciales serán el 2 de octubre de 2026 y el 20 de noviembre de 2026.",
    "Los parciales serán el 2 de octubre de 2026 y el 20 de noviembre de 2026.\nPrimer parcial: 2 de octubre de 2026.",
    "Primer y segundo parcial: 2 de octubre de 2026 y 20 de noviembre de 2026.",
])
def test_plural_no_acepta_fechas_sin_ambas_asociaciones_individuales(respuesta):
    with pytest.raises(ErrorPostproceso):
        validar(respuesta)


def test_tercer_parcial_inexistente_no_llama_llm(reindexado):
    r = preguntar(reindexado, "el tercer parcial")
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["confianza"] == 0
    reindexado.llm.generar_con_respaldo.assert_not_called()


@pytest.mark.parametrize("evento,respuesta,ids", [
    ("la PFO 2", PFO.splitlines()[1], [402]),
    ("las PFO", PFO, [401, 402, 403, 404]),
])
def test_pfo_singular_y_plural_conservan_identidad_y_rangos(reindexado, evento, respuesta, ids):
    e = reindexado
    e.filas = [fuente(contenido, documento_id=64, fragmento_id=401 + indice)
               for indice, contenido in enumerate(PFO.splitlines())]
    e.respuesta, e.ids = respuesta, ids
    r = preguntar(e, evento)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"


def test_pfo_5_inexistente_no_se_infiere_del_orden(reindexado):
    e = reindexado
    e.filas = [fuente(PFO, documento_id=64, fragmento_id=401)]
    r = preguntar(e, "la PFO 5")
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    e.llm.generar_con_respaldo.assert_not_called()


@pytest.mark.parametrize("orden", [False, True])
def test_validacion_no_depende_del_orden_de_fragmentos(orden):
    filas = filas_reindexadas()
    if orden:
        filas.reverse()
    validar("Los parciales corresponden a la semana 8 y la semana 15.\n" + INDIVIDUALES, filas=filas)


@pytest.mark.parametrize("agregado", [
    "Los parciales corresponden a la semana 16.",
    "Los parciales serán el 27 de noviembre de 2026.",
])
def test_recuperatorio_no_presta_fecha_ni_semana_al_agregado(agregado, caplog):
    respuesta = agregado + "\n" + INDIVIDUALES
    with pytest.raises(ErrorPostproceso):
        validar(respuesta)
    assert "agregado_no_respaldado_por_individuales" in caplog.text
    assert respuesta not in caplog.text and PRIMERO not in caplog.text and RESUMEN not in caplog.text


def test_agregado_no_convierte_inicio_de_semana_en_dia_de_examen():
    contenidos = ["Semana 8\nPrimer parcial\nInicia el 30/9.",
                  "Semana 15\nSegundo parcial\nInicia el 18/11."]
    individuales = ("Primer parcial: semana 8, que inicia el 30/9.\n"
                    "Segundo parcial: semana 15, que inicia el 18/11.")
    validar_eventos_mencionados(individuales, contenidos, consulta="¿Cuándo son los parciales?")
    with pytest.raises(ErrorEvento):
        validar_eventos_mencionados("Los parciales serán el 30/9 y el 18/11.\n" + individuales,
                                    contenidos, consulta="¿Cuándo son los parciales?")


def test_asociaciones_generalizan_a_otros_numeros_y_familias():
    contenidos = ["PFO 3: del 1 al 7 de marzo de 2028.", "PFO 8: del 15 al 21 de abril de 2028."]
    respuesta = "\n".join(contenidos)
    validar_eventos_mencionados(respuesta, contenidos, consulta="¿Cuándo son las PFO?")
    with pytest.raises(ErrorEvento):
        validar_eventos_mencionados("PFO 3: del 15 al 21 de abril de 2028.\nPFO 8: del 1 al 7 de marzo de 2028.",
                                    contenidos, consulta="¿Cuándo son las PFO?")


@pytest.mark.parametrize("respuesta", [
    "Semana 15\nPrimer parcial: viernes 2 de octubre de 2026.\nSemana 8\nSegundo parcial: viernes 20 de noviembre de 2026.",
    "Semana 8\nPrimer parcial: viernes 2 de octubre de 2026.\nSemana 15\nSegundo parcial: viernes 20 de noviembre de 2026.\nRecuperatorio general: viernes 27 de noviembre de 2026.",
])
def test_headings_no_permiten_asociar_semana_incorrecta_a_un_evento(respuesta):
    with pytest.raises(ErrorPostproceso):
        validar(respuesta)


def test_consulta_con_dos_identidades_explicitas_no_se_resuelve_con_una_sola():
    with pytest.raises(ErrorPostproceso):
        validar("Primer parcial: viernes 2 de octubre de 2026.",
                consulta="¿Cuándo son el primer y el segundo parcial?")

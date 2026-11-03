"""Mismas garantías en chat, carga, regeneración y FAQ HIT; sin red ni escrituras reales."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.db import faq as repo
from app.models.faq import FaqPendiente, FragmentoFuenteFaq, ResumenCargaFaq
from app.providers.base import RespuestaLLM
from app.providers.embeddings.base import ResultadoEmbedding
from app.services import conversacion, curador_faq, recuperador
from app.services.sanitizador import normalizar_comparacion
from app.settings import Configuracion
from tests.unit.services.test_cache_faq import coincidencia
from tests.unit.services.test_eventos_cronograma import PRIMERO, OTROS
from tests.unit.services.test_retrieval_academico import (
    entorno_academico as entorno_academico, http_academico as http_academico, fragmento, preguntar,
)

PREGUNTA = "¿Cuándo es el primer parcial de Técnicas de Programación comisión F?"
RESPUESTA = "El primer parcial corresponde a la semana 8, que inicia el 30 de septiembre."
ORIGINAL = ResultadoEmbedding([1., 0.] + [0.] * 766, "gemini", "gemini-embedding-001", 768)
DIRIGIDO = ResultadoEmbedding([0., 1.] + [0.] * 766, "gemini", "gemini-embedding-001", 768)


def como_fuente(f):
    return FragmentoFuenteFaq(**f.model_dump(exclude={"similitud"}),
                              estado_documento="activo", es_primaria=True,
                              version_documento={"indexado_en": "2026-11-01T00:00:00"})


@pytest.fixture
def mundo(entorno_academico, monkeypatch):
    e = entorno_academico
    e.filas[:] = [fragmento(11, documento_id=20, drive_id="cronograma-f", contenido=PRIMERO,
                          similitud=.67656797170639),
                 fragmento(13, documento_id=20, drive_id="cronograma-f", contenido=OTROS,
                          similitud=.68394923210144)]
    monkeypatch.setenv("RAG_UMBRAL_CRONOGRAMA", "0.68")
    async def embed(texto):
        return DIRIGIDO if texto == "Primer parcial: fecha, semana e inicio." else ORIGINAL
    e.embeddings.embed_query = AsyncMock(side_effect=embed)

    def buscar(vector, *, filtros, modalidad, top_k, documento_ids):
        if modalidad == "complementaria":
            return []
        return [f.model_copy(update={"similitud": .730975687503815})
                if f.fragmento_id == 11 and vector == DIRIGIDO.vector else f
                for f in e.filas if recuperador._es_primario(f, filtros, modalidad)][:top_k]
    buscar_rag = Mock(side_effect=buscar)
    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", buscar_rag)
    buscar_faq = Mock(return_value=None)
    contador = Mock()
    guardar = Mock(return_value=ResumenCargaFaq(id=2, creada=False))
    snapshot = Mock(side_effect=lambda ids: [como_fuente(f) for f in e.filas if f.fragmento_id in ids])
    monkeypatch.setattr(repo, "buscar_en_cache_faq", buscar_faq)
    monkeypatch.setattr(repo, "incrementar_acierto_cache_faq", contador)
    monkeypatch.setattr(repo, "guardar_faq_curada", guardar)
    monkeypatch.setattr(repo, "obtener_fuentes_para_guardar", snapshot)
    monkeypatch.setattr(curador_faq, "listar_materias_chat", conversacion.repositorio.listar_materias_chat)
    e.llm.generar_con_respaldo.return_value = RespuestaLLM(
        json.dumps({"respuesta": RESPUESTA, "fragmento_ids": [11]}), "groq", "simulado")
    return SimpleNamespace(e=e, buscar_rag=buscar_rag, buscar_faq=buscar_faq,
                           contador=contador, guardar=guardar, snapshot=snapshot)


def cargar(mundo):
    return asyncio.run(curador_faq.cargar_pregunta(
        curador_faq.EntradaFaq(pregunta_canonica=PREGUNTA, materia=47,
                              comision="F", periodo="2026C2"),
        configuracion=Configuracion(), gestor_embeddings=mundo.e.embeddings, gestor_llm=mundo.e.llm,
    ))


def test_chat_y_t41_seleccionan_evento_sin_persistir_vector_dirigido(http_academico, mundo):
    r = preguntar(http_academico, PREGUNTA, {"periodo": "2026C2"})
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["fuentes"][0]["fragmento_ids"] == [11]
    assert r.json()["confianza"] == .730975687503815
    assert cargar(mundo).id == 2
    faq, fuentes = mundo.guardar.call_args.args
    assert faq.embedding == ORIGINAL.vector and faq.embedding != DIRIGIDO.vector
    assert faq.pregunta_canonica == PREGUNTA
    assert faq.pregunta_normalizada == normalizar_comparacion(PREGUNTA)
    assert fuentes[0].fragmento_ids == [11]
    assert fuentes[0].version_documento == como_fuente(mundo.e.filas[0]).version_documento
    for llamada in mundo.e.llm.generar_con_respaldo.await_args_list:
        solicitud = llamada.args[0]
        assert solicitud.consulta == PREGUNTA
        contexto = json.loads(solicitud.contexto)
        assert len(contexto) == 1 and contexto[0]["fragmento_id"] == 11
        assert "Semana 8" in contexto[0]["contenido"]
        assert "23/9" not in contexto[0]["contenido"]
        assert "Semana 7" in mundo.e.filas[0].contenido  # Original completo conservado.


@pytest.mark.parametrize("contenido", [OTROS, "Segundo parcial: 18/11.", "Actividad pre parcial: 11/11.",
                                       "Recuperatorio del primer parcial: 25/11."])
def test_sin_evento_exacto_chat_abstiene_y_curador_no_guarda(http_academico, mundo, contenido):
    mundo.e.filas[:] = [fragmento(13, contenido=contenido, similitud=.99)]
    r = preguntar(http_academico, PREGUNTA, {"periodo": "2026C2"})
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["confianza"] == 0
    assert cargar(mundo) is None
    mundo.e.llm.generar_con_respaldo.assert_not_awaited()
    mundo.guardar.assert_not_called()
    mundo.snapshot.assert_not_called()


def test_t43_reutiliza_identidad_original_y_mismas_garantias(mundo, monkeypatch):
    monkeypatch.setattr(repo, "listar_faq_pendientes", Mock(return_value=[FaqPendiente(
        id=2, pregunta_canonica=PREGUNTA, embedding=ORIGINAL.vector,
        materia_id=47, comision="F", periodo="2026C2", tipo_documental="cronograma",
    )]))
    r = asyncio.run(curador_faq.regenerar_pendientes(
        configuracion=Configuracion(), gestor_embeddings=mundo.e.embeddings, gestor_llm=mundo.e.llm,
    ))
    assert r["regeneradas"] == 1 and r["fallidas"] == 0
    mundo.e.embeddings.embed_query.assert_awaited_once_with("Primer parcial: fecha, semana e inicio.")
    faq, fuentes = mundo.guardar.call_args.args
    assert faq.embedding == ORIGINAL.vector and faq.fecha_limite is not None
    assert fuentes[0].fragmento_ids == [11]


def test_t43_sin_evidencia_correcta_conserva_pendiente(mundo, monkeypatch):
    mundo.e.filas[:] = [mundo.e.filas[1]]
    monkeypatch.setattr(repo, "listar_faq_pendientes", Mock(return_value=[FaqPendiente(
        id=2, pregunta_canonica=PREGUNTA, embedding=ORIGINAL.vector,
        materia_id=47, comision="F", periodo="2026C2", tipo_documental="cronograma",
    )]))
    r = asyncio.run(curador_faq.regenerar_pendientes(
        configuracion=Configuracion(), gestor_embeddings=mundo.e.embeddings, gestor_llm=mundo.e.llm,
    ))
    assert r["regeneradas"] == 0 and r["fallidas"] == 1
    mundo.guardar.assert_not_called()
    mundo.e.llm.generar_con_respaldo.assert_not_awaited()


def test_faq_antigua_con_preparcial_es_miss_sin_acierto(http_academico, mundo, monkeypatch):
    mundo.buscar_faq.return_value = coincidencia(
        id=2, pregunta_canonica=PREGUNTA,
        respuesta="El primer parcial será el 11 de noviembre.", materia_id=47,
        comision="F", periodo="2026C2", tipo_documental="cronograma",
    )
    monkeypatch.setattr(repo, "obtener_evidencia_faq", Mock(return_value=[como_fuente(mundo.e.filas[1])]))
    r = preguntar(http_academico, PREGUNTA, {"periodo": "2026C2"})
    assert r.status_code == 200 and r.json()["respuesta"] == RESPUESTA
    assert r.json()["fuentes"][0]["fragmento_ids"] == [11]
    mundo.contador.assert_not_called()
    mundo.e.llm.generar_con_respaldo.assert_awaited_once()


def test_faq_hit_correcto_conserva_precision_y_no_usa_llm(http_academico, mundo, monkeypatch):
    mundo.buscar_faq.return_value = coincidencia(
        id=2, pregunta_canonica=PREGUNTA, respuesta=RESPUESTA, materia_id=47,
        comision="F", periodo="2026C2", tipo_documental="cronograma",
    )
    monkeypatch.setattr(repo, "obtener_evidencia_faq", Mock(return_value=[como_fuente(mundo.e.filas[0])]))
    r = preguntar(http_academico, PREGUNTA, {"periodo": "2026C2"})
    assert r.status_code == 200 and r.json()["respuesta"] == RESPUESTA
    mundo.contador.assert_called_once_with(2)
    mundo.buscar_rag.assert_not_called()
    mundo.e.llm.generar_con_respaldo.assert_not_awaited()
    mundo.e.embeddings.embed_query.assert_awaited_once_with(PREGUNTA)


def test_hit_con_fecha_exacta_inferida_de_semana_es_miss(http_academico, mundo, monkeypatch):
    mundo.buscar_faq.return_value = coincidencia(
        id=2, pregunta_canonica=PREGUNTA, respuesta="El primer parcial será el 30 de septiembre.",
        materia_id=47, comision="F", periodo="2026C2", tipo_documental="cronograma",
    )
    monkeypatch.setattr(repo, "obtener_evidencia_faq", Mock(return_value=[como_fuente(mundo.e.filas[0])]))
    r = preguntar(http_academico, PREGUNTA, {"periodo": "2026C2"})
    assert r.status_code == 200 and r.json()["respuesta"] == RESPUESTA
    mundo.contador.assert_not_called()


def test_recuperatorio_asociado_requiere_esa_asociacion_explicita(http_academico, mundo):
    q = "¿Cuándo es el recuperatorio del primer parcial de Técnicas de Programación comisión F?"
    mundo.e.filas[:] = [fragmento(13, contenido=OTROS, similitud=.99)]
    r = preguntar(http_academico, q, {"periodo": "2026C2"})
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    mundo.e.llm.generar_con_respaldo.assert_not_awaited()
    mundo.e.filas[:] = [fragmento(13, similitud=.9, contenido=(
        "Semana 10\nInicia el 14/10\nRecuperatorio del primer parcial."
    ))]
    mundo.e.llm.generar_con_respaldo.return_value = RespuestaLLM(json.dumps({
        "respuesta": "Corresponde a la semana 10, que inicia el 14/10.", "fragmento_ids": [13],
    }), "groq", "simulado")
    r = preguntar(http_academico, q, {"periodo": "2026C2"})
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["fuentes"][0]["fragmento_ids"] == [13]


@pytest.mark.parametrize("respuesta", [
    "El primer parcial será el 30/9.",
    "El primer parcial (actividad pre-parcial) corresponde a la semana 8, que inicia el 30/9.",
    "El primer parcial corresponde a la semana 7, que inicia el 23/9.",
])
def test_llm_no_puede_renombrar_ni_tomar_fecha_de_otra_semana(http_academico, mundo, respuesta):
    mundo.e.llm.generar_con_respaldo.return_value = RespuestaLLM(json.dumps({
        "respuesta": respuesta, "fragmento_ids": [11],
    }), "groq", "simulado")
    r = preguntar(http_academico, PREGUNTA, {"periodo": "2026C2"})
    assert r.status_code == 503 and respuesta not in r.text
    with pytest.raises(ValueError):
        cargar(mundo)
    mundo.guardar.assert_not_called()


@pytest.mark.parametrize("evento,semana,fecha", [
    ("segundo parcial", 15, "18/11"),
    ("recuperatorios", 16, "25/11"),
    ("actividad pre-parcial", 14, "11/11"),
])
def test_otros_eventos_usan_su_propia_evidencia(http_academico, mundo, evento, semana, fecha):
    mundo.e.filas[:] = [mundo.e.filas[1].model_copy(update={"similitud": .9})]
    mundo.e.llm.generar_con_respaldo.return_value = RespuestaLLM(json.dumps({
        "respuesta": f"Corresponde a la semana {semana}, que inicia el {fecha}.", "fragmento_ids": [13],
    }), "groq", "simulado")
    r = preguntar(http_academico, f"¿Cuándo es {evento} de Técnicas de Programación comisión F?",
                  {"periodo": "2026C2"})
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    enviado = json.loads(mundo.e.llm.generar_con_respaldo.call_args.args[0].contexto)[0]["contenido"]
    assert f"Semana {semana}" in enviado and fecha in enviado

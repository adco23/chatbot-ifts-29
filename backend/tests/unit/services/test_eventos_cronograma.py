"""Identidad del evento, asociación temporal y recuperación sin servicios reales."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.rag import FiltrosRecuperacion
from app.providers.embeddings.base import ResultadoEmbedding
from app.services import recuperador
from app.services.eventos_cronograma import (
    ErrorEvento, EventoCronograma, bloques_evento, consulta_dirigida, contexto_evento,
    eventos_en_texto, identificar_evento, respalda_evento, validar_respuesta_evento,
    validar_eventos_mencionados,
)
from app.services.postprocesador import ErrorPostproceso, validar_generacion
from app.settings import Configuracion
from app.services.validacion_embeddings_rag import ErrorEmbeddingIncompatible
from tests.unit.services.test_retrieval_academico import fragmento

PRIMERO = """Semana 7
Inicia el 23/9
Actividad pre parcial
Semana 8
Inicia el 30/9
Primer parcial
Semana 9
Inicia el 7/10
Arreglos"""
OTROS = """Semana 14
Inicia el 11/11
Actividad pre parcial
Semana 15
Inicia el 18/11
Segundo parcial
Semana 16
Inicia el 25/11
Recuperatorios"""
EVENTO = EventoCronograma("parcial", 1)


@pytest.mark.parametrize("texto,evento", [
    ("primer parcial", EVENTO), ("primero parcial", EVENTO), ("1er parcial", EVENTO),
    ("1° parcial", EVENTO), ("1º parcial", EVENTO), ("1.er parcial", EVENTO),
    ("parcial 1", EVENTO), ("parcial n°1", EVENTO), ("I parcial", EVENTO),
    ("segundo parcial", EventoCronograma("parcial", 2)),
    ("2do parcial", EventoCronograma("parcial", 2)), ("2º parcial", EventoCronograma("parcial", 2)),
    ("parcial II", EventoCronograma("parcial", 2)),
    ("tercer parcial", EventoCronograma("parcial", 3)),
    ("actividad pre-parcial", EventoCronograma("preparcial")),
    ("pre parcial", EventoCronograma("preparcial")), ("preparcial", EventoCronograma("preparcial")),
    ("actividad pre–parcial", EventoCronograma("preparcial")),
    ("actividad pre‑parcial", EventoCronograma("preparcial")),
    ("recuperatorio", EventoCronograma("recuperatorio")),
    ("recuperatorios", EventoCronograma("recuperatorio")),
    ("recuperatorio del primer parcial", EventoCronograma("recuperatorio", 1)),
    ("recuperatorio del 2do parcial", EventoCronograma("recuperatorio", 2)),
    ("recuperatorio del parcial 1", EventoCronograma("recuperatorio", 1)),
    ("Primer parcial - recuperatorio", EventoCronograma("recuperatorio", 1)),
    ("actividad pre parcial del segundo parcial", EventoCronograma("preparcial", 2)),
])
def test_identidad_de_eventos_reutilizable(texto, evento):
    assert identificar_evento(f"¿Cuándo es {texto} de otra materia comisión Z?") == evento
    assert eventos_en_texto(texto) == (evento,)


@pytest.mark.parametrize("texto", ["horarios de clases", "cronograma", "parciales", "parcial",
                                    "primer parcial y segundo parcial", "repartición"])
def test_consulta_general_no_fuerza_un_evento(texto):
    assert identificar_evento(texto) is None


def test_primero_preparcial_y_recuperatorio_asociado_son_distintos():
    assert len({EVENTO, identificar_evento("pre parcial"),
                identificar_evento("recuperatorio del primer parcial")}) == 3
    assert not respalda_evento("Recuperatorio del primer parcial: 20/10.", EVENTO)
    assert not respalda_evento("Primer parcial: 30/9.", EventoCronograma("recuperatorio", 1))
    assert not respalda_evento("Recuperatorios: 25/11.", EventoCronograma("recuperatorio", 1))


def test_bloques_asocian_evento_fecha_y_semana_sin_modificar_fragmento():
    bloques = bloques_evento(PRIMERO, EVENTO)
    assert len(bloques) == 1 and bloques[0].semana == 8 and bloques[0].inicio_semana
    assert "30/9" in bloques[0].texto and "23/9" not in bloques[0].texto
    assert "Actividad pre" not in contexto_evento(PRIMERO, EVENTO)
    assert not respalda_evento(OTROS, EVENTO)
    assert PRIMERO.startswith("Semana 7")


@pytest.mark.parametrize("evento,fecha,semana", [
    (EventoCronograma("parcial", 2), "18/11", 15),
    (EventoCronograma("recuperatorio"), "25/11", 16),
    (EventoCronograma("preparcial"), "11/11", 14),
])
def test_cada_evento_conserva_su_bloque(evento, fecha, semana):
    b = bloques_evento(OTROS, evento)
    assert len(b) == 1 and b[0].semana == semana and fecha in b[0].texto
    validar_respuesta_evento(
        f"Corresponde a la semana {semana}, que inicia el {fecha}.", [OTROS], evento,
    )


@pytest.mark.parametrize("respuesta", [
    "El primer parcial corresponde a la semana 8, que inicia el 30 de septiembre.",
    "Está previsto para la semana 8 (inicio: 30/9).",
    "Corresponde a la octava semana.",
    "Corresponde a la semana que inicia el 30/9.",
])
def test_parafrasis_no_necesita_copia_literal_ni_repetir_evento(respuesta):
    validar_respuesta_evento(respuesta, [PRIMERO], EVENTO)


@pytest.mark.parametrize("respuesta", [
    "El primer parcial será el 30 de septiembre.",
    "El primer parcial se toma el 30/9 en la semana 8.",
    "La semana 8 inicia el 30/9. El parcial será ese día.",
    "La semana 8 inicia el 30/9. El examen es el 30/9.",
    "La semana 8 inicia el 30/9 y el parcial será el 30/9.",
    "El primer parcial corresponde a la semana 14, que inicia el 30/9.",
    "El primer parcial corresponde a la semana 8, que inicia el 23/9.",
    "El primer parcial (actividad pre-parcial) corresponde a la semana 8.",
    "El segundo parcial corresponde a la semana 8, que inicia el 30/9.",
])
def test_rechaza_renombrado_fechas_de_otro_bloque_y_precision_inventada(respuesta):
    with pytest.raises(ErrorEvento):
        validar_respuesta_evento(respuesta, [PRIMERO], EVENTO)


def test_dia_exacto_se_permite_si_la_fuente_si_lo_indica():
    validar_respuesta_evento("El primer parcial será el 30/9.",
                            ["Semana 8\nPrimer parcial: 30/9."], EVENTO)
    validar_respuesta_evento("El primer parcial inicia el 30/9.",
                            ["Semana 8\nEl primer parcial inicia el 30/9."], EVENTO)


def test_pregunta_general_puede_enumerar_eventos_con_fechas_correctas():
    fuente = "Primer parcial: 30/9. Segundo parcial: 18/11."
    validar_eventos_mencionados("El primer parcial será el 30/9. El segundo parcial será el 18/11.",
                                [fuente])
    with pytest.raises(ErrorEvento):
        validar_eventos_mencionados("El primer parcial será el 18/11.", [fuente])
    validar_eventos_mencionados(
        "El primer parcial corresponde a la semana 8, que inicia el 30/9.", [PRIMERO],
    )


def test_inicia_sin_actor_dentro_de_semana_no_fecha_examen():
    with pytest.raises(ErrorEvento):
        validar_respuesta_evento("El primer parcial será el 30/9.",
                                ["Semana 8. Primer parcial. Inicia el 30/9."], EVENTO)


def test_evento_al_inicio_del_fragmento_no_se_pierde_por_encabezado_posterior():
    assert respalda_evento("Primer parcial: 30/9.\nSemana 9\nArreglos", EVENTO)


def test_dos_eventos_en_un_fragmento_no_comparten_sus_fechas():
    texto = "Primer parcial: 30/9. Segundo parcial: 18/11."
    assert "18/11" not in contexto_evento(texto, EVENTO)
    with pytest.raises(ErrorEvento):
        validar_respuesta_evento("El primer parcial será el 18/11.", [texto], EVENTO)


def test_fuente_ambigua_no_presta_fecha_de_otro_evento():
    assert not respalda_evento("Primer parcial y segundo parcial: 30/9 y 18/11.", EVENTO)


def test_postprocesador_rechaza_renombrado_aunque_fecha_exista():
    fuente = fragmento(contenido=OTROS)
    with pytest.raises(ErrorPostproceso, match="evento_no_respaldado"):
        validar_generacion(json.dumps({
            "respuesta": "El primer parcial será el 11 de noviembre.", "fragmento_ids": [1],
        }), [fuente], fragmento_ids_primarios=[1], consulta="¿Cuándo es el primer parcial?")


def _embedding(dirigido=False):
    return ResultadoEmbedding(([0., 1.] if dirigido else [1., 0.]) + [0.] * 766,
                              "gemini", "gemini-embedding-001", 768)


def _recuperar(monkeypatch, primero, segundo=None, consulta="¿Cuándo es el primer parcial?"):
    emb = SimpleNamespace(embed_query=AsyncMock(side_effect=[_embedding(), _embedding(True)]))
    llamadas = []
    def buscar(vector, **kwargs):
        llamadas.append((vector, kwargs))
        if kwargs["modalidad"] == "complementaria":
            return []
        return primero if vector[0] == 1 else (segundo if segundo is not None else primero)
    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", buscar)
    r = asyncio.run(recuperador.recuperar_fragmentos(
        consulta, filtros=FiltrosRecuperacion(materia_id=47, comision="F", periodo="2026C2",
                                            tipo_documental="cronograma"),
        gestor=emb, configuracion=Configuracion(rag_umbral_cronograma=.68),
    ))
    return r, emb, llamadas


def test_consulta_dirigida_es_secundaria_y_no_transfiere_scores(monkeypatch):
    primero = fragmento(11, contenido=PRIMERO, similitud=.67656797170639)
    otro = fragmento(13, contenido=OTROS, similitud=.99)
    actualizado = primero.model_copy(update={"similitud": .730975687503815})
    r, emb, llamadas = _recuperar(monkeypatch, [otro, primero], [otro, actualizado])
    assert r.evidencia_suficiente and r.confianza == actualizado.similitud
    assert r.fragmento_ids_primarios == [11] and r.fragmentos[0] is actualizado
    assert [x.args[0] for x in emb.embed_query.await_args_list] == [
        "¿Cuándo es el primer parcial?", consulta_dirigida(EVENTO),
    ]
    assert llamadas[0][1]["top_k"] == llamadas[1][1]["top_k"] == 20
    assert llamadas[0][1]["filtros"] == llamadas[1][1]["filtros"]
    assert primero.similitud == .67656797170639 and primero.contenido == PRIMERO


@pytest.mark.parametrize("contenido", [OTROS, "Segundo parcial: 18/11.", "Actividad pre parcial: 11/11."])
def test_evento_distinto_no_da_confianza_aunque_score_sea_alto(monkeypatch, contenido):
    r, emb, _ = _recuperar(monkeypatch, [fragmento(contenido=contenido, similitud=.99)])
    assert not r.evidencia_suficiente and r.confianza == 0 and r.fragmento_ids_primarios == []
    assert emb.embed_query.await_count == 2


def test_evento_correcto_bajo_umbral_no_toma_score_del_incorrecto(monkeypatch):
    r, _, _ = _recuperar(monkeypatch, [fragmento(11, contenido=PRIMERO, similitud=.67),
                                      fragmento(13, contenido=OTROS, similitud=.99)])
    assert r.confianza == .67 and not r.evidencia_suficiente


def test_primera_recuperacion_suficiente_no_vectoriza_consulta_dirigida(monkeypatch):
    r, emb, _ = _recuperar(monkeypatch, [fragmento(contenido=PRIMERO, similitud=.8)])
    assert r.evidencia_suficiente
    emb.embed_query.assert_awaited_once_with("¿Cuándo es el primer parcial?")


def test_cronograma_general_conserva_su_recorrido(monkeypatch):
    r, emb, llamadas = _recuperar(monkeypatch, [fragmento()], consulta="¿Cuál es el cronograma?")
    assert r.evidencia_suficiente and llamadas[0][1]["top_k"] == 5
    emb.embed_query.assert_awaited_once()


def test_embedding_dirigido_incompatible_no_se_consulta_en_pgvector(monkeypatch):
    gestor = SimpleNamespace(embed_query=AsyncMock(side_effect=[
        _embedding(), ResultadoEmbedding([1.] + [0.] * 767, "cloudflare", "otro", 768),
    ]))
    buscar = Mock(return_value=[fragmento(contenido=OTROS)])
    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", buscar)
    with pytest.raises(ErrorEmbeddingIncompatible):
        asyncio.run(recuperador.recuperar_fragmentos(
            "¿Cuándo es el primer parcial?", gestor=gestor, configuracion=Configuracion(),
            filtros=FiltrosRecuperacion(materia_id=47, comision="F", tipo_documental="cronograma"),
        ))
    buscar.assert_called_once()

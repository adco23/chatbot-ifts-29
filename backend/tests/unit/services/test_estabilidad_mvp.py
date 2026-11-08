"""Estabilización local: saludos, alcance, administración y contexto del chat."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.chat import ContextoChat, MateriaChat, SolicitudChat
from app.services import conversacion
from app.services.intencion_documental import (
    es_consulta_administrativa,
    es_consulta_institucional,
    requiere_materia_por_consulta,
)
from app.services.respuestas_locales import ALCANCE, SALUDO, respuesta_local
from app.settings import Configuracion
from tests.unit.services.test_conversacion import entorno as entorno, recuperacion


@pytest.mark.parametrize("texto", [
    "Hola", "HOLA!", "¡Hola!", "Buenas tardes", "Buenos días.",
    "Hola, ¿en qué me podés ayudar?", "¿En qué me puedes ayudar?",
    "¿Qué podés hacer?", "¿QUÉ PUEDES HACER?", "¿Para qué sirve este asistente?",
])
def test_saludo_y_descubrimiento_solo_mensajes_completos(texto):
    assert respuesta_local(texto) == SALUDO


@pytest.mark.parametrize("texto", [
    "Dame la receta del chipá", "DAME LA RECETA DEL CHIPA",
    "¿Cómo preparo la receta de una tortilla?", "Quiero una receta de empanadas.",
])
def test_alcance_culinario_inequivoco(texto):
    assert respuesta_local(texto) == ALCANCE


@pytest.mark.parametrize("texto", [
    "Hola, quiero consultar el horario de Técnicas de Programación",
    "¿Qué podés hacer para explicar el programa de Lógica?",
    "¿Cómo hago una inscripción?", "¿Cómo preparo un examen?",
    "Dame una receta para estudiar programación", "Dame una receta de SQL",
    "Dame una receta para preparar el examen", "Dame una receta para las clases",
    "Dame una receta para Bedelía", "¿Cuánto gana Messi?", "Tengo otra consulta",
    "Dame una receta para aprender Base de Datos", "Dame una receta para cálculo",
    "Dame una receta para análisis matemático", "Dame una receta para SQL y Lógica",
    "¿Cuándo abre la inscripción?\nAclaración: Hola",
    "Consulta académica\nAclaración: Dame la receta del chipá",
    "[DNI_OCULTO] ¿Cuáles son las materias del primer cuatrimestre?",
])
def test_ambiguedad_o_consulta_academica_no_se_intercepta(texto):
    assert respuesta_local(texto) is None


@pytest.mark.parametrize("texto", [
    "Quiero saber los horarios de Bedelía", "¿Cómo me contacto con Bedelía?",
    "Quiero el certificado de asistencia a un examen parcial",
    "Solicitar una constancia de alumno regular", "Necesito información de un trámite",
])
def test_alcance_administrativo_prima_sobre_palabras_academicas(texto):
    assert es_consulta_administrativa(texto)
    assert es_consulta_institucional(texto)
    assert not requiere_materia_por_consulta(texto)


def test_cronograma_academico_no_se_convierte_en_tramite():
    texto = "Quiero el cronograma de Análisis Matemático"
    assert not es_consulta_administrativa(texto)
    assert not es_consulta_institucional(texto)
    assert requiere_materia_por_consulta(texto)


def test_mencionar_sector_no_convierte_un_cronograma_en_horario_de_oficina():
    texto = "Quiero el cronograma de Análisis Matemático publicado por Bedelía"
    assert not es_consulta_administrativa(texto)
    assert requiere_materia_por_consulta(texto)


def test_listado_general_de_materias_es_institucional():
    assert es_consulta_institucional("¿Cuáles son las materias del primer cuatrimestre?")
    assert es_consulta_institucional("¿Qué materias tiene la carrera?")


def test_nueva_materia_explicita_reemplaza_anterior_y_conserva_otros_campos():
    materia = MateriaChat(id=93, codigo="LC", nombre="Lógica Computacional")
    previo = ContextoChat(materia_id=82, consulta_pendiente="Consulta anterior")
    nuevo = conversacion._resolver_contexto(previo, "Lógica, comisión F en 2026C2", [materia])
    assert nuevo.materia_id == 93 and nuevo.comision == "F" and nuevo.periodo == "2026C2"
    assert nuevo.consulta_pendiente == "Consulta anterior"
    assert previo.comision is None


@pytest.fixture
def materias_contexto():
    return [
        MateriaChat(id=1, codigo="TP", nombre="Técnicas de Programación"),
        MateriaChat(id=2, codigo="BD", nombre="Administración de Base de Datos"),
    ]


def test_cambio_explicito_de_base_de_datos_a_tecnicas(materias_contexto):
    anterior = ContextoChat(materia_id=2, comision="A", periodo="2026C2")
    nuevo = conversacion._resolver_contexto(
        anterior, "¿Cuándo son los parciales de Técnicas de Programación comisión A?",
        materias_contexto,
    )
    assert nuevo.materia_id == 1 and nuevo.comision == "A" and nuevo.periodo == "2026C2"
    assert anterior.materia_id == 2


def test_aclaracion_b_conserva_materia_y_consulta_pendiente(materias_contexto):
    pendiente = "¿Cuándo son los parciales de Base de Datos?"
    anterior = ContextoChat(materia_id=2, periodo="2026C2", consulta_pendiente=pendiente)
    solicitud = SolicitudChat(mensaje="B", contexto=anterior)
    nuevo = conversacion._resolver_contexto(
        anterior, conversacion._texto_consulta(solicitud), materias_contexto,
    )
    assert nuevo.materia_id == 2 and nuevo.comision == "B" and nuevo.periodo == "2026C2"
    assert nuevo.consulta_pendiente == pendiente


def test_materia_de_ultima_intervencion_no_se_confunde_con_la_pendiente(materias_contexto):
    anterior = ContextoChat(materia_id=2, consulta_pendiente="Parciales de Base de Datos")
    solicitud = SolicitudChat(mensaje="Técnicas de Programación, comisión A", contexto=anterior)
    nuevo = conversacion._resolver_contexto(
        anterior, conversacion._texto_consulta(solicitud), materias_contexto,
    )
    assert nuevo.materia_id == 1 and nuevo.comision == "A"


def test_captura_documental_no_usa_atajos_ni_registro_t40(entorno, monkeypatch):
    entorno.documentos.return_value = []
    entorno.rag.return_value = recuperacion([])
    directo = Mock(side_effect=AssertionError("No usar directorio en curación."))
    registrar = Mock(side_effect=AssertionError("No registrar métricas en curación."))
    faq = AsyncMock(side_effect=AssertionError("La curación no busca caché."))
    monkeypatch.setattr(conversacion.derivacion, "resolver_contacto_directo", directo)
    monkeypatch.setattr(conversacion.repositorio_derivaciones,
                        "registrar_derivacion_anonima", registrar)
    monkeypatch.setattr(conversacion, "buscar_faq", faq)
    resultado = asyncio.run(conversacion.generar_documental(
        SolicitudChat(mensaje="¿Cómo me contacto con Bedelía?"),
        configuracion=Configuracion(), gestor_embeddings=entorno.embeddings,
        gestor_llm=entorno.llm,
    ))
    assert resultado is None
    directo.assert_not_called()
    registrar.assert_not_called()
    faq.assert_not_called()

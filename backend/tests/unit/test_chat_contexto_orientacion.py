"""Comisiones explícitas y orientación de ingreso; HTTP sin servicios externos."""

import pytest

from app.models.chat import ContextoChat, MateriaChat
from app.services.comisiones import CatalogoComisiones
from app.services.conversacion import _resolver_contexto
from tests.unit.test_chat_cierre_mvp import (
    agregar, cierre as cierre, documento, fragmento, http as http,
    periodo_activo, preguntar,
)


@pytest.mark.parametrize("consulta,esperada", [
    ("comisión A", "A"), ("comision B", "B"), ("soy de la C", "C"),
    ("curso en la comisión D", "D"), ("mi comisión es la b", "B"),
    ("Comisión: a", "A"), ("pertenezco a la D", "D"),
    ("¿Cómo sé a qué comisión pertenezco?", None),
    ("¿Cuál es mi comisión?", None), ("¿Qué comisión me corresponde?", None),
    ("¿Cómo veo la comisión asignada?", None), ("Comisión de Base de Datos", None),
    ("La comisión a la que pertenezco", None), ("comisión anterior", None),
    ("comisión ACADEMICA", None),
])
def test_extractor_solo_reconoce_expresiones_de_comision(consulta, esperada):
    contexto = _resolver_contexto(ContextoChat(), consulta, [])
    assert contexto.comision == esperada


@pytest.mark.parametrize("aclaracion", ["A", "comisión a", "soy de la A"])
def test_aclaracion_sustituye_comision_anterior_sin_recuperar_datos(aclaracion):
    anterior = ContextoChat(consulta_pendiente="Cronograma de la comisión F")
    texto = f"{anterior.consulta_pendiente}\nAclaración: {aclaracion}"
    contexto = _resolver_contexto(anterior, texto, [])
    assert contexto.comision == "A" and anterior.comision is None


def test_codigos_del_catalogo_se_resuelven_sin_cambiar_la_gramatica():
    catalogo = CatalogoComisiones("2026C2", {"2026C2": ("A", "E2")})
    contexto = _resolver_contexto(ContextoChat(), "Curso en la comisión E2", [], catalogo=catalogo)
    assert contexto.comision == "E2" and catalogo.admite(contexto.comision)


def test_pregunta_sobre_comision_no_inventa_valor_en_http(http, cierre):
    doc = documento(50)
    cierre.documentos.append(doc)
    cierre.filas.insert(0, fragmento(doc,
        "Para conocer tu comisión, verificá la información en la plataforma institucional.",
        similitud=.95))
    r = preguntar(http, "¿Cómo sé a qué comisión pertenezco?")
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["contexto"]["comision"] is None
    assert r.json()["campo_requerido"] is None and "PERTENEZCO" not in r.text
    assert cierre.buscar.call_args.kwargs["filtros"].comision is None


@pytest.mark.parametrize("consulta", [
    "¿Qué tengo que hacer el primer día de clases?",
    "¿Qué debo llevar el primer día de clases?",
    "¿Cómo prepararme para el primer día de clases?",
])
def test_orientacion_primer_dia_usa_documentacion_general(http, cierre, consulta):
    doc = documento(50)
    cierre.documentos.append(doc)
    cierre.filas.insert(0, fragmento(doc,
        "Para el primer día de clases, llevá elementos para tomar apuntes.", similitud=.95))
    r = preguntar(http, consulta)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["contexto"] == {
        "materia_id": None, "comision": None, "periodo": None, "consulta_pendiente": None,
    }
    assert r.json()["fuentes"][0]["documento_id"] == doc.id
    filtros = cierre.buscar.call_args.kwargs["filtros"]
    assert filtros.tipo_documental is None and filtros.materia_id is None
    cierre.faq.assert_called_once()
    cierre.llm.generar_con_respaldo.assert_awaited_once()


@pytest.mark.parametrize("consulta", [
    "¿Cuándo es la primera clase de Base de Datos de la comisión A?",
    "¿Qué tengo que hacer el primer día de clases de Base de Datos de la comisión A?",
])
def test_primera_clase_de_materia_conserva_contexto_academico(http, cierre, consulta):
    cierre.materias.return_value.append(MateriaChat(
        id=2, codigo="ABD", nombre="Administración de Base de Datos"))
    doc = agregar(cierre, materia_id=2)
    r = preguntar(http, consulta)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    contexto = r.json()["contexto"]
    assert (contexto["materia_id"], contexto["comision"], contexto["periodo"]) == (2, "A", periodo_activo())
    assert r.json()["fuentes"][0]["documento_id"] == doc.id
    assert cierre.buscar.call_args_list[0].kwargs["filtros"].tipo_documental == "cronograma"


def test_orientacion_con_contexto_academico_explicito_lo_respeta(http, cierre):
    agregar(cierre)
    r = preguntar(http, "¿Qué tengo que hacer el primer día de clases?",
                  {"materia_id": 3, "comision": "A"})
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["contexto"]["materia_id"] == 3
    assert cierre.buscar.call_args_list[0].kwargs["filtros"].tipo_documental == "cronograma"

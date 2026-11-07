"""Resolución única contra catálogo y regresiones de aliases demasiado amplios."""

import logging

import pytest

from app.models.chat import MateriaChat
from app.services.sinonimos import (
    limpiar_texto,
    nombres_mencionados,
    normalizar_y_buscar_materia,
    resolver_materia,
)


@pytest.fixture
def catalogo():
    return [
        MateriaChat(id=103, codigo="TP", nombre="Técnicas de Programación"),
        MateriaChat(id=211, codigo="BDD", nombre="Administración de Base de Datos"),
        MateriaChat(id=307, codigo="AM", nombre="Elementos de Análisis Matemático"),
        MateriaChat(id=419, codigo="LC", nombre="Lógica Computacional"),
    ]


@pytest.mark.parametrize("texto,indice", [
    ("Técnicas de Programación", 0), ("horario de técnicas", 0),
    ("Necesito información de Programación", 0), ("Contenidos de tec prog", 0),
    ("Contenidos de tec. de prog", 0), ("PROGRAMACIÓN 1", 0),
    ("Administración de Base de Datos", 1), ("Necesito información de Base de Datos", 1),
    ("Programa de admin de datos", 1), ("¿Qué temas tiene bd?", 1), ("Consulta de SQL", 1),
    ("Administracion de Base de Datos", 1), ("Administración de Bases de Datos", 1),
    ("Administracion de Bases de Datos", 1), ("Bases de Datos", 1), ("ABD", 1),
    ("Elementos de Análisis Matemático", 2),
    ("Quiero el cronograma de Análisis Matemático", 2), ("programa de análisis", 2),
    ("Programa de matemática", 2), ("Contenidos de cálculo", 2),
    ("Lógica Computacional", 3), ("Tengo una consulta de Lógica", 3),
    ("Contenidos de logica comp", 3),
    ("  CRONOGRAMA\u00a0DE\nANÁLISIS  MATEMÁTICO  ", 2),
    ("(Base de Datos), por favor", 1), ("TP, comisión F", 0), ("Código BDD", 1),
])
def test_nombres_aliases_codigos_y_normalizacion(catalogo, texto, indice):
    encontrada = resolver_materia(texto, catalogo)
    assert encontrada is catalogo[indice]


@pytest.mark.parametrize("texto", [
    "¿Cuáles son las materias del primer cuatrimestre?", "Quiero saber mis materias",
    "¿Con cuánto promociono una materia?", "Los materiales están publicados",
    "Estoy pensando matemáticamente", "Técnicamente es posible", "Hay varias programaciones",
    "Tengo mis datos personales", "Bases institucionales", "Pensamiento computacional",
    "programaciones", "analisisadicional", "NoSQL", "superlogica", "subTP", "TPextra",
    "¿Puedo tomar mate?", "Un mate antes del parcial", "Después del parcial voy a tomar mate",
    "mate", "Elementos del programa institucional", "", "\t ",
    "base", "administración", "datos personales", "subABD", "ABDatos", "superBD",
])
def test_no_infiere_materias_por_subcadenas_o_terminos_genericos(catalogo, texto):
    assert resolver_materia(texto, catalogo) is None
    assert normalizar_y_buscar_materia(texto) is None


@pytest.mark.parametrize("texto", [
    "cronograma de mate", "horario de mate", "materia mate", "materia de mate",
    "examen de mate", "programa de mate", "parciales de mate",
])
def test_mate_solo_en_construccion_academica_explicita(catalogo, texto):
    assert resolver_materia(texto, catalogo) is catalogo[2]


@pytest.mark.parametrize("texto", [
    "Base de Datos y Lógica", "Programación y cálculo", "TP y AM",
    "Quiero comparar Técnicas de Programación con Lógica Computacional",
])
def test_varias_materias_no_eligen_por_orden(catalogo, texto):
    assert resolver_materia(texto, catalogo) is None
    assert resolver_materia(texto, list(reversed(catalogo))) is None


@pytest.mark.parametrize("identidad", [1, 73, 999, 1204])
def test_identidad_procede_del_catalogo(identidad):
    materia = MateriaChat(id=identidad, codigo="MAT", nombre="Elementos de Análisis Matemático")
    assert resolver_materia("Cronograma de Análisis Matemático", [materia]).id == identidad


def test_no_inventa_materia_ausente(catalogo):
    assert resolver_materia("Técnicas de Programación", []) is None
    assert resolver_materia("Técnicas de Programación", catalogo[1:]) is None


def test_nombre_duplicado_en_catalogo_no_produce_id_arbitrario(catalogo):
    duplicada = catalogo[0].model_copy(update={"id": 990, "codigo": "OTRA"})
    assert resolver_materia("Técnicas de Programación", [*catalogo, duplicada]) is None


def test_repite_aliases_de_la_misma_materia_sin_crear_ambiguedad(catalogo):
    assert resolver_materia("Base de Datos, SQL, admin de datos", catalogo) is catalogo[1]


def test_no_genera_iniciales_o_primera_palabra_de_nombres_desconocidos():
    materia = MateriaChat(id=81, codigo="DM42", nombre="Diseño de Modelos Especiales")
    assert resolver_materia("DME", [materia]) is None
    assert resolver_materia("Modelos", [materia]) is None
    assert resolver_materia("Diseño", [materia]) is None
    assert resolver_materia("Diseño de Modelos Especiales", [materia]) is materia
    assert resolver_materia("DM42", [materia]) is materia


@pytest.mark.parametrize("codigo,consulta,coincide", [
    ("1", "Estoy en el cuatrimestre 1", False), ("1", "materia 1", True),
    ("12", "Comisión 12", False), ("12", "código de materia 12", True),
    ("A", "comisión A", False), ("A", "materia A", True),
])
def test_codigos_ambiguos_necesitan_referencia_expresa(codigo, consulta, coincide):
    materia = MateriaChat(id=51, codigo=codigo, nombre="Geometría")
    assert (resolver_materia(consulta, [materia]) is materia) is coincide


@pytest.mark.parametrize("texto,nombre", [
    ("tec prog", "Técnicas de Programación"),
    ("sql", "Administración de Base de Datos"),
    ("calculo", "Elementos de Análisis Matemático"),
    ("logica comp", "Lógica Computacional"),
    ("SQL y lógica", None),
])
def test_wrapper_compatible_resuelve_solo_nombre_inequivoco(texto, nombre):
    assert normalizar_y_buscar_materia(texto) == nombre


@pytest.mark.parametrize("texto,nombres", [
    ("Dame una receta para aprender Base de Datos", ("Administración de Base de Datos",)),
    ("Dame una receta para cálculo", ("Elementos de Análisis Matemático",)),
    ("Quiero SQL y LÓGICA", ("Administración de Base de Datos", "Lógica Computacional")),
    ("Dame la receta del chipá", ()),
    ("Quiero saber mis materias", ()),
])
def test_nombres_mencionados_incluye_ambiguas_sin_inventar_identidad(texto, nombres):
    assert nombres_mencionados(texto) == nombres


def test_normalizacion_sin_registro_de_consultas(catalogo, caplog):
    caplog.set_level(logging.DEBUG)
    assert limpiar_texto(" LÓGICA\u00a0  COMPUTACIONAL ") == "logica computacional"
    assert limpiar_texto("") == ""
    assert resolver_materia("Lógica Computacional", catalogo) is catalogo[3]
    assert caplog.records == []

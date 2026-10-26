"""Parser puro: catálogo sintético, sin servicios externos."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services.metadata_documental import ErrorMetadataDocumental, resolver_metadata_documental
from app.settings import Configuracion

CATALOGO = [SimpleNamespace(id=47, nombre="Técnicas de Programación")]


@pytest.mark.parametrize("nombre,periodo", [
    ("GENERAL__Preguntas-frecuentes.pdf", None),
    ("GENERAL__Plan-de-estudios-TSDS.docx", None),
    ("GENERAL__Información-oficial-IFTS29", None),
    ("2026c2__general__Calendario-academico.PDF", "2026C2"),
])
def test_general(nombre, periodo):
    metadata = resolver_metadata_documental(nombre, [])
    assert metadata.materia_id is metadata.comision is metadata.materia_nombre is None
    assert metadata.periodo == periodo


@pytest.mark.parametrize("materia", ["Tecnicas-de-Programacion", "TÉCNICAS de  Programación"])
@pytest.mark.parametrize("comision,esperada,tipo", [
    ("COM-GENERAL", None, "Programa"), ("COM-f", "F", "Cronograma"),
])
def test_especifico_y_nombre_oficial(materia, comision, esperada, tipo):
    metadata = resolver_metadata_documental(f"2026C2__{materia}__{comision}__{tipo}.pdf", CATALOGO)
    assert metadata.materia_id == 47
    assert metadata.materia_nombre == "Técnicas de Programación"
    assert metadata.materia_normalizada == "tecnicas de programacion"
    assert metadata.comision == esperada and metadata.tipo == tipo and metadata.periodo == "2026C2"


@pytest.mark.parametrize("nombre,categoria", [
    ("2026C2__Inexistente__COM-F__Programa.pdf", "materia_no_reconocida"),
    ("2026C3__Tecnicas-de-Programacion__COM-F__Programa.pdf", "periodo_invalido"),
    ("２０２６C2__Tecnicas-de-Programacion__COM-F__Programa.pdf", "periodo_invalido"),
    ("2026C2__Tecnicas-de-Programacion__F__Programa.pdf", "comision_invalida"),
    ("2026C2__Tecnicas-de-Programacion__COM-Ñ__Programa.pdf", "comision_invalida"),
    ("2026C2__Tecnicas-de-Programacion__COM-F__Inventado.pdf", "tipo_invalido"),
    ("2026C2__GENERAL__COM-F__Programa.pdf", "nombre_invalido"),
    ("GENERAL____Programa.pdf", "nombre_invalido"),
    ("Preguntas_frecuentes.pdf", "nombre_invalido"),
    ("2026C2__Tecnicas-de-Programacion__Programa.pdf", "nombre_invalido"),
])
def test_invalidos_no_se_reinterpretan_como_generales(nombre, categoria):
    with pytest.raises(ErrorMetadataDocumental) as error:
        resolver_metadata_documental(nombre, CATALOGO)
    assert error.value.categoria == categoria


def test_ambiguedad_no_elige_primera_materia():
    with pytest.raises(ErrorMetadataDocumental, match="materia_ambigua"):
        resolver_metadata_documental("2026C2__Tecnicas-de-Programacion__COM-F__Programa.pdf",
                                    CATALOGO + [SimpleNamespace(id=91, nombre="TECNICAS-DE-PROGRAMACION")])


def test_no_fuzzy_ni_codigo_alias():
    with pytest.raises(ErrorMetadataDocumental, match="materia_no_reconocida"):
        resolver_metadata_documental("2026C2__Tecnicas-Programacion__COM-F__Programa.pdf", CATALOGO)


def test_configuracion_comision_y_tipos():
    nombre = "2026C2__Tecnicas-de-Programacion__COM-A123__Guia.pdf"
    config = Configuracion(metadata_comision_max_longitud=4, metadata_tipos_documentales="Programa,Guia")
    assert resolver_metadata_documental(nombre, CATALOGO, comision_max_longitud=4,
                                       tipos_documentales=config.metadata_tipos_documentales).comision == "A123"
    with pytest.raises(ErrorMetadataDocumental, match="comision_invalida"):
        resolver_metadata_documental(nombre, CATALOGO, comision_max_longitud=3)
    # El marcador reservado se admite incluso si el máximo es menor a su longitud.
    assert resolver_metadata_documental("2026C2__Tecnicas-de-Programacion__COM-GENERAL__Programa.pdf",
                                       CATALOGO, comision_max_longitud=1).comision is None
    with pytest.raises(ValidationError):
        Configuracion(metadata_comision_max_longitud=33)
    with pytest.raises(ValidationError):
        Configuracion(metadata_tipos_documentales="")


def test_parser_no_importa_proveedores_ni_realiza_io():
    import ast
    import inspect
    from app.services import metadata_documental

    arbol = ast.parse(inspect.getsource(metadata_documental))
    imports = [n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)]
    assert not any("provider" in (m or "") or "repositorio" in (m or "") for m in imports)
    assert resolver_metadata_documental("GENERAL__Ingreso.pdf", []).tipo == "General"

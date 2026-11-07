"""Declaraciones estructuradas, alcance común y reportes sin contenido sensible."""

from dataclasses import asdict

import pytest

from app.models.rag import DocumentoPersistido, MetadataDocumental
from app.services.coherencia_documental import (
    ErrorCoherenciaDocumental, exigir_coherencia_documental, validar_coherencia_documental,
)


def metadata(**cambios):
    return MetadataDocumental(**({"materia_id": 91, "tipo": "Cronograma",
                                "periodo": "2027C1", "comision": "A"} | cambios))


def validar(texto, **cambios):
    return validar_coherencia_documental(texto, metadata(**cambios), documento="drive-prueba")


@pytest.mark.parametrize("declaracion,detectado", [
    ("Período: 2026-2C", "2026C2"), ("Periodo académico: 2026C2", "2026C2"),
    ("Segundo cuatrimestre de 2026", "2026C2"), ("segundo cuatrimestre 2026", "2026C2"),
    ("2° cuatrimestre de 2026", "2026C2"),
    ("Año: 2026\nCuatrimestre: 2", "2026C2"),
    ("Cuatrimestre: 2\nAño académico: 2026", "2026C2"),
    ("### **Período:** 2026-2C", "2026C2"),
])
def test_periodo_inequivoco_no_coincide(declaracion, detectado):
    resultado = validar(declaracion)
    assert not resultado.aceptado
    assert asdict(resultado.problemas[0]) == {
        "documento": "drive-prueba", "categoria": "incoherencia_documental",
        "campo": "periodo", "metadata_esperada": "2027C1",
        "valor_contextual_detectado": (detectado,), "severidad": "error",
        "accion": "rechazo_sin_cambios",
    }


@pytest.mark.parametrize("declaracion", [
    "Comisión: F", "Comisión F", "### Comisión: F",
    "Este documento corresponde exclusivamente a la comisión F.",
    "Este programa aplica exclusivamente a comisión F.",
])
def test_comision_declarada_es_distinta(declaracion):
    r = validar(declaracion)
    assert not r.aceptado
    assert r.problemas[0].campo == "comision"
    assert r.problemas[0].valor_contextual_detectado == ("F",)


def test_ambos_campos_reportados_sin_parrafos():
    r = validar("Comisión F / segundo cuatrimestre 2026\nContenido privado de estudiantes.")
    assert not r.aceptado and {p.campo for p in r.problemas} == {"periodo", "comision"}
    assert "privado" not in str(asdict(r))


@pytest.mark.parametrize("texto", [
    "Bibliografía: Pérez 2026, edición 2024.", "Resolución 2026/2", "Copyright 2026",
    "La clase empieza el 14/08/2026.", "Se utilizó el ejemplo histórico de 2026.",
    "Consultar a la comisión F por el trabajo conjunto.",
    "Puede compararse con la Comisión F.", "Ejemplo: Comisión F",
    "Ejemplo histórico: Comisión F / Período: 2026-2C",
    "Referencias bibliográficas: Segundo cuatrimestre de 2026 / Comisión F",
    "Resolución: Período: 2026-2C",
    "El documento anterior correspondía al segundo cuatrimestre de 2026.",
    "Año: 2026\nOtra sección\nCuatrimestre: 2", "Año: 2026",
    "Período: 2026/2027", "Comisión F (consultas conjuntas)", "ComisionF",
])
def test_no_bloquea_menciones_incidentales_ni_infiere_contexto(texto):
    assert validar(texto).aceptado


@pytest.mark.parametrize("encabezado", [
    "Bibliografía", "Referencias", "Ejemplos históricos:", "Resoluciones:",
])
def test_encabezado_incidental_no_oculta_contexto_academico_posterior(encabezado):
    texto = (f"{encabezado}\nLibro X, 2023\n\nEvaluación\n"
             "Período: 2026-2C\nComisión: F")
    resultado = validar(texto)
    assert not resultado.aceptado
    assert len(resultado.problemas) == 2
    problemas = {p.campo: p for p in resultado.problemas}
    assert problemas["periodo"].metadata_esperada == "2027C1"
    assert problemas["periodo"].valor_contextual_detectado == ("2026C2",)
    assert problemas["comision"].metadata_esperada == "A"
    assert problemas["comision"].valor_contextual_detectado == ("F",)
    assert all(p.severidad == "error" and p.accion == "rechazo_sin_cambios"
               for p in resultado.problemas)


def test_linea_ignorada_no_une_campos_de_periodo_no_adyacentes():
    assert validar("Año: 2026\nBibliografía\nCuatrimestre: 2").problemas == ()


@pytest.mark.parametrize("comision", [None, "TODAS"])
@pytest.mark.parametrize("texto", [
    "Comisiones: A, B, C y D", "Comisiones: A y B", "Comisiones: Todas",
    "Aplica a todas las comisiones.", "Este documento aplica a todas las comisiones.",
    "La comisión A participa en un ejemplo.",
])
def test_todas_admite_grupos_alcance_institucional_e_incidentales(comision, texto):
    assert validar(texto, comision=comision).aceptado


@pytest.mark.parametrize("comision", [None, "TODAS"])
def test_todas_no_admite_declaracion_especifica_exclusiva(comision):
    r = validar("Este documento corresponde exclusivamente a la comisión A.", comision=comision)
    assert not r.aceptado and r.problemas[0].metadata_esperada == "TODAS"


@pytest.mark.parametrize("texto,campo", [
    ("Período: 2026C2\nPeríodo: 2027C1", "periodo"),
    ("Comisión A\nComisión F", "comision"),
    ("Comisiones: Todas\nComisión A", "comision"),
])
def test_declaraciones_multiples_son_aviso_no_bloqueo(texto, campo, caplog):
    r = exigir_coherencia_documental(texto, metadata(), documento="drive-prueba")
    assert r.aceptado and len(r.problemas) == 1
    problema = r.problemas[0]
    assert (problema.campo, problema.severidad, problema.accion) == (
        campo, "aviso", "continuar_sin_inferir")
    assert caplog.records[0].coherencia_documental == asdict(problema)


def test_error_y_log_solo_contienen_reporte_acotado(caplog):
    texto = "Período: 2026-2C\nDato sensible que no debe aparecer en el reporte."
    with pytest.raises(ErrorCoherenciaDocumental) as error:
        exigir_coherencia_documental(texto, metadata(), documento="drive-prueba")
    assert error.value.resultado.problemas[0].valor_contextual_detectado == ("2026C2",)
    assert "esperado=2027C1" in caplog.text and "2026C2" in caplog.text
    assert "Dato sensible" not in str(error.value) + caplog.text + str(caplog.records[0].__dict__)


def test_coincidencias_y_ausencia_no_agregan_contexto_ni_avisos(caplog):
    m = metadata()
    previo = m.model_dump()
    assert validar_coherencia_documental("Comisión A\nPrimer cuatrimestre de 2027", m,
                                        documento="uno").problemas == ()
    assert validar_coherencia_documental("Sin declaración de contexto.", m,
                                        documento="uno").problemas == ()
    assert m.model_dump() == previo and caplog.records == []


@pytest.mark.parametrize("cambios,campos", [
    ({"materia_id": None, "periodo": None, "comision": None, "tipo": "General"}, {"periodo", "comision"}),
    ({"periodo": None}, {"periodo"}),
    ({"comision": None}, {"comision"}),
    ({"comision": "TODAS"}, {"comision"}),
])
def test_retirar_contexto_no_reinterpreta_declaraciones_como_generales(cambios, campos):
    anterior = DocumentoPersistido(id=1, titulo="Anterior", drive_id="uno", materia_id=91,
                                  periodo="2027C1", comision="A")
    nueva = metadata(**cambios)
    previo = anterior.model_dump(), nueva.model_dump()
    r = validar_coherencia_documental("Período: 2027-1C\nComisión: A", nueva,
                                      documento="uno", anterior=anterior)
    assert not r.aceptado
    assert {p.campo for p in r.problemas} == campos
    assert (anterior.model_dump(), nueva.model_dump()) == previo


def test_retirar_contexto_sin_declaraciones_explicitas_no_bloquea_documento_general():
    anterior = DocumentoPersistido(id=1, titulo="Anterior", drive_id="uno", materia_id=91,
                                  periodo="2026C2", comision="A")
    r = validar_coherencia_documental(
        "Orientación para estudiantes. Referencias históricas de 2023.",
        MetadataDocumental(tipo="General"), documento="uno", anterior=anterior,
    )
    assert r.aceptado and r.problemas == ()


@pytest.mark.parametrize("texto,campo", [
    ("Comisión: A\nContenido previo.", "periodo"),
    ("Período: 2027C1\nContenido previo.", "comision"),
])
def test_contexto_parcial_no_garantiza_coherencia_de_encabezados_perdidos(texto, campo):
    anterior = DocumentoPersistido(id=1, titulo="Anterior.docx", drive_id="uno", materia_id=91,
                                  periodo="2026C2", comision="A")
    r = validar_coherencia_documental(texto, metadata(), documento="uno", anterior=anterior,
                                      exigir_contexto_explicito=True)
    assert not r.aceptado
    assert any(p.campo == campo and p.categoria == "contexto_persistido_no_verificable"
               for p in r.problemas)

"""Contacto institucional explícito antes de RAG; directorio local, sin servicios reales."""

import json
from unittest.mock import Mock

import pytest

from app.services import derivacion


def registro(**cambios):
    return {
        "department": "Bedelía", "email": "bedelia@ifts29.edu.ar",
        "schedule": "Lunes a viernes de 18:30 a 21:30",
        "contact_person": "Equipo de Bedelía", "keywords": ["inscripción"],
    } | cambios


def escribir_directorio(tmp_path, datos):
    ruta = tmp_path / "derivaciones.json"
    ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    return ruta


@pytest.mark.parametrize("consulta", [
    "¿Cómo me contacto con Bedelía?", "¿Cuál es el horario de Bedelía?",
    "Quiero saber los horarios de Bedelía", "¿A qué hora atiende Bedelía?",
    "¿Cómo contacto a Bedelía?", "¿Cómo puedo comunicarme con Bedelía?",
    "¿Cómo puedo escribirle a Bedelía?", "Correo electrónico de Bedelía",
    "Necesito el correo de Bedelía", "¿Me pasás el teléfono de Bedelía?",
    "Horario de atención de Bedelía", "Hola, ¿cómo me contacto con Bedelía?",
    "Por favor, horario de Bedelía. Gracias", "¿Cuál es el horario de la Bedelía?",
    "HORARIO DE BEDELIA", "¿Cuándo abre Bedelía?", "¿A qué hora cierra Bedelía?",
])
def test_contacto_u_horario_explicitos_conservan_datos(tmp_path, consulta):
    dato = registro()
    ruta = escribir_directorio(tmp_path, [dato])
    assert derivacion.resolver_contacto_directo(consulta, ruta) == dato


@pytest.mark.parametrize("consulta", [
    "Quiero el certificado de asistencia a un examen parcial",
    "Quiero el certificado de alumno regular de Bedelía",
    "Necesito tramitar una constancia en Bedelía",
    "Quiero el horario de Bedelía para pedir un certificado",
    "Quiero saber los horarios de Bedelía y el cronograma de Programación",
    "¿Cuál es el horario de Programación? Bedelía informa eso",
    "Me dijeron que el horario de Bedelía cambió", "Bedelía", "inscripción",
    "No quiero saber los horarios de Bedelía", "No me contacto con Bedelía",
    '¿Qué significa "horario de Bedelía"?', '"¿Cómo me contacto con Bedelía?"',
    "¿Cómo me contacto con Bedelial?", "¿Cuál es el horario de Otra Bedelía?",
    "¿Cuál es el horario de Tesorería?", "", "  ", None, 1,
])
def test_contenido_ambiguo_negado_desconocido_o_sin_objetivo_no_toma_atajo(tmp_path, consulta):
    ruta = escribir_directorio(tmp_path, [registro()])
    assert derivacion.resolver_contacto_directo(consulta, ruta) is None


@pytest.mark.parametrize("consulta", [
    "¿Cómo me contacto con Bedelía?\nAclaración: Por favor",
    "Por favor\nAclaración: ¿Cómo me contacto con Bedelía?",
    "¿Cómo me contacto con Bedelía?\nAclaración: Mi correo es [CORREO_OCULTO]",
    "Mi DNI es [DNI_OCULTO]\nAclaración: ¿Cuál es el horario de Bedelía?",
    "Mi correo es [CORREO_OCULTO] y ¿cómo me contacto con Bedelía?",
    "¿Cómo me contacto con Bedelía? y mi correo es [CORREO_OCULTO]",
    "Me llamo [NOMBRE_OCULTO], ¿cómo me contacto con Bedelía?",
])
def test_combinacion_con_cortesia_o_pii_oculto_no_reconstruye_original(tmp_path, consulta):
    ruta = escribir_directorio(tmp_path, [registro()])
    assert derivacion.resolver_contacto_directo(consulta, ruta)["department"] == "Bedelía"


@pytest.mark.parametrize("consulta", [
    "Necesito un certificado de asistencia\nAclaración: ¿Cuál es el horario de Bedelía?",
    "¿Cuál es el horario de Bedelía?\nAclaración: También quiero saber mis materias",
    "Quiero el cronograma de Programación\nAclaración: ¿Cómo contacto a Bedelía?",
])
def test_consulta_pendiente_de_contenido_no_se_descarta(tmp_path, consulta):
    ruta = escribir_directorio(tmp_path, [registro()])
    assert derivacion.resolver_contacto_directo(consulta, ruta) is None


def test_nombre_de_sector_y_datos_provienen_del_directorio(tmp_path):
    dato = registro(
        department="Oficina de Alumnos", email="alumnos@ifts29.edu.ar",
        schedule="Martes 19:00 a 20:00", contact_person=None,
    )
    ruta = escribir_directorio(tmp_path, [dato])
    assert derivacion.resolver_contacto_directo("¿Cuál es el horario de Oficina de Alumnos?", ruta) == dato
    assert derivacion.resolver_contacto_directo("¿Cuál es el horario de Bedelía?", ruta) is None


@pytest.mark.parametrize("consulta", [
    "¿Cuál es el horario de Bedelía y Secretaría Académica?",
    "¿Cuál es el horario de Bedelía?\nAclaración: Horario de Secretaría Académica",
])
def test_varios_sectores_no_se_eligen_por_orden(tmp_path, consulta):
    ruta = escribir_directorio(tmp_path, [
        registro(), registro(department="Secretaría Académica", email="secretaria@ifts29.edu.ar"),
    ])
    assert derivacion.resolver_contacto_directo(consulta, ruta) is None


def test_directorio_duplicado_no_elige_contacto_arbitrariamente(tmp_path):
    ruta = escribir_directorio(tmp_path, [registro(), registro(email="otro@ifts29.edu.ar")])
    assert derivacion.resolver_contacto_directo("Horario de Bedelía", ruta) is None


@pytest.mark.parametrize("modo", ["faltante", "json", "incompleto"])
def test_directorio_no_disponible_o_invalido_no_rompe_consulta(tmp_path, modo):
    ruta = tmp_path / "derivaciones.json"
    if modo == "json":
        ruta.write_text("JSON_INVALIDO", encoding="utf-8")
    elif modo == "incompleto":
        escribir_directorio(tmp_path, [registro(email="invalido")])
    assert derivacion.resolver_contacto_directo("Horario de Bedelía", ruta) is None


def test_sin_objetivo_de_contacto_no_lee_directorio(monkeypatch):
    cargar = Mock(side_effect=AssertionError("No corresponde consultar el directorio."))
    monkeypatch.setattr(derivacion, "load_referrals", cargar)
    assert derivacion.resolver_contacto_directo("Quiero un certificado") is None
    cargar.assert_not_called()

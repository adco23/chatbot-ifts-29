"""T46: ocultación local en el límite de entrada, sin red ni registro de datos."""

import logging

import pytest

from app.models.chat import ContextoChat, DerivacionChat, SolicitudChat
from app.services.conversacion import _texto_consulta
from app.services.sanitizador import (
    normalizar_comparacion,
    sanitizar_datos_personales,
    sanitizar_texto,
)


@pytest.mark.parametrize("entrada,esperado", [
    ("Escribime a alumno@example.org.", "Escribime a [CORREO_OCULTO]."),
    ("alumno+consulta@estudiantes.ifts29.edu.ar", "[CORREO_OCULTO]"),
    ("ALUMNO@EXAMPLE.ORG", "[CORREO_OCULTO]"),
    ("1234567", "[DNI_OCULTO]"),
    ("12345678", "[DNI_OCULTO]"),
    ("1.234.567", "[DNI_OCULTO]"),
    ("12.345.678", "[DNI_OCULTO]"),
    ("DNI: 12.345.678.", "DNI: [DNI_OCULTO]."),
    ("20-12345678-3", "[CUIL_OCULTO]"),
    ("20123456783", "[CUIL_OCULTO]"),
    ("27 - 12345678 - 3", "[CUIL_OCULTO]"),
    ("30\u201312345678\u20133", "[CUIL_OCULTO]"),
    ("+54 9 11 1234-5678", "[TELEFONO_OCULTO]"),
    ("11 1234-5678", "[TELEFONO_OCULTO]"),
    ("011 1234-5678", "[TELEFONO_OCULTO]"),
    ("(011) 1234-5678", "[TELEFONO_OCULTO]"),
    ("+54 11 1234 5678", "[TELEFONO_OCULTO]"),
    ("+5491112345678", "[TELEFONO_OCULTO]"),
    ("1112345678", "[TELEFONO_OCULTO]"),
    ("01112345678", "[TELEFONO_OCULTO]"),
    ("011 15 1234-5678", "[TELEFONO_OCULTO]"),
    ("+54 9 (11) 1234-5678", "[TELEFONO_OCULTO]"),
    ("(221) 456-7890", "[TELEFONO_OCULTO]"),
    ("0341 456-7890", "[TELEFONO_OCULTO]"),
    ("02954 123-456", "[TELEFONO_OCULTO]"),
    ("+54 9 2954 123-456", "[TELEFONO_OCULTO]"),
])
def test_formatos_y_marcadores(entrada, esperado):
    assert sanitizar_datos_personales(entrada) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    ("Me llamo Lucas Monteras", "Me llamo [NOMBRE_OCULTO]"),
    ("Mi nombre es Lucas Monteras", "Mi nombre es [NOMBRE_OCULTO]"),
    ("Soy Lucas Monteras", "Soy [NOMBRE_OCULTO]"),
    ("me llamo Ana", "me llamo [NOMBRE_OCULTO]"),
    ("MI NOMBRE ES María Pérez", "MI NOMBRE ES [NOMBRE_OCULTO]"),
    ("Soy Ana María Pérez, necesito inscribirme.", "Soy [NOMBRE_OCULTO], necesito inscribirme."),
    ("Me llamo Juan de la Cruz y necesito ayuda.", "Me llamo [NOMBRE_OCULTO] y necesito ayuda."),
    ("Soy Juan de la Cruz.", "Soy [NOMBRE_OCULTO]."),
    ("Me llamo María López-García.", "Me llamo [NOMBRE_OCULTO]."),
    ("Hola, soy Lucas Monteras de comisión A.", "Hola, soy [NOMBRE_OCULTO] de comisión A."),
])
def test_autopresentacion_explicita(entrada, esperado):
    assert sanitizar_datos_personales(entrada) == esperado


@pytest.mark.parametrize("entrada", [
    "Soy estudiante", "Soy ingresante", "Soy alumno regular", "Soy de comisión A",
    "SOY ESTUDIANTE", "SOY INGRESANTE", "SOY ALUMNO REGULAR", "SOY DE COMISIÓN A",
    "Soy Estudiante", "Soy Alumno Regular", "Soy Estudiante Ingresante",
    "Soy Docente Titular", "Soy Técnico Superior", "Soy Secretaría Académica",
    "Soy De Primer Año", "Soy Responsable De Inscripciones", "Soy La Nueva Ingresante",
    "Soy De Comisión A", "Soy Lucas", "soy lucas monteras", "Soy LUCAS MONTERAS",
    "El profesor Lucas Monteras dicta clases.", "Hablé con María Pérez sobre el programa.",
    "¿Quién es Lucas Monteras?", "¿Cuánto dura la carrera?",
    "El programa de Técnicas de Programación incluye algoritmos.",
    "Comisión A, 2026C2, semana 8, examen el 30/09/2026.",
    "Fechas: 12.03.2026 y 30-09-2026.",
    "Código ABC12345678, referencia 99123456789999 y versión 12.345.678.901.",
    "Registro 99123456789 y expediente 123456789.",
    "Identificador 201234567830 y código 12345678-9.",
    "Identificadores 12345678\u20139 y 20123456783\u20149.",
    "[CORREO_OCULTO] [CUIL_OCULTO] [TELEFONO_OCULTO] [DNI_OCULTO] [NOMBRE_OCULTO]",
])
def test_conserva_texto_sin_coincidencia_segura(entrada):
    assert sanitizar_datos_personales(entrada) == entrada


COMBINADO = (
    "Me llamo Lucas Monteras, DNI 12.345.678, CUIL 20-12345678-3, "
    "correo alumno@example.org y teléfono +54 9 11 1234-5678."
)
OCULTO = (
    "Me llamo [NOMBRE_OCULTO], DNI [DNI_OCULTO], CUIL [CUIL_OCULTO], "
    "correo [CORREO_OCULTO] y teléfono [TELEFONO_OCULTO]."
)


def test_varios_datos_y_precedencia_sin_ocultacion_parcial():
    assert sanitizar_datos_personales(COMBINADO) == OCULTO
    assert OCULTO.count("[DNI_OCULTO]") == OCULTO.count("[CUIL_OCULTO]") == 1


@pytest.mark.parametrize("entrada", [COMBINADO, OCULTO, "Soy estudiante", "", "1234567"])
def test_idempotencia(entrada):
    una_vez = sanitizar_datos_personales(entrada)
    assert sanitizar_datos_personales(una_vez) == una_vez


def test_normaliza_unicode_espacios_y_controles_antes_de_ocultar():
    assert sanitizar_datos_personales(
        "\tDNI:\u00a0１２３４５６７８\r\n correo: alum\u200bno@example.org\x00 "
    ) == "DNI: [DNI_OCULTO] correo: [CORREO_OCULTO]"


def test_normalizacion_compartida_no_oculta_documentos_ni_contactos():
    assert sanitizar_texto("\tCorreo\u00a0alumno@example.org\x00\n12345678 ") == (
        "Correo alumno@example.org 12345678"
    )
    assert normalizar_comparacion("TÉCNICAS 12345678 alumno@example.org") == (
        "tecnicas 12345678 alumno@example.org"
    )
    contacto = DerivacionChat(
        sector="Bedelía", correo="bedelia@ifts29.edu.ar", horario="18:30 a 21:30",
        responsable="María Pérez",
    )
    assert contacto.responsable == "María Pérez" and contacto.correo == "bedelia@ifts29.edu.ar"


@pytest.mark.parametrize("campo", ["mensaje", "consulta_pendiente"])
def test_oculta_al_construir_cada_modelo_sin_guardar_original(campo):
    modelo = (SolicitudChat(mensaje=COMBINADO) if campo == "mensaje"
              else ContextoChat(consulta_pendiente=COMBINADO))
    assert getattr(modelo, campo) == OCULTO
    assert modelo.model_dump()[campo] == OCULTO
    assert "Lucas Monteras" not in repr(modelo) + modelo.model_dump_json()
    assert modelo.model_extra is None
    assert set(vars(modelo)) == set(type(modelo).model_fields)


def test_solicitud_con_contexto_anidado_y_consulta_completa_sin_original():
    solicitud = SolicitudChat.model_validate({
        "mensaje": COMBINADO, "contexto": {"consulta_pendiente": COMBINADO},
    })
    assert solicitud.mensaje == solicitud.contexto.consulta_pendiente == OCULTO
    assert _texto_consulta(solicitud) == f"{OCULTO}\nAclaración: {OCULTO}"
    assert _texto_consulta(SolicitudChat.model_validate_json(solicitud.model_dump_json())) == (
        _texto_consulta(solicitud)
    )


@pytest.mark.parametrize("entrada", ["Soy estudiante", "Soy ingresante", "Soy alumno regular",
                                    "Soy de comisión A", "Soy Alumno Regular"])
def test_modelos_conservan_frases_academicas(entrada):
    solicitud = SolicitudChat(mensaje=entrada, contexto=ContextoChat(consulta_pendiente=entrada))
    assert solicitud.mensaje == solicitud.contexto.consulta_pendiente == entrada


@pytest.mark.parametrize("entrada", [None, "", " \t\x00 "])
def test_pendiente_opcional_vacia_sigue_siendo_none(entrada):
    assert ContextoChat(consulta_pendiente=entrada).consulta_pendiente is None


def test_no_registra_coincidencias_categorias_ni_valores(caplog):
    caplog.set_level(logging.DEBUG)
    assert sanitizar_datos_personales(COMBINADO) == OCULTO
    assert not caplog.records

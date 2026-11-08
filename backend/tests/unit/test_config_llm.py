"""Pruebas de la configuración de los proveedores de modelos de lenguaje."""

import pytest
from pydantic import ValidationError

from app.config import obtener_configuracion

# Valores ficticios: no son claves reales ni tienen su formato.
CLAVE_GEMINI = "clave-ficticia-gemini"
CLAVE_GROQ = "clave-ficticia-groq"


def test_sin_variables_no_hay_proveedores_disponibles():
    config = obtener_configuracion()

    assert config.gemini_api_key is None
    assert config.groq_api_key is None
    assert config.llm_orden_proveedores == ["gemini", "groq"]
    assert config.proveedores_disponibles == []


def test_solo_clave_de_groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", CLAVE_GROQ)

    assert obtener_configuracion().proveedores_disponibles == ["groq"]


def test_ambas_claves_respetan_el_orden_por_defecto(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CLAVE_GEMINI)
    monkeypatch.setenv("GROQ_API_KEY", CLAVE_GROQ)

    assert obtener_configuracion().proveedores_disponibles == ["gemini", "groq"]


def test_el_orden_configurado_se_invierte(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CLAVE_GEMINI)
    monkeypatch.setenv("GROQ_API_KEY", CLAVE_GROQ)
    monkeypatch.setenv("LLM_ORDEN_PROVEEDORES", "groq,gemini")

    assert obtener_configuracion().proveedores_disponibles == ["groq", "gemini"]


def test_el_orden_se_normaliza(monkeypatch):
    monkeypatch.setenv("LLM_ORDEN_PROVEEDORES", "  GROQ , Gemini,groq ,, GEMINI ")

    assert obtener_configuracion().llm_orden_proveedores == ["groq", "gemini"]


def test_proveedor_desconocido_lanza_error(monkeypatch):
    monkeypatch.setenv("LLM_ORDEN_PROVEEDORES", "gemini,openai")

    with pytest.raises(ValidationError) as error:
        obtener_configuracion()

    mensaje = str(error.value)
    assert "openai" in mensaje
    assert "Nombres válidos: gemini, groq" in mensaje


@pytest.mark.parametrize("valor", ["", "  ", ","])
def test_orden_vacio_lanza_error(monkeypatch, valor):
    monkeypatch.setenv("LLM_ORDEN_PROVEEDORES", valor)

    with pytest.raises(ValidationError):
        obtener_configuracion()


@pytest.mark.parametrize("valor", ["", "   ", "\t"])
def test_clave_en_blanco_se_trata_como_no_configurada(monkeypatch, valor):
    monkeypatch.setenv("GEMINI_API_KEY", valor)
    monkeypatch.setenv("GROQ_API_KEY", CLAVE_GROQ)

    config = obtener_configuracion()

    assert config.gemini_api_key is None
    assert config.proveedores_disponibles == ["groq"]


@pytest.mark.parametrize(
    "variable",
    ["GEMINI_TIMEOUT_SEGUNDOS", "GROQ_TIMEOUT_SEGUNDOS", "LLM_PRESUPUESTO_SEGUNDOS"],
)
@pytest.mark.parametrize("valor", ["0", "-1.5"])
def test_tiempo_no_positivo_lanza_error(monkeypatch, variable, valor):
    monkeypatch.setenv(variable, valor)

    with pytest.raises(ValidationError):
        obtener_configuracion()


def test_presupuesto_menor_que_un_tiempo_maximo_lanza_error(monkeypatch):
    monkeypatch.setenv("GEMINI_TIMEOUT_SEGUNDOS", "8")
    monkeypatch.setenv("LLM_PRESUPUESTO_SEGUNDOS", "5")

    with pytest.raises(ValidationError, match="LLM_PRESUPUESTO_SEGUNDOS"):
        obtener_configuracion()


def test_valores_por_defecto_de_tiempos_son_coherentes():
    config = obtener_configuracion()

    suma = config.gemini_timeout_segundos + config.groq_timeout_segundos
    assert suma <= config.llm_presupuesto_segundos < 10


def test_las_claves_no_aparecen_en_repr_ni_str(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CLAVE_GEMINI)
    monkeypatch.setenv("GROQ_API_KEY", CLAVE_GROQ)

    config = obtener_configuracion()

    for texto in (repr(config), str(config)):
        assert CLAVE_GEMINI not in texto
        assert CLAVE_GROQ not in texto
    assert config.gemini_api_key.get_secret_value() == CLAVE_GEMINI


@pytest.mark.parametrize(
    ("variable", "valor"),
    [
        ("LLM_ORDEN_PROVEEDORES", "openai"),
        ("GROQ_TIMEOUT_SEGUNDOS", "0"),
        # Falla en el validador del modelo completo, que recibe todos los valores.
        ("LLM_PRESUPUESTO_SEGUNDOS", "1"),
    ],
)
def test_las_claves_no_aparecen_en_errores_de_validacion(monkeypatch, variable, valor):
    monkeypatch.setenv("GEMINI_API_KEY", CLAVE_GEMINI)
    monkeypatch.setenv("GROQ_API_KEY", CLAVE_GROQ)
    monkeypatch.setenv(variable, valor)

    with pytest.raises(ValidationError) as error:
        obtener_configuracion()

    for texto in (str(error.value), repr(error.value)):
        assert CLAVE_GEMINI not in texto
        assert CLAVE_GROQ not in texto


def test_health_responde_200_sin_variables(cliente):
    assert cliente.get("/health").status_code == 200

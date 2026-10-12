"""Pruebas de la configuración de embeddings, límite de solicitudes y administración."""

import pytest
from pydantic import ValidationError

from app.config import PROVEEDORES_EMBEDDINGS_VALIDOS, obtener_configuracion

# Valores ficticios: no son credenciales reales ni tienen su formato.
CLAVE_GEMINI = "clave-ficticia-gemini"
CUENTA_CLOUDFLARE = "cuenta-ficticia-cloudflare"
TOKEN_CLOUDFLARE = "token-ficticio-cloudflare"
TOKEN_ADMIN = "token-ficticio-de-administracion"

SECRETOS = {
    "GEMINI_API_KEY": CLAVE_GEMINI,
    "CLOUDFLARE_ACCOUNT_ID": CUENTA_CLOUDFLARE,
    "CLOUDFLARE_API_TOKEN": TOKEN_CLOUDFLARE,
    "ADMIN_API_TOKEN": TOKEN_ADMIN,
}


@pytest.fixture
def secretos(monkeypatch):
    for variable, valor in SECRETOS.items():
        monkeypatch.setenv(variable, valor)


def test_valores_por_defecto():
    config = obtener_configuracion()

    assert config.embedding_primary == "gemini"
    assert config.orden_embeddings == ["gemini", "cloudflare"]
    assert config.proveedores_embeddings_disponibles == []
    assert (config.gemini_embedding_modelo, config.gemini_embedding_dimension) == (
        "gemini-embedding-001",
        768,
    )
    assert (config.cloudflare_embedding_modelo, config.cloudflare_embedding_dimension) == (
        "@cf/baai/bge-m3",
        1024,
    )
    assert config.cloudflare_account_id is None
    assert config.cloudflare_api_token is None
    assert config.admin_api_token is None
    assert (config.limite_solicitudes_maximo, config.limite_solicitudes_ventana_segundos) == (
        10,
        60.0,
    )


def test_los_nombres_validos_son_gemini_y_cloudflare():
    assert set(PROVEEDORES_EMBEDDINGS_VALIDOS) == {"gemini", "cloudflare"}


def test_embedding_primary_invierte_el_orden(monkeypatch, secretos):
    monkeypatch.setenv("EMBEDDING_PRIMARY", "cloudflare")

    config = obtener_configuracion()

    assert config.orden_embeddings == ["cloudflare", "gemini"]
    assert config.proveedores_embeddings_disponibles == ["cloudflare", "gemini"]


@pytest.mark.parametrize("valor", ["Cloudflare", "  CLOUDFLARE  ", "cloudflare\n"])
def test_embedding_primary_se_normaliza(monkeypatch, valor):
    monkeypatch.setenv("EMBEDDING_PRIMARY", valor)

    assert obtener_configuracion().embedding_primary == "cloudflare"


@pytest.mark.parametrize("valor", ["openai", "groq", "", "gemini,cloudflare"])
def test_embedding_primary_desconocido_lanza_error(monkeypatch, valor):
    monkeypatch.setenv("EMBEDDING_PRIMARY", valor)

    with pytest.raises(ValidationError) as error:
        obtener_configuracion()

    assert "EMBEDDING_PRIMARY" in str(error.value)
    assert "Nombres válidos: gemini, cloudflare" in str(error.value)


def test_solo_se_listan_los_proveedores_con_credenciales(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", CUENTA_CLOUDFLARE)
    assert obtener_configuracion().proveedores_embeddings_disponibles == []

    obtener_configuracion.cache_clear()
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN_CLOUDFLARE)
    assert obtener_configuracion().proveedores_embeddings_disponibles == ["cloudflare"]

    obtener_configuracion.cache_clear()
    monkeypatch.setenv("GEMINI_API_KEY", CLAVE_GEMINI)
    assert obtener_configuracion().proveedores_embeddings_disponibles == ["gemini", "cloudflare"]


@pytest.mark.parametrize("variable", ["CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"])
@pytest.mark.parametrize("valor", ["", "   ", "\t"])
def test_credencial_de_cloudflare_en_blanco_se_trata_como_no_configurada(
    monkeypatch, secretos, variable, valor
):
    monkeypatch.setenv(variable, valor)

    assert obtener_configuracion().proveedores_embeddings_disponibles == ["gemini"]


@pytest.mark.parametrize("valor", ["", "   ", "\t"])
def test_token_de_administracion_en_blanco_se_trata_como_no_configurado(monkeypatch, valor):
    monkeypatch.setenv("ADMIN_API_TOKEN", valor)

    assert obtener_configuracion().admin_api_token is None


@pytest.mark.parametrize(
    "variable",
    [
        "GEMINI_EMBEDDING_DIMENSION",
        "CLOUDFLARE_EMBEDDING_DIMENSION",
        "GEMINI_EMBEDDING_TIMEOUT_SEGUNDOS",
        "CLOUDFLARE_EMBEDDING_TIMEOUT_SEGUNDOS",
        "EMBEDDING_TIMEOUT_DOCUMENTOS_SEGUNDOS",
        "EMBEDDING_BACKOFF_BASE_SEGUNDOS",
        "EMBEDDING_BACKOFF_MAXIMO_SEGUNDOS",
        "EMBEDDING_PRESUPUESTO_SEGUNDOS",
        "LIMITE_SOLICITUDES_MAXIMO",
        "LIMITE_SOLICITUDES_VENTANA_SEGUNDOS",
    ],
)
@pytest.mark.parametrize("valor", ["0", "-1"])
def test_valor_no_positivo_lanza_error(monkeypatch, variable, valor):
    monkeypatch.setenv(variable, valor)

    with pytest.raises(ValidationError):
        obtener_configuracion()


@pytest.mark.parametrize(
    ("variable", "valor"),
    [
        ("GEMINI_EMBEDDING_DIMENSION", "768.5"),
        ("LIMITE_SOLICITUDES_MAXIMO", "diez"),
        ("EMBEDDING_PRESUPUESTO_SEGUNDOS", "inf"),
        ("LIMITE_SOLICITUDES_VENTANA_SEGUNDOS", "nan"),
    ],
)
def test_valor_con_formato_invalido_lanza_error(monkeypatch, variable, valor):
    monkeypatch.setenv(variable, valor)

    with pytest.raises(ValidationError):
        obtener_configuracion()


@pytest.mark.parametrize(
    ("valor", "valido"), [("0", True), ("5", True), ("-1", False), ("6", False)]
)
def test_los_reintentos_estan_acotados(monkeypatch, valor, valido):
    monkeypatch.setenv("EMBEDDING_REINTENTOS", valor)

    if valido:
        assert obtener_configuracion().embedding_reintentos == int(valor)
    else:
        with pytest.raises(ValidationError):
            obtener_configuracion()


def test_la_espera_maxima_no_puede_ser_menor_que_la_base(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKOFF_BASE_SEGUNDOS", "2")
    monkeypatch.setenv("EMBEDDING_BACKOFF_MAXIMO_SEGUNDOS", "1")

    with pytest.raises(ValidationError, match="EMBEDDING_BACKOFF_MAXIMO_SEGUNDOS"):
        obtener_configuracion()


def test_los_secretos_no_aparecen_en_repr_ni_str(secretos):
    config = obtener_configuracion()

    for texto in (repr(config), str(config)):
        for secreto in SECRETOS.values():
            assert secreto not in texto
    assert config.cloudflare_api_token.get_secret_value() == TOKEN_CLOUDFLARE
    assert config.cloudflare_account_id.get_secret_value() == CUENTA_CLOUDFLARE
    assert config.admin_api_token.get_secret_value() == TOKEN_ADMIN


@pytest.mark.parametrize(
    ("variable", "valor"),
    [
        ("EMBEDDING_PRIMARY", "openai"),
        ("CLOUDFLARE_EMBEDDING_DIMENSION", "0"),
        ("LIMITE_SOLICITUDES_MAXIMO", "0"),
        # Falla en el validador del modelo completo, que recibe todos los valores.
        ("EMBEDDING_BACKOFF_MAXIMO_SEGUNDOS", "0.1"),
    ],
)
def test_los_secretos_no_aparecen_en_errores_de_validacion(monkeypatch, secretos, variable, valor):
    monkeypatch.setenv(variable, valor)

    with pytest.raises(ValidationError) as error:
        obtener_configuracion()

    for texto in (str(error.value), repr(error.value)):
        for secreto in SECRETOS.values():
            assert secreto not in texto


def test_health_responde_200_con_la_configuracion_nueva_sin_variables(cliente):
    assert cliente.get("/health").status_code == 200

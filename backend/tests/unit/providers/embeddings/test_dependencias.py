"""Pruebas de la construcción del gestor de embeddings a partir de la configuración."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.settings import PROVEEDORES_EMBEDDINGS_VALIDOS, obtener_configuracion
from app.providers import CuotaAgotada
from app.providers.embeddings import EmbeddingsCloudflare, EmbeddingsGemini, GestorEmbeddings
from app.providers.embeddings.dependencias import (
    crear_proveedor_embeddings,
    obtener_gestor_embeddings,
)
from app.providers.embeddings.falso import EmbeddingsFalso
from tests.unit.providers.embeddings.ayudas import CONSULTA, crear_gestor, ejecutar

# Valores ficticios: no son credenciales reales ni tienen su formato.
CLAVE_GEMINI = "clave-ficticia-gemini"
CUENTA_CLOUDFLARE = "cuenta-ficticia-cloudflare"
TOKEN_CLOUDFLARE = "token-ficticio-cloudflare"


@pytest.fixture
def credenciales(monkeypatch):
    """Carga las credenciales de los dos proveedores."""
    monkeypatch.setenv("GEMINI_API_KEY", CLAVE_GEMINI)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", CUENTA_CLOUDFLARE)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN_CLOUDFLARE)


def nombres(gestor: GestorEmbeddings) -> list[str]:
    return [proveedor.nombre for proveedor in gestor.proveedores]


def test_sin_credenciales_construye_un_gestor_vacio():
    gestor = obtener_gestor_embeddings()

    assert gestor.proveedores == ()
    assert gestor.presupuesto_segundos == 4.0


def test_por_defecto_gemini_es_el_principal_y_cloudflare_el_respaldo(credenciales):
    gestor = obtener_gestor_embeddings()

    assert [type(proveedor) for proveedor in gestor.proveedores] == [
        EmbeddingsGemini,
        EmbeddingsCloudflare,
    ]
    assert nombres(gestor) == ["gemini", "cloudflare"]


def test_embedding_primary_invierte_el_orden(credenciales, monkeypatch):
    monkeypatch.setenv("EMBEDDING_PRIMARY", "cloudflare")

    gestor = obtener_gestor_embeddings()

    assert [type(proveedor) for proveedor in gestor.proveedores] == [
        EmbeddingsCloudflare,
        EmbeddingsGemini,
    ]
    assert nombres(gestor) == ["cloudflare", "gemini"]


@pytest.mark.parametrize(
    ("principal", "esperado"),
    [("gemini", "cloudflare"), ("cloudflare", "gemini")],
)
def test_embedding_primary_decide_quien_se_intenta_primero(monkeypatch, principal, esperado):
    """El orden de la configuración llega hasta el recorrido del gestor."""
    monkeypatch.setenv("EMBEDDING_PRIMARY", principal)
    orden = obtener_configuracion().orden_embeddings
    # El que quede primero falla: debe responder el otro.
    proveedores = [
        EmbeddingsFalso([CuotaAgotada()] if indice == 0 else [None], nombre=nombre)
        for indice, nombre in enumerate(orden)
    ]

    resultado = ejecutar(crear_gestor(proveedores).embed_query(CONSULTA))

    assert orden[0] == principal
    assert resultado.proveedor == esperado
    assert [proveedor.llamadas for proveedor in proveedores] == [1, 1]


def test_solo_con_la_clave_de_gemini_no_hay_respaldo(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CLAVE_GEMINI)

    assert nombres(obtener_gestor_embeddings()) == ["gemini"]


@pytest.mark.parametrize("variable", ["CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"])
def test_cloudflare_necesita_la_cuenta_y_el_token(monkeypatch, variable):
    monkeypatch.setenv(variable, "valor-ficticio")

    assert obtener_gestor_embeddings().proveedores == ()


def test_solo_con_cloudflare_es_el_unico_proveedor_aunque_no_sea_el_principal(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", CUENTA_CLOUDFLARE)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN_CLOUDFLARE)

    assert nombres(obtener_gestor_embeddings()) == ["cloudflare"]


def test_valores_por_defecto_de_modelos_dimensiones_y_tiempos(credenciales):
    gestor = obtener_gestor_embeddings()
    gemini, cloudflare = gestor.proveedores

    assert (gemini.modelo, gemini.dimension) == ("gemini-embedding-001", 768)
    assert (cloudflare.modelo, cloudflare.dimension) == ("@cf/baai/bge-m3", 1024)
    assert (gemini._timeout_segundos, cloudflare._timeout_segundos) == (2.0, 2.0)
    assert gemini._timeout_documentos_segundos == 10.0
    assert cloudflare._timeout_documentos_segundos == 10.0
    assert (gestor.reintentos, gestor.presupuesto_segundos) == (1, 4.0)
    assert (gestor.backoff_base_segundos, gestor.backoff_maximo_segundos) == (0.25, 1.0)


def test_respeta_modelos_dimensiones_tiempos_y_reintentos_configurados(credenciales, monkeypatch):
    variables = {
        "GEMINI_EMBEDDING_MODELO": "modelo-gemini",
        "GEMINI_EMBEDDING_DIMENSION": "1536",
        "GEMINI_EMBEDDING_TIMEOUT_SEGUNDOS": "1.5",
        "CLOUDFLARE_EMBEDDING_MODELO": "@cf/otro/modelo",
        "CLOUDFLARE_EMBEDDING_DIMENSION": "384",
        "CLOUDFLARE_EMBEDDING_TIMEOUT_SEGUNDOS": "3",
        "EMBEDDING_TIMEOUT_DOCUMENTOS_SEGUNDOS": "20",
        "EMBEDDING_REINTENTOS": "3",
        "EMBEDDING_BACKOFF_BASE_SEGUNDOS": "0.1",
        "EMBEDDING_BACKOFF_MAXIMO_SEGUNDOS": "0.8",
        "EMBEDDING_PRESUPUESTO_SEGUNDOS": "6",
    }
    for variable, valor in variables.items():
        monkeypatch.setenv(variable, valor)

    gestor = obtener_gestor_embeddings()
    gemini, cloudflare = gestor.proveedores

    assert (gemini.modelo, gemini.dimension, gemini._timeout_segundos) == (
        "modelo-gemini",
        1536,
        1.5,
    )
    assert (cloudflare.modelo, cloudflare.dimension, cloudflare._timeout_segundos) == (
        "@cf/otro/modelo",
        384,
        3.0,
    )
    assert gemini._timeout_documentos_segundos == 20.0
    assert cloudflare._timeout_documentos_segundos == 20.0
    assert (gestor.reintentos, gestor.presupuesto_segundos) == (3, 6.0)
    assert (gestor.backoff_base_segundos, gestor.backoff_maximo_segundos) == (0.1, 0.8)


def test_cada_proveedor_recibe_sus_credenciales(credenciales):
    gemini, cloudflare = obtener_gestor_embeddings().proveedores

    assert gemini._api_key == CLAVE_GEMINI
    assert cloudflare._api_key == TOKEN_CLOUDFLARE
    assert cloudflare._account_id == CUENTA_CLOUDFLARE


def test_el_gestor_se_construye_una_sola_vez():
    assert obtener_gestor_embeddings() is obtener_gestor_embeddings()


def test_cada_nombre_valido_de_la_configuracion_tiene_su_proveedor(credenciales):
    config = obtener_configuracion()

    for nombre in PROVEEDORES_EMBEDDINGS_VALIDOS:
        assert crear_proveedor_embeddings(nombre, config).nombre == nombre


@pytest.mark.parametrize("nombre", ["gemini", "cloudflare", "falso", "openai"])
def test_crear_proveedor_sin_credenciales_o_desconocido_lanza_error(nombre):
    with pytest.raises(ValueError):
        crear_proveedor_embeddings(nombre, obtener_configuracion())


def test_se_puede_sobrescribir_con_dependency_overrides():
    app = FastAPI()

    @app.get("/proveedores")
    def listar(
        gestor: Annotated[GestorEmbeddings, Depends(obtener_gestor_embeddings)],
    ) -> list[str]:
        return [proveedor.nombre for proveedor in gestor.proveedores]

    cliente = TestClient(app)
    assert cliente.get("/proveedores").json() == []

    gestor_de_prueba = crear_gestor([EmbeddingsFalso()])
    app.dependency_overrides[obtener_gestor_embeddings] = lambda: gestor_de_prueba

    assert cliente.get("/proveedores").json() == ["falso"]

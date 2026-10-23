"""Pruebas de integración con los proveedores reales de embeddings.

Hacen llamadas de red y consumen un poco de la cuota gratuita (cinco llamadas en total).
Están excluidas por defecto: ni `pytest` ni la integración continua las ejecutan.

Para correrlas, desde backend/ y con las credenciales cargadas en .env o en el entorno:

    pytest -m integracion tests/integration/providers/test_embeddings_reales.py

Cada prueba se omite sola si faltan las credenciales del proveedor que necesita.

Confirman lo que la documentación no deja cerrado:

- que cada modelo devuelve vectores de la dimensión configurada (768 en Gemini con
  `outputDimensionality`, 1024 en `@cf/baai/bge-m3`);
- que Gemini acepta `taskType` y `outputDimensionality` en el primer nivel de la
  solicitud;
- que las credenciales funcionan en el nivel gratuito.
"""

import asyncio

import pytest

from app.settings import Configuracion
from app.providers import CuotaAgotada
from app.providers.embeddings import GestorEmbeddings, ResultadoEmbedding
from app.providers.embeddings._http import ProveedorEmbeddingsHTTP
from app.providers.embeddings.dependencias import crear_proveedor_embeddings
from app.providers.embeddings.falso import EmbeddingsFalso

pytestmark = pytest.mark.integracion

CONSULTA = "¿Cuándo empiezan las clases?"
DOCUMENTOS = ["Las clases empiezan en marzo.", "Bedelía atiende por la tarde."]


def proveedor_real(nombre: str, config: Configuracion) -> ProveedorEmbeddingsHTTP:
    if nombre not in config.proveedores_embeddings_disponibles:
        pytest.skip(f"Faltan las credenciales de {nombre} para embeddings.")
    return crear_proveedor_embeddings(nombre, config)


def comprobar_vector(vector: list[float], dimension: int) -> None:
    assert len(vector) == dimension
    assert sum(valor * valor for valor in vector) == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("nombre", ["gemini", "cloudflare"])
def test_proveedor_real_vectoriza_consulta_y_documentos(nombre, configuracion_real):
    proveedor = proveedor_real(nombre, configuracion_real)

    async def escenario() -> tuple[list[float], list[list[float]]]:
        try:
            consulta = await proveedor.embed_query(CONSULTA)
            documentos = await proveedor.embed_documents(DOCUMENTOS)
            return consulta, documentos
        finally:
            await proveedor.aclose()

    consulta, documentos = asyncio.run(escenario())

    assert len(documentos) == len(DOCUMENTOS)
    for vector in (consulta, *documentos):
        comprobar_vector(vector, proveedor.dimension)
    # Dos textos distintos no dan el mismo vector.
    assert documentos[0] != documentos[1]


def test_el_gestor_usa_el_respaldo_real_cuando_el_principal_falla(configuracion_real):
    if not configuracion_real.proveedores_embeddings_disponibles:
        pytest.skip("No hay credenciales de ningún proveedor de embeddings.")
    # Se usa como respaldo el último disponible, que es el respaldo configurado.
    nombre = configuracion_real.proveedores_embeddings_disponibles[-1]
    respaldo = proveedor_real(nombre, configuracion_real)
    principal = EmbeddingsFalso([CuotaAgotada()], nombre="principal-simulado")
    gestor = GestorEmbeddings(
        [principal, respaldo],
        reintentos=configuracion_real.embedding_reintentos,
        backoff_base_segundos=configuracion_real.embedding_backoff_base_segundos,
        backoff_maximo_segundos=configuracion_real.embedding_backoff_maximo_segundos,
        presupuesto_segundos=configuracion_real.embedding_presupuesto_segundos,
    )

    async def escenario() -> ResultadoEmbedding:
        try:
            return await gestor.embed_query(CONSULTA)
        finally:
            await respaldo.aclose()

    resultado = asyncio.run(escenario())

    assert principal.llamadas == 1
    assert resultado.proveedor == nombre
    assert resultado.modelo == respaldo.modelo
    comprobar_vector(resultado.vector, respaldo.dimension)

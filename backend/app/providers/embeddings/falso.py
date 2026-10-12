"""Generador de embeddings falso, solo para pruebas y scripts de desarrollo.

No figura en `PROVEEDORES_EMBEDDINGS_VALIDOS` ni en el registro de `dependencias.py`: no
se puede activar por configuración, así que nunca sirve vectores falsos en producción.
"""

import asyncio
import hashlib
from collections.abc import Sequence

from app.providers.base import ErrorProveedor
from app.providers.embeddings.base import ProveedorEmbeddings, Vector, normalizar_l2

MODELO_FALSO = "embeddings-falso"


def vector_falso(texto: str, dimension: int, semilla: str = "") -> Vector:
    """Vector normalizado que depende solo del texto, la dimensión y la semilla."""
    valores: list[float] = []
    bloque = 0
    while len(valores) < dimension:
        resumen = hashlib.sha256(f"{semilla}|{bloque}|{texto}".encode()).digest()
        valores.extend(byte - 127.5 for byte in resumen)
        bloque += 1
    return normalizar_l2(valores[:dimension])


class EmbeddingsFalso(ProveedorEmbeddings):
    """Devuelve vectores deterministas o lanza errores fijados de antemano, sin red.

    Cada elemento de `resultados` es `None` (la llamada responde) o un `ErrorProveedor`
    (se lanza). Se consumen en orden, uno por llamada a `embed_documents` o
    `embed_query`. Cuando la secuencia se agota, **se repite el último elemento**: así
    un solo resultado alcanza para un proveedor que siempre responde o que siempre
    falla.

    Los vectores dependen del texto y del nombre del proveedor: dos proveedores falsos
    con nombres distintos devuelven vectores distintos para el mismo texto, igual que
    los reales.

    Atributos, pensados para las pruebas:
        llamadas: cantidad de llamadas recibidas, entre documentos y consultas.
        ultimos_textos: textos de la última llamada, o una lista vacía si no hubo.
    """

    def __init__(
        self,
        resultados: Sequence[ErrorProveedor | None] = (None,),
        *,
        nombre: str = "falso",
        dimension: int = 8,
        latencia_segundos: float = 0.0,
    ) -> None:
        if not resultados:
            raise ValueError("EmbeddingsFalso necesita una secuencia con al menos un resultado.")
        if dimension <= 0:
            raise ValueError("La dimensión de los embeddings debe ser mayor que cero.")
        self._resultados = tuple(resultados)
        self._nombre = nombre
        self._dimension = dimension
        self._latencia_segundos = latencia_segundos
        self.llamadas = 0
        self.ultimos_textos: list[str] = []

    @property
    def nombre(self) -> str:
        return self._nombre

    @property
    def modelo(self) -> str:
        return MODELO_FALSO

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, textos: Sequence[str]) -> list[Vector]:
        return await self._responder(list(textos))

    async def embed_query(self, texto: str) -> Vector:
        (vector,) = await self._responder([texto])
        return vector

    async def _responder(self, textos: list[str]) -> list[Vector]:
        self.llamadas += 1
        self.ultimos_textos = textos
        if self._latencia_segundos > 0:
            await asyncio.sleep(self._latencia_segundos)

        indice = min(self.llamadas, len(self._resultados)) - 1
        resultado = self._resultados[indice]
        if isinstance(resultado, ErrorProveedor):
            if resultado.proveedor is None:
                resultado.proveedor = self._nombre
            raise resultado
        return [vector_falso(texto, self._dimension, self._nombre) for texto in textos]

"""Base de los generadores de embeddings que se consumen por HTTP con `httpx`.

Reúne lo que es igual en todos: el armado de lotes, la validación de la respuesta y la
normalización de los vectores. Cada proveedor aporta sus URL, su cuerpo y la ubicación
de los vectores dentro de su respuesta.

Privacidad: ningún mensaje de excepción armado acá incluye la clave, los textos ni los
vectores.
"""

from abc import abstractmethod
from collections.abc import Sequence
from typing import Any

import httpx

from app.providers._http import ClienteHTTP
from app.providers.base import ErrorNoRecuperable
from app.providers.embeddings.base import (
    ProveedorEmbeddings,
    Vector,
    normalizar_l2,
    validar_textos,
)

# Textos por llamada. La documentación de los proveedores no publica el máximo de un
# lote para estos modelos, así que este tope es propio y se puede cambiar por
# proveedor al construirlo. Queda por confirmar con las pruebas de integración.
LOTE_MAXIMO_POR_DEFECTO = 100


class ProveedorEmbeddingsHTTP(ClienteHTTP, ProveedorEmbeddings):
    """Generador de embeddings que hace una llamada HTTP con JSON por lote de textos.

    Tiene dos tiempos máximos por llamada:

    - `timeout_segundos`, corto, para la consulta: forma parte de la respuesta del chat.
    - `timeout_documentos_segundos`, más holgado, para cada lote de documentos: se usa
      al indexar, donde no hay nadie esperando. Si no se indica, vale lo mismo que el
      anterior.
    """

    def __init__(
        self,
        api_key: str,
        modelo: str,
        dimension: int,
        timeout_segundos: float,
        *,
        timeout_documentos_segundos: float | None = None,
        lote_maximo: int = LOTE_MAXIMO_POR_DEFECTO,
        cliente: httpx.AsyncClient | None = None,
    ) -> None:
        if dimension <= 0:
            raise ValueError("La dimensión de los embeddings debe ser mayor que cero.")
        if lote_maximo <= 0:
            raise ValueError("El tamaño máximo de lote debe ser mayor que cero.")
        super().__init__(
            api_key=api_key, modelo=modelo, timeout_segundos=timeout_segundos, cliente=cliente
        )
        self._dimension = dimension
        self._lote_maximo = lote_maximo
        self._timeout_documentos_segundos = (
            timeout_segundos if timeout_documentos_segundos is None else timeout_documentos_segundos
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, textos: Sequence[str]) -> list[Vector]:
        lista = validar_textos(textos)
        vectores: list[Vector] = []
        for inicio in range(0, len(lista), self._lote_maximo):
            lote = lista[inicio : inicio + self._lote_maximo]
            datos = await self._solicitar_json(
                self._url_documentos(),
                self._encabezados(),
                self._cuerpo_documentos(lote),
                self._timeout_documentos_segundos,
            )
            vectores.extend(self._normalizar(self._vectores_de_documentos(datos), len(lote)))
        return vectores

    async def embed_query(self, texto: str) -> Vector:
        (consulta,) = validar_textos([texto])
        datos = await self._solicitar_json(
            self._url_consulta(), self._encabezados(), self._cuerpo_consulta(consulta)
        )
        (vector,) = self._normalizar(self._vectores_de_consulta(datos), 1)
        return vector

    def _normalizar(self, crudos: object, esperados: int) -> list[Vector]:
        """Valida los vectores tal como llegaron y los devuelve normalizados.

        Un vector de otra dimensión nunca se devuelve: terminaría en un índice que no
        es el suyo.
        """
        if not isinstance(crudos, list) or len(crudos) != esperados:
            raise self._error_de_estructura()
        vectores: list[Vector] = []
        for crudo in crudos:
            if not isinstance(crudo, list) or not all(_es_numero(valor) for valor in crudo):
                raise self._error_de_estructura()
            if len(crudo) != self._dimension:
                raise self._error_de_dimension(len(crudo))
            try:
                vectores.append(normalizar_l2(crudo))
            except ValueError:
                raise self._error_de_estructura() from None
        return vectores

    def _error_de_dimension(self, recibida: int) -> ErrorNoRecuperable:
        # Es un problema de configuración (modelo o dimensión), no una falla pasajera.
        return ErrorNoRecuperable(
            f"El proveedor {self.nombre} devolvió vectores de dimensión {recibida} y se "
            f"esperaban de {self._dimension}. Revisar el modelo y la dimensión configurados.",
            proveedor=self.nombre,
        )

    @abstractmethod
    def _url_documentos(self) -> str:
        """URL para vectorizar un lote de documentos. No incluye la clave."""

    @abstractmethod
    def _url_consulta(self) -> str:
        """URL para vectorizar una consulta. No incluye la clave."""

    @abstractmethod
    def _encabezados(self) -> dict[str, str]:
        """Encabezados de la solicitud, entre ellos el que lleva la clave."""

    @abstractmethod
    def _cuerpo_documentos(self, textos: Sequence[str]) -> dict[str, Any]:
        """Cuerpo JSON para vectorizar un lote de documentos."""

    @abstractmethod
    def _cuerpo_consulta(self, texto: str) -> dict[str, Any]:
        """Cuerpo JSON para vectorizar una consulta."""

    @abstractmethod
    def _vectores_de_documentos(self, datos: dict[str, Any]) -> object:
        """Extrae de una respuesta de éxito la lista de vectores, sin validarla."""

    @abstractmethod
    def _vectores_de_consulta(self, datos: dict[str, Any]) -> object:
        """Extrae de una respuesta de éxito una lista con el único vector, sin validarla."""


def _es_numero(valor: object) -> bool:
    # `bool` es subclase de `int`, pero un vector no lleva valores lógicos.
    return isinstance(valor, int | float) and not isinstance(valor, bool)

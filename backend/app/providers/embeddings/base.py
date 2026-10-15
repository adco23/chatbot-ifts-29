"""Contrato común de los generadores de embeddings.

Define la interfaz `ProveedorEmbeddings`, los resultados que devuelve el gestor y el
error que se lanza cuando ningún proveedor responde. No hace llamadas de red.

Los errores de un proveedor son los mismos que los de los modelos de lenguaje
(`app.providers.base`): `CuotaAgotada`, `ErrorTemporal` y `ErrorNoRecuperable`.

Doble índice: cada proveedor genera vectores con un modelo y una dimensión propios, que
no son comparables con los del otro. Por eso un resultado informa siempre qué proveedor
lo generó, y nunca se mezclan vectores de proveedores distintos en una misma búsqueda
ni en una misma columna.
"""

import math
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.providers.base import ErrorProveedor

Vector = list[float]


@dataclass(frozen=True)
class ResultadoEmbedding:
    """Vector de una consulta, con el proveedor que lo generó."""

    vector: Vector
    # Nombre del proveedor (ver `ProveedorEmbeddings.nombre`). Indica contra qué índice
    # se debe buscar.
    proveedor: str
    modelo: str
    dimension: int


@dataclass(frozen=True)
class ResultadoEmbeddings:
    """Vectores de un conjunto de documentos, todos generados por el mismo proveedor.

    `vectores` respeta el orden de los textos recibidos.
    """

    vectores: list[Vector]
    proveedor: str
    modelo: str
    dimension: int


def normalizar_l2(valores: Sequence[float]) -> Vector:
    """Devuelve el vector escalado a norma euclídea 1.

    Lanza:
        ValueError: el vector está vacío, tiene valores no finitos o su norma es cero.
    """
    norma = math.hypot(*valores)
    if not math.isfinite(norma) or norma == 0.0:
        raise ValueError("El vector no se puede normalizar: norma nula o no finita.")
    return [valor / norma for valor in valores]


def validar_textos(textos: Sequence[str]) -> list[str]:
    """Comprueba que sea una secuencia de textos con contenido y la devuelve como lista.

    El mensaje del error no incluye los textos.

    Lanza:
        ValueError: se recibió un único texto en lugar de una secuencia, o alguno de
            los textos no es una cadena o está en blanco.
    """
    if isinstance(textos, str):
        raise ValueError("Se esperaba una secuencia de textos, no un único texto.")
    lista = list(textos)
    for texto in lista:
        if not isinstance(texto, str) or not texto.strip():
            raise ValueError("Los textos a vectorizar no pueden estar vacíos.")
    return lista


class EmbeddingsNoDisponibles(Exception):
    """La lanza el gestor cuando ningún proveedor pudo generar los vectores.

    PUNTO DE ENGANCHE DEL ÚLTIMO RECURSO (pendiente, sin implementar): el endpoint
    `/chat` debe capturar esta excepción al vectorizar la consulta y, en lugar de
    devolver un error, pasar al último recurso que se defina para ese caso. Sin vector
    no hay búsqueda semántica posible, así que ese camino no puede depender de ella.

    No hereda de `ErrorProveedor` a propósito: no es la falla de un proveedor sino el
    resultado final del gestor.

    Atributos:
        errores: errores individuales, en el orden en que se intentaron los proveedores.
    """

    def __init__(self, errores: Iterable[ErrorProveedor]) -> None:
        self.errores: list[ErrorProveedor] = list(errores)
        # El resumen usa solo el proveedor y el tipo de error, nunca los mensajes.
        detalle = ", ".join(
            f"{error.proveedor or 'desconocido'}: {type(error).__name__}" for error in self.errores
        )
        mensaje = "Ningún proveedor de embeddings pudo generar los vectores"
        super().__init__(f"{mensaje} ({detalle})." if detalle else f"{mensaje}.")


class SinProveedoresDeEmbeddings(EmbeddingsNoDisponibles):
    """No hay ningún proveedor para intentar (por ejemplo, no se cargó ninguna clave).

    Es una `EmbeddingsNoDisponibles` con la lista de errores vacía y un mensaje propio:
    quien la reciba puede distinguir el caso sin cambiar el contrato.
    """

    def __init__(self) -> None:
        super().__init__([])
        self.args = ("No hay proveedores de embeddings configurados.",)


class ProveedorEmbeddings(ABC):
    """Interfaz que implementan todos los generadores de embeddings.

    Contrato de las implementaciones:

    - Los vectores devueltos están normalizados (norma L2 igual a 1) y tienen
      exactamente `dimension` componentes.
    - `embed_documents` devuelve un vector por texto, en el mismo orden.
    - Lanzan solo excepciones de la jerarquía `ErrorProveedor`. Las propias de la
      librería HTTP se traducen a la subclase que corresponda. La única excepción es
      `ValueError` ante una entrada inválida (textos vacíos), que es un error de quien
      llama y no del proveedor.
    - No reintentan ni cambian de proveedor: de eso se ocupa `GestorEmbeddings`.
    - No registran el contenido de los textos.
    """

    @property
    @abstractmethod
    def nombre(self) -> str:
        """Identificador corto y estable del proveedor (por ejemplo, "gemini")."""

    @property
    @abstractmethod
    def modelo(self) -> str:
        """Identificador del modelo de embeddings configurado."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Cantidad de componentes de los vectores que devuelve."""

    @abstractmethod
    async def embed_documents(self, textos: Sequence[str]) -> list[Vector]:
        """Vectoriza textos que se van a indexar.

        Lanza:
            ErrorProveedor: alguna de sus subclases, según el tipo de falla.
            ValueError: alguno de los textos está vacío.
        """

    @abstractmethod
    async def embed_query(self, texto: str) -> Vector:
        """Vectoriza una consulta para buscar contra el índice de este proveedor.

        Lanza:
            ErrorProveedor: alguna de sus subclases, según el tipo de falla.
            ValueError: el texto está vacío.
        """

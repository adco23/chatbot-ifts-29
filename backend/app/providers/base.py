"""Contrato común de los proveedores de modelos de lenguaje.

Define la interfaz `ProveedorLLM`, los objetos de entrada y salida y la jerarquía de
excepciones que respetan todas las implementaciones. No hace llamadas de red.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class SolicitudLLM:
    """Entrada de una generación de texto.

    Las partes del prompt viajan separadas para que cada proveedor las ubique donde
    corresponda en su propia API.
    """

    instrucciones_sistema: str
    # Fragmentos recuperados ya formateados; puede estar vacío.
    contexto: str
    consulta: str
    # Si son None, cada proveedor usa su valor por defecto.
    temperatura: float | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class RespuestaLLM:
    """Salida de una generación de texto."""

    texto: str
    # Nombre del proveedor que respondió (ver `ProveedorLLM.nombre`).
    proveedor: str
    # Identificador del modelo usado.
    modelo: str


class ErrorProveedor(Exception):
    """Base de los errores que puede lanzar un proveedor de modelos de lenguaje.

    Atributos:
        proveedor: nombre del proveedor que falló, o None si no se conoce.
        recuperable: indica si el gestor puede intentar con otro proveedor. Lo fija
            cada subclase.

    Privacidad: el mensaje de la excepción nunca debe incluir el texto de la solicitud
    ni el de la respuesta, ni completo ni en parte. Tampoco se debe copiar el mensaje
    de error del SDK cuando pueda contenerlos. Alcanza con describir qué falló (por
    ejemplo, el código de estado o el tipo de error).
    """

    recuperable: bool = False
    mensaje_por_defecto: str = "Error del proveedor de modelo de lenguaje."

    def __init__(self, mensaje: str | None = None, *, proveedor: str | None = None) -> None:
        super().__init__(mensaje or self.mensaje_por_defecto)
        self.proveedor = proveedor


class CuotaAgotada(ErrorProveedor):
    """Se alcanzó el límite de uso del proveedor (por ejemplo, un 429)."""

    recuperable = True
    mensaje_por_defecto = "Se agotó la cuota del proveedor."


class ErrorTemporal(ErrorProveedor):
    """Falla transitoria: red, tiempo de espera agotado o error 5xx."""

    recuperable = True
    mensaje_por_defecto = "Falla temporal del proveedor."


class ContenidoBloqueado(ErrorProveedor):
    """El proveedor rechazó la solicitud por sus filtros de seguridad."""

    recuperable = False
    mensaje_por_defecto = "El proveedor bloqueó la solicitud por sus filtros de seguridad."


class ErrorNoRecuperable(ErrorProveedor):
    """Solicitud inválida, clave incorrecta o modelo inexistente."""

    recuperable = False
    mensaje_por_defecto = "Error no recuperable del proveedor."


class TodosLosProveedoresFallaron(Exception):
    """La lanza el gestor cuando ningún proveedor pudo responder.

    No hereda de `ErrorProveedor` a propósito: no es la falla de un proveedor sino el
    resultado final del gestor, y no debe confundirse con ellas en un `except`.

    Atributos:
        errores: errores individuales, en el orden en que se intentaron los proveedores.
    """

    def __init__(self, errores: Iterable[ErrorProveedor]) -> None:
        self.errores: list[ErrorProveedor] = list(errores)
        # El resumen usa solo el proveedor y el tipo de error, nunca los mensajes.
        detalle = ", ".join(
            f"{error.proveedor or 'desconocido'}: {type(error).__name__}" for error in self.errores
        )
        mensaje = "Todos los proveedores de modelo de lenguaje fallaron"
        super().__init__(f"{mensaje} ({detalle})." if detalle else f"{mensaje}.")


class ProveedorLLM(ABC):
    """Interfaz que implementan todos los proveedores de modelos de lenguaje.

    Contrato de las implementaciones:

    - `generar` lanza solo excepciones de la jerarquía `ErrorProveedor`. Las excepciones
      propias del SDK o de la librería HTTP se traducen a la subclase que corresponda.
    - No reintentan internamente: de los reintentos y del respaldo se ocupa el gestor.
    - No registran el contenido del prompt ni el de la respuesta.
    """

    @property
    @abstractmethod
    def nombre(self) -> str:
        """Identificador corto y estable del proveedor (por ejemplo, "gemini")."""

    @abstractmethod
    async def generar(self, solicitud: SolicitudLLM) -> RespuestaLLM:
        """Genera una respuesta para la solicitud.

        Lanza:
            ErrorProveedor: alguna de sus subclases, según el tipo de falla.
        """

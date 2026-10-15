"""Gestor que prueba los proveedores en orden y pasa al respaldo cuando uno falla."""

import asyncio
import logging
import time
from collections.abc import Sequence

from app.providers.base import (
    ErrorProveedor,
    ErrorTemporal,
    ProveedorLLM,
    RespuestaLLM,
    SolicitudLLM,
    TodosLosProveedoresFallaron,
)

logger = logging.getLogger(__name__)


class SinProveedoresConfigurados(TodosLosProveedoresFallaron):
    """No hay ningún proveedor para intentar (por ejemplo, no se cargó ninguna clave).

    Es una `TodosLosProveedoresFallaron` con la lista de errores vacía y un mensaje
    propio, para que quien la reciba pueda distinguir el caso sin cambiar el contrato.
    """

    def __init__(self) -> None:
        super().__init__([])
        self.args = ("No hay proveedores de modelo de lenguaje configurados.",)


class GestorProveedores:
    """Genera una respuesta con el primer proveedor que pueda darla.

    No guarda estado entre llamadas: cada generación recorre la lista desde el
    principio, sin recordar fallas anteriores.
    """

    def __init__(self, proveedores: Sequence[ProveedorLLM], presupuesto_segundos: float) -> None:
        self._proveedores: tuple[ProveedorLLM, ...] = tuple(proveedores)
        self._presupuesto_segundos = presupuesto_segundos

    @property
    def proveedores(self) -> tuple[ProveedorLLM, ...]:
        """Proveedores en el orden en que se intentan."""
        return self._proveedores

    @property
    def presupuesto_segundos(self) -> float:
        """Tiempo total de una generación, contando todos los intentos."""
        return self._presupuesto_segundos

    async def generar_con_respaldo(self, solicitud: SolicitudLLM) -> RespuestaLLM:
        """Devuelve la respuesta del primer proveedor que responda.

        Lanza:
            ErrorProveedor: un error no recuperable (`ContenidoBloqueado` o
                `ErrorNoRecuperable`), sin probar con los proveedores siguientes.
            TodosLosProveedoresFallaron: ningún proveedor respondió dentro del
                presupuesto, o no hay proveedores configurados.
        """
        if not self._proveedores:
            logger.warning("No hay proveedores de modelo de lenguaje configurados.")
            raise SinProveedoresConfigurados()

        inicio = time.monotonic()
        errores: list[ErrorProveedor] = []
        for proveedor in self._proveedores:
            restante = self._presupuesto_segundos - (time.monotonic() - inicio)
            if restante <= 0:
                logger.warning(
                    "Presupuesto de %.2f s agotado: no se intenta con el proveedor %s.",
                    self._presupuesto_segundos,
                    proveedor.nombre,
                )
                break

            inicio_intento = time.monotonic()
            try:
                respuesta = await self._intentar(proveedor, solicitud, restante)
            except ErrorProveedor as error:
                # Solo se registran el proveedor, la categoría y la duración: nunca el
                # mensaje del error ni el contenido de la solicitud.
                logger.warning(
                    "El proveedor %s falló: %s en %.2f s.",
                    proveedor.nombre,
                    type(error).__name__,
                    time.monotonic() - inicio_intento,
                )
                if not error.recuperable:
                    raise
                errores.append(error)
                continue

            logger.info(
                "El proveedor %s respondió en %.2f s.",
                proveedor.nombre,
                time.monotonic() - inicio_intento,
            )
            return respuesta

        raise TodosLosProveedoresFallaron(errores)

    @staticmethod
    async def _intentar(
        proveedor: ProveedorLLM, solicitud: SolicitudLLM, limite_segundos: float
    ) -> RespuestaLLM:
        """Llama al proveedor con el tiempo restante como tope."""
        try:
            async with asyncio.timeout(limite_segundos):
                return await proveedor.generar(solicitud)
        except ErrorProveedor as error:
            if error.proveedor is None:
                error.proveedor = proveedor.nombre
            raise
        except TimeoutError:
            raise ErrorTemporal(
                f"El proveedor {proveedor.nombre} superó el tiempo restante del presupuesto.",
                proveedor=proveedor.nombre,
            ) from None
        except Exception as error:
            # Un proveedor no debería lanzar otra cosa (ver el contrato en base.py). Si
            # pasa, se trata como falla temporal para no dejar sin respuesta a quien
            # consulta. No se encadena la excepción original: podría incluir contenido.
            raise ErrorTemporal(
                f"El proveedor {proveedor.nombre} lanzó una excepción fuera del contrato "
                f"({type(error).__name__}).",
                proveedor=proveedor.nombre,
            ) from None

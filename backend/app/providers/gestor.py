"""Gestor que prueba los proveedores en orden y pasa al respaldo cuando uno falla."""

import asyncio
import logging
import math
import time
from collections.abc import Sequence

from app.providers.base import (
    ContenidoBloqueado,
    ErrorNoRecuperable,
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

    def __init__(
        self, proveedores: Sequence[ProveedorLLM], presupuesto_segundos: float,
        *, reintentos_principal: int = 1, backoff_segundos: float = 0.25,
        motivo_groq_omitido: str | None = None,
    ) -> None:
        if not math.isfinite(presupuesto_segundos) or presupuesto_segundos <= 0:
            raise ValueError("El presupuesto LLM debe ser finito y positivo.")
        if type(reintentos_principal) is not int or reintentos_principal not in (0, 1):
            raise ValueError("Sólo se admite cero o un retry del principal.")
        if not math.isfinite(backoff_segundos) or backoff_segundos < 0:
            raise ValueError("El backoff debe ser finito y no negativo.")
        self._proveedores: tuple[ProveedorLLM, ...] = tuple(proveedores)
        self._presupuesto_segundos = presupuesto_segundos
        self._reintentos_principal = reintentos_principal
        self._backoff_segundos = backoff_segundos
        self._motivo_groq_omitido = (
            None if any(p.nombre == "groq" for p in self._proveedores)
            else "excluido del orden configurado" if motivo_groq_omitido == "excluido del orden configurado"
            else "no configurado"
        )

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
            ContenidoBloqueado: se detiene el flujo, sin retry ni respaldo.
            TodosLosProveedoresFallaron: ningún proveedor respondió dentro del
                presupuesto, o no hay proveedores configurados.
        """
        if not self._proveedores:
            logger.warning("Groq omitido: %s.", self._motivo_groq_omitido)
            logger.warning("No hay proveedores de modelo de lenguaje configurados.")
            raise SinProveedoresConfigurados()

        plazo_global = time.monotonic() + self._presupuesto_segundos
        errores: list[ErrorProveedor] = []
        groq_iniciado = False
        for indice, proveedor in enumerate(self._proveedores):
            restante = plazo_global - time.monotonic()
            if restante <= 0:
                logger.warning(
                    "Presupuesto de %.2f s agotado: no se intenta con el proveedor %s.",
                    self._presupuesto_segundos,
                    proveedor.nombre,
                )
                break

            siguientes = self._proveedores[indice + 1:]
            # Reservar tiempo para el respaldo incluso si el principal se cuelga.
            reserva = min(restante / 2, sum(
                self._timeout_de(p) or restante / (len(siguientes) + 1) for p in siguientes
            )) if siguientes else 0.0
            plazo_proveedor = plazo_global - reserva
            intentos = 1 + (self._reintentos_principal if indice == 0 else 0)
            for intento in range(1, intentos + 1):
                disponible = plazo_proveedor - time.monotonic()
                if disponible <= 0:
                    break
                pendientes = intentos - intento
                if pendientes and disponible > self._backoff_segundos:
                    limite = (disponible - self._backoff_segundos) / (pendientes + 1)
                else:
                    limite = disponible
                timeout = self._timeout_de(proveedor)
                if timeout is not None:
                    limite = min(limite, timeout)
                inicio_intento = time.monotonic()
                if proveedor.nombre == "groq":
                    groq_iniciado = True
                    if indice > 0:
                        # WARNING: el logger de la aplicación puede filtrar INFO.
                        logger.warning(
                            "fallback a Groq iniciado: restante=%.2f s limite=%.2f s.",
                            plazo_global - inicio_intento, limite,
                        )
                try:
                    respuesta = await self._intentar(proveedor, solicitud, limite)
                except ErrorProveedor as error:
                    mensaje = ("Groq falló: proveedor=%s categoria=%s causa=%s http=%s detalle=%s "
                               if proveedor.nombre == "groq" else
                               "El proveedor %s falló: %s causa=%s http=%s detalle=%s ")
                    logger.warning(
                        mensaje +
                        "intento=%d/%d limite=%.2f s en %.2f s.",
                        proveedor.nombre, type(error).__name__, error.causa,
                        error.estado_http, error.detalle, intento, intentos, limite,
                        time.monotonic() - inicio_intento,
                    )
                    if isinstance(error, ContenidoBloqueado):
                        raise
                    if not error.recuperable and not isinstance(error, ErrorNoRecuperable):
                        raise
                    errores.append(error)
                    if (isinstance(error, ErrorTemporal) and pendientes
                            and plazo_proveedor - time.monotonic() > self._backoff_segundos):
                        logger.info("Se reintenta el proveedor %s después de %.2f s.",
                                    proveedor.nombre, self._backoff_segundos)
                        await asyncio.sleep(self._backoff_segundos)
                        continue
                    # 429/cuota y fallos permanentes no se reintentan en este proveedor.
                    break
                logger.info("El proveedor %s respondió en %.2f s (intento=%d).",
                            proveedor.nombre, time.monotonic() - inicio_intento, intento)
                if proveedor.nombre == "groq" and indice > 0:
                    logger.warning(
                        "Groq respondió: generación completada en %.2f s; "
                        "validación posterior del chat pendiente.",
                        time.monotonic() - inicio_intento,
                    )
                return respuesta
        if self._motivo_groq_omitido is not None:
            logger.warning("Groq omitido: %s.", self._motivo_groq_omitido)
        elif not groq_iniciado:
            logger.warning("Groq omitido: presupuesto insuficiente (restante=%.3f s).",
                           plazo_global - time.monotonic())
        raise TodosLosProveedoresFallaron(errores)

    @staticmethod
    def _timeout_de(proveedor: ProveedorLLM) -> float | None:
        valor = getattr(proveedor, "timeout_segundos", None)
        return valor if type(valor) in (int, float) and math.isfinite(valor) and valor > 0 else None

    @staticmethod
    async def _intentar(
        proveedor: ProveedorLLM, solicitud: SolicitudLLM, limite_segundos: float
    ) -> RespuestaLLM:
        """Llama al proveedor con el tiempo restante como tope."""
        try:
            async with asyncio.timeout(limite_segundos):
                respuesta = await proveedor.generar(solicitud)
                if not isinstance(respuesta, RespuestaLLM) or not isinstance(respuesta.texto, str) or not respuesta.texto.strip():
                    raise ErrorTemporal(proveedor=proveedor.nombre, causa="respuesta_invalida")
                return respuesta
        except ErrorProveedor as error:
            if error.proveedor is None:
                error.proveedor = proveedor.nombre
            raise
        except TimeoutError:
            raise ErrorTemporal(
                f"El proveedor {proveedor.nombre} superó el tiempo restante del presupuesto.",
                proveedor=proveedor.nombre,
                causa="timeout", detalle="presupuesto_intento",
            ) from None
        except Exception as error:
            # Un proveedor no debería lanzar otra cosa (ver el contrato en base.py). Si
            # pasa, se trata como falla temporal para no dejar sin respuesta a quien
            # consulta. No se encadena la excepción original: podría incluir contenido.
            raise ErrorTemporal(
                f"El proveedor {proveedor.nombre} lanzó una excepción fuera del contrato "
                f"({type(error).__name__}).",
                proveedor=proveedor.nombre,
                causa="otra", detalle="excepcion_inesperada",
            ) from None

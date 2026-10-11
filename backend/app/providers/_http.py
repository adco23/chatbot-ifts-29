"""Base HTTP compartida por los proveedores que se consumen con `httpx`.

Concentra lo que es igual en todos: el cliente, el tiempo máximo total, la traducción
de las fallas de transporte y el mapeo general de códigos de estado. Cada proveedor
aporta su URL, su cuerpo y la lectura de su respuesta.

Privacidad: ningún mensaje de excepción armado acá incluye la clave, el texto de la
solicitud ni el cuerpo de la respuesta. Las excepciones de `httpx` se descartan con
`from None` para que tampoco viajen encadenadas.
"""

import asyncio
import ssl
from abc import abstractmethod
from typing import Any

import httpx

from app.providers.base import (
    CuotaAgotada,
    ErrorNoRecuperable,
    ErrorProveedor,
    ErrorTemporal,
    ProveedorLLM,
    RespuestaLLM,
    SolicitudLLM,
)

# Fallas de transporte que no se arreglan reintentando ni cambiando de momento:
# indican una solicitud mal armada de nuestro lado.
_ERRORES_DE_TRANSPORTE_PERMANENTES = (httpx.UnsupportedProtocol, httpx.LocalProtocolError)


def error_por_estado(estado: int, proveedor: str) -> ErrorProveedor:
    """Traduce un código de estado HTTP que no es de éxito al error del contrato.

    Es el mapeo general; cada proveedor resuelve antes sus casos particulares.
    """
    mensaje = f"El proveedor {proveedor} respondió con el estado HTTP {estado}."
    if estado == 429:
        return CuotaAgotada(mensaje, proveedor=proveedor)
    # 408 es un tiempo de espera agotado del lado del servidor: es transitorio.
    if estado == 408 or estado >= 500:
        return ErrorTemporal(mensaje, proveedor=proveedor)
    if 400 <= estado < 500:
        return ErrorNoRecuperable(mensaje, proveedor=proveedor)
    # 1xx y 3xx no son respuestas esperadas de estas APIs.
    return ErrorTemporal(mensaje, proveedor=proveedor)


def codigos_de_error(respuesta: httpx.Response) -> set[str]:
    """Extrae los identificadores de error del cuerpo, en minúsculas.

    Lee solo los campos cortos que clasifican el error (`code`, `status`, `type`) y
    nunca `message`, que puede repetir parte de la solicitud.
    """
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return set()
    error = cuerpo.get("error") if isinstance(cuerpo, dict) else None
    if not isinstance(error, dict):
        return set()
    return {
        valor.lower()
        for campo in ("code", "status", "type")
        if isinstance(valor := error.get(campo), str)
    }


def _es_falla_de_certificado(error: BaseException) -> bool:
    """Indica si la falla de transporte viene de la verificación del certificado TLS."""
    actual: BaseException | None = error
    while actual is not None:
        if isinstance(actual, ssl.SSLCertVerificationError):
            return True
        actual = actual.__cause__ or actual.__context__
    return False


class ProveedorHTTP(ProveedorLLM):
    """Proveedor que hace una única llamada HTTP con JSON por generación."""

    def __init__(
        self,
        api_key: str,
        modelo: str,
        timeout_segundos: float,
        cliente: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._modelo = modelo
        self._timeout_segundos = timeout_segundos
        # Un cliente inyectado es de quien lo creó: `aclose` no lo cierra.
        self._cliente_propio = cliente is None
        self._cliente = cliente or httpx.AsyncClient(timeout=timeout_segundos)

    @property
    def modelo(self) -> str:
        """Modelo configurado para las solicitudes."""
        return self._modelo

    def __repr__(self) -> str:
        # Sin la clave: este texto puede terminar en un log o en un traceback.
        return f"{type(self).__name__}(modelo={self._modelo!r})"

    async def aclose(self) -> None:
        """Cierra el cliente HTTP si lo creó este proveedor."""
        if self._cliente_propio:
            await self._cliente.aclose()

    async def generar(self, solicitud: SolicitudLLM) -> RespuestaLLM:
        respuesta = await self._enviar(self._url(), self._encabezados(), self._cuerpo(solicitud))
        if not respuesta.is_success:
            raise self._error_de_estado(respuesta)
        try:
            datos = respuesta.json()
        except ValueError:
            raise self._error_de_estructura() from None
        if not isinstance(datos, dict):
            raise self._error_de_estructura()
        return self._interpretar(datos)

    async def _enviar(
        self, url: str, encabezados: dict[str, str], cuerpo: dict[str, Any]
    ) -> httpx.Response:
        """Hace el POST y traduce cualquier falla de transporte."""
        try:
            # El timeout de httpx se aplica por etapa (conexión, lectura, escritura);
            # asyncio.timeout garantiza además el tope total del proveedor.
            async with asyncio.timeout(self._timeout_segundos):
                return await self._cliente.post(
                    url, headers=encabezados, json=cuerpo, timeout=self._timeout_segundos
                )
        except (TimeoutError, httpx.TimeoutException):
            raise ErrorTemporal(
                f"El proveedor {self.nombre} no respondió en {self._timeout_segundos:g} s.",
                proveedor=self.nombre,
            ) from None
        except _ERRORES_DE_TRANSPORTE_PERMANENTES as error:
            raise self._error_al_armar(error) from None
        except httpx.TransportError as error:
            detalle = type(error).__name__
            if _es_falla_de_certificado(error):
                # Suele pasar cuando un antivirus o un proxy inspecciona el tráfico
                # HTTPS con un certificado propio (ver docs/proveedores-llm.md).
                detalle += ": no se pudo verificar el certificado TLS"
            raise ErrorTemporal(
                f"Falla de conexión con el proveedor {self.nombre} ({detalle}).",
                proveedor=self.nombre,
            ) from None
        except Exception as error:
            # Por ejemplo, una URL inválida por el nombre del modelo o una clave con
            # caracteres que no admite un encabezado.
            raise self._error_al_armar(error) from None

    def _error_al_armar(self, error: Exception) -> ErrorNoRecuperable:
        return ErrorNoRecuperable(
            f"No se pudo armar la solicitud al proveedor {self.nombre} ({type(error).__name__}).",
            proveedor=self.nombre,
        )

    def _error_de_estructura(self) -> ErrorTemporal:
        return ErrorTemporal(
            f"El proveedor {self.nombre} devolvió una respuesta con estructura inesperada.",
            proveedor=self.nombre,
        )

    def _error_de_texto_vacio(self, motivo: object = None) -> ErrorTemporal:
        detalle = f" (motivo de fin: {motivo})" if isinstance(motivo, str) and motivo else ""
        return ErrorTemporal(
            f"El proveedor {self.nombre} devolvió una respuesta sin texto{detalle}.",
            proveedor=self.nombre,
        )

    def _error_de_estado(self, respuesta: httpx.Response) -> ErrorProveedor:
        """Traduce una respuesta que no es de éxito. Los proveedores lo especializan."""
        return error_por_estado(respuesta.status_code, self.nombre)

    @abstractmethod
    def _url(self) -> str:
        """URL del endpoint de generación. No incluye la clave."""

    @abstractmethod
    def _encabezados(self) -> dict[str, str]:
        """Encabezados de la solicitud, entre ellos el que lleva la clave."""

    @abstractmethod
    def _cuerpo(self, solicitud: SolicitudLLM) -> dict[str, Any]:
        """Cuerpo JSON de la solicitud en el formato del proveedor."""

    @abstractmethod
    def _interpretar(self, datos: dict[str, Any]) -> RespuestaLLM:
        """Lee una respuesta de éxito o lanza el `ErrorProveedor` que corresponda."""

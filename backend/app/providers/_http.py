"""Base HTTP compartida por los proveedores que se consumen con `httpx`.

Concentra lo que es igual en todos: el cliente, el tiempo máximo total, la traducción
de las fallas de transporte y el mapeo general de códigos de estado. Cada proveedor
aporta su URL, su cuerpo y la lectura de su respuesta.

`ClienteHTTP` es el transporte, sin saber qué se le pide al proveedor. Lo comparten los
proveedores de modelos de lenguaje (`ProveedorHTTP`, en este módulo) y los de
embeddings (`app.providers.embeddings`).

Privacidad: ningún mensaje de excepción armado acá incluye la clave, el texto de la
solicitud ni el cuerpo de la respuesta. Las excepciones de `httpx` se descartan con
`from None` para que tampoco viajen encadenadas.
"""

import asyncio
import ssl
from abc import ABC, abstractmethod
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


class ClienteHTTP(ABC):
    """Hace llamadas HTTP con JSON a un proveedor y traduce sus fallas al contrato.

    No sabe qué se le pide al proveedor: de eso se ocupan las subclases.
    """

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
    @abstractmethod
    def nombre(self) -> str:
        """Identificador corto y estable del proveedor (por ejemplo, "gemini")."""

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

    async def _solicitar_json(
        self,
        url: str,
        encabezados: dict[str, str],
        cuerpo: dict[str, Any],
        timeout_segundos: float | None = None,
    ) -> dict[str, Any]:
        """Hace el POST y devuelve el objeto JSON de una respuesta de éxito.

        Lanza:
            ErrorProveedor: falla de transporte, estado que no es de éxito o cuerpo que
                no es un objeto JSON.
        """
        respuesta = await self._enviar(url, encabezados, cuerpo, timeout_segundos)
        if not respuesta.is_success:
            raise self._error_de_estado(respuesta)
        try:
            datos = respuesta.json()
        except ValueError:
            raise self._error_de_estructura() from None
        if not isinstance(datos, dict):
            raise self._error_de_estructura()
        return datos

    async def _enviar(
        self,
        url: str,
        encabezados: dict[str, str],
        cuerpo: dict[str, Any],
        timeout_segundos: float | None = None,
    ) -> httpx.Response:
        """Hace el POST y traduce cualquier falla de transporte.

        `timeout_segundos` reemplaza, solo para esta llamada, el tiempo máximo del
        proveedor.
        """
        limite = self._timeout_segundos if timeout_segundos is None else timeout_segundos
        try:
            # El timeout de httpx se aplica por etapa (conexión, lectura, escritura);
            # asyncio.timeout garantiza además el tope total del proveedor.
            async with asyncio.timeout(limite):
                return await self._cliente.post(
                    url, headers=encabezados, json=cuerpo, timeout=limite
                )
        except (TimeoutError, httpx.TimeoutException):
            raise ErrorTemporal(
                f"El proveedor {self.nombre} no respondió en {limite:g} s.",
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

    def _error_de_estado(self, respuesta: httpx.Response) -> ErrorProveedor:
        """Traduce una respuesta que no es de éxito. Los proveedores lo especializan."""
        return error_por_estado(respuesta.status_code, self.nombre)


class ProveedorHTTP(ClienteHTTP, ProveedorLLM):
    """Proveedor que hace una única llamada HTTP con JSON por generación."""

    @property
    def timeout_segundos(self) -> float:
        return self._timeout_segundos

    async def generar(self, solicitud: SolicitudLLM) -> RespuestaLLM:
        try:
            url, encabezados, cuerpo = self._url(), self._encabezados(), self._cuerpo(solicitud)
        except Exception:
            raise ErrorNoRecuperable(proveedor=self.nombre, causa="solicitud_invalida") from None
        datos = await self._solicitar_json(url, encabezados, cuerpo)
        try:
            return self._interpretar(datos)
        except ErrorProveedor:
            raise
        except Exception:
            raise self._error_de_estructura() from None

    async def _enviar(self, *args, **kwargs) -> httpx.Response:
        # El transporte compartido queda intacto. Sólo el LLM añade diagnóstico.
        try:
            return await super()._enviar(*args, **kwargs)
        except ErrorProveedor as error:
            original = error.__context__
            if isinstance(original, (TimeoutError, httpx.TimeoutException)):
                error.causa = "timeout"
                error.detalle = next((nombre for clase, nombre in (
                    (httpx.ConnectTimeout, "connect"), (httpx.ReadTimeout, "read"),
                    (httpx.WriteTimeout, "write"), (httpx.PoolTimeout, "pool"),
                ) if isinstance(original, clase)), "total")
            elif isinstance(original, httpx.TransportError) and not isinstance(error, ErrorNoRecuperable):
                error.causa = "conexion"
                error.detalle = "tls" if _es_falla_de_certificado(original) else None
            elif isinstance(error, ErrorNoRecuperable):
                error.causa = "solicitud_invalida"
            raise

    def _con_estado(self, error: ErrorProveedor, estado: int) -> ErrorProveedor:
        error.estado_http = estado
        error.causa = (
            "http_429" if estado == 429 else "cuota" if isinstance(error, CuotaAgotada)
            else "http_5xx" if estado >= 500 else "timeout" if estado == 408
            else "http_4xx" if 400 <= estado < 500 else "http_otro"
        )
        return error

    def _error_de_estructura(self) -> ErrorTemporal:
        return ErrorTemporal(proveedor=self.nombre, causa="respuesta_invalida", detalle="estructura")

    def _error_de_texto_vacio(self, motivo: object = None) -> ErrorTemporal:
        return ErrorTemporal(
            f"El proveedor {self.nombre} devolvió una respuesta sin texto.",
            proveedor=self.nombre, causa="respuesta_invalida",
            detalle=motivo if isinstance(motivo, str) else "texto_vacio",
        )

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

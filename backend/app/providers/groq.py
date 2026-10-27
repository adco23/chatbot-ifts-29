"""Proveedor de Groq mediante su API REST compatible con OpenAI (`chat/completions`).

Formatos según la documentación consultada el 2026-10-11:
https://console.groq.com/docs/api-reference y https://console.groq.com/docs/errors
"""

from typing import Any

import httpx

from app.providers._http import ProveedorHTTP, codigos_de_error, error_por_estado
from app.providers.base import (
    ContenidoBloqueado,
    CuotaAgotada,
    ErrorProveedor,
    ErrorTemporal,
    RespuestaLLM,
    SolicitudLLM,
)
from app.providers.formato import armar_mensaje_usuario

URL_BASE = "https://api.groq.com/openai/v1"

_CODIGOS_DE_CUOTA = frozenset({"rate_limit_exceeded"})


class ProveedorGroq(ProveedorHTTP):
    """Genera texto con un modelo alojado en Groq."""

    @property
    def nombre(self) -> str:
        return "groq"

    def _url(self) -> str:
        return f"{URL_BASE}/chat/completions"

    def _encabezados(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _cuerpo(self, solicitud: SolicitudLLM) -> dict[str, Any]:
        mensajes: list[dict[str, str]] = []
        if solicitud.instrucciones_sistema.strip():
            mensajes.append({"role": "system", "content": solicitud.instrucciones_sistema})
        mensajes.append({"role": "user", "content": armar_mensaje_usuario(solicitud)})
        cuerpo: dict[str, Any] = {"model": self._modelo, "messages": mensajes}
        if solicitud.temperatura is not None:
            cuerpo["temperature"] = solicitud.temperatura
        if solicitud.max_tokens is not None:
            # `max_tokens` está marcado como obsoleto en la referencia de la API.
            cuerpo["max_completion_tokens"] = solicitud.max_tokens
        return cuerpo

    def _error_de_estado(self, respuesta: httpx.Response) -> ErrorProveedor:
        estado = respuesta.status_code
        # 413 con el código `rate_limit_exceeded`: la solicitud sola supera el límite
        # de tokens por minuto del plan. Es un límite de uso (otro proveedor sí puede
        # responder), así que va como CuotaAgotada. Este caso no figura en la página de
        # errores: es comportamiento observado de la API. Un 413 sin ese código queda
        # como ErrorNoRecuperable.
        if estado == 413 and codigos_de_error(respuesta) & _CODIGOS_DE_CUOTA:
            return self._con_estado(CuotaAgotada(
                f"El proveedor {self.nombre} informó límite de uso (estado HTTP {estado}).",
                proveedor=self.nombre,
            ), estado)
        # 498 es un código propio de Groq: capacidad agotada del servicio («intentar
        # más tarde»). Aunque sea 4xx, es transitorio.
        if estado == 498:
            return self._con_estado(ErrorTemporal(
                f"El proveedor {self.nombre} respondió con el estado HTTP {estado}.",
                proveedor=self.nombre,
            ), estado)
        # 401, 403, 404 y 422 quedan en el mapeo general como ErrorNoRecuperable.
        return self._con_estado(error_por_estado(estado, self.nombre), estado)

    def _interpretar(self, datos: dict[str, Any]) -> RespuestaLLM:
        opciones = datos.get("choices")
        if not isinstance(opciones, list) or not opciones:
            raise self._error_de_estructura()
        opcion = opciones[0]
        if not isinstance(opcion, dict):
            raise self._error_de_estructura()

        motivo = opcion.get("finish_reason")
        if motivo == "content_filter":
            raise ContenidoBloqueado(
                f"El proveedor {self.nombre} bloqueó la solicitud por sus filtros ({motivo}).",
                proveedor=self.nombre, causa="bloqueo", detalle="content_filter",
            )

        mensaje = opcion.get("message")
        if not isinstance(mensaje, dict):
            raise self._error_de_estructura()
        # Solo se usa `content`; el campo `reasoning` de los modelos con razonamiento
        # se ignora.
        texto = mensaje.get("content")
        if texto is None:
            texto = ""
        if not isinstance(texto, str):
            raise self._error_de_estructura()
        if not texto.strip():
            raise self._error_de_texto_vacio(motivo)

        modelo_usado = datos.get("model")
        return RespuestaLLM(
            texto=texto,
            proveedor=self.nombre,
            modelo=modelo_usado if isinstance(modelo_usado, str) and modelo_usado else self._modelo,
        )

"""Proveedor de Gemini mediante la API REST de Google (`generateContent`).

Formatos según la documentación consultada el 2026-10-11:
https://ai.google.dev/api/generate-content y https://ai.google.dev/gemini-api/docs/api-errors
"""

from typing import Any
from urllib.parse import quote

import httpx

from app.providers._http import ProveedorHTTP, codigos_de_error, error_por_estado
from app.providers.base import (
    ContenidoBloqueado,
    CuotaAgotada,
    ErrorProveedor,
    RespuestaLLM,
    SolicitudLLM,
)
from app.providers.formato import armar_mensaje_usuario

URL_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Identificadores de error que indican límite de uso. La documentación los asocia
# siempre al estado 429; se revisan igual por si llegan con otro estado.
_CODIGOS_DE_CUOTA = frozenset(
    {"resource_exhausted", "rate_limit_exceeded", "quota_exceeded", "too_many_requests"}
)

# Valores de `finishReason` que son un rechazo de los filtros del proveedor.
# RECITATION, LANGUAGE, OTHER y MAX_TOKENS no lo son: si dejan la respuesta sin
# texto se tratan como falla temporal, para que el gestor pruebe con el respaldo.
_FINES_POR_BLOQUEO = frozenset({"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"})


class ProveedorGemini(ProveedorHTTP):
    """Genera texto con un modelo de Gemini."""

    @property
    def nombre(self) -> str:
        return "gemini"

    def _url(self) -> str:
        return f"{URL_BASE}/models/{quote(self._modelo, safe='')}:generateContent"

    def _encabezados(self) -> dict[str, str]:
        # La clave va por encabezado y no como parámetro `?key=`, para que no quede
        # en la URL ni en los mensajes de error de httpx.
        return {"x-goog-api-key": self._api_key}

    def _cuerpo(self, solicitud: SolicitudLLM) -> dict[str, Any]:
        cuerpo: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": armar_mensaje_usuario(solicitud)}]}]
        }
        if solicitud.instrucciones_sistema.strip():
            cuerpo["systemInstruction"] = {"parts": [{"text": solicitud.instrucciones_sistema}]}
        configuracion: dict[str, Any] = {}
        if solicitud.temperatura is not None:
            configuracion["temperature"] = solicitud.temperatura
        if solicitud.max_tokens is not None:
            configuracion["maxOutputTokens"] = solicitud.max_tokens
        if configuracion:
            cuerpo["generationConfig"] = configuracion
        return cuerpo

    def _error_de_estado(self, respuesta: httpx.Response) -> ErrorProveedor:
        estado = respuesta.status_code
        if codigos_de_error(respuesta) & _CODIGOS_DE_CUOTA:
            return CuotaAgotada(
                f"El proveedor {self.nombre} informó límite de uso (estado HTTP {estado}).",
                proveedor=self.nombre,
            )
        # Casos revisados que quedan en el mapeo general:
        # - 400 FAILED_PRECONDITION (nivel gratuito no disponible en la región o
        #   facturación sin habilitar): es un problema de la cuenta, no de cuota, y no
        #   se resuelve solo. Queda como ErrorNoRecuperable.
        # - 403 PERMISSION_DENIED: la clave no tiene acceso. La cuota agotada llega
        #   siempre como 429, así que el 403 queda como ErrorNoRecuperable.
        # - 504 DEADLINE_EXCEEDED: es un 5xx, queda como ErrorTemporal.
        return error_por_estado(estado, self.nombre)

    def _interpretar(self, datos: dict[str, Any]) -> RespuestaLLM:
        # Si el filtro actúa sobre el prompt, no hay candidatos y el motivo viene en
        # `promptFeedback.blockReason`.
        observaciones = datos.get("promptFeedback")
        if isinstance(observaciones, dict) and observaciones.get("blockReason"):
            raise self._error_de_bloqueo(observaciones["blockReason"])

        candidatos = datos.get("candidates")
        if not isinstance(candidatos, list) or not candidatos:
            raise self._error_de_estructura()
        candidato = candidatos[0]
        if not isinstance(candidato, dict):
            raise self._error_de_estructura()

        motivo = candidato.get("finishReason")
        if motivo in _FINES_POR_BLOQUEO:
            raise self._error_de_bloqueo(motivo)

        texto = self._texto_del_candidato(candidato)
        if not texto.strip():
            raise self._error_de_texto_vacio(motivo)

        modelo_usado = datos.get("modelVersion")
        return RespuestaLLM(
            texto=texto,
            proveedor=self.nombre,
            modelo=modelo_usado if isinstance(modelo_usado, str) and modelo_usado else self._modelo,
        )

    def _texto_del_candidato(self, candidato: dict[str, Any]) -> str:
        """Une el texto de las partes, sin las de razonamiento (`thought`)."""
        contenido = candidato.get("content")
        if contenido is None:
            # Un candidato cortado antes de generar no trae `content`.
            return ""
        if not isinstance(contenido, dict):
            raise self._error_de_estructura()
        partes = contenido.get("parts", [])
        if not isinstance(partes, list):
            raise self._error_de_estructura()
        return "".join(
            parte["text"]
            for parte in partes
            if isinstance(parte, dict)
            and isinstance(parte.get("text"), str)
            and not parte.get("thought")
        )

    def _error_de_bloqueo(self, motivo: object) -> ContenidoBloqueado:
        # El motivo es un valor de enumeración del proveedor, no texto del usuario.
        detalle = motivo if isinstance(motivo, str) else "sin detalle"
        return ContenidoBloqueado(
            f"El proveedor {self.nombre} bloqueó la solicitud por sus filtros ({detalle}).",
            proveedor=self.nombre,
        )

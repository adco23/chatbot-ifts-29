"""Embeddings de Cloudflare Workers AI mediante su API REST.

Se llama por HTTP con un token de API, no desde un Worker. Formatos según la
documentación consultada el 2026-10-11:
https://developers.cloudflare.com/workers-ai/get-started/rest-api/ ,
https://developers.cloudflare.com/workers-ai/models/bge-m3/ y
https://developers.cloudflare.com/workers-ai/platform/errors/

Notas sobre `@cf/baai/bge-m3`, el modelo por defecto:

- La solicitud lleva los textos en `text` y la respuesta trae los vectores en
  `result.data`, dentro del sobre común de la API (`success`, `errors`, `messages`).
- No necesita instrucciones ni prefijos para las consultas: documentos y consultas se
  vectorizan igual.
- Ni Cloudflare ni la ficha del modelo indican que los vectores lleguen normalizados:
  la base los normaliza siempre.
- La cuota gratuita agotada y la falta de capacidad llegan como 429.

Pendiente de confirmar con una llamada real (`pytest -m integracion`): la página del
modelo en Cloudflare no publica la dimensión de salida. La ficha de BAAI indica 1024.
Si fuera otra, la validación de la base rechazaría la respuesta.
"""

from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

import httpx

from app.providers.embeddings._http import LOTE_MAXIMO_POR_DEFECTO, ProveedorEmbeddingsHTTP

URL_BASE = "https://api.cloudflare.com/client/v4"


class EmbeddingsCloudflare(ProveedorEmbeddingsHTTP):
    """Genera embeddings con un modelo de Cloudflare Workers AI.

    `api_key` es el token de API de Cloudflare, con permiso de Workers AI.
    """

    def __init__(
        self,
        api_key: str,
        modelo: str,
        dimension: int,
        timeout_segundos: float,
        *,
        account_id: str,
        timeout_documentos_segundos: float | None = None,
        lote_maximo: int = LOTE_MAXIMO_POR_DEFECTO,
        cliente: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            modelo=modelo,
            dimension=dimension,
            timeout_segundos=timeout_segundos,
            timeout_documentos_segundos=timeout_documentos_segundos,
            lote_maximo=lote_maximo,
            cliente=cliente,
        )
        self._account_id = account_id

    @property
    def nombre(self) -> str:
        return "cloudflare"

    def _url_documentos(self) -> str:
        # El nombre del modelo forma parte de la ruta con sus barras y su arroba
        # (`@cf/baai/bge-m3`), así que esos caracteres no se codifican.
        cuenta = quote(self._account_id, safe="")
        return f"{URL_BASE}/accounts/{cuenta}/ai/run/{quote(self._modelo, safe='@/')}"

    def _url_consulta(self) -> str:
        return self._url_documentos()

    def _encabezados(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _cuerpo_documentos(self, textos: Sequence[str]) -> dict[str, Any]:
        return {"text": list(textos)}

    def _cuerpo_consulta(self, texto: str) -> dict[str, Any]:
        return self._cuerpo_documentos([texto])

    def _vectores_de_documentos(self, datos: dict[str, Any]) -> object:
        # Un sobre con `success` en falso no trae vectores aunque el estado sea 200.
        if datos.get("success") is False:
            return None
        resultado = datos.get("result")
        return resultado.get("data") if isinstance(resultado, dict) else None

    def _vectores_de_consulta(self, datos: dict[str, Any]) -> object:
        return self._vectores_de_documentos(datos)

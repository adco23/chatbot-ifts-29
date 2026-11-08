"""Embeddings de Gemini mediante la API REST de Google.

Formatos según la documentación consultada el 2026-10-11:
https://ai.google.dev/gemini-api/docs/embeddings y https://ai.google.dev/api/embeddings

Notas sobre `gemini-embedding-001`, el modelo por defecto:

- Admite entre 128 y 3072 dimensiones. Solo los vectores de 3072 llegan normalizados:
  con cualquier otra dimensión hay que normalizar a mano, cosa que la base hace siempre.
- Admite tipos de tarea: `RETRIEVAL_DOCUMENT` al indexar y `RETRIEVAL_QUERY` al
  consultar. `gemini-embedding-2` no los admite, así que cambiar de modelo obliga a
  revisar este módulo.
- Acepta hasta 2.048 tokens por texto. El recorte de los fragmentos es tarea de quien
  llama.
- Tiene baja anunciada para el 14 de mayo de 2028.

Pendientes de confirmar con una llamada real (`pytest -m integracion`):

- `taskType` y `outputDimensionality` se envían en el primer nivel de la solicitud,
  como en el ejemplo de la guía para este modelo. La referencia de la API los marca
  como obsoletos en favor de `embedContentConfig`. Si Google los retirara, la respuesta
  llegaría con otra dimensión y la validación de la base la rechazaría.
- Los límites del nivel gratuito ya no figuran en la documentación: se consultan en
  Google AI Studio.
"""

from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

import httpx

from app.providers._http import codigos_de_error, error_por_estado
from app.providers.base import CuotaAgotada, ErrorProveedor
from app.providers.embeddings._http import ProveedorEmbeddingsHTTP
from app.providers.gemini import URL_BASE

TAREA_DOCUMENTO = "RETRIEVAL_DOCUMENT"
TAREA_CONSULTA = "RETRIEVAL_QUERY"

# Identificadores de error que indican límite de uso. Son los mismos que revisa el
# proveedor de modelo de lenguaje de Gemini (app/providers/gemini.py).
_CODIGOS_DE_CUOTA = frozenset(
    {"resource_exhausted", "rate_limit_exceeded", "quota_exceeded", "too_many_requests"}
)


class EmbeddingsGemini(ProveedorEmbeddingsHTTP):
    """Genera embeddings con un modelo de Gemini."""

    @property
    def nombre(self) -> str:
        return "gemini"

    def _url_documentos(self) -> str:
        return f"{URL_BASE}/models/{quote(self._modelo, safe='')}:batchEmbedContents"

    def _url_consulta(self) -> str:
        return f"{URL_BASE}/models/{quote(self._modelo, safe='')}:embedContent"

    def _encabezados(self) -> dict[str, str]:
        # La clave va por encabezado y no como parámetro `?key=`, para que no quede
        # en la URL ni en los mensajes de error de httpx.
        return {"x-goog-api-key": self._api_key}

    def _cuerpo_documentos(self, textos: Sequence[str]) -> dict[str, Any]:
        return {"requests": [self._solicitud(texto, TAREA_DOCUMENTO) for texto in textos]}

    def _cuerpo_consulta(self, texto: str) -> dict[str, Any]:
        return self._solicitud(texto, TAREA_CONSULTA)

    def _solicitud(self, texto: str, tarea: str) -> dict[str, Any]:
        return {
            # En un lote, el modelo de cada solicitud debe coincidir con el de la URL.
            "model": f"models/{self._modelo}",
            "content": {"parts": [{"text": texto}]},
            "taskType": tarea,
            "outputDimensionality": self._dimension,
        }

    def _vectores_de_documentos(self, datos: dict[str, Any]) -> object:
        embeddings = datos.get("embeddings")
        if not isinstance(embeddings, list):
            return None
        return [self._valores(embedding) for embedding in embeddings]

    def _vectores_de_consulta(self, datos: dict[str, Any]) -> object:
        return [self._valores(datos.get("embedding"))]

    @staticmethod
    def _valores(embedding: object) -> object:
        return embedding.get("values") if isinstance(embedding, dict) else None

    def _error_de_estado(self, respuesta: httpx.Response) -> ErrorProveedor:
        estado = respuesta.status_code
        if codigos_de_error(respuesta) & _CODIGOS_DE_CUOTA:
            return CuotaAgotada(
                f"El proveedor {self.nombre} informó límite de uso (estado HTTP {estado}).",
                proveedor=self.nombre,
            )
        # 400 por clave inválida o FAILED_PRECONDITION, 403 y 404 quedan en el mapeo
        # general como ErrorNoRecuperable: son problemas de configuración.
        return error_por_estado(estado, self.nombre)

"""Proveedor falso, solo para pruebas y scripts de desarrollo.

No figura en `PROVEEDORES_LLM_VALIDOS` ni en el registro de `dependencias.py`: no se
puede activar por configuración, así que nunca sirve respuestas falsas en producción.
"""

import asyncio
from collections.abc import Sequence

from app.providers.base import ErrorProveedor, ProveedorLLM, RespuestaLLM, SolicitudLLM

MODELO_FALSO = "modelo-falso"


class ProveedorFalso(ProveedorLLM):
    """Devuelve resultados fijados de antemano, sin red.

    Cada elemento de `resultados` es un texto (se devuelve como respuesta) o un
    `ErrorProveedor` (se lanza). Se consumen en orden, uno por llamada. Cuando la
    secuencia se agota, **se repite el último elemento**: así un solo resultado alcanza
    para un proveedor que siempre responde o que siempre falla.

    Atributos, pensados para las pruebas:
        llamadas: cantidad de veces que se llamó a `generar`.
        ultima_solicitud: última solicitud recibida, o None si no hubo llamadas.
    """

    def __init__(
        self,
        resultados: Sequence[str | ErrorProveedor],
        *,
        nombre: str = "falso",
        latencia_segundos: float = 0.0,
    ) -> None:
        if isinstance(resultados, str) or not resultados:
            raise ValueError("ProveedorFalso necesita una secuencia con al menos un resultado.")
        self._resultados = tuple(resultados)
        self._nombre = nombre
        self._latencia_segundos = latencia_segundos
        self.llamadas = 0
        self.ultima_solicitud: SolicitudLLM | None = None

    @property
    def nombre(self) -> str:
        return self._nombre

    async def generar(self, solicitud: SolicitudLLM) -> RespuestaLLM:
        self.llamadas += 1
        self.ultima_solicitud = solicitud
        if self._latencia_segundos > 0:
            await asyncio.sleep(self._latencia_segundos)

        indice = min(self.llamadas, len(self._resultados)) - 1
        resultado = self._resultados[indice]
        if isinstance(resultado, ErrorProveedor):
            if resultado.proveedor is None:
                resultado.proveedor = self._nombre
            raise resultado
        return RespuestaLLM(texto=resultado, proveedor=self._nombre, modelo=MODELO_FALSO)

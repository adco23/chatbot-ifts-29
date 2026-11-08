"""Utilidades compartidas por las pruebas de los proveedores concretos."""

import json
from collections.abc import Callable
from typing import Any

import httpx

from app.providers import SolicitudLLM

# Valores ficticios y fáciles de buscar en un mensaje de error.
CLAVE = "clave-ficticia-secreta"
INSTRUCCIONES = "Respondé solo con el contexto provisto."
CONTEXTO = "Las clases empiezan el 16 de marzo."
CONSULTA = "¿Cuándo empiezan las clases de primer año?"

Manejador = Callable[[httpx.Request], httpx.Response]


def crear_solicitud(**cambios: Any) -> SolicitudLLM:
    valores: dict[str, Any] = {
        "instrucciones_sistema": INSTRUCCIONES,
        "contexto": CONTEXTO,
        "consulta": CONSULTA,
    }
    return SolicitudLLM(**(valores | cambios))


class Captura:
    """Manejador de `httpx.MockTransport` que guarda las solicitudes recibidas."""

    def __init__(self, respuesta: httpx.Response | Exception) -> None:
        self._respuesta = respuesta
        self.solicitudes: list[httpx.Request] = []

    def __call__(self, solicitud: httpx.Request) -> httpx.Response:
        self.solicitudes.append(solicitud)
        if isinstance(self._respuesta, Exception):
            raise self._respuesta
        return self._respuesta

    @property
    def ultima(self) -> httpx.Request:
        return self.solicitudes[-1]

    @property
    def cuerpo(self) -> dict[str, Any]:
        return json.loads(self.ultima.content)


def cliente_simulado(manejador: Manejador) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(manejador))


def textos_del_error(error: BaseException) -> str:
    """Todo lo que un traceback mostraría de una excepción, incluidas las encadenadas."""
    partes: list[str] = []
    actual: BaseException | None = error
    while actual is not None:
        partes.extend([str(actual), repr(actual)])
        # `raise ... from None` deja el contexto fuera del traceback.
        contexto = None if actual.__suppress_context__ else actual.__context__
        actual = actual.__cause__ or contexto
    return "\n".join(partes)


def afirmar_sin_datos_sensibles(error: BaseException) -> None:
    texto = textos_del_error(error)
    for dato in (CLAVE, CONSULTA, CONTEXTO, INSTRUCCIONES):
        assert dato not in texto

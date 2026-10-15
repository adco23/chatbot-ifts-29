"""Utilidades compartidas por las pruebas de embeddings."""

import asyncio
import math
from collections.abc import Coroutine, Sequence
from typing import Any

import pytest

from app.providers.embeddings import GestorEmbeddings, ProveedorEmbeddings
from tests.unit.providers.ayudas import textos_del_error

# Valores ficticios y fáciles de buscar en un mensaje de error o en un registro.
CLAVE = "clave-ficticia-secreta"
CUENTA = "cuenta-ficticia-secreta"
CONSULTA = "¿Cuándo empiezan las clases de primer año?"
DOCUMENTOS = [
    "Las clases empiezan el 16 de marzo.",
    "La inscripción a materias cierra el 2 de marzo.",
    "Bedelía atiende de lunes a viernes de 18 a 21.",
]


def ejecutar[T](corutina: Coroutine[Any, Any, T]) -> T:
    # asyncio.run evita depender de un plugin de pytest para pruebas asíncronas.
    return asyncio.run(corutina)


def norma(vector: Sequence[float]) -> float:
    return math.hypot(*vector)


def afirmar_normalizados(vectores: Sequence[Sequence[float]], dimension: int) -> None:
    assert vectores
    for vector in vectores:
        assert len(vector) == dimension
        assert norma(vector) == pytest.approx(1.0)


def vector_crudo(dimension: int, semilla: float = 1.0) -> list[float]:
    """Vector sin normalizar, como el que devuelve un proveedor. Su norma no es 1."""
    return [semilla * (indice % 7 + 1) for indice in range(dimension)]


def afirmar_sin_datos_sensibles(error: BaseException) -> None:
    texto = textos_del_error(error)
    for dato in (CLAVE, CUENTA, CONSULTA, *DOCUMENTOS):
        assert dato not in texto


class RelojFalso:
    """Reloj y espera simulados: el tiempo avanza solo cuando alguien lo pide."""

    def __init__(self) -> None:
        self.ahora = 0.0
        self.esperas: list[float] = []

    def __call__(self) -> float:
        return self.ahora

    async def dormir(self, segundos: float) -> None:
        self.esperas.append(segundos)
        self.ahora += segundos


def crear_gestor(
    proveedores: Sequence[ProveedorEmbeddings], reloj: RelojFalso | None = None, **cambios: Any
) -> GestorEmbeddings:
    """Gestor con los valores por defecto de la configuración, salvo lo que se indique."""
    valores: dict[str, Any] = {
        "reintentos": 1,
        "backoff_base_segundos": 0.25,
        "backoff_maximo_segundos": 1.0,
        "presupuesto_segundos": 4.0,
    }
    if reloj is not None:
        valores |= {"reloj": reloj, "dormir": reloj.dormir}
    return GestorEmbeddings(proveedores, **(valores | cambios))

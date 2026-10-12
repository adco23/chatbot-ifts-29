"""Pruebas del límite de solicitudes por sesión (RNF11)."""

import asyncio
import logging
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.services.limite_solicitudes import (
    ENCABEZADO_SESION,
    MENSAJE_SESION_INVALIDA,
    ContadorEnMemoria,
    ContadorSolicitudes,
    EstadoVentana,
    obtener_contador_solicitudes,
    verificar_limite_solicitudes,
)

# Identificadores aleatorios de ejemplo, como los que genera el widget.
SESION_A = "3f2b8c1e-9d4a-4b6f-8e2a-1c5d7f9a0b3e"
SESION_B = "a7c4e9f2-1b3d-4c8a-9f6e-2d5b8a1c4e7f"


class Reloj:
    """Reloj simulado: el tiempo avanza solo cuando la prueba lo pide."""

    def __init__(self) -> None:
        self.ahora = 1000.0

    def __call__(self) -> float:
        return self.ahora

    def avanzar(self, segundos: float) -> None:
        self.ahora += segundos


@pytest.fixture
def reloj() -> Reloj:
    return Reloj()


@pytest.fixture
def contador(reloj) -> ContadorEnMemoria:
    return ContadorEnMemoria(reloj=reloj)


@pytest.fixture
def cliente(contador, monkeypatch) -> TestClient:
    """Aplicación mínima con una ruta protegida por el límite: 3 solicitudes cada 60 s."""
    monkeypatch.setenv("LIMITE_SOLICITUDES_MAXIMO", "3")
    monkeypatch.setenv("LIMITE_SOLICITUDES_VENTANA_SEGUNDOS", "60")
    return TestClient(crear_app(contador))


def crear_app(contador: ContadorSolicitudes) -> FastAPI:
    app = FastAPI()

    @app.post("/consulta")
    async def consulta(
        sesion_id: Annotated[str, Depends(verificar_limite_solicitudes)],
    ) -> dict[str, str]:
        return {"sesion": sesion_id}

    app.dependency_overrides[obtener_contador_solicitudes] = lambda: contador
    return app


def consultar(cliente: TestClient, sesion_id: str | None = SESION_A):
    encabezados = {} if sesion_id is None else {ENCABEZADO_SESION: sesion_id}
    return cliente.post("/consulta", headers=encabezados)


def estados(cliente: TestClient, cantidad: int, sesion_id: str = SESION_A) -> list[int]:
    return [consultar(cliente, sesion_id).status_code for _ in range(cantidad)]


def registrar(contador: ContadorSolicitudes, sesion_id: str, ventana: float = 60) -> EstadoVentana:
    # asyncio.run evita depender de un plugin de pytest para pruebas asíncronas.
    return asyncio.run(contador.registrar(sesion_id, ventana))


# --- Dependencia de FastAPI ---


def test_bajo_el_limite_las_solicitudes_pasan(cliente):
    respuestas = [consultar(cliente) for _ in range(3)]

    assert [respuesta.status_code for respuesta in respuestas] == [200, 200, 200]
    assert respuestas[0].json() == {"sesion": SESION_A}


def test_sobre_el_limite_responde_429_con_mensaje_en_espanol(cliente):
    assert estados(cliente, 3) == [200, 200, 200]

    respuesta = consultar(cliente)

    assert respuesta.status_code == 429
    assert respuesta.json() == {
        "detail": "Hiciste demasiadas consultas en poco tiempo. "
        "Esperá 60 segundos y volvé a intentar."
    }
    assert respuesta.headers["Retry-After"] == "60"


def test_el_mensaje_informa_lo_que_falta_para_el_reinicio(cliente, reloj):
    estados(cliente, 3)

    reloj.avanzar(45.5)
    respuesta = consultar(cliente)
    assert respuesta.headers["Retry-After"] == "15"
    assert "Esperá 15 segundos" in respuesta.json()["detail"]

    reloj.avanzar(13.9)
    respuesta = consultar(cliente)
    assert respuesta.headers["Retry-After"] == "1"
    assert "Esperá 1 segundo y" in respuesta.json()["detail"]


def test_al_reiniciarse_la_ventana_la_sesion_vuelve_a_tener_su_cupo(cliente, reloj):
    assert estados(cliente, 4) == [200, 200, 200, 429]

    reloj.avanzar(59.9)
    assert consultar(cliente).status_code == 429

    reloj.avanzar(0.1)
    assert estados(cliente, 4) == [200, 200, 200, 429]


def test_las_solicitudes_rechazadas_no_alargan_la_espera(cliente, reloj):
    estados(cliente, 3)
    reloj.avanzar(30)
    assert estados(cliente, 5) == [429] * 5

    reloj.avanzar(30)
    assert consultar(cliente).status_code == 200


def test_las_sesiones_son_independientes_entre_si(cliente):
    assert estados(cliente, 4, SESION_A) == [200, 200, 200, 429]

    assert estados(cliente, 3, SESION_B) == [200, 200, 200]
    assert consultar(cliente, SESION_B).status_code == 429
    assert consultar(cliente, SESION_A).status_code == 429


def test_cada_sesion_tiene_su_propia_ventana(cliente, reloj):
    estados(cliente, 4, SESION_A)
    reloj.avanzar(40)
    estados(cliente, 4, SESION_B)

    reloj.avanzar(20)

    assert consultar(cliente, SESION_A).status_code == 200
    assert consultar(cliente, SESION_B).status_code == 429


def test_el_identificador_no_distingue_mayusculas(cliente):
    assert estados(cliente, 3, SESION_A.upper()) == [200, 200, 200]

    respuesta = consultar(cliente, SESION_A)

    assert respuesta.status_code == 429


def test_devuelve_el_identificador_normalizado(cliente):
    respuesta = consultar(cliente, f"  {SESION_A.upper()}  ")

    assert respuesta.json() == {"sesion": SESION_A}


@pytest.mark.parametrize(
    "sesion_id",
    [
        None,
        "",
        "   ",
        "sesion-1",
        "estudiante@ifts29.edu.ar",
        "30123456",
        SESION_A.replace("-", ""),
        SESION_A[:-1],
        SESION_A + "0",
        "{" + SESION_A + "}",
        SESION_A[:-1] + "g",
        "x" * 5000,
    ],
    ids=[
        "ausente",
        "vacio",
        "en-blanco",
        "texto-corto",
        "correo",
        "documento",
        "sin-guiones",
        "le-falta-un-caracter",
        "le-sobra-un-caracter",
        "con-llaves",
        "no-hexadecimal",
        "muy-largo",
    ],
)
def test_sin_identificador_valido_responde_400_y_no_cuenta(cliente, contador, sesion_id):
    respuesta = consultar(cliente, sesion_id)

    assert respuesta.status_code == 400
    assert respuesta.json() == {"detail": MENSAJE_SESION_INVALIDA}
    assert ENCABEZADO_SESION in MENSAJE_SESION_INVALIDA
    assert contador._ventanas == {}


def test_el_limite_y_la_ventana_se_configuran_por_variables_de_entorno(
    contador, reloj, monkeypatch
):
    monkeypatch.setenv("LIMITE_SOLICITUDES_MAXIMO", "2")
    monkeypatch.setenv("LIMITE_SOLICITUDES_VENTANA_SEGUNDOS", "5")
    cliente = TestClient(crear_app(contador))

    assert estados(cliente, 3) == [200, 200, 429]
    assert consultar(cliente).headers["Retry-After"] == "5"

    reloj.avanzar(5)
    assert estados(cliente, 3) == [200, 200, 429]


def test_sin_variables_se_usan_los_valores_por_defecto(contador):
    cliente = TestClient(crear_app(contador))

    assert estados(cliente, 11) == [200] * 10 + [429]
    assert consultar(cliente).headers["Retry-After"] == "60"


def test_el_identificador_de_sesion_no_queda_en_los_registros(cliente, caplog):
    caplog.set_level(logging.DEBUG)

    assert estados(cliente, 5) == [200, 200, 200, 429, 429]

    assert "superó el límite" in caplog.text
    assert SESION_A not in caplog.text


def test_la_dependencia_usa_cualquier_implementacion_del_contador(monkeypatch):
    """La implementación en memoria se reemplaza sin tocar la dependencia ni la ruta."""

    class ContadorFijo(ContadorSolicitudes):
        def __init__(self) -> None:
            self.recibido: list[tuple[str, float]] = []

        async def registrar(self, sesion_id: str, ventana_segundos: float) -> EstadoVentana:
            self.recibido.append((sesion_id, ventana_segundos))
            return EstadoVentana(cantidad=99, segundos_para_reinicio=7.2)

    monkeypatch.setenv("LIMITE_SOLICITUDES_VENTANA_SEGUNDOS", "120")
    contador = ContadorFijo()

    respuesta = consultar(TestClient(crear_app(contador)))

    assert respuesta.status_code == 429
    assert respuesta.headers["Retry-After"] == "8"
    assert contador.recibido == [(SESION_A, 120.0)]


def test_por_defecto_se_usa_un_unico_contador_en_memoria():
    contador = obtener_contador_solicitudes()

    assert isinstance(contador, ContadorEnMemoria)
    assert obtener_contador_solicitudes() is contador


# --- Contador en memoria ---


def test_no_se_puede_instanciar_la_interfaz():
    with pytest.raises(TypeError):
        ContadorSolicitudes()


def test_el_contador_cuenta_dentro_de_la_ventana(contador, reloj):
    assert registrar(contador, SESION_A) == EstadoVentana(cantidad=1, segundos_para_reinicio=60)

    reloj.avanzar(20)

    assert registrar(contador, SESION_A) == EstadoVentana(cantidad=2, segundos_para_reinicio=40)


def test_el_contador_reinicia_la_ventana_al_vencer(contador, reloj):
    registrar(contador, SESION_A)
    registrar(contador, SESION_A)

    reloj.avanzar(60)

    assert registrar(contador, SESION_A) == EstadoVentana(cantidad=1, segundos_para_reinicio=60)


def test_el_contador_lleva_cada_sesion_por_separado(contador):
    for _ in range(3):
        registrar(contador, SESION_A)

    assert registrar(contador, SESION_B).cantidad == 1
    assert registrar(contador, SESION_A).cantidad == 4


def test_el_contador_descarta_las_ventanas_vencidas_al_llenarse(reloj):
    contador = ContadorEnMemoria(reloj=reloj, max_sesiones=3)
    for indice in range(3):
        registrar(contador, f"sesion-{indice}")

    reloj.avanzar(60)
    registrar(contador, "sesion-nueva")

    assert set(contador._ventanas) == {"sesion-nueva"}


def test_el_contador_descarta_las_mas_antiguas_si_todas_estan_vigentes(reloj):
    contador = ContadorEnMemoria(reloj=reloj, max_sesiones=3)
    for indice in range(5):
        registrar(contador, f"sesion-{indice}")
        reloj.avanzar(1)

    assert list(contador._ventanas) == ["sesion-2", "sesion-3", "sesion-4"]
    # Una sesión vigente sigue contando aunque la memoria esté llena.
    assert registrar(contador, "sesion-4").cantidad == 2
    assert len(contador._ventanas) == 3


def test_el_contador_rechaza_un_maximo_de_sesiones_invalido():
    with pytest.raises(ValueError):
        ContadorEnMemoria(max_sesiones=0)

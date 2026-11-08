"""Pruebas de la autenticación de la API de administración (RNF11)."""

import hmac
import logging

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.services import autenticacion_admin
from app.services.autenticacion_admin import (
    LARGO_MINIMO_TOKEN,
    MENSAJE_NO_AUTORIZADO,
    verificar_token_admin,
)

# Valores ficticios y fáciles de buscar en una respuesta o en un registro.
TOKEN = "token-ficticio-de-administracion"
TOKEN_INCORRECTO = "otro-token-ficticio-que-no-sirve"


def crear_app() -> FastAPI:
    """Aplicación mínima con un router de administración protegido por completo."""
    router = APIRouter(prefix="/admin", dependencies=[Depends(verificar_token_admin)])

    @router.post("/sync")
    def sincronizar() -> dict[str, str]:
        return {"estado": "ok"}

    @router.get("/estado")
    def estado() -> dict[str, str]:
        return {"estado": "ok"}

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def cliente() -> TestClient:
    return TestClient(crear_app())


@pytest.fixture
def con_token(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)


def pedir(cliente: TestClient, autorizacion: str | bytes | None = None):
    encabezados = {} if autorizacion is None else {"Authorization": autorizacion}
    return cliente.post("/admin/sync", headers=encabezados)


def afirmar_rechazo(respuesta) -> None:
    """Todos los rechazos son idénticos: no revelan qué falló."""
    assert respuesta.status_code == 401
    assert respuesta.json() == {"detail": MENSAJE_NO_AUTORIZADO}
    assert respuesta.headers["WWW-Authenticate"] == "Bearer"


def test_token_correcto_permite_el_acceso(cliente, con_token):
    respuesta = pedir(cliente, f"Bearer {TOKEN}")

    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "ok"}


def test_el_esquema_no_distingue_mayusculas(cliente, con_token):
    assert pedir(cliente, f"bearer {TOKEN}").status_code == 200


def test_token_incorrecto_se_rechaza(cliente, con_token):
    afirmar_rechazo(pedir(cliente, f"Bearer {TOKEN_INCORRECTO}"))


def test_encabezado_ausente_se_rechaza(cliente, con_token):
    afirmar_rechazo(pedir(cliente))


@pytest.mark.parametrize(
    "autorizacion",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        f"Bearer {TOKEN}",
        f"Bearer {TOKEN_INCORRECTO}",
        TOKEN,
    ],
    ids=["ausente", "vacio", "solo-esquema", "token-vacio", "token", "otro-token", "sin-esquema"],
)
def test_sin_token_configurado_se_rechaza_siempre(cliente, autorizacion):
    afirmar_rechazo(pedir(cliente, autorizacion))


@pytest.mark.parametrize("valor", ["", "   "])
def test_variable_en_blanco_se_rechaza_aunque_llegue_un_token_vacio(cliente, monkeypatch, valor):
    monkeypatch.setenv("ADMIN_API_TOKEN", valor)

    for autorizacion in (None, "", "Bearer ", f"Bearer {valor}"):
        afirmar_rechazo(pedir(cliente, autorizacion))


def test_un_token_configurado_demasiado_corto_se_rechaza_aunque_coincida(cliente, monkeypatch):
    corto = "x" * (LARGO_MINIMO_TOKEN - 1)
    monkeypatch.setenv("ADMIN_API_TOKEN", corto)

    afirmar_rechazo(pedir(cliente, f"Bearer {corto}"))


def test_un_token_con_el_largo_minimo_se_acepta(cliente, monkeypatch):
    justo = "x" * LARGO_MINIMO_TOKEN
    monkeypatch.setenv("ADMIN_API_TOKEN", justo)

    assert pedir(cliente, f"Bearer {justo}").status_code == 200


@pytest.mark.parametrize(
    "autorizacion",
    [
        TOKEN,
        f"Basic {TOKEN}",
        f"Token {TOKEN}",
        f"Bearer {TOKEN[:-1]}",
        f"Bearer {TOKEN}x",
        f"Bearer {TOKEN.upper()}",
        f"Bearer {TOKEN} extra",
        "Bearer ",
        "Bearer",
        "",
        "Bearer contraseña-con-eñe".encode(),
    ],
    ids=[
        "sin-esquema",
        "esquema-basic",
        "esquema-token",
        "le-falta-un-caracter",
        "le-sobra-un-caracter",
        "otras-mayusculas",
        "con-texto-extra",
        "token-vacio",
        "solo-esquema",
        "vacio",
        "fuera-de-ascii",
    ],
)
def test_credenciales_mal_formadas_o_distintas_se_rechazan(cliente, con_token, autorizacion):
    afirmar_rechazo(pedir(cliente, autorizacion))


def test_todos_los_rechazos_son_indistinguibles(cliente, monkeypatch):
    """La respuesta no revela si falló el encabezado, el token o la configuración."""
    sin_configurar = pedir(cliente, f"Bearer {TOKEN}")
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)
    autenticacion_admin.obtener_configuracion.cache_clear()
    rechazos = [
        sin_configurar,
        pedir(cliente),
        pedir(cliente, f"Bearer {TOKEN_INCORRECTO}"),
        pedir(cliente, f"Basic {TOKEN}"),
    ]

    for respuesta in rechazos:
        afirmar_rechazo(respuesta)
    assert len({respuesta.content for respuesta in rechazos}) == 1
    assert len({respuesta.headers["WWW-Authenticate"] for respuesta in rechazos}) == 1
    assert pedir(cliente, f"Bearer {TOKEN}").status_code == 200


def test_la_dependencia_protege_todas_las_rutas_del_router(cliente, con_token):
    assert cliente.get("/admin/estado").status_code == 401
    assert cliente.post("/admin/sync").status_code == 401

    autorizado = {"Authorization": f"Bearer {TOKEN}"}
    assert cliente.get("/admin/estado", headers=autorizado).status_code == 200
    assert cliente.post("/admin/sync", headers=autorizado).status_code == 200


@pytest.mark.parametrize("configurado", [True, False], ids=["con-token", "sin-token"])
@pytest.mark.parametrize(
    "autorizacion",
    [None, f"Bearer {TOKEN}", f"Bearer {TOKEN_INCORRECTO}"],
    ids=["ausente", "correcto", "incorrecto"],
)
def test_siempre_se_compara_en_tiempo_constante(cliente, monkeypatch, configurado, autorizacion):
    if configurado:
        monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)
    comparaciones: list[tuple[bytes, bytes]] = []
    original = hmac.compare_digest

    def espia(recibido: bytes, esperado: bytes) -> bool:
        comparaciones.append((recibido, esperado))
        return original(recibido, esperado)

    monkeypatch.setattr(autenticacion_admin.hmac, "compare_digest", espia)

    pedir(cliente, autorizacion)

    # Una comparación por solicitud, también cuando falta el encabezado o el token.
    assert len(comparaciones) == 1
    recibido, esperado = comparaciones[0]
    assert isinstance(recibido, bytes)
    assert isinstance(esperado, bytes)
    if configurado:
        assert esperado == TOKEN.encode()
    else:
        assert esperado != TOKEN.encode()
        assert len(esperado) >= LARGO_MINIMO_TOKEN


def test_los_registros_y_las_respuestas_no_incluyen_los_tokens(cliente, con_token, caplog):
    caplog.set_level(logging.DEBUG)

    respuestas = [
        pedir(cliente),
        pedir(cliente, f"Bearer {TOKEN_INCORRECTO}"),
        pedir(cliente, f"Bearer {TOKEN}"),
    ]

    assert "rechazada" in caplog.text
    for texto in (caplog.text, *(respuesta.text for respuesta in respuestas)):
        assert TOKEN not in texto
        assert TOKEN_INCORRECTO not in texto
    for respuesta in respuestas:
        assert TOKEN not in str(respuesta.headers)
        assert TOKEN_INCORRECTO not in str(respuesta.headers)


def test_sin_token_configurado_el_registro_avisa_sin_mostrar_lo_recibido(cliente, caplog):
    caplog.set_level(logging.DEBUG)

    afirmar_rechazo(pedir(cliente, f"Bearer {TOKEN}"))

    errores = [registro for registro in caplog.records if registro.levelno == logging.ERROR]
    assert len(errores) == 1
    assert "ADMIN_API_TOKEN" in errores[0].getMessage()
    assert TOKEN not in caplog.text


def test_el_esquema_de_seguridad_figura_en_la_documentacion_openapi():
    esquema = crear_app().openapi()

    assert {"type": "http", "scheme": "bearer"}.items() <= next(
        iter(esquema["components"]["securitySchemes"].values())
    ).items()
    assert esquema["paths"]["/admin/sync"]["post"]["security"]

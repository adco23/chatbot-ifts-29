"""Pruebas de la configuración de la base de datos y de la creación del cliente."""

import pytest

from app.settings import obtener_configuracion
from app.db.conexion import obtener_cliente_supabase

# Valores ficticios: no son credenciales reales.
URL_SUPABASE = "https://proyecto-ficticio.supabase.co"
CLAVE_SUPABASE = "clave-ficticia-supabase"


def test_sin_variables_no_hay_base():
    config = obtener_configuracion()

    assert config.supabase_url is None
    assert config.supabase_key is None
    assert obtener_cliente_supabase() is None


@pytest.mark.parametrize("valor", ["", "   "])
def test_valores_en_blanco_cuentan_como_no_configurados(monkeypatch, valor):
    monkeypatch.setenv("SUPABASE_URL", valor)
    monkeypatch.setenv("SUPABASE_KEY", valor)

    config = obtener_configuracion()

    assert config.supabase_url is None
    assert config.supabase_key is None


def test_lee_url_y_clave_sin_mostrar_la_clave(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", URL_SUPABASE)
    monkeypatch.setenv("SUPABASE_KEY", CLAVE_SUPABASE)

    config = obtener_configuracion()

    assert config.supabase_url == URL_SUPABASE
    assert config.supabase_key.get_secret_value() == CLAVE_SUPABASE
    assert CLAVE_SUPABASE not in repr(config)


def test_sin_clave_no_crea_cliente(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", URL_SUPABASE)

    assert obtener_cliente_supabase() is None


def test_lee_las_variables_del_archivo_env(tmp_path):
    # La fixture compartida ya se ubicó en tmp_path: este .env es el que se lee.
    (tmp_path / ".env").write_text(
        f"SUPABASE_URL={URL_SUPABASE}\nSUPABASE_KEY={CLAVE_SUPABASE}\n", encoding="utf-8"
    )

    config = obtener_configuracion()

    assert config.supabase_url == URL_SUPABASE
    assert config.supabase_key.get_secret_value() == CLAVE_SUPABASE

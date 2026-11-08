"""Pruebas del armado del mensaje de usuario."""

from app.providers.formato import armar_mensaje_usuario
from tests.unit.providers.ayudas import CONSULTA, CONTEXTO, INSTRUCCIONES, crear_solicitud


def test_con_contexto_incluye_el_bloque_y_la_consulta():
    mensaje = armar_mensaje_usuario(crear_solicitud())

    assert mensaje == f"Contexto:\n{CONTEXTO}\n\nConsulta:\n{CONSULTA}"


def test_sin_contexto_devuelve_solo_la_consulta():
    for contexto in ("", "   \n"):
        assert armar_mensaje_usuario(crear_solicitud(contexto=contexto)) == CONSULTA


def test_no_incluye_las_instrucciones_del_sistema():
    assert INSTRUCCIONES not in armar_mensaje_usuario(crear_solicitud())

"""Atajos sociales por HTTP, después de moderar y sin dependencias documentales."""

import pytest

from app.services import respuestas_locales as locales
from tests.unit.test_chat_moderacion import (
    base as base, entorno as entorno, http as http, pasos as pasos,
    preguntar, sin_dependencias_posteriores,
)


@pytest.mark.parametrize("consulta,respuesta", [
    *[(s, locales.SALUDO) for s in (
        "hola", "holaaaa", "buen día", "buenas", "buenas tardes", "buenas noches",
        "qué tal", "como estas", "cómo andás", "hola como andas", "hola qué tal",
        "buenas noches que tal", "hola bot",
    )],
    *[(s, locales.AGRADECIMIENTO) for s in ("gracias", "muchas gracias", "gracias por la ayuda")],
    *[(s, locales.DESPEDIDA) for s in ("chau", "hasta luego", "nos vemos", "gracias chau")],
])
def test_social_http_termina_antes_de_faq_embeddings_rag_y_llm(
    http, entorno, pasos, base, consulta, respuesta,
):
    r = preguntar(http, consulta)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["respuesta"] == respuesta and r.json()["fuentes"] == []
    pasos.moderar.assert_called_once_with(consulta)
    sin_dependencias_posteriores(entorno, pasos, base)


@pytest.mark.parametrize("consulta", ["Hola, sos un pelotudo", "Gracias, sos un idiota"])
def test_moderacion_tiene_prioridad_sobre_respuesta_social(http, entorno, pasos, base, consulta):
    r = preguntar(http, consulta)
    assert r.status_code == 200 and r.json()["estado"] == "bloqueada"
    sin_dependencias_posteriores(entorno, pasos, base)


@pytest.mark.parametrize("consulta,respuesta", [
    ("hola", locales.SALUDO), ("gracias", locales.AGRADECIMIENTO), ("chau", locales.DESPEDIDA),
])
def test_social_con_consulta_pendiente_no_la_ejecuta_ni_la_descarta(
    http, entorno, pasos, base, consulta, respuesta,
):
    contexto = {"materia_id": 2, "periodo": "2026C2",
                "consulta_pendiente": "¿Cuándo son los parciales de Base de Datos?"}
    r = preguntar(http, consulta, contexto)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["respuesta"] == respuesta
    assert r.json()["contexto"]["consulta_pendiente"] == contexto["consulta_pendiente"]
    assert r.json()["contexto"]["materia_id"] == 2
    sin_dependencias_posteriores(entorno, pasos, base)

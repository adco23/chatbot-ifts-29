"""Respuestas sociales completas, configurables y sin interceptar consultas académicas."""

import json

import pytest

from app.services import respuestas_locales as locales


@pytest.fixture(autouse=True)
def cache_social_aislada():
    locales.cargar_respuestas_sociales.cache_clear()
    yield
    locales.cargar_respuestas_sociales.cache_clear()


SALUDOS = [
    "hola", "holaa", "holaaaa", "buenas", "buen día", "buen dia", "buenos días",
    "buenos dias", "buenas tardes", "buenas noches", "qué tal", "que tal", "cómo estás",
    "como estas", "cómo andás", "como andas", "todo bien?", "hola cómo estás",
    "hola como andas", "hola qué tal", "buenas cómo estás", "buenas que tal",
    "buen día cómo estás", "buenas tardes cómo estás", "buenas noches cómo estás",
    "buenas noches qué tal", "buenas noches como andas", "hey", "ey", "hola bot",
    "hola chat", "buenas bot", "buenas chat", "¡HOLAAAA! ¿CÓMO ESTÁS?",
]
GRACIAS = [
    "gracias", "muchas gracias", "muchísimas gracias", "gracias por la ayuda",
    "gracias por ayudarme", "perfecto gracias", "genial gracias", "buenísimo gracias",
    "excelente gracias", "ok gracias", "listo gracias", "dale gracias", "te agradezco",
    "se agradece", "¡MUCHAS GRACIAS!",
]
DESPEDIDAS = [
    "chau", "chao", "adiós", "adios", "hasta luego", "hasta después", "hasta mañana",
    "nos vemos", "hablamos", "hablamos después", "listo chau", "gracias chau", "me voy",
    "hasta la próxima", "hasta la proxima", "que tengas buen día", "chau gracias",
]


@pytest.mark.parametrize("texto", SALUDOS)
def test_saludos(texto):
    assert locales.respuesta_local(texto) == locales.SALUDO


@pytest.mark.parametrize("texto", GRACIAS)
def test_agradecimientos(texto):
    assert locales.respuesta_local(texto) == locales.AGRADECIMIENTO


@pytest.mark.parametrize("texto", DESPEDIDAS)
def test_despedidas(texto):
    assert locales.respuesta_local(texto) == locales.DESPEDIDA


@pytest.mark.parametrize("texto", [
    "Hola, quiero el cronograma de Base de Datos comisión A",
    "Gracias, ¿cuándo son los parciales?", "Hasta mañana hay inscripción?",
    "Me voy a inscribir en Moodle", "¿Cuántas materias tiene el plan de estudios?",
    "Consulta de inscripción\nAclaración: hola", "hola\nAclaración: consulta académica",
    "Buenas noches necesito el horario de comisión D", "hola desconocido",
    "[CORREO_OCULTO] hola", "holanda", "graciosamente", "chaoticamente",
])
def test_mensaje_mixto_o_coincidencia_parcial_continua_flujo(texto):
    assert locales.respuesta_local(texto) is None


@pytest.mark.parametrize("categoria,respuesta", [
    ("saludos", locales.SALUDO), ("agradecimientos", locales.AGRADECIMIENTO),
    ("despedidas", locales.DESPEDIDA),
])
def test_extender_json_sin_modificar_python(tmp_path, monkeypatch, categoria, respuesta):
    datos = json.loads(locales.RUTA_SOCIALES.read_text(encoding="utf-8"))
    datos[categoria].append("expresion social inventada")
    ruta = tmp_path / "sociales.json"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    monkeypatch.setattr(locales, "RUTA_SOCIALES", ruta)
    assert locales.respuesta_local("¡Expresión social inventada!") == respuesta


@pytest.mark.parametrize("modo", ["ausente", "json_invalido", "vacio", "regex", "controles"])
def test_catalogo_invalido_usa_respaldo_minimo(tmp_path, monkeypatch, modo):
    ruta = tmp_path / "sociales.json"
    if modo == "json_invalido":
        ruta.write_text("no es JSON", encoding="utf-8")
    elif modo in ("vacio", "regex", "controles"):
        ruta.write_text(json.dumps({
            "saludos": [] if modo == "vacio" else ["\x00" if modo == "controles" else ".*"],
            "agradecimientos": ["gracias"], "despedidas": ["chau"],
        }), encoding="utf-8")
    monkeypatch.setattr(locales, "RUTA_SOCIALES", ruta)
    assert locales.respuesta_local("hola") == locales.SALUDO
    assert locales.respuesta_local("gracias") == locales.AGRADECIMIENTO
    assert locales.respuesta_local("chau") == locales.DESPEDIDA
    assert locales.respuesta_local("cronograma de Base de Datos") is None


def test_edicion_social_se_aplica_al_recargar_catalogo(tmp_path, monkeypatch):
    datos = json.loads(locales.RUTA_SOCIALES.read_text(encoding="utf-8"))
    ruta = tmp_path / "sociales.json"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    monkeypatch.setattr(locales, "RUTA_SOCIALES", ruta)
    assert locales.respuesta_local("salutaciones") is None
    datos["saludos"].append("salutaciones")
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    assert locales.respuesta_local("salutaciones") is None
    locales.cargar_respuestas_sociales.cache_clear()
    assert locales.respuesta_local("salutaciones") == locales.SALUDO

"""HTTP con corpus coherente 2026C2/A y proveedores simulados; sin I/O real."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.models.chat import FuenteChat, MateriaChat
from app.models.faq import FragmentoFuenteFaq
from app.models.rag import DocumentoPersistido
from app.providers.base import RespuestaLLM, TodosLosProveedoresFallaron, ErrorTemporal
from app.providers.dependencias import obtener_gestor_proveedores
from app.providers.embeddings.base import ResultadoEmbedding
from app.providers.embeddings.dependencias import obtener_gestor_embeddings
from app.providers.gemini import ProveedorGemini
from app.providers.gestor import GestorProveedores
from app.providers.groq import ProveedorGroq
from app.services import cache_faq, conversacion, recuperador
from app.services.comisiones import CatalogoComisiones
from app.settings import Configuracion, obtener_configuracion
from tests.unit.services.test_eventos_plurales import CORPUS, PARCIALES, PFO, RECUPERATORIOS, fuente
from tests.unit.services.test_cache_faq import coincidencia


@pytest.fixture
def flujo(monkeypatch):
    e = SimpleNamespace(contenido=CORPUS, respuesta=PARCIALES, proveedor="gemini")
    catalogo = CatalogoComisiones("2026C2", {"2026C2": ("A", "B", "C", "D")})
    monkeypatch.setattr(conversacion, "cargar_catalogo", lambda: catalogo)
    monkeypatch.setattr(conversacion.repositorio, "listar_materias_chat", lambda: [
        MateriaChat(id=2, codigo="ABD", nombre="Administración de Base de Datos"),
    ])
    doc = DocumentoPersistido(**fuente().model_dump(exclude={
        "fragmento_id", "contenido", "orden", "similitud", "documento_id"}), id=900, estado="activo")
    monkeypatch.setattr(conversacion.repositorio, "listar_documentos_chat", lambda: [doc])
    e.faq = AsyncMock(return_value=None)
    monkeypatch.setattr(conversacion, "buscar_faq", e.faq)
    e.buscar = Mock(side_effect=lambda vector, **kw:
                    [] if kw["modalidad"] == "complementaria" else [fuente(e.contenido)])
    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", e.buscar)
    monkeypatch.setattr(conversacion, "recuperar_fragmentos", recuperador.recuperar_fragmentos)
    e.fuentes = AsyncMock(return_value=[FuenteChat(
        documento_id=900, drive_id=doc.drive_id, titulo=doc.titulo, fragmento_ids=[1001],
    )])
    monkeypatch.setattr(conversacion, "resolver_fuentes", e.fuentes)
    e.embeddings = SimpleNamespace(embed_query=AsyncMock(return_value=ResultadoEmbedding(
        [1.] + [0.] * 767, "gemini", "gemini-embedding-001", 768)))
    e.llm = SimpleNamespace(generar_con_respaldo=AsyncMock(side_effect=lambda s: RespuestaLLM(
        json.dumps({"respuesta": e.respuesta, "fragmento_ids": [1001]}), e.proveedor, "simulado")))
    from app.main import crear_app
    app = crear_app()
    app.dependency_overrides[obtener_gestor_embeddings] = lambda: e.embeddings
    app.dependency_overrides[obtener_gestor_proveedores] = lambda: e.llm
    app.dependency_overrides[obtener_configuracion] = lambda: Configuracion(rag_umbral_cronograma=.68)
    with TestClient(app) as cliente:
        e.http = cliente
        yield e


def preguntar(e, evento="los parciales"):
    verbo = "es" if evento.split()[0] in ("el", "la") else "son"
    return e.http.post("/chat", json={
        "mensaje": f"¿Cuándo {verbo} {evento} de bases de datos de la comisión A?",
    }, headers={"X-Sesion-Id": "77777777-7777-4777-8777-777777777777"})


@pytest.mark.parametrize("evento,respuesta", [
    ("el primer parcial", PARCIALES.splitlines()[0]),
    ("el segundo parcial", PARCIALES.splitlines()[1]),
    ("los parciales", PARCIALES.replace(".\n", "\n").rstrip(".")),
    ("la PFO 2", PFO.splitlines()[1]),
    ("las PFO", PFO),
    ("los recuperatorios", RECUPERATORIOS),
])
def test_http_eventos_correctos_200_sin_cambiar_contexto_ni_umbral(flujo, evento, respuesta):
    flujo.respuesta = respuesta
    r = preguntar(flujo, evento)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["contexto"]["materia_id"] == 2
    assert r.json()["contexto"]["comision"] == "A" and r.json()["contexto"]["periodo"] == "2026C2"
    assert r.json()["umbral"] == .68 and r.json()["confianza"] == .9
    assert r.json()["fuentes"][0]["fragmento_ids"] == [1001]
    flujo.llm.generar_con_respaldo.assert_awaited_once()
    assert "2026C2" in flujo.fuentes.call_args.args[0][0].periodo


@pytest.mark.parametrize("proveedor", ["gemini", "groq"])
@pytest.mark.parametrize("respuesta", [
    "Primer parcial: 20 de noviembre de 2026. Segundo parcial: 2 de octubre de 2026.",
    "Los parciales son el 2 de octubre de 2026 y el 20 de noviembre de 2026.",
])
def test_generacion_recibida_no_verificable_abstiene_no_503(flujo, proveedor, respuesta, caplog):
    flujo.proveedor, flujo.respuesta = proveedor, respuesta
    r = preguntar(flujo)
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["fuentes"] == [] and respuesta not in r.text
    assert "abstencion_eventos" in caplog.text and respuesta not in caplog.text
    flujo.fuentes.assert_not_called()


def test_evento_tercero_inexistente_abstiene_antes_del_llm(flujo):
    r = preguntar(flujo, "el tercer parcial")
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    flujo.llm.generar_con_respaldo.assert_not_called()


@pytest.mark.parametrize("respuesta", [
    PFO.replace("PFO 3: del 19 al 25 de octubre", "PFO 3: del 9 al 15 de noviembre"),
    PFO.replace("PFO 3: del 19 al 25 de octubre", "PFO 3: del 19 al 26 de octubre"),
])
def test_rango_pfo_no_verificable_no_se_confunde_con_fallo_del_proveedor(flujo, respuesta):
    flujo.respuesta = respuesta
    r = preguntar(flujo, "las PFO")
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    flujo.fuentes.assert_not_called()


def test_falla_real_de_generacion_continua_en_503(flujo):
    flujo.llm.generar_con_respaldo.side_effect = TodosLosProveedoresFallaron([ErrorTemporal()])
    r = preguntar(flujo)
    assert r.status_code == 503 and r.json()["detail"]["codigo"] == "servicio_no_disponible"


@pytest.mark.parametrize("caso,estado,llamadas_esperadas", [
    ("gemini_temporal_groq_valido", "respondida", ["gemini", "gemini", "groq"]),
    ("gemini_temporal_groq_semanticamente_invalido", "sin_evidencia", ["gemini", "gemini", "groq"]),
    ("gemini_invalido_groq_disponible_valido", "sin_evidencia", ["gemini"]),
    ("ambos_generarian_invalido", "sin_evidencia", ["gemini"]),
])
def test_fallback_existente_solo_transporte_y_abstencion_semantica(flujo, caso, estado, llamadas_esperadas):
    llamadas = []
    invalida = "Primer parcial: 20 de noviembre de 2026. Segundo parcial: 2 de octubre de 2026."
    def manejar(request):
        gemini = request.url.host == "generativelanguage.googleapis.com"
        llamadas.append("gemini" if gemini else "groq")
        if gemini and caso.startswith("gemini_temporal"):
            return httpx.Response(503)
        texto = (PARCIALES if caso == "gemini_temporal_groq_valido"
                 or not gemini and caso == "gemini_invalido_groq_disponible_valido" else invalida)
        contenido = json.dumps({"respuesta": texto, "fragmento_ids": [1001]})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": contenido}]}}]}
                              if gemini else {"choices": [{"message": {"content": contenido}}]})
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(manejar))
    gestor = GestorProveedores([
        ProveedorGemini("ficticia", "simulado", 1, cliente=cliente),
        ProveedorGroq("ficticia", "simulado", 1, cliente=cliente),
    ], 3, backoff_segundos=0)
    flujo.http.app.dependency_overrides[obtener_gestor_proveedores] = lambda: gestor
    try:
        r = preguntar(flujo)
    finally:
        asyncio.run(cliente.aclose())
    assert r.status_code == 200 and r.json()["estado"] == estado
    assert llamadas == llamadas_esperadas


@pytest.mark.parametrize("valida", [False, True])
def test_faq_plural_revalida_asociaciones_sin_contar_candidato_invalido(flujo, monkeypatch, valida):
    respuesta = ("Primer parcial: 2 de octubre de 2026 Segundo parcial: 20 de noviembre de 2026"
                 if valida else "Primer parcial: 20 de noviembre de 2026 Segundo parcial: 2 de octubre de 2026")
    hit = coincidencia(pregunta_canonica="¿Cuándo son los parciales de bases de datos de la comisión A?",
        respuesta=respuesta, materia_id=2, comision="A", periodo="2026C2", tipo_documental="cronograma")
    monkeypatch.setattr(conversacion, "buscar_faq", cache_faq.buscar_faq)
    monkeypatch.setattr(cache_faq.repositorio, "buscar_en_cache_faq", Mock(return_value=hit))
    evidencia = FragmentoFuenteFaq(**fuente().model_dump(exclude={"similitud"}),
        es_primaria=True, estado_documento="activo", version_documento={"indexado_en": "2026-09-01T00:00:00"})
    monkeypatch.setattr(cache_faq.repositorio, "obtener_evidencia_faq", Mock(return_value=[evidencia]))
    contador = Mock()
    monkeypatch.setattr(cache_faq.repositorio, "incrementar_acierto_cache_faq", contador)
    r = preguntar(flujo)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    if valida:
        flujo.llm.generar_con_respaldo.assert_not_called()
        contador.assert_called_once_with(hit.id)
    else:
        flujo.llm.generar_con_respaldo.assert_awaited_once()
        contador.assert_not_called()
        assert respuesta not in r.text

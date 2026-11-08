"""POST /chat con adaptadores LLM reales y HTTP simulado; RAG permanece como doble."""

import asyncio

import httpx
import pytest

from app.providers.dependencias import obtener_gestor_proveedores
from app.providers.gemini import ProveedorGemini
from app.providers.gestor import GestorProveedores
from app.providers.groq import ProveedorGroq
from tests.unit.providers.ayudas import CLAVE
from tests.unit.services.test_conversacion import entorno as entorno
from tests.unit.services.test_conversacion import salida
from tests.unit.test_chat import SESION, http as http


@pytest.mark.parametrize("caso,estado,llamadas_esperadas", [
    ("gemini_ok", 200, ["gemini"]),
    ("retry_ok", 200, ["gemini", "gemini"]),
    ("gemini_503", 200, ["gemini", "gemini", "groq"]),
    ("gemini_timeout", 200, ["gemini", "gemini", "groq"]),
    ("gemini_429", 200, ["gemini", "groq"]),
    ("gemini_permanente", 200, ["gemini", "groq"]),
    ("ambos_fallan", 503, ["gemini", "gemini", "groq"]),
    ("groq_invalido", 503, ["gemini", "gemini", "groq"]),
    ("groq_no_json", 503, ["gemini", "gemini", "groq"]),
    ("bloqueado", 200, ["gemini"]),
])
def test_chat_retry_y_respaldo_sin_duplicar_rag(
    http, entorno, caplog, caso, estado, llamadas_esperadas,
):
    llamadas = []

    def manejar(request):
        nombre = "gemini" if request.url.host == "generativelanguage.googleapis.com" else "groq"
        llamadas.append(nombre)
        if nombre == "gemini":
            if caso == "bloqueado":
                return httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
            if caso == "gemini_ok" or caso == "retry_ok" and len(llamadas) == 2:
                return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": salida()}]}}]})
            if caso == "gemini_timeout":
                raise httpx.ReadTimeout(CLAVE)
            codigo = 429 if caso == "gemini_429" else 401 if caso == "gemini_permanente" else 503
            return httpx.Response(codigo, json={"error": {"message": CLAVE}})
        if caso == "ambos_fallan":
            return httpx.Response(503, json={"error": {"message": CLAVE}})
        if caso == "groq_invalido":
            return httpx.Response(200, text=CLAVE)
        if caso == "groq_no_json":
            return httpx.Response(200, json={"choices": [{"message": {"content": "Salida no estructurada"}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": salida()}}]})

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(manejar))
    gestor = GestorProveedores([
        ProveedorGemini(CLAVE, "modelo-gemini", 0.5, cliente=cliente),
        ProveedorGroq(CLAVE, "modelo-groq", 0.5, cliente=cliente),
    ], presupuesto_segundos=2, backoff_segundos=0)
    http.app.dependency_overrides[obtener_gestor_proveedores] = lambda: gestor
    try:
        respuesta = http.post("/chat", headers=SESION, json={"mensaje": "Inscripción"})
    finally:
        asyncio.run(cliente.aclose())
    assert respuesta.status_code == estado
    if estado == 503:
        assert respuesta.json()["detail"]["codigo"] == "servicio_no_disponible"
    else:
        assert respuesta.json()["estado"] == ("bloqueada" if caso == "bloqueado" else "respondida")
    assert llamadas == llamadas_esperadas
    assert entorno.rag.await_count == 1
    assert CLAVE not in caplog.text and CLAVE not in respuesta.text
    if caso == "groq_no_json":
        # Reproduce Gemini fallando, Groq exitoso y 503 posterior de validación.
        # La generación no falló: el log debe permitir distinguir ambos momentos.
        assert "fallback a Groq iniciado" in caplog.text and "Groq respondió" in caplog.text
        assert "Groq falló" not in caplog.text

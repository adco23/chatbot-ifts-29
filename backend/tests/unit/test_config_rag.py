"""Configuración RAG sin modificar las opciones genéricas de proveedores."""

import pytest
from pydantic import ValidationError

from app.settings import Configuracion, obtener_configuracion


def test_defaults_y_variables_rag(monkeypatch):
    config = Configuracion()
    assert config.rag_top_k == 5
    assert config.rag_umbral_confianza == 0.70
    monkeypatch.setenv("RAG_TOP_K", "3")
    monkeypatch.setenv("RAG_UMBRAL_CONFIANZA", "0.65")
    config = obtener_configuracion()
    assert config.rag_top_k == 3
    assert config.rag_umbral_confianza == 0.65


@pytest.mark.parametrize("valor", [0, -1, True, 1.5, "2.5", "abc", ""])
def test_top_k_invalido(valor):
    with pytest.raises(ValidationError):
        Configuracion(rag_top_k=valor)


@pytest.mark.parametrize("valor", [-0.1, 1.1, float("nan"), float("inf")])
def test_umbral_invalido(valor):
    with pytest.raises(ValidationError):
        Configuracion(rag_umbral_confianza=valor)


@pytest.mark.parametrize("valor", [0.0, 1.0])
def test_umbral_admite_extremos(valor):
    assert Configuracion(rag_umbral_confianza=valor).rag_umbral_confianza == valor


def test_no_restringe_configuracion_generica_de_embeddings():
    config = Configuracion(gemini_embedding_dimension=128, embedding_primary="cloudflare")
    assert config.gemini_embedding_dimension == 128
    assert config.cloudflare_embedding_dimension == 1024

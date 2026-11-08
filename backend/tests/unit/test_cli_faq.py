"""CLI FAQ con proveedores simulados y archivos locales de entrada controlados."""

import json
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import cargar_faq as cli
from app.providers import dependencias as llm
from app.providers.embeddings import dependencias as emb
from app.services import curador_faq
from app.models.faq import ResumenCargaFaq


@pytest.mark.parametrize("contenido", ['{}', '[]', '[{"pregunta_canonica":"Pregunta", "respuesta":"inventada"}]',
                                          '[{"pregunta_canonica":"Pregunta", "comision":"F%"}]'])
def test_entrada_invalida_no_crea_proveedores(tmp_path, monkeypatch, contenido):
    archivo = tmp_path / "faq.json"
    archivo.write_text(contenido, encoding="utf-8")
    crear = Mock()
    monkeypatch.setattr(emb, "obtener_gestor_embeddings", crear)
    monkeypatch.setattr(llm, "obtener_gestor_proveedores", crear)
    assert cli.main(["cargar", "--archivo", str(archivo)]) == 1
    crear.assert_not_called()


@pytest.mark.parametrize("valor", ["nan", "inf", "0", "-1"])
def test_presupuesto_invalido_no_crea_proveedores(monkeypatch, valor):
    crear = Mock()
    monkeypatch.setattr(emb, "obtener_gestor_embeddings", crear)
    assert cli.main(["regenerar", "--presupuesto-segundos", valor]) == 1
    crear.assert_not_called()


def test_carga_informa_por_entrada_y_cierra_proveedores(tmp_path, monkeypatch, capsys):
    archivo = tmp_path / "faq.json"
    archivo.write_text(json.dumps([{"pregunta_canonica": "Consulta privada"}] * 2), encoding="utf-8")
    proveedor = Mock(aclose=AsyncMock())
    gestor = Mock(proveedores=[proveedor])
    monkeypatch.setattr(emb, "obtener_gestor_embeddings", lambda: gestor)
    monkeypatch.setattr(llm, "obtener_gestor_proveedores", lambda: Mock(proveedores=[]))
    monkeypatch.setattr(curador_faq, "cargar_pregunta", AsyncMock(side_effect=[
        ResumenCargaFaq(id=3, creada=True), None,
    ]))
    assert cli.main(["cargar", "--archivo", str(archivo)]) == 1
    salida = capsys.readouterr().out
    assert "creada" in salida and "sin_evidencia_o_contexto" in salida
    assert "Consulta privada" not in salida
    proveedor.aclose.assert_awaited_once()


def test_pendientes_diferidas_se_informan_con_codigo_no_cero(monkeypatch, capsys):
    monkeypatch.setattr(emb, "obtener_gestor_embeddings", lambda: Mock(proveedores=[]))
    monkeypatch.setattr(llm, "obtener_gestor_proveedores", lambda: Mock(proveedores=[]))
    monkeypatch.setattr(curador_faq, "regenerar_pendientes", AsyncMock(return_value={
        "procesadas": 1, "regeneradas": 0, "fallidas": 0, "diferidas": 1,
    }))
    assert cli.main(["regenerar"]) == 1
    assert '"diferidas": 1' in capsys.readouterr().out

"""T40: booleano escalar estricto y frontera de errores remotos, sin red real."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError

from app.db import derivaciones as repo


def cliente(datos=True):
    base = MagicMock()
    base.schema.return_value = base
    base.rpc.return_value = base
    base.retry.return_value = base
    base.execute.return_value = SimpleNamespace(data=datos)
    return base


def test_rpc_recibe_unicamente_sector_validado():
    base = cliente()
    assert repo.registrar_derivacion_anonima("  Bedelía  ", cliente=base) is True
    base.schema.assert_called_once_with("public")
    base.rpc.assert_called_once_with("registrar_derivacion_anonima", {"p_sector": "Bedelía"})
    base.retry.assert_called_once_with(False)
    base.execute.assert_called_once_with()
    base.table.assert_not_called()


def test_false_es_booleano_negativo(caplog):
    assert repo.registrar_derivacion_anonima("Bedelía", cliente=cliente(False)) is False


@pytest.mark.parametrize("dato", [
    None, 0, 1, 0.0, 1.0, "true", "false", "True", [], {},
    [{"resultado": True}], {"resultado": True}, [True], object(),
])
def test_otras_respuestas_no_se_convierten_en_true(dato, caplog):
    assert repo.registrar_derivacion_anonima("Bedelía", cliente=cliente(dato)) is False
    assert "respuesta RPC inválida" in caplog.text


@pytest.mark.parametrize("sector", [None, True, 3, [], "", " ", "\t\n", "a" * 201])
def test_sector_invalido_falla_antes_de_crear_cliente(monkeypatch, sector):
    construir = Mock()
    monkeypatch.setattr(repo, "obtener_cliente_supabase", construir)
    with pytest.raises(ValueError):
        repo.registrar_derivacion_anonima(sector)
    construir.assert_not_called()


def test_reutiliza_cliente_central(monkeypatch):
    base = cliente()
    construir = Mock(return_value=base)
    monkeypatch.setattr(repo, "obtener_cliente_supabase", construir)
    assert repo.registrar_derivacion_anonima("Bedelía") is True
    construir.assert_called_once_with()


def test_cliente_ausente_devuelve_false(monkeypatch, caplog):
    monkeypatch.setattr(repo, "obtener_cliente_supabase", lambda: None)
    assert repo.registrar_derivacion_anonima("Bedelía") is False
    assert "cliente ausente" in caplog.text


@pytest.mark.parametrize("error", [
    APIError({"message": "PRIVADO", "code": "XX000", "details": "PRIVADO", "hint": None}),
    httpx.ConnectError("PRIVADO"), httpx.ReadTimeout("PRIVADO"),
])
def test_fallos_remotos_admitidos_no_exponen_payloads(error, caplog, capsys):
    base = cliente()
    base.execute.side_effect = error
    assert repo.registrar_derivacion_anonima("Bedelía", cliente=base) is False
    base.execute.assert_called_once()
    assert type(error).__name__ in caplog.text
    assert "PRIVADO" not in caplog.text + capsys.readouterr().out
    assert all(r.exc_info is None for r in caplog.records)


@pytest.mark.parametrize("etapa", ["cliente", "schema", "rpc", "retry", "execute", "data"])
@pytest.mark.parametrize("tipo", [TypeError, AttributeError])
def test_errores_de_programacion_se_propagan(monkeypatch, etapa, tipo):
    error = tipo("Fallo de programación simulado")
    base = cliente()
    if etapa == "cliente":
        monkeypatch.setattr(repo, "obtener_cliente_supabase", Mock(side_effect=error))
    elif etapa == "data":
        class RespuestaIncorrecta:
            @property
            def data(self):
                raise error
        base.execute.return_value = RespuestaIncorrecta()
    else:
        getattr(base, etapa).side_effect = error
    with pytest.raises(tipo):
        repo.registrar_derivacion_anonima("Bedelía", cliente=None if etapa == "cliente" else base)


def test_otros_errores_internos_tampoco_se_ocultan():
    base = cliente()
    base.execute.side_effect = RuntimeError("Error interno")
    with pytest.raises(RuntimeError):
        repo.registrar_derivacion_anonima("Bedelía", cliente=base)


@pytest.mark.parametrize("estado,dato,esperado", [
    (200, True, True), (200, False, False), (200, "true", False),
    (200, [{"resultado": True}], False),
    (503, {"message": "PRIVADO", "code": "XX000", "hint": None, "details": None}, False),
])
def test_cliente_postgrest_real_con_transporte_simulado(estado, dato, esperado, caplog):
    peticiones = []

    def responder(request):
        peticiones.append(request)
        assert request.method == "POST"
        assert request.url.path == "/rest/v1/rpc/registrar_derivacion_anonima"
        assert json.loads(request.content) == {"p_sector": "Bedelía"}
        return httpx.Response(estado, json=dato)

    with httpx.Client(transport=httpx.MockTransport(responder)) as transporte:
        postgrest = SyncPostgrestClient("https://supabase.invalid/rest/v1", http_client=transporte)
        base = SimpleNamespace(schema=Mock(return_value=postgrest))
        assert repo.registrar_derivacion_anonima("Bedelía", cliente=base) is esperado
    assert len(peticiones) == 1
    assert "PRIVADO" not in caplog.text

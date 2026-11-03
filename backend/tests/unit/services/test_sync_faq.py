"""La fase FAQ es posterior al lease documental y nunca revierte un sync válido."""

import asyncio
from unittest.mock import AsyncMock

from app.services import sincronizador as s
from app.settings import Configuracion
from tests.unit.services.test_sincronizacion_incremental import mundo as mundo, drive, persistido


def test_documento_modificado_finaliza_antes_de_regenerar(mundo, monkeypatch):
    mundo.almacen["uno"] = persistido(id=7)
    mundo.archivos.append(drive(drive_md5_checksum="b" * 32))

    async def regenerar(config, gestor):
        assert mundo.almacen["uno"].drive_md5_checksum == "b" * 32
        assert mundo.bloqueo.call_args.args[1] == "liberar"
        return {"regeneradas": 1}

    post = AsyncMock(side_effect=regenerar)
    monkeypatch.setattr(s, "regenerar_faq_despues_sync", post)
    r = asyncio.run(s.sincronizar_drive_completo(configuracion=Configuracion(), gestor=object()))
    assert r.estado == "ok" and r.actualizados == 1
    post.assert_awaited_once()


def test_fallo_llm_no_rompe_sync_ni_reindexa_de_nuevo(mundo, monkeypatch, caplog):
    mundo.archivos.append(drive())
    monkeypatch.setattr(s, "regenerar_faq_despues_sync", AsyncMock(side_effect=RuntimeError("NO_LOG")))
    r = asyncio.run(s.sincronizar_drive_completo(configuracion=Configuracion(), gestor=object()))
    assert r.estado == "ok" and r.nuevos == 1 and r.errores == 0
    assert mundo.almacen["uno"].estado == "activo"
    mundo.indexacion.assert_awaited_once()
    assert "NO_LOG" not in caplog.text


def test_baja_logica_se_completa_antes_de_procesar_pendientes(mundo, monkeypatch):
    mundo.almacen["uno"] = persistido()
    async def regenerar(config, gestor):
        assert mundo.almacen["uno"].estado == "inactivo"
        assert mundo.almacen["uno"].vigente is False
        assert mundo.bloqueo.call_args.args[1] == "liberar"
    monkeypatch.setattr(s, "regenerar_faq_despues_sync", regenerar)
    r = asyncio.run(s.sincronizar_drive_completo(configuracion=Configuracion(), gestor=object()))
    assert r.inactivados == 1 and r.estado == "ok"


def test_fase_automatica_puede_desactivarse_hasta_medir_t29(mundo, monkeypatch):
    post = AsyncMock()
    monkeypatch.setattr(s, "regenerar_faq_despues_sync", post)
    asyncio.run(s.sincronizar_drive_completo(
        configuracion=Configuracion(faq_regeneracion_maximo=0), gestor=object(),
    ))
    post.assert_not_awaited()

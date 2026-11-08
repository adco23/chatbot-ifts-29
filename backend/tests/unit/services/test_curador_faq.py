"""T41/T43: pipeline compartido real, proveedores/repositorios simulados, sin red."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.faq import FaqPendiente, FragmentoFuenteFaq, ResumenCargaFaq
from app.models.rag import FiltrosRecuperacion
from app.services import conversacion, curador_faq as c
from app.settings import Configuracion
from tests.unit.services.test_conversacion import entorno as entorno, fragmento, recuperacion


def fuente(**cambios):
    datos = fragmento().model_dump(exclude={"similitud"})
    return FragmentoFuenteFaq(**(datos | dict(
        estado_documento="activo", version_documento={"indexado_en": "2026-01-01T00:00:00"},
    ) | cambios))


@pytest.fixture
def carga(entorno, monkeypatch):
    snapshots = Mock(return_value=[fuente()])
    monkeypatch.setattr(c.repositorio, "obtener_fuentes_para_guardar", snapshots)
    guardar = Mock(return_value=ResumenCargaFaq(id=7, creada=True))
    monkeypatch.setattr(c.repositorio, "guardar_faq_curada", guardar)
    cache = AsyncMock()
    monkeypatch.setattr(conversacion, "buscar_faq", cache)
    return SimpleNamespace(entorno=entorno, snapshots=snapshots, guardar=guardar, cache=cache)


def ejecutar(carga, entrada=None):
    return asyncio.run(c.cargar_pregunta(
        entrada or c.EntradaFaq(pregunta_canonica="¿Cuándo abre la inscripción?"),
        configuracion=Configuracion(), gestor_embeddings=carga.entorno.embeddings,
        gestor_llm=carga.entorno.llm,
    ))


def test_carga_explicita_reutiliza_rag_llm_y_fuentes_sin_cache(carga):
    assert ejecutar(carga).id == 7
    faq, fuentes = carga.guardar.call_args.args
    assert faq.respuesta == "Podés inscribirte el 15 de marzo."
    assert faq.pregunta_normalizada == "¿cuando abre la inscripcion?"
    assert faq.embedding == [1.0] + [0.0] * 767
    assert fuentes[0].drive_id == "abc" and fuentes[0].fragmento_ids == [1]
    assert fuentes[0].es_primaria
    assert fuentes[0].version_documento == fuente().version_documento
    carga.cache.assert_not_awaited()
    carga.entorno.embeddings.embed_query.assert_awaited_once()
    carga.entorno.llm.generar_con_respaldo.assert_awaited_once()


def test_carga_repetida_envia_misma_clave_y_preserva_identidad_via_upsert(carga):
    assert ejecutar(carga).id == ejecutar(carga).id
    filas = [llamada.args[0] for llamada in carga.guardar.call_args_list]
    assert filas[0] == filas[1]


def test_no_guarda_si_no_hay_evidencia(carga):
    carga.entorno.rag.return_value = recuperacion([])
    assert ejecutar(carga) is None
    carga.guardar.assert_not_called()
    carga.entorno.llm.generar_con_respaldo.assert_not_awaited()


def test_versiones_se_capturan_antes_de_llm_y_cambios_rechazan(carga):
    carga.snapshots.return_value = [fuente(contenido="Contenido cambiado.")]
    with pytest.raises(conversacion.ErrorConversacion):
        ejecutar(carga)
    carga.entorno.llm.generar_con_respaldo.assert_not_awaited()
    carga.guardar.assert_not_called()


def test_conflicto_de_version_persistencia_no_se_oculta(carga):
    carga.guardar.side_effect = c.repositorio.ErrorRepositorioFaq("Versiones distintas.")
    with pytest.raises(c.repositorio.ErrorRepositorioFaq):
        ejecutar(carga)


def test_captura_snapshot_precede_llm(carga):
    orden = []
    carga.snapshots.side_effect = lambda ids: orden.append("snapshot") or [fuente()]
    respuesta = carga.entorno.llm.generar_con_respaldo.return_value
    async def generar(*args):
        orden.append("llm")
        return respuesta
    carga.entorno.llm.generar_con_respaldo.side_effect = generar
    ejecutar(carga)
    assert orden == ["snapshot", "llm"]


def test_contexto_especifico_no_se_persiste_como_general(carga, monkeypatch):
    f = fuente(materia_id=3, comision="F", periodo="2026C2", tipo_documental="programa")
    gen = conversacion.GeneracionDocumental(
        respuesta=SimpleNamespace(respuesta="Contenidos válidos.", confianza=.8),
        fragmentos=[f.como_fragmento()], primarios=[1],
        embedding=carga.entorno.embeddings.embed_query.return_value,
        filtros=FiltrosRecuperacion(materia_id=3, tipo_documental="programa"),
        fuentes_versionadas=[f],
    )
    monkeypatch.setattr(c, "generar_documental", AsyncMock(return_value=gen))
    ejecutar(carga)
    faq = carga.guardar.call_args.args[0]
    assert (faq.materia_id, faq.comision, faq.periodo) == (3, "F", "2026C2")


def pendiente(id=1):
    return FaqPendiente(id=id, pregunta_canonica="Pregunta curada", embedding=[1.] + [0.] * 767)


def test_regeneracion_reutiliza_vector_y_continua_despues_de_falla(monkeypatch, caplog):
    monkeypatch.setattr(c.repositorio, "listar_faq_pendientes", Mock(return_value=[pendiente(1), pendiente(2)]))
    cargar = AsyncMock(side_effect=[RuntimeError("SECRETO_NO_LOG"), ResumenCargaFaq(id=2, creada=False)])
    monkeypatch.setattr(c, "cargar_pregunta", cargar)
    r = asyncio.run(c.regenerar_pendientes(
        configuracion=Configuracion(), gestor_embeddings=object(), gestor_llm=object(),
    ))
    assert r == dict(procesadas=2, regeneradas=1, fallidas=1, diferidas=0)
    assert cargar.await_args_list[1].kwargs["embedding_consulta"].vector == pendiente().embedding
    assert "SECRETO_NO_LOG" not in caplog.text


def test_regeneracion_acotada_por_cantidad(monkeypatch):
    monkeypatch.setattr(c.repositorio, "listar_faq_pendientes", Mock(return_value=[pendiente(i) for i in (1, 2, 3)]))
    cargar = AsyncMock(return_value=None)
    monkeypatch.setattr(c, "cargar_pregunta", cargar)
    r = asyncio.run(c.regenerar_pendientes(
        configuracion=Configuracion(), gestor_embeddings=object(), gestor_llm=object(), limite=1,
    ))
    assert r["procesadas"] == 1 and cargar.await_count == 1


def test_timeout_conserva_pendiente_sin_escritura(monkeypatch):
    monkeypatch.setattr(c.repositorio, "listar_faq_pendientes", Mock(return_value=[pendiente()]))
    async def lento(*args, **kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(c, "cargar_pregunta", lento)
    guardar = Mock()
    monkeypatch.setattr(c.repositorio, "guardar_faq_curada", guardar)
    r = asyncio.run(c.regenerar_pendientes(
        configuracion=Configuracion(), gestor_embeddings=object(), gestor_llm=object(),
        presupuesto_segundos=.01,
    ))
    assert r["regeneradas"] == 0 and r["diferidas"] == 1
    guardar.assert_not_called()


def test_presupuesto_cero_no_accede_a_servicios(monkeypatch):
    listar = Mock()
    monkeypatch.setattr(c.repositorio, "listar_faq_pendientes", listar)
    asyncio.run(c.regenerar_pendientes(
        configuracion=Configuracion(), gestor_embeddings=object(), gestor_llm=object(),
        presupuesto_segundos=0,
    ))
    listar.assert_not_called()


def test_error_de_listado_se_informa_al_cron_sin_publicar(monkeypatch, caplog):
    monkeypatch.setattr(c.repositorio, "listar_faq_pendientes", Mock(
        side_effect=c.repositorio.ErrorRepositorioFaq("SECRETO_NO_LOG"),
    ))
    with pytest.raises(c.repositorio.ErrorRepositorioFaq):
        asyncio.run(c.regenerar_pendientes(
            configuracion=Configuracion(), gestor_embeddings=object(), gestor_llm=object(),
        ))
    assert "SECRETO_NO_LOG" not in caplog.text

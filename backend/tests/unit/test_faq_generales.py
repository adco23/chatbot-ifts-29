"""Chat y CLI curan evidencia general con el contrato original de match_fragmentos."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.models.faq import FaqPendiente, ResumenCargaFaq
from app.models.rag import FragmentoRecuperado
from app.providers import dependencias as dependencias_llm
from app.providers.base import RespuestaLLM
from app.providers.embeddings import dependencias as dependencias_embeddings
from app.services import conversacion, curador_faq, recuperador
from app.settings import Configuracion
from scripts import cargar_faq
from tests.unit.services.test_cache_faq import evidencia
from tests.unit.test_chat_faq import entorno as entorno, http as http, preguntar


@pytest.fixture(params=[
    ("¿Cuánto dura la carrera?", "La carrera dura tres años.", [35, 123], 0.7225),
    ("¿Cuándo empiezan las clases?", "Las clases empiezan el 17 de marzo.", [120], 0.7240),
])
def general(request, entorno, monkeypatch):
    pregunta, respuesta, ids, score = request.param
    fuentes = [evidencia(
        fragmento_id=identidad, documento_id=20 + indice,
        drive_id=f"general-{indice}", titulo=f"GENERAL__Documento-{indice}",
        contenido=respuesta, tipo_documental="general", es_primaria=False,
    ) for indice, identidad in enumerate(ids)]
    entorno.filas[:] = fuentes

    def buscar(vector, *, filtros, top_k, documento_ids=None, modalidad="primaria"):
        # La RPC general no devuelve tipo_documental. El snapshot sí lo incluye.
        return [FragmentoRecuperado.model_validate(
            f.como_fragmento().model_dump(exclude={"tipo_documental"}) | {"similitud": score}
        ) for f in fuentes if documento_ids is None or f.documento_id in documento_ids][:top_k]

    buscar_rag = Mock(side_effect=buscar)
    snapshots = Mock(side_effect=lambda solicitados: [
        f for f in fuentes if f.fragmento_id in solicitados
    ])
    guardar = Mock(return_value=ResumenCargaFaq(id=9, creada=True))
    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", buscar_rag)
    monkeypatch.setattr(curador_faq.repositorio, "obtener_fuentes_para_guardar", snapshots)
    monkeypatch.setattr(curador_faq.repositorio, "guardar_faq_curada", guardar)
    entorno.llm.generar_con_respaldo.return_value = RespuestaLLM(json.dumps({
        "respuesta": respuesta, "fragmento_ids": ids,
    }), "groq", "simulado")
    return SimpleNamespace(
        e=entorno, pregunta=pregunta, respuesta=respuesta, fuentes=fuentes,
        ids=ids, score=score, buscar=buscar_rag, snapshots=snapshots, guardar=guardar,
    )


def comprobar_faq_general(general):
    faq, fuentes = general.guardar.call_args.args
    assert faq.pregunta_canonica == general.pregunta
    assert faq.respuesta == general.respuesta
    assert (faq.materia_id, faq.comision, faq.periodo) == (None, None, None)
    assert faq.tipo_documental == "general" and faq.vigencia_hasta is None
    assert faq.confianza_evidencia == general.score
    assert faq.embedding == general.e.embeddings.embed_query.return_value.vector
    assert [identidad for f in fuentes for identidad in f.fragmento_ids] == general.ids
    assert all(f.es_primaria for f in fuentes)
    assert [f.version_documento for f in fuentes] == [
        f.version_documento for f in general.fuentes
    ]


def test_chat_respondida_y_cli_cargan_misma_evidencia_general(
    http, general, tmp_path, monkeypatch, capsys,
):
    r = preguntar(http, general.pregunta)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["respuesta"] == general.respuesta
    assert r.json()["confianza"] == general.score and r.json()["umbral"] == 0.70
    assert all(r.json()["contexto"][campo] is None
               for campo in ("materia_id", "comision", "periodo"))
    assert [identidad for f in r.json()["fuentes"] for identidad in f["fragmento_ids"]] == general.ids
    general.guardar.assert_not_called()
    general.snapshots.assert_not_called()

    archivo = tmp_path / "faq.json"
    archivo.write_text(json.dumps([{"pregunta_canonica": general.pregunta}]), encoding="utf-8")
    monkeypatch.setattr(dependencias_embeddings, "obtener_gestor_embeddings", lambda: general.e.embeddings)
    monkeypatch.setattr(dependencias_llm, "obtener_gestor_proveedores", lambda: general.e.llm)
    assert cargar_faq.main(["cargar", "--archivo", str(archivo)]) == 0
    assert json.loads(capsys.readouterr().out)["estado"] == "creada"
    comprobar_faq_general(general)
    general.e.buscar_faq.assert_called_once()  # T41 omite la caché.
    assert general.e.llm.generar_con_respaldo.await_count == 2
    consultas = general.e.llm.generar_con_respaldo.await_args_list
    assert consultas[0].args[0] == consultas[1].args[0]
    assert general.buscar.call_args_list[0].kwargs == general.buscar.call_args_list[1].kwargs
    general.snapshots.assert_called_once_with(general.ids)
    assert general.e.embeddings.embed_query.await_count == 2


def test_regeneracion_general_conserva_contexto_nulo_y_embedding(general, monkeypatch):
    vector = general.e.embeddings.embed_query.return_value.vector
    monkeypatch.setattr(curador_faq.repositorio, "listar_faq_pendientes", Mock(return_value=[
        FaqPendiente(id=9, pregunta_canonica=general.pregunta, embedding=vector),
    ]))
    resumen = asyncio.run(curador_faq.regenerar_pendientes(
        configuracion=Configuracion(), gestor_embeddings=general.e.embeddings,
        gestor_llm=general.e.llm,
    ))
    assert resumen == {"procesadas": 1, "regeneradas": 1, "fallidas": 0, "diferidas": 0}
    comprobar_faq_general(general)
    general.e.embeddings.embed_query.assert_not_awaited()
    general.e.buscar_faq.assert_not_called()


@pytest.mark.parametrize("cambios", [
    {"contenido": "Contenido modificado."}, {"vigente": False},
    {"estado_documento": "inactivo"}, {"materia_id": 47},
    {"comision": "F"}, {"periodo": "2026C2"},
])
def test_snapshot_general_no_relaja_contenido_contexto_o_vigencia(general, cambios):
    general.snapshots.side_effect = None
    general.snapshots.return_value = [f.model_copy(update=cambios) for f in general.fuentes]
    with pytest.raises(conversacion.ErrorConversacion):
        asyncio.run(curador_faq.cargar_pregunta(
            curador_faq.EntradaFaq(pregunta_canonica=general.pregunta),
            configuracion=Configuracion(), gestor_embeddings=general.e.embeddings,
            gestor_llm=general.e.llm,
        ))
    general.e.llm.generar_con_respaldo.assert_not_awaited()
    general.guardar.assert_not_called()


def test_tipo_conocido_en_retrieval_se_sigue_comparando(general):
    buscar_original = general.buscar.side_effect

    def buscar_con_tipo(*args, **kwargs):
        return [f.model_copy(update={"tipo_documental": "general"})
                for f in buscar_original(*args, **kwargs)]

    general.buscar.side_effect = buscar_con_tipo
    general.snapshots.side_effect = None
    general.snapshots.return_value = [
        f.model_copy(update={"tipo_documental": "programa"}) for f in general.fuentes
    ]
    with pytest.raises(conversacion.ErrorConversacion):
        asyncio.run(curador_faq.cargar_pregunta(
            curador_faq.EntradaFaq(pregunta_canonica=general.pregunta),
            configuracion=Configuracion(), gestor_embeddings=general.e.embeddings,
            gestor_llm=general.e.llm,
        ))
    general.e.llm.generar_con_respaldo.assert_not_awaited()
    general.guardar.assert_not_called()

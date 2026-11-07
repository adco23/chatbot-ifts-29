"""Plan general y programas históricos con catálogo oficial y RPC simuladas."""

import json

import pytest

from app.models.chat import MateriaChat
from app.services import comisiones
from tests.unit.services.test_cache_faq import coincidencia, evidencia
from tests.unit.test_chat_cierre_mvp import (
    cierre as cierre, documento, fragmento, http as http, preguntar,
)


CONTEXTO = {"materia_id": 4, "comision": "C", "periodo": "2026C2"}


@pytest.fixture
def corpus(cierre):
    cierre.materias.return_value = [
        MateriaChat(id=4, codigo="LC", nombre="Lógica Computacional"),
        MateriaChat(id=2, codigo="ABD", nombre="Administración de Base de Datos"),
    ]
    plan = cierre.documentos[0]
    cierre.filas[0] = fragmento(plan, "Total: 23 unidades curriculares.")
    programa = documento(
        10, titulo="2023C1__Logica-Computacional__Todas__Programa.pdf",
        materia_id=4, comision="TODAS", periodo="2023C1", tipo_documental="programa",
    )
    cierre.documentos.append(programa)
    cierre.filas.append(fragmento(programa, "Los temas incluyen lógica proposicional y tablas de verdad."))
    cierre.programa = programa
    consulta = cierre.buscar.side_effect

    def sql(vector, *, filtros, top_k, documento_ids, modalidad="primaria"):
        filas = consulta(vector, filtros=filtros, top_k=top_k,
                         documento_ids=documento_ids, modalidad=modalidad)
        if filtros.tipo_documental == "programa" and modalidad == "comun":
            # 0005 real sólo devuelve NULL. TODAS debe funcionar mediante el fallback
            # genérico existente, sin fingir que ya se modificó la RPC en Supabase.
            return [f for f in filas if f.comision is None]
        if filtros.tipo_documental is None:
            return [f.model_copy(update={"tipo_documental": None}) for f in filas]
        return filas

    cierre.buscar.side_effect = sql
    return cierre


def test_plan_ignora_filtros_heredados_sin_borrar_memoria(http, corpus):
    r = preguntar(http, "¿Cuántas materias tiene la carrera?", CONTEXTO)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert "23 unidades curriculares" in r.json()["respuesta"]
    assert r.json()["fuentes"][0]["documento_id"] == 1
    for llamada in corpus.buscar.call_args_list:
        filtros = llamada.kwargs["filtros"]
        assert filtros.materia_id is filtros.comision is filtros.periodo is None
        assert llamada.kwargs["documento_ids"] == [1]
    contexto_faq = corpus.faq.call_args.kwargs["contexto"]
    assert contexto_faq.materia_id is contexto_faq.comision is contexto_faq.periodo is None
    assert all(r.json()["contexto"][k] == v for k, v in CONTEXTO.items())


def agregar_fragmento_curricular(corpus):
    corpus.filas.append(fragmento(
        corpus.documentos[0], "Primer cuatrimestre: Técnicas de Programación y Lógica Computacional.",
        fragmento_id=539, orden=2, similitud=0.9,
    ))


@pytest.mark.parametrize("total", [17, 23])
def test_total_plan_entre_varios_fragmentos_cita_el_agregado_explicito(http, corpus, total):
    corpus.filas[0] = fragmento(corpus.documentos[0], f"Total: {total} unidades curriculares.")
    agregar_fragmento_curricular(corpus)
    # El LLM simulado usa el primer fragmento del prompt. El curricular tiene mayor
    # similitud, pero no puede desplazar al único que demuestra el total solicitado.
    r = preguntar(http, "¿Cuántas materias tiene la carrera?", CONTEXTO)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert f"{total} unidades curriculares" in r.json()["respuesta"]
    assert r.json()["fuentes"][0]["fragmento_ids"] == [10]
    pedido = corpus.llm.generar_con_respaldo.call_args.args[0]
    evidencia = json.loads(pedido.contexto)
    assert [f["fragmento_id"] for f in evidencia] == [10]
    assert f"Total: {total} unidades curriculares." in evidencia[0]["contenido"]


def test_total_plan_varios_fragmentos_sin_agregado_se_abstiene_antes_del_llm(http, corpus):
    corpus.filas[0] = fragmento(corpus.documentos[0], "Segundo cuatrimestre: más unidades curriculares.")
    agregar_fragmento_curricular(corpus)
    r = preguntar(http, "¿Cuántas materias tiene la carrera?", CONTEXTO)
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    corpus.llm.generar_con_respaldo.assert_not_called()


def test_total_plan_numero_incorrecto_se_rechaza_aun_citando_agregado(http, corpus):
    agregar_fragmento_curricular(corpus)
    corpus.salida = "La carrera tiene 6 unidades curriculares."
    r = preguntar(http, "¿Cuántas materias tiene la carrera?", CONTEXTO)
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["fuentes"] == []
    corpus.llm.generar_con_respaldo.assert_awaited_once()


def test_total_plan_con_agregados_contradictorios_se_abstiene_antes_del_llm(http, corpus):
    corpus.filas.append(fragmento(
        corpus.documentos[0], "Total: 19 unidades curriculares.", fragmento_id=540, orden=2,
    ))
    r = preguntar(http, "¿Cuántas materias tiene la carrera?", CONTEXTO)
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    corpus.llm.generar_con_respaldo.assert_not_called()


def test_plan_hit_general_no_borra_contexto_academico(http, corpus):
    corpus.faq.return_value = coincidencia(
        pregunta_canonica="¿Cuántas materias tiene la carrera?",
        respuesta="La carrera tiene 23 unidades curriculares.",
    )
    corpus.evidencia.return_value = [evidencia(
        documento_id=1, fragmento_id=10, drive_id="drive-1", titulo=corpus.documentos[0].titulo,
        contenido="Total: 23 unidades curriculares.",
    )]
    r = preguntar(http, "¿Cuántas materias tiene la carrera?", CONTEXTO)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["contexto"]["materia_id"] == 4
    corpus.contador.assert_called_once_with(7)
    corpus.buscar.assert_not_called()
    corpus.llm.generar_con_respaldo.assert_not_called()


@pytest.mark.parametrize("contexto", [{}, CONTEXTO])
def test_programa_historico_todas_usa_fallback_sin_periodo_heredado(http, corpus, contexto):
    r = preguntar(http, "¿Qué temas se ven en Lógica Computacional?", contexto)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["fuentes"][0]["documento_id"] == corpus.programa.id
    assert r.json()["contexto"]["comision"] == contexto.get("comision")
    assert any(c.kwargs["filtros"].tipo_documental is None
               and c.kwargs["documento_ids"] == [10] for c in corpus.buscar.call_args_list)
    assert all(c.kwargs["filtros"].periodo is None for c in corpus.buscar.call_args_list)
    assert not comisiones.cargar_catalogo().documento_habilitado(corpus.programa, "2026C2")


@pytest.mark.parametrize("periodo", ["2023C1", "2023-1C"])
def test_programa_respeta_periodo_explicito_aunque_no_este_en_catalogo(http, corpus, periodo):
    r = preguntar(http, f"¿Qué contenidos tenía Lógica Computacional en {periodo}?", CONTEXTO)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["fuentes"][0]["documento_id"] == 10
    assert all(c.kwargs["filtros"].periodo == "2023C1" for c in corpus.buscar.call_args_list)


def test_programa_explicito_actual_no_usa_el_historico(http, corpus):
    r = preguntar(http, "¿Qué contenidos tiene Lógica Computacional en 2026C2?", CONTEXTO)
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    corpus.llm.generar_con_respaldo.assert_not_called()


@pytest.mark.parametrize("cambios", [{"estado": "inactivo"}, {"vigente": False}, {"comision": "F"}])
def test_excepcion_programa_no_admite_inactivos_no_vigentes_o_comisiones_ajenas(http, corpus, cambios):
    corpus.documentos[-1] = corpus.programa.model_copy(update=cambios)
    r = preguntar(http, "¿Qué temas se ven en Lógica Computacional?", CONTEXTO)
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    corpus.llm.generar_con_respaldo.assert_not_called()


def agregar_cronograma(corpus, *, identidad=20, periodo="2026C2", materia=2, comision="A"):
    doc = documento(identidad, titulo="Cronograma", tipo_documental="cronograma",
                    materia_id=materia, comision=comision, periodo=periodo)
    corpus.documentos.append(doc)
    corpus.filas.append(fragmento(doc, "Primer parcial: 2 de octubre de 2026.\n"
                                      "Segundo parcial: 20 de noviembre de 2026."))
    return doc


def test_cronograma_historico_no_satisface_periodo_actual(http, corpus):
    agregar_cronograma(corpus, periodo="2023C1", materia=4, comision="C")
    r = preguntar(http, "¿Cuándo es el segundo parcial de Lógica Computacional comisión C?", CONTEXTO)
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    corpus.llm.generar_con_respaldo.assert_not_called()


def test_parciales_consecutivos_conservan_materia_comision_y_periodo(http, corpus):
    agregar_cronograma(corpus)
    corpus.salida = "Primer parcial: 2 de octubre de 2026."
    primera = preguntar(http, "¿Cuándo es el primer parcial de Base de Datos comisión A?")
    assert primera.status_code == 200 and primera.json()["estado"] == "respondida"
    corpus.salida = "Segundo parcial: 20 de noviembre de 2026."
    segunda = preguntar(http, "¿Y el segundo parcial?", primera.json()["contexto"])
    assert segunda.status_code == 200 and segunda.json()["estado"] == "respondida"
    contexto = segunda.json()["contexto"]
    assert contexto["materia_id"] == 2 and contexto["comision"] == "A" and contexto["periodo"] == "2026C2"
    assert all(c.kwargs["filtros"].periodo == "2026C2" for c in corpus.buscar.call_args_list)


def test_parcial_logica_seguido_de_plan_no_hereda_restricciones(http, corpus):
    agregar_cronograma(corpus, materia=4, comision="C")
    corpus.salida = "Segundo parcial: 20 de noviembre de 2026."
    primera = preguntar(http, "¿Cuándo es el segundo parcial de Lógica Computacional comisión C?")
    assert primera.json()["estado"] == "respondida"
    corpus.buscar.reset_mock()
    corpus.salida = None
    segunda = preguntar(http, "¿Cuántas materias tiene la carrera?", primera.json()["contexto"])
    assert segunda.status_code == 200 and segunda.json()["estado"] == "respondida"
    assert segunda.json()["fuentes"][0]["documento_id"] == 1
    assert corpus.buscar.call_args.kwargs["filtros"].materia_id is None

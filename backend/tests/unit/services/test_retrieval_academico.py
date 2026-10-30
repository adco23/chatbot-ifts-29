"""Regresiones académicas sin red, con el recuperador real y repositorio simulado."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app.models.chat import ContextoChat, MateriaChat, SolicitudChat
from app.models.rag import DocumentoPersistido, FiltrosRecuperacion, FragmentoRecuperado
from app.providers.base import RespuestaLLM
from app.providers.dependencias import obtener_gestor_proveedores
from app.providers.embeddings.base import ResultadoEmbedding
from app.providers.embeddings.dependencias import obtener_gestor_embeddings
from app.services import conversacion, recuperador
from app.services.intencion_documental import clasificar_intencion_documental
from app.settings import Configuracion, obtener_configuracion


def fragmento(identidad=1, tipo="cronograma", comision="F", **cambios):
    datos = dict(fragmento_id=identidad, documento_id=identidad, contenido="Las clases son los lunes.",
                 orden=1, similitud=0.8, titulo=f"Documento {identidad}", drive_id=str(identidad),
                 materia_id=47, comision=comision, periodo="2026C2", vigente=True,
                 tipo_documental=tipo)
    return FragmentoRecuperado(**(datos | cambios))


def gestor():
    return SimpleNamespace(embed_query=AsyncMock(return_value=ResultadoEmbedding(
        [1.0] + [0.0] * 767, "gemini", "gemini-embedding-001", 768)))


@pytest.mark.parametrize("texto,tipo", [
    ("horario", "cronograma"), ("horarios", "cronograma"), ("cronograma", "cronograma"),
    ("clase", "cronograma"), ("clases", "cronograma"), ("parcial", "cronograma"),
    ("parciales", "cronograma"), ("examen", "cronograma"), ("exámenes", "cronograma"),
    ("fecha de parcial", "cronograma"), ("fechas de cursada", "cronograma"),
    ("contenidos", "programa"), ("contenido", "programa"), ("programa", "programa"),
    ("objetivos", "programa"), ("unidades", "programa"), ("temas", "programa"),
    ("bibliografía", "programa"), ("inscripción", "general"), ("ingresantes", "general"),
    ("documentación para ingresar", "general"), ("SIU", "general"), ("Moodle", "general"),
    ("información institucional", "general"), ("hola", None), ("temas y horarios", "cronograma"),
    ("subprograma", None),
])
def test_clasifica_sin_llm(texto, tipo):
    assert clasificar_intencion_documental(texto, Configuracion()) == tipo


def test_clasificacion_configurable(monkeypatch):
    monkeypatch.setenv("RAG_TERMINOS_CRONOGRAMA", "cursada,agenda")
    config = Configuracion()
    assert clasificar_intencion_documental("agenda", config) == "cronograma"
    assert clasificar_intencion_documental("horario", config) is None


def recuperar(monkeypatch, respuestas, *, tipo="cronograma", comision="F", periodo=None, **kwargs):
    buscar = Mock(side_effect=respuestas)
    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", buscar)
    embeddings = gestor()
    resultado = asyncio.run(recuperador.recuperar_fragmentos(
        "Consulta", filtros=FiltrosRecuperacion(materia_id=47, comision=comision,
                                               periodo=periodo, tipo_documental=tipo),
        gestor=embeddings, configuracion=Configuracion(), **kwargs,
    ))
    return resultado, buscar, embeddings


def test_cronograma_primario_precede_general_y_confianza_no_sube(monkeypatch):
    primario = fragmento()
    general = fragmento(2, "general", None, materia_id=None, similitud=0.99)
    resultado, buscar, embeddings = recuperar(monkeypatch, [[primario], [general]])
    assert [f.fragmento_id for f in resultado.fragmentos] == [1, 2]
    assert resultado.confianza == resultado.mejor_similitud == 0.8
    assert resultado.fragmento_ids_primarios == [1] and resultado.evidencia_suficiente
    assert [c.kwargs["modalidad"] for c in buscar.call_args_list] == ["primaria", "complementaria"]
    embeddings.embed_query.assert_awaited_once()


@pytest.mark.parametrize("filas", [[], [fragmento(tipo="programa")],
    [fragmento(tipo="general", comision=None, materia_id=None)],
    [fragmento(comision="A")], [fragmento(periodo=None)], [fragmento(periodo="2026C1")],
    [fragmento(vigente=False)], [fragmento(materia_id=99)],
])
def test_solo_cronograma_exacto_habilita_respuesta(monkeypatch, filas):
    resultado, buscar, _ = recuperar(monkeypatch, [filas], periodo="2026C2")
    assert not resultado.evidencia_suficiente and resultado.fragmento_ids_primarios == []
    assert resultado.confianza == 0
    buscar.assert_called_once()  # Nunca buscar complementos para rescatar la consulta.


def test_primaria_bajo_umbral_no_busca_complementos(monkeypatch):
    resultado, buscar, _ = recuperar(monkeypatch, [[fragmento(similitud=0.699)]])
    assert not resultado.evidencia_suficiente and resultado.confianza == 0.699
    buscar.assert_called_once()


def test_periodo_ausente_no_se_infiere(monkeypatch):
    resultado, buscar, _ = recuperar(monkeypatch, [[fragmento(periodo=None)], []])
    assert resultado.evidencia_suficiente
    assert buscar.call_args_list[0].kwargs["filtros"].periodo is None


def test_programa_comun_permitido_y_contexto_explicito_prioritario(monkeypatch):
    comun = fragmento(tipo="programa", comision=None)
    resultado, buscar, _ = recuperar(monkeypatch, [[comun], []], tipo="programa", comision=None)
    assert resultado.evidencia_suficiente and resultado.fragmento_ids_primarios == [1]
    assert buscar.call_args_list[0].kwargs["modalidad"] == "comun"

    especifico = fragmento(tipo="programa", comision="F")
    resultado, buscar, _ = recuperar(monkeypatch, [[especifico], []], tipo="programa")
    assert resultado.fragmento_ids_primarios == [1]
    assert [c.kwargs["modalidad"] for c in buscar.call_args_list] == ["primaria", "complementaria"]


def test_programa_especifico_insuficiente_prueba_comun_con_mismo_embedding(monkeypatch):
    comun = fragmento(2, "programa", None)
    resultado, buscar, embeddings = recuperar(monkeypatch, [[], [comun], []], tipo="programa")
    assert resultado.evidencia_suficiente and resultado.fragmento_ids_primarios == [2]
    assert [c.kwargs["modalidad"] for c in buscar.call_args_list] == ["primaria", "comun", "complementaria"]
    embeddings.embed_query.assert_awaited_once()


def test_top_k_no_desplaza_primaria_y_deduplica(monkeypatch):
    primario = fragmento()
    resultado, _, _ = recuperar(monkeypatch, [[primario], [primario,
        fragmento(2, "general", None, materia_id=None, similitud=0.99),
        fragmento(3, "programa", None, similitud=1.0)]], top_k=2)
    assert [f.fragmento_id for f in resultado.fragmentos] == [1, 2]
    assert resultado.confianza == 0.8


@pytest.fixture
def entorno_academico(monkeypatch):
    filas = [fragmento(), fragmento(2, "programa", None),
             fragmento(3, "general", None, materia_id=None, similitud=0.99)]
    materias = [MateriaChat(id=47, codigo="TP", nombre="Técnicas de Programación")]
    monkeypatch.setattr(conversacion.repositorio, "listar_materias_chat", Mock(return_value=materias))
    documentos = Mock(side_effect=lambda: [DocumentoPersistido(
        id=f.documento_id, drive_id=f.drive_id, titulo=f.titulo, materia_id=f.materia_id,
        comision=f.comision, periodo=f.periodo, tipo_documental=f.tipo_documental,
        vigente=True, estado="activo",
    ) for f in filas])
    monkeypatch.setattr(conversacion.repositorio, "listar_documentos_chat", documentos)
    monkeypatch.setattr(conversacion, "recuperar_fragmentos", recuperador.recuperar_fragmentos)
    monkeypatch.setattr(conversacion, "obtener_metadatos_archivo", Mock(return_value={}))

    # Doble del contrato SQL; su ejecución real se verifica sólo mediante SQL manual.
    def buscar(vector, *, filtros, modalidad, top_k, documento_ids):
        candidatos = [f for f in filas if documento_ids is None or f.documento_id in documento_ids]
        if modalidad == "complementaria":
            return [f for f in candidatos if f.tipo_documental == "general"][:top_k]
        return [f for f in candidatos if recuperador._es_primario(f, filtros, modalidad)][:top_k]

    consulta = Mock(side_effect=buscar)
    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", consulta)
    llm = SimpleNamespace(generar_con_respaldo=AsyncMock(return_value=RespuestaLLM(
        json.dumps({"respuesta": "Las clases son los lunes.", "fragmento_ids": [1]}), "falso", "falso")))
    return SimpleNamespace(filas=filas, consulta=consulta, embeddings=gestor(), llm=llm,
                           documentos=documentos)


@pytest.fixture
def http_academico(entorno_academico, configuracion_aislada):
    from app.main import crear_app

    app = crear_app()
    app.dependency_overrides[obtener_gestor_embeddings] = lambda: entorno_academico.embeddings
    app.dependency_overrides[obtener_gestor_proveedores] = lambda: entorno_academico.llm
    with TestClient(app) as cliente:
        yield cliente


def preguntar(http, mensaje, contexto=None):
    return http.post("/chat", headers={"X-Sesion-Id": "11111111-1111-4111-8111-111111111111"},
                     json={"mensaje": mensaje, "contexto": contexto or {}})


@pytest.mark.parametrize("mensaje,estado,campo", [
    ("Cual es el horario de Tecnicas de Programacion?", "requiere_contexto", "comision"),
    ("Cual es el horario de Tecnicas de Programacion comision F?", "respondida", None),
    ("Cual es el horario de Tecnicas de Programacion comision A?", "sin_evidencia", None),
    ("Cual es el horario?", "requiere_contexto", "materia_id"),
])
def test_regresiones_post_chat(http_academico, entorno_academico, mensaje, estado, campo):
    respuesta = preguntar(http_academico, mensaje)
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["estado"] == estado and datos["campo_requerido"] == campo
    if estado != "respondida":
        entorno_academico.llm.generar_con_respaldo.assert_not_called()
    else:
        assert datos["confianza"] == 0.8
        assert datos["fuentes"][0]["documento_id"] == 1
        peticion = entorno_academico.llm.generar_con_respaldo.call_args.args[0]
        assert json.loads(peticion.contexto)[0]["evidencia"] == "primaria"


def test_contenidos_usa_programa_comun(http_academico, entorno_academico):
    entorno_academico.llm.generar_con_respaldo.return_value = RespuestaLLM(
        json.dumps({"respuesta": "Las clases son los lunes.", "fragmento_ids": [2]}), "falso", "falso")
    respuesta = preguntar(http_academico, "Que contenidos tiene Tecnicas de Programacion?")
    assert respuesta.json()["estado"] == "respondida"
    assert respuesta.json()["fuentes"][0]["documento_id"] == 2
    assert entorno_academico.consulta.call_args_list[0].kwargs["modalidad"] == "comun"


@pytest.mark.parametrize("periodo", ["2026C1", "2027C1"])
def test_periodo_explicito_no_responde_con_otro(http_academico, entorno_academico, periodo):
    respuesta = preguntar(http_academico, "Horario de Tecnicas de Programacion comision F",
                          {"periodo": periodo})
    assert respuesta.json()["estado"] == "sin_evidencia"
    entorno_academico.llm.generar_con_respaldo.assert_not_called()


def test_llm_no_puede_citar_solo_complementos(http_academico, entorno_academico):
    entorno_academico.llm.generar_con_respaldo.return_value = RespuestaLLM(
        json.dumps({"respuesta": "Las clases son los lunes.", "fragmento_ids": [3]}), "falso", "falso")
    respuesta = preguntar(http_academico, "Horario de Tecnicas de Programacion comision F")
    assert respuesta.status_code == 503


def test_pendiente_mantiene_intencion_al_aclarar_comision(http_academico):
    respuesta = preguntar(http_academico, "comision F", {"materia_id": 47,
        "consulta_pendiente": "Cual es el horario de Tecnicas de Programacion?"})
    assert respuesta.json()["estado"] == "respondida"
    assert respuesta.json()["contexto"]["consulta_pendiente"] is None


def test_programa_no_satisface_horario_http(http_academico, entorno_academico):
    entorno_academico.filas[:] = [f for f in entorno_academico.filas if f.tipo_documental != "cronograma"]
    respuesta = preguntar(http_academico, "Horario de Tecnicas de Programacion comision F")
    assert respuesta.json()["estado"] == "sin_evidencia"
    entorno_academico.llm.generar_con_respaldo.assert_not_called()


@pytest.mark.parametrize("override,estado,umbral", [(None, "sin_evidencia", 0.68), (0.67, "respondida", 0.67)])
def test_cronograma_f_score_real_respeta_umbral_especifico_sin_inflarlo(
    http_academico, entorno_academico, override, estado, umbral,
):
    score = 0.679640252611623
    entorno_academico.filas[0] = fragmento(similitud=score)
    http_academico.app.dependency_overrides[obtener_configuracion] = lambda: Configuracion(
        rag_umbral_cronograma=override,
    )
    respuesta = preguntar(http_academico, "Horario de Tecnicas de Programacion comision F").json()
    assert respuesta["estado"] == estado and respuesta["umbral"] == umbral
    assert respuesta["confianza"] == score  # General complementario 0.99 no interviene.
    if estado == "sin_evidencia":
        entorno_academico.llm.generar_con_respaldo.assert_not_called()


def test_contenidos_unico_programa_especifico_no_inventa_comision(http_academico, entorno_academico, monkeypatch):
    entorno_academico.filas[1] = fragmento(2, "programa", "F")
    comparar = Mock(side_effect=AssertionError("Un solo programa no requiere comparar"))
    monkeypatch.setattr(conversacion.repositorio, "obtener_firmas_contenido_documentos", comparar)
    entorno_academico.llm.generar_con_respaldo.return_value = RespuestaLLM(
        json.dumps({"respuesta": "Las clases son los lunes.", "fragmento_ids": [2]}), "falso", "falso")
    datos = preguntar(http_academico, "Que contenidos tiene Tecnicas de Programacion?").json()
    assert datos["estado"] == "respondida" and datos["contexto"]["comision"] is None
    assert datos["fuentes"][0]["documento_id"] == 2
    assert entorno_academico.consulta.call_args_list[0].kwargs["documento_ids"] == [2]
    comparar.assert_not_called()


@pytest.mark.parametrize("iguales,estado", [(False, "requiere_contexto"), (True, "respondida")])
def test_programas_de_varias_comisiones_solo_piden_contexto_si_distintos(
    http_academico, entorno_academico, monkeypatch, iguales, estado,
):
    entorno_academico.filas[1] = fragmento(2, "programa", "F")
    entorno_academico.filas.append(fragmento(4, "programa", "A", contenido="Otros contenidos."))
    comparar = Mock(return_value={2: "igual", 4: "igual" if iguales else "diferente"})
    monkeypatch.setattr(conversacion.repositorio, "obtener_firmas_contenido_documentos", comparar)
    entorno_academico.llm.generar_con_respaldo.return_value = RespuestaLLM(
        json.dumps({"respuesta": "Las clases son los lunes.", "fragmento_ids": [2]}), "falso", "falso")
    datos = preguntar(http_academico, "Que contenidos tiene Tecnicas de Programacion?").json()
    assert datos["estado"] == estado and datos["contexto"]["comision"] is None
    comparar.assert_called_once_with([2, 4])
    if not iguales:
        assert datos["campo_requerido"] == "comision"
        entorno_academico.llm.generar_con_respaldo.assert_not_called()
        entorno_academico.embeddings.embed_query.assert_not_called()


def test_programa_comun_existente_insuficiente_no_pide_comision(http_academico, entorno_academico):
    entorno_academico.filas[1] = fragmento(2, "programa", None, similitud=0.6)
    entorno_academico.filas.append(fragmento(4, "programa", "F", similitud=0.95))
    datos = preguntar(http_academico, "Que contenidos tiene Tecnicas de Programacion?").json()
    assert datos["estado"] == "sin_evidencia" and datos["campo_requerido"] is None
    assert datos["confianza"] == 0.6
    entorno_academico.llm.generar_con_respaldo.assert_not_called()


def test_override_cronograma_no_cambia_umbral_generico(monkeypatch):
    buscar = Mock(return_value=[fragmento(similitud=0.679640252611623)])
    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", buscar)
    resultado = asyncio.run(recuperador.recuperar_fragmentos(
        "Pregunta general", gestor=gestor(), configuracion=Configuracion(rag_umbral_cronograma=0.67),
    ))
    assert resultado.umbral == 0.68 and not resultado.evidencia_suficiente

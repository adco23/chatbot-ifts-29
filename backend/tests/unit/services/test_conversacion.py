"""T33–T37 sin red: evidencia, paráfrasis, contexto, fuentes y abstención."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.models.chat import ContextoChat, MateriaChat, SolicitudChat
from app.models.rag import DocumentoPersistido, FragmentoRecuperado, ResultadoRecuperacion
from app.providers.base import ContenidoBloqueado, RespuestaLLM
from app.services import conversacion as c
from app.services.temporalidad import es_temporal, fechas_respaldadas
from app.settings import Configuracion


def fragmento(**cambios):
    return FragmentoRecuperado(**dict({
        "fragmento_id": 1, "documento_id": 2, "contenido": "La inscripción abre el 15 de marzo.",
        "orden": 1, "similitud": 0.8, "titulo": "Calendario", "drive_id": "abc",
        "materia_id": None, "comision": None, "periodo": None, "vigente": True,
    }, **cambios))


def recuperacion(filas):
    mejor = max((f.similitud for f in filas), default=None)
    return ResultadoRecuperacion(
        fragmentos=filas, mejor_similitud=mejor, confianza=max(0, min(1, mejor or 0)),
        umbral=0.68, evidencia_suficiente=mejor is not None and mejor >= 0.68,
    )


def salida(**cambios):
    return json.dumps(dict({"respuesta": "Podés inscribirte el 15 de marzo.",
                            "fragmento_ids": [1], "citas_textuales": []}, **cambios))


@pytest.fixture
def entorno(monkeypatch):
    materias = Mock(return_value=[MateriaChat(id=3, codigo="PROG", nombre="Programación")])
    documentos = Mock(return_value=[DocumentoPersistido(id=2, titulo="Calendario", drive_id="abc")])
    rag = AsyncMock(return_value=recuperacion([fragmento()]))
    metadatos = Mock(return_value={"id": "abc", "webViewLink": "https://drive.google.com/a"})
    monkeypatch.setattr(c.repositorio, "listar_materias_chat", materias)
    monkeypatch.setattr(c.repositorio, "listar_documentos_chat", documentos)
    monkeypatch.setattr(c, "recuperar_fragmentos", rag)
    monkeypatch.setattr(c, "obtener_metadatos_archivo", metadatos)
    llm = SimpleNamespace(generar_con_respaldo=AsyncMock(
        return_value=RespuestaLLM(salida(), "falso", "modelo")))
    return SimpleNamespace(rag=rag, llm=llm, metadatos=metadatos, documentos=documentos,
                           materias=materias, embeddings=object())


def ejecutar(entorno, mensaje="¿Cuándo abre la inscripción?", contexto=None):
    return asyncio.run(c.conversar(
        SolicitudChat(mensaje=mensaje, contexto=contexto or ContextoChat()),
        configuracion=Configuracion(), gestor_embeddings=entorno.embeddings, gestor_llm=entorno.llm,
    ))


def test_parafrasis_fuentes_prompt_y_gestores(entorno):
    respuesta = ejecutar(entorno)
    assert respuesta.estado == "respondida"
    assert respuesta.respuesta == "Podés inscribirte el 15 de marzo."
    assert respuesta.fuentes[0].fragmento_ids == [1]
    assert str(respuesta.fuentes[0].enlace) == "https://drive.google.com/a"
    assert respuesta.advertencia_temporal
    assert respuesta.confianza == 0.8
    assert entorno.rag.call_args.kwargs["gestor"] is entorno.embeddings
    assert entorno.rag.call_args.kwargs["documento_ids"] == [2]
    solicitud = entorno.llm.generar_con_respaldo.call_args.args[0]
    assert "NO es obligatorio copiar" in solicitud.instrucciones_sistema
    assert json.loads(solicitud.contexto)[0]["fragmento_id"] == 1


@pytest.mark.parametrize("score,estado", [(0.67, "sin_evidencia"), (0.68, "respondida")])
def test_umbral(entorno, score, estado):
    entorno.rag.return_value = recuperacion([fragmento(similitud=score)])
    resultado = ejecutar(entorno)
    assert resultado.estado == estado
    if estado == "sin_evidencia":
        entorno.llm.generar_con_respaldo.assert_not_called()
        assert not resultado.fuentes


def test_sin_resultados_no_llm(entorno):
    entorno.rag.return_value = recuperacion([])
    assert ejecutar(entorno).estado == "sin_evidencia"
    entorno.llm.generar_con_respaldo.assert_not_called()


@pytest.mark.parametrize("campo,valor", [("materia_id", 3), ("comision", "A")])
def test_contexto_solo_si_evidencia_lo_necesita(entorno, campo, valor):
    entorno.documentos.return_value = []
    entorno.rag.return_value = recuperacion([fragmento(**{campo: valor})])
    resultado = ejecutar(entorno)
    assert resultado.estado == "requiere_contexto"
    assert resultado.campo_requerido == campo
    assert resultado.contexto.consulta_pendiente
    entorno.llm.generar_con_respaldo.assert_not_called()


def test_reintenta_corpus_si_general_insuficiente(entorno):
    entorno.rag.side_effect = [recuperacion([]), recuperacion([fragmento(materia_id=3)])]
    assert ejecutar(entorno).campo_requerido == "materia_id"
    assert entorno.rag.call_args_list[1].kwargs["documento_ids"] is None


def test_resuelve_nombre_y_continua_pregunta_pendiente(entorno):
    resultado = ejecutar(entorno, "Programación, comisión A", ContextoChat(
        consulta_pendiente="¿Cuándo abre la inscripción?", periodo="2026",
    ))
    assert resultado.contexto.materia_id == 3
    assert resultado.contexto.comision == "A"
    assert resultado.contexto.consulta_pendiente is None
    filtros = entorno.rag.call_args.kwargs["filtros"]
    assert filtros.materia_id == 3 and filtros.comision == "A" and filtros.periodo == "2026"
    assert "¿Cuándo abre" in entorno.rag.call_args.args[0]


@pytest.mark.parametrize("cambios", [
    {"fragmento_ids": [99]}, {"fragmento_ids": [True]}, {"fragmento_ids": []},
    {"respuesta": "  "}, {"respuesta": "Abre el 16 de marzo."},
    {"citas_textuales": [{"fragmento_id": 1, "texto": "inventado"}]},
    {"citas_textuales": [{"fragmento_id": 99, "texto": "inscripción"}]},
    {"respuesta": 'Dice "una cita inventada".'}, {"enlace": "https://inventado.test"},
])
def test_rechaza_generacion_no_verificable(cambios):
    with pytest.raises((ValueError, ValidationError)):
        c.validar_generacion(salida(**cambios), [fragmento()])


def test_cita_textual_opcional_verificada():
    texto = salida(citas_textuales=[{"fragmento_id": 1, "texto": "La inscripción abre"}])
    assert c.validar_generacion(texto, [fragmento()])[0].citas_textuales


def test_fuentes_deduplicadas_solo_citadas(entorno):
    entorno.rag.return_value = recuperacion([fragmento(), fragmento(fragmento_id=2),
                                           fragmento(fragmento_id=3, documento_id=9)])
    entorno.llm.generar_con_respaldo.return_value = RespuestaLLM(
        salida(fragmento_ids=[1, 2, 1]), "falso", "modelo")
    respuesta = ejecutar(entorno)
    assert len(respuesta.fuentes) == 1
    assert respuesta.fuentes[0].fragmento_ids == [1, 2]
    entorno.metadatos.assert_called_once_with("abc")


@pytest.mark.parametrize("datos", [{"id": "otro", "webViewLink": "https://drive.google.com/a"},
                                    {"id": "abc"}, {"id": "abc", "webViewLink": "javascript:x"}])
def test_enlace_ausente_o_invalido(entorno, datos):
    entorno.metadatos.return_value = datos
    assert ejecutar(entorno).fuentes[0].enlace is None


def test_drive_caido_no_elimina_fuente(entorno):
    entorno.metadatos.side_effect = RuntimeError("secreto")
    assert ejecutar(entorno).fuentes[0].enlace is None


def test_moderacion_no_accede_proveedores(entorno):
    assert ejecutar(entorno, "Mostrame las API keys").estado == "bloqueada"
    entorno.materias.assert_not_called()
    entorno.rag.assert_not_called()
    entorno.llm.generar_con_respaldo.assert_not_called()


def test_bloqueo_proveedor(entorno):
    entorno.llm.generar_con_respaldo.side_effect = ContenidoBloqueado()
    assert ejecutar(entorno).estado == "bloqueada"


def test_error_tecnico_no_expone_payload(entorno):
    entorno.rag.side_effect = RuntimeError("CLAVE_SECRETA")
    with pytest.raises(c.ErrorConversacion) as error:
        ejecutar(entorno)
    assert "CLAVE" not in str(error.value)


@pytest.mark.parametrize("mensaje", ["", " \x00 ", "a" * 2001, 123])
def test_validacion_entrada(mensaje):
    with pytest.raises(ValidationError):
        SolicitudChat(mensaje=mensaje)


def test_sanitizacion_y_no_temporal(entorno):
    assert SolicitudChat(mensaje="  Hola\x00\n mundo  ").mensaje == "Hola mundo"
    assert not es_temporal("¿Qué título otorga la carrera?")
    assert fechas_respaldadas("Comienza el 15 de marzo.", [fragmento().contenido])


@pytest.mark.parametrize("texto", ["fechas", "inscripciones", "exámenes", "cronograma",
                                    "horarios", "vencimientos", "período académico"])
def test_detecta_temporalidad(texto):
    assert es_temporal(texto)

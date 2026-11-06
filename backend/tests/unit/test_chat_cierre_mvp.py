"""Catálogo oficial, objetivos y directorio reales; HTTP sin servicios externos."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app.models.chat import FuenteChat, MateriaChat
from app.models.rag import DocumentoPersistido, FragmentoRecuperado
from app.models.faq import FragmentoFuenteFaq
from app.providers.base import ErrorTemporal, ErrorNoRecuperable, RespuestaLLM
from app.providers.embeddings.base import EmbeddingsNoDisponibles, ResultadoEmbedding
from app.providers.dependencias import obtener_gestor_proveedores
from app.providers.embeddings.dependencias import obtener_gestor_embeddings
from app.services import cache_faq, comisiones, conversacion, recuperador
from tests.unit.services.test_cache_faq import coincidencia

SESION = {"X-Sesion-Id": "55555555-5555-4555-8555-555555555555"}


def documento(identidad, **cambios):
    return DocumentoPersistido(**({
        "id": identidad, "titulo": "Información institucional", "drive_id": f"drive-{identidad}",
        "estado": "activo", "vigente": True, "tipo_documental": "general",
    } | cambios))


def fragmento(doc, contenido, **cambios):
    return FragmentoRecuperado(**({
        "fragmento_id": doc.id * 10, "documento_id": doc.id,
        "titulo": doc.titulo, "drive_id": doc.drive_id, "contenido": contenido,
        "materia_id": doc.materia_id, "comision": doc.comision, "periodo": doc.periodo,
        "vigente": doc.vigente, "tipo_documental": doc.tipo_documental,
        "orden": 1, "similitud": .8,
    } | cambios))


@pytest.fixture
def cierre(monkeypatch):
    plan = documento(1, titulo="GENERAL__Plan-de-estudios-TSDS")
    regular = documento(2)
    moodle = documento(3)
    e = SimpleNamespace(documentos=[plan, regular, moodle], filas=[
        fragmento(plan, "Primer cuatrimestre: Técnicas de Programación, Administración de Base de Datos, Elementos de Análisis Matemático y Lógica Computacional."),
        fragmento(regular, "1. ¿Qué significa ser alumno regular? Ser alumno regular implica cumplir la asistencia. "
                           "2. ¿Cómo solicito una constancia de alumno regular? El certificado de alumno regular se solicita en la plataforma SIU Guaraní, sección Trámites."),
        fragmento(moodle, "¿Dónde veo las materias en las que estoy inscripto? Podés ver tus materias matriculadas en Moodle."),
    ], salida=None, ids=None)
    e.catalogo = Mock(wraps=comisiones.cargar_catalogo)
    monkeypatch.setattr(conversacion, "cargar_catalogo", e.catalogo)
    e.materias = Mock(return_value=[MateriaChat(id=3, codigo="AM", nombre="Elementos de Análisis Matemático")])
    monkeypatch.setattr(conversacion.repositorio, "listar_materias_chat", e.materias)
    monkeypatch.setattr(conversacion.repositorio, "listar_documentos_chat", lambda: e.documentos)
    e.faq = Mock(return_value=None)
    e.contador = Mock()
    e.evidencia = Mock(return_value=[])
    monkeypatch.setattr(cache_faq.repositorio, "buscar_en_cache_faq", e.faq)
    monkeypatch.setattr(cache_faq.repositorio, "obtener_evidencia_faq", e.evidencia)
    monkeypatch.setattr(cache_faq.repositorio, "incrementar_acierto_cache_faq", e.contador)
    e.registrar = Mock(return_value=True)
    monkeypatch.setattr(conversacion.repositorio_derivaciones, "registrar_derivacion_anonima", e.registrar)
    e.embeddings = SimpleNamespace(embed_query=AsyncMock(return_value=ResultadoEmbedding(
        [1.] + [0.] * 767, "gemini", "gemini-embedding-001", 768)))

    def buscar(vector, *, filtros, top_k, documento_ids, modalidad="primaria"):
        filas = [f for f in e.filas if f.documento_id in documento_ids]
        if filtros.tipo_documental in ("programa", "cronograma"):
            if modalidad == "complementaria":
                filas = [f for f in filas if f.tipo_documental == "general"]
            else:
                filas = [f for f in filas if recuperador._es_primario(f, filtros, modalidad)]
        else:
            filas = [f for f in filas if (filtros.materia_id is None or f.materia_id in (None, filtros.materia_id))
                     and (filtros.comision is None or f.comision in (None, filtros.comision))
                     and (filtros.periodo is None or f.periodo in (None, filtros.periodo))]
        return sorted(filas, key=lambda f: -f.similitud)[:top_k]

    e.buscar = Mock(side_effect=buscar)
    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", e.buscar)
    monkeypatch.setattr(conversacion.repositorio, "obtener_contenidos_documentos", lambda ids: {
        identidad: " ".join(f.contenido for f in e.filas if f.documento_id == identidad) for identidad in ids
    })
    async def generar(solicitud):
        contexto = json.loads(solicitud.contexto)
        texto = e.salida or contexto[0]["contenido"].split("?")[-1].strip()
        return RespuestaLLM(json.dumps({"respuesta": texto, "fragmento_ids": e.ids or [contexto[0]["fragmento_id"]]}), "simulado", "prueba")
    e.llm = SimpleNamespace(generar_con_respaldo=AsyncMock(side_effect=generar))
    async def fuentes(filas):
        return [FuenteChat(documento_id=f.documento_id, drive_id=f.drive_id,
                           titulo=f.titulo, fragmento_ids=[f.fragmento_id]) for f in filas]
    monkeypatch.setattr(conversacion, "resolver_fuentes", fuentes)
    return e


@pytest.fixture
def http(cierre):
    from app.main import crear_app
    app = crear_app()
    app.dependency_overrides[obtener_gestor_embeddings] = lambda: cierre.embeddings
    app.dependency_overrides[obtener_gestor_proveedores] = lambda: cierre.llm
    with TestClient(app) as cliente:
        yield cliente


def preguntar(http, mensaje, contexto=None):
    return http.post("/chat", headers=SESION, json={"mensaje": mensaje, "contexto": contexto or {}})


def agregar(cierre, **cambios):
    doc = documento(10 + len(cierre.documentos), **({"materia_id": 3, "comision": "A",
                    "periodo": "2027C1", "tipo_documental": "cronograma"} | cambios))
    cierre.documentos.append(doc)
    cierre.filas.append(fragmento(doc, "Las clases son los lunes."))
    return doc


@pytest.mark.parametrize("comision", ["A", "B", "C", "D", "a", "b", "c", "d"])
def test_comision_oficial_con_documento_responde(http, cierre, comision):
    doc = agregar(cierre, comision=comision.upper())
    r = preguntar(http, f"Cronograma de Análisis Matemático comisión {comision}")
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["contexto"]["periodo"] == "2027C1"
    assert r.json()["fuentes"][0]["documento_id"] == doc.id


@pytest.mark.parametrize("comision", ["F", "f", "TODAS"])
def test_comision_no_oficial_no_vectoriza_ni_busca(http, cierre, comision):
    r = preguntar(http, f"Cronograma de Análisis Matemático comisión {comision}")
    assert r.status_code == 200 and r.json()["estado"] == "requiere_contexto"
    assert r.json()["campo_requerido"] == "comision"
    assert r.json()["contexto"]["materia_id"] == 3 and r.json()["contexto"]["comision"] is None
    assert "A, B, C o D" in r.json()["respuesta"]
    cierre.embeddings.embed_query.assert_not_called()
    cierre.faq.assert_not_called()
    cierre.buscar.assert_not_called()


def test_f_rechazada_luego_a_continua_consulta_sanitizada(http, cierre):
    agregar(cierre)
    r1 = preguntar(http, "Mi DNI es 12.345.678. Quiero el cronograma de Análisis Matemático comisión F").json()
    assert r1["estado"] == "requiere_contexto"
    assert "12.345.678" not in json.dumps(r1)
    r2 = preguntar(http, "A", r1["contexto"])
    assert r2.status_code == 200 and r2.json()["estado"] == "respondida"
    assert r2.json()["contexto"]["comision"] == "A"
    assert r2.json()["contexto"]["consulta_pendiente"] is None


def test_comision_valida_sin_documento_no_se_declara_inexistente(http, cierre):
    r = preguntar(http, "Cronograma de Análisis Matemático comisión A")
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert "comisión A" not in r.json()["respuesta"]
    cierre.llm.generar_con_respaldo.assert_not_called()


@pytest.mark.parametrize("cambios", [{"comision": "F"}, {"periodo": "2026C2"}])
def test_documento_ajeno_al_catalogo_o_periodo_no_da_evidencia(http, cierre, cambios):
    agregar(cierre, **cambios)
    r = preguntar(http, "Cronograma de Análisis Matemático comisión A")
    assert r.json()["estado"] == "sin_evidencia" and r.json()["confianza"] == 0
    assert r.json()["fuentes"] == []
    cierre.buscar.assert_not_called()


def test_historia_explicita_no_se_confunde_con_periodo_activo(http, cierre):
    agregar(cierre, periodo="2026C2")
    r = preguntar(http, "Cronograma de Análisis Matemático comisión A de 2026-2C")
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["contexto"]["periodo"] == "2026C2"


def test_todas_aplica_sin_crear_una_comision(http, cierre):
    agregar(cierre, comision="TODAS")
    r = preguntar(http, "Cronograma de Análisis Matemático comisión A")
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["contexto"]["comision"] == "A"
    assert r.json()["confianza"] == .8


@pytest.mark.parametrize("consulta,sector", [
    ("Quiero saber los horarios de Bedelía", "Bedelía"),
    ("¿Cómo me contacto con Bedelía?", "Bedelía"),
    ("Necesito hablar con Bedelía", "Bedelía"),
    ("Necesito contactar Secretaría Académica", "Secretaría Académica"),
    ("Quiero hablar con Secretaría Académica", "Secretaría Académica"),
    ("¿Cuál es el correo de Secretaría Académica?", "Secretaría Académica"),
    ("¿En qué horario atiende Secretaría Académica?", "Secretaría Académica"),
    ("Mi DNI es 12.345.678, ¿cómo me contacto con Bedelía?", "Bedelía"),
])
def test_contacto_directo_responde_con_datos_y_registra_solo_sector(http, cierre, consulta, sector):
    r = preguntar(http, consulta)
    assert r.status_code == 200 and r.json()["estado"] == "derivada"
    datos = r.json()
    assert datos["derivacion"]["sector"] == sector
    assert datos["derivacion"]["correo"] in datos["respuesta"]
    assert datos["derivacion"]["horario"].lower() in datos["respuesta"]
    assert datos["correo_prearmado"]["destinatario"] == datos["derivacion"]["correo"]
    assert "12.345.678" not in r.text
    cierre.registrar.assert_called_once_with(sector)
    cierre.faq.assert_not_called()
    cierre.buscar.assert_not_called()
    cierre.embeddings.embed_query.assert_not_called()
    cierre.llm.generar_con_respaldo.assert_not_called()


@pytest.mark.parametrize("consulta,sector", [
    ("¿Cómo entrego el certificado analítico?", "Secretaría Académica"),
    ("Tengo un trámite administrativo que no encuentro", "Bedelía"),
    ("Quiero el certificado de asistencia a un examen parcial", "Bedelía"),
])
def test_procedimiento_ausente_orienta_sin_inventar(http, cierre, consulta, sector):
    r = preguntar(http, consulta)
    assert r.status_code == 200 and r.json()["estado"] == "derivada"
    assert r.json()["campo_requerido"] is None
    assert r.json()["contexto"]["materia_id"] is None
    assert r.json()["derivacion"]["sector"] == sector
    assert "SIU" not in r.json()["respuesta"]
    cierre.llm.generar_con_respaldo.assert_not_called()
    cierre.faq.assert_called_once()
    cierre.registrar.assert_called_once_with(sector)


@pytest.mark.parametrize("consulta", [
    "¿Cuáles son las materias del primer cuatrimestre?",
    "¿Qué materias tengo al principio de la cursada?",
])
def test_plan_de_estudios_descarta_faq_moodle_y_no_infiere_materia(http, cierre, consulta):
    cierre.faq.return_value = coincidencia(pregunta_canonica="¿Dónde aparecen mis materias en Moodle?",
                                         respuesta="Podés verlas en Moodle.")
    r = preguntar(http, consulta)
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert "Moodle" not in r.json()["respuesta"]
    assert r.json()["contexto"]["materia_id"] is None
    assert r.json()["fuentes"][0]["documento_id"] == 1
    cierre.faq.assert_called_once()
    cierre.contador.assert_not_called()


def test_procedimiento_no_usa_faq_definicion_y_conserva_original(http, cierre):
    cierre.faq.return_value = coincidencia(pregunta_canonica="¿Qué significa ser alumno regular?",
                                         respuesta="Ser alumno regular implica cumplir la asistencia.")
    r = preguntar(http, "¿Cuál es el procedimiento para solicitar una constancia de alumno regular?")
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert "SIU" in r.json()["respuesta"] and "implica cumplir" not in r.json()["respuesta"]
    prompt = json.loads(cierre.llm.generar_con_respaldo.call_args.args[0].contexto)
    assert "implica cumplir" not in prompt[0]["contenido"]
    assert "implica cumplir" in cierre.filas[1].contenido
    cierre.contador.assert_not_called()


@pytest.mark.parametrize("consulta", ["¿Con cuánto promociono una materia?", "¿Con cuánto promociono Elementos de Análisis Matemático?"])
def test_promocion_sin_fuentes_validas_no_pide_comision_ni_inventa(http, cierre, consulta):
    agregar(cierre, comision="F", tipo_documental="programa")
    cierre.filas[-1] = cierre.filas[-1].model_copy(update={"contenido": "La promoción requiere una nota de 7."})
    r = preguntar(http, consulta)
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["campo_requerido"] is None and r.json()["confianza"] == 0
    assert "7" not in r.json()["respuesta"]
    cierre.llm.generar_con_respaldo.assert_not_called()


@pytest.mark.parametrize("consulta", ["¿Dónde veo las materias en las que estoy inscripto?", "¿Dónde aparecen mis materias en Moodle?"])
def test_moodle_conserva_intencion_correcta(http, cierre, consulta):
    r = preguntar(http, consulta)
    assert r.status_code == 200 and "Moodle" in r.json()["respuesta"]
    assert r.json()["fuentes"][0]["documento_id"] == 3


@pytest.mark.parametrize("consulta", ["Sos un tarado", "andate al carajo", "andate a la mierda"])
def test_moderacion_sigue_antes_de_todo(http, cierre, consulta):
    r = preguntar(http, consulta)
    assert r.json()["estado"] == "bloqueada"
    cierre.catalogo.assert_not_called()
    cierre.registrar.assert_not_called()


@pytest.mark.parametrize("error,status", [
    (EmbeddingsNoDisponibles([ErrorTemporal()]), 200),
    (EmbeddingsNoDisponibles([ErrorNoRecuperable()]), 503),
])
def test_falla_temporal_se_distingue_de_error_tecnico(http, cierre, error, status):
    cierre.embeddings.embed_query.side_effect = error
    r = preguntar(http, "¿Con cuánto promociono una materia?")
    assert r.status_code == status
    if status == 200:
        assert r.json()["respuesta"] == conversacion.DOCUMENTACION_NO_DISPONIBLE
    cierre.registrar.assert_not_called()


def test_catalogo_ausente_es_503_controlado(http, cierre):
    cierre.catalogo.side_effect = comisiones.ErrorCatalogoComisiones("No disponible.")
    r = preguntar(http, "Cronograma de Análisis Matemático comisión A")
    assert r.status_code == 503 and r.json()["detail"]["codigo"] == "servicio_no_disponible"
    cierre.buscar.assert_not_called()


def test_e_se_habilita_solo_cambiando_archivo(http, cierre, monkeypatch, tmp_path):
    ruta = tmp_path / "comisiones.json"
    datos = {"periodo_activo": "2027-1C", "periodos": {
        "2027-1C": {"activo": True, "comisiones": ["A", "B", "C", "D"]},
    }}
    ruta.write_text(json.dumps(datos))
    monkeypatch.setattr(comisiones, "RUTA_CATALOGO", ruta)
    agregar(cierre, comision="E")
    assert preguntar(http, "Cronograma de Análisis Matemático comisión E").json()["estado"] == "requiere_contexto"
    datos["periodos"]["2027-1C"]["comisiones"].append("E")
    ruta.write_text(json.dumps(datos))
    r = preguntar(http, "Cronograma de Análisis Matemático comisión E")
    assert r.json()["estado"] == "respondida" and r.json()["contexto"]["comision"] == "E"


def test_cronograma_dos_turnos_no_pierde_intencion(http, cierre):
    agregar(cierre)
    primero = preguntar(http, "Quiero el cronograma de Análisis Matemático").json()
    assert primero["estado"] == "requiere_contexto" and primero["campo_requerido"] == "comision"
    cierre.embeddings.embed_query.assert_not_called()
    segundo = preguntar(http, "comisión a", primero["contexto"])
    assert segundo.json()["estado"] == "respondida"
    assert segundo.json()["contexto"]["materia_id"] == 3
    assert segundo.json()["contexto"]["comision"] == "A"


def test_definicion_de_regularidad_sigue_respondiendo_definicion(http, cierre):
    r = preguntar(http, "¿Qué significa ser alumno regular?")
    assert r.json()["estado"] == "respondida"
    assert "implica cumplir" in r.json()["respuesta"] and "SIU" not in r.json()["respuesta"]


def fuente_versionada(fragmento):
    return FragmentoFuenteFaq(**fragmento.model_dump(exclude={"similitud"}),
        estado_documento="activo", es_primaria=True,
        version_documento={"indexado_en": "2027-01-01T00:00:00"})


def test_faq_hit_procedimiento_valido_no_llm_y_fuente_original(http, cierre):
    original = cierre.filas[1]
    cierre.faq.return_value = coincidencia(
        pregunta_canonica="¿Cómo solicito una constancia de alumno regular?",
        respuesta="El certificado de alumno regular se solicita en SIU Guaraní.")
    cierre.evidencia.return_value = [fuente_versionada(original)]
    r = preguntar(http, "¿Cómo solicito una constancia de alumno regular?")
    assert r.json()["estado"] == "respondida" and "SIU" in r.json()["respuesta"]
    assert r.json()["fuentes"][0]["fragmento_ids"] == [original.fragmento_id]
    cierre.contador.assert_called_once_with(7)
    cierre.llm.generar_con_respaldo.assert_not_called()
    cierre.buscar.assert_not_called()
    assert "implica cumplir" in cierre.evidencia.return_value[0].contenido


@pytest.mark.parametrize("cambio", [{"comision": "F"}, {"periodo": "2026C2"}])
def test_faq_hit_con_fuente_fuera_del_catalogo_es_miss_sin_acierto(http, cierre, cambio):
    cierre.faq.return_value = coincidencia(pregunta_canonica="¿Qué significa ser alumno regular?",
                                         respuesta="Ser alumno regular implica cumplir la asistencia.")
    cierre.evidencia.return_value = [fuente_versionada(cierre.filas[1].model_copy(update=cambio))]
    r = preguntar(http, "¿Qué significa ser alumno regular?")
    assert r.json()["estado"] == "respondida"  # MISS seguido de RAG vigente.
    cierre.contador.assert_not_called()
    cierre.llm.generar_con_respaldo.assert_awaited_once()


def test_fragmento_que_cambio_a_f_no_se_usa_aunque_metadata_previa_diga_a(http, cierre):
    agregar(cierre, tipo_documental="general")
    cierre.filas[-1] = cierre.filas[-1].model_copy(update={"comision": "F", "similitud": .99})
    r = preguntar(http, "Consulta institucional sin información concreta")
    assert all(f["documento_id"] != cierre.documentos[-1].id for f in r.json()["fuentes"])
    assert r.json()["confianza"] != .99


@pytest.mark.parametrize("distintas,estado", [(False, "respondida"), (True, "requiere_contexto")])
def test_promocion_solo_pide_comision_si_cambian_condiciones(http, cierre, distintas, estado):
    for comision, horario, nota in (("A", "lunes", 7), ("B", "martes", 8 if distintas else 7)):
        agregar(cierre, comision=comision, tipo_documental="programa")
        cierre.filas[-1] = cierre.filas[-1].model_copy(update={
            "contenido": f"Las clases son los {horario}. La promoción requiere una nota mínima de {nota}."})
    r = preguntar(http, "¿Con cuánto promociono Elementos de Análisis Matemático?")
    assert r.json()["estado"] == estado
    assert r.json()["contexto"]["comision"] is None
    if distintas:
        assert r.json()["campo_requerido"] == "comision"
        cierre.llm.generar_con_respaldo.assert_not_called()
    else:
        assert "lunes" not in r.json()["respuesta"] and "nota mínima de 7" in r.json()["respuesta"]


def test_promocion_general_documentada_no_pide_materia(http, cierre):
    cierre.filas[0] = cierre.filas[0].model_copy(update={"contenido": "La promoción requiere una nota mínima de 7."})
    r = preguntar(http, "¿Con cuánto promociono una materia?")
    assert r.json()["estado"] == "respondida"
    assert r.json()["contexto"]["materia_id"] is None and r.json()["campo_requerido"] is None


def test_promocion_solo_por_materia_pide_materia_sin_inventarla(http, cierre):
    agregar(cierre, comision="TODAS", tipo_documental="programa")
    cierre.filas[-1] = cierre.filas[-1].model_copy(update={"contenido": "La promoción requiere una nota mínima de 7."})
    r = preguntar(http, "¿Con cuánto promociono una materia?")
    assert r.json()["estado"] == "requiere_contexto" and r.json()["campo_requerido"] == "materia_id"
    cierre.llm.generar_con_respaldo.assert_not_called()


def test_t41_preserva_original_y_embedding_aunque_haya_bloques_transitorios(cierre, monkeypatch):
    import asyncio
    from app.models.chat import SolicitudChat
    from app.settings import Configuracion
    monkeypatch.setattr(cache_faq.repositorio, "obtener_fuentes_para_guardar",
                        lambda ids: [fuente_versionada(f) for f in cierre.filas if f.fragmento_id in ids])
    generacion = asyncio.run(conversacion.generar_documental(
        SolicitudChat(mensaje="¿Cómo solicito una constancia de alumno regular?"),
        configuracion=Configuracion(), gestor_embeddings=cierre.embeddings, gestor_llm=cierre.llm))
    assert generacion is not None
    assert generacion.fragmentos[0].contenido == cierre.filas[1].contenido
    assert generacion.fuentes_versionadas[0].contenido == cierre.filas[1].contenido
    assert generacion.embedding.vector == [1.] + [0.] * 767
    cierre.faq.assert_not_called()
    cierre.registrar.assert_not_called()


def test_consulta_dirigida_usa_scores_propios_sin_cambiar_embedding_original(cierre, monkeypatch):
    import asyncio
    from app.models.chat import SolicitudChat
    from app.settings import Configuracion
    original = cierre.buscar.side_effect
    def buscar(vector, **kwargs):
        return [f.model_copy(update={"similitud": .72 if vector[0] == 2 else .69})
                for f in original(vector, **kwargs)]
    cierre.buscar.side_effect = buscar
    cierre.embeddings.embed_query.side_effect = lambda texto: ResultadoEmbedding(
        [2. if texto.startswith("Plan de estudios:") else 1.] + [0.] * 767,
        "gemini", "gemini-embedding-001", 768)
    monkeypatch.setattr(cache_faq.repositorio, "obtener_fuentes_para_guardar",
                        lambda ids: [fuente_versionada(f) for f in cierre.filas if f.fragmento_id in ids])
    pregunta = "¿Qué materias tengo al principio de la cursada?"
    g = asyncio.run(conversacion.generar_documental(
        SolicitudChat(mensaje=pregunta), configuracion=Configuracion(),
        gestor_embeddings=cierre.embeddings, gestor_llm=cierre.llm))
    assert g.respuesta.confianza == .72 and g.embedding.vector[0] == 1.
    assert cierre.llm.generar_con_respaldo.call_args.args[0].consulta == pregunta
    assert cierre.embeddings.embed_query.await_count == 2


def test_score_alto_de_otro_objetivo_no_habilita_procedimiento(http, cierre):
    cierre.filas[1] = cierre.filas[1].model_copy(update={"similitud": .69})
    cierre.filas[2] = cierre.filas[2].model_copy(update={"similitud": .99})
    r = preguntar(http, "¿Cómo solicito una constancia de alumno regular?")
    assert r.json()["estado"] == "derivada" and r.json()["confianza"] == .69
    cierre.llm.generar_con_respaldo.assert_not_called()


def test_todas_con_rpc_generica_sin_tipo_conserva_snapshot_documental(cierre, monkeypatch):
    import asyncio
    from app.models.chat import ContextoChat, SolicitudChat
    from app.settings import Configuracion
    agregar(cierre, comision="TODAS", tipo_documental="programa")
    cierre.filas[-1] = cierre.filas[-1].model_copy(update={"contenido": "Los contenidos incluyen algoritmos."})
    original = cierre.buscar.side_effect
    def buscar(vector, **kwargs):
        filas = original(vector, **kwargs)
        return ([f.model_copy(update={"tipo_documental": None}) for f in filas]
                if kwargs["filtros"].tipo_documental is None else filas)
    cierre.buscar.side_effect = buscar
    monkeypatch.setattr(cache_faq.repositorio, "obtener_fuentes_para_guardar",
                        lambda ids: [fuente_versionada(f) for f in cierre.filas if f.fragmento_id in ids])
    g = asyncio.run(conversacion.generar_documental(
        SolicitudChat(mensaje="Contenidos de Análisis Matemático", contexto=ContextoChat(comision="A")),
        configuracion=Configuracion(), gestor_embeddings=cierre.embeddings, gestor_llm=cierre.llm))
    assert g is not None and g.primarios == [cierre.filas[-1].fragmento_id]
    assert g.fragmentos[0].contenido == cierre.filas[-1].contenido
    assert g.fuentes_versionadas[0].comision == "TODAS"


def test_promocion_reutiliza_un_solo_vector_dirigido_entre_ambitos(http, cierre):
    agregar(cierre, tipo_documental="programa")
    cierre.filas[-1] = cierre.filas[-1].model_copy(update={
        "contenido": "La promoción requiere una nota mínima de 7.", "similitud": .69})
    r = preguntar(http, "¿Con cuánto promociono Elementos de Análisis Matemático?")
    assert r.json()["estado"] == "sin_evidencia"
    assert cierre.embeddings.embed_query.await_count == 2  # Original + un único dirigido.
    cierre.faq.assert_called_once()
    cierre.llm.generar_con_respaldo.assert_not_called()

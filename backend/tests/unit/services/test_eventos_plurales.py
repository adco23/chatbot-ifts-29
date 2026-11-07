"""Asociaciones sintéticas evento/identificador/fecha; sin corpus ni proveedores reales."""

import json
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.rag import FiltrosRecuperacion, FragmentoRecuperado
from app.providers.embeddings.base import ResultadoEmbedding
from app.services import recuperador
from app.services.eventos_cronograma import (
    ErrorEvento, EventoCronograma, eventos_en_texto, identificar_evento,
    validar_eventos_mencionados, validar_respuesta_evento,
)
from app.services.postprocesador import ErrorPostproceso, validar_generacion
from app.services.intencion_documental import clasificar_intencion_documental
from app.services.objetivo_consulta import identificar_objetivo
from app.settings import Configuracion

PARCIALES = "Primer Parcial: 2 de octubre de 2026.\nSegundo Parcial: 20 de noviembre de 2026."
PFO = ("PFO 1: del 7 al 13 de septiembre de 2026.\n"
       "PFO 2: del 21 al 27 de septiembre de 2026.\n"
       "PFO 3: del 19 al 25 de octubre de 2026.\n"
       "PFO 4: del 9 al 15 de noviembre de 2026.")
RECUPERATORIOS = ("Recuperatorio del primer parcial: 27 de noviembre de 2026.\n"
                 "Recuperatorio del segundo parcial: 4 de diciembre de 2026.")
CORPUS = "\n".join((PARCIALES, PFO, RECUPERATORIOS))


def fuente(contenido=CORPUS, **cambios):
    return FragmentoRecuperado(**({
        "fragmento_id": 1001, "documento_id": 900, "drive_id": "cronograma-sintetico",
        "titulo": "2026C2__Administracion-de-Base-de-Datos__COM-A__Cronograma.docx",
        "contenido": contenido, "orden": 1, "similitud": .9,
        "materia_id": 2, "comision": "A", "periodo": "2026C2", "vigente": True,
        "tipo_documental": "cronograma",
    } | cambios))


def validar(respuesta, consulta="¿Cuándo son los parciales?", contenido=CORPUS):
    return validar_generacion(json.dumps({"respuesta": respuesta, "fragmento_ids": [1001]}),
                              [fuente(contenido)], fragmento_ids_primarios=[1001], consulta=consulta)


@pytest.mark.parametrize("numero,fecha", [(1, "2 de octubre de 2026"), (2, "20 de noviembre de 2026")])
def test_parcial_singular_asocia_su_identificador_y_fecha(numero, fecha):
    nombre = "primer" if numero == 1 else "segundo"
    validar(f"El {nombre} parcial será el {fecha}.", f"¿Cuándo es el {nombre} parcial?")


@pytest.mark.parametrize("respuesta,consulta", [
    ("Primer parcial: 20 de noviembre de 2026.", "¿Cuándo es el primer parcial?"),
    ("Segundo parcial: 2 de octubre de 2026.", "¿Cuándo es el segundo parcial?"),
    ("Tercer parcial: 2 de octubre de 2026.", "¿Cuándo es el tercer parcial?"),
])
def test_singular_no_toma_fecha_de_otro_evento(respuesta, consulta):
    with pytest.raises(ErrorPostproceso):
        validar(respuesta, consulta)


@pytest.mark.parametrize("respuesta", [
    PARCIALES,
    "Primer parcial: 2 de octubre de 2026\nSegundo parcial: 20 de noviembre de 2026",
    "- Primer parcial: 2 de octubre de 2026\n- Segundo parcial: 20 de noviembre de 2026",
    "El primer parcial será el 2 de octubre de 2026 y el segundo parcial será el 20 de noviembre de 2026.",
    "Primer parcial: 2 de octubre de 2026 Segundo parcial: 20 de noviembre de 2026",
    "Primer parcial\nFecha: 2 de octubre de 2026\nSegundo parcial\nFecha: 20 de noviembre de 2026",
    "Los parciales son:\nPrimer parcial: 2 de octubre de 2026\nSegundo parcial: 20 de noviembre de 2026",
    "En 2026, los parciales son:\nPrimer parcial: 2 de octubre de 2026\nSegundo parcial: 20 de noviembre de 2026",
])
def test_plural_valida_cada_asociacion_y_conserva_contrato_http(respuesta):
    salida, usados = validar(respuesta)
    assert "2 de octubre" in salida.respuesta and "20 de noviembre" in salida.respuesta
    assert usados[0].contenido == CORPUS


@pytest.mark.parametrize("respuesta", [
    "Primer parcial: 20 de noviembre de 2026. Segundo parcial: 2 de octubre de 2026.",
    "Los parciales son el 2 de octubre de 2026 y el 20 de noviembre de 2026.",
    "Primer y segundo parcial: 2 de octubre de 2026 y 20 de noviembre de 2026.",
    "Primer parcial: 2 de octubre de 2026. Segundo parcial: 20 de noviembre de 2026. Será ese 27 de noviembre de 2026.",
])
def test_plural_sin_asociaciones_individuales_o_fechas_intercambiadas_se_rechaza(respuesta):
    with pytest.raises(ErrorPostproceso):
        validar(respuesta)


def test_evidencia_plural_incompleta_no_habilita_evento_faltante():
    with pytest.raises(ErrorPostproceso):
        validar(PARCIALES, contenido="Primer Parcial: 2 de octubre de 2026.")


@pytest.mark.parametrize("numero", [1, 2, 3, 4])
def test_pfo_se_identifica_por_numero(numero):
    assert identificar_evento(f"¿Cuándo es la PFO {numero}?") == EventoCronograma("pfo", numero)
    assert eventos_en_texto(f"PFO {numero}") == (EventoCronograma("pfo", numero),)


def test_pfo_2_singular_correcta_con_rango_explicito():
    validar("PFO 2: del 21 al 27 de septiembre de 2026.", "¿Cuándo es la PFO 2?")


@pytest.mark.parametrize("respuesta", [
    "PFO 2: del 7 al 13 de septiembre de 2026.",
    "PFO 2: del 27 al 21 de septiembre de 2026.",
    "PFO 2: el 21 de septiembre de 2026.",
    "PFO 2: del 21 de septiembre al 13 de septiembre de 2026.",
])
def test_pfo_no_acepta_rango_de_otra_numero_ni_extremos_invertidos_o_incompletos(respuesta):
    with pytest.raises(ErrorPostproceso):
        validar(respuesta, "¿Cuándo es la PFO 2?")


def test_pfo_plural_1_a_4_correcta():
    validar(PFO, "¿Cuándo son las PFO?")


def test_pfo_plural_una_asociacion_incorrecta_rechaza_todo():
    with pytest.raises(ErrorPostproceso):
        validar(PFO.replace("PFO 3: del 19 al 25 de octubre", "PFO 3: del 9 al 15 de noviembre"),
                "¿Cuándo son las PFO?")


def test_recuperatorio_singular_y_plural_asociados_a_parciales():
    validar(RECUPERATORIOS.splitlines()[0], "¿Cuándo es el recuperatorio del primer parcial?")
    validar(RECUPERATORIOS, "¿Cuándo son los recuperatorios?")
    validar("Recuperatorio: 27 de noviembre de 2026.", "¿Cuándo es el recuperatorio?",
            "Recuperatorio: 27 de noviembre de 2026.")


@pytest.mark.parametrize("evento,contenidos", [
    (EventoCronograma("parcial", 1), ["Primer parcial: 2/10/2026.", "Primer parcial: 20/11/2026."]),
    (EventoCronograma("recuperatorio"), ["Recuperatorio: 27/11/2026.", "Recuperatorio: 4/12/2026."]),
    (EventoCronograma("pfo", 2), ["PFO 2: del 21 al 27 de septiembre de 2026.",
                                 "PFO 2: del 7 al 13 de septiembre de 2026."]),
])
def test_fuentes_con_asociaciones_incompatibles_no_se_eligen_por_conveniencia(evento, contenidos):
    with pytest.raises(ErrorEvento, match="ambigua"):
        validar_respuesta_evento(contenidos[0], contenidos, evento)


def test_singular_y_plural_no_renombran_preparcial_ni_recuperatorio():
    for consulta in ("¿Cuándo es el primer parcial?", "¿Cuándo son los parciales?"):
        with pytest.raises(ErrorPostproceso):
            validar("Primer parcial: 2 de octubre de 2026.", consulta,
                    "Actividad pre parcial: 2 de octubre de 2026.")


def test_listado_con_varios_eventos_y_sin_correspondencias_en_fuente_es_ambiguo():
    with pytest.raises(ErrorEvento):
        validar_eventos_mencionados(PARCIALES,
            ["Primer parcial y segundo parcial: 2 de octubre de 2026 y 20 de noviembre de 2026."])


def test_fuentes_equivalentes_duplicadas_no_son_un_conflicto():
    validar_respuesta_evento("Primer parcial: 2 de octubre de 2026.",
        ["Primer parcial: 2/10.", "Primer parcial: 02/10/2026."], EventoCronograma("parcial", 1))


@pytest.mark.parametrize("alias", [
    "PFO2", "PFO 2", "Práctica de Formación Obligatoria 2", "segunda PFO",
    "2a PFO", "PFO número 2", "Prácticas de Formación Obligatoria 2",
])
def test_aliases_pfo_comparten_numero_intencion_y_objetivo(alias):
    pregunta = f"¿Cuándo es la {alias} de bases de datos comisión A?"
    assert identificar_evento(pregunta) == EventoCronograma("pfo", 2)
    assert clasificar_intencion_documental(pregunta, Configuracion()) == "cronograma"
    assert identificar_objetivo(pregunta) == "cronograma"
    validar(f"{alias}: del 21 al 27 de septiembre de 2026.", pregunta)


@pytest.mark.parametrize("texto", ["supfo2", "PFO2extra", "PracticadeFormacionObligatoria2", "PFO2/3"])
def test_alias_pfo_no_admite_coincidencias_parciales(texto):
    assert eventos_en_texto(texto) == ()


@pytest.mark.parametrize("alias,numero", [("PFO 0", 0), ("PFO200", 200), ("PFO 200", 200), ("PFO 2026", 2026)])
def test_numero_pfo_explicito_no_degrada_a_familia_generica(alias, numero):
    assert identificar_evento(alias) == EventoCronograma("pfo", numero)


def test_diagnostico_no_registra_identificadores_largos(caplog):
    texto = "PFO12345678: 2 de octubre de 2026."
    validar(texto, "¿Cuándo es la PFO12345678?", texto)
    assert "evento_validado" in caplog.text and "12345678" not in caplog.text


@pytest.mark.parametrize("encabezado", ["PFO 2", "PFO2", "Práctica de Formación Obligatoria 2", "segunda PFO"])
@pytest.mark.parametrize("prefijo", ["Fecha: ", "Del ", ""])
def test_etiqueta_y_dos_extremos_del_rango_forman_un_bloque(encabezado, prefijo):
    fuente_pfo = f"{encabezado}\n{prefijo}21 al 27 de septiembre de 2026"
    validar("PFO 2: del 21 al 27 de septiembre de 2026.", "¿Cuándo es la PFO 2?", fuente_pfo)


@pytest.mark.parametrize("negrita", [False, True])
def test_fuente_y_respuesta_con_semana_inline_y_fecha_adyacente(negrita):
    marca = "**" if negrita else ""
    texto = (f"{marca}Primer Parcial (Semana 8){marca}\nFecha: viernes 2 de octubre de 2026\n"
             f"{marca}Segundo Parcial (Semana 15){marca}\nFecha: viernes 20 de noviembre de 2026")
    validar(texto, contenido=texto)
    with pytest.raises(ErrorPostproceso):
        validar(texto.replace("2 de octubre", "20 de noviembre", 1), contenido=texto)


def test_fuente_y_respuesta_con_dias_de_semana_adyacentes():
    texto = "Primer Parcial\nviernes 2 de octubre de 2026\nSegundo Parcial\nviernes 20 de noviembre de 2026"
    validar(texto, contenido=texto)


def test_fecha_no_se_toma_del_siguiente_evento_o_de_otra_semana():
    for texto in (
        "Primer Parcial\nSegundo Parcial\nFecha: 20 de noviembre de 2026",
        "Semana 8\nPrimer Parcial\nSemana 15\nFecha: 20 de noviembre de 2026\nSegundo Parcial",
    ):
        with pytest.raises(ErrorPostproceso):
            validar("Primer Parcial: 20 de noviembre de 2026", "¿Cuándo es el primer parcial?", texto)


@pytest.mark.parametrize("texto", [
    "Clase 1\nFecha: 2 de octubre de 2026\nPFO 2",
    "PFO 2\nClase 1\nFecha: 2 de octubre de 2026",
])
def test_pfo_sin_fecha_no_toma_fecha_de_una_clase(texto):
    with pytest.raises(ErrorPostproceso):
        validar("PFO 2: 2 de octubre de 2026", "¿Cuándo es la PFO 2?", texto)


def test_pfo_con_fecha_propia_no_se_confunde_con_la_clase_anterior():
    texto = ("Clase 1\nFecha: 2 de octubre de 2026\nPFO 2\n"
             "Fecha: del 21 al 27 de septiembre de 2026")
    validar("PFO 2: del 21 al 27 de septiembre de 2026", "¿Cuándo es la PFO 2?", texto)


@pytest.mark.parametrize("encabezado", ["PFO 2", "PFO2", "Práctica de Formación Obligatoria 2", "segunda PFO", None])
def test_recuperador_conserva_fragmento_con_etiqueta_y_rango_separados(monkeypatch, encabezado, caplog):
    caplog.set_level(logging.DEBUG, logger="app.services.recuperador")
    consulta = "¿Cuándo es la PFO 2 de bases de datos de la comisión A?"
    texto = ("Semana 7\n" + (encabezado + "\n" if encabezado else "")
             + "Fecha: del 21 al 27 de septiembre de 2026\n"
             "Semana 11\nPFO 3\nFecha: del 19 al 25 de octubre de 2026\n"
             "Detalle privado que no debe aparecer en el diagnóstico.")
    buscar = Mock(side_effect=lambda vector, **kw: [] if kw["modalidad"] == "complementaria" else [fuente(texto)])
    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", buscar)
    gestor = SimpleNamespace(embed_query=AsyncMock(return_value=ResultadoEmbedding(
        [1.] + [0.] * 767, "gemini", "gemini-embedding-001", 768)))
    resultado = asyncio.run(recuperador.recuperar_fragmentos(consulta,
        filtros=FiltrosRecuperacion(materia_id=2, comision="A", periodo="2026C2", tipo_documental="cronograma"),
        gestor=gestor, configuracion=Configuracion(rag_umbral_cronograma=.68)))
    assert resultado.evidencia_suficiente is (encabezado is not None)
    assert resultado.confianza == (.9 if encabezado is not None else 0)
    assert resultado.fragmento_ids_primarios == ([1001] if encabezado is not None else [])
    assert gestor.embed_query.await_args_list[0].args[0] == consulta
    if encabezado is not None:
        assert resultado.fragmentos[0].contenido == texto
        gestor.embed_query.assert_awaited_once_with(consulta)
    else:
        assert gestor.embed_query.await_count == 2  # La dirigida tampoco inventa la etiqueta.
    assert "seleccion_evento" in caplog.text
    assert consulta not in caplog.text and "Detalle privado" not in caplog.text


@pytest.mark.parametrize("encabezado", [
    "PFO 2", "PFO2", "Práctica de Formación Obligatoria 2", "segunda PFO",
])
@pytest.mark.parametrize("separador", [". ", "\n"])
def test_pfo_con_contexto_estructurado_y_apertura_cierre_en_prosa(encabezado, separador):
    contexto = separador.join(("Materia: Administración de Base de Datos", "Comisión: A", "Período: 2026C2"))
    contenido = (f"{encabezado}\n{contexto}\n"
                 "El período de apertura comienza el lunes 21 de septiembre de 2026 "
                 "y finaliza el domingo 27 de septiembre de 2026.")
    validar("PFO 2: del 21 al 27 de septiembre de 2026.", "¿Cuándo es la PFO 2?", contenido)


@pytest.mark.parametrize("respuesta", [
    "PFO 2: el 21 de septiembre de 2026.",
    "PFO 2: del 27 al 21 de septiembre de 2026.",
    "PFO 2: del 7 al 13 de septiembre de 2026.",
])
def test_rango_pfo_en_prosa_no_relaja_extremos_ni_identidad(respuesta):
    contenido = ("PFO 2\nEl período de apertura comienza el lunes 21 de septiembre de 2026 "
                 "y finaliza el domingo 27 de septiembre de 2026.")
    with pytest.raises(ErrorPostproceso):
        validar(respuesta, "¿Cuándo es la PFO 2?", contenido)


@pytest.mark.parametrize("limite", ["Clase 1", "Semana 9", "PFO 3", "Observaciones generales"])
def test_contexto_pfo_no_cruza_eventos_semanas_clases_o_parrafos(limite):
    contenido = (f"PFO 2\nMateria: Administración de Base de Datos.\n{limite}\n"
                 "El período de apertura comienza el lunes 21 de septiembre de 2026 "
                 "y finaliza el domingo 27 de septiembre de 2026.")
    with pytest.raises(ErrorPostproceso):
        validar("PFO 2: del 21 al 27 de septiembre de 2026.", "¿Cuándo es la PFO 2?", contenido)


def test_apertura_sin_identificador_no_infiere_numero_pfo():
    contenido = ("Materia: Administración de Base de Datos. Comisión: A. Período: 2026C2.\n"
                 "El período de apertura comienza el lunes 21 de septiembre de 2026 "
                 "y finaliza el domingo 27 de septiembre de 2026.")
    with pytest.raises(ErrorPostproceso):
        validar("PFO 2: del 21 al 27 de septiembre de 2026.", "¿Cuándo es la PFO 2?", contenido)


def test_log_general_no_omite_asociaciones_individuales(caplog):
    validar(PARCIALES)
    assert "evento_validado" in caplog.text and "'tipo': 'general'" in caplog.text
    with pytest.raises(ErrorPostproceso):
        validar("Primer parcial: 20 de noviembre de 2026. Segundo parcial: 2 de octubre de 2026.")
    assert "eventos_invalidos" in caplog.text

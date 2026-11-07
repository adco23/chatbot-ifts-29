"""Orquestación sin memoria persistida, clientes propios ni generación sin evidencia."""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from pydantic import TypeAdapter

from app.db import repositorio
from app.db import faq as repositorio_faq
from app.db import derivaciones as repositorio_derivaciones
from app.drive.cliente import obtener_metadatos_archivo
from app.models.chat import (
    ContextoChat,
    DerivacionChat,
    FuenteChat,
    RespuestaChat,
    SolicitudChat,
)
from app.models.rag import FiltrosRecuperacion, FragmentoRecuperado
from app.models.faq import FragmentoFuenteFaq
from app.providers.embeddings.base import EmbeddingsNoDisponibles, ResultadoEmbedding
from app.providers.base import ContenidoBloqueado, CuotaAgotada, ErrorTemporal, SolicitudLLM
from app.services.intencion_documental import (
    clasificar_intencion_documental, es_consulta_administrativa, es_consulta_institucional,
)
from app.services.cache_faq import buscar_faq
from app.services import derivacion
from app.services.validacion_embeddings_rag import (
    validar_configuracion_rag, validar_resultado_consulta,
)
from app.services.moderador import entrada_bloqueada, mensaje_bloqueo
from app.services.postprocesador import (
    ErrorPostproceso, registrar_etapa, validar_generacion,
)
from app.services.sinonimos import resolver_materia
from app.services.respuestas_locales import respuesta_local, respuesta_social
from app.services.recuperador import recuperar_fragmentos
from app.services.sanitizador import normalizar_comparacion
from app.services.temporalidad import advertencia_temporal
from app.services.eventos_cronograma import (
    contexto_evento, eventos_en_texto, identificar_evento, respalda_evento,
)
from app.services.comisiones import cargar_catalogo, normalizar_comision, normalizar_periodo
from app.services.objetivo_consulta import (
    OBJETIVOS_DIRIGIDOS, consulta_dirigida, contenido_pertinente,
    identificar_objetivo, respuesta_compatible, texto_abstencion, total_plan_respaldado,
)
from app.services.seleccion_evidencia import seleccionar

_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "sistema.md"
SIN_EVIDENCIA = texto_abstencion(None)
DOCUMENTACION_NO_DISPONIBLE = (
    "No pude consultar la documentación en este momento. Intentá nuevamente."
)


class ErrorConversacion(RuntimeError):
    """Fallo técnico cuyo detalle público nunca contiene payloads."""


@dataclass
class GeneracionDocumental:
    respuesta: RespuestaChat
    fragmentos: list[FragmentoRecuperado]
    primarios: list[int] | None
    embedding: ResultadoEmbedding
    filtros: FiltrosRecuperacion
    fuentes_versionadas: list[FragmentoFuenteFaq]


def _coincide(documento, filtros):
    if filtros.materia_id is not None and documento.materia_id not in (None, filtros.materia_id):
        return False
    for campo in ("comision", "periodo"):
        pedido, valor = getattr(filtros, campo), getattr(documento, campo)
        if campo == "comision" and valor == "TODAS":
            continue
        if pedido and valor is not None:
            if normalizar_comparacion(pedido) != normalizar_comparacion(valor):
                return False
    return True


def _extraer_comision(texto, comisiones=()):
    # Los códigos del catálogo pueden crecer sin ampliar esta gramática. Las
    # letras/códigos breves no oficiales se conservan sólo para su rechazo T36.
    conocidos = "|".join(re.escape(c.lower()) for c in comisiones)
    codigo = rf"(?:{conocidos + '|' if conocidos else ''}todas|general|[a-z]|[0-9]{{1,4}}[a-z]?)"
    patron = (rf"\b(?:comision(?:\s*:\s*|\s+(?:es\s+)?(?:la\s+)?)|"
              rf"(?:soy\s+de|curso\s+en|pertenezco\s+a)\s+la\s+)({codigo})(?!\w)")
    for match in re.finditer(patron, texto):
        # «comisión a la que pertenezco» contiene una preposición, no el código A.
        if match[1] == "a" and re.match(r"\s+(?:la|las|los|que|cual|cuales)\b", texto[match.end():]):
            continue
        return match[1].upper()
    return None


def _resolver_contexto(contexto, texto, materias, *, catalogo=None):
    cambios = {}
    partes = re.split(r"\baclaracion\s*:\s*", normalizar_comparacion(texto))
    # La última intervención puede cambiar explícitamente la materia. Una aclaración
    # breve no contiene una materia y conserva la ya resuelta para la consulta pendiente.
    materia = resolver_materia(partes[-1], materias)
    if materia is not None:
        cambios["materia_id"] = materia.id
    elif contexto.materia_id is None:
        materia = resolver_materia(texto, materias)
        if materia is not None:
            cambios["materia_id"] = materia.id

    periodos = list(re.finditer(r"\b[0-9]{4}(?:c[12]|-[12]c)\b", normalizar_comparacion(texto)))
    if periodos:
        cambios["periodo"] = normalizar_periodo(periodos[-1][0])
    elif contexto.periodo is not None:
        cambios["periodo"] = normalizar_periodo(contexto.periodo)
    comisiones = catalogo.comisiones(cambios.get("periodo")) if catalogo is not None else ()
    ultimo = _extraer_comision(partes[-1], comisiones)
    if contexto.consulta_pendiente and re.fullmatch(r"\s*(?:[a-z]|[0-9]{1,4}[a-z]?)[.!]?\s*", partes[-1]):
        cambios["comision"] = partes[-1].strip(" .!").upper()
    elif ultimo:
        cambios["comision"] = ultimo
    elif contexto.comision is None:
        previo = _extraer_comision(partes[0], comisiones)
        if previo:
            cambios["comision"] = previo
    elif contexto.comision is not None:
        cambios["comision"] = normalizar_comision(contexto.comision)
    return contexto.model_copy(update=cambios)


async def resolver_fuentes(fragmentos):
    fuentes = {}
    for fragmento in fragmentos:
        clave = fragmento.documento_id
        if clave not in fuentes:
            enlace = None
            try:
                async with asyncio.timeout(2):
                    datos = await asyncio.to_thread(obtener_metadatos_archivo, fragmento.drive_id)
                if datos.get("id") == fragmento.drive_id and datos.get("webViewLink"):
                    # Se acepta exclusivamente una URL recibida de Drive, nunca del LLM.
                    tipo_enlace = FuenteChat.model_fields["enlace"].annotation
                    enlace = TypeAdapter(tipo_enlace).validate_python(datos["webViewLink"])
            except Exception as error:
                # Un enlace ausente no elimina una fuente con título e identidad verificables.
                registrar_etapa("fuente_enlace_no_disponible", documento_id=clave,
                                tipo_error=type(error).__name__)
                enlace = None
            fuentes[clave] = FuenteChat(
                documento_id=clave, drive_id=fragmento.drive_id, titulo=fragmento.titulo,
                enlace=enlace, fragmento_ids=[],
            )
        fuentes[clave].fragmento_ids.append(fragmento.fragmento_id)
    return list(fuentes.values())


def _texto_consulta(solicitud: SolicitudChat) -> str:
    pendiente = solicitud.contexto.consulta_pendiente
    return f"{pendiente}\nAclaración: {solicitud.mensaje}" if pendiente else solicitud.mensaje


def _respuesta_derivada(respuesta: RespuestaChat, sector: dict, consulta: str = "", *, directa=False) -> RespuestaChat:
    """Construcción común para contacto directo y fallback; no registra métricas."""
    contacto = DerivacionChat(
        sector=sector["department"], correo=sector["email"],
        horario=sector["schedule"], responsable=sector.get("contact_person"),
    )
    return RespuestaChat.model_validate(respuesta.model_dump() | {
        "estado": "derivada", "derivacion": contacto,
        "correo_prearmado": derivacion.generar_correo_prearmado(contacto),
        "respuesta": derivacion.redactar_derivacion(contacto, consulta, directa=directa),
    })


async def conversar(solicitud: SolicitudChat, *, configuracion, gestor_embeddings, gestor_llm):
    """Todas las dependencias se inyectan; no se registran textos ni excepciones remotas."""
    try:
        respuesta = await _conversar(solicitud, configuracion, gestor_embeddings, gestor_llm)
        if respuesta.estado == "sin_evidencia" and identificar_objetivo(_texto_consulta(solicitud)) not in {
            "plan_estudios", "matriculacion", "promocion", "definicion_regular", "cronograma",
        }:
            sector = await asyncio.to_thread(
                derivacion.find_referral_department, _texto_consulta(solicitud),
            )
            if sector is not None:
                respuesta = _respuesta_derivada(respuesta, sector, _texto_consulta(solicitud))
        if respuesta.estado == "derivada":
            await asyncio.to_thread(
                repositorio_derivaciones.registrar_derivacion_anonima, respuesta.derivacion.sector,
            )
        return respuesta
    except ContenidoBloqueado:
        return RespuestaChat(
            estado="bloqueada", respuesta="No puedo procesar esa solicitud.",
            umbral=configuracion.rag_umbral_confianza,
            contexto=solicitud.contexto.model_copy(update={"consulta_pendiente": None}),
        )
    except EmbeddingsNoDisponibles as error:
        # La lista vacía/configuración y los errores permanentes siguen siendo técnicos.
        if error.errores and all(isinstance(e, (ErrorTemporal, CuotaAgotada)) for e in error.errores):
            return RespuestaChat(
                estado="sin_evidencia", respuesta=DOCUMENTACION_NO_DISPONIBLE,
                umbral=configuracion.rag_umbral_confianza,
                contexto=solicitud.contexto.model_copy(update={"consulta_pendiente": None}),
            )
        raise ErrorConversacion("No se pudo completar la consulta. Intentá nuevamente.") from None
    except Exception:
        raise ErrorConversacion("No se pudo completar la consulta. Intentá nuevamente.") from None


async def generar_documental(solicitud, *, configuracion, gestor_embeddings, gestor_llm,
                             embedding_consulta=None):
    """Mismo flujo RAG para T41/T43; omite exclusivamente la consulta de caché."""
    captura = {}
    await _conversar(solicitud, configuracion, gestor_embeddings, gestor_llm,
                     usar_cache=False, captura=captura, embedding_consulta=embedding_consulta)
    return captura.get("generacion")


async def _conversar(solicitud, config, embeddings, llm, *, usar_cache=True, captura=None,
                     embedding_consulta=None):
    contexto = solicitud.contexto
    pendiente = contexto.consulta_pendiente
    texto = _texto_consulta(solicitud)
    objetivo = identificar_objetivo(texto)
    abstencion = texto_abstencion(objetivo)
    advertencia = advertencia_temporal(texto)
    umbral = config.rag_umbral_confianza

    def responder(estado, respuesta, resultado=None, **extras):
        return RespuestaChat(
            estado=estado, respuesta=respuesta,
            confianza=resultado.confianza if resultado else 0.0,
            umbral=resultado.umbral if resultado else umbral, advertencia_temporal=advertencia,
            contexto=contexto, **extras,
        )

    if entrada_bloqueada(texto):
        contexto = contexto.model_copy(update={"consulta_pendiente": None})
        return responder("bloqueada", mensaje_bloqueo(texto))
    # Atajos únicamente del chat: la curación FAQ conserva su recorrido documental.
    if captura is None:
        local = respuesta_local(texto)
        if local is None and pendiente:
            # Moderación ya evaluó también la pendiente. Una intervención social
            # no la ejecuta ni la elimina: la siguiente aclaración podrá completarla.
            local = respuesta_social(solicitud.mensaje)
        if local is not None:
            return responder("respondida", local)
        contacto = await asyncio.to_thread(derivacion.resolver_contacto_directo, texto)
        if contacto is not None:
            contexto = contexto.model_copy(update={"consulta_pendiente": None})
            return _respuesta_derivada(responder("sin_evidencia", SIN_EVIDENCIA), contacto,
                                       texto, directa=True)
    catalogo = await asyncio.to_thread(cargar_catalogo)
    materias = await asyncio.to_thread(repositorio.listar_materias_chat)
    try:
        contexto = _resolver_contexto(contexto, texto, materias, catalogo=catalogo)
    except ValueError:
        return responder("sin_evidencia", "No encontré información para ese período en el catálogo disponible.")
    periodo_elegido = contexto.periodo or catalogo.periodo_activo
    if periodo_elegido not in catalogo.periodos:
        contexto = contexto.model_copy(update={"consulta_pendiente": None})
        return responder("sin_evidencia", "No encontré información para ese período en el catálogo disponible.")
    if contexto.comision is not None and not catalogo.admite(contexto.comision, periodo_elegido):
        invalida = (contexto.comision if re.fullmatch(r"[A-Z0-9]{1,32}", contexto.comision)
                    else "indicada")
        contexto = contexto.model_copy(update={"comision": None,
            "consulta_pendiente": pendiente or solicitud.mensaje})
        opciones = catalogo.comisiones(periodo_elegido)
        listado = ", ".join(opciones[:-1]) + (f" o {opciones[-1]}" if len(opciones) > 1 else opciones[0])
        return responder("requiere_contexto",
            f"No encuentro la comisión {invalida} entre las comisiones disponibles para este período. "
            f"Indicame si tu comisión es {listado}.", campo_requerido="comision")
    intencion = clasificar_intencion_documental(texto, config)
    institucional = (es_consulta_administrativa(texto)
                     or (contexto.materia_id is None and contexto.comision is None
                         and es_consulta_institucional(texto)))
    if institucional:
        intencion = "general"
    if objetivo in OBJETIVOS_DIRIGIDOS:
        intencion = "general"
        institucional = True
    advertencia = advertencia_temporal(texto, tipo_documental=intencion)
    academica = intencion in ("programa", "cronograma")
    if academica and contexto.periodo is None:
        contexto = contexto.model_copy(update={"periodo": periodo_elegido})
    evento = identificar_evento(texto) if intencion == "cronograma" else None
    umbral = config.umbral_rag_para_tipo(intencion) if academica else config.rag_umbral_confianza
    filtros = FiltrosRecuperacion(
        materia_id=contexto.materia_id, comision=contexto.comision, periodo=contexto.periodo,
        tipo_documental=intencion if academica else None,
    )
    faltantes = [campo for campo in ("materia_id", "comision") if getattr(contexto, campo) is None]
    documentos = await asyncio.to_thread(repositorio.listar_documentos_chat)
    documentos = [d for d in documentos if d.estado == "activo" and d.vigente is True
                  and catalogo.documento_habilitado(d, periodo_elegido)]
    permitidos = {d.id for d in documentos}
    embedding_dirigido = None

    async def vectorizar():
        nonlocal embedding_consulta
        validar_configuracion_rag(config)
        if embedding_consulta is None:
            embedding_consulta = await embeddings.embed_query(texto.strip())
        validar_resultado_consulta(embedding_consulta)
        return embedding_consulta

    async def recuperar(ids=None):
        nonlocal embedding_dirigido
        seleccion_ids = sorted(permitidos if ids is None else permitidos.intersection(ids))

        async def buscar(vector):
            datos = await recuperar_fragmentos(
                texto, filtros=filtros, documento_ids=seleccion_ids,
                gestor=embeddings, configuracion=config, embedding_consulta=vector,
                top_k=max(config.rag_top_k, 20) if objetivo in OBJETIVOS_DIRIGIDOS else None,
            )
            if academica and not datos.evidencia_suficiente:
                comunes = [d.id for d in documentos if d.id in seleccion_ids
                           and d.comision == "TODAS" and d.materia_id == filtros.materia_id
                           and d.tipo_documental == filtros.tipo_documental
                           and d.periodo == filtros.periodo]
                if comunes:
                    # La RPC genérica puede leer alcance TODAS sin modificar el SQL.
                    # Los IDs ya verificaron materia, tipo y período exactos.
                    comun = await recuperar_fragmentos(
                        texto, filtros=FiltrosRecuperacion(periodo=filtros.periodo),
                        documento_ids=comunes, gestor=embeddings, configuracion=config,
                        embedding_consulta=vector,
                    )
                    por_id = {d.id: d for d in documentos}
                    primarios = [f.model_copy(update={"tipo_documental": por_id[f.documento_id].tipo_documental})
                                 for f in comun.fragmentos if f.documento_id in comunes
                                 and f.materia_id == filtros.materia_id and f.comision == "TODAS"
                                 and f.periodo == filtros.periodo
                                 and (evento is None or respalda_evento(f.contenido, evento))]
                    candidatos = [f for f in datos.fragmentos
                                  if f.fragmento_id in (datos.fragmento_ids_primarios or [])]
                    datos = datos.model_copy(update={
                        "fragmentos": [*candidatos, *primarios],
                        "fragmento_ids_primarios": [f.fragmento_id for f in [*candidatos, *primarios]],
                    })
            return seleccionar(datos, documentos=documentos, objetivo=objetivo,
                               limite=config.rag_top_k, catalogo=catalogo, periodo=periodo_elegido)

        datos = await buscar(await vectorizar())
        if objetivo in OBJETIVOS_DIRIGIDOS and not datos.evidencia_suficiente and seleccion_ids:
            if embedding_dirigido is None:
                embedding_dirigido = await embeddings.embed_query(consulta_dirigida(texto, objetivo))
                validar_resultado_consulta(embedding_dirigido)
            adicional = await buscar(embedding_dirigido)
            if adicional.mejor_similitud is not None and (
                datos.mejor_similitud is None or adicional.mejor_similitud > datos.mejor_similitud
            ):
                datos = adicional
        return datos

    resultado = None

    def pedir_contexto(campo):
        nonlocal contexto
        contexto = contexto.model_copy(update={"consulta_pendiente": pendiente or solicitud.mensaje})
        pregunta = ("¿Sobre qué materia es tu consulta?" if campo == "materia_id"
                    else "¿Sobre qué comisión es tu consulta?")
        return responder("requiere_contexto", pregunta, resultado, campo_requerido=campo)

    seleccion_programas = None
    generales = None
    if academica:
        cobertura = [d for d in documentos if d.tipo_documental == intencion
                     and d.estado == "activo" and d.vigente is True
                     and (contexto.materia_id is None or d.materia_id == contexto.materia_id)
                     and (contexto.periodo is None or d.periodo is not None
                          and normalizar_comparacion(d.periodo) == normalizar_comparacion(contexto.periodo))]
        if contexto.materia_id is None:
            return pedir_contexto("materia_id")
        especificos = any(d.comision not in (None, "TODAS") for d in cobertura)
        comunes = any(d.comision in (None, "TODAS") for d in cobertura)
        if contexto.comision is None and intencion == "cronograma" and not any(
            d.comision == "TODAS" for d in cobertura
        ):
            return pedir_contexto("comision")
        if not cobertura:
            contexto = contexto.model_copy(update={"consulta_pendiente": None})
            return responder("sin_evidencia", abstencion)
        seleccion_programas = None
        if intencion == "programa" and contexto.comision is None and especificos and not comunes:
            comisiones = {d.comision.strip().upper() for d in cobertura if d.comision is not None}
            if len(comisiones) > 1:
                firmas = await asyncio.to_thread(
                    repositorio.obtener_firmas_contenido_documentos, [d.id for d in cobertura],
                )
                if len(firmas) != len(cobertura) or len(set(firmas.values())) != 1:
                    return pedir_contexto("comision")
            # La comisión procede de un documento existente, no se asigna al contexto.
            # Sólo se usa para la búsqueda estricta, acotada a los documentos aplicables.
            representante = min(cobertura, key=lambda d: d.id)
            filtros = filtros.model_copy(update={"comision": representante.comision})
            seleccion_programas = [d.id for d in cobertura if d.comision == representante.comision]
    elif faltantes or institucional:
        generales = [d.id for d in documentos if _coincide(d, filtros)
                     and (not institucional or d.tipo_documental == "general")
                     and all(getattr(d, campo) is None for campo in faltantes)]
        if objetivo == "plan_estudios":
            generales = [d.id for d in documentos if d.id in generales
                         and "plan" in normalizar_comparacion(d.titulo)
                         and "estudios" in normalizar_comparacion(d.titulo)]
    # Una búsqueda después de resolver también las aclaraciones T36.
    if usar_cache:
        hit = await buscar_faq(
            texto, contexto=contexto, tipo_documental=intencion if academica else "general",
            embedding=await vectorizar(), configuracion=config, resolver_fuentes=resolver_fuentes,
            catalogo=catalogo,
        )
        if hit is not None:
            return hit
    if academica:
        resultado = await recuperar(seleccion_programas)
    elif generales:
        resultado = await recuperar(generales)
    if objetivo == "promocion" and (resultado is None or not resultado.evidencia_suficiente):
        programas = [d for d in documentos if d.tipo_documental == "programa"
                     and d.periodo == periodo_elegido
                     and (contexto.materia_id is None or d.materia_id == contexto.materia_id)]
        contenidos = await asyncio.to_thread(
            repositorio.obtener_contenidos_documentos, [d.id for d in programas],
        ) if programas else {}
        condiciones = {d.id: contenido_pertinente(contenidos.get(d.id, ""), objetivo, d.titulo)
                       for d in programas}
        programas = [d for d in programas if condiciones[d.id]]
        if programas and contexto.materia_id is None:
            return pedir_contexto("materia_id")
        if programas:
            comunes = [d for d in programas if d.comision in (None, "TODAS")]
            seleccion = comunes or [d for d in programas if contexto.comision is None
                                     or d.comision == contexto.comision]
            if not comunes and contexto.comision is None:
                firmas = {normalizar_comparacion(condiciones[d.id]) for d in seleccion}
                if len(firmas) > 1:
                    return pedir_contexto("comision")
                seleccion = sorted(seleccion, key=lambda d: d.id)[:1]
            if seleccion:
                academica = True
                intencion = "programa"
                filtros = filtros.model_copy(update={
                    "tipo_documental": "programa", "periodo": periodo_elegido,
                    "comision": (None if comunes else seleccion[0].comision),
                })
                contexto = contexto.model_copy(update={"periodo": periodo_elegido})
                resultado = await recuperar([d.id for d in seleccion])
    if institucional and (resultado is None or not resultado.evidencia_suficiente):
        contexto = contexto.model_copy(update={"consulta_pendiente": None})
        return responder("sin_evidencia", abstencion, resultado)
    if not academica and (resultado is None or not resultado.evidencia_suficiente):
        resultado = await recuperar()
        if resultado.evidencia_suficiente:
            relevantes = [f for f in resultado.fragmentos if f.similitud >= resultado.umbral]
            for campo in faltantes:
                if any(getattr(f, campo) is not None for f in relevantes):
                    contexto = contexto.model_copy(update={
                        "consulta_pendiente": pendiente or solicitud.mensaje,
                    })
                    pregunta = ("¿Sobre qué materia es tu consulta?" if campo == "materia_id"
                                else "¿Sobre qué comisión es tu consulta?")
                    return responder("requiere_contexto", pregunta, resultado,
                                     campo_requerido=campo)
    contexto = contexto.model_copy(update={"consulta_pendiente": None})
    if resultado is None or not resultado.evidencia_suficiente:
        return responder("sin_evidencia", abstencion, resultado)
    fragmentos = [f for f in resultado.fragmentos if f.similitud >= resultado.umbral]
    primarios = resultado.fragmento_ids_primarios
    if academica and (not primarios or not any(f.fragmento_id in primarios for f in fragmentos)):
        return responder("sin_evidencia", abstencion)
    if evento is not None and not any(
        f.fragmento_id in (primarios or []) and respalda_evento(f.contenido, evento)
        for f in fragmentos
    ):
        return responder("sin_evidencia", abstencion)
    if not total_plan_respaldado(texto, [f.contenido for f in fragmentos]):
        return responder("sin_evidencia", abstencion, resultado)
    instrucciones = _PROMPT.read_text(encoding="utf-8")
    if objetivo in OBJETIVOS_DIRIGIDOS:
        instrucciones += (
            "\nRespondé al objetivo solicitado. Una definición no explica un procedimiento. "
            "No confundas materias del plan de estudios con materias matriculadas en Moodle. "
            "No infieras condiciones de promoción desde una inscripción o un cronograma."
        )
    if academica:
        instrucciones += (
            "\nLa respuesta académica debe fundamentarse en evidencia primaria. "
            "Citá al menos un fragmento primario. La evidencia complementaria sólo agrega "
            "contexto institucional; no demuestra horarios, contenidos ni fechas específicos. "
            "No sustituyas ni contradigas los datos primarios con información complementaria."
        )
    if intencion == "cronograma" and eventos_en_texto(texto):
        instrucciones += (
            "\nRespondé sobre el evento académico solicitado usando sus bloques específicos. "
            "No equipares parcial ordinario, actividad pre-parcial y recuperatorio. "
            "Si la fuente sólo fecha el inicio de una semana, indicá la semana y cuándo "
            "empieza; no afirmes que el examen se toma ese día. No infieras año ni día exacto."
            " Al enumerar eventos, indicá cada uno en un renglón separado con su identificador "
            "y su fecha o rango completo. No agrupes fechas sin una asociación individual."
        )
    solicitud_llm = SolicitudLLM(
        instrucciones_sistema=instrucciones,
        contexto=json.dumps([
            {"fragmento_id": f.fragmento_id, "documento_id": f.documento_id,
             "titulo": f.titulo,
             "contenido": (contexto_evento(f.contenido, evento)
                           if evento is not None and f.fragmento_id in (primarios or [])
                           else contenido_pertinente(f.contenido, objetivo, f.titulo)),
             "evidencia": "primaria" if primarios is None or f.fragmento_id in primarios else "complementaria"}
            for f in fragmentos
        ], ensure_ascii=False),
        consulta=texto, temperatura=0.1, max_tokens=1200, respuesta_json=True,
    )
    fuentes_versionadas = []
    if captura is not None:
        # Capturar ANTES del LLM: el upsert comprobará que estas versiones siguen vigentes.
        fuentes_versionadas = await asyncio.to_thread(
            repositorio_faq.obtener_fuentes_para_guardar,
            [f.fragmento_id for f in fragmentos],
        )
        actuales = {f.fragmento_id: f for f in fuentes_versionadas}
        for fragmento in fragmentos:
            actual = actuales[fragmento.fragmento_id]
            excluidos = {"similitud"}
            if not academica and fragmento.tipo_documental is None:
                # match_fragmentos no devuelve tipo_documental. El snapshot conserva
                # su valor real; su ausencia en la RPC no representa un cambio.
                excluidos.add("tipo_documental")
            if (actual.estado_documento != "activo" or actual.vigente is not True
                    or actual.como_fragmento().model_dump(exclude=excluidos)
                    != fragmento.model_dump(exclude=excluidos)):
                raise ErrorConversacion("La evidencia cambió durante la generación FAQ.")
    generado = await llm.generar_con_respaldo(solicitud_llm)
    etapa = "validacion_generacion"
    try:
        salida, usados = validar_generacion(
            generado.texto, [f.model_copy(update={"contenido": contenido_pertinente(
                f.contenido, objetivo, f.titulo,
            )}) for f in fragmentos], proveedor=generado.proveedor,
            fragmento_ids_primarios=primarios if academica else None,
            documento_ids_permitidos={f.documento_id for f in fragmentos},
            consulta=texto if intencion == "cronograma" else None,
        )
        if not respuesta_compatible(salida.respuesta, objetivo):
            return responder("sin_evidencia", abstencion, resultado)
        if not total_plan_respaldado(texto, [f.contenido for f in usados], respuesta=salida.respuesta):
            return responder("sin_evidencia", abstencion, resultado)
        originales = {f.fragmento_id: f for f in fragmentos}
        usados = [originales[f.fragmento_id] for f in usados]
        etapa = "fuentes_construccion_error"
        fuentes = await resolver_fuentes(usados)
        registrar_etapa("fuentes_construidas", proveedor=generado.proveedor, cantidad=len(fuentes))
        etapa = "respuesta_final_error"
        respuesta = responder("respondida", salida.respuesta, resultado, fuentes=fuentes)
        if captura is not None:
            captura["generacion"] = GeneracionDocumental(
                respuesta, usados, primarios if academica else None,
                await vectorizar(), filtros,
                [f for f in fuentes_versionadas if f.fragmento_id in salida.fragmento_ids],
            )
        registrar_etapa("respuesta_final_validada", proveedor=generado.proveedor)
        return respuesta
    except ErrorPostproceso as error:
        if intencion == "cronograma" and error.etapa in ("eventos_invalidos", "fechas_invalidas"):
            registrar_etapa("abstencion_eventos", proveedor=generado.proveedor,
                            etapa=error.etapa, motivo=error.motivo)
            return responder("sin_evidencia", abstencion, resultado)
        raise
    except Exception as error:
        registrar_etapa(etapa, proveedor=generado.proveedor, tipo_error=type(error).__name__)
        raise ErrorPostproceso(etapa, "fallo_de_postproceso") from None

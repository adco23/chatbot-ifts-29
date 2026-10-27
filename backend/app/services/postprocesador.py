"""Salida LLM verificable: normalización acotada y diagnóstico sin contenido."""

import json
import logging
import re

from pydantic import ValidationError

from app.models.chat import SalidaGenerador
from app.services.sanitizador import sanitizar_texto
from app.services.temporalidad import fechas_respaldadas

logger = logging.getLogger(__name__)
_CLAVES = {"respuesta", "fragmento_ids", "citas_textuales"}


class ErrorPostproceso(ValueError):
    def __init__(self, etapa, motivo):
        self.etapa = etapa
        self.motivo = motivo
        super().__init__(f"Salida generada rechazada en {etapa}: {motivo}.")


def registrar_etapa(evento, *, proveedor=None, **datos):
    """Los campos se construyen en el servidor: etiquetas, contadores e IDs.

    WARNING permite diagnosticar con la configuración local actual. Nunca pasar
    excepciones, errores Pydantic completos, claves desconocidas ni texto remoto.
    """
    nombre = proveedor if proveedor in ("gemini", "groq", "falso") else "otro"
    logger.warning("%s proveedor=%s datos=%s", evento, nombre, datos)


def _rechazar(evento, motivo, proveedor, **datos):
    registrar_etapa(evento, proveedor=proveedor, motivo=motivo, **datos)
    raise ErrorPostproceso(evento, motivo) from None


def _objeto_sin_duplicados(pares):
    resultado = {}
    for clave, valor in pares:
        if clave in resultado:
            raise ValueError("JSON ambiguo.")
        resultado[clave] = valor
    return resultado


def _constante_invalida(valor):
    raise ValueError("JSON no estándar.")


def _id_canonico(valor):
    if isinstance(valor, str) and re.fullmatch(r"[1-9][0-9]{0,9}", valor.strip()):
        numero = int(valor.strip())
        if numero <= 2147483647:
            return numero
    return valor


def _ids_seguros(valores):
    if not isinstance(valores, (list, tuple, set)):
        return []
    return [i for i in valores if type(i) is int and 0 < i <= 2147483647][:50]


def validar_generacion(
    texto, fragmentos, *, proveedor=None, fragmento_ids_primarios=None,
    documento_ids_permitidos=None,
):
    disponibles = {f.fragmento_id: f for f in fragmentos}
    permitidos = _ids_seguros(list(disponibles))
    if len(disponibles) != len(fragmentos):
        _rechazar("fragmentos_invalidos", "contexto_ambiguo", proveedor)
    if documento_ids_permitidos is not None and any(
        f.documento_id not in documento_ids_permitidos for f in fragmentos
    ):
        _rechazar("fragmentos_invalidos", "documento_incorrecto", proveedor)
    if not isinstance(texto, str):
        _rechazar("json_parseado_error", "texto_invalido", proveedor)
    registrar_etapa("generacion_llm_recibida", proveedor=proveedor, caracteres=len(texto),
                    ids_permitidos=permitidos)
    normalizaciones = []
    limpio = texto.strip()
    if limpio.startswith("\ufeff"):
        limpio = limpio[1:].strip()
        normalizaciones.append("bom")
    bloque = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```", limpio, re.IGNORECASE)
    if bloque:
        limpio = bloque[1].strip()
        normalizaciones.append("bloque_json")
    try:
        datos = json.loads(limpio, object_pairs_hook=_objeto_sin_duplicados,
                           parse_constant=_constante_invalida)
    except (ValueError, RecursionError) as error:
        _rechazar("json_parseado_error", "json_invalido_o_ambiguo", proveedor,
                  tipo_error=type(error).__name__)
    registrar_etapa("json_parseado_ok", proveedor=proveedor,
                    claves=sorted(_CLAVES.intersection(datos)) if isinstance(datos, dict) else [],
                    claves_adicionales=len(set(datos) - _CLAVES) if isinstance(datos, dict) else 0)
    if isinstance(datos, dict):
        if isinstance(datos.get("respuesta"), str):
            datos["respuesta"] = sanitizar_texto(datos["respuesta"])
        referencias = datos.get("fragmento_ids")
        if isinstance(referencias, list):
            ids = []
            for referencia in referencias:
                if isinstance(referencia, dict):
                    if set(referencia) - {"fragmento_id", "documento_id"} or "fragmento_id" not in referencia:
                        _rechazar("schema_respuesta_error", "referencia_estructurada_invalida", proveedor)
                    identidad = _id_canonico(referencia["fragmento_id"])
                    if type(identidad) is not int or identidad not in disponibles:
                        _rechazar("fragmentos_invalidos", "id_no_permitido", proveedor,
                                  ids_citados=_ids_seguros([identidad]), ids_permitidos=permitidos)
                    if "documento_id" in referencia:
                        documento = _id_canonico(referencia["documento_id"])
                        if type(documento) is not int or documento != disponibles[identidad].documento_id:
                            _rechazar("fragmentos_invalidos", "documento_incorrecto", proveedor)
                    ids.append(identidad)
                    normalizaciones.append("referencia_estructurada")
                else:
                    ids.append(_id_canonico(referencia))
            datos["fragmento_ids"] = ids
        if datos.get("citas_textuales", []) is None:
            datos["citas_textuales"] = []
            normalizaciones.append("citas_nulas")
        if isinstance(datos.get("citas_textuales"), list):
            for cita in datos["citas_textuales"]:
                if isinstance(cita, dict):
                    for campo in ("fragmento_id", "documento_id"):
                        if campo in cita:
                            cita[campo] = _id_canonico(cita[campo])
    try:
        salida = SalidaGenerador.model_validate(datos)
    except ValidationError:
        _rechazar("schema_respuesta_error", "contrato_invalido", proveedor,
                  tipo_error="ValidationError")
    registrar_etapa("schema_respuesta_ok", proveedor=proveedor,
                    normalizaciones=sorted(set(normalizaciones)))
    if any(i not in disponibles for i in salida.fragmento_ids):
        _rechazar("fragmentos_invalidos", "id_no_permitido", proveedor,
                  ids_citados=_ids_seguros(salida.fragmento_ids), ids_permitidos=permitidos)
    usados = list(dict.fromkeys(salida.fragmento_ids))
    registrar_etapa("fragmentos_validos", proveedor=proveedor, ids_citados=_ids_seguros(usados),
                    ids_permitidos=permitidos)
    for cita in salida.citas_textuales:
        if cita.fragmento_id not in usados or not sanitizar_texto(cita.texto):
            _rechazar("citas_invalidas", "referencia_de_cita_invalida", proveedor)
        fuente = disponibles[cita.fragmento_id]
        if cita.documento_id is not None and cita.documento_id != fuente.documento_id:
            _rechazar("citas_invalidas", "documento_incorrecto", proveedor)
        if sanitizar_texto(cita.texto) not in sanitizar_texto(fuente.contenido):
            _rechazar("citas_invalidas", "cita_no_respaldada", proveedor)
    contenidos = [disponibles[i].contenido for i in usados]
    for cita in re.findall(r'["“«]([^"”»]+)["”»]', salida.respuesta):
        if not any(sanitizar_texto(cita) in sanitizar_texto(c) for c in contenidos):
            _rechazar("citas_invalidas", "cita_no_respaldada", proveedor)
    registrar_etapa("citas_validas", proveedor=proveedor, cantidad=len(salida.citas_textuales))
    if fragmento_ids_primarios is not None:
        if not any(i in fragmento_ids_primarios for i in usados):
            _rechazar("evidencia_primaria_faltante", "sin_referencia_primaria", proveedor,
                      ids_citados=_ids_seguros(usados))
        contenidos = [disponibles[i].contenido for i in usados if i in fragmento_ids_primarios]
        registrar_etapa("evidencia_primaria_citada", proveedor=proveedor,
                        ids_citados=_ids_seguros([i for i in usados if i in fragmento_ids_primarios]))
    if not fechas_respaldadas(salida.respuesta, contenidos):
        _rechazar("fechas_invalidas", "fecha_no_respaldada", proveedor)
    registrar_etapa("fechas_validas", proveedor=proveedor)
    return salida, [disponibles[i] for i in usados]

"""Directorio local de sectores validado y búsqueda determinista para T38."""

import json
import logging
import re
from pathlib import Path

# Importamos el diccionario global MESSAGES desde el cargador
from app.config.message_loader import MESSAGES
from app.models.chat import CorreoDerivacionChat, DerivacionChat
from app.services.sanitizador import normalizar_comparacion, sanitizar_texto
from app.services.objetivo_consulta import PROCEDIMIENTOS, identificar_objetivo, texto_abstencion

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_JSON_PATH = CONFIG_DIR / "derivaciones.json"
logger = logging.getLogger(__name__)


def generar_correo_prearmado(derivacion: DerivacionChat) -> CorreoDerivacionChat:
    """T39: plantilla fija; no recibe ni copia mensajes o datos del estudiante."""
    return CorreoDerivacionChat(
        destinatario=derivacion.correo,
        asunto="Consulta de estudiante - IFTS 29",
        cuerpo=(
            "Hola,\n\n"
            "Quisiera realizar una consulta relacionada con gestiones estudiantiles del IFTS 29."
            "\n\nMuchas gracias."
        ),
    )


def _get_msg(key_path: str, default: str) -> str:
    """Función auxiliar para buscar mensajes anidados en el diccionario MESSAGES."""
    keys = key_path.split(".")
    val = MESSAGES
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
    return val if isinstance(val, str) else default


def _validar_registro(registro: object) -> dict:
    if not isinstance(registro, dict):
        raise ValueError("Se esperaba un sector del directorio.")
    contacto = DerivacionChat(
        sector=registro.get("department"), correo=registro.get("email"),
        horario=registro.get("schedule"), responsable=registro.get("contact_person"),
    )
    keywords = registro.get("keywords", [])
    if (not isinstance(keywords, list)
            or any(not isinstance(k, str) or not sanitizar_texto(k) for k in keywords)):
        raise ValueError("Las keywords deben ser una lista de textos no vacíos.")
    return registro | {
        "department": contacto.sector, "email": contacto.correo,
        "schedule": contacto.horario, "contact_person": contacto.responsable,
        "keywords": [sanitizar_texto(k) for k in keywords],
    }


def load_referrals(json_path: Path | None = None) -> list[dict]:
    """Conserva las claves originales y omite registros inválidos sin romper el chat."""
    try:
        data = json.loads((json_path or DEFAULT_JSON_PATH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning(_get_msg(
            "derivacion.file_not_found",
            "No se encontró el archivo de derivaciones.",
        ))
        return []
    except json.JSONDecodeError:
        logger.warning(_get_msg(
            "derivacion.json_decode_error",
            "El JSON de derivaciones no es válido.",
        ))
        return []
    except (OSError, UnicodeError) as error:
        logger.warning("No se pudo leer el directorio de derivaciones: tipo=%s.",
                       type(error).__name__)
        return []
    if not isinstance(data, list):
        logger.warning("El directorio de derivaciones debe ser una lista.")
        return []
    validos = []
    for indice, registro in enumerate(data):
        try:
            validos.append(_validar_registro(registro))
        except ValueError:
            logger.warning("Sector de derivación inválido omitido: indice=%d.", indice)
    return validos


_DATO_YA_OCULTO = re.compile(
    r"(?:\b(?:(?:mi )?(?:correo(?: electronico)?|e[- ]?mail|dni|telefono|cuil|cuit|nombre)"
    r"\s*(?:(?:es|:)\s*)?|(?:me llamo|mi nombre es|soy)\s+))?"
    r"\[(?:correo|dni|telefono|cuil|nombre)_oculto\]"
)
_CORTESIA_INICIAL = re.compile(r"^(?:(?:hola|por favor)\b[\s,.!?¡¿]*)+")
_CORTESIA_FINAL = re.compile(r"(?:[\s,.!?¡¿]*\b(?:por favor|gracias))+$")
_OBJETIVO_CONTACTO = re.compile(
    r"\b(?:contacto|contactar(?:me)?|comunico|comunicarme|correo|email|telefono|"
    r"horarios?|atencion|atiende|abre|cierra|escribir(?:le)?|hablar)\b"
)


def _segmento_contacto(texto: str) -> str:
    """Representación transitoria: solo ignora cortesías y valores ya ocultados por T46."""
    limpio, ocultos = _DATO_YA_OCULTO.subn("", texto)
    limpio = limpio.strip(" \t.,;:!?¡¿")
    if ocultos:
        limpio = re.sub(r"^y\b\s*|\s*\by$", "", limpio).strip(" \t.,;:!?¡¿")
    limpio = _CORTESIA_INICIAL.sub("", limpio)
    limpio = _CORTESIA_FINAL.sub("", limpio)
    return limpio.strip(" \t.,;:!?¡¿")


def _patron_contacto(nombre: str) -> re.Pattern[str]:
    sector = rf"(?:(?:la|el) )?{re.escape(normalizar_comparacion(nombre))}"
    dato = (
        r"(?:datos de contacto|contacto|correo(?: electronico)?|e[- ]?mail|telefono|"
        r"horarios?(?: de atencion)?|atencion)"
    )
    pedido = (
        r"(?:(?:(?:cual|cuales) (?:es|son)|quiero saber|quiero conocer|necesito saber|"
        r"me pasas|me decis|quiero|necesito) )?"
    )
    return re.compile(
        rf"(?:como (?:me )?(?:contacto|comunico) (?:con|a) {sector}"
        rf"|como (?:puedo )?(?:contactar(?:me)?|comunicarme|escribir(?:le)?) (?:con|a) {sector}"
        rf"|(?:necesito|quiero|puedo) (?:contactar(?:me)?|comunicarme|hablar|escribir(?:le)?) (?:con |a )?{sector}"
        rf"|{pedido}(?:(?:el|los|su|sus|la) )?{dato}(?: de| con| en)? {sector}"
        rf"|en que horarios? (?:atiende|abre|cierra) {sector}"
        rf"|(?:a que hora|cuando) (?:atiende|abre|cierra) {sector})"
    )


def resolver_contacto_directo(texto: str, json_path: Path | None = None) -> dict | None:
    """Atajo solo para contacto/horario inequívoco de un único sector configurado.

    No usa keywords ni interpreta trámites. Recibe exclusivamente la consulta
    combinada ya sanitizada y moderada; el fallback general continúa separado.
    """
    if not isinstance(texto, str) or not texto.strip():
        return None
    consulta = normalizar_comparacion(texto)
    segmentos = [_segmento_contacto(parte)
                 for parte in re.split(r"\baclaracion\s*:\s*", consulta)]
    segmentos = [parte for parte in segmentos if parte]
    if not segmentos or not any(_OBJETIVO_CONTACTO.search(parte) for parte in segmentos):
        return None
    coincidencias = [
        sector for sector in load_referrals(json_path)
        if all(_patron_contacto(sector["department"]).fullmatch(parte) for parte in segmentos)
    ]
    return coincidencias[0] if len(coincidencias) == 1 else None


def redactar_derivacion(contacto: DerivacionChat, consulta: str, *, directa: bool = False) -> str:
    """Textos seguros: datos del directorio y motivos acotados, sin copiar la consulta."""
    horario = contacto.horario.lower()
    if re.match(r"(?:lunes|martes|miercoles|miércoles|jueves|viernes|sabado|sábado|domingo) a ", horario):
        horario = f"de {horario}"
    atencion = f"El horario de atención es {horario}."
    if directa:
        if re.search(r"\b(?:horarios?|hora|atiende|abre|cierra|atencion)\b",
                     normalizar_comparacion(consulta)):
            return (f"{contacto.sector} atiende {horario}. Si necesitás comunicarte, "
                    f"podés escribir a {contacto.correo}.")
        return (f"Podés comunicarte con {contacto.sector} por correo a {contacto.correo}. "
                f"{atencion}")
    objetivo = identificar_objetivo(consulta)
    if objetivo == "procedimiento_analitico":
        return ("Para consultar cómo realizar la entrega del certificado analítico, "
                f"podés comunicarte con {contacto.sector} a {contacto.correo}. {atencion}")
    if objetivo == "tramite_administrativo":
        return (f"Si no encontrás el trámite que necesitás, podés consultar a {contacto.sector} "
                f"para que te orienten. Podés escribir a {contacto.correo}. {atencion}")
    if objetivo in PROCEDIMIENTOS:
        return (f"{texto_abstencion(objetivo)} Para confirmar cómo tramitarlo, "
                f"podés consultar a {contacto.sector} a {contacto.correo}. {atencion}")
    return f"Para esta consulta podés comunicarte con {contacto.sector} a {contacto.correo}. {atencion}"


def find_referral_department(
    query_text: str, json_path: Path | None = None
) -> dict | None:
    """
    Busca el departamento adecuado para derivar basándose en las keywords
    presentes en el texto de la consulta del usuario.
    """
    if not isinstance(query_text, str) or not query_text.strip():
        return None
    consulta = normalizar_comparacion(query_text)
    for ref in load_referrals(json_path):
        for keyword in [ref["department"], *ref["keywords"]]:
            patron = r"(?<!\w)" + re.escape(normalizar_comparacion(keyword)) + r"(?!\w)"
            if re.search(patron, consulta):
                return ref

    return None

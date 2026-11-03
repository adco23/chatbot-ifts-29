"""Directorio local de sectores validado y búsqueda determinista para T38."""

import json
import logging
import re
from pathlib import Path

# Importamos el diccionario global MESSAGES desde el cargador
from app.config.message_loader import MESSAGES
from app.models.chat import DerivacionChat
from app.services.sanitizador import normalizar_comparacion, sanitizar_texto

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_JSON_PATH = CONFIG_DIR / "derivaciones.json"
logger = logging.getLogger(__name__)


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

import json
from pathlib import Path
from typing import List, Dict, Optional

# Importamos el diccionario global MESSAGES desde el cargador
from app.config.message_loader import MESSAGES

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_JSON_PATH = CONFIG_DIR / "derivaciones.json"

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

def load_referrals(json_path: Optional[Path] = None) -> List[Dict]:
    """
    Carga el listado de sectores de derivación desde un archivo JSON.
    Si el archivo no existe o hay un error de formato, utiliza los mensajes centralizados.
    """
    target_path = json_path or DEFAULT_JSON_PATH
    
    if not target_path.exists():
        warn_msg = _get_msg("derivacion.file_not_found", "⚠️ Advertencia: No se encontró el archivo de derivaciones en la ruta especificada.")
        print(f"{warn_msg} ({target_path})")
        return []
        
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except json.JSONDecodeError as e:
        error_json_msg = _get_msg("derivacion.json_decode_error", "❌ Error al decodificar el JSON de derivaciones:")
        print(f"{error_json_msg} {e}")
        return []
    except Exception as e:
        error_gen_msg = _get_msg("derivacion.unexpected_error", "❌ Error inesperado al leer las derivaciones:")
        print(f"{error_gen_msg} {e}")
        return []

def find_referral_department(query_text: str, json_path: Optional[Path] = None) -> Optional[Dict]:
    """
    Busca el departamento adecuado para derivar basándose en las keywords 
    presentes en el texto de la consulta del usuario.
    """
    referrals = load_referrals(json_path)
    if not referrals:
        return None
        
    query_lower = query_text.lower()
    
    for ref in referrals:
        keywords = ref.get("keywords", [])
        for keyword in keywords:
            if keyword.lower() in query_lower:
                return ref
                
    return None
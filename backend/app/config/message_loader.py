import json
from pathlib import Path

def _load_messages() -> dict:
    # Busca el messages.json en la misma carpeta donde está este script (app/config/)
    json_path = Path(__file__).parent / "messages.json"
    
    if not json_path.exists():
        print(f"⚠️ No se encontró el archivo en: {json_path}")
        return {}
        
    with open(json_path, "r", encoding="utf-8") as file:
        return json.load(file)

# Variable global que importa el resto de los módulos
MESSAGES = _load_messages()
import json
from pathlib import Path

def _load_messages() -> dict:
    # Busca el messages.json asumiendo que está un nivel arriba de 'services'
    json_path = Path(__file__).parent.parent / "messages.json"
    
    if not json_path.exists():
        return {}
        
    with open(json_path, "r", encoding="utf-8") as file:
        return json.load(file)

# Variable global que importa el resto de los módulos
MESSAGES = _load_messages()
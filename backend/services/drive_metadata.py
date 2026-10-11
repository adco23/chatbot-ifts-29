from pathlib import Path
from unidecode import unidecode
from services.message_loader import MESSAGES

def extract_drive_metadata(full_path: str) -> dict:
    """
    Extrae y normaliza los metadatos a partir de la ruta de Drive.
    Espera la estructura: .../Materia/Comisión/Período/Archivo
    """
    parts = Path(full_path).parts
    
    # Verifica que la ruta tenga al menos los 4 niveles obligatorios
    if len(parts) < 4:
        # Buscamos el mensaje en el JSON y le inyectamos la variable
        error_msg = MESSAGES.get("drive_metadata", {}).get("invalid_structure", "Error: {path}")
        return {"status": "ERROR", "message": error_msg.format(path=full_path)}
    
    # Extrae contando desde el final (índices negativos)
    filename = parts[-1]
    period = parts[-2]
    commission = parts[-3]
    subject = parts[-4]
    
    return {
        "status": "SUCCESS",
        "subject": unidecode(subject.lower().strip()),
        "commission": unidecode(commission.lower().strip()),
        "period": unidecode(period.lower().strip()),
        "original_filename": filename,
        "extension": Path(filename).suffix.lower()
    }
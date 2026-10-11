import hashlib
from pathlib import Path

def generate_file_hash(file_path: str) -> str:
    """
    Genera una huella digital (hash SHA-256) del archivo físico.
    Lee en bloques binarios para no saturar la memoria con archivos pesados.
    """
    path = Path(file_path)
    if not path.exists():
        return ""
        
    sha256_hash = hashlib.sha256()
    
    # Abrimos en modo lectura binaria ("rb")
    with open(path, "rb") as f:
        # Leemos el archivo de a pedacitos (4096 bytes)
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
            
    return sha256_hash.hexdigest()

def detect_file_status(file_id: str, current_hash: str, db_hashes: dict) -> str:
    """
    Compara el identificador y la huella actual contra lo que ya está
    guardado (simulado con un diccionario) para detectar el estado.
    """
    if file_id not in db_hashes:
        return "new"
        
    if db_hashes[file_id] != current_hash:
        return "modified"
        
    return "unchanged"
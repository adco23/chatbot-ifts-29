import sys
import os
from pprint import pprint

# 1. Ajuste de rutas: Subimos UN solo nivel (de backend/scripts/ a backend/) 
# para que Python reconozca 'app' como un paquete.
directorio_actual = os.path.dirname(os.path.abspath(__file__))
directorio_backend = os.path.abspath(os.path.join(directorio_actual, ".."))
sys.path.append(directorio_backend)

# Ahora importamos el servicio orquestador
from app.services.sincronizador import run_full_sync
import app.services.sincronizador as sync_module

# 2. Archivos en Drive (Simulación)
archivos_en_drive = {
    "/drive/docs/reglamento_2026.pdf": "hash_nuevo_123",
    "/drive/docs/calendario.pdf": "hash_modificado_456",
    "/drive/docs/faq.pdf": "hash_intacto_789"
}

# 3. Base de datos vectorial (Simulación)
base_de_datos_falsa = {
    "/drive/docs/calendario.pdf": "hash_viejo_000",
    "/drive/docs/faq.pdf": "hash_intacto_789"
}

# 4. Parche técnico
original_detect = sync_module.detect_file_status

def detect_file_status_simulado(file_path, current_hash, mock_db=None):
    return original_detect(file_path, current_hash, mock_db=base_de_datos_falsa)

sync_module.detect_file_status = detect_file_status_simulado

print("🚀 INICIANDO SIMULACIÓN DEL CRON DE VERCEL (T27/T28/T29)\n" + "-"*50)

# 5. Disparamos el orquestador
resultado_api = run_full_sync(archivos_en_drive)

print("\n" + "-"*50 + "\n📦 RESPUESTA QUE RECIBIRÍA VERCEL (JSON):\n")
pprint(resultado_api)
import asyncio
import json
from pathlib import Path
from datetime import datetime

from app.settings import obtener_configuracion
from app.db import repositorio
from app.providers.embeddings.gemini import EmbeddingsGemini
from app.providers.embeddings.gestor import GestorEmbeddings

# Ruta al archivo JSON con las FAQs institucionales
RUTA_JSON = Path(__file__).resolve().parents[1] / "data" / "faqs_institucionales.json"

def calcular_vigencia_hasta() -> str:
    hoy = datetime.now()
    anio = hoy.year
    limite_junio = datetime(anio, 6, 30)
    if hoy <= limite_junio:
        return f"{anio}-06-30"
    else:
        return f"{anio}-12-31"

async def sincronizar():
    print("--- Iniciando sincronización de FAQs institucionales ---")
    
    if not RUTA_JSON.exists():
        print(f"=== Error: No se encontró el archivo en {RUTA_JSON} ===")
        return

    # 1. Obtener la configuración oficial del proyecto
    config = obtener_configuracion()

    # 2. Leer el JSON
    contenido = RUTA_JSON.read_text(encoding="utf-8")
    faqs = json.loads(contenido)
    print(f"Se encontraron {len(faqs)} preguntas para procesar.")

    # 3. Inicializar el proveedor de Gemini usando los atributos exactos de tu config
    proveedor_gemini = EmbeddingsGemini(
        api_key=config.gemini_api_key.get_secret_value(),
        modelo=config.gemini_embedding_modelo,
        dimension=config.gemini_embedding_dimension,
        timeout_segundos=config.gemini_embedding_timeout_segundos,
        timeout_documentos_segundos=config.embedding_timeout_documentos_segundos,
    )
    
    gestor_emb = GestorEmbeddings(
        proveedores=[proveedor_gemini],
        reintentos=3,
        backoff_base_segundos=1.0,
        backoff_maximo_segundos=10.0,
        presupuesto_segundos=30.0
    )

    vigencia = calcular_vigencia_hasta()
    now_str = datetime.now().isoformat()

    insertadas = 0
    for item in faqs:
        pregunta = item["pregunta_canonica"]
        respuesta = item["respuesta"]
        materia_id = item.get("materia_id")
        comision = item.get("comision")

        try:
            # 4. Generar embedding de 768 dimensiones
            res_emb = await gestor_emb.embed_query(pregunta.strip())
            vector = getattr(res_emb, "vector", None)

            if not vector or len(vector) != 768:
                print(f"⚠️ Advertencia: No se pudo generar embedding válido para: '{pregunta}'")
                continue

            # 5. Estructura de datos para la BD
            datos = {
                "pregunta_canonica": pregunta.strip(),
                "respuesta": respuesta.strip(),
                "embedding": vector,
                "materia_id": materia_id,
                "comision": comision,
                "vigencia_hasta": vigencia,
                "generada_en": now_str,
                "aciertos": 0
            }
            
            # Insertar en Supabase a través del repositorio
            await asyncio.to_thread(repositorio.insertar_faq_masivo, datos)
            insertadas += 1
            print(f"✔️ Guardada: {pregunta[:40]}...")

        except Exception as e:
            print(f"❌ Error al procesar '{pregunta}': {e}")

    print(f"--- Sincronización finalizada. Total insertadas exitosamente: {insertadas}/{len(faqs)} ---")

if __name__ == "__main__":
    asyncio.run(sincronizar())
import sys
from pathlib import Path

# 1. Le decimos a Python que la carpeta 'backend' es la raíz del proyecto
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

# 2. Ahora sí podemos importar todo desde 'app' sin problemas
from app.drive.convencion import extract_drive_metadata
from app.drive.extractores import extract_text
from app.services.sincronizador import (
    split_text_into_chunks,
    generate_file_hash,
    detect_file_status,
    generate_embeddings,
    update_document_status,
    sync_document_chunks
)

def probar_todo():
    print("=== PROBANDO T21: METADATOS DE DRIVE ===")
    ruta_simulada = "IFTS_29/Programacion/Comision_A/2026_1/cronograma.pdf"
    metadatos = extract_drive_metadata(ruta_simulada)
    print(metadatos)
    
    print("\n=== PROBANDO T22 Y T23: EXTRACCIÓN Y CHUNKING ===")
    
    # 3. Apuntamos a la carpeta de descargas que está en backend/descargas/
    carpeta_descargas = backend_dir / "descargas"
    archivo_prueba = carpeta_descargas / "prueba.docx"
    
    if not carpeta_descargas.exists():
        carpeta_descargas.mkdir(parents=True, exist_ok=True)
        
    if not archivo_prueba.exists():
        print(f"⚠️ No se encontró el archivo. Por favor, meté un 'prueba.docx' en: {carpeta_descargas}")
        return

    # Usamos str() para que las funciones lo lean como texto y no como objeto Path
    secciones_extraidas = extract_text(str(archivo_prueba))
    print(f"\nSe encontraron {len(secciones_extraidas)} secciones en el archivo.")
    
    todos_los_chunks = []
    titulo_documento = metadatos.get("subject", "Documento Desconocido")
    
    for item in secciones_extraidas:
        nombre_seccion = item.get("section", "General")
        contenido = item.get("content", "")
        
        chunks_de_seccion = split_text_into_chunks(
            text=contenido, 
            document_title=titulo_documento, 
            section_name=nombre_seccion
        )
        todos_los_chunks.extend(chunks_de_seccion)
        
    print(f"Se generaron en total {len(todos_los_chunks)} fragmentos (chunks).")
    
    print("\n=== PROBANDO T24: HUELLA DE CONTENIDO ===")
    base_de_datos_simulada = {
        str(archivo_prueba): "hashfalso123" 
    }
    
    huella_actual = generate_file_hash(str(archivo_prueba))
    print(f"Huella SHA-256 generada: {huella_actual}")
    
    estado = detect_file_status(str(archivo_prueba), huella_actual, base_de_datos_simulada)
    print(f"Estado del archivo detectado: {estado}")

    print("\n=== PROBANDO T25: EMBEDDINGS E INDEXACIÓN ===")
    if estado in ["new", "modified"]:
        print("El archivo es nuevo o cambió. Generando vectores para los fragmentos...")
        chunks_con_vectores = generate_embeddings(todos_los_chunks)
        print(f"Se generaron {len(chunks_con_vectores)} vectores exitosamente.")
        
        if chunks_con_vectores:
            print("\nEstructura del primer chunk listo para la BD:")
            print(f"Claves: {list(chunks_con_vectores[0].keys())}")
            print(f"Dimensiones del vector: {len(chunks_con_vectores[0]['embedding'])}")
            
        print("\n--- Iniciando sincronización con Base de Datos ---")
        sync_document_chunks(str(archivo_prueba), chunks_con_vectores)
        update_document_status(str(archivo_prueba), estado)
        print("--- Fin sincronización ---")
        
    elif estado == "unchanged":
        print("El archivo no cambió. Nos ahorramos generar embeddings.")
        update_document_status(str(archivo_prueba), estado)

if __name__ == "__main__":
    probar_todo()
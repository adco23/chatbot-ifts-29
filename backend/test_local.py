import os
from services.drive_metadata import extract_drive_metadata
from services.text_extractor import extract_text
from services.text_chunker import split_text_into_chunks
from services.file_hasher import generate_file_hash, detect_file_status

def probar_todo():
    print("=== PROBANDO T21: METADATOS DE DRIVE ===")
    ruta_simulada = "IFTS_29/Programacion/Comision_A/2026_1/cronograma.pdf"
    metadatos = extract_drive_metadata(ruta_simulada)
    print(metadatos)
    
    print("\n=== PROBANDO T22 Y T23: EXTRACCIÓN Y CHUNKING ===")
    archivo_prueba = "prueba.docx" 
    
    if os.path.exists(archivo_prueba):
        secciones_extraidas = extract_text(archivo_prueba)
        print(f"\nSe encontraron {len(secciones_extraidas)} secciones en el archivo.")
        
        todos_los_chunks = []
        titulo_documento = metadatos.get("subject", "Documento Desconocido")
        
        for item in secciones_extraidas:
            # Fijate que acá ya estamos usando las claves en inglés ("section" y "content")
            nombre_seccion = item.get("section", "General")
            contenido = item.get("content", "")
            print(f" -> Procesando sección: '{nombre_seccion}' ({len(contenido)} caracteres)")
            
            chunks_de_seccion = split_text_into_chunks(
                text=contenido, 
                document_title=titulo_documento, 
                section_name=nombre_seccion
            )
            todos_los_chunks.extend(chunks_de_seccion)
            
        print(f"\nSe generaron en total {len(todos_los_chunks)} fragmentos (chunks).")
        
        if todos_los_chunks:
            print("\nMostrando los metadatos del primer chunk:")
            print("-" * 40)
            print(f"Metadatos: {todos_los_chunks[0].metadata}")
            print(f"Texto: {todos_los_chunks[0].page_content[:200]}...")
            print("-" * 40)
            
            if len(todos_los_chunks) > 1:
                print("\nMostrando los metadatos del último chunk:")
                print("-" * 40)
                print(f"Metadatos: {todos_los_chunks[-1].metadata}")
                print(f"Texto: {todos_los_chunks[-1].page_content[:200]}...")
                print("-" * 40)
                
        print("\n=== PROBANDO T24: HUELLA DE CONTENIDO ===")
        # Base de datos simulada con un estado anterior
        base_de_datos_simulada = {
            "archivo_viejo.docx": "hashfalso123",
            "prueba.docx": "e4cc31a1fbd964b73d82311f6d59f7efb585646a995685fcf7e8d41d4d3163f5"
        }
        
        huella_actual = generate_file_hash(archivo_prueba)
        print(f"Huella SHA-256 generada: {huella_actual}")
        
        estado = detect_file_status(archivo_prueba, huella_actual, base_de_datos_simulada)
        print(f"Estado del archivo detectado: {estado}")

    else:
        print(f"⚠️ No se encontró '{archivo_prueba}'.")

if __name__ == "__main__":
    probar_todo()
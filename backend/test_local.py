import os
from services.drive_metadata import extract_drive_metadata
from services.text_extractor import extract_text
from services.text_chunker import split_text_into_chunks

def probar_todo():
    print("=== PROBANDO T21: METADATOS DE DRIVE ===")
    ruta_simulada = "IFTS_29/Programacion/Comision_A/2026_1/cronograma.pdf"
    metadatos = extract_drive_metadata(ruta_simulada)
    print(metadatos)
    
    print("\n=== PROBANDO T22 Y T23: EXTRACCIÓN Y CHUNKING ===")
    # Cambiá a prueba.docx para ver cómo detecta las secciones
    archivo_prueba = "prueba.docx" 
    
    if os.path.exists(archivo_prueba):
        # T22: Extraemos el texto dividido por secciones
        secciones_extraidas = extract_text(archivo_prueba)
        print(f"\nSe encontraron {len(secciones_extraidas)} secciones en el archivo.")
        
        todos_los_chunks = []
        # Simulamos que sacamos el título del documento de los metadatos de Drive
        titulo_documento = metadatos.get("materia", "Documento Desconocido")
        
        for item in secciones_extraidas:
            nombre_seccion = item.get("seccion", "General")
            contenido = item.get("contenido", "")
            print(f" -> Procesando sección: '{nombre_seccion}' ({len(contenido)} caracteres)")
            
            # T23: Fragmentamos conservando título y sección
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
            
            # Si tenés varias secciones, mostramos el último también para comparar
            if len(todos_los_chunks) > 1:
                print("\nMostrando los metadatos del último chunk:")
                print("-" * 40)
                print(f"Metadatos: {todos_los_chunks[-1].metadata}")
                print(f"Texto: {todos_los_chunks[-1].page_content[:200]}...")
                print("-" * 40)
    else:
        print(f"⚠️ No se encontró '{archivo_prueba}'.")

if __name__ == "__main__":
    probar_todo()
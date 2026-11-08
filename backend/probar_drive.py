import sys
import os
import io
from pypdf import PdfReader

# Agregamos la ruta actual al path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.drive.cliente import listar_archivos_institucionales, descargar_contenido_archivo
from app.db.repositorio import guardar_documento_institucional, guardar_fragmento_documento
from app.db.conexion import obtener_cliente_supabase

def probar_persistencia_completa():
    print("=== 1. Buscando el archivo 'Preguntas_frecuentes' en Drive ===")
    archivos = listar_archivos_institucionales()
    
    if not archivos:
        print("❌ No se encontraron archivos en la carpeta de Drive.")
        return
    
    archivo_pf = next((f for f in archivos if 'Preguntas_frecuentes' in f.get('name', '')), None)
    
    if not archivo_pf:
        print("❌ No se encontró el archivo 'Preguntas_frecuentes'.")
        return
    
    file_id = archivo_pf.get('id')
    nombre = archivo_pf.get('name')
    print(f"✅ ¡Encontrado! Archivo: '{nombre}'")

    print("\n=== 2. Descargando y extrayendo texto del PDF ===")
    contenido_bytes = descargar_contenido_archivo(file_id)
    
    if not contenido_bytes:
        print("❌ Error al descargar el contenido del archivo.")
        return
    
    texto_extraido = ""
    try:
        reader = PdfReader(io.BytesIO(contenido_bytes))
        for page in reader.pages:
            texto_extraido += page.extract_text() or ""
        print(f"✅ Texto extraído con éxito. Longitud: {len(texto_extraido)} caracteres.")
    except Exception as e:
        print(f"⚠️ Error al leer el PDF: {e}")
        return

    print("\n=== 3. Persistiendo en Supabase a través del Repositorio ===")
    
    # A. Guardar documento institucional
    doc_creado = guardar_documento_institucional(titulo=nombre, drive_id=file_id)
    
    if not doc_creado:
        print("❌ No se pudo guardar el documento en Supabase.")
        return
        
    documento_id = doc_creado.get("id")
    print(f"✅ Documento registrado en Supabase con ID interno: {documento_id}")

    # B. Guardar fragmento asociado
    frag_creado = guardar_fragmento_documento(documento_id=documento_id, contenido=texto_extraido, orden=1)
    
    if frag_creado:
        print("✅ ¡Fragmento guardado exitosamente en la base de datos!")
        print("🚀 ¡Integración de Google Drive con Supabase completada con éxito!")
    else:
        print("❌ Ocurrió un error al guardar el fragmento.")

if __name__ == "__main__":
    probar_persistencia_completa()
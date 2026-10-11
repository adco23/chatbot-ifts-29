from pathlib import Path
from pypdf import PdfReader
from docx import Document

def extract_text(file_path: str) -> list[dict]:
    """
    Extrae el texto de un PDF o DOCX separándolo por secciones.
    Devuelve una lista de diccionarios: [{'seccion': '...', 'contenido': '...'}]
    
    Agregar aquí los formatos a procesar (ej: Google Docs).
    No olvidar instalar las bibliotecas correspondientes.
    """
    path = Path(file_path)
    
    if not path.exists():
        return [{"seccion": "Error", "contenido": f"ERROR: El archivo no existe en {file_path}"}]
        
    extension = path.suffix.lower()
    
    try:
        if extension == '.pdf':
            return _extract_from_pdf(file_path)
        elif extension == '.docx':
            return _extract_from_docx(file_path)
        else:
            return [{"seccion": "Error", "contenido": f"ERROR: Formato no soportado ({extension})."}]
            
    except Exception as e:
        return [{"seccion": "Error", "contenido": f"ERROR al procesar el archivo: {str(e)}"}]

def _extract_from_pdf(file_path: str) -> list[dict]:
    """
    Procesa PDFs. Como pypdf no detecta estilos de título fácilmente,
    agrupa todo el contenido bajo la sección 'General'.
    """
    reader = PdfReader(file_path)
    extracted_text = []
    
    for page in reader.pages:
        text = page.extract_text()
        if text:
            extracted_text.append(text.strip())
            
    contenido_completo = "\n\n".join(extracted_text)
    
    # Devolvemos un único bloque general
    return [{"seccion": "General", "contenido": contenido_completo}]

def _extract_from_docx(file_path: str) -> list[dict]:
    """
    Procesa archivos Word. Detecta los estilos 'Heading' (Títulos) 
    para separar el documento en secciones.
    """
    doc = Document(file_path)
    secciones = []
    seccion_actual = "General"
    contenido_actual = []
    
    for paragraph in doc.paragraphs:
        texto_limpio = paragraph.text.strip()
        if not texto_limpio:
            continue
            
        # Si el estilo empieza con 'Heading' (Heading 1, Heading 2, etc.) o 'Título'
        if paragraph.style.name.startswith('Heading') or paragraph.style.name.startswith('Título'):
            # Guardamos la sección anterior si tenía texto
            if contenido_actual:
                secciones.append({
                    "seccion": seccion_actual,
                    "contenido": "\n".join(contenido_actual)
                })
                contenido_actual = []
            # El nuevo título pasa a ser el nombre de la sección actual
            seccion_actual = texto_limpio
        else:
            contenido_actual.append(texto_limpio)
            
    # Guardamos la última sección que quedó colgada
    if contenido_actual:
        secciones.append({
            "seccion": seccion_actual,
            "contenido": "\n".join(contenido_actual)
        })
        
    return secciones
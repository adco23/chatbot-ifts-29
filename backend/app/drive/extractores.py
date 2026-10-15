from pathlib import Path
from pypdf import PdfReader
from docx import Document
from app.config.message_loader import MESSAGES

def extract_text(file_path: str) -> list[dict]:
    """
    Extrae el texto de un PDF o DOCX separándolo por secciones.
    Devuelve una lista de diccionarios: [{'section': '...', 'content': '...'}]
    
    Agregar aquí los formatos a procesar (ej: Google Docs).
    No olvidar instalar las bibliotecas correspondientes.
    """
    path = Path(file_path)
    
    if not path.exists():
        error_msg = MESSAGES.get("text_extractor", {}).get("file_not_found", "Error: {path}")
        return [{"section": "Error", "content": error_msg.format(path=file_path)}]
        
    extension = path.suffix.lower()
    
    try:
        if extension == '.pdf':
            return _extract_from_pdf(file_path)
        elif extension == '.docx':
            return _extract_from_docx(file_path)
        else:
            error_msg = MESSAGES.get("text_extractor", {}).get("unsupported_format", "Error: {ext}")
            return [{"section": "Error", "content": error_msg.format(ext=extension)}]
            
    except Exception as e:
        error_msg = MESSAGES.get("text_extractor", {}).get("processing_error", "Error: {error}")
        return [{"section": "Error", "content": error_msg.format(error=str(e))}]

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
            
    full_content = "\n\n".join(extracted_text)
    
    # Devolvemos un único bloque general
    return [{"section": "General", "content": full_content}]

def _extract_from_docx(file_path: str) -> list[dict]:
    """
    Procesa archivos Word. Detecta los estilos 'Heading' (Títulos) 
    para separar el documento en secciones.
    """
    doc = Document(file_path)
    sections = []
    current_section = "General"
    current_content = []
    
    for paragraph in doc.paragraphs:
        clean_text = paragraph.text.strip()
        if not clean_text:
            continue
            
        # Si el estilo empieza con 'Heading' (Heading 1, Heading 2, etc.) o 'Título'
        if paragraph.style.name.startswith('Heading') or paragraph.style.name.startswith('Título'):
            # Guardamos la sección anterior si tenía texto
            if current_content:
                sections.append({
                    "section": current_section,
                    "content": "\n".join(current_content)
                })
                current_content = []
            # El nuevo título pasa a ser el nombre de la sección actual
            current_section = clean_text
        else:
            current_content.append(clean_text)
            
    # Guardamos la última sección que quedó colgada
    if current_content:
        sections.append({
            "section": current_section,
            "content": "\n".join(current_content)
        })
        
    return sections
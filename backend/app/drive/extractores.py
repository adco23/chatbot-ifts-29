"""Extracción PDF/DOCX compartida por archivos locales y descargas en memoria."""

from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from docx import Document
from pypdf import PdfReader

from app.config.message_loader import MESSAGES

_EXTENSION_POR_MIME = {
    "application/pdf": ".pdf",
    "application/vnd.google-apps.document": ".pdf",  # Drive lo exporta antes a PDF.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


class ErrorExtraccion(ValueError):
    """Un documento no contiene texto utilizable; no incluye el contenido original."""


def extraer_texto_desde_bytes(
    contenido: bytes, nombre_archivo: str, *, tipo_mime: str | None = None
) -> list[dict]:
    """Extrae secciones sin temporales y rechaza errores o documentos sin texto.

    Los bytes de Google Docs deben haber sido exportados a PDF por el cliente de Drive.
    A diferencia de la interfaz legada, los errores nunca se devuelven como contenido.
    """
    if not isinstance(contenido, bytes) or not contenido:
        raise ErrorExtraccion("El documento no contiene datos para extraer.")
    if not isinstance(nombre_archivo, str):
        raise ErrorExtraccion("El nombre del documento no es válido.")
    extension = _EXTENSION_POR_MIME.get(tipo_mime, Path(nombre_archivo).suffix.lower())
    if extension not in {".pdf", ".docx"}:
        raise ErrorExtraccion("El formato del documento no está soportado.")
    try:
        with BytesIO(contenido) as archivo:
            if extension == ".pdf":
                secciones = _extract_from_pdf(archivo)
            else:
                secciones = _extract_from_docx(archivo)
    except Exception:
        raise ErrorExtraccion("No se pudo extraer texto del documento.") from None
    if not any(seccion["content"].strip() for seccion in secciones):
        raise ErrorExtraccion("El documento no contiene texto extraíble.")
    return secciones


def extract_text(file_path: str) -> list[dict]:
    """Interfaz legada: extrae un archivo local y conserva las secciones de error."""
    path = Path(file_path)

    if not path.exists():
        error_msg = MESSAGES.get("text_extractor", {}).get(
            "file_not_found", "Error: {path}"
        )
        return [{"section": "Error", "content": error_msg.format(path=file_path)}]

    extension = path.suffix.lower()
    if extension not in {".pdf", ".docx"}:
        error_msg = MESSAGES.get("text_extractor", {}).get(
            "unsupported_format", "Error: {ext}"
        )
        return [{"section": "Error", "content": error_msg.format(ext=extension)}]
    try:
        return extraer_texto_desde_bytes(path.read_bytes(), path.name)
    except Exception:
        error_msg = MESSAGES.get("text_extractor", {}).get(
            "processing_error", "Error: {error}"
        )
        return [{
            "section": "Error",
            "content": error_msg.format(error="No se pudo leer o procesar el documento."),
        }]


def _extract_from_pdf(file_path: str | BinaryIO) -> list[dict]:
    """
    Procesa PDFs. Como pypdf no detecta estilos de título fácilmente,
    agrupa todo el contenido bajo la sección 'General'.
    """
    # El modo estricto evita advertencias de recuperación que pueden repetir bytes
    # de un encabezado inválido en los registros de la biblioteca.
    reader = PdfReader(file_path, strict=True)
    extracted_text = []

    for page in reader.pages:
        text = page.extract_text()
        if text:
            extracted_text.append(text.strip())

    full_content = "\n\n".join(extracted_text)

    # Devolvemos un único bloque general
    return [{"section": "General", "content": full_content}]


def _extract_from_docx(file_path: str | BinaryIO) -> list[dict]:
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
        if paragraph.style.name.startswith(
            "Heading"
        ) or paragraph.style.name.startswith("Título"):
            # Guardamos la sección anterior si tenía texto
            if current_content:
                sections.append(
                    {"section": current_section, "content": "\n".join(current_content)}
                )
                current_content = []
            # El nuevo título pasa a ser el nombre de la sección actual
            current_section = clean_text
        else:
            current_content.append(clean_text)

    # Guardamos la última sección que quedó colgada
    if current_content:
        sections.append(
            {"section": current_section, "content": "\n".join(current_content)}
        )

    return sections

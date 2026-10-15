import time
import hashlib
import os

# Ruta a message_loader.py
from app.config.message_loader import MESSAGES

# Función delegada al proveedor correspondiente (Gemini o HuggingFace)
from app.providers.embeddings.gemini import EmbeddingsGemini
from app.providers.embeddings.gestor import GestorEmbeddings

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document


def generate_file_hash(file_path: str) -> str:
    """Calcula la huella SHA-256 de un archivo para detectar si hubo cambios."""
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as file_bytes:
            for byte_block in iter(lambda: file_bytes.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except FileNotFoundError:
        return ""


def detect_file_status(file_path: str, current_hash: str, mock_db: dict) -> str:
    """Determina el estado del archivo comparando el hash actual con el guardado."""
    if file_path not in mock_db:
        return "new"

    stored_hash = mock_db[file_path]
    if current_hash == stored_hash:
        return "unchanged"

    return "modified"


def split_text_into_chunks(
    text: str, document_title: str, section_name: str
) -> list[Document]:
    """Divide el texto inyectando metadatos (título y sección)."""
    if not text.strip():
        return []

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len,
        separators=["\n\n", "\n", ".", " ", ""],
    )

    chunks = text_splitter.split_text(text)

    documents = []
    for chunk in chunks:
        metadata = {"document_title": document_title, "section": section_name}
        documents.append(Document(page_content=chunk, metadata=metadata))

    return documents


def update_document_status(file_id: str, status: str, db_client=None) -> bool:
    """Actualiza el estado general del documento."""
    valid_statuses = ["new", "modified", "unchanged", "observed"]

    if status not in valid_statuses:
        error_msg = MESSAGES.get("sincronizador", {}).get(
            "invalid_status", "Error: '{status}'"
        )
        print(error_msg.format(status=status))
        return False

    success_msg = MESSAGES.get("sincronizador", {}).get(
        "status_updated", "Status updated: {file_id}"
    )
    print(success_msg.format(file_id=file_id, status=status.upper()))
    return True


def sync_document_chunks(file_id: str, new_chunks: list[dict], db_client=None) -> bool:
    """Reemplaza los fragmentos de un documento."""
    if not new_chunks:
        msg = MESSAGES.get("sincronizador", {}).get(
            "no_new_chunks", "No chunks: {file_id}"
        )
        print(msg.format(file_id=file_id))
        return False

    del_msg = MESSAGES.get("sincronizador", {}).get(
        "deleting_old_chunks", "Deleting: {file_id}"
    )
    print(del_msg.format(file_id=file_id))

    ins_msg = MESSAGES.get("sincronizador", {}).get(
        "inserting_new_chunks", "Inserting for: {file_id}"
    )
    print(ins_msg.format(count=len(new_chunks), file_id=file_id))

    return True


def remove_expired_document(file_id: str, db_client=None) -> bool:
    """Elimina por completo un documento y sus fragmentos."""
    msg = MESSAGES.get("sincronizador", {}).get(
        "document_removed", "Removed: {file_id}"
    )
    print(msg.format(file_id=file_id))
    return True


# api_key = os.getenv("GEMINI_API_KEY", "tu-api-key-de-prueba")
# proveedor_gemini = EmbeddingsGemini(api_key=api_key)
# gestor_embeddings = GestorEmbeddings(proveedores=[proveedor_gemini])

api_key = os.getenv("GEMINI_API_KEY", "tu-api-key-de-prueba")
proveedor_gemini = EmbeddingsGemini(
    api_key=api_key,
    modelo="models/text-embedding-004",
    dimension=768,
    timeout_segundos=15.0,
)
gestor_embeddings = GestorEmbeddings(
    proveedores=[proveedor_gemini],
    reintentos=3,
    backoff_base_segundos=1.0,
    backoff_maximo_segundos=10.0,
    presupuesto_segundos=30.0,
)


async def run_full_sync(mock_db_drive: dict) -> dict:
    """
    Orquesta el proceso completo de sincronización.
    Mide el tiempo (Spike vs límite Serverless) y registra resultados y errores.
    """
    start_time = time.time()
    procesados = 0
    errores = 0

    print("Iniciando ciclo de sincronización (Spike T29)...")

    try:
        for file_path, current_hash in mock_db_drive.items():

            # Chequear estado
            status = detect_file_status(file_path, current_hash, mock_db=mock_db_drive)
            update_document_status(file_path, status)

            if status in ["new", "modified"]:
                # Simular extracción y chunking
                chunks = split_text_into_chunks(
                    f"Texto simulado de {file_path}", file_path, "General"
                )

                # Generar vectores esperando (await) al gestor
                embedded_chunks = []
                for chunk in chunks:
                    texto = (
                        chunk.page_content
                        if hasattr(chunk, "page_content")
                        else chunk["content"]
                    )
                    resultado = await gestor_embeddings.embed_query(texto)
                    embedded_chunks.append(resultado.vector)

                # Guardar en BD
                sync_document_chunks(file_path, embedded_chunks)
                procesados += 1

        # Registrar éxito final
        msg_exito = MESSAGES.get("sincronizador", {}).get(
            "sync_success", "Sincronización OK: {procesados} procesados."
        )
        print(msg_exito.format(procesados=procesados))

    except Exception as e:
        errores += 1
        msg_error = MESSAGES.get("sincronizador", {}).get(
            "unexpected_error", "Error en sincronización: {error}"
        )
        print(msg_error.format(error=str(e)))

    end_time = time.time()
    elapsed = end_time - start_time

    return {
        "status": "ok" if errores == 0 else "con_errores",
        "procesados": procesados,
        "errores": errores,
        "tiempo_segundos": round(elapsed, 2),
        "aviso_vercel": (
            "⚠️ Cerca del límite (10s)" if elapsed > 8.0 else "✅ Tiempo seguro"
        ),
    }

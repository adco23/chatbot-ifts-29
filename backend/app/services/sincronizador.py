import hashlib
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

# Ajustá esta ruta dependiendo de dónde hayas guardado message_loader.py y messages.json
from app.config.message_loader import MESSAGES

# Inicializamos el modelo de embeddings a nivel módulo
embedding_model = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

def generate_file_hash(file_path: str) -> str:
    """
    Calcula la huella SHA-256 de un archivo para detectar si hubo cambios en su contenido.
    """
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as file_bytes:
            for byte_block in iter(lambda: file_bytes.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except FileNotFoundError:
        return ""

def detect_file_status(file_path: str, current_hash: str, mock_db: dict) -> str:
    """
    Compara el hash actual con el guardado en la base de datos para determinar
    el estado del archivo ("new", "modified" o "unchanged").
    """
    if file_path not in mock_db:
        return "new"
    
    stored_hash = mock_db[file_path]
    if current_hash == stored_hash:
        return "unchanged"
    
    return "modified"

def split_text_into_chunks(text: str, document_title: str, section_name: str) -> list[Document]:
    """
    Divide el texto en fragmentos (chunks) inyectando los metadatos relevantes
    para no perder el contexto (título y sección).
    """
    if not text.strip():
        return []

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len,
        separators=["\n\n", "\n", ".", " ", ""]
    )
    
    chunks = text_splitter.split_text(text)
    
    documents = []
    for chunk in chunks:
        metadata = {
            "document_title": document_title,
            "section": section_name
        }
        documents.append(Document(page_content=chunk, metadata=metadata))
        
    return documents

def generate_embeddings(chunks: list[Document]) -> list[dict]:
    """
    Genera los vectores (embeddings) para una lista de fragmentos.
    Devuelve diccionarios listos para ser guardados en la base de datos.
    """
    if not chunks:
        return []

    embedded_chunks = []
    texts = [chunk.page_content for chunk in chunks]
    vectors = embedding_model.embed_documents(texts)
    
    for i, chunk in enumerate(chunks):
        embedded_chunks.append({
            "content": chunk.page_content,
            "metadata": chunk.metadata,
            "embedding": vectors[i]
        })
        
    return embedded_chunks

def update_document_status(file_id: str, status: str, db_client=None) -> bool:
    """
    Actualiza el estado general del documento en la base de datos.
    """
    valid_statuses = ["new", "modified", "unchanged", "observed"]
    
    if status not in valid_statuses:
        error_msg = MESSAGES.get("document_indexer", {}).get("invalid_status", "Error: {status}")
        print(error_msg.format(status=status))
        return False
        
    # TODO: Implementar llamada real a Supabase
    success_msg = MESSAGES.get("document_indexer", {}).get("status_updated", "Updated: {file_id}")
    print(success_msg.format(file_id=file_id, status=status.upper()))
    return True

def sync_document_chunks(file_id: str, new_chunks: list[dict], db_client=None) -> bool:
    """
    Reemplaza los fragmentos de un documento. 
    Primero elimina los fragmentos viejos asociados al file_id y luego inserta los nuevos.
    """
    if not new_chunks:
        msg = MESSAGES.get("document_indexer", {}).get("no_new_chunks", "No chunks: {file_id}")
        print(msg.format(file_id=file_id))
        return False
        
    del_msg = MESSAGES.get("document_indexer", {}).get("deleting_old_chunks", "Deleting: {file_id}")
    print(del_msg.format(file_id=file_id))
    
    ins_msg = MESSAGES.get("document_indexer", {}).get("inserting_new_chunks", "Inserting for: {file_id}")
    print(ins_msg.format(count=len(new_chunks), file_id=file_id))
    
    return True

def remove_expired_document(file_id: str, db_client=None) -> bool:
    """
    Elimina por completo un documento y todos sus fragmentos en cascada
    si se detecta que fue borrado del Drive o ya está vencido.
    """
    msg = MESSAGES.get("document_indexer", {}).get("document_removed", "Removed: {file_id}")
    print(msg.format(file_id=file_id))
    return True
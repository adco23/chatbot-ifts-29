from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document

# Inicializamos el modelo de embeddings a nivel módulo.
# Según la arquitectura, si a futuro cambian a Gemini o Cloudflare Workers AI,
# solo se reemplaza esta línea y la importación de arriba.
embedding_model = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

def generate_embeddings(chunks: list[Document]) -> list[dict]:
    """
    Genera los vectores (embeddings) para una lista de fragmentos de texto.
    Devuelve una lista de diccionarios listos para ser insertados en 
    la base de datos vectorial (ej. Supabase / PGVector).
    """
    if not chunks:
        return []

    embedded_chunks = []
    
    # 1. Extraemos solo el texto limpio de cada fragmento
    texts = [chunk.page_content for chunk in chunks]
    
    # 2. Le pedimos al modelo de IA que convierta los textos en vectores
    vectors = embedding_model.embed_documents(texts)
    
    # 3. Ensamblamos el paquete final con el texto, sus metadatos y su vector matemático
    for i, chunk in enumerate(chunks):
        embedded_chunks.append({
            "content": chunk.page_content,
            "metadata": chunk.metadata,
            "embedding": vectors[i]
        })
        
    return embedded_chunks
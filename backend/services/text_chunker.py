from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

def split_text_into_chunks(text: str, document_title: str, section_name: str = "General", chunk_size: int = 1000, chunk_overlap: int = 200) -> list[Document]:
    """
    Divide el texto en fragmentos (chunks) conservando el título del documento 
    y la sección a la que pertenecen como metadatos en cada fragmento.
    """
    if not text or text.startswith("ERROR"):
        return []

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", " ", ""]
    )
    
    # Cortamos el texto en strings crudos primero
    raw_chunks = text_splitter.split_text(text)
    
    # Armamos los objetos Document inyectando el título y la sección a CADA fragmento
    document_chunks = []
    for chunk in raw_chunks:
        doc = Document(
            page_content=chunk,
            metadata={
                "titulo": document_title,
                "seccion": section_name
            }
        )
        document_chunks.append(doc)
        
    return document_chunks
from typing import Any
from app.db.repositorio import buscar_en_cache_faq, incrementar_acierto_cache_faq
from app.providers.embeddings.gestor import GestorEmbeddings

async def procesar_cache_faq(
    mensaje: str,
    contexto: dict[str, Any],
    gestor_embeddings: GestorEmbeddings,
    configuracion: Any,
) -> dict[str, Any] | None:
    """
    Verifica si la consulta del usuario encuentra respuesta en la caché de preguntas frecuentes
    utilizando embeddings de Gemini (768 dimensiones - T12) y umbral semántico.
    """
    if not mensaje or not mensaje.strip():
        return None

    try:
        # 1. Generar el vector real de la consulta respetando T12 (768 dimensiones)
        resultado_embedding = await gestor_embeddings.embed_query(mensaje.strip())
        vector_consulta = getattr(resultado_embedding, "vector", None)
        
        # Validación estricta de dimensiones T12
        if not vector_consulta or len(vector_consulta) != 768:
            return None

        # Sanitizar parámetros del contexto para evitar errores de tipo en Supabase
        raw_materia = contexto.get("materia_id")
        materia_id = int(raw_materia) if raw_materia not in (None, "", 0, "0") else None

        raw_comision = contexto.get("comision")
        comision = str(raw_comision).strip() if raw_comision not in (None, "") else None

        # Umbral semántico real de producción (configurable o 0.68 por defecto)
        umbral = getattr(configuracion, "UMBRAL_CACHE_FAQ", 0.68)

        # 2. Buscar en la base de datos de preguntas frecuentes por similitud semántica
        resultado_cache = buscar_en_cache_faq(
            vector_consulta=vector_consulta,
            materia_id=materia_id,
            comision=comision,
            umbral=umbral,
        )

        if resultado_cache and resultado_cache.get("id"):
            # 3. Incrementar contador de aciertos
            incrementar_acierto_cache_faq(resultado_cache["id"])
            
            return {
                "estado": "respondida",
                "respuesta": resultado_cache.get("respuesta"),
                "fuentes": resultado_cache.get("fuentes", []),
                "confianza": float(resultado_cache.get("similitud", 1.0)),
                "umbral": umbral,
                "contexto": contexto,
            }
    except Exception as e:
        print(f"=== Error en caché FAQ: {e} ===")
        return None
        
    return None
-- =========================================================================
-- Migración 0006: Funciones para la Caché de Preguntas Frecuentes (Bloque 6)
-- =========================================================================

-- 1. Función RPC para buscar en la caché de preguntas frecuentes usando pgvector,
-- filtrando por similitud semántica, vigencia y contexto opcional.
CREATE OR REPLACE FUNCTION match_pregunta_frecuente(
    p_query_embedding vector,
    p_materia_id int4 DEFAULT NULL,
    p_comision varchar DEFAULT NULL,
    p_umbral float8 DEFAULT 0.68
)
RETURNS TABLE (
    id int8,
    pregunta_canonica varchar,
    respuesta varchar,
    materia_id int4,
    comision varchar,
    vigencia_hasta date,
    similitud float8
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        pf.id,
        pf.pregunta_canonica,
        pf.respuesta,
        pf.materia_id,
        pf.comision,
        pf.vigencia_hasta,
        -- Cálculo de similitud de Coseno (1 - distancia)
        (1 - (pf.embedding <=> p_query_embedding)) AS similitud
    FROM pregunta_frecuente pf
    WHERE 
        -- Validar umbral de similitud
        (1 - (pf.embedding <=> p_query_embedding)) >= p_umbral
        -- Validar que no haya expirado la vigencia (vigencia_hasta >= hoy)
        AND (pf.vigencia_hasta IS NULL OR pf.vigencia_hasta >= CURRENT_DATE)
        -- Validar contexto de materia (si se envía, debe coincidir o ser nulo/general)
        AND (p_materia_id IS NULL OR pf.materia_id IS NULL OR pf.materia_id = p_materia_id)
        -- Validar contexto de comisión (si se envía, debe coincidir o ser nulo)
        AND (p_comision IS NULL OR pf.comision IS NULL OR pf.comision ILIKE p_comision)
    ORDER BY similitud DESC
    LIMIT 1;
END;
$$;

-- 2. Función RPC para incrementar de forma atómica los aciertos de la caché (T44)
CREATE OR REPLACE FUNCTION incrementar_acierto_faq(p_id int8)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE pregunta_frecuente
    SET aciertos = COALESCE(aciertos, 0) + 1
    WHERE id = p_id;
END;
$$;
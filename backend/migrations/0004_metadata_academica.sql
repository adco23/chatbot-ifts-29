-- Aplicación MANUAL después de 0003. Sin columnas ni privilegios nuevos de tabla.
-- Metadata explícita: NULL elimina especificidad anterior; no se infiere del texto.
BEGIN;

CREATE FUNCTION public.actualizar_metadata_documento_sync(
    p_drive_id text,
    p_titulo text,
    p_materia_id integer,
    p_comision text,
    p_periodo text,
    p_drive_modified_time timestamptz,
    p_drive_md5_checksum text,
    p_propietario uuid
) RETURNS TABLE (documento_id integer, cambiado boolean)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog
AS $$
DECLARE
    v_documento_id integer;
    v_filas integer;
BEGIN
    PERFORM public.verificar_bloqueo_sync(p_propietario);
    IF p_drive_id IS NULL OR btrim(p_drive_id) = ''
       OR p_titulo IS NULL OR btrim(p_titulo) = '' THEN
        RAISE EXCEPTION 'Se requiere identidad y título.';
    END IF;
    IF p_periodo IS NOT NULL AND p_periodo !~ '^[0-9]{4}C[12]$' THEN
        RAISE EXCEPTION 'Período inválido.';
    END IF;
    IF p_comision IS NOT NULL AND
       (p_comision !~ '^[A-Z0-9]{1,32}$' OR p_comision = 'GENERAL') THEN
        RAISE EXCEPTION 'Comisión inválida; GENERAL se representa con NULL.';
    END IF;
    IF p_materia_id IS NULL AND p_comision IS NOT NULL THEN
        RAISE EXCEPTION 'Una comisión específica requiere materia.';
    END IF;
    IF p_drive_md5_checksum IS NOT NULL AND p_drive_md5_checksum !~ '^[a-fA-F0-9]{32}$' THEN
        RAISE EXCEPTION 'Checksum inválido.';
    END IF;
    -- La FK existente verifica materia_id. Una falla revierte toda la RPC.
    SELECT d.id INTO STRICT v_documento_id FROM public.documento AS d
    WHERE d.drive_id = p_drive_id FOR UPDATE;
    UPDATE public.documento AS d
    SET titulo = p_titulo, materia_id = p_materia_id,
        comision = p_comision, periodo = p_periodo,
        drive_modified_time = p_drive_modified_time,
        drive_md5_checksum = lower(p_drive_md5_checksum), estado = 'activo', vigente = true
    WHERE d.id = v_documento_id AND (
        d.titulo IS DISTINCT FROM p_titulo OR d.materia_id IS DISTINCT FROM p_materia_id
        OR d.comision IS DISTINCT FROM p_comision OR d.periodo IS DISTINCT FROM p_periodo
        OR d.drive_modified_time IS DISTINCT FROM p_drive_modified_time
        OR d.drive_md5_checksum IS DISTINCT FROM lower(p_drive_md5_checksum)
        OR d.estado IS DISTINCT FROM 'activo' OR d.vigente IS DISTINCT FROM true
    );
    GET DIAGNOSTICS v_filas = ROW_COUNT;
    RETURN QUERY SELECT v_documento_id, v_filas = 1;
END;
$$;

CREATE FUNCTION public.reemplazar_fragmentos_documento_academico(
    p_drive_id text,
    p_titulo text,
    p_fragmentos jsonb,
    p_materia_id integer,
    p_comision text,
    p_periodo text,
    p_drive_modified_time timestamptz,
    p_drive_md5_checksum text,
    p_propietario uuid
) RETURNS TABLE (
    documento_id integer, fragmentos_indexados integer,
    indexado_en timestamp without time zone
)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog
AS $$
DECLARE
    v_resultado record;
BEGIN
    -- Conserva validación de vectores, identidad, lease y reemplazo de 0002/0003.
    SELECT * INTO STRICT v_resultado
    FROM public.reemplazar_fragmentos_documento_versionado(
        p_drive_id, p_titulo, p_fragmentos,
        p_drive_modified_time, p_drive_md5_checksum, p_propietario
    );
    PERFORM public.actualizar_metadata_documento_sync(
        p_drive_id, p_titulo, p_materia_id, p_comision, p_periodo,
        p_drive_modified_time, p_drive_md5_checksum, p_propietario
    );
    RETURN QUERY SELECT v_resultado.documento_id, v_resultado.fragmentos_indexados,
                        v_resultado.indexado_en;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.actualizar_metadata_documento_sync(
    text, text, integer, text, text, timestamptz, text, uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.reemplazar_fragmentos_documento_academico(
    text, text, jsonb, integer, text, text, timestamptz, text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.actualizar_metadata_documento_sync(
    text, text, integer, text, text, timestamptz, text, uuid) TO anon;
GRANT EXECUTE ON FUNCTION public.reemplazar_fragmentos_documento_academico(
    text, text, jsonb, integer, text, text, timestamptz, text, uuid) TO anon;

NOTIFY pgrst, 'reload schema';
COMMIT;

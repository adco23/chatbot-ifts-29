-- Aplicación MANUAL, posterior a 0002. No modifica las migraciones anteriores.
BEGIN;

ALTER TABLE public.documento
    ADD COLUMN drive_modified_time timestamptz NULL,
    ADD COLUMN drive_md5_checksum text NULL;

-- Una única concesión, sin historial ni información documental.
-- Aplicar con un rol de migraciones confiable: será propietario de las dos
-- funciones DEFINER. No se crean roles ni se cambian permisos de documento/fragmento.
CREATE TABLE public.rag_sync_bloqueo (
    id integer PRIMARY KEY CHECK (id = 1),
    propietario uuid NULL,
    vence_en timestamptz NULL
);
INSERT INTO public.rag_sync_bloqueo (id) VALUES (1);

-- Incluye posibles privilegios por defecto de Supabase. El backend sólo usará RPC.
REVOKE ALL PRIVILEGES ON TABLE public.rag_sync_bloqueo FROM PUBLIC, anon;

CREATE FUNCTION public.gestionar_bloqueo_sync(p_propietario uuid, p_accion text)
RETURNS TABLE (adquirido boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_filas integer;
BEGIN
    IF p_propietario IS NULL THEN
        RAISE EXCEPTION 'Se requiere propietario del bloqueo.';
    END IF;
    IF p_accion = 'adquirir' THEN
        UPDATE public.rag_sync_bloqueo
        SET propietario = p_propietario,
            vence_en = pg_catalog.clock_timestamp() + interval '5 minutes'
        WHERE id = 1 AND (propietario IS NULL OR vence_en <= pg_catalog.clock_timestamp());
    ELSIF p_accion = 'renovar' THEN
        UPDATE public.rag_sync_bloqueo
        SET vence_en = pg_catalog.clock_timestamp() + interval '5 minutes'
        WHERE id = 1 AND propietario = p_propietario
          AND vence_en > pg_catalog.clock_timestamp();
    ELSIF p_accion = 'liberar' THEN
        UPDATE public.rag_sync_bloqueo SET propietario = NULL, vence_en = NULL
        WHERE id = 1 AND propietario = p_propietario;
    ELSE
        RAISE EXCEPTION 'Acción de bloqueo inválida.';
    END IF;
    GET DIAGNOSTICS v_filas = ROW_COUNT;
    RETURN QUERY SELECT v_filas = 1;
END;
$$;

-- Mantiene el bloqueo de fila hasta finalizar la transacción de escritura.
-- NULL sólo permite reindexación manual fuera de una sincronización en curso.
-- También requiere DEFINER: SELECT FOR UPDATE necesita privilegios sobre la tabla.
-- El llamador sigue siendo INVOKER después de esta comprobación.
CREATE FUNCTION public.verificar_bloqueo_sync(p_propietario uuid)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_bloqueo public.rag_sync_bloqueo%ROWTYPE;
BEGIN
    SELECT * INTO STRICT v_bloqueo FROM public.rag_sync_bloqueo WHERE id = 1 FOR UPDATE;
    IF p_propietario IS NULL THEN
        IF v_bloqueo.propietario IS NOT NULL
           AND v_bloqueo.vence_en > pg_catalog.clock_timestamp() THEN
            RAISE EXCEPTION 'Hay una sincronización en curso.';
        END IF;
    ELSIF v_bloqueo.propietario IS DISTINCT FROM p_propietario
          OR v_bloqueo.vence_en IS NULL
          OR v_bloqueo.vence_en <= pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'La concesión de sincronización no está vigente.';
    END IF;
END;
$$;

-- Reutiliza íntegramente validación, upsert, reemplazo y resumen de 0002.
-- Si falla cualquier UPDATE, también revierte el reemplazo de fragmentos.
CREATE FUNCTION public.reemplazar_fragmentos_documento_versionado(
    p_drive_id text,
    p_titulo text,
    p_fragmentos jsonb,
    p_drive_modified_time timestamptz DEFAULT NULL,
    p_drive_md5_checksum text DEFAULT NULL,
    p_propietario uuid DEFAULT NULL
) RETURNS TABLE (
    documento_id integer,
    fragmentos_indexados integer,
    indexado_en timestamp without time zone
)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog
AS $$
DECLARE
    v_resultado record;
BEGIN
    PERFORM public.verificar_bloqueo_sync(p_propietario);
    IF p_drive_md5_checksum IS NOT NULL AND p_drive_md5_checksum !~ '^[a-fA-F0-9]{32}$' THEN
        RAISE EXCEPTION 'Checksum inválido.';
    END IF;
    SELECT * INTO STRICT v_resultado
    FROM public.reemplazar_fragmentos_documento(p_drive_id, p_titulo, p_fragmentos);
    UPDATE public.documento AS d
    SET drive_modified_time = p_drive_modified_time,
        drive_md5_checksum = lower(p_drive_md5_checksum),
        estado = 'activo', vigente = true
    WHERE d.id = v_resultado.documento_id;
    RETURN QUERY SELECT v_resultado.documento_id, v_resultado.fragmentos_indexados,
                        v_resultado.indexado_en;
END;
$$;

CREATE FUNCTION public.actualizar_estado_documento_sync(
    p_drive_id text, p_activo boolean, p_propietario uuid, p_titulo text DEFAULT NULL
) RETURNS TABLE (documento_id integer, cambiado boolean)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog
AS $$
DECLARE
    v_documento_id integer;
    v_filas integer;
BEGIN
    IF p_propietario IS NULL OR p_activo IS NULL THEN
        RAISE EXCEPTION 'Se requiere una sincronización y estado válidos.';
    END IF;
    IF p_titulo IS NOT NULL AND btrim(p_titulo) = '' THEN
        RAISE EXCEPTION 'Título vacío.';
    END IF;
    PERFORM public.verificar_bloqueo_sync(p_propietario);
    SELECT d.id INTO STRICT v_documento_id FROM public.documento AS d
    WHERE d.drive_id = p_drive_id FOR UPDATE;
    UPDATE public.documento AS d
    SET estado = CASE WHEN p_activo THEN 'activo' ELSE 'inactivo' END,
        vigente = p_activo,
        titulo = COALESCE(p_titulo, d.titulo)
    WHERE d.id = v_documento_id
      AND (d.estado IS DISTINCT FROM CASE WHEN p_activo THEN 'activo' ELSE 'inactivo' END
           OR d.vigente IS DISTINCT FROM p_activo
           OR (p_titulo IS NOT NULL AND d.titulo IS DISTINCT FROM p_titulo));
    GET DIAGNOSTICS v_filas = ROW_COUNT;
    RETURN QUERY SELECT v_documento_id, v_filas = 1;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.gestionar_bloqueo_sync(uuid, text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.verificar_bloqueo_sync(uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.reemplazar_fragmentos_documento_versionado(
    text, text, jsonb, timestamptz, text, uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.actualizar_estado_documento_sync(
    text, boolean, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.gestionar_bloqueo_sync(uuid, text) TO anon;
GRANT EXECUTE ON FUNCTION public.verificar_bloqueo_sync(uuid) TO anon;
GRANT EXECUTE ON FUNCTION public.reemplazar_fragmentos_documento_versionado(
    text, text, jsonb, timestamptz, text, uuid) TO anon;
GRANT EXECUTE ON FUNCTION public.actualizar_estado_documento_sync(
    text, boolean, uuid, text) TO anon;

-- Fallar cerrado si privilegios heredados o un propietario inseguro impiden aislar
-- la tabla. REVOKE sobre anon no elimina permisos obtenidos mediante otros roles.
DO $permisos$
BEGIN
    IF pg_catalog.has_table_privilege('anon', 'public.rag_sync_bloqueo',
           'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
       OR pg_catalog.has_any_column_privilege('anon', 'public.rag_sync_bloqueo',
           'SELECT, INSERT, UPDATE, REFERENCES') THEN
        RAISE EXCEPTION 'anon conserva acceso directo a rag_sync_bloqueo; revisar roles heredados.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS p
        WHERE p.oid IN (
            'public.gestionar_bloqueo_sync(uuid,text)'::pg_catalog.regprocedure,
            'public.verificar_bloqueo_sync(uuid)'::pg_catalog.regprocedure
        ) AND pg_catalog.pg_has_role('anon', p.proowner, 'MEMBER')
    ) THEN
        RAISE EXCEPTION 'anon no puede pertenecer al rol propietario de las funciones del lock.';
    END IF;
END;
$permisos$;

NOTIFY pgrst, 'reload schema';
COMMIT;

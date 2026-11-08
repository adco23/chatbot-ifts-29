-- Aplicación MANUAL después de 0004. No modifica contratos de RPC anteriores.
BEGIN;
SET LOCAL search_path = pg_catalog, pg_temp;

ALTER TABLE public.documento ADD COLUMN tipo_documental varchar NULL;
ALTER TABLE public.documento ADD CONSTRAINT documento_tipo_documental_check
    CHECK (tipo_documental IN ('general', 'programa', 'cronograma'));

-- Backfill conservador: sólo títulos de la convención y metadata coherente.
-- NFKD + eliminación de tildes, guiones y espacios como el parser Python.
-- Usa el máximo por defecto de comisión (8); configuraciones más amplias deberán
-- completar el tipo mediante actualización de metadata. No se asumen desde SQL.
-- Los nombres cuya normalización no sea ASCII quedan sin inferir (NULL).
DO $backfill$
DECLARE
    d record;
    p text[];
    v_periodo text;
    v_comision text;
    v_clave text;
    v_tipo text;
    v_materia integer;
    v_cantidad integer;
BEGIN
    FOR d IN SELECT * FROM public.documento WHERE tipo_documental IS NULL LOOP
        p := string_to_array(regexp_replace(btrim(d.titulo), '\.(pdf|docx)$', '', 'i'), '__');
        SELECT array_agg(btrim(x) ORDER BY n) INTO p FROM unnest(p) WITH ORDINALITY AS a(x, n);
        IF EXISTS (SELECT 1 FROM unnest(p) AS x WHERE x = '' OR x ~ '[[:cntrl:]]') THEN
            CONTINUE;
        END IF;
        v_periodo := NULL;
        v_comision := NULL;
        v_tipo := NULL;
        IF cardinality(p) = 2 AND upper(p[1]) = 'GENERAL' THEN
            IF d.materia_id IS NULL AND d.comision IS NULL AND d.periodo IS NULL THEN
                v_tipo := 'general';
            END IF;
        ELSIF cardinality(p) = 3 AND upper(p[2]) = 'GENERAL'
              AND upper(p[1]) ~ '^[0-9]{4}C[12]$' THEN
            IF d.materia_id IS NULL AND d.comision IS NULL AND d.periodo = upper(p[1]) THEN
                v_tipo := 'general';
            END IF;
        ELSIF cardinality(p) = 4 AND upper(p[1]) ~ '^[0-9]{4}C[12]$'
              AND upper(p[2]) <> 'GENERAL' AND upper(p[3]) ~ '^COM-[A-Z0-9]{1,8}$'
              AND lower(p[4]) IN ('programa', 'cronograma') THEN
            v_periodo := upper(p[1]);
            v_comision := CASE WHEN upper(p[3]) = 'COM-GENERAL' THEN NULL
                              ELSE substr(upper(p[3]), 5) END;
            v_clave := btrim(regexp_replace(regexp_replace(
                lower(pg_catalog.normalize(replace(p[2], '-', ' '), 'NFKD')),
                U&'[\0300-\036f]', '', 'g'), '[[:space:]]+', ' ', 'g'));
            IF v_clave !~ '^[a-z0-9 ]+$' THEN
                CONTINUE;
            END IF;
            SELECT count(*), min(m.id) INTO v_cantidad, v_materia FROM public.materia AS m
            WHERE btrim(regexp_replace(regexp_replace(
                lower(pg_catalog.normalize(replace(m.nombre, '-', ' '), 'NFKD')),
                U&'[\0300-\036f]', '', 'g'), '[[:space:]]+', ' ', 'g')) = v_clave;
            IF v_cantidad = 1 AND d.materia_id = v_materia AND d.periodo = v_periodo
               AND d.comision IS NOT DISTINCT FROM v_comision THEN
                v_tipo := lower(p[4]);
            END IF;
        END IF;
        IF v_tipo IS NOT NULL THEN
            UPDATE public.documento SET tipo_documental = v_tipo WHERE id = d.id;
        END IF;
    END LOOP;
END;
$backfill$;

CREATE FUNCTION public.actualizar_metadata_documento_tipado(
    p_drive_id text, p_titulo text, p_materia_id integer, p_comision text, p_periodo text,
    p_drive_modified_time timestamptz, p_drive_md5_checksum text, p_propietario uuid,
    p_tipo_documental text
) RETURNS TABLE (documento_id integer, cambiado boolean)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog
AS $$
DECLARE
    v_resultado record;
    v_filas integer;
BEGIN
    IF p_tipo_documental IS NULL OR p_tipo_documental NOT IN ('general', 'programa', 'cronograma') THEN
        RAISE EXCEPTION 'Tipo documental inválido.';
    END IF;
    SELECT * INTO STRICT v_resultado FROM public.actualizar_metadata_documento_sync(
        p_drive_id, p_titulo, p_materia_id, p_comision, p_periodo,
        p_drive_modified_time, p_drive_md5_checksum, p_propietario
    );
    UPDATE public.documento AS d SET tipo_documental = p_tipo_documental
    WHERE d.id = v_resultado.documento_id AND d.tipo_documental IS DISTINCT FROM p_tipo_documental;
    GET DIAGNOSTICS v_filas = ROW_COUNT;
    RETURN QUERY SELECT v_resultado.documento_id, v_resultado.cambiado OR v_filas = 1;
END;
$$;

CREATE FUNCTION public.reemplazar_fragmentos_documento_tipado(
    p_drive_id text, p_titulo text, p_fragmentos jsonb,
    p_materia_id integer, p_comision text, p_periodo text,
    p_drive_modified_time timestamptz, p_drive_md5_checksum text, p_propietario uuid,
    p_tipo_documental text
) RETURNS TABLE (documento_id integer, fragmentos_indexados integer,
                 indexado_en timestamp without time zone)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog
AS $$
DECLARE
    v_resultado record;
BEGIN
    IF p_tipo_documental IS NULL OR p_tipo_documental NOT IN ('general', 'programa', 'cronograma') THEN
        RAISE EXCEPTION 'Tipo documental inválido.';
    END IF;
    SELECT * INTO STRICT v_resultado FROM public.reemplazar_fragmentos_documento_academico(
        p_drive_id, p_titulo, p_fragmentos, p_materia_id, p_comision, p_periodo,
        p_drive_modified_time, p_drive_md5_checksum, p_propietario
    );
    UPDATE public.documento AS d SET tipo_documental = p_tipo_documental
    WHERE d.id = v_resultado.documento_id;
    RETURN QUERY SELECT v_resultado.documento_id, v_resultado.fragmentos_indexados,
                        v_resultado.indexado_en;
END;
$$;

DO $migration$
DECLARE
    v_esquema text;
BEGIN
    SELECT n.nspname INTO STRICT v_esquema FROM pg_catalog.pg_extension AS e
    JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace WHERE e.extname = 'vector';
    EXECUTE pg_catalog.format($definition$
        CREATE FUNCTION public.match_fragmentos_academicos(
            p_query_embedding %1$I.vector,
            p_top_k integer DEFAULT 5,
            p_materia_id integer DEFAULT NULL,
            p_comision text DEFAULT NULL,
            p_periodo text DEFAULT NULL,
            -- Compatibilidad interna: sólo NULL/true. Siempre busca documentos vigentes.
            -- false es un error de argumento, no una búsqueda vacía.
            p_vigente boolean DEFAULT true,
            p_documento_ids integer[] DEFAULT NULL,
            p_tipo_documental text DEFAULT 'cronograma',
            p_modalidad text DEFAULT 'primaria'
        ) RETURNS TABLE (
            fragmento_id integer, documento_id integer, contenido text, orden integer,
            similitud double precision, titulo text, drive_id text, materia_id integer,
            comision text, periodo text, vigente boolean, tipo_documental text
        )
        LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog
        AS $rpc$
        DECLARE
            v_norma double precision;
            v_comision text := NULLIF(upper(btrim(p_comision)), '');
            v_periodo text := NULLIF(upper(btrim(p_periodo)), '');
        BEGIN
            IF p_vigente IS FALSE THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'p_vigente sólo admite NULL o true en retrieval académico.';
            END IF;
            IF p_comision IS NOT NULL AND (v_comision IS NULL OR v_comision !~ '^[A-Z0-9]{1,32}$') THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'Comisión inválida: se requieren entre 1 y 32 caracteres alfanuméricos.';
            END IF;
            IF p_periodo IS NOT NULL AND (v_periodo IS NULL OR v_periodo !~ '^[0-9]{4}C[12]$') THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'Período inválido: se requiere YYYYC1 o YYYYC2.';
            END IF;
            IF p_query_embedding IS NULL THEN
                RAISE EXCEPTION 'Se requiere un embedding de consulta.';
            END IF;
            v_norma := %1$I.vector_norm(p_query_embedding);
            IF %1$I.vector_dims(p_query_embedding) <> 768 OR v_norma IS NULL
               OR NOT (v_norma > 0 AND v_norma < 'Infinity'::double precision) THEN
                RAISE EXCEPTION 'Se requiere un vector finito no nulo de dimensión 768.';
            END IF;
            IF p_top_k IS NULL OR p_top_k < 1 THEN
                RAISE EXCEPTION 'Top K debe ser positivo.';
            END IF;
            IF p_tipo_documental IS NULL OR p_tipo_documental NOT IN ('programa', 'cronograma')
               OR p_modalidad IS NULL OR p_modalidad NOT IN ('primaria', 'comun', 'complementaria') THEN
                RAISE EXCEPTION 'Intención o modalidad inválida.';
            END IF;
            RETURN QUERY
            SELECT f.id, d.id, f.contenido, f.orden,
                (1.0 - (f.vector OPERATOR(%1$I.<=>) p_query_embedding))::double precision,
                d.titulo::text, d.drive_id::text, d.materia_id, d.comision::text,
                d.periodo::text, d.vigente, d.tipo_documental::text
            FROM public.fragmento AS f JOIN public.documento AS d ON d.id = f.documento_id
            WHERE lower(btrim(d.estado)) = 'activo' AND d.vigente IS TRUE
              AND f.vector IS NOT NULL AND %1$I.vector_norm(f.vector) > 0
              AND %1$I.vector_norm(f.vector) < 'Infinity'::double precision
              AND (p_documento_ids IS NULL OR d.id = ANY(p_documento_ids))
              AND (
                (p_modalidad = 'primaria'
                    AND d.tipo_documental = p_tipo_documental
                    AND p_materia_id IS NOT NULL AND d.materia_id = p_materia_id
                    AND v_comision IS NOT NULL AND upper(btrim(d.comision)) = v_comision
                    AND (v_periodo IS NULL OR upper(btrim(d.periodo)) = v_periodo))
                OR (p_modalidad = 'comun' AND p_tipo_documental = 'programa'
                    AND d.tipo_documental = 'programa'
                    AND p_materia_id IS NOT NULL AND d.materia_id = p_materia_id
                    AND d.comision IS NULL
                    AND (v_periodo IS NULL OR upper(btrim(d.periodo)) = v_periodo))
                OR (p_modalidad = 'complementaria'
                    AND d.tipo_documental IN (p_tipo_documental, 'general')
                    AND (d.materia_id IS NULL OR d.materia_id = p_materia_id)
                    AND (d.comision IS NULL OR upper(btrim(d.comision)) = v_comision)
                    AND (v_periodo IS NULL OR d.periodo IS NULL OR upper(btrim(d.periodo)) = v_periodo)
                    AND (d.tipo_documental = 'general' OR d.materia_id IS NULL OR d.comision IS NULL
                         OR (v_periodo IS NOT NULL AND d.periodo IS NULL)))
              )
            ORDER BY f.vector OPERATOR(%1$I.<=>) p_query_embedding ASC, f.id ASC
            LIMIT p_top_k;
        END;
        $rpc$;
    $definition$, v_esquema);
    EXECUTE pg_catalog.format(
        'REVOKE EXECUTE ON FUNCTION public.match_fragmentos_academicos(%I.vector, integer, integer, text, text, boolean, integer[], text, text) FROM PUBLIC', v_esquema);
    EXECUTE pg_catalog.format(
        'GRANT EXECUTE ON FUNCTION public.match_fragmentos_academicos(%I.vector, integer, integer, text, text, boolean, integer[], text, text) TO anon', v_esquema);
END;
$migration$;

REVOKE EXECUTE ON FUNCTION public.actualizar_metadata_documento_tipado(
    text, text, integer, text, text, timestamptz, text, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.actualizar_metadata_documento_tipado(
    text, text, integer, text, text, timestamptz, text, uuid, text) TO anon;
REVOKE EXECUTE ON FUNCTION public.reemplazar_fragmentos_documento_tipado(
    text, text, jsonb, integer, text, text, timestamptz, text, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.reemplazar_fragmentos_documento_tipado(
    text, text, jsonb, integer, text, text, timestamptz, text, uuid, text) TO anon;

NOTIFY pgrst, 'reload schema';
COMMIT;

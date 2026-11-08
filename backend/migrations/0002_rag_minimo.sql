-- Aplicación manual sobre el esquema existente. Este archivo no crea tablas,
-- no instala extensiones y no cambia RLS ni los roles de la aplicación.
-- T12: fragmento.vector contiene únicamente Gemini gemini-embedding-001 / 768.
-- El backend debe comprobar la procedencia antes de llamar a estas RPC.

BEGIN;

DO $migration$
DECLARE
    v_vector_schema text;
    v_vector_oid oid;
    v_column record;
    v_attribute record;
    v_table text;
    v_id_attnum smallint;
    v_drive_attnum smallint;
    v_function record;
    v_sequence record;
BEGIN
    SELECT n.nspname, t.oid
    INTO v_vector_schema, v_vector_oid
    FROM pg_catalog.pg_extension AS e
    JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
    JOIN pg_catalog.pg_type AS t ON t.typnamespace = n.oid AND t.typname = 'vector'
    WHERE e.extname = 'vector';

    IF v_vector_schema IS NULL THEN
        RAISE EXCEPTION 'Se requiere la extensión pgvector ya instalada.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'anon') THEN
        RAISE EXCEPTION 'No existe el rol anon esperado; no se crearán roles.';
    END IF;

    -- No asumir tipos o columnas de un esquema distinto al confirmado.
    FOR v_column IN
        SELECT * FROM (VALUES
            ('materia', 'id', 'integer', true),
            ('documento', 'id', 'integer', true),
            ('documento', 'titulo', 'textual', true),
            ('documento', 'drive_id', 'textual', true),
            ('documento', 'materia_id', 'integer', false),
            ('documento', 'comision', 'textual', false),
            ('documento', 'periodo', 'textual', false),
            ('documento', 'vigente', 'boolean', false),
            ('documento', 'estado', 'textual', false),
            ('documento', 'indexado_en', 'timestamp', false),
            ('fragmento', 'id', 'integer', true),
            ('fragmento', 'documento_id', 'integer', false),
            ('fragmento', 'contenido', 'textual', true),
            ('fragmento', 'vector', 'vector', false),
            ('fragmento', 'orden', 'integer', false)
        ) AS required(table_name, column_name, type_name, required_not_null)
    LOOP
        SELECT a.atttypid, a.atttypmod, a.attnotnull
        INTO v_attribute
        FROM pg_catalog.pg_attribute AS a
        WHERE a.attrelid = pg_catalog.to_regclass(
            pg_catalog.format('public.%I', v_column.table_name)
        )
          AND a.attname = v_column.column_name
          AND a.attnum > 0 AND NOT a.attisdropped;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Falta public.%.%.', v_column.table_name, v_column.column_name;
        END IF;

        IF (v_column.type_name = 'integer' AND v_attribute.atttypid <> 'integer'::regtype)
           OR (v_column.type_name = 'boolean' AND v_attribute.atttypid <> 'boolean'::regtype)
           OR (v_column.type_name = 'timestamp'
               AND v_attribute.atttypid <> 'timestamp without time zone'::regtype)
           OR (v_column.type_name = 'textual'
               AND v_attribute.atttypid NOT IN ('text'::regtype, 'varchar'::regtype))
           OR (v_column.type_name = 'vector'
               AND (v_attribute.atttypid <> v_vector_oid OR v_attribute.atttypmod <> 768))
           OR (v_column.required_not_null AND NOT v_attribute.attnotnull)
        THEN
            RAISE EXCEPTION 'Tipo, dimensión o nulabilidad incompatible en public.%.%.',
                v_column.table_name, v_column.column_name;
        END IF;
    END LOOP;

    FOREACH v_table IN ARRAY ARRAY['materia', 'documento', 'fragmento']
    LOOP
        SELECT a.attnum INTO v_id_attnum
        FROM pg_catalog.pg_attribute AS a
        WHERE a.attrelid = pg_catalog.to_regclass(pg_catalog.format('public.%I', v_table))
          AND a.attname = 'id' AND NOT a.attisdropped;

        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint AS c
            WHERE c.conrelid = pg_catalog.to_regclass(pg_catalog.format('public.%I', v_table))
              AND c.contype = 'p' AND c.conkey = ARRAY[v_id_attnum]
        ) THEN
            RAISE EXCEPTION 'Se requiere la PK public.%.id.', v_table;
        END IF;
    END LOOP;

    -- Las altas omiten id: exigir un generador existente, sin inventar secuencias.
    FOREACH v_table IN ARRAY ARRAY['documento', 'fragmento']
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_attribute AS a
            WHERE a.attrelid = pg_catalog.to_regclass(pg_catalog.format('public.%I', v_table))
              AND a.attname = 'id' AND NOT a.attisdropped
              AND (a.attidentity <> '' OR EXISTS (
                  SELECT 1 FROM pg_catalog.pg_attrdef AS d
                  WHERE d.adrelid = a.attrelid AND d.adnum = a.attnum
              ))
        ) THEN
            RAISE EXCEPTION 'public.%.id necesita un generador existente para las altas.', v_table;
        END IF;
    END LOOP;

    SELECT a.attnum INTO v_drive_attnum
    FROM pg_catalog.pg_attribute AS a
    WHERE a.attrelid = 'public.documento'::regclass AND a.attname = 'drive_id';

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_index AS i
        WHERE i.indrelid = 'public.documento'::regclass
          AND i.indisunique AND i.indisvalid AND i.indnkeyatts = 1
          AND i.indpred IS NULL AND i.indexprs IS NULL
          AND i.indkey[0] = v_drive_attnum
    ) THEN
        RAISE EXCEPTION 'Se requiere una clave única no parcial sobre documento.drive_id.';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint AS c
        WHERE c.contype = 'f' AND c.confrelid = 'public.fragmento'::regclass
    ) THEN
        RAISE EXCEPTION 'Hay FK entrantes a fragmento: revisar el reemplazo antes de aplicar.';
    END IF;

    -- CREATE OR REPLACE sólo puede reutilizar estas firmas exactas. Evitar overloads
    -- homónimos que vuelvan ambigua la selección de RPC en PostgREST.
    FOR v_function IN
        SELECT p.* FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN ('reemplazar_fragmentos_documento', 'match_fragmentos')
    LOOP
        IF v_function.prokind <> 'f' OR NOT (
            (v_function.proname = 'reemplazar_fragmentos_documento'
             AND v_function.pronargs = 3
             AND v_function.proargtypes[0] = 'text'::regtype
             AND v_function.proargtypes[1] = 'text'::regtype
             AND v_function.proargtypes[2] = 'jsonb'::regtype)
            OR
            (v_function.proname = 'match_fragmentos'
             AND v_function.pronargs = 7
             AND v_function.proargtypes[0] = v_vector_oid
             AND v_function.proargtypes[1] = 'integer'::regtype
             AND v_function.proargtypes[2] = 'integer'::regtype
             AND v_function.proargtypes[3] = 'text'::regtype
             AND v_function.proargtypes[4] = 'text'::regtype
             AND v_function.proargtypes[5] = 'boolean'::regtype
             AND v_function.proargtypes[6] = 'integer[]'::regtype)
        ) THEN
            RAISE EXCEPTION 'Firma homónima incompatible: %. No se crearán overloads.',
                v_function.oid::regprocedure;
        END IF;
    END LOOP;

    UPDATE public.documento
    SET comision = NULL
    WHERE comision IS NOT NULL
      AND (pg_catalog.btrim(comision) = ''
           OR pg_catalog.lower(pg_catalog.btrim(comision)) = 'general');
    ALTER TABLE public.documento ALTER COLUMN comision SET DEFAULT NULL;

    UPDATE public.documento
    SET estado = pg_catalog.lower(pg_catalog.btrim(estado))
    WHERE estado IS NOT NULL
      AND estado IS DISTINCT FROM pg_catalog.lower(pg_catalog.btrim(estado));
    ALTER TABLE public.documento ALTER COLUMN estado SET DEFAULT 'activo';
    -- No se modifica vigente ni se reemplazan estados NULL.

    EXECUTE pg_catalog.format($definition$
        CREATE OR REPLACE FUNCTION public.reemplazar_fragmentos_documento(
            p_drive_id text,
            p_titulo text,
            p_fragmentos jsonb
        ) RETURNS TABLE (
            documento_id integer,
            fragmentos_indexados integer,
            indexado_en timestamp without time zone
        )
        LANGUAGE plpgsql VOLATILE SECURITY INVOKER
        SET search_path = pg_catalog
        AS $rpc$
        DECLARE
            v_documento_id integer;
            v_cantidad integer;
            v_insertados integer;
            v_fragmento jsonb;
            v_componente jsonb;
            v_ordinal bigint;
            v_vector %1$I.vector;
            v_norma double precision;
            v_indexado_en timestamp without time zone;
        BEGIN
            IF p_drive_id IS NULL OR p_drive_id !~ '[^[:space:]]'
               OR p_titulo IS NULL OR p_titulo !~ '[^[:space:]]' THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'drive_id y titulo deben tener contenido.';
            END IF;
            IF p_fragmentos IS NULL OR pg_catalog.jsonb_typeof(p_fragmentos) <> 'array' THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'Los fragmentos deben ser un array JSON no vacío.';
            END IF;
            v_cantidad := pg_catalog.jsonb_array_length(p_fragmentos);
            IF v_cantidad = 0 THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'Un lote vacío no puede reemplazar el corpus existente.';
            END IF;

            -- Completar todas las validaciones antes del upsert y del DELETE.
            FOR v_fragmento, v_ordinal IN
                SELECT e.value, e.ordinality
                FROM pg_catalog.jsonb_array_elements(p_fragmentos)
                     WITH ORDINALITY AS e(value, ordinality)
            LOOP
                IF pg_catalog.jsonb_typeof(v_fragmento) IS DISTINCT FROM 'object'
                   OR pg_catalog.jsonb_typeof(v_fragmento -> 'contenido') IS DISTINCT FROM 'string'
                   OR (v_fragmento ->> 'contenido') !~ '[^[:space:]]'
                   OR pg_catalog.jsonb_typeof(v_fragmento -> 'orden') IS DISTINCT FROM 'number'
                   OR (v_fragmento ->> 'orden') IS DISTINCT FROM v_ordinal::text
                   OR pg_catalog.jsonb_typeof(v_fragmento -> 'vector') IS DISTINCT FROM 'array'
                THEN
                    RAISE EXCEPTION USING ERRCODE = '22023',
                        MESSAGE = 'Fragmento inválido: contenido, vector u orden consecutivo.';
                END IF;
                IF pg_catalog.jsonb_array_length(v_fragmento -> 'vector') <> 768 THEN
                    RAISE EXCEPTION USING ERRCODE = '22023',
                        MESSAGE = 'Todos los embeddings deben tener 768 componentes.';
                END IF;
                FOR v_componente IN
                    SELECT e.value
                    FROM pg_catalog.jsonb_array_elements(v_fragmento -> 'vector') AS e(value)
                LOOP
                    IF pg_catalog.jsonb_typeof(v_componente) IS DISTINCT FROM 'number' THEN
                        RAISE EXCEPTION USING ERRCODE = '22023',
                            MESSAGE = 'Los componentes del embedding deben ser números finitos.';
                    END IF;
                END LOOP;
                -- pgvector rechaza números no finitos o fuera del rango de float4.
                BEGIN
                    v_vector := (v_fragmento -> 'vector')::text::%1$I.vector;
                EXCEPTION
                    WHEN invalid_text_representation OR numeric_value_out_of_range
                         OR invalid_parameter_value THEN
                        RAISE EXCEPTION USING ERRCODE = '22023',
                            MESSAGE = 'Los componentes del embedding deben ser números finitos representables.';
                END;
                v_norma := %1$I.vector_norm(v_vector);
                IF %1$I.vector_dims(v_vector) <> 768
                   OR v_norma IS NULL
                   OR NOT (v_norma > 0 AND v_norma < 'Infinity'::double precision) THEN
                    RAISE EXCEPTION USING ERRCODE = '22023',
                        MESSAGE = 'El embedding debe ser finito, no nulo y de dimensión 768.';
                END IF;
            END LOOP;

            -- El índice UNIQUE de drive_id arbitra altas concurrentes. El upsert
            -- bloquea la fila hasta el final de la transacción, también al actualizar.
            INSERT INTO public.documento AS d
                (titulo, drive_id, materia_id, comision, periodo, vigente, estado)
            VALUES (pg_catalog.btrim(p_titulo), pg_catalog.btrim(p_drive_id),
                    NULL, NULL, NULL, true, 'activo')
            ON CONFLICT (drive_id) DO UPDATE SET titulo = EXCLUDED.titulo
            RETURNING d.id INTO v_documento_id;

            -- Un documento existente conserva id y todos sus metadatos de selección.
            DELETE FROM public.fragmento AS f WHERE f.documento_id = v_documento_id;
            INSERT INTO public.fragmento (documento_id, contenido, vector, orden)
            SELECT v_documento_id, e.value ->> 'contenido',
                   (e.value -> 'vector')::text::%1$I.vector,
                   (e.value ->> 'orden')::integer
            FROM pg_catalog.jsonb_array_elements(p_fragmentos) AS e(value);
            GET DIAGNOSTICS v_insertados = ROW_COUNT;
            IF v_insertados <> v_cantidad THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'No se insertó el lote completo de fragmentos.';
            END IF;

            v_indexado_en := pg_catalog.clock_timestamp() AT TIME ZONE 'UTC';
            UPDATE public.documento AS d
            SET indexado_en = v_indexado_en WHERE d.id = v_documento_id;
            RETURN QUERY SELECT v_documento_id, v_insertados, v_indexado_en;
        END;
        $rpc$;
    $definition$, v_vector_schema);

    EXECUTE pg_catalog.format($definition$
        CREATE OR REPLACE FUNCTION public.match_fragmentos(
            p_query_embedding %1$I.vector,
            p_top_k integer DEFAULT 5,
            p_materia_id integer DEFAULT NULL,
            p_comision text DEFAULT NULL,
            p_periodo text DEFAULT NULL,
            p_vigente boolean DEFAULT true,
            p_documento_ids integer[] DEFAULT NULL
        ) RETURNS TABLE (
            fragmento_id integer,
            documento_id integer,
            contenido text,
            orden integer,
            similitud double precision,
            titulo text,
            drive_id text,
            materia_id integer,
            comision text,
            periodo text,
            vigente boolean
        )
        LANGUAGE plpgsql STABLE SECURITY INVOKER
        SET search_path = pg_catalog
        AS $rpc$
        DECLARE
            v_norma double precision;
            v_comision text := NULLIF(pg_catalog.lower(pg_catalog.btrim(p_comision)), '');
            v_periodo text := NULLIF(pg_catalog.lower(pg_catalog.btrim(p_periodo)), '');
        BEGIN
            IF p_query_embedding IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'Se requiere un embedding de consulta.';
            END IF;
            v_norma := %1$I.vector_norm(p_query_embedding);
            IF %1$I.vector_dims(p_query_embedding) <> 768
               OR v_norma IS NULL
               OR NOT (v_norma > 0 AND v_norma < 'Infinity'::double precision) THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'La consulta debe tener un vector finito no nulo de dimensión 768.';
            END IF;
            IF p_top_k IS NULL OR p_top_k < 1 THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'p_top_k debe ser un entero positivo.';
            END IF;
            IF v_comision = 'general' THEN
                v_comision := NULL;
            END IF;

            RETURN QUERY
            SELECT f.id, d.id, f.contenido, f.orden,
                   (1.0 - (f.vector OPERATOR(%1$I.<=>) p_query_embedding))::double precision,
                   d.titulo::text, d.drive_id::text, d.materia_id,
                   d.comision::text, d.periodo::text, d.vigente
            FROM public.fragmento AS f
            JOIN public.documento AS d ON d.id = f.documento_id
            WHERE pg_catalog.lower(pg_catalog.btrim(d.estado)) = 'activo'
              AND f.vector IS NOT NULL AND %1$I.vector_norm(f.vector) > 0
              AND (p_materia_id IS NULL OR d.materia_id = p_materia_id OR d.materia_id IS NULL)
              AND (v_comision IS NULL OR d.comision IS NULL
                   OR pg_catalog.lower(pg_catalog.btrim(d.comision)) = v_comision)
              AND (v_periodo IS NULL OR d.periodo IS NULL
                   OR pg_catalog.lower(pg_catalog.btrim(d.periodo)) = v_periodo)
              AND (p_vigente IS NULL OR d.vigente = p_vigente)
              AND (p_documento_ids IS NULL OR d.id = ANY(p_documento_ids))
            ORDER BY f.vector OPERATOR(%1$I.<=>) p_query_embedding ASC, f.id ASC
            LIMIT p_top_k;
        END;
        $rpc$;
    $definition$, v_vector_schema);

    GRANT USAGE ON SCHEMA public TO anon;
    EXECUTE pg_catalog.format('GRANT USAGE ON SCHEMA %I TO anon', v_vector_schema);
    EXECUTE pg_catalog.format('GRANT USAGE ON TYPE %I.vector TO anon', v_vector_schema);
    GRANT SELECT ON public.materia TO anon;
    GRANT SELECT, INSERT, UPDATE ON public.documento TO anon;
    GRANT SELECT, INSERT, DELETE ON public.fragmento TO anon;

    FOR v_sequence IN
        SELECT n.nspname, c.relname
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE c.relkind = 'S' AND c.oid IN (
            SELECT pg_catalog.pg_get_serial_sequence('public.documento', 'id')::regclass
            UNION
            SELECT pg_catalog.pg_get_serial_sequence('public.fragmento', 'id')::regclass
            UNION
            -- También cubrir nextval de una secuencia existente sin OWNED BY.
            SELECT dep.refobjid
            FROM pg_catalog.pg_attribute AS a
            JOIN pg_catalog.pg_attrdef AS d
              ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            JOIN pg_catalog.pg_depend AS dep
              ON dep.classid = 'pg_catalog.pg_attrdef'::regclass AND dep.objid = d.oid
             AND dep.refclassid = 'pg_catalog.pg_class'::regclass
            WHERE a.attrelid IN ('public.documento'::regclass, 'public.fragmento'::regclass)
              AND a.attname = 'id' AND NOT a.attisdropped
        )
    LOOP
        EXECUTE pg_catalog.format('GRANT USAGE ON SEQUENCE %I.%I TO anon',
                                  v_sequence.nspname, v_sequence.relname);
    END LOOP;

    REVOKE ALL ON FUNCTION public.reemplazar_fragmentos_documento(text, text, jsonb) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.reemplazar_fragmentos_documento(text, text, jsonb) TO anon;
    EXECUTE pg_catalog.format(
        'REVOKE ALL ON FUNCTION public.match_fragmentos(%I.vector, integer, integer, text, text, boolean, integer[]) FROM PUBLIC',
        v_vector_schema
    );
    EXECUTE pg_catalog.format(
        'GRANT EXECUTE ON FUNCTION public.match_fragmentos(%I.vector, integer, integer, text, text, boolean, integer[]) TO anon',
        v_vector_schema
    );
END;
$migration$;

COMMIT;

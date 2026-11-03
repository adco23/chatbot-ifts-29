-- Aplicación MANUAL, separada de 0007_cache_faq_consistente.sql.
-- Antes de aplicar: configurar SUPABASE_BACKEND_KEY sólo en FastAPI/CLI y
-- comprobar el acceso con esa credencial privada. No configurar claves de
-- backend en React ni en variables VITE_*. 0007 permite probar FAQ previamente.
-- Esta migración cambia permisos, no datos, respuestas ni contratos del RAG.
BEGIN;
SET LOCAL search_path = pg_catalog, pg_temp;

DO $seguridad$
DECLARE
    v_tabla text;
    v_columnas text;
    v_rol text;
    v_secuencia record;
    v_funcion record;
    v_esquema_vector text;
    v_funciones text[] := ARRAY[
        'reemplazar_fragmentos_documento',
        'match_fragmentos',
        'gestionar_bloqueo_sync',
        'verificar_bloqueo_sync',
        'reemplazar_fragmentos_documento_versionado',
        'actualizar_estado_documento_sync',
        'actualizar_metadata_documento_sync',
        'reemplazar_fragmentos_documento_academico',
        'actualizar_metadata_documento_tipado',
        'reemplazar_fragmentos_documento_tipado',
        'match_fragmentos_academicos',
        'snapshot_documento_faq',
        'fuentes_faq_vigentes',
        'obtener_fuentes_faq',
        'obtener_fuentes_cache_faq',
        'guardar_faq_curada',
        'match_pregunta_frecuente',
        'incrementar_acierto_faq',
        'invalidar_faq_documento'
    ];
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = 'service_role' AND rolbypassrls
    ) THEN
        RAISE EXCEPTION 'Se requiere el rol backend service_role con BYPASSRLS.';
    END IF;
    IF pg_catalog.to_regprocedure('public.guardar_faq_curada(jsonb,jsonb)') IS NULL THEN
        RAISE EXCEPTION 'Aplicar primero 0007_cache_faq_consistente.sql.';
    END IF;
    FOREACH v_rol IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF pg_catalog.pg_has_role(v_rol, 'service_role', 'MEMBER') THEN
            RAISE EXCEPTION 'Un rol público no puede pertenecer al rol privado del backend.';
        END IF;
    END LOOP;

    SELECT n.nspname INTO STRICT v_esquema_vector
    FROM pg_catalog.pg_extension AS e
    JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
    WHERE e.extname = 'vector';
    GRANT USAGE ON SCHEMA public TO service_role;
    EXECUTE pg_catalog.format('GRANT USAGE ON SCHEMA %I TO service_role', v_esquema_vector);
    EXECUTE pg_catalog.format('GRANT USAGE ON TYPE %I.vector TO service_role', v_esquema_vector);
    GRANT SELECT ON public.materia TO service_role;

    FOREACH v_tabla IN ARRAY ARRAY[
        'documento', 'fragmento', 'pregunta_frecuente', 'pregunta_fuente'
    ] LOOP
        EXECUTE pg_catalog.format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', v_tabla);
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON TABLE public.%I FROM PUBLIC, anon, authenticated', v_tabla);
        -- Un REVOKE de tabla no retira concesiones previas por columna.
        SELECT pg_catalog.string_agg(pg_catalog.quote_ident(a.attname), ', ' ORDER BY a.attnum)
        INTO v_columnas
        FROM pg_catalog.pg_attribute AS a
        WHERE a.attrelid = pg_catalog.to_regclass('public.' || v_tabla)
          AND a.attnum > 0 AND NOT a.attisdropped;
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES (%s) ON TABLE public.%I FROM PUBLIC, anon, authenticated',
            v_columnas, v_tabla);
        -- Reemplazar también las ACL amplias ya existentes del backend.
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON TABLE public.%I FROM service_role', v_tabla);
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES (%s) ON TABLE public.%I FROM service_role',
            v_columnas, v_tabla);
    END LOOP;

    -- Documento se conserva en una baja lógica; no se concede DELETE.
    GRANT SELECT, INSERT, UPDATE ON public.documento TO service_role;
    GRANT SELECT, INSERT, DELETE ON public.fragmento TO service_role;
    GRANT SELECT, INSERT, UPDATE ON public.pregunta_frecuente TO service_role;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.pregunta_fuente TO service_role;

    FOR v_secuencia IN
        SELECT DISTINCT n.nspname, c.relname
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE c.relkind = 'S' AND c.oid IN (
            SELECT pg_catalog.pg_get_serial_sequence('public.' || t.nombre, 'id')::regclass
            FROM (VALUES ('documento'), ('fragmento'), ('pregunta_frecuente'),
                         ('pregunta_fuente')) AS t(nombre)
            UNION
            -- También secuencias nextval existentes sin OWNED BY.
            SELECT dep.refobjid
            FROM pg_catalog.pg_attribute AS a
            JOIN pg_catalog.pg_attrdef AS d
              ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            JOIN pg_catalog.pg_depend AS dep
              ON dep.classid = 'pg_catalog.pg_attrdef'::regclass AND dep.objid = d.oid
             AND dep.refclassid = 'pg_catalog.pg_class'::regclass
            WHERE a.attrelid IN ('public.documento'::regclass, 'public.fragmento'::regclass,
                  'public.pregunta_frecuente'::regclass, 'public.pregunta_fuente'::regclass)
              AND a.attname = 'id' AND NOT a.attisdropped
        )
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON SEQUENCE %I.%I FROM PUBLIC, anon, authenticated, service_role',
            v_secuencia.nspname, v_secuencia.relname);
        EXECUTE pg_catalog.format(
            'GRANT USAGE, SELECT ON SEQUENCE %I.%I TO service_role',
            v_secuencia.nspname, v_secuencia.relname);
        FOREACH v_rol IN ARRAY ARRAY['anon', 'authenticated'] LOOP
            IF pg_catalog.has_sequence_privilege(v_rol,
                pg_catalog.format('%I.%I', v_secuencia.nspname, v_secuencia.relname),
                'USAGE, SELECT, UPDATE') THEN
                RAISE EXCEPTION 'Un rol público conserva privilegios heredados sobre una secuencia.';
            END IF;
        END LOOP;
    END LOOP;

    -- El lease sigue encapsulado en sus RPC DEFINER existentes.
    REVOKE ALL PRIVILEGES ON TABLE public.rag_sync_bloqueo
        FROM PUBLIC, anon, authenticated, service_role;
    SELECT pg_catalog.string_agg(pg_catalog.quote_ident(a.attname), ', ' ORDER BY a.attnum)
    INTO v_columnas FROM pg_catalog.pg_attribute AS a
    WHERE a.attrelid = 'public.rag_sync_bloqueo'::regclass
      AND a.attnum > 0 AND NOT a.attisdropped;
    EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES (%s) ON TABLE public.rag_sync_bloqueo '
        'FROM PUBLIC, anon, authenticated, service_role', v_columnas);

    -- Incluye todas las sobrecargas de estos nombres, sin tocar RPC ajenas al bloque.
    FOR v_funcion IN
        SELECT p.oid, p.proowner, n.nspname, p.proname,
               pg_catalog.pg_get_function_identity_arguments(p.oid) AS argumentos
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = ANY(v_funciones) AND p.prokind = 'f'
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON FUNCTION %I.%I(%s) FROM PUBLIC, anon, authenticated',
            v_funcion.nspname, v_funcion.proname, v_funcion.argumentos);
        EXECUTE pg_catalog.format(
            'GRANT EXECUTE ON FUNCTION %I.%I(%s) TO service_role',
            v_funcion.nspname, v_funcion.proname, v_funcion.argumentos);
        FOREACH v_rol IN ARRAY ARRAY['anon', 'authenticated'] LOOP
            IF pg_catalog.pg_has_role(v_rol, v_funcion.proowner, 'MEMBER') THEN
                RAISE EXCEPTION 'Un rol público pertenece al propietario de una RPC restringida.';
            END IF;
            IF pg_catalog.has_function_privilege(v_rol, v_funcion.oid, 'EXECUTE') THEN
                RAISE EXCEPTION 'Un rol público conserva ejecución heredada de una RPC restringida.';
            END IF;
        END LOOP;
    END LOOP;

    FOREACH v_tabla IN ARRAY ARRAY[
        'documento', 'fragmento', 'pregunta_frecuente', 'pregunta_fuente', 'rag_sync_bloqueo'
    ] LOOP
        FOREACH v_rol IN ARRAY ARRAY['anon', 'authenticated'] LOOP
            IF pg_catalog.has_table_privilege(v_rol, 'public.' || v_tabla,
                   'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
               OR pg_catalog.has_any_column_privilege(v_rol, 'public.' || v_tabla,
                   'SELECT, INSERT, UPDATE, REFERENCES') THEN
                RAISE EXCEPTION 'Un rol público conserva acceso heredado a una tabla restringida.';
            END IF;
        END LOOP;
    END LOOP;
END;
$seguridad$;

NOTIFY pgrst, 'reload schema';
COMMIT;

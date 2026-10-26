-- MANUAL, después de 0003 y sin otra sincronización en curso.
-- Datos sintéticos, permisos anon y rollback de todos los objetos/datos de prueba.
BEGIN;

CREATE FUNCTION public.__prueba_fallo_sync_0003() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.drive_id = '__prueba_sync_0003__'
       AND NEW.drive_modified_time = '2030-01-01T00:00:00Z'::timestamptz THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Fallo sintético posterior al reemplazo';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER __prueba_fallo_sync_0003 BEFORE UPDATE ON public.documento
    FOR EACH ROW EXECUTE FUNCTION public.__prueba_fallo_sync_0003();

SET LOCAL ROLE anon;
DO $$
DECLARE
    v_owner uuid := '11111111-1111-4111-8111-111111111111';
    v_otro uuid := '22222222-2222-4222-8222-222222222222';
    v_vector jsonb := jsonb_set(to_jsonb(array_fill(0::double precision, ARRAY[768])), '{0}', '1');
    v_fragmentos jsonb;
    v_id integer;
    v_fragmento_id integer;
    v_cantidad integer;
    v_esquema text;
    v_funcion text;
BEGIN
    FOREACH v_funcion IN ARRAY ARRAY[
        'public.gestionar_bloqueo_sync(uuid,text)',
        'public.verificar_bloqueo_sync(uuid)',
        'public.reemplazar_fragmentos_documento_versionado(text,text,jsonb,timestamptz,text,uuid)',
        'public.actualizar_estado_documento_sync(text,boolean,uuid,text)'
    ] LOOP
        IF NOT pg_catalog.has_function_privilege('anon', v_funcion, 'EXECUTE')
           OR pg_catalog.has_function_privilege('public', v_funcion, 'EXECUTE') THEN
            RAISE EXCEPTION 'Permisos EXECUTE incorrectos en %.', v_funcion;
        END IF;
    END LOOP;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS p
        WHERE p.oid IN ('public.gestionar_bloqueo_sync(uuid,text)'::pg_catalog.regprocedure,
                       'public.verificar_bloqueo_sync(uuid)'::pg_catalog.regprocedure)
          AND (NOT p.prosecdef
               OR NOT COALESCE(p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp'], false))
    ) THEN
        RAISE EXCEPTION 'Las funciones del lock requieren DEFINER y search_path seguro.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS p
        WHERE p.oid IN (
            'public.reemplazar_fragmentos_documento_versionado(text,text,jsonb,timestamptz,text,uuid)'
                ::pg_catalog.regprocedure,
            'public.actualizar_estado_documento_sync(text,boolean,uuid,text)'::pg_catalog.regprocedure
        ) AND p.prosecdef
    ) THEN
        RAISE EXCEPTION 'Las escrituras documentales deben permanecer INVOKER.';
    END IF;
    -- Verificar permisos efectivos, incluidos PUBLIC, herencia y permisos por columna.
    IF pg_catalog.has_table_privilege('anon', 'public.rag_sync_bloqueo',
           'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
       OR pg_catalog.has_any_column_privilege('anon', 'public.rag_sync_bloqueo',
           'SELECT, INSERT, UPDATE, REFERENCES') THEN
        RAISE EXCEPTION 'anon tiene acceso directo al lock.';
    END IF;
    -- Cada sentencia debe fallar realmente con 42501, sin modificar la concesión.
    BEGIN
        PERFORM propietario FROM public.rag_sync_bloqueo WHERE id = 1;
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'anon pudo leer el lock';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE public.rag_sync_bloqueo SET propietario = v_otro WHERE id = 1;
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'anon pudo cambiar propietario';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE public.rag_sync_bloqueo
        SET vence_en = pg_catalog.clock_timestamp() + interval '1 day' WHERE id = 1;
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'anon pudo cambiar vencimiento';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    IF NOT (SELECT adquirido FROM public.gestionar_bloqueo_sync(v_owner, 'adquirir')) THEN
        RAISE EXCEPTION 'Hay una sincronización en curso; ejecutar esta prueba en reposo.';
    END IF;
    IF (SELECT adquirido FROM public.gestionar_bloqueo_sync(v_otro, 'adquirir')) THEN
        RAISE EXCEPTION 'Se permitió un segundo propietario.';
    END IF;
    IF (SELECT adquirido FROM public.gestionar_bloqueo_sync(v_otro, 'renovar'))
       OR (SELECT adquirido FROM public.gestionar_bloqueo_sync(v_otro, 'liberar')) THEN
        RAISE EXCEPTION 'Otro propietario pudo renovar o liberar la concesión.';
    END IF;
    IF NOT (SELECT adquirido FROM public.gestionar_bloqueo_sync(v_owner, 'renovar')) THEN
        RAISE EXCEPTION 'anon no pudo renovar mediante RPC.';
    END IF;
    v_fragmentos := jsonb_build_array(jsonb_build_object(
        'contenido', 'Contenido sintético original', 'vector', v_vector, 'orden', 1));
    SELECT documento_id INTO STRICT v_id
    FROM public.reemplazar_fragmentos_documento_versionado(
        '__prueba_sync_0003__', 'Documento sintético', v_fragmentos,
        '2026-10-23T00:00:00Z', repeat('a', 32), v_owner);
    SELECT id INTO STRICT v_fragmento_id FROM public.fragmento WHERE documento_id = v_id;

    -- Forzar error DESPUÉS del reemplazo; deben sobrevivir identidad, título y fragmento.
    BEGIN
        PERFORM * FROM public.reemplazar_fragmentos_documento_versionado(
            '__prueba_sync_0003__', 'Título que no debe persistir', v_fragmentos,
            '2030-01-01T00:00:00Z', repeat('b', 32), v_owner);
        RAISE EXCEPTION 'No se produjo el fallo forzado.';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    IF NOT EXISTS (SELECT 1 FROM public.documento WHERE id = v_id
        AND titulo = 'Documento sintético' AND drive_md5_checksum = repeat('a', 32)
        AND drive_modified_time = '2026-10-23T00:00:00Z'::timestamptz)
       OR NOT EXISTS (SELECT 1 FROM public.fragmento
                      WHERE id = v_fragmento_id AND documento_id = v_id) THEN
        RAISE EXCEPTION 'El reemplazo y su metadata no fueron atómicos.';
    END IF;

    PERFORM * FROM public.actualizar_estado_documento_sync('__prueba_sync_0003__', false, v_owner);
    IF NOT EXISTS (SELECT 1 FROM public.documento
                   WHERE id = v_id AND estado = 'inactivo' AND vigente = false)
       OR NOT EXISTS (SELECT 1 FROM public.fragmento WHERE id = v_fragmento_id) THEN
        RAISE EXCEPTION 'La baja lógica no conservó la trazabilidad.';
    END IF;
    IF (SELECT cambiado FROM public.actualizar_estado_documento_sync(
        '__prueba_sync_0003__', false, v_owner)) THEN
        RAISE EXCEPTION 'La segunda baja no fue idempotente.';
    END IF;
    SELECT n.nspname INTO STRICT v_esquema FROM pg_catalog.pg_extension e
    JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'vector';
    EXECUTE format('SELECT count(*) FROM public.match_fragmentos($1::%I.vector,
                    5, NULL, NULL, NULL, true, ARRAY[$2])', v_esquema)
        INTO v_cantidad USING v_vector::text, v_id;
    IF v_cantidad <> 0 THEN RAISE EXCEPTION 'El RAG recuperó un documento inactivo.'; END IF;

    PERFORM * FROM public.actualizar_estado_documento_sync('__prueba_sync_0003__', true, v_owner);
    EXECUTE format('SELECT count(*) FROM public.match_fragmentos($1::%I.vector,
                    5, NULL, NULL, NULL, true, ARRAY[$2])', v_esquema)
        INTO v_cantidad USING v_vector::text, v_id;
    IF v_cantidad <> 1 THEN RAISE EXCEPTION 'Falló la reactivación.'; END IF;

END;
$$;

-- Sólo el rol privilegiado que ejecuta esta prueba puede simular el vencimiento.
-- No se agrega una RPC administrativa ni se restaura UPDATE a anon para probarlo.
RESET ROLE;
UPDATE public.rag_sync_bloqueo
SET vence_en = pg_catalog.clock_timestamp() - interval '1 second'
WHERE id = 1 AND propietario = '11111111-1111-4111-8111-111111111111'::uuid;
SET LOCAL ROLE anon;
DO $$
DECLARE
    v_owner uuid := '11111111-1111-4111-8111-111111111111';
    v_otro uuid := '22222222-2222-4222-8222-222222222222';
BEGIN
    -- Vencimiento y fencing: un ejecutor viejo ya no puede escribir ni liberar al nuevo.
    IF (SELECT adquirido FROM public.gestionar_bloqueo_sync(v_owner, 'renovar')) THEN
        RAISE EXCEPTION 'Se renovó una concesión vencida.';
    END IF;
    IF NOT (SELECT adquirido FROM public.gestionar_bloqueo_sync(v_otro, 'adquirir')) THEN
        RAISE EXCEPTION 'No se recuperó la concesión vencida.';
    END IF;
    BEGIN
        PERFORM public.verificar_bloqueo_sync(v_owner);
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Se permitió escribir al ejecutor viejo';
    EXCEPTION WHEN raise_exception THEN NULL;
    END;
    IF (SELECT adquirido FROM public.gestionar_bloqueo_sync(v_owner, 'liberar')) THEN
        RAISE EXCEPTION 'El ejecutor viejo liberó la concesión nueva.';
    END IF;
    IF NOT (SELECT adquirido FROM public.gestionar_bloqueo_sync(v_otro, 'liberar')) THEN
        RAISE EXCEPTION 'anon no pudo liberar mediante RPC.';
    END IF;
    IF NOT (SELECT adquirido FROM public.gestionar_bloqueo_sync(v_owner, 'adquirir')) THEN
        RAISE EXCEPTION 'No se pudo volver a adquirir después de liberar.';
    END IF;
END;
$$;
ROLLBACK;

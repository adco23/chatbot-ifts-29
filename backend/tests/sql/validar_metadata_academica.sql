-- MANUAL, después de aplicar 0004. Datos sintéticos, sesión administrativa.
-- No ejecutado por pytest. No usa proveedores externos. Todo termina en ROLLBACK.
BEGIN;

DO $prueba$
DECLARE
    v_materia integer;
    v_materia_inexistente integer;
    v_resultado record;
    v_id integer;
    v_fragmento integer;
    v_fecha timestamp without time zone;
    v_fragmentos jsonb;
    v_cambiado boolean;
    v_propietario uuid := '00000000-0000-4000-8000-000000000004';
BEGIN
    IF NOT (SELECT adquirido FROM public.gestionar_bloqueo_sync(v_propietario, 'adquirir')) THEN
        RAISE EXCEPTION 'Hay un sync en curso: no continuar esta validación.';
    END IF;
    INSERT INTO public.materia(codigo, nombre) VALUES ('__TEST_METADATA_0004__', 'Materia sintética 0004')
    RETURNING id INTO v_materia;
    SELECT jsonb_build_array(jsonb_build_object('contenido', 'Evidencia sintética 0004',
        'orden', 1, 'vector', jsonb_agg(CASE WHEN n = 1 THEN 1.0 ELSE 0.0 END ORDER BY n)))
    INTO v_fragmentos FROM generate_series(1, 768) AS n;
    SELECT * INTO STRICT v_resultado FROM public.reemplazar_fragmentos_documento_academico(
        '__TEST_DRIVE_METADATA_0004__', '2026C2__Materia-sintetica-0004__COM-F__Cronograma.pdf',
        v_fragmentos, v_materia, 'F', '2026C2', '2026-10-25T00:00:00Z', repeat('a', 32), v_propietario
    );
    v_id := v_resultado.documento_id;
    v_fecha := v_resultado.indexado_en;
    SELECT f.id INTO STRICT v_fragmento FROM public.fragmento AS f WHERE f.documento_id = v_id;

    SELECT cambiado INTO STRICT v_cambiado FROM public.actualizar_metadata_documento_sync(
        '__TEST_DRIVE_METADATA_0004__', 'GENERAL__Ingreso.pdf', NULL, NULL, NULL,
        '2026-10-26T00:00:00Z', repeat('a', 32), v_propietario
    );
    IF NOT v_cambiado OR NOT EXISTS (
        SELECT 1 FROM public.documento AS d WHERE d.id = v_id AND d.materia_id IS NULL
        AND d.comision IS NULL AND d.periodo IS NULL AND d.indexado_en = v_fecha
    ) OR NOT EXISTS (SELECT 1 FROM public.fragmento AS f WHERE f.id = v_fragmento) THEN
        RAISE EXCEPTION 'La actualización de metadata cambió identidad, índice o no limpió NULLs.';
    END IF;

    -- Forzar error de FK al final del reemplazo; deben revertirse título y fragmentos.
    SELECT n INTO STRICT v_materia_inexistente FROM generate_series(-1, -1000, -1) AS n
    WHERE NOT EXISTS (SELECT 1 FROM public.materia AS m WHERE m.id = n) LIMIT 1;
    BEGIN
        PERFORM public.reemplazar_fragmentos_documento_academico(
            '__TEST_DRIVE_METADATA_0004__', 'No debe persistir', v_fragmentos,
            v_materia_inexistente, 'F', '2026C2', '2026-10-27T00:00:00Z', repeat('b', 32), v_propietario
        );
        RAISE EXCEPTION 'Se esperaba foreign_key_violation.';
    EXCEPTION WHEN foreign_key_violation THEN
        NULL;
    END;
    IF NOT EXISTS (SELECT 1 FROM public.documento AS d WHERE d.id = v_id
        AND d.titulo = 'GENERAL__Ingreso.pdf' AND d.indexado_en = v_fecha)
       OR NOT EXISTS (SELECT 1 FROM public.fragmento AS f WHERE f.id = v_fragmento) THEN
        RAISE EXCEPTION 'El reemplazo académico no revirtió íntegramente.';
    END IF;
    IF NOT (SELECT adquirido FROM public.gestionar_bloqueo_sync(v_propietario, 'liberar')) THEN
        RAISE EXCEPTION 'No se pudo liberar el lease sintético.';
    END IF;
END;
$prueba$;

-- Comprobar ejecución con el rol real sin acceso directo al lock.
SET LOCAL ROLE anon;
SELECT * FROM public.reemplazar_fragmentos_documento_academico(
    '__TEST_DRIVE_ANON_0004__', 'GENERAL__Anon.pdf',
    (SELECT jsonb_build_array(jsonb_build_object('contenido', 'Prueba anon', 'orden', 1,
        'vector', jsonb_agg(CASE WHEN n = 1 THEN 1.0 ELSE 0.0 END ORDER BY n)))
     FROM generate_series(1, 768) AS n),
    NULL, NULL, NULL, '2026-10-25T00:00:00Z', repeat('a', 32), NULL
);
SELECT * FROM public.actualizar_metadata_documento_sync(
    '__TEST_DRIVE_ANON_0004__', 'GENERAL__Anon-renombrado.pdf', NULL, NULL, '2026C2',
    '2026-10-26T00:00:00Z', repeat('a', 32), NULL
);
DO $permisos$
BEGIN
    IF has_table_privilege(current_user, 'public.rag_sync_bloqueo', 'SELECT,UPDATE')
       OR has_any_column_privilege(current_user, 'public.rag_sync_bloqueo', 'SELECT,UPDATE') THEN
        RAISE EXCEPTION 'anon tiene acceso directo al lock.';
    END IF;
END;
$permisos$;
RESET ROLE;
ROLLBACK;

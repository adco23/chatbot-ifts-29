-- MANUAL después de 0005, en una sesión administrativa. Nunca invocado por pytest.
-- Datos sintéticos y ROLLBACK; no llama proveedores ni sincroniza Drive.
BEGIN;
DO $datos$
DECLARE
    v_materia integer;
    v_fragmentos jsonb;
    v_item record;
    v_owner uuid := '00000000-0000-4000-8000-000000000005';
BEGIN
    IF NOT (SELECT adquirido FROM public.gestionar_bloqueo_sync(v_owner, 'adquirir')) THEN
        RAISE EXCEPTION 'Hay una sincronización en curso; no continuar.';
    END IF;
    INSERT INTO public.materia(codigo, nombre) VALUES ('__TEST_0005__', 'Materia sintética 0005')
    RETURNING id INTO v_materia;
    SELECT jsonb_build_array(jsonb_build_object('contenido', 'Evidencia sintética 0005',
        'orden', 1, 'vector', jsonb_agg(CASE WHEN n = 1 THEN 1.0 ELSE 0.0 END ORDER BY n)))
    INTO v_fragmentos FROM generate_series(1, 768) AS n;
    FOR v_item IN SELECT * FROM (VALUES
        (1, 'cronograma', 'F', '2026C2'), (2, 'programa', 'F', '2026C2'),
        (3, 'programa', NULL, '2026C2'), (4, 'general', NULL, NULL),
        (5, 'cronograma', 'F', NULL), (6, 'cronograma', 'F', '2026C1'),
        (7, 'cronograma', 'F', '2026C2'), (8, 'cronograma', 'F', '2026C2')
    ) AS x(numero, tipo, comision, periodo) ORDER BY numero LOOP
        PERFORM public.reemplazar_fragmentos_documento_tipado(
            '__TEST_0005_' || v_item.numero, 'Documento sintético ' || v_item.numero,
            v_fragmentos, CASE WHEN v_item.tipo = 'general' THEN NULL ELSE v_materia END,
            v_item.comision, v_item.periodo, '2026-10-26T00:00:00Z', repeat('a', 32), v_owner, v_item.tipo
        );
    END LOOP;
    UPDATE public.documento SET estado = 'inactivo' WHERE drive_id = '__TEST_0005_7';
    UPDATE public.documento SET vigente = false WHERE drive_id = '__TEST_0005_8';
END;
$datos$;

SET LOCAL ROLE anon;
DO $pruebas$
DECLARE
    v_materia integer;
    v_ids integer[];
    v_corpus integer[];
    v_cronograma integer;
    v_programa integer;
    v_comun integer;
    v_general integer;
    v_sin_periodo integer;
    v_otro_periodo integer;
    v_vector text;
    v_esquema text;
    v_sql text;
    v_caso record;
    v_fecha timestamp without time zone;
    v_fragmento integer;
BEGIN
    SELECT id INTO STRICT v_materia FROM public.materia WHERE codigo = '__TEST_0005__';
    SELECT array_agg(id ORDER BY id) INTO v_corpus FROM public.documento
    WHERE drive_id LIKE '__TEST_0005_%';
    SELECT id, indexado_en INTO STRICT v_cronograma, v_fecha FROM public.documento WHERE drive_id = '__TEST_0005_1';
    SELECT id INTO STRICT v_programa FROM public.documento WHERE drive_id = '__TEST_0005_2';
    SELECT id INTO STRICT v_comun FROM public.documento WHERE drive_id = '__TEST_0005_3';
    SELECT id INTO STRICT v_general FROM public.documento WHERE drive_id = '__TEST_0005_4';
    SELECT id INTO STRICT v_sin_periodo FROM public.documento WHERE drive_id = '__TEST_0005_5';
    SELECT id INTO STRICT v_otro_periodo FROM public.documento WHERE drive_id = '__TEST_0005_6';
    SELECT id INTO STRICT v_fragmento FROM public.fragmento WHERE documento_id = v_cronograma;
    SELECT '[' || string_agg(CASE WHEN n = 1 THEN '1' ELSE '0' END, ',' ORDER BY n) || ']'
    INTO v_vector FROM generate_series(1, 768) AS n;
    SELECT n.nspname INTO STRICT v_esquema FROM pg_catalog.pg_extension AS e
    JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace WHERE e.extname = 'vector';
    v_sql := pg_catalog.format('SELECT array_agg(documento_id ORDER BY documento_id)
        FROM public.match_fragmentos_academicos($1::%I.vector, 50, $2, $3, $4, $8, $5, $6, $7)', v_esquema);

    EXECUTE v_sql INTO v_ids USING v_vector, v_materia, 'F', '2026C2', v_corpus, 'cronograma', 'primaria', true;
    IF v_ids IS DISTINCT FROM ARRAY[v_cronograma] THEN
        RAISE EXCEPTION 'Horario F debe excluir Programa, General, sin período, otro período e inactivos.';
    END IF;
    EXECUTE v_sql INTO v_ids USING v_vector, v_materia, 'A', '2026C2', v_corpus, 'cronograma', 'primaria', true;
    IF v_ids IS NOT NULL THEN RAISE EXCEPTION 'La comisión A no tiene evidencia primaria.'; END IF;
    EXECUTE v_sql INTO v_ids USING v_vector, v_materia, 'F', '2026C2', v_corpus, 'programa', 'primaria', true;
    IF v_ids IS DISTINCT FROM ARRAY[v_programa] THEN RAISE EXCEPTION 'Programa específico incorrecto.'; END IF;
    EXECUTE v_sql INTO v_ids USING v_vector, v_materia, NULL::text, '2026C2', v_corpus, 'programa', 'comun', true;
    IF v_ids IS DISTINCT FROM ARRAY[v_comun] THEN RAISE EXCEPTION 'Programa común incorrecto.'; END IF;
    EXECUTE v_sql INTO v_ids USING v_vector, v_materia, 'F', '2026C2', v_corpus, 'cronograma', 'complementaria', true;
    IF v_ids IS DISTINCT FROM ARRAY[v_general, v_sin_periodo] THEN
        RAISE EXCEPTION 'Complementos deben excluir Programa y otros períodos explícitos.';
    END IF;
    EXECUTE v_sql INTO v_ids USING v_vector, v_materia, 'F', NULL::text, v_corpus, 'cronograma', 'primaria', true;
    IF v_ids IS DISTINCT FROM ARRAY[v_cronograma, v_sin_periodo, v_otro_periodo] THEN
        RAISE EXCEPTION 'No debe inventarse período cuando no se solicita.';
    END IF;

    -- NULL no amplía la vigencia: sigue devolviendo exclusivamente documentos vigentes.
    -- Se conserva la normalización de mayúsculas y espacios exteriores.
    EXECUTE v_sql INTO v_ids USING v_vector, v_materia, ' f ', ' 2026c2 ', v_corpus,
        'cronograma', 'primaria', NULL::boolean;
    IF v_ids IS DISTINCT FROM ARRAY[v_cronograma] THEN
        RAISE EXCEPTION 'NULL/normalización alteró la selección vigente.';
    END IF;
    -- El límite de 32 caracteres es válido aunque no exista esa comisión.
    EXECUTE v_sql INTO v_ids USING v_vector, v_materia, repeat('A', 32), '2026C2', v_corpus,
        'cronograma', 'primaria', true;
    IF v_ids IS NOT NULL THEN RAISE EXCEPTION 'La comisión sintética no existe.'; END IF;

    FOR v_caso IN SELECT * FROM (VALUES
        ('F', '2026C2', false), ('', '2026C2', true), ('   ', '2026C2', true),
        ('F-', '2026C2', true), ('Ñ', '2026C2', true), (repeat('A', 33), '2026C2', true),
        ('F', '', true), ('F', '   ', true), ('F', '2026C3', true),
        ('F', '26C2', true), ('F', '2026 C2', true), ('F', '２０２６C2', true)
    ) AS x(comision, periodo, vigente) LOOP
        BEGIN
            EXECUTE v_sql INTO v_ids USING v_vector, v_materia, v_caso.comision, v_caso.periodo,
                v_corpus, 'cronograma', 'primaria', v_caso.vigente;
            RAISE EXCEPTION 'Se esperaba invalid_parameter_value para argumentos inválidos.';
        EXCEPTION WHEN invalid_parameter_value THEN
            NULL;
        END;
    END LOOP;

    -- Cambiar sólo el tipo debe conservar identidad, fecha e IDs de fragmento.
    PERFORM public.actualizar_metadata_documento_tipado(
        '__TEST_0005_1', 'Documento sintético 1', v_materia, 'F', '2026C2',
        '2026-10-26T00:00:00Z', repeat('a', 32), '00000000-0000-4000-8000-000000000005', 'programa'
    );
    IF NOT EXISTS (SELECT 1 FROM public.documento WHERE id = v_cronograma
        AND tipo_documental = 'programa' AND indexado_en = v_fecha)
        OR NOT EXISTS (SELECT 1 FROM public.fragmento WHERE id = v_fragmento) THEN
        RAISE EXCEPTION 'El cambio de tipo alteró el índice.';
    END IF;
END;
$pruebas$;
SELECT * FROM public.gestionar_bloqueo_sync('00000000-0000-4000-8000-000000000005', 'liberar');
RESET ROLE;
ROLLBACK;

-- Validación MANUAL. No forma parte de pytest y no debe ejecutarse automáticamente.
-- Requiere 0002_rag_minimo.sql aplicada, una materia existente y una sesión que pueda
-- crear un trigger en fragmento y hacer SET ROLE anon (por ejemplo, el propietario).
-- Usa vectores sintéticos de norma 1 para verificar SQL, no para indexar documentación.
-- Todas las filas, funciones auxiliares, permisos temporales y el trigger se revierten.
-- PostgreSQL no revierte los incrementos de secuencias: ejecutar en una base de pruebas.
-- Si la herramienta interrumpe ante un error, ejecutar ROLLBACK en la misma sesión.

BEGIN;

CREATE TEMP TABLE rag_validacion_contexto (
    clave text PRIMARY KEY,
    valor text NOT NULL
) ON COMMIT DROP;

CREATE FUNCTION pg_temp.rag_assert(p_condicion boolean, p_mensaje text)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog
AS $assert$
BEGIN
    IF p_condicion IS DISTINCT FROM true THEN
        RAISE EXCEPTION USING MESSAGE = p_mensaje;
    END IF;
END;
$assert$;

-- Envolver las llamadas de prueba para no asumir el esquema de la extensión.
CREATE FUNCTION pg_temp.rag_match(
    p_vector text,
    p_top_k integer DEFAULT 5,
    p_materia_id integer DEFAULT NULL,
    p_comision text DEFAULT NULL,
    p_periodo text DEFAULT NULL,
    p_vigente boolean DEFAULT true,
    p_documento_ids integer[] DEFAULT NULL
)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog
AS $match$
DECLARE
    v_schema text;
    v_resultado jsonb;
BEGIN
    SELECT n.nspname INTO STRICT v_schema
    FROM pg_catalog.pg_extension AS e
    JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
    WHERE e.extname = 'vector';
    EXECUTE pg_catalog.format(
        'SELECT COALESCE(jsonb_agg(to_jsonb(r) - ''ordinality'' ORDER BY r.ordinality), ''[]''::jsonb)
         FROM public.match_fragmentos($1::%I.vector, $2, $3, $4, $5, $6, $7) WITH ORDINALITY AS r',
        v_schema
    ) INTO v_resultado
    USING p_vector, p_top_k, p_materia_id, p_comision, p_periodo, p_vigente, p_documento_ids;
    RETURN v_resultado;
END;
$match$;

DO $fixtures$
DECLARE
    v_prefijo text := '__rag_sql_' || pg_catalog.md5(
        pg_catalog.clock_timestamp()::text || pg_catalog.random()::text
    );
    v_vector jsonb;
    v_ortogonal jsonb;
    v_opuesto jsonb;
    v_cero jsonb;
    v_lote jsonb;
    v_nombre text;
    v_materia integer;
    v_documentos integer[] := ARRAY[]::integer[];
    v_documento integer;
    v_resultado record;
    v_anterior record;
    v_schema text;
    v_temp_schema text;
    v_coincide boolean;
BEGIN
    SELECT m.id INTO v_materia FROM public.materia AS m ORDER BY m.id LIMIT 1;
    PERFORM pg_temp.rag_assert(v_materia IS NOT NULL,
        'Se requiere al menos una materia oficial existente; esta prueba no inventa semillas.');

    SELECT pg_catalog.jsonb_agg(CASE WHEN i = 1 THEN 1 ELSE 0 END ORDER BY i),
           pg_catalog.jsonb_agg(CASE WHEN i = 2 THEN 1 ELSE 0 END ORDER BY i),
           pg_catalog.jsonb_agg(CASE WHEN i = 1 THEN -1 ELSE 0 END ORDER BY i),
           pg_catalog.jsonb_agg(0 ORDER BY i)
    INTO v_vector, v_ortogonal, v_opuesto, v_cero
    FROM pg_catalog.generate_series(1, 768) AS g(i);

    INSERT INTO pg_temp.rag_validacion_contexto (clave, valor) VALUES
        ('prefijo', v_prefijo), ('vector', v_vector::text), ('cero', v_cero::text),
        ('materia', v_materia::text);

    FOREACH v_nombre IN ARRAY ARRAY[
        'general', 'especifico', 'otra_comision', 'vencido',
        'vigencia_null', 'estado_null', 'observado'
    ]
    LOOP
        v_lote := pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
            'contenido', 'Contenido ' || v_nombre,
            'vector', CASE WHEN v_nombre = 'otra_comision' THEN v_opuesto ELSE v_vector END,
            'orden', 1
        ));
        IF v_nombre = 'general' THEN
            v_lote := v_lote || pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
                'contenido', 'Contenido ortogonal', 'vector', v_ortogonal, 'orden', 2
            ));
        END IF;
        SELECT * INTO STRICT v_resultado
        FROM public.reemplazar_fragmentos_documento(v_prefijo || '_' || v_nombre,
                                                    'Título ' || v_nombre, v_lote);
        v_documento := v_resultado.documento_id;
        v_documentos := pg_catalog.array_append(v_documentos, v_documento);
        INSERT INTO pg_temp.rag_validacion_contexto (clave, valor)
        VALUES (v_nombre, v_documento::text);
        PERFORM pg_temp.rag_assert(v_resultado.indexado_en IS NOT NULL,
                                  'El reemplazo debe devolver indexado_en.');
        PERFORM pg_temp.rag_assert(
            v_resultado.fragmentos_indexados = pg_catalog.jsonb_array_length(v_lote),
            'La cantidad devuelta debe coincidir con el lote.'
        );
    END LOOP;
    INSERT INTO pg_temp.rag_validacion_contexto (clave, valor)
    VALUES ('documentos', pg_catalog.array_to_string(v_documentos, ','));

    UPDATE public.documento SET materia_id = v_materia, comision = ' Comisión A ', periodo = ' 2026_1 '
    WHERE id = (SELECT valor::integer FROM pg_temp.rag_validacion_contexto WHERE clave = 'especifico');
    UPDATE public.documento SET materia_id = v_materia, comision = 'Comisión B',
                               periodo = '2026_2', estado = ' Activo '
    WHERE id = (SELECT valor::integer FROM pg_temp.rag_validacion_contexto WHERE clave = 'otra_comision');
    UPDATE public.documento SET vigente = false
    WHERE id = (SELECT valor::integer FROM pg_temp.rag_validacion_contexto WHERE clave = 'vencido');
    UPDATE public.documento SET vigente = NULL
    WHERE id = (SELECT valor::integer FROM pg_temp.rag_validacion_contexto WHERE clave = 'vigencia_null');
    UPDATE public.documento SET estado = NULL
    WHERE id = (SELECT valor::integer FROM pg_temp.rag_validacion_contexto WHERE clave = 'estado_null');
    UPDATE public.documento SET estado = 'observado'
    WHERE id = (SELECT valor::integer FROM pg_temp.rag_validacion_contexto WHERE clave = 'observado');

    -- Repetir por drive_id conserva el documento, sin duplicados ni pérdida de filtros.
    SELECT d.* INTO STRICT v_anterior FROM public.documento AS d
    WHERE d.id = (SELECT valor::integer FROM pg_temp.rag_validacion_contexto WHERE clave = 'especifico');
    v_lote := pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
        'contenido', 'Contenido específico actualizado', 'vector', v_vector, 'orden', 1
    ));
    FOR v_documento IN 1..2 LOOP
        SELECT * INTO STRICT v_resultado
        FROM public.reemplazar_fragmentos_documento(v_anterior.drive_id,
                                                    'Título actualizado', v_lote);
        PERFORM pg_temp.rag_assert(v_resultado.documento_id = v_anterior.id,
                                  'Reindexar debe preservar documento.id.');
    END LOOP;
    SELECT ROW(d.materia_id, d.comision, d.periodo, d.vigente, d.estado)
           IS NOT DISTINCT FROM
           ROW(v_anterior.materia_id, v_anterior.comision, v_anterior.periodo,
               v_anterior.vigente, v_anterior.estado)
    INTO v_coincide FROM public.documento AS d WHERE d.id = v_anterior.id;
    PERFORM pg_temp.rag_assert(v_coincide, 'El upsert debe preservar los metadatos existentes.');
    PERFORM pg_temp.rag_assert(
        (SELECT count(*) = 1 FROM public.documento WHERE drive_id = v_anterior.drive_id)
        AND (SELECT count(*) = 1 FROM public.fragmento WHERE documento_id = v_anterior.id),
        'La reindexación debe ser idempotente en cantidad de documentos y fragmentos.'
    );

    -- Simular filas legacy: ni vector NULL ni vector cero deben recuperarse.
    SELECT n.nspname INTO STRICT v_schema
    FROM pg_catalog.pg_extension AS e
    JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace WHERE e.extname = 'vector';
    SELECT valor::integer INTO v_documento
    FROM pg_temp.rag_validacion_contexto WHERE clave = 'general';
    INSERT INTO public.fragmento (documento_id, contenido, vector, orden)
    VALUES (v_documento, 'Legacy sin vector', NULL, 3);
    EXECUTE pg_catalog.format(
        'INSERT INTO public.fragmento (documento_id, contenido, vector, orden)
         VALUES ($1, ''Legacy vector cero'', $2::%I.vector, 4)', v_schema
    ) USING v_documento, v_cero::text;

    -- SET ROLE anon debe poder usar los auxiliares y el contexto de esta sesión.
    SELECT nspname INTO STRICT v_temp_schema FROM pg_catalog.pg_namespace
    WHERE oid = pg_catalog.pg_my_temp_schema();
    EXECUTE pg_catalog.format('GRANT USAGE ON SCHEMA %I TO anon', v_temp_schema);
    GRANT SELECT, INSERT, UPDATE ON TABLE pg_temp.rag_validacion_contexto TO anon;
    GRANT EXECUTE ON FUNCTION pg_temp.rag_assert(boolean, text) TO anon;
    GRANT EXECUTE ON FUNCTION pg_temp.rag_match(text, integer, integer, text, text, boolean, integer[]) TO anon;
END;
$fixtures$;

CREATE FUNCTION pg_temp.rag_forzar_fallo()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog
AS $trigger$
BEGIN
    IF NEW.contenido = '__rag_fallo_forzado__' THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'rag_validacion_fallo_forzado';
    END IF;
    RETURN NEW;
END;
$trigger$;

CREATE TRIGGER rag_validacion_fallo_insert
BEFORE INSERT ON public.fragmento
FOR EACH ROW EXECUTE FUNCTION pg_temp.rag_forzar_fallo();

SET LOCAL ROLE anon;

DO $checks$
DECLARE
    v_vector text;
    v_cero text;
    v_prefijo text;
    v_materia integer;
    v_otra_materia integer;
    v_documentos integer[];
    v_general integer;
    v_especifico integer;
    v_vencido integer;
    v_nulo integer;
    v_rows jsonb;
    v_resultado record;
    v_antes record;
    v_fragmentos_antes jsonb;
    v_coincide boolean;
    v_error boolean;
    v_lote jsonb;
BEGIN
    SELECT valor INTO v_vector FROM pg_temp.rag_validacion_contexto WHERE clave = 'vector';
    SELECT valor INTO v_cero FROM pg_temp.rag_validacion_contexto WHERE clave = 'cero';
    SELECT valor INTO v_prefijo FROM pg_temp.rag_validacion_contexto WHERE clave = 'prefijo';
    SELECT valor::integer INTO v_materia FROM pg_temp.rag_validacion_contexto WHERE clave = 'materia';
    v_otra_materia := CASE WHEN v_materia = 2147483647 THEN v_materia - 1 ELSE v_materia + 1 END;
    SELECT pg_catalog.string_to_array(valor, ',')::integer[] INTO v_documentos
    FROM pg_temp.rag_validacion_contexto WHERE clave = 'documentos';
    SELECT valor::integer INTO v_general FROM pg_temp.rag_validacion_contexto WHERE clave = 'general';
    SELECT valor::integer INTO v_especifico FROM pg_temp.rag_validacion_contexto WHERE clave = 'especifico';
    SELECT valor::integer INTO v_vencido FROM pg_temp.rag_validacion_contexto WHERE clave = 'vencido';
    SELECT valor::integer INTO v_nulo FROM pg_temp.rag_validacion_contexto WHERE clave = 'vigencia_null';

    PERFORM pg_temp.rag_assert(current_user = 'anon', 'Las comprobaciones deben ejecutarse como anon.');
    v_rows := pg_temp.rag_match(v_vector, 20, NULL, NULL, NULL, true, v_documentos);
    PERFORM pg_temp.rag_assert(pg_catalog.jsonb_array_length(v_rows) = 4,
        'Vigencia true/estado activo y vectores válidos deben dejar cuatro fragmentos.');
    PERFORM pg_temp.rag_assert(abs((v_rows -> 0 ->> 'similitud')::double precision - 1) < 1e-6,
        'Vectores idénticos deben tener similitud 1.');
    PERFORM pg_temp.rag_assert(abs((v_rows -> 2 ->> 'similitud')::double precision) < 1e-6,
        'Vectores ortogonales deben tener similitud 0.');
    PERFORM pg_temp.rag_assert(abs((v_rows -> 3 ->> 'similitud')::double precision + 1) < 1e-6,
        'El coseno opuesto debe conservar similitud -1; SQL no aplica confianza ni umbral.');
    PERFORM pg_temp.rag_assert(
        (v_rows -> 0 ->> 'fragmento_id')::integer < (v_rows -> 1 ->> 'fragmento_id')::integer,
        'Los empates deben tener orden estable por fragmento.id.'
    );
    PERFORM pg_temp.rag_assert((v_rows -> 0) ?& ARRAY[
        'fragmento_id', 'documento_id', 'contenido', 'orden', 'similitud', 'titulo', 'drive_id',
        'materia_id', 'comision', 'periodo', 'vigente'
    ], 'La RPC debe devolver el contrato completo.');
    PERFORM pg_temp.rag_assert(
        (SELECT count(*) = 11 FROM pg_catalog.jsonb_object_keys(v_rows -> 0)),
        'La RPC no debe devolver campos adicionales al contrato.'
    );

    v_rows := pg_temp.rag_match(v_vector, 2, NULL, NULL, NULL, true, v_documentos);
    PERFORM pg_temp.rag_assert(pg_catalog.jsonb_array_length(v_rows) = 2, 'Debe respetarse top_k.');
    v_rows := pg_temp.rag_match(v_vector, 20, v_materia, '  comisión a  ', '2026_1', true, v_documentos);
    PERFORM pg_temp.rag_assert(pg_catalog.jsonb_array_length(v_rows) = 3,
        'Filtros exactos con trim/case deben incluir coincidencias y documentos generales.');
    v_rows := pg_temp.rag_match(v_vector, 20, v_otra_materia, NULL, NULL, true, v_documentos);
    PERFORM pg_temp.rag_assert(pg_catalog.jsonb_array_length(v_rows) = 2,
        'Una materia distinta debe conservar sólo los fragmentos generales.');
    v_rows := pg_temp.rag_match(v_vector, 20, NULL, 'Comisión B', NULL, true, v_documentos);
    PERFORM pg_temp.rag_assert(pg_catalog.jsonb_array_length(v_rows) = 3,
        'Comisión debe filtrar antes de limitar, conservando generales.');
    v_rows := pg_temp.rag_match(v_vector, 20, NULL, NULL, '2026_2', true, v_documentos);
    PERFORM pg_temp.rag_assert(pg_catalog.jsonb_array_length(v_rows) = 3,
        'Período debe filtrar antes de limitar, conservando generales.');

    v_rows := pg_temp.rag_match(v_vector, 20, NULL, NULL, NULL, false, v_documentos);
    PERFORM pg_temp.rag_assert(pg_catalog.jsonb_array_length(v_rows) = 1
        AND (v_rows -> 0 ->> 'documento_id')::integer = v_vencido,
        'Vigencia false no debe incluir true ni NULL.');
    v_rows := pg_temp.rag_match(v_vector, 20, NULL, NULL, NULL, NULL, v_documentos);
    PERFORM pg_temp.rag_assert(pg_catalog.jsonb_array_length(v_rows) = 6,
        'Vigencia NULL debe incluir true/false/NULL, conservando el filtro activo.');
    v_rows := pg_temp.rag_match(v_vector, 20, NULL, NULL, NULL, true, ARRAY[v_nulo]);
    PERFORM pg_temp.rag_assert(v_rows = '[]'::jsonb, 'Vigencia NULL en DB no equivale a true.');
    v_rows := pg_temp.rag_match(v_vector, 20, NULL, NULL, NULL, true, ARRAY[v_general]);
    PERFORM pg_temp.rag_assert(pg_catalog.jsonb_array_length(v_rows) = 2,
        'La lista de documento_ids debe restringir el corpus.');
    v_rows := pg_temp.rag_match(v_vector, 20, NULL, NULL, NULL, true, ARRAY[]::integer[]);
    PERFORM pg_temp.rag_assert(v_rows = '[]'::jsonb, 'documento_ids vacío debe devolver cero filas.');
    v_rows := pg_temp.rag_match(v_vector, 2147483647, NULL, NULL, NULL, true, NULL);
    PERFORM pg_temp.rag_assert(EXISTS (
        SELECT 1 FROM pg_catalog.jsonb_array_elements(v_rows) AS r(value)
        WHERE (r.value ->> 'documento_id')::integer = v_general
    ), 'documento_ids NULL debe permitir buscar en el corpus completo.');

    -- La misma RPC funciona con los privilegios de anon, incluidas las secuencias.
    v_lote := pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
        'contenido', 'Alta como anon', 'vector', v_vector::jsonb, 'orden', 1
    ));
    SELECT * INTO STRICT v_resultado
    FROM public.reemplazar_fragmentos_documento(v_prefijo || '_anon', 'Alta anon', v_lote);
    PERFORM pg_temp.rag_assert(v_resultado.fragmentos_indexados = 1, 'anon debe poder indexar.');
    PERFORM pg_temp.rag_assert((SELECT materia_id IS NULL AND comision IS NULL AND periodo IS NULL
                               AND vigente = true AND estado = 'activo'
                               FROM public.documento WHERE id = v_resultado.documento_id),
        'Los documentos nuevos deben ser generales, vigentes y activos.');
    v_rows := pg_temp.rag_match(v_vector, 5, NULL, NULL, NULL, true, ARRAY[v_resultado.documento_id]);
    PERFORM pg_temp.rag_assert(pg_catalog.jsonb_array_length(v_rows) = 1,
                              'anon debe poder recuperar el documento recién indexado.');

    -- Lote parcialmente válido: no debe actualizar título ni borrar fragmentos.
    SELECT d.* INTO STRICT v_antes FROM public.documento AS d WHERE d.id = v_general;
    SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(f) ORDER BY f.id)
    INTO v_fragmentos_antes FROM public.fragmento AS f WHERE f.documento_id = v_general;
    v_lote := pg_catalog.jsonb_build_array(
        pg_catalog.jsonb_build_object('contenido', 'Válido', 'vector', v_vector::jsonb, 'orden', 1),
        pg_catalog.jsonb_build_object('contenido', 'Inválido', 'vector', (v_vector::jsonb - 767), 'orden', 2)
    );
    v_error := false;
    BEGIN
        PERFORM * FROM public.reemplazar_fragmentos_documento(v_antes.drive_id, 'No persistir', v_lote);
    EXCEPTION WHEN SQLSTATE '22023' THEN
        v_error := true;
    END;
    PERFORM pg_temp.rag_assert(v_error, 'Debe rechazarse una dimensión incorrecta en cualquier fragmento.');

    -- Forzar el fallo DURANTE INSERT: comprueba rollback después del DELETE.
    v_lote := pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
        'contenido', '__rag_fallo_forzado__', 'vector', v_vector::jsonb, 'orden', 1
    ));
    v_error := false;
    BEGIN
        PERFORM * FROM public.reemplazar_fragmentos_documento(v_antes.drive_id, 'No persistir', v_lote);
    EXCEPTION WHEN SQLSTATE 'P0001' THEN
        IF SQLERRM <> 'rag_validacion_fallo_forzado' THEN RAISE; END IF;
        v_error := true;
    END;
    PERFORM pg_temp.rag_assert(v_error, 'El trigger de prueba debe forzar el fallo de inserción.');
    SELECT d.titulo = v_antes.titulo AND d.indexado_en IS NOT DISTINCT FROM v_antes.indexado_en
    INTO v_coincide FROM public.documento AS d WHERE d.id = v_general;
    PERFORM pg_temp.rag_assert(v_coincide, 'El fallo debe revertir título e indexado_en.');
    PERFORM pg_temp.rag_assert((SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(f) ORDER BY f.id)
                               FROM public.fragmento AS f WHERE f.documento_id = v_general)
                              IS NOT DISTINCT FROM v_fragmentos_antes,
        'El rollback debe conservar todos los fragmentos previos, incluidos sus IDs.');

    v_error := false;
    BEGIN
        PERFORM * FROM public.reemplazar_fragmentos_documento(v_prefijo || '_alta_fallida',
                                                             'No crear', v_lote);
    EXCEPTION WHEN SQLSTATE 'P0001' THEN
        IF SQLERRM <> 'rag_validacion_fallo_forzado' THEN RAISE; END IF;
        v_error := true;
    END;
    PERFORM pg_temp.rag_assert(v_error AND NOT EXISTS (
        SELECT 1 FROM public.documento WHERE drive_id = v_prefijo || '_alta_fallida'
    ), 'El rollback también debe deshacer el alta de un documento nuevo.');

    -- Entradas inválidas sin mutaciones.
    FOR v_lote IN SELECT value FROM pg_catalog.jsonb_array_elements(
        pg_catalog.jsonb_build_array(
            '[]'::jsonb,
            pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
                'contenido', 'Orden inválido', 'vector', v_vector::jsonb, 'orden', 2)),
            pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
                'contenido', '   ', 'vector', v_vector::jsonb, 'orden', 1)),
            pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
                'contenido', 'Vector cero', 'vector', v_cero::jsonb, 'orden', 1)),
            pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
                'contenido', 'Componente booleano',
                'vector', pg_catalog.jsonb_set(v_vector::jsonb, '{0}', 'true'::jsonb),
                'orden', 1)),
            pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
                'contenido', 'Componente no representable',
                'vector', pg_catalog.jsonb_set(v_vector::jsonb, '{0}', '1e100'::jsonb),
                'orden', 1))
        )
    )
    LOOP
        v_error := false;
        BEGIN
            PERFORM * FROM public.reemplazar_fragmentos_documento(v_antes.drive_id, 'No persistir', v_lote);
        EXCEPTION WHEN SQLSTATE '22023' THEN v_error := true;
        END;
        PERFORM pg_temp.rag_assert(v_error, 'Se debe rechazar un lote inválido.');
    END LOOP;

    v_error := false;
    BEGIN PERFORM pg_temp.rag_match('[1,0]');
    EXCEPTION WHEN SQLSTATE '22023' THEN v_error := true;
    END;
    PERFORM pg_temp.rag_assert(v_error, 'Se debe rechazar una consulta de otra dimensión.');
    v_error := false;
    BEGIN PERFORM pg_temp.rag_match(v_cero);
    EXCEPTION WHEN SQLSTATE '22023' THEN v_error := true;
    END;
    PERFORM pg_temp.rag_assert(v_error, 'Se debe rechazar una consulta con vector cero.');
    v_error := false;
    BEGIN PERFORM pg_temp.rag_match(v_vector, 0);
    EXCEPTION WHEN SQLSTATE '22023' THEN v_error := true;
    END;
    PERFORM pg_temp.rag_assert(v_error, 'Se debe rechazar top_k no positivo.');

    RAISE NOTICE 'Validación RAG completada como anon; se revertirán todas las filas de prueba.';
END;
$checks$;

RESET ROLE;

ROLLBACK;

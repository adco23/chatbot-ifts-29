-- MANUAL: después de 0007 y antes del corte de permisos de 0008.
-- Datos sintéticos; ejecutar con el propietario confiable de las migraciones.
-- No prueba llamadas LLM ni el cron. No ejecutar desde pytest ni al arrancar la API.
BEGIN;
SET LOCAL search_path = pg_catalog, pg_temp;

DO $prueba$
DECLARE
    v_drive text := '__prueba_faq_0007_' || txid_current()::text;
    v_documento integer;
    v_fragmento integer;
    v_esquema text;
    v_embedding text := '[1' || repeat(',0', 767) || ']';
    v_fuente record;
    v_primera record;
    v_segunda record;
    v_faq jsonb;
    v_fuentes jsonb;
    v_cantidad integer;
    v_match bigint;
    v_rechazada boolean := false;
BEGIN
    SELECT n.nspname INTO STRICT v_esquema FROM pg_extension AS e
    JOIN pg_namespace AS n ON n.oid = e.extnamespace WHERE e.extname = 'vector';
    INSERT INTO public.documento (titulo, drive_id, estado, vigente, tipo_documental, indexado_en)
    VALUES ('GENERAL__Prueba-FAQ-0007', v_drive, 'activo', true, 'general',
            timezone('utc', clock_timestamp())) RETURNING id INTO v_documento;
    EXECUTE format('INSERT INTO public.fragmento (documento_id, contenido, orden, vector) '
        'VALUES ($1, $2, 1, $3::%I.vector(768)) RETURNING id', v_esquema)
        INTO v_fragmento USING v_documento, 'La prueba usa evidencia documental sintética.', v_embedding;
    SELECT * INTO STRICT v_fuente FROM public.obtener_fuentes_faq(ARRAY[v_fragmento]);
    v_faq := jsonb_build_object('pregunta_canonica', 'prueba faq sintetica',
        'pregunta_normalizada', 'prueba faq sintetica',
        'respuesta', 'La prueba usa evidencia documental sintética.',
        'embedding', v_embedding::jsonb, 'tipo_documental', 'general',
        'confianza_evidencia', 0.9);
    v_fuentes := jsonb_build_array(jsonb_build_object('drive_id', v_drive,
        'fragmento_ids', ARRAY[v_fragmento], 'es_primaria', true,
        'version_documento', v_fuente.version_documento));
    SELECT * INTO STRICT v_primera FROM public.guardar_faq_curada(v_faq, v_fuentes);
    SELECT * INTO STRICT v_segunda FROM public.guardar_faq_curada(v_faq, v_fuentes);
    IF NOT v_primera.creada OR v_segunda.creada OR v_primera.id <> v_segunda.id THEN
        RAISE EXCEPTION 'El upsert duplicó o cambió la identidad FAQ.';
    END IF;
    EXECUTE format('SELECT id FROM public.match_pregunta_frecuente('
        '$1::%I.vector, 0.75, NULL, NULL, NULL, ''general'', 0.70)', v_esquema)
        INTO v_match USING v_embedding;
    IF v_match IS DISTINCT FROM v_primera.id THEN
        RAISE EXCEPTION 'No hubo HIT general verificable.';
    END IF;
    UPDATE public.pregunta_frecuente SET vigencia_hasta = CURRENT_DATE - 1 WHERE id = v_primera.id;
    EXECUTE format('SELECT count(*) FROM public.match_pregunta_frecuente('
        '$1::%I.vector, 0.75, NULL, NULL, NULL, ''general'', 0.70) WHERE id = $2', v_esquema)
        INTO v_cantidad USING v_embedding, v_primera.id;
    IF v_cantidad <> 0 THEN RAISE EXCEPTION 'Se entregó una FAQ expirada.'; END IF;
    UPDATE public.pregunta_frecuente SET vigencia_hasta = NULL WHERE id = v_primera.id;

    -- Conservar datos de la prueba para las verificaciones bajo anon, sin tabla auxiliar.
    PERFORM set_config('prueba.faq_id', v_primera.id::text, true);
    PERFORM set_config('prueba.faq_documento', v_documento::text, true);
    PERFORM set_config('prueba.faq_generada',
        (SELECT generada_en::text FROM public.pregunta_frecuente WHERE id = v_primera.id), true);

    -- Fuente cambiada entre generación y publicación: el upsert debe revertir.
    UPDATE public.documento SET titulo = titulo || '-modificado' WHERE id = v_documento;
    BEGIN
        PERFORM public.guardar_faq_curada(v_faq, v_fuentes);
    EXCEPTION WHEN raise_exception THEN
        v_rechazada := true;
    END;
    IF NOT v_rechazada THEN RAISE EXCEPTION 'Se publicó una fuente obsoleta.'; END IF;
    IF (SELECT estado FROM public.pregunta_frecuente WHERE id = v_primera.id) <> 'pendiente' THEN
        RAISE EXCEPTION 'El cambio documental no dejó pendiente la FAQ.';
    END IF;
    -- Regeneración sintética válida: obtener la versión actual y volver a guardar.
    SELECT * INTO STRICT v_fuente FROM public.obtener_fuentes_faq(ARRAY[v_fragmento]);
    v_fuentes := jsonb_build_array(jsonb_build_object('drive_id', v_drive,
        'fragmento_ids', ARRAY[v_fragmento], 'es_primaria', true,
        'version_documento', v_fuente.version_documento));
    PERFORM public.guardar_faq_curada(v_faq, v_fuentes);
    PERFORM set_config('prueba.faq_generada_actual',
        (SELECT generada_en::text FROM public.pregunta_frecuente WHERE id = v_primera.id), true);
END;
$prueba$;

SET LOCAL ROLE anon;
DO $permisos_funcionales$
DECLARE
    v_id bigint := current_setting('prueba.faq_id')::bigint;
    v_generada timestamp := current_setting('prueba.faq_generada_actual')::timestamp;
BEGIN
    -- El acceso encapsulado funciona aunque pregunta_fuente tenga RLS sin policies.
    IF (SELECT count(*) FROM public.obtener_fuentes_cache_faq(v_id, v_generada)) <> 1 THEN
        RAISE EXCEPTION 'anon no pudo obtener una fuente FAQ vigente por RPC.';
    END IF;
    IF EXISTS (SELECT 1 FROM public.obtener_fuentes_cache_faq(
        v_id, current_setting('prueba.faq_generada')::timestamp)) THEN
        RAISE EXCEPTION 'Se mezclaron una respuesta anterior y fuentes nuevas.';
    END IF;
    PERFORM public.incrementar_acierto_faq(v_id);
    IF (SELECT aciertos FROM public.pregunta_frecuente WHERE id = v_id) <> 1 THEN
        RAISE EXCEPTION 'El contador no se incrementó atómicamente.';
    END IF;
    UPDATE public.documento SET estado = 'inactivo', vigente = false
    WHERE id = current_setting('prueba.faq_documento')::integer;
    IF EXISTS (SELECT 1 FROM public.obtener_fuentes_cache_faq(v_id, v_generada)) THEN
        RAISE EXCEPTION 'Se entregó una fuente inactiva.';
    END IF;
END;
$permisos_funcionales$;
RESET ROLE;
ROLLBACK;

-- Aplicación MANUAL después de 0006. No ejecutar desde el arranque ni desde tests.
-- Etapa funcional: conserva anon para probar FAQ antes del corte de credencial de 0008.
-- No modifica RLS ni ACL de tablas. Las tres RPC DEFINER acotadas necesitan acceder
-- a pregunta_fuente, cuyo RLS real ya está activo sin policies. Esto NO endurece el
-- acceso directo preexistente a pregunta_frecuente: el cierre global es 0008.
BEGIN;
SET LOCAL search_path = pg_catalog, pg_temp;

DO $preflight$
DECLARE
    v_esquema text;
    v_incompatible boolean;
BEGIN
    IF pg_catalog.pg_has_role('anon', current_user, 'MEMBER')
       OR pg_catalog.pg_has_role('authenticated', current_user, 'MEMBER')
       OR pg_catalog.row_security_active('public.pregunta_fuente'::regclass) THEN
        RAISE EXCEPTION '0007 requiere un rol de migraciones confiable con acceso a las fuentes.';
    END IF;
    SELECT n.nspname INTO STRICT v_esquema
    FROM pg_catalog.pg_extension AS e
    JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace WHERE e.extname = 'vector';
    IF pg_catalog.to_regclass('public.pregunta_frecuente') IS NULL
       OR pg_catalog.to_regclass('public.pregunta_fuente') IS NULL
       OR pg_catalog.to_regclass('public.documento') IS NULL THEN
        RAISE EXCEPTION 'Se requiere el esquema previo de FAQ y documentos.';
    END IF;
    EXECUTE pg_catalog.format(
        'SELECT EXISTS (SELECT 1 FROM public.pregunta_frecuente WHERE embedding IS NOT NULL '
        'AND (%1$I.vector_dims(embedding) <> 768 OR NOT '
        '(%1$I.vector_norm(embedding) > 0 AND %1$I.vector_norm(embedding) < ''Infinity''::float8)))',
        v_esquema) INTO v_incompatible;
    IF v_incompatible THEN
        RAISE EXCEPTION 'Hay embeddings FAQ incompatibles; revisar antes de aplicar 0007.';
    END IF;
    IF EXISTS (SELECT 1 FROM public.pregunta_frecuente WHERE aciertos < 0) THEN
        RAISE EXCEPTION 'Hay contadores FAQ negativos; revisar sin borrar registros.';
    END IF;
    EXECUTE pg_catalog.format(
        'ALTER TABLE public.pregunta_frecuente ALTER COLUMN embedding TYPE %I.vector(768) '
        'USING embedding::%I.vector(768)', v_esquema, v_esquema);
END;
$preflight$;

ALTER TABLE public.pregunta_frecuente
    ADD COLUMN pregunta_normalizada text,
    ADD COLUMN periodo varchar NULL,
    ADD COLUMN tipo_documental text NOT NULL DEFAULT 'general',
    ADD COLUMN estado text NOT NULL DEFAULT 'inactiva',
    ADD COLUMN confianza_evidencia double precision NOT NULL DEFAULT 0;

-- Se preservan respuesta, vector e identidad anteriores, pero no se publican sin
-- reconstruir procedencia. La base confirmada contiene una FAQ y ninguna fuente.
UPDATE public.pregunta_frecuente SET
    pregunta_normalizada = btrim(regexp_replace(lower(regexp_replace(
        pg_catalog.normalize(pregunta_canonica, 'NFKD'), U&'[\0300-\036f]', '', 'g')),
        '[[:space:]]+', ' ', 'g')),
    comision = CASE WHEN btrim(comision) = '' OR upper(btrim(comision)) = 'GENERAL'
                    THEN NULL ELSE upper(btrim(comision)) END,
    aciertos = COALESCE(aciertos, 0),
    generada_en = COALESCE(generada_en, timezone('utc', CURRENT_TIMESTAMP));

ALTER TABLE public.pregunta_frecuente
    ALTER COLUMN pregunta_normalizada SET NOT NULL,
    ALTER COLUMN aciertos SET NOT NULL,
    ALTER COLUMN aciertos SET DEFAULT 0,
    ALTER COLUMN generada_en SET NOT NULL,
    ALTER COLUMN generada_en SET DEFAULT timezone('utc', CURRENT_TIMESTAMP),
    ADD CONSTRAINT faq_pregunta_normalizada_check CHECK (btrim(pregunta_normalizada) <> ''),
    ADD CONSTRAINT faq_aciertos_check CHECK (aciertos >= 0),
    ADD CONSTRAINT faq_estado_check CHECK (estado IN ('activa', 'pendiente', 'inactiva')),
    ADD CONSTRAINT faq_tipo_documental_check CHECK (tipo_documental IN ('general', 'programa', 'cronograma')),
    ADD CONSTRAINT faq_comision_check CHECK (comision ~ '^[A-Z0-9]{1,32}$'),
    ADD CONSTRAINT faq_periodo_check CHECK (periodo ~ '^[0-9]{4}C[12]$'),
    ADD CONSTRAINT faq_confianza_check CHECK (confianza_evidencia >= 0 AND confianza_evidencia <= 1),
    ADD CONSTRAINT faq_activa_completa_check CHECK (estado <> 'activa' OR (
        embedding IS NOT NULL AND respuesta IS NOT NULL AND btrim(respuesta) <> ''));

-- md5 mantiene pequeño el índice para preguntas largas; el upsert comprueba también
-- la igualdad del texto normalizado y falla ante una hipotética colisión.
CREATE UNIQUE INDEX uq_faq_pregunta_contexto ON public.pregunta_frecuente (
    md5(pregunta_normalizada), (COALESCE(materia_id, 0)), (COALESCE(comision, '')),
    (COALESCE(periodo, '')), tipo_documental);
CREATE INDEX idx_faq_contexto_activo ON public.pregunta_frecuente
    (materia_id, comision, periodo, tipo_documental, vigencia_hasta) WHERE estado = 'activa';
CREATE INDEX idx_faq_pendiente ON public.pregunta_frecuente (id) WHERE estado = 'pendiente';

ALTER TABLE public.pregunta_fuente
    ADD COLUMN pregunta_frecuente_id bigint,
    ADD COLUMN fragmento_ids integer[] NOT NULL DEFAULT '{}',
    ADD COLUMN es_primaria boolean NOT NULL DEFAULT false,
    ADD COLUMN version_documento jsonb;
-- Únicamente reconcilia la relación antigua inequívoca; no reutiliza la identity
-- de fuente para nuevas FAQ. Restricciones posteriores abortan ante datos inválidos.
UPDATE public.pregunta_fuente SET pregunta_frecuente_id = id;
ALTER TABLE public.pregunta_fuente DROP CONSTRAINT pregunta_fuente_id_fkey;
ALTER TABLE public.pregunta_fuente
    ALTER COLUMN pregunta_frecuente_id SET NOT NULL,
    ALTER COLUMN drive_id SET NOT NULL,
    ADD CONSTRAINT pregunta_fuente_faq_fkey FOREIGN KEY (pregunta_frecuente_id)
        REFERENCES public.pregunta_frecuente(id) ON DELETE CASCADE,
    ADD CONSTRAINT pregunta_fuente_faq_documento_key UNIQUE (pregunta_frecuente_id, drive_id);
-- El esquema real ya tiene PK. El bootstrap histórico de 0001 no la declaraba.
DO $fuente_pk$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                   WHERE conrelid = 'public.pregunta_fuente'::regclass AND contype = 'p') THEN
        ALTER TABLE public.pregunta_fuente ADD CONSTRAINT pregunta_fuente_pkey PRIMARY KEY (id);
    END IF;
END;
$fuente_pk$;
CREATE INDEX idx_pregunta_fuente_drive ON public.pregunta_fuente (drive_id, pregunta_frecuente_id);

-- Una instantánea de campos YA existentes, no un contador de versiones nuevo.
CREATE FUNCTION public.snapshot_documento_faq(p_documento public.documento)
RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$
    SELECT jsonb_build_object(
        'indexado_en', (p_documento).indexado_en,
        'drive_modified_time', (p_documento).drive_modified_time,
        'drive_md5_checksum', (p_documento).drive_md5_checksum,
        'titulo', (p_documento).titulo, 'materia_id', (p_documento).materia_id,
        'comision', (p_documento).comision, 'periodo', (p_documento).periodo,
        'estado', (p_documento).estado, 'vigente', (p_documento).vigente,
        'tipo_documental', (p_documento).tipo_documental);
$$;

UPDATE public.pregunta_fuente AS pf SET version_documento = public.snapshot_documento_faq(d)
FROM public.documento AS d WHERE d.drive_id = pf.drive_id;
ALTER TABLE public.pregunta_fuente ALTER COLUMN version_documento SET NOT NULL;

CREATE FUNCTION public.fuentes_faq_vigentes(p_pregunta_id bigint)
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$
    SELECT EXISTS (SELECT 1 FROM public.pregunta_fuente WHERE pregunta_frecuente_id = p_pregunta_id)
    AND NOT EXISTS (
        SELECT 1 FROM public.pregunta_fuente AS pf
        LEFT JOIN public.documento AS d ON d.drive_id = pf.drive_id
        WHERE pf.pregunta_frecuente_id = p_pregunta_id AND (
            d.id IS NULL OR d.estado IS DISTINCT FROM 'activo' OR d.vigente IS NOT TRUE
            OR d.indexado_en IS NULL
            OR pf.version_documento IS DISTINCT FROM public.snapshot_documento_faq(d)
            OR cardinality(pf.fragmento_ids) = 0
            OR EXISTS (SELECT 1 FROM unnest(pf.fragmento_ids) AS x(id)
                       WHERE NOT EXISTS (SELECT 1 FROM public.fragmento AS f
                                         WHERE f.id = x.id AND f.documento_id = d.id))));
$$;

CREATE FUNCTION public.obtener_fuentes_faq(p_fragmento_ids integer[])
RETURNS TABLE (
    fragmento_id integer, documento_id integer, contenido text, orden integer,
    titulo text, drive_id text, materia_id integer, comision text, periodo text,
    vigente boolean, tipo_documental text, estado_documento text, version_documento jsonb
)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$
    SELECT f.id, d.id, f.contenido, f.orden, d.titulo::text, d.drive_id::text,
        d.materia_id, d.comision::text, d.periodo::text, d.vigente,
        d.tipo_documental::text, d.estado::text, public.snapshot_documento_faq(d)
    FROM public.fragmento AS f JOIN public.documento AS d ON d.id = f.documento_id
    WHERE f.id = ANY(p_fragmento_ids) ORDER BY f.id;
$$;

CREATE FUNCTION public.obtener_fuentes_cache_faq(
    p_pregunta_id bigint, p_generada_en_esperada timestamp without time zone
)
RETURNS TABLE (
    fragmento_id integer, documento_id integer, contenido text, orden integer,
    titulo text, drive_id text, materia_id integer, comision text, periodo text,
    vigente boolean, tipo_documental text, estado_documento text,
    version_documento jsonb, es_primaria boolean
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
    SELECT f.id, d.id, f.contenido, f.orden, d.titulo::text, d.drive_id::text,
        d.materia_id, d.comision::text, d.periodo::text, d.vigente, d.tipo_documental::text,
        d.estado::text, public.snapshot_documento_faq(d), pf.es_primaria
    FROM public.pregunta_fuente AS pf JOIN public.documento AS d ON d.drive_id = pf.drive_id
    JOIN public.fragmento AS f ON f.documento_id = d.id AND f.id = ANY(pf.fragmento_ids)
    JOIN public.pregunta_frecuente AS q ON q.id = pf.pregunta_frecuente_id
    WHERE q.id = p_pregunta_id AND q.estado = 'activa'
      AND q.generada_en = p_generada_en_esperada
      AND (q.vigencia_hasta IS NULL OR q.vigencia_hasta >= CURRENT_DATE)
      AND public.fuentes_faq_vigentes(q.id)
    ORDER BY d.id, f.orden, f.id;
$$;
REVOKE ALL ON FUNCTION public.obtener_fuentes_cache_faq(bigint, timestamp without time zone) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.obtener_fuentes_cache_faq(bigint, timestamp without time zone) TO anon, service_role;

-- Invalida en la misma transacción que cambia un documento. No genera respuestas,
-- no llama servicios y no modifica el mecanismo de indexación ni sus concesiones.
CREATE FUNCTION public.invalidar_faq_documento()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF TG_OP = 'DELETE' OR public.snapshot_documento_faq(OLD)
                           IS DISTINCT FROM public.snapshot_documento_faq(NEW) THEN
        UPDATE public.pregunta_frecuente AS q SET estado = 'pendiente'
        WHERE q.id IN (SELECT pf.pregunta_frecuente_id FROM public.pregunta_fuente AS pf
                       WHERE pf.drive_id = OLD.drive_id);
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER documento_invalida_faq BEFORE UPDATE OR DELETE ON public.documento
    FOR EACH ROW EXECUTE FUNCTION public.invalidar_faq_documento();

-- El contador sigue siendo un UPDATE atómico. El servicio lo llama después de
-- construir una RespuestaChat válida; un id inexistente no cuenta como acierto.
CREATE OR REPLACE FUNCTION public.incrementar_acierto_faq(p_id bigint)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF p_id IS NULL OR p_id <= 0 THEN RAISE EXCEPTION 'ID de FAQ inválido.'; END IF;
    UPDATE public.pregunta_frecuente AS q SET aciertos = q.aciertos + 1
    WHERE q.id = p_id AND q.estado = 'activa';
    IF NOT FOUND THEN RAISE EXCEPTION 'La FAQ ya no es elegible para contar un acierto.'; END IF;
END;
$$;

DO $rpc$
DECLARE
    v_esquema text;
    v_funcion record;
BEGIN
    SELECT n.nspname INTO STRICT v_esquema FROM pg_catalog.pg_extension AS e
    JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace WHERE e.extname = 'vector';
    -- La firma desplegada puede diferir de 0006: resolver TODAS las identidades
    -- reales de este nombre y esquema, sin asumir orden, tipos ni nombres de argumentos.
    -- Si no existe, el bucle es un no-op. RESTRICT conserva dependencias ajenas:
    -- cualquier error aborta la transacción completa y permite reintentar tras ROLLBACK.
    -- No reaplicar la migración completa si ya terminó con COMMIT exitoso.
    FOR v_funcion IN
        SELECT n.nspname, p.proname,
               pg_catalog.pg_get_function_identity_arguments(p.oid) AS argumentos
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname = 'match_pregunta_frecuente' AND p.prokind = 'f'
        ORDER BY p.oid
    LOOP
        EXECUTE pg_catalog.format(
            'DROP FUNCTION IF EXISTS %I.%I(%s) RESTRICT',
            v_funcion.nspname, v_funcion.proname, v_funcion.argumentos);
    END LOOP;
    EXECUTE pg_catalog.format($definition$
    CREATE FUNCTION public.match_pregunta_frecuente(
        p_query_embedding %1$I.vector, p_umbral double precision,
        p_materia_id integer DEFAULT NULL, p_comision text DEFAULT NULL,
        p_periodo text DEFAULT NULL, p_tipo_documental text DEFAULT 'general',
        p_umbral_evidencia double precision DEFAULT 0.70
    ) RETURNS TABLE (
        id bigint, pregunta_canonica text, respuesta text, materia_id integer,
        comision text, periodo text, tipo_documental text, estado text,
        vigencia_hasta date, similitud double precision, confianza_evidencia double precision,
        generada_en timestamp without time zone
    ) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $body$
    DECLARE
        v_comision text := NULLIF(upper(btrim(p_comision)), '');
        v_periodo text := NULLIF(upper(btrim(p_periodo)), '');
        v_norma double precision;
    BEGIN
        IF p_query_embedding IS NULL THEN RAISE EXCEPTION 'Se requiere embedding.'; END IF;
        v_norma := %1$I.vector_norm(p_query_embedding);
        IF %1$I.vector_dims(p_query_embedding) <> 768 OR NOT (v_norma > 0 AND v_norma < 'Infinity'::float8)
           OR p_umbral IS NULL OR NOT (p_umbral >= 0 AND p_umbral <= 1)
           OR p_umbral_evidencia IS NULL OR NOT (p_umbral_evidencia >= 0 AND p_umbral_evidencia <= 1) THEN
            RAISE EXCEPTION 'Vector o umbral FAQ inválido.';
        END IF;
        IF p_materia_id IS NOT NULL AND p_materia_id <= 0
           OR p_comision IS NOT NULL AND (v_comision IS NULL OR v_comision !~ '^[A-Z0-9]{1,32}$')
           OR p_periodo IS NOT NULL AND (v_periodo IS NULL OR v_periodo !~ '^[0-9]{4}C[12]$')
           OR p_tipo_documental IS NULL OR p_tipo_documental NOT IN ('general', 'programa', 'cronograma') THEN
            RAISE EXCEPTION 'Contexto FAQ inválido.';
        END IF;
        RETURN QUERY SELECT q.id, q.pregunta_canonica::text, q.respuesta::text, q.materia_id,
            q.comision::text, q.periodo::text, q.tipo_documental, q.estado, q.vigencia_hasta,
            (1.0 - (q.embedding OPERATOR(%1$I.<=>) p_query_embedding))::float8,
            q.confianza_evidencia, q.generada_en
        FROM public.pregunta_frecuente AS q
        WHERE q.estado = 'activa' AND q.embedding IS NOT NULL AND btrim(q.respuesta) <> ''
          AND %1$I.vector_norm(q.embedding) > 0 AND %1$I.vector_norm(q.embedding) < 'Infinity'::float8
          AND q.confianza_evidencia >= p_umbral_evidencia
          AND (q.vigencia_hasta IS NULL OR q.vigencia_hasta >= CURRENT_DATE)
          AND public.fuentes_faq_vigentes(q.id)
          AND (1.0 - (q.embedding OPERATOR(%1$I.<=>) p_query_embedding)) >= p_umbral
          AND q.tipo_documental = p_tipo_documental
          AND (q.periodo IS NOT DISTINCT FROM v_periodo)
          AND (
            (p_tipo_documental = 'cronograma' AND p_materia_id IS NOT NULL
                AND v_comision IS NOT NULL AND q.materia_id = p_materia_id AND q.comision = v_comision)
            OR (p_tipo_documental = 'programa' AND p_materia_id IS NOT NULL
                AND q.materia_id = p_materia_id AND (q.comision = v_comision OR q.comision IS NULL))
            OR (p_tipo_documental = 'general'
                AND (q.materia_id IS NULL OR q.materia_id = p_materia_id)
                AND (q.comision IS NULL OR q.comision = v_comision)))
          AND EXISTS (SELECT 1 FROM public.pregunta_fuente AS s
                      JOIN public.documento AS d ON d.drive_id = s.drive_id
                      WHERE s.pregunta_frecuente_id = q.id AND s.es_primaria
                        AND (p_tipo_documental = 'general' OR d.tipo_documental = p_tipo_documental))
        ORDER BY (q.materia_id IS NOT NULL) DESC, (q.comision IS NOT NULL) DESC,
            q.embedding OPERATOR(%1$I.<=>) p_query_embedding ASC, q.id ASC
        LIMIT 1;
    END;
    $body$;
    $definition$, v_esquema);
    EXECUTE pg_catalog.format(
        'REVOKE ALL ON FUNCTION public.match_pregunta_frecuente(%I.vector, double precision, integer, text, text, text, double precision) FROM PUBLIC, anon, authenticated', v_esquema);
    EXECUTE pg_catalog.format(
        'GRANT EXECUTE ON FUNCTION public.match_pregunta_frecuente(%I.vector, double precision, integer, text, text, text, double precision) TO anon, service_role', v_esquema);

    EXECUTE pg_catalog.format($definition$
    CREATE FUNCTION public.guardar_faq_curada(p_faq jsonb, p_fuentes jsonb)
    RETURNS TABLE (id bigint, creada boolean)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $body$
    DECLARE
        v_pregunta text := btrim(p_faq->>'pregunta_canonica');
        v_normalizada text := p_faq->>'pregunta_normalizada';
        v_respuesta text := btrim(p_faq->>'respuesta');
        v_materia integer := (p_faq->>'materia_id')::integer;
        v_comision text := NULLIF(upper(btrim(p_faq->>'comision')), '');
        v_periodo text := NULLIF(upper(btrim(p_faq->>'periodo')), '');
        v_tipo text := COALESCE(p_faq->>'tipo_documental', 'general');
        v_confianza double precision := (p_faq->>'confianza_evidencia')::float8;
        v_fecha_limite timestamptz := (p_faq->>'fecha_limite')::timestamptz;
        v_embedding %1$I.vector(768);
        v_id bigint;
        v_nueva boolean;
        s jsonb;
        d public.documento;
        v_ids integer[];
        v_primaria boolean;
        v_primarias integer := 0;
    BEGIN
        IF v_fecha_limite IS NOT NULL THEN
            IF clock_timestamp() >= v_fecha_limite THEN
                RAISE EXCEPTION 'Se agotó el presupuesto de regeneración FAQ.';
            END IF;
            PERFORM set_config('lock_timeout', '1000ms', true);
        END IF;
        IF jsonb_typeof(p_faq) IS DISTINCT FROM 'object'
           OR jsonb_typeof(p_fuentes) IS DISTINCT FROM 'array'
           OR jsonb_array_length(p_fuentes) NOT BETWEEN 1 AND 50
           OR v_pregunta IS NULL OR v_pregunta = '' OR length(v_pregunta) > 2000
           OR v_respuesta IS NULL OR v_respuesta = '' OR length(v_respuesta) > 8000
           OR v_normalizada IS NULL OR v_normalizada <> btrim(regexp_replace(lower(regexp_replace(
                pg_catalog.normalize(v_pregunta, 'NFKD'), U&'[\0300-\036f]', '', 'g')),
                '[[:space:]]+', ' ', 'g'))
           OR jsonb_typeof(p_faq->'embedding') IS DISTINCT FROM 'array'
           OR v_confianza IS NULL OR NOT (v_confianza >= 0 AND v_confianza <= 1)
           OR v_tipo NOT IN ('general', 'programa', 'cronograma') THEN
            RAISE EXCEPTION 'FAQ curada incompleta o inválida.';
        END IF;
        v_embedding := (p_faq->'embedding')::text::%1$I.vector(768);
        IF NOT (%1$I.vector_norm(v_embedding) > 0 AND %1$I.vector_norm(v_embedding) < 'Infinity'::float8)
           OR v_materia IS NOT NULL AND v_materia <= 0
           OR v_comision IS NOT NULL AND v_comision !~ '^[A-Z0-9]{1,32}$'
           OR v_periodo IS NOT NULL AND v_periodo !~ '^[0-9]{4}C[12]$'
           OR v_tipo IN ('programa', 'cronograma') AND v_materia IS NULL
           OR v_tipo = 'cronograma' AND v_comision IS NULL THEN
            RAISE EXCEPTION 'Embedding o contexto curado inválido.';
        END IF;
        IF (SELECT count(DISTINCT x->>'drive_id') FROM jsonb_array_elements(p_fuentes) AS x)
           <> jsonb_array_length(p_fuentes) THEN RAISE EXCEPTION 'Fuentes repetidas.'; END IF;
        -- Bloqueo de filas existentes, en orden estable; no agrega otro lease.
        -- Impide activar una respuesta si el documento cambió durante la generación.
        FOR s IN SELECT x FROM jsonb_array_elements(p_fuentes) AS x ORDER BY x->>'drive_id' LOOP
            IF v_fecha_limite IS NOT NULL THEN
                IF clock_timestamp() >= v_fecha_limite THEN
                    RAISE EXCEPTION 'Se agotó el presupuesto de regeneración FAQ.';
                END IF;
                PERFORM set_config('lock_timeout',
                    greatest(1, least(1000, floor(extract(epoch FROM
                        (v_fecha_limite - clock_timestamp())) * 1000)))::text || 'ms', true);
            END IF;
            SELECT * INTO STRICT d FROM public.documento WHERE drive_id = s->>'drive_id' FOR SHARE;
            IF v_fecha_limite IS NOT NULL AND clock_timestamp() >= v_fecha_limite THEN
                RAISE EXCEPTION 'Se agotó el presupuesto de regeneración FAQ.';
            END IF;
            IF d.estado IS DISTINCT FROM 'activo' OR d.vigente IS NOT TRUE OR d.indexado_en IS NULL
               OR s->'version_documento' IS DISTINCT FROM public.snapshot_documento_faq(d)
               OR jsonb_typeof(s->'fragmento_ids') IS DISTINCT FROM 'array'
               OR jsonb_array_length(s->'fragmento_ids') NOT BETWEEN 1 AND 50
               OR jsonb_typeof(s->'es_primaria') IS DISTINCT FROM 'boolean' THEN
                RAISE EXCEPTION 'La fuente no está vigente o cambió durante la generación.';
            END IF;
            SELECT array_agg((x #>> '{}')::integer) INTO v_ids
            FROM jsonb_array_elements(s->'fragmento_ids') AS x;
            IF (SELECT count(DISTINCT x) FROM unnest(v_ids) AS x) <> cardinality(v_ids)
               OR EXISTS (SELECT 1 FROM unnest(v_ids) AS x
                          WHERE NOT EXISTS (SELECT 1 FROM public.fragmento AS f
                                            WHERE f.id = x AND f.documento_id = d.id)) THEN
                RAISE EXCEPTION 'Fragmentos de fuente inválidos.';
            END IF;
            v_primaria := (s->>'es_primaria')::boolean;
            IF v_primaria THEN
                IF d.materia_id IS NOT NULL AND d.materia_id IS DISTINCT FROM v_materia
                   OR d.comision IS NOT NULL AND upper(btrim(d.comision)) IS DISTINCT FROM v_comision
                   OR d.periodo IS NOT NULL AND upper(btrim(d.periodo)) IS DISTINCT FROM v_periodo
                   OR v_tipo IN ('programa', 'cronograma') AND d.tipo_documental IS DISTINCT FROM v_tipo
                   OR v_tipo = 'cronograma' AND (d.materia_id IS DISTINCT FROM v_materia
                                      OR upper(btrim(d.comision)) IS DISTINCT FROM v_comision)
                   OR v_tipo = 'programa' AND d.materia_id IS DISTINCT FROM v_materia THEN
                    RAISE EXCEPTION 'La fuente primaria no respeta el contexto curado.';
                END IF;
                v_primarias := v_primarias + 1;
            END IF;
        END LOOP;
        IF v_primarias = 0 THEN RAISE EXCEPTION 'Se requiere evidencia primaria.'; END IF;
        IF v_fecha_limite IS NOT NULL THEN
            IF clock_timestamp() >= v_fecha_limite THEN
                RAISE EXCEPTION 'Se agotó el presupuesto de regeneración FAQ.';
            END IF;
            PERFORM set_config('lock_timeout',
                greatest(1, least(1000, floor(extract(epoch FROM
                    (v_fecha_limite - clock_timestamp())) * 1000)))::text || 'ms', true);
        END IF;
        INSERT INTO public.pregunta_frecuente AS q (
            pregunta_canonica, pregunta_normalizada, respuesta, embedding, materia_id,
            comision, periodo, tipo_documental, confianza_evidencia, vigencia_hasta, estado, generada_en
        ) VALUES (v_pregunta, v_normalizada, v_respuesta, v_embedding, v_materia, v_comision,
            v_periodo, v_tipo, v_confianza, (p_faq->>'vigencia_hasta')::date,
            'activa', timezone('utc', clock_timestamp()))
        ON CONFLICT ((md5(pregunta_normalizada)), (COALESCE(materia_id, 0)),
                     (COALESCE(comision, '')), (COALESCE(periodo, '')), tipo_documental)
        DO UPDATE SET pregunta_canonica = EXCLUDED.pregunta_canonica,
            respuesta = EXCLUDED.respuesta, embedding = EXCLUDED.embedding,
            confianza_evidencia = EXCLUDED.confianza_evidencia, estado = 'activa',
            vigencia_hasta = EXCLUDED.vigencia_hasta, generada_en = EXCLUDED.generada_en
        WHERE q.pregunta_normalizada = EXCLUDED.pregunta_normalizada
        RETURNING q.id, (q.xmax = 0) INTO v_id, v_nueva;
        IF v_id IS NULL THEN RAISE EXCEPTION 'Colisión de identidad FAQ; revisar.'; END IF;
        DELETE FROM public.pregunta_fuente WHERE pregunta_frecuente_id = v_id;
        INSERT INTO public.pregunta_fuente (
            pregunta_frecuente_id, drive_id, fragmento_ids, es_primaria, version_documento)
        SELECT v_id, x->>'drive_id',
            ARRAY(SELECT (z #>> '{}')::integer FROM jsonb_array_elements(x->'fragmento_ids') AS z),
            (x->>'es_primaria')::boolean, x->'version_documento'
        FROM jsonb_array_elements(p_fuentes) AS x;
        IF v_fecha_limite IS NOT NULL AND clock_timestamp() >= v_fecha_limite THEN
            RAISE EXCEPTION 'Se agotó el presupuesto de regeneración FAQ.';
        END IF;
        RETURN QUERY SELECT v_id, v_nueva;
    END;
    $body$;
    $definition$, v_esquema);
END;
$rpc$;

REVOKE ALL ON FUNCTION public.guardar_faq_curada(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.guardar_faq_curada(jsonb, jsonb) TO anon, service_role;
REVOKE ALL ON FUNCTION public.incrementar_acierto_faq(bigint) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.incrementar_acierto_faq(bigint) TO anon, service_role;
REVOKE ALL ON FUNCTION public.snapshot_documento_faq(public.documento) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.fuentes_faq_vigentes(bigint) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.obtener_fuentes_faq(integer[]) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.invalidar_faq_documento() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.snapshot_documento_faq(public.documento),
    public.fuentes_faq_vigentes(bigint), public.obtener_fuentes_faq(integer[])
    TO anon, service_role;
GRANT EXECUTE ON FUNCTION public.invalidar_faq_documento() TO service_role;

-- Exactitud primero: no HNSW/IVFFlat para el volumen FAQ actual.
NOTIFY pgrst, 'reload schema';
COMMIT;

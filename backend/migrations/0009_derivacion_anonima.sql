-- Aplicación manual. T40: evento institucional mínimo, sin vínculos a otras tablas.
BEGIN;
SET LOCAL search_path = pg_catalog, pg_temp;

CREATE TABLE public.derivacion_evento (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sector VARCHAR(200) NOT NULL,
    creado_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT derivacion_evento_sector_no_vacio CHECK (sector ~ '[^[:space:]]')
);

ALTER TABLE public.derivacion_evento ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.derivacion_evento
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL PRIVILEGES (id, sector, creado_at) ON TABLE public.derivacion_evento
    FROM PUBLIC, anon, authenticated, service_role;

DO $permisos_secuencia$
DECLARE
    v_secuencia regclass := pg_catalog.pg_get_serial_sequence(
        'public.derivacion_evento', 'id'
    )::regclass;
BEGIN
    EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM PUBLIC, anon, authenticated, service_role',
        v_secuencia
    );
END;
$permisos_secuencia$;

CREATE FUNCTION public.registrar_derivacion_anonima(p_sector text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $registrar$
DECLARE
    v_sector text := pg_catalog.btrim(p_sector);
BEGIN
    IF v_sector IS NULL OR v_sector !~ '[^[:space:]]'
       OR pg_catalog.char_length(v_sector) > 200 THEN
        RAISE EXCEPTION 'Se requiere un sector de entre 1 y 200 caracteres.'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO public.derivacion_evento (sector) VALUES (v_sector);
    RETURN true;
END;
$registrar$;

REVOKE ALL PRIVILEGES ON FUNCTION public.registrar_derivacion_anonima(text)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.registrar_derivacion_anonima(text) TO service_role;

COMMIT;

"""Revisa el contrato de 0007 sin ejecutar SQL ni probar atomicidad PostgreSQL."""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
SQL = (BACKEND / "migrations/0007_cache_faq_consistente.sql").read_text(encoding="utf-8")
CODIGO = re.sub(r"--[^\n]*", "", SQL)


def test_migracion_incremental_atomica_sin_corte_global_de_seguridad():
    assert CODIGO.index("BEGIN;") < CODIGO.index("DO $preflight$")
    assert CODIGO.rstrip().endswith("COMMIT;")
    assert "ROW LEVEL SECURITY" not in CODIGO and "CREATE POLICY" not in CODIGO
    assert not re.search(r"(?:GRANT|REVOKE).*\bON\s+(?:TABLE|ALL TABLES)", CODIGO)
    assert "ADD COLUMN revision" not in CODIGO and "advisory" not in CODIGO
    assert "rag_sync_bloqueo" not in CODIGO


def test_vector_se_valida_antes_de_convertir_y_no_se_borran_faq_existentes():
    preflight = CODIGO.split("DO $preflight$")[1].split("$preflight$;")[0]
    assert "pg_catalog.pg_extension" in preflight and "extnamespace" in preflight
    assert preflight.index("vector_dims(embedding) <> 768") < preflight.index("ALTER COLUMN embedding TYPE")
    assert "vector_norm(embedding) > 0" in preflight
    assert "vector_norm(embedding) < ''Infinity''" in preflight
    assert "vector(768)" in preflight
    assert not re.search(r"DELETE\s+FROM\s+public\.pregunta_frecuente", CODIGO, re.I)
    assert "estado text NOT NULL DEFAULT 'inactiva'" in CODIGO
    assert "ALTER COLUMN aciertos SET NOT NULL" in CODIGO
    assert "ALTER COLUMN aciertos SET DEFAULT 0" in CODIGO


def test_fuente_tiene_identidad_propia_y_fk_explicita_sin_fk_a_fragmentos():
    assert "ADD COLUMN pregunta_frecuente_id bigint" in CODIGO
    assert "DROP CONSTRAINT pregunta_fuente_id_fkey" in CODIGO
    assert "FOREIGN KEY (pregunta_frecuente_id)" in CODIGO
    assert "UNIQUE (pregunta_frecuente_id, drive_id)" in CODIGO
    assert "ADD COLUMN fragmento_ids integer[]" in CODIGO
    assert "ADD COLUMN es_primaria boolean" in CODIGO
    assert "REFERENCES public.fragmento" not in CODIGO
    assert "DROP COLUMN id" not in CODIGO
    assert "conrelid = 'public.pregunta_fuente'::regclass AND contype = 'p'" in CODIGO


def test_reutiliza_versionado_documental_y_verifica_todas_las_fuentes():
    snapshot = CODIGO.split("CREATE FUNCTION public.snapshot_documento_faq(")[1].split("$$;")[0]
    for campo in ("indexado_en", "drive_modified_time", "drive_md5_checksum", "materia_id",
                  "comision", "periodo", "tipo_documental", "estado", "vigente"):
        assert f"(p_documento).{campo}" in snapshot
    guardia = CODIGO.split("CREATE FUNCTION public.fuentes_faq_vigentes(")[1].split("$$;")[0]
    assert "AND NOT EXISTS" in guardia and "LEFT JOIN public.documento" in guardia
    assert "d.vigente IS NOT TRUE" in guardia and "d.estado IS DISTINCT FROM 'activo'" in guardia
    assert "pf.version_documento IS DISTINCT FROM public.snapshot_documento_faq(d)" in guardia
    assert "cardinality(pf.fragmento_ids) = 0" in guardia
    assert "f.id = x.id AND f.documento_id = d.id" in guardia


def test_matcher_cierra_contrato_anterior_y_no_modifica_retrieval_rag():
    assert "DROP FUNCTION IF EXISTS %I.%I(%s) RESTRICT" in CODIGO
    assert not re.search(r"(?:CREATE|ALTER|DROP).*FUNCTION public\.match_fragmentos", CODIGO)
    matcher = CODIGO.split("CREATE FUNCTION public.match_pregunta_frecuente(")[1]
    matcher = matcher.split("$body$;")[0]
    assert "p_umbral double precision," in matcher
    assert "p_umbral double precision DEFAULT" not in matcher
    assert "vector_dims(p_query_embedding) <> 768" in matcher
    assert "q.periodo IS NOT DISTINCT FROM v_periodo" in matcher
    assert "q.materia_id = p_materia_id AND q.comision = v_comision" in matcher
    assert "q.materia_id IS NULL OR q.materia_id = p_materia_id" in matcher
    assert "q.confianza_evidencia >= p_umbral_evidencia" in matcher
    assert "public.fuentes_faq_vigentes(q.id)" in matcher
    assert "s.es_primaria" in matcher
    assert matcher.index("(q.materia_id IS NOT NULL) DESC") < matcher.index("LIMIT 1")


def _borrado_matcher():
    rpc = CODIGO.split("DO $rpc$")[1].split("$rpc$;")[0]
    return rpc.split("FOR v_funcion IN")[1].split("END LOOP;")[0]


def test_drop_matcher_resuelve_firma_real_y_sobrecargas_desde_catalogo():
    borrado = _borrado_matcher()
    assert "FROM pg_catalog.pg_proc AS p" in borrado
    assert "JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace" in borrado
    assert "pg_catalog.pg_get_function_identity_arguments(p.oid) AS argumentos" in borrado
    assert "n.nspname = 'public'" in borrado
    assert "p.proname = 'match_pregunta_frecuente'" in borrado
    assert "p.prokind = 'f'" in borrado
    assert "v_funcion.nspname, v_funcion.proname, v_funcion.argumentos" in borrado
    # Ni la firma histórica ni la real limitan la selección de sobrecargas.
    assert "proargtypes" not in borrado and "pronargs" not in borrado
    assert "vector, integer, varchar, double precision" not in borrado
    assert "vector, double precision, integer, text" not in borrado
    assert "DROP FUNCTION public.match_pregunta_frecuente(" not in CODIGO


def test_drop_matcher_admite_ausencia_y_no_exige_una_sola_sobrecarga():
    borrado = _borrado_matcher()
    assert "ORDER BY p.oid" in borrado
    assert "DROP FUNCTION IF EXISTS %I.%I(%s) RESTRICT" in borrado
    assert "LIMIT 1" not in borrado and "INTO STRICT" not in borrado
    assert "RAISE" not in borrado


def test_error_de_dependencia_en_drop_revierte_migracion_sin_cascade():
    borrado = _borrado_matcher()
    assert "RESTRICT" in borrado and "CASCADE" not in borrado
    assert "EXCEPTION" not in borrado
    assert "COMMIT" not in borrado and "ROLLBACK" not in borrado
    assert CODIGO.index("BEGIN;") < CODIGO.index("FOR v_funcion IN") < CODIGO.rindex("COMMIT;")


def test_definers_acotados_y_resolucion_segura_del_operador():
    assert CODIGO.count("SECURITY DEFINER") == 4
    assert CODIGO.count("SECURITY DEFINER SET search_path = pg_catalog, pg_temp") == 4
    assert "OPERATOR(%1$I.<=>)" in CODIGO
    for funcion, firma in (
        ("obtener_fuentes_cache_faq", "bigint, timestamp without time zone"),
        ("guardar_faq_curada", "jsonb, jsonb"),
        ("invalidar_faq_documento", ""),
    ):
        assert f"REVOKE ALL ON FUNCTION public.{funcion}({firma}) FROM PUBLIC, anon, authenticated" in CODIGO
    assert "GRANT EXECUTE ON FUNCTION public.invalidar_faq_documento() TO service_role" in CODIGO
    assert not re.search(r"GRANT[^;]*invalidar_faq_documento\(\)[^;]*TO anon", CODIGO, re.S)


def test_upsert_atomico_comprueba_snapshot_y_preserva_aciertos():
    cuerpo = CODIGO.split("CREATE FUNCTION public.guardar_faq_curada(")[1].split("$body$;")[0]
    assert "FOR SHARE" in cuerpo
    assert "ORDER BY x->>'drive_id'" in cuerpo
    assert "s->'version_documento' IS DISTINCT FROM public.snapshot_documento_faq(d)" in cuerpo
    assert "IF v_primarias = 0" in cuerpo
    assert "ON CONFLICT" in cuerpo and "DO UPDATE SET" in cuerpo
    actualizacion = cuerpo.split("DO UPDATE SET")[1].split("RETURNING")[0]
    assert "aciertos" not in actualizacion
    assert "DELETE FROM public.pregunta_fuente WHERE pregunta_frecuente_id = v_id" in cuerpo
    assert "INSERT INTO public.pregunta_fuente" in cuerpo
    assert "EXCEPTION WHEN" not in cuerpo  # Un error revierte FAQ y relaciones juntas.


def test_hit_vincula_respuesta_y_fuentes_a_la_misma_generacion():
    getter = CODIGO.split("CREATE FUNCTION public.obtener_fuentes_cache_faq(")[1].split("$$;")[0]
    assert "p_generada_en_esperada timestamp without time zone" in getter
    assert "q.generada_en = p_generada_en_esperada" in getter
    matcher = CODIGO.split("CREATE FUNCTION public.match_pregunta_frecuente(")[1].split("$body$;")[0]
    assert "generada_en timestamp without time zone" in matcher
    assert "q.confianza_evidencia, q.generada_en" in matcher


def test_regeneracion_limita_espera_y_revierte_publicacion_fuera_de_plazo():
    cuerpo = CODIGO.split("CREATE FUNCTION public.guardar_faq_curada(")[1].split("$body$;")[0]
    assert "(p_faq->>'fecha_limite')::timestamptz" in cuerpo
    assert "set_config('lock_timeout'" in cuerpo
    assert cuerpo.index("clock_timestamp() >= v_fecha_limite") < cuerpo.index("FOR SHARE")
    assert cuerpo.rindex("clock_timestamp() >= v_fecha_limite") > cuerpo.index("INSERT INTO public.pregunta_fuente")
    assert "ADD COLUMN fecha_limite" not in CODIGO


def test_invalidacion_sql_no_genera_ni_sincroniza_y_contador_es_atomico():
    trigger = CODIGO.split("CREATE FUNCTION public.invalidar_faq_documento()")[1].split("$$;")[0]
    assert "SET estado = 'pendiente'" in trigger
    assert "pf.drive_id = OLD.drive_id" in trigger
    assert not re.search(r"\b(?:http|gemini|groq|embed|sincronizar)\b", trigger, re.I)
    contador = CODIGO.split("CREATE OR REPLACE FUNCTION public.incrementar_acierto_faq(")[1].split("$$;")[0]
    assert "SET aciertos = q.aciertos + 1" in contador
    assert "q.estado = 'activa'" in contador
    assert "fuentes_faq_vigentes" not in contador  # No consultar tabla protegida como anon.


def test_indices_solo_btree_y_no_embarcan_busqueda_aproximada():
    assert "CREATE INDEX idx_faq_contexto_activo" in CODIGO
    assert "CREATE INDEX idx_faq_pendiente" in CODIGO
    assert "CREATE INDEX idx_pregunta_fuente_drive" in CODIGO
    assert "using hnsw" not in CODIGO.lower()
    assert "using ivfflat" not in CODIGO.lower()


def test_prueba_manual_es_sintetica_y_reversible_no_se_ejecuta_desde_pytest():
    manual = (BACKEND / "tests/sql/validar_cache_faq.sql").read_text(encoding="utf-8")
    assert "__prueba_faq_0007_" in manual
    assert "SET LOCAL ROLE anon" in manual
    assert "Se publicó una fuente obsoleta" in manual
    assert "Se mezclaron una respuesta anterior y fuentes nuevas" in manual
    assert manual.rstrip().endswith("ROLLBACK;")
    assert "COMMIT;" not in manual

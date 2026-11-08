"""Contrato estático de 0008; no ejecuta SQL ni prueba ACL efectivas de PostgreSQL."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parents[2] / "migrations" /
       "0008_seguridad_supabase.sql").read_text(encoding="utf-8")
SIN_COMENTARIOS = re.sub(r"--[^\n]*", "", SQL)


def test_seguridad_es_una_migracion_separada_y_transaccional():
    assert "0007_cache_faq_consistente.sql" in SQL
    assert "SUPABASE_BACKEND_KEY" in SQL
    assert "BEGIN;" in SIN_COMENTARIOS and SIN_COMENTARIOS.rstrip().endswith("COMMIT;")
    assert "SET LOCAL search_path = pg_catalog, pg_temp;" in SIN_COMENTARIOS
    assert not re.search(r"\b(?:INSERT|DELETE|UPDATE)\s+(?:INTO|FROM|public\.)", SIN_COMENTARIOS)
    assert "SECURITY DEFINER" not in SIN_COMENTARIOS


def test_rls_y_acl_no_se_limitan_a_permisos_de_tabla():
    for tabla in ("documento", "fragmento", "pregunta_frecuente", "pregunta_fuente"):
        assert f"'{tabla}'" in SIN_COMENTARIOS
    assert "ENABLE ROW LEVEL SECURITY" in SIN_COMENTARIOS
    assert "FROM PUBLIC, anon, authenticated" in SIN_COMENTARIOS
    assert "REVOKE ALL PRIVILEGES (%s) ON TABLE" in SIN_COMENTARIOS
    assert "REVOKE ALL PRIVILEGES ON SEQUENCE" in SIN_COMENTARIOS
    assert "has_table_privilege" in SIN_COMENTARIOS
    assert "has_any_column_privilege" in SIN_COMENTARIOS
    assert "has_sequence_privilege" in SIN_COMENTARIOS


def test_grants_de_escritura_no_habilitan_borrado_fisico_documental():
    assert "GRANT SELECT, INSERT, UPDATE ON public.documento TO service_role;" in SIN_COMENTARIOS
    assert "GRANT SELECT, INSERT, DELETE ON public.fragmento TO service_role;" in SIN_COMENTARIOS
    assert ("GRANT SELECT, INSERT, UPDATE ON public.pregunta_frecuente TO service_role;"
            in SIN_COMENTARIOS)
    assert ("GRANT SELECT, INSERT, UPDATE, DELETE ON public.pregunta_fuente TO service_role;"
            in SIN_COMENTARIOS)
    assert not re.search(r"GRANT[^;]+\bTO\s+(?:PUBLIC|anon|authenticated)\b", SIN_COMENTARIOS)


def test_rpc_se_restringen_por_nombre_y_todas_las_sobrecargas():
    for nombre in ("match_fragmentos", "match_fragmentos_academicos", "gestionar_bloqueo_sync",
                   "verificar_bloqueo_sync", "match_pregunta_frecuente", "guardar_faq_curada",
                   "obtener_fuentes_faq", "obtener_fuentes_cache_faq", "incrementar_acierto_faq"):
        assert f"'{nombre}'" in SIN_COMENTARIOS
    assert "p.proname = ANY(v_funciones)" in SIN_COMENTARIOS
    assert "pg_get_function_identity_arguments" in SIN_COMENTARIOS
    assert "GRANT EXECUTE ON FUNCTION %I.%I(%s) TO service_role" in SIN_COMENTARIOS
    assert "has_function_privilege" in SIN_COMENTARIOS
    assert "ALL FUNCTIONS IN SCHEMA" not in SIN_COMENTARIOS


def test_lock_permanece_sin_acceso_directo_y_vector_resuelve_su_esquema():
    assert "REVOKE ALL PRIVILEGES ON TABLE public.rag_sync_bloqueo" in SIN_COMENTARIOS
    assert not re.search(r"GRANT[^;]+ON(?: TABLE)? public\.rag_sync_bloqueo", SIN_COMENTARIOS)
    assert "pg_catalog.pg_extension" in SIN_COMENTARIOS and "e.extnamespace" in SIN_COMENTARIOS
    assert "GRANT USAGE ON TYPE %I.vector TO service_role" in SIN_COMENTARIOS
    assert "rolname = 'service_role' AND rolbypassrls" in SIN_COMENTARIOS

"""Contratos estáticos de 0005. No ejecuta SQL ni demuestra su validez en PostgreSQL."""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
SQL = (BACKEND / "migrations/0005_tipo_documental.sql").read_text(encoding="utf-8")


def test_columna_nullable_sin_default_y_check_canonico():
    assert "ADD COLUMN tipo_documental varchar NULL" in SQL
    assert "CHECK (tipo_documental IN ('general', 'programa', 'cronograma'))" in SQL
    assert "ALTER COLUMN" not in SQL


def test_backfill_no_toca_vectores_ni_metadata_academica():
    cuerpo = SQL.split("DO $backfill$")[1].split("$backfill$;")[0]
    assert "UPDATE public.documento SET tipo_documental = v_tipo WHERE id = d.id" in cuerpo
    assert "v_cantidad = 1 AND d.materia_id = v_materia" in cuerpo
    assert "d.comision IS NOT DISTINCT FROM v_comision" in cuerpo
    assert "d.periodo = v_periodo" in cuerpo
    assert "^COM-[A-Z0-9]{1,8}$" in cuerpo
    assert "fragmento" not in cuerpo and "indexado_en" not in cuerpo
    assert "pg_catalog.normalize" in cuerpo and "CONTINUE" in cuerpo


def test_rpc_generica_no_se_reemplaza_y_no_hay_privilegios_elevados():
    assert not re.search(r"(?:CREATE|ALTER|DROP).*FUNCTION public\.match_fragmentos\(", SQL)
    assert "SECURITY DEFINER" not in SQL and "rag_sync_bloqueo" not in SQL
    assert SQL.count("SECURITY INVOKER") == 3
    assert not re.search(r"GRANT\s+(?!EXECUTE)", SQL)
    assert SQL.count("FROM PUBLIC") == SQL.count("TO anon") == 3


def test_wrappers_reutilizan_0004_y_preservan_atomicidad():
    assert "FROM public.actualizar_metadata_documento_sync(" in SQL
    assert "FROM public.reemplazar_fragmentos_documento_academico(" in SQL
    assert "DELETE FROM" not in SQL and "EXCEPTION WHEN" not in SQL
    assert "SET search_path = pg_catalog" in SQL and SQL.rstrip().endswith("COMMIT;")


def test_match_academico_filtra_antes_del_limite():
    cuerpo = SQL.split("CREATE FUNCTION public.match_fragmentos_academicos(")[1]
    assert "d.vigente IS TRUE" in cuerpo and "lower(btrim(d.estado)) = 'activo'" in cuerpo
    assert "d.materia_id = p_materia_id" in cuerpo
    assert "upper(btrim(d.comision)) = v_comision" in cuerpo
    assert "v_periodo IS NULL OR upper(btrim(d.periodo)) = v_periodo" in cuerpo
    assert "d.tipo_documental = p_tipo_documental" in cuerpo
    assert "p_tipo_documental = 'programa'" in cuerpo and "d.comision IS NULL" in cuerpo
    assert cuerpo.index("WHERE lower") < cuerpo.index("ORDER BY f.vector") < cuerpo.index("LIMIT p_top_k")
    assert "vector_dims(p_query_embedding) <> 768" in cuerpo
    assert "OPERATOR(%1$I.<=>)" in cuerpo


def test_vigencia_conserva_contrato_rechaza_false_y_exige_true():
    cuerpo = SQL.split("CREATE FUNCTION public.match_fragmentos_academicos(")[1]
    assert "p_vigente boolean DEFAULT true" in cuerpo
    assert "IF p_vigente IS FALSE THEN" in cuerpo
    assert "p_vigente sólo admite NULL o true" in cuerpo
    assert "d.vigente IS TRUE" in cuerpo
    assert "d.vigente = p_vigente" not in cuerpo
    assert cuerpo.index("IF p_vigente IS FALSE") < cuerpo.index("RETURN QUERY")


def test_comision_y_periodo_validan_normalizados_y_no_admiten_vacios():
    cuerpo = SQL.split("CREATE FUNCTION public.match_fragmentos_academicos(")[1]
    assert "v_comision text := NULLIF(upper(btrim(p_comision)), '')" in cuerpo
    assert "v_periodo text := NULLIF(upper(btrim(p_periodo)), '')" in cuerpo
    for parametro, normalizado, patron in (
        ("p_comision", "v_comision", "^[A-Z0-9]{1,32}$"),
        ("p_periodo", "v_periodo", "^[0-9]{4}C[12]$"),
    ):
        guardia = f"IF {parametro} IS NOT NULL AND ({normalizado} IS NULL OR {normalizado} !~ '{patron}')"
        assert guardia in cuerpo
        assert cuerpo.index(guardia) < cuerpo.index("RETURN QUERY")
    assert cuerpo.count("ERRCODE = '22023'") == 3


def test_sql_manual_es_rollback_y_usa_anon():
    manual = (BACKEND / "tests/sql/validar_tipo_documental.sql").read_text(encoding="utf-8")
    assert "SET LOCAL ROLE anon" in manual and manual.rstrip().endswith("ROLLBACK;")
    assert "COMMIT;" not in manual

"""Comprobaciones estáticas del contrato SQL; no ejecutan la migración."""

from pathlib import Path
import re

SQL = (Path(__file__).resolve().parents[2] / "migrations/0004_metadata_academica.sql").read_text(encoding="utf-8")


def cuerpo(nombre):
    return re.search(rf"CREATE FUNCTION public\.{nombre}\([\s\S]+?AS \$\$([\s\S]+?)\$\$;", SQL).group(1)


def test_solo_dos_rpc_invoker_sin_privilegios_de_tabla():
    assert SQL.count("CREATE FUNCTION") == SQL.count("SECURITY INVOKER") == 2
    assert "SECURITY DEFINER" not in SQL
    assert SQL.count("SET search_path = pg_catalog") == 2
    assert "rag_sync_bloqueo" not in SQL
    assert not re.search(r"GRANT\s+(?!EXECUTE)", SQL)
    assert "ALTER TABLE" not in SQL and "CREATE TABLE" not in SQL


def test_public_no_puede_ejecutar_y_anon_si():
    assert SQL.count("FROM PUBLIC") == SQL.count("TO anon") == 2
    for funcion in ("actualizar_metadata_documento_sync", "reemplazar_fragmentos_documento_academico"):
        assert f"REVOKE EXECUTE ON FUNCTION public.{funcion}" in SQL
        assert f"GRANT EXECUTE ON FUNCTION public.{funcion}" in SQL


def test_actualizacion_bloqueada_por_identidad_y_no_toca_fragmentos():
    funcion = cuerpo("actualizar_metadata_documento_sync")
    assert "public.verificar_bloqueo_sync(p_propietario)" in funcion
    assert "WHERE d.drive_id = p_drive_id FOR UPDATE" in funcion
    assert "fragmento" not in funcion and "indexado_en" not in funcion
    assert "materia_id = p_materia_id" in funcion and "comision = p_comision" in funcion
    assert "periodo = p_periodo" in funcion and "COALESCE" not in funcion
    assert "p_comision = 'GENERAL'" in funcion


def test_reemplazo_reutiliza_rpc_versionada_en_la_misma_funcion():
    funcion = cuerpo("reemplazar_fragmentos_documento_academico")
    assert "public.reemplazar_fragmentos_documento_versionado(" in funcion
    assert "public.actualizar_metadata_documento_sync(" in funcion
    assert "DELETE" not in funcion and "INSERT" not in funcion
    assert "EXCEPTION WHEN" not in funcion  # No tragar fallos que deben revertir la transacción.
    assert SQL.rstrip().endswith("COMMIT;")


def test_validacion_manual_es_sintetica_con_rollback():
    manual = (Path(__file__).resolve().parents[1] / "sql/validar_metadata_academica.sql").read_text(encoding="utf-8")
    assert "SET LOCAL ROLE anon" in manual
    assert "ROLLBACK;" in manual and "COMMIT;" not in manual
    assert "indexado_en" in manual and "foreign_key_violation" in manual

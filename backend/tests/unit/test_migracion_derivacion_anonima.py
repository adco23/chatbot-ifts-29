"""Contrato estático de 0009; no ejecuta SQL ni verifica permisos efectivos de PostgreSQL."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parents[2] / "migrations" /
       "0009_derivacion_anonima.sql").read_text(encoding="utf-8")
SQL = re.sub(r"--[^\n]*", "", SQL)


def test_tabla_minima_anonima_con_identidad_y_timestamp_db():
    tabla = re.search(r"CREATE TABLE public\.derivacion_evento \((.*?)\n\);", SQL, re.S)[1]
    columnas = [linea.strip().split()[0] for linea in tabla.splitlines()
                if linea.strip() and not linea.strip().startswith("CONSTRAINT")]
    assert columnas == ["id", "sector", "creado_at"]
    assert "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY" in tabla
    assert "sector VARCHAR(200) NOT NULL" in tabla
    assert "TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP" in tabla
    assert "CHECK (sector ~ '[^[:space:]]')" in tabla
    assert "REFERENCES" not in SQL


def test_tabla_y_secuencia_sin_acceso_directo():
    assert "ALTER TABLE public.derivacion_evento ENABLE ROW LEVEL SECURITY;" in SQL
    assert re.search(r"REVOKE ALL PRIVILEGES ON TABLE public.derivacion_evento\s+"
                     r"FROM PUBLIC, anon, authenticated, service_role;", SQL)
    assert "REVOKE ALL PRIVILEGES (id, sector, creado_at)" in SQL
    assert "pg_catalog.pg_get_serial_sequence" in SQL
    assert "REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM PUBLIC, anon, authenticated, service_role" in SQL
    assert "CREATE POLICY" not in SQL and "GRANT SELECT" not in SQL
    assert "GRANT INSERT" not in SQL


def test_rpc_recibe_solo_sector_y_devuelve_booleano():
    assert "CREATE FUNCTION public.registrar_derivacion_anonima(p_sector text)" in SQL
    assert "RETURNS boolean" in SQL
    assert "v_sector IS NULL" in SQL and "v_sector !~ '[^[:space:]]'" in SQL
    assert "pg_catalog.char_length(v_sector) > 200" in SQL
    assert "INSERT INTO public.derivacion_evento (sector) VALUES (v_sector);" in SQL
    assert "RETURN true;" in SQL and "RETURNS TABLE" not in SQL


def test_rpc_definer_con_resolution_segura_y_execute_restringido():
    assert "LANGUAGE plpgsql SECURITY DEFINER" in SQL
    assert "SET search_path = pg_catalog, pg_temp" in SQL
    assert re.search(r"REVOKE ALL PRIVILEGES ON FUNCTION public\.registrar_derivacion_anonima\(text\)"
                     r"\s+FROM PUBLIC, anon, authenticated, service_role;", SQL)
    grants = re.findall(r"GRANT[^;]+;", SQL)
    assert grants == [
        "GRANT EXECUTE ON FUNCTION public.registrar_derivacion_anonima(text) TO service_role;",
    ]


def test_migracion_transaccional_y_limitada_a_objetos_nuevos():
    assert SQL.strip().startswith("BEGIN;") and SQL.rstrip().endswith("COMMIT;")
    assert SQL.count("CREATE TABLE") == 1 and SQL.count("CREATE FUNCTION") == 1
    assert "DROP " not in SQL and "UPDATE " not in SQL and "DELETE " not in SQL
    assert "ALTER DEFAULT PRIVILEGES" not in SQL and "CREATE TRIGGER" not in SQL

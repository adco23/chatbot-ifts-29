"""Regresión estática del SQL; los permisos efectivos se prueban manualmente en PG.

No ejecuta migraciones ni SQL: detecta reintroducción de GRANT y ampliaciones
accidentales de SECURITY DEFINER antes de aplicar 0003.
"""

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
MIGRACION = BACKEND / "migrations" / "0003_sincronizacion_incremental.sql"
PRUEBA_SQL = BACKEND / "tests" / "sql" / "validar_sincronizacion_incremental.sql"
LOCK = {"gestionar_bloqueo_sync", "verificar_bloqueo_sync"}
ESCRITURAS = {"reemplazar_fragmentos_documento_versionado", "actualizar_estado_documento_sync"}


def sql_sin_comentarios(ruta):
    return re.sub(r"--[^\n]*", "", ruta.read_text(encoding="utf-8"))


def funciones():
    return {
        nombre: (cabecera, cuerpo)
        for nombre, cabecera, cuerpo in re.findall(
            r"CREATE FUNCTION public\.(\w+)\s*\((.*?)\bAS\s+\$\$(.*?)\$\$;",
            sql_sin_comentarios(MIGRACION), re.S | re.I,
        )
    }


def test_tabla_privada_sin_grants_directos():
    sql = sql_sin_comentarios(MIGRACION)
    assert re.search(
        r"REVOKE ALL PRIVILEGES ON TABLE public\.rag_sync_bloqueo FROM PUBLIC, anon;", sql,
    )
    grants = re.findall(r"\bGRANT\b[^;]+;", sql, re.S | re.I)
    assert len(grants) == 4
    assert all(re.match(r"GRANT EXECUTE ON FUNCTION public\.", grant) for grant in grants)
    assert all("rag_sync_bloqueo" not in grant for grant in grants)


@pytest.mark.parametrize("nombre", sorted(LOCK))
def test_definer_solo_para_acceder_al_lock(nombre):
    cabecera, cuerpo = funciones()[nombre]
    assert "SECURITY DEFINER SET search_path = pg_catalog, pg_temp" in cabecera
    assert "SECURITY INVOKER" not in cabecera
    assert not re.search(r"\bEXECUTE\b", cuerpo, re.I)  # Sin SQL dinámico privilegiado.
    # IS DISTINCT FROM es un comparador, no una lectura de tabla.
    sentencias = re.sub(r"\bIS\s+(?:NOT\s+)?DISTINCT\s+FROM\b", "COMPARADOR", cuerpo, flags=re.I)
    relaciones = re.findall(r"\b(?:UPDATE|FROM)\s+([\w.]+)", sentencias, re.I)
    assert relaciones and set(relaciones) == {"public.rag_sync_bloqueo"}
    assert not re.search(r"(?<!pg_catalog\.)\bclock_timestamp\(", cuerpo)


@pytest.mark.parametrize("nombre", sorted(ESCRITURAS))
def test_no_eleva_escrituras_documentales(nombre):
    cabecera, cuerpo = funciones()[nombre]
    assert "SECURITY INVOKER SET search_path = pg_catalog" in cabecera
    assert "SECURITY DEFINER" not in cabecera
    assert "PERFORM public.verificar_bloqueo_sync(p_propietario);" in cuerpo


def test_solo_dos_funciones_elevadas_y_api_sin_cambios():
    definiciones = funciones()
    assert set(definiciones) == LOCK | ESCRITURAS
    assert {nombre for nombre, (cabecera, _) in definiciones.items()
            if "SECURITY DEFINER" in cabecera} == LOCK
    assert "RETURNS TABLE (adquirido boolean)" in definiciones["gestionar_bloqueo_sync"][0]
    assert "RETURNS void" in definiciones["verificar_bloqueo_sync"][0]


@pytest.mark.parametrize("nombre", sorted(LOCK | ESCRITURAS))
def test_execute_restringido_y_atomico(nombre):
    sql = sql_sin_comentarios(MIGRACION)
    assert re.search(rf"REVOKE EXECUTE ON FUNCTION public\.{nombre}\([^;]+FROM PUBLIC;", sql)
    assert re.search(rf"GRANT EXECUTE ON FUNCTION public\.{nombre}\([^;]+TO anon;", sql)
    assert sql.index("BEGIN;") < sql.index(f"CREATE FUNCTION public.{nombre}")
    assert sql.index(f"GRANT EXECUTE ON FUNCTION public.{nombre}") < sql.index("COMMIT;")


def test_migracion_rechaza_herencia_y_propietario_inseguro():
    sql = sql_sin_comentarios(MIGRACION)
    assert "pg_catalog.has_table_privilege('anon', 'public.rag_sync_bloqueo'" in sql
    assert "pg_catalog.has_any_column_privilege('anon', 'public.rag_sync_bloqueo'" in sql
    assert "pg_catalog.pg_has_role('anon', p.proowner, 'MEMBER')" in sql


def test_prueba_manual_no_necesita_update_directo_para_anon():
    sql = sql_sin_comentarios(PRUEBA_SQL)
    assert sql.count("EXCEPTION WHEN insufficient_privilege THEN NULL;") == 3
    assert "pg_catalog.has_function_privilege('anon', v_funcion, 'EXECUTE')" in sql
    # La simulación del vencimiento se hace fuera del rol anon, sin agregar RPC insegura.
    assert re.search(
        r"RESET ROLE;\s*UPDATE public\.rag_sync_bloqueo.*?SET LOCAL ROLE anon;", sql, re.S,
    )
    assert not re.search(r"\bGRANT\b", sql, re.I)
    assert sql.rstrip().endswith("ROLLBACK;")

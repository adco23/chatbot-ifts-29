"""Consultas de catálogo/cobertura: contrato estricto y errores diferenciados."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.db import repositorio as r


def cliente(filas):
    base = MagicMock()
    consulta = base.table.return_value
    consulta.select.return_value = consulta
    consulta.eq.return_value = consulta
    consulta.execute.return_value = SimpleNamespace(data=filas)
    return base


def test_materias_tipadas():
    base = cliente([{"id": 4, "codigo": "PROG", "nombre": "Programación"}])
    assert r.listar_materias_chat(cliente=base)[0].id == 4
    base.table.assert_called_once_with("materia")


def test_documentos_solo_activos_vigentes():
    base = cliente([{"id": 2, "titulo": "FAQ", "drive_id": "abc"}])
    assert r.listar_documentos_chat(cliente=base)[0].materia_id is None
    assert base.table.return_value.eq.call_args_list[0].args == ("estado", "activo")
    assert base.table.return_value.eq.call_args_list[1].args == ("vigente", True)


@pytest.mark.parametrize("operacion", [r.listar_materias_chat, r.listar_documentos_chat])
def test_vacio_no_es_error(operacion):
    assert operacion(cliente=cliente([])) == []


@pytest.mark.parametrize("operacion", [r.listar_materias_chat, r.listar_documentos_chat])
def test_fallos_sin_payload(operacion):
    base = cliente([])
    base.table.return_value.execute.side_effect = RuntimeError("SECRETO")
    with pytest.raises(r.ErrorRepositorio) as error:
        operacion(cliente=base)
    assert "SECRETO" not in str(error.value)


@pytest.mark.parametrize("operacion", [r.listar_materias_chat, r.listar_documentos_chat])
def test_fila_invalida_no_es_catalogo_vacio(operacion):
    with pytest.raises(r.ErrorRepositorio):
        operacion(cliente=cliente([{"id": "invalido"}]))

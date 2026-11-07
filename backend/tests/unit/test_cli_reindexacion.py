"""Contrato CLI de reindexación individual, con servicio y proveedores simulados."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from app.providers import dependencias as llm
from app.providers.embeddings import dependencias as emb
from app import settings
from scripts import probar_rag as cli


DRIVE_ID = "drive-cronograma-sintetico"
TITULO = "2026C2__Administracion-de-Base-de-Datos__COM-A__Cronograma.docx"


def argumentos(*extras):
    return ["reindexar", "--drive-id", DRIVE_ID, "--documento-id-esperado", "64",
            "--titulo-esperado", TITULO, *extras]


@pytest.fixture
def dobles(monkeypatch):
    from app.services import reindexacion

    config = settings.Configuracion(_env_file=None)
    monkeypatch.setattr(settings, "obtener_configuracion", lambda: config)
    servicio = AsyncMock(return_value={
        "estado": "validado", "documento_id": 64, "pfo": [1, 2, 3, 4],
    })
    monkeypatch.setattr(reindexacion, "reindexar_documento", servicio)
    embeddings = Mock(side_effect=AssertionError("El CLI no debe crear proveedores."))
    generacion = Mock(side_effect=AssertionError("No corresponde generar respuestas."))
    monkeypatch.setattr(emb, "obtener_gestor_embeddings", embeddings)
    monkeypatch.setattr(llm, "obtener_gestor_proveedores", generacion)
    return servicio, embeddings, generacion, config


@pytest.mark.parametrize("modo", ["--dry-run", "--aplicar", "--verificar"])
def test_parser_modos_explicitos_y_requisitos_pfo(modo, tmp_path):
    entrada = argumentos("--exigir-pfo", "1", "2", "3", "4", modo)
    if modo != "--dry-run":
        entrada += ["--respaldo", str(tmp_path / "respaldo.json")]
    opciones = cli.crear_analizador().parse_args(entrada)
    assert opciones.comando == "reindexar"
    assert opciones.drive_id == [DRIVE_ID]
    assert opciones.documento_id_esperado == 64 and opciones.titulo_esperado == TITULO
    assert opciones.exigir_pfo == [1, 2, 3, 4]
    if modo != "--dry-run":
        assert isinstance(opciones.respaldo, Path)


@pytest.mark.parametrize("extras", [
    [], ["--dry-run", "--aplicar"], ["--aplicar", "--verificar"],
    ["--dry-run", "--verificar"], ["--dry-run", "--exigir-pfo", "0"],
    ["--dry-run", "--exigir-pfo", "-1"], ["--dry-run", "--exigir-pfo"],
])
def test_parser_rechaza_modo_ambiguo_o_requisito_invalido(extras):
    with pytest.raises(SystemExit) as error:
        cli.crear_analizador().parse_args(argumentos(*extras))
    assert error.value.code == 2


@pytest.mark.parametrize("bandera,valor", [
    ("--drive-id", DRIVE_ID), ("--documento-id-esperado", "64"),
    ("--titulo-esperado", TITULO),
])
def test_parser_exige_identidad_completa(bandera, valor):
    entrada = argumentos("--dry-run")
    indice = entrada.index(bandera)
    del entrada[indice:indice + 2]
    with pytest.raises(SystemExit) as error:
        cli.crear_analizador().parse_args(entrada)
    assert error.value.code == 2


@pytest.mark.parametrize("valor", ["0", "-1", "texto"])
def test_parser_rechaza_documento_id_no_positivo(valor):
    entrada = argumentos("--dry-run")
    entrada[entrada.index("--documento-id-esperado") + 1] = valor
    with pytest.raises(SystemExit) as error:
        cli.crear_analizador().parse_args(entrada)
    assert error.value.code == 2


@pytest.mark.parametrize("valor", ["", "   "])
@pytest.mark.parametrize("bandera", ["--drive-id", "--titulo-esperado"])
def test_parser_rechaza_identificadores_vacios(bandera, valor):
    entrada = argumentos("--dry-run")
    entrada[entrada.index(bandera) + 1] = valor
    with pytest.raises(SystemExit):
        cli.crear_analizador().parse_args(entrada)


@pytest.mark.parametrize("modo", ["--dry-run", "--aplicar", "--verificar"])
def test_cli_delega_un_solo_documento_y_no_crea_proveedores(dobles, tmp_path, capsys, modo):
    servicio, embeddings, generacion, config = dobles
    respaldo = tmp_path / "respaldo.json"
    entrada = argumentos(modo, "--exigir-pfo", "1", "2", "3", "4")
    if modo != "--dry-run":
        entrada += ["--respaldo", str(respaldo)]
    assert cli.main(entrada) == 0
    servicio.assert_awaited_once_with(
        drive_id=DRIVE_ID, documento_id_esperado=64, titulo_esperado=TITULO,
        pfo_exigidas=[1, 2, 3, 4], modo=modo.removeprefix("--"),
        respaldo=None if modo == "--dry-run" else respaldo, configuracion=config,
    )
    salida = json.loads(capsys.readouterr().out)
    assert salida == {"estado": "validado", "documento_id": 64, "pfo": [1, 2, 3, 4]}
    embeddings.assert_not_called()
    generacion.assert_not_called()


@pytest.mark.parametrize("drive_id_adicional", [DRIVE_ID, "otro-documento"])
def test_varios_drive_ids_se_rechazan_antes_del_servicio(dobles, capsys, drive_id_adicional):
    servicio, embeddings, generacion, _ = dobles
    assert cli.main(argumentos("--dry-run", "--drive-id", drive_id_adicional)) in (1, 2)
    servicio.assert_not_called()
    embeddings.assert_not_called()
    generacion.assert_not_called()


@pytest.mark.parametrize("modo", ["--aplicar", "--verificar"])
def test_respaldo_obligatorio_se_valida_antes_del_servicio(dobles, modo):
    servicio, embeddings, generacion, _ = dobles
    assert cli.main(argumentos(modo)) in (1, 2)
    servicio.assert_not_called()
    embeddings.assert_not_called()
    generacion.assert_not_called()


def test_error_operacional_informa_categoria_segura(dobles, capsys):
    from app.services.reindexacion import ErrorReindexacion

    servicio, _, _, _ = dobles
    servicio.side_effect = ErrorReindexacion("pfo_requerida_ausente")
    assert cli.main(argumentos("--dry-run", "--exigir-pfo", "1", "2", "3", "4")) == 1
    capturado = capsys.readouterr()
    salida = json.loads(capturado.out or capturado.err)
    assert salida["categoria"] == "pfo_requerida_ausente"


def test_error_inesperado_no_expone_texto_remoto(dobles, capsys, caplog):
    servicio, _, _, _ = dobles
    privado = "respuesta completa privada api-key-sintetica header sensible"
    servicio.side_effect = RuntimeError(privado)
    assert cli.main(argumentos("--dry-run")) == 1
    capturado = capsys.readouterr()
    assert privado not in capturado.out + capturado.err
    assert "api-key-sintetica" not in capturado.out + capturado.err
    assert privado not in caplog.text


def test_entrada_sin_requisitos_pfo_no_inventa_numeros(dobles):
    servicio, _, _, _ = dobles
    assert cli.main(argumentos("--dry-run")) == 0
    assert servicio.await_args.kwargs["pfo_exigidas"] == []


def test_ejecutar_async_preserva_configuracion_inyectada(dobles):
    servicio, _, _, config = dobles
    opciones = cli.crear_analizador().parse_args(argumentos("--dry-run"))
    assert asyncio.run(cli._ejecutar(opciones)) == 0
    assert servicio.await_args.kwargs["configuracion"] is config

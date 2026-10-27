"""Verificación manual de indexación y recuperación; solo se conecta al ejecutarse.

No genera respuestas con un LLM. Los comandos indexar y poc escriben documentos y
fragmentos en Supabase; preguntar solo consulta el índice existente.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.rag import DocumentoDrive, ResultadoRecuperacion

DOCUMENTOS_POR_DEFECTO = (
    "GENERAL__Preguntas-frecuentes",
    "GENERAL__Plan-de-estudios-TSDS",
    "GENERAL__Informacion-oficial-IFTS29.pdf",
)
CONSULTAS_POR_DEFECTO = (
    "¿Cómo puedo contactar al IFTS 29?",
    "¿Cuál es el plan de estudios de Desarrollo de Software?",
    "¿Cómo se realiza la inscripción?",
)
TIPOS_ADMITIDOS = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.google-apps.document",
    }
)


class ErrorSeleccion(ValueError):
    """La selección explícita no identifica documentos de forma inequívoca."""


def _coincide_nombre(nombre: str, solicitado: str) -> bool:
    if nombre == solicitado:
        return True
    if Path(solicitado).suffix.lower() in {".pdf", ".docx"}:
        return False
    return nombre in {f"{solicitado}.pdf", f"{solicitado}.docx"}


def seleccionar_documentos(
    archivos: Sequence[dict],
    *,
    nombres: Sequence[str] = (),
    drive_ids: Sequence[str] = (),
) -> list[DocumentoDrive]:
    """Resuelve nombres exactos o bases PDF/DOCX; rechaza nombres ambiguos."""
    from app.models.rag import DocumentoDrive

    seleccionados: list[dict] = []
    for nombre in nombres:
        coincidencias = [
            archivo
            for archivo in archivos
            if _coincide_nombre(archivo.get("name", ""), nombre)
        ]
        if not coincidencias:
            raise ErrorSeleccion(f"No se encontró el documento solicitado: {nombre}.")
        if len(coincidencias) != 1:
            raise ErrorSeleccion(f"Nombre ambiguo: {nombre}. Seleccioná con --drive-id.")
        seleccionados.append(coincidencias[0])
    for drive_id in drive_ids:
        coincidencias = [archivo for archivo in archivos if archivo.get("id") == drive_id]
        if len(coincidencias) != 1:
            raise ErrorSeleccion("No se pudo resolver un identificador de Drive solicitado.")
        seleccionados.append(coincidencias[0])
    documentos: list[DocumentoDrive] = []
    vistos: set[str] = set()
    for archivo in seleccionados:
        if archivo.get("mimeType") not in TIPOS_ADMITIDOS:
            raise ErrorSeleccion("La selección contiene un formato no admitido.")
        if not archivo.get("id") or not archivo.get("name"):
            raise ErrorSeleccion("Drive devolvió metadatos incompletos.")
        if archivo["id"] in vistos:
            continue
        vistos.add(archivo["id"])
        documentos.append(
            DocumentoDrive(
                drive_id=archivo["id"],
                titulo=archivo["name"],
                tipo_mime=archivo["mimeType"],
                enlace=archivo.get("webViewLink"),
                drive_modified_time=archivo.get("modifiedTime"),
                drive_md5_checksum=archivo.get("md5Checksum"),
            )
        )
    if not documentos:
        raise ErrorSeleccion("La selección debe incluir al menos un documento.")
    return documentos


def _entero_positivo(valor: str) -> int:
    try:
        numero = int(valor)
    except ValueError:
        raise argparse.ArgumentTypeError("Se esperaba un número entero positivo.") from None
    if numero <= 0:
        raise argparse.ArgumentTypeError("Se esperaba un número entero positivo.")
    return numero


def _texto_no_vacio(valor: str) -> str:
    texto = valor.strip()
    if not texto:
        raise argparse.ArgumentTypeError("Se esperaba un texto con contenido.")
    return texto


def crear_analizador() -> argparse.ArgumentParser:
    analizador = argparse.ArgumentParser(description=__doc__)
    comandos = analizador.add_subparsers(dest="comando", required=True)
    for nombre in ("indexar", "preguntar", "poc"):
        comando = comandos.add_parser(nombre)
        comando.add_argument(
            "--drive-id",
            action="append",
            type=_texto_no_vacio,
            default=[],
            help="ID exacto de Drive.",
        )
        if nombre != "preguntar":
            comando.add_argument(
                "--nombre",
                action="append",
                type=_texto_no_vacio,
                help="Nombre exacto o base sin extensión PDF/DOCX.",
            )
        if nombre != "indexar":
            comando.add_argument("--top-k", type=_entero_positivo)
            comando.add_argument("--materia-id", type=_entero_positivo)
            comando.add_argument("--comision")
            comando.add_argument("--periodo")
            comando.add_argument("--tipo-documental", choices=("general", "programa", "cronograma"))
            comando.add_argument("--vigente", choices=("true", "false", "todos"), default="true")
            if nombre == "preguntar":
                comando.add_argument("--consulta", type=_texto_no_vacio, required=True)
                comando.add_argument("--documento-id", type=_entero_positivo, action="append")
            else:
                comando.add_argument("--consulta", type=_texto_no_vacio, action="append")
    return analizador


async def _resolver_seleccion(argumentos: argparse.Namespace) -> list[DocumentoDrive]:
    from app.drive.cliente import listar_archivos_institucionales, obtener_metadatos_archivo

    nombres = argumentos.nombre
    if nombres is None:
        nombres = [] if argumentos.drive_id else list(DOCUMENTOS_POR_DEFECTO)
    archivos = (
        await asyncio.to_thread(listar_archivos_institucionales, estricto=True) if nombres else []
    )
    por_id = {archivo["id"]: archivo for archivo in archivos}
    for drive_id in argumentos.drive_id:
        if drive_id not in por_id:
            por_id[drive_id] = await asyncio.to_thread(obtener_metadatos_archivo, drive_id)
    return seleccionar_documentos(
        list(por_id.values()), nombres=nombres, drive_ids=argumentos.drive_id
    )


def mostrar_recuperacion(consulta: str, resultado: ResultadoRecuperacion) -> None:
    print(f"\nConsulta: {consulta}")
    mejor = "sin resultados"
    if resultado.mejor_similitud is not None:
        mejor = f"{resultado.mejor_similitud:.4f}"
    print(
        f"Mejor similitud: {mejor}; confianza: {resultado.confianza:.4f}; "
        f"umbral: {resultado.umbral:.4f}; "
        f"evidencia suficiente: {'sí' if resultado.evidencia_suficiente else 'no'}"
    )
    if not resultado.fragmentos:
        print("Sin fragmentos para los filtros solicitados.")
    for posicion, fragmento in enumerate(resultado.fragmentos, start=1):
        categoria = ("complementaria" if resultado.fragmento_ids_primarios is not None
                     and fragmento.fragmento_id not in resultado.fragmento_ids_primarios else "primaria")
        print(
            f"{posicion}. {fragmento.titulo} · documento {fragmento.documento_id} · "
            f"fragmento {fragmento.fragmento_id} · orden {fragmento.orden} · "
            f"similitud {fragmento.similitud:.4f} · tipo {fragmento.tipo_documental or 'sin tipo'} · "
            f"evidencia {categoria}"
        )
        print(fragmento.contenido)


def _formatear_fecha_utc(fecha: datetime) -> str:
    # La columna existente no tiene zona, pero la RPC escribe sus valores en UTC.
    instante = fecha.replace(tzinfo=UTC) if fecha.tzinfo is None else fecha
    return instante.astimezone(UTC).isoformat()


async def _ejecutar(argumentos: argparse.Namespace) -> int:
    from app.db.repositorio import obtener_documento_por_drive_id
    from app.models.rag import FiltrosRecuperacion
    from app.providers.embeddings.dependencias import obtener_gestor_embeddings
    from app.services.recuperador import recuperar_fragmentos
    from app.services.sincronizador import run_full_sync
    from app.services.validacion_embeddings_rag import validar_configuracion_rag
    from app.settings import obtener_configuracion

    config = obtener_configuracion()
    validar_configuracion_rag(config)
    gestor = obtener_gestor_embeddings()
    try:
        documento_ids: list[int] | None = None
        if argumentos.comando in {"indexar", "poc"}:
            documentos = await _resolver_seleccion(argumentos)
            if argumentos.comando == "poc" and not 2 <= len(documentos) <= 3:
                raise ErrorSeleccion("La prueba poc requiere seleccionar dos o tres documentos.")
            sincronizacion = await run_full_sync(documentos, gestor=gestor, configuracion=config)
            for resultado in sincronizacion.resultados:
                if resultado.error is not None:
                    print(f"No indexado: {resultado.titulo} ({resultado.error}).", file=sys.stderr)
                else:
                    resumen = resultado.resumen
                    print(
                        f"Indexado: {resultado.titulo}; documento {resumen.documento_id}; "
                        f"fragmentos {resumen.fragmentos_indexados}; "
                        f"{_formatear_fecha_utc(resumen.indexado_en)} UTC"
                    )
            if sincronizacion.errores:
                return 1
            if argumentos.comando == "indexar":
                return 0
            documento_ids = [
                resultado.resumen.documento_id for resultado in sincronizacion.resultados
            ]
        else:
            documento_ids = list(argumentos.documento_id or [])
            for drive_id in argumentos.drive_id:
                documento = await asyncio.to_thread(obtener_documento_por_drive_id, drive_id)
                if documento is None:
                    raise ErrorSeleccion("Un identificador de Drive no está indexado.")
                documento_ids.append(documento.id)
            documento_ids = list(dict.fromkeys(documento_ids)) or None
        filtros = FiltrosRecuperacion(
            materia_id=argumentos.materia_id,
            comision=argumentos.comision,
            periodo=argumentos.periodo,
            vigente={"true": True, "false": False, "todos": None}[argumentos.vigente],
            tipo_documental=argumentos.tipo_documental,
        )
        consultas = (
            [argumentos.consulta]
            if argumentos.comando == "preguntar"
            else argumentos.consulta or CONSULTAS_POR_DEFECTO
        )
        for consulta in consultas:
            resultado = await recuperar_fragmentos(
                consulta,
                filtros=filtros,
                top_k=argumentos.top_k,
                documento_ids=documento_ids,
                gestor=gestor,
                configuracion=config,
            )
            mostrar_recuperacion(consulta, resultado)
        return 0
    finally:
        for proveedor in gestor.proveedores:
            cerrar = getattr(proveedor, "aclose", None)
            if cerrar is not None:
                try:
                    await cerrar()
                except Exception:
                    print("No se pudo cerrar un cliente de embeddings.", file=sys.stderr)


def main(argumentos: Sequence[str] | None = None) -> int:
    # El import del módulo no cambia sys.path ni carga configuración o clientes.
    ruta_backend = str(Path(__file__).resolve().parents[1])
    if ruta_backend not in sys.path:
        sys.path.insert(0, ruta_backend)
    opciones = crear_analizador().parse_args(argumentos)
    try:
        return asyncio.run(_ejecutar(opciones))
    except ErrorSeleccion as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"No se pudo completar la operación ({type(error).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

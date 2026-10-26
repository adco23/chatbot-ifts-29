"""CLI incremental y verificación posterior a la aplicación MANUAL de 0004.

ejecutar escribe en Supabase. verificar sólo lee y consulta embeddings, sin LLM.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path


def crear_analizador():
    analizador = argparse.ArgumentParser(description=__doc__)
    comandos = analizador.add_subparsers(dest="comando", required=True)
    comandos.add_parser("ejecutar")
    comandos.add_parser("validar-nombres", help="Sólo lee Drive y catálogo; no descarga ni escribe.")
    verificar = comandos.add_parser("verificar")
    verificar.add_argument("--documento-id", type=int, action="append", required=True)
    verificar.add_argument("--consulta", action="append", required=True)
    return analizador


async def _ejecutar(argumentos):
    from app.db import repositorio
    from app.providers.embeddings.dependencias import obtener_gestor_embeddings
    from app.services.recuperador import recuperar_fragmentos
    from app.services.sincronizador import SincronizacionEnCurso, sincronizar_drive_completo

    gestor = None
    try:
        if argumentos.comando == "validar-nombres":
            from app.drive.cliente import listar_documentos_sincronizables
            from app.services.metadata_documental import (
                ErrorMetadataDocumental, resolver_metadata_documental,
            )
            from app.settings import obtener_configuracion

            config = obtener_configuracion()
            catalogo = await asyncio.to_thread(repositorio.listar_materias_chat)
            documentos = await asyncio.to_thread(listar_documentos_sincronizables)
            resultados = []
            for documento in documentos:
                fila = {"drive_id": documento.drive_id, "nombre": documento.titulo}
                try:
                    fila["metadata"] = resolver_metadata_documental(
                        documento.titulo, catalogo,
                        comision_max_longitud=config.metadata_comision_max_longitud,
                        tipos_documentales=config.metadata_tipos_documentales,
                    ).model_dump()
                except ErrorMetadataDocumental as error:
                    fila["error"] = error.categoria
                resultados.append(fila)
            invalidos = [r for r in resultados if "error" in r]
            print(json.dumps({"documentos": resultados, "requieren_correccion": invalidos},
                             ensure_ascii=False, indent=2))
            return 1 if invalidos else 0
        if argumentos.comando == "ejecutar":
            # Se usa el gestor central; no se crean adaptadores independientes.
            gestor = obtener_gestor_embeddings()
            resumen = await sincronizar_drive_completo(gestor=gestor)
            print(resumen.model_dump_json(indent=2))
            return 0 if resumen.estado == "ok" else 1
        if any(i <= 0 for i in argumentos.documento_id) or any(
            not consulta.strip() for consulta in argumentos.consulta
        ):
            raise ValueError("Argumentos inválidos.")
        ids = set(argumentos.documento_id)
        documentos = await asyncio.to_thread(repositorio.listar_documentos_sync)
        encontrados = [d for d in documentos if d.id in ids]
        correcto = len(encontrados) == len(ids)
        estado = []
        for documento in encontrados:
            cantidad = await asyncio.to_thread(
                repositorio.contar_fragmentos_documento, documento.id,
            )
            valido = documento.estado == "inactivo" and documento.vigente is False and cantidad > 0
            correcto = correcto and valido
            estado.append({"documento_id": documento.id, "estado": documento.estado,
                           "vigente": documento.vigente, "fragmentos_conservados": cantidad})
        gestor = obtener_gestor_embeddings()
        recuperaciones = []
        for numero, consulta in enumerate(argumentos.consulta, start=1):
            resultado = await recuperar_fragmentos(
                consulta, documento_ids=sorted(ids), gestor=gestor,
            )
            correcto = correcto and not resultado.fragmentos
            recuperaciones.append({"consulta_numero": numero,
                                   "fragmentos_recuperados": len(resultado.fragmentos)})
        print(json.dumps({"verificacion_correcta": correcto, "documentos": estado,
                          "recuperaciones": recuperaciones}, ensure_ascii=False, indent=2))
        return 0 if correcto else 1
    except SincronizacionEnCurso:
        print("Ya hay una sincronización en curso.", file=sys.stderr)
        return 2
    except Exception:
        print("No se pudo completar la operación. Revisá configuración y migración 0004.",
              file=sys.stderr)
        return 1
    finally:
        if gestor is not None:
            for proveedor in gestor.proveedores:
                cerrar = getattr(proveedor, "aclose", None)
                if cerrar is not None:
                    try:
                        await cerrar()
                    except Exception:
                        print("No se pudo cerrar un proveedor.", file=sys.stderr)


def main(argumentos=None):
    ruta_backend = str(Path(__file__).resolve().parents[1])
    if ruta_backend not in sys.path:
        sys.path.insert(0, ruta_backend)
    opciones = crear_analizador().parse_args(argumentos)
    return asyncio.run(_ejecutar(opciones))


if __name__ == "__main__":
    raise SystemExit(main())

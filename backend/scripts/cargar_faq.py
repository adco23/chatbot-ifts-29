"""Carga curada y regeneración FAQ explícitas. No aplica SQL ni sincroniza Drive."""

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path


def crear_analizador():
    parser = argparse.ArgumentParser(description=__doc__)
    comandos = parser.add_subparsers(dest="comando", required=True)
    cargar = comandos.add_parser("cargar", help="Generar y guardar preguntas curadas desde JSON.")
    cargar.add_argument("--archivo", type=Path, required=True)
    regenerar = comandos.add_parser("regenerar", help="Procesar FAQ pendientes sin sincronizar Drive.")
    regenerar.add_argument("--limite", type=int, default=10)
    regenerar.add_argument("--presupuesto-segundos", type=float, default=60)
    return parser


async def ejecutar(argumentos):
    from app.providers.dependencias import obtener_gestor_proveedores
    from app.providers.embeddings.dependencias import obtener_gestor_embeddings
    from app.services.curador_faq import EntradaFaq, cargar_pregunta, regenerar_pendientes
    from app.settings import obtener_configuracion

    embeddings = llm = None
    try:
        # Validar TODO el archivo antes de crear proveedores o efectuar llamadas externas.
        entradas = []
        if argumentos.comando == "cargar":
            datos = json.loads(argumentos.archivo.read_text(encoding="utf-8"))
            if not isinstance(datos, list) or not datos:
                raise ValueError("Se requiere una lista no vacía.")
            entradas = [EntradaFaq.model_validate(dato) for dato in datos]
        elif (argumentos.limite <= 0 or not math.isfinite(argumentos.presupuesto_segundos)
              or argumentos.presupuesto_segundos <= 0):
            raise ValueError("Los límites deben ser positivos.")
        configuracion = obtener_configuracion()
        embeddings = obtener_gestor_embeddings()
        llm = obtener_gestor_proveedores()
        if argumentos.comando == "regenerar":
            resumen = await regenerar_pendientes(
                configuracion=configuracion, gestor_embeddings=embeddings, gestor_llm=llm,
                limite=argumentos.limite, presupuesto_segundos=argumentos.presupuesto_segundos,
            )
            print(json.dumps(resumen))
            return 1 if resumen["fallidas"] or resumen["diferidas"] else 0
        errores = 0
        for numero, entrada in enumerate(entradas, start=1):
            try:
                resultado = await cargar_pregunta(
                    entrada, configuracion=configuracion,
                    gestor_embeddings=embeddings, gestor_llm=llm,
                )
                if resultado is None:
                    errores += 1
                    print(json.dumps({"entrada": numero, "estado": "sin_evidencia_o_contexto"}))
                else:
                    print(json.dumps({"entrada": numero,
                                      "estado": "creada" if resultado.creada else "actualizada",
                                      "faq_id": resultado.id}))
            except Exception as error:
                errores += 1
                print(json.dumps({"entrada": numero, "estado": "error",
                                  "tipo": type(error).__name__}))
        return 1 if errores else 0
    except Exception as error:
        print(f"No se pudo completar la operación FAQ: {type(error).__name__}.", file=sys.stderr)
        return 1
    finally:
        for gestor in (embeddings, llm):
            for proveedor in getattr(gestor, "proveedores", ()):
                cerrar = getattr(proveedor, "aclose", None)
                if cerrar is not None:
                    try:
                        await cerrar()
                    except Exception:
                        print("No se pudo cerrar un proveedor FAQ.", file=sys.stderr)


def main(argumentos=None):
    ruta = str(Path(__file__).resolve().parents[1])
    if ruta not in sys.path:
        sys.path.insert(0, ruta)
    return asyncio.run(ejecutar(crear_analizador().parse_args(argumentos)))


if __name__ == "__main__":
    raise SystemExit(main())

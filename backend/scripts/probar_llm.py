"""Verificación manual de los proveedores de modelos de lenguaje.

Solo para desarrollo: la aplicación no carga este archivo. Construye el gestor con la
configuración real (variables de entorno y .env), hace una generación e informa qué
proveedor respondió. Hace llamadas reales y consume cuota.

Uso, desde backend/ (el .env se busca en el directorio actual):

    python scripts/probar_llm.py --consulta "¿Qué es un IFTS?"
    python scripts/probar_llm.py --consulta "Hola" --simular-fallo gemini:cuota

Códigos de salida: 0 si hubo respuesta, 1 si la generación falló, 2 si no se pudo
intentar (sin claves, configuración inválida o argumentos incorrectos).
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

# Permite ejecutar el script directamente sin instalar el paquete `app`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError  # noqa: E402

from app.settings import PROVEEDORES_LLM_VALIDOS, Configuracion, obtener_configuracion  # noqa: E402
from app.providers import (  # noqa: E402
    ContenidoBloqueado,
    CuotaAgotada,
    ErrorNoRecuperable,
    ErrorProveedor,
    ErrorTemporal,
    GestorProveedores,
    ProveedorLLM,
    RespuestaLLM,
    SinProveedoresConfigurados,
    SolicitudLLM,
    TodosLosProveedoresFallaron,
)
from app.providers._http import ProveedorHTTP  # noqa: E402
from app.providers.dependencias import crear_proveedor  # noqa: E402
from app.providers.falso import ProveedorFalso  # noqa: E402

FALLOS_SIMULABLES: dict[str, type[ErrorProveedor]] = {
    "cuota": CuotaAgotada,
    "temporal": ErrorTemporal,
    "bloqueado": ContenidoBloqueado,
    "invalido": ErrorNoRecuperable,
}


def fallo_simulado(valor: str) -> tuple[str, str]:
    """Valida un argumento `NOMBRE:TIPO` de --simular-fallo."""
    nombre, separador, tipo = valor.partition(":")
    nombre, tipo = nombre.strip().lower(), tipo.strip().lower()
    if not separador or nombre not in PROVEEDORES_LLM_VALIDOS or tipo not in FALLOS_SIMULABLES:
        raise argparse.ArgumentTypeError(
            f"'{valor}' no es válido. Formato: NOMBRE:TIPO, con NOMBRE entre "
            f"{', '.join(PROVEEDORES_LLM_VALIDOS)} y TIPO entre {', '.join(FALLOS_SIMULABLES)}."
        )
    return nombre, tipo


def leer_argumentos() -> argparse.Namespace:
    analizador = argparse.ArgumentParser(
        description="Prueba la generación con los proveedores configurados en .env."
    )
    analizador.add_argument("--consulta", required=True, help="Texto de la consulta.")
    analizador.add_argument("--contexto", default="", help="Contexto que acompaña a la consulta.")
    analizador.add_argument("--instrucciones", default="", help="Instrucciones del sistema.")
    analizador.add_argument(
        "--simular-fallo",
        action="append",
        default=[],
        type=fallo_simulado,
        metavar="NOMBRE:TIPO",
        help=(
            "Reemplaza ese proveedor por uno falso que siempre falla con ese error. "
            f"TIPO: {', '.join(FALLOS_SIMULABLES)}. Se puede repetir."
        ),
    )
    return analizador.parse_args()


def construir_proveedores(config: Configuracion, fallos: dict[str, str]) -> list[ProveedorLLM]:
    """Arma la lista en el orden configurado, con los reemplazos simulados."""
    proveedores: list[ProveedorLLM] = []
    for nombre in config.llm_orden_proveedores:
        if nombre in fallos:
            error = FALLOS_SIMULABLES[fallos[nombre]](
                f"Falla simulada ({fallos[nombre]}).", proveedor=nombre
            )
            proveedores.append(ProveedorFalso([error], nombre=nombre))
        elif nombre in config.proveedores_disponibles:
            proveedores.append(crear_proveedor(nombre, config))
    return proveedores


def describir(proveedor: ProveedorLLM, fallos: dict[str, str]) -> str:
    if proveedor.nombre in fallos:
        return f"{proveedor.nombre} (simulado: {fallos[proveedor.nombre]})"
    return proveedor.nombre


def informar_error(error: Exception) -> None:
    print(f"Tipo de error: {type(error).__name__}")
    if isinstance(error, SinProveedoresConfigurados):
        print(f"Detalle: {error}")
    elif isinstance(error, TodosLosProveedoresFallaron):
        print("Todos los proveedores fallaron. Intentos, en orden:")
        for intento in error.errores:
            print(f"  - {intento.proveedor}: {type(intento).__name__}. {intento}")
        if not error.errores:
            print("  (ninguno: el presupuesto de tiempo se agotó antes del primer intento)")
    elif isinstance(error, ErrorProveedor):
        print(f"Proveedor: {error.proveedor}")
        print(f"Detalle: {error}")
        print("Es un error no recuperable: no se probó con los proveedores siguientes.")


async def ejecutar(
    gestor: GestorProveedores, solicitud: SolicitudLLM
) -> tuple[RespuestaLLM | None, Exception | None, float]:
    inicio = time.monotonic()
    try:
        respuesta = await gestor.generar_con_respaldo(solicitud)
        return respuesta, None, time.monotonic() - inicio
    except (ErrorProveedor, TodosLosProveedoresFallaron) as error:
        return None, error, time.monotonic() - inicio
    finally:
        for proveedor in gestor.proveedores:
            if isinstance(proveedor, ProveedorHTTP):
                await proveedor.aclose()


def main() -> int:
    # Evita que un carácter que la consola no sabe mostrar corte la salida.
    # line_buffering mantiene el orden entre la salida y los registros de stderr.
    for flujo in (sys.stdout, sys.stderr):
        flujo.reconfigure(errors="replace", line_buffering=True)
    argumentos = leer_argumentos()

    # Muestra por stderr los registros del gestor (proveedor, categoría y duración).
    logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(message)s")
    logging.getLogger("app.providers").setLevel(logging.INFO)

    try:
        config = obtener_configuracion()
    except ValidationError as error:
        # El texto de la validación no incluye los valores leídos (ver app/config.py).
        print(f"La configuración no es válida:\n{error}")
        return 2

    if not config.proveedores_disponibles:
        print(
            "No hay ninguna clave configurada. Cargá GEMINI_API_KEY o GROQ_API_KEY en "
            "backend/.env (ver .env.example) y ejecutá el script desde backend/."
        )
        return 2

    fallos = dict(argumentos.simular_fallo)
    proveedores = construir_proveedores(config, fallos)
    gestor = GestorProveedores(proveedores, presupuesto_segundos=config.llm_presupuesto_segundos)
    solicitud = SolicitudLLM(
        instrucciones_sistema=argumentos.instrucciones,
        contexto=argumentos.contexto,
        consulta=argumentos.consulta,
    )

    print(f"Proveedores, en orden: {', '.join(describir(p, fallos) for p in proveedores)}")
    print(f"Presupuesto total: {config.llm_presupuesto_segundos:g} s\n")

    respuesta, error, duracion = asyncio.run(ejecutar(gestor, solicitud))

    print()
    if respuesta is None:
        print("RESULTADO: la generación falló.")
        if error is not None:
            informar_error(error)
        print(f"Tiempo total: {duracion:.2f} s")
        return 1

    print("RESULTADO: respuesta obtenida.")
    print(f"Proveedor: {respuesta.proveedor}")
    print(f"Modelo: {respuesta.modelo}")
    print(f"Tiempo total: {duracion:.2f} s")
    print(f"Texto:\n{respuesta.texto}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

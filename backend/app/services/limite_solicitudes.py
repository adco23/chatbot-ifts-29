"""Límite de solicitudes por sesión (RNF11).

Tiene tres piezas:

- `ContadorSolicitudes`: interfaz del almacenamiento del contador.
- `ContadorEnMemoria`: primera implementación, dentro del proceso.
- `verificar_limite_solicitudes`: dependencia de FastAPI que identifica la sesión, cuenta
  la solicitud y responde 429 si se superó el límite.

Uso en una ruta:

    @router.post("/chat")
    async def chat(sesion_id: Annotated[str, Depends(verificar_limite_solicitudes)]): ...

IMPORTANTE, limitación de `ContadorEnMemoria`: en Vercel cada instancia serverless
tiene su propia memoria, que no se comparte con las demás y se pierde cuando la
instancia se recicla. Con varias instancias atendiendo, cada una cuenta por separado y
el límite real resulta mayor que el configurado. Esta implementación sirve para
desarrollo y pruebas. En producción debe reemplazarse por una que guarde el contador en
Supabase, cuando exista el esquema de base de datos. El cambio no toca la interfaz ni
las rutas: alcanza con que `obtener_contador_solicitudes` devuelva la nueva
implementación.

Privacidad (RNF01): la sesión se identifica con un valor aleatorio que genera el
widget, sin datos de la persona. No se usa la dirección IP ni se registra el
identificador en los logs.

Alcance: como el identificador lo elige el cliente, quien lo cambie en cada solicitud
esquiva el límite. Esta pieza frena el uso excesivo de una sesión normal, no un abuso
deliberado.
"""

import logging
import math
import re
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.settings import Configuracion, obtener_configuracion

logger = logging.getLogger(__name__)

# Encabezado en el que el widget envía el identificador de sesión.
ENCABEZADO_SESION = "X-Sesion-Id"

# Formato canónico de un UUID. Acotar el formato evita que lleguen datos identificables
# por error y que el contador guarde claves de tamaño arbitrario.
_FORMATO_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

MENSAJE_SESION_INVALIDA = (
    "Falta el identificador de sesión o no es válido. "
    f"Enviá un UUID en el encabezado {ENCABEZADO_SESION}."
)


@dataclass(frozen=True)
class EstadoVentana:
    """Estado de la ventana de una sesión después de contar una solicitud."""

    # Solicitudes contadas en la ventana actual, incluida la que se acaba de registrar.
    cantidad: int
    # Segundos que faltan para que la ventana se reinicie.
    segundos_para_reinicio: float


class ContadorSolicitudes(ABC):
    """Almacenamiento del contador de solicitudes por sesión.

    Usa ventanas fijas: la ventana de una sesión empieza con su primera solicitud y
    dura `ventana_segundos`. Al vencer, la siguiente solicitud abre una ventana nueva.

    El método es asíncrono para que una implementación que consulte la base de datos
    cumpla la misma interfaz. En esa implementación, contar y leer debe ser una sola
    operación atómica.
    """

    @abstractmethod
    async def registrar(self, sesion_id: str, ventana_segundos: float) -> EstadoVentana:
        """Cuenta una solicitud de la sesión y devuelve el estado de su ventana."""


class ContadorEnMemoria(ContadorSolicitudes):
    """Contador guardado en la memoria del proceso. Ver la limitación en el módulo.

    Para que la memoria no crezca sin tope, al llegar a `max_sesiones` descarta las
    ventanas vencidas y, si no alcanza, las más antiguas.
    """

    def __init__(
        self, reloj: Callable[[], float] = time.monotonic, max_sesiones: int = 10_000
    ) -> None:
        if max_sesiones <= 0:
            raise ValueError("La cantidad máxima de sesiones debe ser mayor que cero.")
        # Se inyecta en las pruebas para no depender del tiempo real.
        self._reloj = reloj
        self._max_sesiones = max_sesiones
        # Sesión → (momento en que vence la ventana, solicitudes contadas).
        self._ventanas: dict[str, tuple[float, int]] = {}
        self._candado = threading.Lock()

    async def registrar(self, sesion_id: str, ventana_segundos: float) -> EstadoVentana:
        with self._candado:
            ahora = self._reloj()
            vence, cantidad = self._ventanas.get(sesion_id, (0.0, 0))
            if cantidad == 0 or ahora >= vence:
                self._hacer_lugar(ahora, sesion_id)
                vence, cantidad = ahora + ventana_segundos, 0
            cantidad += 1
            self._ventanas[sesion_id] = (vence, cantidad)
            return EstadoVentana(cantidad=cantidad, segundos_para_reinicio=max(vence - ahora, 0.0))

    def _hacer_lugar(self, ahora: float, sesion_id: str) -> None:
        """Libera espacio antes de abrir la ventana de `sesion_id`."""
        self._ventanas.pop(sesion_id, None)
        if len(self._ventanas) < self._max_sesiones:
            return
        vencidas = [clave for clave, (vence, _) in self._ventanas.items() if ahora >= vence]
        for clave in vencidas:
            del self._ventanas[clave]
        while len(self._ventanas) >= self._max_sesiones:
            # Los diccionarios conservan el orden de inserción: la primera es la más antigua.
            del self._ventanas[next(iter(self._ventanas))]


@lru_cache
def obtener_contador_solicitudes() -> ContadorSolicitudes:
    """Devuelve el contador, creado una sola vez por proceso. Apta para `Depends`.

    Es el punto de reemplazo: cuando exista la implementación apoyada en Supabase, se
    devuelve desde acá. En las pruebas se cambia con `app.dependency_overrides`.
    """
    return ContadorEnMemoria()


async def verificar_limite_solicitudes(
    config: Annotated[Configuracion, Depends(obtener_configuracion)],
    contador: Annotated[ContadorSolicitudes, Depends(obtener_contador_solicitudes)],
    sesion_id: Annotated[str | None, Header(alias=ENCABEZADO_SESION)] = None,
) -> str:
    """Cuenta la solicitud de la sesión y la rechaza si superó el límite.

    Devuelve el identificador de sesión ya validado, en minúsculas.

    Lanza:
        HTTPException 400: falta el identificador o no tiene formato de UUID.
        HTTPException 429: la sesión superó `LIMITE_SOLICITUDES_MAXIMO` dentro de la
            ventana de `LIMITE_SOLICITUDES_VENTANA_SEGUNDOS`. Incluye `Retry-After`.
    """
    identificador = (sesion_id or "").strip().lower()
    if not _FORMATO_UUID.fullmatch(identificador):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=MENSAJE_SESION_INVALIDA)

    estado = await contador.registrar(identificador, config.limite_solicitudes_ventana_segundos)
    if estado.cantidad > config.limite_solicitudes_maximo:
        espera = max(math.ceil(estado.segundos_para_reinicio), 1)
        # Sin el identificador: no hace falta para diagnosticar y no debe quedar en logs.
        logger.info("Solicitud rechazada: una sesión superó el límite de solicitudes.")
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=mensaje_limite_superado(espera),
            headers={"Retry-After": str(espera)},
        )
    return identificador


def mensaje_limite_superado(espera_segundos: int) -> str:
    """Mensaje para quien consulta cuando su sesión superó el límite."""
    unidad = "segundo" if espera_segundos == 1 else "segundos"
    return (
        "Hiciste demasiadas consultas en poco tiempo. "
        f"Esperá {espera_segundos} {unidad} y volvé a intentar."
    )

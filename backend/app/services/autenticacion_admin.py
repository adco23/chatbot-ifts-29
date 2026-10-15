"""Autenticación de la API de administración (RNF11).

`verificar_token_admin` es una dependencia de FastAPI que exige un token compartido. El
token se lee de la variable de entorno `ADMIN_API_TOKEN` y quien llama lo envía en el
encabezado `Authorization: Bearer <token>`.

Uso recomendado, a nivel de router, para que toda ruta de administración quede
protegida aunque quien la agregue se olvide de pedirlo:

    router = APIRouter(dependencies=[Depends(verificar_token_admin)])

Reglas:

- La comparación se hace en tiempo constante con `hmac.compare_digest`.
- Si la variable no está configurada, o el token tiene menos de
  `LARGO_MINIMO_TOKEN` caracteres, se rechazan todas las solicitudes. Nunca se permite
  el acceso por omisión.
- La respuesta es la misma en todos los rechazos: no revela si faltó el encabezado, si
  el token no coincide o si el servidor no tiene token configurado.
- Los logs informan que hubo un rechazo, sin el token recibido ni el esperado.

Tarea programada de Vercel: al invocar una ruta, Vercel Cron envía
`Authorization: Bearer` con el valor de la variable `CRON_SECRET`. Para que la
sincronización programada pase esta verificación, `CRON_SECRET` y `ADMIN_API_TOKEN`
deben tener el mismo valor en el proyecto de Vercel.

REEMPLAZO PREVISTO: este mecanismo usa un único token para toda la administración. Si
más adelante se usan administradores individuales (tabla `administrador` del modelo de
datos), esta dependencia se reemplaza por una que consulte la base y devuelva al
administrador autenticado. Las rutas siguen declarando la dependencia igual.
"""

import hmac
import logging
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Configuracion, obtener_configuracion

logger = logging.getLogger(__name__)

# Un token más corto se considera no configurado. Es el mínimo que recomienda Vercel
# para el secreto de sus tareas programadas.
LARGO_MINIMO_TOKEN = 16

MENSAJE_NO_AUTORIZADO = "No autorizado."

# `auto_error=False`: los rechazos los arma esta dependencia, para que todos sean
# iguales. El esquema queda declarado en la documentación OpenAPI.
_esquema_bearer = HTTPBearer(
    auto_error=False,
    description="Token de la API de administración.",
)

# Valor contra el que se compara cuando no hay token configurado, para que ese caso
# tarde lo mismo que una comparación real. Es aleatorio y distinto en cada proceso.
_TOKEN_DE_RELLENO = secrets.token_urlsafe(32)


def _token_configurado(config: Configuracion) -> str | None:
    """Devuelve el token de administración, o None si falta o es demasiado corto."""
    if config.admin_api_token is None:
        return None
    token = config.admin_api_token.get_secret_value()
    return token if len(token) >= LARGO_MINIMO_TOKEN else None


async def verificar_token_admin(
    config: Annotated[Configuracion, Depends(obtener_configuracion)],
    credenciales: Annotated[HTTPAuthorizationCredentials | None, Depends(_esquema_bearer)] = None,
) -> None:
    """Exige el token de administración. Rechaza con 401 ante cualquier otra situación.

    Lanza:
        HTTPException 401: falta el encabezado, el esquema no es Bearer, el token no
            coincide o el servidor no tiene un token válido configurado.
    """
    esperado = _token_configurado(config)
    recibido = credenciales.credentials if credenciales is not None else ""

    # Se compara siempre, haya o no token configurado, y sobre bytes: `compare_digest`
    # no admite texto con caracteres fuera de ASCII.
    coincide = hmac.compare_digest(
        recibido.encode("utf-8"), (esperado or _TOKEN_DE_RELLENO).encode("utf-8")
    )

    if esperado is None:
        logger.error(
            "ADMIN_API_TOKEN no está configurado o tiene menos de %d caracteres: se "
            "rechazan todas las solicitudes de administración.",
            LARGO_MINIMO_TOKEN,
        )
    elif not coincide:
        logger.warning("Solicitud de administración rechazada: credencial ausente o incorrecta.")

    if esperado is None or not coincide:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=MENSAJE_NO_AUTORIZADO,
            headers={"WWW-Authenticate": "Bearer"},
        )

"""Gestor de embeddings: prueba los proveedores en orden y pasa al respaldo si uno falla.

Qué hace ante cada falla de un proveedor:

- `ErrorTemporal` (tiempo agotado, falla de conexión, 5xx): reintenta en el mismo
  proveedor con espera exponencial acotada y, si se agotan los reintentos, pasa al
  siguiente.
- `CuotaAgotada` (429): pasa al siguiente sin reintentar. Insistir contra un límite de
  uso gasta tiempo y cuota sin cambiar el resultado.
- `ErrorNoRecuperable` (otros 4xx, dimensión inesperada): lo registra como error de
  configuración y pasa al siguiente sin reintentar.

A diferencia del gestor de modelos de lenguaje, acá un error de configuración no corta
el recorrido: con el doble índice, el respaldo puede responder igual contra su propio
índice.

Todos los vectores de una llamada salen de un único proveedor. Si el principal falla a
mitad de un conjunto de documentos, lo que haya generado se descarta y el conjunto
completo se pide al siguiente.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence

from app.providers.base import ErrorProveedor, ErrorTemporal
from app.providers.embeddings.base import (
    EmbeddingsNoDisponibles,
    ProveedorEmbeddings,
    ResultadoEmbedding,
    ResultadoEmbeddings,
    SinProveedoresDeEmbeddings,
    validar_textos,
)

logger = logging.getLogger(__name__)


class GestorEmbeddings:
    """Genera embeddings con el primer proveedor que pueda darlos.

    No guarda estado entre llamadas: cada una recorre la lista desde el principio, sin
    recordar fallas anteriores.

    Presupuesto de tiempo: se aplica solo a `embed_query`, que forma parte de la
    respuesta del chat. Se reparte en partes iguales entre los proveedores que faltan
    probar, y lo que uno no usa pasa al siguiente. Así los reintentos del principal
    nunca dejan sin tiempo al respaldo. `embed_documents` no tiene presupuesto: se usa
    al indexar y queda acotado por el tiempo máximo y los reintentos de cada proveedor.

    Doble índice: al indexar hacen falta los vectores de todos los proveedores, uno por
    columna, y no el respaldo. Para eso está `proveedores`, que permite pedirle los
    vectores a cada uno por separado.
    """

    def __init__(
        self,
        proveedores: Sequence[ProveedorEmbeddings],
        *,
        reintentos: int,
        backoff_base_segundos: float,
        backoff_maximo_segundos: float,
        presupuesto_segundos: float,
        dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        if reintentos < 0:
            raise ValueError("La cantidad de reintentos no puede ser negativa.")
        if backoff_base_segundos <= 0 or backoff_maximo_segundos < backoff_base_segundos:
            raise ValueError(
                "La espera base debe ser mayor que cero y la espera máxima no puede ser menor."
            )
        if presupuesto_segundos <= 0:
            raise ValueError("El presupuesto de tiempo debe ser mayor que cero.")
        self._proveedores: tuple[ProveedorEmbeddings, ...] = tuple(proveedores)
        self._reintentos = reintentos
        self._backoff_base_segundos = backoff_base_segundos
        self._backoff_maximo_segundos = backoff_maximo_segundos
        self._presupuesto_segundos = presupuesto_segundos
        # Se inyectan en las pruebas para no depender del tiempo real.
        self._dormir = dormir
        self._reloj = reloj

    @property
    def proveedores(self) -> tuple[ProveedorEmbeddings, ...]:
        """Proveedores en el orden en que se intentan: primero el principal."""
        return self._proveedores

    @property
    def reintentos(self) -> int:
        """Reintentos por proveedor ante una falla temporal."""
        return self._reintentos

    @property
    def backoff_base_segundos(self) -> float:
        """Espera antes del primer reintento; se duplica en cada reintento siguiente."""
        return self._backoff_base_segundos

    @property
    def backoff_maximo_segundos(self) -> float:
        """Tope de la espera entre reintentos."""
        return self._backoff_maximo_segundos

    @property
    def presupuesto_segundos(self) -> float:
        """Tiempo total de `embed_query`, contando reintentos y respaldo."""
        return self._presupuesto_segundos

    async def embed_query(self, texto: str) -> ResultadoEmbedding:
        """Vectoriza una consulta e informa qué proveedor lo hizo.

        Quien llama debe buscar contra el índice del proveedor informado.

        Lanza:
            EmbeddingsNoDisponibles: ningún proveedor respondió dentro del presupuesto,
                o no hay proveedores configurados.
            ValueError: el texto está vacío.
        """
        (consulta,) = validar_textos([texto])
        vector, proveedor = await self._con_respaldo(
            "consulta", lambda actual: actual.embed_query(consulta), self._presupuesto_segundos
        )
        return ResultadoEmbedding(
            vector=vector,
            proveedor=proveedor.nombre,
            modelo=proveedor.modelo,
            dimension=proveedor.dimension,
        )

    async def embed_documents(self, textos: Sequence[str]) -> ResultadoEmbeddings:
        """Vectoriza documentos con un único proveedor e informa cuál fue.

        Lanza:
            EmbeddingsNoDisponibles: ningún proveedor respondió, o no hay proveedores
                configurados.
            ValueError: alguno de los textos está vacío.
        """
        lista = validar_textos(textos)
        if lista:
            vectores, proveedor = await self._con_respaldo(
                "documentos", lambda actual: actual.embed_documents(lista), None
            )
        elif self._proveedores:
            # Sin textos no hay nada que pedir: se evita la llamada.
            vectores, proveedor = [], self._proveedores[0]
        else:
            raise SinProveedoresDeEmbeddings()
        return ResultadoEmbeddings(
            vectores=vectores,
            proveedor=proveedor.nombre,
            modelo=proveedor.modelo,
            dimension=proveedor.dimension,
        )

    async def _con_respaldo[T](
        self,
        operacion: str,
        llamar: Callable[[ProveedorEmbeddings], Awaitable[T]],
        presupuesto: float | None,
    ) -> tuple[T, ProveedorEmbeddings]:
        """Devuelve el resultado del primer proveedor que responda, y ese proveedor."""
        if not self._proveedores:
            logger.warning("No hay proveedores de embeddings configurados.")
            raise SinProveedoresDeEmbeddings()

        vence = None if presupuesto is None else self._reloj() + presupuesto
        errores: list[ErrorProveedor] = []
        for indice, proveedor in enumerate(self._proveedores):
            limite: float | None = None
            if vence is not None:
                ahora = self._reloj()
                if vence - ahora <= 0:
                    logger.warning(
                        "Presupuesto de %.2f s agotado: no se intenta con el proveedor de "
                        "embeddings %s.",
                        presupuesto,
                        proveedor.nombre,
                    )
                    break
                # Parte de este proveedor: el tiempo restante dividido entre los que
                # faltan probar, contándolo a él.
                limite = ahora + (vence - ahora) / (len(self._proveedores) - indice)
            try:
                valor = await self._intentar_con_reintentos(proveedor, operacion, llamar, limite)
            except ErrorProveedor as error:
                errores.append(error)
                continue
            return valor, proveedor

        logger.error("Ningún proveedor de embeddings pudo vectorizar (%s).", operacion)
        raise EmbeddingsNoDisponibles(errores)

    async def _intentar_con_reintentos[T](
        self,
        proveedor: ProveedorEmbeddings,
        operacion: str,
        llamar: Callable[[ProveedorEmbeddings], Awaitable[T]],
        limite: float | None,
    ) -> T:
        """Llama al proveedor y reintenta las fallas temporales, hasta `limite`."""
        intento = 0
        while True:
            intento += 1
            inicio = self._reloj()
            try:
                valor = await self._intentar(proveedor, llamar, limite)
            except ErrorProveedor as error:
                espera = self._espera_para_reintentar(error, intento, limite)
                self._registrar_falla(
                    proveedor, operacion, error, self._reloj() - inicio, intento, espera
                )
                if espera is None:
                    raise
                await self._dormir(espera)
                continue
            logger.info(
                "El proveedor de embeddings %s respondió (%s) en %.2f s.",
                proveedor.nombre,
                operacion,
                self._reloj() - inicio,
            )
            return valor

    def _espera_para_reintentar(
        self, error: ErrorProveedor, intento: int, limite: float | None
    ) -> float | None:
        """Segundos a esperar antes de reintentar, o None si no corresponde reintentar."""
        if not isinstance(error, ErrorTemporal) or intento > self._reintentos:
            return None
        espera = min(
            self._backoff_base_segundos * 2 ** (intento - 1), self._backoff_maximo_segundos
        )
        # Si la espera consume lo que le queda al proveedor, conviene pasar al siguiente.
        if limite is not None and self._reloj() + espera >= limite:
            return None
        return espera

    def _registrar_falla(
        self,
        proveedor: ProveedorEmbeddings,
        operacion: str,
        error: ErrorProveedor,
        duracion: float,
        intento: int,
        espera: float | None,
    ) -> None:
        # Solo se registran el proveedor, la categoría y los tiempos: nunca el mensaje
        # del error ni el contenido de los textos.
        datos = (proveedor.nombre, operacion, type(error).__name__, duracion, intento)
        if espera is not None:
            logger.warning(
                "El proveedor de embeddings %s falló (%s): %s en %.2f s, intento %d. "
                "Se reintenta en %.2f s.",
                *datos,
                espera,
            )
        elif error.recuperable:
            logger.warning(
                "El proveedor de embeddings %s falló (%s): %s en %.2f s, intento %d. "
                "Se pasa al siguiente proveedor.",
                *datos,
            )
        else:
            logger.error(
                "Error de configuración del proveedor de embeddings %s (%s): %s en %.2f s, "
                "intento %d. Revisar credenciales, modelo y dimensión. Se pasa al siguiente "
                "proveedor.",
                *datos,
            )

    async def _intentar[T](
        self,
        proveedor: ProveedorEmbeddings,
        llamar: Callable[[ProveedorEmbeddings], Awaitable[T]],
        limite: float | None,
    ) -> T:
        """Hace una llamada al proveedor, con lo que queda hasta `limite` como tope."""
        restante = None if limite is None else max(limite - self._reloj(), 0.0)
        try:
            async with asyncio.timeout(restante):
                return await llamar(proveedor)
        except ErrorProveedor as error:
            if error.proveedor is None:
                error.proveedor = proveedor.nombre
            raise
        except TimeoutError:
            raise ErrorTemporal(
                f"El proveedor {proveedor.nombre} superó su parte del presupuesto de tiempo.",
                proveedor=proveedor.nombre,
            ) from None
        except Exception as error:
            # Un proveedor no debería lanzar otra cosa (ver el contrato en base.py). Si
            # pasa, se trata como falla temporal. No se encadena la excepción original:
            # podría incluir contenido.
            raise ErrorTemporal(
                f"El proveedor {proveedor.nombre} lanzó una excepción fuera del contrato "
                f"({type(error).__name__}).",
                proveedor=proveedor.nombre,
            ) from None

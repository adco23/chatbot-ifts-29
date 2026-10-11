"""Pruebas del contrato de proveedores de modelos de lenguaje."""

import asyncio
import dataclasses

import pytest

from app.providers import (
    ContenidoBloqueado,
    CuotaAgotada,
    ErrorNoRecuperable,
    ErrorProveedor,
    ErrorTemporal,
    ProveedorLLM,
    RespuestaLLM,
    SolicitudLLM,
    TodosLosProveedoresFallaron,
)

ERRORES_DE_PROVEEDOR = [CuotaAgotada, ErrorTemporal, ContenidoBloqueado, ErrorNoRecuperable]


class ProveedorDePrueba(ProveedorLLM):
    """Implementación mínima del contrato, sin red."""

    @property
    def nombre(self) -> str:
        return "prueba"

    async def generar(self, solicitud: SolicitudLLM) -> RespuestaLLM:
        return RespuestaLLM(texto="respuesta fija", proveedor=self.nombre, modelo="modelo-prueba")


def crear_solicitud() -> SolicitudLLM:
    return SolicitudLLM(
        instrucciones_sistema="Respondé solo con el contexto.",
        contexto="",
        consulta="¿Cuándo empiezan las clases?",
    )


def test_no_se_puede_instanciar_la_interfaz():
    with pytest.raises(TypeError):
        ProveedorLLM()


def test_subclase_completa_genera_una_respuesta():
    proveedor = ProveedorDePrueba()

    # asyncio.run evita depender de un plugin de pytest para pruebas asíncronas.
    respuesta = asyncio.run(proveedor.generar(crear_solicitud()))

    assert isinstance(respuesta, RespuestaLLM)
    assert respuesta.texto == "respuesta fija"
    assert respuesta.proveedor == "prueba"
    assert respuesta.modelo == "modelo-prueba"


def test_subclase_sin_generar_no_se_puede_instanciar():
    class SinGenerar(ProveedorLLM):
        @property
        def nombre(self) -> str:
            return "incompleto"

    with pytest.raises(TypeError):
        SinGenerar()


def test_subclase_sin_nombre_no_se_puede_instanciar():
    class SinNombre(ProveedorLLM):
        async def generar(self, solicitud: SolicitudLLM) -> RespuestaLLM:
            return RespuestaLLM(texto="", proveedor="", modelo="")

    with pytest.raises(TypeError):
        SinNombre()


@pytest.mark.parametrize(
    ("excepcion", "recuperable"),
    [
        (CuotaAgotada, True),
        (ErrorTemporal, True),
        (ContenidoBloqueado, False),
        (ErrorNoRecuperable, False),
    ],
)
def test_recuperable_segun_el_tipo_de_error(excepcion, recuperable):
    assert excepcion().recuperable is recuperable


@pytest.mark.parametrize("excepcion", ERRORES_DE_PROVEEDOR)
def test_errores_se_capturan_como_error_proveedor(excepcion):
    with pytest.raises(ErrorProveedor):
        raise excepcion(proveedor="prueba")


@pytest.mark.parametrize("excepcion", [ErrorProveedor, *ERRORES_DE_PROVEEDOR])
def test_error_guarda_el_proveedor_y_el_mensaje(excepcion):
    error = excepcion("Respuesta 429 del servicio.", proveedor="prueba")

    assert error.proveedor == "prueba"
    assert str(error) == "Respuesta 429 del servicio."


@pytest.mark.parametrize("excepcion", [ErrorProveedor, *ERRORES_DE_PROVEEDOR])
def test_error_sin_argumentos_tiene_mensaje_y_proveedor_desconocido(excepcion):
    error = excepcion()

    assert error.proveedor is None
    assert str(error)


def test_todos_los_proveedores_fallaron_conserva_los_errores():
    errores = [CuotaAgotada(proveedor="gemini"), ErrorTemporal(proveedor="groq")]

    error = TodosLosProveedoresFallaron(errores)

    assert error.errores == errores
    assert "gemini: CuotaAgotada" in str(error)
    assert "groq: ErrorTemporal" in str(error)


def test_todos_los_proveedores_fallaron_no_incluye_los_mensajes_individuales():
    error = TodosLosProveedoresFallaron([ErrorTemporal("detalle interno", proveedor="gemini")])

    assert "detalle interno" not in str(error)


def test_todos_los_proveedores_fallaron_no_es_error_proveedor():
    assert not issubclass(TodosLosProveedoresFallaron, ErrorProveedor)

    with pytest.raises(TodosLosProveedoresFallaron):
        try:
            raise TodosLosProveedoresFallaron([CuotaAgotada(proveedor="gemini")])
        except ErrorProveedor:
            pytest.fail("No debe capturarse como ErrorProveedor.")


def test_solicitud_tiene_opcionales_con_valor_por_defecto():
    solicitud = crear_solicitud()

    assert solicitud.instrucciones_sistema == "Respondé solo con el contexto."
    assert solicitud.contexto == ""
    assert solicitud.consulta == "¿Cuándo empiezan las clases?"
    assert solicitud.temperatura is None
    assert solicitud.max_tokens is None


def test_solicitud_acepta_los_opcionales():
    solicitud = SolicitudLLM(
        instrucciones_sistema="sistema",
        contexto="fragmento",
        consulta="consulta",
        temperatura=0.2,
        max_tokens=512,
    )

    assert solicitud.temperatura == 0.2
    assert solicitud.max_tokens == 512


def test_respuesta_se_construye_con_sus_campos():
    respuesta = RespuestaLLM(texto="hola", proveedor="gemini", modelo="modelo-x")

    assert (respuesta.texto, respuesta.proveedor, respuesta.modelo) == (
        "hola",
        "gemini",
        "modelo-x",
    )


def test_solicitud_y_respuesta_son_inmutables():
    solicitud = crear_solicitud()
    respuesta = RespuestaLLM(texto="hola", proveedor="gemini", modelo="modelo-x")

    with pytest.raises(dataclasses.FrozenInstanceError):
        solicitud.consulta = "otra"
    with pytest.raises(dataclasses.FrozenInstanceError):
        respuesta.texto = "otro"

"""Pruebas del contrato de los generadores de embeddings."""

import dataclasses
import math

import pytest

from app.providers import CuotaAgotada, ErrorTemporal
from app.providers.embeddings import (
    EmbeddingsNoDisponibles,
    ProveedorEmbeddings,
    ResultadoEmbedding,
    ResultadoEmbeddings,
    SinProveedoresDeEmbeddings,
    normalizar_l2,
)
from app.providers.embeddings.base import validar_textos
from app.providers.embeddings.falso import MODELO_FALSO, EmbeddingsFalso, vector_falso
from tests.unit.providers.embeddings.ayudas import (
    CONSULTA,
    DOCUMENTOS,
    afirmar_normalizados,
    ejecutar,
    norma,
)


def test_normalizar_l2_deja_norma_uno_y_conserva_la_direccion():
    assert normalizar_l2([3.0, 4.0]) == pytest.approx([0.6, 0.8])
    assert norma(normalizar_l2([2, -5, 0.5, 11])) == pytest.approx(1.0)


def test_normalizar_l2_no_cambia_un_vector_ya_normalizado():
    assert normalizar_l2([0.6, 0.8]) == pytest.approx([0.6, 0.8])


@pytest.mark.parametrize(
    "valores",
    [[], [0.0, 0.0, 0.0], [1.0, math.nan], [math.inf, 1.0]],
    ids=["vacio", "nulo", "nan", "infinito"],
)
def test_normalizar_l2_rechaza_vectores_que_no_se_pueden_normalizar(valores):
    with pytest.raises(ValueError):
        normalizar_l2(valores)


def test_validar_textos_devuelve_una_lista_con_los_mismos_textos():
    assert validar_textos(tuple(DOCUMENTOS)) == DOCUMENTOS
    assert validar_textos([]) == []


@pytest.mark.parametrize(
    "textos",
    [CONSULTA, [CONSULTA, ""], [CONSULTA, "   \n"], [CONSULTA, None], [42]],
    ids=["texto-suelto", "vacio", "en-blanco", "none", "numero"],
)
def test_validar_textos_rechaza_entradas_invalidas_sin_mostrar_el_contenido(textos):
    with pytest.raises(ValueError) as info:
        validar_textos(textos)

    assert CONSULTA not in str(info.value)


def test_no_se_puede_instanciar_la_interfaz():
    with pytest.raises(TypeError):
        ProveedorEmbeddings()


def test_subclase_incompleta_no_se_puede_instanciar():
    class SinConsulta(ProveedorEmbeddings):
        nombre = "incompleto"
        modelo = "m"
        dimension = 3

        async def embed_documents(self, textos):
            return []

    with pytest.raises(TypeError):
        SinConsulta()


@pytest.mark.parametrize("clase", [ResultadoEmbedding, ResultadoEmbeddings])
def test_los_resultados_son_inmutables(clase):
    campo = "vector" if clase is ResultadoEmbedding else "vectores"
    resultado = clase(**{campo: [], "proveedor": "gemini", "modelo": "m", "dimension": 3})

    with pytest.raises(dataclasses.FrozenInstanceError):
        resultado.proveedor = "cloudflare"


def test_embeddings_no_disponibles_resume_proveedor_y_tipo_sin_los_mensajes():
    cuota = CuotaAgotada(f"detalle con {CONSULTA}", proveedor="gemini")
    temporal = ErrorTemporal(proveedor="cloudflare")

    error = EmbeddingsNoDisponibles([cuota, temporal])

    assert error.errores == [cuota, temporal]
    assert "gemini: CuotaAgotada" in str(error)
    assert "cloudflare: ErrorTemporal" in str(error)
    assert CONSULTA not in str(error)


def test_sin_proveedores_es_un_caso_de_embeddings_no_disponibles():
    error = SinProveedoresDeEmbeddings()

    assert isinstance(error, EmbeddingsNoDisponibles)
    assert error.errores == []
    assert str(error) == "No hay proveedores de embeddings configurados."


def test_vector_falso_es_determinista_y_normalizado():
    vector = vector_falso(CONSULTA, 768)

    assert vector == vector_falso(CONSULTA, 768)
    assert vector != vector_falso(DOCUMENTOS[0], 768)
    assert vector != vector_falso(CONSULTA, 768, semilla="otro")
    afirmar_normalizados([vector], 768)


def test_proveedor_falso_cumple_el_contrato():
    proveedor = EmbeddingsFalso(nombre="prueba", dimension=16)

    vectores = ejecutar(proveedor.embed_documents(DOCUMENTOS))
    consulta = ejecutar(proveedor.embed_query(CONSULTA))

    assert (proveedor.nombre, proveedor.modelo, proveedor.dimension) == ("prueba", MODELO_FALSO, 16)
    assert len(vectores) == len(DOCUMENTOS)
    afirmar_normalizados([*vectores, consulta], 16)
    assert proveedor.llamadas == 2
    assert proveedor.ultimos_textos == [CONSULTA]


def test_proveedor_falso_consume_los_resultados_en_orden_y_repite_el_ultimo():
    cuota = CuotaAgotada()
    proveedor = EmbeddingsFalso([cuota, None], nombre="prueba")

    with pytest.raises(CuotaAgotada) as info:
        ejecutar(proveedor.embed_query(CONSULTA))
    assert info.value is cuota
    assert info.value.proveedor == "prueba"

    assert len(ejecutar(proveedor.embed_query(CONSULTA))) == 8
    assert len(ejecutar(proveedor.embed_query(CONSULTA))) == 8


@pytest.mark.parametrize("argumentos", [{"resultados": []}, {"dimension": 0}])
def test_proveedor_falso_rechaza_parametros_invalidos(argumentos):
    with pytest.raises(ValueError):
        EmbeddingsFalso(**argumentos)

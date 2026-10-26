"""Convención de nombres pura y determinista; no accede a servicios ni al LLM."""

import re
import unicodedata
from collections.abc import Sequence
from typing import Protocol

from app.models.rag import MetadataDocumental


class MateriaCatalogo(Protocol):
    id: int
    nombre: str


class ErrorMetadataDocumental(ValueError):
    def __init__(self, categoria: str):
        self.categoria = categoria
        super().__init__(categoria)


def normalizar_materia(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto.casefold())
    return " ".join("".join(
        c for c in descompuesto if not unicodedata.combining(c)
    ).replace("-", " ").split())


def resolver_metadata_documental(
    nombre: str,
    catalogo: Sequence[MateriaCatalogo],
    *,
    comision_max_longitud: int = 8,
    tipos_documentales: Sequence[str] = ("Programa", "Cronograma"),
) -> MetadataDocumental:
    """Resuelve sólo coincidencias exactas normalizadas; nunca degrada a general."""
    if not 1 <= comision_max_longitud <= 32:
        raise ValueError("Límite de comisión inválido.")
    base = re.sub(r"\.(pdf|docx)$", "", nombre, flags=re.IGNORECASE)
    partes = [p.strip() for p in base.split("__")]
    if any(not p or any(unicodedata.category(c).startswith("C") for c in p) for p in partes):
        raise ErrorMetadataDocumental("nombre_invalido")
    if len(partes) == 2 and partes[0].upper() == "GENERAL":
        return MetadataDocumental(tipo="General")
    if len(partes) not in (3, 4):
        raise ErrorMetadataDocumental("nombre_invalido")
    periodo = partes[0].upper()
    if not re.fullmatch(r"[0-9]{4}C[12]", periodo):
        raise ErrorMetadataDocumental("periodo_invalido")
    if len(partes) == 3:
        if partes[1].upper() != "GENERAL":
            raise ErrorMetadataDocumental("nombre_invalido")
        return MetadataDocumental(periodo=periodo, tipo="General")
    # GENERAL sólo es válido en las formas explícitas de dos o tres componentes.
    if partes[1].upper() == "GENERAL":
        raise ErrorMetadataDocumental("nombre_invalido")
    comision = partes[2].upper()
    if comision == "COM-GENERAL":
        comision = None
    elif re.fullmatch(rf"COM-[A-Z0-9]{{1,{comision_max_longitud}}}", comision):
        comision = comision[4:]
    else:
        raise ErrorMetadataDocumental("comision_invalida")
    tipos = {normalizar_materia(t): t for t in tipos_documentales}
    tipo = tipos.get(normalizar_materia(partes[3]))
    if tipo is None:
        raise ErrorMetadataDocumental("tipo_invalido")
    clave = normalizar_materia(partes[1])
    coincidencias = [m for m in catalogo if normalizar_materia(m.nombre) == clave]
    if not coincidencias:
        raise ErrorMetadataDocumental("materia_no_reconocida")
    if len(coincidencias) != 1:
        raise ErrorMetadataDocumental("materia_ambigua")
    materia = coincidencias[0]
    return MetadataDocumental(
        materia_id=materia.id, materia_normalizada=clave, materia_nombre=materia.nombre,
        comision=comision, periodo=periodo, tipo=tipo,
    )

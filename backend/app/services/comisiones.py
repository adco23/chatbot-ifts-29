"""Catálogo oficial local. Los documentos nunca crean períodos o comisiones."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

RUTA_CATALOGO = Path(__file__).resolve().parents[1] / "config" / "comisiones.json"


class ErrorCatalogoComisiones(RuntimeError):
    """Configuración inválida; el mensaje no contiene datos de entrada."""


def normalizar_periodo(valor: str) -> str:
    """Acepta el formato público YYYY-1C y el formato ya persistido YYYYC1."""
    if not isinstance(valor, str):
        raise ValueError("Período inválido.")
    match = re.fullmatch(r"([0-9]{4})(?:-([12])C|C([12]))", valor.strip().upper())
    if match is None:
        raise ValueError("Período inválido.")
    return f"{match[1]}C{match[2] or match[3]}"


def normalizar_comision(valor: str | None) -> str | None:
    return valor.strip().upper() if valor is not None else None


@dataclass(frozen=True)
class CatalogoComisiones:
    periodo_activo: str
    periodos: dict[str, tuple[str, ...]]

    def comisiones(self, periodo: str | None = None) -> tuple[str, ...]:
        return self.periodos.get(normalizar_periodo(periodo or self.periodo_activo), ())

    def admite(self, comision: str, periodo: str | None = None) -> bool:
        return normalizar_comision(comision) in self.comisiones(periodo)

    def documento_habilitado(self, documento, periodo: str | None = None) -> bool:
        """NULL/TODAS son alcance documental, nunca una comisión del estudiante."""
        elegido = normalizar_periodo(periodo or self.periodo_activo)
        if elegido not in self.periodos:
            return False
        propio = documento.periodo
        if propio is not None:
            try:
                if normalizar_periodo(propio) != elegido:
                    return False
            except ValueError:
                return False
        comision = normalizar_comision(documento.comision)
        return comision in (None, "TODAS") or self.admite(comision, elegido)


def cargar_catalogo(ruta: Path | None = None) -> CatalogoComisiones:
    """Lee una configuración pequeña por consulta; no requiere cambiar Python."""
    try:
        def objeto_unico(pares):
            resultado = {}
            for clave, valor in pares:
                if clave in resultado:
                    raise ValueError
                resultado[clave] = valor
            return resultado

        datos = json.loads((ruta or RUTA_CATALOGO).read_text(encoding="utf-8"),
                           object_pairs_hook=objeto_unico)
        if not isinstance(datos, dict) or set(datos) != {"periodo_activo", "periodos"}:
            raise ValueError
        activo = normalizar_periodo(datos["periodo_activo"])
        periodos = datos["periodos"]
        if not isinstance(periodos, dict) or not periodos:
            raise ValueError
        normalizados = {}
        activos = []
        for periodo, definicion in periodos.items():
            clave = normalizar_periodo(periodo)
            if clave in normalizados or not isinstance(definicion, dict):
                raise ValueError
            if set(definicion) != {"activo", "comisiones"} or type(definicion["activo"]) is not bool:
                raise ValueError
            valores = definicion["comisiones"]
            if not isinstance(valores, list) or not valores:
                raise ValueError
            if any(not isinstance(v, str) for v in valores):
                raise ValueError
            comisiones = tuple(v.strip().upper() for v in valores)
            if (len(set(comisiones)) != len(comisiones)
                    or any(not re.fullmatch(r"[A-Z0-9]{1,32}", v)
                           or v in {"TODAS", "GENERAL"} for v in comisiones)):
                raise ValueError
            normalizados[clave] = comisiones
            if definicion["activo"]:
                activos.append(clave)
        if activos != [activo] or activo not in normalizados:
            raise ValueError
        return CatalogoComisiones(activo, normalizados)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        raise ErrorCatalogoComisiones("El catálogo oficial de comisiones no está disponible.") from None

"""Detección temporal determinista y control conservador de fechas explícitas."""

import re
from datetime import date

from app.services.sanitizador import normalizar_comparacion

RECORDATORIO = (
    "Verificá la información vigente en el canal institucional oficial del IFTS 29."
)
_TEMAS = re.compile(
    r"\b(?:fecha\w*|inscrip\w*|examen\w*|examenes|cronograma\w*|horario\w*|"
    r"venc\w*|periodo\w*|cuatrimestre\w*|calendario\w*|cuando|ano|ciclo lectivo)\b"
)
_MESES = (
    "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|"
    "octubre|noviembre|diciembre"
)
_FECHAS = re.compile(
    rf"\b(?:\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}[/-]\d{{1,2}}"
    rf"(?:[/-]\d{{2,4}})?|\d{{1,2}} de (?:{_MESES})(?: de \d{{4}})?|"
    rf"(?:{_MESES}) de \d{{4}}|(?:19|20)\d{{2}}|hoy|manana|ayer)\b"
)


def es_temporal(texto: str) -> bool:
    normalizado = normalizar_comparacion(texto)
    return bool(_TEMAS.search(normalizado) or _FECHAS.search(normalizado))


def fechas_respaldadas(respuesta: str, contenidos: list[str]) -> bool:
    fechas = [_fecha_canonica(f) for f in _FECHAS.findall(normalizar_comparacion(respuesta))]
    evidencia = [_fecha_canonica(f) for texto in contenidos
                 for f in _FECHAS.findall(normalizar_comparacion(texto))]
    return all(any(_respalda_fecha(fecha, fuente) for fuente in evidencia) for fecha in fechas)


_NUMERO_MES = {nombre: indice for indice, nombre in enumerate((
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
), start=1)} | {"setiembre": 9}


def _fecha_canonica(texto):
    if texto in ("hoy", "manana", "ayer"):
        return ("relativa", texto, None, None)
    if re.fullmatch(r"(?:19|20)\d{2}", texto):
        return ("anio", None, None, texto)
    if m := re.fullmatch(rf"({_MESES}) de (\d{{4}})", texto):
        return ("mes", None, _NUMERO_MES[m[1]], m[2])
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", texto):
        anio, mes, dia = m[1], int(m[2]), int(m[3])
    elif m := re.fullmatch(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", texto):
        dia, mes, anio = int(m[1]), int(m[2]), m[3]
    elif m := re.fullmatch(rf"(\d{{1,2}}) de ({_MESES})(?: de (\d{{4}}))?", texto):
        dia, mes, anio = int(m[1]), _NUMERO_MES[m[2]], m[3]
    else:
        return None
    try:
        date(int(anio) if anio and len(anio) == 4 else 2000, mes, dia)
    except ValueError:
        return None
    # Un año de dos dígitos no se convierte en uno de cuatro: no inferir siglo.
    return ("dia", dia, mes, anio)


def _respalda_fecha(fecha, fuente):
    if fecha is None or fuente is None:
        return False
    if fecha == fuente:
        return True
    tipo, dia, mes, anio = fecha
    if tipo == "dia":
        return (fuente[0] == "dia" and (dia, mes) == fuente[1:3]
                and (anio is None or anio == fuente[3]))
    if tipo == "mes":
        return fuente[0] in ("dia", "mes") and mes == fuente[2] and anio == fuente[3]
    if tipo == "anio":
        return fuente[0] in ("dia", "mes", "anio") and anio == fuente[3]
    return False

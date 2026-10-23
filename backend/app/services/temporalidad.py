"""Detección temporal determinista y control conservador de fechas explícitas."""

import re

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
    fechas = _FECHAS.findall(normalizar_comparacion(respuesta))
    evidencia = [normalizar_comparacion(texto) for texto in contenidos]
    return all(any(fecha in texto for texto in evidencia) for fecha in fechas)

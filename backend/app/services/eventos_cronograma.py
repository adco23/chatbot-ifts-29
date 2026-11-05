"""Eventos académicos y sus bloques de evidencia; sin modelos externos ni IDs del corpus."""

import re
from dataclasses import dataclass
from typing import Literal

from app.services.sanitizador import normalizar_comparacion
from app.services.temporalidad import fechas_explicitas, fechas_respaldadas

_ORDINALES = {
    "primer": 1, "primero": 1, "primera": 1, "segundo": 2, "segunda": 2,
    "tercer": 3, "tercero": 3, "tercera": 3, "cuarto": 4, "cuarta": 4,
    "quinto": 5, "quinta": 5, "sexto": 6, "sexta": 6, "septimo": 7, "septima": 7,
    "octavo": 8, "octava": 8, "noveno": 9, "novena": 9, "decimo": 10, "decima": 10,
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
    "viii": 8, "ix": 9, "x": 10,
}
_ORD = ("(?:" + "|".join(sorted(_ORDINALES, key=len, reverse=True))
        + r"|[1-9][0-9]?(?:\.?\s*(?:er|ro|do|to|mo|vo|no|o|a|°))?)")
_PARCIAL = re.compile(
    rf"\b(?:(?P<antes>{_ORD})\s+(?:examen\s+)?parcial(?:es)?\b|"
    rf"parcial(?:es)?\b(?:\s+(?:n(?:ro|umero)?\.?\s*[°o]?\s*)?"
    rf"(?P<despues>{_ORD})(?![\w/]))?)"
)
_PRE = re.compile(r"\b(?:actividad(?:es)?\s+)?pre[\s-]*parcial(?:es)?\b")
_RECUP = re.compile(r"\brecuperatorios?\b")
_SEMANA = re.compile(r"\bsemana\s+(\d{1,2})\b", re.IGNORECASE)
_INICIO = re.compile(r"\b(?:inicia|inician|inicio|comienza|comienzan|comienzo|empieza|empiezan)\b")
_SEPARAR = re.compile(r"[\r\n]+|;\s*|(?<=[.!?])\s+")
_GUIONES = str.maketrans({c: "-" for c in "‐‑‒–—−"})


@dataclass(frozen=True)
class EventoCronograma:
    tipo: Literal["parcial", "preparcial", "recuperatorio"]
    numero: int | None = None

    @property
    def nombre(self) -> str:
        ordinal = {1: "primer", 2: "segundo", 3: "tercer"}.get(self.numero, f"{self.numero}°")
        parcial = f"{ordinal} parcial" if self.numero is not None else "parcial"
        if self.tipo == "parcial":
            return parcial
        nombre = "actividad pre parcial" if self.tipo == "preparcial" else "recuperatorios"
        return f"{nombre} del {parcial}" if self.numero is not None else nombre


@dataclass(frozen=True)
class BloqueEvento:
    evento: EventoCronograma
    texto: str
    semana: int | None
    inicio_semana: bool


class ErrorEvento(ValueError):
    """Su motivo es una etiqueta del servidor, nunca contenido de la consulta/fuente."""


def _numero(valor: str | None) -> int | None:
    if valor is None:
        return None
    if valor in _ORDINALES:
        return _ORDINALES[valor]
    numeros = re.match(r"[0-9]+", valor)
    return int(numeros[0]) if numeros else None


def _numero_parcial(match) -> int | None:
    return _numero(match["antes"] or match["despues"])


def eventos_en_texto(texto: str) -> tuple[EventoCronograma, ...]:
    """Las menciones anidadas pertenecen al pre-parcial/recuperatorio, no al examen ordinario."""
    normalizado = normalizar_comparacion(texto).translate(_GUIONES)
    menciones = []
    protegidos = []
    for patron, tipo in ((_PRE, "preparcial"), (_RECUP, "recuperatorio")):
        for match in patron.finditer(normalizado):
            fin = match.end()
            cola = normalizado[fin:]
            conector = re.match(
                r"^[\s:(-]*(?:(?:del?|al|para(?:\s+el)?|correspondientes?\s+al)\s+)?", cola,
            )
            asociado = _PARCIAL.match(cola, conector.end())
            numero = _numero_parcial(asociado) if asociado else None
            if asociado and numero is not None:
                fin += asociado.end()
            inicio = match.start()
            if tipo == "recuperatorio" and numero is None:
                anteriores = list(_PARCIAL.finditer(normalizado[:inicio]))
                if anteriores:
                    previo = anteriores[-1]
                    if re.fullmatch(r"\s*[-:(]\s*", normalizado[previo.end():inicio]):
                        numero = _numero_parcial(previo)
                        inicio = previo.start()
            protegidos.append((inicio, fin))
            menciones.append((inicio, EventoCronograma(tipo, numero)))
    for match in _PARCIAL.finditer(normalizado):
        if not any(inicio <= match.start() < fin for inicio, fin in protegidos):
            menciones.append((match.start(), EventoCronograma("parcial", _numero_parcial(match))))
    return tuple(dict.fromkeys(evento for _, evento in sorted(menciones, key=lambda m: m[0])))


def identificar_evento(texto: str) -> EventoCronograma | None:
    eventos = eventos_en_texto(texto)
    if len(eventos) != 1 or eventos[0] == EventoCronograma("parcial"):
        return None  # Una consulta general/listado no fuerza un único evento.
    return eventos[0]


def _coincide(pedido: EventoCronograma, fuente: EventoCronograma) -> bool:
    return pedido.tipo == fuente.tipo and (pedido.numero is None or pedido.numero == fuente.numero)


def consulta_dirigida(evento: EventoCronograma) -> str:
    return f"{evento.nombre.capitalize()}: fecha, semana e inicio."


def _inicio_de_semana(texto: str, semana: int | None) -> bool:
    if semana is None:
        return False
    normalizado = normalizar_comparacion(texto).translate(_GUIONES)
    inicio = _INICIO.search(normalizado)
    if not inicio:
        return False
    if re.search(r"\b(?:inicio|comienzo)\s+de\s+(?:la\s+)?semana\b|"
                 r"\bsemana\s+(?:que\s+)?(?:inicia|comienza|empieza)\b", normalizado):
        return True
    for fila in _SEPARAR.split(texto):
        normal = normalizar_comparacion(fila).translate(_GUIONES)
        marca = _INICIO.search(normal)
        if marca and any(m.start() < marca.start()
                         for p in (_PARCIAL, _PRE, _RECUP) for m in p.finditer(normal)):
            return False  # La fuente fecha explícitamente el inicio del evento, no de la semana.
    return True


def _crear_bloques(texto: str, semana: int | None, evento: EventoCronograma) -> list[BloqueEvento]:
    menciones = eventos_en_texto(texto)
    compatibles = [e for e in menciones if _coincide(evento, e)]
    if not compatibles:
        return []
    otros = [e for e in menciones if not _coincide(evento, e)]
    # Una unidad con varias fechas/eventos requiere una fila explícita; no usar bolsa de fechas.
    if otros or len(set(fechas_explicitas(texto))) > 1:
        filas = []
        for fila in _SEPARAR.split(texto):
            mencion = eventos_en_texto(fila)
            if mencion and all(_coincide(evento, e) for e in mencion) and fechas_explicitas(fila):
                filas.append(BloqueEvento(
                    mencion[0], (f"Semana {semana}\n" if semana is not None else "") + fila,
                    semana, _inicio_de_semana(fila, semana),
                ))
        return filas
    return [BloqueEvento(
        compatibles[0], texto.strip(), semana,
        _inicio_de_semana(texto, semana),
    )]


def bloques_evento(contenido: str, evento: EventoCronograma) -> tuple[BloqueEvento, ...]:
    """Vista transitoria: nunca sustituye el contenido persistido ni sus fragmentos."""
    encabezados = list(_SEMANA.finditer(contenido))
    bloques = []
    if encabezados:
        if encabezados[0].start():
            bloques.extend(_crear_bloques(contenido[:encabezados[0].start()], None, evento))
        for indice, encabezado in enumerate(encabezados):
            fin = encabezados[indice + 1].start() if indice + 1 < len(encabezados) else len(contenido)
            bloques.extend(_crear_bloques(contenido[encabezado.start():fin], int(encabezado[1]), evento))
    else:
        filas = [f.strip() for f in _SEPARAR.split(contenido) if f.strip()]
        for indice, fila in enumerate(filas):
            if not any(_coincide(evento, e) for e in eventos_en_texto(fila)):
                continue
            unidad = fila
            if not fechas_explicitas(fila):
                adyacentes = [filas[i] for i in (indice - 1, indice + 1) if 0 <= i < len(filas)
                              and not eventos_en_texto(filas[i]) and fechas_explicitas(filas[i])
                              and re.match(r"^(?:fecha|dia|inicia|inicio|[0-9])\b",
                                           normalizar_comparacion(filas[i]))]
                if len(adyacentes) == 1:
                    unidad += "\n" + adyacentes[0]
            bloques.extend(_crear_bloques(unidad, None, evento))
    return tuple(dict.fromkeys(bloques))


def respalda_evento(contenido: str, evento: EventoCronograma) -> bool:
    return bool(bloques_evento(contenido, evento))


def contexto_evento(contenido: str, evento: EventoCronograma) -> str:
    return "\n\n".join(b.texto for b in bloques_evento(contenido, evento))


def _semanas(texto: str) -> set[int]:
    normalizado = normalizar_comparacion(texto)
    semanas = {int(m[1]) for m in _SEMANA.finditer(normalizado)}
    for palabra, numero in _ORDINALES.items():
        if re.search(r"\b" + re.escape(palabra) + r"\s+semana\b", normalizado):
            semanas.add(numero)
    return semanas


def _precision_semanal(respuesta: str) -> bool:
    normalizado = normalizar_comparacion(respuesta)
    if re.search(r"\b(?:ese|mismo|dicho)\s+dia\b", normalizado):
        return False
    anterior = ""
    frases = [parte for frase in _SEPARAR.split(normalizado)
              for parte in re.split(r"\s+(?:y|pero)\s+|,\s*(?=el\s+(?:examen|parcial))", frase)]
    for frase in frases:
        # Un día concreto sólo puede presentarse como inicio de semana, no como día del examen.
        dias = [f for f in fechas_explicitas(frase) if re.search(r"\d[/-]|\d de ", f)]
        if dias and (not _INICIO.search(frase)
                     or not (re.search(r"\bsemana\b", frase)
                             or re.fullmatch(r"semana\s+\d+", anterior))):
            return False
        anterior = frase.strip()
    return True


def validar_respuesta_evento(respuesta: str, contenidos: list[str], evento: EventoCronograma) -> None:
    bloques = [b for texto in contenidos for b in bloques_evento(texto, evento)]
    if not bloques:
        raise ErrorEvento("evento_no_respaldado")
    mencionados = eventos_en_texto(respuesta)
    if any(not any(_coincide(e, b.evento) for b in bloques) for e in mencionados):
        raise ErrorEvento("evento_renombrado")
    semanas = _semanas(respuesta)
    for bloque in bloques:
        if (not fechas_respaldadas(respuesta, [bloque.texto])
                or semanas and (bloque.semana is None or semanas != {bloque.semana})):
            continue
        if bloque.inicio_semana and not _precision_semanal(respuesta):
            continue
        return
    raise ErrorEvento("fecha_semana_o_precision_del_evento_no_respaldada")


def validar_eventos_mencionados(respuesta: str, contenidos: list[str]) -> None:
    """Una pregunta general tampoco permite intercambiar fechas al enumerar eventos."""
    mencionados = eventos_en_texto(respuesta)
    if len(mencionados) == 1:
        if mencionados[0] != EventoCronograma("parcial"):
            validar_respuesta_evento(respuesta, contenidos, mencionados[0])
        return
    for evento in mencionados:
        if evento == EventoCronograma("parcial"):
            continue
        afirmaciones = [fila for fila in _SEPARAR.split(respuesta)
                        if eventos_en_texto(fila) == (evento,)
                        and (fechas_explicitas(fila) or _semanas(fila))]
        if not afirmaciones:
            afirmaciones = [b.texto for b in bloques_evento(respuesta, evento)]
        if not afirmaciones:
            raise ErrorEvento("asociacion_de_evento_ambigua")
        for afirmacion in afirmaciones:
            validar_respuesta_evento(afirmacion, contenidos, evento)

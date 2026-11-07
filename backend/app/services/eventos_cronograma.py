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
_PFO_NOMBRE = r"(?:pfos?|practicas? de formacion obligatorias?)"
_PFO_ID = rf"(?:{_ORD}|[0-9]+)"
_PFO = re.compile(
    rf"\b(?:(?P<antes>{_ORD})\s+{_PFO_NOMBRE}(?!\w)|"
    rf"pfos?(?P<compacto>[0-9]+)(?![\w/])|"
    rf"{_PFO_NOMBRE}\s+(?:n(?:ro|umero)?\.?\s*[°o]?\s*)?"
    rf"(?P<despues>{_PFO_ID})(?![\w/])|"
    rf"{_PFO_NOMBRE}(?!\w)(?!\s+(?:n(?:ro|umero)?\.?\s*[°o]?\s*)?[0-9]))"
)
_PFO_ETIQUETA = (
    rf"(?:{_ORD}\s+{_PFO_NOMBRE}|pfos?[0-9]+|"
    rf"{_PFO_NOMBRE}(?:\s+(?:n(?:ro|umero)?\.?\s*[°o]?\s*)?{_PFO_ID})?)"
)
_SEMANA = re.compile(r"\bsemana\s+(\d{1,2})\b", re.IGNORECASE)
_ENCABEZADO_SEMANA = re.compile(
    r"^[ \t]*(?:#{1,6}\s*|[-*•]\s*)?(?:\*\*)?semana\s+(\d{1,2})\b",
    re.IGNORECASE | re.MULTILINE,
)
_ENCABEZADO_CLASE = re.compile(
    r"^[ \t]*(?:#{1,6}\s*|[-*•]\s*)?(?:\*\*)?clase\s+(?:n[°o]?\s*)?[0-9]+\b",
    re.IGNORECASE | re.MULTILINE,
)
_FILA_TEMPORAL = re.compile(
    r"^(?:fechas?|dias?|inicia|inicio|comienza|empieza|finaliza|fin|desde|hasta|del?|entre|"
    r"rango|lunes|martes|miercoles|jueves|viernes|sabado|domingo|[0-9]{1,4})\b"
)
_DIA_SEMANA = r"(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo)"
_APERTURA_PFO = re.compile(
    rf"\b(?:el\s+)?periodo de apertura\s+(?:comienza|inicia|empieza)\s+el\s+(?:{_DIA_SEMANA}\s+)?"
)
_INICIO = re.compile(r"\b(?:inicia|inician|inicio|comienza|comienzan|comienzo|empieza|empiezan)\b")
_SEPARAR = re.compile(r"[\r\n]+|;\s*|(?<=[.!?])\s+")
_GUIONES = str.maketrans({c: "-" for c in "‐‑‒–—−"})
_INICIO_EVENTO = (
    rf"(?:(?:el|la|los|las)\s+)?(?:{_ORD}\s+(?:examen\s+)?parcial\b|"
    rf"parcial(?:es)?\b|(?:actividad\s+)?pre[\s-]*parcial\b|recuperatorios?\b|{_PFO_ETIQUETA})"
)
_CLAUSULAS = re.compile(
    rf"(?:\s+(?:y|mientras que)\s+|,\s*|\s+[-–—]\s+)(?={_INICIO_EVENTO})",
    re.IGNORECASE,
)
_ETIQUETA = re.compile(
    rf"\b(?:(?:el|la)\s+)?(?:recuperatorios?(?:\s+del\s+{_ORD}\s+parcial)?|"
    rf"{_ORD}\s+(?:examen\s+)?parcial|parcial(?:\s+{_ORD})?|{_PFO_ETIQUETA}|"
    r"(?:actividad\s+)?pre[\s-]*parcial)\s*[:→]", re.IGNORECASE,
)


@dataclass(frozen=True)
class EventoCronograma:
    tipo: Literal["parcial", "preparcial", "recuperatorio", "pfo"]
    numero: int | None = None

    @property
    def nombre(self) -> str:
        if self.tipo == "pfo":
            return f"PFO {self.numero}" if self.numero is not None else "PFO"
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

    def __init__(self, motivo: str, *, guarda: str | None = None):
        self.guarda = guarda
        super().__init__(motivo)


def _numero(valor: str | None) -> int | None:
    if valor is None:
        return None
    if valor in _ORDINALES:
        return _ORDINALES[valor]
    numeros = re.match(r"[0-9]+", valor)
    return int(numeros[0]) if numeros else None


def _numero_parcial(match) -> int | None:
    return _numero(match["antes"] or match["despues"])


def _numero_pfo(match) -> int | None:
    return _numero(match["antes"] or match["compacto"] or match["despues"])


def normalizar_referencias_pfo(texto: str) -> str:
    """Vista para clasificación; comparte identidad y límites con el validador."""
    normalizado = normalizar_comparacion(texto).translate(_GUIONES)
    return _PFO.sub(lambda m: f"pfo {_numero_pfo(m)}" if _numero_pfo(m) is not None else "pfo",
                    normalizado)


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
    for match in _PFO.finditer(normalizado):
        menciones.append((match.start(), EventoCronograma("pfo", _numero_pfo(match))))
    return tuple(dict.fromkeys(evento for _, evento in sorted(menciones, key=lambda m: m[0])))


def identificar_evento(texto: str) -> EventoCronograma | None:
    eventos = eventos_en_texto(texto)
    if len(eventos) != 1 or eventos[0] in (EventoCronograma("parcial"), EventoCronograma("pfo")):
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
                         for p in (_PARCIAL, _PRE, _RECUP, _PFO) for m in p.finditer(normal)):
            return False  # La fuente fecha explícitamente el inicio del evento, no de la semana.
    return True


def expandir_rangos_explicitos(texto: str) -> str:
    """Sólo repite mes/año ya explícitos: «7 al 13 de septiembre», sin inferencias."""
    def expandir(match):
        extremo = match[2]
        if not fechas_explicitas(extremo):
            return match[0]
        sufijo = extremo[extremo.index(" "):]
        return f"{match[1]}{sufijo} al {extremo}"
    return re.sub(r"\b([0-9]{1,2})\s*(?:al?|hasta|[-–—])\s*"
                  r"([0-9]{1,2} de [a-z]+(?: de [0-9]{4})?)\b", expandir, texto,
                  flags=re.IGNORECASE)


def _filas_evento(texto: str) -> list[str]:
    filas = []
    for fila in _SEPARAR.split(texto):
        unidades = []
        for parte in _CLAUSULAS.split(fila):
            # También admite listas ya normalizadas por caché, sólo con etiquetas
            # explícitas «evento: fecha»; no adivina correspondencias por posición.
            etiquetas = list(_ETIQUETA.finditer(parte))
            cortes = [0, *(m.start() for m in etiquetas[1:]), len(parte)]
            for a, b in zip(cortes, cortes[1:]):
                unidad = parte[a:b].strip()
                if not unidad:
                    continue
                if unidades and eventos_en_texto(unidades[-1]) and not (
                    fechas_explicitas(unidades[-1]) or _SEMANA.search(unidades[-1])
                ):
                    # «Primer parcial y segundo parcial: fecha1 y fecha2» no asigna
                    # fechas individuales: mantener la unidad ambigua para rechazarla.
                    unidades[-1] += " " + unidad
                else:
                    unidades.append(unidad)
        filas.extend(unidades)
    return filas


def _fila_temporal(fila: str) -> bool:
    normal = normalizar_comparacion(fila).replace("**", "").lstrip(" -*#•|\t")
    return bool(_FILA_TEMPORAL.search(normal) and fechas_explicitas(fila))


def _campo_contexto_pfo(fila: str) -> bool:
    """Sólo campos estructurados; nunca salta párrafos o encabezados libres."""
    normal = normalizar_comparacion(fila).strip(" .")
    return bool(re.fullmatch(
        r"(?:materia\s*:\s*[^:\n]{1,160}|comision\s*:\s*[a-z0-9]{1,32}|"
        r"periodo\s*:\s*[0-9]{4}(?:c[12]|-[12]c))", normal,
    ))


def _unidades_evento(texto: str) -> list[str]:
    """Une sólo campos temporales adyacentes, sin cruzar otro evento o semana."""
    filas = _filas_evento(texto)
    posteriores = {}
    for indice, fila in enumerate(filas):
        if not eventos_en_texto(fila) and not _ENCABEZADO_CLASE.search(fila):
            continue
        indices = []
        siguiente = indice + 1
        eventos = eventos_en_texto(fila)
        pfo = len(eventos) == 1 and eventos[0].tipo == "pfo"
        while siguiente < len(filas):
            candidata = filas[siguiente]
            if (eventos_en_texto(candidata) or _ENCABEZADO_CLASE.search(candidata)
                    or _ENCABEZADO_SEMANA.search(candidata)):
                break
            apertura = pfo and _APERTURA_PFO.match(normalizar_comparacion(candidata))
            if _fila_temporal(candidata) or apertura:
                indices.append(siguiente)
            elif not (pfo and not indices and _campo_contexto_pfo(candidata)):
                break
            siguiente += 1
        posteriores[indice] = indices
    ocupadas = {i for indices in posteriores.values() for i in indices}
    unidades = []
    for indice, indices in posteriores.items():
        if not eventos_en_texto(filas[indice]):
            continue  # Sus fechas están ocupadas, sin convertir una clase en PFO/parcial.
        anterior = indice - 1
        previa = (anterior >= 0 and anterior not in ocupadas
                  and not eventos_en_texto(filas[anterior]) and _fila_temporal(filas[anterior]))
        if previa and indices:
            unidades.append(filas[indice])  # Dos campos sin propietario inequívoco.
        elif indices:
            unidades.append("\n".join([filas[indice], *(filas[i] for i in indices)]))
        elif previa and not fechas_explicitas(filas[indice]):
            unidades.append(filas[indice] + "\n" + filas[anterior])
        else:
            unidades.append(filas[indice])
    return unidades


def _rango_inequivoco(texto: str, *, pfo: bool = False) -> bool:
    dias = _dias(texto)
    if len(dias) <= 1:
        return True
    if len(dias) != 2:
        return False
    normal = normalizar_comparacion(texto).translate(_GUIONES)
    primero = normal.find(dias[0])
    ultimo = normal.find(dias[1], primero + len(dias[0]))
    medio = normal[primero + len(dias[0]):ultimo]
    return bool(re.fullmatch(r"\s*(?:al?|hasta|-)\s*", medio)
                or re.search(r"\b(?:fecha de )?fin\s*:\s*$", medio)
                and re.search(r"\b(?:inicio|desde)\s*:\s*$", normal[:primero])
                or pfo and any(m.end() == primero for m in _APERTURA_PFO.finditer(normal))
                and re.fullmatch(rf"\s+y\s+(?:finaliza|termina|concluye)\s+el\s+(?:{_DIA_SEMANA}\s+)?", medio))


def _crear_bloques(texto: str, semana: int | None, evento: EventoCronograma) -> list[BloqueEvento]:
    menciones = eventos_en_texto(texto)
    if (len(menciones) != 1 or not _coincide(evento, menciones[0])
            or _ENCABEZADO_CLASE.search(texto)
            or not _rango_inequivoco(texto, pfo=evento.tipo == "pfo")):
        return []
    locales = _semanas(texto)
    if len(locales) > 1 or semana is not None and locales and locales != {semana}:
        return []
    elegida = next(iter(locales)) if locales else semana
    vista = (f"Semana {elegida}\n" if elegida is not None and not locales else "") + texto.strip()
    return [BloqueEvento(menciones[0], vista, elegida, _inicio_de_semana(vista, elegida))]


def bloques_evento(contenido: str, evento: EventoCronograma) -> tuple[BloqueEvento, ...]:
    """Vista transitoria: nunca sustituye el contenido persistido ni sus fragmentos."""
    contenido = expandir_rangos_explicitos(contenido)
    # «Primer Parcial (Semana 8)» declara una semana local; no inicia otro bloque.
    encabezados = list(_ENCABEZADO_SEMANA.finditer(contenido))
    bloques = []
    capitulos = []
    if not encabezados or encabezados[0].start():
        capitulos.append((contenido[:encabezados[0].start()] if encabezados else contenido, None))
    for indice, encabezado in enumerate(encabezados):
        fin = encabezados[indice + 1].start() if indice + 1 < len(encabezados) else len(contenido)
        capitulos.append((contenido[encabezado.start():fin], int(encabezado[1])))
    for texto, semana in capitulos:
        # Un rango narrativo de apertura debe pertenecer a los campos adyacentes
        # de su PFO, no a cualquier párrafo posterior del mismo capítulo.
        apertura_pfo = evento.tipo == "pfo" and _APERTURA_PFO.search(normalizar_comparacion(texto))
        completos = [] if apertura_pfo else _crear_bloques(texto, semana, evento)
        if completos:
            bloques.extend(completos)
        else:
            for unidad in _unidades_evento(texto):
                bloques.extend(_crear_bloques(unidad, semana, evento))
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


def _dias(texto: str) -> list[str]:
    dias = []
    for fecha in fechas_explicitas(texto):
        if re.search(r"\d[/-]|\d de ", fecha) and not any(
            fechas_respaldadas(fecha, [otra]) or fechas_respaldadas(otra, [fecha]) for otra in dias
        ):
            dias.append(fecha)
    return dias


def _evidencia_no_ambigua(bloques: list[BloqueEvento]) -> None:
    for indice, bloque in enumerate(bloques):
        for otro in bloques[indice + 1:]:
            if bloque.evento != otro.evento:
                continue
            dias, otros = _dias(bloque.texto), _dias(otro.texto)
            if (dias and otros and (len(dias) != len(otros) or any(
                not (fechas_respaldadas(a, [b]) or fechas_respaldadas(b, [a]))
                for a, b in zip(dias, otros)
            ))) or (bloque.semana is not None and otro.semana is not None
                    and bloque.semana != otro.semana):
                raise ErrorEvento("evidencia_de_evento_ambigua")


def validar_respuesta_evento(respuesta: str, contenidos: list[str], evento: EventoCronograma) -> None:
    respuesta = expandir_rangos_explicitos(respuesta)
    mencionados = eventos_en_texto(respuesta)
    if evento.numero is None and len(mencionados) > 1:
        if any(not _coincide(evento, e) for e in mencionados):
            raise ErrorEvento("evento_renombrado")
        validar_eventos_mencionados(respuesta, contenidos)
        return
    # La identidad explícita de una afirmación plural se valida individualmente.
    if evento.numero is None and len(mencionados) == 1 and _coincide(evento, mencionados[0]):
        evento = mencionados[0]
    bloques = [b for texto in contenidos for b in bloques_evento(texto, evento)]
    if not bloques:
        raise ErrorEvento("evento_no_respaldado")
    if evento.numero is None and len({b.evento for b in bloques}) > 1:
        raise ErrorEvento("asociacion_de_evento_ambigua", guarda="evento_generico_con_varias_identidades")
    _evidencia_no_ambigua(bloques)
    if any(not any(_coincide(e, b.evento) for b in bloques) for e in mencionados):
        raise ErrorEvento("evento_renombrado")
    semanas = _semanas(respuesta)
    for bloque in bloques:
        if (not fechas_respaldadas(respuesta, [bloque.texto])
                or semanas and (bloque.semana is None or semanas != {bloque.semana})):
            continue
        dias, originales = _dias(respuesta), _dias(bloque.texto)
        if (len(dias) > 1 or evento.tipo == "pfo" and len(originales) > 1) and (
            len(dias) != len(originales)
            or any(not fechas_respaldadas(a, [b]) for a, b in zip(dias, originales))
        ):
            continue
        if bloque.inicio_semana and not _precision_semanal(respuesta):
            continue
        return
    raise ErrorEvento("fecha_semana_o_precision_del_evento_no_respaldada")


def _afirmaciones_individuales(respuesta: str, unidades: list[str], evento: EventoCronograma) -> list[str]:
    bloques = [b for b in bloques_evento(respuesta, evento) if b.evento == evento]
    if any(b.semana is not None for b in bloques):
        # Un heading Semana N es contexto estructural de su bloque. Conservarlo
        # permite contrastar también la semana, sin tomarla de una fila cercana.
        return [b.texto for b in bloques]
    afirmaciones = [fila for fila in unidades if eventos_en_texto(fila) == (evento,)
                    and (fechas_explicitas(fila) or _semanas(fila))]
    return afirmaciones or [b.texto for b in bloques]


def validar_eventos_mencionados(respuesta: str, contenidos: list[str], *, consulta: str | None = None) -> None:
    """Valida identidades individuales antes de aceptar cualquier resumen plural."""
    mencionados = eventos_en_texto(respuesta)
    pedidos = eventos_en_texto(consulta) if consulta is not None else ()
    familias_pedidas = {p.tipo for p in pedidos}
    if pedidos and (not mencionados or not any(e.tipo in familias_pedidas for e in mencionados)):
        raise ErrorEvento("asociacion_de_evento_ambigua", guarda="familia_solicitada_ausente")
    if not mencionados:
        return  # Los cronogramas también contienen fechas sin uno de estos eventos.
    familias_plurales = {p.tipo for p in pedidos if p.numero is None}
    if len(mencionados) == 1 and not familias_plurales:
        validar_respuesta_evento(respuesta, contenidos, mencionados[0])
        return
    unidades = _unidades_evento(respuesta)
    afirmaciones_validadas = []
    soportes = {}
    # El orden es de validación, nunca una asociación por posición o proximidad.
    for evento in (e for e in mencionados if e.numero is not None):
        afirmaciones = _afirmaciones_individuales(respuesta, unidades, evento)
        if not afirmaciones or any(not (_dias(a) or _semanas(a)) for a in afirmaciones):
            raise ErrorEvento("asociacion_de_evento_ambigua", guarda="evento_sin_afirmacion_temporal")
        apoyos = []
        for afirmacion in afirmaciones:
            validar_respuesta_evento(afirmacion, contenidos, evento)
            afirmaciones_validadas.append(afirmacion)
            # Sólo pueden respaldar el agregado los bloques compatibles con la
            # afirmación individual que acaba de pasar las mismas guardas estrictas.
            for contenido in contenidos:
                for bloque in bloques_evento(contenido, evento):
                    try:
                        validar_respuesta_evento(afirmacion, [bloque.texto], evento)
                    except ErrorEvento:
                        continue
                    apoyos.append(bloque)
        soportes[evento] = apoyos

    esperados = {e for contenido in contenidos for e in eventos_en_texto(contenido)
                 if e.tipo in familias_plurales and e.numero is not None
                 and any(_dias(b.texto) or b.semana is not None for b in bloques_evento(contenido, e))}
    esperados.update(e for e in pedidos if e.numero is not None)
    if not esperados.issubset(soportes):
        raise ErrorEvento("asociacion_de_evento_ambigua", guarda="listado_individual_incompleto")

    for evento in (e for e in mencionados if e.numero is None):
        afirmaciones = _afirmaciones_individuales(respuesta, unidades, evento)
        individuales = [b for e, bloques in soportes.items() if e.tipo == evento.tipo for b in bloques]
        if individuales:
            for afirmacion in afirmaciones:
                if not (fechas_explicitas(afirmacion) or _semanas(afirmacion)):
                    continue  # Encabezado sin una afirmación temporal adicional.
                semanas = {b.semana for b in individuales if b.semana is not None}
                if (not fechas_respaldadas(expandir_rangos_explicitos(afirmacion), [b.texto for b in individuales])
                        or not _semanas(afirmacion).issubset(semanas)
                        or any(b.inicio_semana for b in individuales) and not _precision_semanal(afirmacion)):
                    raise ErrorEvento("asociacion_de_evento_ambigua", guarda="agregado_no_respaldado_por_individuales")
                afirmaciones_validadas.append(afirmacion)
        else:
            if not afirmaciones:
                raise ErrorEvento("asociacion_de_evento_ambigua", guarda="evento_sin_afirmacion_individual")
            # Un recuperatorio adicional no es un parcial: conserva su identidad
            # y debe tener respaldo propio; nunca presta fechas al agregado pedido.
            for afirmacion in afirmaciones:
                validar_respuesta_evento(afirmacion, contenidos, evento)
                afirmaciones_validadas.append(afirmacion)
    for fila in _filas_evento(respuesta):
        fechas = fechas_explicitas(fila)
        solo_ano = fechas and all(re.fullmatch(r"[0-9]{4}", fecha) for fecha in fechas)
        if (fechas and not solo_ano or _semanas(fila)) and not any(
            normalizar_comparacion(fila) in normalizar_comparacion(a) for a in afirmaciones_validadas
        ):
            raise ErrorEvento("asociacion_de_evento_ambigua", guarda="fecha_o_semana_sin_asociacion_individual")

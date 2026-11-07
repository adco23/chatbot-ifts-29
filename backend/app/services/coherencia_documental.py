"""Chequeo preventivo local de declaraciones académicas explícitas; sin inferencia."""

import logging
import re
from dataclasses import asdict, dataclass
from typing import Literal

from app.models.rag import DocumentoPersistido, MetadataDocumental
from app.services.sanitizador import normalizar_comparacion

logger = logging.getLogger(__name__)
_COMISION = r"[a-z]{1,3}[0-9]{0,3}"
_IGNORAR = re.compile(
    r"(?:bibliografia|referencias(?: bibliograficas)?|ejemplos?(?: historicos?)?|"
    r"copyright|resoluciones?)(?:\s*:.*)?"
)
_PERIODO = re.compile(
    r"(?:(?:periodo(?: academico)?|cuatrimestre)\s*:\s*)?"
    r"(?:(?P<ano>20[0-9]{2})\s*-?\s*(?:c(?P<c1>[12])|(?P<c2>[12])c)"
    r"|(?P<ordinal>primer|primero|segundo|1[°ºo]?|2[°ºo]?) cuatrimestre(?: de)? "
    r"(?P<ano_ordinal>20[0-9]{2}))"
)


@dataclass(frozen=True)
class ProblemaCoherencia:
    documento: str  # Drive ID, nunca el título ni párrafos del documento.
    categoria: str
    campo: Literal["periodo", "comision", "contenido"]
    metadata_esperada: str | None
    valor_contextual_detectado: tuple[str, ...]
    severidad: Literal["error", "aviso"]
    accion: Literal["rechazo_sin_cambios", "continuar_sin_inferir"]


@dataclass(frozen=True)
class ResultadoCoherencia:
    problemas: tuple[ProblemaCoherencia, ...] = ()

    @property
    def aceptado(self) -> bool:
        return not any(p.severidad == "error" for p in self.problemas)


class ErrorCoherenciaDocumental(ValueError):
    """Independiente del error de nombre: jamás provoca la baja de la versión previa."""

    categoria = "incoherencia_documental"

    def __init__(self, resultado: ResultadoCoherencia):
        super().__init__(self.categoria)
        self.resultado = resultado


def _declaraciones(texto: str) -> tuple[set[str], set[tuple[str, ...]]]:
    periodos: set[str] = set()
    comisiones: set[tuple[str, ...]] = set()
    ano = None
    cuatrimestre = None
    for original in texto.splitlines():
        linea = normalizar_comparacion(original).replace("**", "").strip(" #*_.")
        if _IGNORAR.fullmatch(linea):
            # Se omite únicamente esta línea; ningún encabezado oculta lo posterior.
            ano = cuatrimestre = None
            continue
        # Sólo campos completos o encabezados. Una frase incidental no coincide.
        for campo in re.split(r"\s*[/|;]\s*", linea):
            campo = campo.strip(" #*_.")
            periodo = _PERIODO.fullmatch(campo)
            if periodo:
                valores = periodo.groupdict()
                numero = valores["c1"] or valores["c2"]
                if numero is None:
                    numero = "2" if valores["ordinal"].startswith(("seg", "2")) else "1"
                periodos.add(f"{valores['ano'] or valores['ano_ordinal']}C{numero}")
            # Año y cuatrimestre sólo se combinan si son campos adyacentes completos.
            nuevo_ano = re.fullmatch(r"ano(?: academico| lectivo)?\s*:\s*(20[0-9]{2})", campo)
            nuevo_cuatri = re.fullmatch(r"cuatrimestre\s*:\s*(primer|primero|segundo|[12][°ºo]?)", campo)
            if nuevo_ano and cuatrimestre:
                periodos.add(f"{nuevo_ano[1]}C{cuatrimestre}")
            if nuevo_cuatri and ano:
                numero = "2" if nuevo_cuatri[1].startswith(("seg", "2")) else "1"
                periodos.add(f"{ano}C{numero}")
            ano = nuevo_ano[1] if nuevo_ano else None
            cuatrimestre = ("2" if nuevo_cuatri[1].startswith(("seg", "2")) else "1") if nuevo_cuatri else None
            especifica = re.fullmatch(
                rf"(?:comision(?:\s*:\s*|\s+)|(?:este (?:documento|programa|cronograma) "
                rf"(?:corresponde|aplica) exclusivamente a (?:la )?comision)\s+)({_COMISION})",
                campo,
            )
            if especifica:
                comisiones.add((especifica[1].upper(),))
            varias = re.fullmatch(
                rf"comisiones\s*:\s*({_COMISION}(?:\s*(?:,|\by\b)\s*{_COMISION})+)", campo,
            )
            if varias:
                comisiones.add(tuple(sorted(set(re.split(r"\s*(?:,|\by\b)\s*", varias[1])))))
            if re.fullmatch(
                r"comisiones?\s*:\s*todas(?: las comisiones)?|"
                r"(?:este (?:documento|programa|cronograma) )?aplica a todas las comisiones", campo,
            ):
                comisiones.add(("TODAS",))
    return periodos, {tuple(c.upper() for c in grupo) for grupo in comisiones}


def validar_coherencia_documental(
    texto: str, metadata: MetadataDocumental, *, documento: str,
    anterior: DocumentoPersistido | None = None,
    exigir_contexto_explicito: bool = False,
) -> ResultadoCoherencia:
    """Retorna valores acotados, nunca el texto. Ante ambigüedad sólo avisa.

    No busca años sueltos ni menciones a comisiones en párrafos. NULL/TODAS es
    alcance común: una enumeración o declaración común es válida; un encabezado
    específico único lo contradice. No convierte ningún alcance automáticamente.
    Al retirar contexto previo, una declaración específica no pasa a ser general.
    Para DOCX sin encabezados reconstruibles se puede exigir contexto verificable.
    """
    periodos, grupos = _declaraciones(texto)
    problemas = []

    def informar(campo, esperado, valores, *, ambiguo=False, categoria=None):
        problemas.append(ProblemaCoherencia(
            documento=documento, categoria=categoria or (
                "contexto_ambiguo" if ambiguo else "incoherencia_documental"),
            campo=campo, metadata_esperada=esperado,
            valor_contextual_detectado=tuple(sorted(valores)),
            severidad="aviso" if ambiguo else "error",
            accion="continuar_sin_inferir" if ambiguo else "rechazo_sin_cambios",
        ))

    retira_materia = anterior is not None and anterior.materia_id is not None and metadata.materia_id is None
    retira_periodo = anterior is not None and anterior.periodo is not None and metadata.periodo is None
    amplia_comision = (anterior is not None and anterior.comision not in (None, "TODAS")
                      and metadata.comision in (None, "TODAS"))
    if (metadata.periodo is not None or retira_periodo or retira_materia) and periodos:
        if len(periodos) > 1:
            informar("periodo", metadata.periodo, periodos, ambiguo=True)
        elif metadata.periodo not in periodos:
            informar("periodo", metadata.periodo, periodos)
    if (metadata.materia_id is not None or retira_materia or amplia_comision) and grupos:
        esperado = metadata.comision or "TODAS"
        if len(grupos) > 1:
            informar("comision", esperado, {", ".join(g) for g in grupos}, ambiguo=True)
        else:
            grupo = next(iter(grupos))
            if esperado == "TODAS":
                incompatible = len(grupo) == 1 and grupo != ("TODAS",)
            else:
                incompatible = "TODAS" not in grupo and esperado not in grupo
            if incompatible:
                informar("comision", esperado, grupo)
    if exigir_contexto_explicito and anterior is not None:
        # Los antiguos chunks DOCX no guardan section. La ausencia de una declaración
        # no prueba que no existiera en un encabezado descartado al fragmentar.
        necesarios = {
            "periodo": (anterior.periodo is not None or metadata.periodo is not None, periodos),
            "comision": (anterior.comision is not None or metadata.comision is not None, grupos),
        }
        for campo, (necesario, declaraciones) in necesarios.items():
            if necesario and not declaraciones:
                informar(campo, getattr(metadata, campo), (), categoria="contexto_persistido_no_verificable")
        if not any(necesario for necesario, _ in necesarios.values()) and not (periodos or grupos):
            informar("contenido", None, (), categoria="contexto_persistido_no_verificable")
    return ResultadoCoherencia(tuple(problemas))


def exigir_coherencia_documental(
    texto: str, metadata: MetadataDocumental, *, documento: str,
    anterior: DocumentoPersistido | None = None,
    exigir_contexto_explicito: bool = False,
) -> ResultadoCoherencia:
    resultado = validar_coherencia_documental(
        texto, metadata, documento=documento, anterior=anterior,
        exigir_contexto_explicito=exigir_contexto_explicito,
    )
    exigir_resultado_coherencia(resultado)
    return resultado


def exigir_resultado_coherencia(resultado: ResultadoCoherencia) -> None:
    """Un único reporte seguro para rechazo y avisos, también sin texto persistido."""
    for problema in resultado.problemas:
        logger.warning("Coherencia documental: documento=%s categoria=%s campo=%s "
                       "esperado=%s detectado=%s severidad=%s accion=%s.",
                       problema.documento, problema.categoria, problema.campo,
                       problema.metadata_esperada, problema.valor_contextual_detectado,
                       problema.severidad, problema.accion,
                       extra={"coherencia_documental": asdict(problema)})
    if not resultado.aceptado:
        raise ErrorCoherenciaDocumental(resultado)

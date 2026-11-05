"""Normalización compartida y ocultación local de datos de entrada, sin registro."""

import re
import unicodedata


_CORREO = re.compile(
    r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@"
    r"(?:[\w](?:[\w-]*[\w])?\.)+[a-zA-Z]{2,63}(?![\w-])"
)
_GUION = r"[-\u2010-\u2015]"
_CUIL = re.compile(
    r"(?<![\w.+\-\u2010-\u2015])(?:20|23|24|27|30|33|34)"
    rf"(?:\s*{_GUION}\s*[0-9]{{8}}\s*{_GUION}\s*[0-9]|[0-9]{{9}})"
    r"(?!\w|[.\-\u2010-\u2015]\s*[0-9])"
)
_SEPARADOR = r"[ .\-\u2010-\u2015]*"
_TELEFONO = re.compile(
    r"(?<![\w.+\-\u2010-\u2015])"
    rf"(?:\+?54{_SEPARADOR}(?:9{_SEPARADOR})?)?"
    rf"(?:\(?0?11\)?{_SEPARADOR}(?:15{_SEPARADOR})?[0-9]{{4}}"
    rf"{_SEPARADOR}[0-9]{{4}}"
    rf"|\(?0?[23][0-9]{{2}}\)?{_SEPARADOR}(?:15{_SEPARADOR})?[0-9]{{3}}"
    rf"{_SEPARADOR}[0-9]{{4}}"
    rf"|\(?0?[23][0-9]{{3}}\)?{_SEPARADOR}(?:15{_SEPARADOR})?[0-9]{{3}}"
    rf"{_SEPARADOR}[0-9]{{3}})"
    r"(?!\w|[.\-\u2010-\u2015]\s*[0-9])"
)
_DNI = re.compile(
    r"(?<![\w.+\-\u2010-\u2015])(?:[0-9]{1,2}\.[0-9]{3}\.[0-9]{3}|[0-9]{7,8})"
    r"(?!\w|[.\-\u2010-\u2015]\s*[0-9])"
)
_PARTE_NOMBRE = r"[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+(?:['’\-][A-ZÁÉÍÓÚÜÑ]?[a-záéíóúüñ]+)*"
_OTRA_PARTE_NOMBRE = rf"\s+(?:(?:de|del|la|las|los)\s+){{0,2}}{_PARTE_NOMBRE}"
_PRESENTACION_NOMBRE = re.compile(
    rf"(?<!\w)(?P<prefijo>(?i:me llamo|mi nombre es))\s+"
    rf"{_PARTE_NOMBRE}(?:{_OTRA_PARTE_NOMBRE}){{0,3}}(?![\w’'\-])"
)
_PRESENTACION_SOY = re.compile(
    rf"(?<!\w)(?P<prefijo>(?i:soy))\s+"
    rf"(?P<nombre>{_PARTE_NOMBRE}(?:{_OTRA_PARTE_NOMBRE}){{1,3}})(?![\w’'\-])"
)
# «Soy» también introduce roles y pertenencia; ante esas señales no ocultar.
_ROLES_O_CONTEXTO = frozenset({
    "academica", "academico", "alumna", "alumno", "alumnos", "bedelia", "comision",
    "aspirante", "auxiliar", "ayudante", "coordinador", "coordinadora", "docente",
    "egresada", "egresado", "estudiante",
    "ifts", "ingresante", "ingeniera", "ingeniero", "licenciada", "licenciado", "materia",
    "profesor", "profesora", "programacion", "recursante", "regular", "representante",
    "responsable", "secretaria", "superior", "tecnica", "tecnico", "tecnicas", "tutor",
    "tutora", "universitaria", "universitario",
})


def sanitizar_texto(texto: str) -> str:
    texto = unicodedata.normalize("NFKC", texto)
    texto = "".join(
        " " if caracter.isspace() else caracter
        for caracter in texto
        if caracter.isspace() or not unicodedata.category(caracter).startswith("C")
    )
    return re.sub(r"\s+", " ", texto).strip()


def _ocultar_nombre_soy(coincidencia: re.Match[str]) -> str:
    partes = normalizar_comparacion(coincidencia["nombre"]).split()
    if (partes[0] in {"de", "del", "el", "la", "los", "las", "un", "una", "al"}
            or set(partes) & _ROLES_O_CONTEXTO):
        return coincidencia[0]
    return f"{coincidencia['prefijo']} [NOMBRE_OCULTO]"


def sanitizar_datos_personales(texto: str) -> str:
    """Sólo para entrada del usuario; no conserva originales ni informa coincidencias.

    No valida identidades. Los números aislados pueden ser otros identificadores;
    los nombres ambiguos y teléfonos locales sin área quedan fuera de las reglas.
    """
    texto = sanitizar_texto(texto)
    for patron, marcador in (
        (_CORREO, "[CORREO_OCULTO]"),
        (_CUIL, "[CUIL_OCULTO]"),
        (_TELEFONO, "[TELEFONO_OCULTO]"),
        (_DNI, "[DNI_OCULTO]"),
    ):
        texto = patron.sub(marcador, texto)
    texto = _PRESENTACION_NOMBRE.sub(
        lambda coincidencia: f"{coincidencia['prefijo']} [NOMBRE_OCULTO]", texto,
    )
    return _PRESENTACION_SOY.sub(_ocultar_nombre_soy, texto)


def normalizar_comparacion(texto: str) -> str:
    return "".join(
        caracter for caracter in unicodedata.normalize("NFKD", sanitizar_texto(texto).casefold())
        if not unicodedata.combining(caracter)
    )

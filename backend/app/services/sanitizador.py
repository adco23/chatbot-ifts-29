"""Normalización mínima; no almacena ni registra la entrada."""

import re
import unicodedata


def sanitizar_texto(texto: str) -> str:
    texto = unicodedata.normalize("NFKC", texto)
    texto = "".join(
        " " if caracter.isspace() else caracter
        for caracter in texto
        if caracter.isspace() or not unicodedata.category(caracter).startswith("C")
    )
    return re.sub(r"\s+", " ", texto).strip()


def normalizar_comparacion(texto: str) -> str:
    return "".join(
        caracter for caracter in unicodedata.normalize("NFKD", sanitizar_texto(texto).casefold())
        if not unicodedata.combining(caracter)
    )

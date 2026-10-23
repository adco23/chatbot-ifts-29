"""Guardas mínimas; los filtros de los proveedores continúan activos."""

import re

from app.services.sanitizador import normalizar_comparacion


def entrada_bloqueada(texto: str) -> bool:
    normalizado = normalizar_comparacion(texto)
    return bool(re.search(
        r"\b(?:mostra\w*|revela\w*|imprimi\w*|dame)\b.{0,60}"
        r"\b(?:api[ _-]?keys?|credenciales|secretos|prompt (?:del )?sistema)\b"
        r"|\bignora\w* (?:todas |las |tus )*(?:instrucciones|reglas)\b",
        normalizado,
    ))

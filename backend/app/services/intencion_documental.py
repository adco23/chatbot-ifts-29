"""Intención documental determinista, sin proveedores ni llamadas externas."""

import re

from app.models.rag import TipoDocumental
from app.services.sanitizador import normalizar_comparacion
from app.settings import Configuracion


def clasificar_intencion_documental(texto: str, config: Configuracion) -> TipoDocumental | None:
    consulta = normalizar_comparacion(texto)
    for tipo in ("cronograma", "programa", "general"):
        for termino in getattr(config, f"rag_terminos_{tipo}"):
            patron = r"(?<!\w)" + re.escape(normalizar_comparacion(termino)) + r"(?!\w)"
            if re.search(patron, consulta):
                return tipo
    return None

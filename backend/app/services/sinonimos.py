import unicodedata

# Mapeo oficial de materias del IFTS 29 y sus posibles sinónimos o formas abreviadas
MAPEO_MATERIAS = {
    "Técnicas de Programación": [
        "tecnicas", "programacion", "tec prog", "tec. de prog", "programación 1", "tecnicas de programacion"
    ],
    "Administración de Base de Datos": [
        "base de datos", "bd", "datos", "admin de datos", "sql", "bases", "administracion de base de datos"
    ],
    "Elementos de Análisis Matemático": [
        "analisis", "matematica", "analisis matematico", "mate", "calculo", "elementos de analisis matematico"
    ],
    "Lógica Computacional": [
        "logica", "computacional", "logica comp", "logica computacional"
    ]
}

def limpiar_texto(texto: str) -> str:
    """
    Convierte el texto a minúsculas y remueve acentos/tildes para una comparación robusta.
    """
    if not texto:
        return ""
    texto_min = texto.lower().strip()
    # Remueve tildes y caracteres diacríticos
    nfkd_form = unicodedata.normalize('NFKD', texto_min)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])

def normalizar_y_buscar_materia(texto_usuario: str) -> str | None:
    """
    Recibe el mensaje del usuario, lo limpia y busca a qué materia oficial
    corresponde según el diccionario de sinónimos.
    """
    texto_limpio = limpiar_texto(texto_usuario)
    
    for materia_oficial, sinonimos in MAPEO_MATERIAS.items():
        # Verificamos si el texto coincide con el nombre oficial limpio o con alguno de sus sinónimos
        nombre_oficial_limpio = limpiar_texto(materia_oficial)
        if nombre_oficial_limpio in texto_limpio or any(limpiar_texto(sin) in texto_limpio for sin in sinonimos):
            return materia_oficial
            
    return None
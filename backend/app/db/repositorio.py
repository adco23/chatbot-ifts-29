from app.db.conexion import obtener_cliente_supabase


def obtener_materias_repo():
    """Recupera el listado oficial de materias desde Supabase (T10/T11)."""
    supabase = obtener_cliente_supabase()
    if not supabase:
        return []
    try:
        response = supabase.table("materia").select("*").execute()
        return response.data
    except Exception as e:
        print(f"Error al consultar materias en Supabase: {e}")
        return []

def registrar_consulta_anonima(texto_sanitizado: str, resultado: str, confianza: float = 0.0):
    """Registra de forma anónima las consultas procesadas para las métricas (RF09)."""
    supabase = obtener_cliente_supabase()
    if not supabase:
        return
    try:
        data = {
            "texto_sanitizado": texto_sanitizado,
            "resultado": resultado,
            "confianza": confianza
        }
        supabase.table("consulta").insert(data).execute()
    except Exception as e:
        print(f"Error al guardar métrica en Supabase: {e}")

def guardar_documento_institucional(titulo: str, drive_id: str, vigente: bool = True, estado: str = "activo"):
    """Inserta o registra un documento institucional sincronizado desde Drive."""
    supabase = obtener_cliente_supabase()
    if not supabase:
        return None
    try:
        data = {
            "titulo": titulo,
            "drive_id": drive_id,
            "vigente": vigente,
            "estado": estado
        }
        # Forzamos el esquema public explícitamente por si PostgREST no lo resuelve por defecto
        response = supabase.schema("public").table("documento").insert(data).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        print(f"Error al guardar documento en Supabase: {e}")
        return None

def guardar_fragmento_documento(
    documento_id: int,
    contenido: str,
    vector: list[float] | None = None,
    orden: int = 1,
):
    """Inserta un fragmento de texto asociado a un documento, con su embedding."""
    supabase = obtener_cliente_supabase()
    if not supabase:
        return None

    try:
        data = {
            "documento_id": documento_id,
            "contenido": contenido,
            "orden": orden,
            "vector": vector,
        }

        response = supabase.table("fragmento").insert(data).execute()
        return response.data[0] if response.data else None

    except Exception as e:
        print(f"Error al guardar fragmento en Supabase: {e}")
        return None
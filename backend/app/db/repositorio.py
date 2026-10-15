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
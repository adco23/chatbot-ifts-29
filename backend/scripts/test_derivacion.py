import sys
from pathlib import Path

# Agregamos la carpeta backend al path para que Python reconozca el paquete 'app'
current_dir = Path(__file__).resolve().parent
backend_dir = current_dir.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.services.derivacion import load_referrals, find_referral_department


def run_tests():
    print("=" * 60)
    print("🧪 INICIANDO PRUEBAS DEL MÓDULO DE DERIVACIÓN (T26)")
    print("=" * 60)

    # 1. Probar la carga general del JSON simulado
    print("\n[Test 1] Cargando archivo de derivaciones...")
    referrals = load_referrals()
    if referrals:
        print(f"✅ Éxito: Se cargaron {len(referrals)} sectores de derivación.")
        for r in referrals:
            print(f"   - Sector: {r.get('department')} | Email: {r.get('email')}")
    else:
        print("❌ Fallo: No se pudieron cargar los datos de derivación.")
        return

    # 2. Probar consultas simuladas con diferentes palabras clave
    queries_to_test = [
        "Hola, ¿dónde puedo pedir mi certificado de alumno regular?",
        "Necesito averiguar sobre las correlativas para inscribirme a materias",
        "Quiero presentar los papeles para equivalencias de otra carrera",
        "¿Cuál es el horario de atención de secretaría académica?",
        "Consulta general sobre el sistema de colectivos",  # Debería no matchear nada
    ]

    print("\n[Test 2] Probando búsqueda de departamentos por consulta:")
    for query in queries_to_test:
        print(f'\n   Consulta: "{query}"')
        result = find_referral_department(query)
        if result:
            print(f"   -> 🎯 Derivar a: {result.get('department')}")
            print(f"      📧 Email: {result.get('email')}")
            print(f"      🕒 Horario: {result.get('schedule')}")
            print(f"      👤 Responsable: {result.get('contact_person')}")
        else:
            print(
                "   -> 📭 Sin derivación específica (la consulta puede ser respondida por el bot o requiere operador general)."
            )

    print("\n" + "=" * 60)
    print("🏁 PRUEBAS FINALIZADAS")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()

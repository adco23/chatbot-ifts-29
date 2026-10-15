import sys
from pathlib import Path
import asyncio

# Configurar rutas
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

from app.services.sincronizador import run_full_sync


async def main():
    print("🚀 INICIANDO SIMULACIÓN DEL CRON DE VERCEL (T27/T28/T29)")
    print("-" * 50)

    # 1. Armamos un diccionario simulando lo que leería de Drive
    # Usamos un archivo inventado para no depender del docx real en este test rápido
    mock_db_drive = {"/drive/docs/reglamento_2026.pdf": "hash_nuevo_inventado_789"}

    # 2. Corremos la sincronización con el formato asíncrono
    try:
        resultado = await run_full_sync(mock_db_drive)

        print("\n" + "-" * 50)
        print("📦 RESPUESTA QUE RECIBIRÍA VERCEL (JSON):\n")
        print(resultado)

    except Exception as e:
        print(f"\n❌ El orquestador explotó feo: {e}")


if __name__ == "__main__":
    # Así se arranca una función asíncrona desde un archivo suelto
    asyncio.run(main())

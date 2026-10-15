import os
import json
from googleapiclient.discovery import build
from google.oauth2 import service_account
from io import BytesIO
from googleapiclient.http import MediaIoBaseDownload

# Scopes estrictamente en modo solo lectura
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
CREDENTIALS_FILE = 'credentials.json'

def conectar_drive():
    """Conecta con la API de Google Drive usando la cuenta de servicio."""
    try:
        if os.path.exists(CREDENTIALS_FILE):
            creds = service_account.Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
            return build('drive', 'v3', credentials=creds)
        return None
    except Exception as e:
        print(f"Error al conectar con Google Drive: {e}")
        return None

def listar_archivos_institucionales():
    """Lista los archivos de la carpeta institucional configurada."""
    service = conectar_drive()
    if not service:
        return []
    
    try:
        # ID de la carpeta institucional compartida
        folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "11kZr6lwqHUzofr37mTd-noucxwunmIr8")
        query = f"'{folder_id}' in parents and trashed=false"
        results = service.files().list(
            q=query, 
            pageSize=50, 
            fields="files(id, name, mimeType, webViewLink)"
        ).execute()
        
        return results.get('files', [])
    except Exception as e:
        print(f"Error al listar archivos: {e}")
        return []


def descargar_contenido_archivo(file_id):
    """Descarga el contenido binario de un archivo de Drive dado su ID."""
    service = conectar_drive()
    if not service:
        return None
    
    try:
        request = service.files().get_media(fileId=file_id)
        fh = BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        fh.seek(0)
        return fh.read()
    except Exception as e:
        print(f"Error al descargar el archivo con ID {file_id}: {e}")
        return None
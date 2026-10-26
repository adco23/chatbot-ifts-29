"""Lectura de documentos de Drive; no contiene operaciones de escritura remota."""

import hashlib
import json
import os
from contextlib import contextmanager
from io import BytesIO

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from app.models.rag import DocumentoDrive
from app.settings import obtener_configuracion

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
CREDENTIALS_FILE = "credentials.json"
TIPO_GOOGLE_DOCS = "application/vnd.google-apps.document"
TIPO_PDF = "application/pdf"
_CAMPOS_ARCHIVO = "id,name,mimeType,webViewLink,modifiedTime,md5Checksum"
TIPOS_ADMITIDOS = frozenset({
    TIPO_PDF, TIPO_GOOGLE_DOCS,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
})


class ErrorDrive(RuntimeError):
    """Falla de lectura cuyo mensaje no expone credenciales ni contenido remoto."""


def conectar_drive():
    """Conecta con Drive mediante la cuenta de servicio y permisos de solo lectura."""
    try:
        secreto = obtener_configuracion().google_service_account_json
        if secreto is not None:
            credenciales = service_account.Credentials.from_service_account_info(
                json.loads(secreto.get_secret_value()), scopes=SCOPES,
            )
            return build("drive", "v3", credentials=credenciales)
        if os.path.exists(CREDENTIALS_FILE):
            credenciales = service_account.Credentials.from_service_account_file(
                CREDENTIALS_FILE, scopes=SCOPES
            )
            return build("drive", "v3", credentials=credenciales)
        return None
    except Exception:
        print("No se pudo conectar con Google Drive.")
        return None


@contextmanager
def _usar_servicio(servicio=None):
    propio = servicio is None
    cliente = conectar_drive() if propio else servicio
    if cliente is None:
        raise ErrorDrive("No se pudo conectar con Google Drive.")
    try:
        yield cliente
    finally:
        cerrar = getattr(cliente, "close", None)
        if propio and callable(cerrar):
            try:
                cerrar()
            except Exception:
                # Una falla al cerrar no debe ocultar el resultado de la lectura.
                print("No se pudo cerrar el cliente de Google Drive.")


def listar_archivos_institucionales(
    *, servicio=None, estricto: bool = False, validar_carpeta: bool = False,
) -> list[dict]:
    """Lista todas las páginas de hijos directos de la carpeta configurada.

    El modo legado devuelve ``[]`` ante una falla. El flujo de indexación usa
    ``estricto=True`` para distinguir una carpeta vacía de un error de lectura.
    """
    try:
        with _usar_servicio(servicio) as cliente:
            carpeta = os.environ.get(
                "GOOGLE_DRIVE_FOLDER_ID", "11kZr6lwqHUzofr37mTd-noucxwunmIr8"
            )
            if validar_carpeta:
                datos = cliente.files().get(
                    fileId=carpeta, fields="id,mimeType,trashed",
                ).execute()
                if (not isinstance(datos, dict) or datos.get("id") != carpeta
                        or datos.get("mimeType") != "application/vnd.google-apps.folder"
                        or datos.get("trashed") is not False):
                    raise ErrorDrive("La carpeta institucional no está disponible.")
            carpeta = carpeta.replace("\\", "\\\\").replace("'", "\\'")
            consulta = f"'{carpeta}' in parents and trashed=false"
            archivos = []
            pagina = None
            paginas_vistas = set()
            while True:
                parametros = {
                    "q": consulta,
                    "pageSize": 50,
                    "fields": f"nextPageToken,incompleteSearch,files({_CAMPOS_ARCHIVO})",
                }
                if pagina is not None:
                    parametros["pageToken"] = pagina
                resultado = cliente.files().list(**parametros).execute()
                if not isinstance(resultado, dict) or resultado.get("incompleteSearch", False):
                    raise ErrorDrive("El listado de Drive está incompleto.")
                elementos = resultado.get("files", [])
                if not isinstance(elementos, list) or not all(
                    isinstance(elemento, dict) for elemento in elementos
                ):
                    raise ErrorDrive("La respuesta de Google Drive no es válida.")
                archivos.extend(elementos)
                pagina = resultado.get("nextPageToken")
                if not pagina:
                    return archivos
                if not isinstance(pagina, str) or pagina in paginas_vistas:
                    raise ErrorDrive("La paginación de Google Drive no es válida.")
                paginas_vistas.add(pagina)
    except Exception:
        mensaje = "No se pudieron listar los archivos de Google Drive."
        if estricto:
            raise ErrorDrive(mensaje) from None
        print(mensaje)
        return []


def listar_documentos_sincronizables() -> list[DocumentoDrive]:
    """Snapshot completo validado antes de permitir cualquier baja."""
    archivos = listar_archivos_institucionales(estricto=True, validar_carpeta=True)
    documentos = []
    vistos = set()
    try:
        for archivo in archivos:
            if not isinstance(archivo.get("id"), str) or not archivo["id"].strip():
                raise ValueError("ID inválido.")
            if archivo["id"] in vistos:
                raise ValueError("ID repetido.")
            vistos.add(archivo["id"])
            if not isinstance(archivo.get("mimeType"), str):
                raise ValueError("MIME ausente.")
            if archivo["mimeType"] not in TIPOS_ADMITIDOS:
                continue
            if not archivo.get("modifiedTime"):
                raise ValueError("Versión ausente.")
            documentos.append(DocumentoDrive(
                drive_id=archivo["id"], titulo=archivo["name"], tipo_mime=archivo["mimeType"],
                enlace=archivo.get("webViewLink"), drive_modified_time=archivo["modifiedTime"],
                drive_md5_checksum=archivo.get("md5Checksum"),
            ))
        return documentos
    except Exception:
        raise ErrorDrive("El snapshot de Drive no es válido para sincronizar.") from None


def validar_version_descargada(documento: DocumentoDrive, contenido: bytes) -> None:
    """No etiquetar una descarga con una versión que cambió mientras se descargaba."""
    datos = obtener_metadatos_archivo(documento.drive_id)
    actual = DocumentoDrive(
        drive_id=datos["id"], titulo=datos["name"], tipo_mime=datos["mimeType"],
        drive_modified_time=datos.get("modifiedTime"),
        drive_md5_checksum=datos.get("md5Checksum"),
    )
    if (actual.drive_id != documento.drive_id
            or actual.tipo_mime != documento.tipo_mime
            or actual.drive_modified_time != documento.drive_modified_time
            or actual.drive_md5_checksum != documento.drive_md5_checksum):
        raise ErrorDrive("La versión del documento cambió durante la descarga.")
    if documento.drive_md5_checksum and documento.tipo_mime != TIPO_GOOGLE_DOCS:
        checksum = hashlib.md5(contenido, usedforsecurity=False).hexdigest()
        if checksum != documento.drive_md5_checksum.lower():
            raise ErrorDrive("La descarga no corresponde al checksum de Drive.")


def obtener_metadatos_archivo(drive_id: str, *, servicio=None) -> dict:
    """Obtiene un archivo por ID; siempre informa las fallas mediante ErrorDrive."""
    try:
        if not isinstance(drive_id, str) or not drive_id.strip():
            raise ErrorDrive("El identificador del documento no es válido.")
        with _usar_servicio(servicio) as cliente:
            archivo = cliente.files().get(
                fileId=drive_id, fields=_CAMPOS_ARCHIVO
            ).execute()
            if not isinstance(archivo, dict) or not all(
                isinstance(archivo.get(campo), str) and archivo[campo].strip()
                for campo in ("id", "name", "mimeType")
            ):
                raise ErrorDrive("Los metadatos del documento no son válidos.")
            return archivo
    except Exception:
        raise ErrorDrive("No se pudieron obtener los metadatos del documento de Drive.") from None


def descargar_contenido_archivo(
    file_id,
    *,
    tipo_mime: str | None = None,
    servicio=None,
    estricto: bool = False,
) -> bytes | None:
    """Descarga bytes o exporta Google Docs a PDF, íntegramente en memoria."""
    try:
        if not isinstance(file_id, str) or not file_id.strip():
            raise ErrorDrive("El identificador del documento no es válido.")
        with _usar_servicio(servicio) as cliente:
            if tipo_mime == TIPO_GOOGLE_DOCS:
                solicitud = cliente.files().export_media(fileId=file_id, mimeType=TIPO_PDF)
            else:
                solicitud = cliente.files().get_media(fileId=file_id)
            with BytesIO() as contenido:
                descarga = MediaIoBaseDownload(contenido, solicitud)
                terminado = False
                while not terminado:
                    _, terminado = descarga.next_chunk()
                return contenido.getvalue()
    except Exception:
        mensaje = "No se pudo descargar el documento de Google Drive."
        if estricto:
            raise ErrorDrive(mensaje) from None
        print(mensaje)
        return None

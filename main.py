import os
import io
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# === CONFIGURACIÓN ===
NOMBRE_ARCHIVO_LOCAL = 'reporte_demo.xlsx'
NOMBRE_ARCHIVO_DRIVE = 'REPORTE_SUBIDO_DESDE_RENDER.xlsx'
NOMBRE_CARPETA_DRIVE = 'REPORTE_ETIQUETADO'
SCOPES = ['https://www.googleapis.com/auth/drive']

# === CREACIÓN DEL ARCHIVO EXCEL SI NO EXISTE ===
from openpyxl import Workbook
if not os.path.exists(NOMBRE_ARCHIVO_LOCAL):
    wb = Workbook()
    ws = wb.active
    ws.title = "Datos"
    ws.append(["Fecha", "Dato"])
    ws.append([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Ejemplo"])
    wb.save(NOMBRE_ARCHIVO_LOCAL)

# === AUTENTICACIÓN ===
import json
from google.auth.transport.requests import Request

# ✅ Carga desde archivo secreto (no variable de entorno)
with open("/etc/secrets/GOOGLE_CREDENTIALS_JSON") as f:
    credentials_dict = json.load(f)

credentials = service_account.Credentials.from_service_account_info(
    credentials_dict, scopes=SCOPES)

service = build('drive', 'v3', credentials=credentials)

# === BUSCAR LA CARPETA DE DESTINO ===
def buscar_id_carpeta(nombre_carpeta):
    query = f"name = '{nombre_carpeta}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    resultados = service.files().list(q=query, spaces='drive', fields="files(id, name)").execute()
    archivos = resultados.get('files', [])
    if archivos:
        return archivos[0]['id']
    return None

carpeta_id = buscar_id_carpeta(NOMBRE_CARPETA_DRIVE)
if not carpeta_id:
    raise Exception("❌ Carpeta compartida no encontrada en Drive. Verifica el nombre y que esté compartida.")

# === SUBIR ARCHIVO .XLSX ===
archivo_metadata = {
    'name': NOMBRE_ARCHIVO_DRIVE,
    'parents': [carpeta_id]
}
media = MediaFileUpload(NOMBRE_ARCHIVO_LOCAL, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

archivo = service.files().create(
    body=archivo_metadata,
    media_body=media,
    fields='id'
).execute()

print(f"✅ Archivo subido correctamente. ID: {archivo.get('id')}")

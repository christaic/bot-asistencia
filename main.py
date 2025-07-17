import os
import json
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from openpyxl import Workbook

# === CONFIGURACIÓN ===
NOMBRE_ARCHIVO_LOCAL = 'reporte_demo.xlsx'
NOMBRE_ARCHIVO_DRIVE = 'REPORTE_SUBIDO_DESDE_RENDER.xlsx'
NOMBRE_CARPETA_DRIVE = 'REPORTE_ETIQUETADO'
DRIVE_ID = '0APLUfvLE2SqAUk9PVA'  # ✅ Tu unidad compartida
SCOPES = ['https://www.googleapis.com/auth/drive']

# === CREAR ARCHIVO DEMO SI NO EXISTE ===
if not os.path.exists(NOMBRE_ARCHIVO_LOCAL):
    wb = Workbook()
    ws = wb.active
    ws.title = "Datos"
    ws.append(["Fecha", "Dato"])
    ws.append([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Ejemplo"])
    wb.save(NOMBRE_ARCHIVO_LOCAL)

# === AUTENTICACIÓN DESDE ARCHIVO JSON ===
with open("/etc/secrets/GOOGLE_CREDENTIALS_JSON") as f:
    credentials_dict = json.load(f)

credentials = service_account.Credentials.from_service_account_info(
    credentials_dict, scopes=SCOPES)

service = build('drive', 'v3', credentials=credentials)

# === BUSCAR ID DE LA CARPETA ===
def buscar_id_carpeta(nombre_carpeta):
    query = f"name = '{nombre_carpeta}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    resultados = service.files().list(
        q=query,
        corpora='drive',  # ✅ Buscar solo dentro de unidad compartida
        driveId=DRIVE_ID,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
        fields="files(id, name)"
    ).execute()
    archivos = resultados.get('files', [])
    if archivos:
        return archivos[0]['id']
    return None

# === INTENTAR SUBIR ARCHIVO ===
carpeta_id = buscar_id_carpeta(NOMBRE_CARPETA_DRIVE)
if not carpeta_id:
    raise Exception("❌ Carpeta no encontrada. Asegúrate de que esté en la unidad compartida y compartida con la cuenta de servicio.")

metadata_archivo = {
    'name': NOMBRE_ARCHIVO_DRIVE,
    'parents': [carpeta_id]
}
media = MediaFileUpload(NOMBRE_ARCHIVO_LOCAL, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

try:
    archivo = service.files().create(
        body=metadata_archivo,
        media_body=media,
        fields='id, webViewLink',
        supportsAllDrives=True
    ).execute()

    print(f"✅ Archivo subido correctamente. ID: {archivo.get('id')}")
    print(f"🔗 Enlace: {archivo.get('webViewLink')}")

except Exception as e:
    print("❌ Error al subir archivo:")
    print(e)

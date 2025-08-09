import asyncio
import unicodedata, re
import os
import io
import json
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from google.oauth2 import service_account
from googleapiclient.discovery import build
from pytz import timezone

# Zona horaria de Lima (UTC-5)
LIMA_TZ = timezone("America/Lima")

# -------------------- CONFIGURACIÓN --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN") # Token del bot
NOMBRE_CARPETA_DRIVE = "ASISTENCIA_BOT"  # Carpeta principal
DRIVE_ID = "0AOy_EhsaSY_HUk9PVA"  # ID de la unidad compartida
ALLOWED_CHATS = [-1002640857147, -4718591093, -4831456255, -1002814603547, -1002838776671, -4951443286, -4870196969, -4824829490, -4979512409, -4903731585, -4910534813, -4845865029, -4643755320, -4860386920]  # Reemplaza con los IDs de tus grupos

def chat_permitido(chat_id: int) -> bool:
    """Verifica si el chat está permitido"""
    return chat_id in ALLOWED_CHATS

# -------------------- MENSAJE ES PARA BOT --------------------

def mensaje_es_para_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    En grupos/supergrupos:
      - True si es /comando mencionando al bot (/ingreso @Bot o /ingreso@Bot)
      - True si el mensaje es respuesta a un mensaje del bot
    En privado: siempre True
    """
    msg = update.message
    if not msg:
        return False

    chat_type = msg.chat.type
    bot_username = (context.bot.username or "").lower()
    texto = (msg.text or "").strip().lower()

    # En privado siempre responde
    if chat_type == "private":
        return True

    if chat_type in ("group", "supergroup"):
        # 1) /comando con mención (con o sin espacio)
        if texto.startswith("/") and f"@{bot_username}" in texto:
            return True

        # 2) Respuesta directa a un mensaje del bot
        r = msg.reply_to_message
        if r:
            # username del autor del mensaje al que se responde (puede ser None)
            replied_username = getattr(getattr(r, "from_user", None), "username", None)
            if replied_username and replied_username.lower() == bot_username:
                return True

        return False

    return False



# Carga de credenciales desde variable de entorno
CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

# -------------------- LOGGING --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Error handler global ---
async def log_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("[UNHANDLED] Excepción no controlada", exc_info=context.error)

# --------- GOOGLE APIs (Drive + Sheets) ----------
# Asegúrate que CREDENTIALS_JSON ya esté definido arriba
# scopes: Drive (lectura/escritura) + Sheets (lectura/escritura)
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

def get_services():
    creds_info = json.loads(CREDENTIALS_JSON)
    creds = service_account.Credentials.from_service_account_info(
        creds_info, scopes=SCOPES
    )
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    return drive, sheets

# --- Google Sheets helpers ---

# --- Helpers de Google Sheets (colócalos junto a tus otras funciones de Sheets) ---

def set_cell_value(spreadsheet_id: str, sheet_title: str, a1: str, value):
    """
    Escribe un solo valor en la celda A1 indicada (por ejemplo 'F12') en la hoja 'sheet_title'.
    """
    body = {"values": [[value]]}
    sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_title}!{a1}",
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()

def update_single_cell(spreadsheet_id: str, sheet_title: str, col_letter: str, row: int, value):
    """
    Actualiza UNA sola celda en formato A1 (p.ej. Registros!F2) usando USER_ENTERED.
    No toca fórmulas de otras columnas.
    """
    range_name = f"{sheet_title}!{col_letter}{row}"
    body = {"values": [[value]]}
    try:
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()
        logger.info(f"[DEBUG] update_single_cell OK -> {range_name} = {value}")
    except Exception as e:
        logger.error(f"[ERROR] update_single_cell {range_name}: {e}")
        raise


# Inicializa servicios (¡debe ir antes de usar drive_service/sheets_service!)
drive_service, sheets_service = get_services()

def gs_set_cell(spreadsheet_id: str, row: int, header: str, value):
    """Escribe una sola celda por encabezado sin tocar fórmulas de otras columnas."""
    col = COL[header]  # p.ej. "D" para "TIPO DE TRABAJO"
    rng = f"{SHEET_TITLE}!{col}{row}"
    body = {"values": [[value]]}
    # sheets_service debe ser tu cliente de Google Sheets (v4)
    sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=rng,
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()


def get_or_create_main_folder():
    """Busca la carpeta principal en la unidad compartida. Si no existe, la crea."""
    query = f"name='{NOMBRE_CARPETA_DRIVE}' and '{DRIVE_ID}' in parents and trashed=false"
    results = drive_service.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    # Crear carpeta si no existe
    metadata = {
        "name": NOMBRE_CARPETA_DRIVE,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [DRIVE_ID]
    }
    folder = drive_service.files().create(
        body=metadata,
        fields="id",
        supportsAllDrives=True
    ).execute()
    return folder["id"]

# ID de la carpeta principal
MAIN_FOLDER_ID = get_or_create_main_folder()

# ================== Google Sheets (constantes) ==================
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
SHEET_TITLE = "Registros"

HEADERS = ["MES","FECHA","CUADRILLA","TIPO DE TRABAJO","ATS/PETAR",
           "HORA INGRESO","HORA BREAK OUT","HORA BREAK IN","HORA SALIDA"]

COL = {
    "MES":"A","FECHA":"B","CUADRILLA":"C","TIPO DE TRABAJO":"D","ATS/PETAR":"E",
    "HORA INGRESO":"F","HORA BREAK OUT":"G","HORA BREAK IN":"H","HORA SALIDA":"I",
}

MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
# ===============================================================

# -------------------- ESTRUCTURA DE FILA --------------------
def generar_base_data(cuadrilla, tipo_trabajo):
    ahora = datetime.now(LIMA_TZ)
    mes = MESES[ahora.month - 1]
    return {
        "MES": mes,
        "FECHA": str(ahora.strftime("%Y-%m-%d")),
        "CUADRILLA": str(cuadrilla),
        "TIPO DE TRABAJO": str(tipo_trabajo),
        "ATS/PETAR": "",
        "HORA INGRESO": "",
        "HORA BREAK OUT": "",
        "HORA BREAK IN": "",
        "HORA SALIDA": "",
    }

# --------------NOMBRE LIMPIO------------------
# Si lo pones en False, el archivo se llamará solo con el nombre limpio (riesgo de duplicados).
UNIQUE_BY_CHAT_ID = True

def _sanitize_name(text: str) -> str:
    """Quita acentos, símbolos raros y espacios extra para usar como nombre de archivo."""
    base = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode()
    base = re.sub(r'[\\/*?:"<>._|]', "", base)  # inválidos en nombres
    base = re.sub(r"\s+", " ", base).strip()
    return base

def nombre_limpio_grupo(update: Update) -> str:
    """Devuelve solo el nombre limpio (útil para mostrar en mensajes)."""
    titulo = update.effective_chat.title or f"GRUPO {update.effective_chat.id}"
    return _sanitize_name(titulo)

def nombre_archivo_grupo(update: Update) -> str:
    """
    Devuelve el nombre EXACTO del archivo (Google Sheet): el título del grupo,
    tal cual, sin limpiar ni añadir .xlsx
    """
    return (update.effective_chat.title or f"GRUPO {update.effective_chat.id}").strip()


#-------------Búsqueda en Drive por nombre-------------#

def buscar_archivo_en_drive(nombre_archivo: str, mime: str | None = None):
    # Reemplaza tu versión anterior por esta (acepta mime opcional)
    q = [
        f"name='{nombre_archivo}'",
        f"'{MAIN_FOLDER_ID}' in parents",
        "trashed=false",
    ]
    if mime:
        q.append(f"mimeType='{mime}'")
    query = " and ".join(q)

    results = drive_service.files().list(
        q=query,
        fields="files(id, name, mimeType)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = results.get("files", [])
    return files[0] if files else None


#-------------Crear (si falta) el spreadsheet del grupo y asegurar hoja/encabezados--------------#

def ensure_spreadsheet_for_group(update: Update) -> str:
    """
    Asegura que exista el Google Sheet para este grupo y devuelve su file_id.
    Si no existe, lo crea dentro de MAIN_FOLDER_ID.
    """
    name = nombre_archivo_grupo(update)
    archivo = buscar_archivo_en_drive(name, SHEET_MIME)
    if archivo:
        return archivo["id"]

    meta = {
        "name": name,
        "mimeType": SHEET_MIME,
        "parents": [MAIN_FOLDER_ID],
    }
    created = drive_service.files().create(
        body=meta,
        fields="id",
        supportsAllDrives=True
    ).execute()
    return created["id"]


def ensure_sheet_and_headers(spreadsheet_id: str):
    """
    Asegura que exista una pestaña llamada SHEET_TITLE y que la fila 1 tenga HEADERS.
    Además congela fila 1 (opcional).
    """
    # 1) Obtener metadata
    meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = meta.get("sheets", [])
    sheet_id = None
    for s in sheets:
        if s["properties"]["title"] == SHEET_TITLE:
            sheet_id = s["properties"]["sheetId"]
            break

    requests = []

    # 2) Crear la hoja si no existe
    if sheet_id is None:
        requests.append({
            "addSheet": {
                "properties": {
                    "title": SHEET_TITLE,
                    "gridProperties": {"frozenRowCount": 1}
                }
            }
        })
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests}
        ).execute()

    # 3) Asegurar headers en A1:I1
    vr = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{SHEET_TITLE}!A1:I1"
    ).execute()
    row = vr.get("values", [])
    if not row or row[0] != HEADERS:
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{SHEET_TITLE}!A1:I1",
            valueInputOption="RAW",
            body={"values": [HEADERS]}
        ).execute()

def append_base_row(spreadsheet_id: str, data: dict) -> int:
    """
    Inserta una nueva fila (vacía o con base) bajo los HEADERS y devuelve el número de fila insertada.
    Devuelve el NÚMERO de fila (2, 3, 4, ...).
    """
    ahora = datetime.now(LIMA_TZ)
    payload = {
        "MES": ahora.strftime("%B"),
        "FECHA": ahora.strftime("%Y-%m-%d"),
        "CUADRILLA": data.get("CUADRILLA", ""),
        "TIPO DE TRABAJO": data.get("TIPO DE TRABAJO", ""),
        "ATS/PETAR": "",
        "HORA INGRESO": "",
        "HORA BREAK OUT": "",
        "HORA BREAK IN": "",
        "HORA SALIDA": "",
    }
    row = [[payload.get(h, "") for h in HEADERS]]

    resp = sheets_service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{SHEET_TITLE}!A:A",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": row}
    ).execute()

    # Parsear fila desde updatedRange (p.ej. "Registros!A5:I5")
    updated_range = resp.get("updates", {}).get("updatedRange", "")
    # ... A5:I5 -> fila 5
    try:
        a1 = updated_range.split("!")[1].split(":")[0]  # "A5"
        fila = int(''.join([c for c in a1 if c.isdigit()]))
    except Exception:
        fila = None
    return fila or 2  # fallback

def update_cell(spreadsheet_id: str, col_key: str, row: int, value: str):
    """
    Actualiza UNA celda (col_key es el encabezado, no la letra).
    """
    col_letter = COL[col_key]
    sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{SHEET_TITLE}!{col_letter}{row}",
        valueInputOption="USER_ENTERED",
        body={"values": [[value]]}
    ).execute()

# -------------------- ESTADOS TEMPORALES --------------------
user_data = {}

# -------------------- BOT INFO --------------------
BOT_USERNAME = None

async def init_bot_info(app):
    global BOT_USERNAME
    bot_info = await app.bot.get_me()
    BOT_USERNAME = f"@{bot_info.username}"
    logger.info(f"Bot iniciado como {BOT_USERNAME}")

#_--------------------Insertar la fila base y obtener el número de fila----------#
def _parse_row_from_updated_range(updated_range: str) -> int:
    # Ej: "Registros!A2:I2" o "'Registros'!A2:I2"
    tail = updated_range.split("!")[1]  # "A2:I2"
    a1 = tail.split(":")[0]             # "A2"
    row = int(re.findall(r"\d+", a1)[0])
    return row

def gs_append_base_row(ssid: str, data: dict) -> int:
    # Ordenar valores según HEADERS
    row_vals = [[ data.get(h, "") for h in HEADERS ]]
    resp = sheets_service.spreadsheets().values().append(
        spreadsheetId=ssid,
        range=f"{SHEET_TITLE}!A:I",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": row_vals}
    ).execute()
    return _parse_row_from_updated_range(resp["updates"]["updatedRange"])

#-------------------Actualizar celdas específicas (sin tocar fórmulas en J+)--------#

def gs_update_cells(ssid: str, row: int, updates: dict[str, str]):
    # updates: {"TIPO DE TRABAJO": "Ordenamiento", "HORA INGRESO": "08:15"}
    data = []
    for header, value in updates.items():
        col = COL[header]
        data.append({"range": f"{SHEET_TITLE}!{col}{row}", "values": [[value]]})
    sheets_service.spreadsheets().values().batchUpdate(
        spreadsheetId=ssid,
        body={"valueInputOption":"USER_ENTERED", "data": data}
    ).execute()

# -------------------- VALIDACIÓN DE CONTENIDO --------------------

async def validar_contenido(update: Update, tipo: str):
    if tipo == "texto" and not update.message.text:
        await update.message.reply_text("⚠️ Debes enviar el *nombre de tu cuadrilla* en texto. ✍️📝")
        return False
    if tipo == "foto" and not update.message.photo:
        await update.message.reply_text("⚠️ Debes enviar una *foto*, no texto.🤳📸")
        return False
    return True

# -------------------- COMANDOS DEL BOT --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not chat_permitido(chat_id):
        return
    if update.message.chat.type in ['group', 'supergroup']:
        if not mensaje_es_para_bot(update, context):
            return

    await update.message.reply_text(
        "👋 ¡Hola! Para iniciar, usa el comando /ingreso y etiquetame 💪💪."
    )

async def ingreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id  # <-- Definir aquí
    if not chat_permitido(chat_id):
        return
    if update.message.chat.type in ['group', 'supergroup']:
        if not mensaje_es_para_bot(update, context):
            return

    chat_id = update.effective_chat.id
    user_data[chat_id] = {"paso": 0}  # 👈 Reinicia el flujo al paso 0

    await update.message.reply_text(
        "✍️ Escribe el nombre de tu cuadrilla\n\n"
        "Ejemplo:\nT1: Juan Pérez\nT2: José Flores"
    )

# -------------------- NOMBRE CUADRILLA --------------------
async def nombre_cuadrilla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info("[DEBUG] Entrando en nombre_cuadrilla...")
        if not mensaje_es_para_bot(update, context):
            logger.info("[DEBUG] mensaje_es_para_bot devolvió False.")
            return

        chat_id = update.effective_chat.id
        logger.info(f"[DEBUG] chat_id = {chat_id}")

        if chat_id not in user_data:
            user_data[chat_id] = {"paso": 0}
            logger.info(f"[DEBUG] user_data[{chat_id}] inicializado en 0")

        if user_data[chat_id].get("paso") != 0:
            logger.info(f"[DEBUG] Paso no es 0. Paso actual: {user_data[chat_id].get('paso')}")
            return

        if not await validar_contenido(update, "texto"):
            logger.info("[DEBUG] validar_contenido devolvió False.")
            return

        user_data[chat_id]["cuadrilla"] = update.message.text.strip()
        logger.info(f"[DEBUG] Cuadrilla recibida: {user_data[chat_id]['cuadrilla']}")

        keyboard = [
            [InlineKeyboardButton("✅ Confirma el nombre de tu cuadrilla", callback_data="confirmar_nombre")],
            [InlineKeyboardButton("✏️ Corregir nombre", callback_data="corregir_nombre")],
        ]
        await update.message.reply_text(
            f"Has ingresado la cuadrilla:\n*{user_data[chat_id]['cuadrilla']}*\n\n¿Es correcto?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        logger.info("[DEBUG] Botones enviados correctamente.")
    except Exception as e:
        logger.error(f"[ERROR] nombre_cuadrilla: {e}")
        await update.message.reply_text("❌ Error interno al procesar el nombre de cuadrilla.")


# ------------------ HANDLE NOMBRE CUADRILLA ------------------ #

async def handle_nombre_cuadrilla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        if not query:  # No es callback
            return

        chat_id = query.message.chat.id
        await query.answer()

        logger.info(f"[DEBUG] handle_nombre_cuadrilla -> data={query.data}, state={user_data.get(chat_id)}")

        if query.data == "confirmar_nombre":
            # Guardas mínimas
            if chat_id not in user_data or "cuadrilla" not in user_data[chat_id] or not user_data[chat_id]["cuadrilla"].strip():
                logger.warning(f"[WARN] No hay 'cuadrilla' para chat {chat_id}.")
                await query.edit_message_text("⚠️ No encontré el nombre de la cuadrilla. Escribe de nuevo y confirma.")
                user_data.setdefault(chat_id, {})["paso"] = 0
                return

            # Idempotencia: si ya existe fila creada, no vuelvas a crear otra
            if user_data[chat_id].get("spreadsheet_id") and user_data[chat_id].get("row"):
                logger.info(f"[DEBUG] Fila ya creada (sheet={user_data[chat_id]['spreadsheet_id']}, row={user_data[chat_id]['row']}). Saltando append.")
            else:
                # 1) Asegurar Sheet del grupo
                spreadsheet_id = ensure_spreadsheet_for_group(update)
                ensure_sheet_and_headers(spreadsheet_id)

                # 2) Crear la fila base y guardar referencia
                base = {"CUADRILLA": user_data[chat_id]["cuadrilla"], "TIPO DE TRABAJO": ""}
                fila = append_base_row(spreadsheet_id, base)
                user_data[chat_id]["spreadsheet_id"] = spreadsheet_id
                user_data[chat_id]["row"] = fila
                logger.info(f"[DEBUG] Fila creada -> sheet={spreadsheet_id}, row={fila}, cuadrilla='{base['CUADRILLA']}'")

            # 3) Avanzar de estado
            user_data[chat_id]["paso"] = "tipo_trabajo"
            logger.info(f"[DEBUG] Paso -> 'tipo_trabajo' (chat {chat_id})")

            keyboard = [
                [InlineKeyboardButton("📌 Ordenamiento", callback_data="tipo_ordenamiento")],
                [InlineKeyboardButton("🏷 Etiquetado", callback_data="tipo_etiquetado")],
            ]
            await query.edit_message_text("Selecciona el tipo de trabajo:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "corregir_nombre":
            user_data.setdefault(chat_id, {})
            user_data[chat_id]["cuadrilla"] = ""
            user_data[chat_id]["paso"] = 0
            logger.info(f"[DEBUG] Corrección de cuadrilla. Estado -> {user_data[chat_id]}")
            await query.edit_message_text(
                "✍️ *Escribe el nombre de tu cuadrilla*\n\n"
                "*Ejemplo:*\n"
                "*T1: Juan Pérez*\n"
                "*T2: José Flores*\n",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"[ERROR] handle_nombre_cuadrilla: {e}")
        # Evita crashear si query no existe por algún motivo
        try:
            await update.callback_query.message.reply_text("❌ Error interno en la confirmación de cuadrilla.")
        except Exception:
            pass


# ------------------ HANDLE TIPO TRABAJO ------------------ #

async def handle_tipo_trabajo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        if not query:
            return
        await query.answer()

        chat_id = query.message.chat.id
        data = query.data
        if data not in ("tipo_ordenamiento", "tipo_etiquetado"):
            logger.warning(f"[DEBUG] handle_tipo_trabajo: callback inesperado: {data}")
            return

        # 1) Determinar el tipo
        tipo = "Ordenamiento" if data == "tipo_ordenamiento" else "Etiquetado"
        user_data.setdefault(chat_id, {})
        user_data[chat_id]["tipo"] = tipo

        # 2) Asegurar que ya tenemos spreadsheet + fila
        spreadsheet_id = user_data[chat_id].get("spreadsheet_id")
        row = user_data[chat_id].get("row")

        if not spreadsheet_id or not row:
            # Guardas de seguridad: si por alguna razón no existe, lo creamos aquí
            spreadsheet_id = ensure_spreadsheet_for_group(update)
            ensure_sheet_and_headers(spreadsheet_id)
            base = {
                "CUADRILLA": user_data[chat_id].get("cuadrilla", ""),
                "TIPO DE TRABAJO": ""  # lo seteamos abajo
            }
            row = append_base_row(spreadsheet_id, base)
            user_data[chat_id]["spreadsheet_id"] = spreadsheet_id
            user_data[chat_id]["row"] = row
            logger.info(f"[DEBUG] (fallback) creada fila base -> sheet={spreadsheet_id}, row={row}")

        # 3) Actualizar SOLO la celda "TIPO DE TRABAJO" en esa fila
        gs_set_cell(spreadsheet_id, row, "TIPO DE TRABAJO", tipo)

        # 4) Avanzar de estado
        user_data[chat_id]["paso"] = 1
        logger.info(f"[DEBUG] Tipo de trabajo: {tipo}, row={row}, state={user_data[chat_id]}")

        # 5) Pedir selfie de ingreso
        await query.edit_message_text(
            f"Tipo de trabajo seleccionado: *{tipo}*\n\n📸 Ahora envía tu selfie de inicio.",
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"[ERROR] handle_tipo_trabajo: {e}")
        try:
            await update.callback_query.message.reply_text("❌ Error interno al seleccionar el tipo de trabajo.")
        except Exception:
            pass

async def foto_ingreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not mensaje_es_para_bot(update, context):
        return
    if chat_id not in user_data or user_data[chat_id].get("paso") != 1:
        return
    if not await validar_contenido(update, "foto"):
        return

    # Verifica que tengamos hoja y fila
    spreadsheet_id = user_data.get(chat_id, {}).get("spreadsheet_id")
    row = user_data.get(chat_id, {}).get("row")
    if not spreadsheet_id or not row:
        logger.error(f"[ERROR] foto_ingreso: faltan spreadsheet_id/row en user_data[{chat_id}] = {user_data.get(chat_id)}")
        await update.message.reply_text("❌ No hay registro activo. Usa /ingreso para iniciar.")
        return

    hora_ingreso = datetime.now(LIMA_TZ).strftime("%H:%M")
    user_data[chat_id]["hora_ingreso"] = hora_ingreso

    # Actualizamos SOLO la celda de HORA INGRESO en esa fila
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            update_single_cell,
            spreadsheet_id,
            SHEET_TITLE,
            COL["HORA INGRESO"],  # F normalmente
            row,
            hora_ingreso
        )
    except Exception as e:
        logger.error(f"[ERROR] foto_ingreso: {e}")
        await update.message.reply_text("❌ No se pudo guardar la hora de ingreso.")
        return

    keyboard = [
        [InlineKeyboardButton("🔄 Repetir Selfie", callback_data="repetir_foto_inicio")],
        [InlineKeyboardButton("📝📋 Continuar con ATS/PETAR", callback_data="continuar_ats")],
    ]
    await update.message.reply_text("¿Es correcto el selfie de inicio?", reply_markup=InlineKeyboardMarkup(keyboard))


# -------------------- MANEJAR REPETICIÓN DE FOTOS --------------------
async def manejar_repeticion_fotos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        if not query:
            logger.warning("[DEBUG] manejar_repeticion_fotos llamado sin callback_query.")
            return

        chat_id = query.message.chat.id
        await query.answer()
        logger.info(f"[DEBUG] manejar_repeticion_fotos: chat_id={chat_id}, data={query.data}")

        # Teclado genérico para ATS
        ats_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ATS/PETAR Sí", callback_data="ats_si")],
            [InlineKeyboardButton("❌ ATS/PETAR No", callback_data="ats_no")],
        ])

        # --- SELFIE INICIO ---
        if query.data == "repetir_foto_inicio":
            user_data.setdefault(chat_id, {})["paso"] = 1
            logger.info(f"[DEBUG] Paso cambiado a 1 (selfie inicio) para chat {chat_id}")
            await query.edit_message_text(
                "📸 Envía nuevamente tu *selfie de inicio*.", parse_mode="Markdown"
            )

        elif query.data == "continuar_ats":
            await query.edit_message_text("¿Realizaste ATS/PETAR?", reply_markup=ats_keyboard)

        # --- ATS/PETAR ---
        elif query.data == "repetir_foto_ats":
            user_data.setdefault(chat_id, {})["paso"] = 2
            logger.info(f"[DEBUG] Paso cambiado a 2 (repetir foto ATS) para chat {chat_id}")
            await query.edit_message_text(
                "📸 Envía nuevamente la *foto del ATS/PETAR*.", parse_mode="Markdown"
            )

        elif query.data == "reenviar_ats":
            # Opción cuando eligieron "No" pero quieren enviar foto igual
            user_data.setdefault(chat_id, {})["paso"] = 2
            logger.info(f"[DEBUG] Paso cambiado a 2 (reenviar ATS) para chat {chat_id}")
            await query.edit_message_text(
                "Ok. 📸 Envía la *foto del ATS/PETAR* de todas formas.", parse_mode="Markdown"
            )

        elif query.data == "continuar_post_ats":
            user_data.setdefault(chat_id, {})["paso"] = "selfie_salida"
            logger.info(f"[DEBUG] Paso cambiado a 'selfie_salida' para chat {chat_id}")

            # 1) Edita el mensaje anterior para cerrar el hilo
            await query.edit_message_text("✅ ¡Registro completado!")

            # 2) Envía el motivador y guarda su message_id para ignorar replies
            mensaje = await context.bot.send_message(
                chat_id=chat_id,
                text="¡Excelente! 🎉 Ya estás listo para comenzar.\n\n💪 *Puedes iniciar tu jornada.* 💪",
                parse_mode="Markdown"
            )
            user_data[chat_id]["msg_id_motivador"] = mensaje.message_id

        # --- SELFIE SALIDA ---
        elif query.data == "repetir_foto_salida":
            user_data.setdefault(chat_id, {})
            user_data[chat_id].pop("selfie_salida", None)
            user_data[chat_id]["paso"] = "selfie_salida"
            logger.info(f"[DEBUG] Repetir selfie salida, paso='selfie_salida' para chat {chat_id}")
            await query.edit_message_text(
                "📸 Por favor, envía nuevamente tu *selfie de salida*.",
                parse_mode="Markdown"
            )

        else:
            logger.info(f"[DEBUG] Callback no reconocido en manejar_repeticion_fotos: {query.data}")

    except Exception as e:
        logger.error(f"[ERROR] manejar_repeticion_fotos: {e}")
        if update.callback_query:
            await update.callback_query.message.reply_text("❌ Error interno al manejar repetición de fotos.")

# -------------------- FOTO ATS/PETAR --------------------

async def foto_ats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not mensaje_es_para_bot(update, context):
            return

        chat_id = update.effective_chat.id
        ud = user_data.get(chat_id) or {}

        # Debe venir de "ats_si" (paso 2)
        if ud.get("paso") != 2:
            return
        if not await validar_contenido(update, "foto"):
            return

        # Asegurar Spreadsheet + Hoja + Fila activa
        spreadsheet_id = ud.get("spreadsheet_id")
        row = ud.get("row")

        if not spreadsheet_id or not row:
            # Fallback: si por alguna razón no existe, lo creamos
            spreadsheet_id = ensure_spreadsheet_for_group(update)
            ensure_sheet_and_headers(spreadsheet_id)
            base = {
                "CUADRILLA": ud.get("cuadrilla", ""),
                "TIPO DE TRABAJO": ud.get("tipo", "")
            }
            row = append_base_row(spreadsheet_id, base)
            ud["spreadsheet_id"] = spreadsheet_id
            ud["row"] = row

        # Marcar ATS/PETAR = "Sí" (solo esa celda) SIN cambiar el paso (se cambia con continuar_post_ats)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            update_single_cell,
            spreadsheet_id,
            SHEET_TITLE,
            COL["ATS/PETAR"],   # normalmente "E"
            row,
            "Sí"
        )

        ud["ats_foto"] = "OK"
        user_data[chat_id] = ud
        logger.info(f"[DEBUG] ATS/PETAR='Sí' escrito en fila={row}, sheet={spreadsheet_id}")

        # Botonera para confirmar o repetir
        keyboard = [
            [InlineKeyboardButton("🔄 Repetir Foto ATS/PETAR", callback_data="repetir_foto_ats")],
            [InlineKeyboardButton("➡️ Continuar a jornada", callback_data="continuar_post_ats")],
        ]
        await update.message.reply_text(
            "¿Es correcta la foto del ATS/PETAR?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"[ERROR] foto_ats: {e}")
        await update.message.reply_text("❌ Error al registrar la foto del ATS/PETAR. Intenta de nuevo.")

# -------------------- HANDLE ATS/PETAR --------------------
async def handle_ats_petar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        if not query:
            logger.warning("[DEBUG] handle_ats_petar llamado sin callback_query.")
            return

        chat_id = query.message.chat.id
        await query.answer()
        data = query.data
        logger.info(f"[DEBUG] handle_ats_petar: chat_id={chat_id}, data={data}")

        # Traer ids guardados al confirmar nombre (o crear fallback si faltan)
        spreadsheet_id = user_data.get(chat_id, {}).get("spreadsheet_id")
        row = user_data.get(chat_id, {}).get("row")

        # --- ATS: Sí -> pedimos foto y paso=2
        if data == "ats_si":
            user_data.setdefault(chat_id, {})["paso"] = 2
            logger.info(f"[DEBUG] Paso cambiado a 2 (espera foto ATS/PETAR) para chat {chat_id}")
            await query.edit_message_text(
                "📸 *Por favor, envía la foto del ATS/PETAR para continuar.*",
                parse_mode="Markdown"
            )
            return

        # --- ATS: No -> escribir 'No' en la fila y pasar a selfie_salida
        if data == "ats_no":
            # Fallback por si falta spreadsheet o fila (no debería, pero por seguridad)
            if not spreadsheet_id:
                spreadsheet_id = ensure_spreadsheet_for_group(update)
                ensure_sheet_and_headers(spreadsheet_id)
                user_data.setdefault(chat_id, {})["spreadsheet_id"] = spreadsheet_id

            if not row:
                base = {
                    "CUADRILLA": user_data.get(chat_id, {}).get("cuadrilla", ""),
                    "TIPO DE TRABAJO": user_data.get(chat_id, {}).get("tipo", ""),
                }
                row = append_base_row(spreadsheet_id, base)
                user_data[chat_id]["row"] = row
                logger.info(f"[DEBUG] Fallback: creada fila base {row} para chat {chat_id}")

            # Actualizar solo la celda ATS/PETAR de esa fila
            set_cell_value(spreadsheet_id, SHEET_TITLE, f"{COL['ATS/PETAR']}{row}", "No")
            logger.info(f"[DEBUG] ATS/PETAR='No' escrito en fila {row}")

            user_data[chat_id]["paso"] = "selfie_salida"

            # Botón por si igual desean enviar foto del ATS
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📸 Enviar foto de ATS/PETAR de todas formas", callback_data="reenviar_ats")]
            ])

            await query.edit_message_text(
                "⚠️ *Recuerda siempre realizar ATS/PETAR antes de iniciar la jornada.* ⚠️\n\n"
                "✅ Previenes accidentes.\n"
                "✅ Proteges tu vida y la de tu equipo.\n\n"
                "¡La seguridad empieza contigo! 💪",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            return

        # --- Reabrir la botonera ATS si llegan desde 'reenviar_ats'
        if data == "reenviar_ats":
            ats_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ ATS/PETAR Sí", callback_data="ats_si")],
                [InlineKeyboardButton("❌ ATS/PETAR No", callback_data="ats_no")],
            ])
            await query.edit_message_text("¿Realizaste ATS/PETAR?", reply_markup=ats_keyboard)
            return

        logger.warning(f"[DEBUG] handle_ats_petar: callback no manejado -> {data}")

    except Exception as e:
        logger.error(f"[ERROR] handle_ats_petar: {e}")
        try:
            await update.callback_query.message.reply_text("❌ Error interno en ATS/PETAR.")
        except Exception:
            pass


# -------------------- BREAK OUT --------------------

async def breakout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not mensaje_es_para_bot(update, context):
            return

        chat_id = update.effective_chat.id
        hora = datetime.now(LIMA_TZ).strftime("%H:%M")

        # Traer el spreadsheet y la fila de la jornada actual
        spreadsheet_id = user_data.get(chat_id, {}).get("spreadsheet_id")
        row = user_data.get(chat_id, {}).get("row")

        # Fallbacks por si algo faltara (no debería, pero mejor seguros)
        if not spreadsheet_id:
            spreadsheet_id = ensure_spreadsheet_for_group(update)
            ensure_sheet_and_headers(spreadsheet_id)
            user_data.setdefault(chat_id, {})["spreadsheet_id"] = spreadsheet_id
            logger.info(f"[DEBUG] breakout: creado/asegurado spreadsheet_id={spreadsheet_id}")

        if not row:
            base = {
                "CUADRILLA": user_data.get(chat_id, {}).get("cuadrilla", ""),
                "TIPO DE TRABAJO": user_data.get(chat_id, {}).get("tipo", ""),
            }
            row = append_base_row(spreadsheet_id, base)
            user_data[chat_id]["row"] = row
            logger.info(f"[DEBUG] breakout: creada fila base row={row}")

        # Escribir solo la celda de HORA BREAK OUT en la fila actual
        set_cell_value(spreadsheet_id, SHEET_TITLE, f"{COL['HORA BREAK OUT']}{row}", hora)
        logger.info(f"[DEBUG] breakout: set {COL['HORA BREAK OUT']}{row} = {hora}")

        await update.message.reply_text(f"🍽️😋 Salida a Break 😋🍽️, registrado a las {hora}.💪💪")

    except Exception as e:
        logger.error(f"[ERROR] breakout: {e}")
        await update.message.reply_text("❌ Error registrando Break Out. Intenta de nuevo.")


# -------------------- BREAK IN --------------------

async def breakin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not mensaje_es_para_bot(update, context):
            return

        chat_id = update.effective_chat.id
        hora = datetime.now(LIMA_TZ).strftime("%H:%M")

        # Recuperar contexto de la jornada actual
        spreadsheet_id = user_data.get(chat_id, {}).get("spreadsheet_id")
        row = user_data.get(chat_id, {}).get("row")

        # Fallback: asegurar spreadsheet y headers
        if not spreadsheet_id:
            spreadsheet_id = ensure_spreadsheet_for_group(update)
            ensure_sheet_and_headers(spreadsheet_id)
            user_data.setdefault(chat_id, {})["spreadsheet_id"] = spreadsheet_id
            logger.info(f"[DEBUG] breakin: creado/asegurado spreadsheet_id={spreadsheet_id}")

        # Fallback: si no hay fila activa, crear base
        if not row:
            base = {
                "CUADRILLA": user_data.get(chat_id, {}).get("cuadrilla", ""),
                "TIPO DE TRABAJO": user_data.get(chat_id, {}).get("tipo", ""),
            }
            row = append_base_row(spreadsheet_id, base)
            user_data[chat_id]["row"] = row
            logger.info(f"[DEBUG] breakin: creada fila base row={row}")

        # Escribir solo la celda de HORA BREAK IN
        set_cell_value(spreadsheet_id, SHEET_TITLE, f"{COL['HORA BREAK IN']}{row}", hora)
        logger.info(f"[DEBUG] breakin: set {COL['HORA BREAK IN']}{row} = {hora}")

        await update.message.reply_text(
            f"🚶🚀 Regreso de Break 🚀🚶, registrado a las {hora}👀👀.\n\n"
            " 💪 *Puedes continuar tu jornada.* 💪 "
        )

    except Exception as e:
        logger.error(f"[ERROR] breakin: {e}")
        await update.message.reply_text("❌ Error registrando Break In. Intenta de nuevo.")


# -------------------- SALIDA --------------------

async def salida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not mensaje_es_para_bot(update, context):
            return

        chat_id = update.effective_chat.id

        # Recuperar lo que ya tenemos guardado
        ud = user_data.setdefault(chat_id, {})
        spreadsheet_id = ud.get("spreadsheet_id")
        row = ud.get("row")

        # Asegurar que existe el spreadsheet del grupo y la hoja con headers
        if not spreadsheet_id:
            spreadsheet_id = ensure_spreadsheet_for_group(update)
            ensure_sheet_and_headers(spreadsheet_id)
            ud["spreadsheet_id"] = spreadsheet_id
            logger.info(f"[DEBUG] salida: asegurado spreadsheet_id={spreadsheet_id}")

        # Si por algún motivo no hay fila activa, creamos una base
        if not row:
            base = {
                "CUADRILLA": ud.get("cuadrilla", ""),
                "TIPO DE TRABAJO": ud.get("tipo", ""),
            }
            row = append_base_row(spreadsheet_id, base)
            ud["row"] = row
            logger.info(f"[DEBUG] salida: creada fila base row={row}")

        # Solo cambiamos el paso, sin resetear user_data del chat
        ud["paso"] = "selfie_salida"
        logger.info(f"[DEBUG] salida: paso='selfie_salida' chat_id={chat_id}, row={row}")

        await update.message.reply_text("📸 Envía tu selfie de salida para finalizar la jornada.")
    except Exception as e:
        logger.error(f"[ERROR] salida: {e}")
        await update.message.reply_text("❌ Error preparando la salida. Intenta de nuevo.")


# -------------------- CALLBACK SALIDA --------------------
async def manejar_salida_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        if not query:  # Aseguramos que es callback
            logger.warning("[DEBUG] manejar_salida_callback llamado sin callback_query.")
            return

        chat_id = query.message.chat.id
        await query.answer()
        logger.info(f"[DEBUG] manejar_salida_callback: chat_id={chat_id}, data={query.data}, user_data={user_data.get(chat_id)}")

        if query.data == "repetir_foto_salida":
            user_data[chat_id]["paso"] = "selfie_salida"
            logger.info(f"[DEBUG] Paso cambiado a 'selfie_salida' para chat {chat_id}")
            await query.edit_message_text(
                "🔄 Por favor, envía nuevamente tu *selfie de salida*.",
                parse_mode="Markdown"
            )

        elif query.data == "finalizar_salida":
            user_data[chat_id]["paso"] = None
            logger.info(f"[DEBUG] Jornada finalizada para chat {chat_id}")
            await query.edit_message_text(
                "💪 *¡Buen trabajo! Jornada finalizada.*\n\n"
                "👏 *Gracias por tu apoyo hoy.*\n\n"
                "🫡 ¡Cambio y fuera! 🫡",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"[ERROR] manejar_salida_callback: {e}")
        if update.callback_query:
            await update.callback_query.message.reply_text("❌ Error interno en la salida.")

# -------------------- SELFIE SALIDA --------------------

async def selfie_salida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # ⚠️ No valides mensaje_es_para_bot aquí: la foto puede venir sin mención
        chat_id = update.effective_chat.id
        ud = user_data.get(chat_id) or {}

        # Solo procede si estamos pidiendo selfie de salida
        if ud.get("paso") != "selfie_salida":
            logger.info(f"[DEBUG] selfie_salida ignorado, paso actual: {ud}")
            return

        if not await validar_contenido(update, "foto"):
            return

        # Asegurar Spreadsheet + Hoja + Fila activa
        spreadsheet_id = ud.get("spreadsheet_id")
        if not spreadsheet_id:
            spreadsheet_id = ensure_spreadsheet_for_group(update)
            ensure_sheet_and_headers(spreadsheet_id)
            ud["spreadsheet_id"] = spreadsheet_id
        else:
            ensure_sheet_and_headers(spreadsheet_id)  # idempotente

        row = ud.get("row")
        if not row:
            base = {
                "CUADRILLA": ud.get("cuadrilla", ""),
                "TIPO DE TRABAJO": ud.get("tipo", "")
            }
            row = append_base_row(spreadsheet_id, base)
            ud["row"] = row

        # Escribir HORA SALIDA en la celda de la fila activa
        hora_salida = datetime.now(LIMA_TZ).strftime("%H:%M")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            update_single_cell,
            spreadsheet_id,
            SHEET_TITLE,
            COL["HORA SALIDA"],  # p.ej. "I"
            row,
            hora_salida
        )
        ud["hora_salida"] = hora_salida
        user_data[chat_id] = ud
        logger.info(f"[DEBUG] HORA SALIDA '{hora_salida}' escrita en {COL['HORA SALIDA']}{row} (sheet={spreadsheet_id})")

        # Teclado de confirmación
        keyboard = [
            [InlineKeyboardButton("🔄 Repetir Selfie de Salida", callback_data="repetir_foto_salida")],
            [InlineKeyboardButton("✅ Finalizar Jornada", callback_data="finalizar_salida")],
        ]
        await update.message.reply_text(
            f"🚪 Hora de salida registrada a las *{hora_salida}*.\n\n¿Está correcta la selfie?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        # No cambiamos el paso aquí; se cierra en manejar_salida_callback -> "finalizar_salida"

    except Exception as e:
        logger.error(f"[ERROR] selfie_salida: {e}")
        await update.message.reply_text("❌ Error interno al registrar la selfie de salida.")


# -------------------- MANEJAR FOTOS --------------------

async def manejar_fotos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id

        # ⛔ Ignorar si es respuesta al mensaje motivador (las fotos no tienen texto/comando)
        if update.message.reply_to_message:
            if update.message.reply_to_message.message_id == user_data.get(chat_id, {}).get("msg_id_motivador"):
                logger.info(f"[DEBUG] Ignorado: respuesta al motivador. chat_id={chat_id}")
                return

        # 📸 En fotos NO verifiques mensaje_es_para_bot (no hay /comando ni mención)
        paso = user_data.get(chat_id, {}).get("paso")
        logger.info(f"[DEBUG] manejar_fotos paso={paso} chat_id={chat_id}")

        if paso == 1:
            await foto_ingreso(update, context)
        elif paso == 2:
            await foto_ats(update, context)
        elif paso == "selfie_salida":
            await selfie_salida(update, context)
        else:
            await update.message.reply_text(
                "⚠️ No es momento de enviar fotos.\n\nUsa /ingreso @TuBot para comenzar."
            )
    except Exception as e:
        logger.error(f"[ERROR] manejar_fotos: {e}")

# -------------------- MAIN --------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.post_init = init_bot_info  # ok si es async, PTB lo maneja internamente

    # --------- COMANDOS PRINCIPALES ---------
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ingreso", ingreso))
    app.add_handler(CommandHandler("breakout", breakout))
    app.add_handler(CommandHandler("breakin", breakin))
    app.add_handler(CommandHandler("salida", salida))

    # --------- MENSAJES ---------
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, nombre_cuadrilla))
    app.add_handler(MessageHandler(filters.PHOTO, manejar_fotos))

    # --------- CALLBACKS CUADRILLA ---------
    app.add_handler(CallbackQueryHandler(handle_nombre_cuadrilla, pattern="^(confirmar_nombre|corregir_nombre)$"))
    app.add_handler(CallbackQueryHandler(handle_tipo_trabajo, pattern="^tipo_"))

    # --------- CALLBACKS ATS/PETAR ---------
    app.add_handler(CallbackQueryHandler(handle_ats_petar, pattern="^ats_(si|no)$"))
    app.add_handler(CallbackQueryHandler(manejar_repeticion_fotos, pattern="^(continuar_ats|repetir_foto_inicio|repetir_foto_ats|continuar_post_ats|reenviar_ats)$"))

    # --------- CALLBACKS SALIDA ---------
    app.add_handler(CallbackQueryHandler(manejar_salida_callback, pattern="^(repetir_foto_salida|finalizar_salida)$"))

    # --------- ERRORES ---------
    app.add_error_handler(log_error)

    print("🚀 Bot de Asistencia en ejecución...")
    app.run_polling()  # <-- SIN await

if __name__ == "__main__":
    main()  # <-- SIN asyncio.run y sin nest_asyncio




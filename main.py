import asyncio
import re
import unicodedata
import os
import io
import json
import logging
from datetime import datetime
import pandas as pd
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
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from pytz import timezone

# Zona horaria de Lima (UTC-5)
LIMA_TZ = timezone("America/Lima")

# -------------------- CONFIGURACIÓN --------------------
BOT_TOKEN = "8105661196:AAG1-NNOUHp6_0joPdP5CXSOCPTHxkPLKmI"  # Token del bot
NOMBRE_CARPETA_DRIVE = "ASISTENCIA_BOT"  # Carpeta principal
DRIVE_ID = "0AOy_EhsaSY_HUk9PVA"  # ID de la unidad compartida
ALLOWED_CHATS = [-1002640857147, -4718591093, -4831456255, -1002814603547, -1002838776671, -4951443286, -4870196969, -4824829490, -4979512409, -4903731585, -4910534813, -4845865029, -4643755320, -4860386920]  # Reemplaza con los IDs de tus grupos

def chat_permitido(chat_id: int) -> bool:
    """Verifica si el chat está permitido"""
    return chat_id in ALLOWED_CHATS

# -------------------- MENSAJE ES PARA BOT --------------------
def mensaje_es_para_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Verifica si el mensaje está dirigido al bot:
    - En grupos: si está etiquetado con un comando (/comando @Bot) o si responden a un mensaje del bot.
    - En privado: siempre responde.
    """
    if not update.message:
        return False

    chat_type = update.message.chat.type
    bot_username = context.bot.username.lower()
    texto = (update.message.text or "").strip().lower()

    # En privado siempre responde
    if chat_type == "private":
        return True

    # En grupo o supergrupo:
    if chat_type in ["group", "supergroup"]:
        # 1. Si es un comando con mención (ejemplo: /ingreso @Bot)
        if texto.startswith("/") and f"@{bot_username}" in texto:
            return True

        # 2. Si el mensaje es respuesta a un mensaje enviado por el bot
        if update.message.reply_to_message and \
           update.message.reply_to_message.from_user.username and \
           update.message.reply_to_message.from_user.username.lower() == bot_username:
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

# -------------------- GOOGLE DRIVE SERVICE --------------------
def get_drive_service():
    creds_dict = json.loads(CREDENTIALS_JSON)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

drive_service = get_drive_service()

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

# -------------------- FUNCIONES DE GOOGLE DRIVE --------------------
def buscar_archivo_en_drive(nombre_archivo):
    query = f"name='{nombre_archivo}' and '{MAIN_FOLDER_ID}' in parents and trashed=false"
    results = drive_service.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = results.get("files", [])
    return files[0] if files else None

def descargar_excel(file_id):
    request = drive_service.files().get_media(fileId=file_id, supportsAllDrives=True)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)
    return pd.read_excel(fh)

def subir_excel(file_id, df):
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    media = MediaIoBaseUpload(buffer, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    drive_service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()

# --------------NOMBRE LIMPIO------------------
def obtener_nombre_grupo_y_archivo(update: Update):
    """Obtiene el nombre del grupo y devuelve: (archivo_excel, nombre_limpio)"""
    nombre_grupo = update.effective_chat.title or f"GRUPO {update.effective_chat.id}"
    nombre_ascii = unicodedata.normalize("NFKD", nombre_grupo).encode("ASCII", "ignore").decode()
    nombre_limpio = re.sub(r'[\\/*?:"<>|]', '', nombre_ascii).strip()[:100]
    return f"{nombre_limpio}.xlsx", nombre_limpio


# --------------CREAR O ACTUALIZAR EXCEL------------------
def crear_o_actualizar_excel(update: Update, data: dict):
    try:
        nombre_grupo = update.effective_chat.title
        nombre_archivo = f"{nombre_grupo}.xlsx"
        logger.info(f"[DEBUG] Procesando archivo: {nombre_archivo} con datos: {data}")

        archivo_drive = buscar_archivo_en_drive(nombre_archivo)
        if archivo_drive:
            logger.info(f"[DEBUG] Archivo existente en Drive: {archivo_drive['name']}")
            df = descargar_excel(archivo_drive['id'])
            df = pd.concat([df, pd.DataFrame([data])], ignore_index=True)
            subir_excel(archivo_drive['id'], df)
            logger.info(f"[DEBUG] Archivo {nombre_archivo} actualizado en Drive.")
        else:
            logger.info(f"[DEBUG] Archivo no encontrado, creando {nombre_archivo}")
            df = pd.DataFrame([data])
            buffer = io.BytesIO()
            df.to_excel(buffer, index=False)
            buffer.seek(0)
            drive_service.files().create(
                media_body=MediaIoBaseUpload(buffer, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                body={'name': nombre_archivo, 'parents': [DRIVE_FOLDER_ID]}
            ).execute()
            logger.info(f"[DEBUG] Archivo {nombre_archivo} creado en Drive.")
    except Exception as e:
        logger.error(f"[ERROR] crear_o_actualizar_excel: {e}")


# -------------------- ESTRUCTURA DE FILA --------------------
def generar_base_data(cuadrilla, tipo_trabajo):
    ahora = datetime.now(LIMA_TZ)
    return {
        "MES": str(ahora.strftime("%B")),
        "FECHA": str(ahora.strftime("%Y-%m-%d")),
        "CUADRILLA": str(cuadrilla),
        "TIPO DE TRABAJO": str(tipo_trabajo),
        "ATS/PETAR": "",
        "HORA INGRESO": "",
        "HORA BREAK OUT": "",
        "HORA BREAK IN": "",
        "HORA SALIDA": "",
    }


# -------------------- ESTADOS TEMPORALES --------------------
user_data = {}

# -------------------- BOT INFO --------------------
BOT_USERNAME = None

async def init_bot_info(app):
    global BOT_USERNAME
    bot_info = await app.bot.get_me()
    BOT_USERNAME = f"@{bot_info.username}"
    logger.info(f"Bot iniciado como {BOT_USERNAME}")

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
        if not query:  # No es un callback
            return

        chat_id = query.message.chat.id
        await query.answer()
        logger.info(f"[DEBUG] Llego al handler de nombre_cuadrilla con data = {query.data}")
        logger.info(f"[DEBUG] Callback: {query.data}, user_data: {user_data.get(chat_id)}")

        if query.data == "confirmar_nombre":
            # Crear una fila inicial en el Excel (si deseas guardar al confirmar)
            data = generar_base_data(user_data[chat_id]["cuadrilla"], "")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, crear_o_actualizar_excel, update, data)

            user_data[chat_id]["paso"] = "tipo_trabajo"
            logger.info(f"[DEBUG] Paso cambiado a 'tipo_trabajo' para chat {chat_id}")

            keyboard = [
                [InlineKeyboardButton("📌 Ordenamiento", callback_data="tipo_ordenamiento")],
                [InlineKeyboardButton("🏷 Etiquetado", callback_data="tipo_etiquetado")],
            ]
            await query.edit_message_text("Selecciona el tipo de trabajo:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "corregir_nombre":
            user_data[chat_id]["cuadrilla"] = ""
            user_data[chat_id]["paso"] = 0
            logger.info(f"[DEBUG] Corrección de cuadrilla: {user_data[chat_id]}")
            await query.edit_message_text(
                "✍️ *Escribe el nombre de tu cuadrilla*\n\n"
                "*Ejemplo:*\n"
                "*T1: Juan Pérez*\n"
                "*T2: José Flores*\n",
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"[ERROR] handle_nombre_cuadrilla: {e}")
        await query.message.reply_text("❌ Error interno en la confirmación de cuadrilla.")

# ------------------ HANDLE TIPO TRABAJO ------------------ #
async def handle_tipo_trabajo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        if not query:  # Aseguramos que es callback
            return

        query = update.callback_query
        chat_id = query.message.chat.id
        await query.answer()

        tipo = "Ordenamiento" if query.data == "tipo_ordenamiento" else "Etiquetado"
        user_data[chat_id]["tipo"] = tipo
        user_data[chat_id]["paso"] = 1
        logger.info(f"[DEBUG] Tipo de trabajo: {tipo}, estado: {user_data[chat_id]}")

        nombre_grupo = query.message.chat.title
        archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")
        if archivo_drive:
            df = descargar_excel(archivo_drive["id"])
            df.at[df.index[-1], "TIPO DE TRABAJO"] = tipo
            subir_excel(archivo_drive["id"], df)
        else:
            data = generar_base_data(user_data.get(chat_id, {}).get("cuadrilla", ""), tipo)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, crear_o_actualizar_excel, update, data)

        await query.edit_message_text(
            f"Tipo de trabajo seleccionado: *{tipo}*\n\n📸 Ahora envía tu selfie de inicio.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"[ERROR] handle_tipo_trabajo: {e}")
        await update.callback_query.message.reply_text("❌ Error interno al seleccionar el tipo de trabajo.")


async def foto_ingreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not mensaje_es_para_bot(update, context):
        return
        
    if chat_id not in user_data or user_data[chat_id].get("paso") != 1:
        return
    if not await validar_contenido(update, "foto"):
        return
        
    hora_ingreso = datetime.now(LIMA_TZ).strftime("%H:%M")
    user_data[chat_id]["hora_ingreso"] = hora_ingreso

    # Aquí ya actualizamos la fila existente en Excel
    nombre_grupo = update.effective_chat.title
    archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")
    if archivo_drive:
        df = descargar_excel(archivo_drive["id"])
        df.at[df.index[-1], "HORA INGRESO"] = hora_ingreso
        subir_excel(archivo_drive["id"], df)
    else:
        await update.message.reply_text("❌ No hay registro de cuadrilla. Usa /ingreso para iniciar.")
        return

    keyboard = [
        [InlineKeyboardButton("🔄 Repetir Selfie", callback_data="repetir_foto_inicio")],
        [InlineKeyboardButton("📝📋 Continuar con ATS/PETAR", callback_data="continuar_ats")],
    ]
    await update.message.reply_text(
        "¿Es correcto el selfie de inicio?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


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
            await query.edit_message_text("¿Realizaste ATS/PETAR?", reply_markup=ats_keyboard)

        elif query.data == "continuar_post_ats":
            user_data.setdefault(chat_id, {})["paso"] = "selfie_salida"
            logger.info(f"[DEBUG] Paso cambiado a 'selfie_salida' para chat {chat_id}")
            await query.edit_message_text(
                "¡Excelente! 🎉 Ya estás listo para comenzar.\n\n"
                "💪 *Puedes iniciar tu jornada.* 💪",
                parse_mode="Markdown"
            )

        # --- SELFIE SALIDA ---
        elif query.data == "repetir_foto_salida":
            user_data.setdefault(chat_id, {})
            if "selfie_salida" in user_data[chat_id]:
                del user_data[chat_id]["selfie_salida"]
            user_data[chat_id]["paso"] = "selfie_salida"
            logger.info(f"[DEBUG] Repetir selfie salida, paso='selfie_salida' para chat {chat_id}")
            await query.edit_message_text(
                "📸 Por favor, envía nuevamente tu *selfie de salida*.",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"[ERROR] manejar_repeticion_fotos: {e}")
        if update.callback_query:
            await update.callback_query.message.reply_text("❌ Error interno al manejar repetición de fotos.")

# -------------------- FOTO ATS/PETAR --------------------
async def foto_ats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not mensaje_es_para_bot(update, context):
        return

    if chat_id not in user_data or user_data[chat_id].get("paso") != 2:
        return
    if not await validar_contenido(update, "foto"):
        return

    user_data[chat_id]["ats_foto"] = "OK"  # Marca que ATS/PETAR tiene foto

    nombre_grupo = update.effective_chat.title
    archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")
    if archivo_drive:
        df = descargar_excel(archivo_drive["id"])
        df.at[df.index[-1], "ATS/PETAR"] = "Sí"  # Marca Sí en la última fila
        subir_excel(archivo_drive["id"], df)

    keyboard = [
        [InlineKeyboardButton("🔄 Repetir Foto ATS/PETAR", callback_data="repetir_foto_ats")],
        [InlineKeyboardButton("➡️ Continuar a jornada", callback_data="continuar_post_ats")],
    ]
    await update.message.reply_text(
        "¿Es correcta la foto del ATS/PETAR?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )



# -------------------- HANDLE ATS/PETAR --------------------
async def handle_ats_petar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        if not query:  # Aseguramos que es callback
            logger.warning("[DEBUG] handle_ats_petar llamado sin callback_query.")
            return

        chat_id = query.message.chat.id
        await query.answer()
        logger.info(f"[DEBUG] handle_ats_petar: chat_id={chat_id}, data={query.data}")

        nombre_grupo = query.message.chat.title
        archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")
        loop = asyncio.get_running_loop()

        # ----------------- RESPUESTA ATS SI -----------------
        if query.data == "ats_si":
            user_data[chat_id]["paso"] = 2
            logger.info(f"[DEBUG] Paso cambiado a 2 (espera foto ATS/PETAR) para chat {chat_id}")
            await query.edit_message_text(
                "📸 *Por favor, envía la foto del ATS/PETAR para continuar.*",
                parse_mode="Markdown"
            )
            return

        # ----------------- RESPUESTA ATS NO -----------------
        if not archivo_drive:
            # Si no existe el archivo, lo creamos con la base
            data = generar_base_data(
                user_data.get(chat_id, {}).get("cuadrilla", ""),
                user_data.get(chat_id, {}).get("tipo", "")
            )
            await loop.run_in_executor(None, crear_o_actualizar_excel, update, data)
            archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")
            logger.info(f"[DEBUG] Archivo {nombre_grupo}.xlsx creado para ATS=No")

        if archivo_drive:
            df = await loop.run_in_executor(None, descargar_excel, archivo_drive["id"])
            if not df.empty:
                df.at[df.index[-1], "ATS/PETAR"] = "No"
                await loop.run_in_executor(None, subir_excel, archivo_drive["id"], df)
                logger.info(f"[DEBUG] ATS/PETAR='No' registrado en {nombre_grupo}.xlsx")
            else:
                logger.warning(f"[DEBUG] No se encontró ninguna fila para actualizar en {nombre_grupo}.xlsx")

        user_data[chat_id]["paso"] = "selfie_salida"
        logger.info(f"[DEBUG] Paso cambiado a 'selfie_salida' para chat {chat_id}")

        keyboard = [
            [InlineKeyboardButton("📸 Enviar foto de ATS/PETAR de todas formas", callback_data="reenviar_ats")]
        ]
        await query.edit_message_text(
            "⚠️ *Recuerda siempre enviar ATS/PETAR antes del inicio de cada jornada.* ⚠️\n\n"
            "✅ Previenes accidentes.\n"
            "✅ Proteges tu vida y la de tu equipo.\n\n"
            "¡La seguridad empieza contigo!\n"
            "💪 *Puedes iniciar tu jornada.* 💪",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"[ERROR] handle_ats_petar: {e}")
        if update.callback_query:
            await update.callback_query.message.reply_text("❌ Error interno en ATS/PETAR.")


# -------------------- BREAK OUT --------------------
async def breakout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mensaje_es_para_bot(update, context):
        return

    chat_id = update.effective_chat.id
    hora = datetime.now(LIMA_TZ).strftime("%H:%M")
    nombre_grupo = update.effective_chat.title
    loop = asyncio.get_running_loop()

    archivo_drive = await loop.run_in_executor(None, buscar_archivo_en_drive, f"{nombre_grupo}.xlsx")
    if not archivo_drive:
        await update.message.reply_text("❌ No hay registro de ingreso previo.")
        return

    df = await loop.run_in_executor(None, descargar_excel, archivo_drive["id"])
    df.at[df.index[-1], "HORA BREAK OUT"] = hora
    await loop.run_in_executor(None, subir_excel, archivo_drive["id"], df)

    await update.message.reply_text(f"🍽️😋 Salida a Break 😋🍽️, registrado a las {hora}.💪💪")


async def breakin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mensaje_es_para_bot(update, context):
        return

    chat_id = update.effective_chat.id
    hora = datetime.now(LIMA_TZ).strftime("%H:%M")
    nombre_grupo = update.effective_chat.title
    loop = asyncio.get_running_loop()

    archivo_drive = await loop.run_in_executor(None, buscar_archivo_en_drive, f"{nombre_grupo}.xlsx")
    if not archivo_drive:
        await update.message.reply_text("❌ No hay registro de ingreso previo.")
        return

    df = await loop.run_in_executor(None, descargar_excel, archivo_drive["id"])
    df.at[df.index[-1], "HORA BREAK IN"] = hora
    await loop.run_in_executor(None, subir_excel, archivo_drive["id"], df)

    await update.message.reply_text(
        f"🚶🚀 Regreso de Break 🚀🚶, registrado a las {hora}👀👀.\n\n"
        "*Escribe /start @VTetiquetado_bot* para continuar."
    )


# -------------------- SALIDA --------------------
async def salida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id  # <-- Definir aquí
    if not mensaje_es_para_bot(update, context):
        return

    chat_id = update.effective_chat.id
    user_data[chat_id] = {"paso": "selfie_salida"}

    await update.message.reply_text(
        "📸 Envía tu selfie de salida para finalizar la jornada."
    )


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
        chat_id = update.effective_chat.id
        if not mensaje_es_para_bot(update, context):
            return

        # Validar estado
        if user_data.get(chat_id, {}).get("paso") != "selfie_salida":
            logger.info(f"[DEBUG] selfie_salida ignorado, paso actual: {user_data.get(chat_id)}")
            return

        if not await validar_contenido(update, "foto"):
            return

        hora_salida = datetime.now(LIMA_TZ).strftime("%H:%M")
        nombre_grupo = update.effective_chat.title

        loop = asyncio.get_running_loop()
        archivo_drive = await loop.run_in_executor(None, buscar_archivo_en_drive, f"{nombre_grupo}.xlsx")

        if not archivo_drive:
            await update.message.reply_text("❌ No hay registro de ingreso previo.")
            logger.warning(f"[DEBUG] No se encontró archivo para {nombre_grupo}")
            return

        # Actualizar Excel
        df = await loop.run_in_executor(None, descargar_excel, archivo_drive["id"])
        if df.empty:
            await update.message.reply_text("⚠️ No hay datos previos para actualizar.")
            logger.warning(f"[DEBUG] Excel {nombre_grupo} está vacío.")
            return

        df.at[df.index[-1], "HORA SALIDA"] = hora_salida
        await loop.run_in_executor(None, subir_excel, archivo_drive["id"], df)
        logger.info(f"[DEBUG] HORA SALIDA='{hora_salida}' actualizada en {nombre_grupo}.xlsx")

        keyboard = [
            [InlineKeyboardButton("🔄 Repetir Selfie de Salida", callback_data="repetir_foto_salida")],
            [InlineKeyboardButton("✅ Finalizar Jornada", callback_data="finalizar_salida")],
        ]
        await update.message.reply_text(
            f"🚪 Hora de salida registrada a las *{hora_salida}*.\n\n¿Está correcta la selfie?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"[ERROR] selfie_salida: {e}")
        await update.message.reply_text("❌ Error interno al registrar la selfie de salida.")

# -------------------- MANEJAR FOTOS --------------------
async def manejar_fotos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id  # <-- Definir aquí
    if not mensaje_es_para_bot(update, context):
        return
    chat_id = update.effective_chat.id
    paso = user_data.get(chat_id, {}).get("paso")

    if paso == 1:
        await foto_ingreso(update, context)
    elif paso == 2:
        await foto_ats(update, context)
    elif paso == "selfie_salida":
        await selfie_salida(update, context)
    else:
        await update.message.reply_text("⚠️ No es momento de enviar fotos ⚠️ \n\n. *Usa /ingreso y etiquetame para comenzar.*")

# -------------------- MAIN --------------------
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.post_init = init_bot_info  # Inicializa el nombre del bot

    # --------- COMANDOS PRINCIPALES ---------
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ingreso", ingreso))
    app.add_handler(CommandHandler("breakout", breakout))
    app.add_handler(CommandHandler("breakin", breakin))
    app.add_handler(CommandHandler("salida", salida))

    # --------- MENSAJES ---------
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, nombre_cuadrilla))
    app.add_handler(MessageHandler(filters.PHOTO, manejar_fotos))  # Centralizamos todas las fotos

    # --------- CALLBACKS CUADRILLA ---------
    app.add_handler(CallbackQueryHandler(handle_nombre_cuadrilla, pattern="^(confirmar_nombre|corregir_nombre)$"))
    app.add_handler(CallbackQueryHandler(handle_tipo_trabajo, pattern="^tipo_"))

    # --------- CALLBACKS ATS/PETAR ---------
    app.add_handler(CallbackQueryHandler(handle_ats_petar, pattern="^ats_(si|no)$"))
    app.add_handler(CallbackQueryHandler(manejar_repeticion_fotos, pattern="^(continuar_ats|repetir_foto_inicio|repetir_foto_ats|continuar_post_ats)$"))

    # --------- CALLBACKS SALIDA ---------
    app.add_handler(CallbackQueryHandler(manejar_salida_callback, pattern="^(repetir_foto_salida|finalizar_salida)$"))

    print("🚀 Bot de Asistencia en ejecución...")
    await app.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())

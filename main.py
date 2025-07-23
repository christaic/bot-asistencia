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
BOT_TOKEN = "8105661196:AAE43P8yPbgJZau38HLUjbTCdTxckJFAnhs"  # Token del bot
NOMBRE_CARPETA_DRIVE = "ASISTENCIA_BOT"  # Carpeta principal
DRIVE_ID = "0AOy_EhsaSY_HUk9PVA"  # ID de la unidad compartida
ALLOWED_CHATS = [-1002640857147, -4718591093, -4831456255, -1002814603547, -1002838776671, -4951443286, -4870196969, -4824829490, -4979512409, -4903731585, -4910534813, -4845865029, -4643755320, -4860386920]  # Reemplaza con los IDs de tus grupos

def chat_permitido(chat_id: int) -> bool:
    """Verifica si el chat está permitido"""
    return chat_id in ALLOWED_CHATS

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
def crear_o_actualizar_excel(update: Update, data):
    nombre_grupo, nombre_archivo = obtener_nombre_grupo_y_archivo(update)
    archivo_drive = buscar_archivo_en_drive(nombre_archivo)

    if archivo_drive:
        # ✅ Ya existe el archivo → descargarlo y agregar el nuevo registro
        df_existente = descargar_excel(archivo_drive["id"])
        df = pd.concat([df_existente, pd.DataFrame([data])], ignore_index=True)
        subir_excel(archivo_drive["id"], df)
    else:
        # ✅ No existe → crear el archivo con el primer registro
        df = pd.DataFrame([data])
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False)
        buffer.seek(0)
        media = MediaIoBaseUpload(buffer, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        file_metadata = {
            "name": nombre_archivo,
            "parents": [MAIN_FOLDER_ID]
        }
        drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True
        ).execute()

# -------------------- ESTRUCTURA DE FILA --------------------
def generar_base_data(cuadrilla, tipo_trabajo):
    ahora = datetime.now(LIMA_TZ)
    return {
        "MES": ahora.strftime("%B"),
        "FECHA": ahora.strftime("%Y-%m-%d"),
        "CUADRILLA": cuadrilla,
        "TIPO DE TRABAJO": tipo_trabajo,
        "ATS/PETAR": "",
        "HORA INGRESO": "",
        "HORA BREAK OUT": "",
        "HORA BREAK IN": "",
        "HORA SALIDA": "",
        "HORAS BREAK": "",
        "HORAS LABORADAS": "",
        "AVANCE": "",
        "OBSERVACIÓN": "",
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

def mensaje_es_para_bot(update: Update):
    mensaje = update.message
    if not mensaje:
        return False
    # Mención directa
    if BOT_USERNAME and BOT_USERNAME in (mensaje.text or ""):
        return True
    # Respuesta al bot
    if mensaje.reply_to_message and mensaje.reply_to_message.from_user.username == BOT_USERNAME.strip("@"):
        return True
    return False

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
    # Filtra mensajes en grupos para que solo responda si lo mencionan
    if update.message.chat.type in ['group', 'supergroup']:
        if not (
            update.message.text.startswith(f"/start@{context.bot.username}")
            or (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id)
        ):
            return

    await update.message.reply_text(
        "¡Hola! 👷‍♀️👷‍♂️, Escribe /ingreso para comenzar tu registro de asistencia.✅✅"
    )

async def ingreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mensaje_es_para_bot(update):
        return
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"paso": 0}
    await update.message.reply_text(
        "✍️ *Escribe el nombre de tu cuadrilla*\n\n"
        "*Ejemplo:*\n"
        "*T1: Juan Pérez*\n"
        "*T2: José Flores*\n",
        parse_mode="Markdown"
    )

async def nombre_cuadrilla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_data or user_data[chat_id].get("paso") != 0:
        return
    if not await validar_contenido(update, "texto"):
        return
    user_data[chat_id]["cuadrilla"] = update.message.text
    keyboard = [
        [InlineKeyboardButton("✅ Confirma el nombre de tu cuadrilla", callback_data="confirmar_nombre")],
        [InlineKeyboardButton("✏️ Corregir nombre", callback_data="corregir_nombre")],
    ]
    await update.message.reply_text(
        f"Has ingresado la cuadrilla:\n*{user_data[chat_id]['cuadrilla']}*\n\n¿Es correcto?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_nombre_cuadrilla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    await query.answer()
    if query.data == "confirmar_nombre":
        keyboard = [
            [InlineKeyboardButton("📌 Ordenamiento", callback_data="tipo_ordenamiento")],
            [InlineKeyboardButton("🏷 Etiquetado", callback_data="tipo_etiquetado")],
        ]
        await query.edit_message_text(
            "Selecciona el tipo de trabajo:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif query.data == "corregir_nombre":
        user_data[chat_id]["cuadrilla"] = ""
        await query.edit_message_text(
            "✍️ *Escribe el nombre de tu cuadrilla*\n\n"
            "*Ejemplo:*\n"
            "*T1: Juan Pérez*\n"
            "*T2: José Flores*\n",
            parse_mode="Markdown"
        )

async def handle_tipo_trabajo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    await query.answer()
    tipo = "Ordenamiento" if query.data == "tipo_ordenamiento" else "Etiquetado"
    user_data[chat_id]["tipo"] = tipo
    user_data[chat_id]["paso"] = 1
    await query.edit_message_text(
        f"Tipo de trabajo seleccionado: *{tipo}*\n\n📸 Ahora envia tu selfie de inicio.",
        parse_mode="Markdown"
    )

async def foto_ingreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_data or user_data[chat_id].get("paso") != 1:
        return
    if not await validar_contenido(update, "foto"):
        return
        
    hora_ingreso = datetime.now(LIMA_TZ).strftime("%H:%M")
    user_data[chat_id]["hora_ingreso"] = hora_ingreso
    data = generar_base_data(user_data[chat_id]["cuadrilla"], user_data[chat_id]["tipo"])
    data["HORA INGRESO"] = hora_ingreso
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, crear_o_actualizar_excel, update, data)


    keyboard = [
        [InlineKeyboardButton("🔄 Repetir Selfie", callback_data="repetir_foto_inicio")],
        [InlineKeyboardButton("📝📋 Continuar con ATS/PETAR", callback_data="continuar_ats")],
    ]
    await update.message.reply_text("¿Es correcto el selfie de inicio?", reply_markup=InlineKeyboardMarkup(keyboard))

# -------------------- MANEJAR REPETICIÓN DE FOTOS --------------------
async def manejar_repeticion_fotos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    await query.answer()

    # --- SELFIE INICIO ---
    if query.data == "repetir_foto_inicio":
        user_data[chat_id]["paso"] = 1
        await query.edit_message_text(
            "📸 Envía nuevamente tu *selfie de inicio*.", parse_mode="Markdown"
        )

    elif query.data == "continuar_ats":
        keyboard = [
            [InlineKeyboardButton("✅ ATS/PETAR Sí", callback_data="ats_si")],
            [InlineKeyboardButton("❌ ATS/PETAR No", callback_data="ats_no")],
        ]
        await query.edit_message_text(
            "¿Realizaste ATS/PETAR?", reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # --- ATS/PETAR ---
    elif query.data == "repetir_foto_ats":
        keyboard = [
            [InlineKeyboardButton("✅ ATS/PETAR Sí", callback_data="ats_si")],
            [InlineKeyboardButton("❌ ATS/PETAR No", callback_data="ats_no")],
        ]
        await query.edit_message_text(
            "¿Realizaste ATS/PETAR?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


    elif query.data == "continuar_post_ats":
        user_data[chat_id]["paso"] = 3  # Marca como listo para salir
        await query.edit_message_text(
            "¡Excelente! 🎉 Ya estás listo para comenzar.\n\n"
            "*Escribe /start @VTetiquetado_bot* para iniciar tu jornada.",
            parse_mode="Markdown"
        )
    
    # --- SELFIE SALIDA ---
    elif query.data == "repetir_foto_salida":
        if "selfie_salida" in user_data.get(chat_id, {}):
            del user_data[chat_id]["selfie_salida"]
        user_data[chat_id]["paso"] = "selfie_salida"
        await query.edit_message_text(
            "📸 Por favor, envía nuevamente tu *selfie de salida*.",
            parse_mode="Markdown"
        )

# -------------------- ATS/PETAR --------------------
async def handle_ats_petar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    await query.answer()

    nombre_grupo = query.message.chat.title
    archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")

    # Si respondió SÍ
    if query.data == "ats_si":
        user_data[chat_id]["paso"] = 2
        await query.edit_message_text(
            "📸 *Por favor, envía la foto del ATS/PETAR para continuar.*",
            parse_mode="Markdown"
        )
        return

    # Si respondió NO: Guardar en el Excel
    if archivo_drive:
        df = descargar_excel(archivo_drive["id"])
        df.at[df.index[-1], "ATS/PETAR"] = "No"
        subir_excel(archivo_drive["id"], df)

    keyboard = [
        [InlineKeyboardButton("📸 Enviar foto de ATS/PETAR de todas formas", callback_data="reenviar_ats")]
    ]
    await query.edit_message_text(
        "⚠️ *Recuerda enviar ATS al iniciar cada jornada.* ⚠️\n\n"
        "✅ Previenes accidentes.\n"
        "✅ Proteges tu vida y la de tu equipo.\n\n"
        "¡La seguridad empieza contigo!\n"
        "**Escribe /start @VTetiquetado_bot** para iniciar tu jornada.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# -------------------- FOTO ATS/PETAR --------------------
async def foto_ats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_data or user_data[chat_id].get("paso") != 2:
        return

    if not await validar_contenido(update, "foto"):
        return

    nombre_grupo = update.effective_chat.title
    loop = asyncio.get_running_loop()

    archivo_drive = await loop.run_in_executor(None, buscar_archivo_en_drive, f"{nombre_grupo}.xlsx")

    if archivo_drive:
        df = await loop.run_in_executor(None, descargar_excel, archivo_drive["id"])
        df.at[df.index[-1], "ATS/PETAR"] = "Sí"
        await loop.run_in_executor(None, subir_excel, archivo_drive["id"], df)

    keyboard = [
        [InlineKeyboardButton("🔄 Repetir Foto ATS/PETAR", callback_data="repetir_foto_ats")],
        [InlineKeyboardButton("✅ Continuar", callback_data="continuar_post_ats")],
    ]
    await update.message.reply_text("¿Es correcta la foto ATS/PETAR?", reply_markup=InlineKeyboardMarkup(keyboard))


async def breakout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mensaje_es_para_bot(update):
        return

    hora = datetime.now(LIMA_TZ).strftime("%H:%M")
    nombre_grupo = update.effective_chat.title
    loop = asyncio.get_running_loop()

    archivo_drive = await loop.run_in_executor(None, buscar_archivo_en_drive, f"{nombre_grupo}.xlsx")
    if not archivo_drive:
        await update.message.reply_text("No hay registro de ingreso previo.")
        return

    df = await loop.run_in_executor(None, descargar_excel, archivo_drive["id"])
    df.at[df.index[-1], "HORA BREAK OUT"] = hora
    await loop.run_in_executor(None, subir_excel, archivo_drive["id"], df)

    await update.message.reply_text(f"🍽️😋 Salida a Break 😋🍽️, registrado a las {hora}.💪💪")


async def breakin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mensaje_es_para_bot(update):
        return

    hora = datetime.now(LIMA_TZ).strftime("%H:%M")
    nombre_grupo = update.effective_chat.title
    loop = asyncio.get_running_loop()

    archivo_drive = await loop.run_in_executor(None, buscar_archivo_en_drive, f"{nombre_grupo}.xlsx")
    if not archivo_drive:
        await update.message.reply_text("No hay registro de ingreso previo.")
        return

    df = await loop.run_in_executor(None, descargar_excel, archivo_drive["id"])
    df.at[df.index[-1], "HORA BREAK IN"] = hora
    await loop.run_in_executor(None, subir_excel, archivo_drive["id"], df)

    await update.message.reply_text(
        f"🚶🚀 Regreso de Break 🚀🚶, registrado a las {hora}👀👀.\n\n"
        "**Escribe /start @VTetiquetado_bot** para continuar."
    )

# -------------------- SALIDA --------------------
async def salida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not mensaje_es_para_bot(update):
        return

    # Prepara el estado para recibir selfie de salida
    user_data[chat_id] = user_data.get(chat_id, {})
    user_data[chat_id]["paso"] = "selfie_salida"

    await update.message.reply_text(
        "📸 Por favor, envía tu *selfie de salida*.\n"
        "Registraremos automáticamente tu hora de salida cuando la recibamos.",
        parse_mode="Markdown"
    )


# -------------------- CALLBACK SALIDA --------------------
async def manejar_salida_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    await query.answer()

    if query.data == "repetir_foto_salida":
        if "selfie_salida" in user_data.get(chat_id, {}):
            del user_data[chat_id]["selfie_salida"]
        user_data[chat_id]["paso"] = "selfie_salida"
        await query.edit_message_text(
            "🔄 Por favor, envía nuevamente tu *selfie de salida*.",
            parse_mode="Markdown"
        )

    elif query.data == "finalizar_salida":
        if chat_id in user_data:
            user_data[chat_id]["paso"] = None
        await query.edit_message_text(
            "💪 *¡Buen trabajo! Hasta mañana.*\n\n"
            "👏 *Gracias por tu apoyo en la jornada de hoy.*\n\n"
            "🫡 ¡Cambio y fuera! 🫡",
            parse_mode="Markdown"
        )

# -------------------- SELFIE SALIDA --------------------
async def selfie_salida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    hora_salida = datetime.now(LIMA_TZ).strftime("%H:%M")

    nombre_grupo = update.effective_chat.title
    archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")
    if not archivo_drive:
        await update.message.reply_text("❌ No hay registro de ingreso previo.")
        return

    # Descargar la foto
    ruta = f"reportes/{chat_id}_selfie_salida.jpg"
    archivo = await update.message.photo[-1].get_file()
    await archivo.download_to_drive(ruta)
    user_data[chat_id]["selfie_salida"] = ruta

    # Actualizar el Excel con la hora de salida
    df = descargar_excel(archivo_drive["id"])
    df.at[df.index[-1], "HORA SALIDA"] = hora_salida
    subir_excel(archivo_drive["id"], df)

    # Teclado de confirmación
    keyboard = [
        [InlineKeyboardButton("🔄 Repetir Selfie de Salida", callback_data="repetir_foto_salida")],
        [InlineKeyboardButton("✅ Finalizar Jornada", callback_data="finalizar_salida")],
    ]
    await update.message.reply_text(
        f"🚪 Hora de salida registrada a las *{hora_salida}*.\n\n"
        "¿Está correcta la selfie?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# -------------------- MANEJAR FOTOS --------------------
async def manejar_fotos(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.post_init = init_bot_info
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ingreso", ingreso))
    app.add_handler(CommandHandler("breakout", breakout))
    app.add_handler(CommandHandler("breakin", breakin))
    app.add_handler(CommandHandler("salida", salida))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, nombre_cuadrilla))
    app.add_handler(MessageHandler(filters.PHOTO, manejar_fotos))
    app.add_handler(CallbackQueryHandler(handle_nombre_cuadrilla, pattern="^(confirmar_nombre|corregir_nombre)$"))
    app.add_handler(CallbackQueryHandler(handle_tipo_trabajo, pattern="^tipo_"))
    app.add_handler(CallbackQueryHandler(manejar_repeticion_fotos, pattern="^(repetir_foto_|continuar_ats|continuar_post_ats|reenviar_ats)$"))
    app.add_handler(CallbackQueryHandler(handle_ats_petar, pattern="^ats_"))
    app.add_handler(CallbackQueryHandler(manejar_salida_callback, pattern="^(repetir_foto_salida|finalizar_salida)$"))
    print("Bot en ejecución...")
    app.run_polling()

if __name__ == "__main__":
    main()

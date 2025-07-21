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

# -------------------- LOGGING --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------- VARIABLES DE ENTORNO --------------------
TOKEN = os.environ["TOKEN"]
FOLDER_ID = os.environ["FOLDER_ID"]
CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

# -------------------- GOOGLE DRIVE SERVICE --------------------
def get_drive_service():
    creds_dict = json.loads(CREDENTIALS_JSON)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

drive_service = get_drive_service()

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

# -------------------- FUNCIONES DE GOOGLE DRIVE --------------------
def buscar_archivo_en_drive(nombre_archivo):
    query = f"name='{nombre_archivo}' and '{FOLDER_ID}' in parents and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    return files[0] if files else None

def descargar_excel(file_id):
    request = drive_service.files().get_media(fileId=file_id)
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
    drive_service.files().update(fileId=file_id, media_body=media).execute()

def crear_o_actualizar_excel(nombre_grupo, data):
    nombre_archivo = f"{nombre_grupo}.xlsx"
    archivo_drive = buscar_archivo_en_drive(nombre_archivo)
    if archivo_drive:
        df_existente = descargar_excel(archivo_drive["id"])
        df = pd.concat([df_existente, pd.DataFrame([data])], ignore_index=True)
        subir_excel(archivo_drive["id"], df)
    else:
        df = pd.DataFrame([data])
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False)
        buffer.seek(0)
        file_metadata = {"name": nombre_archivo, "parents": [FOLDER_ID]}
        media = MediaIoBaseUpload(buffer, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()

# -------------------- ESTRUCTURA DE FILA --------------------
def generar_base_data(cuadrilla, tipo_trabajo):
    ahora = datetime.now()
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

# -------------------- COMANDOS DEL BOT --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mensaje_es_para_bot(update):
        return
    await update.message.reply_text("¡Hola! Usa /ingreso para comenzar tu registro de asistencia.")

async def ingreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mensaje_es_para_bot(update):
        return
    chat_id = update.effective_chat.id
    user_data[chat_id] = {}
    await update.message.reply_text(
        "✍️ *Escribe el nombre de tu cuadrilla*\n\n"
        "*Ejemplo:*\n"
        "*T1: Juan Pérez*\n"
        "*T2: José Flores*\n",
        parse_mode="Markdown",
    )

async def nombre_cuadrilla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mensaje_es_para_bot(update):
        return
    chat_id = update.effective_chat.id
    if chat_id not in user_data or "cuadrilla" in user_data[chat_id]:
        return
    user_data[chat_id]["cuadrilla"] = update.message.text
    keyboard = [
        [InlineKeyboardButton("📌 Ordenamiento", callback_data="tipo_ordenamiento")],
        [InlineKeyboardButton("🏷 Etiquetado", callback_data="tipo_etiquetado")],
    ]
    await update.message.reply_text(
        "Selecciona el tipo de trabajo:", reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_tipo_trabajo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    await query.answer()
    tipo = "Ordenamiento" if query.data == "tipo_ordenamiento" else "Etiquetado"
    user_data[chat_id]["tipo"] = tipo
    await query.edit_message_text(
        f"Tipo de trabajo seleccionado: *{tipo}*\n\n📸 Envía ahora la foto de ingreso.",
        parse_mode="Markdown",
    )

async def foto_ingreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mensaje_es_para_bot(update):
        return
    chat_id = update.effective_chat.id
    if chat_id not in user_data or "tipo" not in user_data[chat_id]:
        return
    hora_ingreso = datetime.now().strftime("%H:%M")
    user_data[chat_id]["hora_ingreso"] = hora_ingreso
    data = generar_base_data(user_data[chat_id]["cuadrilla"], user_data[chat_id]["tipo"])
    data["HORA INGRESO"] = hora_ingreso
    crear_o_actualizar_excel(update.effective_chat.title, data)
    keyboard = [
        [InlineKeyboardButton("✅ ATS/PETAR Sí", callback_data="ats_si")],
        [InlineKeyboardButton("❌ ATS/PETAR No", callback_data="ats_no")],
    ]
    await update.message.reply_text(
        "¿Realizaste ATS/PETAR?", reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_ats_petar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    await query.answer()
    respuesta = "Sí" if query.data == "ats_si" else "No"
    user_data[chat_id]["ats_petar"] = respuesta
    nombre_grupo = query.message.chat.title
    archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")
    if archivo_drive:
        df = descargar_excel(archivo_drive["id"])
        df.at[df.index[-1], "ATS/PETAR"] = respuesta
        subir_excel(archivo_drive["id"], df)
    if respuesta == "Sí":
        await query.edit_message_text(
            "¡Excelente! 🎉 Ya estás listo para empezar. **Etiqueta a @VTetiquetado_bot** para comenzar actividades.",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(
            "🔔 *Recuerda enviar ATS al iniciar cada jornada.* 🔔\n\n"
            "✅ Previenes accidentes\n"
            "✅ Proteges tu vida y la de tu equipo\n\n"
            "⚠️¡La seguridad empieza contigo!⚠️\n\n"
            "**Etiqueta a @VTetiquetado_bot** para comenzar actividades.",
            parse_mode="Markdown",
        )

async def breakout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mensaje_es_para_bot(update):
        return
    hora = datetime.now().strftime("%H:%M")
    nombre_grupo = update.effective_chat.title
    archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")
    if not archivo_drive:
        await update.message.reply_text("No hay registro de ingreso previo.")
        return
    df = descargar_excel(archivo_drive["id"])
    df.at[df.index[-1], "HORA BREAK OUT"] = hora
    subir_excel(archivo_drive["id"], df)
    await update.message.reply_text(f"☕ Salida a break registrado a las {hora}.")

async def breakin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mensaje_es_para_bot(update):
        return
    hora = datetime.now().strftime("%H:%M")
    nombre_grupo = update.effective_chat.title
    archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")
    if not archivo_drive:
        await update.message.reply_text("No hay registro de ingreso previo.")
        return
    df = descargar_excel(archivo_drive["id"])
    df.at[df.index[-1], "HORA BREAK IN"] = hora
    subir_excel(archivo_drive["id"], df)
    await update.message.reply_text(f"🚀 Regreso de break registrado a las {hora}.")

async def salida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mensaje_es_para_bot(update):
        return
    if not update.message.photo:
        await update.message.reply_text("📸 Por favor, envía tu foto de salida.")
        return
    hora_salida = datetime.now().strftime("%H:%M")
    nombre_grupo = update.effective_chat.title
    archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")
    if not archivo_drive:
        await update.message.reply_text("No hay registro de ingreso previo.")
        return
    df = descargar_excel(archivo_drive["id"])
    df.at[df.index[-1], "HORA SALIDA"] = hora_salida
    try:
        h_ingreso = datetime.strptime(df.at[df.index[-1], "HORA INGRESO"], "%H:%M")
        h_salida = datetime.strptime(hora_salida, "%H:%M")
        if pd.notnull(df.at[df.index[-1], "HORA BREAK OUT"]) and pd.notnull(df.at[df.index[-1], "HORA BREAK IN"]):
            h_breakout = datetime.strptime(df.at[df.index[-1], "HORA BREAK OUT"], "%H:%M")
            h_breakin = datetime.strptime(df.at[df.index[-1], "HORA BREAK IN"], "%H:%M")
            h_break = (h_breakin - h_breakout).seconds / 3600
        else:
            h_break = 0
        horas_lab = ((h_salida - h_ingreso).seconds / 3600) - h_break
        df.at[df.index[-1], "HORAS BREAK"] = f"{h_break:.2f}"
        df.at[df.index[-1], "HORAS LABORADAS"] = f"{horas_lab:.2f}"
    except Exception as e:
        logger.error(f"Error calculando horas: {e}")
    subir_excel(archivo_drive["id"], df)
    await update.message.reply_text(f"👏💪 Salida registrada a las {hora_salida}. *¡Buen trabajo!*. Gracias, hasta mañana👏💪")

# -------------------- MAIN --------------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.post_init = init_bot_info
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ingreso", ingreso))
    app.add_handler(CommandHandler("breakout", breakout))
    app.add_handler(CommandHandler("breakin", breakin))
    app.add_handler(CommandHandler("salida", salida))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, nombre_cuadrilla))
    app.add_handler(MessageHandler(filters.PHOTO, foto_ingreso))
    app.add_handler(CallbackQueryHandler(handle_tipo_trabajo, pattern="^tipo_"))
    app.add_handler(CallbackQueryHandler(handle_ats_petar, pattern="^ats_"))
    print("Bot en ejecución...")
    app.run_polling()

if __name__ == "__main__":
    main()

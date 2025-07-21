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
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

# -------------------- LOGGING --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------- VARIABLES DE ENTORNO --------------------
TOKEN = os.environ["TOKEN"]  # Token del bot
FOLDER_ID = os.environ["FOLDER_ID"]  # ID de la carpeta en Drive
CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

# -------------------- CONFIGURAR CREDENCIALES --------------------
with open("credentials.json", "w") as f:
    json.dump(json.loads(CREDENTIALS_JSON), f)

gauth = GoogleAuth()
gauth.LoadCredentialsFile("credentials.json")
if gauth.credentials is None:
    gauth.LocalWebserverAuth()
elif gauth.access_token_expired:
    gauth.Refresh()
else:
    gauth.Authorize()
gauth.SaveCredentialsFile("credentials.json")
drive = GoogleDrive(gauth)

# -------------------- ESTADOS TEMPORALES --------------------
user_data = {}  # Estructura {chat_id: {"cuadrilla": "", "tipo": "", ...}}

# -------------------- FUNCIONES GOOGLE DRIVE --------------------
def buscar_archivo_en_drive(nombre_archivo):
    query = f"title='{nombre_archivo}' and '{FOLDER_ID}' in parents and trashed=false"
    lista_archivos = drive.ListFile({'q': query}).GetList()
    return lista_archivos[0] if lista_archivos else None


def crear_o_actualizar_excel(nombre_grupo, data):
    nombre_archivo = f"{nombre_grupo}.xlsx"
    archivo_drive = buscar_archivo_en_drive(nombre_archivo)

    if archivo_drive:
        contenido = io.BytesIO(archivo_drive.GetContentBinary())
        df_existente = pd.read_excel(contenido)
        df = pd.concat([df_existente, pd.DataFrame([data])], ignore_index=True)
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False)
        buffer.seek(0)
        archivo_drive.SetContentBinary(buffer.getvalue())
        archivo_drive.Upload()
    else:
        df = pd.DataFrame([data])
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False)
        buffer.seek(0)
        nuevo_archivo = drive.CreateFile({
            'title': nombre_archivo,
            'parents': [{'id': FOLDER_ID}]
        })
        nuevo_archivo.SetContentBinary(buffer.getvalue())
        nuevo_archivo.Upload()


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


# -------------------- COMANDOS DEL BOT --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Hola! Usa /ingreso para comenzar tu registro de asistencia.")


async def ingreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data[chat_id] = {}
    await update.message.reply_text(
        "✍️ *Escribe el nombre de tu cuadrilla (Ejemplo: Juan Pérez, José Flores).*",
        parse_mode="Markdown",
    )


async def nombre_cuadrilla(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        contenido = io.BytesIO(archivo_drive.GetContentBinary())
        df = pd.read_excel(contenido)
        df.at[df.index[-1], "ATS/PETAR"] = respuesta
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False)
        buffer.seek(0)
        archivo_drive.SetContentBinary(buffer.getvalue())
        archivo_drive.Upload()

    if respuesta == "Sí":
        await query.edit_message_text(
            "¡Excelente, crack! 🎉 Ya estás listo para comenzar. **Etiqueta a @VTetiquetado_bot** para iniciar tu jornada.",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(
            "⚠️ *Recuerda enviar ATS al iniciar cada jornada.*\n\n"
            "✅ Previenes accidentes\n"
            "✅ Cumples normativa\n"
            "✅ Proteges tu vida y la de tu equipo\n\n"
            "¡La seguridad empieza contigo!",
            parse_mode="Markdown",
        )


async def breakout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hora = datetime.now().strftime("%H:%M")
    nombre_grupo = update.effective_chat.title
    archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")
    if not archivo_drive:
        await update.message.reply_text("No hay registro de ingreso previo.")
        return

    contenido = io.BytesIO(archivo_drive.GetContentBinary())
    df = pd.read_excel(contenido)
    df.at[df.index[-1], "HORA BREAK OUT"] = hora
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    archivo_drive.SetContentBinary(buffer.getvalue())
    archivo_drive.Upload()
    await update.message.reply_text(f"☕ Break OUT registrado a las {hora}.")


async def breakin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hora = datetime.now().strftime("%H:%M")
    nombre_grupo = update.effective_chat.title
    archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")
    if not archivo_drive:
        await update.message.reply_text("No hay registro de ingreso previo.")
        return

    contenido = io.BytesIO(archivo_drive.GetContentBinary())
    df = pd.read_excel(contenido)
    df.at[df.index[-1], "HORA BREAK IN"] = hora
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    archivo_drive.SetContentBinary(buffer.getvalue())
    archivo_drive.Upload()
    await update.message.reply_text(f"🚀 Break IN registrado a las {hora}.")


async def salida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("📸 Por favor, envía tu foto de salida.")
        return

    hora_salida = datetime.now().strftime("%H:%M")
    nombre_grupo = update.effective_chat.title
    archivo_drive = buscar_archivo_en_drive(f"{nombre_grupo}.xlsx")
    if not archivo_drive:
        await update.message.reply_text("No hay registro de ingreso previo.")
        return

    contenido = io.BytesIO(archivo_drive.GetContentBinary())
    df = pd.read_excel(contenido)
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

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    archivo_drive.SetContentBinary(buffer.getvalue())
    archivo_drive.Upload()
    await update.message.reply_text(f"Salida registrada a las {hora_salida}. ¡Buen trabajo!")

# -------------------- MAIN --------------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()

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

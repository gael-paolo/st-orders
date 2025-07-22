import streamlit as st
import pandas as pd
import io
import os
import unicodedata
from datetime import datetime
from google.cloud import storage
from google.oauth2 import service_account
import tempfile
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# --- CONFIGURACIÓN ---
BUCKET_NAME = "bk_orders"
OBLIGATORIAS = ["np", "cantidad", "cliente", "acr", "canal", "referencia", "respaldo", "via", "usuario"]

st.set_page_config(page_title="Carga de Pedidos", layout="wide")
st.title("📦 Portal de Pedidos - Partes")

# 🔐 Clave de acceso
access_key = st.text_input("Clave de acceso", type="password")
if access_key != st.secrets["APP_KEY"]:
    st.warning("Ingresa la clave correcta para continuar.")
    st.stop()

# --- AUTENTICACIÓN GCP vía st.secrets ---
credentials_dict = st.secrets["gcp_service_account"]
credentials = service_account.Credentials.from_service_account_info(credentials_dict)

# --- FUNCIONES AUXILIARES ---
def normalizar(texto):
    if pd.isnull(texto):
        return ""
    texto = str(texto).strip().upper()
    texto = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode("utf-8")
    return texto

def normalizar_usuario(texto):
    if pd.isnull(texto):
        return ""
    texto = str(texto).strip().lower()
    texto = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode("utf-8")
    return texto

def upload_to_gcs(file_path, filename, folder):
    client = storage.Client(credentials=credentials)
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(f"{folder}{filename}")
    blob.upload_from_filename(file_path)

# --- INTERFAZ ---
st.markdown("Ingresa tus ítems en la tabla o sube un archivo Excel/CSV con los campos requeridos.")

columnas = [
    "np", "cantidad", "descripcion", "cliente", "acr", "aps", "modelo",
    "canal", "referencia", "respaldo", "via", "usuario"]

st.subheader("🧮 Ingreso manual en tabla")
num_filas = st.number_input("Número de ítems", min_value=1, max_value=50, value=5)
df_vacio = pd.DataFrame(columns=columnas, index=range(num_filas))
df_tabla = st.data_editor(df_vacio, num_rows="dynamic", use_container_width=True)

st.subheader("📁 O subir archivo Excel o CSV")
archivo = st.file_uploader("Selecciona archivo", type=["xlsx", "csv"])

if archivo:
    if archivo.name.endswith(".xlsx"):
        df_final = pd.read_excel(archivo)
    else:
        df_final = pd.read_csv(archivo, sep=None, engine="python")
    fuente = "archivo"
else:
    df_final = df_tabla
    fuente = "tabla"

# --- BOTÓN DE ENVÍO ---
if st.button("📤 Generar y Enviar Pedido"):

    if not all(col in df_final.columns for col in OBLIGATORIAS):
        st.error(f"❌ Faltan columnas obligatorias. Se requieren: {', '.join(OBLIGATORIAS)}")
        st.stop()

    df_final = df_final.dropna(subset=OBLIGATORIAS)
    if df_final.empty:
        st.error("❌ No se puede procesar: no hay filas completas con todos los campos obligatorios.")
        st.stop()

    # LIMPIEZA
    df_final["np"] = df_final["np"].astype(str).str.replace("-", "").str.strip()

    for col in df_final.columns:
        if col == "usuario":
            df_final[col] = df_final[col].apply(normalizar_usuario)
        else:
            df_final[col] = df_final[col].apply(normalizar)

    # Normalización de vía
    df_final["via"] = df_final["via"].replace({
        "AEREO": "air", "AÉREA": "air", "AÉREO": "air", "AEREA": "air",
        "MARITIMO": "sea", "MARÍTIMO": "sea", "MARÍTIMA": "sea"
    })

    # Agrupar por tipo de envío
    agrupado = df_final.groupby("via")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fecha_registro = datetime.now().strftime("%d/%m/%y")
    errores = []
    archivos_generados = []
    dataframes_para_descarga = []

    for via, df_grupo in agrupado:
        if via == "air":
            folder = "air/pending/"
        elif via == "sea":
            folder = "sea/pending/"
        else:
            errores.append(via)
            continue

        filename = f"pedido_{via}_{timestamp}.csv"
        temp_path = os.path.join(tempfile.gettempdir(), filename)

        df_grupo["np"] = df_grupo["np"].str.upper()
        df_grupo["fecha"] = fecha_registro

        # Reordenar columnas
        columnas_final = [
            "np", "cantidad", "descripcion", "cliente", "usuario", "fecha", "referencia",
            "canal", "respaldo", "acr", "aps", "modelo", "via"]
        
        df_grupo = df_grupo[columnas_final]

        df_grupo.to_csv(
            temp_path,
            index=False,
            sep=";",
            encoding="utf-8-sig",
            quoting=1)

        try:
            upload_to_gcs(temp_path, filename, folder)
            archivos_generados.append((filename, temp_path))
            dataframes_para_descarga.append(df_grupo)
        except Exception as e:
            errores.append(f"{via}: {e}")

    # ARCHIVO CONSOLIDADO PARA DESCARGA
    if dataframes_para_descarga:
        df_total = pd.concat(dataframes_para_descarga, ignore_index=True)
        try:
            # CREDENCIALES
            gmail_user = st.secrets["email"]["gmail_user"]
            gmail_password = st.secrets["email"]["gmail_password"]

            # DESTINATARIO(S)
            destinatarios = ["destinatario@ejemplo.com"]  # puedes cambiar a una lista si son varios

            # MENSAJE
            mensaje_html = f"""
            <p>Hola,</p>
            <p>Adjunto encontrarás el detalle del pedido generado automáticamente:</p>
            {df_total.to_html(index=False)}
            <p>Saludos,<br>Equipo de Pedidos</p>
            """

            # CONFIGURACIÓN DEL MENSAJE
            msg = MIMEMultipart()
            msg['From'] = gmail_user
            msg['To'] = ", ".join(destinatarios)
            msg['Subject'] = f"Pedido generado - {timestamp}"
            msg.attach(MIMEText(mensaje_html, 'html'))

            # ENVÍO SMTP
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, destinatarios, msg.as_string())
            server.quit()

            st.success("📧 Correo enviado correctamente.")

        except Exception as e:
            st.error(f"❌ Error al enviar el correo: {e}")

    # RESULTADO
    if errores:
        st.warning(f"⚠️ Algunos envíos fallaron o tenían vía no reconocida: {errores}")
    else:
        st.success("✅ Todos los pedidos fueron enviados correctamente.")
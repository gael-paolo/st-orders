import streamlit as st
import pandas as pd
import io
import os
import unicodedata
from datetime import datetime
from google.cloud import storage
from google.oauth2 import service_account
import tempfile

# --- CONFIGURACIÓN ---
BUCKET_NAME = "bk_orders"
OBLIGATORIAS = ["np", "cantidad", "cliente", "acr", "canal", "referencia", "respaldo", "via", "usuario"]

st.set_page_config(page_title="Carga de Pedidos", layout="wide")
st.title("📦 Portal de Pedidos - Taiyo")

# --- AUTENTICACIÓN GCP vía st.secrets ---
credentials_dict = st.secrets["gcp_service_account"]
credentials = service_account.Credentials.from_service_account_info(credentials_dict)

# --- FUNCIÓN AUXILIAR: quitar tildes y pasar a mayúsculas ---
def normalizar(texto):
    if pd.isnull(texto):
        return ""
    texto = str(texto).strip().upper()
    texto = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode("utf-8")
    return texto

# --- FUNCIÓN: subir archivo a bucket según la vía ---
def upload_to_gcs(file_path, filename, folder):
    client = storage.Client(credentials=credentials)
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(f"{folder}{filename}")
    blob.upload_from_filename(file_path)

# --- INGRESO TABLA o ARCHIVO ---
st.markdown("Ingresa tus ítems en la tabla o sube un archivo Excel/CSV con los campos requeridos.")

columnas = [
    "np", "cantidad", "descripcion", "cliente", "acr", "aps", "modelo",
    "canal", "referencia", "respaldo", "via", "usuario"
]

# A: tabla editable
st.subheader("🧮 Ingreso manual en tabla")
num_filas = st.number_input("Número de ítems", min_value=1, max_value=50, value=5)
df_vacio = pd.DataFrame(columns=columnas, index=range(num_filas))
df_tabla = st.data_editor(df_vacio, num_rows="dynamic", use_container_width=True)

# B: archivo cargado
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

    # 1. Validar columnas requeridas
    if not all(col in df_final.columns for col in OBLIGATORIAS):
        st.error(f"❌ Faltan columnas obligatorias. Se requieren: {', '.join(OBLIGATORIAS)}")
        st.stop()

    # 2. Eliminar filas vacías
    df_final = df_final.dropna(subset=OBLIGATORIAS)
    if df_final.empty:
        st.error("❌ No se puede procesar: no hay filas completas con todos los campos obligatorios.")
        st.stop()

    # 3. LIMPIEZA Y NORMALIZACIÓN
    df_final["np"] = df_final["np"].astype(str).str.replace("-", "").str.strip()

    for col in df_final.columns:
        df_final[col] = df_final[col].apply(normalizar)

    # Vía normalizada
    df_final["via"] = df_final["via"].replace({
        "AEREO": "air", "AÉREA": "air", "AÉREO": "air",
        "MARITIMO": "sea", "MARÍTIMO": "sea", "MARÍTIMA": "sea"
    })

    # 4. Agrupar por tipo de envío
    agrupado = df_final.groupby("via")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    errores = []
    archivos_generados = []

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

        # Mayúsculas finales para np
        df_grupo["np"] = df_grupo["np"].str.upper()
        df_grupo["fecha"] = timestamp

        # Guardar archivo temporal
        df_grupo.to_csv(
            temp_path,
            index=False,
            sep=";",
            encoding="utf-8-sig",
            quoting=1
        )

        try:
            upload_to_gcs(temp_path, filename, folder)
            archivos_generados.append((filename, temp_path))
        except Exception as e:
            errores.append(f"{via}: {e}")

    # 5. Resultado
    if errores:
        st.warning(f"⚠️ Algunos envíos fallaron o tenían vía no reconocida: {errores}")
    else:
        st.success("✅ Todos los pedidos fueron enviados correctamente.")
        for nombre_archivo, ruta_archivo in archivos_generados:
            with open(ruta_archivo, "rb") as f:
                st.download_button(
                    label=f"📥 Descargar {nombre_archivo}",
                    data=f.read(),
                    file_name=nombre_archivo,
                    mime="text/csv"
                )

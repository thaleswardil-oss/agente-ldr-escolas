import streamlit as st

st.set_page_config(page_title="Agente LDR - Escolas", layout="wide")

st.title("🚀 Agente de Enriquecimento de Leads - LDR")
st.write("Bem-vindo à ferramenta interna de prospecção de escolas.")

# Verificando se as chaves do Supabase e Gemini estão configuradas
try:
    supabase_url = st.secrets["SUPABASE_URL"]
    st.success("Conexão com o Supabase configurada com sucesso!")
except Exception:
    st.warning("⚠️ Atenção: As chaves do Supabase ainda não foram configuradas nas Secrets do Streamlit.")

uploaded_file = st.file_uploader("Envie a planilha do INEP (.xlsx)", type=["xlsx"])

if uploaded_file is not None:
    st.info("Planilha recebida! O motor de processamento será ativado aqui.")

import streamlit as st
import pandas as pd
import google.generativeai as genai
from supabase import create_client
import time
import json

# --- CONFIGURAÇÃO VISUAL ---
st.set_page_config(page_title="Agente LDR Pipeline", layout="wide")
st.markdown("""
    <style>
    h1, h2, h3, p, label { color: #004225 !important; }
    .stButton>button { background-color: #004225 !important; color: white !important; font-weight: bold; }
    .stMetric { background-color: #f0f9eb; padding: 10px; border-radius: 10px; border: 1px solid #64CD32; }
    </style>
    """, unsafe_allow_html=True)

# --- CONEXÕES ---
try:
    supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    
    # Modelos: Flash para busca rápida, Pro para extração densa
    model_flash = genai.GenerativeModel('gemini-1.5-flash', tools=[{"google_search_retrieval": {}}])
    model_pro = genai.GenerativeModel('gemini-1.5-pro')
except Exception as e:
    st.error(f"Erro de configuração: {e}")

# --- PIPELINE DE ENRIQUECIMENTO (3 ETAPAS) ---

def pipeline_enriquecimento(escola_nome, cidade, uf, endereco):
    # ETAPA 1: DESCOBERTA DE FONTES (Grounding)
    prompt_busca = f"Localize o site oficial, Instagram e página de transparência da escola {escola_nome} em {cidade}-{uf}. Retorne os links encontrados."
    
    try:
        search_res = model_flash.generate_content(prompt_busca)
        fontes = search_res.text
        links = []
        if hasattr(search_res.candidates[0], 'grounding_metadata'):
            metadata = search_res.candidates[0].grounding_metadata
            if hasattr(metadata, 'search_entry_point'):
                links.append(metadata.search_entry_point.rendered_content)
    except:
        fontes = "Busca falhou"
        links = []

    # ETAPA 2: EXTRAÇÃO DE IDENTIDADE (CNPJ, RAZÃO, DIRETOR)
    # Aqui usamos JSON Estruturado para garantir que o código não quebre
    prompt_identidade = f"""
    Com base nestas informações de busca: {fontes}
    Extraia os dados da escola: {escola_nome}, {cidade}-{uf}.
    Retorne APENAS um JSON com: cnpj, razao_social, diretor, email, site, telefone_alternativo.
    Se não encontrar, use "Não identificado".
    """
    
    try:
        # Forçamos o Gemini a responder em JSON puro
        res_id = model_pro.generate_content(
            prompt_identidade, 
            generation_config={"response_mime_type": "application/json"}
        )
        dados_id = json.loads(res_id.text)
    except:
        dados_id = {}

    # ETAPA 3: DETECÇÃO DE TECNOLOGIA (SGE E AGENDA)
    prompt_tech = f"""
    Analise os sinais digitais da escola {escola_nome} ({fontes}).
    Identifique especificamente:
    1. SGE (Software de Gestão): Procure por nomes como Proesc, Totvs, Sophia, WPensar, Escola Web, SAGEx, RM, Lyceum.
    2. Agenda Digital: Procure por ClassApp, Agenda Edu, ClipEscola, Google Agenda.
    3. Crie uma frase comercial para abordagem.
    Retorne APENAS um JSON com: sge_atual, agenda_digital, observacoes.
    """
    
    try:
        res_tech = model_pro.generate_content(
            prompt_tech,
            generation_config={"response_mime_type": "application/json"}
        )
        dados_tech = json.loads(res_tech.text)
    except:
        dados_tech = {}

    # CONSOLIDAÇÃO FINAL
    resultado = {
        "cnpj": dados_id.get("cnpj", "Não identificado"),
        "razao_social": dados_id.get("razao_social", "Não identificado"),
        "diretor": dados_id.get("diretor", "Não identificado"),
        "email": dados_id.get("email", "Não identificado"),
        "site": dados_id.get("site", "Não identificado"),
        "telefone_alternativo": dados_id.get("telefone_alternativo", "Não identificado"),
        "sge_atual": dados_tech.get("sge_atual", "Não identificado"),
        "agenda_digital": dados_tech.get("agenda_digital", "Não identificado"),
        "observacoes": dados_tech.get("observacoes", "Não identificado"),
        "fontes": links
    }
    return resultado

# --- INTERFACE ---
st.title("🌿 Agente LDR Pipeline - Proesc")

user_email = st.sidebar.text_input("E-mail:", value="thales@proesc.com")
if not user_email: st.stop()

tab1, tab2, tab3, tab4 = st.tabs(["📂 1. Upload", "🎯 2. Filtros", "🚀 3. Processar", "📜 4. Histórico"])

with tab1:
    file = st.file_uploader("Subir Planilha INEP", type="xlsx")
    if file:
        st.session_state['df_raw'] = pd.read_excel(file)
        st.success("Planilha carregada!")

with tab2:
    if 'df_raw' in st.session_state:
        df = st.session_state['df_raw']
        # Filtro CRM (Índice 14 / Coluna O)
        if len(df.columns) > 14:
            df = df[~df.iloc[:, 14].astype(str).str.lower().str.contains('sim|yes|true|1', na=False)].copy()
        
        c1, c2, c3 = st.columns(3)
        with c1:
            f_uf = st.selectbox("UF", ["Todos"] + sorted([str(x) for x in df.iloc[:, 3].unique()]))
        with c2:
            f_porte = st.selectbox("Porte", ["Todos"] + sorted([str(x) for x in df.iloc[:, 12].unique()]))
        with c3:
            limite = st.number_input("Qtd", 1, 50, 3)

        if f_uf != "Todos": df = df[df.iloc[:, 3].astype(str) == f_uf]
        if f_porte != "Todos": df = df[df.iloc[:, 12].astype(str) == f_porte]
        st.session_state['df_final'] = df.head(limite)
        st.metric("Selecionadas", len(st.session_state['df_final']))

with tab3:
    if 'df_final' in st.session_state:
        dff = st.session_state['df_final']
        if st.button("🚀 INICIAR PIPELINE DE ENRIQUECIMENTO"):
            res_rodada = supabase.table("rodadas").insert({"nome_arquivo": "Pipeline LDR", "total_leads": len(dff), "usuario_email": user_email}).execute()
            rid = res_rodada.data[0]['id']
            
            barra = st.progress(0)
            status_txt = st.empty()
            
            for i, (idx, row) in enumerate(dff.iterrows()):
                nome, uf, mun, end = str(row.iloc[1]), str(row.iloc[3]), str(row.iloc[4]), str(row.iloc[7])
                status_txt.text(f"📍 Fase {i+1}/{len(dff)}: Processando {nome}")
                
                # EXECUÇÃO DO PIPELINE
                res = pipeline_enriquecimento(nome, mun, uf, end)
                
                # SALVAMENTO
                dados_save = {
                    "rodada_id": rid, "nome_escola": nome, "municipio": mun, "uf": uf, "telefone_inep": str(row.iloc[8]),
                    "status": "Completa", **res
                }
                supabase.table("leads_enriquecidos").insert(dados_save).execute()
                barra.progress((i + 1) / len(dff))
                time.sleep(1)
            
            st.success("✅ Ciclo de Enriquecimento Finalizado!")

with tab4:
    rodadas = supabase.table("rodadas").select("*").eq("usuario_email", user_email).order("created_at", desc=True).execute()
    for r in rodadas.data:
        with st.expander(f"📁 {r['created_at'][:16]} - {r['total_leads']} leads"):
            leads = supabase.table("leads_enriquecidos").select("*").eq("rodada_id", r['id']).execute()
            if leads.data:
                res_df = pd.DataFrame(leads.data)
                cols = ['nome_escola', 'municipio', 'uf', 'telefone_inep', 'telefone_alternativo', 'cnpj', 'razao_social', 'diretor', 'email', 'site', 'sge_atual', 'agenda_digital', 'observacoes']
                st.dataframe(res_df[[c for c in cols if c in res_df.columns]])

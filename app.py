import streamlit as st
import pandas as pd
import google.generativeai as genai
from supabase import create_client
import time
import json
import re

# --- CONFIGURAÇÃO VISUAL ---
st.set_page_config(page_title="Agente LDR Proesc v4", layout="wide")
st.markdown("""
    <style>
    h1, h2, h3, p { color: #004225 !important; }
    .stButton>button { background-color: #004225 !important; color: white !important; font-weight: bold; height: 3em; border-radius: 10px; }
    .stMetric { background-color: #f0f9eb; padding: 15px; border-radius: 10px; border: 1px solid #64CD32; }
    [data-testid="stExpander"] { border: 1px solid #64CD32; border-radius: 10px; }
    </style>
    """, unsafe_allow_html=True)

# --- CONEXÕES ---
try:
    supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    # Flash para busca (rápido), Pro para interpretação (inteligente)
    model_flash = genai.GenerativeModel('gemini-1.5-flash', tools=[{"google_search_retrieval": {}}])
    model_pro = genai.GenerativeModel('gemini-1.5-pro')
except Exception as e:
    st.error(f"Erro de conexão: {e}")

# --- LOGICA DE PESOS E STATUS ---
PESOS = {
    "diretor": 25,
    "sge_atual": 25,
    "agenda_digital": 20,
    "telefone_alternativo": 15,
    "cnpj": 5,
    "email": 5,
    "site": 5
}

def calcular_score_proesc(dados):
    score = 0
    for campo, peso in PESOS.items():
        valor = dados.get(campo, "")
        if valor and valor != "Não identificado" and valor != "null":
            score += peso
    
    # Status baseado em Negócio (Proesc)
    # Requisito para "Completa": Ter Diretor OU Telefone + Ter SGE ou Agenda
    tem_decisor = (dados.get("diretor") != "Não identificado" or dados.get("telefone_alternativo") != "Não identificado")
    tem_tech = (dados.get("sge_atual") != "Não identificado" or dados.get("agenda_digital") != "Não identificado")
    
    if tem_decisor and tem_tech: status = "💎 Completa (Pronta p/ BDR)"
    elif tem_decisor or tem_tech: status = "🟡 Parcial"
    else: status = "⚪ Sem Dados Relevantes"
    
    return score, status

# --- PIPELINE DE ENRIQUECIMENTO ---

def normalizar_nome(nome):
    """Limpa apenas espaços e caracteres inúteis, mantendo siglas (SESI, COC, etc)"""
    return re.sub(r"\s+", " ", str(nome).strip())

def pipeline_ldr(nome, mun, uf):
    # ETAPA 1: DESCOBERTA (Busca os Links Reais)
    prompt_1 = f"Localize o site oficial, Instagram e dados de registro da escola {nome} em {mun}-{uf}."
    try:
        res_1 = model_flash.generate_content(prompt_1)
        # Extraímos os metadados brutos (evidências do Google)
        evidencias = res_1.text
        links = []
        if hasattr(res_1.candidates[0], 'grounding_metadata'):
            metadata = res_1.candidates[0].grounding_metadata
            if hasattr(metadata, 'search_entry_point'):
                links.append(metadata.search_entry_point.rendered_content)
    except:
        return None

    # ETAPA 2: EXTRAÇÃO E INTERPRETAÇÃO (A IA analisa o que o Google achou)
    prompt_2 = f"""
    Analise estas evidências brutas: {evidencias}
    Sobre a escola: {nome}.
    Extraia EXCLUSIVAMENTE em JSON:
    - cnpj
    - razao_social
    - diretor (Procure no QSA ou páginas institucional)
    - telefone_alternativo (Um contato diferente do padrão)
    - email
    - site
    - sge_atual (Ex: Proesc, Sophia, Totvs, WPensar)
    - agenda_digital (Ex: ClassApp, Agenda Edu)
    - observacoes (Resumo técnico para abordagem de vendas)
    
    Regra: Use "Não identificado" se não houver evidência clara.
    """
    
    try:
        res_2 = model_pro.generate_content(prompt_2, generation_config={"response_mime_type": "application/json"})
        dados = json.loads(res_2.text)
        dados["fontes"] = links
        return dados
    except:
        return None

# --- INTERFACE STREAMLIT ---
st.title("🌿 Agente LDR Enterprise - Proesc v4")

user_email = st.sidebar.text_input("E-mail BDR:", value="thales@proesc.com")
if not user_email: st.stop()

tab1, tab2, tab3, tab4 = st.tabs(["📂 Upload", "🎯 Filtros", "🚀 Processar", "📜 Auditoria"])

with tab1:
    file = st.file_uploader("Arraste sua planilha INEP aqui", type="xlsx")
    if file:
        st.session_state['df_raw'] = pd.read_excel(file)
        st.success("Planilha carregada!")

with tab2:
    if 'df_raw' in st.session_state:
        df = st.session_state['df_raw']
        # Limpeza CRM (Coluna O / 14)
        if len(df.columns) > 14:
            df = df[~df.iloc[:, 14].astype(str).str.lower().str.contains('sim|yes|true|1', na=False)].copy()
        
        c1, c2, c3 = st.columns(3)
        with c1: f_uf = st.selectbox("UF", ["Todos"] + sorted([str(x) for x in df.iloc[:, 3].unique()]))
        with c2: f_porte = st.selectbox("Porte", ["Todos"] + sorted([str(x) for x in df.iloc[:, 12].unique()]))
        with c3: limite = st.number_input("Tamanho do Lote", 1, 100, 3)

        if f_uf != "Todos": df = df[df.iloc[:, 3].astype(str) == f_uf]
        if f_porte != "Todos": df = df[df.iloc[:, 12].astype(str) == f_porte]
        st.session_state['df_final'] = df.head(limite)
        st.metric("Escolas prontas", len(st.session_state['df_final']))

with tab3:
    if 'df_final' in st.session_state:
        df_p = st.session_state['df_final']
        if st.button("🚀 INICIAR CICLO DE INTELIGÊNCIA"):
            # Criar Rodada
            rodada = supabase.table("rodadas").insert({"nome_arquivo": "Remessa v4", "total_leads": len(df_p), "usuario_email": user_email}).execute()
            rid = rodada.data[0]['id']
            
            prog = st.progress(0)
            status_txt = st.empty()
            
            for i, (idx, row) in enumerate(df_p.iterrows()):
                nome_original = str(row.iloc[1])
                nome_clean = normalizar_nome(nome_original)
                mun, uf = str(row.iloc[4]), str(row.iloc[3])
                
                status_txt.info(f"Analisando evidências de: **{nome_clean}** ({i+1}/{len(df_p)})")
                
                # Executar Pipeline
                resultado = pipeline_ldr(nome_clean, mun, uf)
                
                if resultado:
                    score, status = calcular_score_proesc(resultado)
                    dados_supabase = {
                        "rodada_id": rid, "nome_escola": nome_clean, "municipio": mun, "uf": uf,
                        "telefone_inep": str(row.iloc[8]), "status": status, "confianca": score,
                        **resultado
                    }
                else:
                    dados_supabase = {
                        "rodada_id": rid, "nome_escola": nome_clean, "status": "Erro na Busca", "confianca": 0
                    }
                
                supabase.table("leads_enriquecidos").insert(dados_supabase).execute()
                prog.progress((i + 1) / len(df_p))
                time.sleep(1)
            
            st.success("✅ Rodada finalizada com sucesso!")

with tab4:
    rodadas = supabase.table("rodadas").select("*").eq("usuario_email", user_email).order("created_at", desc=True).execute()
    for r in rodadas.data:
        with st.expander(f"📁 {r['created_at'][:16]} | {r['total_leads']} leads"):
            leads = supabase.table("leads_enriquecidos").select("*").eq("rodada_id", r['id']).order("confianca", desc=True).execute()
            if leads.data:
                res_df = pd.DataFrame(leads.data)
                # Ordenar por valor comercial
                cols = ['confianca', 'status', 'nome_escola', 'sge_atual', 'agenda_digital', 'diretor', 'telefone_alternativo', 'email', 'site']
                st.dataframe(res_df[cols])
                st.download_button("📥 Baixar Planilha BDR", res_df.to_csv(index=False).encode('utf-8'), f"ldr_{r['id']}.csv")

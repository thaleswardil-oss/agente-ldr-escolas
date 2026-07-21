import streamlit as st
import pandas as pd
import google.generativeai as genai
from supabase import create_client
import time
import json
import re

# --- CONFIGURAÇÃO VISUAL ---
st.set_page_config(page_title="Agente LDR Proesc", layout="wide")

st.markdown("""
    <style>
    h1, h2, h3, p, span, label { color: #004225 !important; }
    .stButton>button {
        background-color: #004225 !important;
        color: white !important;
        border-radius: 8px;
        font-weight: bold;
    }
    .stMetric { background-color: #f0f9eb; padding: 10px; border-radius: 10px; border: 1px solid #64CD32; }
    </style>
    """, unsafe_allow_html=True)

# --- CONEXÕES ---
try:
    supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    # Configuração de alta performance para busca
    model = genai.GenerativeModel(
        model_name='gemini-1.5-flash-latest',
        tools=[{"google_search_retrieval": {}}]
    )
except Exception as e:
    st.error(f"Erro de conexão: {e}")

# --- FUNÇÃO DE PESQUISA AVANÇADA ---
def enriquecer_escola(nome, cidade, uf, endereco, tel_inep):
    # Prompt agressivo para forçar o uso da busca
    prompt = f"""
    Aja como um Agente de Inteligência (LDR). Sua tarefa é pesquisar na internet e extrair dados reais da escola abaixo.
    
    ALVO: {nome}
    LOCALIDADE: {cidade} - {uf}
    REFERÊNCIA: {endereco}
    TELEFONE CONHECIDO: {tel_inep}

    INSTRUÇÕES DE BUSCA:
    1. Pesquise no Google pelo nome da escola + cidade para achar o site oficial e redes sociais.
    2. Busque em sites de transparência (como Casa dos Dados ou Econodata) para achar o CNPJ e a Razão Social.
    3. Consulte o Quadro de Sócios e Administradores (QSA) para identificar o Diretor ou Sócio.
    4. Procure por sinais de softwares (SGE) no rodapé do site da escola ou em notícias de implementação.
    5. Procure por Agenda Digital analisando o portal do aluno da escola.

    DEVOLVA EXCLUSIVAMENTE UM JSON COM:
    - cnpj (com pontuação)
    - razao_social (nome empresarial completo)
    - diretor (nome do gestor ou sócio principal)
    - telefone_alternativo (um número fixo ou celular diferente de {tel_inep})
    - email (contato oficial)
    - site (URL completa)
    - sge_atual (Ex: Proesc, Sophia, Totvs, WPensar, Escola Web, SAGEx)
    - agenda_digital (Ex: ClassApp, Agenda Edu, ClipEscola)
    - observacoes (Frase curta para o BDR)

    * IMPORTANTE: Se não encontrar de jeito nenhum, escreva "Não identificado". Não invente dados.
    """
    
    try:
        # Geração com temperatura baixa para maior precisão
        response = model.generate_content(prompt, generation_config={"temperature": 0.1})
        
        # Limpeza de resposta para garantir apenas o JSON
        json_text = re.search(r'\{.*\}', response.text, re.DOTALL)
        if json_text:
            dados = json.loads(json_text.group())
            # Lista de campos para validação
            campos = ['cnpj', 'razao_social', 'diretor', 'telefone_alternativo', 'email', 'site', 'sge_atual', 'agenda_digital', 'observacoes']
            for c in campos:
                if c not in dados or not dados[c] or dados[c] == "null":
                    dados[c] = "Não identificado"
            return dados
        return None
    except:
        return None

# --- INTERFACE ---
st.title("🌿 Agente LDR - Inteligência Comercial")

user_email = st.sidebar.text_input("E-mail de acesso:", value="thales@proesc.com")
if not user_email:
    st.info("👈 Informe seu e-mail para começar.")
    st.stop()

tab1, tab2, tab3, tab4 = st.tabs(["📂 1. Upload", "🎯 2. Filtros", "🚀 3. Processar", "📜 4. Histórico"])

with tab1:
    file = st.file_uploader("Subir Planilha INEP", type="xlsx")
    if file:
        df_raw = pd.read_excel(file)
        st.session_state['df_raw'] = df_raw
        st.success(f"Carregado: {len(df_raw)} escolas.")

with tab2:
    if 'df_raw' in st.session_state:
        df = st.session_state['df_raw']
        # Filtro CRM (Índice 14 / Coluna O)
        if len(df.columns) > 14:
            df = df[~df.iloc[:, 14].astype(str).str.lower().str.contains('sim|yes|true|1', na=False)].copy()

        c1, c2, c3 = st.columns(3)
        with c1:
            ufs = ["Todos"] + sorted([str(x) for x in df.iloc[:, 3].unique()])
            f_uf = st.selectbox("Estado", ufs)
        with c2:
            portes = ["Todos"] + sorted([str(x) for x in df.iloc[:, 12].unique()])
            f_porte = st.selectbox("Porte", portes)
        with c3:
            limite = st.number_input("Qtd. Leads", 1, 500, 5)

        if f_uf != "Todos": df = df[df.iloc[:, 3].astype(str) == f_uf]
        if f_porte != "Todos": df = df[df.iloc[:, 12].astype(str) == f_porte]
        df_final = df.head(limite)
        st.session_state['df_final'] = df_final
        st.metric("Prontas para Enriquecer", len(df_final))
    else:
        st.write("Aguardando arquivo...")

with tab3:
    if 'df_final' in st.session_state:
        dff = st.session_state['df_final']
        if st.button("🚀 INICIAR ENRIQUECIMENTO"):
            res_rodada = supabase.table("rodadas").insert({
                "nome_arquivo": "Remessa LDR", "total_leads": len(dff), "usuario_email": user_email
            }).execute()
            rid = res_rodada.data[0]['id']
            
            barra = st.progress(0)
            status_log = st.empty()
            
            for i, (idx, row) in enumerate(dff.iterrows()):
                nome, uf, mun, end, tel = str(row.iloc[1]), str(row.iloc[3]), str(row.iloc[4]), str(row.iloc[7]), str(row.iloc[8])
                status_log.text(f"Buscando dados reais de: {nome}...")
                
                res = enriquecer_escola(nome, mun, uf, end, tel)
                
                # Template de salvamento com a nova coluna
                dados_save = {
                    "rodada_id": rid, "nome_escola": nome, "municipio": mun, "uf": uf, "telefone_inep": tel,
                    "status": "Completa" if res else "Sem dados",
                    "cnpj": "Não identificado", "razao_social": "Não identificado", "diretor": "Não identificado",
                    "telefone_alternativo": "Não identificado", "email": "Não identificado", 
                    "site": "Não identificado", "sge_atual": "Não identificado", 
                    "agenda_digital": "Não identificado", "observacoes": "Não identificado"
                }
                
                if res:
                    dados_save.update(res)
                
                supabase.table("leads_enriquecidos").insert(dados_save).execute()
                barra.progress((i + 1) / len(dff))
            
            st.success("✅ Processo Finalizado!")
    else:
        st.write("Aguardando filtros...")

with tab4:
    st.subheader("Histórico")
    rodadas = supabase.table("rodadas").select("*").eq("usuario_email", user_email).order("created_at", desc=True).execute()
    for r in rodadas.data:
        with st.expander(f"📁 {r['created_at'][:16]} - {r['total_leads']} leads"):
            leads = supabase.table("leads_enriquecidos").select("*").eq("rodada_id", r['id']).execute()
            if leads.data:
                res_df = pd.DataFrame(leads.data)
                # Ordem das colunas atualizada com o Telefone Alternativo
                cols_order = ['nome_escola', 'municipio', 'uf', 'telefone_inep', 'telefone_alternativo', 'cnpj', 'razao_social', 'diretor', 'email', 'site', 'sge_atual', 'agenda_digital', 'observacoes']
                st.dataframe(res_df[[c for c in cols_order if c in res_df.columns]])
                st.download_button("📥 Baixar CSV", res_df.to_csv(index=False).encode('utf-8'), f"leads_{r['id']}.csv")

import streamlit as st
import pandas as pd
import google.generativeai as genai
from supabase import create_client
import time
import json

# --- CONFIGURAÇÃO DE CORES (FIX CONTRASTE) ---
st.set_page_config(page_title="Agente LDR", layout="wide")

st.markdown("""
    <style>
    /* Forçar cores para garantir visibilidade */
    h1, h2, h3, p, span, label {
        color: #004225 !important; /* Verde Amazônia */
    }
    .stButton>button {
        background-color: #004225 !important;
        color: white !important;
        font-weight: bold;
        border-radius: 10px;
    }
    .stTabs [data-baseweb="tab"] {
        font-weight: bold;
        color: #004225 !important;
    }
    .stTabs [aria-selected="true"] {
        background-color: #B4F069 !important;
        border-radius: 5px;
    }
    </style>
    """, unsafe_allow_html=True)

# --- CONEXÕES ---
# Usando try/except para não travar a tela se as chaves falharem
try:
    supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    model = genai.GenerativeModel(
        model_name='gemini-1.5-flash-latest', 
        tools=[{"google_search_retrieval": {}}]
    )
except Exception as e:
    st.error(f"Erro de conexão: {e}")

# --- FUNÇÃO DE PESQUISA ---
def pesquisar_escola(escola_dados):
    prompt = f"Enriqueça via busca real: {escola_dados['nome']}, {escola_dados['municipio']}-{escola_dados['uf']}. Retorne JSON puro: cnpj, razao_social, diretor, telefone_alternativo, email, sge_atual, agenda_digital, site, observacoes."
    try:
        response = model.generate_content(prompt)
        texto_json = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(texto_json)
    except:
        return None

# --- INTERFACE ---
st.title("🌿 Agente LDR - Enriquecimento")

# Login simplificado que não trava a renderização
user_email = st.sidebar.text_input("Seu E-mail:", placeholder="admin@escola.com")

if not user_email:
    st.info("👈 Por favor, digite seu e-mail na barra lateral para começar.")
    st.stop()

# Abas principais
tab1, tab2, tab3 = st.tabs(["📂 1. Upload", "🎯 2. Filtrar", "🚀 3. Enriquecer"])

with tab1:
    st.subheader("Subir Lista do INEP")
    uploaded_file = st.file_uploader("Selecione o arquivo .xlsx", type="xlsx")
    if uploaded_file:
        df_original = pd.read_excel(uploaded_file)
        st.session_state['df_original'] = df_original
        st.success(f"Planilha carregada com {len(df_original)} escolas!")

with tab2:
    if 'df_original' in st.session_state:
        df = st.session_state['df_original']
        
        # Busca automática de colunas
        def get_col(options):
            for opt in options:
                for c in df.columns:
                    if opt.lower() in c.lower(): return c
            return df.columns[0]

        col_uf = get_col(['uf', 'estado'])
        col_porte = get_col(['porte', 'tamanho'])

        st.subheader("Configurar Filtros")
        c1, c2, c3 = st.columns(3)
        
        with c1:
            uf_sel = st.selectbox("Estado (UF)", ["Todos"] + sorted(list(df[col_uf].unique())))
        with c2:
            porte_sel = st.selectbox("Porte", ["Todos"] + sorted(list(df[col_porte].unique())))
        with c3:
            limite = st.number_input("Qtd. Leads", 1, 500, 10)

        # Filtragem
        df_f = df.copy()
        if uf_sel != "Todos": df_f = df_f[df_f[col_uf] == uf_sel]
        if porte_sel != "Todos": df_f = df_f[df_f[col_porte] == porte_sel]
        df_f = df_f.head(limite)
        
        st.session_state['df_filtrado'] = df_f
        st.metric("Escolas selecionadas", len(df_f))
        
        if st.button("CONFIRMAR E IR PARA O PASSO 3"):
            st.success("Filtros aplicados! Vá para a aba '3. Enriquecer'")
    else:
        st.write("Aguardando upload da planilha...")

with tab3:
    if 'df_filtrado' in st.session_state:
        df_f = st.session_state['df_filtrado']
        st.subheader("Execução")
        
        if st.button("COMEÇAR AGORA"):
            # Criar rodada
            rodada = supabase.table("rodadas").insert({
                "nome_arquivo": "Remessa", "total_leads": len(df_f), "usuario_email": user_email
            }).execute()
            rid = rodada.data[0]['id']
            
            prog = st.progress(0)
            for i, (idx, row) in enumerate(df_f.iterrows()):
                nome = str(row.get('Escola') or row.get('Nome') or "Escola")
                st.write(f"🔍 Pesquisando: {nome}...")
                
                res = pesquisar_escola({
                    "nome": nome, 
                    "municipio": row.get('Município') or row.get('Municipio', ''),
                    "uf": row.get('UF', '')
                })
                
                # Salvar no banco
                dados = {
                    "rodada_id": rid, "nome_escola": nome, 
                    "status": "Completa" if res else "Sem dados"
                }
                if res: dados.update(res)
                supabase.table("leads_enriquecidos").insert(dados).execute()
                
                prog.progress((i + 1) / len(df_f))
            
            st.success("Tudo pronto! Confira o histórico no banco.")
    else:
        st.write("Defina os filtros no Passo 2 primeiro.")
    else:
        st.write("Aguardando início do processamento...")

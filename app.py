import streamlit as st
import pandas as pd
import google.generativeai as genai
from supabase import create_client
import time
import json

# --- CONFIGURAÇÃO DE CORES E TEMA ---
st.set_page_config(page_title="Agente LDR - Amazônia", layout="wide")

# CSS para aplicar o Verde Amazônia e Verde Amapá
st.markdown(f"""
    <style>
    :root {{
        --verde-amazonia: #004225;
        --verde-amapa: #64CD32;
        --verde-claro: #B4F069;
    }}
    .stApp {{
        background-color: #f8fcf5;
    }}
    .stButton>button {{
        background-color: var(--verde-amazonia);
        color: white;
        border-radius: 8px;
        border: none;
        height: 3em;
        width: 100%;
        font-weight: bold;
    }}
    .stButton>button:hover {{
        background-color: var(--verde-amapa);
        color: white;
    }}
    /* Estilo dos cards de filtros */
    .filter-container {{
        background-color: white;
        padding: 20px;
        border-radius: 15px;
        border: 1px solid var(--verde-claro);
    }}
    /* Badge de contagem */
    .count-badge {{
        background-color: var(--verde-amazonia);
        color: white;
        padding: 10px;
        border-radius: 8px;
        text-align: center;
        font-weight: bold;
    }}
    </style>
    """, unsafe_allow_html=True)

# --- CONEXÕES ---
supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
model = genai.GenerativeModel(model_name='gemini-1.5-flash', tools=[{"google_search": {}}])

# --- FUNÇÃO DE PESQUISA ---
def pesquisar_escola(escola_dados):
    prompt = f"Enriqueça os dados desta escola brasileira via busca real: {escola_dados['nome']}, {escola_dados['municipio']}-{escola_dados['uf']}. Retorne JSON puro com: cnpj, razao_social, diretor, telefone_alternativo, email, sge_atual, agenda_digital, site, observacoes."
    try:
        response = model.generate_content(prompt)
        texto_json = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(texto_json), "Concluído"
    except:
        return None, "Erro na busca"

# --- INTERFACE ---
st.title("🌿 Agente LDR")

if 'user_email' not in st.session_state:
    st.session_state.user_email = st.text_input("E-mail para login:")
    st.stop()

# Simulação de Stepper usando Tabs
step1, step2, step3 = st.tabs(["1. Upload da lista", "2. Filtrar remessa", "3. Enriquecimento"])

# --- PASSO 1: UPLOAD ---
with step1:
    uploaded_file = st.file_uploader("Suba sua planilha do INEP", type="xlsx")
    if uploaded_file:
        df_original = pd.read_excel(uploaded_file)
        st.session_state['df_original'] = df_original
        st.success("Planilha carregada! Vá para a aba '2. Filtrar remessa'")

# --- PASSO 2: FILTRAR ---
with step2:
    if 'df_original' in st.session_state:
        df = st.session_state['df_original']
        
        # Detectar colunas automaticamente
        def find_col(possible_names):
            for name in possible_names:
                for col in df.columns:
                    if name.lower() in col.lower(): return col
            return None

        col_porte = find_col(['porte'])
        col_uf = find_col(['uf'])
        
        st.subheader("Configurar remessa")
        
        c1, c2 = st.columns(2)
        with c1:
            filtro_porte = st.selectbox("PORTE", ["Todos os portes"] + list(df[col_porte].unique())) if col_porte else st.text("Coluna Porte não achada")
        with c2:
            filtro_uf = st.selectbox("UF", ["Todos"] + list(df[col_uf].unique())) if col_uf else st.text("Coluna UF não achada")
        
        qtd_limite = st.number_input("QUANTIDADE", min_value=1, max_value=len(df), value=min(10, len(df)))

        # Aplicar filtros
        df_filtrado = df.copy()
        if col_porte and filtro_porte != "Todos os portes":
            df_filtrado = df_filtrado[df_filtrado[col_porte] == filtro_porte]
        if col_uf and filtro_uf != "Todos":
            df_filtrado = df_filtrado[df_filtrado[col_uf] == filtro_uf]
        
        df_filtrado = df_filtrado.head(qtd_limite)
        st.session_state['df_filtrado'] = df_filtrado

        st.markdown(f"""
            <div class='count-badge'>
                Escolas disponíveis com esses filtros: {len(df_filtrado)}
            </div>
        """, unsafe_allow_html=True)
        
        if st.button("🚀 Iniciar enriquecimento"):
            st.info("Iniciando... Vá para a aba '3. Enriquecimento' para ver o progresso.")
            st.session_state['iniciar'] = True
    else:
        st.warning("Primeiro, suba uma planilha na aba 1.")

# --- PASSO 3: ENRIQUECIMENTO ---
with step3:
    if 'iniciar' in st.session_state and st.session_state['iniciar']:
        df_final = st.session_state['df_filtrado']
        
        # Criar a rodada
        rodada = supabase.table("rodadas").insert({
            "nome_arquivo": "Remessa Filtrada",
            "total_leads": len(df_final),
            "usuario_email": st.session_state.user_email
        }).execute()
        rodada_id = rodada.data[0]['id']

        progress_bar = st.progress(0)
        
        for idx, row in df_final.iterrows():
            nome_escola = str(row.get('Escola') or row.get('Nome'))
            st.write(f"Enriquecendo: **{nome_escola}**...")
            
            res, status = pesquisar_escola({
                "nome": nome_escola,
                "municipio": row.get('Município') or row.get('Municipio'),
                "uf": row.get('UF')
            })
            
            # Salvar
            supabase.table("leads_enriquecidos").insert({
                "rodada_id": rodada_id,
                "nome_escola": nome_escola,
                "status": "Completa" if res else "Erro",
                **(res if res else {})
            }).execute()
            
            progress_bar.progress((idx + 1) / len(df_final))
        
        st.success("Processo concluído!")
        del st.session_state['iniciar'] # Reseta para não rodar em loop
    else:
        st.write("Aguardando início do processamento...")

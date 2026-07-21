import streamlit as st
import pandas as pd
import google.generativeai as genai
from supabase import create_client
import time
import json

# --- CONFIGURAÇÃO VISUAL (VERDE AMAZÔNIA) ---
st.set_page_config(page_title="Agente LDR Amazônia", layout="wide")

st.markdown("""
    <style>
    /* Cores principais */
    :root {
        --verde-amazonia: #004225;
        --verde-amapa: #64CD32;
    }
    /* Forçar cores de texto e botões */
    h1, h2, h3, p, span, label, .stMarkdown { color: #004225 !important; }
    
    .stButton>button {
        background-color: #004225 ! Ads;
        color: white !important;
        border-radius: 8px;
        width: 100%;
        font-weight: bold;
        border: none;
        padding: 10px;
    }
    .stButton>button:hover { background-color: #64CD32 !important; }
    
    /* Estilo das abas */
    .stTabs [data-baseweb="tab"] { color: #004225 !important; font-weight: bold; }
    .stTabs [aria-selected="true"] { border-bottom: 3px solid #64CD32 !important; }
    </style>
    """, unsafe_allow_html=True)

# --- INICIALIZAÇÃO DE APIs ---
try:
    supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    # Configuração correta para busca real (Grounding)
    model = genai.GenerativeModel(
        model_name='gemini-1.5-flash-latest',
        tools=[{"google_search_retrieval": {}}]
    )
except Exception as e:
    st.error(f"Erro de configuração: {e}")

# --- FUNÇÃO DE ENRIQUECIMENTO ---
def enriquecer_escola(nome, cidade, uf):
    prompt = f"""
    Enriqueça os dados da escola '{nome}' em {cidade}-{uf} usando busca real.
    Retorne APENAS um JSON com estas chaves: 
    cnpj, razao_social, diretor, telefone_alternativo, email, sge_atual, agenda_digital, site, observacoes.
    Use "Não identificado" para campos não encontrados.
    """
    try:
        response = model.generate_content(prompt)
        # Extração do JSON
        txt = response.text.replace('```json', '').replace('```', '').strip()
        dados = json.loads(txt)
        
        # Extração das fontes (Audit Trail)
        fontes = []
        if hasattr(response.candidates[0], 'grounding_metadata'):
            meta = response.candidates[0].grounding_metadata
            if hasattr(meta, 'search_entry_point'):
                fontes.append(meta.search_entry_point.rendered_content)
        
        return dados, fontes
    except:
        return None, []

# --- INTERFACE PRINCIPAL ---
st.title("🌿 Agente LDR - Inteligência Comercial")

# Sidebar para E-mail
st.sidebar.header("Acesso")
user_email = st.sidebar.text_input("Seu e-mail:")

if not user_email:
    st.info("👈 Por favor, informe seu e-mail na barra lateral.")
    st.stop()

# Abas do Fluxo
tab1, tab2, tab3, tab4 = st.tabs(["📂 1. Upload", "🎯 2. Filtros", "🚀 3. Processar", "📜 4. Histórico"])

# ABA 1: UPLOAD
with tab1:
    st.subheader("Subir Planilha do INEP")
    file = st.file_uploader("Arquivo .xlsx", type="xlsx")
    if file:
        df_ini = pd.read_excel(file)
        st.session_state['df_raw'] = df_ini
        st.success(f"Carregado: {len(df_ini)} registros.")

# ABA 2: FILTROS
with tab2:
    if 'df_raw' in st.session_state:
        df = st.session_state['df_raw']
        
        # Identificar colunas automaticamente
        def find_c(list_names):
            for n in list_names:
                for c in df.columns:
                    if n.lower() in c.lower(): return c
            return df.columns[0]

        c_uf = find_c(['uf', 'estado'])
        c_porte = find_c(['porte', 'tamanho'])
        
        st.subheader("Configurar Remessa")
        col1, col2 = st.columns(2)
        with col1:
            f_uf = st.selectbox("Filtrar UF", ["Todos"] + sorted(list(df[c_uf].unique())))
        with col2:
            f_porte = st.selectbox("Filtrar Porte", ["Todos"] + sorted(list(df[c_porte].unique())))
        
        limite = st.number_input("Quantidade de escolas para enriquecer", 1, 100, 10)
        
        # Aplicar filtros
        dff = df.copy()
        if f_uf != "Todos": dff = dff[dff[c_uf] == f_uf]
        if f_porte != "Todos": dff = dff[dff[c_porte] == f_porte]
        dff = dff.head(limite)
        
        st.session_state['df_final'] = dff
        st.metric("Escolas selecionadas", len(dff))
        st.write("Se estiver tudo certo, vá para a aba **3. Processar**.")
    else:
        st.warning("Aguardando planilha na aba 1.")

# ABA 3: PROCESSAR
with tab3:
    if 'df_final' in st.session_state:
        dff = st.session_state['df_final']
        st.subheader("Iniciar Enriquecimento Real")
        st.write("O agente usará o Google Search para encontrar dados oficiais.")
        
        if st.button("🚀 INICIAR AGORA"):
            # Registrar Rodada
            new_r = supabase.table("rodadas").insert({
                "nome_arquivo": "Processamento LDR",
                "total_leads": len(dff),
                "usuario_email": user_email
            }).execute()
            rid = new_r.data[0]['id']
            
            barra = st.progress(0)
            status = st.empty()
            
            for i, (idx, row) in enumerate(dff.iterrows()):
                nome_esc = str(row.get('Escola') or row.get('Nome') or "Escola")
                status.text(f"Enriquecendo ({i+1}/{len(dff)}): {nome_esc}")
                
                res, urls = enriquecer_escola(
                    nome_esc, 
                    row.get('Município') or row.get('Municipio', ''),
                    row.get('UF', '')
                )
                
                # Salvar no banco
                insert_data = {
                    "rodada_id": rid,
                    "nome_escola": nome_esc,
                    "municipio": row.get('Município') or row.get('Municipio', ''),
                    "uf": row.get('UF', ''),
                    "status": "Completa" if res else "Sem dados",
                    "fontes": urls
                }
                if res: insert_data.update(res)
                supabase.table("leads_enriquecidos").insert(insert_data).execute()
                
                barra.progress((i + 1) / len(dff))
            
            st.success("✅ Processamento finalizado! Veja os dados na aba Histórico.")
    else:
        st.info("Configure os filtros na aba anterior.")

# ABA 4: HISTÓRICO
with tab4:
    st.subheader("Minhas Rodadas")
    rodadas = supabase.table("rodadas").select("*").eq("usuario_email", user_email).order("created_at", desc=True).execute()
    
    for r in rodadas.data:
        with st.expander(f"📁 {r['created_at'][:16]} - {r['total_leads']} escolas"):
            leads = supabase.table("leads_enriquecidos").select("*").eq("rodada_id", r['id']).execute()
            if leads.data:
                df_res = pd.DataFrame(leads.data)
                st.dataframe(df_res)
                # Exportar
                csv = df_res.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Baixar Excel (CSV)", csv, f"leads_{r['id']}.csv", "text/csv")

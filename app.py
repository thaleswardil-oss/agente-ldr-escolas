import streamlit as st
import pandas as pd
import google.generativeai as genai
from supabase import create_client
import time
import json

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
    model = genai.GenerativeModel(
        model_name='gemini-1.5-flash-latest',
        tools=[{"google_search_retrieval": {}}]
    )
except Exception as e:
    st.error(f"Erro de conexão: {e}")

# --- FUNÇÃO DE PESQUISA (PROMPT ESTILO LOVABLE/BDR) ---
def enriquecer_escola(nome, cidade, uf, endereco, tel_inep):
    prompt = f"""
    Você é um especialista em inteligência de mercado educacional.
    PESQUISE NO GOOGLE A ESCOLA: {nome} em {cidade}-{uf}.
    
    DADOS ORIGINAIS:
    Endereço: {endereco}
    Telefone INEP: {tel_inep}

    REQUISITOS DE EXTRAÇÃO:
    1. CNPJ e Razão Social exata.
    2. Diretor/Responsável (se achar no QSA, indique).
    3. Identifique o SGE (Ex: Proesc, Sophia, Totvs, WPensar, RM, Lyceum).
    4. Identifique a Agenda Digital (Ex: Agenda Edu, ClassApp, ClipEscola).
    5. E-mail de contato e Site.
    6. Observação comercial curta para o BDR.

    Responda APENAS em JSON puro com as chaves:
    cnpj, razao_social, diretor, telefone_alternativo, email, sge_atual, agenda_digital, site, observacoes.
    Use "Não identificado" se não encontrar.
    """
    try:
        response = model.generate_content(prompt)
        txt = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(txt), response.candidates[0].grounding_metadata.search_entry_point.rendered_content if hasattr(response.candidates[0], 'grounding_metadata') else ""
    except:
        return None, ""

# --- INTERFACE ---
st.title("🌿 Agente LDR - Inteligência Comercial")

user_email = st.sidebar.text_input("Seu e-mail:", value="thales@proesc.com")
if not user_email:
    st.info("👈 Informe seu e-mail para começar.")
    st.stop()

tab1, tab2, tab3, tab4 = st.tabs(["📂 1. Upload", "🎯 2. Filtros", "🚀 3. Processar", "📜 4. Histórico"])

with tab1:
    file = st.file_uploader("Subir Planilha INEP (Colunas A-O)", type="xlsx")
    if file:
        # Lendo sem cabeçalho para garantir os índices
        df_raw = pd.read_excel(file)
        st.session_state['df_raw'] = df_raw
        st.success(f"Carregado: {len(df_raw)} escolas.")

with tab2:
    if 'df_raw' in st.session_state:
        df = st.session_state['df_raw']
        
        # MAPEAMENTO FIXO PELO PADRÃO INEP QUE VOCÊ PASSOU:
        # A:0(Status), B:1(Nome), D:3(UF), E:4(Mun), H:7(End), I:8(Tel), M:12(Porte), O:14(CRM)
        
        st.subheader("Configuração de Remessa")
        
        # Filtro CRM Automático (Coluna O / Índice 14)
        col_crm_idx = 14
        if len(df.columns) > col_crm_idx:
            antes = len(df)
            df = df[~df.iloc[:, col_crm_idx].astype(str).str.lower().str.contains('sim|yes|true|1', na=False)].copy()
            st.warning(f"💡 {antes - len(df)} escolas removidas (já estão no CRM).")

        c1, c2, c3 = st.columns(3)
        with c1:
            uf_col = 3 # Coluna D
            ufs = ["Todos"] + sorted([str(x) for x in df.iloc[:, uf_col].unique()])
            f_uf = st.selectbox("Estado", ufs)
        with c2:
            porte_col = 12 # Coluna M
            portes = ["Todos"] + sorted([str(x) for x in df.iloc[:, porte_col].unique()])
            f_porte = st.selectbox("Porte", portes)
        with c3:
            limite = st.number_input("Qtd. Leads", 1, 500, 10)

        # Aplicar Filtros
        if f_uf != "Todos": df = df[df.iloc[:, uf_col].astype(str) == f_uf]
        if f_porte != "Todos": df = df[df.iloc[:, porte_col].astype(str) == f_porte]
        df_final = df.head(limite)
        
        st.session_state['df_final'] = df_final
        st.metric("Prontas para Enriquecer", len(df_final))
    else:
        st.write("Aguardando arquivo...")

with tab3:
    if 'df_final' in st.session_state:
        dff = st.session_state['df_final']
        if st.button("🚀 INICIAR PROCESSO"):
            res_rodada = supabase.table("rodadas").insert({
                "nome_arquivo": "Remessa LDR", "total_leads": len(dff), "usuario_email": user_email
            }).execute()
            rid = res_rodada.data[0]['id']
            
            barra = st.progress(0)
            status_log = st.empty()
            
            for i, (idx, row) in enumerate(dff.iterrows()):
                # Pegando dados pelos índices exatos da sua lista
                nome = str(row.iloc[1]) # Coluna B
                uf = str(row.iloc[3])   # Coluna D
                mun = str(row.iloc[4])  # Coluna E
                end = str(row.iloc[7])  # Coluna H
                tel = str(row.iloc[8])  # Coluna I
                
                status_log.text(f"Enriquecendo ({i+1}/{len(dff)}): {nome}")
                
                res, fonte_html = enriquecer_escola(nome, mun, uf, end, tel)
                
                # Dados para salvar (Garante que os dados originais do INEP fiquem salvos)
                dados_save = {
                    "rodada_id": rid,
                    "nome_escola": nome,
                    "municipio": mun,
                    "uf": uf,
                    "telefone_inep": tel,
                    "status": "Completa" if res else "Sem dados",
                    "fontes": [fonte_html] if fonte_html else []
                }
                if res: dados_save.update(res)
                
                supabase.table("leads_enriquecidos").insert(dados_save).execute()
                barra.progress((i + 1) / len(dff))
            
            st.success("✅ Finalizado! Veja na aba Histórico.")
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
                # Reorganizar colunas para ficar igual ao seu print antigo
                cols_order = ['nome_escola', 'municipio', 'uf', 'telefone_inep', 'cnpj', 'razao_social', 'diretor', 'email', 'site', 'sge_atual', 'agenda_digital', 'observacoes']
                st.dataframe(res_df[[c for c in cols_order if c in res_df.columns]])
                st.download_button("📥 Baixar CSV", res_df.to_csv(index=False).encode('utf-8'), f"leads_{r['id']}.csv")

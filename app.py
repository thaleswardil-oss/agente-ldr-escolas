import streamlit as st
import pandas as pd
import google.generativeai as genai
from supabase import create_client
import time
import json

# --- CONFIGURAÇÃO VISUAL ---
st.set_page_config(page_title="Agente LDR Amazônia", layout="wide")

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

# --- INICIALIZAÇÃO DE CONEXÕES ---
try:
    supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    model = genai.GenerativeModel(
        model_name='gemini-1.5-flash-latest',
        tools=[{"google_search_retrieval": {}}]
    )
except Exception as e:
    st.error(f"Erro de configuração: {e}")

# --- FUNÇÃO DE PESQUISA GEMINI ---
def enriquecer_escola(nome, cidade, uf, endereco, telefone):
    prompt = f"""
    Enriqueça os dados da escola '{nome}' localizada em {cidade}-{uf}.
    Endereço base: {endereco}
    Telefone base: {telefone}
    
    Use a busca real para encontrar:
    1. CNPJ e Razão Social (Transparência/QSA).
    2. Nome do Diretor ou Sócio (indique se veio do QSA).
    3. E-mail e Telefone Alternativo atualizados.
    4. SGE (Sistema de Gestão) e Agenda Digital (ex: Sophia, Totvs, WPensar, Agenda Edu).
    5. Site oficial.
    6. Uma breve observação para abordagem comercial.

    Retorne APENAS um JSON com estas chaves: 
    cnpj, razao_social, diretor, telefone_alternativo, email, sge_atual, agenda_digital, site, observacoes.
    Use "Não identificado" para o que não encontrar.
    """
    try:
        response = model.generate_content(prompt)
        txt = response.text.replace('```json', '').replace('```', '').strip()
        dados = json.loads(txt)
        
        fontes = []
        if hasattr(response.candidates[0], 'grounding_metadata'):
            meta = response.candidates[0].grounding_metadata
            if hasattr(meta, 'search_entry_point'):
                fontes.append(meta.search_entry_point.rendered_content)
        return dados, fontes
    except:
        return None, []

# --- INTERFACE ---
st.title("🌿 Agente LDR - Inteligência Comercial Proesc")

user_email = st.sidebar.text_input("Seu e-mail (Login):", value="")

if not user_email:
    st.info("👈 Por favor, informe seu e-mail na barra lateral para começar.")
    st.stop()

tab1, tab2, tab3, tab4 = st.tabs(["📂 1. Upload", "🎯 2. Filtros", "🚀 3. Processar", "📜 4. Histórico"])

# ABA 1: UPLOAD
with tab1:
    file = st.file_uploader("Subir Planilha padrão INEP", type="xlsx")
    if file:
        df_raw = pd.read_excel(file)
        st.session_state['df_raw'] = df_raw
        st.success(f"Planilha carregada com {len(df_raw)} linhas!")

# ABA 2: FILTROS E LIMPEZA CRM
with tab2:
    if 'df_raw' in st.session_state:
        df = st.session_state['df_raw']
        
        # Mapeamento Flexível de Colunas
        def find_c(targets, default_idx):
            for t in targets:
                for i, c in enumerate(df.columns):
                    if t.lower() in str(c).lower(): return c
            if len(df.columns) > default_idx: return df.columns[default_idx]
            return None

        col_nome = find_c(['nome', 'escola'], 1)
        col_uf = find_c(['uf', 'estado'], 3)
        col_mun = find_c(['município', 'municipio', 'cidade'], 4)
        col_end = find_c(['endereço', 'endereco', 'logradouro'], 7)
        col_tel = find_c(['telefone', 'fone'], 8)
        col_porte = find_c(['porte', 'tamanho'], 12)
        col_crm = find_c(['crm', 'está no crm'], 14)

        st.subheader("Configurar Filtros e Limpeza")
        
        # 1. Filtro Automático CRM
        if col_crm:
            antes = len(df)
            df_limpo = df[~df[col_crm].astype(str).str.lower().str.contains('sim|yes|true|1', na=False)].copy()
            removidos = antes - len(df_limpo)
            if removidos > 0:
                st.warning(f"🚫 {removidos} escolas removidas porque já estão no CRM.")
        else:
            df_limpo = df.copy()

        c1, c2, c3 = st.columns(3)
        with c1:
            uf_list = ["Todos"] + sorted([str(x) for x in df_limpo[col_uf].unique()]) if col_uf else ["N/A"]
            f_uf = st.selectbox("Filtrar UF", uf_list)
        with c2:
            porte_list = ["Todos"] + sorted([str(x) for x in df_limpo[col_porte].unique()]) if col_porte else ["N/A"]
            f_porte = st.selectbox("Filtrar Porte", porte_list)
        with c3:
            limite = st.number_input("Quantidade para processar", 1, 500, 10)
        
        # Aplicar Filtros
        dff = df_limpo.copy()
        if col_uf and f_uf != "Todos": dff = dff[dff[col_uf].astype(str) == f_uf]
        if col_porte and f_porte != "Todos": dff = dff[dff[col_porte].astype(str) == f_porte]
        dff = dff.head(limite)
        
        st.session_state['df_final'] = dff
        st.metric("Prontas para enriquecer", len(dff))
    else:
        st.write("Aguardando upload...")

# ABA 3: PROCESSAR
with tab3:
    if 'df_final' in st.session_state:
        dff = st.session_state['df_final']
        if st.button("🚀 INICIAR ENRIQUECIMENTO REAL"):
            res_rodada = supabase.table("rodadas").insert({
                "nome_arquivo": "Remessa INEP", "total_leads": len(dff), "usuario_email": user_email
            }).execute()
            rid = res_rodada.data[0]['id']
            
            barra = st.progress(0)
            status = st.empty()
            
            for i, (idx, row) in enumerate(dff.iterrows()):
                nome_esc = str(row.get(col_nome, "Escola Desconhecida"))
                status.text(f"🔍 Pesquisando: {nome_esc}...")
                
                res, urls = enriquecer_escola(
                    nome_esc, 
                    str(row.get(col_mun, '')),
                    str(row.get(col_uf, '')),
                    str(row.get(col_end, '')),
                    str(row.get(col_tel, ''))
                )
                
                # Salvar no Supabase
                dados_save = {
                    "rodada_id": rid, "nome_escola": nome_esc, "fontes": urls,
                    "status": "Completa" if res else "Sem dados"
                }
                if res: dados_save.update(res)
                supabase.table("leads_enriquecidos").insert(dados_save).execute()
                barra.progress((i + 1) / len(dff))
            
            st.success("✅ Processamento finalizado! Confira o histórico.")
    else:
        st.write("Aguardando filtros...")

# ABA 4: HISTÓRICO
with tab4:
    st.subheader("Meus Enriquecimentos")
    rodadas = supabase.table("rodadas").select("*").eq("usuario_email", user_email).order("created_at", desc=True).execute()
    
    for r in rodadas.data:
        with st.expander(f"📁 {r['created_at'][:16]} - {r['total_leads']} escolas"):
            leads = supabase.table("leads_enriquecidos").select("*").eq("rodada_id", r['id']).execute()
            if leads.data:
                df_res = pd.DataFrame(leads.data)
                st.dataframe(df_res)
                csv = df_res.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Baixar CSV", csv, f"leads_{r['id']}.csv", "text/csv")

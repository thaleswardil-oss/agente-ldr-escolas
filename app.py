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
    # gemini-1.5-flash-latest é o mais estável para busca real
    model = genai.GenerativeModel(
        model_name='gemini-1.5-flash-latest',
        tools=[{"google_search_retrieval": {}}]
    )
except Exception as e:
    st.error(f"Erro de configuração inicial: {e}")

# --- FUNÇÃO DE PESQUISA GEMINI ---
def enriquecer_escola(nome, cidade, uf, endereco, telefone):
    prompt = f"""
    Enriqueça os dados da escola '{nome}' em {cidade}-{uf}. 
    Endereço: {endereco}. Tel: {telefone}.
    
    Retorne um JSON puro (sem markdown) com: 
    cnpj, razao_social, diretor, telefone_alternativo, email, sge_atual, agenda_digital, site, observacoes.
    Use "Não identificado" para campos vazios.
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
st.title("🌿 Agente LDR - Inteligência Proesc")

user_email = st.sidebar.text_input("Seu e-mail de acesso:", value="")

if not user_email:
    st.info("👈 Digite seu e-mail na lateral para liberar o sistema.")
    st.stop()

tab1, tab2, tab3, tab4 = st.tabs(["📂 1. Upload", "🎯 2. Filtros", "🚀 3. Processar", "📜 4. Histórico"])

# ABA 1: UPLOAD
with tab1:
    st.subheader("Subir Planilha INEP")
    file = st.file_uploader("Selecione o arquivo .xlsx", type="xlsx")
    if file:
        df_raw = pd.read_excel(file)
        st.session_state['df_raw'] = df_raw
        st.success(f"Planilha carregada: {len(df_raw)} escolas encontradas.")

# ABA 2: FILTROS E MAPEAMENTO INEP (A-O)
with tab2:
    if 'df_raw' in st.session_state:
        df = st.session_state['df_raw']
        
        # BUSCADOR DE COLUNAS (Agora com str(c) para evitar o erro da sua imagem)
        def find_c(keywords, index_fallback):
            # 1. Tenta por nome
            for k in keywords:
                for c in df.columns:
                    if k.lower() in str(c).lower(): return c
            # 2. Tenta pela posição INEP (A=0, B=1...)
            if len(df.columns) > index_fallback:
                return df.columns[index_fallback]
            return None

        # Mapeamento conforme sua lista (A-O)
        col_nome  = find_c(['escola', 'nome'], 1)       # B
        col_uf    = find_c(['uf', 'estado'], 3)         # D
        col_mun   = find_c(['município', 'cidade'], 4)  # E
        col_end   = find_c(['endereço', 'logradouro'], 7) # H
        col_tel   = find_c(['telefone', 'fone'], 8)     # I
        col_porte = find_c(['porte'], 12)               # M
        col_crm   = find_c(['crm', 'está no'], 14)      # O

        st.subheader("Configuração da Remessa")

        # FILTRO DE CRM (Regra: Excluir quem está marcado como Sim)
        if col_crm:
            antes = len(df)
            df_limpo = df[~df[col_crm].astype(str).str.lower().str.contains('sim|yes|true|1', na=False)].copy()
            removidos = antes - len(df_limpo)
            if removidos > 0:
                st.warning(f"💡 {removidos} escolas removidas por já estarem no CRM.")
        else:
            df_limpo = df.copy()

        c1, c2, c3 = st.columns(3)
        with c1:
            ufs = ["Todos"] + sorted([str(x) for x in df_limpo[col_uf].unique()]) if col_uf else ["N/A"]
            f_uf = st.selectbox("Estado", ufs)
        with c2:
            portes = ["Todos"] + sorted([str(x) for x in df_limpo[col_porte].unique()]) if col_porte else ["N/A"]
            f_porte = st.selectbox("Porte", portes)
        with c3:
            limite = st.number_input("Quantidade", 1, 500, 10)

        # Aplicar Filtros Finais
        df_f = df_limpo.copy()
        if col_uf and f_uf != "Todos": df_f = df_f[df_f[col_uf].astype(str) == f_uf]
        if col_porte and f_porte != "Todos": df_f = df_f[df_f[col_porte].astype(str) == f_porte]
        df_f = df_f.head(limite)
        
        st.session_state['df_final'] = df_f
        st.metric("Total selecionado", len(df_f))
    else:
        st.write("Aguardando upload na Aba 1.")

# ABA 3: PROCESSAR
with tab3:
    if 'df_final' in st.session_state:
        df_proc = st.session_state['df_final']
        if st.button("🚀 INICIAR ENRIQUECIMENTO"):
            # Criar rodada no Supabase
            nova_rodada = supabase.table("rodadas").insert({
                "nome_arquivo": "Remessa INEP", "total_leads": len(df_proc), "usuario_email": user_email
            }).execute()
            rid = nova_rodada.data[0]['id']
            
            prog = st.progress(0)
            status_txt = st.empty()
            
            for i, (idx, row) in enumerate(df_proc.iterrows()):
                nome = str(row.get(col_nome, "Escola"))
                status_txt.text(f"Processando ({i+1}/{len(df_proc)}): {nome}")
                
                # Chamada IA
                res, urls = enriquecer_escola(
                    nome, 
                    str(row.get(col_mun, '')), 
                    str(row.get(col_uf, '')),
                    str(row.get(col_end, '')),
                    str(row.get(col_tel, ''))
                )
                
                # Salvar no Banco
                dados_final = {
                    "rodada_id": rid, "nome_escola": nome, "fontes": urls,
                    "status": "Completa" if res else "Sem dados"
                }
                if res: dados_final.update(res)
                supabase.table("leads_enriquecidos").insert(dados_final).execute()
                
                prog.progress((i + 1) / len(df_proc))
            
            st.success("✅ Concluído! Vá para a Aba 4 para baixar.")
    else:
        st.info("Defina os filtros primeiro.")

# ABA 4: HISTÓRICO
with tab4:
    st.subheader("Histórico de Rodadas")
    rodadas = supabase.table("rodadas").select("*").eq("usuario_email", user_email).order("created_at", desc=True).execute()
    
    for r in rodadas.data:
        with st.expander(f"📁 {r['created_at'][:16]} - {r['total_leads']} leads"):
            leads = supabase.table("leads_enriquecidos").select("*").eq("rodada_id", r['id']).execute()
            if leads.data:
                res_df = pd.DataFrame(leads.data)
                st.dataframe(res_df)
                csv = res_df.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Baixar CSV", csv, f"leads_{r['id']}.csv", "text/csv")

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
    model = genai.GenerativeModel(
        model_name='gemini-1.5-flash-latest',
        tools=[{"google_search_retrieval": {}}]
    )
except Exception as e:
    st.error(f"Erro de conexão: {e}")

# --- FUNÇÃO DE PESQUISA (MAPEAMENTO COMPLETO) ---
def enriquecer_escola(nome, cidade, uf, endereco, tel_inep):
    prompt = f"""
    Como um Agente de Inteligência Comercial (LDR), realize uma busca profunda na internet sobre a escola abaixo.
    
    ESCOLA: {nome}
    LOCAL: {cidade} - {uf}
    ENDEREÇO CONHECIDO: {endereco}
    TELEFONE INEP: {tel_inep}

    VOCÊ DEVE RETORNAR OS SEGUINTES CAMPOS OBRIGATORIAMENTE:
    1. cnpj: Busque pelo nome + município.
    2. razao_social: Conforme cadastrado na Receita Federal.
    3. diretor: Identifique o sócio-administrador ou responsável pedagógico (consulte o QSA).
    4. telefone_alternativo: Um número de telefone diferente de {tel_inep}.
    5. email: E-mail da secretaria, financeiro ou direção.
    6. sge_atual: Identifique se usam softwares como Sophia, Totvs, Escola Web, SAGEx, Proesc, WPensar, etc.
    7. agenda_digital: Identifique se usam ClassApp, Agenda Edu, ClipEscola, Google Agenda ou comunicadores próprios.
    8. site: URL oficial.
    9. observacoes: Uma frase curta e relevante para o BDR usar na prospecção.

    REGRAS CRÍTICAS:
    - Se não encontrar um dado, preencha o campo estritamente com "Não identificado".
    - Nunca deixe campos vazios ou nulos.
    - Responda apenas o JSON puro, sem textos explicativos.
    """
    
    try:
        response = model.generate_content(prompt)
        # Limpador de JSON robusto
        json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
        if json_match:
            dados = json.loads(json_match.group())
            # Garante que todos os campos existem no dicionário
            campos_obrigatorios = ['cnpj', 'razao_social', 'diretor', 'telefone_alternativo', 'email', 'sge_atual', 'agenda_digital', 'site', 'observacoes']
            for campo in campos_obrigatorios:
                if campo not in dados or not dados[campo]:
                    dados[campo] = "Não identificado"
            return dados
        return None
    except Exception as e:
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
        st.subheader("Configuração de Remessa")
        
        # Filtro CRM (Coluna O / Índice 14)
        if len(df.columns) > 14:
            antes = len(df)
            df = df[~df.iloc[:, 14].astype(str).str.lower().str.contains('sim|yes|true|1', na=False)].copy()
            st.warning(f"💡 {antes - len(df)} escolas removidas (já no CRM).")

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
        if st.button("🚀 INICIAR ENRIQUECIMENTO"):
            res_rodada = supabase.table("rodadas").insert({
                "nome_arquivo": "Remessa Enriquecida", "total_leads": len(dff), "usuario_email": user_email
            }).execute()
            rid = res_rodada.data[0]['id']
            
            barra = st.progress(0)
            status_log = st.empty()
            
            for i, (idx, row) in enumerate(dff.iterrows()):
                nome = str(row.iloc[1]) # Coluna B
                uf = str(row.iloc[3])   # Coluna D
                mun = str(row.iloc[4])  # Coluna E
                end = str(row.iloc[7])  # Coluna H
                tel = str(row.iloc[8])  # Coluna I
                
                status_log.text(f"Pesquisando ({i+1}/{len(dff)}): {nome}")
                
                res = enriquecer_escola(nome, mun, uf, end, tel)
                
                # Montagem do registro para o banco
                dados_save = {
                    "rodada_id": rid,
                    "nome_escola": nome,
                    "municipio": mun,
                    "uf": uf,
                    "telefone_inep": tel,
                    "status": "Completa" if res else "Erro na busca",
                    "cnpj": "Não identificado",
                    "razao_social": "Não identificado",
                    "diretor": "Não identificado",
                    "email": "Não identificado",
                    "site": "Não identificado",
                    "sge_atual": "Não identificado",
                    "agenda_digital": "Não identificado",
                    "observacoes": "Não identificado"
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
                cols_order = ['nome_escola', 'municipio', 'uf', 'telefone_inep', 'cnpj', 'razao_social', 'diretor', 'email', 'site', 'sge_atual', 'agenda_digital', 'observacoes']
                st.dataframe(res_df[[c for c in cols_order if c in res_df.columns]])
                csv = res_df.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Baixar CSV", csv, f"leads_{r['id']}.csv")

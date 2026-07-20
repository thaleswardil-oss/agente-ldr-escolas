import streamlit as st
import pandas as pd
import google.generativeai as genai
from supabase import create_client
import time
import json

# --- CONFIGURAÇÕES INICIAIS ---
st.set_page_config(page_title="LDR Enriquecedor", layout="wide")

# Conectar ao Supabase
supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

# Conectar ao Gemini
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
model = genai.GenerativeModel(
    model_name='gemini-1.5-flash', # Flash é mais rápido para buscas
    tools=[{"google_search_retrieval": {}}] 
)

# --- FUNÇÕES DE LÓGICA ---

def pesquisar_escola(escola_dados):
    """Faz a busca no Google via Gemini com Grounding"""
    prompt = f"""
    Enriqueça os dados desta escola brasileira. 
    DADOS INEP (PONTO DE PARTIDA):
    Nome: {escola_dados['nome']}
    Cidade/UF: {escola_dados['municipio']} - {escola_dados['uf']}
    Endereço: {escola_dados.get('endereco', 'Não informado')}
    Telefone: {escola_dados.get('telefone', 'Não informado')}

    REGRAS:
    1. Use a busca real para confirmar se a escola existe e se os dados batem com a cidade.
    2. Se não encontrar algo, responda "Não identificado". NUNCA invente.
    3. Extraia: cnpj, razao_social, diretor (indique se é do QSA), telefone_alternativo, email, sge_atual (ex: Sophia, Totvs, WPensar), agenda_digital (ex: Agenda Edu, ClassApp), site.
    4. Crie uma frase de observação para abordagem comercial.

    Responda EXCLUSIVAMENTE em formato JSON puro, sem markdown, com as chaves:
    cnpj, razao_social, diretor, telefone_alternativo, email, sge_atual, agenda_digital, site, observacoes.
    """
    
    for tentativa in range(3): # Resiliência: 3 tentativas
        try:
            response = model.generate_content(prompt)
            # Extrair o texto e os links das fontes
            texto_json = response.text.replace('```json', '').replace('```', '').strip()
            dados = json.loads(texto_json)
            
            # Pegar fontes das citações (groundingMetadata)
            fontes = []
            if hasattr(response.candidates[0], 'grounding_metadata'):
                metadata = response.candidates[0].grounding_metadata
                if hasattr(metadata, 'search_entry_point'):
                    fontes.append(metadata.search_entry_point.rendered_content)

            return dados, fontes, "Concluído"
        except Exception as e:
            time.sleep(2 * (tentativa + 1)) # Backoff simples
            erro = str(e)
    
    return None, [], f"Erro: {erro}"

# --- INTERFACE ---

st.title("🏫 Agente LDR - Enriquecimento de Escolas")

# Login Simples
if 'user_email' not in st.session_state:
    st.session_state.user_email = st.text_input("Digite seu e-mail para começar:")
    st.stop()

tab_upload, tab_historico = st.tabs(["Subir Planilha", "Histórico de Rodadas"])

with tab_upload:
    uploaded_file = st.file_uploader("Arquivo INEP (.xlsx)", type="xlsx")
    
    if uploaded_file:
        df = pd.read_excel(uploaded_file)
        # Limpeza básica (excluir o que já está no CRM se houver a coluna)
        if 'CRM' in df.columns:
            df = df[~df['CRM'].astype(str).str.lower().contains('sim|yes|true|1', na=False)]
        
        st.write(f"Total de escolas para processar: {len(df)}")
        
        if st.button("Iniciar Enriquecimento Real"):
            # 1. Criar Rodada no Banco
            rodada = supabase.table("rodadas").insert({
                "nome_arquivo": uploaded_file.name,
                "total_leads": len(df),
                "usuario_email": st.session_state.user_email
            }).execute()
            rodada_id = rodada.data[0]['id']
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for index, row in df.iterrows():
                escola_inep = {
                    "nome": row.get('Escola') or row.get('Nome'),
                    "municipio": row.get('Município') or row.get('Municipio'),
                    "uf": row.get('UF'),
                    "endereco": row.get('Endereço') or row.get('Endereco'),
                    "telefone": row.get('Telefone')
                }
                
                status_text.text(f"Processando: {escola_inep['nome']}...")
                
                # Chamada com Grounding
                resultado, fontes, status_final = pesquisar_escola(escola_inep)
                
                # 2. Salvar no Banco
                if resultado:
                    supabase.table("leads_enriquecidos").insert({
                        "rodada_id": rodada_id,
                        "status": "Completa" if resultado.get('cnpj') != "Não identificado" else "Parcial",
                        "nome_escola": escola_inep['nome'],
                        "municipio": escola_inep['municipio'],
                        "uf": escola_inep['uf'],
                        "telefone_inep": str(escola_inep['telefone']),
                        **resultado,
                        "fontes": fontes
                    }).execute()
                else:
                    supabase.table("leads_enriquecidos").insert({
                        "rodada_id": rodada_id,
                        "status": "Erro",
                        "nome_escola": escola_inep['nome'],
                        "erro_mensagem": status_final
                    }).execute()
                
                progress_bar.progress((index + 1) / len(df))
            
            st.success("Processamento finalizado!")

with tab_historico:
    # Busca rodadas do usuário
    rodadas_db = supabase.table("rodadas").select("*").eq("usuario_email", st.session_state.user_email).order("created_at", desc=True).execute()
    
    for r in rodadas_db.data:
        with st.expander(f"Data: {r['created_at'][:10]} - Arquivo: {r['nome_arquivo']}"):
            leads = supabase.table("leads_enriquecidos").select("*").eq("rodada_id", r['id']).execute()
            df_leads = pd.DataFrame(leads.data)
            if not df_leads.empty:
                st.dataframe(df_leads)
                # Botão para baixar XLSX
                st.download_button("Baixar Resultados (.xlsx)", 
                                 df_leads.to_csv(index=False).encode('utf-8'), 
                                 file_name=f"enriquecimento_{r['id']}.csv")

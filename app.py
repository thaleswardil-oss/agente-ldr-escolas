import ast
import json
import math
import re
import time
import traceback
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

try:
    import google.generativeai as genai
    ERRO_IMPORT_GEMINI = ""
except Exception as exc:
    genai = None
    ERRO_IMPORT_GEMINI = f"{type(exc).__name__}: {exc}"

try:
    from supabase import create_client
    ERRO_IMPORT_SUPABASE = ""
except Exception as exc:
    create_client = None
    ERRO_IMPORT_SUPABASE = f"{type(exc).__name__}: {exc}"

st.set_page_config(page_title="Agente LDR Proesc v4", layout="wide")
st.markdown(
    """
    <style>
    h1, h2, h3, p { color: #004225 !important; }
    .stButton>button { background-color: #004225 !important; color: white !important; font-weight: bold; height: 3em; border-radius: 10px; }
    .stMetric { background-color: #f0f9eb; padding: 15px; border-radius: 10px; border: 1px solid #64CD32; }
    [data-testid="stExpander"] { border: 1px solid #64CD32; border-radius: 10px; }
    </style>
    """,
    unsafe_allow_html=True,
)

VALOR_NAO_IDENTIFICADO = "Não identificado"
DELAY_RETRY = [2, 4, 8, 16]
MAX_TENTATIVAS = len(DELAY_RETRY) + 1
PESOS = {
    "diretor": 25,
    "sge_atual": 25,
    "agenda_digital": 20,
    "telefone_alternativo": 15,
    "cnpj": 5,
    "email": 5,
    "site": 5,
}
PRIORIDADE_FONTES = {
    "site_oficial": 600,
    "receita": 500,
    "qsa": 400,
    "google_maps": 300,
    "instagram": 200,
    "facebook": 100,
    "outro": 0,
}
SIGLAS_PRESERVADAS = {
    "SESI",
    "SENAI",
    "COC",
    "SESC",
    "SENAC",
    "IF",
    "IFPA",
    "IFAP",
    "IFMA",
    "IFCE",
    "IFPI",
    "IFRN",
    "IFPE",
    "IFPB",
    "IFBA",
    "IFAL",
    "IFS",
    "IFES",
    "IFRJ",
    "IFSP",
    "IFPR",
    "IFSC",
    "IFRS",
    "IFC",
    "IFMS",
    "IFMT",
    "IFGO",
    "IFG",
    "IFTO",
    "UNICEF",
    "APAE",
    "CIEP",
    "EMEF",
    "EMEI",
    "EJA",
}
ARTIGOS_MINUSCULOS = {"da", "das", "de", "do", "dos", "e"}
MAPA_TERMOS = {
    "ESC": "Escola",
    "ESCOLA": "Escola",
    "COLEGIO": "Colégio",
    "COLÉGIO": "Colégio",
    "CENTRO": "Centro",
    "EDUCACIONAL": "Educacional",
    "EDUCACAO": "Educação",
    "EDUCAÇÃO": "Educação",
    "INFANTIL": "Infantil",
    "ENSINO": "Ensino",
    "POLICIA": "Polícia",
    "POLÍCIA": "Polícia",
    "MILITAR": "Militar",
    "GERACAO": "Geração",
    "GERAÇÃO": "Geração",
    "INTEGRADO": "Integrado",
    "INTEGRAL": "Integral",
    "TECNICO": "Técnico",
    "TÉCNICO": "Técnico",
    "TECNOLOGIA": "Tecnologia",
    "CRIANCA": "Criança",
    "CRIANÇA": "Criança",
    "JARDIM": "Jardim",
    "COOPERATIVA": "Cooperativa",
    "INSTITUTO": "Instituto",
    "FUNDACAO": "Fundação",
    "FUNDAÇÃO": "Fundação",
    "ASSOCIACAO": "Associação",
    "ASSOCIAÇÃO": "Associação",
}
VALORES_VAZIOS = {
    "",
    "null",
    "none",
    "nan",
    "n/a",
    "na",
    "não identificado",
    "nao identificado",
    "não informado",
    "nao informado",
    "não encontrado",
    "nao encontrado",
    "indisponível",
    "indisponivel",
    "sem dados",
    "desconhecido",
}
CAMPOS_RESULTADO = [
    "cnpj",
    "razao_social",
    "diretor",
    "telefone_alternativo",
    "email",
    "site",
    "sge_atual",
    "agenda_digital",
    "observacoes",
]


def agora_iso():
    return datetime.now(timezone.utc).isoformat()


def texto_seguro(valor):
    if valor is None:
        return ""
    if isinstance(valor, float) and math.isnan(valor):
        return ""
    return str(valor).strip()


def valor_presente(valor):
    texto = texto_seguro(valor)
    return texto.lower() not in VALORES_VAZIOS


def limitar_texto(valor, limite=12000):
    texto = texto_seguro(valor)
    if len(texto) <= limite:
        return texto
    return texto[:limite] + "…"


def objeto_para_dict(objeto):
    if objeto is None:
        return {}
    if isinstance(objeto, dict):
        return objeto
    if hasattr(objeto, "to_dict"):
        try:
            convertido = objeto.to_dict()
            if isinstance(convertido, dict):
                return convertido
        except Exception:
            convertido = None
    if hasattr(objeto, "__dict__"):
        try:
            return {k: v for k, v in vars(objeto).items() if not k.startswith("_")}
        except Exception:
            return {}
    return {}


def obter_atributo(objeto, nome, padrao=None):
    if objeto is None:
        return padrao
    if isinstance(objeto, dict):
        return objeto.get(nome, padrao)
    try:
        return getattr(objeto, nome, padrao)
    except Exception:
        return padrao


def lista_segura(valor):
    if valor is None:
        return []
    if isinstance(valor, list):
        return valor
    if isinstance(valor, tuple):
        return list(valor)
    try:
        return list(valor)
    except Exception:
        return []


def remover_acentos(texto):
    normalizado = unicodedata.normalize("NFD", texto_seguro(texto))
    return "".join(char for char in normalizado if unicodedata.category(char) != "Mn")


def normalizar_espacos(texto):
    return re.sub(r"\s+", " ", texto_seguro(texto)).strip()


def capitalizar_token(token, indice):
    bruto = texto_seguro(token)
    if not bruto:
        return ""
    superior = bruto.upper()
    sem_acento = remover_acentos(superior)
    if superior in SIGLAS_PRESERVADAS or sem_acento in SIGLAS_PRESERVADAS:
        return superior
    if sem_acento in MAPA_TERMOS:
        return MAPA_TERMOS[sem_acento]
    minusculo = bruto.lower()
    if indice > 0 and minusculo in ARTIGOS_MINUSCULOS:
        return minusculo
    if re.fullmatch(r"\d+[A-Za-z]?", bruto):
        return bruto.upper()
    if re.fullmatch(r"[IVXLCDM]+", superior):
        return superior
    return minusculo[:1].upper() + minusculo[1:]


def reorganizar_tipo_escola(tokens):
    if not tokens:
        return tokens
    tipos = ["Colégio", "Escola"]
    for tipo in tipos:
        if tipo not in tokens:
            continue
        indice = tokens.index(tipo)
        if indice == 0:
            return tokens
        antes = tokens[:indice]
        depois = tokens[indice + 1 :]
        conectivo = []
        if depois and depois[0].lower() in {"da", "das", "de", "do", "dos"}:
            conectivo = [depois[0].lower()]
            depois = depois[1:]
        return [tipo] + conectivo + antes + depois
    return tokens


def normalizar_nome(nome):
    bruto = normalizar_espacos(nome)
    if not bruto:
        return VALOR_NAO_IDENTIFICADO
    bruto = re.sub(r"[^\wÀ-ÿ\-\.\s]", " ", bruto, flags=re.UNICODE)
    bruto = normalizar_espacos(bruto)
    tokens_originais = bruto.split(" ")
    tokens_filtrados = []
    for token in tokens_originais:
        superior_sem_acento = remover_acentos(token.upper())
        if superior_sem_acento in {"PART", "PARTICULAR"}:
            continue
        tokens_filtrados.append(token)
    tokens = [capitalizar_token(token, indice) for indice, token in enumerate(tokens_filtrados)]
    tokens = [token for token in tokens if token]
    tokens = reorganizar_tipo_escola(tokens)
    tokens = [capitalizar_token(token, indice) for indice, token in enumerate(tokens)]
    nome_normalizado = normalizar_espacos(" ".join(tokens))
    return nome_normalizado or VALOR_NAO_IDENTIFICADO


def normalizar_uf(uf):
    texto = remover_acentos(texto_seguro(uf)).upper()
    texto = re.sub(r"[^A-Z]", "", texto)
    return texto[:2]


def normalizar_municipio(municipio):
    bruto = normalizar_espacos(municipio)
    if not bruto:
        return VALOR_NAO_IDENTIFICADO
    return " ".join(capitalizar_token(token, indice) for indice, token in enumerate(bruto.split()))


def normalizar_cnpj(cnpj):
    digitos = re.sub(r"\D", "", texto_seguro(cnpj))
    if len(digitos) != 14:
        return VALOR_NAO_IDENTIFICADO
    if digitos == digitos[0] * 14:
        return VALOR_NAO_IDENTIFICADO
    pesos_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma_1 = sum(int(digitos[i]) * pesos_1[i] for i in range(12))
    resto_1 = soma_1 % 11
    dv_1 = 0 if resto_1 < 2 else 11 - resto_1
    pesos_2 = [6] + pesos_1
    soma_2 = sum(int(digitos[i]) * pesos_2[i] for i in range(13))
    resto_2 = soma_2 % 11
    dv_2 = 0 if resto_2 < 2 else 11 - resto_2
    if digitos[-2:] != f"{dv_1}{dv_2}":
        return VALOR_NAO_IDENTIFICADO
    return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"


def normalizar_email(email):
    texto = texto_seguro(email).lower()
    encontrados = re.findall(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", texto)
    if not encontrados:
        return VALOR_NAO_IDENTIFICADO
    return encontrados[0]


def normalizar_telefone(telefone):
    texto = texto_seguro(telefone)
    encontrados = re.findall(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?(?:9?\d{4})[\s\-]?\d{4}", texto)
    candidato = encontrados[0] if encontrados else texto
    digitos = re.sub(r"\D", "", candidato)
    if digitos.startswith("55") and len(digitos) in {12, 13}:
        digitos = digitos[2:]
    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    if len(digitos) == 10:
        return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"
    return VALOR_NAO_IDENTIFICADO


def normalizar_url(url):
    texto = texto_seguro(url).strip(".,;:()[]{}<>\"")
    if not texto:
        return VALOR_NAO_IDENTIFICADO
    if texto.startswith("www."):
        texto = "https://" + texto
    if not re.match(r"^https?://", texto, flags=re.IGNORECASE):
        if re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/.*)?$", texto):
            texto = "https://" + texto
        else:
            return VALOR_NAO_IDENTIFICADO
    try:
        parsed = urlparse(texto)
        if not parsed.netloc or "." not in parsed.netloc:
            return VALOR_NAO_IDENTIFICADO
        return texto
    except Exception:
        return VALOR_NAO_IDENTIFICADO


def normalizar_valor_generico(valor):
    texto = normalizar_espacos(valor)
    if texto.lower() in VALORES_VAZIOS:
        return VALOR_NAO_IDENTIFICADO
    return texto


def validar_diretor(valor):
    texto = normalizar_valor_generico(valor)
    if texto == VALOR_NAO_IDENTIFICADO:
        return VALOR_NAO_IDENTIFICADO
    termos_genericos = {
        "diretor",
        "diretora",
        "direção",
        "direcao",
        "gestor",
        "gestora",
        "coordenação",
        "coordenacao",
        "não identificado",
    }
    if texto.lower() in termos_genericos or len(texto) < 4:
        return VALOR_NAO_IDENTIFICADO
    return texto


def classificar_fonte(url, tipo_informado=""):
    tipo = remover_acentos(texto_seguro(tipo_informado)).lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "site": "site_oficial",
        "oficial": "site_oficial",
        "site_oficial": "site_oficial",
        "receita_federal": "receita",
        "receita": "receita",
        "qsa": "qsa",
        "quadro_societario": "qsa",
        "google_maps": "google_maps",
        "maps": "google_maps",
        "instagram": "instagram",
        "facebook": "facebook",
    }
    if tipo in aliases:
        return aliases[tipo]
    host = texto_seguro(url).lower()
    if "instagram.com" in host:
        return "instagram"
    if "facebook.com" in host or "fb.com" in host:
        return "facebook"
    if "google.com/maps" in host or "maps.google" in host or "goo.gl/maps" in host:
        return "google_maps"
    if "receita" in host or "gov.br/receitafederal" in host:
        return "receita"
    if "qsa" in host:
        return "qsa"
    return "outro"


def dominio_url(url):
    normalizada = normalizar_url(url)
    if normalizada == VALOR_NAO_IDENTIFICADO:
        return ""
    try:
        return urlparse(normalizada).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def deduplicar_fontes(fontes):
    resultado = []
    vistos = set()
    for fonte in lista_segura(fontes):
        if isinstance(fonte, str):
            item = {"url": fonte, "titulo": "", "tipo": classificar_fonte(fonte)}
        else:
            item = objeto_para_dict(fonte)
        url = normalizar_url(item.get("url", item.get("uri", "")))
        if url == VALOR_NAO_IDENTIFICADO:
            continue
        chave = url.rstrip("/").lower()
        if chave in vistos:
            continue
        vistos.add(chave)
        resultado.append(
            {
                "url": url,
                "titulo": limitar_texto(item.get("titulo", item.get("title", "")), 300),
                "tipo": classificar_fonte(url, item.get("tipo", item.get("tipo_fonte", ""))),
            }
        )
    return resultado


def urls_no_texto(texto):
    urls = re.findall(r"https?://[^\s\]\[\)\(<>\"']+", texto_seguro(texto))
    return deduplicar_fontes(urls)


def serializar_json(valor):
    return json.dumps(valor, ensure_ascii=False, default=str)


def resumo_erro(excecao):
    return limitar_texto(f"{type(excecao).__name__}: {excecao}", 2000)


class PipelineLogger:
    def __init__(self, escola, callback=None):
        self.escola = escola
        self.callback = callback
        self.registros = []
        self.erros = []

    def registrar(self, step, mensagem, nivel="INFO", detalhe=""):
        registro = {
            "timestamp": agora_iso(),
            "escola": self.escola,
            "step": step,
            "nivel": nivel,
            "mensagem": limitar_texto(mensagem, 2000),
            "detalhe": limitar_texto(detalhe, 6000),
        }
        self.registros.append(registro)
        if nivel == "ERRO":
            self.erros.append(registro)
        if self.callback:
            try:
                self.callback(registro)
            except Exception as exc:
                registro_callback = {
                    "timestamp": agora_iso(),
                    "escola": self.escola,
                    "step": "LOG",
                    "nivel": "ERRO",
                    "mensagem": "Falha ao exibir o log na interface.",
                    "detalhe": resumo_erro(exc),
                }
                self.registros.append(registro_callback)
                self.erros.append(registro_callback)

    def info(self, step, mensagem, detalhe=""):
        self.registrar(step, mensagem, "INFO", detalhe)

    def alerta(self, step, mensagem, detalhe=""):
        self.registrar(step, mensagem, "ALERTA", detalhe)

    def erro(self, step, mensagem, excecao=None, detalhe=""):
        complemento = detalhe
        if excecao is not None:
            complemento = normalizar_espacos(f"{detalhe} {resumo_erro(excecao)}")
        self.registrar(step, mensagem, "ERRO", complemento)


@st.cache_resource(show_spinner=False)
def inicializar_servicos(supabase_url, supabase_key, gemini_api_key):
    if genai is None:
        raise RuntimeError(f"google.generativeai não pôde ser importado: {ERRO_IMPORT_GEMINI}")
    if create_client is None:
        raise RuntimeError(f"supabase não pôde ser importado: {ERRO_IMPORT_SUPABASE}")
    cliente_supabase = create_client(supabase_url, supabase_key)
    genai.configure(api_key=gemini_api_key)
    return cliente_supabase


def obter_segredo(nome, padrao=None, obrigatorio=False):
    try:
        valor = st.secrets[nome]
    except Exception:
        valor = padrao
    if obrigatorio and not texto_seguro(valor):
        raise RuntimeError(f"Secret obrigatório ausente: {nome}")
    return valor


def modelos_unicos(*nomes):
    resultado = []
    for nome in nomes:
        texto = texto_seguro(nome)
        if texto and texto not in resultado:
            resultado.append(texto)
    return resultado


def extrair_texto_resposta(response):
    if response is None:
        raise ValueError("Resposta vazia do Gemini")
    texto_direto = ""
    try:
        texto_direto = texto_seguro(obter_atributo(response, "text", ""))
    except Exception:
        texto_direto = ""
    if texto_direto:
        return texto_direto
    candidatos = lista_segura(obter_atributo(response, "candidates", []))
    partes_texto = []
    for candidato in candidatos:
        conteudo = obter_atributo(candidato, "content")
        partes = lista_segura(obter_atributo(conteudo, "parts", []))
        for parte in partes:
            texto = texto_seguro(obter_atributo(parte, "text", ""))
            if texto:
                partes_texto.append(texto)
    if partes_texto:
        return "\n".join(partes_texto)
    feedback = obter_atributo(response, "prompt_feedback")
    feedback_dict = objeto_para_dict(feedback)
    finish_reasons = []
    for candidato in candidatos:
        finish_reason = texto_seguro(obter_atributo(candidato, "finish_reason", ""))
        if finish_reason:
            finish_reasons.append(finish_reason)
    diagnostico = {
        "prompt_feedback": feedback_dict,
        "finish_reasons": finish_reasons,
        "response": objeto_para_dict(response),
    }
    raise ValueError(f"Gemini respondeu sem texto utilizável: {limitar_texto(serializar_json(diagnostico), 3000)}")


def extrair_fontes_grounding(response, texto_resposta=""):
    fontes = []
    candidatos = lista_segura(obter_atributo(response, "candidates", []))
    for candidato in candidatos:
        metadata = obter_atributo(candidato, "grounding_metadata")
        chunks = lista_segura(obter_atributo(metadata, "grounding_chunks", []))
        for chunk in chunks:
            web = obter_atributo(chunk, "web")
            uri = texto_seguro(obter_atributo(web, "uri", ""))
            titulo = texto_seguro(obter_atributo(web, "title", ""))
            if uri:
                fontes.append({"url": uri, "titulo": titulo, "tipo": classificar_fonte(uri)})
    fontes.extend(urls_no_texto(texto_resposta))
    return deduplicar_fontes(fontes)


def limpar_bloco_json(texto):
    limpo = texto_seguro(texto)
    limpo = re.sub(r"^\s*```(?:json)?\s*", "", limpo, flags=re.IGNORECASE)
    limpo = re.sub(r"\s*```\s*$", "", limpo)
    limpo = limpo.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return limpo.strip()


def tentar_json_decoder(texto):
    decoder = json.JSONDecoder()
    for indice, char in enumerate(texto):
        if char not in "[{":
            continue
        try:
            valor, _ = decoder.raw_decode(texto[indice:])
            return valor
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("Nenhum JSON recuperável encontrado", texto, 0)


def recuperar_json(texto):
    limpo = limpar_bloco_json(texto)
    erros = []
    tentativas = [
        limpo,
        re.sub(r",\s*([}\]])", r"\1", limpo),
    ]
    for tentativa in tentativas:
        try:
            return json.loads(tentativa)
        except Exception as exc:
            erros.append(resumo_erro(exc))
        try:
            return tentar_json_decoder(tentativa)
        except Exception as exc:
            erros.append(resumo_erro(exc))
    try:
        literal = ast.literal_eval(limpo)
        if isinstance(literal, (dict, list)):
            return literal
    except Exception as exc:
        erros.append(resumo_erro(exc))
    raise ValueError(f"JSON inválido. Tentativas: {' | '.join(erros[-5:])}")


def validar_objeto_json(valor, contexto):
    if not isinstance(valor, dict):
        raise ValueError(f"JSON de {contexto} deve ser um objeto, recebido: {type(valor).__name__}")
    return valor


def gerar_conteudo_com_retry(
    modelos,
    prompt,
    logger,
    step,
    usar_google=False,
    exigir_json=False,
    max_output_tokens=8192,
):
    ultimo_erro = None
    modelos = modelos_unicos(*modelos)
    for tentativa in range(MAX_TENTATIVAS):
        modelo_nome = modelos[min(tentativa, len(modelos) - 1)]
        logger.info(step, f"Tentativa {tentativa + 1}/{MAX_TENTATIVAS} com {modelo_nome}.")
        try:
            model = genai.GenerativeModel(modelo_nome)
            generation_config = {"max_output_tokens": max_output_tokens}
            if exigir_json and tentativa < 3:
                generation_config["response_mime_type"] = "application/json"
            kwargs = {"generation_config": generation_config}
            if usar_google:
                kwargs["tools"] = "google_search_retrieval"
            response = model.generate_content(prompt, **kwargs)
            texto = extrair_texto_resposta(response)
            if not texto:
                raise ValueError("Gemini retornou texto vazio")
            fontes = extrair_fontes_grounding(response, texto) if usar_google else urls_no_texto(texto)
            logger.info(step, f"Gemini respondeu com {len(texto)} caracteres usando {modelo_nome}.")
            return {
                "response": response,
                "texto": texto,
                "fontes": fontes,
                "modelo": modelo_nome,
                "tentativa": tentativa + 1,
            }
        except Exception as exc:
            ultimo_erro = exc
            logger.erro(step, f"Falha na tentativa {tentativa + 1} com {modelo_nome}.", exc)
            if tentativa < len(DELAY_RETRY):
                espera = DELAY_RETRY[tentativa]
                logger.alerta(step, f"Retry em {espera} segundos.")
                time.sleep(espera)
    raise RuntimeError(f"Todas as tentativas do Gemini falharam: {resumo_erro(ultimo_erro)}")


def gerar_json_com_retry(modelos, prompt, logger, step, contexto):
    ultimo_erro = None
    for ciclo in range(2):
        try:
            resposta = gerar_conteudo_com_retry(
                modelos=modelos,
                prompt=prompt,
                logger=logger,
                step=step,
                usar_google=False,
                exigir_json=True,
            )
            logger.info(step, "JSON recebido do Gemini.", limitar_texto(resposta["texto"], 3000))
            parsed = recuperar_json(resposta["texto"])
            objeto = validar_objeto_json(parsed, contexto)
            resposta["json"] = objeto
            return resposta
        except Exception as exc:
            ultimo_erro = exc
            logger.erro(step, "Erro de parsing ou validação do JSON.", exc)
            if ciclo == 0:
                logger.alerta(step, "Nova geração solicitada para recuperar JSON válido.")
    raise RuntimeError(f"Não foi possível obter JSON válido para {contexto}: {resumo_erro(ultimo_erro)}")


def preparar_candidatos(valor):
    if valor is None:
        return []
    if isinstance(valor, dict):
        if "valor" in valor:
            return [valor]
        return []
    if isinstance(valor, str):
        return [{"valor": valor, "tipo_fonte": "outro", "url": "", "confianca": 0}]
    if isinstance(valor, list):
        candidatos = []
        for item in valor:
            if isinstance(item, dict):
                candidatos.append(item)
            elif isinstance(item, str):
                candidatos.append({"valor": item, "tipo_fonte": "outro", "url": "", "confianca": 0})
        return candidatos
    return []


def pontuar_candidato(candidato):
    item = objeto_para_dict(candidato)
    url = texto_seguro(item.get("url", item.get("fonte", "")))
    tipo = classificar_fonte(url, item.get("tipo_fonte", item.get("tipo", "")))
    try:
        confianca = float(item.get("confianca", 0))
    except Exception:
        confianca = 0
    confianca = max(0, min(100, confianca))
    return PRIORIDADE_FONTES.get(tipo, 0) * 1000 + confianca


def selecionar_candidato(candidatos, normalizador, logger, step, campo):
    preparados = preparar_candidatos(candidatos)
    validos = []
    for candidato in preparados:
        item = objeto_para_dict(candidato)
        valor_original = item.get("valor", "")
        valor_normalizado = normalizador(valor_original)
        if valor_normalizado == VALOR_NAO_IDENTIFICADO:
            continue
        item["valor_normalizado"] = valor_normalizado
        item["tipo_fonte_normalizado"] = classificar_fonte(
            item.get("url", item.get("fonte", "")), item.get("tipo_fonte", item.get("tipo", ""))
        )
        item["pontuacao_fonte"] = pontuar_candidato(item)
        validos.append(item)
    if not validos:
        return VALOR_NAO_IDENTIFICADO, None
    validos.sort(key=lambda item: item["pontuacao_fonte"], reverse=True)
    escolhido = validos[0]
    valores_distintos = {item["valor_normalizado"].lower() for item in validos}
    if len(valores_distintos) > 1:
        logger.alerta(
            step,
            f"Conflito encontrado em {campo}; escolhida a fonte mais confiável.",
            serializar_json(validos[:5]),
        )
    return escolhido["valor_normalizado"], escolhido


def extrair_lista_candidatos(objeto, campo):
    chaves = [campo, f"{campo}_candidatos", f"candidatos_{campo}"]
    for chave in chaves:
        if chave in objeto:
            return objeto[chave]
    candidatos_gerais = objeto.get("candidatos", {})
    if isinstance(candidatos_gerais, dict) and campo in candidatos_gerais:
        return candidatos_gerais[campo]
    return []


def normalizar_identidade_json(objeto):
    resultado = dict(objeto)
    confirmada = resultado.get("identidade_confirmada", False)
    if isinstance(confirmada, str):
        confirmada = confirmada.strip().lower() in {"true", "sim", "yes", "1", "confirmada"}
    resultado["identidade_confirmada"] = bool(confirmada)
    resultado["conflitos"] = lista_segura(resultado.get("conflitos", []))
    resultado["evidencias_descartadas"] = lista_segura(resultado.get("evidencias_descartadas", []))
    resultado["justificativa"] = limitar_texto(resultado.get("justificativa", ""), 4000)
    return resultado


def juntar_observacoes(*partes):
    resultado = []
    vistos = set()
    for parte in partes:
        texto = normalizar_espacos(parte)
        if not valor_presente(texto):
            continue
        chave = texto.lower()
        if chave in vistos:
            continue
        vistos.add(chave)
        resultado.append(texto)
    return " | ".join(resultado) if resultado else VALOR_NAO_IDENTIFICADO


def step_0_normalizacao(nome, municipio, uf, telefone_inep, logger):
    logger.info("STEP 0", "Normalização iniciada.")
    resultado = {
        "nome_original": texto_seguro(nome),
        "nome_escola": normalizar_nome(nome),
        "municipio": normalizar_municipio(municipio),
        "uf": normalizar_uf(uf),
        "telefone_inep": normalizar_telefone(telefone_inep),
    }
    logger.info("STEP 0", f"Nome normalizado: {resultado['nome_escola']}.")
    return resultado


def construir_prompt_pesquisa(dados, objetivo):
    base = (
        f"Escola alvo: {dados['nome_escola']}\n"
        f"Nome original no INEP: {dados['nome_original']}\n"
        f"Município: {dados['municipio']}\n"
        f"UF: {dados['uf']}\n"
        f"Telefone INEP: {dados['telefone_inep']}\n"
    )
    objetivos = {
        "identidade": (
            "Pesquise somente evidências para confirmar a identidade desta escola. Localize site oficial, nome oficial, razão social, CNPJ, endereço, Google Maps e perfis sociais oficiais. "
            "Não conclua dados sem fonte. Para cada informação, informe o trecho encontrado, a URL completa e o tipo da fonte. Diferencie escolas homônimas pelo município, UF, endereço e telefone."
        ),
        "contatos": (
            "Pesquise somente evidências públicas de contato e decisão relacionadas a esta escola: diretor, diretora, mantenedor, telefone, WhatsApp e e-mail. "
            "Priorize páginas do site oficial, documentos institucionais, Receita/QSA, Google Maps e perfis oficiais. Para cada achado, informe trecho, URL completa e tipo da fonte. Não misture outra unidade ou escola homônima."
        ),
        "tecnologia": (
            "Pesquise somente evidências sobre tecnologias usadas por esta escola, especialmente sistema de gestão escolar, ERP/SGE, portal do aluno, aplicativo, agenda digital e fornecedores educacionais. "
            "Procure páginas de login, políticas de privacidade, links de aplicativos, comunicados, tutoriais, vagas, publicações e subdomínios. Para cada achado, informe trecho, URL completa e tipo da fonte. Não deduza apenas por aparência do site."
        ),
    }
    return (
        base
        + "\n"
        + objetivos[objetivo]
        + "\nEntregue um dossiê textual de evidências. A resposta será analisada por outra etapa e não deve inventar campos ausentes."
    )


def step_1_pesquisa(dados, modelos_busca, logger):
    logger.info("STEP 1", f"Buscando escola {dados['nome_escola']}.")
    dossies = []
    fontes = []
    modelos_usados = []
    erros = []
    for objetivo in ["identidade", "contatos", "tecnologia"]:
        logger.info("STEP 1", f"Pesquisa direcionada: {objetivo}.")
        try:
            resposta = gerar_conteudo_com_retry(
                modelos=modelos_busca,
                prompt=construir_prompt_pesquisa(dados, objetivo),
                logger=logger,
                step="STEP 1",
                usar_google=True,
                exigir_json=False,
                max_output_tokens=8192,
            )
            dossies.append(f"PESQUISA {objetivo.upper()}\n{resposta['texto']}")
            fontes.extend(resposta["fontes"])
            modelos_usados.append(resposta["modelo"])
            logger.info("STEP 1", f"Google respondeu para {objetivo} com {len(resposta['fontes'])} fontes.")
        except Exception as exc:
            erro = f"Pesquisa {objetivo}: {resumo_erro(exc)}"
            erros.append(erro)
            logger.erro("STEP 1", f"Pesquisa {objetivo} esgotou os retries; pipeline continuará.", exc)
    fontes = deduplicar_fontes(fontes)
    evidencias = "\n\n".join(dossies)
    if not evidencias:
        logger.erro("STEP 1", "Nenhuma pesquisa retornou evidências.")
    else:
        logger.info("STEP 1", f"Pesquisa concluída com {len(fontes)} fontes consolidadas.")
    return {
        "evidencias": limitar_texto(evidencias, 50000),
        "fontes": fontes,
        "modelos_usados": modelos_unicos(*modelos_usados),
        "erros": erros,
        "pesquisa_sucesso": bool(evidencias),
    }


def step_2_validacao_identidade(dados, pesquisa, modelos_analise, logger):
    logger.info("STEP 2", "Validação da identidade iniciada.")
    if not pesquisa["evidencias"]:
        logger.erro("STEP 2", "Identidade não pode ser validada sem evidências.")
        return {
            "identidade_confirmada": False,
            "nome_oficial": dados["nome_escola"],
            "razao_social": VALOR_NAO_IDENTIFICADO,
            "cnpj": VALOR_NAO_IDENTIFICADO,
            "site": VALOR_NAO_IDENTIFICADO,
            "conflitos": [],
            "justificativa": "Nenhuma evidência de pesquisa disponível.",
            "modelo_usado": "",
            "erro": "Nenhuma evidência de pesquisa disponível.",
        }
    prompt = f"""
Você é o validador de identidade de uma escola brasileira. Analise somente as evidências fornecidas. Não pesquise e não use conhecimento externo.

ALVO INEP
Nome original: {dados['nome_original']}
Nome normalizado: {dados['nome_escola']}
Município: {dados['municipio']}
UF: {dados['uf']}
Telefone INEP: {dados['telefone_inep']}

EVIDÊNCIAS
{pesquisa['evidencias']}

Valide se as evidências pertencem à mesma escola e à mesma unidade. Considere nome, município, UF, endereço, telefone, domínio e CNPJ. Descarte homônimas e unidades de outras cidades.
Quando houver conflito, mantenha todos os candidatos. A escolha final será feita pelo código com esta prioridade: site oficial, Receita, QSA, Google Maps, Instagram, Facebook.
Retorne exclusivamente um objeto JSON com:
identidade_confirmada como boolean;
nome_oficial como texto;
justificativa como texto;
conflitos como lista;
evidencias_descartadas como lista;
razao_social_candidatos, cnpj_candidatos e site_candidatos como listas de objetos contendo valor, tipo_fonte, url, evidencia e confianca de 0 a 100.
Use listas vazias quando não houver candidato confiável.
"""
    try:
        resposta = gerar_json_com_retry(modelos_analise, prompt, logger, "STEP 2", "validação de identidade")
        objeto = normalizar_identidade_json(resposta["json"])
        razao_social, razao_escolhida = selecionar_candidato(
            extrair_lista_candidatos(objeto, "razao_social"),
            normalizar_valor_generico,
            logger,
            "STEP 2",
            "razão social",
        )
        cnpj, cnpj_escolhido = selecionar_candidato(
            extrair_lista_candidatos(objeto, "cnpj"),
            normalizar_cnpj,
            logger,
            "STEP 2",
            "CNPJ",
        )
        site, site_escolhido = selecionar_candidato(
            extrair_lista_candidatos(objeto, "site"),
            normalizar_url,
            logger,
            "STEP 2",
            "site",
        )
        nome_oficial = normalizar_nome(objeto.get("nome_oficial", dados["nome_escola"]))
        resultado = {
            "identidade_confirmada": objeto["identidade_confirmada"],
            "nome_oficial": nome_oficial,
            "razao_social": razao_social,
            "cnpj": cnpj,
            "site": site,
            "conflitos": objeto["conflitos"],
            "evidencias_descartadas": objeto["evidencias_descartadas"],
            "justificativa": objeto["justificativa"],
            "selecoes": {
                "razao_social": razao_escolhida,
                "cnpj": cnpj_escolhido,
                "site": site_escolhido,
            },
            "modelo_usado": resposta["modelo"],
            "erro": "",
        }
        logger.info(
            "STEP 2",
            "Identidade validada." if resultado["identidade_confirmada"] else "Identidade não confirmada com segurança.",
        )
        return resultado
    except Exception as exc:
        logger.erro("STEP 2", "Falha na validação da identidade; pipeline continuará com fallback.", exc)
        return {
            "identidade_confirmada": False,
            "nome_oficial": dados["nome_escola"],
            "razao_social": VALOR_NAO_IDENTIFICADO,
            "cnpj": VALOR_NAO_IDENTIFICADO,
            "site": VALOR_NAO_IDENTIFICADO,
            "conflitos": [],
            "evidencias_descartadas": [],
            "justificativa": "Falha técnica na validação da identidade.",
            "selecoes": {},
            "modelo_usado": "",
            "erro": resumo_erro(exc),
        }


def step_3_extracao(dados, pesquisa, identidade, modelos_analise, logger):
    logger.info("STEP 3", "Extração de contatos e dados comerciais iniciada.")
    if not pesquisa["evidencias"]:
        logger.erro("STEP 3", "Extração sem evidências; campos permanecerão vazios.")
        return {
            "diretor": VALOR_NAO_IDENTIFICADO,
            "telefone_alternativo": VALOR_NAO_IDENTIFICADO,
            "email": VALOR_NAO_IDENTIFICADO,
            "observacoes": VALOR_NAO_IDENTIFICADO,
            "modelo_usado": "",
            "erro": "Nenhuma evidência disponível.",
            "selecoes": {},
        }
    prompt = f"""
Você é um analista de dados comerciais de escolas brasileiras. Use somente as evidências abaixo. Não pesquise e não complete por suposição.

ESCOLA ALVO
Nome: {identidade['nome_oficial']}
Nome INEP: {dados['nome_escola']}
Município: {dados['municipio']}
UF: {dados['uf']}
Identidade confirmada: {identidade['identidade_confirmada']}
CNPJ validado: {identidade['cnpj']}
Site validado: {identidade['site']}

EVIDÊNCIAS
{pesquisa['evidencias']}

Extraia candidatos somente quando o trecho indicar claramente a mesma escola ou unidade. Não use telefone ou e-mail de outra cidade, de outra unidade, de associação genérica ou de fornecedor.
Quando houver conflito, mantenha todos os candidatos. A escolha final será feita pelo código com esta prioridade: site oficial, Receita, QSA, Google Maps, Instagram, Facebook.
Retorne exclusivamente um objeto JSON com:
diretor_candidatos, telefone_alternativo_candidatos e email_candidatos como listas de objetos contendo valor, tipo_fonte, url, evidencia e confianca de 0 a 100;
observacoes como texto comercial objetivo contendo achados úteis, sinais de decisão, mantenedora, unidade, divergências ou limitações;
conflitos como lista.
Use listas vazias quando não houver evidência clara.
"""
    try:
        resposta = gerar_json_com_retry(modelos_analise, prompt, logger, "STEP 3", "extração comercial")
        objeto = resposta["json"]
        diretor, diretor_escolhido = selecionar_candidato(
            extrair_lista_candidatos(objeto, "diretor"),
            validar_diretor,
            logger,
            "STEP 3",
            "diretor",
        )
        telefone, telefone_escolhido = selecionar_candidato(
            extrair_lista_candidatos(objeto, "telefone_alternativo"),
            normalizar_telefone,
            logger,
            "STEP 3",
            "telefone",
        )
        email, email_escolhido = selecionar_candidato(
            extrair_lista_candidatos(objeto, "email"),
            normalizar_email,
            logger,
            "STEP 3",
            "e-mail",
        )
        observacoes = normalizar_valor_generico(objeto.get("observacoes", ""))
        resultado = {
            "diretor": diretor,
            "telefone_alternativo": telefone,
            "email": email,
            "observacoes": observacoes,
            "conflitos": lista_segura(objeto.get("conflitos", [])),
            "modelo_usado": resposta["modelo"],
            "erro": "",
            "selecoes": {
                "diretor": diretor_escolhido,
                "telefone_alternativo": telefone_escolhido,
                "email": email_escolhido,
            },
        }
        logger.info("STEP 3", "Extração concluída.")
        return resultado
    except Exception as exc:
        logger.erro("STEP 3", "Falha na extração; pipeline continuará com campos vazios.", exc)
        return {
            "diretor": VALOR_NAO_IDENTIFICADO,
            "telefone_alternativo": VALOR_NAO_IDENTIFICADO,
            "email": VALOR_NAO_IDENTIFICADO,
            "observacoes": VALOR_NAO_IDENTIFICADO,
            "conflitos": [],
            "modelo_usado": "",
            "erro": resumo_erro(exc),
            "selecoes": {},
        }


def step_4_tecnologia(dados, pesquisa, identidade, extracao, modelos_analise, logger):
    logger.info("STEP 4", "Análise de tecnologia iniciada.")
    if not pesquisa["evidencias"]:
        logger.erro("STEP 4", "Análise de tecnologia sem evidências.")
        return {
            "sge_atual": VALOR_NAO_IDENTIFICADO,
            "agenda_digital": VALOR_NAO_IDENTIFICADO,
            "observacoes_tecnologia": VALOR_NAO_IDENTIFICADO,
            "modelo_usado": "",
            "erro": "Nenhuma evidência disponível.",
            "selecoes": {},
        }
    prompt = f"""
Você é um analista de tecnologia educacional. Use somente as evidências abaixo. Não pesquise e não deduza fornecedor sem um sinal verificável.

ESCOLA ALVO
Nome: {identidade['nome_oficial']}
Município: {dados['municipio']}
UF: {dados['uf']}
Site validado: {identidade['site']}

EVIDÊNCIAS
{pesquisa['evidencias']}

Identifique candidatos de SGE atual e Agenda Digital. Evidência válida pode incluir domínio ou subdomínio de login, nome explícito em comunicado, política de privacidade, aplicativo oficial, link de portal, tutorial, documento institucional ou publicação oficial.
Não trate Google Classroom, WhatsApp, e-mail ou rede social como SGE sem evidência explícita de gestão escolar. Não confunda sistema de ensino, material didático, adquirente financeira ou fornecedor de conteúdo com SGE.
Quando houver conflito, mantenha todos os candidatos. A escolha final será feita pelo código com esta prioridade: site oficial, Receita, QSA, Google Maps, Instagram, Facebook.
Retorne exclusivamente um objeto JSON com:
sge_atual_candidatos e agenda_digital_candidatos como listas de objetos contendo valor, tipo_fonte, url, evidencia e confianca de 0 a 100;
observacoes_tecnologia como texto objetivo;
conflitos como lista.
Use listas vazias quando não houver evidência clara.
"""
    try:
        resposta = gerar_json_com_retry(modelos_analise, prompt, logger, "STEP 4", "análise de tecnologia")
        objeto = resposta["json"]
        sge, sge_escolhido = selecionar_candidato(
            extrair_lista_candidatos(objeto, "sge_atual"),
            normalizar_valor_generico,
            logger,
            "STEP 4",
            "SGE",
        )
        agenda, agenda_escolhida = selecionar_candidato(
            extrair_lista_candidatos(objeto, "agenda_digital"),
            normalizar_valor_generico,
            logger,
            "STEP 4",
            "Agenda Digital",
        )
        resultado = {
            "sge_atual": sge,
            "agenda_digital": agenda,
            "observacoes_tecnologia": normalizar_valor_generico(objeto.get("observacoes_tecnologia", "")),
            "conflitos": lista_segura(objeto.get("conflitos", [])),
            "modelo_usado": resposta["modelo"],
            "erro": "",
            "selecoes": {
                "sge_atual": sge_escolhido,
                "agenda_digital": agenda_escolhida,
            },
        }
        logger.info("STEP 4", "Análise de tecnologia concluída.")
        return resultado
    except Exception as exc:
        logger.erro("STEP 4", "Falha na análise de tecnologia; pipeline continuará.", exc)
        return {
            "sge_atual": VALOR_NAO_IDENTIFICADO,
            "agenda_digital": VALOR_NAO_IDENTIFICADO,
            "observacoes_tecnologia": VALOR_NAO_IDENTIFICADO,
            "conflitos": [],
            "modelo_usado": "",
            "erro": resumo_erro(exc),
            "selecoes": {},
        }


def calcular_score_proesc(dados):
    score = 0
    for campo, peso in PESOS.items():
        if valor_presente(dados.get(campo)):
            score += peso
    return min(100, max(0, int(score)))


def definir_status(dados, score, houve_erro_fatal=False):
    if houve_erro_fatal and score == 0:
        return "Erro"
    if score == 0:
        return "Sem dados"
    tem_diretor = valor_presente(dados.get("diretor"))
    tem_contato = valor_presente(dados.get("telefone_alternativo")) or valor_presente(dados.get("email"))
    tem_tecnologia = valor_presente(dados.get("sge_atual")) or valor_presente(dados.get("agenda_digital"))
    if score >= 70 and tem_diretor and tem_contato and tem_tecnologia:
        return "Completa"
    return "Parcial"


def step_5_score(resultado, houve_erro_fatal, logger):
    logger.info("STEP 5", "Cálculo do score iniciado.")
    score = calcular_score_proesc(resultado)
    status = definir_status(resultado, score, houve_erro_fatal)
    logger.info("STEP 5", f"Score {score} e status {status}.")
    return score, status


def erro_coluna_inexistente(excecao):
    texto = texto_seguro(excecao)
    padroes = [
        r"Could not find the '([^']+)' column",
        r'column "([^"]+)" does not exist',
        r"column ([A-Za-z0-9_]+) does not exist",
        r"PGRST204.*?'([^']+)'",
    ]
    for padrao in padroes:
        match = re.search(padrao, texto, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)
    return ""


def inserir_supabase_resiliente(tabela, payload, supabase, logger, step):
    payload_atual = dict(payload)
    removidas = []
    ultimo_erro = None
    tentativa_transiente = 0
    ajustes_schema = 0
    limite_ajustes_schema = len(payload_atual) + 5
    while tentativa_transiente < MAX_TENTATIVAS and ajustes_schema <= limite_ajustes_schema:
        try:
            resposta = supabase.table(tabela).insert(payload_atual).execute()
            dados = lista_segura(obter_atributo(resposta, "data", []))
            logger.info(step, f"Salvo no banco na tentativa {tentativa_transiente + 1}.")
            return dados, removidas
        except Exception as exc:
            ultimo_erro = exc
            coluna = erro_coluna_inexistente(exc)
            if coluna and coluna in payload_atual:
                removidas.append(coluna)
                payload_atual.pop(coluna, None)
                ajustes_schema += 1
                logger.alerta(step, f"Coluna não disponível no Supabase: {coluna}. Salvamento continuará sem essa coluna.")
                continue
            logger.erro(step, f"Erro ao salvar no Supabase na tentativa {tentativa_transiente + 1}.", exc)
            if tentativa_transiente < len(DELAY_RETRY):
                espera = DELAY_RETRY[tentativa_transiente]
                logger.alerta(step, f"Retry de banco em {espera} segundos.")
                time.sleep(espera)
            tentativa_transiente += 1
    raise RuntimeError(f"Falha definitiva ao salvar em {tabela}: {resumo_erro(ultimo_erro)}")


def montar_observacoes_com_auditoria(observacoes, auditoria):
    resumo = {
        "tempo_gasto_segundos": auditoria.get("tempo_gasto_segundos"),
        "erro": auditoria.get("erro", ""),
        "modelos_utilizados": auditoria.get("modelos_utilizados", []),
        "score": auditoria.get("score"),
        "status": auditoria.get("status"),
        "fontes": [fonte.get("url", "") for fonte in auditoria.get("fontes", []) if isinstance(fonte, dict)],
    }
    return juntar_observacoes(observacoes, f"AUDITORIA: {serializar_json(resumo)}")


def step_6_salvar(supabase, payload, auditoria, logger):
    logger.info("STEP 6", "Salvamento iniciado.")
    payload_completo = dict(payload)
    payload_completo["fontes"] = auditoria.get("fontes", [])
    payload_completo["tempo_gasto"] = auditoria.get("tempo_gasto_segundos", 0)
    payload_completo["erro"] = auditoria.get("erro", "")
    payload_completo["modelo_utilizado"] = ", ".join(auditoria.get("modelos_utilizados", []))
    payload_completo["auditoria"] = auditoria
    payload_completo["observacoes"] = montar_observacoes_com_auditoria(payload_completo.get("observacoes", ""), auditoria)
    try:
        dados, removidas = inserir_supabase_resiliente(
            "leads_enriquecidos",
            payload_completo,
            supabase,
            logger,
            "STEP 6",
        )
        return {"salvo": True, "dados": dados, "colunas_removidas": removidas, "erro": ""}
    except Exception as exc:
        logger.erro("STEP 6", "Não foi possível salvar a escola após todos os retries.", exc)
        return {"salvo": False, "dados": [], "colunas_removidas": [], "erro": resumo_erro(exc)}


def pipeline_ldr(nome, municipio, uf, telefone_inep, rodada_id, supabase, modelos_busca, modelos_analise, callback_log=None):
    inicio = time.perf_counter()
    logger = PipelineLogger(normalizar_nome(nome), callback_log)
    modelos_usados = []
    erros_pipeline = []
    houve_erro_fatal = False
    dados_norm = step_0_normalizacao(nome, municipio, uf, telefone_inep, logger)
    pesquisa = step_1_pesquisa(dados_norm, modelos_busca, logger)
    modelos_usados.extend(pesquisa["modelos_usados"])
    erros_pipeline.extend(pesquisa["erros"])
    if not pesquisa["pesquisa_sucesso"]:
        houve_erro_fatal = True
    identidade = step_2_validacao_identidade(dados_norm, pesquisa, modelos_analise, logger)
    if identidade.get("modelo_usado"):
        modelos_usados.append(identidade["modelo_usado"])
    if identidade.get("erro"):
        erros_pipeline.append(f"STEP 2: {identidade['erro']}")
    extracao = step_3_extracao(dados_norm, pesquisa, identidade, modelos_analise, logger)
    if extracao.get("modelo_usado"):
        modelos_usados.append(extracao["modelo_usado"])
    if extracao.get("erro"):
        erros_pipeline.append(f"STEP 3: {extracao['erro']}")
    tecnologia = step_4_tecnologia(dados_norm, pesquisa, identidade, extracao, modelos_analise, logger)
    if tecnologia.get("modelo_usado"):
        modelos_usados.append(tecnologia["modelo_usado"])
    if tecnologia.get("erro"):
        erros_pipeline.append(f"STEP 4: {tecnologia['erro']}")
    resultado = {
        "cnpj": identidade["cnpj"],
        "razao_social": identidade["razao_social"],
        "diretor": extracao["diretor"],
        "telefone_alternativo": extracao["telefone_alternativo"],
        "email": extracao["email"],
        "site": identidade["site"],
        "sge_atual": tecnologia["sge_atual"],
        "agenda_digital": tecnologia["agenda_digital"],
        "observacoes": juntar_observacoes(
            extracao["observacoes"],
            tecnologia["observacoes_tecnologia"],
            identidade["justificativa"] if not identidade["identidade_confirmada"] else "",
        ),
    }
    score, status = step_5_score(resultado, houve_erro_fatal, logger)
    tempo_gasto = round(time.perf_counter() - inicio, 2)
    erro_consolidado = " | ".join(dict.fromkeys(erro for erro in erros_pipeline if valor_presente(erro)))
    auditoria = {
        "fontes": pesquisa["fontes"],
        "tempo_gasto_segundos": tempo_gasto,
        "erro": limitar_texto(erro_consolidado, 10000),
        "modelos_utilizados": modelos_unicos(*modelos_usados),
        "score": score,
        "status": status,
        "identidade_confirmada": identidade["identidade_confirmada"],
        "conflitos": {
            "identidade": identidade.get("conflitos", []),
            "extracao": extracao.get("conflitos", []),
            "tecnologia": tecnologia.get("conflitos", []),
        },
        "selecoes": {
            "identidade": identidade.get("selecoes", {}),
            "extracao": extracao.get("selecoes", {}),
            "tecnologia": tecnologia.get("selecoes", {}),
        },
        "logs": logger.registros,
        "finalizado_em": agora_iso(),
    }
    payload = {
        "rodada_id": rodada_id,
        "nome_escola": identidade["nome_oficial"] if valor_presente(identidade["nome_oficial"]) else dados_norm["nome_escola"],
        "municipio": dados_norm["municipio"],
        "uf": dados_norm["uf"],
        "telefone_inep": dados_norm["telefone_inep"],
        "status": status,
        "confianca": score,
        **resultado,
    }
    salvamento = step_6_salvar(supabase, payload, auditoria, logger)
    if not salvamento["salvo"]:
        auditoria["erro_salvamento"] = salvamento["erro"]
    return {
        "resultado": resultado,
        "payload": payload,
        "auditoria": auditoria,
        "salvamento": salvamento,
        "score": score,
        "status": status,
    }


def obter_celula(row, indice, padrao=""):
    try:
        return row.iloc[indice]
    except Exception:
        return padrao


def lista_opcoes_coluna(df, indice):
    if indice >= len(df.columns):
        return []
    valores = []
    for valor in df.iloc[:, indice].dropna().tolist():
        texto = texto_seguro(valor)
        if texto and texto not in valores:
            valores.append(texto)
    return sorted(valores)


def criar_rodada(supabase, nome_arquivo, total_leads, user_email):
    payload = {
        "nome_arquivo": limitar_texto(nome_arquivo or "Remessa v4", 255),
        "total_leads": int(total_leads),
        "usuario_email": user_email,
    }
    ultimo_erro = None
    for tentativa in range(MAX_TENTATIVAS):
        try:
            resposta = supabase.table("rodadas").insert(payload).execute()
            dados = lista_segura(obter_atributo(resposta, "data", []))
            if not dados or "id" not in dados[0]:
                raise RuntimeError("Supabase não retornou o ID da rodada")
            return dados[0]["id"]
        except Exception as exc:
            ultimo_erro = exc
            if tentativa < len(DELAY_RETRY):
                time.sleep(DELAY_RETRY[tentativa])
    raise RuntimeError(f"Não foi possível criar a rodada: {resumo_erro(ultimo_erro)}")


def buscar_rodadas(supabase, user_email):
    resposta = (
        supabase.table("rodadas")
        .select("*")
        .eq("usuario_email", user_email)
        .order("created_at", desc=True)
        .execute()
    )
    return lista_segura(obter_atributo(resposta, "data", []))


def buscar_leads_rodada(supabase, rodada_id):
    resposta = (
        supabase.table("leads_enriquecidos")
        .select("*")
        .eq("rodada_id", rodada_id)
        .order("confianca", desc=True)
        .execute()
    )
    return lista_segura(obter_atributo(resposta, "data", []))


st.title("🌿 Agente LDR Enterprise - Proesc v4")

erro_inicializacao = ""
supabase = None
try:
    supabase_url = obter_segredo("SUPABASE_URL", obrigatorio=True)
    supabase_key = obter_segredo("SUPABASE_KEY", obrigatorio=True)
    gemini_api_key = obter_segredo("GEMINI_API_KEY", obrigatorio=True)
    supabase = inicializar_servicos(supabase_url, supabase_key, gemini_api_key)
except Exception as exc:
    erro_inicializacao = resumo_erro(exc)

modelo_busca_principal = obter_segredo("GEMINI_SEARCH_MODEL", "gemini-3.5-flash")
modelo_analise_principal = obter_segredo("GEMINI_ANALYSIS_MODEL", "gemini-2.5-pro")
modelos_busca = modelos_unicos(modelo_busca_principal, "gemini-2.5-flash")
modelos_analise = modelos_unicos(modelo_analise_principal, "gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash")

user_email = st.sidebar.text_input("E-mail BDR:", value="thales@proesc.com")
if not user_email:
    st.stop()

if erro_inicializacao:
    st.error(f"Erro de conexão: {erro_inicializacao}")
    st.stop()

tab1, tab2, tab3, tab4 = st.tabs(["📂 Upload", "🎯 Filtros", "🚀 Processar", "📜 Auditoria"])

with tab1:
    arquivo = st.file_uploader("Arraste sua planilha INEP aqui", type="xlsx")
    if arquivo:
        try:
            df_upload = pd.read_excel(arquivo)
            if df_upload.empty:
                st.error("A planilha está vazia.")
            elif len(df_upload.columns) < 13:
                st.error("A planilha não possui a estrutura mínima esperada do INEP.")
            else:
                st.session_state["df_raw"] = df_upload
                st.session_state["nome_arquivo"] = arquivo.name
                st.success("Planilha carregada!")
        except Exception as exc:
            st.error(f"Erro ao ler a planilha: {resumo_erro(exc)}")

with tab2:
    if "df_raw" in st.session_state:
        df = st.session_state["df_raw"].copy()
        try:
            if len(df.columns) > 14:
                mascara_processada = df.iloc[:, 14].astype(str).str.lower().str.contains(r"sim|yes|true|1", na=False)
                df = df[~mascara_processada].copy()
            c1, c2, c3 = st.columns(3)
            opcoes_uf = lista_opcoes_coluna(df, 3)
            opcoes_porte = lista_opcoes_coluna(df, 12)
            with c1:
                f_uf = st.selectbox("UF", ["Todos"] + opcoes_uf)
            with c2:
                f_porte = st.selectbox("Porte", ["Todos"] + opcoes_porte)
            with c3:
                limite = st.number_input("Tamanho do Lote", min_value=1, max_value=100, value=3, step=1)
            if f_uf != "Todos":
                df = df[df.iloc[:, 3].astype(str) == f_uf]
            if f_porte != "Todos":
                df = df[df.iloc[:, 12].astype(str) == f_porte]
            st.session_state["df_final"] = df.head(int(limite)).copy()
            st.metric("Escolas prontas", len(st.session_state["df_final"]))
        except Exception as exc:
            st.error(f"Erro ao aplicar os filtros: {resumo_erro(exc)}")
    else:
        st.info("Carregue uma planilha na aba Upload.")

with tab3:
    if "df_final" in st.session_state:
        df_p = st.session_state["df_final"]
        if df_p.empty:
            st.warning("Nenhuma escola disponível com os filtros atuais.")
        if st.button("🚀 INICIAR CICLO DE INTELIGÊNCIA", disabled=df_p.empty):
            try:
                rid = criar_rodada(
                    supabase,
                    st.session_state.get("nome_arquivo", "Remessa v4"),
                    len(df_p),
                    user_email,
                )
            except Exception as exc:
                st.error(resumo_erro(exc))
                rid = None
            if rid is not None:
                progresso = st.progress(0)
                status_txt = st.empty()
                log_placeholder = st.empty()
                linhas_log = []
                sucessos = 0
                falhas_salvamento = 0

                def atualizar_log(registro):
                    horario = registro["timestamp"][11:19]
                    linha = f"{horario} | {registro['nivel']} | {registro['step']} | {registro['mensagem']}"
                    if registro.get("detalhe"):
                        linha += f" | {registro['detalhe']}"
                    linhas_log.append(linha)
                    log_placeholder.code("\n".join(linhas_log[-250:]), language="text")

                for posicao, (_, row) in enumerate(df_p.iterrows(), start=1):
                    nome = obter_celula(row, 1)
                    municipio = obter_celula(row, 4)
                    uf = obter_celula(row, 3)
                    telefone_inep = obter_celula(row, 8)
                    nome_exibicao = normalizar_nome(nome)
                    status_txt.info(f"Analisando: **{nome_exibicao}**")
                    try:
                        retorno = pipeline_ldr(
                            nome=nome,
                            municipio=municipio,
                            uf=uf,
                            telefone_inep=telefone_inep,
                            rodada_id=rid,
                            supabase=supabase,
                            modelos_busca=modelos_busca,
                            modelos_analise=modelos_analise,
                            callback_log=atualizar_log,
                        )
                        if retorno["salvamento"]["salvo"]:
                            sucessos += 1
                        else:
                            falhas_salvamento += 1
                            st.error(f"{nome_exibicao}: {retorno['salvamento']['erro']}")
                    except Exception as exc:
                        falhas_salvamento += 1
                        detalhe = limitar_texto(traceback.format_exc(), 6000)
                        atualizar_log(
                            {
                                "timestamp": agora_iso(),
                                "nivel": "ERRO",
                                "step": "PIPELINE",
                                "mensagem": f"Erro não tratado em {nome_exibicao}; próxima escola será processada.",
                                "detalhe": detalhe,
                            }
                        )
                        payload_erro = {
                            "rodada_id": rid,
                            "nome_escola": nome_exibicao,
                            "municipio": normalizar_municipio(municipio),
                            "uf": normalizar_uf(uf),
                            "telefone_inep": normalizar_telefone(telefone_inep),
                            "status": "Erro",
                            "confianca": 0,
                            "cnpj": VALOR_NAO_IDENTIFICADO,
                            "razao_social": VALOR_NAO_IDENTIFICADO,
                            "diretor": VALOR_NAO_IDENTIFICADO,
                            "telefone_alternativo": VALOR_NAO_IDENTIFICADO,
                            "email": VALOR_NAO_IDENTIFICADO,
                            "site": VALOR_NAO_IDENTIFICADO,
                            "sge_atual": VALOR_NAO_IDENTIFICADO,
                            "agenda_digital": VALOR_NAO_IDENTIFICADO,
                            "observacoes": f"Erro não tratado no pipeline: {resumo_erro(exc)}",
                        }
                        auditoria_erro = {
                            "fontes": [],
                            "tempo_gasto_segundos": 0,
                            "erro": resumo_erro(exc),
                            "modelos_utilizados": [],
                            "score": 0,
                            "status": "Erro",
                            "logs": linhas_log[-50:],
                            "traceback": detalhe,
                            "finalizado_em": agora_iso(),
                        }
                        logger_fallback = PipelineLogger(nome_exibicao, atualizar_log)
                        salvamento_erro = step_6_salvar(supabase, payload_erro, auditoria_erro, logger_fallback)
                        if salvamento_erro["salvo"]:
                            sucessos += 1
                            falhas_salvamento -= 1
                        else:
                            st.error(f"{nome_exibicao}: erro no pipeline e no salvamento: {salvamento_erro['erro']}")
                    progresso.progress(posicao / len(df_p))
                status_txt.empty()
                if falhas_salvamento == 0:
                    st.success(f"✅ Rodada finalizada! {sucessos} escolas salvas.")
                else:
                    st.warning(f"Rodada finalizada com {sucessos} escolas salvas e {falhas_salvamento} falhas de salvamento.")
    else:
        st.info("Configure o lote na aba Filtros.")

with tab4:
    st.subheader("Auditoria de Rodadas")
    try:
        rodadas_db = buscar_rodadas(supabase, user_email)
        if not rodadas_db:
            st.info("Nenhuma rodada encontrada para este usuário.")
        for rodada in rodadas_db:
            criado_em = texto_seguro(rodada.get("created_at", ""))
            data_exibicao = criado_em[:16] if criado_em else "Sem data"
            total_leads = rodada.get("total_leads", 0)
            with st.expander(f"📁 {data_exibicao} | {total_leads} leads"):
                try:
                    leads_db = buscar_leads_rodada(supabase, rodada.get("id"))
                    if leads_db:
                        res_df = pd.DataFrame(leads_db)
                        cols = [
                            "confianca",
                            "status",
                            "nome_escola",
                            "sge_atual",
                            "agenda_digital",
                            "diretor",
                            "telefone_alternativo",
                            "email",
                            "cnpj",
                            "site",
                            "tempo_gasto",
                            "modelo_utilizado",
                            "erro",
                        ]
                        cols_existentes = [coluna for coluna in cols if coluna in res_df.columns]
                        st.dataframe(res_df[cols_existentes], use_container_width=True)
                        st.download_button(
                            "📥 Baixar CSV",
                            res_df.to_csv(index=False).encode("utf-8-sig"),
                            f"ldr_{rodada.get('id')}.csv",
                            mime="text/csv",
                            key=f"download_{rodada.get('id')}",
                        )
                    else:
                        st.info("Nenhum lead salvo nesta rodada.")
                except Exception as exc:
                    st.error(f"Erro ao carregar os leads da rodada: {resumo_erro(exc)}")
    except Exception as exc:
        st.error(f"Erro ao carregar auditoria: {resumo_erro(exc)}")

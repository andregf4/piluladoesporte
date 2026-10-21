#!/usr/bin/env python3
"""
Coletor gratuito de notícias de atletas brasileiros (modalidades olímpicas).

Fluxo:
  Google News RSS (por modalidade e por site-fonte) + páginas HTML opcionais
  -> filtro por palavras-chave -> extração (data, atleta, competição, modalidade)
  -> remoção de duplicados -> Google Sheets

Dois modos de saída:
  - Google Sheets: defina SHEET_ID (+ GOOGLE_CREDENTIALS)
  - CSV (sem Google Cloud): não defina SHEET_ID; grava em noticias.csv

Variáveis de ambiente:
  SHEET_ID            (opcional) ID da planilha (trecho da URL entre /d/ e /edit)
  GOOGLE_CREDENTIALS  conteúdo do JSON da conta de serviço (ou use credentials.json)
  LOOKBACK_DAYS       (opcional) quantos dias para trás considerar. Padrão: 2
"""
import csv
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import gspread
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials

# --------------------------------------------------------------------------
# CONFIGURAÇÃO
# --------------------------------------------------------------------------
TZ = ZoneInfo("America/Sao_Paulo")
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "2"))
SHEET_ID = os.getenv("SHEET_ID", "")
ABA_NOTICIAS = "Fila de notícias"
ABA_ATLETAS = "Atletas"  # opcional: coluna A = nome, coluna B = modalidade
ARQ_NOTICIAS = "noticias.csv"  # usado quando SHEET_ID não está definido (modo CSV)
ARQ_ATLETAS = "atletas.csv"    # opcional no modo CSV: colunas nome,modalidade
MIN_SCORE = 3
USER_AGENT = "Mozilla/5.0 (compatible; AtletasBRBot/1.0)"

CABECALHO = [
    "Data do fato", "Atleta", "Competição", "Modalidade", "Resultado (título)",
    "Fonte", "Link", "Relevância", "Status", "Coletado em",
]

# Modalidade -> palavras-chave (sem acento, minúsculas)
MODALIDADES = {
    "Atletismo": ["atletismo", "maratona", "salto com vara", "salto em distancia", "salto triplo",
                  "arremesso de peso", "lancamento de dardo", "lancamento de disco", "100m", "200m",
                  "400m", "800m", "1500m", "5000m", "10000m", "decatlo", "heptatlo", "marcha atletica"],
    "Natação": ["natacao", "nadador", "nadadora", "piscina olimpica"],
    "Maratona aquática": ["maratona aquatica", "aguas abertas"],
    "Saltos ornamentais": ["saltos ornamentais", "salto ornamental", "trampolim", "plataforma 10m"],
    "Nado artístico": ["nado artistico", "nado sincronizado"],
    "Polo aquático": ["polo aquatico"],
    "Judô": ["judo", "judoca"],
    "Ginástica": ["ginastica artistica", "ginastica ritmica", "ginastica de trampolim", "ginasta", "ginastica"],
    "Vôlei de praia": ["volei de praia"],
    "Vôlei": ["volei", "voleibol", "superliga"],
    "Futebol": ["futebol"],
    "Basquete": ["basquete", "basquetebol"],
    "Handebol": ["handebol"],
    "Rúgbi": ["rugbi", "rugby"],
    "Hóquei": ["hoquei"],
    "Surfe": ["surfe", "surfista", "wsl"],
    "Skate": ["skate", "skatista"],
    "Escalada": ["escalada esportiva", "escalador", "escaladora"],
    "Breaking": ["breaking", "breakdance"],
    "Boxe": ["boxe", "boxeador", "boxeadora", "pugilista"],
    "Luta": ["luta olimpica", "wrestling", "luta livre", "greco-romana"],
    "Taekwondo": ["taekwondo"],
    "Karatê": ["karate"],
    "Esgrima": ["esgrima", "esgrimista"],
    "Remo": ["remo", "remador", "remadora"],
    "Canoagem": ["canoagem", "canoista", "slalom"],
    "Ciclismo": ["ciclismo", "ciclista", "bmx", "mountain bike", "ciclismo de pista", "ciclismo de estrada", "uci"],
    "Tênis": ["tenis", "tenista", "atp", "wta", "roland garros", "wimbledon", "aberto"],
    "Tênis de mesa": ["tenis de mesa", "smash", "wtt"],
    "Badminton": ["badminton"],
    "Levantamento de peso": ["levantamento de peso", "halterofilismo", "halterofilista"],
    "Hipismo": ["hipismo", "adestramento", "concurso completo"],
    "Tiro esportivo": ["tiro esportivo", "tiro ao prato", "atirador", "atiradora"],
    "Tiro com arco": ["tiro com arco", "arqueiro", "arqueira"],
    "Triatlo": ["triatlo", "triatleta"],
    "Pentatlo moderno": ["pentatlo moderno"],
    "Vela": ["vela olimpica", "velejador", "velejadora", "iatismo", "windsurf", "kitefoil"],
    "Golfe": ["golfe", "golfista"],
}
# Modalidades de alto ruído: só valem se o texto citar contexto olímpico/seleção.
EXIGE_CONTEXTO = {
    "Futebol": ["olimpic", "olimpiada", "pre-olimpico", "selecao feminina", "selecao olimpica"],
    "Basquete": ["olimpic", "olimpiada", "pre-olimpico", "selecao"],
}

COMPETICOES = [
    ("Jogos Olímpicos", ["jogos olimpicos", "olimpiada", "olimpiadas", "olimpico", "paris 2024", "los angeles 2028", "la 2028"]),
    ("Pré-Olímpico", ["pre-olimpico", "pre olimpico"]),
    ("Paralimpíadas", ["paralimpiada", "paralimpiadas", "jogos paralimpicos"]),
    ("Mundial", ["mundial", "campeonato mundial", "world championships", "world championship"]),
    ("Jogos Pan-Americanos", ["pan-americano", "pan americano", "jogos pan", "pan-americanos"]),
    ("Copa do Mundo", ["copa do mundo", "world cup"]),
    ("Diamond League", ["diamond league", "liga dos diamantes"]),
    ("Liga das Nações", ["liga das nacoes", "nations league"]),
    ("Grand Slam", ["grand slam", "roland garros", "wimbledon", "us open", "aberto da australia"]),
    ("Circuito ATP/WTA", ["atp", "wta", "challenger"]),
    ("Circuito Mundial de Surfe", ["circuito mundial de surfe", "wsl", "world surf league"]),
    ("Grand Prix", ["grand prix"]),
    ("Sul-Americano", ["sul-americano", "sul americano", "sul-americanos"]),
    ("Campeonato Brasileiro", ["campeonato brasileiro de", "troféu brasil", "trofeu brasil"]),
    ("Tour de France / Giro", ["tour de france", "giro d'italia", "vuelta"]),
]

# Palavras que indicam "resultado expressivo"
RESULTADO_RE = re.compile(
    r"\b(ouro|prata|bronze|medalha\w*|campea\w*|campeao|campeoes|titulo|recorde\w*|"
    r"vence\w*|venceu|conquista\w*|garante\w*|vaga|classifica\w*|podio|bicampea\w*|"
    r"tricampea\w*|final|finalista|supera\w*|bate\w*)\b"
)

# Palavras que NÃO podem fazer parte do nome de um atleta (heurística)
NAO_NOME = {
    "brasil", "brasileiro", "brasileira", "brasileiros", "brasileiras", "ouro", "prata", "bronze",
    "mundial", "olimpiada", "olimpiadas", "olimpico", "olimpicos", "paris", "pan", "copa", "liga",
    "campeonato", "veja", "confira", "saiba", "como", "quem", "onde", "tudo", "sobre", "jogos",
    "equipe", "selecao", "time", "atleta", "atletas", "cob", "cbat", "cbda", "cbv", "cbj", "cbg",
    "grand", "slam", "tour", "world", "diamond", "challenger", "circuito", "etapa", "final",
    "janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro",
    "outubro", "novembro", "dezembro", "estados", "unidos", "franca", "japao", "china", "italia",
    "espanha", "alemanha", "argentina", "portugal", "sao", "paulo", "rio", "janeiro", "globo",
    "esporte", "noticias", "hoje", "ontem", "semana", "mulheres", "homens", "feminino", "masculino",
    "fim", "jejum", "primeira", "primeiro", "ultima", "ultimo", "nova", "novo", "ao", "vivo",
}
PARTICULAS = {"da", "de", "do", "das", "dos", "e"}

# Fontes via Google News (filtro site:) -> cobre o item 1 sem depender de RSS próprio
SITES_FONTE = [
    "ge.globo.com", "cob.org.br", "olympics.com", "worldathletics.org", "worldaquatics.com",
    "agenciabrasil.ebc.com.br", "espn.com.br", "lance.com.br", "olimpiadatododia.com.br",
    "cbat.org.br", "cbda.org.br", "cbv.com.br", "cbj.com.br", "cbg.org.br",
]

# Páginas HTML sem RSS (opcional). Verifique a URL antes de usar.
# Formato: (nome_fonte, url, prefixo_para_links_relativos)
PAGINAS_HTML = [
    # ("COB", "https://www.cob.org.br/pt/noticias", "https://www.cob.org.br"),
]


# --------------------------------------------------------------------------
# UTILITÁRIOS
# --------------------------------------------------------------------------
def norm(txt: str) -> str:
    """minúsculas, sem acento, espaços normalizados."""
    txt = unicodedata.normalize("NFD", txt or "")
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", txt.lower()).strip()


def contem(texto_norm: str, palavra: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(palavra)}(?!\w)", texto_norm) is not None


def google_news_url(query: str) -> str:
    return ("https://news.google.com/rss/search?q=" + quote_plus(query)
            + "&hl=pt-BR&gl=BR&ceid=BR:pt-419")


def baixar_feed(url: str):
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return feedparser.parse(r.content)


# --------------------------------------------------------------------------
# EXTRAÇÃO
# --------------------------------------------------------------------------
def detectar_modalidade(texto_norm: str, fallback: str = "") -> str:
    melhor, tam = "", 0
    for mod, palavras in MODALIDADES.items():
        for p in palavras:
            if contem(texto_norm, p) and len(p) > tam:
                if mod in EXIGE_CONTEXTO and not any(c in texto_norm for c in EXIGE_CONTEXTO[mod]):
                    continue
                melhor, tam = mod, len(p)
    return melhor or fallback


def detectar_competicao(texto_norm: str) -> str:
    for nome, palavras in COMPETICOES:
        if any(contem(texto_norm, norm(p)) for p in palavras):
            return nome
    return ""


def detectar_atleta(titulo: str, atletas_conhecidos: dict):
    """Retorna (nome, modalidade_conhecida, confiavel)."""
    t_norm = norm(titulo)
    for nome_norm, (nome, mod) in atletas_conhecidos.items():
        if contem(t_norm, nome_norm):
            return nome, mod, True

    # Heurística: sequência de palavras capitalizadas no título
    palavras = re.findall(r"[\wÀ-ÿ'’-]+", titulo)
    seqs, atual = [], []
    for p in palavras:
        if p[0].isupper() and not p.isupper() or (p.isupper() and len(p) > 3):
            atual.append(p)
        elif p.lower() in PARTICULAS and atual:
            atual.append(p)
        else:
            if atual:
                seqs.append(atual)
            atual = []
    if atual:
        seqs.append(atual)

    for seq in seqs:
        while seq and (norm(seq[0]) in NAO_NOME or norm(seq[0]) in PARTICULAS):
            seq = seq[1:]
        while seq and (norm(seq[-1]) in NAO_NOME or norm(seq[-1]) in PARTICULAS):
            seq = seq[:-1]
        caps = [w for w in seq if w.lower() not in PARTICULAS]
        if len(caps) >= 2 and not any(norm(w) in NAO_NOME for w in caps):
            return " ".join(seq), "", False
    return "", "", False


def avaliar(titulo: str, resumo: str, atleta_ok: bool, competicao: str, atleta: str):
    """Pontuação simples 0-5 e flag de relevância."""
    t = norm(f"{titulo} {resumo}")
    tem_br = "brasil" in t or atleta_ok
    tem_resultado = RESULTADO_RE.search(norm(titulo)) is not None
    score = 0
    score += 2 if tem_resultado else 0
    score += 1 if tem_br else 0
    score += 1 if competicao else 0
    score += 1 if atleta else 0
    # Sem menção a Brasil/atleta conhecido, exige mais sinais (atleta + competição + resultado)
    limite = MIN_SCORE if tem_br else MIN_SCORE + 1
    return (tem_resultado and score >= limite), score


# --------------------------------------------------------------------------
# COLETA
# --------------------------------------------------------------------------
def montar_consultas():
    termos = "(ouro OR prata OR bronze OR medalha OR título OR recorde OR campeão OR campeã OR classificação)"
    consultas = []
    for mod in MODALIDADES:
        consultas.append((f'"{mod}" (brasileiro OR brasileira) {termos} when:{LOOKBACK_DAYS}d', mod))
    for site in SITES_FONTE:
        consultas.append((f"site:{site} (brasileiro OR brasileira) {termos} when:{LOOKBACK_DAYS}d", ""))
    return consultas


def coletar_google_news():
    itens = []
    for query, _mod in montar_consultas():
        try:
            feed = baixar_feed(google_news_url(query))
        except Exception as e:  # noqa: BLE001
            print(f"[aviso] falha em '{query[:50]}...': {e}")
            continue
        for e in feed.entries:
            fonte = (e.get("source") or {}).get("title", "")
            titulo = e.get("title", "")
            if " - " in titulo:
                titulo, _, sufixo = titulo.rpartition(" - ")
                fonte = fonte or sufixo
            pub = e.get("published_parsed")
            data = (datetime(*pub[:6], tzinfo=timezone.utc).astimezone(TZ)
                    if pub else datetime.now(TZ))
            resumo = BeautifulSoup(e.get("summary", ""), "html.parser").get_text(" ")
            itens.append({"titulo": titulo.strip(), "resumo": resumo, "link": e.get("link", ""),
                          "fonte": fonte, "data": data})
        time.sleep(1)  # gentileza com o servidor
    return itens


def coletar_html():
    itens = []
    for nome, url, base in PAGINAS_HTML:
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:  # noqa: BLE001
            print(f"[aviso] falha em {nome}: {e}")
            continue
        for a in soup.find_all("a", href=True):
            titulo = a.get_text(" ", strip=True)
            if len(titulo) < 30:
                continue
            link = a["href"] if a["href"].startswith("http") else base + a["href"]
            itens.append({"titulo": titulo, "resumo": "", "link": link, "fonte": nome,
                          "data": datetime.now(TZ)})
    return itens


# --------------------------------------------------------------------------
# GOOGLE SHEETS
# --------------------------------------------------------------------------
def conectar_planilha():
    escopos = ["https://www.googleapis.com/auth/spreadsheets"]
    cred_json = os.getenv("GOOGLE_CREDENTIALS")
    if cred_json:
        creds = Credentials.from_service_account_info(json.loads(cred_json), scopes=escopos)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=escopos)
    return gspread.authorize(creds).open_by_key(SHEET_ID)


def obter_aba(planilha, nome, linhas=2000, colunas=12):
    try:
        return planilha.worksheet(nome)
    except gspread.WorksheetNotFound:
        return planilha.add_worksheet(title=nome, rows=linhas, cols=colunas)


def carregar_atletas(planilha) -> dict:
    try:
        valores = planilha.worksheet(ABA_ATLETAS).get_all_values()[1:]
    except gspread.WorksheetNotFound:
        return {}
    return {norm(l[0]): (l[0].strip(), l[1].strip() if len(l) > 1 else "")
            for l in valores if l and l[0].strip() and len(norm(l[0])) > 5}


def carregar_atletas_csv() -> dict:
    if not os.path.exists(ARQ_ATLETAS):
        return {}
    with open(ARQ_ATLETAS, encoding="utf-8") as f:
        linhas = list(csv.reader(f))[1:]
    return {norm(l[0]): (l[0].strip(), l[1].strip() if len(l) > 1 else "")
            for l in linhas if l and l[0].strip() and len(norm(l[0])) > 5}


def ler_csv() -> list:
    if not os.path.exists(ARQ_NOTICIAS):
        return []
    with open(ARQ_NOTICIAS, encoding="utf-8") as f:
        return list(csv.reader(f))


def gravar_csv(existentes: list, novas: list):
    with open(ARQ_NOTICIAS, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([CABECALHO] + novas + existentes[1:])  # mais novas no topo


def manter_apenas_datas(rows: list, datas_validas: set) -> list:
    """Mantém somente linhas cuja coluna 'Data do fato' esteja entre as datas válidas."""
    if not rows:
        return [CABECALHO]

    cabecalho = rows[0]

    filtradas = [
        linha
        for linha in rows[1:]
        if linha and linha[0].strip() in datas_validas
    ]

    return [cabecalho] + filtradas


# --------------------------------------------------------------------------
# PRINCIPAL
# --------------------------------------------------------------------------
def main():
    # Data local da execução do workflow (fuso de São Paulo).
    agora = datetime.now(TZ)

    data_consulta_date = agora.date()
    data_anterior_date = data_consulta_date - timedelta(days=1)

    data_consulta = data_consulta_date.strftime("%d/%m/%Y")
    data_anterior = data_anterior_date.strftime("%d/%m/%Y")

    datas_validas = {data_consulta, data_anterior}
    aba = None
    if SHEET_ID:
        planilha = conectar_planilha()
        aba = obter_aba(planilha, ABA_NOTICIAS)
        atletas = carregar_atletas(planilha)
        existentes = aba.get_all_values() or [CABECALHO]
    else:
        atletas = carregar_atletas_csv()
        existentes = ler_csv() or [CABECALHO]

    # Remove registros anteriores a ontem.
    # O CSV/planilha mantém somente notícias de hoje e de ontem.
    existentes = manter_apenas_datas(existentes, datas_validas)

    links_vistos = {l[6] for l in existentes[1:] if len(l) > 6}
    titulos_vistos = {norm(l[4]) for l in existentes[1:] if len(l) > 4}
    eventos_vistos = {(l[0], norm(l[1]), l[2]) for l in existentes[1:] if len(l) > 2 and l[1]}

    bruto = coletar_google_news() + coletar_html()
    print(f"Itens coletados (bruto): {len(bruto)}")

    novas = []
    for it in bruto:
        # Aceita somente notícias publicadas hoje ou ontem,
        # considerando o fuso horário de São Paulo.
        data_noticia = it["data"].astimezone(TZ).date()

        if data_noticia not in {data_consulta_date, data_anterior_date} or it["link"] in links_vistos:
            continue
        titulo_norm = norm(it["titulo"])
        if titulo_norm in titulos_vistos:
            continue

        atleta, mod_atleta, confiavel = detectar_atleta(it["titulo"], atletas)
        texto = norm(f"{it['titulo']} {it['resumo']}")
        modalidade = detectar_modalidade(texto, fallback=mod_atleta)
        competicao = detectar_competicao(texto)
        relevante, score = avaliar(it["titulo"], it["resumo"], confiavel, competicao, atleta)
        if not relevante or not modalidade:
            continue

        data_fmt = data_noticia.strftime("%d/%m/%Y")
        chave_evento = (data_fmt, norm(atleta), competicao)
        if atleta and chave_evento in eventos_vistos:
            continue

        atleta_exib = atleta if (confiavel or not atleta) else f"{atleta} (?)"
        novas.append([
            data_fmt, atleta_exib, competicao, modalidade, it["titulo"], it["fonte"],
            it["link"], score, "Novo", datetime.now(TZ).strftime("%d/%m/%Y %H:%M"),
        ])
        links_vistos.add(it["link"])
        titulos_vistos.add(titulo_norm)
        eventos_vistos.add(chave_evento)

    novas.sort(key=lambda r: (r[0][-4:], r[0][3:5], r[0][:2], r[7]), reverse=True)
    if aba is not None:
        # Regrava a aba para excluir notícias de datas anteriores e manter apenas as de hoje.
        linhas_finais = [CABECALHO] + novas + existentes[1:]
        aba.clear()
        aba.update(linhas_finais, value_input_option="USER_ENTERED")
    else:
        gravar_csv(existentes, novas)
    print(f"Novas linhas adicionadas: {len(novas)}")


if __name__ == "__main__":
    main()

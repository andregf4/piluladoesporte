#!/usr/bin/env python3
"""
Coletor gratuito de notícias de atletas brasileiros (modalidades olímpicas).

Objetivo:
    Coletar somente notícias sobre atletas brasileiros envolvidos
    em conquistas de competições internacionais.

Fluxo:
    Google News RSS (por modalidade e por site-fonte)
    + páginas HTML opcionais
    -> filtro de competição internacional
    -> filtro de conquista/resultado
    -> filtro de atleta
    -> extração (data, atleta, competição, modalidade)
    -> remoção de duplicados
    -> Google Sheets / CSV

Dois modos de saída:
    - Google Sheets: defina SHEET_ID (+ GOOGLE_CREDENTIALS)
    - CSV (sem Google Cloud): não defina SHEET_ID; grava em noticias.csv

Variáveis de ambiente:
    SHEET_ID            (opcional) ID da planilha
    GOOGLE_CREDENTIALS  conteúdo do JSON da conta de serviço
    LOOKBACK_DAYS       quantos dias para trás considerar. Padrão: 2
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
ABA_ATLETAS = "Atletas"
ARQ_NOTICIAS = "noticias.csv"
ARQ_ATLETAS = "atletas.csv"

MIN_SCORE = 3
USER_AGENT = "Mozilla/5.0 (compatible; AtletasBRBot/1.0)"


CABECALHO = [
    "Data do fato",
    "Atleta",
    "Competição",
    "Modalidade",
    "Resultado (título)",
    "Fonte",
    "Link",
    "Relevância",
    "Status",
    "Coletado em",
]


# --------------------------------------------------------------------------
# MODALIDADES
# --------------------------------------------------------------------------

MODALIDADES = {
    "Atletismo": [
        "atletismo", "maratona", "salto com vara", "salto em distancia",
        "salto triplo", "arremesso de peso", "lancamento de dardo",
        "lancamento de disco", "100m", "200m", "400m", "800m",
        "1500m", "5000m", "10000m", "decatlo", "heptatlo",
        "marcha atletica"
    ],

    "Natação": [
        "natacao", "nadador", "nadadora", "piscina olimpica"
    ],

    "Maratona aquática": [
        "maratona aquatica", "aguas abertas"
    ],

    "Saltos ornamentais": [
        "saltos ornamentais", "salto ornamental", "trampolim",
        "plataforma 10m"
    ],

    "Nado artístico": [
        "nado artistico", "nado sincronizado"
    ],

    "Polo aquático": [
        "polo aquatico"
    ],

    "Judô": [
        "judo", "judoca"
    ],

    "Ginástica": [
        "ginastica artistica", "ginastica ritmica",
        "ginastica de trampolim", "ginasta", "ginastica"
    ],

    "Vôlei de praia": [
        "volei de praia"
    ],

    "Vôlei": [
        "volei", "voleibol"
    ],

    "Futebol": [
        "futebol"
    ],

    "Basquete": [
        "basquete", "basquetebol"
    ],

    "Handebol": [
        "handebol"
    ],

    "Rúgbi": [
        "rugbi", "rugby"
    ],

    "Hóquei": [
        "hoquei"
    ],

    "Surfe": [
        "surfe", "surfista", "wsl"
    ],

    "Skate": [
        "skate", "skatista"
    ],

    "Escalada": [
        "escalada esportiva", "escalador", "escaladora"
    ],

    "Breaking": [
        "breaking", "breakdance"
    ],

    "Boxe": [
        "boxe", "boxeador", "boxeadora", "pugilista"
    ],

    "Luta": [
        "luta olimpica", "wrestling", "luta livre", "greco-romana"
    ],

    "Taekwondo": [
        "taekwondo"
    ],

    "Karatê": [
        "karate"
    ],

    "Esgrima": [
        "esgrima", "esgrimista"
    ],

    "Remo": [
        "remo", "remador", "remadora"
    ],

    "Canoagem": [
        "canoagem", "canoista", "slalom"
    ],

    "Ciclismo": [
        "ciclismo", "ciclista", "bmx", "mountain bike",
        "ciclismo de pista", "ciclismo de estrada", "uci"
    ],

    "Tênis": [
        "tenis", "tenista", "atp", "wta", "roland garros",
        "wimbledon"
    ],

    "Tênis de mesa": [
        "tenis de mesa", "smash", "wtt"
    ],

    "Badminton": [
        "badminton"
    ],

    "Levantamento de peso": [
        "levantamento de peso", "halterofilismo", "halterofilista"
    ],

    "Hipismo": [
        "hipismo", "adestramento", "concurso completo"
    ],

    "Tiro esportivo": [
        "tiro esportivo", "tiro ao prato", "atirador", "atiradora"
    ],

    "Tiro com arco": [
        "tiro com arco", "arqueiro", "arqueira"
    ],

    "Triatlo": [
        "triatlo", "triatleta"
    ],

    "Pentatlo moderno": [
        "pentatlo moderno"
    ],

    "Vela": [
        "vela olimpica", "velejador", "velejadora",
        "iatismo", "windsurf", "kitefoil"
    ],

    "Golfe": [
        "golfe", "golfista"
    ],
}


# Modalidades que podem gerar muito ruído.
# Nelas, exigimos também contexto de seleção/competição internacional.
EXIGE_CONTEXTO = {
    "Futebol": [
        "selecao", "brasil", "copa do mundo",
        "mundial", "world cup", "libertadores"
    ],

    "Basquete": [
        "selecao", "brasil", "mundial",
        "world cup", "olimpiada"
    ],

    "Vôlei": [
        "selecao", "brasil", "mundial",
        "nations league", "liga das nacoes"
    ],
}


# --------------------------------------------------------------------------
# COMPETIÇÕES INTERNACIONAIS
# --------------------------------------------------------------------------

# SOMENTE competições internacionais.
#
# Não colocar aqui campeonatos nacionais.
#
# A ordem é importante: competições mais específicas aparecem primeiro.

COMPETICOES = [
    (
        "Jogos Olímpicos",
        [
            "jogos olimpicos",
            "olimpiada",
            "olimpiadas",
            "olimpico",
            "paris 2024",
            "los angeles 2028",
            "la 2028",
        ],
    ),

    (
        "Paralimpíadas",
        [
            "paralimpiada",
            "paralimpiadas",
            "jogos paralimpicos",
        ],
    ),

    (
        "Mundial",
        [
            "campeonato mundial",
            "mundial",
            "world championships",
            "world championship",
            "world cup",
        ],
    ),

    (
        "Copa do Mundo",
        [
            "copa do mundo",
            "world cup",
        ],
    ),

    (
        "Jogos Pan-Americanos",
        [
            "pan-americano",
            "pan americano",
            "jogos pan",
            "pan-americanos",
        ],
    ),

    (
        "Sul-Americano",
        [
            "sul-americano",
            "sul americano",
            "sul-americanos",
        ],
    ),

    (
        "Diamond League",
        [
            "diamond league",
            "liga dos diamantes",
        ],
    ),

    (
        "Liga das Nações",
        [
            "liga das nacoes",
            "nations league",
        ],
    ),

    (
        "Grand Slam",
        [
            "grand slam",
            "roland garros",
            "wimbledon",
            "us open",
            "aberto da australia",
            "australian open",
        ],
    ),

    (
        "Circuito ATP/WTA",
        [
            "atp",
            "wta",
            "challenger",
        ],
    ),

    (
        "Circuito Mundial de Surfe",
        [
            "circuito mundial de surfe",
            "wsl",
            "world surf league",
        ],
    ),

    (
        "Grand Prix",
        [
            "grand prix",
        ],
    ),

    (
        "Tour de France / Giro",
        [
            "tour de france",
            "giro d'italia",
            "giro ditalia",
            "vuelta",
        ],
    ),
]


# --------------------------------------------------------------------------
# COMPETIÇÕES / EVENTOS NACIONAIS A SEREM BLOQUEADOS
# --------------------------------------------------------------------------

# Mesmo que uma notícia contenha "brasileiro", "campeão" ou "medalha",
# ela será descartada se houver forte indicação de competição nacional.

TERMOS_NACIONAIS = [
    "campeonato brasileiro",
    "brasileirao",
    "brasileirão",
    "serie a",
    "serie b",
    "serie c",
    "serie d",
    "copa do brasil",
    "supercopa do brasil",
    "copa verde",
    "copa do nordeste",
    "trofeu brasil",
    "troféu brasil",
    "superliga",
    "superliga masculina",
    "superliga feminina",
    "novo basquete brasil",
    "nbb",
    "paulistao",
    "paulistão",
    "campeonato paulista",
    "cariocao",
    "cariocão",
    "campeonato carioca",
    "gaucho",
    "gaúcho",
    "campeonato gaucho",
    "campeonato gaúcho",
    "mineiro",
    "campeonato mineiro",
    "paranaense",
    "campeonato paranaense",
    "pernambucano",
    "campeonato pernambucano",
    "baiano",
    "campeonato baiano",
    "cearense",
    "campeonato cearense",
    "capixaba",
    "campeonato capixaba",
    "brasiliense",
    "campeonato brasiliense",
    "copa paulista",
    "copa rio",
    "copa do estado",
]


# --------------------------------------------------------------------------
# TERMOS QUE INDICAM NOTÍCIA CENTRADA EM CLUBE
# --------------------------------------------------------------------------

# A presença de um clube na notícia não é suficiente para descartá-la,
# pois um atleta brasileiro pode atuar por um clube estrangeiro.
#
# O bloqueio acontece principalmente quando a notícia não apresenta
# um atleta brasileiro identificável e o foco aparente é o clube.

TERMOS_CLUBE = [
    "clube",
    "time",
    "equipe",
    "diretoria",
    "presidente",
    "tecnico",
    "técnico",
    "treinador",
    "contratacao",
    "contratação",
    "mercado da bola",
    "janela de transferencias",
    "janela de transferências",
    "elenco",
    "torcida",
    "estadio",
    "estádio",
    "uniforme",
    "patrocinador",
    "folha salarial",
]


# --------------------------------------------------------------------------
# RESULTADOS QUE REPRESENTAM CONQUISTA
# --------------------------------------------------------------------------

# Aqui evitamos considerar como "conquista" uma simples classificação,
# participação, chegada à final etc.

CONQUISTA_RE = re.compile(
    r"\b("
    r"ouro|"
    r"prata|"
    r"bronze|"
    r"medalha\w*|"
    r"campea\w*|"
    r"campeao|"
    r"campeã|"
    r"campeoes|"
    r"campeãs|"
    r"titulo|"
    r"título|"
    r"titulos|"
    r"títulos|"
    r"conquista\w*|"
    r"vence\w*|"
    r"venceu|"
    r"vitoria|"
    r"vitória|"
    r"podio|"
    r"pódio|"
    r"bicampea\w*|"
    r"tricampea\w*|"
    r"recorde\w*"
    r")\b"
)


# --------------------------------------------------------------------------
# PALAVRAS QUE NÃO PODEM FAZER PARTE DO NOME DE UM ATLETA
# --------------------------------------------------------------------------

NAO_NOME = {
    "brasil", "brasileiro", "brasileira", "brasileiros", "brasileiras",
    "ouro", "prata", "bronze", "mundial", "olimpiada", "olimpiadas",
    "olimpico", "olimpicos", "paris", "pan", "copa", "liga",
    "campeonato", "veja", "confira", "saiba", "como", "quem", "onde",
    "tudo", "sobre", "jogos", "equipe", "selecao", "time", "atleta",
    "atletas", "cob", "cbat", "cbda", "cbv", "cbj", "cbg", "grand",
    "slam", "tour", "world", "diamond", "challenger", "circuito",
    "etapa", "final", "janeiro", "fevereiro", "marco", "abril", "maio",
    "junho", "julho", "agosto", "setembro", "outubro", "novembro",
    "dezembro", "estados", "unidos", "franca", "japao", "china",
    "italia", "espanha", "alemanha", "argentina", "portugal", "sao",
    "paulo", "rio", "globo", "esporte", "noticias", "hoje", "ontem",
    "semana", "mulheres", "homens", "feminino", "masculino", "fim",
    "jejum", "primeira", "primeiro", "ultima", "ultimo", "nova", "novo",
    "ao", "vivo",
}


PARTICULAS = {"da", "de", "do", "das", "dos", "e"}


# --------------------------------------------------------------------------
# FONTES
# --------------------------------------------------------------------------

SITES_FONTE = [
    "ge.globo.com",
    "cob.org.br",
    "olympics.com",
    "worldathletics.org",
    "worldaquatics.com",
    "agenciabrasil.ebc.com.br",
    "espn.com.br",
    "lance.com.br",
    "olimpiadatododia.com.br",
    "cbat.org.br",
    "cbda.org.br",
    "cbv.com.br",
    "cbj.com.br",
    "cbg.org.br",
]


PAGINAS_HTML = [
    # ("COB", "https://www.cob.org.br/pt/noticias", "https://www.cob.org.br"),
]


# --------------------------------------------------------------------------
# UTILITÁRIOS
# --------------------------------------------------------------------------

def norm(txt: str) -> str:
    """Minúsculas, sem acento e com espaços normalizados."""
    txt = unicodedata.normalize("NFD", txt or "")
    txt = "".join(
        c for c in txt
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", txt.lower()).strip()


def contem(texto_norm: str, palavra: str) -> bool:
    return (
        re.search(
            rf"(?<!\w){re.escape(norm(palavra))}(?!\w)",
            texto_norm,
        )
        is not None
    )


def google_news_url(query: str) -> str:
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(query)
        + "&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    )


def baixar_feed(url: str):
    r = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    r.raise_for_status()
    return feedparser.parse(r.content)


# --------------------------------------------------------------------------
# DETECÇÃO
# --------------------------------------------------------------------------

def detectar_modalidade(
    texto_norm: str,
    fallback: str = "",
) -> str:

    melhor = ""
    tam = 0

    for mod, palavras in MODALIDADES.items():
        for p in palavras:

            if not contem(texto_norm, p):
                continue

            if (
                mod in EXIGE_CONTEXTO
                and not any(
                    contem(texto_norm, c)
                    for c in EXIGE_CONTEXTO[mod]
                )
            ):
                continue

            if len(norm(p)) > tam:
                melhor = mod
                tam = len(norm(p))

    return melhor or fallback


def detectar_competicao(texto_norm: str) -> str:

    for nome, palavras in COMPETICOES:

        if any(
            contem(texto_norm, p)
            for p in palavras
        ):
            return nome

    return ""


def eh_competicao_nacional(texto_norm: str) -> bool:

    return any(
        contem(texto_norm, termo)
        for termo in TERMOS_NACIONAIS
    )


def tem_conquista(titulo: str, resumo: str) -> bool:

    texto = norm(f"{titulo} {resumo}")

    return CONQUISTA_RE.search(texto) is not None


def detectar_atleta(
    titulo: str,
    atletas_conhecidos: dict,
):
    """
    Retorna:
        (nome, modalidade_conhecida, confiavel)

    Primeiro procura atletas cadastrados na aba Atletas.
    Se não encontrar, tenta identificar uma sequência de nomes
    próprios no título.
    """

    t_norm = norm(titulo)

    # --------------------------------------------------------------
    # 1. Atletas conhecidos
    # --------------------------------------------------------------

    for nome_norm, (nome, mod) in atletas_conhecidos.items():

        if contem(t_norm, nome_norm):
            return nome, mod, True

    # --------------------------------------------------------------
    # 2. Heurística para nomes não cadastrados
    # --------------------------------------------------------------

    palavras = re.findall(
        r"[\wÀ-ÿ'’-]+",
        titulo,
    )

    seqs = []
    atual = []

    for p in palavras:

        if (
            (p[0].isupper() and not p.isupper())
            or (p.isupper() and len(p) > 3)
        ):
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

        while seq and (
            norm(seq[0]) in NAO_NOME
            or norm(seq[0]) in PARTICULAS
        ):
            seq = seq[1:]

        while seq and (
            norm(seq[-1]) in NAO_NOME
            or norm(seq[-1]) in PARTICULAS
        ):
            seq = seq[:-1]

        caps = [
            w for w in seq
            if w.lower() not in PARTICULAS
        ]

        if (
            len(caps) >= 2
            and not any(
                norm(w) in NAO_NOME
                for w in caps
            )
        ):
            return " ".join(seq), "", False

    return "", "", False


# --------------------------------------------------------------------------
# FILTRO DE NOTÍCIA CENTRADA EM CLUBE
# --------------------------------------------------------------------------

def parece_noticia_de_clube(
    titulo: str,
    resumo: str,
    atleta: str,
    atleta_confiavel: bool,
) -> bool:

    texto = norm(f"{titulo} {resumo}")
    titulo_norm = norm(titulo)

    # Se encontramos explicitamente um atleta cadastrado,
    # damos prioridade à notícia do atleta.
    if atleta_confiavel:
        return False

    # Sem atleta confiável, verificamos se a notícia parece
    # estar centrada em clube/equipe.
    quantidade_clube = sum(
        1 for termo in TERMOS_CLUBE
        if contem(texto, termo)
    )

    # Se o título fala explicitamente de clube/equipe,
    # mas não identificamos um atleta conhecido, descartamos.
    foco_clube_titulo = any(
        contem(titulo_norm, termo)
        for termo in TERMOS_CLUBE
    )

    if foco_clube_titulo:
        return True

    # Muitos termos relacionados a clube também indicam
    # que provavelmente a notícia não é sobre uma conquista
    # individual do atleta.
    if quantidade_clube >= 3:
        return True

    return False


# --------------------------------------------------------------------------
# AVALIAÇÃO
# --------------------------------------------------------------------------

def avaliar(
    titulo: str,
    resumo: str,
    atleta_ok: bool,
    competicao: str,
    atleta: str,
):
    """
    Pontuação simples de 0 a 5.

    Critérios:
        +2 conquista
        +1 brasileiro / atleta conhecido
        +1 competição internacional
        +1 atleta identificado

    A notícia precisa necessariamente conter:
        - conquista
        - competição internacional
        - atleta
    """

    t = norm(f"{titulo} {resumo}")

    tem_brasileiro = (
        "brasil" in t
        or "brasileiro" in t
        or "brasileira" in t
        or atleta_ok
    )

    conquista = tem_conquista(
        titulo,
        resumo,
    )

    score = 0

    score += 2 if conquista else 0
    score += 1 if tem_brasileiro else 0
    score += 1 if competicao else 0
    score += 1 if atleta else 0

    # Para ser relevante, exigimos os três elementos principais:
    # conquista + competição + atleta.
    relevante = (
        conquista
        and bool(competicao)
        and bool(atleta)
        and score >= MIN_SCORE
    )

    return relevante, score


# --------------------------------------------------------------------------
# CONSULTAS GOOGLE NEWS
# --------------------------------------------------------------------------

def montar_consultas():

    # Termos de conquista.
    termos_conquista = (
        "(ouro OR prata OR bronze OR medalha OR "
        "título OR campeã OR campeão OR conquista OR "
        "venceu OR vitória OR pódio OR recorde)"
    )

    # Termos que reforçam a natureza internacional.
    termos_internacionais = (
        "(mundial OR internacional OR world OR "
        "olímpico OR olimpíada OR "
        "pan-americano OR sul-americano OR "
        "grand slam OR diamond league OR "
        "nations league OR world cup OR "
        "roland garros OR wimbledon OR "
        "us open OR australian open OR "
        "tour de france OR giro d'italia OR vuelta)"
    )

    consultas = []

    # --------------------------------------------------------------
    # Consultas por modalidade
    # --------------------------------------------------------------

    for mod in MODALIDADES:

        consulta = (
            f'"{mod}" '
            f'(brasileiro OR brasileira OR Brasil) '
            f'{termos_internacionais} '
            f'{termos_conquista} '
            f'when:{LOOKBACK_DAYS}d'
        )

        consultas.append(
            (consulta, mod)
        )

    # --------------------------------------------------------------
    # Consultas por fonte
    # --------------------------------------------------------------

    for site in SITES_FONTE:

        consulta = (
            f"site:{site} "
            f'(brasileiro OR brasileira OR Brasil) '
            f'{termos_internacionais} '
            f'{termos_conquista} '
            f'when:{LOOKBACK_DAYS}d'
        )

        consultas.append(
            (consulta, "")
        )

    return consultas


# --------------------------------------------------------------------------
# COLETA GOOGLE NEWS
# --------------------------------------------------------------------------

def coletar_google_news():

    itens = []

    for query, _mod in montar_consultas():

        try:
            feed = baixar_feed(
                google_news_url(query)
            )

        except Exception as e:
            print(
                f"[aviso] falha em "
                f"'{query[:80]}...': {e}"
            )
            continue

        for e in feed.entries:

            fonte = (
                e.get("source") or {}
            ).get("title", "")

            titulo = e.get(
                "title",
                "",
            )

            if " - " in titulo:

                titulo, _, sufixo = (
                    titulo.rpartition(" - ")
                )

                fonte = fonte or sufixo

            pub = e.get(
                "published_parsed"
            )

            if pub:

                data = (
                    datetime(
                        *pub[:6],
                        tzinfo=timezone.utc,
                    ).astimezone(TZ)
                )

            else:

                data = datetime.now(TZ)

            resumo = BeautifulSoup(
                e.get("summary", ""),
                "html.parser",
            ).get_text(" ")

            itens.append(
                {
                    "titulo": titulo.strip(),
                    "resumo": resumo,
                    "link": e.get("link", ""),
                    "fonte": fonte,
                    "data": data,
                }
            )

        time.sleep(1)

    return itens


# --------------------------------------------------------------------------
# COLETA HTML
# --------------------------------------------------------------------------

def coletar_html():

    itens = []

    for nome, url, base in PAGINAS_HTML:

        try:

            r = requests.get(
                url,
                headers={
                    "User-Agent": USER_AGENT
                },
                timeout=30,
            )

            r.raise_for_status()

            soup = BeautifulSoup(
                r.text,
                "html.parser",
            )

        except Exception as e:

            print(
                f"[aviso] falha em {nome}: {e}"
            )

            continue

        for a in soup.find_all(
            "a",
            href=True,
        ):

            titulo = a.get_text(
                " ",
                strip=True,
            )

            if len(titulo) < 30:
                continue

            link = (
                a["href"]
                if a["href"].startswith("http")
                else base + a["href"]
            )

            itens.append(
                {
                    "titulo": titulo,
                    "resumo": "",
                    "link": link,
                    "fonte": nome,
                    "data": datetime.now(TZ),
                }
            )

    return itens


# --------------------------------------------------------------------------
# GOOGLE SHEETS
# --------------------------------------------------------------------------

def conectar_planilha():

    escopos = [
        "https://www.googleapis.com/auth/spreadsheets"
    ]

    cred_json = os.getenv(
        "GOOGLE_CREDENTIALS"
    )

    if cred_json:

        creds = (
            Credentials.from_service_account_info(
                json.loads(cred_json),
                scopes=escopos,
            )
        )

    else:

        creds = (
            Credentials.from_service_account_file(
                "credentials.json",
                scopes=escopos,
            )
        )

    return gspread.authorize(
        creds
    ).open_by_key(SHEET_ID)


def obter_aba(
    planilha,
    nome,
    linhas=2000,
    colunas=12,
):

    try:
        return planilha.worksheet(nome)

    except gspread.WorksheetNotFound:

        return planilha.add_worksheet(
            title=nome,
            rows=linhas,
            cols=colunas,
        )


def carregar_atletas(planilha) -> dict:

    try:

        valores = (
            planilha
            .worksheet(ABA_ATLETAS)
            .get_all_values()[1:]
        )

    except gspread.WorksheetNotFound:

        return {}

    return {
        norm(l[0]): (
            l[0].strip(),
            l[1].strip()
            if len(l) > 1
            else "",
        )
        for l in valores
        if (
            l
            and l[0].strip()
            and len(norm(l[0])) > 5
        )
    }


def carregar_atletas_csv() -> dict:

    if not os.path.exists(
        ARQ_ATLETAS
    ):
        return {}

    with open(
        ARQ_ATLETAS,
        encoding="utf-8",
    ) as f:

        linhas = list(
            csv.reader(f)
        )[1:]

    return {
        norm(l[0]): (
            l[0].strip(),
            l[1].strip()
            if len(l) > 1
            else "",
        )
        for l in linhas
        if (
            l
            and l[0].strip()
            and len(norm(l[0])) > 5
        )
    }


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

def ler_csv() -> list:

    if not os.path.exists(
        ARQ_NOTICIAS
    ):
        return []

    with open(
        ARQ_NOTICIAS,
        encoding="utf-8",
    ) as f:

        return list(
            csv.reader(f)
        )


def gravar_csv(
    existentes: list,
    novas: list,
):

    with open(
        ARQ_NOTICIAS,
        "w",
        encoding="utf-8",
        newline="",
    ) as f:

        csv.writer(f).writerows(
            [CABECALHO]
            + novas
            + existentes[1:]
        )


def manter_apenas_datas(
    rows: list,
    datas_validas: set,
) -> list:

    if not rows:
        return [CABECALHO]

    cabecalho = rows[0]

    filtradas = [
        linha
        for linha in rows[1:]
        if (
            linha
            and linha[0].strip()
            in datas_validas
        )
    ]

    return [
        cabecalho
    ] + filtradas


# --------------------------------------------------------------------------
# PRINCIPAL
# --------------------------------------------------------------------------

def main():

    # Data local da execução do workflow.
    agora = datetime.now(TZ)

    data_consulta_date = agora.date()

    data_anterior_date = (
        data_consulta_date
        - timedelta(days=1)
    )

    data_consulta = (
        data_consulta_date.strftime(
            "%d/%m/%Y"
        )
    )

    data_anterior = (
        data_anterior_date.strftime(
            "%d/%m/%Y"
        )
    )

    datas_validas = {
        data_consulta,
        data_anterior,
    }

    aba = None

    # --------------------------------------------------------------
    # Carrega atletas e notícias existentes
    # --------------------------------------------------------------

    if SHEET_ID:

        planilha = conectar_planilha()

        aba = obter_aba(
            planilha,
            ABA_NOTICIAS,
        )

        atletas = carregar_atletas(
            planilha
        )

        existentes = (
            aba.get_all_values()
            or [CABECALHO]
        )

    else:

        atletas = carregar_atletas_csv()

        existentes = (
            ler_csv()
            or [CABECALHO]
        )

    # --------------------------------------------------------------
    # Mantém somente notícias de hoje e ontem
    # --------------------------------------------------------------

    existentes = manter_apenas_datas(
        existentes,
        datas_validas,
    )

    links_vistos = {
        l[6]
        for l in existentes[1:]
        if len(l) > 6
    }

    titulos_vistos = {
        norm(l[4])
        for l in existentes[1:]
        if len(l) > 4
    }

    eventos_vistos = {
        (
            l[0],
            norm(l[1]),
            l[2],
        )
        for l in existentes[1:]
        if (
            len(l) > 2
            and l[1]
        )
    }

    # --------------------------------------------------------------
    # COLETA
    # --------------------------------------------------------------

    bruto = (
        coletar_google_news()
        + coletar_html()
    )

    print(
        f"Itens coletados (bruto): "
        f"{len(bruto)}"
    )

    novas = []

    # --------------------------------------------------------------
    # FILTROS
    # --------------------------------------------------------------

    for it in bruto:

        # ----------------------------------------------------------
        # Data
        # ----------------------------------------------------------

        data_noticia = (
            it["data"]
            .astimezone(TZ)
            .date()
        )

        if data_noticia not in {
            data_consulta_date,
            data_anterior_date,
        }:

            continue

        # ----------------------------------------------------------
        # Duplicidade por link
        # ----------------------------------------------------------

        if it["link"] in links_vistos:
            continue

        titulo_norm = norm(
            it["titulo"]
        )

        # ----------------------------------------------------------
        # Duplicidade por título
        # ----------------------------------------------------------

        if titulo_norm in titulos_vistos:
            continue

        # ----------------------------------------------------------
        # Texto completo
        # ----------------------------------------------------------

        texto = norm(
            f"{it['titulo']} "
            f"{it['resumo']}"
        )

        # ----------------------------------------------------------
        # 1. Rejeita competições nacionais
        # ----------------------------------------------------------

        if eh_competicao_nacional(
            texto
        ):

            print(
                "[descartada - competição nacional] "
                f"{it['titulo']}"
            )

            continue

        # ----------------------------------------------------------
        # 2. Identifica competição internacional
        # ----------------------------------------------------------

        competicao = detectar_competicao(
            texto
        )

        if not competicao:

            print(
                "[descartada - sem competição internacional] "
                f"{it['titulo']}"
            )

            continue

        # ----------------------------------------------------------
        # 3. Identifica atleta
        # ----------------------------------------------------------

        atleta, mod_atleta, confiavel = (
            detectar_atleta(
                it["titulo"],
                atletas,
            )
        )

        if not atleta:

            print(
                "[descartada - sem atleta] "
                f"{it['titulo']}"
            )

            continue

        # ----------------------------------------------------------
        # 4. Rejeita notícias claramente centradas em clubes
        # ----------------------------------------------------------

        if parece_noticia_de_clube(
            it["titulo"],
            it["resumo"],
            atleta,
            confiavel,
        ):

            print(
                "[descartada - foco em clube] "
                f"{it['titulo']}"
            )

            continue

        # ----------------------------------------------------------
        # 5. Identifica modalidade
        # ----------------------------------------------------------

        modalidade = detectar_modalidade(
            texto,
            fallback=mod_atleta,
        )

        if not modalidade:

            print(
                "[descartada - sem modalidade] "
                f"{it['titulo']}"
            )

            continue

        # ----------------------------------------------------------
        # 6. Exige conquista efetiva
        # ----------------------------------------------------------

        if not tem_conquista(
            it["titulo"],
            it["resumo"],
        ):

            print(
                "[descartada - sem conquista] "
                f"{it['titulo']}"
            )

            continue

        # ----------------------------------------------------------
        # 7. Avaliação final
        # ----------------------------------------------------------

        relevante, score = avaliar(
            it["titulo"],
            it["resumo"],
            confiavel,
            competicao,
            atleta,
        )

        if not relevante:

            print(
                "[descartada - baixa relevância] "
                f"{it['titulo']}"
            )

            continue

        # ----------------------------------------------------------
        # 8. Evita repetição do mesmo evento
        # ----------------------------------------------------------

        data_fmt = (
            data_noticia.strftime(
                "%d/%m/%Y"
            )
        )

        chave_evento = (
            data_fmt,
            norm(atleta),
            competicao,
        )

        if (
            atleta
            and chave_evento
            in eventos_vistos
        ):

            continue

        # ----------------------------------------------------------
        # 9. Prepara registro
        # ----------------------------------------------------------

        atleta_exib = (
            atleta
            if confiavel
            else f"{atleta} (?)"
        )

        novas.append(
            [
                data_fmt,
                atleta_exib,
                competicao,
                modalidade,
                it["titulo"],
                it["fonte"],
                it["link"],
                score,
                "Novo",
                datetime.now(TZ).strftime(
                    "%d/%m/%Y %H:%M"
                ),
            ]
        )

        links_vistos.add(
            it["link"]
        )

        titulos_vistos.add(
            titulo_norm
        )

        eventos_vistos.add(
            chave_evento
        )

    # --------------------------------------------------------------
    # Ordenação
    # --------------------------------------------------------------

    novas.sort(
        key=lambda r: (
            r[0][-4:],
            r[0][3:5],
            r[0][:2],
            r[7],
        ),
        reverse=True,
    )

    # --------------------------------------------------------------
    # SALVAMENTO
    # --------------------------------------------------------------

    if aba is not None:

        linhas_finais = (
            [CABECALHO]
            + novas
            + existentes[1:]
        )

        aba.clear()

        aba.update(
            linhas_finais,
            value_input_option="USER_ENTERED",
        )

    else:

        gravar_csv(
            existentes,
            novas,
        )

    print(
        f"Novas linhas adicionadas: "
        f"{len(novas)}"
    )


if __name__ == "__main__":
    main()

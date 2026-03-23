import streamlit as st
import pandas as pd
import numpy as np
import unicodedata
import re
from io import BytesIO

st.set_page_config(page_title="GolEmNúmeros Dashboard", layout="wide")

# =========================================
# SESSION STATE
# =========================================
if "melhores_ganhos_mercado" not in st.session_state:
    st.session_state["melhores_ganhos_mercado"] = pd.DataFrame()

if "melhores_combos_2_mercado" not in st.session_state:
    st.session_state["melhores_combos_2_mercado"] = pd.DataFrame()

if "melhores_combos_3_mercado" not in st.session_state:
    st.session_state["melhores_combos_3_mercado"] = pd.DataFrame()

if "filtros_bt_manual" not in st.session_state:
    st.session_state["filtros_bt_manual"] = []

if "df_bt_manual" not in st.session_state:
    st.session_state["df_bt_manual"] = pd.DataFrame()

if "probs_bt_manual" not in st.session_state:
    st.session_state["probs_bt_manual"] = {}

if "resumo_bt_manual" not in st.session_state:
    st.session_state["resumo_bt_manual"] = {}

if "baseline_geral" not in st.session_state:
    st.session_state["baseline_geral"] = pd.DataFrame()

if "jogos_dia_ns_importados" not in st.session_state:
    st.session_state["jogos_dia_ns_importados"] = pd.DataFrame()

if "metodologias_grafico_filtradas" not in st.session_state:
    st.session_state["metodologias_grafico_filtradas"] = []

if "metodologia_visual_selecionada" not in st.session_state:
    st.session_state["metodologia_visual_selecionada"] = None

if "metodo_manual_digitado" not in st.session_state:
    st.session_state["metodo_manual_digitado"] = {
        "Mercado": "",
        "Tipo_Metodologia": "",
        "Variaveis": "",
        "Faixas": "",
        "Nome_Metodologia": ""
    }

# =========================================
# LINK DA PLANILHA
# =========================================
PLANILHA_ODDS = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSF5WBP5KeBr6cVbAK0yH2IJf_luqoK90gOz1fj_VlS_hoAb4E6v_awCWO-bTi28I-mWYWEeewnhmTh/pub?output=csv"

# =========================================
# CSS RESPONSIVO
# =========================================
st.markdown("""
<style>
    .stApp {
        background: linear-gradient(180deg, #03142c 0%, #02101f 100%);
        color: white;
    }

    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0a2a5a 0%, #051b38 100%);
        border-right: 1px solid rgba(255,255,255,0.08);
    }

    .block-container {
        padding-top: 2rem;
        padding-bottom: 1rem;
        max-width: 1400px;
    }

    .titulo-topo {
        font-size: 2rem;
        font-weight: 800;
        color: #ffffff;
        margin-top: 0.6rem;
        margin-bottom: 0.35rem;
        line-height: 1.2;
    }

    .subtitulo-topo {
        font-size: 1rem;
        color: #b7c9e8;
        margin-bottom: 1rem;
    }

    .caixa-selecao {
        background: linear-gradient(180deg, #0b2a59 0%, #0a2144 100%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 12px;
        padding: 10px;
        margin-top: 0.35rem;
        margin-bottom: 0.9rem;
    }

    .cards-grid {
        display: grid;
        grid-template-columns: repeat(7, minmax(110px, 1fr));
        gap: 10px;
        margin-bottom: 14px;
    }

    .card {
        background: linear-gradient(180deg, #0b2a59 0%, #0a2144 100%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 10px;
        padding: 8px 10px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.20);
        min-height: 52px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        text-align: center;
    }

    .card-title {
        font-size: 0.68rem;
        color: #d7e7ff;
        margin-bottom: 2px;
        line-height: 1.1;
        text-align: center;
    }

    .card-value {
        font-size: 1.02rem;
        font-weight: 800;
        color: white;
        line-height: 1.1;
        text-align: center;
        word-break: break-word;
    }

    .equipes-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 14px;
        align-items: start;
        margin-top: 8px;
        margin-bottom: 18px;
    }

    .painel-time {
        border-radius: 14px;
        padding: 10px 10px 6px 10px;
        box-shadow: 0 3px 12px rgba(0,0,0,0.22);
    }

    .painel-casa {
        background: linear-gradient(180deg, rgba(20,70,160,0.45) 0%, rgba(8,32,82,0.75) 100%);
        border: 1px solid rgba(100,170,255,0.28);
    }

    .painel-visit {
        background: linear-gradient(180deg, rgba(150,25,35,0.45) 0%, rgba(75,10,18,0.78) 100%);
        border: 1px solid rgba(255,120,120,0.28);
    }

    .painel-time-titulo {
        font-size: 1.18rem;
        font-weight: 800;
        margin-bottom: 10px;
        text-align: center;
    }

    .painel-casa .painel-time-titulo {
        color: #86c5ff;
    }

    .painel-visit .painel-time-titulo {
        color: #ff9fa8;
    }

    .lista-metricas-time {
        display: flex;
        flex-direction: column;
        gap: 8px;
        width: 100%;
    }

    .card-time {
        border-radius: 10px;
        padding: 9px 10px;
        min-height: 58px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.20);
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        text-align: center;
    }

    .card-time-titulo {
        font-size: 0.82rem;
        opacity: 0.98;
        margin-bottom: 4px;
        line-height: 1.15;
        text-align: center;
        width: 100%;
        font-weight: 700;
    }

    .card-time-valor {
        font-size: 1.35rem;
        font-weight: 800;
        color: white;
        line-height: 1.05;
        text-align: center;
        width: 100%;
    }

    .card-casa {
        background: linear-gradient(180deg, #12408f 0%, #0a2a5d 100%);
        border: 1px solid rgba(130,190,255,0.22);
    }

    .card-visit {
        background: linear-gradient(180deg, #8d1f2d 0%, #5a111d 100%);
        border: 1px solid rgba(255,150,150,0.22);
    }

    .secao-titulo {
        font-size: 1rem;
        font-weight: 800;
        color: white;
        margin-bottom: 0.6rem;
    }

    div[data-testid="stDataFrame"] {
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 12px;
        overflow: hidden;
    }

    .bt-box {
        background: linear-gradient(180deg, rgba(20,30,70,0.95) 0%, rgba(10,18,40,0.95) 100%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 16px;
        padding: 16px;
        margin-bottom: 14px;
    }

    @media (max-width: 1100px) {
        .cards-grid {
            grid-template-columns: repeat(4, minmax(110px, 1fr));
        }
    }

    @media (max-width: 768px) {
        .block-container {
            padding-top: 1rem;
            padding-left: 0.8rem;
            padding-right: 0.8rem;
        }

        .titulo-topo {
            font-size: 1.6rem;
            line-height: 1.15;
        }

        .subtitulo-topo {
            font-size: 0.95rem;
        }

        .caixa-selecao {
            padding: 8px;
            margin-bottom: 0.8rem;
        }

        .cards-grid {
            grid-template-columns: 1fr;
            gap: 8px;
        }

        .equipes-grid {
            grid-template-columns: 1fr;
            gap: 12px;
        }

        .painel-time {
            padding: 8px 8px 6px 8px;
        }

        .painel-time-titulo {
            font-size: 1.05rem;
            margin-bottom: 8px;
        }

        .card {
            min-height: 50px;
            padding: 8px;
        }

        .card-title {
            font-size: 0.72rem;
        }

        .card-value {
            font-size: 0.98rem;
        }

        .card-time {
            min-height: 54px;
            padding: 8px;
        }

        .card-time-titulo {
            font-size: 0.78rem;
            line-height: 1.12;
        }

        .card-time-valor {
            font-size: 1.05rem;
        }
    }
</style>
""", unsafe_allow_html=True)

# =========================================
# FUNÇÕES BÁSICAS
# =========================================
@st.cache_data(ttl=60)
def carregar_google_sheet(url_csv: str) -> pd.DataFrame:
    df = pd.read_csv(url_csv)

    colunas_essenciais = [
        "Home Team", "Visitor Team", "Country", "League", "Hour", "Status"
    ]
    cols_existentes = [c for c in colunas_essenciais if c in df.columns]
    if cols_existentes:
        df = df.dropna(subset=cols_existentes).copy()

    return df


def coluna_existe(df, coluna):
    return coluna in df.columns


def normalizar_nome_coluna(txt):
    if txt is None:
        return ""
    txt = str(txt).strip().lower()
    txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("utf-8")
    txt = txt.replace("º", "").replace("°", "")
    txt = re.sub(r"\s+", " ", txt)
    return txt


def mapa_colunas(df):
    return {normalizar_nome_coluna(c): c for c in df.columns}


def encontrar_coluna_real(df, nome_desejado):
    mp = mapa_colunas(df)
    return mp.get(normalizar_nome_coluna(nome_desejado))


def obter_valor_coluna_flex(df, nome_desejado):
    if df.empty:
        return "-"
    col_real = encontrar_coluna_real(df, nome_desejado)
    if not col_real:
        return "-"
    valor = df.iloc[0][col_real]
    if pd.isna(valor):
        return "-"
    return str(valor)


def multiselect_seguro(label, df, coluna, key=None):
    if coluna_existe(df, coluna):
        valores = sorted(df[coluna].dropna().astype(str).unique().tolist())
        return st.sidebar.multiselect(label, valores, default=valores, key=key)
    return []


def to_num_val(v):
    try:
        return float(str(v).replace("%", "").replace(",", ".").strip())
    except:
        return None


def fmt_num(v, casas=2):
    n = to_num_val(v)
    if n is None:
        return "-"
    return f"{n:.{casas}f}"


def fmt_pct(v, casas=0):
    n = to_num_val(v)
    if n is None:
        return "-"
    return f"{n:.{casas}f}%"


def media_lista(valores):
    nums = [to_num_val(v) for v in valores]
    nums = [x for x in nums if x is not None]
    if not nums:
        return "-"
    return f"{sum(nums) / len(nums):.2f}"


def to_num_series(s):
    return pd.to_numeric(
        s.astype(str)
         .str.replace("%", "", regex=False)
         .str.replace(",", ".", regex=False)
         .str.strip(),
        errors="coerce"
    )

# =========================================
# FUNÇÕES DE FILTRO E RENDER
# =========================================
def aplicar_filtros(
    df,
    pais_sel,
    liga_sel,
    mercado_odd_sel,
    odd_range_sel,
    status_sel,
    busca_partida
):
    filtrado = df.copy()

    if pais_sel and coluna_existe(filtrado, "Country"):
        filtrado = filtrado[filtrado["Country"].astype(str).isin(pais_sel)]

    if liga_sel and coluna_existe(filtrado, "League"):
        filtrado = filtrado[filtrado["League"].astype(str).isin(liga_sel)]

    if mercado_odd_sel and odd_range_sel:
        col_odd_real_filtro = encontrar_coluna_real(filtrado, mercado_odd_sel)
        if col_odd_real_filtro and col_odd_real_filtro in filtrado.columns:
            filtrado[col_odd_real_filtro] = pd.to_numeric(
                filtrado[col_odd_real_filtro],
                errors="coerce"
            )

            odd_min = min(odd_range_sel[0], odd_range_sel[1])
            odd_max = max(odd_range_sel[0], odd_range_sel[1])

            filtrado = filtrado[
                filtrado[col_odd_real_filtro].between(
                    odd_min,
                    odd_max,
                    inclusive="both"
                )
            ]

    if status_sel and coluna_existe(filtrado, "Status"):
        filtrado = filtrado[filtrado["Status"].astype(str).isin(status_sel)]

    if busca_partida:
        alvo = busca_partida.strip().lower()

        mask_home = (
            filtrado["Home Team"].astype(str).str.lower().str.contains(alvo, na=False, regex=False)
            if coluna_existe(filtrado, "Home Team") else pd.Series(False, index=filtrado.index)
        )

        mask_away = (
            filtrado["Visitor Team"].astype(str).str.lower().str.contains(alvo, na=False, regex=False)
            if coluna_existe(filtrado, "Visitor Team") else pd.Series(False, index=filtrado.index)
        )

        filtrado = filtrado[mask_home | mask_away]

    return filtrado


def render_cards(cards):
    html = '<div class="cards-grid">'
    for titulo, valor in cards:
        html += (
            f'<div class="card">'
            f'<div class="card-title">{titulo}</div>'
            f'<div class="card-value">{valor}</div>'
            f'</div>'
        )
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)


def render_cards_verticais(metricas_casa, metricas_visit, nome_casa="CASA", nome_visit="VISITANTE"):
    html = '<div class="equipes-grid">'

    html += (
        f'<div class="painel-time painel-casa">'
        f'<div class="painel-time-titulo">🏠 {nome_casa}</div>'
        f'<div class="lista-metricas-time">'
    )

    for titulo, valor in metricas_casa:
        html += (
            f'<div class="card-time card-casa">'
            f'<div class="card-time-titulo">{titulo}</div>'
            f'<div class="card-time-valor">{valor}</div>'
            f'</div>'
        )

    html += '</div></div>'

    html += (
        f'<div class="painel-time painel-visit">'
        f'<div class="painel-time-titulo">✈️ {nome_visit}</div>'
        f'<div class="lista-metricas-time">'
    )

    for titulo, valor in metricas_visit:
        html += (
            f'<div class="card-time card-visit">'
            f'<div class="card-time-titulo">{titulo}</div>'
            f'<div class="card-time-valor">{valor}</div>'
            f'</div>'
        )

    html += '</div></div>'
    html += '</div>'

    st.markdown(html, unsafe_allow_html=True)

# =========================================
# VARIÁVEIS COMBINADAS
# =========================================
def criar_variaveis_combinadas(df_base: pd.DataFrame) -> pd.DataFrame:
    df_comb = df_base.copy()

    def c(nome):
        col_real = encontrar_coluna_real(df_comb, nome)
        if col_real and col_real in df_comb.columns:
            return pd.to_numeric(df_comb[col_real], errors="coerce")
        return pd.Series(pd.NA, index=df_comb.index, dtype="Float64")

    def c_multi(*nomes):
        for nome in nomes:
            col_real = encontrar_coluna_real(df_comb, nome)
            if col_real and col_real in df_comb.columns:
                return pd.to_numeric(df_comb[col_real], errors="coerce")
        return pd.Series(pd.NA, index=df_comb.index, dtype="Float64")

    df_comb["[COMB] Produto | H2H_Win_Casa x Atual_MarcouPrimeiro_Casa"] = (
        (c("Confrontos diretos - vitórias casa") / 100) *
        (c("Quando marcam o primeiro gol e ganha o jogo casa") / 100)
    ) * 100

    df_comb["[COMB] Produto | H2H_Win_Visitante x Atual_MarcouPrimeiro_Visitante"] = (
        (c("Confrontos diretos - vitórias visitante") / 100) *
        (c("Quando marcam o primeiro gol e ganha o jogo visitante") / 100)
    ) * 100

    df_comb["[COMB] Diff_CV | Atual_Win"] = (
        c("Vitórias casa") - c("Vitórias visitante")
    )

    df_comb["[COMB] Pressao_Liquida_1T_Casa"] = (
        c("Média de chutes no gol marcados 1º tempo casa") -
        c("Média total de chutes sofridos 1º tempo casa")
    )

    df_comb["[COMB] Pressao_Liquida_1T_Visitante"] = (
        c("Média de chutes no gol marcados 1º tempo visitante") -
        c("Média total de chutes sofridos 1º tempo visitante")
    )

    df_comb["[COMB] Diff | Pressao_Liquida_1T"] = (
        df_comb["[COMB] Pressao_Liquida_1T_Casa"] -
        df_comb["[COMB] Pressao_Liquida_1T_Visitante"]
    )

    df_comb["[COMB] Soma | H2H_Win_Visitante x Atual_Win_Visitante"] = (
        c("Confrontos diretos - vitórias visitante") + c("Vitórias visitante")
    )

    df_comb["[COMB] Soma | H2H_Win_Casa x Atual_Win_Casa"] = (
        c("Confrontos diretos - vitórias casa") + c("Vitórias casa")
    )

    df_comb["[COMB] Soma Media Gols 2T"] = (
        c("Média de gols 46-60' minutos") +
        c("Média de gols 61-75' minutos") +
        c("Média de gols 76-90' minutos")
    )

    df_comb["[COMB] Soma Total Media Gols"] = (
        c("Média de gols 0-15' minutos") +
        c("Média de gols 16-30' minutos") +
        c("Média de gols 31-45' minutos") +
        c("Média de gols 46-60' minutos") +
        c("Média de gols 61-75' minutos") +
        c("Média de gols 76-90' minutos")
    )

    df_comb["[COMB] Soma Media Gols Meio"] = (
        c("Média de gols 16-30' minutos") +
        c("Média de gols 31-45' minutos") +
        c("Média de gols 46-60' minutos") +
        c("Média de gols 61-75' minutos")
    )

    df_comb["[COMB] Soma Total % Over Gols"] = (
        c("Mais de 0.5 gols 0-15'") +
        c("Mais de 0.5 gols 16-30'") +
        c("Mais de 0.5 gols 31-45'") +
        c("Mais de 0.5 gols 46-60'") +
        c("Mais de 0.5 gols 61-75'") +
        c("Mais de 0.5 gols 76-90'")
    )

    df_comb["[COMB] Diff | Gols_Sofridos_1T"] = (
        c("Média de gols sofridos primeiro tempo casa") -
        c("Média de gols sofridos primeiro tempo visitante")
    )

    df_comb["[COMB] Diff | Gols_Marcados_1T"] = (
        c("Média de gols marcados primeiro tempo casa") -
        c("Média de gols marcados primeiro tempo visitante")
    )

    df_comb["[COMB] Diff | Chutes_Sofridos_1T"] = (
        c("Média total de chutes sofridos 1º tempo casa") -
        c("Média total de chutes sofridos 1º tempo visitante")
    )

    serie_chutes_marc_casa = c_multi(
        "Média de chutes marcados 1º tempo casa",
        "Média de chutes marcados primeiro tempo casa"
    )

    serie_chutes_marc_visit = c_multi(
        "Média de chutes marcados 1º tempo visitante",
        "Média de chutes marcados primeiro tempo visitante"
    )

    if serie_chutes_marc_casa.isna().all() or serie_chutes_marc_visit.isna().all():
        serie_chutes_marc_casa = c_multi("Média de chutes no gol marcados 1º tempo casa")
        serie_chutes_marc_visit = c_multi("Média de chutes no gol marcados 1º tempo visitante")

    df_comb["[COMB] Diff | Chutes_Marcados_1T"] = (
        serie_chutes_marc_casa - serie_chutes_marc_visit
    )

    return df_comb

# =========================================
# CONFIG BASE ATIVA
# =========================================
nome_planilha = "1_Odds_Indicadores"
url_planilha = PLANILHA_ODDS
chave_base = nome_planilha

# =========================================
# CARREGAR BASES
# =========================================
try:
    df_base = carregar_google_sheet(PLANILHA_ODDS)
except Exception as e:
    st.error(f"Erro ao carregar a planilha ativa: {e}")
    st.stop()

if df_base.empty:
    st.warning("A planilha ativa está vazia.")
    st.stop()

df = df_base.copy()
df_odds = criar_variaveis_combinadas(df_base.copy())

# =========================================
# CRIAR COLUNA PARTIDA
# =========================================
if coluna_existe(df, "Home Team") and coluna_existe(df, "Visitor Team"):
    df["Partida"] = df["Home Team"].astype(str) + " x " + df["Visitor Team"].astype(str)

if coluna_existe(df_odds, "Home Team") and coluna_existe(df_odds, "Visitor Team"):
    df_odds["Partida"] = df_odds["Home Team"].astype(str) + " x " + df_odds["Visitor Team"].astype(str)

# =========================================
# SIDEBAR / NAVEGAÇÃO
# =========================================
pagina = st.sidebar.radio(
    "Área",
    ["Dashboard", "Backtest"],
    index=0
)

if pagina == "Dashboard":
    st.sidebar.markdown("## ⚽ GOL EM NÚMEROS")
    st.sidebar.markdown("## Fonte de dados")
    st.sidebar.markdown(f"**Base ativa:** {nome_planilha}")

    if st.sidebar.button("Atualizar dados"):
        st.cache_data.clear()
        st.rerun()

    st.sidebar.markdown("## Filtre os dados")

    pais_sel = multiselect_seguro("Country", df, "Country", key=f"country_{chave_base}")
    liga_sel = multiselect_seguro("League", df, "League", key=f"league_{chave_base}")

    st.sidebar.markdown("### Filtro por Odds")

    odds_colunas_disponiveis = [
        "Odds casa para vencer",
        "Odds empate",
        "Odds visitante para vencer",
        "Odds mais de 2.5",
        "Odds menos de 2.5",
        "Odds ambas equipes marcarem sim",
        "Odds ambas equipes marcarem não",
    ]

    odds_colunas_validas = [c for c in odds_colunas_disponiveis if encontrar_coluna_real(df, c)]

    mercado_odd_sel = None
    odd_range_sel = None

    if odds_colunas_validas:
        mercado_odd_sel = st.sidebar.selectbox(
            "Mercado de Odds",
            odds_colunas_validas,
            key=f"mercado_odds_{chave_base}"
        )

        col_odd_real_sidebar = encontrar_coluna_real(df, mercado_odd_sel)

        if col_odd_real_sidebar and col_odd_real_sidebar in df.columns:
            serie_odds_sidebar = pd.to_numeric(df[col_odd_real_sidebar], errors="coerce").dropna()

            if not serie_odds_sidebar.empty:
                odd_min_base = float(serie_odds_sidebar.min())
                odd_max_base = float(serie_odds_sidebar.max())

                odd_inicial = st.sidebar.number_input(
                    "Odd inicial",
                    min_value=0.0,
                    value=float(round(odd_min_base, 2)),
                    step=0.01,
                    key=f"odd_inicial_{chave_base}"
                )

                odd_final = st.sidebar.number_input(
                    "Odd final",
                    min_value=0.0,
                    value=float(round(odd_max_base, 2)),
                    step=0.01,
                    key=f"odd_final_{chave_base}"
                )

                odd_range_sel = (odd_inicial, odd_final)

    status_sel = multiselect_seguro("Status", df, "Status", key=f"status_{chave_base}")
    busca_partida = st.sidebar.text_input("Buscar partida", key=f"busca_partida_{chave_base}")

else:
    st.sidebar.markdown("## Painel do Backtest")

    pais_sel = []
    liga_sel = []
    mercado_odd_sel = None
    odd_range_sel = None
    status_sel = []
    busca_partida = ""

# =========================================
# APLICAR FILTROS NA BASE PRINCIPAL
# =========================================
df_filtrado = aplicar_filtros(
    df,
    pais_sel,
    liga_sel,
    mercado_odd_sel,
    odd_range_sel,
    status_sel,
    busca_partida
).copy()

# =========================================
# TÍTULO
# =========================================
titulo_pagina = "Dashboard" if pagina == "Dashboard" else "Backtest"

st.markdown(
    f'<div class="titulo-topo">⚽ GolEmNúmeros - {titulo_pagina}</div>',
    unsafe_allow_html=True
)

st.markdown(
    f'<div class="subtitulo-topo">Fonte atual: {nome_planilha} | Linhas carregadas: {len(df)}</div>',
    unsafe_allow_html=True
)

# =========================================
# CAMPO DE SELEÇÃO DE PARTIDA NO TOPO
# =========================================
if pagina == "Dashboard":
    if coluna_existe(df_filtrado, "Partida"):
        partidas_disponiveis = sorted(df_filtrado["Partida"].dropna().astype(str).unique())

        if len(partidas_disponiveis) > 0:
            st.markdown('<div class="caixa-selecao">', unsafe_allow_html=True)
            partida_selecionada = st.selectbox(
                f"Selecionar partida na planilha: {nome_planilha}",
                partidas_disponiveis,
                index=0,
                key=f"partida_topo_{chave_base}"
            )
            st.markdown('</div>', unsafe_allow_html=True)
            df_filtrado = df_filtrado[df_filtrado["Partida"].astype(str) == partida_selecionada].copy()
        else:
            partida_selecionada = None
    else:
        partida_selecionada = None
else:
    partida_selecionada = None

# =========================================
# DADOS DA PLANILHA
# =========================================
linha_odds_partida = pd.DataFrame()
home_nome = "CASA"
visit_nome = "VISITANTE"

if partida_selecionada is not None and coluna_existe(df_odds, "Partida"):
    linha_odds_partida = df_odds[
        df_odds["Partida"].astype(str) == str(partida_selecionada)
    ].copy()

if linha_odds_partida.empty and not df_filtrado.empty:
    linha_odds_partida = df_filtrado.copy()

if not linha_odds_partida.empty:
    if coluna_existe(linha_odds_partida, "Home Team"):
        home_nome = str(linha_odds_partida.iloc[0]["Home Team"])
    if coluna_existe(linha_odds_partida, "Visitor Team"):
        visit_nome = str(linha_odds_partida.iloc[0]["Visitor Team"])

odd_casa = obter_valor_coluna_flex(linha_odds_partida, "Odds casa para vencer")
odd_empate = obter_valor_coluna_flex(linha_odds_partida, "Odds empate")
odd_visitante = obter_valor_coluna_flex(linha_odds_partida, "Odds visitante para vencer")
odd_over25 = obter_valor_coluna_flex(linha_odds_partida, "Odds mais de 2.5")
odd_under25 = obter_valor_coluna_flex(linha_odds_partida, "Odds menos de 2.5")
odd_btts_sim = obter_valor_coluna_flex(linha_odds_partida, "Odds ambas equipes marcarem sim")
odd_btts_nao = obter_valor_coluna_flex(linha_odds_partida, "Odds ambas equipes marcarem não")

precisao_casa = obter_valor_coluna_flex(linha_odds_partida, "Precisão nos chutes no alvo casa")
precisao_visit = obter_valor_coluna_flex(linha_odds_partida, "Precisão nos chutes no alvo visitante")

chutes_gol_casa = obter_valor_coluna_flex(linha_odds_partida, "Chutes por gol casa")
chutes_gol_visit = obter_valor_coluna_flex(linha_odds_partida, "Chutes por gol visitante")

marca_primeiro_ganha_casa = obter_valor_coluna_flex(linha_odds_partida, "Quando marcam o primeiro gol e ganha o jogo casa")
marca_primeiro_ganha_visit = obter_valor_coluna_flex(linha_odds_partida, "Quando marcam o primeiro gol e ganha o jogo visitante")

sofre_primeiro_ganha_casa = obter_valor_coluna_flex(linha_odds_partida, "Quando sofre o primeiro gol e ganha o jogo casa")
sofre_primeiro_ganha_visit = obter_valor_coluna_flex(linha_odds_partida, "Quando sofre o primeiro gol e ganha o jogo visitante")

gols_marc_1t_casa = obter_valor_coluna_flex(linha_odds_partida, "Média de gols marcados primeiro tempo casa")
gols_marc_1t_visit = obter_valor_coluna_flex(linha_odds_partida, "Média de gols marcados primeiro tempo visitante")

gols_sofr_1t_casa = obter_valor_coluna_flex(linha_odds_partida, "Média de gols sofridos primeiro tempo casa")
gols_sofr_1t_visit = obter_valor_coluna_flex(linha_odds_partida, "Média de gols sofridos primeiro tempo visitante")

chutes_gol_1t_casa = obter_valor_coluna_flex(linha_odds_partida, "Média de chutes no gol marcados 1º tempo casa")
chutes_gol_1t_visit = obter_valor_coluna_flex(linha_odds_partida, "Média de chutes no gol marcados 1º tempo visitante")

chutes_sofr_1t_casa = obter_valor_coluna_flex(linha_odds_partida, "Média total de chutes sofridos 1º tempo casa")
chutes_sofr_1t_visit = obter_valor_coluna_flex(linha_odds_partida, "Média total de chutes sofridos 1º tempo visitante")

h2h_vit_casa = obter_valor_coluna_flex(linha_odds_partida, "Confrontos diretos - vitórias casa")
h2h_vit_visit = obter_valor_coluna_flex(linha_odds_partida, "Confrontos diretos - vitórias visitante")

h2h_marcou_primeiro_casa = obter_valor_coluna_flex(linha_odds_partida, "Confrontos diretos - marcou primeiro casa")
h2h_marcou_primeiro_visit = obter_valor_coluna_flex(linha_odds_partida, "Confrontos diretos - marcou primeiro visitante")

vitorias_casa = obter_valor_coluna_flex(linha_odds_partida, "Vitórias casa")
vitorias_visit = obter_valor_coluna_flex(linha_odds_partida, "Vitórias visitante")

gols_0_15 = obter_valor_coluna_flex(linha_odds_partida, "Média de gols 0-15' minutos")
gols_16_30 = obter_valor_coluna_flex(linha_odds_partida, "Média de gols 16-30' minutos")
gols_31_45 = obter_valor_coluna_flex(linha_odds_partida, "Média de gols 31-45' minutos")
gols_46_60 = obter_valor_coluna_flex(linha_odds_partida, "Média de gols 46-60' minutos")
gols_61_75 = obter_valor_coluna_flex(linha_odds_partida, "Média de gols 61-75' minutos")
gols_76_90 = obter_valor_coluna_flex(linha_odds_partida, "Média de gols 76-90' minutos")

over05_0_15 = obter_valor_coluna_flex(linha_odds_partida, "Mais de 0.5 gols 0-15'")
over05_16_30 = obter_valor_coluna_flex(linha_odds_partida, "Mais de 0.5 gols 16-30'")
over05_31_45 = obter_valor_coluna_flex(linha_odds_partida, "Mais de 0.5 gols 31-45'")
over05_46_60 = obter_valor_coluna_flex(linha_odds_partida, "Mais de 0.5 gols 46-60'")
over05_61_75 = obter_valor_coluna_flex(linha_odds_partida, "Mais de 0.5 gols 61-75'")
over05_76_90 = obter_valor_coluna_flex(linha_odds_partida, "Mais de 0.5 gols 76-90'")

# =========================================
# MÉTRICAS DERIVADAS
# =========================================
ml_potencia_ofensiva_casa = media_lista([
    gols_marc_1t_casa, chutes_gol_casa, precisao_casa,
    marca_primeiro_ganha_casa, odd_casa
])

ml_potencia_ofensiva_visit = media_lista([
    gols_marc_1t_visit, chutes_gol_visit, precisao_visit,
    marca_primeiro_ganha_visit, odd_visitante
])

ml_solidez_defensiva_casa = media_lista([
    gols_sofr_1t_casa, chutes_sofr_1t_casa,
    sofre_primeiro_ganha_casa, odd_under25
])

ml_solidez_defensiva_visit = media_lista([
    gols_sofr_1t_visit, chutes_sofr_1t_visit,
    sofre_primeiro_ganha_visit, odd_under25
])

ml_pressao_jogo_casa = media_lista([
    chutes_gol_1t_casa, gols_0_15, gols_16_30, over05_0_15, over05_16_30
])

ml_pressao_jogo_visit = media_lista([
    chutes_gol_1t_visit, gols_31_45, gols_46_60, over05_31_45, over05_46_60
])

ml_controle_partida_casa = media_lista([
    vitorias_casa, h2h_vit_casa, odd_casa, odd_empate
])

ml_controle_partida_visit = media_lista([
    vitorias_visit, h2h_vit_visit, odd_visitante, odd_empate
])

# =========================================
# CARDS
# =========================================
cards_1 = [
    ("🏠 Casa", fmt_num(odd_casa)),
    ("🤝 Empate", fmt_num(odd_empate)),
    ("✈️ Visitante", fmt_num(odd_visitante)),
    ("⬆️ Over 2.5", fmt_num(odd_over25)),
    ("⬇️ Under 2.5", fmt_num(odd_under25)),
    ("✅ BTTS Sim", fmt_num(odd_btts_sim)),
    ("❌ BTTS Não", fmt_num(odd_btts_nao)),
]

metricas_base_casa = [
    ("Precisão nos chutes no alvo casa", fmt_pct(precisao_casa)),
    ("Chutes por gol casa", fmt_num(chutes_gol_casa)),
    ("Quando marcam o primeiro gol e ganha o jogo casa", fmt_pct(marca_primeiro_ganha_casa)),
    ("Quando sofre o primeiro gol e ganha o jogo casa", fmt_pct(sofre_primeiro_ganha_casa)),
    ("Média de gols marcados primeiro tempo casa", fmt_num(gols_marc_1t_casa)),
    ("Média de chutes no gol marcados 1º tempo casa", fmt_num(chutes_gol_1t_casa)),
    ("Média total de chutes sofridos 1º tempo casa", fmt_num(chutes_sofr_1t_casa)),
    ("Confrontos diretos - vitórias casa", fmt_pct(h2h_vit_casa)),
    ("Confrontos diretos - marcou primeiro casa", fmt_pct(h2h_marcou_primeiro_casa)),
    ("Vitórias casa", fmt_pct(vitorias_casa)),
    ("Potência Ofensiva casa", ml_potencia_ofensiva_casa),
    ("Solidez Defensiva casa", ml_solidez_defensiva_casa),
    ("Pressão de Jogo casa", ml_pressao_jogo_casa),
    ("Controle da Partida casa", ml_controle_partida_casa),
]

metricas_base_visit = [
    ("Precisão nos chutes no alvo visitante", fmt_pct(precisao_visit)),
    ("Chutes por gol visitante", fmt_num(chutes_gol_visit)),
    ("Quando marcam o primeiro gol e ganha o jogo visitante", fmt_pct(marca_primeiro_ganha_visit)),
    ("Quando sofre o primeiro gol e ganha o jogo visitante", fmt_pct(sofre_primeiro_ganha_visit)),
    ("Média de gols marcados primeiro tempo visitante", fmt_num(gols_marc_1t_visit)),
    ("Média de chutes no gol marcados 1º tempo visitante", fmt_num(chutes_gol_1t_visit)),
    ("Média total de chutes sofridos 1º tempo visitante", fmt_num(chutes_sofr_1t_visit)),
    ("Confrontos diretos - vitórias visitante", fmt_pct(h2h_vit_visit)),
    ("Confrontos diretos - marcou primeiro visitante", fmt_pct(h2h_marcou_primeiro_visit)),
    ("Vitórias visitante", fmt_pct(vitorias_visit)),
    ("Potência Ofensiva visitante", ml_potencia_ofensiva_visit),
    ("Solidez Defensiva visitante", ml_solidez_defensiva_visit),
    ("Pressão de Jogo visitante", ml_pressao_jogo_visit),
    ("Controle da Partida visitante", ml_controle_partida_visit),
]

cards_janelas_gols = [
    ("Média de gols 0-15' minutos", fmt_num(gols_0_15)),
    ("Média de gols 16-30' minutos", fmt_num(gols_16_30)),
    ("Média de gols 31-45' minutos", fmt_num(gols_31_45)),
    ("Média de gols 46-60' minutos", fmt_num(gols_46_60)),
    ("Média de gols 61-75' minutos", fmt_num(gols_61_75)),
    ("Média de gols 76-90' minutos", fmt_num(gols_76_90)),
]

cards_janelas_over = [
    ("Mais de 0.5 gols 0-15'", fmt_pct(over05_0_15)),
    ("Mais de 0.5 gols 16-30'", fmt_pct(over05_16_30)),
    ("Mais de 0.5 gols 31-45'", fmt_pct(over05_31_45)),
    ("Mais de 0.5 gols 46-60'", fmt_pct(over05_46_60)),
    ("Mais de 0.5 gols 61-75'", fmt_pct(over05_61_75)),
    ("Mais de 0.5 gols 76-90'", fmt_pct(over05_76_90)),
]

# =========================================
# BACKTEST - TARGETS E RESULTADOS
# =========================================
def criar_targets_reais(df_base: pd.DataFrame) -> pd.DataFrame:
    df_real = df_base.copy()

    col_status = encontrar_coluna_real(df_real, "Status")
    if col_status and col_status in df_real.columns:
        df_real = df_real[df_real[col_status].astype(str).str.upper() == "FT"].copy()

    col_home = encontrar_coluna_real(df_real, "Result Home")
    col_away = encontrar_coluna_real(df_real, "Result Visitor")
    if not col_home or not col_away:
        return df_real

    df_real[col_home] = pd.to_numeric(df_real[col_home], errors="coerce")
    df_real[col_away] = pd.to_numeric(df_real[col_away], errors="coerce")

    df_real = df_real.dropna(subset=[col_home, col_away]).copy()

    df_real["Gols_Total"] = df_real[col_home] + df_real[col_away]

    df_real["Casa_Vence_Real"] = (df_real[col_home] > df_real[col_away]).astype(int)
    df_real["Empate_Real"] = (df_real[col_home] == df_real[col_away]).astype(int)
    df_real["Visitante_Vence_Real"] = (df_real[col_home] < df_real[col_away]).astype(int)

    df_real["Casa_ou_Empate_Real"] = (df_real[col_home] >= df_real[col_away]).astype(int)
    df_real["Empate_ou_Visitante_Real"] = (df_real[col_home] <= df_real[col_away]).astype(int)
    df_real["Casa_ou_Visitante_Real"] = (df_real[col_home] != df_real[col_away]).astype(int)

    df_real["Over_05_Real"] = (df_real["Gols_Total"] > 0.5).astype(int)
    df_real["Over_15_Real"] = (df_real["Gols_Total"] > 1.5).astype(int)
    df_real["Over_25_Real"] = (df_real["Gols_Total"] > 2.5).astype(int)

    df_real["Under_05_Real"] = (df_real["Gols_Total"] < 0.5).astype(int)
    df_real["Under_15_Real"] = (df_real["Gols_Total"] < 1.5).astype(int)
    df_real["Under_25_Real"] = (df_real["Gols_Total"] < 2.5).astype(int)

    df_real["BTTS_Sim_Real"] = ((df_real[col_home] > 0) & (df_real[col_away] > 0)).astype(int)
    df_real["BTTS_Nao_Real"] = ((df_real[col_home] == 0) | (df_real[col_away] == 0)).astype(int)

    mapa_odds_profit = {
        "Casa_Vence_Real": "Odds casa para vencer",
        "Empate_Real": "Odds empate",
        "Visitante_Vence_Real": "Odds visitante para vencer",
        "Over_25_Real": "Odds mais de 2.5",
        "Under_25_Real": "Odds menos de 2.5",
        "BTTS_Sim_Real": "Odds ambas equipes marcarem sim",
        "BTTS_Nao_Real": "Odds ambas equipes marcarem não",
    }

    for mercado_real, col_odd_nome in mapa_odds_profit.items():
        col_odd_real = encontrar_coluna_real(df_real, col_odd_nome)
        if col_odd_real and mercado_real in df_real.columns:
            df_real[col_odd_real] = pd.to_numeric(df_real[col_odd_real], errors="coerce")
            df_real[f"{mercado_real}_Profit"] = np.where(
                df_real[mercado_real] == 1,
                df_real[col_odd_real] - 1,
                -1.0
            )
        else:
            df_real[f"{mercado_real}_Profit"] = np.nan

    return df_real


def testar_variaveis_por_faixa(
    df_modelo: pd.DataFrame,
    variaveis_modelo: list[str],
    min_jogos_faixa: int = 20,
    q_faixas: int = 5
):
    mercados = [
        "Casa_Vence_Real",
        "Empate_Real",
        "Visitante_Vence_Real",
        "Over_25_Real",
        "Under_25_Real",
        "BTTS_Sim_Real",
        "BTTS_Nao_Real",
    ]

    baseline_rows = []
    resultado_rows = []

    for mercado in mercados:
        if mercado not in df_modelo.columns:
            continue

        profit_col = f"{mercado}_Profit"
        if profit_col not in df_modelo.columns:
            continue

        base_mercado = df_modelo[[mercado, profit_col]].copy()
        base_mercado = base_mercado.dropna()

        if len(base_mercado) > 0:
            baseline_rows.append({
                "Mercado": mercado,
                "Jogos": len(base_mercado),
                "Taxa_Acerto": base_mercado[mercado].mean(),
                "Lucro_Total": base_mercado[profit_col].sum(),
                "Ganho_pp": base_mercado[profit_col].mean(),
            })

        for variavel in variaveis_modelo:
            col_real = encontrar_coluna_real(df_modelo, variavel)
            if not col_real or col_real not in df_modelo.columns:
                continue

            base = df_modelo[[col_real, mercado, profit_col]].copy()
            base[col_real] = pd.to_numeric(base[col_real], errors="coerce")
            base = base.dropna()

            if len(base) < max(min_jogos_faixa, q_faixas):
                continue

            try:
                base["Faixa"] = pd.qcut(
                    base[col_real],
                    q=min(q_faixas, base[col_real].nunique()),
                    duplicates="drop"
                )
            except:
                continue

            resumo = (
                base.groupby("Faixa", observed=True)
                .agg(
                    Jogos=(mercado, "size"),
                    Taxa_Acerto=(mercado, "mean"),
                    Lucro_Total=(profit_col, "sum"),
                    Ganho_pp=(profit_col, "mean")
                )
                .reset_index()
            )

            resumo = resumo[resumo["Jogos"] >= min_jogos_faixa].copy()
            if resumo.empty:
                continue

            resumo["Mercado"] = mercado
            resumo["Variavel"] = variavel
            resumo["Faixa"] = resumo["Faixa"].astype(str)

            resultado_rows.extend(resumo.to_dict("records"))

    baseline_geral = pd.DataFrame(baseline_rows)
    resultado_faixas = pd.DataFrame(resultado_rows)

    return baseline_geral, resultado_faixas


def testar_combinacoes_2_variaveis(
    df_modelo: pd.DataFrame,
    variaveis_modelo: list[str],
    min_jogos_combo: int = 15,
    q_faixas: int = 4,
    max_combinacoes: int = 40
):
    from itertools import combinations

    mercados = [
        "Casa_Vence_Real",
        "Empate_Real",
        "Visitante_Vence_Real",
        "Over_25_Real",
        "Under_25_Real",
        "BTTS_Sim_Real",
        "BTTS_Nao_Real",
    ]

    variaveis_validas = []
    for variavel in variaveis_modelo:
        col_real = encontrar_coluna_real(df_modelo, variavel)
        if col_real and col_real in df_modelo.columns:
            variaveis_validas.append((variavel, col_real))

    combos = list(combinations(variaveis_validas, 2))[:max_combinacoes]
    resultado_rows = []

    for mercado in mercados:
        if mercado not in df_modelo.columns:
            continue

        profit_col = f"{mercado}_Profit"
        if profit_col not in df_modelo.columns:
            continue

        for (var1, col1), (var2, col2) in combos:
            base = df_modelo[[col1, col2, mercado, profit_col]].copy()
            base[col1] = pd.to_numeric(base[col1], errors="coerce")
            base[col2] = pd.to_numeric(base[col2], errors="coerce")
            base = base.dropna()

            if len(base) < max(min_jogos_combo, q_faixas * 2):
                continue

            try:
                base["Faixa_1"] = pd.qcut(
                    base[col1],
                    q=min(q_faixas, base[col1].nunique()),
                    duplicates="drop"
                )
                base["Faixa_2"] = pd.qcut(
                    base[col2],
                    q=min(q_faixas, base[col2].nunique()),
                    duplicates="drop"
                )
            except:
                continue

            resumo = (
                base.groupby(["Faixa_1", "Faixa_2"], observed=True)
                .agg(
                    Jogos=(mercado, "size"),
                    Taxa_Acerto=(mercado, "mean"),
                    Lucro_Total=(profit_col, "sum"),
                    Ganho_pp=(profit_col, "mean")
                )
                .reset_index()
            )

            resumo = resumo[resumo["Jogos"] >= min_jogos_combo].copy()
            if resumo.empty:
                continue

            resumo["Mercado"] = mercado
            resumo["Variavel_1"] = var1
            resumo["Variavel_2"] = var2
            resumo["Faixa_1"] = resumo["Faixa_1"].astype(str)
            resumo["Faixa_2"] = resumo["Faixa_2"].astype(str)

            resultado_rows.extend(resumo.to_dict("records"))

    return pd.DataFrame(resultado_rows)


def testar_combinacoes_3_variaveis(
    df_modelo: pd.DataFrame,
    variaveis_modelo: list[str],
    min_jogos_combo: int = 15,
    q_faixas: int = 3,
    max_combinacoes: int = 25
):
    from itertools import combinations

    mercados = [
        "Casa_Vence_Real",
        "Empate_Real",
        "Visitante_Vence_Real",
        "Over_25_Real",
        "Under_25_Real",
        "BTTS_Sim_Real",
        "BTTS_Nao_Real",
    ]

    variaveis_validas = []
    for variavel in variaveis_modelo:
        col_real = encontrar_coluna_real(df_modelo, variavel)
        if col_real and col_real in df_modelo.columns:
            variaveis_validas.append((variavel, col_real))

    combos = list(combinations(variaveis_validas, 3))[:max_combinacoes]
    resultado_rows = []

    for mercado in mercados:
        if mercado not in df_modelo.columns:
            continue

        profit_col = f"{mercado}_Profit"
        if profit_col not in df_modelo.columns:
            continue

        for (var1, col1), (var2, col2), (var3, col3) in combos:
            base = df_modelo[[col1, col2, col3, mercado, profit_col]].copy()
            base[col1] = pd.to_numeric(base[col1], errors="coerce")
            base[col2] = pd.to_numeric(base[col2], errors="coerce")
            base[col3] = pd.to_numeric(base[col3], errors="coerce")
            base = base.dropna()

            if len(base) < max(min_jogos_combo, q_faixas * 3):
                continue

            try:
                base["Faixa_1"] = pd.qcut(
                    base[col1],
                    q=min(q_faixas, base[col1].nunique()),
                    duplicates="drop"
                )
                base["Faixa_2"] = pd.qcut(
                    base[col2],
                    q=min(q_faixas, base[col2].nunique()),
                    duplicates="drop"
                )
                base["Faixa_3"] = pd.qcut(
                    base[col3],
                    q=min(q_faixas, base[col3].nunique()),
                    duplicates="drop"
                )
            except:
                continue

            resumo = (
                base.groupby(["Faixa_1", "Faixa_2", "Faixa_3"], observed=True)
                .agg(
                    Jogos=(mercado, "size"),
                    Taxa_Acerto=(mercado, "mean"),
                    Lucro_Total=(profit_col, "sum"),
                    Ganho_pp=(profit_col, "mean")
                )
                .reset_index()
            )

            resumo = resumo[resumo["Jogos"] >= min_jogos_combo].copy()
            if resumo.empty:
                continue

            resumo["Mercado"] = mercado
            resumo["Variavel_1"] = var1
            resumo["Variavel_2"] = var2
            resumo["Variavel_3"] = var3
            resumo["Faixa_1"] = resumo["Faixa_1"].astype(str)
            resumo["Faixa_2"] = resumo["Faixa_2"].astype(str)
            resumo["Faixa_3"] = resumo["Faixa_3"].astype(str)

            resultado_rows.extend(resumo.to_dict("records"))

    return pd.DataFrame(resultado_rows)


@st.cache_data(show_spinner=False)
def rodar_modelo_historico(df_odds_base: pd.DataFrame, variaveis_modelo: list[str]):
    df_modelo = criar_targets_reais(df_odds_base.copy())

    baseline_geral, resultado_faixas = testar_variaveis_por_faixa(
        df_modelo,
        variaveis_modelo,
        min_jogos_faixa=20,
        q_faixas=5
    )

    resultado_combos_2 = testar_combinacoes_2_variaveis(
        df_modelo,
        variaveis_modelo,
        min_jogos_combo=15,
        q_faixas=4,
        max_combinacoes=40
    )

    resultado_combos_3 = testar_combinacoes_3_variaveis(
        df_modelo,
        variaveis_modelo,
        min_jogos_combo=15,
        q_faixas=3,
        max_combinacoes=25
    )

    melhores_ganhos_mercado = pd.DataFrame()
    if not resultado_faixas.empty:
        melhores_ganhos_mercado = (
            resultado_faixas
            .sort_values(["Mercado", "Ganho_pp", "Jogos"], ascending=[True, False, False])
            .groupby("Mercado", as_index=False)
            .head(10)
            .reset_index(drop=True)
        )

    melhores_combos_2_mercado = pd.DataFrame()
    if not resultado_combos_2.empty:
        melhores_combos_2_mercado = (
            resultado_combos_2
            .sort_values(["Mercado", "Ganho_pp", "Jogos"], ascending=[True, False, False])
            .groupby("Mercado", as_index=False)
            .head(10)
            .reset_index(drop=True)
        )

    melhores_combos_3_mercado = pd.DataFrame()
    if not resultado_combos_3.empty:
        melhores_combos_3_mercado = (
            resultado_combos_3
            .sort_values(["Mercado", "Ganho_pp", "Jogos"], ascending=[True, False, False])
            .groupby("Mercado", as_index=False)
            .head(10)
            .reset_index(drop=True)
        )

    return baseline_geral, melhores_ganhos_mercado, melhores_combos_2_mercado, melhores_combos_3_mercado

# =========================================
# VARIÁVEIS BASE DO MODELO HISTÓRICO
# =========================================
variaveis_modelo = [
    "[COMB] Produto | H2H_Win_Casa x Atual_MarcouPrimeiro_Casa",
    "[COMB] Produto | H2H_Win_Visitante x Atual_MarcouPrimeiro_Visitante",
    "[COMB] Diff_CV | Atual_Win",
    "[COMB] Pressao_Liquida_1T_Casa",
    "[COMB] Pressao_Liquida_1T_Visitante",
    "[COMB] Diff | Pressao_Liquida_1T",
    "[COMB] Soma | H2H_Win_Visitante x Atual_Win_Visitante",
    "[COMB] Soma | H2H_Win_Casa x Atual_Win_Casa",
    "[COMB] Soma Media Gols 2T",
    "[COMB] Soma Total Media Gols",
    "[COMB] Soma Media Gols Meio",
    "[COMB] Soma Total % Over Gols",
    "[COMB] Diff | Gols_Sofridos_1T",
    "[COMB] Diff | Gols_Marcados_1T",
    "[COMB] Diff | Chutes_Sofridos_1T",
    "[COMB] Diff | Chutes_Marcados_1T",
]

# =========================================
# VARIÁVEIS VÁLIDAS PARA O BACKTEST
# =========================================
variaveis_modelo_backtest = [
    v for v in variaveis_modelo
    if encontrar_coluna_real(df_odds, v) is not None
]

# =========================================
# FUNÇÕES AUXILIARES - GRÁFICO ROI / LUCRO ACUMULADO
# =========================================
def parse_faixa_intervalo(faixa_txt):
    faixa_txt = str(faixa_txt).strip()

    if len(faixa_txt) < 5:
        return None, None, None, None

    esquerda_fechada = faixa_txt.startswith("[")
    direita_fechada = faixa_txt.endswith("]")

    miolo = faixa_txt[1:-1]
    partes = miolo.split(",")

    if len(partes) != 2:
        return None, None, None, None

    def conv(v):
        v = str(v).strip().replace(",", ".")
        if v.lower() in ["-inf", "inf", "+inf"]:
            return float(v.replace("+", ""))
        return float(v)

    try:
        limite_esq = conv(partes[0])
        limite_dir = conv(partes[1])
        return limite_esq, limite_dir, esquerda_fechada, direita_fechada
    except:
        return None, None, None, None


def aplicar_filtro_faixa(serie, faixa_txt):
    limite_esq, limite_dir, esquerda_fechada, direita_fechada = parse_faixa_intervalo(faixa_txt)

    if limite_esq is None:
        return pd.Series(False, index=serie.index)

    mask_esq = serie >= limite_esq if esquerda_fechada else serie > limite_esq
    mask_dir = serie <= limite_dir if direita_fechada else serie < limite_dir

    return mask_esq & mask_dir


def montar_curva_metodologia(df_base_real, linha_metodo):
    mercado = str(linha_metodo["Mercado"])
    tipo = str(linha_metodo["Tipo_Metodologia"])
    variaveis_txt = str(linha_metodo["Variaveis"])
    faixas_txt = str(linha_metodo["Faixas"])

    profit_col = f"{mercado}_Profit"
    if profit_col not in df_base_real.columns:
        return pd.DataFrame()

    variaveis = [v.strip() for v in variaveis_txt.split(" + ")]
    faixas = [f.strip() for f in faixas_txt.split(" | ")]

    if len(variaveis) != len(faixas):
        return pd.DataFrame()

    df_tmp = df_base_real.copy()
    mask_final = pd.Series(True, index=df_tmp.index)

    for variavel, faixa in zip(variaveis, faixas):
        col_real = encontrar_coluna_real(df_tmp, variavel)
        if not col_real or col_real not in df_tmp.columns:
            return pd.DataFrame()

        serie_num = pd.to_numeric(df_tmp[col_real], errors="coerce")
        mask_final &= aplicar_filtro_faixa(serie_num, faixa)

    df_met = df_tmp[mask_final].copy()
    if df_met.empty:
        return pd.DataFrame()

    col_data = encontrar_coluna_real(df_met, "Date")
    col_hora = encontrar_coluna_real(df_met, "Hour")

    if col_data and col_data in df_met.columns:
        try:
            df_met["_data_plot"] = pd.to_datetime(df_met[col_data], errors="coerce")

            if col_hora and col_hora in df_met.columns:
                df_met["_dt_plot"] = pd.to_datetime(
                    df_met["_data_plot"].astype(str) + " " + df_met[col_hora].astype(str),
                    errors="coerce"
                )
                df_met = df_met.sort_values("_dt_plot", kind="stable")
            else:
                df_met = df_met.sort_values("_data_plot", kind="stable")
        except:
            pass

    df_met = df_met.reset_index(drop=True)
    df_met["Lucro"] = pd.to_numeric(df_met[profit_col], errors="coerce").fillna(0.0)
    df_met["Lucro_Acumulado"] = df_met["Lucro"].cumsum()
    df_met["Entrada"] = range(1, len(df_met) + 1)

    nome_metodo = f"{mercado} | {tipo} | {variaveis_txt} | {faixas_txt}"
    df_met["Metodologia"] = nome_metodo

    if "_dt_plot" in df_met.columns:
        df_met["Data_Ordem"] = df_met["_dt_plot"]
    elif "_data_plot" in df_met.columns:
        df_met["Data_Ordem"] = df_met["_data_plot"]
    else:
        df_met["Data_Ordem"] = pd.NaT

    return df_met[["Entrada", "Lucro", "Lucro_Acumulado", "Metodologia", "Data_Ordem"]].copy()

# =========================================
# FUNÇÕES AUXILIARES - IMPORTAR METODOLOGIA / JOGOS DO DIA
# =========================================
def montar_jogos_do_dia_ns_por_metodologia(df_base_ns: pd.DataFrame, metodologia_dict: dict):
    if not metodologia_dict:
        return pd.DataFrame()

    variaveis_txt = str(metodologia_dict["Variaveis"])
    faixas_txt = str(metodologia_dict["Faixas"])
    mercado = str(metodologia_dict["Mercado"])
    tipo = str(metodologia_dict["Tipo_Metodologia"])

    variaveis = [v.strip() for v in variaveis_txt.split(" + ")]
    faixas = [f.strip() for f in faixas_txt.split(" | ")]

    if len(variaveis) != len(faixas):
        return pd.DataFrame()

    df_tmp = df_base_ns.copy()
    mask_final = pd.Series(True, index=df_tmp.index)

    for variavel, faixa in zip(variaveis, faixas):
        col_real = encontrar_coluna_real(df_tmp, variavel)
        if not col_real or col_real not in df_tmp.columns:
            return pd.DataFrame()

        serie_num = pd.to_numeric(df_tmp[col_real], errors="coerce")
        mask_final &= aplicar_filtro_faixa(serie_num, faixa)

    df_saida = df_tmp[mask_final].copy()

    if df_saida.empty:
        return df_saida

    df_saida["Mercado_Metodologia"] = mercado
    df_saida["Tipo_Metodologia"] = tipo
    df_saida["Variaveis_Metodologia"] = variaveis_txt
    df_saida["Faixas_Metodologia"] = faixas_txt

    return df_saida


def dataframe_para_excel_bytes(df_export: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_export.to_excel(writer, index=False, sheet_name="Jogos_do_Dia_NS")
    return output.getvalue()

# =========================================
# RENDERIZAÇÃO PRINCIPAL
# =========================================
if pagina == "Dashboard":
    st.markdown("## Dashboard da Partida")

    if linha_odds_partida.empty:
        st.warning("Não foi possível localizar a linha da partida selecionada.")
    else:
        render_cards(cards_1)

        st.markdown("### Análise por Equipe")
        render_cards_verticais(
            metricas_base_casa,
            metricas_base_visit,
            home_nome,
            visit_nome
        )

        st.markdown("### Janelas de Gols")
        st.markdown("#### Médias de gols")
        render_cards(cards_janelas_gols)

        st.markdown("#### Frequência de over 0.5")
        render_cards(cards_janelas_over)

elif pagina == "Backtest":
    st.markdown("## Backtest")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.markdown("### Modelo histórico")

        if st.button("Rodar modelo histórico", key="rodar_modelo_historico_btn"):
            with st.spinner("Processando backtest..."):
                baseline_geral, melhores_ganhos_mercado, melhores_combos_2_mercado, melhores_combos_3_mercado = rodar_modelo_historico(
                    df_odds,
                    variaveis_modelo_backtest
                )

                st.session_state["baseline_geral"] = baseline_geral
                st.session_state["melhores_ganhos_mercado"] = melhores_ganhos_mercado
                st.session_state["melhores_combos_2_mercado"] = melhores_combos_2_mercado
                st.session_state["melhores_combos_3_mercado"] = melhores_combos_3_mercado

    with col2:
        st.markdown("### Resumo")
        st.write(f"Variáveis válidas: {len(variaveis_modelo_backtest)}")

    if not st.session_state["baseline_geral"].empty:
        st.markdown("### Baseline geral por mercado")
        st.dataframe(st.session_state["baseline_geral"], use_container_width=True)

    if not st.session_state["melhores_ganhos_mercado"].empty:
        st.markdown("### Melhores ganhos por mercado")
        st.dataframe(st.session_state["melhores_ganhos_mercado"], use_container_width=True)

    if not st.session_state["melhores_combos_2_mercado"].empty:
        st.markdown("### Melhores combinações de 2 variáveis")
        st.dataframe(st.session_state["melhores_combos_2_mercado"], use_container_width=True)

    if not st.session_state["melhores_combos_3_mercado"].empty:
        st.markdown("### Melhores combinações de 3 variáveis")
        st.dataframe(st.session_state["melhores_combos_3_mercado"], use_container_width=True)

    st.markdown("### Melhores metodologias com ROI positivo")

    df_gain_1 = st.session_state["melhores_ganhos_mercado"].copy()
    df_gain_2 = st.session_state["melhores_combos_2_mercado"].copy()
    df_gain_3 = st.session_state["melhores_combos_3_mercado"].copy()

    if not df_gain_1.empty:
        df_gain_1["Tipo_Metodologia"] = "1 variável"
        df_gain_1["Variaveis"] = df_gain_1["Variavel"].astype(str)
        df_gain_1["Faixas"] = df_gain_1["Faixa"].astype(str)
    else:
        df_gain_1 = pd.DataFrame(columns=[
            "Mercado", "Jogos", "Taxa_Acerto", "Lucro_Total", "Ganho_pp",
            "Tipo_Metodologia", "Variaveis", "Faixas"
        ])

    if not df_gain_2.empty:
        df_gain_2["Tipo_Metodologia"] = "2 variáveis"
        df_gain_2["Variaveis"] = (
            df_gain_2["Variavel_1"].astype(str) + " + " +
            df_gain_2["Variavel_2"].astype(str)
        )
        df_gain_2["Faixas"] = (
            df_gain_2["Faixa_1"].astype(str) + " | " +
            df_gain_2["Faixa_2"].astype(str)
        )
    else:
        df_gain_2 = pd.DataFrame(columns=[
            "Mercado", "Jogos", "Taxa_Acerto", "Lucro_Total", "Ganho_pp",
            "Tipo_Metodologia", "Variaveis", "Faixas"
        ])

    if not df_gain_3.empty:
        df_gain_3["Tipo_Metodologia"] = "3 variáveis"
        df_gain_3["Variaveis"] = (
            df_gain_3["Variavel_1"].astype(str) + " + " +
            df_gain_3["Variavel_2"].astype(str) + " + " +
            df_gain_3["Variavel_3"].astype(str)
        )
        df_gain_3["Faixas"] = (
            df_gain_3["Faixa_1"].astype(str) + " | " +
            df_gain_3["Faixa_2"].astype(str) + " | " +
            df_gain_3["Faixa_3"].astype(str)
        )
    else:
        df_gain_3 = pd.DataFrame(columns=[
            "Mercado", "Jogos", "Taxa_Acerto", "Lucro_Total", "Ganho_pp",
            "Tipo_Metodologia", "Variaveis", "Faixas"
        ])

    df_metodos_roi = pd.concat([
        df_gain_1[["Mercado", "Jogos", "Taxa_Acerto", "Lucro_Total", "Ganho_pp", "Tipo_Metodologia", "Variaveis", "Faixas"]],
        df_gain_2[["Mercado", "Jogos", "Taxa_Acerto", "Lucro_Total", "Ganho_pp", "Tipo_Metodologia", "Variaveis", "Faixas"]],
        df_gain_3[["Mercado", "Jogos", "Taxa_Acerto", "Lucro_Total", "Ganho_pp", "Tipo_Metodologia", "Variaveis", "Faixas"]],
    ], ignore_index=True)

    if df_metodos_roi.empty:
        st.info("Nenhuma metodologia encontrada ainda. Rode o backtest primeiro.")
    else:
        df_metodos_roi["ROI_%"] = df_metodos_roi["Ganho_pp"] * 100

        col_f1, col_f2, col_f3, col_f4 = st.columns(4)

        with col_f1:
            mercados_disp = ["Todos"] + sorted(df_metodos_roi["Mercado"].dropna().astype(str).unique().tolist())
            mercado_sel_roi = st.selectbox(
                "Filtrar mercado",
                mercados_disp,
                key="mercado_sel_roi_positivo"
            )

        with col_f2:
            tipos_disp = ["Todos"] + sorted(df_metodos_roi["Tipo_Metodologia"].dropna().astype(str).unique().tolist())
            tipo_sel_roi = st.selectbox(
                "Filtrar tipo",
                tipos_disp,
                key="tipo_sel_roi_positivo"
            )

        with col_f3:
            min_jogos_roi = st.number_input(
                "Mínimo de jogos",
                min_value=1,
                value=20,
                step=1,
                key="min_jogos_roi_positivo"
            )

        with col_f4:
            roi_minimo = st.number_input(
                "ROI mínimo %",
                min_value=0.0,
                value=0.0,
                step=0.1,
                key="roi_minimo_positivo"
            )

        df_filtrado_roi = df_metodos_roi.copy()

        if mercado_sel_roi != "Todos":
            df_filtrado_roi = df_filtrado_roi[
                df_filtrado_roi["Mercado"].astype(str) == mercado_sel_roi
            ].copy()

        if tipo_sel_roi != "Todos":
            df_filtrado_roi = df_filtrado_roi[
                df_filtrado_roi["Tipo_Metodologia"].astype(str) == tipo_sel_roi
            ].copy()

        df_filtrado_roi = df_filtrado_roi[
            (df_filtrado_roi["Jogos"] >= min_jogos_roi) &
            (df_filtrado_roi["ROI_%"] > roi_minimo)
        ].copy()

        if df_filtrado_roi.empty:
            st.warning("Nenhuma metodologia com ROI positivo encontrada com esses filtros.")
        else:
            df_filtrado_roi = df_filtrado_roi.sort_values(
                ["ROI_%", "Lucro_Total", "Jogos", "Taxa_Acerto"],
                ascending=[False, False, False, False]
            ).reset_index(drop=True)

            df_filtrado_roi.insert(0, "Ranking", range(1, len(df_filtrado_roi) + 1))
            df_filtrado_roi["Taxa_Acerto"] = (df_filtrado_roi["Taxa_Acerto"] * 100).round(2)
            df_filtrado_roi["ROI_%"] = df_filtrado_roi["ROI_%"].round(2)
            df_filtrado_roi["Lucro_Total"] = df_filtrado_roi["Lucro_Total"].round(2)
            df_filtrado_roi["Ganho_pp"] = df_filtrado_roi["Ganho_pp"].round(4)

            st.dataframe(
                df_filtrado_roi[
                    [
                        "Ranking",
                        "Mercado",
                        "Tipo_Metodologia",
                        "Variaveis",
                        "Faixas",
                        "Jogos",
                        "Taxa_Acerto",
                        "Lucro_Total",
                        "Ganho_pp",
                        "ROI_%"
                    ]
                ],
                use_container_width=True
            )

            st.markdown("### Melhor metodologia por mercado")

            df_melhor_mercado = (
                df_filtrado_roi
                .sort_values(["Mercado", "ROI_%", "Lucro_Total", "Jogos"], ascending=[True, False, False, False])
                .groupby("Mercado", as_index=False)
                .head(1)
                .reset_index(drop=True)
            )

            st.dataframe(
                df_melhor_mercado[
                    [
                        "Mercado",
                        "Tipo_Metodologia",
                        "Variaveis",
                        "Faixas",
                        "Jogos",
                        "Taxa_Acerto",
                        "Lucro_Total",
                        "Ganho_pp",
                        "ROI_%"
                    ]
                ],
                use_container_width=True
            )

            st.markdown("### Selecionar metodologia")

            col_s1, col_s2, col_s3, col_s4, col_s5 = st.columns([1.1, 1.2, 2.5, 2.0, 0.7])

            with col_s1:
                mercado_input = st.text_input(
                    "Mercado",
                    value=st.session_state["metodo_manual_digitado"]["Mercado"],
                    key="mercado_input_metodo_manual",
                    placeholder="Ex: BTTS_Nao_Real"
                )

            with col_s2:
                tipo_input = st.text_input(
                    "Tipo_Metodologia",
                    value=st.session_state["metodo_manual_digitado"]["Tipo_Metodologia"],
                    key="tipo_input_metodo_manual",
                    placeholder="Ex: 3 variáveis"
                )

            with col_s3:
                variaveis_input = st.text_area(
                    "Variáveis",
                    value=st.session_state["metodo_manual_digitado"]["Variaveis"],
                    key="variaveis_input_metodo_manual",
                    height=120,
                    placeholder="[COMB] Produto | H2H_Win_Casa x Atual_MarcouPrimeiro_Casa + [COMB] Diff_CV | Atual_Win + [COMB] Pressao_Liquida_1T_Casa"
                )

            with col_s4:
                faixas_input = st.text_area(
                    "Faixas",
                    value=st.session_state["metodo_manual_digitado"]["Faixas"],
                    key="faixas_input_metodo_manual",
                    height=120,
                    placeholder="(2.667, 16.0] | (20.0, 100.0] | (-21.001, -3.8]"
                )

            with col_s5:
                st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                aplicar_metodo = st.button(
                    "Aplicar",
                    key="btn_aplicar_metodo_visual_manual"
                )

            if aplicar_metodo:
                mercado_input = str(mercado_input).strip()
                tipo_input = str(tipo_input).strip()
                variaveis_input = str(variaveis_input).strip().replace("\n", " ")
                faixas_input = str(faixas_input).strip().replace("\n", " ")

                if not mercado_input or not tipo_input or not variaveis_input or not faixas_input:
                    st.warning("Preencha Mercado, Tipo_Metodologia, Variáveis e Faixas.")
                else:
                    metodo_dict = {
                        "Mercado": mercado_input,
                        "Tipo_Metodologia": tipo_input,
                        "Variaveis": variaveis_input,
                        "Faixas": faixas_input,
                        "Nome_Metodologia": (
                            f"{mercado_input} | {tipo_input} | {variaveis_input} | {faixas_input}"
                        )
                    }

                    st.session_state["metodologia_visual_selecionada"] = metodo_dict
                    st.session_state["metodo_manual_digitado"] = metodo_dict

            metodo_visual = st.session_state.get("metodologia_visual_selecionada", None)

            if metodo_visual:
                st.dataframe(
                    pd.DataFrame([{
                        "Mercado": metodo_visual["Mercado"],
                        "Tipo_Metodologia": metodo_visual["Tipo_Metodologia"],
                        "Variaveis": metodo_visual["Variaveis"],
                        "Faixas": metodo_visual["Faixas"],
                    }]),
                    use_container_width=True
                )

                col_a1, col_a2, col_a3 = st.columns(3)

                with col_a1:
                    btn_adicionar_grafico = st.button(
                        "Adicionar ao gráfico",
                        key="btn_adicionar_metodo_select"
                    )

                with col_a2:
                    btn_importar_ns = st.button(
                        "Importar jogos do dia NS",
                        key="btn_importar_ns_select"
                    )

                with col_a3:
                    btn_limpar_grafico = st.button(
                        "Limpar metodologias do gráfico",
                        key="btn_limpar_metodos_grafico_select"
                    )

                if btn_limpar_grafico:
                    st.session_state["metodologias_grafico_filtradas"] = []

                if btn_adicionar_grafico:
                    ja_existe = any(
                        (
                            x["Mercado"] == metodo_visual["Mercado"] and
                            x["Tipo_Metodologia"] == metodo_visual["Tipo_Metodologia"] and
                            x["Variaveis"] == metodo_visual["Variaveis"] and
                            x["Faixas"] == metodo_visual["Faixas"]
                        )
                        for x in st.session_state["metodologias_grafico_filtradas"]
                    )

                    if not ja_existe:
                        st.session_state["metodologias_grafico_filtradas"].append(metodo_visual)

                if btn_importar_ns:
                    df_base_ns = df_odds.copy()

                    col_status_ns = encontrar_coluna_real(df_base_ns, "Status")
                    if col_status_ns and col_status_ns in df_base_ns.columns:
                        df_base_ns = df_base_ns[
                            df_base_ns[col_status_ns].astype(str).str.upper() != "FT"
                        ].copy()

                    df_jogos_ns = montar_jogos_do_dia_ns_por_metodologia(
                        df_base_ns,
                        metodo_visual
                    )

                    st.session_state["jogos_dia_ns_importados"] = df_jogos_ns

            if not st.session_state["jogos_dia_ns_importados"].empty:
                st.markdown("#### Jogos do dia NS encontrados pela metodologia")

                df_ns_view = st.session_state["jogos_dia_ns_importados"].copy()

                colunas_prioritarias = [
                    "Partida",
                    "Home Team",
                    "Visitor Team",
                    "League",
                    "Country",
                    "Hour",
                    "Status",
                    "Mercado_Metodologia",
                    "Tipo_Metodologia",
                    "Variaveis_Metodologia",
                    "Faixas_Metodologia",
                ]

                colunas_mostrar = [c for c in colunas_prioritarias if c in df_ns_view.columns]
                restantes = [c for c in df_ns_view.columns if c not in colunas_mostrar]
                df_ns_view = df_ns_view[colunas_mostrar + restantes]

                st.dataframe(df_ns_view, use_container_width=True)

                excel_bytes = dataframe_para_excel_bytes(df_ns_view)

                st.download_button(
                    label="Exportar jogos do dia NS para Excel",
                    data=excel_bytes,
                    file_name="jogos_do_dia_ns_metodologia.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="download_excel_jogos_ns_metodologia"
                )

            st.markdown("### Gráfico de lucro acumulado por metodologia")

            df_base_real_plot = criar_targets_reais(df_odds.copy())

            st.markdown("#### Metodologias adicionadas para o gráfico")

            if len(st.session_state["metodologias_grafico_filtradas"]) == 0:
                st.info("Nenhuma metodologia adicionada ainda.")
            else:
                df_metodos_escolhidos = pd.DataFrame(
                    st.session_state["metodologias_grafico_filtradas"]
                )
                st.dataframe(df_metodos_escolhidos, use_container_width=True)

                modo_plot = st.selectbox(
                    "Modo do gráfico",
                    [
                        "Cada metodologia separada",
                        "Todas no mesmo gráfico",
                        "Linha combinada"
                    ],
                    key="modo_plot_lucro_acumulado_manual"
                )

                curvas = []

                for metodo_dict in st.session_state["metodologias_grafico_filtradas"]:
                    linha_metodo = pd.Series(metodo_dict)
                    curva = montar_curva_metodologia(df_base_real_plot, linha_metodo)

                    if not curva.empty:
                        curva["Metodologia_Selecionada"] = metodo_dict["Nome_Metodologia"]
                        curvas.append(curva)

                if len(curvas) == 0:
                    st.warning("Não foi possível gerar curvas para as metodologias selecionadas.")
                else:
                    df_curvas = pd.concat(curvas, ignore_index=True)

                    if modo_plot == "Cada metodologia separada":
                        for nome_met in df_curvas["Metodologia_Selecionada"].unique():
                            st.markdown(f"#### {nome_met}")

                            df_plot_ind = df_curvas[
                                df_curvas["Metodologia_Selecionada"] == nome_met
                            ][["Entrada", "Lucro_Acumulado"]].copy()

                            df_plot_ind = df_plot_ind.set_index("Entrada")
                            st.line_chart(df_plot_ind, use_container_width=True)

                    elif modo_plot == "Todas no mesmo gráfico":
                        df_plot_multi = df_curvas.pivot_table(
                            index="Entrada",
                            columns="Metodologia_Selecionada",
                            values="Lucro_Acumulado",
                            aggfunc="last"
                        ).sort_index()

                        st.line_chart(df_plot_multi, use_container_width=True)

                    else:
                        df_comb = df_curvas.copy()

                        df_comb["Ordem_Interna"] = df_comb.groupby(
                            "Metodologia_Selecionada"
                        ).cumcount() + 1

                        if "Data_Ordem" in df_comb.columns:
                            df_comb = df_comb.sort_values(
                                ["Data_Ordem", "Metodologia_Selecionada", "Ordem_Interna"],
                                kind="stable"
                            ).reset_index(drop=True)
                        else:
                            df_comb = df_comb.sort_values(
                                ["Metodologia_Selecionada", "Ordem_Interna"],
                                kind="stable"
                            ).reset_index(drop=True)

                        df_comb["Entrada_Combinada"] = range(1, len(df_comb) + 1)
                        df_comb["Lucro_Acumulado"] = df_comb["Lucro"].cumsum()

                        st.line_chart(
                            df_comb.set_index("Entrada_Combinada")[["Lucro_Acumulado"]],
                            use_container_width=True
                        )

                        st.markdown("#### Operações da linha combinada")
                        resumo_comb = df_comb[
                            [
                                "Entrada_Combinada",
                                "Metodologia_Selecionada",
                                "Lucro",
                                "Lucro_Acumulado"
                            ]
                        ].copy()

                        resumo_comb["Lucro"] = resumo_comb["Lucro"].round(2)
                        resumo_comb["Lucro_Acumulado"] = resumo_comb["Lucro_Acumulado"].round(2)

                        st.dataframe(resumo_comb, use_container_width=True)

                    st.markdown("#### Resumo das metodologias selecionadas")

                    resumo_plot = (
                        df_curvas.groupby("Metodologia_Selecionada", as_index=False)
                        .agg(
                            Entradas=("Entrada", "max"),
                            Lucro_Final=("Lucro_Acumulado", "last")
                        )
                        .sort_values("Lucro_Final", ascending=False)
                        .reset_index(drop=True)
                    )

                    resumo_plot["Lucro_Final"] = resumo_plot["Lucro_Final"].round(2)
                    st.dataframe(resumo_plot, use_container_width=True)

    if not st.session_state["df_bt_manual"].empty:
        st.markdown("### Backtest manual")
        st.dataframe(st.session_state["df_bt_manual"], use_container_width=True)

    if st.session_state["probs_bt_manual"]:
        st.markdown("### Probabilidades")
        st.write(st.session_state["probs_bt_manual"])

    if st.session_state["resumo_bt_manual"]:
        st.markdown("### Resumo manual")
        st.write(st.session_state["resumo_bt_manual"])
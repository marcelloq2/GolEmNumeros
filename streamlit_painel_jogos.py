import streamlit as st
import pandas as pd
import numpy as np
import unicodedata
import re

st.set_page_config(page_title="GolEmNúmeros Dashboard", layout="wide")

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

if "df_bt_manual" not in st.session_state:
    st.session_state["df_bt_manual"] = pd.DataFrame()

if "probs_bt_manual" not in st.session_state:
    st.session_state["probs_bt_manual"] = {}
# =========================================
# LINK DA PLANILHA
# =========================================
PLANILHA_ODDS = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSF5WBP5KeBr6cVbAK0yH2IJf_luqoK90gOz1fj_VlS_hoAb4E6v_awCWO-bTi28I-mWYWEeewnhmTh/pub?output=csv"

# =========================================
# CSS
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
        padding-top: 3.2rem;
        padding-bottom: 1rem;
    }

    .card {
        background: linear-gradient(180deg, #0b2a59 0%, #0a2144 100%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 7px;
        padding: 6px 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.20);
        min-height: 44px;
    }

    .card-title {
        font-size: 0.62rem;
        color: #d7e7ff;
        margin-bottom: 2px;
        line-height: 1.0;
        text-align: center;
    }

    .card-value {
        font-size: 0.95rem;
        font-weight: 800;
        color: white;
        word-break: break-word;
        line-height: 1.0;
        text-align: center;
    }

    .titulo-topo {
        font-size: 2rem;
        font-weight: 800;
        color: #ffffff;
        margin-top: 1rem;
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
        padding: 14px;
        margin-top: 0.5rem;
        margin-bottom: 1.2rem;
    }

    .painel-time {
        border-radius: 14px;
        padding: 8px 6px;
        margin-bottom: 10px;
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
        font-size: 1.10rem;
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
        width: 48%;
        min-width: 220px;
        max-width: 290px;
        margin: 0 auto;
    }

    .card-time {
        border-radius: 10px;
        padding: 7px 8px;
        margin-bottom: 7px;
        min-height: 50px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.20);
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        text-align: center;
    }

    .card-time-titulo {
        font-size: 0.78rem;
        opacity: 0.98;
        margin-bottom: 4px;
        line-height: 1.12;
        text-align: center;
        width: 100%;
        font-weight: 700;
    }

    .card-time-valor {
        font-size: 1.28rem;
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

    .bt-chip {
        display: inline-block;
        background: rgba(80,110,220,0.35);
        border: 1px solid rgba(120,150,255,0.25);
        color: white;
        padding: 6px 12px;
        border-radius: 10px;
        margin: 4px 6px 4px 0;
        font-size: 14px;
    }

    .bt-card {
        background: linear-gradient(180deg, rgba(22,53,120,0.95) 0%, rgba(18,41,94,0.95) 100%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 14px;
        padding: 14px;
        text-align: center;
        margin-bottom: 12px;
    }

    .bt-card-red {
        background: linear-gradient(180deg, rgba(139,20,52,0.95) 0%, rgba(110,15,40,0.95) 100%);
    }

    .bt-card-green {
        background: linear-gradient(180deg, rgba(28,110,78,0.95) 0%, rgba(18,80,56,0.95) 100%);
    }

    .bt-card-gold {
        background: linear-gradient(180deg, rgba(243,207,72,0.95) 0%, rgba(209,170,42,0.95) 100%);
        color: #101010;
    }

    .bt-rank-box {
        background: linear-gradient(180deg, rgba(28,24,60,0.95) 0%, rgba(18,15,40,0.95) 100%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 16px;
        padding: 14px;
    }
</style>
""", unsafe_allow_html=True)

# =========================================
# FUNÇÕES
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

def aplicar_filtros(
    df,
    pais_sel,
    liga_sel,
    mercado_odd_sel,
    odd_range_sel,
    status_sel,
    short_sel,
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

    if short_sel and coluna_existe(filtrado, "Short"):
        filtrado = filtrado[filtrado["Short"].astype(str).isin(short_sel)]

    if busca_partida:
        alvo = busca_partida.strip().lower()

        mask_home = (
            filtrado["Home Team"].astype(str).str.lower().str.contains(alvo, na=False)
            if coluna_existe(filtrado, "Home Team") else pd.Series(False, index=filtrado.index)
        )

        mask_away = (
            filtrado["Visitor Team"].astype(str).str.lower().str.contains(alvo, na=False)
            if coluna_existe(filtrado, "Visitor Team") else pd.Series(False, index=filtrado.index)
        )

        filtrado = filtrado[mask_home | mask_away]

    return filtrado

def render_cards(cards):
    cols = st.columns(len(cards))
    for col, (titulo, valor) in zip(cols, cards):
        with col:
            st.markdown(
                f"""
                <div class="card">
                    <div class="card-title">{titulo}</div>
                    <div class="card-value">{valor}</div>
                </div>
                """,
                unsafe_allow_html=True
            )

def render_cards_verticais(metricas_casa, metricas_visit, nome_casa="CASA", nome_visit="VISITANTE"):
    col1, col2 = st.columns(2, gap="small")

    with col1:
        st.markdown('<div class="painel-time painel-casa">', unsafe_allow_html=True)
        st.markdown(f'<div class="painel-time-titulo">🏠 {nome_casa}</div>', unsafe_allow_html=True)
        st.markdown('<div class="lista-metricas-time">', unsafe_allow_html=True)

        for titulo, valor in metricas_casa:
            st.markdown(
                f"""
                <div class="card-time card-casa">
                    <div class="card-time-titulo">{titulo}</div>
                    <div class="card-time-valor">{valor}</div>
                </div>
                """,
                unsafe_allow_html=True
            )

        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="painel-time painel-visit">', unsafe_allow_html=True)
        st.markdown(f'<div class="painel-time-titulo">✈️ {nome_visit}</div>', unsafe_allow_html=True)
        st.markdown('<div class="lista-metricas-time">', unsafe_allow_html=True)

        for titulo, valor in metricas_visit:
            st.markdown(
                f"""
                <div class="card-time card-visit">
                    <div class="card-time-titulo">{titulo}</div>
                    <div class="card-time-valor">{valor}</div>
                </div>
                """,
                unsafe_allow_html=True
            )

        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

def normalizar_texto(valor):
    if pd.isna(valor):
        return ""
    return str(valor).strip().lower().replace("  ", " ")

def obter_linha_partida_por_times(df_origem, df_destino, partida):
    if (
        not coluna_existe(df_origem, "Partida")
        or not coluna_existe(df_origem, "Home Team")
        or not coluna_existe(df_origem, "Visitor Team")
        or not coluna_existe(df_destino, "Home Team")
        or not coluna_existe(df_destino, "Visitor Team")
        or df_origem.empty
        or df_destino.empty
        or not partida
        or partida == "Todas"
    ):
        return pd.DataFrame()

    linha_origem = df_origem[df_origem["Partida"].astype(str) == str(partida)].head(1).copy()
    if linha_origem.empty:
        return pd.DataFrame()

    home_sel = normalizar_texto(linha_origem.iloc[0]["Home Team"])
    away_sel = normalizar_texto(linha_origem.iloc[0]["Visitor Team"])

    home_dest = df_destino["Home Team"].astype(str).map(normalizar_texto)
    away_dest = df_destino["Visitor Team"].astype(str).map(normalizar_texto)

    return df_destino[(home_dest == home_sel) & (away_dest == away_sel)].head(1).copy()

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

def criar_variaveis_combinadas(df_base: pd.DataFrame) -> pd.DataFrame:
    df_comb = df_base.copy()

    colunas_necessarias = [
        "Confrontos diretos - vitórias casa",
        "Vitórias casa",
        "Quando marcam o primeiro gol e ganha o jogo casa",
        "Vitórias visitante",
        "Média de chutes no gol marcados 1º tempo casa",
        "Média total de chutes sofridos 1º tempo casa",
        "Média de chutes no gol marcados 1º tempo visitante",
        "Média total de chutes sofridos 1º tempo visitante",
        "Confrontos diretos - vitórias visitante",
        "Quando marcam o primeiro gol e ganha o jogo visitante",
        "Média de gols 46-60' minutos",
        "Média de gols 61-75' minutos",
        "Média de gols 76-90' minutos",
        "Média de gols 0-15' minutos",
        "Média de gols 16-30' minutos",
        "Média de gols 31-45' minutos",
        "Mais de 0.5 gols 0-15'",
        "Mais de 0.5 gols 16-30'",
        "Mais de 0.5 gols 31-45'",
        "Mais de 0.5 gols 46-60'",
        "Mais de 0.5 gols 61-75'",
        "Mais de 0.5 gols 76-90'",
        "Média de gols sofridos primeiro tempo casa",
        "Média de gols sofridos primeiro tempo visitante",
        "Média de gols marcados primeiro tempo casa",
        "Média de gols marcados primeiro tempo visitante",
        "Média de chutes marcados 1º tempo casa",
        "Média de chutes marcados 1º tempo visitante",
        "Média de chutes marcados primeiro tempo casa",
        "Média de chutes marcados primeiro tempo visitante",
    ]

    for nome in colunas_necessarias:
        col_real = encontrar_coluna_real(df_comb, nome)
        if col_real:
            df_comb[col_real] = to_num_series(df_comb[col_real])

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
        "Média de chutes marcados primeiro tempo casa",
        "Media de chutes marcados 1º tempo casa",
        "Media de chutes marcados primeiro tempo casa",
        "Média de chutes 1º tempo casa",
        "Media de chutes 1º tempo casa"
    )

    serie_chutes_marc_visit = c_multi(
        "Média de chutes marcados 1º tempo visitante",
        "Média de chutes marcados primeiro tempo visitante",
        "Media de chutes marcados 1º tempo visitante",
        "Media de chutes marcados primeiro tempo visitante",
        "Média de chutes 1º tempo visitante",
        "Media de chutes 1º tempo visitante"
    )

    if serie_chutes_marc_casa.isna().all() or serie_chutes_marc_visit.isna().all():
        serie_chutes_marc_casa = c_multi(
            "Média de chutes no gol marcados 1º tempo casa",
            "Média de chutes no gol marcados primeiro tempo casa",
            "Media de chutes no gol marcados 1º tempo casa",
            "Media de chutes no gol marcados primeiro tempo casa"
        )

        serie_chutes_marc_visit = c_multi(
            "Média de chutes no gol marcados 1º tempo visitante",
            "Média de chutes no gol marcados primeiro tempo visitante",
            "Media de chutes no gol marcados 1º tempo visitante",
            "Media de chutes no gol marcados primeiro tempo visitante"
        )

    df_comb["[COMB] Diff | Chutes_Marcados_1T"] = (
        serie_chutes_marc_casa - serie_chutes_marc_visit
    )

    return df_comb    

# =========================================
# SIDEBAR
# =========================================
st.sidebar.markdown("## ⚽ GOL EM NÚMEROS")
st.sidebar.markdown("## Fonte de dados")
st.sidebar.markdown("**Base ativa:** 1_Odds_Indicadores")

if st.sidebar.button("Atualizar dados"):
    st.cache_data.clear()
    st.rerun()


nome_planilha = "1_Odds_Indicadores"
url_planilha = PLANILHA_ODDS
chave_base = nome_planilha

# =========================================
# CARREGAR BASES
# =========================================
try:
    df = carregar_google_sheet(url_planilha)
except Exception as e:
    st.error(f"Erro ao carregar a planilha ativa: {e}")
    st.stop()

if df.empty:
    st.warning("A planilha ativa está vazia.")
    st.stop()

try:
    df_odds = carregar_google_sheet(PLANILHA_ODDS)
except Exception as e:
    st.error(f"Erro ao carregar a base fixa de odds/indicadores: {e}")
    st.stop()

df_odds = criar_variaveis_combinadas(df_odds)

# =========================================
# CRIAR COLUNA PARTIDA
# =========================================
if coluna_existe(df, "Home Team") and coluna_existe(df, "Visitor Team"):
    df["Partida"] = df["Home Team"].astype(str) + " x " + df["Visitor Team"].astype(str)

if coluna_existe(df_odds, "Home Team") and coluna_existe(df_odds, "Visitor Team"):
    df_odds["Partida"] = df_odds["Home Team"].astype(str) + " x " + df_odds["Visitor Team"].astype(str)

# =========================================
# FILTROS SIDEBAR
# =========================================
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

odds_colunas_validas = [
    c for c in odds_colunas_disponiveis
    if coluna_existe(df, c)
]

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
short_sel = multiselect_seguro("Short", df, "Short", key=f"short_{chave_base}")
busca_partida = st.sidebar.text_input("Buscar partida", key=f"busca_partida_{chave_base}")

df_filtrado = aplicar_filtros(
    df,
    pais_sel,
    liga_sel,
    mercado_odd_sel,
    odd_range_sel,
    status_sel,
    short_sel,
    busca_partida
).copy()

# =========================================
# TÍTULO
# =========================================
st.markdown('<div class="titulo-topo">⚽ GolEmNúmeros - Dashboard</div>', unsafe_allow_html=True)
st.markdown(
    f'<div class="subtitulo-topo">Fonte atual: {nome_planilha} | Linhas carregadas: {len(df)}</div>',
    unsafe_allow_html=True
)

# =========================================
# CAMPO DE SELEÇÃO DE PARTIDA NO TOPO
# =========================================
if coluna_existe(df_filtrado, "Partida"):
    partidas_disponiveis = sorted(df_filtrado["Partida"].dropna().astype(str).unique())

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
# =========================================
# MÉTRICAS GERAIS
# =========================================
total_linhas = len(df_filtrado)

if coluna_existe(df_filtrado, "Partida"):
    total_partidas = df_filtrado["Partida"].nunique()
else:
    total_partidas = total_linhas

ligas_unicas = df_filtrado["League"].nunique() if coluna_existe(df_filtrado, "League") else 0
paises_unicos = df_filtrado["Country"].nunique() if coluna_existe(df_filtrado, "Country") else 0
ft_qtd = (df_filtrado["Status"].astype(str).str.upper() == "FT").sum() if coluna_existe(df_filtrado, "Status") else 0
ns_qtd = (df_filtrado["Status"].astype(str).str.upper() == "NS").sum() if coluna_existe(df_filtrado, "Status") else 0

live_qtd = (
    df_filtrado["Status"].astype(str).str.upper().isin(["LIVE", "1H", "HT", "2H"]).sum()
    if coluna_existe(df_filtrado, "Status") else 0
)

hoje_qtd = 0
if coluna_existe(df_filtrado, "Hour"):
    hoje_str = pd.Timestamp.now().strftime("%d/%m/%Y")
    hoje_qtd = df_filtrado["Hour"].astype(str).str.startswith(hoje_str, na=False).sum()

# =========================================
# DADOS DA PLANILHA 1_Odds_Indicadores
# =========================================
linha_odds_partida = pd.DataFrame()
home_nome = "CASA"
visit_nome = "VISITANTE"

if partida_selecionada is not None and coluna_existe(df_odds, "Partida"):
    linha_odds_partida = df_odds[
        df_odds["Partida"].astype(str) == str(partida_selecionada)
    ].copy()

# fallback: usa a própria base filtrada se não achar na df_odds
if linha_odds_partida.empty and not df_filtrado.empty:
    linha_odds_partida = df_filtrado.copy()

if not linha_odds_partida.empty:
    if coluna_existe(linha_odds_partida, "Home Team"):
        home_nome = str(linha_odds_partida.iloc[0]["Home Team"])
    if coluna_existe(linha_odds_partida, "Visitor Team"):
        visit_nome = str(linha_odds_partida.iloc[0]["Visitor Team"])

if linha_odds_partida.empty:
    st.warning("Não foi possível localizar a linha da partida selecionada na base.")

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
# VARIÁVEIS COMBINADAS DA LINHA
# =========================================
comb_pressao_liquida_1t_casa = obter_valor_coluna_flex(linha_odds_partida, "[COMB] Pressao_Liquida_1T_Casa")
comb_pressao_liquida_1t_visitante = obter_valor_coluna_flex(linha_odds_partida, "[COMB] Pressao_Liquida_1T_Visitante")
comb_soma_h2h_win_casa_atual_win_casa = obter_valor_coluna_flex(linha_odds_partida, "[COMB] Soma | H2H_Win_Casa x Atual_Win_Casa")
comb_soma_h2h_win_visit_atual_win_visit = obter_valor_coluna_flex(linha_odds_partida, "[COMB] Soma | H2H_Win_Visitante x Atual_Win_Visitante")
comb_prod_h2h_win_casa_marcou_primeiro_casa = obter_valor_coluna_flex(linha_odds_partida, "[COMB] Produto | H2H_Win_Casa x Atual_MarcouPrimeiro_Casa")
comb_prod_h2h_win_visit_marcou_primeiro_visit = obter_valor_coluna_flex(linha_odds_partida, "[COMB] Produto | H2H_Win_Visitante x Atual_MarcouPrimeiro_Visitante")

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
    ("Média de gols sofridos primeiro tempo visitante", fmt_num(gols_sofr_1t_visit)),
    ("Média de chutes no gol marcados 1º tempo casa", fmt_num(chutes_gol_1t_casa)),
    ("Média total de chutes sofridos 1º tempo casa", fmt_num(chutes_sofr_1t_casa)),
    ("Confrontos diretos - vitórias casa", fmt_pct(h2h_vit_casa)),
    ("Confrontos diretos - marcou primeiro casa", fmt_pct(h2h_marcou_primeiro_casa)),
    ("Vitórias casa", fmt_pct(vitorias_casa)),
    ("[COMB] Pressao_Liquida_1T_Casa", fmt_num(comb_pressao_liquida_1t_casa)),
    ("[COMB] Soma | H2H_Win_Casa x Atual_Win_Casa", fmt_num(comb_soma_h2h_win_casa_atual_win_casa)),
    ("[COMB] Produto | H2H_Win_Casa x Atual_MarcouPrimeiro_Casa", fmt_num(comb_prod_h2h_win_casa_marcou_primeiro_casa)),
    ("[COMB] Diff | Gols_Marcados_1T", fmt_num(obter_valor_coluna_flex(linha_odds_partida, "[COMB] Diff | Gols_Marcados_1T"))),
    ("[COMB] Diff | Pressao_Liquida_1T", fmt_num(obter_valor_coluna_flex(linha_odds_partida, "[COMB] Diff | Pressao_Liquida_1T"))),
    ("[COMB] Diff | Gols_Sofridos_1T", fmt_num(obter_valor_coluna_flex(linha_odds_partida, "[COMB] Diff | Gols_Sofridos_1T"))),
    ("[COMB] Diff | Chutes_Sofridos_1T", fmt_num(obter_valor_coluna_flex(linha_odds_partida, "[COMB] Diff | Chutes_Sofridos_1T"))),
    ("Odds menos de 2.5", fmt_num(odd_under25)),
    ("Odds ambas equipes marcarem sim", fmt_num(odd_btts_sim)),
    ("Odds ambas equipes marcarem não", fmt_num(odd_btts_nao)),
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
    ("Média de gols sofridos primeiro tempo casa", fmt_num(gols_sofr_1t_casa)),
    ("Média de chutes no gol marcados 1º tempo visitante", fmt_num(chutes_gol_1t_visit)),
    ("Média total de chutes sofridos 1º tempo casa", fmt_num(chutes_sofr_1t_casa)),
    ("Confrontos diretos - vitórias visitante", fmt_pct(h2h_vit_visit)),
    ("Confrontos diretos - marcou primeiro visitante", fmt_pct(h2h_marcou_primeiro_visit)),
    ("Vitórias visitante", fmt_pct(vitorias_visit)),
    ("[COMB] Pressao_Liquida_1T_Visitante", fmt_num(comb_pressao_liquida_1t_visitante)),
    ("[COMB] Soma | H2H_Win_Visitante x Atual_Win_Visitante", fmt_num(comb_soma_h2h_win_visit_atual_win_visit)),
    ("[COMB] Produto | H2H_Win_Visitante x Atual_MarcouPrimeiro_Visitante", fmt_num(comb_prod_h2h_win_visit_marcou_primeiro_visit)),
    ("[COMB] Diff | Pressao_Liquida_1T", fmt_num(obter_valor_coluna_flex(linha_odds_partida, "[COMB] Diff | Pressao_Liquida_1T"))),
    ("[COMB] Diff | Gols_Sofridos_1T", fmt_num(obter_valor_coluna_flex(linha_odds_partida, "[COMB] Diff | Gols_Sofridos_1T"))),
    ("[COMB] Diff | Chutes_Sofridos_1T", fmt_num(obter_valor_coluna_flex(linha_odds_partida, "[COMB] Diff | Chutes_Sofridos_1T"))),
    ("[COMB] Diff | Chutes_Marcados_1T", fmt_num(obter_valor_coluna_flex(linha_odds_partida, "[COMB] Diff | Chutes_Marcados_1T"))),
    ("Odds menos de 2.5", fmt_num(odd_under25)),
    ("Odds ambas equipes marcarem sim", fmt_num(odd_btts_sim)),
    ("Odds ambas equipes marcarem não", fmt_num(odd_btts_nao)),
    ("Potência Ofensiva visitante", ml_potencia_ofensiva_visit),
    ("Solidez Defensiva visitante", ml_solidez_defensiva_visit),
    ("Pressão de Jogo visitante", ml_pressao_jogo_visit),
    ("Controle da Partida visitante", ml_controle_partida_visit),
]

cards_janelas = [
    ("Média de gols 0-15' minutos", fmt_num(gols_0_15)),
    ("Média de gols 16-30' minutos", fmt_num(gols_16_30)),
    ("Média de gols 31-45' minutos", fmt_num(gols_31_45)),
    ("Média de gols 46-60' minutos", fmt_num(gols_46_60)),
    ("Média de gols 61-75' minutos", fmt_num(gols_61_75)),
    ("Média de gols 76-90' minutos", fmt_num(gols_76_90)),
    ("Mais de 0.5 gols 0-15'", fmt_pct(over05_0_15)),
    ("Mais de 0.5 gols 16-30'", fmt_pct(over05_16_30)),
    ("Mais de 0.5 gols 31-45'", fmt_pct(over05_31_45)),
    ("Mais de 0.5 gols 46-60'", fmt_pct(over05_46_60)),
    ("Mais de 0.5 gols 61-75'", fmt_pct(over05_61_75)),
    ("Mais de 0.5 gols 76-90'", fmt_pct(over05_76_90)),
]

# =========================================
# MODELO HISTÓRICO - TARGETS REAIS E TESTE POR FAIXA
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

    # 1X2
    df_real["Casa_Vence_Real"] = (df_real[col_home] > df_real[col_away]).astype(int)
    df_real["Empate_Real"] = (df_real[col_home] == df_real[col_away]).astype(int)
    df_real["Visitante_Vence_Real"] = (df_real[col_home] < df_real[col_away]).astype(int)

    # Dupla chance
    df_real["Casa_ou_Empate_Real"] = (df_real[col_home] >= df_real[col_away]).astype(int)
    df_real["Empate_ou_Visitante_Real"] = (df_real[col_home] <= df_real[col_away]).astype(int)
    df_real["Casa_ou_Visitante_Real"] = (df_real[col_home] != df_real[col_away]).astype(int)

    # Over
    df_real["Over_05_Real"] = (df_real["Gols_Total"] > 0.5).astype(int)
    df_real["Over_15_Real"] = (df_real["Gols_Total"] > 1.5).astype(int)
    df_real["Over_25_Real"] = (df_real["Gols_Total"] > 2.5).astype(int)

    # Under
    df_real["Under_05_Real"] = (df_real["Gols_Total"] < 0.5).astype(int)
    df_real["Under_15_Real"] = (df_real["Gols_Total"] < 1.5).astype(int)
    df_real["Under_25_Real"] = (df_real["Gols_Total"] < 2.5).astype(int)

    # BTTS
    df_real["BTTS_Sim_Real"] = ((df_real[col_home] > 0) & (df_real[col_away] > 0)).astype(int)
    df_real["BTTS_Nao_Real"] = ((df_real[col_home] == 0) | (df_real[col_away] == 0)).astype(int)

    # =========================================
    # PROFIT REAL POR MERCADO COM ODD DISPONÍVEL
    # =========================================
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
    df_base: pd.DataFrame,
    variaveis_teste: list[str],
    min_jogos_faixa: int = 20,
    q_faixas: int = 5
) -> tuple[pd.DataFrame, pd.DataFrame]:

    mercados_reais = [
        "Casa_Vence_Real",
        "Empate_Real",
        "Visitante_Vence_Real",
        "Casa_ou_Empate_Real",
        "Empate_ou_Visitante_Real",
        "Casa_ou_Visitante_Real",
        "Over_05_Real",
        "Over_15_Real",
        "Over_25_Real",
        "Under_05_Real",
        "Under_15_Real",
        "Under_25_Real",
        "BTTS_Sim_Real",
        "BTTS_Nao_Real",
    ]

    df_teste = df_base.copy()

    mercados_validos = [m for m in mercados_reais if m in df_teste.columns]
    if not mercados_validos:
        return pd.DataFrame(), pd.DataFrame()

    baseline_geral = (
        df_teste[mercados_validos]
        .mean()
        .rename("Taxa_Geral")
        .reset_index()
        .rename(columns={"index": "Mercado"})
    )

    resultados = []

    for var in variaveis_teste:
        col_var = encontrar_coluna_real(df_teste, var)
        if not col_var or col_var not in df_teste.columns:
            continue

        base_var = df_teste.copy()
        base_var[col_var] = pd.to_numeric(base_var[col_var], errors="coerce")
        base_var = base_var.dropna(subset=[col_var]).copy()

        if len(base_var) < max(40, q_faixas * 8):
            continue

        try:
            base_var["Faixa"] = pd.qcut(base_var[col_var], q=q_faixas, duplicates="drop")
        except Exception:
            continue

        for mercado in mercados_validos:
            taxa_geral = float(df_teste[mercado].mean())
            col_profit = f"{mercado}_Profit"
            profit_disponivel = col_profit in base_var.columns and base_var[col_profit].notna().any()

            agg_dict = {
                "Jogos": (mercado, "size"),
                "Taxa": (mercado, "mean"),
            }

            if profit_disponivel:
                agg_dict["Profit_Total"] = (col_profit, "sum")

            resumo = (
                base_var
                .groupby("Faixa", observed=True)
                .agg(**agg_dict)
                .reset_index()
            )

            resumo = resumo[resumo["Jogos"] >= min_jogos_faixa].copy()
            if resumo.empty:
                continue

            resumo["Variavel"] = var
            resumo["Mercado"] = mercado
            resumo["Taxa_Geral"] = taxa_geral
            resumo["Ganho_pp"] = (resumo["Taxa"] - resumo["Taxa_Geral"]) * 100
            resumo["Taxa"] = resumo["Taxa"] * 100
            resumo["Taxa_Geral"] = resumo["Taxa_Geral"] * 100

            if profit_disponivel:
                resumo["ROI_pct"] = np.where(
                    resumo["Jogos"] > 0,
                    (resumo["Profit_Total"] / resumo["Jogos"]) * 100,
                    np.nan
                )

                profit_geral = float(df_teste[col_profit].dropna().sum()) if col_profit in df_teste.columns else np.nan
                jogos_profit_geral = float(df_teste[col_profit].dropna().shape[0]) if col_profit in df_teste.columns else np.nan

                resumo["Profit_Geral"] = profit_geral
                resumo["ROI_Geral_pct"] = (
                    (profit_geral / jogos_profit_geral) * 100
                    if jogos_profit_geral and jogos_profit_geral > 0 else np.nan
                )
            else:
                resumo["Profit_Total"] = np.nan
                resumo["ROI_pct"] = np.nan
                resumo["Profit_Geral"] = np.nan
                resumo["ROI_Geral_pct"] = np.nan

            resultados.append(
                resumo[[
                    "Mercado", "Variavel", "Faixa", "Jogos",
                    "Taxa_Geral", "Taxa", "Ganho_pp",
                    "Profit_Total", "ROI_pct", "Profit_Geral", "ROI_Geral_pct"
                ]]
            )

    if not resultados:
        return baseline_geral, pd.DataFrame()

    resultado_faixas = pd.concat(resultados, ignore_index=True)
    resultado_faixas = resultado_faixas.sort_values(
        ["Mercado", "Ganho_pp", "Jogos"],
        ascending=[True, False, False]
    ).reset_index(drop=True)

    return baseline_geral, resultado_faixas


# =========================================
# VARIÁVEIS QUE SERÃO TESTADAS
# =========================================
variaveis_modelo = [
    "Precisão nos chutes no alvo casa",
    "Precisão nos chutes no alvo visitante",
    "Chutes por gol casa",
    "Chutes por gol visitante",
    "Quando marcam o primeiro gol e ganha o jogo casa",
    "Quando marcam o primeiro gol e ganha o jogo visitante",
    "Quando sofre o primeiro gol e ganha o jogo casa",
    "Quando sofre o primeiro gol e ganha o jogo visitante",
    "Média de gols marcados primeiro tempo casa",
    "Média de gols marcados primeiro tempo visitante",
    "Média de gols sofridos primeiro tempo casa",
    "Média de gols sofridos primeiro tempo visitante",
    "Média de chutes no gol marcados 1º tempo casa",
    "Média de chutes no gol marcados 1º tempo visitante",
    "Média total de chutes sofridos 1º tempo casa",
    "Média total de chutes sofridos 1º tempo visitante",
    "Confrontos diretos - vitórias casa",
    "Confrontos diretos - vitórias visitante",
    "Confrontos diretos - marcou primeiro casa",
    "Confrontos diretos - marcou primeiro visitante",
    "Vitórias casa",
    "Vitórias visitante",
    "[COMB] Pressao_Liquida_1T_Casa",
    "[COMB] Pressao_Liquida_1T_Visitante",
    "[COMB] Soma | H2H_Win_Casa x Atual_Win_Casa",
    "[COMB] Soma | H2H_Win_Visitante x Atual_Win_Visitante",
    "[COMB] Produto | H2H_Win_Casa x Atual_MarcouPrimeiro_Casa",
    "[COMB] Produto | H2H_Win_Visitante x Atual_MarcouPrimeiro_Visitante",
    "[COMB] Diff | Gols_Marcados_1T",
    "[COMB] Diff | Pressao_Liquida_1T",
    "[COMB] Diff | Gols_Sofridos_1T",
    "[COMB] Diff | Chutes_Sofridos_1T",
    "[COMB] Diff | Chutes_Marcados_1T",
    "Odds menos de 2.5",
    "Odds ambas equipes marcarem sim",
    "Odds ambas equipes marcarem não",
    "Potência Ofensiva casa",
    "Potência Ofensiva visitante",
    "Solidez Defensiva casa",
    "Solidez Defensiva visitante",
    "Pressão de Jogo casa",
    "Pressão de Jogo visitante",
    "Controle da Partida casa",
    "Controle da Partida visitante",
    "Média de gols 0-15' minutos",
    "Média de gols 16-30' minutos",
    "Média de gols 31-45' minutos",
    "Média de gols 46-60' minutos",
    "Média de gols 61-75' minutos",
    "Média de gols 76-90' minutos",
    "Mais de 0.5 gols 0-15'",
    "Mais de 0.5 gols 16-30'",
    "Mais de 0.5 gols 31-45'",
    "Mais de 0.5 gols 46-60'",
    "Mais de 0.5 gols 61-75'",
    "Mais de 0.5 gols 76-90'",
]

variaveis_backtest_manual = variaveis_modelo.copy() + [
    "Diff | Precisão nos chutes no alvo",
    "Diff | Chutes por gol",
    "Diff | Potência Ofensiva",
    "Diff | Solidez Defensiva",
    "Diff | Pressão de Jogo",
    "Diff | Controle da Partida",

    "Razão | Potência Ofensiva",
    "Razão | Pressão de Jogo",
    "Razão | Controle da Partida",
]
# =========================================
# MODELO HISTÓRICO - COMBINAÇÕES DE 2 VARIÁVEIS
# =========================================
# =========================================
# MODELO HISTÓRICO - COMBINAÇÕES DE 2 VARIÁVEIS
# =========================================
def testar_combinacoes_2_variaveis(
    df_base: pd.DataFrame,
    variaveis_teste: list[str],
    min_jogos_combo: int = 15,
    q_faixas: int = 4,
    max_combinacoes: int = 40
) -> pd.DataFrame:

    mercados_reais = [
        "Casa_Vence_Real",
        "Empate_Real",
        "Visitante_Vence_Real",
        "Casa_ou_Empate_Real",
        "Empate_ou_Visitante_Real",
        "Casa_ou_Visitante_Real",
        "Over_05_Real",
        "Over_15_Real",
        "Over_25_Real",
        "Under_05_Real",
        "Under_15_Real",
        "Under_25_Real",
        "BTTS_Sim_Real",
        "BTTS_Nao_Real",
    ]

    df_teste = df_base.copy()
    mercados_validos = [m for m in mercados_reais if m in df_teste.columns]
    if not mercados_validos:
        return pd.DataFrame()

    variaveis_validas = []
    for var in variaveis_teste:
        col = encontrar_coluna_real(df_teste, var)
        if col and col in df_teste.columns:
            variaveis_validas.append(var)

    variaveis_validas = variaveis_validas[:max_combinacoes]
    resultados = []

    for i in range(len(variaveis_validas)):
        for j in range(i + 1, len(variaveis_validas)):
            var1 = variaveis_validas[i]
            var2 = variaveis_validas[j]

            col1 = encontrar_coluna_real(df_teste, var1)
            col2 = encontrar_coluna_real(df_teste, var2)

            if not col1 or not col2:
                continue

            base_combo = df_teste.copy()
            base_combo[col1] = pd.to_numeric(base_combo[col1], errors="coerce")
            base_combo[col2] = pd.to_numeric(base_combo[col2], errors="coerce")
            base_combo = base_combo.dropna(subset=[col1, col2]).copy()

            if len(base_combo) < max(60, q_faixas * q_faixas * 2):
                continue

            try:
                base_combo["Faixa_1"] = pd.qcut(base_combo[col1], q=q_faixas, duplicates="drop")
                base_combo["Faixa_2"] = pd.qcut(base_combo[col2], q=q_faixas, duplicates="drop")
            except Exception:
                continue

            for mercado in mercados_validos:
                taxa_geral = float(df_teste[mercado].mean())
                col_profit = f"{mercado}_Profit"
                profit_disponivel = col_profit in base_combo.columns and base_combo[col_profit].notna().any()

                agg_dict = {
                    "Jogos": (mercado, "size"),
                    "Taxa": (mercado, "mean"),
                }

                if profit_disponivel:
                    agg_dict["Profit_Total"] = (col_profit, "sum")

                resumo = (
                    base_combo
                    .groupby(["Faixa_1", "Faixa_2"], observed=True)
                    .agg(**agg_dict)
                    .reset_index()
                )

                resumo = resumo[resumo["Jogos"] >= min_jogos_combo].copy()
                if resumo.empty:
                    continue

                resumo["Variavel_1"] = var1
                resumo["Variavel_2"] = var2
                resumo["Mercado"] = mercado
                resumo["Taxa_Geral"] = taxa_geral
                resumo["Ganho_pp"] = (resumo["Taxa"] - resumo["Taxa_Geral"]) * 100
                resumo["Taxa"] = resumo["Taxa"] * 100
                resumo["Taxa_Geral"] = resumo["Taxa_Geral"] * 100
                resumo["Metodo"] = resumo["Variavel_1"] + " + " + resumo["Variavel_2"]
                resumo["Faixa"] = (
                    resumo["Faixa_1"].astype(str) + " | " +
                    resumo["Faixa_2"].astype(str)
                )

                if profit_disponivel:
                    resumo["ROI_pct"] = np.where(
                        resumo["Jogos"] > 0,
                        (resumo["Profit_Total"] / resumo["Jogos"]) * 100,
                        np.nan
                    )

                    profit_geral = float(df_teste[col_profit].dropna().sum()) if col_profit in df_teste.columns else np.nan
                    jogos_profit_geral = float(df_teste[col_profit].dropna().shape[0]) if col_profit in df_teste.columns else np.nan

                    resumo["Profit_Geral"] = profit_geral
                    resumo["ROI_Geral_pct"] = (
                        (profit_geral / jogos_profit_geral) * 100
                        if jogos_profit_geral and jogos_profit_geral > 0 else np.nan
                    )
                else:
                    resumo["Profit_Total"] = np.nan
                    resumo["ROI_pct"] = np.nan
                    resumo["Profit_Geral"] = np.nan
                    resumo["ROI_Geral_pct"] = np.nan

                resultados.append(
                    resumo[[
                        "Mercado", "Metodo",
                        "Variavel_1", "Variavel_2",
                        "Faixa", "Jogos", "Taxa_Geral", "Taxa", "Ganho_pp",
                        "Profit_Total", "ROI_pct", "Profit_Geral", "ROI_Geral_pct"
                    ]]
                )

    if not resultados:
        return pd.DataFrame()

    resultado_combos_2 = pd.concat(resultados, ignore_index=True)
    resultado_combos_2 = resultado_combos_2.sort_values(
        ["Mercado", "Ganho_pp", "Jogos"],
        ascending=[True, False, False]
    ).reset_index(drop=True)

    return resultado_combos_2
def testar_combinacoes_3_variaveis(
    df_base: pd.DataFrame,
    variaveis_teste: list[str],
    min_jogos_combo: int = 15,
    q_faixas: int = 3,
    max_combinacoes: int = 50
) -> pd.DataFrame:

    mercados_reais = [
        "Casa_Vence_Real",
        "Empate_Real",
        "Visitante_Vence_Real",
        "Casa_ou_Empate_Real",
        "Empate_ou_Visitante_Real",
        "Casa_ou_Visitante_Real",
        "Over_05_Real",
        "Over_15_Real",
        "Over_25_Real",
        "Under_05_Real",
        "Under_15_Real",
        "Under_25_Real",
        "BTTS_Sim_Real",
        "BTTS_Nao_Real",
    ]

    df_teste = df_base.copy()
    mercados_validos = [m for m in mercados_reais if m in df_teste.columns]
    if not mercados_validos:
        return pd.DataFrame()

    variaveis_validas = []
    for var in variaveis_teste:
        col = encontrar_coluna_real(df_teste, var)
        if col and col in df_teste.columns:
            variaveis_validas.append(var)

    variaveis_validas = variaveis_validas[:max_combinacoes]
    resultados = []

    for i in range(len(variaveis_validas)):
        for j in range(i + 1, len(variaveis_validas)):
            for k in range(j + 1, len(variaveis_validas)):
                var1 = variaveis_validas[i]
                var2 = variaveis_validas[j]
                var3 = variaveis_validas[k]

                col1 = encontrar_coluna_real(df_teste, var1)
                col2 = encontrar_coluna_real(df_teste, var2)
                col3 = encontrar_coluna_real(df_teste, var3)

                if not col1 or not col2 or not col3:
                    continue

                base_combo = df_teste.copy()
                base_combo[col1] = pd.to_numeric(base_combo[col1], errors="coerce")
                base_combo[col2] = pd.to_numeric(base_combo[col2], errors="coerce")
                base_combo[col3] = pd.to_numeric(base_combo[col3], errors="coerce")
                base_combo = base_combo.dropna(subset=[col1, col2, col3]).copy()

                if len(base_combo) < max(80, q_faixas * q_faixas * q_faixas * 2):
                    continue

                try:
                    base_combo["Faixa_1"] = pd.qcut(base_combo[col1], q=q_faixas, duplicates="drop")
                    base_combo["Faixa_2"] = pd.qcut(base_combo[col2], q=q_faixas, duplicates="drop")
                    base_combo["Faixa_3"] = pd.qcut(base_combo[col3], q=q_faixas, duplicates="drop")
                except Exception:
                    continue

                for mercado in mercados_validos:
                    taxa_geral = float(df_teste[mercado].mean())
                    col_profit = f"{mercado}_Profit"
                    profit_disponivel = col_profit in base_combo.columns and base_combo[col_profit].notna().any()

                    agg_dict = {
                        "Jogos": (mercado, "size"),
                        "Taxa": (mercado, "mean"),
                    }

                    if profit_disponivel:
                        agg_dict["Profit_Total"] = (col_profit, "sum")

                    resumo = (
                        base_combo
                        .groupby(["Faixa_1", "Faixa_2", "Faixa_3"], observed=True)
                        .agg(**agg_dict)
                        .reset_index()
                    )

                    resumo = resumo[resumo["Jogos"] >= min_jogos_combo].copy()
                    if resumo.empty:
                        continue

                    resumo["Variavel_1"] = var1
                    resumo["Variavel_2"] = var2
                    resumo["Variavel_3"] = var3
                    resumo["Mercado"] = mercado
                    resumo["Taxa_Geral"] = taxa_geral
                    resumo["Ganho_pp"] = (resumo["Taxa"] - resumo["Taxa_Geral"]) * 100
                    resumo["Taxa"] = resumo["Taxa"] * 100
                    resumo["Taxa_Geral"] = resumo["Taxa_Geral"] * 100
                    resumo["Metodo"] = (
                        resumo["Variavel_1"] + " + " +
                        resumo["Variavel_2"] + " + " +
                        resumo["Variavel_3"]
                    )
                    resumo["Faixa"] = (
                        resumo["Faixa_1"].astype(str) + " | " +
                        resumo["Faixa_2"].astype(str) + " | " +
                        resumo["Faixa_3"].astype(str)
                    )

                    if profit_disponivel:
                        resumo["ROI_pct"] = np.where(
                            resumo["Jogos"] > 0,
                            (resumo["Profit_Total"] / resumo["Jogos"]) * 100,
                            np.nan
                        )

                        profit_geral = float(df_teste[col_profit].dropna().sum()) if col_profit in df_teste.columns else np.nan
                        jogos_profit_geral = float(df_teste[col_profit].dropna().shape[0]) if col_profit in df_teste.columns else np.nan

                        resumo["Profit_Geral"] = profit_geral
                        resumo["ROI_Geral_pct"] = (
                            (profit_geral / jogos_profit_geral) * 100
                            if jogos_profit_geral and jogos_profit_geral > 0 else np.nan
                        )
                    else:
                        resumo["Profit_Total"] = np.nan
                        resumo["ROI_pct"] = np.nan
                        resumo["Profit_Geral"] = np.nan
                        resumo["ROI_Geral_pct"] = np.nan

                    resultados.append(
                        resumo[[
                            "Mercado", "Metodo",
                            "Variavel_1", "Variavel_2", "Variavel_3",
                            "Faixa", "Jogos", "Taxa_Geral", "Taxa", "Ganho_pp",
                            "Profit_Total", "ROI_pct", "Profit_Geral", "ROI_Geral_pct"
                        ]]
                    )

    if not resultados:
        return pd.DataFrame()

    resultado_combos_3 = pd.concat(resultados, ignore_index=True)
    resultado_combos_3 = resultado_combos_3.sort_values(
        ["Mercado", "Ganho_pp", "Jogos"],
        ascending=[True, False, False]
    ).reset_index(drop=True)

    return resultado_combos_3

# =========================================
# FUNÇÃO ORQUESTRADORA DO BACKTEST
# =========================================
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
# VARIÁVEIS VÁLIDAS PARA O BACKTEST
# usa apenas colunas reais do dataframe
# =========================================
variaveis_modelo_backtest = [
    v for v in variaveis_modelo
    if encontrar_coluna_real(df_odds, v) is not None
]


# =========================================
# ABAS PRINCIPAIS
# =========================================
aba_dashboard, aba_backtest = st.tabs(["Dashboard", "Backtest"])


# =========================================
# ABA DASHBOARD
# =========================================
with aba_dashboard:
    render_cards(cards_1)

    st.markdown("### Indicadores por equipe")
    render_cards_verticais(
        metricas_base_casa,
        metricas_base_visit,
        nome_casa=home_nome,
        nome_visit=visit_nome
    )

    st.markdown("### Janelas de gols e over 0.5")
    render_cards(cards_janelas)


# =========================================
# ABA BACKTEST
# =========================================
with aba_backtest:
    st.markdown("## Backtest")

    if st.button("Rodar Backtest", key="rodar_backtest_aba"):
        with st.spinner("Analisando base histórica..."):
            (
                baseline_geral,
                melhores_ganhos_mercado,
                melhores_combos_2_mercado,
                melhores_combos_3_mercado
            ) = rodar_modelo_historico(
                df_odds,
                variaveis_modelo_backtest
            )

        st.session_state["baseline_geral_backtest"] = baseline_geral
        st.session_state["melhores_ganhos_mercado"] = melhores_ganhos_mercado
        st.session_state["melhores_combos_2_mercado"] = melhores_combos_2_mercado
        st.session_state["melhores_combos_3_mercado"] = melhores_combos_3_mercado

    st.markdown("### Baseline Geral por Mercado")
    baseline_df = st.session_state.get("baseline_geral_backtest", pd.DataFrame())
    if baseline_df.empty:
        st.info("Clique em 'Rodar Backtest' para gerar os resultados.")
    else:
        st.dataframe(
            baseline_df,
            use_container_width=True,
            hide_index=True
        )

    st.markdown("### Melhores Ganhos por Mercado")
    ganhos_df = st.session_state.get("melhores_ganhos_mercado", pd.DataFrame())
    if ganhos_df.empty:
        st.info("Nenhum resultado disponível ainda.")
    else:
        colunas_ganhos = [
            c for c in [
                "Mercado", "Variavel", "Faixa", "Jogos",
                "Taxa_Geral", "Taxa", "Ganho_pp",
                "Profit_Total", "ROI_pct", "Profit_Geral", "ROI_Geral_pct"
            ] if c in ganhos_df.columns
        ]
        st.dataframe(
            ganhos_df[colunas_ganhos],
            use_container_width=True,
            hide_index=True
        )

    st.markdown("### Melhores Combinações de 2 Variáveis")
    combos2_df = st.session_state.get("melhores_combos_2_mercado", pd.DataFrame())
    if combos2_df.empty:
        st.info("Nenhum resultado de combinação de 2 variáveis ainda.")
    else:
        colunas_combos2 = [
            c for c in [
                "Mercado", "Metodo", "Variavel_1", "Variavel_2",
                "Faixa", "Jogos", "Taxa_Geral", "Taxa", "Ganho_pp",
                "Profit_Total", "ROI_pct", "Profit_Geral", "ROI_Geral_pct"
            ] if c in combos2_df.columns
        ]
        st.dataframe(
            combos2_df[colunas_combos2],
            use_container_width=True,
            hide_index=True
        )

    st.markdown("### Melhores Combinações de 3 Variáveis")
    combos3_df = st.session_state.get("melhores_combos_3_mercado", pd.DataFrame())
    if combos3_df.empty:
        st.info("Nenhum resultado de combinação de 3 variáveis ainda.")
    else:
        colunas_combos3 = [
            c for c in [
                "Mercado", "Metodo", "Variavel_1", "Variavel_2", "Variavel_3",
                "Faixa", "Jogos", "Taxa_Geral", "Taxa", "Ganho_pp",
                "Profit_Total", "ROI_pct", "Profit_Geral", "ROI_Geral_pct"
            ] if c in combos3_df.columns
        ]
        st.dataframe(
            combos3_df[colunas_combos3],
            use_container_width=True,
            hide_index=True
        )

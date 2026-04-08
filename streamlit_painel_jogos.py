import itertools
import re
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="GolEmNúmeros", layout="wide")

# =========================================================
# CONFIGURAÇÕES
# USE AS URLS PUBLICADAS DE CADA ABA
# =========================================================
URL_PAGINA1 = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRVsf4nH4SJ7cBV174FLEkkmpFLCxiS4FKKyhrTlKnKoUpVX9giYZ6V5_AMGavD3-AEadpm_zynvBK6/pub?gid=0&single=true&output=csv"
URL_PAGINA2 = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRVsf4nH4SJ7cBV174FLEkkmpFLCxiS4FKKyhrTlKnKoUpVX9giYZ6V5_AMGavD3-AEadpm_zynvBK6/pub?gid=272845724&single=true&output=csv"


COLUNAS_EXCLUIDAS = {
    "Taxa Acerto Casa cobrar 5 escanteios primeiro",
    "Taxa de Acerto Casa cobrar 5 escanteios primeiro",
    "Taxa Acerto Casa marcar primeiro",
    "Taxa de Acerto Casa marcar primeiro",
    "Taxa de Acerto Menos de 8.5 escanteios",
    "Taxa de Acerto Mais de 4 escanteios 1° tempo",
    "Taxa de Acerto Menos de 10.5 escanteios",
    "Taxa de Acerto Menos de 11.5 escanteios",
    "Taxa de Acerto Menos de 9.5 escanteios",
    "Taxa de Acerto Visitante marcar primeiro",
    "Taxa de Acerto Mais de 7.5 escanteios",
    "Taxa de Acerto Menos de 4 escanteios 1° tempo",
    "Taxa de Acerto Menos de 5 escanteios 1° tempo",
    "Taxa de Acerto Casa marcar mais escanteios (1x2)",
    "Taxa de Acerto Casa cobrar 7 escanteios primeiro",
    "Taxa de Acerto Mais de 8.5 escanteios",
    "Menos de 6 escanteios 1° tempo",
    "Taxa de Acerto Menos de 6 escanteios 1° tempo",
    "Taxa de Acerto Fora cobrar 5 escanteios primeiro",
    "Taxa de Acerto Fora marcar mais escanteios (1x2)",
    "Taxa de Acerto Fora cobrar 7 escanteios primeiro",
    "Taxa de Acerto Fora cobrar 3 escanteios primeiro",
    "Taxa de Acerto Mais de 5 escanteios 1° tempo",
    "Taxa de Acerto Mais de 10.5 escanteios",
    "Taxa de Acerto Menos de 7.5 escanteios",
}

MERCADOS_EXCLUIDOS = {
    "Casa cobrar 5 escanteios primeiro",
    "Casa marcar primeiro",
    "Menos de 8.5 escanteios",
    "Mais de 4 escanteios 1° tempo",
    "Menos de 10.5 escanteios",
    "Menos de 11.5 escanteios",
    "Menos de 9.5 escanteios",
    "Visitante marcar primeiro",
    "Mais de 7.5 escanteios",
    "Menos de 4 escanteios 1° tempo",
    "Menos de 5 escanteios 1° tempo",
    "Casa marcar mais escanteios (1x2)",
    "Casa cobrar 7 escanteios primeiro",
    "Mais de 8.5 escanteios",
    "Menos de 6 escanteios 1° tempo",
    "Fora cobrar 5 escanteios primeiro",
    "Fora marcar mais escanteios (1x2)",
    "Fora cobrar 7 escanteios primeiro",
    "Fora cobrar 3 escanteios primeiro",
    "Mais de 5 escanteios 1° tempo",
    "Mais de 10.5 escanteios",
    "Menos de 7.5 escanteios",
}

# =========================================================
# ESTILO
# =========================================================
st.markdown(
    """
    <style>
    .stApp {
        background: linear-gradient(180deg, #061327 0%, #081a34 100%);
        color: #ffffff;
    }
    section[data-testid="stSidebar"] {
        background: #020c1d;
        border-right: 1px solid rgba(255,255,255,0.08);
    }
    .block-container {
        padding-top: 1rem;
        padding-bottom: 1rem;
        max-width: 1550px;
    }
    .card-metrica {
        background: rgba(19, 36, 68, 0.95);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 18px;
        padding: 16px 18px;
        box-shadow: 0 8px 20px rgba(0,0,0,0.18);
    }
    .card-metrica .rotulo {
        font-size: 12px;
        color: #9fb3d9;
        margin-bottom: 8px;
    }
    .card-metrica .valor {
        font-size: 30px;
        font-weight: 700;
        color: #ffffff;
        line-height: 1.1;
    }
    .painel-bloco {
        background: rgba(18, 33, 63, 0.95);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 24px;
        padding: 18px;
        box-shadow: 0 10px 24px rgba(0,0,0,0.18);
    }
    .painel-titulo {
        font-size: 34px;
        font-weight: 800;
        color: white;
        margin-bottom: 4px;
    }
    .painel-subtitulo {
        color: #9fb3d9;
        font-size: 15px;
        margin-bottom: 18px;
    }
    .fonte-badge {
        display: inline-block;
        background: rgba(34,197,94,0.14);
        color: #86efac;
        border: 1px solid rgba(134,239,172,0.12);
        padding: 6px 10px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 600;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =========================================================
# FUNÇÕES BÁSICAS
# =========================================================
def normalizar_nome_coluna(nome: str) -> str:
    return re.sub(r"\s+", " ", str(nome).strip()).lower()


def remover_colunas_excluidas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    excluidas_norm = {normalizar_nome_coluna(c) for c in COLUNAS_EXCLUIDAS}
    cols_remover = [c for c in df.columns if normalizar_nome_coluna(c) in excluidas_norm]

    if cols_remover:
        df = df.drop(columns=cols_remover, errors="ignore")

    return df


def remover_mercados_excluidos(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    mercados_norm = {normalizar_nome_coluna(m) for m in MERCADOS_EXCLUIDOS}
    colunas_mercado = [
        c for c in df.columns
        if normalizar_nome_coluna(c) in {
            "a mais provavel",
            "a mais provável",
            "previsoes",
            "previsões",
            "mercado previsto",
            "mercado",
            "a melhor",
        }
    ]

    if not colunas_mercado:
        return df

    mask_excluir = pd.Series(False, index=df.index)
    for col in colunas_mercado:
        valores_norm = df[col].astype(str).map(normalizar_nome_coluna)
        mask_excluir = mask_excluir | valores_norm.isin(mercados_norm)

    if mask_excluir.any():
        df = df.loc[~mask_excluir].copy()

    return df


@st.cache_data(show_spinner=False)
def carregar_csv(url: str) -> pd.DataFrame:
    if not url or "COLE_AQUI" in url:
        return pd.DataFrame()
    df = pd.read_csv(url)
    df.columns = [str(c).strip() for c in df.columns]
    df = remover_colunas_excluidas(df)
    df = remover_mercados_excluidos(df)
    return df


def achar_coluna(df: pd.DataFrame, candidatos: List[str]) -> str | None:
    mapa = {str(c).strip().lower(): c for c in df.columns}
    for nome in candidatos:
        chave = nome.strip().lower()
        if chave in mapa:
            return mapa[chave]
    return None


def converter_numerico_serie(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str)
        .str.replace("%", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip(),
        errors="coerce",
    )


def card_metrica(rotulo: str, valor: str):
    st.markdown(
        f"""
        <div class='card-metrica'>
            <div class='rotulo'>{rotulo}</div>
            <div class='valor'>{valor}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# PREPARAÇÃO DAS BASES
# =========================================================
def preparar_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()

    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.strip()

    df = remover_mercados_excluidos(df)

    col_odd = achar_coluna(df, ["Odd Ofertada"])
    col_valor = achar_coluna(df, ["Valor esperado"])
    col_saldo = achar_coluna(df, ["Saldo entre odd ofertada e esperada", "Saldo entre odd ofertada e valor esperado"])
    col_chance = achar_coluna(df, ["Previsão de chance", "Previsao de chance", "Chance"])
    col_stats = achar_coluna(df, ["Estatisticas Ultimos Jogos", "Estatísticas Ultimos Jogos"])

    if col_odd:
        df[col_odd] = converter_numerico_serie(df[col_odd])
    if col_valor:
        df[col_valor] = converter_numerico_serie(df[col_valor])
    if col_chance:
        df[col_chance] = converter_numerico_serie(df[col_chance])
    if col_saldo:
        df[col_saldo] = converter_numerico_serie(df[col_saldo])
    elif col_odd and col_valor:
        df["Saldo entre odd ofertada e esperada"] = df[col_odd] - df[col_valor]

    if col_stats:
        stats_num = converter_numerico_serie(df[col_stats])
        if stats_num.notna().sum() >= max(20, int(len(df) * 0.2)):
            df[col_stats] = stats_num

    return df


def montar_targets_basicos(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()

    col_resultado = achar_coluna(df, ["Resultado"])
    col_ht = achar_coluna(df, ["HT"])
    col_prev = achar_coluna(df, ["A Mais Provavel", "Previsões", "Previsoes"])
    col_odd = achar_coluna(df, ["Odd Ofertada"])
    col_status = achar_coluna(df, ["Status"])

    if not col_resultado or not col_ht:
        return df

    def separar_placar(coluna: pd.Series):
        placar = coluna.astype(str).str.extract(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
        g1 = pd.to_numeric(placar[0], errors="coerce")
        g2 = pd.to_numeric(placar[1], errors="coerce")
        return g1, g2

    df["FT_Home_Goals"], df["FT_Away_Goals"] = separar_placar(df[col_resultado])
    df["HT_Home_Goals"], df["HT_Away_Goals"] = separar_placar(df[col_ht])
    df["ST_Home_Goals"] = df["FT_Home_Goals"] - df["HT_Home_Goals"]
    df["ST_Away_Goals"] = df["FT_Away_Goals"] - df["HT_Away_Goals"]
    df["FT_Total_Goals"] = df["FT_Home_Goals"] + df["FT_Away_Goals"]
    df["HT_Total_Goals"] = df["HT_Home_Goals"] + df["HT_Away_Goals"]
    df["ST_Total_Goals"] = df["ST_Home_Goals"] + df["ST_Away_Goals"]
    df["FT_Goal_Diff"] = df["FT_Home_Goals"] - df["FT_Away_Goals"]

    targets = {
        "Menos de 1.5 gols 2° tempo": (df["ST_Total_Goals"] < 2).astype(int),
        "Ambas as equipes marcarem (Sim)": (((df["FT_Home_Goals"] > 0) & (df["FT_Away_Goals"] > 0))).astype(int),
        "Mais de 2.5 gols": (df["FT_Total_Goals"] >= 3).astype(int),
        "Menos de 2.5 gols 2° tempo": (df["ST_Total_Goals"] < 3).astype(int),
        "Menos de 1.5 gols 1° tempo": (df["HT_Total_Goals"] < 2).astype(int),
        "Mais de 1.5 gols": (df["FT_Total_Goals"] >= 2).astype(int),
        "Empate ou visitante para vencer": (df["FT_Goal_Diff"] <= 0).astype(int),
        "Mais de 1.5 gols 2° tempo": (df["ST_Total_Goals"] >= 2).astype(int),
        "Casa para vencer": (df["FT_Goal_Diff"] > 0).astype(int),
        "Menos de 2.5 gols 1° tempo": (df["HT_Total_Goals"] < 3).astype(int),
        "Mais de 0.5 gols 1° tempo": (df["HT_Total_Goals"] >= 1).astype(int),
        "Casa para vencer ou empate": (df["FT_Goal_Diff"] >= 0).astype(int),
        "Menos de 2.5 gols": (df["FT_Total_Goals"] < 3).astype(int),
        "Mais de 0.5 gols 2° tempo": (df["ST_Total_Goals"] >= 1).astype(int),
        "Visitante para vencer": (df["FT_Goal_Diff"] < 0).astype(int),
        "Menos de 3.5 gols": (df["FT_Total_Goals"] < 4).astype(int),
        "Menos de 0.5 gols 2° tempo": (df["ST_Total_Goals"] < 1).astype(int),
        "Mais de 3.5 gols": (df["FT_Total_Goals"] >= 4).astype(int),
        "Menos de 4.5 gols": (df["FT_Total_Goals"] < 5).astype(int),
        "Mais de 1.5 gols 1° tempo": (df["HT_Total_Goals"] >= 2).astype(int),
        "Menos de 1.5 gols": (df["FT_Total_Goals"] < 2).astype(int),
        "Menos de 0.5 gols 1° tempo": (df["HT_Total_Goals"] < 1).astype(int),
    }

    if col_prev and col_odd:
        df["Target_Real"] = np.nan
        for mercado, serie in targets.items():
            mask = df[col_prev] == mercado
            if mask.any():
                df.loc[mask, "Target_Real"] = serie.loc[mask]

        df["Profit_Odd_Ofertada"] = np.where(
            df["Target_Real"] == 1,
            df[col_odd] - 1,
            np.where(df["Target_Real"] == 0, -1, np.nan),
        )

    # histórico/backtest só com jogos terminados
    if col_status and col_status in df.columns:
        df = df[df[col_status].astype(str).str.upper() == "FT"].copy()
    else:
        df = df.dropna(subset=["FT_Home_Goals", "FT_Away_Goals", "HT_Home_Goals", "HT_Away_Goals"]).copy()

    return df


def resumo_backtest(df_hist: pd.DataFrame) -> Dict[str, object]:
    if df_hist.empty or "Profit_Odd_Ofertada" not in df_hist.columns:
        return {"entradas": 0, "lucro": 0.0, "dd": 0.0, "pf": 0.0, "curva": pd.Series(dtype=float)}

    base = df_hist.dropna(subset=["Profit_Odd_Ofertada"]).copy()
    if base.empty:
        return {"entradas": 0, "lucro": 0.0, "dd": 0.0, "pf": 0.0, "curva": pd.Series(dtype=float)}

    curva = base["Profit_Odd_Ofertada"].cumsum()
    pico = curva.cummax()
    dd = float((curva - pico).min()) if len(curva) else 0.0
    ganhos = base.loc[base["Profit_Odd_Ofertada"] > 0, "Profit_Odd_Ofertada"].sum()
    perdas = abs(base.loc[base["Profit_Odd_Ofertada"] < 0, "Profit_Odd_Ofertada"].sum())
    pf = float(ganhos / perdas) if perdas > 0 else 0.0

    return {
        "entradas": int(len(base)),
        "lucro": float(base["Profit_Odd_Ofertada"].sum()),
        "dd": dd,
        "pf": pf,
        "curva": curva,
    }


# =========================================================
# BACKTEST CRUZADO
# =========================================================
def criar_faixas_numericas(
    df: pd.DataFrame,
    col: str,
    modo: str,
    qtd_bins: int = 4,
) -> pd.Series:
    s = pd.to_numeric(df[col], errors="coerce")
    if s.dropna().nunique() < 2:
        return pd.Series(np.nan, index=df.index, dtype=object)

    try:
        if modo == "Quartis":
            return pd.qcut(s, q=min(qtd_bins, s.dropna().nunique()), duplicates="drop").astype(str)
        if modo == "Quintis":
            return pd.qcut(s, q=min(5, s.dropna().nunique()), duplicates="drop").astype(str)
        return pd.cut(s, bins=qtd_bins, include_lowest=True, duplicates="drop").astype(str)
    except Exception:
        return pd.Series(np.nan, index=df.index, dtype=object)


def score_final(roi: float, pf: float, winrate: float, qtd: int, dd: float) -> float:
    return (
        roi * 0.40
        + pf * 25 * 0.25
        + winrate * 0.10
        + np.log1p(max(qtd, 0)) * 8 * 0.15
        - abs(dd) * 0.10
    )


def calcular_metricas_grupo(df: pd.DataFrame) -> Dict[str, float]:
    base = df.dropna(subset=["Profit_Odd_Ofertada", "Target_Real"]).copy()
    qtd = len(base)
    if qtd == 0:
        return {
            "Qtd_Entradas": 0,
            "Acertos": 0,
            "Erros": 0,
            "Winrate_%": np.nan,
            "Odd_Media": np.nan,
            "Lucro_Total": np.nan,
            "ROI_%": np.nan,
            "DD_Max": np.nan,
            "Profit_Factor": np.nan,
            "Score_Final": np.nan,
        }

    acertos = int(base["Target_Real"].sum())
    erros = qtd - acertos
    winrate = float(base["Target_Real"].mean() * 100)
    odd_media = float(base["Odd Ofertada"].mean()) if "Odd Ofertada" in base.columns else np.nan
    lucro = float(base["Profit_Odd_Ofertada"].sum())
    roi = float((lucro / qtd) * 100)
    curva = base["Profit_Odd_Ofertada"].cumsum()
    pico = curva.cummax()
    dd_max = float((curva - pico).min())
    ganhos = base.loc[base["Profit_Odd_Ofertada"] > 0, "Profit_Odd_Ofertada"].sum()
    perdas = abs(base.loc[base["Profit_Odd_Ofertada"] < 0, "Profit_Odd_Ofertada"].sum())
    pf = float(ganhos / perdas) if perdas > 0 else np.nan
    score = float(score_final(roi, 0 if np.isnan(pf) else pf, winrate, qtd, dd_max))

    return {
        "Qtd_Entradas": qtd,
        "Acertos": acertos,
        "Erros": erros,
        "Winrate_%": round(winrate, 2),
        "Odd_Media": round(odd_media, 2) if not np.isnan(odd_media) else np.nan,
        "Lucro_Total": round(lucro, 2),
        "ROI_%": round(roi, 2),
        "DD_Max": round(dd_max, 2),
        "Profit_Factor": round(pf, 2) if not np.isnan(pf) else np.nan,
        "Score_Final": round(score, 2),
    }


def parse_faixas_texto(faixas_texto: str) -> List[Tuple[str, str]]:
    pares = []
    if not faixas_texto or not isinstance(faixas_texto, str):
        return pares

    partes = [p.strip() for p in faixas_texto.split(" | ") if p.strip()]
    for parte in partes:
        if ": " in parte:
            var, faixa = parte.split(": ", 1)
            pares.append((var.strip(), faixa.strip()))
    return pares


def aplicar_filtro_faixa_textual(df: pd.DataFrame, nome_variavel: str, faixa_texto: str) -> pd.DataFrame:
    mapa_variaveis = {
        "Estatisticas Ultimos Jogos": achar_coluna(df, ["Estatisticas Ultimos Jogos", "Estatísticas Ultimos Jogos"]),
        "Previsão de chance": achar_coluna(df, ["Previsão de chance", "Previsao de chance", "Chance"]),
        "Odd Ofertada": achar_coluna(df, ["Odd Ofertada"]),
        "Valor esperado": achar_coluna(df, ["Valor esperado"]),
        "Saldo entre odd ofertada e esperada": achar_coluna(df, ["Saldo entre odd ofertada e esperada", "Saldo entre odd ofertada e valor esperado"]),
    }

    col_real = mapa_variaveis.get(nome_variavel)
    if not col_real or col_real not in df.columns:
        return df.iloc[0:0].copy()

    serie = pd.to_numeric(df[col_real], errors="coerce")

    m = re.match(r"^([\(\[])\s*([-+]?\d*\.?\d+)\s*,\s*([-+]?\d*\.?\d+)\s*([\)\]])$", faixa_texto)
    if not m:
        return df.iloc[0:0].copy()

    left_bracket, a_str, b_str, right_bracket = m.groups()
    a = float(a_str)
    b = float(b_str)

    cond_left = serie >= a if left_bracket == "[" else serie > a
    cond_right = serie <= b if right_bracket == "]" else serie < b

    return df[cond_left & cond_right].copy()


def filtrar_jogos_do_dia_por_metodologia(df_jogos_dia: pd.DataFrame, mercado: str, faixas_txt: str) -> pd.DataFrame:
    if df_jogos_dia.empty or not faixas_txt:
        return pd.DataFrame()

    base = df_jogos_dia.copy()

    # primeiro filtra o mesmo mercado/previsão, se a coluna existir
    col_prev = achar_coluna(base, ["A Mais Provavel", "Previsões", "Previsoes"])
    if col_prev and mercado and mercado != "Todos":
        base = base[base[col_prev] == mercado].copy()

    pares = parse_faixas_texto(faixas_txt)
    if not pares:
        return pd.DataFrame()

    filtrado = base.copy()
    for nome_var, faixa in pares:
        filtrado = aplicar_filtro_faixa_textual(filtrado, nome_var, faixa)
        if filtrado.empty:
            break

    return filtrado


@st.cache_data(show_spinner=False)
def rodar_backtest_cruzado(
    df_hist: pd.DataFrame,
    mercado: str,
    liga: str,
    min_entradas: int,
    roi_min: float,
    dd_max_aceitavel: float,
    pf_min: float,
    variaveis_escolhidas: Tuple[str, ...],
    profundidade: int,
    modo_faixa: str,
) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    if df_hist.empty:
        return pd.DataFrame(), {}

    base = df_hist.copy()

    col_prev = achar_coluna(base, ["A Mais Provavel", "Previsões", "Previsoes"])
    col_liga = achar_coluna(base, ["League", "Liga"])

    if mercado != "Todos" and col_prev:
        base = base[base[col_prev] == mercado].copy()
    if liga != "Todas" and col_liga:
        base = base[base[col_liga] == liga].copy()

    base = base.dropna(subset=["Profit_Odd_Ofertada", "Target_Real"]).copy()
    if base.empty:
        return pd.DataFrame(), {}

    mapa_variaveis = {
        "Estatisticas Ultimos Jogos": achar_coluna(base, ["Estatisticas Ultimos Jogos", "Estatísticas Ultimos Jogos"]),
        "Previsão de chance": achar_coluna(base, ["Previsão de chance", "Previsao de chance", "Chance"]),
        "Odd Ofertada": achar_coluna(base, ["Odd Ofertada"]),
        "Valor esperado": achar_coluna(base, ["Valor esperado"]),
        "Saldo entre odd ofertada e esperada": achar_coluna(base, ["Saldo entre odd ofertada e esperada", "Saldo entre odd ofertada e valor esperado"]),
    }

    faixas_criadas = []
    for nome_var in variaveis_escolhidas:
        col_real = mapa_variaveis.get(nome_var)
        if not col_real or col_real not in base.columns:
            continue

        if pd.api.types.is_numeric_dtype(base[col_real]):
            col_faixa = f"FAIXA__{nome_var}"
            base[col_faixa] = criar_faixas_numericas(base, col_real, modo_faixa)
            faixas_criadas.append((nome_var, col_faixa))
        else:
            col_faixa = f"FAIXA__{nome_var}"
            base[col_faixa] = base[col_real].astype(str).replace({"nan": np.nan})
            faixas_criadas.append((nome_var, col_faixa))

    if not faixas_criadas:
        return pd.DataFrame(), {}

    profundidade = max(1, min(profundidade, len(faixas_criadas)))

    resultados = []
    detalhes_grupos: Dict[str, pd.DataFrame] = {}

    for tamanho in range(1, profundidade + 1):
        for combo in itertools.combinations(faixas_criadas, tamanho):
            nomes_vars = [x[0] for x in combo]
            cols_faixa = [x[1] for x in combo]

            temp = base.dropna(subset=cols_faixa).copy()
            if temp.empty:
                continue

            agrupado = temp.groupby(cols_faixa, dropna=False)
            for chaves, grupo in agrupado:
                if not isinstance(chaves, tuple):
                    chaves = (chaves,)

                metricas = calcular_metricas_grupo(grupo)
                if metricas["Qtd_Entradas"] < min_entradas:
                    continue
                if pd.notna(metricas["ROI_%"]) and metricas["ROI_%"] < roi_min:
                    continue
                if pd.notna(metricas["DD_Max"]) and metricas["DD_Max"] < dd_max_aceitavel:
                    continue
                if pd.notna(metricas["Profit_Factor"]) and metricas["Profit_Factor"] < pf_min:
                    continue

                faixas_txt = []
                for nome_var, chave in zip(nomes_vars, chaves):
                    faixas_txt.append(f"{nome_var}: {chave}")

                chave_grupo = " | ".join(faixas_txt)
                detalhes_grupos[chave_grupo] = grupo.copy()

                resultados.append(
                    {
                        "Variáveis": " + ".join(nomes_vars),
                        "Faixas": chave_grupo,
                        **metricas,
                    }
                )

    if not resultados:
        return pd.DataFrame(), {}

    resumo = pd.DataFrame(resultados)
    resumo = resumo.sort_values(
        ["Score_Final", "ROI_%", "Lucro_Total", "Winrate_%"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)

    return resumo, detalhes_grupos




# =========================================================
# DASHBOARD PRINCIPAL — JOGOS DO DIA
# =========================================================
def score_operacional_dashboard(row, col_chance, col_odd, col_odd_justa, col_saldo):
    pts = 0
    chance = row[col_chance] if col_chance and col_chance in row.index else np.nan
    odd = row[col_odd] if col_odd and col_odd in row.index else np.nan
    odd_justa = row[col_odd_justa] if col_odd_justa and col_odd_justa in row.index else np.nan
    saldo = row[col_saldo] if col_saldo and col_saldo in row.index else np.nan

    if pd.notna(chance):
        if chance >= 75:
            pts += 4
        elif chance >= 70:
            pts += 3
        elif chance >= 65:
            pts += 2
        elif chance >= 58:
            pts += 1

    if pd.notna(saldo):
        if saldo >= 0.40:
            pts += 4
        elif saldo >= 0.30:
            pts += 3
        elif saldo >= 0.15:
            pts += 2
        elif saldo >= 0.05:
            pts += 1
        elif saldo < 0:
            pts -= 2

    if pd.notna(odd):
        if 1.70 <= odd <= 2.20:
            pts += 2
        elif 1.55 <= odd < 1.70 or 2.20 < odd <= 2.50:
            pts += 1
        elif odd < 1.45:
            pts -= 1

    if pd.notna(odd) and pd.notna(odd_justa):
        if odd > odd_justa:
            pts += 2
        elif odd < odd_justa:
            pts -= 1

    return max(0, min(100, pts * 8))


def classificar_sinal_dashboard(score: float) -> str:
    if score >= 80:
        return 'FORTE'
    if score >= 60:
        return 'BOA'
    if score >= 40:
        return 'OBSERVAR'
    return 'EVITAR'


def preparar_dashboard_dia(df_pagina2: pd.DataFrame) -> pd.DataFrame:
    if df_pagina2.empty:
        return pd.DataFrame()

    df = preparar_dataframe(df_pagina2.copy())

    col_liga = achar_coluna(df, ['League', 'Liga'])
    col_casa = achar_coluna(df, ['Home Team', 'Casa'])
    col_visitante = achar_coluna(df, ['Visitor Team', 'Visitante'])
    col_mercado = achar_coluna(df, ['A Mais Provavel', 'A Mais Provável', 'Previsões', 'Previsoes', 'Mercado'])
    col_chance = achar_coluna(df, ['Previsão de chance', 'Previsao de chance', 'Chance'])
    col_odd = achar_coluna(df, ['Odd Ofertada', 'Odd'])
    col_odd_justa = achar_coluna(df, ['Valor esperado', 'Odd Justa', 'Odd Esperada'])
    col_saldo = achar_coluna(df, ['Saldo entre odd ofertada e esperada', 'Saldo entre odd ofertada e valor esperado', 'Saldo', 'Edge'])

    if not col_odd_justa and col_chance:
        df['Odd Justa Calc'] = np.where(df[col_chance] > 0, 100 / df[col_chance], np.nan)
        col_odd_justa = 'Odd Justa Calc'

    if not col_saldo and col_odd and col_odd_justa:
        df['Saldo Calc'] = df[col_odd] - df[col_odd_justa]
        col_saldo = 'Saldo Calc'

    df['Score_Operacional'] = df.apply(
        lambda row: score_operacional_dashboard(row, col_chance, col_odd, col_odd_justa, col_saldo),
        axis=1,
    )
    df['Sinal'] = df['Score_Operacional'].apply(classificar_sinal_dashboard)

    out = pd.DataFrame()
    out['Liga'] = df[col_liga] if col_liga else '-'
    out['Casa'] = df[col_casa] if col_casa else '-'
    out['Visitante'] = df[col_visitante] if col_visitante else '-'
    out['Mercado Previsto'] = df[col_mercado] if col_mercado else '-'
    out['Chance'] = pd.to_numeric(df[col_chance], errors='coerce') if col_chance else np.nan
    out['Odd'] = pd.to_numeric(df[col_odd], errors='coerce') if col_odd else np.nan
    out['Odd Justa'] = pd.to_numeric(df[col_odd_justa], errors='coerce') if col_odd_justa else np.nan
    out['Saldo'] = pd.to_numeric(df[col_saldo], errors='coerce') if col_saldo else np.nan
    out['Score_Operacional'] = df['Score_Operacional']
    out['Sinal'] = df['Sinal']
    return out


def render_dashboard_principal(df_pagina2: pd.DataFrame):
    st.markdown("<div class='painel-titulo'>Dashboard — Jogos do Dia</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='painel-subtitulo'>Painel principal no estilo do backtest, mas focado nas previsões, valor e seleção operacional dos jogos do dia.</div>",
        unsafe_allow_html=True,
    )
    st.markdown("<span class='fonte-badge'>Página2 — Jogos do dia / Previsões</span>", unsafe_allow_html=True)

    df_dash = preparar_dashboard_dia(df_pagina2)
    if df_dash.empty:
        st.warning('Não há dados suficientes na Página2 para montar o dashboard.')
        return

    with st.sidebar:
        st.markdown('---')
        st.markdown('### Dashboard — Filtros rápidos')
        mercados_dash = ['Todos'] + sorted([x for x in df_dash['Mercado Previsto'].dropna().astype(str).unique() if str(x).strip()])
        ligas_dash = ['Todas'] + sorted([x for x in df_dash['Liga'].dropna().astype(str).unique() if str(x).strip()])
        mercado_dash = st.selectbox('Mercado do dia', mercados_dash, key='mercado_dash')
        liga_dash = st.selectbox('Liga do dia', ligas_dash, key='liga_dash')
        chance_min_dash = st.slider('Chance mínima', 0, 100, 55, 1, key='chance_dash')
        odd_min_dash, odd_max_dash = st.slider('Faixa de odd', 1.00, 10.00, (1.70, 2.20), 0.01, key='odd_dash')
        score_min_dash = st.slider('Score operacional mínimo', 0, 100, 40, 1, key='score_dash')
        somente_valor_dash = st.checkbox('Somente saldo positivo', value=False, key='valor_dash')
        somente_aprovados_dash = st.checkbox('Somente FORTE/BOA', value=False, key='aprovados_dash')

    df_f = df_dash.copy()
    if mercado_dash != 'Todos':
        df_f = df_f[df_f['Mercado Previsto'].astype(str) == mercado_dash]
    if liga_dash != 'Todas':
        df_f = df_f[df_f['Liga'].astype(str) == liga_dash]
    df_f = df_f[df_f['Chance'].fillna(0) >= chance_min_dash]
    df_f = df_f[(df_f['Odd'].fillna(0) >= odd_min_dash) & (df_f['Odd'].fillna(0) <= odd_max_dash)]
    df_f = df_f[df_f['Score_Operacional'].fillna(0) >= score_min_dash]
    if somente_valor_dash:
        df_f = df_f[df_f['Saldo'].fillna(-999) > 0]
    if somente_aprovados_dash:
        df_f = df_f[df_f['Sinal'].isin(['FORTE', 'BOA'])]
    df_f = df_f.sort_values(['Score_Operacional', 'Saldo'], ascending=[False, False], na_position='last')

    total_jogos = len(df_dash)
    total_filtrados = len(df_f)
    chance_media = df_f['Chance'].mean() if not df_f.empty else np.nan
    odd_media = df_f['Odd'].mean() if not df_f.empty else np.nan
    melhor_saldo = df_f['Saldo'].max() if not df_f.empty else np.nan
    mercado_lider = df_f['Mercado Previsto'].mode().iloc[0] if not df_f.empty and not df_f['Mercado Previsto'].dropna().empty else '-'

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1: card_metrica('Jogos do dia', f'{total_jogos}')
    with c2: card_metrica('Aprovados', f'{total_filtrados}')
    with c3: card_metrica('Chance média', '-' if pd.isna(chance_media) else f'{chance_media:.1f}%')
    with c4: card_metrica('Odd média', '-' if pd.isna(odd_media) else f'{odd_media:.2f}')
    with c5: card_metrica('Melhor saldo', '-' if pd.isna(melhor_saldo) else f'{melhor_saldo:.2f}')
    with c6: card_metrica('Mercado líder', mercado_lider)

    col_main, col_side = st.columns([2.15, 1.0])
    with col_main:
        st.markdown("<div class='painel-bloco'>", unsafe_allow_html=True)
        st.markdown('### Tabela principal de previsões')
        if df_f.empty:
            st.warning('Nenhum jogo encontrado com os filtros atuais.')
        else:
            view = df_f.copy()
            view['Chance'] = view['Chance'].map(lambda x: f'{x:.1f}%' if pd.notna(x) else '-')
            for c in ['Odd', 'Odd Justa', 'Saldo']:
                view[c] = view[c].map(lambda x: f'{x:.2f}' if pd.notna(x) else '-')
            view['Score_Operacional'] = view['Score_Operacional'].map(lambda x: int(x) if pd.notna(x) else 0)
            st.dataframe(view, use_container_width=True, height=430)
        st.markdown('</div>', unsafe_allow_html=True)

    with col_side:
        st.markdown("<div class='painel-bloco'>", unsafe_allow_html=True)
        st.markdown('### Top oportunidades')
        if df_f.empty:
            st.info('Sem jogos após os filtros.')
        else:
            top_df = df_f[['Casa', 'Visitante', 'Mercado Previsto', 'Chance', 'Odd', 'Score_Operacional', 'Sinal']].head(5).copy()
            top_df['Chance'] = top_df['Chance'].map(lambda x: f'{x:.1f}%' if pd.notna(x) else '-')
            top_df['Odd'] = top_df['Odd'].map(lambda x: f'{x:.2f}' if pd.notna(x) else '-')
            top_df['Score_Operacional'] = top_df['Score_Operacional'].map(lambda x: int(x) if pd.notna(x) else 0)
            st.dataframe(top_df, use_container_width=True, height=230)
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        st.markdown("<div class='painel-bloco'>", unsafe_allow_html=True)
        st.markdown('### Resumo por mercado')
        if df_f.empty:
            st.info('Sem dados suficientes para resumo por mercado.')
        else:
            resumo_mercado = df_f.groupby('Mercado Previsto', dropna=False).size().reset_index(name='Jogos').sort_values('Jogos', ascending=False)
            st.dataframe(resumo_mercado, use_container_width=True, height=180)
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown("<div class='painel-bloco'>", unsafe_allow_html=True)
    st.markdown('### Detalhe do jogo selecionado')
    if not df_f.empty:
        opcoes_jogo = (df_f['Casa'].astype(str) + ' x ' + df_f['Visitante'].astype(str)).tolist()
        jogo_sel = st.selectbox('Escolha um jogo para detalhar', opcoes_jogo, index=0, key='jogo_detalhe_dash')
        row = df_f.iloc[opcoes_jogo.index(jogo_sel)]

        d1, d2 = st.columns([2, 1])
        with d1:
            st.markdown(f"**{row.get('Casa', '-')} x {row.get('Visitante', '-')}**")
            st.caption(f"{row.get('Liga', '-')} • Mercado: {row.get('Mercado Previsto', '-')}")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric('Chance prevista', '-' if pd.isna(row.get('Chance', np.nan)) else f"{row.get('Chance'):.1f}%")
            k2.metric('Odd ofertada', '-' if pd.isna(row.get('Odd', np.nan)) else f"{row.get('Odd'):.2f}")
            k3.metric('Odd justa', '-' if pd.isna(row.get('Odd Justa', np.nan)) else f"{row.get('Odd Justa'):.2f}")
            k4.metric('Saldo', '-' if pd.isna(row.get('Saldo', np.nan)) else f"{row.get('Saldo'):.2f}")

            leitura = []
            chance = row.get('Chance', np.nan)
            saldo = row.get('Saldo', np.nan)
            score = row.get('Score_Operacional', 0)
            if pd.notna(chance):
                leitura.append('chance prevista alta' if chance >= 70 else 'chance prevista aceitável' if chance >= 60 else 'chance prevista mais apertada')
            if pd.notna(saldo):
                leitura.append('odd acima do esperado com boa margem' if saldo >= 0.30 else 'odd ainda acima do esperado' if saldo > 0 else 'margem fraca ou negativa')
            leitura.append('score operacional forte' if score >= 80 else 'score operacional bom' if score >= 60 else 'score operacional mais fraco')
            st.success('Leitura operacional: ' + ', '.join(leitura) + '.')

        with d2:
            st.metric('Score operacional', f"{int(row.get('Score_Operacional', 0))}")
            st.metric('Sinal', row.get('Sinal', '-'))
            checklist = pd.DataFrame({
                'Critério': ['Chance alta', 'Odd competitiva', 'Saldo positivo', 'Faixa operacional'],
                'Status': [
                    'Sim' if pd.notna(row.get('Chance', np.nan)) and row.get('Chance', 0) >= 65 else 'Não',
                    'Sim' if pd.notna(row.get('Odd', np.nan)) and 1.70 <= row.get('Odd', 0) <= 2.20 else 'Não',
                    'Sim' if pd.notna(row.get('Saldo', np.nan)) and row.get('Saldo', 0) > 0 else 'Não',
                    'Sim' if row.get('Sinal', '') in ['FORTE', 'BOA'] else 'Não',
                ]
            })
            st.dataframe(checklist, use_container_width=True, height=180)
    else:
        st.warning('Nenhum jogo encontrado para detalhamento.')
    st.markdown('</div>', unsafe_allow_html=True)

# =========================================================
# CARGA DAS BASES
# =========================================================
df_pagina1 = montar_targets_basicos(preparar_dataframe(carregar_csv(URL_PAGINA1)))
df_pagina2 = preparar_dataframe(carregar_csv(URL_PAGINA2))

# =========================================================
# SIDEBAR GERAL
# =========================================================
st.sidebar.markdown("## GolEmNúmeros")
st.sidebar.markdown("### Histórico / Backtest Cruzado")

col_prev_p1 = achar_coluna(df_pagina1, ["A Mais Provavel", "Previsões", "Previsoes"])
col_liga_p1 = achar_coluna(df_pagina1, ["League", "Liga"])

mercados = ["Todos"]
if col_prev_p1 and not df_pagina1.empty:
    mercados += sorted([x for x in df_pagina1[col_prev_p1].dropna().astype(str).unique() if str(x).strip()])
mercado_sel = st.sidebar.selectbox("Mercado", mercados)

ligas = ["Todas"]
if col_liga_p1 and not df_pagina1.empty:
    ligas += sorted([x for x in df_pagina1[col_liga_p1].dropna().astype(str).unique() if str(x).strip()])
liga_sel = st.sidebar.selectbox("Liga", ligas)

min_entradas = st.sidebar.number_input("Min entradas", min_value=5, value=30, step=5)
roi_min = st.sidebar.number_input("ROI mínimo (%)", value=0.0, step=1.0)
dd_max_aceitavel = st.sidebar.number_input("DD máximo aceitável", value=-10.0, step=1.0)
pf_min = st.sidebar.number_input("Profit Factor mínimo", value=1.00, step=0.05, format="%.2f")

variaveis_escolhidas = st.sidebar.multiselect(
    "Variáveis para cruzar",
    [
        "Estatisticas Ultimos Jogos",
        "Previsão de chance",
        "Odd Ofertada",
        "Valor esperado",
        "Saldo entre odd ofertada e esperada",
    ],
    default=["Previsão de chance", "Odd Ofertada", "Saldo entre odd ofertada e esperada"],
)

profundidade = st.sidebar.selectbox("Profundidade do teste", [1, 2, 3], index=1)
modo_faixa = st.sidebar.selectbox("Modo de corte", ["Quartis", "Quintis", "Faixas automáticas"], index=0)
rodar_bt = st.sidebar.button("Rodar backtest cruzado", use_container_width=True)

st.sidebar.markdown("### Fontes")
st.sidebar.info("Página1 → Histórico / Backtest")
st.sidebar.info("Página2 → Jogos do dia / Previsões")

# =========================================================
# BACKTEST GERAL DA PÁGINA1
# =========================================================
back_geral = resumo_backtest(df_pagina1)

# =========================================================
# EXECUÇÃO DO BACKTEST CRUZADO
# =========================================================
if rodar_bt:
    resumo_cruzado, detalhes_grupos = rodar_backtest_cruzado(
        df_hist=df_pagina1,
        mercado=mercado_sel,
        liga=liga_sel,
        min_entradas=min_entradas,
        roi_min=roi_min,
        dd_max_aceitavel=dd_max_aceitavel,
        pf_min=pf_min,
        variaveis_escolhidas=tuple(variaveis_escolhidas),
        profundidade=int(profundidade),
        modo_faixa=modo_faixa,
    )
    st.session_state["resumo_cruzado"] = resumo_cruzado
    st.session_state["detalhes_grupos"] = detalhes_grupos

resumo_cruzado = st.session_state.get("resumo_cruzado", pd.DataFrame())
detalhes_grupos = st.session_state.get("detalhes_grupos", {})

aba_dashboard, aba_backtest = st.tabs(['Dashboard', 'Histórico / Backtest Cruzado'])

with aba_dashboard:
    render_dashboard_principal(df_pagina2)

with aba_backtest:
    st.markdown("<div class='painel-titulo'>Histórico — Backtest Cruzado</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='painel-subtitulo'>Cruza variáveis do histórico para encontrar grupos com melhor ROI, drawdown controlado, amostra mínima e profit factor consistente.</div>",
        unsafe_allow_html=True,
    )
    st.markdown("<span class='fonte-badge'>Página1 — Histórico / Backtest</span>", unsafe_allow_html=True)

    # =========================================================
    # CARDS DO TOPO
    # =========================================================
    if not resumo_cruzado.empty:
        melhor_roi = resumo_cruzado["ROI_%"].max()
        melhor_pf = resumo_cruzado["Profit_Factor"].max()
        melhor_dd = resumo_cruzado["DD_Max"].max()
        melhor_score = resumo_cruzado["Score_Final"].max()
        grupos = len(resumo_cruzado)
    else:
        melhor_roi = 0.0
        melhor_pf = 0.0
        melhor_dd = 0.0
        melhor_score = 0.0
        grupos = 0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        card_metrica("Entradas analisadas", f"{back_geral['entradas']}")
    with c2:
        card_metrica("Grupos encontrados", f"{grupos}")
    with c3:
        card_metrica("Melhor ROI", f"{melhor_roi:.2f}%")
    with c4:
        card_metrica("Melhor DD", f"{melhor_dd:.2f}")
    with c5:
        card_metrica("Melhor PF", f"{melhor_pf:.2f}")
    with c6:
        card_metrica("Melhor Score", f"{melhor_score:.2f}")

    # =========================================================
    # CORPO
    # =========================================================
    col_esq, col_dir = st.columns([1.7, 1.0])

    with col_esq:
        st.markdown("<div class='painel-bloco'>", unsafe_allow_html=True)
        st.markdown("### Ranking dos grupos")

        if resumo_cruzado.empty:
            st.info("Clique em **Rodar backtest cruzado** na sidebar para gerar os grupos.")
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            colunas_rank = [
                "Variáveis",
                "Faixas",
                "Qtd_Entradas",
                "Acertos",
                "Erros",
                "Winrate_%",
                "Odd_Media",
                "Lucro_Total",
                "ROI_%",
                "DD_Max",
                "Profit_Factor",
                "Score_Final",
            ]
            st.dataframe(resumo_cruzado[colunas_rank], use_container_width=True, height=480)
            st.markdown("</div>", unsafe_allow_html=True)

            opcoes_grupo = resumo_cruzado["Faixas"].tolist()
            grupo_escolhido = st.selectbox("Grupo para inspecionar", opcoes_grupo)
            grupo_df = detalhes_grupos.get(grupo_escolhido, pd.DataFrame()).copy()

            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
            st.markdown("<div class='painel-bloco'>", unsafe_allow_html=True)
            st.markdown("### Entradas do grupo selecionado")

            linha_grupo = resumo_cruzado.loc[resumo_cruzado["Faixas"] == grupo_escolhido].copy()
            variaveis_grupo = ""
            faixas_grupo = ""

            if not linha_grupo.empty:
                variaveis_grupo = str(linha_grupo.iloc[0]["Variáveis"])
                faixas_grupo = str(linha_grupo.iloc[0]["Faixas"])

            if grupo_df.empty:
                st.info("Sem detalhes para o grupo selecionado.")
            else:
                cols_show = []
                for nome in [
                    "League", "Liga", "Home Team", "Casa", "Visitor Team", "Visitante",
                    "A Mais Provavel", "Previsões", "Previsoes", "Odd Ofertada", "Previsão de chance",
                    "Valor esperado", "Saldo entre odd ofertada e esperada", "Profit_Odd_Ofertada", "Target_Real"
                ]:
                    c = achar_coluna(grupo_df, [nome])
                    if c and c not in cols_show:
                        cols_show.append(c)

                for extra in ["Profit_Odd_Ofertada", "Target_Real"]:
                    if extra in grupo_df.columns and extra not in cols_show:
                        cols_show.append(extra)

                st.dataframe(grupo_df[cols_show], use_container_width=True, height=340)

                st.markdown("---")
                st.markdown("### Filtros da metodologia selecionada")
                st.caption("Essas faixas serão usadas para encontrar os jogos do dia que se encaixam na metodologia.")

                col_f1, col_f2 = st.columns(2)

                with col_f1:
                    st.text_area(
                        "Variáveis",
                        value=variaveis_grupo,
                        height=120,
                        key="campo_variaveis_metodologia",
                    )

                with col_f2:
                    st.text_area(
                        "Faixas",
                        value=faixas_grupo,
                        height=120,
                        key="campo_faixas_metodologia",
                    )

                st.markdown("---")
                st.markdown("### Jogos do dia que se encaixam nessa metodologia")

                # usa o mercado selecionado no backtest para bater com o mesmo mercado na Página2
                df_jogos_metodologia = filtrar_jogos_do_dia_por_metodologia(
                    df_pagina2,
                    mercado_sel,
                    faixas_grupo
                )

                if df_jogos_metodologia.empty:
                    st.warning("Nenhum jogo do dia se encaixou nas faixas dessa metodologia.")
                else:
                    cols_jogos_dia = []
                    for nome in [
                        "League", "Liga", "Home Team", "Casa", "Visitor Team", "Visitante",
                        "A Mais Provavel", "Previsões", "Previsoes",
                        "Odd Ofertada", "Previsão de chance", "Valor esperado",
                        "Saldo entre odd ofertada e esperada"
                    ]:
                        c = achar_coluna(df_jogos_metodologia, [nome])
                        if c and c not in cols_jogos_dia:
                            cols_jogos_dia.append(c)

                    st.dataframe(
                        df_jogos_metodologia[cols_jogos_dia],
                        use_container_width=True,
                        height=260
                    )
                    st.success(f"{len(df_jogos_metodologia)} jogo(s) do dia encontrados para essa metodologia.")

            st.markdown("</div>", unsafe_allow_html=True)

    with col_dir:
        st.markdown("<div class='painel-bloco'>", unsafe_allow_html=True)
        st.markdown("### Curva do grupo")

        if resumo_cruzado.empty:
            st.info("A curva do grupo aparece aqui depois do primeiro teste.")
        else:
            grupo_curva = st.selectbox(
                "Curva de qual grupo?",
                resumo_cruzado["Faixas"].tolist(),
                key="grupo_curva_select",
            )
            grupo_df_curva = detalhes_grupos.get(grupo_curva, pd.DataFrame()).copy()

            if not grupo_df_curva.empty and "Profit_Odd_Ofertada" in grupo_df_curva.columns:
                curva = grupo_df_curva["Profit_Odd_Ofertada"].dropna().cumsum()
                st.line_chart(curva, height=260)
                met = calcular_metricas_grupo(grupo_df_curva)
                st.markdown(f"- **Entradas:** {met['Qtd_Entradas']}")
                st.markdown(f"- **ROI:** {met['ROI_%']}%")
                st.markdown(f"- **DD:** {met['DD_Max']}")
                st.markdown(f"- **PF:** {met['Profit_Factor']}")
            else:
                st.info("Sem profit suficiente para desenhar a curva.")

        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        st.markdown("<div class='painel-bloco'>", unsafe_allow_html=True)
        st.markdown("### Resumo rápido")
        st.markdown("- O ranking usa **ROI + PF + Winrate + Qtd - DD** no Score Final.")
        st.markdown("- ROI sozinho pode enganar; o filtro de **DD** e **amostra mínima** protege melhor.")
        st.markdown("- O ideal é aplicar os grupos aprovados depois na aba de jogos do dia.")
        st.markdown("</div>", unsafe_allow_html=True)

    # =========================================================
    # DEBUG LEVE
    # =========================================================
    with st.expander("Conferência das bases"):
        st.write("Página1:", df_pagina1.shape)
        st.write("Página2:", df_pagina2.shape)
        if not df_pagina1.empty:
            st.write("Colunas Página1:", list(df_pagina1.columns))
        if not df_pagina2.empty:
            st.write("Colunas Página2:", list(df_pagina2.columns))
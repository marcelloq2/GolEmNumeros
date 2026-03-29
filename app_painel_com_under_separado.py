import streamlit as st
import pandas as pd
import numpy as np
import re
import unicodedata
import requests
from io import StringIO
from pandas.errors import EmptyDataError

st.set_page_config(page_title="Painel Bonito - Pipeline Completo", layout="wide")

CSV_1 = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSF5WBP5KeBr6cVbAK0yH2IJf_luqoK90gOz1fj_VlS_hoAb4E6v_awCWO-bTi28I-mWYWEeewnhmTh/pub?gid=0&single=true&output=csv"
CSV_2 = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSF5WBP5KeBr6cVbAK0yH2IJf_luqoK90gOz1fj_VlS_hoAb4E6v_awCWO-bTi28I-mWYWEeewnhmTh/pub?gid=1768207754&single=true&output=csv"

MIN_LINHAS_FAIXA = 20
Q_FAIXAS = 5
TOP_VARIAVEIS_POR_ALVO = 20
TOP_LIVE = 100
MAPEAMENTO_MANUAL = {}
PALAVRAS_PROIBIDAS = [
    "result", "resultado", "score", "placar",
    "gols_casa_final", "gols_fora_final", "saldo_gols_final", "total_gols_final",
    "target_casa_vence", "target_visitante_vence", "target_casa_2mais", "target_visitante_2mais",
    "casa_vence", "visitante_vence", "empate", "casa_2mais", "visitante_2mais",
    "faixa_margem"
]

st.markdown("""
<style>
    .stApp {
        background: linear-gradient(180deg, #03101e 0%, #04182b 100%);
        color: #eef5ff;
    }
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #071726 0%, #09213a 100%);
        border-right: 1px solid rgba(255,255,255,0.08);
    }
    .block-container {max-width: 1450px; padding-top: 1.2rem;}
    .hero {
        background: linear-gradient(90deg, rgba(0,180,255,0.08), rgba(0,80,180,0.05));
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 22px;
        padding: 22px;
        margin-bottom: 16px;
    }
    .hero-title {font-size: 2.1rem; font-weight: 800; color: white;}
    .hero-sub {font-size: .95rem; color: #9db3ca; margin-top: 6px;}
    .metric-card {
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 18px;
        padding: 14px 16px;
        min-height: 98px;
    }
    .metric-label {font-size: .78rem; color: #8fa7c2;}
    .metric-value {font-size: 1.8rem; color: white; font-weight: 800; margin-top: 6px; line-height: 1.1;}
    .metric-sub {font-size: .78rem; color: #7188a3; margin-top: 4px;}
    .panel-box {
        background: rgba(255,255,255,0.035);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 22px;
        padding: 18px;
    }
    .section-title {font-size: 1.35rem; font-weight: 800; color: white; margin-bottom: 4px;}
    .section-sub {font-size: .9rem; color: #8fa7c2; margin-bottom: 14px;}
    .pill {
        display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: .72rem; font-weight: 700;
        border: 1px solid rgba(255,255,255,.10); background: rgba(255,255,255,0.04); color: #dfeafb; margin-right: 6px;
    }
    .pill-elite {background: rgba(16,185,129,.10); border-color: rgba(16,185,129,.28); color: #86efac;}
    .pill-forte {background: rgba(6,182,212,.10); border-color: rgba(6,182,212,.28); color: #67e8f9;}
    .pill-moderado {background: rgba(245,158,11,.10); border-color: rgba(245,158,11,.28); color: #fcd34d;}
    .pill-neutro {background: rgba(148,163,184,.10); border-color: rgba(148,163,184,.28); color: #cbd5e1;}
    .pill-casa {background: rgba(59,130,246,.10); border-color: rgba(59,130,246,.28); color: #93c5fd;}
    .pill-visitante {background: rgba(168,85,247,.10); border-color: rgba(168,85,247,.28); color: #d8b4fe;}
    .pill-equilibrado {background: rgba(148,163,184,.10); border-color: rgba(148,163,184,.28); color: #cbd5e1;}
    .detail-card {
        background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.03));
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 18px; padding: 14px 16px;
    }
    .detail-label {font-size: .78rem; color: #8fa7c2;}
    .detail-value {font-size: 1.65rem; font-weight: 800; color: white; margin-top: 6px;}
</style>
""", unsafe_allow_html=True)


def normalizar_texto(txt):
    if pd.isna(txt):
        return ""
    txt = str(txt).strip()
    txt = unicodedata.normalize("NFKD", txt).encode("ASCII", "ignore").decode("utf-8")
    txt = txt.lower().strip()
    txt = re.sub(r"\s+", " ", txt)
    return txt


def normalizar_coluna(col):
    col = normalizar_texto(col)
    col = col.replace("%", " pct ").replace("/", " ").replace("-", " ").replace(".", " ").replace("|", "_")
    col = re.sub(r"[^a-z0-9_ ]", "", col)
    col = re.sub(r"\s+", "_", col).strip("_")
    return col


def to_float_series(s):
    if s is None:
        return s
    s = s.astype(str)
    s = s.str.replace("%", "", regex=False)
    s = s.str.replace(r"(?<=\d)\.(?=\d{3}(\D|$))", "", regex=True)
    s = s.str.replace(",", ".", regex=False)
    s = s.str.replace(r"[^\d\.\-]", "", regex=True)
    return pd.to_numeric(s, errors="coerce")


def carregar_csv(url, nome="base"):
    resp = requests.get(url, timeout=40)
    resp.raise_for_status()
    texto = resp.text.strip()
    if not texto:
        raise ValueError(f"{nome} retornou vazio.")
    if "<html" in texto.lower() or "<!doctype html" in texto.lower():
        raise ValueError(f"{nome} retornou HTML em vez de CSV.")
    try:
        df = pd.read_csv(StringIO(texto))
    except EmptyDataError:
        raise ValueError(f"{nome} sem colunas legíveis.")
    if len(df.columns) == 0:
        raise ValueError(f"{nome} sem colunas.")
    df.columns = [normalizar_coluna(c) for c in df.columns]
    return df


def encontrar_coluna(df, candidatos):
    cols = df.columns.tolist()
    for cand in candidatos:
        cand_norm = normalizar_coluna(cand)
        for c in cols:
            if c == cand_norm:
                return c
    for cand in candidatos:
        cand_norm = normalizar_coluna(cand)
        for c in cols:
            if cand_norm in c:
                return c
    return None


def mapear_colunas_principais(df):
    candidatos = {
        "home_team": ["home_team", "casa", "mandante", "time_casa", "home", "equipe_casa"],
        "away_team": ["visitor_team", "away_team", "visitante", "fora", "time_visitante", "away", "equipe_fora"],
        "status": ["status", "situacao", "state"],
        "result": ["resultado", "result", "placar", "score", "final_score"],
        "gols_casa": ["result_home", "gols_casa", "gol_casa", "home_goals", "ft_home", "resultado_casa"],
        "gols_fora": ["result_visitor", "gols_visitante", "gol_visitante", "away_goals", "ft_away", "resultado_visitante"],
        "league": ["league", "liga", "campeonato"],
        "hour": ["hour", "hora", "time"],
        "odds_home_win": ["Odds casa para vencer", "odd casa para vencer", "odds_casa_para_vencer", "odd_casa", "odds casa", "home_win_odds", "odd_home_win", "match_odds_1", "odds_1", "1"],
        "odds_draw": ["Odds empate", "odd empate", "odds_empate", "draw_odds", "odd_draw", "match_odds_x", "odds_x", "x"],
        "odds_away_win": ["Odds visitante para vencer", "odd visitante para vencer", "odds_visitante_para_vencer", "odd_visitante", "odds visitante", "away_win_odds", "odd_away_win", "match_odds_2", "odds_2", "2"],
    }
    mapa = {}
    for chave, lista in candidatos.items():
        if chave in MAPEAMENTO_MANUAL:
            manual = normalizar_coluna(MAPEAMENTO_MANUAL[chave])
            mapa[chave] = manual if manual in df.columns else None
        else:
            mapa[chave] = encontrar_coluna(df, lista)
    return mapa


def extrair_gols_do_resultado(df, col_resultado):
    if col_resultado is None or col_resultado not in df.columns:
        return pd.Series([np.nan] * len(df)), pd.Series([np.nan] * len(df))
    txt = df[col_resultado].astype(str).str.extract(r"(\d+)\s*[-xX:]\s*(\d+)")
    gols_casa = pd.to_numeric(txt[0], errors="coerce")
    gols_fora = pd.to_numeric(txt[1], errors="coerce")
    return gols_casa, gols_fora


def criar_targets(df, mapa):
    gols_casa = None
    gols_fora = None
    if mapa.get("gols_casa") and mapa.get("gols_fora"):
        gols_casa = to_float_series(df[mapa["gols_casa"]])
        gols_fora = to_float_series(df[mapa["gols_fora"]])
    if gols_casa is None or gols_fora is None or gols_casa.notna().sum() == 0 or gols_fora.notna().sum() == 0:
        gc2, gf2 = extrair_gols_do_resultado(df, mapa.get("result"))
        if gols_casa is None or gols_casa.notna().sum() == 0:
            gols_casa = gc2
        if gols_fora is None or gols_fora.notna().sum() == 0:
            gols_fora = gf2
    df["gols_casa_final"] = gols_casa
    df["gols_fora_final"] = gols_fora
    df["saldo_gols_final"] = df["gols_casa_final"] - df["gols_fora_final"]
    df["total_gols_final"] = df["gols_casa_final"] + df["gols_fora_final"]
    df["target_casa_vence"] = np.where(df["saldo_gols_final"] > 0, 1, 0)
    df["target_visitante_vence"] = np.where(df["saldo_gols_final"] < 0, 1, 0)
    df["target_casa_2mais"] = np.where(df["saldo_gols_final"] >= 2, 1, 0)
    df["target_visitante_2mais"] = np.where(df["saldo_gols_final"] <= -2, 1, 0)
    return df


def identificar_colunas_numericas(df):
    numericas = []
    for c in df.columns:
        s = to_float_series(df[c])
        taxa_validos = s.notna().mean()
        unicos = s.nunique(dropna=True)
        if taxa_validos >= 0.50 and unicos >= 4:
            numericas.append(c)
    return numericas


def separar_colunas_casa_fora(colunas):
    pares, cols_set = [], set(colunas)
    padroes = [("casa", "visitante"), ("home", "visitor"), ("home", "away"), ("mandante", "visitante")]
    for c in colunas:
        for a, b in padroes:
            if a in c:
                candidato = c.replace(a, b)
                if candidato in cols_set:
                    pares.append((c, candidato))
    pares_unicos, vistos = [], set()
    for x, y in pares:
        chave = tuple(sorted([x, y]))
        if chave not in vistos and x != y:
            vistos.add(chave)
            pares_unicos.append((x, y))
    return pares_unicos


def coluna_proibida(nome_coluna):
    nome = normalizar_coluna(nome_coluna)
    return any(p in nome for p in PALAVRAS_PROIBIDAS)


def criar_variaveis_derivadas_validas(df, colunas_numericas):
    pares = separar_colunas_casa_fora(colunas_numericas)
    criadas = []
    for c1, c2 in pares:
        if coluna_proibida(c1) or coluna_proibida(c2):
            continue
        s1 = to_float_series(df[c1])
        s2 = to_float_series(df[c2])
        nome_diff = f"diff__{c1}__vs__{c2}"
        nome_soma = f"soma__{c1}__mais__{c2}"
        nome_ratio = f"ratio__{c1}__div__{c2}"
        df[nome_diff] = s1 - s2
        df[nome_soma] = s1 + s2
        df[nome_ratio] = np.where((s2.notna()) & (s2 != 0), s1 / s2, np.nan)
        criadas.extend([nome_diff, nome_soma, nome_ratio])
    return df, criadas, pares


def montar_variaveis_validas(df_hist, colunas_numericas, vars_criadas):
    candidatas = []
    for c in colunas_numericas:
        if not coluna_proibida(c):
            candidatas.append(c)
    for c in vars_criadas:
        if not coluna_proibida(c):
            candidatas.append(c)
    candidatas = list(dict.fromkeys(candidatas))
    variaveis_validas = []
    for c in candidatas:
        if c in df_hist.columns:
            s = pd.to_numeric(df_hist[c], errors="coerce")
            if s.notna().sum() >= MIN_LINHAS_FAIXA * 2 and s.nunique(dropna=True) >= 4:
                variaveis_validas.append(c)
    return variaveis_validas


def analisar_faixas_binario(df, var, alvo_binario, min_linhas=20, q_faixas=5):
    aux = df[[var, alvo_binario]].copy()
    aux[var] = pd.to_numeric(aux[var], errors="coerce")
    aux[alvo_binario] = pd.to_numeric(aux[alvo_binario], errors="coerce")
    aux = aux.dropna()
    if len(aux) < min_linhas * 2:
        return pd.DataFrame()
    try:
        n_q = min(q_faixas, aux[var].nunique())
        if n_q < 2:
            return pd.DataFrame()
        aux["faixa"] = pd.qcut(aux[var], q=n_q, duplicates="drop")
    except Exception:
        return pd.DataFrame()
    baseline = aux[alvo_binario].mean()
    resumo = (
        aux.groupby("faixa", observed=False)
        .agg(jogos=(alvo_binario, "size"), taxa_acerto=(alvo_binario, "mean"))
        .reset_index()
    )
    resumo = resumo[resumo["jogos"] >= min_linhas].copy()
    if resumo.empty:
        return pd.DataFrame()
    resumo["var"] = var
    resumo["baseline"] = baseline
    resumo["lift"] = resumo["taxa_acerto"] - baseline
    resumo["forca"] = resumo["lift"] * np.log1p(resumo["jogos"])
    return resumo.sort_values(["forca", "taxa_acerto", "jogos"], ascending=[False, False, False])


def analisar_alvo(df_hist, variaveis_validas, alvo_binario):
    todos = []
    for v in variaveis_validas:
        r = analisar_faixas_binario(df_hist, v, alvo_binario, MIN_LINHAS_FAIXA, Q_FAIXAS)
        if not r.empty:
            todos.append(r)
    if not todos:
        return pd.DataFrame()
    return pd.concat(todos, ignore_index=True).sort_values(["forca", "taxa_acerto", "jogos"], ascending=[False, False, False])


def criar_score_por_regras(df, regras, nome_score):
    df = df.copy()
    df[nome_score] = 0.0
    df[f"{nome_score}__regras"] = 0
    for _, row in regras.iterrows():
        var = row["var"]
        faixa = row["faixa"]
        lift = row["lift"]
        if var not in df.columns:
            continue
        try:
            left = faixa.left
            right = faixa.right
            mask = pd.to_numeric(df[var], errors="coerce").between(left, right, inclusive="right")
            df.loc[mask, nome_score] += lift
            df.loc[mask, f"{nome_score}__regras"] += 1
        except Exception:
            continue
    return df


def classificar_nivel(x):
    if pd.isna(x):
        return "Sem sinal"
    a = abs(x)
    if a < 0.03:
        return "Fraco"
    elif a < 0.08:
        return "Moderado"
    elif a < 0.15:
        return "Forte"
    return "Muito forte"


def criar_semaforo_oportunidades(df):
    df = df.copy()
    def definir_semaforo(row):
        vc, vm = row["vantagem_casa"], row["vantagem_2mais_casa"]
        if vc >= 0.12 and vm >= 0.08:
            return "🟢 Entrada forte casa"
        elif vc >= 0.08 and vm >= 0.03:
            return "🟡 Entrada moderada casa"
        elif vc >= 0.03:
            return "🟠 Observar casa"
        elif vc <= -0.12 and vm <= -0.08:
            return "🟢 Entrada forte visitante"
        elif vc <= -0.08 and vm <= -0.03:
            return "🟡 Entrada moderada visitante"
        elif vc <= -0.03:
            return "🟠 Observar visitante"
        return "🔴 Evitar"
    def prioridade(txt):
        mapa = {
            "🟢 Entrada forte casa": 1, "🟢 Entrada forte visitante": 1,
            "🟡 Entrada moderada casa": 2, "🟡 Entrada moderada visitante": 2,
            "🟠 Observar casa": 3, "🟠 Observar visitante": 3, "🔴 Evitar": 4,
        }
        return mapa.get(txt, 99)
    def mercado_operacional(txt):
        if txt == "🟢 Entrada forte casa":
            return "Casa vencer / Casa -1.5 / observar goleada"
        elif txt == "🟡 Entrada moderada casa":
            return "Casa vencer / Casa DNB / Casa -0.5"
        elif txt == "🟠 Observar casa":
            return "Observar domínio da casa no live"
        elif txt == "🟢 Entrada forte visitante":
            return "Visitante vencer / Visitante -1.5 / observar goleada"
        elif txt == "🟡 Entrada moderada visitante":
            return "Visitante vencer / Visitante DNB / Visitante -0.5"
        elif txt == "🟠 Observar visitante":
            return "Observar domínio do visitante no live"
        return "Evitar entrada em margem"
    df["semaforo_oportunidade"] = df.apply(definir_semaforo, axis=1)
    df["prioridade_operacional"] = df["semaforo_oportunidade"].apply(prioridade)
    df["mercado_operacional"] = df["semaforo_oportunidade"].apply(mercado_operacional)
    return df


def montar_painel_oportunidades(df):
    df = df.copy()
    df["vantagem_casa"] = df["score_casa_vence"] - df["score_visitante_vence"]
    df["vantagem_2mais_casa"] = df["score_casa_2mais"] - df["score_visitante_2mais"]
    def direcao(row):
        if row["vantagem_casa"] > 0.03:
            return "Casa"
        elif row["vantagem_casa"] < -0.03:
            return "Visitante"
        return "Equilibrado"
    df["direcao_prevista"] = df.apply(direcao, axis=1)
    df["nivel_forca_vencedor"] = df["vantagem_casa"].apply(classificar_nivel)
    df["nivel_forca_margem"] = df["vantagem_2mais_casa"].apply(classificar_nivel)
    def texto_margem(row):
        vc, vm = row["vantagem_casa"], row["vantagem_2mais_casa"]
        if vc > 0.03 and vm > 0.05:
            return "Casa com chance de vencer por 2+"
        elif vc > 0.03:
            return "Casa com tendência de vencer"
        elif vc < -0.03 and vm < -0.05:
            return "Visitante com chance de vencer por 2+"
        elif vc < -0.03:
            return "Visitante com tendência de vencer"
        return "Jogo equilibrado"
    df["leitura_final"] = df.apply(texto_margem, axis=1)
    def mercado(row):
        txt = row["leitura_final"]
        if txt == "Casa com chance de vencer por 2+":
            return "Casa vencer / Casa -1.5"
        elif txt == "Casa com tendência de vencer":
            return "Casa vencer / Casa DNB"
        elif txt == "Visitante com chance de vencer por 2+":
            return "Visitante vencer / Visitante -1.5"
        elif txt == "Visitante com tendência de vencer":
            return "Visitante vencer / Visitante DNB"
        return "Evitar margem / jogo equilibrado"
    df["mercado_sugerido"] = df.apply(mercado, axis=1)
    df["score_geral_oportunidade"] = df["vantagem_casa"].abs() * 0.60 + df["vantagem_2mais_casa"].abs() * 0.40
    df = criar_semaforo_oportunidades(df)
    return df


@st.cache_data(ttl=1800, show_spinner=False)
def rodar_pipeline_completo():
    df1 = carregar_csv(CSV_1, "ABA_1")
    try:
        df2 = carregar_csv(CSV_2, "ABA_2")
        aba2_ok = True
    except Exception:
        df2 = pd.DataFrame()
        aba2_ok = False

    mapa1 = mapear_colunas_principais(df1)
    if aba2_ok and not df2.empty:
        mapa2 = mapear_colunas_principais(df2)
        chaves_merge = []
        for k in ["home_team", "away_team", "hour", "league", "status"]:
            c1, c2 = mapa1.get(k), mapa2.get(k)
            if c1 and c2:
                chaves_merge.append((c1, c2))
        df2_aj = df2.copy()
        rename_2 = {c2: c1 for c1, c2 in chaves_merge if c1 != c2}
        df2_aj = df2_aj.rename(columns=rename_2)
        chaves_finais = [c1 for c1, _ in chaves_merge if c1 in df1.columns and c1 in df2_aj.columns]
        if len(chaves_finais) >= 2:
            cols_extras_2 = [c for c in df2_aj.columns if c not in chaves_finais]
            df_base = df1.merge(df2_aj[chaves_finais + cols_extras_2], on=chaves_finais, how="left")
        else:
            df_base = pd.concat([df1.reset_index(drop=True), df2.reset_index(drop=True)], axis=1)
    else:
        df_base = df1.copy()
    df_base = df_base.loc[:, ~df_base.columns.duplicated()].copy()

    mapa_base = mapear_colunas_principais(df_base)
    df_base = criar_targets(df_base, mapa_base)
    status_col = mapa_base.get("status")
    if status_col and status_col in df_base.columns:
        df_base[status_col] = df_base[status_col].astype(str).str.upper().str.strip()

    df_hist = df_base.copy()
    if status_col and status_col in df_hist.columns:
        df_hist = df_hist[df_hist[status_col] == "FT"].copy()
    df_hist = df_hist[df_hist["saldo_gols_final"].notna()].copy()

    if status_col and status_col in df_base.columns:
        df_ns = df_base[df_base[status_col] == "NS"].copy()
    else:
        df_ns = pd.DataFrame()

    colunas_numericas = identificar_colunas_numericas(df_hist)
    for c in colunas_numericas:
        df_hist[c] = to_float_series(df_hist[c])
        df_base[c] = to_float_series(df_base[c])
        if not df_ns.empty and c in df_ns.columns:
            df_ns[c] = to_float_series(df_ns[c])

    df_hist, vars_criadas_hist, pares_encontrados = criar_variaveis_derivadas_validas(df_hist, colunas_numericas)
    df_base, vars_criadas_base, _ = criar_variaveis_derivadas_validas(df_base, colunas_numericas)
    if not df_ns.empty:
        df_ns, vars_criadas_ns, _ = criar_variaveis_derivadas_validas(df_ns, colunas_numericas)
    variaveis_validas = montar_variaveis_validas(df_hist, colunas_numericas, vars_criadas_hist)

    res_casa_vence = analisar_alvo(df_hist, variaveis_validas, "target_casa_vence")
    res_visitante_vence = analisar_alvo(df_hist, variaveis_validas, "target_visitante_vence")
    res_casa_2mais = analisar_alvo(df_hist, variaveis_validas, "target_casa_2mais")
    res_visitante_2mais = analisar_alvo(df_hist, variaveis_validas, "target_visitante_2mais")

    if res_casa_vence.empty or res_visitante_vence.empty:
        raise ValueError("Não houve regras suficientes. Revise nomes de colunas e variáveis válidas.")

    top_casa_vence = res_casa_vence.head(TOP_VARIAVEIS_POR_ALVO).copy()
    top_visitante_vence = res_visitante_vence.head(TOP_VARIAVEIS_POR_ALVO).copy()
    top_casa_2mais = res_casa_2mais.head(TOP_VARIAVEIS_POR_ALVO).copy()
    top_visitante_2mais = res_visitante_2mais.head(TOP_VARIAVEIS_POR_ALVO).copy()

    for base_nome, base_df in [("hist", df_hist), ("base", df_base), ("ns", df_ns)]:
        if base_df.empty:
            continue
        base_df = criar_score_por_regras(base_df, top_casa_vence, "score_casa_vence")
        base_df = criar_score_por_regras(base_df, top_visitante_vence, "score_visitante_vence")
        base_df = criar_score_por_regras(base_df, top_casa_2mais, "score_casa_2mais")
        base_df = criar_score_por_regras(base_df, top_visitante_2mais, "score_visitante_2mais")
        if base_nome == "hist":
            df_hist = base_df
        elif base_nome == "base":
            df_base = base_df
        else:
            df_ns = base_df

    def montar_saida_oportunidades(df_origem: pd.DataFrame) -> pd.DataFrame:
        if df_origem is None or df_origem.empty:
            return pd.DataFrame()
        df_oportunidades = montar_painel_oportunidades(df_origem.copy())

        colunas_saida = []
        for c in [
            mapa_base.get("league"), mapa_base.get("hour"), mapa_base.get("home_team"),
            mapa_base.get("away_team"), mapa_base.get("status"), mapa_base.get("result"),
            mapa_base.get("gols_casa"), mapa_base.get("gols_fora"),
            mapa_base.get("odds_home_win"), mapa_base.get("odds_draw"), mapa_base.get("odds_away_win"),
        ]:
            if c and c in df_oportunidades.columns and c not in colunas_saida:
                colunas_saida.append(c)
        extras = [
            "score_casa_vence", "score_visitante_vence", "score_casa_2mais", "score_visitante_2mais",
            "vantagem_casa", "vantagem_2mais_casa", "direcao_prevista", "nivel_forca_vencedor", "nivel_forca_margem",
            "leitura_final", "mercado_sugerido", "semaforo_oportunidade", "prioridade_operacional", "mercado_operacional",
            "score_geral_oportunidade", "gols_casa_final", "gols_fora_final",
            "odds_casa_para_vencer", "odds_empate", "odds_visitante_para_vencer",
        ]
        for c in extras:
            if c in df_oportunidades.columns and c not in colunas_saida:
                colunas_saida.append(c)

        return df_oportunidades[colunas_saida].copy().sort_values(
            ["prioridade_operacional", "score_geral_oportunidade"], ascending=[True, False]
        ).head(TOP_LIVE)

    df_oportunidades_live = montar_saida_oportunidades(df_ns if not df_ns.empty else df_base)
    df_oportunidades_ft = montar_saida_oportunidades(df_hist)
    df_oportunidades_todos = montar_saida_oportunidades(df_base)

    def resumo_regras(df_regras, nome):
        if df_regras.empty:
            return pd.DataFrame()
        r = df_regras[["var", "faixa", "jogos", "taxa_acerto", "baseline", "lift", "forca"]].copy()
        r["alvo"] = nome
        return r

    resumo_top_regras = pd.concat([
        resumo_regras(top_casa_vence, "Casa vence"),
        resumo_regras(top_visitante_vence, "Visitante vence"),
        resumo_regras(top_casa_2mais, "Casa 2+"),
        resumo_regras(top_visitante_2mais, "Visitante 2+"),
    ], ignore_index=True)

    return {
        "df_oportunidades_live": df_oportunidades_live,
        "df_oportunidades_ft": df_oportunidades_ft,
        "df_oportunidades_todos": df_oportunidades_todos,
        "resumo_top_regras": resumo_top_regras,
        "variaveis_validas": pd.DataFrame({"variavel_modelagem": pd.Series(variaveis_validas)}),
        "df_ns": df_ns,
        "mapa_base": mapa_base,
        "df_base": df_base,
        "diagnostico": {
            "linhas_base": len(df_base),
            "linhas_hist": len(df_hist),
            "linhas_ns": len(df_ns),
            "num_numericas": len(colunas_numericas),
            "num_variaveis_validas": len(variaveis_validas),
            "num_pares": len(pares_encontrados),
        }
    }


def pill_status_html(status):
    classe = "pill-neutro"
    if status == "Elite":
        classe = "pill-elite"
    elif status == "Forte":
        classe = "pill-forte"
    elif status == "Moderado":
        classe = "pill-moderado"
    return f"<span class='pill {classe}'>{status}</span>"


def pill_direcao_html(d):
    classe = "pill-equilibrado"
    if d == "Casa":
        classe = "pill-casa"
    elif d == "Visitante":
        classe = "pill-visitante"
    return f"<span class='pill {classe}'>{d}</span>"


def classificar_status_visual(semaforo):
    t = str(semaforo)
    if "Entrada forte" in t:
        return "Elite"
    if "moderada" in t.lower():
        return "Forte"
    if "Observar" in t:
        return "Moderado"
    return "Neutro"




def formatar_hora_exibicao(valor):
    if pd.isna(valor):
        return "-"

    s = str(valor).strip()
    if not s:
        return "-"

    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"

    dig = re.sub(r"\D", "", s)

    if len(dig) == 12:
        hh = dig[8:10]
        mm = dig[10:12]
        if hh.isdigit() and mm.isdigit():
            hh_i = int(hh)
            mm_i = int(mm)
            if 0 <= hh_i <= 23 and 0 <= mm_i <= 59:
                return f"{hh_i:02d}:{mm_i:02d}"

    if len(dig) > 4:
        hh = dig[-4:-2]
        mm = dig[-2:]
        if hh.isdigit() and mm.isdigit():
            hh_i = int(hh)
            mm_i = int(mm)
            if 0 <= hh_i <= 23 and 0 <= mm_i <= 59:
                return f"{hh_i:02d}:{mm_i:02d}"

    if len(dig) == 4 and dig.isdigit():
        hh_i = int(dig[:2])
        mm_i = int(dig[2:])
        if 0 <= hh_i <= 23 and 0 <= mm_i <= 59:
            return f"{hh_i:02d}:{mm_i:02d}"

    return s


def fmt_score(v):
    try:
        if pd.isna(v):
            return "-"
    except Exception:
        pass
    try:
        return str(int(round(float(v))))
    except Exception:
        s = str(v).strip()
        return s if s else "-"

def fmt(v, nd=3):
    try:
        if pd.isna(v):
            return "-"
    except Exception:
        pass
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return str(v)



# =========================================================
# UNDER LIVE
# =========================================================
ODD_OVER25_MAX = 2.00

def simplificar(col):
    return re.sub(r"[^a-z0-9]", "", normalizar_coluna(col))

def ajustar_percentual_0_1(s):
    s = to_float_series(s)
    if s is None or s.dropna().empty:
        return s
    med = s.dropna().median()
    if med > 1:
        s = s / 100.0
    return s.clip(lower=0, upper=1)

def zscore_series(s):
    s = pd.to_numeric(s, errors="coerce")
    if s.dropna().nunique() <= 1:
        return pd.Series(np.zeros(len(s)), index=s.index)
    std = s.std(skipna=True)
    if pd.isna(std) or std == 0:
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - s.mean(skipna=True)) / std

def minmax_0_1(s):
    s = pd.to_numeric(s, errors="coerce")
    mn = s.min(skipna=True)
    mx = s.max(skipna=True)
    if pd.isna(mn) or pd.isna(mx) or mx == mn:
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - mn) / (mx - mn)

ALIASES_UNDER = {
    "media_0_15": ["madia_de_gols_0_15_minutos", "Média de gols 0-15' minutos"],
    "media_16_30": ["madia_de_gols_16_30_minutos", "Média de gols 16-30' minutos"],
    "media_31_45": ["madia_de_gols_31_45_minutos", "Média de gols 31-45' minutos"],
    "media_46_60": ["madia_de_gols_46_60_minutos", "Média de gols 46-60' minutos"],
    "media_61_75": ["madia_de_gols_61_75_minutos", "Média de gols 61-75' minutos"],
    "media_76_90": ["madia_de_gols_76_90_minutos", "Média de gols 76-90' minutos"],
    "precisao_casa": ["precisao_nos_chutes_no_alvo_casa", "Precisão nos chutes no alvo casa"],
    "precisao_visitante": ["precisao_nos_chutes_no_alvo_visitante", "Precisão nos chutes no alvo visitante"],
    "chutes_por_gol_casa": ["chutes_por_gol_casa", "Chutes por gol casa"],
    "chutes_por_gol_visitante": ["chutes_por_gol_visitante", "Chutes por gol visitante"],
    "chutes_gol_1t_casa": ["madia_de_chutes_no_gol_marcados_1ao_tempo_casa", "Média de chutes no gol marcados 1º tempo casa"],
    "chutes_gol_1t_visitante": ["madia_de_chutes_no_gol_marcados_1ao_tempo_visitante", "Média de chutes no gol marcados 1º tempo visitante"],
    "chutes_sofridos_1t_casa": ["madia_total_de_chutes_sofridos_1ao_tempo_casa", "Média total de chutes sofridos 1º tempo casa"],
    "chutes_sofridos_1t_visitante": ["madia_total_de_chutes_sofridos_1ao_tempo_visitante", "Média total de chutes sofridos 1º tempo visitante"],
    "odd_over25": ["odds_mais_de_2_5", "Odds mais de 2,5", "Odds mais de 2.5"],
    "league": ["League", "liga", "campeonato"],
    "hour": ["Hour", "hora", "time"],
    "home_team": ["Home Team", "casa", "mandante", "time_casa", "home"],
    "away_team": ["Visitor Team", "Away Team", "visitante", "fora", "time_visitante", "away"],
    "status": ["Status", "situacao", "state"],
}

def encontrar_coluna_flexivel(df, aliases):
    cols = list(df.columns)
    cols_simpl = {simplificar(c): c for c in cols}
    for alias in aliases:
        n = normalizar_coluna(alias)
        if n in df.columns:
            return n
    for alias in aliases:
        s = simplificar(alias)
        if s in cols_simpl:
            return cols_simpl[s]
    for alias in aliases:
        s = simplificar(alias)
        for c in cols:
            cs = simplificar(c)
            if s in cs or cs in s:
                return c
    return None

def mapear_colunas_under(df):
    mapa = {}
    for chave, aliases in ALIASES_UNDER.items():
        mapa[chave] = encontrar_coluna_flexivel(df, aliases)
    return mapa

def unir_bases_generico(df1, df2):
    chaves_candidatas = [
        normalizar_coluna("League"),
        normalizar_coluna("Hour"),
        normalizar_coluna("Status"),
        normalizar_coluna("Home Team"),
        normalizar_coluna("Visitor Team"),
    ]
    comuns = [c for c in chaves_candidatas if c in df1.columns and c in df2.columns]
    if len(comuns) >= 2:
        cols_extras_2 = [c for c in df2.columns if c not in comuns]
        return df1.merge(df2[comuns + cols_extras_2], on=comuns, how="left").loc[:, ~df1.merge(df2[comuns + cols_extras_2], on=comuns, how="left").columns.duplicated()].copy()
    return df1.loc[:, ~df1.columns.duplicated()].copy()

def criar_score_1_under(df, mapa):
    df = df.copy()
    m0 = to_float_series(df[mapa["media_0_15"]]); m1 = to_float_series(df[mapa["media_16_30"]]); m2 = to_float_series(df[mapa["media_31_45"]])
    m3 = to_float_series(df[mapa["media_46_60"]]); m4 = to_float_series(df[mapa["media_61_75"]]); m5 = to_float_series(df[mapa["media_76_90"]])
    df["score1_media_1t"] = (m0 + m1 + m2) / 3
    df["score1_media_2t"] = (m3 + m4 + m5) / 3
    df["score1_media_total"] = (m0 + m1 + m2 + m3 + m4 + m5) / 6
    df["score1_media_inicio"] = (m0 + m1) / 2
    df["score1_media_fim"] = (m4 + m5) / 2
    df["score1_diff_2t_vs_1t"] = df["score1_media_2t"] - df["score1_media_1t"]
    df["score1_diff_fim_vs_inicio"] = df["score1_media_fim"] - df["score1_media_inicio"]
    df["score1_range_media_janelas"] = (pd.concat([m0,m1,m2,m3,m4,m5], axis=1).max(axis=1) - pd.concat([m0,m1,m2,m3,m4,m5], axis=1).min(axis=1))
    df["score1_respiro_total"] = 1 / df["score1_media_total"].clip(lower=0.01)
    s1_a = zscore_series(-df["score1_media_total"])
    s1_b = zscore_series(-df["score1_media_2t"])
    s1_c = zscore_series(df["score1_respiro_total"])
    s1_d = zscore_series(-df["score1_diff_2t_vs_1t"])
    s1_e = zscore_series(-df["score1_diff_fim_vs_inicio"])
    s1_f = zscore_series(-df["score1_range_media_janelas"])
    df["Score_1_Janelas"] = s1_a*0.25 + s1_b*0.20 + s1_c*0.20 + s1_d*0.15 + s1_e*0.10 + s1_f*0.10
    return df

def criar_score_2_under(df, mapa):
    df = df.copy()
    precisao_c = ajustar_percentual_0_1(df[mapa["precisao_casa"]]); precisao_v = ajustar_percentual_0_1(df[mapa["precisao_visitante"]])
    chutes_por_gol_c = to_float_series(df[mapa["chutes_por_gol_casa"]]); chutes_por_gol_v = to_float_series(df[mapa["chutes_por_gol_visitante"]])
    chutes_gol_1t_c = to_float_series(df[mapa["chutes_gol_1t_casa"]]); chutes_gol_1t_v = to_float_series(df[mapa["chutes_gol_1t_visitante"]])
    chutes_sofridos_1t_c = to_float_series(df[mapa["chutes_sofridos_1t_casa"]]); chutes_sofridos_1t_v = to_float_series(df[mapa["chutes_sofridos_1t_visitante"]])
    df["score2_precisao_media"] = (precisao_c + precisao_v) / 2
    df["score2_chutes_por_gol_media"] = (chutes_por_gol_c + chutes_por_gol_v) / 2
    df["score2_chutes_gol_1t_media"] = (chutes_gol_1t_c + chutes_gol_1t_v) / 2
    df["score2_chutes_sofridos_1t_media"] = (chutes_sofridos_1t_c + chutes_sofridos_1t_v) / 2
    df["score2_eficiencia_perigosa"] = df["score2_precisao_media"] / df["score2_chutes_por_gol_media"].clip(lower=0.01)
    df["score2_pressao_1t"] = df["score2_chutes_gol_1t_media"] + df["score2_chutes_sofridos_1t_media"]
    s2_a = zscore_series(-df["score2_precisao_media"])
    s2_b = zscore_series(df["score2_chutes_por_gol_media"])
    s2_c = zscore_series(-df["score2_chutes_gol_1t_media"])
    s2_d = zscore_series(-df["score2_chutes_sofridos_1t_media"])
    s2_e = zscore_series(-df["score2_eficiencia_perigosa"])
    s2_f = zscore_series(-df["score2_pressao_1t"])
    df["Score_2_Chutes"] = s2_a*0.18 + s2_b*0.24 + s2_c*0.18 + s2_d*0.18 + s2_e*0.12 + s2_f*0.10
    return df

def criar_score_under_operacional(df):
    df = df.copy()
    s1 = minmax_0_1(df["Score_1_Janelas"]); s2 = minmax_0_1(df["Score_2_Chutes"])
    df["Score_Under_Operacional"] = (s1 * 0.60 + s2 * 0.40)
    df["Score_Under_Operacional_0_100"] = df["Score_Under_Operacional"] * 100
    def classificar(x):
        if pd.isna(x): return "Sem sinal"
        if x >= 75: return "🟢 Operar"
        elif x >= 60: return "🟡 Operar com gestão"
        elif x >= 45: return "🟠 Só observar"
        return "🔴 Evitar"
    df["Classificacao_Under"] = df["Score_Under_Operacional_0_100"].apply(classificar)
    return df

def criar_janelas_exposicao(df, mapa):
    df = df.copy()
    janelas = [
        ("0-15", to_float_series(df[mapa["media_0_15"]])),
        ("16-30", to_float_series(df[mapa["media_16_30"]])),
        ("31-45", to_float_series(df[mapa["media_31_45"]])),
        ("46-60", to_float_series(df[mapa["media_46_60"]])),
        ("61-75", to_float_series(df[mapa["media_61_75"]])),
        ("76-90", to_float_series(df[mapa["media_76_90"]])),
    ]
    window_scores = {}
    for nome, serie in janelas:
        risco_janela = minmax_0_1(serie)
        seguranca_janela = 1 - risco_janela
        score_janela = seguranca_janela * 0.55 + minmax_0_1(df["Score_1_Janelas"]) * 0.25 + minmax_0_1(df["Score_2_Chutes"]) * 0.20
        window_scores[nome] = score_janela
    for nome, score in window_scores.items():
        df[f"score_exp_{nome}"] = score * 100
    nomes_janelas = [x[0] for x in janelas]
    def top_janelas_row(row):
        pares = [(j, row[f"score_exp_{j}"]) for j in nomes_janelas]
        pares = sorted(pares, key=lambda x: x[1], reverse=True)
        return pd.Series({"janela_1": pares[0][0], "score_janela_1": pares[0][1], "janela_2": pares[1][0], "score_janela_2": pares[1][1], "janela_3": pares[2][0], "score_janela_3": pares[2][1]})
    df[["janela_1","score_janela_1","janela_2","score_janela_2","janela_3","score_janela_3"]] = df.apply(top_janelas_row, axis=1)
    def leitura(row):
        cls = row["Classificacao_Under"]; j1 = row["janela_1"]
        if cls == "🟢 Operar": return f"Operar under. Melhor exposição em {j1}."
        if cls == "🟡 Operar com gestão": return f"Operar com gestão. Melhor faixa: {j1}."
        if cls == "🟠 Só observar": return f"Observar live e priorizar {j1}."
        return f"Evitar under. Melhor faixa {j1}, mas sem segurança suficiente."
    df["leitura_operacional"] = df.apply(leitura, axis=1)
    return df

@st.cache_data(ttl=1800, show_spinner=False)
def rodar_pipeline_under_live():
    df1 = carregar_csv(CSV_1, "UNDER_ABA_1")
    try:
        df2 = carregar_csv(CSV_2, "UNDER_ABA_2")
        aba2_ok = True
    except Exception:
        df2 = pd.DataFrame()
        aba2_ok = False
    df_base = unir_bases_generico(df1, df2) if aba2_ok and not df2.empty else df1.copy()
    mapa = mapear_colunas_under(df_base)
    faltantes = [k for k, v in mapa.items() if v is None]
    if faltantes:
        raise ValueError(f"Colunas Under não encontradas: {faltantes}")
    status_col = mapa["status"]
    df_base[status_col] = df_base[status_col].astype(str).str.upper().str.strip()
    df_ns = df_base[df_base[status_col] == "NS"].copy()
    odd_over_col = mapa["odd_over25"]
    df_ns[odd_over_col] = to_float_series(df_ns[odd_over_col])
    df_ns = df_ns[df_ns[odd_over_col] < ODD_OVER25_MAX].copy()
    if df_ns.empty:
        return {"jogos": pd.DataFrame(), "janelas": pd.DataFrame(), "mapa": mapa, "diagnostico": {"linhas_ns": 0}}
    df_ns = criar_score_1_under(df_ns, mapa)
    df_ns = criar_score_2_under(df_ns, mapa)
    df_ns = criar_score_under_operacional(df_ns)
    df_ns = criar_janelas_exposicao(df_ns, mapa)
    cols_sinais = [
        mapa["league"], mapa["hour"], mapa["home_team"], mapa["away_team"], odd_over_col,
        "Score_1_Janelas", "Score_2_Chutes", "Score_Under_Operacional_0_100",
        "Classificacao_Under", "janela_1", "score_janela_1", "janela_2", "score_janela_2", "janela_3", "score_janela_3", "leitura_operacional"
    ]
    cols_sinais = [c for c in cols_sinais if c in df_ns.columns]
    jogos = df_ns[cols_sinais].copy().sort_values(["Score_Under_Operacional_0_100", "score_janela_1"], ascending=[False, False]).reset_index(drop=True)
    if mapa["hour"] in jogos.columns:
        jogos[mapa["hour"]] = jogos[mapa["hour"]].apply(formatar_hora_exibicao)
    jogos["jogo_label"] = jogos[mapa["home_team"]].astype(str) + " x " + jogos[mapa["away_team"]].astype(str)
    linhas = []
    for _, row in df_ns.iterrows():
        base_info = {
            "league": row.get(mapa["league"], np.nan),
            "hour": formatar_hora_exibicao(row.get(mapa["hour"], np.nan)),
            "home_team": row.get(mapa["home_team"], np.nan),
            "away_team": row.get(mapa["away_team"], np.nan),
            "odd_over25": row.get(odd_over_col, np.nan),
            "Score_1_Janelas": row.get("Score_1_Janelas", np.nan),
            "Score_2_Chutes": row.get("Score_2_Chutes", np.nan),
            "Score_Under_Operacional_0_100": row.get("Score_Under_Operacional_0_100", np.nan),
            "Classificacao_Under": row.get("Classificacao_Under", np.nan),
        }
        for janela in ["0-15","16-30","31-45","46-60","61-75","76-90"]:
            linhas.append({**base_info, "janela": janela, "score_exposicao": row.get(f"score_exp_{janela}", np.nan)})
    tabela_janelas = pd.DataFrame(linhas).sort_values(["Score_Under_Operacional_0_100","score_exposicao"], ascending=[False, False])
    return {
        "jogos": jogos,
        "janelas": tabela_janelas,
        "mapa": mapa,
        "diagnostico": {"linhas_ns": len(df_ns), "linhas_filtradas_odd": len(jogos), "odd_over25_max": ODD_OVER25_MAX}
    }


st.sidebar.markdown("## Ajustes")
mostrar_diag = st.sidebar.checkbox("Mostrar diagnóstico", value=False)
qtde_top = st.sidebar.slider("Qtd. jogos na fila", 10, 100, 30, 5)
tipo_jogos = st.sidebar.radio(
    "Tipo de jogos",
    ["Jogos do dia (NS)", "Jogos passados (FT)", "Todos"],
    index=0,
)

with st.spinner("Rodando pipeline completo..."):
    resultado = rodar_pipeline_completo()

with st.spinner("Montando Leitura Under Live..."):
    resultado_under = rodar_pipeline_under_live()

if tipo_jogos == "Jogos passados (FT)":
    df_live = resultado["df_oportunidades_ft"].copy().head(qtde_top)
    rotulo_fonte = "FT"
elif tipo_jogos == "Todos":
    df_live = resultado["df_oportunidades_todos"].copy().head(qtde_top)
    rotulo_fonte = "TODOS"
else:
    df_live = resultado["df_oportunidades_live"].copy().head(qtde_top)
    rotulo_fonte = "NS"

resumo_top_regras = resultado["resumo_top_regras"].copy()
variaveis_validas_df = resultado["variaveis_validas"].copy()
diagnostico = resultado["diagnostico"]
mapa_base = resultado["mapa_base"]

league_col = mapa_base.get("league") if mapa_base.get("league") in df_live.columns else None
hour_col = mapa_base.get("hour") if mapa_base.get("hour") in df_live.columns else None
home_col = mapa_base.get("home_team") if mapa_base.get("home_team") in df_live.columns else None
away_col = mapa_base.get("away_team") if mapa_base.get("away_team") in df_live.columns else None
status_col = mapa_base.get("status") if mapa_base.get("status") in df_live.columns else None
result_col = mapa_base.get("result") if mapa_base.get("result") in df_live.columns else None

odd_home_col = encontrar_coluna(df_live, [
    "Odds casa para vencer", "odd casa para vencer", "odds_casa_para_vencer",
    "odd_casa", "odds casa", "home_win_odds", "odd_home_win", "match_odds_1", "odds_1", "1"
])
odd_draw_col = encontrar_coluna(df_live, [
    "Odds empate", "odd empate", "odds_empate",
    "draw_odds", "odd_draw", "match_odds_x", "odds_x", "x"
])
odd_away_col = encontrar_coluna(df_live, [
    "Odds visitante para vencer", "odd visitante para vencer", "odds_visitante_para_vencer",
    "odd_visitante", "odds visitante", "away_win_odds", "odd_away_win", "match_odds_2", "odds_2", "2"
])

if hour_col:
    df_live[hour_col] = df_live[hour_col].apply(formatar_hora_exibicao)

if df_live.empty:
    st.error("O pipeline rodou, mas não gerou oportunidades finais.")
    st.stop()

df_live["status_visual"] = df_live["semaforo_oportunidade"].apply(classificar_status_visual)
df_live["jogo_label"] = df_live[home_col].astype(str) + " x " + df_live[away_col].astype(str)

st.markdown("<div class='hero'><div class='hero-title'>Oportunidades operacionais do dia</div><div class='hero-sub'>Leitura rápida • score • cenário 2+ • mercado • pipeline completo rodando no app.</div></div>", unsafe_allow_html=True)

m1, m2, m3, m4 = st.columns(4)
with m1:
    st.markdown(f"<div class='metric-card'><div class='metric-label'>Jogos exibidos</div><div class='metric-value'>{len(df_live)}</div><div class='metric-sub'>fila operacional</div></div>", unsafe_allow_html=True)
with m2:
    st.markdown(f"<div class='metric-card'><div class='metric-label'>Elite</div><div class='metric-value'>{int((df_live['status_visual']=='Elite').sum())}</div><div class='metric-sub'>prioridade máxima</div></div>", unsafe_allow_html=True)
with m3:
    st.markdown(f"<div class='metric-card'><div class='metric-label'>Fortes</div><div class='metric-value'>{int((df_live['status_visual']=='Forte').sum())}</div><div class='metric-sub'>boa convergência</div></div>", unsafe_allow_html=True)
with m4:
    st.markdown(f"<div class='metric-card'><div class='metric-label'>Base filtrada</div><div class='metric-value'>{rotulo_fonte}</div><div class='metric-sub'>NS, FT ou todos</div></div>", unsafe_allow_html=True)

if mostrar_diag:
    with st.expander("Diagnóstico do pipeline", expanded=False):
        st.write(diagnostico)
        st.write("Mapeamento base:", mapa_base)
        st.write("Colunas oportunidades:", df_live.columns.tolist())

tab1, tab_under, tab2, tab3 = st.tabs(["Painel do Dia", "Leitura Under Live", "Top Regras", "Variáveis Válidas"])

with tab1:
    c1, c2 = st.columns([1.1, 1])
    with c1:
        st.markdown("<div class='panel-box'><div class='section-title'>Fila operacional</div><div class='section-sub'>Ranking limpo para comparação rápida entre os jogos.</div></div>", unsafe_allow_html=True)
        tabela = df_live[[hour_col, league_col, home_col, away_col, "direcao_prevista", "status_visual", "score_geral_oportunidade", "mercado_sugerido"]].copy()
        tabela.columns = ["Hora", "Liga", "Casa", "Visitante", "Direção", "Status", "Score", "Mercado"]
        st.dataframe(tabela, use_container_width=True, hide_index=True)

    with c2:
        st.markdown("<div class='panel-box'><div class='section-title'>Jogo selecionado</div><div class='section-sub'>Escolha o jogo para abrir a leitura detalhada abaixo.</div></div>", unsafe_allow_html=True)
        opcoes = df_live["jogo_label"].tolist()
        escolhido = st.selectbox("Selecione uma partida", options=opcoes, index=0)
        row = df_live[df_live["jogo_label"] == escolhido].iloc[0]
        odd_home_txt = fmt(row.get(odd_home_col), 2) if odd_home_col else "-"
        odd_draw_txt = fmt(row.get(odd_draw_col), 2) if odd_draw_col else "-"
        odd_away_txt = fmt(row.get(odd_away_col), 2) if odd_away_col else "-"
        league_txt = row[league_col] if league_col else "-"
        hour_txt = row[hour_col] if hour_col else "-"
        status_partida = str(row.get(status_col, "")).upper().strip() if status_col else ""
        placar_txt = ""
        if status_partida == "FT":
            gc = row.get("gols_casa_final")
            gf = row.get("gols_fora_final")
            if pd.notna(gc) and pd.notna(gf):
                placar_txt = f" • {fmt_score(gc)}-{fmt_score(gf)}"
            elif result_col and pd.notna(row.get(result_col)):
                placar_txt = f" • {row.get(result_col)}"
        st.markdown(
            f"<div class='panel-box'>"
            f"{pill_status_html(row['status_visual'])}{pill_direcao_html(row['direcao_prevista'])}"
            f"<div style='font-size:2rem;font-weight:800;color:white;margin-top:10px;'>{row['jogo_label']}</div>"
            f"<div style='color:#8fa7c2;font-size:.95rem;margin-top:8px;'>{league_txt}{placar_txt} • {hour_txt}</div>"
            f"<div style='margin-top:10px;display:flex;flex-wrap:wrap;gap:8px 10px;'>"
            f"<span class='pill' style='margin-right:0;'>Odd casa: {odd_home_txt}</span>"
            f"<span class='pill' style='margin-right:0;'>Odd empate: {odd_draw_txt}</span>"
            f"<span class='pill' style='margin-right:0;'>Odd visitante: {odd_away_txt}</span>"
            f"</div>"
            f"</div>", unsafe_allow_html=True)

    d1, d2, d3, d4 = st.columns(4)
    for col, label, value in [
        (d1, "Score casa vence", row.get("score_casa_vence")),
        (d2, "Score visitante vence", row.get("score_visitante_vence")),
        (d3, "Casa 2+", row.get("score_casa_2mais")),
        (d4, "Visitante 2+", row.get("score_visitante_2mais")),
    ]:
        with col:
            st.markdown(f"<div class='detail-card'><div class='detail-label'>{label}</div><div class='detail-value'>{fmt(value)}</div></div>", unsafe_allow_html=True)

    e1, e2 = st.columns([1.2, 1])
    with e1:
        st.markdown("<div class='section-title' style='margin-top:14px;'>Leitura final</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='detail-card'><div style='font-size:1.02rem;color:white;font-weight:700'>{row.get('leitura_final','-')}</div></div>", unsafe_allow_html=True)
        st.markdown("<div class='section-title' style='margin-top:14px;'>Mercado sugerido</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='detail-card'><div style='font-size:1.02rem;color:white;font-weight:700'>{row.get('mercado_sugerido','-')}</div></div>", unsafe_allow_html=True)
        st.markdown("<div class='section-title' style='margin-top:14px;'>Mercado operacional</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='detail-card'><div style='font-size:1rem;color:white;font-weight:700'>{row.get('mercado_operacional','-')}</div></div>", unsafe_allow_html=True)

    with e2:
        st.markdown("<div class='section-title' style='margin-top:14px;'>Alertas do dia</div>", unsafe_allow_html=True)
        elite = df_live[df_live["status_visual"] == "Elite"]["jogo_label"].head(1)
        forte = df_live[df_live["status_visual"] == "Forte"]["jogo_label"].head(1)
        evitar = df_live[df_live["status_visual"] == "Neutro"]["jogo_label"].head(1)
        st.markdown(f"<div class='detail-card' style='border-color:rgba(16,185,129,.28);background:rgba(16,185,129,.08)'><div class='detail-label'>Entrada forte</div><div style='font-size:1.05rem;font-weight:700;color:white;margin-top:8px'>{elite.iloc[0] if not elite.empty else 'Nenhum'}</div></div>", unsafe_allow_html=True)
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='detail-card' style='border-color:rgba(6,182,212,.28);background:rgba(6,182,212,.08)'><div class='detail-label'>Boa observação</div><div style='font-size:1.05rem;font-weight:700;color:white;margin-top:8px'>{forte.iloc[0] if not forte.empty else 'Nenhum'}</div></div>", unsafe_allow_html=True)
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='detail-card' style='border-color:rgba(239,68,68,.28);background:rgba(239,68,68,.08)'><div class='detail-label'>Evitar</div><div style='font-size:1.05rem;font-weight:700;color:white;margin-top:8px'>{evitar.iloc[0] if not evitar.empty else 'Nenhum'}</div></div>", unsafe_allow_html=True)


with tab_under:
    df_under = resultado_under["jogos"].copy().head(qtde_top)
    mapa_under = resultado_under["mapa"]
    diag_under = resultado_under["diagnostico"]

    st.markdown("<div class='hero'><div class='hero-title'>Leitura Under Live</div><div class='hero-sub'>Leitura rápida para operações em under gols ao vivo.</div></div>", unsafe_allow_html=True)

    if mostrar_diag:
        with st.expander("Diagnóstico Under", expanded=False):
            st.write(diag_under)
            st.write("Mapeamento under:", mapa_under)
            if not df_under.empty:
                st.write("Colunas under:", df_under.columns.tolist())

    if df_under.empty:
        st.warning("Nenhum jogo elegível para under encontrado com odd over 2.5 menor que 2.00.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        operar = int((df_under["Classificacao_Under"] == "🟢 Operar").sum())
        gestao = int((df_under["Classificacao_Under"] == "🟡 Operar com gestão").sum())
        observar = int((df_under["Classificacao_Under"] == "🟠 Só observar").sum())
        with c1:
            st.markdown(f"<div class='metric-card'><div class='metric-label'>Jogos filtrados</div><div class='metric-value'>{len(df_under)}</div><div class='metric-sub'>odd over 2.5 &lt; 2</div></div>", unsafe_allow_html=True)
        with c2:
            st.markdown(f"<div class='metric-card'><div class='metric-label'>Operar</div><div class='metric-value'>{operar}</div><div class='metric-sub'>entrada forte</div></div>", unsafe_allow_html=True)
        with c3:
            st.markdown(f"<div class='metric-card'><div class='metric-label'>Operar gestão</div><div class='metric-value'>{gestao}</div><div class='metric-sub'>entrada com gestão</div></div>", unsafe_allow_html=True)
        with c4:
            st.markdown(f"<div class='metric-card'><div class='metric-label'>Só observar</div><div class='metric-value'>{observar}</div><div class='metric-sub'>priorizar leitura</div></div>", unsafe_allow_html=True)

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        st.markdown("<div class='panel-box'><div class='section-title'>Fila Under do Dia</div><div class='section-sub'>Jogos priorizados por odd over 2.5, score under e melhores janelas de exposição.</div></div>", unsafe_allow_html=True)
        under_tabela = df_under[[mapa_under["hour"], "jogo_label", mapa_under["odd_over25"], "Score_Under_Operacional_0_100", "Classificacao_Under", "janela_1"]].copy()
        under_tabela.columns = ["Hora", "Jogo", "Odd O2.5", "Score Under", "Classificação", "Melhor janela"]
        st.dataframe(under_tabela, use_container_width=True, hide_index=True)

        escolhido_under = st.selectbox("Selecione um jogo under", options=df_under["jogo_label"].tolist(), index=0, key="under_select")
        row_u = df_under[df_under["jogo_label"] == escolhido_under].iloc[0]
        league_u = row_u.get(mapa_under["league"], "-")
        hour_u = row_u.get(mapa_under["hour"], "-")

        st.markdown(
            f"<div class='panel-box'>"
            f"<div style='font-size:2rem;font-weight:800;color:white;margin-top:4px;'>{row_u['jogo_label']}</div>"
            f"<div style='color:#8fa7c2;font-size:.95rem;margin-top:8px;'>{league_u} • {hour_u}</div>"
            f"<div style='margin-top:10px;display:flex;flex-wrap:wrap;gap:8px 10px;'>"
            f"<span class='pill' style='margin-right:0;'>Odd Over 2.5: {fmt(row_u.get(mapa_under['odd_over25']), 2)}</span>"
            f"<span class='pill' style='margin-right:0;'>{row_u.get('Classificacao_Under','-')}</span>"
            f"<span class='pill' style='margin-right:0;'>Score Under: {fmt(row_u.get('Score_Under_Operacional_0_100'), 1)}</span>"
            f"</div>"
            f"</div>", unsafe_allow_html=True)

        u1, u2, u3, u4 = st.columns(4)
        for col, label, value in [
            (u1, "Score 1 - Janelas", row_u.get("Score_1_Janelas")),
            (u2, "Score 2 - Chutes", row_u.get("Score_2_Chutes")),
            (u3, "Melhor janela", row_u.get("janela_1")),
            (u4, "Score exposição", row_u.get("score_janela_1")),
        ]:
            with col:
                mostrador = value if isinstance(value, str) else fmt(value, 1)
                st.markdown(f"<div class='detail-card'><div class='detail-label'>{label}</div><div class='detail-value'>{mostrador}</div></div>", unsafe_allow_html=True)

        ux1, ux2, ux3 = st.columns(3)
        for col, titulo, janela, score in [
            (ux1, "1ª melhor janela", row_u.get("janela_1"), row_u.get("score_janela_1")),
            (ux2, "2ª melhor janela", row_u.get("janela_2"), row_u.get("score_janela_2")),
            (ux3, "3ª melhor janela", row_u.get("janela_3"), row_u.get("score_janela_3")),
        ]:
            with col:
                st.markdown(f"<div class='detail-card'><div class='detail-label'>{titulo}</div><div style='font-size:1.05rem;font-weight:700;color:white;margin-top:6px'>{janela}</div><div style='font-size:.9rem;color:#8fa7c2;margin-top:8px'>Score exposição: {fmt(score,1)}</div></div>", unsafe_allow_html=True)

        st.markdown("<div class='section-title' style='margin-top:14px;'>Leitura operacional</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='detail-card'><div style='font-size:1.02rem;color:white;font-weight:700'>{row_u.get('leitura_operacional','-')}</div></div>", unsafe_allow_html=True)

        st.markdown("<div class='section-title' style='margin-top:14px;'>Janelas por jogo</div>", unsafe_allow_html=True)
        tabela_janelas_view = resultado_under["janelas"].copy()
        tabela_janelas_view["jogo"] = tabela_janelas_view["home_team"].astype(str) + " x " + tabela_janelas_view["away_team"].astype(str)
        tabela_janelas_view = tabela_janelas_view[tabela_janelas_view["jogo"] == escolhido_under][["janela", "score_exposicao"]].copy()
        tabela_janelas_view.columns = ["Janela", "Score exposição"]
        st.dataframe(tabela_janelas_view.sort_values("Score exposição", ascending=False), use_container_width=True, hide_index=True)

with tab2:
    st.markdown("<div class='section-title'>Top regras</div><div class='section-sub'>Melhores faixas por alvo binário geradas pelo pipeline completo.</div>", unsafe_allow_html=True)
    st.dataframe(resumo_top_regras, use_container_width=True, hide_index=True)

with tab3:
    st.markdown("<div class='section-title'>Variáveis válidas</div><div class='section-sub'>Variáveis finais para modelagem encontradas no histórico FT.</div>", unsafe_allow_html=True)
    st.dataframe(variaveis_validas_df, use_container_width=True, hide_index=True)

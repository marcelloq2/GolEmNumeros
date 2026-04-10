import re
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

# ============================================================
# CONFIG
# ============================================================
st.set_page_config(
    page_title="GolEmNúmeros - Leitura Pré Live",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="collapsed",
)

CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTHLhAS8u9JBw2sFr2qkQdsUBeRWIFaWm0STZ17yCROnYbBWgrqlBFS6bo35rtsKhHpg3NstBBHDFIe/pub?gid=0&single=true&output=csv"


# ============================================================
# CSS
# ============================================================
def inject_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --soft: #9eb7de;
            --text: #edf4ff;
        }

        .stApp {
            background:
                radial-gradient(circle at 12% 18%, rgba(55, 131, 255, 0.18), transparent 26%),
                radial-gradient(circle at 86% 14%, rgba(94, 242, 160, 0.14), transparent 22%),
                radial-gradient(circle at 82% 82%, rgba(157, 140, 255, 0.16), transparent 24%),
                linear-gradient(180deg, #04101d 0%, #081323 45%, #050b16 100%);
            color: var(--text);
        }

        .block-container {
            max-width: 1450px;
            padding-top: 1rem;
            padding-bottom: 2rem;
        }

        h1, h2, h3, h4, h5, h6, p, span, div, label {
            color: var(--text) !important;
        }

        .gn-shell {
            background: linear-gradient(180deg, rgba(16,30,59,.88), rgba(10,18,35,.90));
            border: 1px solid rgba(150, 201, 255, 0.18);
            border-radius: 24px;
            box-shadow:
                0 0 0 1px rgba(255,255,255,0.03) inset,
                0 18px 40px rgba(0,0,0,.28),
                0 0 60px rgba(58, 123, 255, 0.10);
            padding: 18px 18px 16px 18px;
            backdrop-filter: blur(10px);
            margin-bottom: 18px;
        }

        .gn-header {
            display:flex;
            align-items:center;
            justify-content:space-between;
            padding: 10px 14px;
            border-radius: 18px;
            background: linear-gradient(180deg, rgba(31,49,89,.95), rgba(14,24,44,.95));
            border: 1px solid rgba(130,190,255,.20);
            box-shadow: 0 0 24px rgba(98, 177, 255, 0.12);
            margin-bottom: 16px;
            gap: 16px;
        }

        .gn-title {
            font-size: 2rem;
            font-weight: 800;
            letter-spacing: .2px;
        }

        .gn-subtitle {
            color: var(--soft) !important;
            font-size: .95rem;
            margin-top: 4px;
        }

        .gn-chiprow {
            display:flex;
            gap: 10px;
            flex-wrap: wrap;
        }

        .gn-chip {
            background: linear-gradient(180deg, rgba(28,44,74,.95), rgba(14,25,46,.96));
            border: 1px solid rgba(132,190,255,.18);
            border-radius: 14px;
            padding: 9px 14px;
            min-width: 120px;
            text-align:center;
            box-shadow: 0 0 18px rgba(98, 177, 255, 0.12);
        }

        .gn-chip-label {
            color: #b2caea !important;
            font-size: .76rem;
            margin-bottom: 4px;
        }

        .gn-chip-value {
            font-size: 1.6rem;
            font-weight: 800;
            line-height: 1.0;
        }

        .gn-panel {
            background: linear-gradient(180deg, rgba(18,31,58,.90), rgba(10,18,35,.92));
            border: 1px solid rgba(125,186,255,.18);
            border-radius: 22px;
            padding: 14px;
            box-shadow: 0 0 28px rgba(73, 132, 255, 0.10);
            height: 100%;
        }

        .gn-panel-title {
            font-size: 1.05rem;
            font-weight: 800;
            margin-bottom: 12px;
            color: #f2f7ff !important;
            letter-spacing: .2px;
        }

        .match-card {
            background: linear-gradient(180deg, rgba(18,31,58,.96), rgba(11,20,38,.96));
            border: 1px solid rgba(132,190,255,.18);
            border-radius: 20px;
            padding: 14px;
            box-shadow: 0 0 28px rgba(81, 142, 255, 0.12);
            margin-bottom: 12px;
        }

        .match-league {
            color: #9ec4ff !important;
            font-size: .86rem;
            font-weight: 700;
            margin-bottom: 6px;
        }

        .match-name {
            font-size: 1.15rem;
            font-weight: 800;
            margin-bottom: 8px;
        }

        .match-meta {
            display:flex;
            gap:8px;
            flex-wrap:wrap;
            margin-bottom: 10px;
        }

        .pill {
            display:inline-block;
            padding: 5px 10px;
            border-radius: 999px;
            font-size: .78rem;
            font-weight: 700;
            border: 1px solid rgba(255,255,255,.08);
            background: rgba(255,255,255,.04);
        }

        .pill-green { background: rgba(94,242,160,.10); color: #9ff8c6 !important; }
        .pill-yellow { background: rgba(255,214,103,.10); color: #ffe8a2 !important; }
        .pill-red { background: rgba(255,111,135,.10); color: #ffafbe !important; }
        .pill-blue { background: rgba(98,177,255,.10); color: #b8dcff !important; }

        .odds-row {
            display:grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 8px;
            margin-bottom: 10px;
        }

        .odd-box {
            background: linear-gradient(180deg, rgba(33,50,86,.96), rgba(16,26,47,.96));
            border: 1px solid rgba(132,190,255,.16);
            border-radius: 14px;
            padding: 9px;
            text-align:center;
        }

        .odd-label { font-size: .72rem; color:#a7c1e5 !important; }
        .odd-value { font-size: 1.2rem; font-weight: 800; }

        .kv-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0 8px;
        }

        .kv-table td {
            padding: 9px 12px;
            background: rgba(255,255,255,.03);
            border-top: 1px solid rgba(255,255,255,.03);
            border-bottom: 1px solid rgba(255,255,255,.03);
            vertical-align: top;
        }

        .kv-table td:first-child {
            border-left: 1px solid rgba(132,190,255,.10);
            border-radius: 12px 0 0 12px;
            color: #b8cdeb !important;
            width: 64%;
        }

        .kv-table td:last-child {
            border-right: 1px solid rgba(132,190,255,.10);
            border-radius: 0 12px 12px 0;
            text-align:right;
            font-weight: 800;
            white-space: pre-line;
        }

        .compare-table {
            width:100%;
            border-collapse: collapse;
            overflow:hidden;
            border-radius: 18px;
            border: 1px solid rgba(132,190,255,.16);
        }

        .compare-table th, .compare-table td {
            padding: 10px 12px;
            border-bottom: 1px solid rgba(255,255,255,.05);
            text-align: center;
        }

        .compare-table th {
            background: rgba(255,255,255,.04);
            font-size: .82rem;
            color: #b4cbeb !important;
        }

        .compare-table td:first-child, .compare-table th:first-child {
            text-align: left;
        }

        div[data-testid="stDataFrame"] {
            background: rgba(9,19,37,.86);
            border: 1px solid rgba(132,190,255,.16);
            border-radius: 20px;
            padding: 8px;
            box-shadow: 0 0 28px rgba(81, 142, 255, 0.10);
        }

        .stButton > button {
            width: 100%;
            border-radius: 14px;
            border: 1px solid rgba(132,190,255,.20);
            color: white;
            background: linear-gradient(180deg, rgba(37,58,97,.96), rgba(16,26,47,.98));
            box-shadow: 0 0 20px rgba(81, 142, 255, 0.14);
            font-weight: 700;
        }

        .stDownloadButton > button {
            border-radius: 12px;
        }

        .stSelectbox label, .stTextInput label {
            color: #bfd4f0 !important;
            font-weight: 700;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# HELPERS
# ============================================================
def to_float(val) -> Optional[float]:
    if pd.isna(val):
        return None
    s = str(val).strip()
    if s == "":
        return None
    s = s.replace("%", "").replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)
    if s in {"", ".", "-", "-."}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def split_pair_value(val) -> tuple[Optional[float], Optional[float]]:
    if pd.isna(val):
        return None, None
    s = str(val).strip()
    if not s:
        return None, None
    s = s.replace("%", "").replace(",", ".")
    parts = [p.strip() for p in re.split(r"\||/| x | X | vs | VS |;", s) if p.strip()]
    nums = []
    for p in parts:
        n = to_float(p)
        if n is not None:
            nums.append(n)
    if len(nums) >= 2:
        return nums[0], nums[1]
    n = to_float(s)
    return n, None


def fmt_num(v, nd: int = 2, pct: bool = False) -> str:
    if v is None:
        return "-"
    if isinstance(v, float) and np.isnan(v):
        return "-"
    if isinstance(v, str):
        s = v.strip()
        if s in {"", "-", "nan", "None"}:
            return "-"
        n = to_float(s)
        if n is None:
            return s
        v = n
    if pct:
        return f"{float(v):.{nd}f}%"
    return f"{float(v):.{nd}f}"


def fmt_pct(v, nd: int = 2) -> str:
    if v is None:
        return "-"
    if isinstance(v, float) and np.isnan(v):
        return "-"
    if isinstance(v, str):
        s = v.strip()
        if s in {"", "-", "nan", "None"}:
            return "-"
        n = to_float(s)
        if n is None:
            return s
        v = n
    try:
        v = float(v)
        if abs(v) <= 1:
            v *= 100
        return f"{v:.{nd}f}%"
    except Exception:
        return "-"


def infer_prob_from_odd(odd: Optional[float]) -> Optional[float]:
    if odd is None or odd <= 0:
        return None
    return (1 / odd) * 100


def pick_confidence(score: float) -> Tuple[str, str]:
    if score >= 70:
        return "Alta", "green"
    if score >= 50:
        return "Média", "yellow"
    return "Baixa", "red"


def get_first_existing(df: pd.DataFrame, names: list[str]) -> Optional[str]:
    lower_map = {c.lower().strip(): c for c in df.columns}
    for name in names:
        key = name.lower().strip()
        if key in lower_map:
            return lower_map[key]
    return None


def has_detail_data(row: pd.Series) -> bool:
    critical_required = [
        "odd_home", "odd_away", "odd_over25", "odd_under25",
        "odd_btts_yes", "odd_btts_no", "odd_over05ht"
    ]
    compare_required = [
        "win_pct_home", "win_pct_away",
        "draw_pct_home", "draw_pct_away",
        "loss_pct_home", "loss_pct_away",
        "efficiency_home", "efficiency_away",
        "games_home", "games_away"
    ]
    pattern_required = [
        "res1_home", "res1_away",
        "res2_home", "res2_away",
        "res1_ht_home", "res1_ht_away"
    ]

    def is_filled(val) -> bool:
        if val is None:
            return False
        if isinstance(val, float) and pd.isna(val):
            return False
        return str(val).strip() not in {"", "-", "nan", "None"}

    critical_ok = all(is_filled(row.get(col)) for col in critical_required)
    compare_count = sum(1 for col in compare_required if is_filled(row.get(col)))
    pattern_count = sum(1 for col in pattern_required if is_filled(row.get(col)))

    return critical_ok and compare_count >= 6 and pattern_count >= 2


def calcular_forca_geral(row: pd.Series) -> int:
    score_casa = 0
    score_fora = 0
    campos = ["win_pct", "efficiency", "efficacy_ht", "efficiency_2h"]

    for base in campos:
        casa = to_float(row.get(f"{base}_home"))
        fora = to_float(row.get(f"{base}_away"))

        if casa is None or fora is None:
            continue

        if casa > fora:
            score_casa += 1
        elif fora > casa:
            score_fora += 1

    return score_casa - score_fora


def compute_daily_indicator_means(df: pd.DataFrame) -> dict:
    indicator_cols = ["over15", "over25", "over05ht", "under25", "under15", "btts"]
    means = {}
    for col in indicator_cols:
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce")
            means[col] = float(vals.mean()) if vals.notna().any() else None
        else:
            means[col] = None
    return means


def compute_daily_pattern_means(df: pd.DataFrame) -> dict:
    pattern_bases = [
        "scored_first",
        "scored_first_ht",
        "conceded_first_ht",
        "conceded_first",
    ]
    means = {}
    for base in pattern_bases:
        vals = []
        for side in ["home", "away"]:
            col = f"{base}_{side}"
            if col in df.columns:
                serie = pd.to_numeric(df[col], errors="coerce")
                vals.extend(serie.dropna().tolist())
        means[base] = float(np.mean(vals)) if vals else None
    return means


def compute_daily_compare_means(df: pd.DataFrame) -> dict:
    compare_bases = [
        "win_pct",
        "draw_pct",
        "loss_pct",
        "efficiency",
        "efficacy_ht",
        "efficiency_2h",
        "games",
        "avg_gf",
        "avg_ga",
        "avg_gf_ht",
        "avg_ga_ht",
        "avg_gf_2h",
        "avg_ga_2h",
    ]
    means = {}

    for base in compare_bases:
        vals = []
        for side in ["home", "away"]:
            col = f"{base}_{side}"
            if col in df.columns:
                serie = pd.to_numeric(df[col], errors="coerce")
                vals.extend(serie.dropna().tolist())
        means[base] = float(np.mean(vals)) if vals else None

    return means


def indicator_with_day_signal(value, day_mean) -> str:
    text = fmt_pct(value, 2)
    val_num = to_float(value)
    mean_num = to_float(day_mean)
    if val_num is None or mean_num is None:
        return text
    return f"{text} 🟢" if val_num > mean_num else text


# ============================================================
# LOAD DATA
# ============================================================
@st.cache_data(show_spinner=False, ttl=300)
def load_data() -> pd.DataFrame:
    df = pd.read_csv(CSV_URL)
    df.columns = [str(c).strip() for c in df.columns]

    rename_map = {}
    possible_names = {
        "league": ["League", "Liga", "Campeonato"],
        "country": ["Country", "País"],
        "home": ["Home Team", "Casa", "Mandante", "Time Casa"],
        "away": ["Visitor Team", "Visitante", "Fora", "Time Visitante"],
        "hour": ["Hour", "Hora"],
        "date": ["Date", "Data"],
        "status": ["Status"],
        "odd_home": ["Odds Casa para vencer", "(Odds)Casa para vencer", "Odd Casa"],
        "odd_away": ["Odds Visitante para vencer", "(Odds)Visitante para vencer", "Odd Visitante"],
        "odd_over25": ["Odds Mais de 2.5 gols", "(Odds)Mais de 2.5 gols"],
        "odd_under25": ["Odds Menos de 2.5 gols", "(Odds)Menos de 2.5 gols"],
        "odd_btts_yes": ["Odds Ambas as equipes marcam (Sim)", "(Odds)Ambas as equipes marcam (Sim)"],
        "odd_btts_no": ["Odds Ambas as equipes marcam (Não)", "(Odds)Ambas as equipes marcam (Não)"],
        "odd_over05ht": ["Odds Mais de 0.5 gol 1º tempo", "(Odds)Mais de 0.5 gol 1º tempo"],
        "win_pct": ["(W%) Vitórias"],
        "draw_pct": ["(D%) Empates"],
        "loss_pct": ["(L%) Derrotas"],
        "efficiency": ["Eficiência"],
        "efficacy_ht": ["Eficácia 1º tempo"],
        "efficiency_2h": ["Eficiência 2º tempo"],
        "rank": ["Classificação"],
        "games": ["Número de jogos calculados"],
        "avg_gf": ["Média de gols marcados"],
        "avg_ga": ["Média de gols sofridos"],
        "avg_gf_ht": ["Média de gols marcados 1º tempo"],
        "avg_ga_ht": ["Média de gols sofridos 1º tempo"],
        "avg_gf_2h": ["Média de gols marcados 2º tempo"],
        "avg_ga_2h": ["Média de gols sofridos 2º tempo"],
        "over15": ["Mais de 1.5 gols"],
        "over25": ["Mais de 2.5 gols"],
        "over05ht": ["Mais de 0.5 gol 1º tempo"],
        "under25": ["Menos de 2.5 gols"],
        "under15": ["Menos de 1.5 gols"],
        "btts": ["Ambas marcam", "Ambas as equipes marcam"],
        "scored_first": ["Marcou primeiro gol"],
        "scored_first_ht": ["Marcou primeiro gol 1º tempo"],
        "conceded_first_ht": ["Sofreu primeiro gol 1º tempo"],
        "conceded_first": ["Sofreu primeiro gol"],
        "res1": ["Primeiro resultado mais comum"],
        "res2": ["Segundo resultado mais comum"],
        "res3": ["Terceiro resultado mais comum"],
        "res1_ht": ["Primeiro resultado mais comum 1º tempo"],
        "res2_ht": ["Segundo resultado mais comum 1º tempo"],
        "res3_ht": ["Terceiro resultado mais comum 1º tempo"],
        "win_pct_ht": ["(W%) Vitórias 1º tempo"],
        "win_pct_2h": ["(W%) Vitórias 2º tempo"],
    }

    for new_name, options in possible_names.items():
        col = get_first_existing(df, options)
        if col:
            rename_map[col] = new_name

    df = df.rename(columns=rename_map)

    pair_base_cols = [
        "win_pct", "draw_pct", "loss_pct", "efficiency", "efficacy_ht", "efficiency_2h",
        "rank", "games", "avg_gf", "avg_ga", "avg_gf_ht", "avg_ga_ht",
        "avg_gf_2h", "avg_ga_2h", "scored_first", "scored_first_ht",
        "conceded_first_ht", "conceded_first", "win_pct_ht", "win_pct_2h"
    ]

    for col in ["odd_home", "odd_away", "odd_over25", "odd_under25", "odd_btts_yes", "odd_btts_no", "odd_over05ht"]:
        if col in df.columns:
            df[col] = df[col].apply(to_float)

    for col in pair_base_cols:
        if col in df.columns:
            split_vals = df[col].apply(split_pair_value)
            df[f"{col}_home"] = split_vals.apply(lambda x: x[0])
            df[f"{col}_away"] = split_vals.apply(lambda x: x[1])

    for col in ["res1", "res2", "res3", "res1_ht", "res2_ht", "res3_ht"]:
        if col in df.columns:
            df[f"{col}_home"] = df[col].astype(str).apply(
                lambda s: [p.strip() for p in str(s).split("|")][0] if "|" in str(s) else str(s)
            )
            df[f"{col}_away"] = df[col].astype(str).apply(
                lambda s: [p.strip() for p in str(s).split("|")][1]
                if "|" in str(s) and len([p.strip() for p in str(s).split("|")]) > 1
                else "-"
            )

    overall_numeric_cols = ["over15", "over25", "over05ht", "under25", "under15", "btts"]
    for col in overall_numeric_cols:
        if col in df.columns:
            df[col] = df[col].apply(to_float)

    for col in ["league", "country", "home", "away", "hour", "date", "status"]:
        if col not in df.columns:
            df[col] = "-"

    df["match_name"] = df["home"].astype(str) + " vs " + df["away"].astype(str)
    df["prob_home"] = df["odd_home"].apply(infer_prob_from_odd) if "odd_home" in df.columns else None
    df["prob_away"] = df["odd_away"].apply(infer_prob_from_odd) if "odd_away" in df.columns else None
    df["prob_over25"] = df["odd_over25"].apply(infer_prob_from_odd) if "odd_over25" in df.columns else None
    df["prob_under25"] = df["odd_under25"].apply(infer_prob_from_odd) if "odd_under25" in df.columns else None

    def define_direction(row: pd.Series) -> str:
        home_w = row.get("win_pct_home")
        away_w = row.get("win_pct_away")

        if home_w is not None and away_w is not None:
            if home_w - away_w >= 12:
                return "Casa para vencer"
            if away_w - home_w >= 12:
                return "Visitante para vencer"

        if row.get("over25") is not None and row.get("over25") >= 62:
            return "Mais de 2.5 gols"
        if row.get("under25") is not None and row.get("under25") >= 60:
            return "Menos de 2.5 gols"
        if row.get("btts") is not None and row.get("btts") >= 60:
            return "Ambas marcam"
        return "Jogo equilibrado"

    def score_row(row: pd.Series) -> float:
        score = 0.0
        bases = ["win_pct", "efficiency", "scored_first", "win_pct_ht"]
        weights = {"win_pct": 0.28, "efficiency": 0.22, "scored_first": 0.20, "win_pct_ht": 0.20}

        for base in bases:
            for side in ["home", "away"]:
                value = row.get(f"{base}_{side}")
                if value is not None and not pd.isna(value):
                    score += float(value) * (weights[base] / 2)

        games_home = row.get("games_home")
        games_away = row.get("games_away")
        if games_home is not None and not pd.isna(games_home):
            score += min(float(games_home), 40) * 0.05
        if games_away is not None and not pd.isna(games_away):
            score += min(float(games_away), 40) * 0.05

        return min(score, 100)

    df["direcao_sugerida"] = df.apply(define_direction, axis=1)
    df["score_final"] = df.apply(score_row, axis=1)
    df[["confianca", "confianca_cor"]] = df["score_final"].apply(lambda s: pd.Series(pick_confidence(s)))
    df["forca_diff"] = df.apply(calcular_forca_geral, axis=1)

    def market_tag(row: pd.Series) -> str:
        d = row.get("direcao_sugerida", "")
        if d == "Casa para vencer":
            return "ML Casa"
        if d == "Visitante para vencer":
            return "ML Visitante"
        if d == "Mais de 2.5 gols":
            return "Over 2.5"
        if d == "Menos de 2.5 gols":
            return "Under 2.5"
        if d == "Ambas marcam":
            return "BTTS Sim"
        return "Sem vantagem clara"

    df["mercado_sugerido"] = df.apply(market_tag, axis=1)
    return df


# ============================================================
# RENDER HELPERS
# ============================================================
def html_header(total_matches: int, total_opportunities: int, avg_score: float) -> None:
    st.markdown(
        f"""
        <div class="gn-shell">
            <div class="gn-header">
                <div>
                    <div class="gn-title">GolEmNúmeros - Leitura Pré Live</div>
                    <div class="gn-subtitle">Painel operacional com lista de confrontos e detalhe individual por clique</div>
                </div>
                <div class="gn-chiprow">
                    <div class="gn-chip">
                        <div class="gn-chip-label">Jogos</div>
                        <div class="gn-chip-value">{total_matches}</div>
                    </div>
                    <div class="gn-chip">
                        <div class="gn-chip-label">Oportunidades</div>
                        <div class="gn-chip-value">{total_opportunities}</div>
                    </div>
                    <div class="gn-chip">
                        <div class="gn-chip-label">Score Médio</div>
                        <div class="gn-chip-value">{avg_score:.0f}</div>
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def pill(label: str, kind: str = "blue") -> str:
    return f'<span class="pill pill-{kind}">{label}</span>'


def render_match_card(row: pd.Series, idx: int) -> None:
    confidence_color = row.get("confianca_cor", "blue")
    status = str(row.get("status", "-")).upper()
    status_kind = "green" if status == "NS" else "yellow" if status in {"HT", "LIVE", "AO VIVO"} else "blue"
    data_flag = has_detail_data(row)
    data_badge = (
        '<span title="Confronto com dados detalhados" '
        'style="display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;'
        'border-radius:50%;background:rgba(255,214,103,.18);border:1px solid rgba(255,214,103,.55);'
        'color:#ffe26d;font-size:12px;box-shadow:0 0 10px rgba(255,214,103,.28);">⬤</span>'
        if data_flag else ""
    )
    data_text = '<div style="font-size:.78rem;color:#ffe8a2;margin-top:8px;font-weight:700;">🟡 Com dados</div>' if data_flag else ""

    st.markdown(
        f"""
        <div class="match-card">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
                <div class="match-league">{row.get('league', '-')}</div>
                <div>{data_badge}</div>
            </div>
            <div class="match-name">{row.get('match_name', '-')}</div>
            <div class="match-meta">
                {pill(str(row.get('hour', '-')), 'blue')}
                {pill(status, status_kind)}
                {pill(str(row.get('confianca', '-')), confidence_color)}
            </div>
            <div class="odds-row">
                <div class="odd-box"><div class="odd-label">Casa</div><div class="odd-value">{fmt_num(row.get('odd_home'))}</div></div>
                <div class="odd-box"><div class="odd-label">Over 2.5</div><div class="odd-value">{fmt_num(row.get('odd_over25'))}</div></div>
                <div class="odd-box"><div class="odd-label">Visitante</div><div class="odd-value">{fmt_num(row.get('odd_away'))}</div></div>
            </div>
            <div style="font-size:.88rem;color:#a8c5ea;margin-bottom:6px;">Leitura: <b>{row.get('direcao_sugerida','-')}</b></div>
            <div style="font-size:.88rem;color:#a8c5ea;">Mercado: <b>{row.get('mercado_sugerido','-')}</b></div>
            <div style="font-size:.88rem;color:#a8c5ea;margin-top:6px;">Força geral: <b>{row.get('forca_diff','-')}</b></div>
            {data_text}
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button(f"Abrir confronto #{idx+1}", key=f"open_match_{idx}"):
        st.session_state["selected_match"] = idx
        st.rerun()


def render_metric_panel(title: str, rows: list[tuple[str, str]]) -> None:
    html = f'<div class="gn-panel"><div class="gn-panel-title">{title}</div><table class="kv-table">'
    for k, v in rows:
        html += f"<tr><td>{k}</td><td>{v}</td></tr>"
    html += "</table></div>"
    st.markdown(html, unsafe_allow_html=True)


def render_compare_table(row: pd.Series, daily_compare_means: dict) -> None:
    pair_bases = [
        ("(W%) Vitórias", "win_pct", True),
        ("(D%) Empates", "draw_pct", True),
        ("(L%) Derrotas", "loss_pct", True),
        ("Eficiência", "efficiency", True),
        ("Eficácia 1º tempo", "efficacy_ht", True),
        ("Eficiência 2º tempo", "efficiency_2h", True),
        ("Classificação", "rank", False),
        ("Número de jogos", "games", False),
        ("Média gols marcados", "avg_gf", False),
        ("Média gols sofridos", "avg_ga", False),
        ("Média gols marcados 1ºT", "avg_gf_ht", False),
        ("Média gols sofridos 1ºT", "avg_ga_ht", False),
        ("Média gols marcados 2ºT", "avg_gf_2h", False),
        ("Média gols sofridos 2ºT", "avg_ga_2h", False),
    ]

    html = """
    <div class="gn-panel">
        <div class="gn-panel-title">Comparativo principal do confronto</div>
        <table class="compare-table">
            <thead>
                <tr>
                    <th>Métrica</th>
                    <th>Casa</th>
                    <th>Visitante</th>
                </tr>
            </thead>
            <tbody>
    """

    def to_num(v):
        n = to_float(v)
        if n is None:
            return None
        return n * 100 if abs(n) <= 1 else n

    for label, base, is_pct in pair_bases:
        home_raw = row.get(f"{base}_home")
        away_raw = row.get(f"{base}_away")

        if is_pct:
            home_v = fmt_pct(home_raw, 2)
            away_v = fmt_pct(away_raw, 2)
        else:
            home_v = fmt_num(home_raw, 1, False)
            away_v = fmt_num(away_raw, 1, False)

        home_n = to_num(home_raw)
        away_n = to_num(away_raw)

        if base != "rank":
            media = daily_compare_means.get(base)
            media_n = to_num(media)

            if home_n is not None and media_n is not None and home_n > media_n:
                home_v = f"{home_v} 🟢"

            if away_n is not None and media_n is not None and away_n > media_n:
                away_v = f"{away_v} 🟢"

        html += (
            "<tr>"
            f"<td>{label}</td>"
            f"<td>{home_v}</td>"
            f"<td>{away_v}</td>"
            "</tr>"
        )

    html += """
            </tbody>
        </table>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_overall_match_panel(row: pd.Series, daily_means: dict) -> None:
    render_metric_panel(
        "Indicadores gerais do confronto",
        [
            ("Mais de 1.5 gols", indicator_with_day_signal(row.get("over15"), daily_means.get("over15"))),
            ("Mais de 2.5 gols", indicator_with_day_signal(row.get("over25"), daily_means.get("over25"))),
            ("Mais de 0.5 gol 1º tempo", indicator_with_day_signal(row.get("over05ht"), daily_means.get("over05ht"))),
            ("Menos de 2.5 gols", indicator_with_day_signal(row.get("under25"), daily_means.get("under25"))),
            ("Menos de 1.5 gols", indicator_with_day_signal(row.get("under15"), daily_means.get("under15"))),
            ("Ambas marcam", indicator_with_day_signal(row.get("btts"), daily_means.get("btts"))),
        ],
    )


def render_patterns_events_table(row: pd.Series, daily_pattern_means: dict) -> None:
    rows = [
        ("Marcou primeiro gol", "scored_first"),
        ("Marcou primeiro gol 1º tempo", "scored_first_ht"),
        ("Sofreu primeiro gol 1º tempo", "conceded_first_ht"),
        ("Sofreu primeiro gol", "conceded_first"),
    ]

    html = """
    <div class="gn-panel">
        <div class="gn-panel-title">Padrões e eventos</div>
        <table class="compare-table">
            <thead>
                <tr>
                    <th>Métrica</th>
                    <th>Casa</th>
                    <th>Visitante</th>
                </tr>
            </thead>
            <tbody>
    """

    for label, base in rows:
        casa_raw = row.get(f"{base}_home")
        visitante_raw = row.get(f"{base}_away")

        casa_txt = fmt_pct(casa_raw, 2)
        visitante_txt = fmt_pct(visitante_raw, 2)

        media = daily_pattern_means.get(base)
        casa_num = to_float(casa_raw)
        visitante_num = to_float(visitante_raw)
        media_num = to_float(media)

        if casa_num is not None and media_num is not None and casa_num > media_num:
            casa_txt = f"{casa_txt} 🟢"

        if visitante_num is not None and media_num is not None and visitante_num > media_num:
            visitante_txt = f"{visitante_txt} 🟢"

        html += (
            "<tr>"
            f"<td>{label}</td>"
            f"<td>{casa_txt}</td>"
            f"<td>{visitante_txt}</td>"
            "</tr>"
        )

    html += """
            </tbody>
        </table>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_results_common_table(row: pd.Series) -> None:
    rows = [
        ("Primeiro resultado mais comum", "res1"),
        ("Segundo resultado mais comum", "res2"),
        ("Terceiro resultado mais comum", "res3"),
        ("Primeiro resultado mais comum 1º tempo", "res1_ht"),
        ("Segundo resultado mais comum 1º tempo", "res2_ht"),
        ("Terceiro resultado mais comum 1º tempo", "res3_ht"),
    ]

    html = """
    <div class="gn-panel">
        <div class="gn-panel-title">Resultados mais comuns</div>
        <table class="compare-table">
            <thead>
                <tr>
                    <th>Métrica</th>
                    <th>Casa</th>
                    <th>Visitante</th>
                </tr>
            </thead>
            <tbody>
    """

    for label, base in rows:
        casa = str(row.get(f"{base}_home", "-"))
        visitante = str(row.get(f"{base}_away", "-"))

        html += (
            "<tr>"
            f"<td>{label}</td>"
            f"<td>{casa}</td>"
            f"<td>{visitante}</td>"
            "</tr>"
        )

    html += """
            </tbody>
        </table>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


# ============================================================
# APP
# ============================================================
def main() -> None:
    inject_css()

    try:
        df = load_data()
    except Exception as e:
        st.error(f"Erro ao carregar a planilha pública: {e}")
        st.stop()

    if df.empty:
        st.warning("A planilha está vazia.")
        st.stop()

    if "selected_match" not in st.session_state:
        st.session_state["selected_match"] = None

    filter_col1, filter_col2, filter_col3, filter_col4, filter_col5, filter_col6 = st.columns([1.05, 1.0, 0.95, 1.25, 1.1, 1.0])

    with filter_col1:
        leagues = ["Todas"] + sorted([x for x in df["league"].dropna().astype(str).unique().tolist() if x != "-"])
        league_filter = st.selectbox("Liga", leagues)

    with filter_col2:
        statuses = ["Todos"] + sorted([x for x in df["status"].dropna().astype(str).unique().tolist() if x != "-"])
        status_filter = st.selectbox("Status", statuses)

    with filter_col3:
        confs = ["Todas", "Alta", "Média", "Baixa"]
        conf_filter = st.selectbox("Confiança", confs)

    with filter_col4:
        search = st.text_input("Buscar confronto", placeholder="Ex: Liverpool")

    with filter_col5:
        filtro_forca = st.selectbox(
            "Força geral",
            ["Todos", "Casa forte", "Visitante forte", "Equilibrados"]
        )

    with filter_col6:
        dados_filter = st.selectbox(
            "Dados",
            ["Todos", "Com dados", "Sem dados"]
        )

    filtered = df.copy()

    if league_filter != "Todas":
        filtered = filtered[filtered["league"].astype(str) == league_filter]

    if status_filter != "Todos":
        filtered = filtered[filtered["status"].astype(str) == status_filter]

    if conf_filter != "Todas":
        filtered = filtered[filtered["confianca"].astype(str) == conf_filter]

    if search.strip():
        mask = filtered["match_name"].astype(str).str.contains(search, case=False, na=False)
        filtered = filtered[mask]

    if filtro_forca == "Casa forte":
        filtered = filtered[filtered["forca_diff"] >= 2]
    elif filtro_forca == "Visitante forte":
        filtered = filtered[filtered["forca_diff"] <= -2]
    elif filtro_forca == "Equilibrados":
        filtered = filtered[filtered["forca_diff"].between(-1, 1)]

    if dados_filter == "Com dados":
        filtered = filtered[filtered.apply(has_detail_data, axis=1)]
    elif dados_filter == "Sem dados":
        filtered = filtered[~filtered.apply(has_detail_data, axis=1)]

    filtered = filtered.sort_values(
        ["forca_diff", "score_final", "league", "hour"],
        ascending=[False, False, True, True]
    ).reset_index(drop=True)

    daily_means = compute_daily_indicator_means(df)
    daily_pattern_means = compute_daily_pattern_means(df)
    daily_compare_means = compute_daily_compare_means(df)

    total_opps = int((filtered["score_final"] >= 50).sum()) if "score_final" in filtered.columns else 0
    avg_score = float(filtered["score_final"].mean()) if len(filtered) else 0.0
    html_header(len(filtered), total_opps, avg_score)

    selected = st.session_state.get("selected_match")
    if selected is None or selected >= len(filtered):
        st.markdown('<div class="gn-shell">', unsafe_allow_html=True)
        st.markdown("### Confrontos do dia")
        list_cols = st.columns(3)
        for i, (_, row) in enumerate(filtered.iterrows()):
            with list_cols[i % 3]:
                render_match_card(row, i)
        st.markdown("</div>", unsafe_allow_html=True)

        show_cols = [
            c for c in [
                "league", "hour", "match_name", "status", "odd_home", "odd_over25", "odd_away",
                "direcao_sugerida", "mercado_sugerido", "confianca", "score_final", "forca_diff"
            ] if c in filtered.columns
        ]
        st.dataframe(filtered[show_cols], use_container_width=True, hide_index=True)
        return

    row = filtered.iloc[selected]

    if st.button("← Voltar para lista de confrontos"):
        st.session_state["selected_match"] = None
        st.rerun()

    top_html = f"""
    <div class="gn-shell">
        <div class="gn-header">
            <div>
                <div class="gn-subtitle">{row.get('league','-')}</div>
                <div class="gn-title">{row.get('home','-')} <span style='font-size:1.1rem;color:#8fa8ce;'>vs</span> {row.get('away','-')}</div>
                <div class="gn-subtitle">{row.get('date','-')} &nbsp;&nbsp; {row.get('hour','-')} &nbsp;&nbsp; Status: {row.get('status','-')}</div>
            </div>
            <div class="gn-chiprow">
                <div class="gn-chip"><div class="gn-chip-label">Casa</div><div class="gn-chip-value">{fmt_num(row.get('odd_home'))}</div></div>
                <div class="gn-chip"><div class="gn-chip-label">Over 2.5</div><div class="gn-chip-value">{fmt_num(row.get('odd_over25'))}</div></div>
                <div class="gn-chip"><div class="gn-chip-label">Visitante</div><div class="gn-chip-value">{fmt_num(row.get('odd_away'))}</div></div>
                <div class="gn-chip"><div class="gn-chip-label">Confiança</div><div class="gn-chip-value">{row.get('confianca','-')}</div></div>
            </div>
        </div>
    </div>
    """
    st.markdown(top_html, unsafe_allow_html=True)

    row1_col1, row1_col2, row1_col3 = st.columns([1.0, 1.0, 1.0])

    with row1_col1:
        render_metric_panel(
            "Mercados",
            [
                ("Odds Casa para vencer", fmt_num(row.get("odd_home"))),
                ("Odds Visitante para vencer", fmt_num(row.get("odd_away"))),
                ("Odds Mais de 2.5 gols", fmt_num(row.get("odd_over25"))),
                ("Odds Menos de 2.5 gols", fmt_num(row.get("odd_under25"))),
                ("Odds Ambas as equipes marcam (Sim)", fmt_num(row.get("odd_btts_yes"))),
                ("Odds Ambas as equipes marcam (Não)", fmt_num(row.get("odd_btts_no"))),
                ("Odds Mais de 0.5 gol 1º tempo", fmt_num(row.get("odd_over05ht"))),
            ],
        )

    with row1_col2:
        render_metric_panel(
            "Inteligência do jogo",
            [
                ("Direção sugerida", str(row.get("direcao_sugerida", "-"))),
                ("Mercado sugerido", str(row.get("mercado_sugerido", "-"))),
                ("Score final", fmt_num(row.get("score_final"), 1)),
                ("Confiança", str(row.get("confianca", "-"))),
                ("Prob. implícita Casa", fmt_pct(row.get("prob_home"), 2)),
                ("Prob. implícita Visitante", fmt_pct(row.get("prob_away"), 2)),
                ("Prob. implícita Over 2.5", fmt_pct(row.get("prob_over25"), 2)),
                ("Força geral", fmt_num(row.get("forca_diff"), 0)),
            ],
        )

    with row1_col3:
        render_overall_match_panel(row, daily_means)

    row2_col1, row2_col2, row2_col3 = st.columns([1.15, 0.95, 0.95])

    with row2_col1:
        render_compare_table(row, daily_compare_means)

    with row2_col2:
        render_patterns_events_table(row, daily_pattern_means)

    with row2_col3:
        render_results_common_table(row)

    export_cols = [
        c for c in [
            "league", "date", "hour", "home", "away", "status", "odd_home", "odd_away", "odd_over25",
            "odd_under25", "odd_btts_yes", "odd_btts_no", "odd_over05ht",
            "direcao_sugerida", "mercado_sugerido", "confianca", "score_final", "forca_diff"
        ] if c in filtered.columns
    ]
    csv_out = filtered[export_cols].to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Baixar confrontos filtrados em CSV",
        data=csv_out,
        file_name="confrontos_filtrados_pre_live.csv",
        mime="text/csv"
    )


if __name__ == "__main__":
    main()
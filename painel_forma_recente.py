import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(page_title="Painel de Forma Recente", layout="wide")

# =========================================================
# ESTILO
# =========================================================
st.markdown("""
<style>
html, body, [class*="css"] {
    background: #071018;
    color: #e8edf2;
}
.block-container {
    padding-top: 1.2rem;
    padding-bottom: 2rem;
    max-width: 1450px;
}
.main-title {
    font-size: 30px;
    font-weight: 800;
    color: #ffffff;
    margin-bottom: 6px;
}
.sub-title {
    font-size: 14px;
    color: #a9b4c2;
    margin-bottom: 20px;
}
.card {
    background: linear-gradient(180deg, #0d1722 0%, #09121a 100%);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 18px;
    padding: 16px 18px;
    box-shadow: 0 8px 26px rgba(0,0,0,0.25);
    margin-bottom: 14px;
}
.metric-card {
    background: linear-gradient(180deg, #101c28 0%, #0a121a 100%);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 14px;
    text-align: center;
    min-height: 96px;
}
.section-title {
    font-size: 18px;
    font-weight: 700;
    margin-bottom: 12px;
    color: #ffffff;
}
.small-note {
    font-size: 12px;
    color: #8fa2b6;
}
.metric-label {
    font-size: 12px;
    color: #9fb0c2;
    margin-bottom: 6px;
}
.metric-value {
    font-size: 26px;
    font-weight: 800;
    color: #ffffff;
}
.metric-sub {
    font-size: 11px;
    color: #7f91a6;
    margin-top: 5px;
}
.tag-green, .tag-yellow, .tag-red, .tag-blue {
    display: inline-block;
    padding: 6px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    margin-right: 8px;
    margin-bottom: 8px;
}
.tag-green { background: rgba(34,197,94,0.15); color: #6ee7a3; border: 1px solid rgba(34,197,94,0.35); }
.tag-yellow { background: rgba(245,158,11,0.15); color: #fcd34d; border: 1px solid rgba(245,158,11,0.35); }
.tag-red { background: rgba(239,68,68,0.15); color: #fca5a5; border: 1px solid rgba(239,68,68,0.35); }
.tag-blue { background: rgba(59,130,246,0.15); color: #93c5fd; border: 1px solid rgba(59,130,246,0.35); }

div[data-baseweb="input"] input {
    background: #0a121a !important;
    color: white !important;
}
thead tr th {
    background-color: #0c1723 !important;
    color: white !important;
}
</style>
""", unsafe_allow_html=True)

# =========================================================
# FUNÇÕES
# =========================================================
def clamp(x, min_v=0, max_v=100):
    return max(min_v, min(max_v, x))

def parse_placar(placar: str):
    if not placar:
        return np.nan, np.nan

    txt = str(placar).strip().lower()
    if txt in ["adiado", "cancelado", "postergado", "-", ""]:
        return np.nan, np.nan

    if "-" not in txt:
        return np.nan, np.nan

    try:
        a, b = txt.replace(" ", "").split("-")
        return int(a), int(b)
    except Exception:
        return np.nan, np.nan

def peso_posicao_adversario(posicao_adversario, total_times):
    if pd.isna(posicao_adversario) or total_times <= 0:
        return 1.0

    faixa = posicao_adversario / total_times

    if faixa <= 0.20:
        return 1.30
    elif faixa <= 0.40:
        return 1.15
    elif faixa <= 0.60:
        return 1.00
    elif faixa <= 0.80:
        return 0.90
    return 0.80

def peso_recencia(idx):
    pesos = {0: 1.30, 1: 1.15, 2: 1.00, 3: 0.90}
    return pesos.get(idx, 1.0)

def montar_df(jogos, total_times, lado=""):
    rows = []

    for i, jogo in enumerate(jogos):
        gm, gs = parse_placar(jogo["placar"])
        pos_adv = jogo["posicao_adversario"]

        peso_pos = peso_posicao_adversario(pos_adv, total_times)
        peso_rec = peso_recencia(i)
        peso_final = peso_pos * peso_rec

        rows.append({
            "jogo": i + 1,
            "placar": jogo["placar"],
            "posicao_do_adversario": pos_adv,
            "gols_marcados": gm,
            "gols_sofridos": gs,
            "peso_posicao_adversario": round(peso_pos, 2),
            "peso_recencia": round(peso_rec, 2),
            "peso_final": round(peso_final, 2),
            "lado": lado
        })

    return pd.DataFrame(rows)

def media_ponderada(serie, pesos):
    serie = pd.to_numeric(serie, errors="coerce")
    pesos = pd.to_numeric(pesos, errors="coerce")
    mask = serie.notna() & pesos.notna()

    if mask.sum() == 0:
        return np.nan

    return np.average(serie[mask], weights=pesos[mask])

def taxa_ponderada_boolean(df, col_bool, col_peso="peso_final"):
    if df.empty:
        return np.nan

    base = df.copy()
    mask = base[col_bool].notna() & base[col_peso].notna()
    if mask.sum() == 0:
        return np.nan

    return float(np.average(
        base.loc[mask, col_bool].astype(float),
        weights=base.loc[mask, col_peso].astype(float)
    )) * 100

def calcular_metricas(df):
    if df.empty:
        return {}

    d = df.copy()

    d["marcou"] = (d["gols_marcados"] >= 1).astype(float)
    d["marcou_2+"] = (d["gols_marcados"] >= 2).astype(float)
    d["sofreu"] = (d["gols_sofridos"] >= 1).astype(float)
    d["clean_sheet"] = (d["gols_sofridos"] == 0).astype(float)
    d["btts"] = ((d["gols_marcados"] >= 1) & (d["gols_sofridos"] >= 1)).astype(float)
    d["over_1_5"] = ((d["gols_marcados"] + d["gols_sofridos"]) >= 2).astype(float)
    d["over_2_5"] = ((d["gols_marcados"] + d["gols_sofridos"]) >= 3).astype(float)
    d["over_3_5"] = ((d["gols_marcados"] + d["gols_sofridos"]) >= 4).astype(float)
    d["under_2_5"] = ((d["gols_marcados"] + d["gols_sofridos"]) <= 2).astype(float)
    d["under_3_5"] = ((d["gols_marcados"] + d["gols_sofridos"]) <= 3).astype(float)

    media_gm = media_ponderada(d["gols_marcados"], d["peso_final"])
    media_gs = media_ponderada(d["gols_sofridos"], d["peso_final"])
    media_total = media_ponderada(d["gols_marcados"] + d["gols_sofridos"], d["peso_final"])

    total_gols_validos = (d["gols_marcados"].fillna(0) + d["gols_sofridos"].fillna(0))
    std_total = total_gols_validos.std(ddof=0) if len(total_gols_validos) > 0 else 0
    confiabilidade = clamp(100 - (std_total * 18), 35, 95)

    return {
        "media_gols_marcados": round(media_gm, 2) if pd.notna(media_gm) else 0.0,
        "media_gols_sofridos": round(media_gs, 2) if pd.notna(media_gs) else 0.0,
        "media_total_gols": round(media_total, 2) if pd.notna(media_total) else 0.0,
        "pct_marcou": round(taxa_ponderada_boolean(d, "marcou"), 1),
        "pct_marcou_2+": round(taxa_ponderada_boolean(d, "marcou_2+"), 1),
        "pct_sofreu": round(taxa_ponderada_boolean(d, "sofreu"), 1),
        "pct_clean_sheet": round(taxa_ponderada_boolean(d, "clean_sheet"), 1),
        "pct_btts": round(taxa_ponderada_boolean(d, "btts"), 1),
        "pct_over_1_5": round(taxa_ponderada_boolean(d, "over_1_5"), 1),
        "pct_over_2_5": round(taxa_ponderada_boolean(d, "over_2_5"), 1),
        "pct_over_3_5": round(taxa_ponderada_boolean(d, "over_3_5"), 1),
        "pct_under_2_5": round(taxa_ponderada_boolean(d, "under_2_5"), 1),
        "pct_under_3_5": round(taxa_ponderada_boolean(d, "under_3_5"), 1),
        "confiabilidade": round(confiabilidade, 1),
        "df": d
    }

def forca_ofensiva(media_marcados, pct_marcou, pct_marcou_2):
    score = (
        media_marcados * 3.2 +
        (pct_marcou / 100) * 3.8 +
        (pct_marcou_2 / 100) * 3.0
    )
    return clamp(round(score, 2), 0, 10)

def forca_defensiva(media_sofridos, pct_sofreu, pct_clean):
    score = (
        (2.5 - media_sofridos) * 2.8 +
        ((100 - pct_sofreu) / 100) * 3.7 +
        (pct_clean / 100) * 3.5
    )
    return clamp(round(score, 2), 0, 10)

def combinar_probabilidades(mc, mv):
    casa_marca = (
        mc["pct_marcou"] * 0.42 +
        mv["pct_sofreu"] * 0.33 +
        clamp(mc["media_gols_marcados"] * 35, 0, 100) * 0.25
    )

    visitante_marca = (
        mv["pct_marcou"] * 0.42 +
        mc["pct_sofreu"] * 0.33 +
        clamp(mv["media_gols_marcados"] * 35, 0, 100) * 0.25
    )

    exp_gols_casa = (mc["media_gols_marcados"] * 0.58) + (mv["media_gols_sofridos"] * 0.42)
    exp_gols_visit = (mv["media_gols_marcados"] * 0.58) + (mc["media_gols_sofridos"] * 0.42)
    total_esperado = exp_gols_casa + exp_gols_visit

    btts = ((casa_marca + visitante_marca) / 2) - abs(casa_marca - visitante_marca) * 0.15

    over_1_5 = (
        mc["pct_over_1_5"] * 0.30 +
        mv["pct_over_1_5"] * 0.30 +
        clamp(total_esperado * 38, 0, 100) * 0.40
    )

    over_2_5 = (
        mc["pct_over_2_5"] * 0.32 +
        mv["pct_over_2_5"] * 0.32 +
        clamp((total_esperado - 1.2) * 42, 0, 100) * 0.36
    )

    over_3_5 = (
        mc["pct_over_3_5"] * 0.32 +
        mv["pct_over_3_5"] * 0.32 +
        clamp((total_esperado - 2.0) * 40, 0, 100) * 0.36
    )

    under_2_5 = 100 - over_2_5
    under_3_5 = 100 - over_3_5

    return {
        "casa_marca": round(clamp(casa_marca), 1),
        "visitante_marca": round(clamp(visitante_marca), 1),
        "btts": round(clamp(btts), 1),
        "over_1_5": round(clamp(over_1_5), 1),
        "over_2_5": round(clamp(over_2_5), 1),
        "over_3_5": round(clamp(over_3_5), 1),
        "under_2_5": round(clamp(under_2_5), 1),
        "under_3_5": round(clamp(under_3_5), 1),
        "exp_gols_casa": round(exp_gols_casa, 2),
        "exp_gols_visitante": round(exp_gols_visit, 2),
        "total_esperado": round(total_esperado, 2),
    }

def prob_resultado(exp_casa, exp_visit):
    dif = exp_casa - exp_visit

    casa = 45 + (dif * 18)
    empate = 28 - abs(dif) * 6
    visitante = 27 - (dif * 18)

    soma = casa + empate + visitante
    if soma <= 0:
        return 33.3, 33.3, 33.3

    casa = casa / soma * 100
    empate = empate / soma * 100
    visitante = visitante / soma * 100

    return round(clamp(casa), 1), round(clamp(empate), 1), round(clamp(visitante), 1)

def nivel_texto(p):
    if p >= 75:
        return "Muito forte"
    elif p >= 62:
        return "Forte"
    elif p >= 52:
        return "Moderada"
    return "Fraca"

def confianca_texto(v):
    if v >= 78:
        return "Alta"
    elif v >= 60:
        return "Média"
    return "Baixa"

def odd_justa(prob_percent):
    if prob_percent <= 0:
        return np.nan
    return round(100 / prob_percent, 2)

def gerar_diagnostico(prob, fo_casa, fd_casa, fo_visit, fd_visit, conf):
    linhas = []

    if prob["casa_marca"] >= 70:
        linhas.append("Mandante com tendência forte para marcar pelo menos 1 gol.")
    elif prob["casa_marca"] >= 58:
        linhas.append("Mandante com boa chance de marcar, mas sem domínio total.")
    else:
        linhas.append("Mandante sem força tão clara para gol.")

    if prob["visitante_marca"] >= 65:
        linhas.append("Visitante também apresenta boa capacidade de marcar fora.")
    elif prob["visitante_marca"] <= 45:
        linhas.append("Visitante chega com produção ofensiva mais limitada fora.")

    if prob["btts"] >= 60:
        linhas.append("Ambas marcam aparece com suporte razoável na forma recente.")
    elif prob["btts"] <= 45:
        linhas.append("Ambas marcam não surge como principal tendência.")

    if prob["over_2_5"] >= 58:
        linhas.append("Leitura inclinada para jogo com 3 ou mais gols.")
    elif prob["under_2_5"] >= 58:
        linhas.append("Leitura mais inclinada para under 2.5.")

    if fo_casa > fo_visit + 1.2:
        linhas.append("Força ofensiva recente do mandante é superior.")
    elif fo_visit > fo_casa + 1.2:
        linhas.append("Visitante apresenta produção ofensiva recente competitiva.")
    else:
        linhas.append("Produção ofensiva recente relativamente equilibrada.")

    if fd_casa > fd_visit + 1.0:
        linhas.append("Mandante chega com defesa recente mais sólida.")
    elif fd_visit > fd_casa + 1.0:
        linhas.append("Visitante apresenta consistência defensiva recente melhor.")
    else:
        linhas.append("As defesas têm nível próximo.")

    if conf == "Baixa":
        linhas.append("Amostra curta ou irregular: tratar a leitura com cautela.")
    elif conf == "Média":
        linhas.append("Leitura útil, mas ainda sujeita a oscilação.")
    else:
        linhas.append("Forma recente relativamente consistente.")

    return linhas

# =========================================================
# TOPO
# =========================================================
st.markdown('<div class="main-title">Painel de Forma Recente</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-title">Informe apenas os placares dos últimos 4 jogos e a posição dos adversários enfrentados. '
    'Sem necessidade de digitar nome dos adversários.</div>',
    unsafe_allow_html=True
)

c1, c2, c3 = st.columns([1.2, 1.2, 0.8])

with c1:
    nome_casa = st.text_input("Time da casa", value="Corinthians")
with c2:
    nome_visit = st.text_input("Time visitante", value="Vasco")
with c3:
    total_times = st.number_input("Quantidade de times na liga", min_value=10, max_value=30, value=20, step=1)

# =========================================================
# ENTRADA
# =========================================================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.markdown('<div class="section-title">Últimos 4 jogos usados na leitura</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="small-note">Digite o placar no formato 2-1. '
    'A posição é sempre a posição do adversário enfrentado naquele jogo.</div>',
    unsafe_allow_html=True
)

col_casa, col_visit = st.columns(2)

jogos_casa = []
jogos_visit = []

with col_casa:
    st.markdown(f"### {nome_casa} jogando em casa")
    for i in range(4):
        st.markdown(f"**Jogo {i+1}**")
        c_1, c_2 = st.columns([1.1, 1.0])

        placar = c_1.text_input(
            f"Placar - jogo {i+1} ({nome_casa} em casa)",
            value="3-2" if i == 0 else "",
            key=f"c_pla_{i}"
        )
        pos_adv = c_2.number_input(
            f"Posição do adversário - jogo {i+1}",
            min_value=1,
            max_value=int(total_times),
            value=min(i + 4, int(total_times)),
            key=f"c_pos_{i}"
        )

        jogos_casa.append({
            "placar": placar,
            "posicao_adversario": pos_adv
        })

with col_visit:
    st.markdown(f"### {nome_visit} jogando fora")
    for i in range(4):
        st.markdown(f"**Jogo {i+1}**")
        v_1, v_2 = st.columns([1.1, 1.0])

        placar = v_1.text_input(
            f"Placar - jogo {i+1} ({nome_visit} fora)",
            value="0-2" if i == 0 else "",
            key=f"v_pla_{i}"
        )
        pos_adv = v_2.number_input(
            f"Posição do adversário - jogo {i+1}",
            min_value=1,
            max_value=int(total_times),
            value=min(i + 6, int(total_times)),
            key=f"v_pos_{i}"
        )

        jogos_visit.append({
            "placar": placar,
            "posicao_adversario": pos_adv
        })

st.markdown('</div>', unsafe_allow_html=True)

calcular = st.button("Calcular leitura da partida", use_container_width=True)

if calcular:
    df_casa = montar_df(jogos_casa, total_times, lado="Mandante em casa")
    df_visit = montar_df(jogos_visit, total_times, lado="Visitante fora")

    metricas_casa = calcular_metricas(df_casa)
    metricas_visit = calcular_metricas(df_visit)

    fo_casa = forca_ofensiva(
        metricas_casa["media_gols_marcados"],
        metricas_casa["pct_marcou"],
        metricas_casa["pct_marcou_2+"]
    )
    fd_casa = forca_defensiva(
        metricas_casa["media_gols_sofridos"],
        metricas_casa["pct_sofreu"],
        metricas_casa["pct_clean_sheet"]
    )

    fo_visit = forca_ofensiva(
        metricas_visit["media_gols_marcados"],
        metricas_visit["pct_marcou"],
        metricas_visit["pct_marcou_2+"]
    )
    fd_visit = forca_defensiva(
        metricas_visit["media_gols_sofridos"],
        metricas_visit["pct_sofreu"],
        metricas_visit["pct_clean_sheet"]
    )

    prob = combinar_probabilidades(metricas_casa, metricas_visit)
    p_casa, p_empate, p_visit = prob_resultado(prob["exp_gols_casa"], prob["exp_gols_visitante"])

    confianca_final = round((metricas_casa["confiabilidade"] + metricas_visit["confiabilidade"]) / 2, 1)
    confianca_txt = confianca_texto(confianca_final)

    # RESUMO
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f'<div class="section-title">{nome_casa} x {nome_visit}</div>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <span class="tag-blue">Confiança da leitura: {confianca_txt} ({confianca_final}%)</span>
        <span class="tag-green">Gols esperados {nome_casa}: {prob["exp_gols_casa"]}</span>
        <span class="tag-yellow">Gols esperados {nome_visit}: {prob["exp_gols_visitante"]}</span>
        <span class="tag-red">Total esperado: {prob["total_esperado"]}</span>
        """,
        unsafe_allow_html=True
    )
    st.markdown('</div>', unsafe_allow_html=True)

    # FORÇAS
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Força das equipes</div>', unsafe_allow_html=True)

    f1, f2, f3, f4 = st.columns(4)
    cards_forca = [
        (f"Força ofensiva {nome_casa}", fo_casa),
        (f"Força defensiva {nome_casa}", fd_casa),
        (f"Força ofensiva {nome_visit}", fo_visit),
        (f"Força defensiva {nome_visit}", fd_visit),
    ]

    for col, (label, valor) in zip([f1, f2, f3, f4], cards_forca):
        with col:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">{label}</div>
                    <div class="metric-value">{valor:.2f}</div>
                    <div class="metric-sub">Escala 0 a 10</div>
                </div>
                """,
                unsafe_allow_html=True
            )

    st.markdown('</div>', unsafe_allow_html=True)

    # PROBABILIDADES
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Probabilidades principais</div>', unsafe_allow_html=True)

    p1, p2, p3, p4, p5, p6 = st.columns(6)
    probs_1 = [
        ("Casa marca", prob["casa_marca"]),
        ("Visitante marca", prob["visitante_marca"]),
        ("Ambas marcam", prob["btts"]),
        ("Over 1.5", prob["over_1_5"]),
        ("Over 2.5", prob["over_2_5"]),
        ("Under 2.5", prob["under_2_5"]),
    ]

    for col, (label, value) in zip([p1, p2, p3, p4, p5, p6], probs_1):
        with col:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">{label}</div>
                    <div class="metric-value">{value:.1f}%</div>
                    <div class="metric-sub">{nivel_texto(value)} | Odd justa {odd_justa(value)}</div>
                </div>
                """,
                unsafe_allow_html=True
            )

    st.markdown("<br>", unsafe_allow_html=True)

    q1, q2, q3, q4, q5 = st.columns(5)
    probs_2 = [
        ("Over 3.5", prob["over_3_5"]),
        ("Under 3.5", prob["under_3_5"]),
        ("Casa vence", p_casa),
        ("Empate", p_empate),
        ("Visitante vence", p_visit),
    ]

    for col, (label, value) in zip([q1, q2, q3, q4, q5], probs_2):
        with col:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">{label}</div>
                    <div class="metric-value">{value:.1f}%</div>
                    <div class="metric-sub">{nivel_texto(value)} | Odd justa {odd_justa(value)}</div>
                </div>
                """,
                unsafe_allow_html=True
            )

    st.markdown('</div>', unsafe_allow_html=True)

    # RESUMO DAS FORMAS
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Resumo da forma recente</div>', unsafe_allow_html=True)

    s1, s2 = st.columns(2)

    with s1:
        resumo_casa = pd.DataFrame({
            "Métrica": [
                "Média gols marcados",
                "Média gols sofridos",
                "% marcou gol",
                "% marcou 2+ gols",
                "% sofreu gol",
                "% clean sheet",
                "% ambas marcam",
                "% over 1.5",
                "% over 2.5",
                "% under 2.5",
                "Confiabilidade"
            ],
            "Valor": [
                metricas_casa["media_gols_marcados"],
                metricas_casa["media_gols_sofridos"],
                f'{metricas_casa["pct_marcou"]:.1f}%',
                f'{metricas_casa["pct_marcou_2+"]:.1f}%',
                f'{metricas_casa["pct_sofreu"]:.1f}%',
                f'{metricas_casa["pct_clean_sheet"]:.1f}%',
                f'{metricas_casa["pct_btts"]:.1f}%',
                f'{metricas_casa["pct_over_1_5"]:.1f}%',
                f'{metricas_casa["pct_over_2_5"]:.1f}%',
                f'{metricas_casa["pct_under_2_5"]:.1f}%',
                f'{metricas_casa["confiabilidade"]:.1f}%'
            ]
        })
        st.markdown(f"### {nome_casa} em casa")
        st.dataframe(resumo_casa, use_container_width=True, hide_index=True)

    with s2:
        resumo_visit = pd.DataFrame({
            "Métrica": [
                "Média gols marcados",
                "Média gols sofridos",
                "% marcou gol",
                "% marcou 2+ gols",
                "% sofreu gol",
                "% clean sheet",
                "% ambas marcam",
                "% over 1.5",
                "% over 2.5",
                "% under 2.5",
                "Confiabilidade"
            ],
            "Valor": [
                metricas_visit["media_gols_marcados"],
                metricas_visit["media_gols_sofridos"],
                f'{metricas_visit["pct_marcou"]:.1f}%',
                f'{metricas_visit["pct_marcou_2+"]:.1f}%',
                f'{metricas_visit["pct_sofreu"]:.1f}%',
                f'{metricas_visit["pct_clean_sheet"]:.1f}%',
                f'{metricas_visit["pct_btts"]:.1f}%',
                f'{metricas_visit["pct_over_1_5"]:.1f}%',
                f'{metricas_visit["pct_over_2_5"]:.1f}%',
                f'{metricas_visit["pct_under_2_5"]:.1f}%',
                f'{metricas_visit["confiabilidade"]:.1f}%'
            ]
        })
        st.markdown(f"### {nome_visit} fora")
        st.dataframe(resumo_visit, use_container_width=True, hide_index=True)

    st.markdown('</div>', unsafe_allow_html=True)

    # JOGOS USADOS
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Jogos usados na leitura</div>', unsafe_allow_html=True)

    t1, t2 = st.columns(2)

    with t1:
        st.markdown(f"### {nome_casa} em casa")
        st.dataframe(df_casa, use_container_width=True, hide_index=True)

    with t2:
        st.markdown(f"### {nome_visit} fora")
        st.dataframe(df_visit, use_container_width=True, hide_index=True)

    st.markdown('</div>', unsafe_allow_html=True)

    # DIAGNÓSTICO
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Diagnóstico automático</div>', unsafe_allow_html=True)

    mercado_principal = max(
        {
            "Casa marca": prob["casa_marca"],
            "Visitante marca": prob["visitante_marca"],
            "Ambas marcam": prob["btts"],
            "Over 1.5": prob["over_1_5"],
            "Over 2.5": prob["over_2_5"],
            "Under 2.5": prob["under_2_5"],
            "Under 3.5": prob["under_3_5"],
            "Casa vence": p_casa,
            "Empate": p_empate,
            "Visitante vence": p_visit
        }.items(),
        key=lambda x: x[1]
    )

    st.markdown(
        f"""
        <span class="tag-green">Mercado mais forte: {mercado_principal[0]} ({mercado_principal[1]:.1f}%)</span>
        <span class="tag-blue">Odd justa: {odd_justa(mercado_principal[1])}</span>
        <span class="tag-yellow">Confiança da leitura: {confianca_txt}</span>
        """,
        unsafe_allow_html=True
    )

    diagnostico = gerar_diagnostico(prob, fo_casa, fd_casa, fo_visit, fd_visit, confianca_txt)
    for item in diagnostico:
        st.write(f"- {item}")

    st.markdown('</div>', unsafe_allow_html=True)

else:
    st.info("Preencha apenas os placares e as posições dos adversários, depois clique em Calcular leitura da partida.")
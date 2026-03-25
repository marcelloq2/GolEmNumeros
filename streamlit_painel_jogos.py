import streamlit as st
import pandas as pd
import numpy as np
import re

st.set_page_config(
    page_title="Painel GGolEmNumeros",
    layout="centered",
    initial_sidebar_state="collapsed"
)

LINK_CSV = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSF5WBP5KeBr6cVbAK0yH2IJf_luqoK90gOz1fj_VlS_hoAb4E6v_awCWO-bTi28I-mWYWEeewnhmTh/pub?output=csv"

st.markdown("""
<style>
.block-container{
    padding-top: 0.8rem;
    padding-bottom: 2rem;
    padding-left: 0.7rem;
    padding-right: 0.7rem;
    max-width: 760px;
}
.main-title {
    font-size: 1.35rem;
    font-weight: 800;
    margin-bottom: 0.1rem;
    color: #ffffff;
}
.sub-title {
    font-size: 0.82rem;
    color: #b8c3d6;
    margin-bottom: 1rem;
}
.section-title {
    font-size: 0.98rem;
    font-weight: 800;
    color: white;
    margin-top: 0.8rem;
    margin-bottom: 0.6rem;
}
.card-box {
    background: linear-gradient(180deg, #0f172a 0%, #111827 100%);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 12px;
    margin-bottom: 10px;
    box-shadow: 0 4px 14px rgba(0,0,0,0.18);
}
.badge {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 0.74rem;
    font-weight: 700;
    text-align: center;
}
.badge-ns {
    background: rgba(37, 99, 235, 0.20);
    color: #93c5fd;
    border: 1px solid rgba(147,197,253,0.35);
}
.badge-ft {
    background: rgba(34, 197, 94, 0.18);
    color: #86efac;
    border: 1px solid rgba(134,239,172,0.35);
}
.badge-soft {
    background: rgba(255,255,255,0.06);
    color: #d1d5db;
    border: 1px solid rgba(255,255,255,0.08);
}
.small-label {
    font-size: 0.72rem;
    color: #94a3b8;
}
.big-value {
    font-size: 0.98rem;
    font-weight: 800;
    color: #f8fafc;
}
.placar-ft {
    font-size: 1.35rem;
    font-weight: 900;
    color: #ffffff;
    margin-top: 6px;
    margin-bottom: 6px;
}
.box-detalhe {
    background: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 14px;
    padding: 12px;
    margin-bottom: 14px;
}
</style>
""", unsafe_allow_html=True)


def normalizar_texto(txt):
    if txt is None:
        return ""
    txt = str(txt).strip()
    txt = re.sub(r"\s+", " ", txt)
    return txt


def slug(txt):
    txt = normalizar_texto(txt).lower()
    txt = (
        txt.replace("ã", "a")
           .replace("á", "a")
           .replace("à", "a")
           .replace("â", "a")
           .replace("é", "e")
           .replace("ê", "e")
           .replace("í", "i")
           .replace("ó", "o")
           .replace("ô", "o")
           .replace("õ", "o")
           .replace("ú", "u")
           .replace("ç", "c")
    )
    txt = re.sub(r"[^a-z0-9]+", "_", txt)
    return txt.strip("_")


@st.cache_data(show_spinner=False)
def carregar_base():
    df = pd.read_csv(LINK_CSV)
    df.columns = [normalizar_texto(c) for c in df.columns]
    return df


def encontrar_coluna(df, candidatos):
    mapa = {c: slug(c) for c in df.columns}
    for cand in candidatos:
        for col_original, col_slug in mapa.items():
            if col_slug == cand:
                return col_original
    return None


def obter_colunas(df):
    colunas = {}
    colunas["country"] = encontrar_coluna(df, ["country", "pais"])
    colunas["league"] = encontrar_coluna(df, ["league", "liga", "campeonato", "competicao", "short"])
    colunas["hour"] = encontrar_coluna(df, ["hour", "hora", "horario", "time"])
    colunas["status"] = encontrar_coluna(df, ["status", "estado", "situacao"])
    colunas["home"] = encontrar_coluna(df, ["home_team", "time_casa", "casa", "mandante", "home"])
    colunas["away"] = encontrar_coluna(df, ["visitor_team", "away_team", "time_visitante", "visitante", "fora", "away"])
    colunas["gols_home"] = encontrar_coluna(df, ["result_home", "gols_casa", "home_goals", "placar_casa", "resultado_casa"])
    colunas["gols_away"] = encontrar_coluna(df, ["result_visitor", "gols_visitante", "away_goals", "placar_visitante", "resultado_visitante"])

    colunas["odd_casa"] = encontrar_coluna(df, ["odds_casa_para_vencer", "odds"])
    colunas["odd_visitante"] = encontrar_coluna(df, ["odds_visitante_para_vencer", "odds_2"])
    colunas["odd_over25"] = encontrar_coluna(df, ["odds_mais_de_2_5", "odds_3"])
    colunas["odd_btts_sim"] = encontrar_coluna(df, ["odds_ambas_equipes_marcarem_sim", "odds_5"])

    colunas["prec_casa"] = encontrar_coluna(df, ["precisao_nos_chutes_no_alvo_casa"])
    colunas["prec_visit"] = encontrar_coluna(df, ["precisao_nos_chutes_no_alvo_visitante"])
    colunas["chg_casa"] = encontrar_coluna(df, ["chutes_por_gol_casa"])
    colunas["chg_visit"] = encontrar_coluna(df, ["chutes_por_gol_visitante"])

    colunas["mpg_casa"] = encontrar_coluna(df, ["quando_marcam_o_primeiro_gol_e_ganha_o_jogo_casa"])
    colunas["mpg_visit"] = encontrar_coluna(df, ["quando_marcam_o_primeiro_gol_e_ganha_o_jogo_visitante"])
    colunas["spg_casa"] = encontrar_coluna(df, ["quando_sofre_o_primeiro_gol_e_ganha_o_jogo_casa"])
    colunas["spg_visit"] = encontrar_coluna(df, ["quando_sofre_o_primeiro_gol_e_ganha_o_jogo_visitante"])

    colunas["gm1t_casa"] = encontrar_coluna(df, ["media_de_gols_marcados_primeiro_tempo_casa"])
    colunas["gm1t_visit"] = encontrar_coluna(df, ["media_de_gols_marcados_primeiro_tempo_visitante"])
    colunas["gs1t_casa"] = encontrar_coluna(df, ["media_de_gols_sofridos_primeiro_tempo_casa"])
    colunas["gs1t_visit"] = encontrar_coluna(df, ["media_de_gols_sofridos_primeiro_tempo_visitante"])
    colunas["cgm1t_casa"] = encontrar_coluna(df, ["media_de_chutes_no_gol_marcados_1_tempo_casa"])
    colunas["cgm1t_visit"] = encontrar_coluna(df, ["media_de_chutes_no_gol_marcados_1_tempo_visitante"])
    colunas["cts1t_casa"] = encontrar_coluna(df, ["media_total_de_chutes_sofridos_1_tempo_casa"])
    colunas["cts1t_visit"] = encontrar_coluna(df, ["media_total_de_chutes_sofridos_1_tempo_visitante"])

    colunas["h2h_vit_casa"] = encontrar_coluna(df, ["confrontos_diretos_vitorias_casa"])
    colunas["h2h_vit_visit"] = encontrar_coluna(df, ["confrontos_diretos_vitorias_visitante"])
    colunas["vit_casa"] = encontrar_coluna(df, ["vitorias_casa"])
    colunas["vit_visit"] = encontrar_coluna(df, ["vitorias_visitante"])

    colunas["g_0_15"] = encontrar_coluna(df, ["media_de_gols_0_15_minutos"])
    colunas["g_16_30"] = encontrar_coluna(df, ["media_de_gols_16_30_minutos"])
    colunas["g_31_45"] = encontrar_coluna(df, ["media_de_gols_31_45_minutos"])
    colunas["g_46_60"] = encontrar_coluna(df, ["media_de_gols_46_60_minutos"])
    colunas["g_61_75"] = encontrar_coluna(df, ["media_de_gols_61_75_minutos"])
    colunas["g_76_90"] = encontrar_coluna(df, ["media_de_gols_76_90_minutos"])

    colunas["o05_0_15"] = encontrar_coluna(df, ["mais_de_0_5_gols_0_15"])
    colunas["o05_16_30"] = encontrar_coluna(df, ["mais_de_0_5_gols_16_30"])
    colunas["o05_31_45"] = encontrar_coluna(df, ["mais_de_0_5_gols_31_45"])
    colunas["o05_46_60"] = encontrar_coluna(df, ["mais_de_0_5_gols_46_60"])
    colunas["o05_61_75"] = encontrar_coluna(df, ["mais_de_0_5_gols_61_75"])
    colunas["o05_76_90"] = encontrar_coluna(df, ["mais_de_0_5_gols_76_90"])

    colunas["score_casa_pronto"] = encontrar_coluna(df, ["score_casa"])
    colunas["score_visit_pronto"] = encontrar_coluna(df, ["score_visitante"])
    colunas["score_gols_pronto"] = encontrar_coluna(df, ["score_gols"])
    return colunas


def to_num(s):
    return pd.to_numeric(s, errors="coerce")


def fmt(v, casas=1):
    try:
        if pd.isna(v):
            return "—"
        n = float(v)
        if n.is_integer():
            return str(int(n))
        return f"{n:.{casas}f}"
    except Exception:
        return "—"


def fmt_placar_valor(v):
    if pd.isna(v) or str(v).strip() == "":
        return ""
    try:
        n = float(v)
        if n.is_integer():
            return str(int(n))
        return str(n).replace(".0", "")
    except Exception:
        return str(v).strip()


def extrair_hora_jogo(valor):
    if pd.isna(valor):
        return pd.NaT
    texto = str(valor).strip()
    match = re.search(r"(\d{1,2}:\d{2})", texto)
    if match:
        return pd.to_datetime(match.group(1), format="%H:%M", errors="coerce")
    tentativa = pd.to_datetime(texto, errors="coerce", dayfirst=True)
    if pd.notna(tentativa):
        return tentativa
    return pd.NaT


def exibir_hora_curta(valor):
    if pd.isna(valor):
        return "—"
    texto = str(valor).strip()
    match = re.search(r"(\d{1,2}:\d{2})", texto)
    if match:
        return match.group(1)
    dt = pd.to_datetime(texto, errors="coerce", dayfirst=True)
    if pd.notna(dt):
        return dt.strftime("%H:%M")
    return texto


def valor_col(row, col):
    if not col:
        return np.nan
    return row[col] if col in row.index else np.nan


def montar_id_partida(row, cols):
    partes = []
    for k in ["status", "country", "league", "hour", "home", "away"]:
        c = cols.get(k)
        if c:
            partes.append(str(row[c]))
    return " | ".join(partes)


def normalizar_serie_0_100(s):
    s = to_num(s)
    minimo = s.min(skipna=True)
    maximo = s.max(skipna=True)
    if pd.isna(minimo) or pd.isna(maximo) or minimo == maximo:
        return pd.Series(np.nan, index=s.index)
    return ((s - minimo) / (maximo - minimo) * 100).clip(0, 100)


def prob_implicita(odd):
    odd = to_num(odd)
    return np.where((odd > 0) & np.isfinite(odd), 1 / odd, np.nan)


def safe_inv(s):
    s = to_num(s)
    return np.where((s > 0) & np.isfinite(s), 1 / s, np.nan)


def soma_existente(df, colunas):
    series = []
    for c in colunas:
        if c is not None and c in df.columns:
            series.append(to_num(df[c]))
    if not series:
        return pd.Series(np.nan, index=df.index)
    return pd.concat(series, axis=1).sum(axis=1, min_count=1)


def criar_scores_painel(df, cols):
    df = df.copy()

    if cols["score_casa_pronto"] and cols["score_casa_pronto"] in df.columns:
        df["__score_casa__"] = to_num(df[cols["score_casa_pronto"]])
    else:
        prob_casa = pd.Series(prob_implicita(df[cols["odd_casa"]]), index=df.index) if cols["odd_casa"] else pd.Series(np.nan, index=df.index)
        eff_casa = pd.Series(safe_inv(df[cols["chg_casa"]]), index=df.index) if cols["chg_casa"] else pd.Series(np.nan, index=df.index)
        ofensivo_casa = soma_existente(df, [cols["prec_casa"], cols["gm1t_casa"], cols["cgm1t_casa"], cols["mpg_casa"], cols["vit_casa"], cols["h2h_vit_casa"]])
        frag_rival_visit = soma_existente(df, [cols["gs1t_visit"], cols["cts1t_visit"]])
        resiliencia_casa = soma_existente(df, [cols["spg_casa"]])
        bruto_casa = pd.concat([prob_casa, ofensivo_casa, eff_casa, frag_rival_visit, resiliencia_casa], axis=1).mean(axis=1)
        df["__score_casa__"] = normalizar_serie_0_100(bruto_casa)

    if cols["score_visit_pronto"] and cols["score_visit_pronto"] in df.columns:
        df["__score_visitante__"] = to_num(df[cols["score_visit_pronto"]])
    else:
        prob_visit = pd.Series(prob_implicita(df[cols["odd_visitante"]]), index=df.index) if cols["odd_visitante"] else pd.Series(np.nan, index=df.index)
        eff_visit = pd.Series(safe_inv(df[cols["chg_visit"]]), index=df.index) if cols["chg_visit"] else pd.Series(np.nan, index=df.index)
        ofensivo_visit = soma_existente(df, [cols["prec_visit"], cols["gm1t_visit"], cols["cgm1t_visit"], cols["mpg_visit"], cols["vit_visit"], cols["h2h_vit_visit"]])
        frag_rival_casa = soma_existente(df, [cols["gs1t_casa"], cols["cts1t_casa"]])
        resiliencia_visit = soma_existente(df, [cols["spg_visit"]])
        bruto_visit = pd.concat([prob_visit, ofensivo_visit, eff_visit, frag_rival_casa, resiliencia_visit], axis=1).mean(axis=1)
        df["__score_visitante__"] = normalizar_serie_0_100(bruto_visit)

    if cols["score_gols_pronto"] and cols["score_gols_pronto"] in df.columns:
        df["__score_gols__"] = to_num(df[cols["score_gols_pronto"]])
    else:
        prob_over = pd.Series(prob_implicita(df[cols["odd_over25"]]), index=df.index) if cols["odd_over25"] else pd.Series(np.nan, index=df.index)
        prob_btts = pd.Series(prob_implicita(df[cols["odd_btts_sim"]]), index=df.index) if cols["odd_btts_sim"] else pd.Series(np.nan, index=df.index)
        janelas_gols = soma_existente(df, [cols["g_0_15"], cols["g_16_30"], cols["g_31_45"], cols["g_46_60"], cols["g_61_75"], cols["g_76_90"]])
        janelas_o05 = soma_existente(df, [cols["o05_0_15"], cols["o05_16_30"], cols["o05_31_45"], cols["o05_46_60"], cols["o05_61_75"], cols["o05_76_90"]])
        ofensivo_total = soma_existente(df, [cols["gm1t_casa"], cols["gm1t_visit"], cols["cgm1t_casa"], cols["cgm1t_visit"]])
        frag_total = soma_existente(df, [cols["gs1t_casa"], cols["gs1t_visit"], cols["cts1t_casa"], cols["cts1t_visit"]])
        bruto_gols = pd.concat([prob_over, prob_btts, janelas_gols, janelas_o05, ofensivo_total, frag_total], axis=1).mean(axis=1)
        df["__score_gols__"] = normalizar_serie_0_100(bruto_gols)

    return df


def render_card(status_txt, country, hour, home, away, league, fc, fv, ge, placar=None):
    with st.container():
        st.markdown('<div class="card-box">', unsafe_allow_html=True)

        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            estilo = "badge-ns" if status_txt == "NS" else "badge-ft"
            st.markdown(f'<span class="badge {estilo}">{status_txt}</span>', unsafe_allow_html=True)
        with c2:
            st.markdown(f'<div style="text-align:center;"><span class="badge badge-soft">{country if pd.notna(country) else "—"}</span></div>', unsafe_allow_html=True)
        with c3:
            st.markdown(f'<div style="text-align:right;"><span class="badge badge-soft">{hour}</span></div>', unsafe_allow_html=True)

        st.markdown(f"**{home if pd.notna(home) else 'Casa'}**")
        st.markdown("**vs**")
        st.markdown(f"**{away if pd.notna(away) else 'Visitante'}**")

        if placar is not None:
            st.markdown(f'<div class="placar-ft">{placar}</div>', unsafe_allow_html=True)

        a1, a2 = st.columns(2)
        with a1:
            st.markdown('<div class="small-label">Liga</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="big-value">{league if pd.notna(league) else "—"}</div>', unsafe_allow_html=True)
        with a2:
            st.markdown('<div class="small-label">Força Casa</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="big-value">{fc}</div>', unsafe_allow_html=True)

        b1, b2 = st.columns(2)
        with b1:
            st.markdown('<div class="small-label">Força Visitante</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="big-value">{fv}</div>', unsafe_allow_html=True)
        with b2:
            st.markdown('<div class="small-label">Gols / Potencial</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="big-value">{ge}</div>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)


def mostrar_detalhes_confronto(row, cols, status_card):
    home = valor_col(row, cols["home"])
    away = valor_col(row, cols["away"])
    country = valor_col(row, cols["country"])
    league = valor_col(row, cols["league"])
    hour = row["__hora_exibicao__"]
    fc = fmt(row["__score_casa__"])
    fv = fmt(row["__score_visitante__"])
    ge = fmt(row["__score_gols__"])
    placar = row["__placar__"]

    st.markdown('<div class="box-detalhe">', unsafe_allow_html=True)
    st.markdown(f"**{home if pd.notna(home) else 'Casa'} x {away if pd.notna(away) else 'Visitante'}**")
    c1, c2 = st.columns(2)
    c1.write(f"**País:** {country if pd.notna(country) else '—'}")
    c2.write(f"**Hora:** {hour}")
    c3, c4 = st.columns(2)
    c3.write(f"**Liga:** {league if pd.notna(league) else '—'}")
    c4.write(f"**Status:** {status_card}")

    if status_card == "FT":
        st.write(f"**Placar:** {placar}")

    c5, c6 = st.columns(2)
    c5.write(f"**Força Casa:** {fc}")
    c6.write(f"**Força Visitante:** {fv}")
    st.write(f"**Gols / Potencial:** {ge}")
    st.markdown('</div>', unsafe_allow_html=True)


def alternar_confronto(chave):
    st.session_state[chave] = not st.session_state.get(chave, False)


df = carregar_base()
cols = obter_colunas(df)

if cols["status"] is None:
    st.error("A planilha não tem uma coluna de Status identificável.")
    st.stop()

df[cols["status"]] = df[cols["status"]].astype(str).str.upper().str.strip()

if cols["gols_home"] and cols["gols_away"]:
    df["__placar__"] = df[cols["gols_home"]].apply(fmt_placar_valor) + "x" + df[cols["gols_away"]].apply(fmt_placar_valor)
    df["__placar__"] = df["__placar__"].replace("x", "—")
else:
    df["__placar__"] = "—"

if cols["hour"]:
    df["__hora_ordem__"] = df[cols["hour"]].apply(extrair_hora_jogo)
    df["__hora_exibicao__"] = df[cols["hour"]].apply(exibir_hora_curta)
else:
    df["__hora_ordem__"] = pd.NaT
    df["__hora_exibicao__"] = "—"

df = criar_scores_painel(df, cols)

st.markdown('<div class="main-title">⚽ Painel GGolEmNumeros</div>', unsafe_allow_html=True)
st.markdown(
    f'<div class="sub-title">Base online carregada | Registros: <b>{len(df)}</b></div>',
    unsafe_allow_html=True
)

pais_opcoes = sorted(df[cols["country"]].dropna().astype(str).unique().tolist()) if cols["country"] else []
liga_base = sorted(df[cols["league"]].dropna().astype(str).unique().tolist()) if cols["league"] else []
liga_opcoes = ["Todas"] + liga_base

aba1, aba2 = st.tabs(["Jogos do Dia", "Finalizados"])

with aba1:
    st.markdown('<div class="section-title">Jogos NS</div>', unsafe_allow_html=True)

    paises_ns_selecionados = st.multiselect(
        "Filtrar países (NS)",
        options=pais_opcoes,
        default=[],
        key="paises_ns_selecionados",
        placeholder="Selecione um ou mais países"
    )

    ligas_ns_selecionadas = st.multiselect(
    "Filtrar ligas (NS)",
    options=liga_opcoes,
    default=["Todas"],
    key="ligas_ns_selecionadas",
    placeholder="Selecione uma ou mais ligas"
)

    busca_ns = st.text_input("Buscar equipe (NS)", key="busca_ns").strip().lower()

    filtro_ativo_ns = bool(paises_ns_selecionados or ligas_ns_selecionadas or busca_ns)

    if not filtro_ativo_ns:
        st.info("Selecione um país, uma liga ou digite uma equipe para mostrar os jogos NS.")
    else:
        df_ns = df[df[cols["status"]] == "NS"].copy()

        if paises_ns_selecionados and cols["country"]:
            df_ns = df_ns[df_ns[cols["country"]].astype(str).isin(paises_ns_selecionados)]

        if cols["league"] and ligas_ns_selecionadas and "Todas" not in ligas_ns_selecionadas:
    df_ns = df_ns[df_ns[cols["league"]].astype(str).isin(ligas_ns_selecionadas)]
        if busca_ns:
            mask_home = df_ns[cols["home"]].astype(str).str.lower().str.contains(busca_ns, na=False) if cols["home"] else False
            mask_away = df_ns[cols["away"]].astype(str).str.lower().str.contains(busca_ns, na=False) if cols["away"] else False
            df_ns = df_ns[mask_home | mask_away]

        df_ns = df_ns.sort_values(by="__hora_ordem__", ascending=True, na_position="last")

        st.metric("Qtd Jogos NS", len(df_ns))

        if df_ns.empty:
            st.warning("Nenhum jogo NS encontrado com esse filtro.")
        else:
            for i, (_, row) in enumerate(df_ns.iterrows()):
                home = valor_col(row, cols["home"])
                away = valor_col(row, cols["away"])
                country = valor_col(row, cols["country"])
                league = valor_col(row, cols["league"])
                hour = row["__hora_exibicao__"]
                fc = fmt(row["__score_casa__"])
                fv = fmt(row["__score_visitante__"])
                ge = fmt(row["__score_gols__"])

                game_id = f"ns_{i}_{montar_id_partida(row, cols)}"
                state_key = f"mostrar_{game_id}"

                if state_key not in st.session_state:
                    st.session_state[state_key] = False

                render_card("NS", country, hour, home, away, league, fc, fv, ge)

                texto_botao = "Ocultar confronto" if st.session_state[state_key] else "Ver confronto"
                st.button(
                    texto_botao,
                    key=f"btn_{game_id}",
                    use_container_width=True,
                    on_click=alternar_confronto,
                    args=(state_key,)
                )

                if st.session_state[state_key]:
                    mostrar_detalhes_confronto(row, cols, "NS")

with aba2:
    st.markdown('<div class="section-title">Jogos FT</div>', unsafe_allow_html=True)

    paises_ft_selecionados = st.multiselect(
        "Filtrar países (FT)",
        options=pais_opcoes,
        default=[],
        key="paises_ft_selecionados",
        placeholder="Selecione um ou mais países"
    )

    ligas_ft_selecionadas = st.multiselect(
        "Filtrar ligas (FT)",
        options=liga_opcoes,
        default=[],
        key="ligas_ft_selecionadas",
        placeholder="Selecione uma ou mais ligas"
    )

    busca_ft = st.text_input("Buscar equipe (FT)", key="busca_ft").strip().lower()

    filtro_ativo_ft = bool(paises_ft_selecionados or ligas_ft_selecionadas or busca_ft)

    if not filtro_ativo_ft:
        st.info("Selecione um país, uma liga ou digite uma equipe para mostrar os jogos FT.")
    else:
        df_ft = df[df[cols["status"]] == "FT"].copy()

        if paises_ft_selecionados and cols["country"]:
            df_ft = df_ft[df_ft[cols["country"]].astype(str).isin(paises_ft_selecionados)]

        if ligas_ft_selecionadas and cols["league"]:
            df_ft = df_ft[df_ft[cols["league"]].astype(str).isin(ligas_ft_selecionadas)]

        if busca_ft:
            mask_home = df_ft[cols["home"]].astype(str).str.lower().str.contains(busca_ft, na=False) if cols["home"] else False
            mask_away = df_ft[cols["away"]].astype(str).str.lower().str.contains(busca_ft, na=False) if cols["away"] else False
            df_ft = df_ft[mask_home | mask_away]

        df_ft = df_ft.sort_values(by="__hora_ordem__", ascending=True, na_position="last")

        st.metric("Qtd Jogos FT", len(df_ft))

        if df_ft.empty:
            st.warning("Nenhum jogo FT encontrado com esse filtro.")
        else:
            for i, (_, row) in enumerate(df_ft.iterrows()):
                home = valor_col(row, cols["home"])
                away = valor_col(row, cols["away"])
                country = valor_col(row, cols["country"])
                league = valor_col(row, cols["league"])
                hour = row["__hora_exibicao__"]
                placar = row["__placar__"]
                fc = fmt(row["__score_casa__"])
                fv = fmt(row["__score_visitante__"])
                ge = fmt(row["__score_gols__"])

                game_id = f"ft_{i}_{montar_id_partida(row, cols)}"
                state_key = f"mostrar_{game_id}"

                if state_key not in st.session_state:
                    st.session_state[state_key] = False

                render_card("FT", country, hour, home, away, league, fc, fv, ge, placar=placar)

                texto_botao = "Ocultar confronto" if st.session_state[state_key] else "Ver confronto"
                st.button(
                    texto_botao,
                    key=f"btn_{game_id}",
                    use_container_width=True,
                    on_click=alternar_confronto,
                    args=(state_key,)
                )

                if st.session_state[state_key]:
                    mostrar_detalhes_confronto(row, cols, "FT")
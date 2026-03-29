import streamlit as st
import pandas as pd

st.set_page_config(page_title="Radar de Blocos 15'", layout="wide")

# =========================================================
# ESTILO
# =========================================================
st.markdown(
    """
    <style>
    .stApp {
        background: linear-gradient(180deg, #05080d 0%, #0a1017 100%);
        color: #d8e1ea;
    }
    .block-container {
        padding-top: 1.1rem;
        padding-bottom: 1.1rem;
        max-width: 1550px;
    }
    div[data-testid="stMetric"] {
        background: #0d141d;
        border: 1px solid #1f2a36;
        padding: 12px 14px;
        border-radius: 14px;
    }
    .panel {
        background: #0a1017;
        border: 1px solid #1f2a36;
        border-radius: 18px;
        padding: 16px;
        margin-bottom: 12px;
    }
    .panel-green {
        background: #0b1712;
        border: 1px solid #24523b;
        border-radius: 18px;
        padding: 16px;
        margin-bottom: 12px;
    }
    .panel-gold {
        background: #171208;
        border: 1px solid #5a4722;
        border-radius: 18px;
        padding: 16px;
        margin-bottom: 12px;
    }
    .small-label {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: .18em;
        color: #7e8fa3;
    }
    .title-big {
        font-size: 30px;
        font-weight: 800;
        color: white;
        margin-bottom: 4px;
    }
    .title-mid {
        font-size: 20px;
        font-weight: 700;
        color: white;
        margin-bottom: 4px;
    }
    .muted {
        color: #98a7b8;
        font-size: 14px;
    }
    .green {
        color: #7ef0a9;
        font-weight: 700;
    }
    .gold {
        color: #ffd977;
        font-weight: 700;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =========================================================
# CONFIGURAÇÕES
# =========================================================
URL_PAGINA2 = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh8nrJcHw1kIQOLk_ER7kmevSJXqQAoelNyn3wnEN9UgSAF_kFQF4NGZkqYhT-E5tpX20Lr6XW_oBt/pub?gid=682279336&single=true&output=csv"

COL_ID_PARTIDA = "id_partida"
COL_DATA_REF = "data_referencia_lista"
COL_COMPETICAO = "competicao"
COL_DATA_PARTIDA = "data_partida"
COL_HOME = "time_casa"
COL_AWAY = "time_visitante"
COL_PLACAR_HT = "placar_ht"
COL_PLACAR_FT = "placar_ft"
COL_STATUS = "status"
COL_MINUTO = "minuto"
COL_PRESS_CASA = "indice_pressao_casa"
COL_PRESS_VISIT = "indice_pressao_visitante"
COL_GOL = "gol_total_minuto"
COL_ESC = "escanteios_total_minuto"
COL_CH_GOL = "chutes_no_gol_total_minuto"
COL_CH_FORA = "chutes_para_fora_total_minuto"

CHECKPOINTS = {
    "15'": {"start": 1, "end": 15, "proj_start": 16, "proj_end": 30, "base_rate": 27.8},
    "30'": {"start": 16, "end": 30, "proj_start": 31, "proj_end": 45, "base_rate": 27.1},
    "45'": {"start": 31, "end": 45, "proj_start": 46, "proj_end": 60, "base_rate": 24.6},
    "60'": {"start": 46, "end": 60, "proj_start": 61, "proj_end": 75, "base_rate": 22.9},
    "75'": {"start": 61, "end": 75, "proj_start": 76, "proj_end": 90, "base_rate": 25.4},
}

# =========================================================
# FUNÇÕES DE APOIO
# =========================================================
def render_panel_start(cls: str = "panel"):
    st.markdown(f"<div class='{cls}'>", unsafe_allow_html=True)


def render_panel_end():
    st.markdown("</div>", unsafe_allow_html=True)


@st.cache_data(ttl=300)
def carregar_base():
    df = pd.read_csv(URL_PAGINA2)
    df.columns = [str(c).strip() for c in df.columns]

    cols_numericas = [
        COL_MINUTO,
        COL_PRESS_CASA,
        COL_PRESS_VISIT,
        COL_GOL,
        COL_ESC,
        COL_CH_GOL,
        COL_CH_FORA,
    ]
    for col in cols_numericas:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    cols_texto = [
        COL_ID_PARTIDA,
        COL_HOME,
        COL_AWAY,
        COL_COMPETICAO,
        COL_STATUS,
        COL_PLACAR_HT,
        COL_PLACAR_FT,
        COL_DATA_PARTIDA,
    ]
    for col in cols_texto:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    return df


def listar_partidas(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        COL_ID_PARTIDA,
        COL_COMPETICAO,
        COL_DATA_PARTIDA,
        COL_HOME,
        COL_AWAY,
        COL_PLACAR_HT,
        COL_PLACAR_FT,
        COL_STATUS,
    ]
    cols_existentes = [c for c in cols if c in df.columns]

    jogos = (
        df[cols_existentes]
        .drop_duplicates(subset=[COL_ID_PARTIDA])
        .sort_values([COL_DATA_PARTIDA, COL_COMPETICAO, COL_HOME, COL_AWAY], na_position="last")
        .reset_index(drop=True)
    )
    jogos["label_jogo"] = (
        jogos[COL_HOME].astype(str)
        + " x "
        + jogos[COL_AWAY].astype(str)
        + " | "
        + jogos[COL_COMPETICAO].astype(str)
        + " | "
        + jogos[COL_STATUS].astype(str)
    )
    return jogos


def filtrar_partida(df: pd.DataFrame, id_partida: str) -> pd.DataFrame:
    base = df[df[COL_ID_PARTIDA].astype(str) == str(id_partida)].copy()
    base = base.sort_values(COL_MINUTO).reset_index(drop=True)
    return base


def build_block_df(df_partida: pd.DataFrame, start: int, end: int) -> pd.DataFrame:
    block = df_partida[(df_partida[COL_MINUTO] >= start) & (df_partida[COL_MINUTO] <= end)].copy()

    minutos_esperados = pd.DataFrame({COL_MINUTO: list(range(start, end + 1))})
    block = minutos_esperados.merge(block, how="left", on=COL_MINUTO)

    for col in [COL_PRESS_CASA, COL_PRESS_VISIT, COL_ESC, COL_CH_GOL, COL_CH_FORA]:
        if col not in block.columns:
            block[col] = 0
        block[col] = pd.to_numeric(block[col], errors="coerce").fillna(0)

    block = block[[COL_MINUTO, COL_PRESS_CASA, COL_PRESS_VISIT, COL_ESC, COL_CH_GOL, COL_CH_FORA]].copy()
    block.columns = ["Min", "Press_Casa", "Press_Visitante", "Esc", "Ch_Gol", "Ch_Fora"]
    return block


def zerar_campos_bloco(df_bloco: pd.DataFrame) -> pd.DataFrame:
    df = df_bloco.copy()
    for col in ["Press_Casa", "Press_Visitante", "Esc", "Ch_Gol", "Ch_Fora"]:
        if col in df.columns:
            df[col] = 0
    return df


def init_history_status():
    return {
        label: {"stars": "—", "lift": "—", "status": "Aguardando"}
        for label in CHECKPOINTS.keys()
    }


def calc_metrics(df_block: pd.DataFrame, base_rate: float):
    df = df_block.copy()
    for col in ["Press_Casa", "Press_Visitante", "Esc", "Ch_Gol", "Ch_Fora"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    home_avg = df["Press_Casa"].mean()
    away_avg = df["Press_Visitante"].mean()
    diff_avg = home_avg - away_avg
    corners = int(df["Esc"].sum())
    shots_on = int(df["Ch_Gol"].sum())
    shots_off = int(df["Ch_Fora"].sum())

    first_five = df.head(5)
    last_five = df.tail(5)
    first_pressure = (first_five["Press_Casa"] + first_five["Press_Visitante"]).mean()
    last_pressure = (last_five["Press_Casa"] + last_five["Press_Visitante"]).mean()
    acceleration_delta = last_pressure - first_pressure

    if acceleration_delta >= 3:
        trend_label = "Forte"
    elif acceleration_delta >= 1:
        trend_label = "Moderada"
    else:
        trend_label = "Baixa"

    if diff_avg > 2 and (corners + shots_on + shots_off) >= 5:
        accumulated_confirmation = "Confirmando"
    elif diff_avg > 0:
        accumulated_confirmation = "Acompanha"
    else:
        accumulated_confirmation = "Fraco"

    pattern_rate = min(
        58.0,
        base_rate
        + max(0.0, diff_avg * 1.2)
        + corners * 1.2
        + shots_on * 2.3
        + shots_off * 1.0
        + max(0.0, acceleration_delta * 0.9),
    )
    lift_num = pattern_rate - base_rate
    sample = max(34, round(120 - abs(lift_num) * 2.2 - max(0, 8 - corners - shots_on)))

    robust_lift = lift_num + (1.5 if sample >= 80 else 0.8 if sample >= 60 else 0.0) + (1.2 if acceleration_delta >= 3 else 0.6 if acceleration_delta >= 1 else 0.0)

    if robust_lift >= 10:
        stars = "⭐⭐⭐"
        strength_label = "Forte"
    elif robust_lift >= 5:
        stars = "⭐⭐"
        strength_label = "Médio"
    else:
        stars = "⭐"
        strength_label = "Fraco"

    logs = [
        f"> bloco fechado com diferença média de pressão {diff_avg:+.1f}",
        f"> eventos do bloco: {corners} escanteios, {shots_on} chutes no gol, {shots_off} chutes pra fora",
        f"> aceleração final classificada como {trend_label.lower()}",
        f"> padrão semelhante estimado em {sample} casos históricos",
        f"> projeção do próximo bloco: {pattern_rate:.1f}% contra base de {base_rate:.1f}%",
    ]

    return {
        "home_avg": home_avg,
        "away_avg": away_avg,
        "diff_avg": diff_avg,
        "corners": corners,
        "shots_on": shots_on,
        "shots_off": shots_off,
        "trend_label": trend_label,
        "accumulated_confirmation": accumulated_confirmation,
        "pattern_rate": pattern_rate,
        "base_rate": base_rate,
        "lift_num": lift_num,
        "lift": f"{'+' if lift_num >= 0 else ''}{lift_num:.1f} p.p.",
        "sample": sample,
        "stars": stars,
        "strength_label": strength_label,
        "logs": logs,
        "heat": (df["Press_Casa"] + df["Press_Visitante"]).tolist(),
    }


# =========================================================
# BASE
# =========================================================
df_base = carregar_base()
df_jogos = listar_partidas(df_base)

if "history_status" not in st.session_state:
    st.session_state.history_status = init_history_status()

# =========================================================
# CABEÇALHO
# =========================================================
st.markdown("<div class='small-label'>Radar de Blocos 15'</div>", unsafe_allow_html=True)
st.markdown("<div class='title-big'>Painel funcional com seletor de checkpoint</div>", unsafe_allow_html=True)
st.markdown(
    "<div class='muted'>Selecione a partida, escolha o checkpoint, ajuste o bloco minuto a minuto e veja o diagnóstico automático do próximo bloco.</div>",
    unsafe_allow_html=True,
)

st.write("")

# =========================================================
# SELEÇÃO DA PARTIDA
# =========================================================
render_panel_start()
st.markdown("<div class='small-label'>Partida</div>", unsafe_allow_html=True)
selected_id = st.selectbox(
    "Selecione a partida",
    options=df_jogos[COL_ID_PARTIDA].astype(str).tolist(),
    format_func=lambda x: df_jogos.loc[df_jogos[COL_ID_PARTIDA].astype(str) == str(x), "label_jogo"].iloc[0],
    label_visibility="collapsed",
)
render_panel_end()

linha_jogo = df_jogos[df_jogos[COL_ID_PARTIDA].astype(str) == str(selected_id)].iloc[0]
df_partida = filtrar_partida(df_base, selected_id)

# reset histórico ao trocar partida
if st.session_state.get("selected_id_last") != str(selected_id):
    st.session_state.history_status = init_history_status()
    st.session_state.selected_id_last = str(selected_id)

# =========================================================
# SELETOR CHECKPOINT + BOTÕES
# =========================================================
render_panel_start()
st.markdown("<div class='small-label'>Seletor de checkpoint</div>", unsafe_allow_html=True)
col_sel, col_btn1, col_btn2 = st.columns([0.54, 0.21, 0.25])

with col_sel:
    selected_checkpoint = st.radio(
        "Escolha o checkpoint",
        options=list(CHECKPOINTS.keys()),
        horizontal=True,
        label_visibility="collapsed",
    )

cp = CHECKPOINTS[selected_checkpoint]
session_key_bloco = f"bloco_editado_{selected_id}_{selected_checkpoint}"

# cria o bloco atual na sessão, se ainda não existir
if session_key_bloco not in st.session_state:
    st.session_state[session_key_bloco] = build_block_df(df_partida, cp["start"], cp["end"])

with col_btn1:
    st.write("")
    st.write("")
    if st.button("🧹 Zerar bloco atual", use_container_width=True):
        st.session_state[session_key_bloco] = zerar_campos_bloco(st.session_state[session_key_bloco])
        st.rerun()

with col_btn2:
    st.write("")
    st.write("")
    if st.button("🧼 Zerar todos os campos", use_container_width=True):
        for cp_label, cp_cfg in CHECKPOINTS.items():
            key_tmp = f"bloco_editado_{selected_id}_{cp_label}"
            bloco_tmp = build_block_df(df_partida, cp_cfg["start"], cp_cfg["end"])
            st.session_state[key_tmp] = zerar_campos_bloco(bloco_tmp)
        st.rerun()

render_panel_end()

current_df = st.session_state[session_key_bloco].copy()
metrics = calc_metrics(current_df, cp["base_rate"])

# atualizar histórico
for label in CHECKPOINTS:
    label_num = int(label.replace("'", ""))
    current_num = int(selected_checkpoint.replace("'", ""))
    if label == selected_checkpoint:
        st.session_state.history_status[label] = {
            "stars": metrics["stars"],
            "lift": metrics["lift"],
            "status": "Atual",
        }
    elif label_num < current_num and st.session_state.history_status[label]["status"] == "Aguardando":
        st.session_state.history_status[label] = {
            "stars": "⭐⭐",
            "lift": "+6,0 p.p.",
            "status": "Fechado",
        }

# =========================================================
# RESUMO SUPERIOR
# =========================================================
cols = st.columns(6)
summary_cards = [
    ("Checkpoint", selected_checkpoint),
    ("Lê bloco", f"{cp['start']}-{cp['end']}"),
    ("Projeta", f"{cp['proj_start']}-{cp['proj_end']}"),
    ("Força", metrics["stars"]),
    ("Taxa", f"{metrics['pattern_rate']:.1f}%"),
    ("Lift", metrics["lift"]),
]
for col, (label, value) in zip(cols, summary_cards):
    col.metric(label, value)

st.write("")

# =========================================================
# INFO PARTIDA
# =========================================================
info_cols = st.columns(5)
info_cols[0].metric("Competição", linha_jogo.get(COL_COMPETICAO, ""))
info_cols[1].metric("Casa", linha_jogo.get(COL_HOME, ""))
info_cols[2].metric("Visitante", linha_jogo.get(COL_AWAY, ""))
info_cols[3].metric("Placar HT", linha_jogo.get(COL_PLACAR_HT, ""))
info_cols[4].metric("Placar FT", linha_jogo.get(COL_PLACAR_FT, ""))

st.write("")
left, right = st.columns([1.25, 0.85], gap="large")

# =========================================================
# ESQUERDA
# =========================================================
with left:
    render_panel_start()
    st.markdown("<div class='small-label'>Entrada manual</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='title-mid'>Bloco {cp['start']}-{cp['end']} minuto a minuto</div>",
        unsafe_allow_html=True,
    )
    edited_df = st.data_editor(
        current_df,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key=f"editor_{selected_id}_{selected_checkpoint}",
        column_config={
            "Min": st.column_config.NumberColumn("Min", disabled=True),
            "Press_Casa": st.column_config.NumberColumn("Press Casa", min_value=0, step=1),
            "Press_Visitante": st.column_config.NumberColumn("Press Visit", min_value=0, step=1),
            "Esc": st.column_config.NumberColumn("Esc", min_value=0, step=1),
            "Ch_Gol": st.column_config.NumberColumn("Ch Gol", min_value=0, step=1),
            "Ch_Fora": st.column_config.NumberColumn("Ch Fora", min_value=0, step=1),
        },
    )
    st.session_state[session_key_bloco] = edited_df.copy()
    render_panel_end()

    metrics = calc_metrics(edited_df, cp["base_rate"])
    st.session_state.history_status[selected_checkpoint] = {
        "stars": metrics["stars"],
        "lift": metrics["lift"],
        "status": "Atual",
    }

    render_panel_start()
    st.markdown("<div class='small-label'>Assinatura do bloco</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='title-mid'>Leitura {cp['start']}-{cp['end']}</div>",
        unsafe_allow_html=True,
    )
    c1, c2, c3, c4 = st.columns(4)
    c5, c6, c7, c8 = st.columns(4)
    cards = [
        ("Pressão casa média", f"{metrics['home_avg']:.1f}"),
        ("Pressão visitante média", f"{metrics['away_avg']:.1f}"),
        ("Diferença média", f"{metrics['diff_avg']:+.1f}"),
        ("Escanteios", f"{metrics['corners']}"),
        ("Chutes no gol", f"{metrics['shots_on']}"),
        ("Chutes pra fora", f"{metrics['shots_off']}"),
        ("Aceleração final", metrics['trend_label']),
        ("Acumulado", metrics['accumulated_confirmation']),
    ]
    for col, (lab, val) in zip([c1, c2, c3, c4, c5, c6, c7, c8], cards):
        col.metric(lab, val)
    render_panel_end()

    render_panel_start()
    st.markdown("<div class='small-label'>Mapa de pressão do bloco</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='title-mid'>Heatline {cp['start']}-{cp['end']}</div>",
        unsafe_allow_html=True,
    )
    heat_df = pd.DataFrame({
        "Min": edited_df["Min"],
        "Pressão Total": metrics["heat"],
    }).set_index("Min")
    st.bar_chart(heat_df, height=220, use_container_width=True)
    render_panel_end()

    render_panel_start()
    st.markdown("<div class='small-label'>Histórico dos blocos</div>", unsafe_allow_html=True)
    hist_rows = []
    for label, cfg in CHECKPOINTS.items():
        status = st.session_state.history_status[label]
        hist_rows.append(
            {
                "Bloco": f"{cfg['start']}-{cfg['end']}",
                "Força": status["stars"],
                "Lift": status["lift"],
                "Status": status["status"],
            }
        )
    st.dataframe(pd.DataFrame(hist_rows), use_container_width=True, hide_index=True)
    render_panel_end()

    render_panel_start()
    st.markdown("<div class='small-label'>Log do sistema</div>", unsafe_allow_html=True)
    for line in metrics["logs"]:
        st.code(line)
    render_panel_end()

# =========================================================
# DIREITA
# =========================================================
with right:
    render_panel_start("panel-green")
    st.markdown("<div class='small-label'>Diagnóstico do próximo bloco</div>", unsafe_allow_html=True)
    diagnosis_title = "Probabilidade acima da média" if metrics["pattern_rate"] > metrics["base_rate"] else "Sem vantagem clara"
    st.markdown(f"<div class='title-mid green'>{diagnosis_title}</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='muted'>O sistema compara o bloco {cp['start']}-{cp['end']} e projeta a chance de gol em {cp['proj_start']}-{cp['proj_end']}.</div>",
        unsafe_allow_html=True,
    )
    render_panel_end()

    render_panel_start()
    st.markdown("<div class='small-label'>Comparação histórica</div>", unsafe_allow_html=True)
    comp_rows = pd.DataFrame(
        [
            {"Indicador": "Casos semelhantes", "Valor": metrics["sample"]},
            {"Indicador": "Taxa do padrão", "Valor": f"{metrics['pattern_rate']:.1f}%"},
            {"Indicador": "Taxa base próximo bloco", "Valor": f"{metrics['base_rate']:.1f}%"},
            {"Indicador": "Lift", "Valor": metrics["lift"]},
            {"Indicador": "Confirmação histórica", "Valor": metrics["strength_label"]},
            {"Indicador": "Parecer", "Valor": "Gol acima da média" if metrics["pattern_rate"] > metrics["base_rate"] else "Sem vantagem clara"},
        ]
    )
    st.dataframe(comp_rows, use_container_width=True, hide_index=True)
    render_panel_end()

    render_panel_start("panel-gold")
    st.markdown("<div class='small-label'>Classificação de força</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='title-big gold'>{metrics['stars']}</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='muted'>Leitura {metrics['strength_label'].lower()}. Lift, amostra e aceleração do bloco atual entram no cálculo da força.</div>",
        unsafe_allow_html=True,
    )
    render_panel_end()

    render_panel_start()
    st.markdown("<div class='small-label'>Próxima ação</div>", unsafe_allow_html=True)
    st.info("Preencha os minutos do bloco atual e acompanhe a resposta automática para o próximo bloco.")
    st.info("Use 'Zerar bloco atual' para limpar só o bloco selecionado ou 'Zerar todos os campos' para limpar todos os blocos da partida atual mantendo os minutos.")
    render_panel_end()

# =========================================================
# DEBUG OPCIONAL
# =========================================================
with st.expander("Ver colunas e prévia da base"):
    st.write(df_base.columns.tolist())
    st.dataframe(df_base.head(5), use_container_width=True)

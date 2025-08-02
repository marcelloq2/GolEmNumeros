# ==============================================
# Painel de Análises - com Menu Lateral e Tema
# ==============================================
import streamlit as st
import pandas as pd

# =====================
# URLs
# =====================
CSV_URL_ANALISES = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRllqAy4Dq2kJRQ3RhyJrN_k8fYFm3T9cU5TksbAiR-PEMSxgaTCz6vJa7zVD-m1HLpK3IEVWyXJL2l/pub?gid=1361130010&single=true&output=csv"
CSV_URL_JOGOS_DIA = "https://docs.google.com/spreadsheets/d/1Zxx_oYXAchtvjjwik5wnD07NS3UzB2iSxX0lnAlAGc4/export?format=csv&gid=930191265"

# =====================
# Layout inicial
# =====================
st.set_page_config(page_title="Painel de Análises", layout="wide")

# =====================
# Menu lateral
# =====================
with st.sidebar:
    st.image("https://i.imgur.com/CiRZK3z.png", width=180)
    st.markdown("## Menu")
    menu = st.radio("", ["Painel de Análises", "Em Desenvolvimento"])
    st.markdown("---")
    tema_escolhido = st.radio("🎨 Tema Visual", ["🌙 Escuro", "🌞 Claro"], index=0)

# =====================
# Tema Visual
# =====================
if tema_escolhido == "🌞 Claro":
    st.markdown("""
        <style>
        body, .stApp { background-color: #ffffff !important; color: #000000 !important; }
        .css-1cpxqw2, .css-18e3th9 { background-color: #f0f2f6 !important; color: #000000 !important; }
        .st-bx, .st-cz { background-color: #ffffff !important; color: #000000 !important; }
        </style>
    """, unsafe_allow_html=True)

# =====================
# Carregar dados
# =====================
@st.cache_data
def carregar_dados():
    df_analise = pd.read_csv(CSV_URL_ANALISES)
    df_analise.columns = df_analise.columns.str.strip().str.replace(" ", "_")
    df_jogos = pd.read_csv(CSV_URL_JOGOS_DIA)
    df_jogos.columns = df_jogos.columns.str.strip().str.replace(" ", "_")
    return df_analise, df_jogos

df, df_jogos_dia = carregar_dados()

# =====================
# Conteúdo principal
# =====================
if menu == "Painel de Análises":
    st.title("📊 Painel de Análises")

    # ---------------------
    # Filtros
    # ---------------------
    with st.expander("🔎 Filtros", expanded=True):
        cols1 = st.columns(3)
        palpites = sorted(df["Palpite"].dropna().unique().tolist())
        palpite_opcoes = ["Todas Variáveis"] + palpites
        palpite_sel = cols1[0].multiselect("Palpite", options=palpite_opcoes, default=["Todas Variáveis"])
        if "Todas Variáveis" in palpite_sel:
            palpite_sel = palpites

        placares_provaveis = df["Placar_Provável"].dropna().unique().tolist()
        placar_sel = cols1[1].multiselect("Placar Provável", options=["Todas Variáveis"] + placares_provaveis, default=["Todas Variáveis"])
        if "Todas Variáveis" in placar_sel:
            placar_sel = placares_provaveis

        placares_improvaveis = df["Placar_Improvável"].dropna().unique().tolist()
        placar_improv_sel = cols1[2].multiselect("Placar Improvável", options=["Todas Variáveis"] + placares_improvaveis, default=["Todas Variáveis"])
        if "Todas Variáveis" in placar_improv_sel:
            placar_improv_sel = placares_improvaveis

        st.markdown("---")
        cols2 = st.columns(2)
        dif_min, dif_max = cols2[0].slider("V1 - Diferença % entre 1º e 2º maior", 0, 100, (10, 100))
        v2_sel = cols2[1].multiselect("V2 - Acertou Maior Probabilidade", [0, 1], default=[0, 1])

    # ---------------------
    # Aplicar filtros
    # ---------------------
    df_filtrado = df.query(
        "Palpite in @palpite_sel and "
        "Placar_Provável in @placar_sel and "
        "Placar_Improvável in @placar_improv_sel and "
        "V1_Diferença_entre_maior_e_segundo_maior_pct >= @dif_min and "
        "V1_Diferença_entre_maior_e_segundo_maior_pct <= @dif_max and "
        "V2_Acertou_Maior_Probabilidade in @v2_sel"
    )

    # ---------------------
    # Métricas
    # ---------------------
    st.subheader("📈 Taxas de Acerto (%)")
    metricas = [
        "Over_0.5FT", "Over_1.5FT", "Over_2.5FT", "Under_2.5FT", "Casa_Empate",
        "Visitante_Empate", "Casa", "Visitante", "Btts_Sim", "Btts_Não",
        "Under_1.5_FT", "Over_0.5_HT", "Ambas_Marcam_HT", "Over_1.5_HT"
    ]

    def cor_por_valor(valor):
        if valor >= 0.7: return "🟢"
        elif valor >= 0.4: return "🟡"
        else: return "🔴"

    for i in range(0, len(metricas), 3):
        cols = st.columns(3)
        for j in range(3):
            if i + j < len(metricas):
                metrica = metricas[i + j]
                if metrica in df_filtrado.columns:
                    col_data = pd.to_numeric(df_filtrado[metrica], errors='coerce')
                    media = col_data.mean()
                    qtd_total = col_data.count()
                    qtd_acertos = int(round(media * qtd_total)) if not pd.isna(media) else 0
                    if not pd.isna(media) and media > 0:
                        cor = cor_por_valor(media)
                        odd_justa = 1 / media
                        texto = (
                            f"**{metrica}**: {cor} <span style='font-size:18px'><strong>{media:.0%}</strong></span> "
                            f"({qtd_acertos}/{qtd_total} jogos) — 💡 Odd Justa: <strong>{odd_justa:.2f}</strong>"
                        )
                        cols[j].markdown(texto, unsafe_allow_html=True)

    # ---------------------
    # Jogos do dia
    # ---------------------
    st.subheader("📌 Jogos do Dia (com esse filtro)")
    df_jogos_filtro = df_jogos_dia.query(
        "Palpite in @palpite_sel and "
        "Placar_Provável in @placar_sel and "
        "Placar_Improvável in @placar_improv_sel"
    )
    if df_jogos_filtro.empty:
        st.info("Nenhum jogo do dia encontrado com esse filtro.")
    else:
        st.dataframe(df_jogos_filtro.reset_index(drop=True), use_container_width=True)

# =====================
# Página futura
# =====================
elif menu == "Em Desenvolvimento":
    st.title("🚧 Em Desenvolvimento")
    st.info("Essa página está sendo planejada para incluir novos recursos.")

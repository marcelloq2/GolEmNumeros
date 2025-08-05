# ==============================================
# Gol em Números - com Menu Lateral e Tema
# ==============================================
import streamlit as st
import pandas as pd
from PIL import Image
import os
import matplotlib.pyplot as plt
import seaborn as sns

# =====================
# URLs
# =====================
CSV_URL_ANALISES = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRllqAy4Dq2kJRQ3RhyJrN_k8fYFm3T9cU5TksbAiR-PEMSxgaTCz6vJa7zVD-m1HLpK3IEVWyXJL2l/pub?gid=1361130010&single=true&output=csv"
CSV_URL_JOGOS_DIA = "https://docs.google.com/spreadsheets/d/1Zxx_oYXAchtvjjwik5wnD07NS3UzB2iSxX0lnAlAGc4/export?format=csv&gid=930191265"

# =====================
# Layout inicial
# =====================
st.set_page_config(page_title="Gol em Números", layout="wide")

# =====================
# Mostrar Logo
# =====================
col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    try:
        logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
        if os.path.exists(logo_path):
            logo = Image.open(logo_path)
            st.image(logo, use_container_width=True)
        else:
            st.markdown("### ⚽ Gol em Números")
    except Exception as e:
        st.warning(f"Erro ao carregar o logo: {e}")
        st.markdown("### ⚽ Gol em Números")

# =====================
# Menu lateral
# =====================
with st.sidebar:
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
    st.title("📊 Gol em Números")

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

    # ---------------------
    # Aplicar filtros
    # ---------------------
    df_filtrado = df.query(
        "Palpite in @palpite_sel and "
        "Placar_Provável in @placar_sel and "
        "Placar_Improvável in @placar_improv_sel"
    )

    # ---------------------
    # Métricas
    # ---------------------
    st.subheader("📈 Taxas de Acerto (%)")
    metricas = [
        "Over_0.5FT", "Over_1.5FT", "Over_2.5FT", "Under_2.5FT", "Under_3.5FT",
        "Under_1.5_FT", "Casa_Empate", "Visitante_Empate", "Casa", "Visitante",
        "Empate", "Btts_Sim", "Btts_Não",  
        "Contra_0x0", "Contra_0x1", "Contra_0x2", "Contra_0x3",
        "Contra_1x0", "Contra_1x1", "Contra_1x2", "Contra_1x3",
        "Contra_2x0", "Contra_2x1", "Contra_2x2", "Contra_2x3",
        "Contra_3x0", "Contra_3x1", "Contra_3x2", "Contra_3x3",
        "Contra_Goleada_Casa", "Contra_Goleada_Visitante",
        "Qualquer_outra_vitória_em_casa", "Qualquer_outra_vitória_de_visitante"
    ]

    def cor_por_valor(valor):
        if valor >= 0.7: return "🟢"
        elif valor >= 0.4: return "🟡"
        else: return "🔴"

    back_odd_metricas = [
        "Over_0.5FT", "Over_1.5FT", "Over_2.5FT", "Under_2.5FT", "Under_3.5FT",
        "Under_1.5_FT", "Casa_Empate", "Visitante_Empate", "Casa", "Visitante",
        "Empate", "Btts_Sim", "Btts_Não"
    ]

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
                    if not pd.isna(media):
                        cor = cor_por_valor(media)
                        texto = f"**{metrica}**: {cor} <span style='font-size:18px'><strong>{media:.0%}</strong></span> ({qtd_acertos}/{qtd_total} jogos)"
                        if metrica.startswith("Contra_"):
                            try:
                                odd_max = round(1 / (1 - media), 2) if media < 1 else '∞'
                                texto += f" - Odd Máx Lay: {odd_max}"
                            except:
                                pass
                        elif metrica in back_odd_metricas:
                            try:
                                odd_back = round(1 / media, 2) if media > 0 else '∞'
                                texto += f" - Odd Justa Back: {odd_back}"
                            except:
                                pass
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

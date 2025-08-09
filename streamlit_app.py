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

# ---------------------
# Guia rápido: como este painel funciona
# ---------------------
with st.expander("🧠 Como este painel pensa (guia rápido)", expanded=False):
    st.markdown("""
**O que você está vendo aqui?**  
Este painel mostra **taxas de acerto** e **odds justas** para várias metodologias (Over/Under, Casa/Empate/Visitante, BTTS etc.), além da lista de **Jogos do Dia** filtrados pelo que você escolher.

---

### 🔎 Sobre os filtros
- **Palpite / Placar Provável / Placar Improvável:** o painel **filtra o DF** (`df`) antes de calcular qualquer métrica.
- Se você marcar **“Todas Variáveis”**, o painel considera **todas** as opções daquela dimensão.

---

### 📈 Como calculamos as “Taxas de Acerto”
- Para cada métrica (ex.: `Over_1.5FT`), pegamos a coluna correspondente no DF filtrado e:
  - Convertamos para número (`to_numeric`) e **ignoramos NaN**.
  - **Taxa de acerto** = **média** da coluna (ex.: 0.76 = 76%).
  - **Acertos e total** mostrados (ex.: `1852/2446`) vêm de:
    - `qtd_total` = quantidade de linhas **válidas** na coluna.
    - `qtd_acertos` = `round(média * qtd_total)`.

- **Cores**:
  - 🟢 ≥ 70%
  - 🟡 ≥ 40% e < 70%
  - 🔴 < 40%

---

### 🎯 Odds mostradas
- Para métricas de **back** (ex.: Over/Under, Casa/Visitante/Empate, BTTS):
  - **Odd Justa Back** = `1 / p`  (onde `p` é a taxa de acerto).
- Para métricas “**Contra_…**” (conceito de **Lay**):
  - **Odd Máx Lay** = `1 / (1 - p)` (se `p` = probabilidade do evento, `1-p` é a prob. de **NÃO** acontecer).
- Obs.: se `p` = 0 ⇒ odd tende a ∞ (mostrada como “∞”).

---

### 📌 Jogos do Dia
- A grade usa **o mesmo conjunto de filtros** (Palpite/Placares).
- Serve para você cruzar as taxas com os jogos que realmente acontecem hoje.

---

### ⚠️ Boas práticas de leitura
- **Amostra** importa: taxas com poucos jogos variam mais.
- Use as odds justas como **referência** para precificação; compare com o mercado.
- Combine **taxas** + **placar provável/improvável** + **contexto** do jogo.

> Dica: clique no título deste bloco para abri-lo/fechá-lo quando quiser.
""")


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


if menu == "Painel de Análises":
    # Tudo que pertence ao painel principal
    # (filtros, métricas, tabelas, gráficos, etc.)
    ...

elif menu == "Em Desenvolvimento":
    st.title("🚧 Em Desenvolvimento")
    st.info("Essa página está sendo planejada para incluir novos recursos.")

    with st.expander("📘 Como usar esta aba (guia rápido)", expanded=False):
        st.markdown("""
        ### ❓ O que esta área faz?
        Aqui você analisa o que realmente aconteceu nos jogos quando o modelo previu um
        **Placar_Provável** ou um **Placar_Improvável**.

        #### Como funciona:
        1. Escolha um placar na lista.
        2. O app filtra os jogos históricos com esse placar.
        3. Exibe médias de gols, taxa de acerto, matriz de confusão, etc.
        """)

    # Restante do código específico dessa aba
    ...



    # ---------------------
    # Médias de gols reais por Placar (Provável e Improvável)
    # ---------------------
    base = df.copy()  # usa a base completa (sem filtros)

    # garantir numéricos
    for c in ["Gols_Casa_Real_FT", "Gols_Visitante_Real_FT"]:
        if c in base.columns:
            base[c] = pd.to_numeric(base[c], errors="coerce")

    tab1, tab2 = st.tabs(["⚽ Placar Provável", "🧪 Placar Improvável"])

    def bloco_medias(campo_placar: str, widget_key_suffix: str):
        col_needed = {campo_placar, "Gols_Casa_Real_FT", "Gols_Visitante_Real_FT"}
        if not col_needed.issubset(base.columns):
            st.warning(
                f"Colunas necessárias não encontradas (esperado: {campo_placar}, "
                "Gols_Casa_Real_FT, Gols_Visitante_Real_FT)."
            )
            return None

        df_gols = base.dropna(
            subset=[campo_placar, "Gols_Casa_Real_FT", "Gols_Visitante_Real_FT"]
        ).copy()
        if df_gols.empty:
            st.info("Nenhum jogo com gols reais disponíveis para calcular as médias.")
            return None

        # lista de opções
        opcoes = sorted(df_gols[campo_placar].astype(str).unique().tolist())
        defaults = ["2x1", "1x0", "2x0", "1x1"]
        default = next((d for d in defaults if d in opcoes), opcoes[0])

        placar_focus = st.selectbox(
            f"Escolha um {campo_placar.replace('_', ' ').lower()} para detalhar",
            opcoes,
            index=opcoes.index(default),
            key=f"sel_{campo_placar}_{widget_key_suffix}",  # 👈 key única por aba
        )

        df_focus = df_gols[df_gols[campo_placar] == placar_focus]
        media_casa = df_focus["Gols_Casa_Real_FT"].mean()
        media_visit = df_focus["Gols_Visitante_Real_FT"].mean()
        media_total = media_casa + media_visit

        c1, c2, c3 = st.columns(3)
        c1.metric(f"Média gols CASA ({placar_focus})", f"{media_casa:.2f}")
        c2.metric(f"Média gols VISITANTE ({placar_focus})", f"{media_visit:.2f}")
        c3.metric("Média TOTAL", f"{media_total:.2f}")

        # gráfico
        fig, ax = plt.subplots(figsize=(4, 2.5))
        ax.bar(["Casa", "Visitante"], [media_casa, media_visit])
        ax.set_ylabel("Gols")
        ax.set_title(f"Médias de gols reais • {placar_focus}")
        st.pyplot(fig)

        # Tabela geral (todas as categorias desse campo)
        tabela = (
            df_gols.groupby(campo_placar, as_index=False)
            .agg(
                Jogos=(campo_placar, "size"),
                Média_Gols_Casa=("Gols_Casa_Real_FT", "mean"),
                Média_Gols_Visitante=("Gols_Visitante_Real_FT", "mean"),
            )
        )
        tabela["Média_Gols_Total"] = (
            tabela["Média_Gols_Casa"] + tabela["Média_Gols_Visitante"]
        )
        tabela = tabela.sort_values(
            ["Jogos", "Média_Gols_Total"], ascending=[False, False]
        )

        st.markdown("#### Visão geral")
        st.dataframe(
            tabela.style.format(
                {
                    "Média_Gols_Casa": "{:.2f}",
                    "Média_Gols_Visitante": "{:.2f}",
                    "Média_Gols_Total": "{:.2f}",
                }
            ),
            use_container_width=True,
        )

        return df_gols, df_focus, placar_focus

    # -------- Tab 1: Placar Provável
    with tab1:
        st.subheader("⚽ Médias de gols reais por **Placar_Provável**")
        res = bloco_medias("Placar_Provável", "prov")
        if res is not None:
            df_gols, df_focus, placar_focus = res

            # Taxa de acerto exato (placar provável == placar real)
            reais = (
                df_focus["Gols_Casa_Real_FT"].round(0).astype("Int64").astype(str)
                + "x"
                + df_focus["Gols_Visitante_Real_FT"].round(0).astype("Int64").astype(str)
            )
            acertos_exatos = (reais == placar_focus).sum()
            total = df_focus.shape[0]
            taxa = acertos_exatos / total if total else 0.0
            st.info(
                f"🎯 **Acerto exato do {placar_focus} como provável**: "
                f"**{taxa:.2%}** ({acertos_exatos}/{total})"
            )

            # Acerto por vencedor (Casa/Empate/Visitante)
            df_focus = df_focus.copy()
            df_focus["Vencedor_Real"] = df_focus.apply(
                lambda r: "Casa"
                if r["Gols_Casa_Real_FT"] > r["Gols_Visitante_Real_FT"]
                else (
                    "Visitante"
                    if r["Gols_Visitante_Real_FT"] > r["Gols_Casa_Real_FT"]
                    else "Empate"
                ),
                axis=1,
            )

            def vencedor_do_placar(txt):
                try:
                    a, b = str(txt).split("x")
                    a, b = int(a), int(b)
                    if a > b: return "Casa"
                    if b > a: return "Visitante"
                    return "Empate"
                except Exception:
                    return None

            df_focus["Vencedor_Esperado"] = df_focus["Placar_Provável"].apply(
                vencedor_do_placar
            )
            df_focus["Acertou_Vencedor"] = (
                df_focus["Vencedor_Real"] == df_focus["Vencedor_Esperado"]
            )

            acertos_vencedor = int(df_focus["Acertou_Vencedor"].sum())
            taxa_vencedor = acertos_vencedor / total if total else 0.0

            st.markdown("#### 📊 Acerto por vencedor (com base no Placar_Provável)")
            c1, c2, c3 = st.columns(3)
            c1.metric("Jogos avaliados", f"{total}")
            c2.metric("Acertos de vencedor", f"{acertos_vencedor}")
            c3.metric("Taxa de acerto (vencedor)", f"{taxa_vencedor:.2%}")

            por_classe = (
                df_focus.groupby("Vencedor_Esperado", dropna=False)
                .agg(Jogos=("Acertou_Vencedor", "size"), Acertos=("Acertou_Vencedor", "sum"))
                .reset_index()
                .rename(columns={"Vencedor_Esperado": "Esperado"})
            )
            por_classe["Taxa_Acerto"] = por_classe["Acertos"] / por_classe["Jogos"]
            por_classe = por_classe.sort_values("Esperado")

            st.dataframe(
                por_classe.style.format({"Taxa_Acerto": "{:.2%}"}),
                use_container_width=True,
            )

            conf = pd.crosstab(df_focus["Vencedor_Real"], df_focus["Vencedor_Esperado"])
            st.markdown("##### 🧭 Matriz de confusão (Vencedor Real × Vencedor Esperado)")
            st.dataframe(conf, use_container_width=True)

    # -------- Tab 2: Placar Improvável
    with tab2:
        st.subheader("🧪 Médias de gols reais por **Placar_Improvável**")
        res = bloco_medias("Placar_Improvável", "improv")
        if res is not None:
            df_gols, df_focus, placar_focus = res

            reais = (
                df_focus["Gols_Casa_Real_FT"].round(0).astype("Int64").astype(str)
                + "x"
                + df_focus["Gols_Visitante_Real_FT"].round(0).astype("Int64").astype(str)
            )
            acertos_exatos = (reais == placar_focus).sum()
            total = df_focus.shape[0]
            taxa = acertos_exatos / total if total else 0.0
            st.info(
                f"🎯 **Acerto exato do {placar_focus} como improvável**: "
                f"**{taxa:.2%}** ({acertos_exatos}/{total})"
            )

            df_focus = df_focus.copy()
            df_focus["Vencedor_Real"] = df_focus.apply(
                lambda r: "Casa"
                if r["Gols_Casa_Real_FT"] > r["Gols_Visitante_Real_FT"]
                else (
                    "Visitante"
                    if r["Gols_Visitante_Real_FT"] > r["Gols_Casa_Real_FT"]
                    else "Empate"
                ),
                axis=1,
            )

            def vencedor_do_placar(txt):
                try:
                    a, b = str(txt).split("x")
                    a, b = int(a), int(b)
                    if a > b: return "Casa"
                    if b > a: return "Visitante"
                    return "Empate"
                except Exception:
                    return None

            df_focus["Vencedor_Esperado"] = df_focus["Placar_Improvável"].apply(
                vencedor_do_placar
            )
            df_focus["Acertou_Vencedor"] = (
                df_focus["Vencedor_Real"] == df_focus["Vencedor_Esperado"]
            )

            acertos_vencedor = int(df_focus["Acertou_Vencedor"].sum())
            taxa_vencedor = acertos_vencedor / total if total else 0.0

            st.markdown("#### 📊 Acerto por vencedor (com base no Placar_Improvável)")
            c1, c2, c3 = st.columns(3)
            c1.metric("Jogos avaliados", f"{total}")
            c2.metric("Acertos de vencedor", f"{acertos_vencedor}")
            c3.metric("Taxa de acerto (vencedor)", f"{taxa_vencedor:.2%}")

            por_classe = (
                df_focus.groupby("Vencedor_Esperado", dropna=False)
                .agg(Jogos=("Acertou_Vencedor", "size"), Acertos=("Acertou_Vencedor", "sum"))
                .reset_index()
                .rename(columns={"Vencedor_Esperado": "Esperado"})
            )
            por_classe["Taxa_Acerto"] = por_classe["Acertos"] / por_classe["Jogos"]
            por_classe = por_classe.sort_values("Esperado")

            st.dataframe(
                por_classe.style.format({"Taxa_Acerto": "{:.2%}"}),
                use_container_width=True,
            )

            conf = pd.crosstab(df_focus["Vencedor_Real"], df_focus["Vencedor_Esperado"])
            st.markdown("##### 🧭 Matriz de confusão (Vencedor Real × Vencedor Esperado)")
            st.dataframe(conf, use_container_width=True)


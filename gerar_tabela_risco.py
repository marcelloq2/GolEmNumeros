"""Gera a tabela de RISCO DE GOL usada pelo semáforo ao vivo (2026-09-15).

Responde, a partir do histórico de partidas já salvo: "dado o estado de pressão
agora e o placar, qual a chance do favorito marcar e da zebra marcar nos
próximos N minutos?". O usuário opera LAY na pior equipe (zebra), então o número
que importa pra ele é a chance da ZEBRA marcar — é o gol contra a posição dele.

Roda LOCAL de propósito (varrer milhares de arquivos é caro em CPU/memória e não
precisa acontecer no Railway): gera `lay_risk_table.json`, que é pequeno e vai
versionado junto do código. O backend só lê esse arquivo.

Uso:
    python gerar_tabela_risco.py [caminho_do_momentum_history]
"""
import json
import glob
import os
import sys
from collections import defaultdict
from datetime import datetime

JANELA_FRENTE = 10   # minutos olhados à frente pra ver se saiu gol
JANELA_TRAS = 5      # minutos usados pra medir a pressão "agora"
MIN_MINUTO = 5
MAX_MINUTO = 80
MIN_AMOSTRA_CELULA = 200   # abaixo disso a célula (pressão+placar) não é confiável

# Faixas de pressão ORIENTADAS AO FAVORITO: positivo = favorito pressionando.
# graphPoints guarda valor assinado (>0 = casa, <0 = visitante), então é só
# multiplicar por -1 quando o favorito for o visitante.
FAIXAS = [
    ("zebra_forte",    -100, -40),
    ("zebra",           -40, -15),
    ("equilibrio",      -15,  15),
    ("favorito",         15,  40),
    ("favorito_forte",   40, 101),
]

ESTADOS = ("favorito_perdendo", "empatado", "favorito_ganhando")


def faixa_de(pressao):
    for nome, lo, hi in FAIXAS:
        if lo <= pressao < hi:
            return nome
    return "favorito_forte" if pressao >= 0 else "zebra_forte"


def favorito_de(d):
    """'home'/'away' pela odd de abertura (menor odd = favorito). None se o jogo
    era parelho demais pra ter favorito claro (aí não entra na amostra)."""
    oo = d.get("opening_odds") or {}
    try:
        h, a = float(oo.get("h")), float(oo.get("a"))
    except (TypeError, ValueError):
        return None
    if h <= 0 or a <= 0 or abs(h - a) < 0.15:
        return None
    return "home" if h < a else "away"


def gerar(base_dir):
    arquivos = glob.glob(os.path.join(base_dir, "*.json"))
    acc = defaultdict(lambda: {"n": 0, "fav": 0, "zebra": 0})       # (faixa, estado)
    acc_faixa = defaultdict(lambda: {"n": 0, "fav": 0, "zebra": 0})  # faixa (fallback)
    partidas = 0

    for fpath in arquivos:
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue

        fav = favorito_de(d)
        gp = d.get("graphPoints") or []
        if not fav or len(gp) < 30:
            continue
        partidas += 1

        zebra = "away" if fav == "home" else "home"
        sinal = 1 if fav == "home" else -1
        pressao_por_min = {}
        for p in gp:
            m, v = p.get("minute"), p.get("value")
            if m is not None and v is not None:
                pressao_por_min[int(m)] = float(v) * sinal

        gols = [(g.get("minute"), g.get("team")) for g in (d.get("goals") or [])
                if g.get("minute") is not None and g.get("team") in ("home", "away")]

        for m in range(MIN_MINUTO, MAX_MINUTO + 1):
            janela = [pressao_por_min[k] for k in range(m - JANELA_TRAS + 1, m + 1)
                      if k in pressao_por_min]
            if len(janela) < JANELA_TRAS:
                continue
            faixa = faixa_de(sum(janela) / len(janela))

            gols_fav = sum(1 for gm, gt in gols if gm <= m and gt == fav)
            gols_zeb = sum(1 for gm, gt in gols if gm <= m and gt == zebra)
            estado = ("favorito_ganhando" if gols_fav > gols_zeb else
                      "favorito_perdendo" if gols_fav < gols_zeb else "empatado")

            fav_marcou  = any(m < gm <= m + JANELA_FRENTE and gt == fav for gm, gt in gols)
            zeb_marcou  = any(m < gm <= m + JANELA_FRENTE and gt == zebra for gm, gt in gols)

            for alvo in (acc[(faixa, estado)], acc_faixa[faixa]):
                alvo["n"] += 1
                alvo["fav"] += 1 if fav_marcou else 0
                alvo["zebra"] += 1 if zeb_marcou else 0

    def pct(b):
        return {
            "n": b["n"],
            "fav_pct":   round(b["fav"] / b["n"] * 100, 2) if b["n"] else None,
            "zebra_pct": round(b["zebra"] / b["n"] * 100, 2) if b["n"] else None,
        }

    return {
        "gerado_em": datetime.now().isoformat(),
        "partidas": partidas,
        "janela_frente": JANELA_FRENTE,
        "janela_tras": JANELA_TRAS,
        "min_amostra_celula": MIN_AMOSTRA_CELULA,
        "faixas": [{"nome": n, "lo": lo, "hi": hi} for n, lo, hi in FAIXAS],
        "celulas": {f"{faixa}|{estado}": pct(acc[(faixa, estado)])
                    for faixa, _, _ in FAIXAS for estado in ESTADOS
                    if acc[(faixa, estado)]["n"]},
        "por_faixa": {faixa: pct(acc_faixa[faixa]) for faixa, _, _ in FAIXAS
                      if acc_faixa[faixa]["n"]},
    }


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "momentum_history")
    print(f"lendo: {base}")
    tabela = gerar(base)
    destino = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lay_risk_table.json")
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(tabela, f, ensure_ascii=False, indent=2)
    print(f"partidas usadas: {tabela['partidas']}")
    print(f"celulas: {len(tabela['celulas'])} | salvo em: {destino}")

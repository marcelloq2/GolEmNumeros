"""
Scraper do StatArea
- /predictions  → lista de partidas do dia com odds
- /compare/teams/... → detalhes do confronto (form, H2H, stats, standings, bet stats)
"""
import requests
from bs4 import BeautifulSoup
import json
import re
import time
import os
import glob
from datetime import datetime, timedelta

BASE_URL = "https://www.statarea.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = False  # certificado SSL do statarea.com pode estar expirado
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    session.get(BASE_URL, timeout=15)
    time.sleep(1)
    return session


# ─────────────────────────────────────────
# 1. LISTA DE PARTIDAS (/predictions)
# ─────────────────────────────────────────

def fetch_predictions(date: str = None, session: requests.Session = None) -> list[dict]:
    """
    Retorna lista de partidas com odds e link para detalhes.
    date: 'YYYY-MM-DD' ou None para hoje.
    """
    if session is None:
        session = create_session()
    if date:
        url = f"{BASE_URL}/predictions/date/{date}/competition"
    else:
        url = f"{BASE_URL}/predictions"
    r = session.get(url, timeout=15)
    r.raise_for_status()
    return _parse_predictions_page(r.text)


def _parse_predictions_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    matches = []

    for competition in soup.find_all("div", class_="competition"):
        league_name = _league_name(competition)
        country = _country(competition)

        body = competition.find("div", class_="body")
        if not body:
            continue

        cmatches = body.find_all("div", class_="cmatch")
        matchrows = body.find_all("div", class_="matchrow")
        inforows = body.find_all("div", class_="inforow")

        for i, cmatch in enumerate(cmatches):
            m = _parse_cmatch(cmatch)
            if not m:
                continue
            m["liga"] = league_name
            m["pais"] = country
            if i < len(inforows):
                m.update(_parse_inforow(inforows[i]))
            if i < len(matchrows):
                m.update(_parse_matchrow(matchrows[i]))
            matches.append(m)

    return matches


def _league_name(competition_div) -> str:
    header = competition_div.find("div", class_="header")
    if not header:
        return "N/D"
    link = header.find("a", class_="snandinglink")
    if link and link.get("href"):
        part = link["href"].split("/standings/competition/")[-1]
        return part.replace("+", " ").replace("%2F", "/").split("?")[0]
    return "N/D"


def _country(competition_div) -> str:
    header = competition_div.find("div", class_="header")
    if not header:
        return "N/D"
    img = header.find("img")
    return img.get("alt", "").replace(" country flag", "").strip() if img else "N/D"


def _parse_cmatch(cmatch) -> dict | None:
    time_div = cmatch.find("div", class_="time")
    if not time_div:
        return None
    teams_div = cmatch.find("div", class_="teams")
    if not teams_div:
        return None
    links = teams_div.find_all("a", href=True)
    if len(links) < 2:
        return None

    tip_div   = cmatch.find("div", class_="tip")
    tip_text  = tip_div.get_text(strip=True) if tip_div else "N/D"

    # Placar FT: div.result > a.goals  (ex: "2:1")
    resultado = None
    result_div = cmatch.find("div", class_="result")
    if result_div:
        goals_a = result_div.find("a", class_="goals")
        if goals_a:
            resultado = goals_a.get_text(strip=True)

    # TIP acertou: div.tip > div.value.success
    tip_acertou = None
    if result_div:  # só tem sentido se houve resultado
        val_div = tip_div.find("div", class_="value") if tip_div else None
        if val_div:
            tip_acertou = "success" in (val_div.get("class") or [])

    # StatArea retorna horário em UTC-4 (EDT, Railway us-east); BRT = UTC-4 + 1h
    hora_raw = time_div.get_text(strip=True)
    try:
        t = datetime.strptime(hora_raw, "%H:%M") + timedelta(hours=1)
        hora_ajustada = t.strftime("%H:%M")
    except Exception:
        hora_ajustada = hora_raw

    return {
        "hora": hora_ajustada,
        "resultado": resultado,
        "tip_acertou": tip_acertou,
        "casa": links[0].get_text(strip=True),
        "fora": links[1].get_text(strip=True),
        "tip": tip_text,
        "url_detalhes": links[0]["href"],
        "data_coleta": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _parse_inforow(inforow) -> dict:
    boxes = inforow.find_all("div", class_="coefbox")
    values = []
    for box in boxes:
        val = box.find("div", class_=lambda c: c and c.startswith("value"))
        if val:
            t = val.get_text(strip=True)
            if t.isdigit():
                values.append(int(t))
    keys = ["odds_1", "odds_x", "odds_2", "odds_h1", "odds_hx", "odds_h2",
            "over_1_5", "over_2_5", "over_3_5", "bts", "ots"]
    return {k: (values[i] if i < len(values) else None) for i, k in enumerate(keys)}


def _parse_matchrow(matchrow) -> dict:
    def get_like(cls):
        d = matchrow.find("div", class_=cls)
        if not d:
            return None
        v = d.find("div", class_="value")
        t = v.get_text(strip=True) if v else ""
        return int(t) if t.isdigit() else None

    result = {
        "votos_favor":  get_like("likepositive"),
        "votos_contra": get_like("likenegative"),
    }

    # Gols FT e placar HT a partir do matchrow > div.teams
    teams = matchrow.find("div", class_="teams")
    if teams:
        host  = teams.find("div", class_="hostteam")
        guest = teams.find("div", class_="guestteam")

        if host:
            g = host.find("a", class_="goals")
            if g:
                result["gols_casa"] = g.get_text(strip=True)

        if guest:
            g = guest.find("a", class_="goals")
            if g:
                result["gols_fora"] = g.get_text(strip=True)

            # HT: <div class="htres"><b>HT</b> 1:0</div>
            htres = guest.find("div", class_="htres")
            if htres:
                ht_txt = htres.get_text(strip=True)          # "HT 1:0"
                ht_score = re.sub(r'(?i)ht\s*', '', ht_txt).strip()  # "1:0"
                if re.match(r'^\d+:\d+$', ht_score):
                    result["ht_score"] = ht_score

    # TIP acertou no matchrow (redundante com cmatch, mas útil se chamado direto)
    tip_val = matchrow.find("div", class_="tip")
    if tip_val:
        val_div = tip_val.find("div", class_="value")
        if val_div:
            tip_ok = "success" in (val_div.get("class") or [])
            if tip_ok:
                result["tip_acertou"] = True

    return result


# ─────────────────────────────────────────
# 2. DETALHES DO CONFRONTO (/compare/teams/...)
# ─────────────────────────────────────────

def fetch_match_details(url: str, session: requests.Session = None) -> dict:
    """
    Retorna todas as seções da página de confronto:
    form dos times, últimas 10 partidas, estatísticas, classificação, bet stats.
    """
    if session is None:
        session = create_session()
    r = session.get(url, timeout=15)
    r.raise_for_status()
    return _parse_compare_page(r.text, url)


def _parse_compare_page(html: str, source_url: str = "") -> dict:
    soup = BeautifulSoup(html, "html.parser")
    dc = soup.find("div", class_="datacotainer")
    if not dc:
        return {"url": source_url, "erro": "datacotainer não encontrado"}

    result = {"url": source_url}

    # Informações dos times
    teamsinfo = dc.find("div", class_="teamsinfo")
    if teamsinfo:
        result["info_times"] = _parse_teamsinfo(teamsinfo)

    # Gráfico H2H (win/draw/loss %)
    chart = dc.find("div", class_="stackedbarchart")
    if chart:
        result["grafico_h2h"] = _parse_h2h_chart(chart)

    # Últimas 10 partidas de cada time
    last10 = dc.find("div", class_="lastteamsmatches")
    if last10:
        result["ultimas_10_partidas"] = _parse_last10(last10)

    # H2H — confrontos diretos (pode estar vazio se não houver histórico).
    # A página tem DOIS <div class="matchbtwteams">: o primeiro é só um ícone de navegação
    # (sempre vazio) e o segundo é o container real com os confrontos — por isso pega
    # o que realmente tem "matchitem" dentro, em vez do primeiro encontrado.
    h2h_divs = dc.find_all("div", class_="matchbtwteams")
    h2h_div = next((d for d in h2h_divs if d.find("div", class_="matchitem")), None)
    if h2h_div:
        result["confrontos_diretos"] = _parse_matchitems(h2h_div)

    # Fatos estatísticos (médias, clean sheets, etc.)
    stats = dc.find("div", class_="teamsstatistics")
    if stats:
        result["estatisticas"] = _parse_teamsstatistics(stats)

    # Classificação
    standings = dc.find("div", class_="teamstandings")
    if standings:
        result["classificacao"] = _parse_standings(standings)

    # Bet statistics (1/X/2, over/under por time)
    betstat = dc.find("div", class_="teamsbetstatistics")
    if betstat:
        result["bet_statistics"] = _parse_betstatistics(betstat)

    return result


def _parse_teamsinfo(div) -> dict:
    halves = div.find_all("div", class_="halfcontainer")
    teams = {}
    for half in halves:
        name_div = half.find("div", class_="name")
        name = name_div.get_text(strip=True) if name_div else "?"
        rows = {}
        for row in half.find_all("div", class_="datarow"):
            label_div = row.find("div", class_="label")
            value_div = row.find("div", class_="value")
            if label_div and value_div:
                rows[label_div.get_text(strip=True)] = value_div.get_text(strip=True)
        teams[name] = rows
    return teams


def _parse_h2h_chart(chart) -> dict:
    percents = chart.find_all("div", class_="percent")
    legend_items = chart.find_all("div", class_="item")
    result = {}
    for i, item in enumerate(legend_items):
        label = item.get_text(strip=True)
        pct = percents[i].get_text(strip=True) if i < len(percents) else "?"
        result[label] = pct
    return result


def _parse_last10(div) -> dict:
    result = {}
    for half in div.find_all("div", class_="halfcontainer"):
        caption = half.find("div", class_="caption")
        team_name = caption.get_text(strip=True) if caption else "?"

        # Form string (W/D/L)
        graphics = half.find("div", class_="graphics")
        form_text = ""
        if graphics:
            formastext = graphics.find("div", class_="formastext")
            form_text = formastext.get_text(strip=True) if formastext else ""

        # Últimas partidas
        matches_div = half.find("div", class_="matches")
        partidas = _parse_matchitems(matches_div) if matches_div else []

        result[team_name] = {
            "form": form_text,
            "partidas": partidas,
        }
    return result


def _parse_matchitems(container) -> list[dict]:
    items = []
    for item in container.find_all("div", class_="matchitem"):
        comp = item.find("div", class_="competition")
        date = item.find("div", class_="date")
        match_div = item.find("div", class_="match")
        if not match_div:
            continue

        host = match_div.find("div", class_="hostteam")
        guest = match_div.find("div", class_="guestteam")
        if not host or not guest:
            continue

        host_name = host.find("div", class_="name")
        guest_name = guest.find("div", class_="name")
        host_goals = host.find("div", class_="goals")
        guest_goals = guest.find("div", class_="goals")

        # Primeiro tempo — dentro de div.details > div.info > div.holder, tem 2x div.goals
        # (o 1º é o placar HT do mandante, o 2º do visitante) seguidos de div.label "half time result"
        ht_casa, ht_fora = "", ""
        details_div = item.find("div", class_="details")
        holder = details_div.find("div", class_="holder") if details_div else None
        if holder:
            label = holder.find("div", class_="label")
            if label and "half time" in label.get_text(strip=True).lower():
                ht_goals = holder.find_all("div", class_="goals")
                if len(ht_goals) >= 2:
                    ht_casa = ht_goals[0].get_text(strip=True)
                    ht_fora = ht_goals[1].get_text(strip=True)

        # Artilheiros — cada gol vem num div.action, com div.player tipo "13' Nome do Jogador",
        # dentro de div.hostteam (marcou o mandante) ou div.guestteam (marcou o visitante)
        scorers = []
        if details_div:
            for action in details_div.find_all("div", class_="action"):
                player_div = action.find("div", class_="player")
                if not player_div:
                    continue
                texto = player_div.get_text(strip=True)
                if not texto:
                    continue
                is_host_goal = action.find("div", class_="hostteam") is not None
                time_marcador = (host_name.get_text(strip=True) if host_name else "") if is_host_goal \
                    else (guest_name.get_text(strip=True) if guest_name else "")
                scorers.append(f"{texto} ({time_marcador})" if time_marcador else texto)

        items.append({
            "competicao": comp.get_text(strip=True) if comp else "",
            "data": date.get_text(strip=True) if date else "",
            "casa": host_name.get_text(strip=True) if host_name else "",
            "gols_casa": host_goals.get_text(strip=True) if host_goals else "",
            "fora": guest_name.get_text(strip=True) if guest_name else "",
            "gols_fora": guest_goals.get_text(strip=True) if guest_goals else "",
            "ht_casa": ht_casa,
            "ht_fora": ht_fora,
            "artilheiros": scorers[:10],
        })
    return items


def _parse_teamsstatistics(div) -> dict:
    result = {}
    for half in div.find_all("div", class_="halfcontainer"):
        name_div = half.find("div", class_="name")
        team_name = name_div.get_text(strip=True) if name_div else "?"
        stats = {}
        for item in half.find_all("div", class_="factitem"):
            label = item.find("div", class_="label")
            value = item.find("div", class_="value")
            if label and value:
                stats[label.get_text(strip=True)] = value.get_text(strip=True)
        result[team_name] = stats
    return result


def _parse_standings(div) -> list[dict]:
    rows = []
    data_div = div.find("div", class_="data")
    if not data_div:
        return []
    competition = data_div.find("div", class_="competition")
    comp_name = competition.get_text(strip=True) if competition else ""

    for row in data_div.find_all("div", class_="standingrow"):
        def g(cls):
            el = row.find("div", class_=cls)
            return el.get_text(strip=True) if el else ""
        common = row.find("div", class_="common")
        name_link = common.find("a") if common else None
        overall = row.find("div", class_="overall")
        away = row.find("div", class_="away")
        home = row.find("div", class_="home")

        def section_data(sec):
            if not sec:
                return {}
            return {
                "v": sec.find("div", class_="wins").get_text(strip=True) if sec.find("div", class_="wins") else "",
                "e": sec.find("div", class_="draws").get_text(strip=True) if sec.find("div", class_="draws") else "",
                "d": sec.find("div", class_="loses").get_text(strip=True) if sec.find("div", class_="loses") else "",
                "gm": sec.find("div", class_=["goals", "host"]).get_text(strip=True) if sec.find("div", class_=["goals", "host"]) else "",
                "gc": sec.find("div", class_=["goals", "guest"]).get_text(strip=True) if sec.find("div", class_=["goals", "guest"]) else "",
            }

        rows.append({
            "competicao": comp_name,
            "pos": common.find("div", class_="pos").get_text(strip=True) if common and common.find("div", class_="pos") else "",
            "time": name_link.get_text(strip=True) if name_link else "",
            "jogos": common.find("div", class_="matches").get_text(strip=True) if common and common.find("div", class_="matches") else "",
            "pontos": row.find("div", class_="points").get_text(strip=True) if row.find("div", class_="points") else "",
            "geral": section_data(overall),
            "casa": section_data(home),
            "fora": section_data(away),
            "destacado": "highlite" in " ".join(row.get("class", [])),
        })
    return rows


def _parse_betstatistics(div) -> dict:
    result = {}
    for half in div.find_all("div", class_="halfcontainer"):
        name_div = half.find("div", class_="name")
        team_name = name_div.get_text(strip=True) if name_div else "?"
        charts = {}
        for chart in half.find_all("div", class_="barchart"):
            title_div = chart.find("div", class_="title")
            title = title_div.get_text(strip=True) if title_div else "geral"
            rows = {}
            for row in chart.find_all("div", class_="barrow"):
                row_name = row.find("div", class_="name")
                bar = row.find("div", class_="bar")
                if row_name and bar:
                    rows[row_name.get_text(strip=True)] = bar.get_text(strip=True)
            if rows:
                charts[title] = rows
        result[team_name] = charts
    return result


# ─────────────────────────────────────────
# 3. PIPELINE COMPLETO
# ─────────────────────────────────────────

def scrape_all(date: str = None, fetch_details: bool = True, delay: float = 2.0) -> list[dict]:
    """
    Busca todas as partidas do dia e os detalhes de confronto de cada uma.
    delay: segundos entre requests de detalhe (respeitar o servidor).
    """
    session = create_session()
    print(f"[{datetime.now():%H:%M:%S}] Buscando partidas...")
    matches = fetch_predictions(date=date, session=session)
    print(f"  {len(matches)} partidas encontradas.")

    if fetch_details:
        for i, match in enumerate(matches, 1):
            url = match.get("url_detalhes")
            if not url:
                continue
            print(f"  [{i:3}/{len(matches)}] {match['casa']} vs {match['fora']}...")
            try:
                match["detalhes"] = fetch_match_details(url, session=session)
            except Exception as e:
                match["detalhes"] = {"erro": str(e)}
            time.sleep(delay)

    return matches


def scrape_backtest(date_str: str, out_dir: str = "backtest", delay: float = 1.0) -> list[dict]:
    """
    Scraping de lista de partidas de uma data passada (sem detalhes — rápido).
    date_str: 'YYYY-MM-DD'
    Salva em out_dir/YYYY-MM-DD.json
    """
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{date_str}.json")
    if os.path.exists(out_path):
        print(f"  [{date_str}] Já existe — pulando.")
        with open(out_path, encoding="utf-8") as f:
            return json.load(f)

    session = create_session()
    print(f"  [{date_str}] Buscando partidas...")
    matches = fetch_predictions(date=date_str, session=session)
    # Marca a data de referência em cada partida
    for m in matches:
        m["data_partida"] = date_str
    # Calcula momento baseado no histórico acumulado
    backtest_dir_abs = os.path.abspath(out_dir)
    print(f"  [{date_str}] Calculando momento dos times...")
    add_momentum_to_matches(matches, date_str, backtest_dir_abs)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)
    print(f"  [{date_str}] {len(matches)} partidas -> {out_path}")
    time.sleep(delay)
    return matches


def scrape_backtest_days(n_days: int = 7, out_dir: str = "backtest") -> dict:
    """Scraping dos últimos N dias. Retorna dict {date: [matches]}."""
    today = datetime.now().date()
    results = {}
    for i in range(1, n_days + 1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        results[d] = scrape_backtest(d, out_dir=out_dir)
    return results


# ─────────────────────────────────────────
# MOMENTO DOS TIMES (forma via backtest)
# ─────────────────────────────────────────

def compute_rolling_form(team_name: str, date_str: str, backtest_dir: str, n: int = 5) -> str:
    """
    Calcula a forma recente de um time olhando os arquivos de backtest anteriores à data.
    Retorna string W/D/L com até n resultados (mais recente primeiro).
    """
    files = sorted(glob.glob(os.path.join(backtest_dir, "????-??-??.json")))
    prev_files = [f for f in files if os.path.basename(f).replace(".json", "") < date_str]

    results = []
    search = team_name.strip().lower()

    for fpath in reversed(prev_files):
        try:
            with open(fpath, encoding="utf-8") as fp:
                matches = json.load(fp)
            if isinstance(matches, dict):
                matches = matches.get("matches", [])
        except Exception:
            continue

        for m in matches:
            if not m.get("resultado"):
                continue
            nc = (m.get("casa") or "").strip().lower()
            nf = (m.get("fora") or "").strip().lower()
            is_home = nc == search
            is_away = nf == search
            if not is_home and not is_away:
                continue
            try:
                parts = re.split(r"[-:]", m["resultado"])
                gc, gf = int(parts[0]), int(parts[1])
            except Exception:
                continue
            if is_home:
                results.append("W" if gc > gf else "D" if gc == gf else "L")
            else:
                results.append("W" if gf > gc else "D" if gf == gc else "L")

        if len(results) >= n:
            break

    return "".join(results[:n])


def form_label_pt(form: str):
    """Retorna 'Em Alta' / 'Estável' / 'Irregular' / 'Em Baixa' — ou None se amostra pequena."""
    if not form or len(form) < 3:
        return None
    wins = form.count("W")
    pct = wins / len(form)
    if pct >= 0.7:
        return "Em Alta"
    if pct >= 0.5:
        return "Estável"
    if pct >= 0.3:
        return "Irregular"
    return "Em Baixa"


def add_momentum_to_matches(matches: list, date_str: str, backtest_dir: str):
    """Injeta momento_casa / momento_fora em cada partida da lista."""
    for m in matches:
        fc = compute_rolling_form(m.get("casa", ""), date_str, backtest_dir)
        ff = compute_rolling_form(m.get("fora", ""), date_str, backtest_dir)
        m["form_casa"]    = fc
        m["form_fora"]    = ff
        m["momento_casa"] = form_label_pt(fc)
        m["momento_fora"] = form_label_pt(ff)


def recompute_momentum_all(backtest_dir: str = "backtest"):
    """Recomputa momento_casa/momento_fora em todos os arquivos de backtest existentes."""
    backtest_dir_abs = os.path.abspath(backtest_dir)
    files = sorted(glob.glob(os.path.join(backtest_dir_abs, "????-??-??.json")))
    print(f"Recomputando momento para {len(files)} arquivo(s)...")
    for fpath in files:
        date_str = os.path.basename(fpath).replace(".json", "")
        with open(fpath, encoding="utf-8") as f:
            matches = json.load(f)
        if isinstance(matches, dict):
            matches = matches.get("matches", [])
        add_momentum_to_matches(matches, date_str, backtest_dir_abs)
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(matches, f, ensure_ascii=False, indent=2)
        print(f"  {date_str} — {len(matches)} partidas atualizadas")
    print("Concluido!")


def save_json(data, filename: str = "predictions.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Salvo: {filename} ({len(data)} registros)")


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

if __name__ == "__main__":
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "list"

    if mode == "full":
        print("Modo COMPLETO — partidas + detalhes de cada confronto")
        data = scrape_all(fetch_details=True, delay=2.5)
        save_json(data, "predictions_full.json")
    elif mode == "detail":
        # Testa detalhes de uma partida específica
        url = sys.argv[2] if len(sys.argv) > 2 else \
            "https://www.statarea.com/compare/teams/Betis (Spain)/Real+Madrid (Spain)"
        print(f"Modo DETALHE — {url}")
        session = create_session()
        details = fetch_match_details(url, session)
        save_json([details], "detail_test.json")
        # Preview
        print(f"\nGráfico H2H: {details.get('grafico_h2h')}")
        for team, info in (details.get("ultimas_10_partidas") or {}).items():
            print(f"Form {team}: {info.get('form')} ({len(info.get('partidas',[]))} partidas)")
        print(f"Classificação rows: {len(details.get('classificacao', []))}")
    elif mode == "backtest":
        # python scraper.py backtest 7          → últimos 7 dias
        # python scraper.py backtest 2026-04-23 → data específica
        import re as _re
        arg = sys.argv[2] if len(sys.argv) > 2 else "7"
        if _re.match(r'\d{4}-\d{2}-\d{2}', arg):
            print(f"Modo BACKTEST — data específica: {arg}")
            scrape_backtest(arg)
        else:
            n = int(arg)
            print(f"Modo BACKTEST — últimos {n} dias")
            scrape_backtest_days(n)
    elif mode == "momentum":
        # python scraper.py momentum → recomputa momento em todos os arquivos de backtest
        print("Modo MOMENTUM — recomputando forma de todos os arquivos de backtest")
        recompute_momentum_all()
    else:
        print("Modo LISTA — partidas do dia")
        session = create_session()
        data = fetch_predictions(session=session)
        save_json(data, "predictions.json")
        print(f"\nPrimeiras 5 partidas:")
        for p in data[:5]:
            print(f"  {p['hora']} | {p.get('liga','')[:28]} | {p['casa']} vs {p['fora']} | TIP:{p['tip']} | 1:{p.get('odds_1')}% X:{p.get('odds_x')}% 2:{p.get('odds_2')}%")

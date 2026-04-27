"""
Servidor Flask — API + frontend para exibir dados do StatArea
"""
from flask import Flask, jsonify, send_from_directory, abort, request
import json, os, glob, re, threading, time
import requests as http_req
from datetime import datetime

FOTMOB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.fotmob.com/",
    "Accept":  "*/*",
}

app = Flask(__name__, static_folder="static", static_url_path="/static")
DATA_DIR    = os.path.dirname(__file__)
MOMENTUM_DIR = os.path.join(DATA_DIR, "momentum_history")
os.makedirs(MOMENTUM_DIR, exist_ok=True)


def ajustar_hora(hora_str):
    """Subtrai 3 horas do horário vindo do StatArea (UTC → BRT)."""
    if not hora_str:
        return hora_str
    from datetime import datetime, timedelta
    try:
        t = datetime.strptime(hora_str.strip(), "%H:%M") - timedelta(hours=3)
        return t.strftime("%H:%M")
    except Exception:
        return hora_str


def load_predictions():
    # Prefere o arquivo full (com detalhes), senão usa o simples
    for fname in ["predictions_full.json", "predictions.json"]:
        path = os.path.join(DATA_DIR, fname)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f), fname
    return [], None


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/matches")
def api_matches():
    matches, source = load_predictions()
    # Retorna lista resumida (sem detalhes) para a listagem
    summary = []
    for m in matches:
        summary.append({
            "id": m.get("url_detalhes", "").split("/compare/teams/")[-1],
            "hora": m.get("hora"),
            "liga": m.get("liga"),
            "pais": m.get("pais"),
            "casa": m.get("casa"),
            "fora": m.get("fora"),
            "tip": m.get("tip"),
            "odds_1": m.get("odds_1"),
            "odds_x": m.get("odds_x"),
            "odds_2": m.get("odds_2"),
            "odds_h1": m.get("odds_h1"),
            "odds_hx": m.get("odds_hx"),
            "odds_h2": m.get("odds_h2"),
            "over_1_5": m.get("over_1_5"),
            "over_2_5": m.get("over_2_5"),
            "over_3_5": m.get("over_3_5"),
            "bts": m.get("bts"),
            "ots": m.get("ots"),
            "votos_favor": m.get("votos_favor"),
            "votos_contra": m.get("votos_contra"),
            "tem_detalhes": "detalhes" in m and "erro" not in m.get("detalhes", {}),
            "url_detalhes": m.get("url_detalhes"),
            "data_coleta": m.get("data_coleta"),
        })
    return jsonify({"total": len(summary), "source": source, "matches": summary})


@app.route("/api/match/<path:match_id>")
def api_match_detail(match_id):
    matches, _ = load_predictions()
    for m in matches:
        mid = m.get("url_detalhes", "").split("/compare/teams/")[-1]
        if mid == match_id:
            return jsonify(m)
    abort(404)


@app.route("/api/status")
def api_status():
    matches, source = load_predictions()
    has_details = sum(1 for m in matches if "detalhes" in m and "erro" not in m.get("detalhes", {}))
    return jsonify({
        "total_partidas": len(matches),
        "com_detalhes": has_details,
        "source": source,
    })


BACKTEST_DIR = os.path.join(DATA_DIR, "backtest")


def load_backtest_dates():
    """Retorna lista de datas disponíveis no backtest, ordenadas desc."""
    if not os.path.exists(BACKTEST_DIR):
        return []
    files = glob.glob(os.path.join(BACKTEST_DIR, "????-??-??.json"))
    dates = sorted(
        [os.path.basename(f).replace(".json", "") for f in files],
        reverse=True
    )
    return dates


def load_backtest_day(date_str: str):
    path = os.path.join(BACKTEST_DIR, f"{date_str}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@app.route("/api/backtest/dates")
def api_backtest_dates():
    dates = load_backtest_dates()
    return jsonify({"dates": dates, "total": len(dates)})


@app.route("/api/backtest/<date_str>")
def api_backtest_day(date_str):
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        abort(400)
    matches = load_backtest_day(date_str)
    if matches is None:
        abort(404)
    # Calcula estatísticas resumidas do dia
    tips = {"1": 0, "X": 0, "2": 0, "N/D": 0}
    for m in matches:
        t = m.get("tip", "N/D")
        tips[t] = tips.get(t, 0) + 1

    over60      = [m for m in matches if (m.get("odds_1") or 0) >= 60 or (m.get("odds_2") or 0) >= 60]
    bts_high    = [m for m in matches if (m.get("bts") or 0) >= 60]
    over25_high = [m for m in matches if (m.get("over_2_5") or 0) >= 65]
    with_result = [m for m in matches if m.get("resultado")]

    # Acurácia dos TIPs
    decididos  = [m for m in matches if m.get("tip_acertou") is not None]
    acertaram  = [m for m in decididos if m.get("tip_acertou") is True]
    tip_acuracia = round(len(acertaram) / len(decididos) * 100, 1) if decididos else None

    return jsonify({
        "date": date_str,
        "total": len(matches),
        "tips": tips,
        "alta_confianca": len(over60),
        "bts_provavel": len(bts_high),
        "over25_provavel": len(over25_high),
        "com_resultado": len(with_result),
        "tips_acertaram": len(acertaram),
        "tips_decididos": len(decididos),
        "tip_acuracia": tip_acuracia,
        "matches": matches,
    })


@app.route("/api/patterns")
def api_patterns():
    """Cruza padrões do backtest (probabilidades × placares reais)."""
    dates = load_backtest_dates()

    def range_key(val):
        if val is None: return None
        v = int(val)
        if v >= 80: return "80+"
        if v >= 70: return "70-79"
        if v >= 60: return "60-69"
        if v >= 50: return "50-59"
        return None

    def add(d, key, hit):
        if key not in d:
            d[key] = {"total": 0, "hit": 0}
        d[key]["total"] += 1
        if hit:
            d[key]["hit"] += 1

    tip_pat   = {}   # tip_type → range → {total, hit}
    over15    = {}
    over25    = {}
    over35    = {}
    bts_pat   = {}
    score_freq    = {}   # placar FT real → contagem
    ht_score_freq = {}   # placar HT real → contagem
    momento_mat   = {}   # "CasaMomento|ForaMomento" → {total, hit, casa, fora}
    total         = 0
    total_ht      = 0

    for date in dates:
        matches = load_backtest_day(date)
        if not matches:
            continue
        if isinstance(matches, dict):
            matches = matches.get("matches", [])

        for m in matches:
            if not m.get("resultado"):
                continue
            try:
                parts = re.split(r"[-:]", m["resultado"])
                gc, gf = int(parts[0]), int(parts[1])
            except Exception:
                continue

            tg = gc + gf
            total += 1
            score_freq[f"{gc}-{gf}"] = score_freq.get(f"{gc}-{gf}", 0) + 1

            # HT score
            ht_raw = m.get("ht_score")
            if ht_raw:
                try:
                    ht_parts = re.split(r"[-:]", str(ht_raw))
                    hg, ag = int(ht_parts[0]), int(ht_parts[1])
                    ht_key = f"{hg}-{ag}"
                    ht_score_freq[ht_key] = ht_score_freq.get(ht_key, 0) + 1
                    total_ht += 1
                except Exception:
                    pass

            # Over/Under
            for field, thr, acc in [
                ("over_1_5", 1, over15),
                ("over_2_5", 2, over25),
                ("over_3_5", 3, over35),
            ]:
                rng = range_key(m.get(field))
                if rng:
                    add(acc, rng, tg > thr)

            # BTS
            rng = range_key(m.get("bts"))
            if rng:
                add(bts_pat, rng, gc > 0 and gf > 0)

            # TIP accuracy (só TIPs simples)
            tip = m.get("tip", "")
            acertou = m.get("tip_acertou")
            if acertou is not None and tip in ("1", "2", "X"):
                odds_map = {"1": "odds_1", "2": "odds_2", "X": "odds_x"}
                rng = range_key(m.get(odds_map[tip]))
                if rng:
                    if tip not in tip_pat:
                        tip_pat[tip] = {}
                    add(tip_pat[tip], rng, acertou)

            # Matriz de Momento
            mc = m.get("momento_casa")
            mf = m.get("momento_fora")
            if mc and mf and acertou is not None:
                key = f"{mc}|{mf}"
                if key not in momento_mat:
                    momento_mat[key] = {"total": 0, "hit": 0, "casa": mc, "fora": mf}
                momento_mat[key]["total"] += 1
                if acertou:
                    momento_mat[key]["hit"] += 1

    def with_pct(d):
        return {k: {**v, "pct": round(v["hit"] / v["total"] * 100, 1) if v["total"] else 0}
                for k, v in sorted(d.items())}

    # Adiciona % à matriz de momento
    momento_mat_pct = {
        k: {**v, "pct": round(v["hit"] / v["total"] * 100, 1) if v["total"] else 0}
        for k, v in momento_mat.items()
    }

    return jsonify({
        "total_partidas": total,
        "datas": dates,
        "tip":     {k: with_pct(v) for k, v in tip_pat.items()},
        "over_1_5": with_pct(over15),
        "over_2_5": with_pct(over25),
        "over_3_5": with_pct(over35),
        "bts":        with_pct(bts_pat),
        "score_freq":    score_freq,
        "ht_score_freq": ht_score_freq,
        "total_ht":      total_ht,
        "momento_matrix": momento_mat_pct,
    })


@app.route("/api/radar/live")
def api_radar_live():
    """Busca jogos ao vivo do radarfutebol.com (dados embutidos no HTML como JSON)."""
    import html as h_lib
    RADAR_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.radarfutebol.com/",
    }
    try:
        r = http_req.get("https://www.radarfutebol.com/", headers=RADAR_HEADERS, timeout=12)
        r.raise_for_status()
        m = re.search(r'id="app" data-page="([^"]+)"', r.text)
        if not m:
            return jsonify({"error": "Não foi possível parsear a página"}), 503
        data = json.loads(h_lib.unescape(m.group(1)))
        camps = data.get("props", {}).get("campeonatosIniciais", [])
        live = []
        for camp in camps:
            for ev_id, ev in camp.get("eventos", {}).items():
                if ev.get("status") != "inprogress":
                    continue
                live.append({
                    "id":           ev["idEvento"],
                    "casa":         ev.get("timeCasa", ""),
                    "fora":         ev.get("timeFora", ""),
                    "liga":         ev.get("nomeCampeonato", ""),
                    "pais":         ev.get("nomeCategoria", ""),
                    "flag":         ev.get("flag", ""),
                    "tempo":        ev.get("tempoTexto", ""),
                    "golCasaFt":    ev.get("golTimeCasaFt", 0),
                    "golForaFt":    ev.get("golTimeForaFt", 0),
                    "golCasaHt":    ev.get("golTimeCasaHt", 0),
                    "golForaHt":    ev.get("golTimeForaHt", 0),
                    "cartaoCasa":   ev.get("cartaoVermelhoTimeCasa", 0),
                    "cartaoFora":   ev.get("cartaoVermelhoTimeFora", 0),
                    "odd1":         ev.get("oddTimeCasa"),
                    "oddX":         ev.get("oddEmpate"),
                    "odd2":         ev.get("oddTimeFora"),
                    "under15":      ev.get("oddUnder15FT"),
                    "over15":       ev.get("oddOver15FT"),
                    "under25":      ev.get("oddUnder25FT"),
                    "over25":       ev.get("oddOver25FT"),
                    "bttsSim":      ev.get("oddBttsSim"),
                    "bttsNao":      ev.get("oddBttsNao"),
                    "clOdd1":       ev.get("classOddTimeCasa", ""),
                    "clOddX":       ev.get("classOddEmpate", ""),
                    "clOdd2":       ev.get("classOddTimeFora", ""),
                    "clUnder15":    ev.get("classOddUnder15FT", ""),
                    "clOver15":     ev.get("classOddOver15FT", ""),
                    "clUnder25":    ev.get("classOddUnder25FT", ""),
                    "clOver25":     ev.get("classOddOver25FT", ""),
                    "clBttsSim":    ev.get("classOddBttsSim", ""),
                    "clBttsNao":    ev.get("classOddBttsNao", ""),
                    "slugCategoria": ev.get("slugCategoria", ""),
                    "idWilliamhill": ev.get("idWilliamhill", ""),
                    "linkBetfairBr": ev.get("linkBetfairBr", ""),
                })
        return jsonify({"live": live, "total": len(live)})
    except Exception as e:
        return jsonify({"error": str(e), "live": [], "total": 0}), 503


# ── Cache simples de momentum em memória (evita abrir browser repetidamente) ──
_momentum_cache = {}
_momentum_lock  = threading.Lock()   # proteção para acesso concorrente


def _fetch_radar_live_data():
    """Busca lista de jogos ao vivo do radarfutebol.com. Retorna lista ou []."""
    import html as h_lib
    RADAR_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer":    "https://www.radarfutebol.com/",
    }
    try:
        r = http_req.get("https://www.radarfutebol.com/", headers=RADAR_HEADERS, timeout=12)
        r.raise_for_status()
        m = re.search(r'id="app" data-page="([^"]+)"', r.text)
        if not m:
            return []
        data  = json.loads(h_lib.unescape(m.group(1)))
        camps = data.get("props", {}).get("campeonatosIniciais", [])
        live  = []
        for camp in camps:
            for ev_id, ev in camp.get("eventos", {}).items():
                if ev.get("status") != "inprogress":
                    continue
                live.append({
                    "id":   ev["idEvento"],
                    "casa": ev.get("timeCasa", ""),
                    "fora": ev.get("timeFora", ""),
                    "liga": ev.get("nomeCampeonato", ""),
                })
        return live
    except Exception as e:
        print(f"[monitor] Erro ao buscar radar live: {e}")
        return []


def _process_momentum(event_id, casa="", fora="", liga=""):
    """Busca momentum do SofaScore via Playwright, detecta FT e salva se encerrado.
    Retorna dict com os dados ou None em caso de erro.
    Usa cache de 90s para evitar chamadas repetidas.
    """
    from playwright.sync_api import sync_playwright

    # Verifica cache primeiro
    with _momentum_lock:
        cached = _momentum_cache.get(event_id)
        if cached and time.time() - cached["ts"] < 90:
            return cached["data"]

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page = ctx.new_page()
            page.goto("https://www.sofascore.com/", timeout=15000, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            result = page.evaluate(f"""async () => {{
                try {{
                    const [rGraph, rInc] = await Promise.all([
                        fetch('/api/v1/event/{event_id}/graph'),
                        fetch('/api/v1/event/{event_id}/incidents')
                    ]);
                    const graph = rGraph.ok ? await rGraph.json() : {{}};
                    const inc   = rInc.ok  ? await rInc.json()  : {{}};
                    return {{status: 200, graph, incidents: inc}};
                }} catch(e) {{
                    return {{error: e.message}};
                }}
            }}""")
            browser.close()

        if result.get("status") != 200:
            return None

        graph     = result.get("graph", {})
        incidents = result.get("incidents", {})
        inc_list  = incidents.get("incidents", [])

        # Extrai gols — APENAS incidentType == "goal"
        goals_uniq = []
        for inc in inc_list:
            if inc.get("incidentType") == "goal":
                goals_uniq.append({
                    "minute":    inc.get("time", 0),
                    "addedTime": inc.get("addedTime", 0),
                    "team":      "home" if inc.get("isHome") else "away",
                })

        # ── Detecta fim de partida ──────────────────────────────────────
        # Critério 1: incidente "period" com text == "FT"
        finished = any(
            i.get("incidentType") == "period" and i.get("text") == "FT"
            for i in inc_list
        )
        # Critério 2: graphPoints chegam ao minuto >= 90
        if not finished:
            pts = graph.get("graphPoints") or []
            if pts and max((p.get("minute", 0) for p in pts), default=0) >= 90:
                finished = True

        data = {**graph, "goals": goals_uniq, "finished": finished}

        # ── Auto-save quando a partida termina ────────────────────────
        saved = False
        if finished:
            today     = datetime.now().strftime("%Y-%m-%d")
            save_file = os.path.join(MOMENTUM_DIR, f"{today}_{event_id}.json")
            if not os.path.exists(save_file):
                payload = {
                    "event_id":    event_id,
                    "date":        today,
                    "saved_at":    datetime.now().isoformat(),
                    "casa":        casa,
                    "fora":        fora,
                    "liga":        liga,
                    "graphPoints": graph.get("graphPoints", []),
                    "goals":       goals_uniq,
                }
                with open(save_file, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                saved = True
                print(f"[momentum] Salvo: {save_file}")
                # Atualiza cache de análise em background
                threading.Thread(target=_rebuild_analysis_cache, daemon=True).start()
            else:
                saved = True   # já existia

        data["saved"] = saved

        with _momentum_lock:
            _momentum_cache[event_id] = {"ts": time.time(), "data": data}
        return data

    except Exception as e:
        print(f"[momentum] Erro event {event_id}: {e}")
        return None


# ── Monitor de fundo: verifica jogos ao vivo a cada 5 min e salva os encerrados ──
def _background_monitor():
    """Thread daemon que varre os jogos ao vivo e salva momentum quando FT."""
    print("[monitor] Thread de monitoramento iniciada.")
    # Aguarda 60s na primeira vez (Flask precisa subir antes)
    time.sleep(60)
    while True:
        try:
            today     = datetime.now().strftime("%Y-%m-%d")
            live_list = _fetch_radar_live_data()
            pendentes = [
                m for m in live_list
                if not os.path.exists(
                    os.path.join(MOMENTUM_DIR, f"{today}_{m['id']}.json")
                )
            ]
            if pendentes:
                print(f"[monitor] {len(live_list)} ao vivo, {len(pendentes)} ainda não salvos — verificando...")
                for m in pendentes:
                    data = _process_momentum(m["id"], m["casa"], m["fora"], m["liga"])
                    if data and data.get("finished"):
                        print(f"[monitor] ✓ Encerrado e salvo: {m['casa']} x {m['fora']}")
                    # Pequena pausa entre chamadas Playwright para não sobrecarregar
                    time.sleep(3)
            else:
                print(f"[monitor] {len(live_list)} ao vivo, todos já salvos ou sem jogos.")
        except Exception as e:
            print(f"[monitor] Erro geral: {e}")
        # Aguarda 5 minutos antes da próxima rodada
        time.sleep(300)


# Inicia a thread de monitoramento (daemon = morre junto com o Flask)
threading.Thread(target=_background_monitor, daemon=True, name="MomentumMonitor").start()


@app.route("/api/radar/momentum/<int:event_id>")
def api_radar_momentum(event_id):
    """Busca dados de Attack Momentum do SofaScore via Playwright.
    Query params opcionais: casa, fora, liga — usados ao salvar histórico.
    """
    from flask import request as flask_req

    casa = flask_req.args.get("casa", "")
    fora = flask_req.args.get("fora", "")
    liga = flask_req.args.get("liga", "")

    data = _process_momentum(event_id, casa, fora, liga)
    if data is None:
        return jsonify({"error": "Sem dados do SofaScore"}), 503
    return jsonify(data)


@app.route("/api/momentum/history")
def api_momentum_history():
    """Lista partidas com momentum salvo (mais recentes primeiro)."""
    files = sorted(glob.glob(os.path.join(MOMENTUM_DIR, "*.json")), reverse=True)
    matches = []
    for fpath in files[:200]:
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            pts   = d.get("graphPoints", [])
            total = len(pts)
            matches.append({
                "event_id":  d.get("event_id"),
                "date":      d.get("date"),
                "saved_at":  d.get("saved_at"),
                "casa":      d.get("casa", ""),
                "fora":      d.get("fora", ""),
                "liga":      d.get("liga", ""),
                "goals":     d.get("goals", []),
                "points":    total,
                "filename":  os.path.basename(fpath),
            })
        except Exception:
            pass
    return jsonify({"matches": matches, "total": len(matches)})


@app.route("/api/momentum/history/<int:event_id>")
def api_momentum_history_match(event_id):
    """Retorna dados completos de um evento salvo."""
    files = glob.glob(os.path.join(MOMENTUM_DIR, f"*_{event_id}.json"))
    if not files:
        return jsonify({"error": "Não encontrado"}), 404
    with open(files[0], encoding="utf-8") as f:
        return jsonify(json.load(f))


def _minute_range(minute):
    """Classifica o minuto em faixa de jogo."""
    if minute <= 30:  return "early"   # 0-30
    if minute <= 45:  return "ht"      # 31-45 (+ acrésc. 1T)
    if minute <= 70:  return "second"  # 46-70
    return "late"                       # 71-90+


def _goal_situation(team, sh_before, sa_before):
    """Situação do time que marcou, no momento antes do gol."""
    if sh_before == sa_before:
        return "drawing"
    if team == "home":
        return "leading" if sh_before > sa_before else "trailing"
    else:
        return "leading" if sa_before > sh_before else "trailing"


@app.route("/api/momentum/patterns")
def api_momentum_patterns():
    """Extrai padrões pré-gol de todos os históricos salvos.
    Cada padrão inclui minute_range e situation para filtragem contextual.
    """
    WINDOW = 8   # graphPoints antes do gol a capturar
    patterns = []
    files = glob.glob(os.path.join(MOMENTUM_DIR, "*.json"))
    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            pts   = sorted(data.get("graphPoints", []), key=lambda p: p.get("minute", 0))
            goals = sorted(data.get("goals", []), key=lambda g: g.get("minute", 0))
            if not pts or not goals:
                continue
            for idx, goal in enumerate(goals):
                gmin = goal.get("minute", 0)
                team = goal.get("team", "home")

                # Score antes deste gol (conta gols anteriores)
                sh = sum(1 for g in goals[:idx] if g.get("team") == "home")
                sa = sum(1 for g in goals[:idx] if g.get("team") == "away")

                before = [p for p in pts if p.get("minute", 0) < gmin]
                if len(before) < 3:
                    continue
                window = before[-WINDOW:]
                values = [p.get("value", 0) for p in window]
                patterns.append({
                    "team":         team,
                    "values":       values,
                    "goal_min":     gmin,
                    "match":        f"{data.get('casa','?')} x {data.get('fora','?')}",
                    "date":         data.get("date", ""),
                })
        except Exception:
            continue
    return jsonify({"patterns": patterns, "total": len(patterns), "window": WINDOW})


@app.route("/api/momentum/export")
def api_momentum_export():
    """Gera planilha Excel com os dados das partidas ao vivo salvas."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from flask import send_file
    import io

    # Carrega todos os arquivos salvos
    files = sorted(glob.glob(os.path.join(MOMENTUM_DIR, "*.json")), reverse=True)
    matches_data = []
    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                matches_data.append(json.load(f))
        except Exception:
            pass

    wb = Workbook()

    # ── Estilos ───────────────────────────────────────────────────────────
    hdr_font  = Font(bold=True, color="FFFFFF", size=10)
    hdr_fill  = PatternFill("solid", fgColor="1E3A5F")
    hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    c_align   = Alignment(horizontal="center", vertical="center")
    fill_green = PatternFill("solid", fgColor="C6EFCE")
    fill_red   = PatternFill("solid", fgColor="FFC7CE")
    fill_alt   = PatternFill("solid", fgColor="F0F4FF")
    thin = Border(
        left=Side(style="thin", color="CCCCCC"),
        right=Side(style="thin", color="CCCCCC"),
        top=Side(style="thin", color="CCCCCC"),
        bottom=Side(style="thin", color="CCCCCC"),
    )

    def _hcell(ws, row, col, value, width=None):
        c = ws.cell(row=row, column=col, value=value)
        c.font = hdr_font; c.fill = hdr_fill
        c.alignment = hdr_align; c.border = thin
        if width:
            ws.column_dimensions[get_column_letter(col)].width = width
        return c

    def _dcell(ws, row, col, value, fill=None):
        c = ws.cell(row=row, column=col, value=value)
        c.alignment = c_align; c.border = thin
        if fill: c.fill = fill
        return c

    # ── Sheet 1: Resumo ───────────────────────────────────────────────────
    ws1 = wb.active
    ws1.title = "Resumo"
    ws1.row_dimensions[1].height = 32

    hdrs = [
        ("Data",         12), ("Liga",         22), ("Casa",         20),
        ("Fora",         20), ("Placar FT",     10), ("Placar HT",     10),
        ("Gols Casa FT",  10), ("Gols Fora FT", 10), ("Gols Casa HT", 10),
        ("Gols Fora HT", 10), ("Total Gols",    10), ("Minutos\nSalvos", 10),
    ]
    for col, (label, width) in enumerate(hdrs, 1):
        _hcell(ws1, 1, col, label, width)

    for ri, d in enumerate(matches_data, 2):
        goals = d.get("goals", [])
        pts   = d.get("graphPoints", [])
        gc_ft = sum(1 for g in goals if g.get("team") == "home")
        gf_ft = sum(1 for g in goals if g.get("team") == "away")
        gc_ht = sum(1 for g in goals if g.get("team") == "home" and g.get("minute", 0) <= 45)
        gf_ht = sum(1 for g in goals if g.get("team") == "away" and g.get("minute", 0) <= 45)
        row_fill = fill_alt if ri % 2 == 0 else None
        _dcell(ws1, ri, 1, d.get("date", ""), row_fill)
        _dcell(ws1, ri, 2, d.get("liga", ""), row_fill)
        _dcell(ws1, ri, 3, d.get("casa", ""), row_fill)
        _dcell(ws1, ri, 4, d.get("fora", ""), row_fill)
        _dcell(ws1, ri, 5, f"{gc_ft}:{gf_ft}", fill_green if gc_ft + gf_ft > 0 else row_fill)
        _dcell(ws1, ri, 6, f"{gc_ht}:{gf_ht}", row_fill)
        _dcell(ws1, ri, 7, gc_ft, row_fill)
        _dcell(ws1, ri, 8, gf_ft, row_fill)
        _dcell(ws1, ri, 9, gc_ht, row_fill)
        _dcell(ws1, ri, 10, gf_ht, row_fill)
        _dcell(ws1, ri, 11, gc_ft + gf_ft,
               fill_green if gc_ft + gf_ft >= 3 else (fill_red if gc_ft + gf_ft == 0 else row_fill))
        _dcell(ws1, ri, 12, len(pts), row_fill)

    ws1.freeze_panes = "A2"

    # ── Sheet 2: Gols por Minuto ──────────────────────────────────────────
    ws2 = wb.create_sheet("Gols")
    ws2.row_dimensions[1].height = 40

    _hcell(ws2, 1, 1, "Minuto", 8)
    _hcell(ws2, 1, 2, "Partida", 26)
    _hcell(ws2, 1, 3, "Data", 12)
    _hcell(ws2, 1, 4, "Liga", 20)
    _hcell(ws2, 1, 5, "Time", 12)
    _hcell(ws2, 1, 6, "Acréscimo", 10)
    _hcell(ws2, 1, 7, "Placar Após", 12)

    ri = 2
    for d in matches_data:
        goals = sorted(d.get("goals", []), key=lambda g: g.get("minute", 0))
        partida = f"{d.get('casa','?')} x {d.get('fora','?')}"
        sh, sa = 0, 0
        for g in goals:
            team = g.get("team", "")
            if team == "home": sh += 1
            else: sa += 1
            row_fill = fill_green if team == "home" else fill_red
            _dcell(ws2, ri, 1, g.get("minute", ""), row_fill)
            _dcell(ws2, ri, 2, partida, row_fill)
            _dcell(ws2, ri, 3, d.get("date", ""), row_fill)
            _dcell(ws2, ri, 4, d.get("liga", ""), row_fill)
            _dcell(ws2, ri, 5, d.get("casa", "") if team == "home" else d.get("fora", ""), row_fill)
            _dcell(ws2, ri, 6, g.get("addedTime", 0) or 0, row_fill)
            _dcell(ws2, ri, 7, f"{sh}:{sa}", row_fill)
            ri += 1
    ws2.freeze_panes = "A2"

    # ── Sheet 3: Momentum (matrix minuto × partida) ───────────────────────
    ws3 = wb.create_sheet("Momentum")
    ws3.row_dimensions[1].height = 44

    _hcell(ws3, 1, 1, "Min", 5)
    for ci, d in enumerate(matches_data, 2):
        label = f"{d.get('casa','?')} x {d.get('fora','?')}\n{d.get('date','')}"
        _hcell(ws3, 1, ci, label, 18)
        ws3.column_dimensions[get_column_letter(ci)].width = 8

    for rmi, minute in enumerate(range(1, 91), 2):
        c = ws3.cell(row=rmi, column=1, value=minute)
        c.font = Font(bold=True, size=9)
        c.alignment = c_align
        for ci, d in enumerate(matches_data, 2):
            # Monta mapa tolerando int e float como chave
            pts_map = {}
            for p in d.get("graphPoints", []):
                pts_map[int(p["minute"])] = p["value"]
            val = pts_map.get(minute)      # None quando realmente ausente
            cell = ws3.cell(row=rmi, column=ci, value=val if val is not None else "")
            cell.alignment = c_align
            cell.border = thin
            cell.font = Font(size=8)
            if val is not None:
                if val > 0:   cell.fill = fill_green
                elif val < 0: cell.fill = fill_red
    ws3.freeze_panes = "B2"

    # ── Sheet 4: Dados_ML (formato largo para modelo — 1 linha por partida) ─
    ws4 = wb.create_sheet("Dados_ML")
    ws4.row_dimensions[1].height = 28

    # Colunas fixas de contexto
    ctx_cols = [
        ("date", 12), ("liga", 20), ("casa", 18), ("fora", 18),
        ("gc_ft", 8), ("gf_ft", 8), ("gc_ht", 8), ("gf_ht", 8),
        ("total_gols", 10), ("tem_gol", 8),
    ]
    # + colunas de minuto 1..90
    min_cols = [f"min_{m}" for m in range(1, 91)]

    all_cols = ctx_cols + [(c, 7) for c in min_cols]
    for ci, (label, width) in enumerate(all_cols, 1):
        _hcell(ws4, 1, ci, label, width)

    for ri, d in enumerate(matches_data, 2):
        goals = d.get("goals", [])
        pts   = d.get("graphPoints", [])
        gc_ft = sum(1 for g in goals if g.get("team") == "home")
        gf_ft = sum(1 for g in goals if g.get("team") == "away")
        gc_ht = sum(1 for g in goals if g.get("team") == "home" and g.get("minute", 0) <= 45)
        gf_ht = sum(1 for g in goals if g.get("team") == "away" and g.get("minute", 0) <= 45)
        total = gc_ft + gf_ft
        row_fill = fill_alt if ri % 2 == 0 else None

        ctx_vals = [
            d.get("date", ""), d.get("liga", ""), d.get("casa", ""), d.get("fora", ""),
            gc_ft, gf_ft, gc_ht, gf_ht, total, 1 if total > 0 else 0,
        ]
        for ci, val in enumerate(ctx_vals, 1):
            _dcell(ws4, ri, ci, val, row_fill)

        # Mapa minuto → valor (int chave)
        pts_map = {}
        for p in pts:
            pts_map[int(p["minute"])] = p["value"]

        for mi, minute in enumerate(range(1, 91), len(ctx_cols) + 1):
            val = pts_map.get(minute, 0)   # 0 quando minuto ausente
            cell = ws4.cell(row=ri, column=mi, value=val)
            cell.alignment = c_align
            cell.border = thin
            cell.font = Font(size=8)
            if val > 0:   cell.fill = fill_green
            elif val < 0: cell.fill = fill_red
            elif row_fill: cell.fill = row_fill

    ws4.freeze_panes = "A2"

    # ── Envia o arquivo ───────────────────────────────────────────────────
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"partidas_ao_vivo_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=fname,
    )


ANALYSIS_CACHE_FILE = os.path.join(DATA_DIR, "momentum_analysis.json")


def _compute_analysis():
    """Computa padrões de gol. Testa janelas de 3-15 min automaticamente
    e usa a que produz maior acurácia balanceada."""
    import math, random as _rand

    NONE_GAP  = 12
    NONE_STEP = 3
    CANDIDATES = list(range(3, 16))   # testa 3 a 15 minutos

    # Pré-carrega todos os arquivos uma única vez
    files_list = sorted(glob.glob(os.path.join(MOMENTUM_DIR, "*.json")))
    all_data = []
    for fpath in files_list:
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            pts   = sorted(d.get("graphPoints", []), key=lambda p: float(p.get("minute", 0)))
            goals = sorted(d.get("goals",       []), key=lambda g: float(g.get("minute", 0)))
            if len(pts) < 5:
                continue
            all_data.append({
                "pt_list":   [(float(p["minute"]), p["value"]) for p in pts],
                "goal_list": [(float(g["minute"]), g["team"])  for g in goals],
            })
        except Exception:
            continue

    total_matches = len(all_data)
    total_goals   = sum(len(d["goal_list"]) for d in all_data)

    def feats(w):
        tail  = sum(w[-4:]) / max(len(w), 1)
        trend = w[-1] - w[0]
        peak  = max(w, key=abs)
        return tail + 0.3 * trend + 0.2 * peak

    def extract_windows(W):
        hw, aw, nw = [], [], []
        for d in all_data:
            pt_list   = d["pt_list"]
            goal_list = d["goal_list"]
            if len(pt_list) < W + 2:
                continue
            goal_mins = [gm for gm, _ in goal_list]
            for gmin, team in goal_list:
                before = [(m, v) for m, v in pt_list if m < gmin]
                if len(before) < W:
                    continue
                win = [v for _, v in before[-W:]]
                (hw if team == "home" else aw).append(win)
            for i in range(W, len(pt_list) - 1, NONE_STEP):
                m_now = pt_list[i][0]
                if any(abs(gm - m_now) <= NONE_GAP for gm in goal_mins):
                    continue
                nw.append([pt_list[j][1] for j in range(i - W, i)])
        return hw, aw, nw

    def best_threshold(hw, aw, nw):
        goal_n   = len(hw) + len(aw)
        if goal_n < 4:
            return 0.0, 8
        none_bal = _rand.sample(nw, min(len(nw), max(goal_n, 1)))
        labeled  = ([(w, "home") for w in hw] +
                    [(w, "away") for w in aw] +
                    [(w, "none") for w in none_bal])
        best_bal = 0.0; best_T = 8
        for T in range(1, 60):
            cnt = {"home": [0,0], "away": [0,0], "none": [0,0]}
            for w, lbl in labeled:
                score = feats(w)
                pred  = "home" if score > T else ("away" if score < -T else "none")
                cnt[lbl][1] += 1; cnt[lbl][0] += int(pred == lbl)
            recalls = [cnt[k][0]/cnt[k][1] for k in cnt if cnt[k][1]]
            bal = sum(recalls)/len(recalls) if recalls else 0
            if bal > best_bal:
                best_bal = bal; best_T = T
        return best_bal, best_T

    # ── Busca a melhor janela ─────────────────────────────────────────────
    window_scores = {}
    best_window = 8; best_acc_overall = 0.0; best_T_overall = 8

    for W in CANDIDATES:
        hw, aw, nw = extract_windows(W)
        acc, T     = best_threshold(hw, aw, nw)
        window_scores[W] = round(acc * 100, 1)
        if acc > best_acc_overall:
            best_acc_overall = acc
            best_window      = W
            best_T_overall   = T

    # ── Janelas finais com a melhor janela ────────────────────────────────
    W = best_window
    home_wins, away_wins, none_wins = extract_windows(W)
    ht_wins, st_wins = [], []
    for d in all_data:
        pt_list   = d["pt_list"]
        goal_list = d["goal_list"]
        goal_mins = [gm for gm, _ in goal_list]
        for gmin, team in goal_list:
            before = [(m, v) for m, v in pt_list if m < gmin]
            if len(before) < W:
                continue
            win = [v for _, v in before[-W:]]
            (ht_wins if gmin <= 45 else st_wins).append(win)

    def win_stats(wins):
        n = len(wins)
        if not n:
            return {"avg": [0.0]*W, "std": [0.0]*W, "n": 0, "tail_mean": 0.0}
        avg = [sum(w[i] for w in wins) / n for i in range(W)]
        std = [math.sqrt(sum((w[i]-avg[i])**2 for w in wins) / max(n-1,1)) for i in range(W)]
        tail_mean = sum(sum(w[-4:])/max(len(w),1) for w in wins) / n
        return {"avg": [round(v,1) for v in avg],
                "std": [round(v,1) for v in std],
                "n": n, "tail_mean": round(tail_mean, 1)}

    # Acerto por categoria com melhor threshold
    goal_n   = len(home_wins) + len(away_wins)
    none_bal = _rand.sample(none_wins, min(len(none_wins), max(goal_n, 1)))
    labeled  = ([(w,"home") for w in home_wins] +
                [(w,"away") for w in away_wins] +
                [(w,"none") for w in none_bal])
    cnt = {"home":[0,0], "away":[0,0], "none":[0,0]}
    for w, lbl in labeled:
        score = feats(w)
        pred  = "home" if score > best_T_overall else ("away" if score < -best_T_overall else "none")
        cnt[lbl][1] += 1; cnt[lbl][0] += int(pred == lbl)

    # ── Probabilidade: P(gol nos próx. LOOKAHEAD min | sinal X) ──────────
    LOOKAHEAD = 10
    prob = {"home": [0,0], "away": [0,0], "any": [0,0], "none": [0,0]}
    for d in all_data:
        pt_list   = d["pt_list"]
        goal_list = d["goal_list"]
        if len(pt_list) < W + 2:
            continue
        for i in range(W, len(pt_list)):
            m_now = pt_list[i][0]
            win   = [pt_list[j][1] for j in range(i - W, i)]
            score = feats(win)
            if score > best_T_overall:
                sig = "home"
            elif score < -best_T_overall:
                sig = "away"
            elif abs(score) > best_T_overall * 0.6:
                sig = "any"
            else:
                sig = "none"
            goals_ahead = [(gm, gt) for gm, gt in goal_list
                           if gm > m_now and gm <= m_now + LOOKAHEAD]
            prob[sig][1] += 1
            if sig == "home":
                if any(gt == "home" for _, gt in goals_ahead):
                    prob[sig][0] += 1
            elif sig == "away":
                if any(gt == "away" for _, gt in goals_ahead):
                    prob[sig][0] += 1
            elif sig == "any":
                if goals_ahead:
                    prob[sig][0] += 1
            else:  # none
                if not goals_ahead:
                    prob[sig][0] += 1

    def sp(a, b): return round(a/b*100, 1) if b else None

    return {
        "patterns": {
            "home": win_stats(home_wins),
            "away": win_stats(away_wins),
            "any":  win_stats(home_wins + away_wins),
            "none": win_stats(none_wins),
            "ht":   win_stats(ht_wins),
            "st":   win_stats(st_wins),
        },
        "threshold":     best_T_overall,
        "accuracy":      round(best_acc_overall * 100, 1),
        "acc_home":      sp(*cnt.get("home", (0,1))),
        "acc_away":      sp(*cnt.get("away", (0,1))),
        "acc_none":      sp(*cnt.get("none", (0,1))),
        "prob_home":     sp(*prob["home"]),
        "prob_away":     sp(*prob["away"]),
        "prob_any":      sp(*prob["any"]),
        "prob_none":     sp(*prob["none"]),
        "prob_lookahead": LOOKAHEAD,
        "total_matches": total_matches,
        "total_goals":   total_goals,
        "window":        W,
        "window_scores": window_scores,
        "computed_at":   datetime.now().isoformat(),
    }


def _rebuild_analysis_cache():
    """Reconstrói o cache de análise e salva em disco. Chamado automaticamente."""
    try:
        result = _compute_analysis()
        with open(ANALYSIS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[analysis] Cache atualizado — {result['total_matches']} partidas, "
              f"acurácia {result['accuracy']}%")
    except Exception as e:
        print(f"[analysis] Erro ao reconstruir cache: {e}")


@app.route("/api/momentum/analysis")
def api_momentum_analysis():
    """Retorna análise de padrões de gol. Usa cache em disco; recalcula se ausente."""
    # Tenta ler cache
    if os.path.exists(ANALYSIS_CACHE_FILE):
        try:
            with open(ANALYSIS_CACHE_FILE, encoding="utf-8") as f:
                return jsonify(json.load(f))
        except Exception:
            pass
    # Cache ausente/corrompido: calcula na hora e salva
    result = _compute_analysis()
    try:
        with open(ANALYSIS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/momentum/similar", methods=["POST"])
def api_momentum_similar():
    """Busca partidas salvas com padrão de momentum similar ao atual."""
    try:
        body    = request.get_json(force=True) or {}
        pts_raw = body.get("points", [])
        W       = int(body.get("window", 8))
        LOOKAHEAD = 10

        if len(pts_raw) < W:
            return jsonify({"similar": [], "total": 0, "goal_home": 0, "goal_away": 0, "goal_none": 0})

        cur_vals = [float(p.get("value", 0)) for p in pts_raw[-W:]]

        def normalize(vals):
            mx = max(abs(v) for v in vals) or 1.0
            return [v / mx for v in vals]

        cur_norm = normalize(cur_vals)

        similar = []
        for fpath in sorted(glob.glob(os.path.join(MOMENTUM_DIR, "*.json"))):
            try:
                with open(fpath, encoding="utf-8") as f:
                    d = json.load(f)
                pt_list = sorted([(float(p["minute"]), float(p["value"]))
                                  for p in d.get("graphPoints", [])
                                  if "minute" in p and "value" in p],
                                 key=lambda x: x[0])
                goals = d.get("goals", [])
                if len(pt_list) < W + 2:
                    continue

                best_dist = float("inf")
                best_outcome = "none"
                best_min = 0

                for i in range(W, len(pt_list)):
                    win = [pt_list[j][1] for j in range(i - W, i)]
                    win_norm = normalize(win)
                    dist = sum((a - b) ** 2 for a, b in zip(cur_norm, win_norm)) ** 0.5
                    if dist < best_dist:
                        best_dist = dist
                        m_now = pt_list[i][0]
                        ahead = [(float(g.get("minute", 0)), g.get("team", ""))
                                 for g in goals
                                 if float(g.get("minute", 0)) > m_now
                                 and float(g.get("minute", 0)) <= m_now + LOOKAHEAD]
                        if any(t == "home" for _, t in ahead):
                            best_outcome = "home"
                        elif any(t == "away" for _, t in ahead):
                            best_outcome = "away"
                        elif ahead:
                            best_outcome = "any"
                        else:
                            best_outcome = "none"
                        best_min = int(m_now)

                similar.append({
                    "casa":     d.get("casa", "—"),
                    "fora":     d.get("fora", "—"),
                    "liga":     d.get("liga", ""),
                    "date":     d.get("date", ""),
                    "outcome":  best_outcome,
                    "distance": round(best_dist, 3),
                    "minute":   best_min,
                })
            except Exception:
                continue

        similar.sort(key=lambda x: x["distance"])
        top = similar[:5]
        return jsonify({
            "similar":   top,
            "total":     len(similar),
            "goal_home": sum(1 for s in top if s["outcome"] == "home"),
            "goal_away": sum(1 for s in top if s["outcome"] == "away"),
            "goal_none": sum(1 for s in top if s["outcome"] == "none"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Uniscore / unik8s ────────────────────────────────────────────────────────

UNISCORE_HEADERS = {
    "Origin":       "https://uniscore.com",
    "Referer":      "https://uniscore.com/pt-BR/",
    "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":       "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Content-Type": "application/json",
}
UNISCORE_BASE = "https://api.unik8s.com/api/v2"

# Cache da lista de partidas do dia (30s TTL)
_uni_events_cache = {"ts": 0, "data": []}

def _uni_events_today():
    """Retorna lista de eventos de futebol do dia (cache 30s)."""
    global _uni_events_cache
    if time.time() - _uni_events_cache["ts"] < 30 and _uni_events_cache["data"]:
        return _uni_events_cache["data"]
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        r = http_req.post(
            f"{UNISCORE_BASE}/sport/football/scheduled-events-pagination-v2/{today}/locale/BR/type/all?language=pt-BR",
            json={}, headers=UNISCORE_HEADERS, timeout=8
        )
        events = r.json().get("data", {}).get("events", [])
        _uni_events_cache = {"ts": time.time(), "data": events}
        return events
    except Exception:
        return []

def _uni_find(casa, fora):
    """Encontra evento Uniscore pelo nome dos times (fuzzy com remoção de acentos)."""
    import unicodedata

    def norm(s):
        """Normaliza: minúsculo, sem acentos, sem pontuação."""
        s = s.lower()
        s = unicodedata.normalize("NFD", s)
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")  # remove diacritics
        s = re.sub(r"[^a-z0-9 ]", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    def words(s):
        """Conjunto de palavras significativas (>= 3 letras)."""
        return {w for w in norm(s).split() if len(w) >= 3}

    def score_team(query, candidate):
        """Pontuação de similaridade entre dois nomes de time."""
        qn = norm(query)
        cn = norm(candidate)
        # Prefixo dos primeiros 5 chars
        prefix_match = qn[:5] == cn[:5] and len(qn) >= 4
        # Interseção de palavras
        qw = words(query)
        cw = words(candidate)
        common = qw & cw
        word_score = len(common) / max(len(qw), 1)
        # Substring bidirecional
        substr = (qn[:8] in cn) or (cn[:8] in qn)
        return (3 if prefix_match else 0) + (word_score * 2) + (1 if substr else 0)

    best = None
    best_score = 0.0
    for ev in _uni_events_today():
        hn = ev.get("homeTeam", {}).get("name", "")
        an = ev.get("awayTeam", {}).get("name", "")
        sc = score_team(casa, hn) + score_team(fora, an)
        if sc > best_score:
            best_score = sc
            best = ev
    # Threshold mínimo: pelo menos um nome com score >= 2
    if best_score < 2.0:
        return None
    return best

def _uni_enrich_one(casa, fora):
    """Busca e retorna dados Uniscore para um par casa/fora. Retorna dict."""
    ev = _uni_find(casa, fora)
    if not ev:
        return {"found": False, "casa": casa, "fora": fora}

    eid = ev.get("id")
    result = {
        "found":  True,
        "id":     eid,
        "casa":   ev.get("homeTeam", {}).get("name"),
        "fora":   ev.get("awayTeam", {}).get("name"),
        "status": ev.get("status", {}).get("description"),
        "minuto": ev.get("time", {}).get("current"),
        "placar": {
            "casa": ev.get("homeScore", {}).get("current"),
            "fora": ev.get("awayScore", {}).get("current"),
        },
        "stats": {
            "escanteios_casa": ev.get("homeCornerKicks", 0),
            "escanteios_fora": ev.get("awayCornerKicks", 0),
            "chutes_casa":     ev.get("homeShotOnTarget", 0),
            "chutes_fora":     ev.get("awayShotOnTarget", 0),
            "amarelos_casa":   ev.get("homeYellowCards", 0),
            "amarelos_fora":   ev.get("awayYellowCards", 0),
            "vermelhos_casa":  ev.get("homeRedCards", 0),
            "vermelhos_fora":  ev.get("awayRedCards", 0),
        },
    }

    # Detalhes: clima, árbitro, estádio
    try:
        r = http_req.get(f"{UNISCORE_BASE}/football/event/{eid}?language=pt-BR",
                         headers=UNISCORE_HEADERS, timeout=6)
        d = r.json().get("data", {}).get("event", {})
        result["clima"]   = d.get("environment")
        result["arbitro"] = d.get("referee", {}).get("name") if isinstance(d.get("referee"), dict) else d.get("referee")
        result["estadio"] = d.get("venue", {}).get("name") if isinstance(d.get("venue"), dict) else None
    except Exception:
        pass

    # Incidentes
    try:
        r = http_req.get(f"{UNISCORE_BASE}/football/event/{eid}/incidents?language=pt-BR",
                         headers=UNISCORE_HEADERS, timeout=6)
        incs = r.json().get("data", {}).get("incidents", [])
        result["incidents"] = [
            {
                "type":    i.get("incidentType"),
                "class":   i.get("incidentClass"),
                "minute":  i.get("time"),
                "player":  i.get("player", {}).get("name"),
                "assist":  i.get("assist1", {}).get("name") if i.get("assist1") else None,
                "isHome":  i.get("isHome"),
                "score_h": i.get("homeScore"),
                "score_a": i.get("awayScore"),
            }
            for i in (incs or [])
        ]
    except Exception:
        result["incidents"] = []

    # Forma recente
    try:
        r = http_req.get(f"{UNISCORE_BASE}/football/event/{eid}/recent-form?language=pt-BR",
                         headers=UNISCORE_HEADERS, timeout=6)
        d = r.json().get("data", {})
        def parse_form(matches):
            out = []
            for m in (matches or []):
                hs = m.get("homeScore", {})
                as_ = m.get("awayScore", {})
                out.append({
                    "home":   m.get("homeTeam", {}).get("name"),
                    "away":   m.get("awayTeam", {}).get("name"),
                    "score":  f"{hs.get('current',0)}-{as_.get('current',0)}",
                    "status": m.get("status", {}).get("type"),
                })
            return out
        result["forma_casa"] = parse_form(d.get("home", {}).get("latest_matches", []))
        result["forma_fora"] = parse_form(d.get("away", {}).get("latest_matches", []))
    except Exception:
        result["forma_casa"] = []
        result["forma_fora"] = []

    # Jogador destaque
    try:
        r = http_req.get(f"{UNISCORE_BASE}/sport/football/events/{eid}/top-players?language=pt-BR",
                         headers=UNISCORE_HEADERS, timeout=6)
        d = r.json().get("data", {})
        def parse_player(p):
            if not p: return None
            return {
                "name":   p.get("name"),
                "pos":    p.get("position"),
                "rating": p.get("rating"),
                "attrs":  p.get("attributes", {}),
            }
        result["top_casa"] = parse_player(d.get("home_player"))
        result["top_fora"] = parse_player(d.get("away_player"))
    except Exception:
        result["top_casa"] = None
        result["top_fora"] = None

    return result


@app.route("/api/uniscore/enrich")
def api_uniscore_enrich():
    """Retorna dados enriquecidos do Uniscore para uma partida ao vivo."""
    casa = request.args.get("casa", "")
    fora = request.args.get("fora", "")
    if not casa or not fora:
        return jsonify({"error": "casa e fora obrigatórios"}), 400
    return jsonify(_uni_enrich_one(casa, fora))


@app.route("/api/uniscore/live-all", methods=["POST"])
def api_uniscore_live_all():
    """Recebe lista [{casa, fora}] e retorna enriquecimento paralelo de todas."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    body = request.get_json(force=True, silent=True) or {}
    partidas = body.get("partidas", [])
    if not partidas:
        return jsonify([])

    # Pré-aquece cache de eventos (1 chamada para todas)
    _uni_events_today()

    results = [None] * len(partidas)
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut_map = {
            ex.submit(_uni_enrich_one, p.get("casa", ""), p.get("fora", "")): i
            for i, p in enumerate(partidas)
        }
        for fut in as_completed(fut_map):
            idx = fut_map[fut]
            try:
                results[idx] = fut.result()
            except Exception:
                results[idx] = {"found": False,
                                "casa": partidas[idx].get("casa", ""),
                                "fora": partidas[idx].get("fora", "")}

    return jsonify(results)


@app.route("/api/fotmob/live")
def api_fotmob_live():
    """Busca jogos ao vivo diretamente via FotMob /api/data/matches (sem bloqueio)."""
    try:
        r = http_req.get(
            "https://www.fotmob.com/api/data/matches",
            headers=FOTMOB_HEADERS,
            params={"timezone": "America/Sao_Paulo", "ccode3": "BRA"},
            timeout=12
        )
        r.raise_for_status()
        data = r.json()

        live = []
        for league in data.get("leagues", []):
            for match in league.get("matches", []):
                status   = match.get("status", {})
                started  = status.get("started", False)
                finished = status.get("finished", False)
                if not started or finished:
                    continue
                home = match.get("home", {})
                away = match.get("away", {})
                live.append({
                    "id":        str(match.get("id", "")),
                    "home":      home.get("name", ""),
                    "away":      away.get("name", ""),
                    "homeScore": home.get("score"),
                    "awayScore": away.get("score"),
                    "minute":    status.get("liveTime", {}).get("short", ""),
                    "league":    league.get("name", ""),
                    "country":   league.get("ccode", ""),
                })
        return jsonify({"live": live, "total": len(live)})
    except Exception as e:
        return jsonify({"error": str(e), "live": [], "total": 0}), 503


@app.route("/api/fotmob/match/<match_id>")
def api_fotmob_match_detail(match_id):
    """Busca detalhes completos via Playwright Chrome (bypassa CF — inclui momentum)."""
    from playwright.sync_api import sync_playwright

    result = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                channel="chrome",
                headless=False,
                args=["--window-position=-32000,-32000", "--window-size=1,1"]
            )
            ctx  = browser.new_context(locale="pt-BR")
            page = ctx.new_page()

            def on_resp(resp):
                if "matchDetails" in resp.url and not result:
                    try:
                        result["data"] = resp.json()
                    except Exception:
                        pass

            page.on("response", on_resp)
            page.goto(
                f"https://www.fotmob.com/match/{match_id}",
                wait_until="domcontentloaded",
                timeout=25000
            )
            # Aguarda a chamada assíncrona do matchDetails
            for _ in range(30):
                if result:
                    break
                page.wait_for_timeout(300)
            browser.close()

        if not result:
            return jsonify({"error": "matchDetails não capturado"}), 503

        raw = result["data"]

        # ── Header ──
        teams  = raw.get("header", {}).get("teams", [])
        status = raw.get("header", {}).get("status", {})
        home_t = teams[0] if len(teams) > 0 else {}
        away_t = teams[1] if len(teams) > 1 else {}
        gen    = raw.get("general", {})

        # ── Momentum ──
        momentum = raw.get("content", {}).get("matchFacts", {}).get("momentum", {})
        mom_data = momentum.get("main", {}).get("data", [])

        # ── Stats ──
        stats_raw = raw.get("content", {}).get("matchFacts", {}).get("stats", {})
        stats_out = []
        for block in stats_raw.get("stats", []):
            for stat in block.get("stats", []):
                vals = stat.get("stats", [])
                if len(vals) >= 2:
                    stats_out.append({
                        "title": stat.get("title", ""),
                        "home":  str(vals[0]),
                        "away":  str(vals[1]),
                    })

        # ── Escalações ──
        lineup_raw = raw.get("content", {}).get("lineup", {}).get("lineup", [])
        lineup_out = {"home": [], "away": []}
        if lineup_raw:
            block = lineup_raw[0]
            players = block.get("players", [[], []])
            for i, side in enumerate(["home", "away"]):
                for p in players[i] if i < len(players) else []:
                    lineup_out[side].append({
                        "name":    p.get("name", {}).get("lastName") or p.get("name", {}).get("fullName", ""),
                        "shirt":   p.get("shirt", ""),
                        "pos":     p.get("position", ""),
                        "starter": True,
                    })

        # ── Odds ──
        odds_out = {}
        for market in raw.get("content", {}).get("odds", {}).get("parser", []):
            mname = market.get("name", "")
            odds  = market.get("odds", [{}])[0] if market.get("odds") else {}
            if "1x2" in mname.lower() or "match" in mname.lower():
                odds_out = {
                    "home": odds.get("homeOdds") or odds.get("1"),
                    "draw": odds.get("drawOdds") or odds.get("X"),
                    "away": odds.get("awayOdds") or odds.get("2"),
                }
                break

        return jsonify({
            "home":       home_t.get("name", ""),
            "away":       away_t.get("name", ""),
            "homeScore":  home_t.get("score"),
            "awayScore":  away_t.get("score"),
            "minute":     status.get("liveTime", {}).get("short", ""),
            "league":     gen.get("leagueName", ""),
            "momentum":   mom_data,
            "stats":      stats_out,
            "lineup":     lineup_out,
            "odds":       odds_out,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/match/live/<path:match_id>")
def api_match_live(match_id):
    """Busca detalhes ao vivo do StatArea para qualquer partida."""
    from scraper import fetch_match_details, create_session
    url = f"https://www.statarea.com/compare/teams/{match_id}"
    try:
        session = create_session()
        details = fetch_match_details(url, session=session)
        return jsonify({"detalhes": details, "url_detalhes": url})
    except Exception as e:
        abort(503)


@app.route("/code")
@app.route("/code/<path:filepath>")
def code_viewer(filepath=None):
    base = os.path.dirname(__file__)
    files = {
        "app.py": os.path.join(base, "app.py"),
        "scraper.py": os.path.join(base, "scraper.py"),
        "static/index.html": os.path.join(base, "static", "index.html"),
    }
    if filepath and filepath in files:
        path = files[filepath]
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            content = f"Erro ao ler arquivo: {e}"
        ext = filepath.rsplit(".", 1)[-1]
        lang = {"py": "python", "html": "html", "js": "javascript"}.get(ext, "plaintext")
        size = os.path.getsize(path)
        lines = content.count("\n") + 1
        return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Código — {filepath}</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #1a1a2e; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; min-height: 100vh; }}
  .topbar {{ background: #16213e; border-bottom: 1px solid #0f3460; padding: 12px 20px; display: flex; align-items: center; gap: 16px; position: sticky; top: 0; z-index: 10; }}
  .topbar a {{ color: #4ecca3; text-decoration: none; font-size: 14px; }}
  .topbar a:hover {{ text-decoration: underline; }}
  .filename {{ font-size: 16px; font-weight: 600; color: #fff; }}
  .meta {{ font-size: 12px; color: #888; margin-left: auto; }}
  .code-wrap {{ padding: 20px; }}
  pre {{ border-radius: 8px; font-size: 13px; line-height: 1.6; overflow-x: auto; }}
  pre code {{ font-family: 'Consolas', 'Monaco', monospace; }}
  .copy-btn {{ background: #0f3460; border: 1px solid #4ecca3; color: #4ecca3; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; }}
  .copy-btn:hover {{ background: #4ecca3; color: #1a1a2e; }}
  .separator {{ color: #444; }}
</style>
</head>
<body>
<div class="topbar">
  <a href="/code">← Arquivos</a>
  <span class="separator">|</span>
  <span class="filename">📄 {filepath}</span>
  <button class="copy-btn" onclick="copyCode()">Copiar</button>
  <span class="meta">{lines} linhas &nbsp;·&nbsp; {size:,} bytes</span>
</div>
<div class="code-wrap">
  <pre><code class="language-{lang}" id="codeblock">{content.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')}</code></pre>
</div>
<script>
  hljs.highlightAll();
  function copyCode() {{
    navigator.clipboard.writeText(document.getElementById('codeblock').innerText);
    const btn = document.querySelector('.copy-btn');
    btn.textContent = 'Copiado!';
    setTimeout(() => btn.textContent = 'Copiar', 2000);
  }}
</script>
</body>
</html>"""

    # Página índice dos arquivos
    file_info = []
    for name, path in files.items():
        try:
            size = os.path.getsize(path)
            lines = sum(1 for _ in open(path, encoding="utf-8"))
            icon = "🐍" if name.endswith(".py") else "🌐"
        except:
            size, lines, icon = 0, 0, "📄"
        file_info.append((name, size, lines, icon))

    cards = ""
    for name, size, lines, icon in file_info:
        cards += f"""
        <a href="/code/{name}" class="card">
          <div class="card-icon">{icon}</div>
          <div class="card-info">
            <div class="card-name">{name}</div>
            <div class="card-meta">{lines} linhas · {size:,} bytes</div>
          </div>
        </a>"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Visualizador de Código — Gol em Números</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #1a1a2e; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; min-height: 100vh; }}
  .header {{ background: #16213e; border-bottom: 1px solid #0f3460; padding: 20px; text-align: center; }}
  .header h1 {{ font-size: 22px; color: #4ecca3; }}
  .header p {{ font-size: 13px; color: #888; margin-top: 4px; }}
  .grid {{ display: flex; flex-direction: column; gap: 12px; padding: 30px; max-width: 600px; margin: 0 auto; }}
  .card {{ display: flex; align-items: center; gap: 16px; background: #16213e; border: 1px solid #0f3460; border-radius: 10px; padding: 16px 20px; text-decoration: none; color: inherit; transition: border-color .2s, background .2s; }}
  .card:hover {{ border-color: #4ecca3; background: #1e2d50; }}
  .card-icon {{ font-size: 28px; }}
  .card-name {{ font-size: 16px; font-weight: 600; color: #fff; }}
  .card-meta {{ font-size: 12px; color: #888; margin-top: 3px; }}
  .back {{ display: inline-block; margin: 20px 30px 0; color: #4ecca3; text-decoration: none; font-size: 14px; }}
  .back:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div class="header">
  <h1>📂 Visualizador de Código</h1>
  <p>Gol em Números — StatArea Dashboard</p>
</div>
<a href="/" class="back">← Voltar ao painel</a>
<div class="grid">{cards}</div>
</body>
</html>"""


if __name__ == "__main__":
    print("Servidor rodando em http://localhost:5000")
    app.run(debug=False, port=5000, use_reloader=False)

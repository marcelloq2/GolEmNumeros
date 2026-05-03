"""
Servidor Flask — API + frontend para exibir dados do StatArea
"""
from flask import Flask, jsonify, send_from_directory, abort, request
import json, os, glob, re, threading, time
import requests as http_req
from datetime import datetime
import github_storage

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


def _shift_hora(hora_str, delta_hours):
    """Soma delta_hours ao horário HH:MM. Retorna novo HH:MM ou original."""
    from datetime import datetime, timedelta
    if not hora_str:
        return hora_str
    try:
        t = datetime.strptime(hora_str.strip(), "%H:%M") + timedelta(hours=delta_hours)
        return t.strftime("%H:%M")
    except Exception:
        return hora_str


FLAG_FILE = os.path.join(DATA_DIR, ".times_adjusted")


def _flag_today():
    """Retorna a data gravada no flag, ou '' se não existir."""
    try:
        with open(FLAG_FILE, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


@app.route("/api/fix-times/status")
def api_fix_times_status():
    """Verifica se o ajuste já foi aplicado hoje."""
    today = datetime.now().strftime("%Y-%m-%d")
    done  = _flag_today() == today
    return jsonify({"done": done, "date": today})


@app.route("/api/fix-times", methods=["POST"])
def api_fix_times():
    """Adiciona +3h nos horários do predictions JSON — só uma vez por dia."""
    today = datetime.now().strftime("%Y-%m-%d")

    # Bloqueia segunda execução no mesmo dia
    if _flag_today() == today:
        return jsonify({"ok": False, "already_done": True,
                        "msg": "Horários já foram ajustados hoje."})

    body  = request.get_json(force=True, silent=True) or {}
    delta = int(body.get("delta", 3))

    matches, source = load_predictions()
    if not matches:
        return jsonify({"ok": False, "error": "Nenhum arquivo de partidas encontrado"}), 404

    fname = source or "predictions_full.json"
    path  = os.path.join(DATA_DIR, fname)

    updated = 0
    for m in matches:
        if m.get("hora"):
            m["hora"] = _shift_hora(m["hora"], delta)
            updated += 1

    with open(path, "w", encoding="utf-8") as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)

    # Grava flag com a data de hoje
    with open(FLAG_FILE, "w") as f:
        f.write(today)

    return jsonify({"ok": True, "updated": updated, "delta": delta})


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
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--window-size=1280,720",
                ]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                java_script_enabled=True,
            )
            page = ctx.new_page()
            # Esconde sinais de automação
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page.goto("https://www.sofascore.com/", timeout=25000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            result = page.evaluate(f"""async () => {{
                try {{
                    const [rGraph, rInc, rStats] = await Promise.all([
                        fetch('/api/v1/event/{event_id}/graph'),
                        fetch('/api/v1/event/{event_id}/incidents'),
                        fetch('/api/v1/event/{event_id}/statistics')
                    ]);
                    const graph = rGraph.ok  ? await rGraph.json()  : {{}};
                    const inc   = rInc.ok    ? await rInc.json()    : {{}};
                    const stats = rStats.ok  ? await rStats.json()  : {{}};
                    return {{status: 200, graph, incidents: inc, statistics: stats}};
                }} catch(e) {{
                    return {{error: e.message}};
                }}
            }}""")
            browser.close()

        if result.get("status") != 200:
            return None

        graph      = result.get("graph", {})
        incidents  = result.get("incidents", {})
        statistics = result.get("statistics", {})
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
                # ── Odds de abertura (snapshot pré-jogo do Uniscore) ──
                opening_odds = {}
                try:
                    uni_ev = _uni_find(casa, fora)
                    if uni_ev:
                        uni_eid  = uni_ev.get("id")
                        uni_odds = _uni_odds_today().get(uni_eid, {})
                        if uni_odds:
                            opening_odds = {
                                "h":        uni_odds.get("h"),
                                "x":        uni_odds.get("x"),
                                "a":        uni_odds.get("a"),
                                "ou_line":  uni_odds.get("ou_line"),
                                "ou_over":  uni_odds.get("ou_over"),
                                "ou_under": uni_odds.get("ou_under"),
                            }
                except Exception:
                    pass

                payload = {
                    "event_id":     event_id,
                    "date":         today,
                    "saved_at":     datetime.now().isoformat(),
                    "casa":         casa,
                    "fora":         fora,
                    "liga":         liga,
                    "graphPoints":  graph.get("graphPoints", []),
                    "goals":        goals_uniq,
                    "statistics":   statistics.get("statistics", []),
                    "opening_odds": opening_odds,
                }
                with open(save_file, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                saved = True
                print(f"[momentum] Salvo: {save_file}")
                # Persiste no GitHub para sobreviver a reinicializações
                github_storage.push_file_bg(save_file, f"momentum_history/{today}_{event_id}.json")
                # Invalida caches que dependem do histórico
                global _pattern_tips_cache, _odds_patterns_cache, _stats_patterns_cache
                _pattern_tips_cache  = {"ts": 0, "data": None}
                _odds_patterns_cache = {"ts": 0, "data": None}
                _stats_patterns_cache = {"ts": 0, "data": None}
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

# Restaura dados do GitHub ao iniciar (backtest + momentum_history + predictions)
threading.Thread(
    target=github_storage.sync_on_startup,
    args=(MOMENTUM_DIR, BACKTEST_DIR, DATA_DIR),
    daemon=True,
    name="GitHubSync"
).start()


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
    """Computa padrões de gol com janela ótima INDIVIDUAL por categoria (3-15 min)."""
    import math, random as _rand

    NONE_GAP  = 12
    NONE_STEP = 3
    CANDIDATES = list(range(3, 16))

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

    # ── Pré-extrai todas as janelas para todos os W candidatos ────────────
    wins_cache = {}
    for W in CANDIDATES:
        hw, aw, nw, ht_w, st_w = [], [], [], [], []
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
                (ht_w if gmin <= 45 else st_w).append(win)
            for i in range(W, len(pt_list) - 1, NONE_STEP):
                m_now = pt_list[i][0]
                if any(abs(gm - m_now) <= NONE_GAP for gm in goal_mins):
                    continue
                nw.append([pt_list[j][1] for j in range(i - W, i)])
        wins_cache[W] = (hw, aw, nw, ht_w, st_w)

    # ── Busca janela+threshold ótimos para uma categoria específica ───────
    def find_best_for(pos_fn, neg_fn, direction="pos"):
        """
        direction: "pos"  → score > T prediz positivo (gol casa)
                   "neg"  → score < -T prediz positivo (gol visitante)
                   "any"  → |score| > T prediz positivo (qualquer gol / 1T / 2T)
                   "none" → |score| < T prediz positivo (sem gol)
        """
        best_W = CANDIDATES[0]; best_acc = 0.0; best_T = 8
        for W in CANDIDATES:
            pos = pos_fn(W)
            neg = neg_fn(W)
            if len(pos) < 4:
                continue
            neg_bal = _rand.sample(neg, min(len(neg), max(len(pos), 1)))
            labeled = [(w, True) for w in pos] + [(w, False) for w in neg_bal]
            for T in range(1, 60):
                if direction == "pos":
                    correct = sum(1 for w, lbl in labeled if (feats(w) > T) == lbl)
                elif direction == "neg":
                    correct = sum(1 for w, lbl in labeled if (feats(w) < -T) == lbl)
                elif direction == "any":
                    correct = sum(1 for w, lbl in labeled if (abs(feats(w)) > T) == lbl)
                else:  # none
                    correct = sum(1 for w, lbl in labeled if (abs(feats(w)) <= T) == lbl)
                bal = correct / max(len(labeled), 1)
                if bal > best_acc:
                    best_acc = bal; best_T = T; best_W = W
        return best_W, best_T, round(best_acc * 100, 1)

    # Janela ótima individual por categoria
    home_W, home_T, home_acc = find_best_for(
        lambda W: wins_cache[W][0], lambda W: wins_cache[W][2], "pos")
    away_W, away_T, away_acc = find_best_for(
        lambda W: wins_cache[W][1], lambda W: wins_cache[W][2], "neg")
    any_W,  any_T,  any_acc  = find_best_for(
        lambda W: wins_cache[W][0] + wins_cache[W][1], lambda W: wins_cache[W][2], "any")
    none_W, none_T, none_acc = find_best_for(
        lambda W: wins_cache[W][2], lambda W: wins_cache[W][0] + wins_cache[W][1], "none")
    ht_W,   ht_T,   ht_acc   = find_best_for(
        lambda W: wins_cache[W][3], lambda W: wins_cache[W][2], "any")
    st_W,   st_T,   st_acc   = find_best_for(
        lambda W: wins_cache[W][4], lambda W: wins_cache[W][2], "any")

    # Janela global (para o modelo geral e prob)
    window_scores = {}
    best_window = 8; best_acc_overall = 0.0; best_T_overall = 8
    for W in CANDIDATES:
        hw, aw, nw, _, _ = wins_cache[W]
        goal_n = len(hw) + len(aw)
        if goal_n < 4:
            window_scores[W] = 0.0; continue
        none_bal = _rand.sample(nw, min(len(nw), max(goal_n, 1)))
        labeled  = ([(w,"home") for w in hw] + [(w,"away") for w in aw] +
                    [(w,"none") for w in none_bal])
        best_bal = 0.0; T_found = 8
        for T in range(1, 60):
            cnt = {"home":[0,0],"away":[0,0],"none":[0,0]}
            for w, lbl in labeled:
                score = feats(w)
                pred  = "home" if score > T else ("away" if score < -T else "none")
                cnt[lbl][1] += 1; cnt[lbl][0] += int(pred == lbl)
            recalls = [cnt[k][0]/cnt[k][1] for k in cnt if cnt[k][1]]
            bal = sum(recalls)/len(recalls) if recalls else 0
            if bal > best_bal:
                best_bal = bal; T_found = T
        window_scores[W] = round(best_bal * 100, 1)
        if best_bal > best_acc_overall:
            best_acc_overall = best_bal; best_window = W; best_T_overall = T_found

    # ── win_stats com W dinâmico por categoria ────────────────────────────
    def win_stats(wins, W):
        n = len(wins)
        if not n:
            return {"avg": [0.0]*W, "std": [0.0]*W, "n": 0, "tail_mean": 0.0}
        avg = [sum(w[i] for w in wins) / n for i in range(W)]
        std = [math.sqrt(sum((w[i]-avg[i])**2 for w in wins)/max(n-1,1)) for i in range(W)]
        tail_mean = sum(sum(w[-4:])/max(len(w),1) for w in wins) / n
        return {"avg": [round(v,1) for v in avg],
                "std": [round(v,1) for v in std],
                "n": n, "tail_mean": round(tail_mean, 1)}

    home_wins = wins_cache[home_W][0]
    away_wins = wins_cache[away_W][1]
    any_wins  = wins_cache[any_W][0]  + wins_cache[any_W][1]
    none_wins = wins_cache[none_W][2]
    ht_wins   = wins_cache[ht_W][3]
    st_wins   = wins_cache[st_W][4]

    # ── Probabilidade: P(gol nos próx. LOOKAHEAD min | sinal X) ──────────
    LOOKAHEAD = 10
    W = best_window
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
            if score > best_T_overall:               sig = "home"
            elif score < -best_T_overall:            sig = "away"
            elif abs(score) > best_T_overall * 0.6: sig = "any"
            else:                                    sig = "none"
            goals_ahead = [(gm, gt) for gm, gt in goal_list
                           if gm > m_now and gm <= m_now + LOOKAHEAD]
            prob[sig][1] += 1
            if sig == "home"  and any(gt=="home"  for _,gt in goals_ahead): prob[sig][0] += 1
            elif sig == "away" and any(gt=="away" for _,gt in goals_ahead): prob[sig][0] += 1
            elif sig == "any"  and goals_ahead:                             prob[sig][0] += 1
            elif sig == "none" and not goals_ahead:                         prob[sig][0] += 1

    def sp(a, b): return round(a/b*100, 1) if b else None

    per_cat = {
        "home": {"window": home_W, "threshold": home_T, "accuracy": home_acc},
        "away": {"window": away_W, "threshold": away_T, "accuracy": away_acc},
        "any":  {"window": any_W,  "threshold": any_T,  "accuracy": any_acc},
        "none": {"window": none_W, "threshold": none_T, "accuracy": none_acc},
        "ht":   {"window": ht_W,   "threshold": ht_T,   "accuracy": ht_acc},
        "st":   {"window": st_W,   "threshold": st_T,   "accuracy": st_acc},
    }

    return {
        "patterns": {
            "home": win_stats(home_wins, home_W),
            "away": win_stats(away_wins, away_W),
            "any":  win_stats(any_wins,  any_W),
            "none": win_stats(none_wins, none_W),
            "ht":   win_stats(ht_wins,   ht_W),
            "st":   win_stats(st_wins,   st_W),
        },
        "per_category":  per_cat,
        "threshold":     best_T_overall,
        "accuracy":      round(best_acc_overall * 100, 1),
        "acc_home":      home_acc,
        "acc_away":      away_acc,
        "acc_none":      none_acc,
        "prob_home":     sp(*prob["home"]),
        "prob_away":     sp(*prob["away"]),
        "prob_any":      sp(*prob["any"]),
        "prob_none":     sp(*prob["none"]),
        "prob_lookahead": LOOKAHEAD,
        "total_matches": total_matches,
        "total_goals":   total_goals,
        "window":        best_window,
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


# ── Padrões de Estatísticas ──────────────────────────────────────────────────

# Estatísticas que queremos rastrear (key SofaScore → label PT-BR)
_STAT_LABELS = {
    "ballPossession":     "Posse de Bola (Casa %)",
    "shotsOnTarget":      "Chutes no Alvo",
    "totalShots":         "Total de Chutes",
    "cornerKicks":        "Escanteios",
    "yellowCards":        "Cartões Amarelos",
    "saves":              "Defesas (GK)",
    "bigChancesCreated":  "Grandes Chances",
    "foulsCommitted":     "Faltas Cometidas",
    "totalPasses":        "Passes Totais",
    "tacklesWon":         "Desarmes",
}

_stats_patterns_cache = {"ts": 0, "data": None}


def _parse_stat_val(raw):
    """Converte string (ex: '57%', '3') ou número para float. None se inválido."""
    if raw is None:
        return None
    try:
        return float(str(raw).replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def _extract_stats(statistics_list):
    """Extrai {key: (home_val, away_val)} do período 'ALL'."""
    result = {}
    for period_data in statistics_list:
        if period_data.get("period") != "ALL":
            continue
        for group in period_data.get("groups", []):
            for item in group.get("statisticsItems", []):
                key = item.get("key", "")
                if not key:
                    continue
                hv = _parse_stat_val(item.get("homeValue") or item.get("home"))
                av = _parse_stat_val(item.get("awayValue") or item.get("away"))
                if hv is not None or av is not None:
                    result[key] = (hv, av)
        break  # só período ALL
    return result


@app.route("/api/momentum/stats-patterns")
def api_stats_patterns():
    """Analisa padrões das estatísticas finais das partidas salvas,
    agrupando por resultado (Casa V., Empate, Vis. V., Over/Under 2.5, BTS)."""
    global _stats_patterns_cache
    if (time.time() - _stats_patterns_cache["ts"] < 1800
            and _stats_patterns_cache["data"] is not None):
        return jsonify(_stats_patterns_cache["data"])

    OUTCOME_KEYS = ["casaV", "emp", "visV", "o25", "u25", "btts", "nbtts"]
    # {outcome: {stat_key_h|_a: [values]}}
    buckets = {oc: {} for oc in OUTCOME_KEYS}
    total = 0

    for fpath in glob.glob(os.path.join(MOMENTUM_DIR, "*.json")):
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            stats_raw = d.get("statistics", [])
            if not stats_raw:
                continue
            stat_vals = _extract_stats(stats_raw)
            if not stat_vals:
                continue

            goals = d.get("goals", [])
            gh  = sum(1 for g in goals if g.get("team") == "home")
            ga  = sum(1 for g in goals if g.get("team") == "away")
            tot = gh + ga

            active = set()
            if gh > ga:            active.add("casaV")
            elif ga > gh:          active.add("visV")
            else:                  active.add("emp")
            active.add("o25" if tot > 2 else "u25")
            active.add("btts" if gh >= 1 and ga >= 1 else "nbtts")

            for oc in active:
                for key, (hv, av) in stat_vals.items():
                    if hv is not None:
                        buckets[oc].setdefault(key + "_h", []).append(hv)
                    if av is not None:
                        buckets[oc].setdefault(key + "_a", []).append(av)
            total += 1
        except Exception:
            continue

    # Calcula médias e contagens
    outcomes_result = {}
    for oc, stats in buckets.items():
        if not stats:
            outcomes_result[oc] = {"n": 0}
            continue
        entry = {"n": 0}
        for k, vals in stats.items():
            if vals:
                entry[k] = round(sum(vals) / len(vals), 1)
                entry["n"] = max(entry["n"], len(vals))
        outcomes_result[oc] = entry

    # Detecta quais chaves de stats existem em pelo menos 1 outcome
    all_keys = set()
    for oc_data in outcomes_result.values():
        all_keys.update(k for k in oc_data if k not in ("n",))
    stat_keys_found = sorted(all_keys)

    result = {
        "total":        total,
        "outcomes":     outcomes_result,
        "stat_keys":    stat_keys_found,
        "stat_labels":  _STAT_LABELS,
        "computed_at":  datetime.now().isoformat(),
    }
    _stats_patterns_cache = {"ts": time.time(), "data": result}
    return jsonify(result)


# ── Correlação de Odds de Abertura ──────────────────────────────────────────

_ODDS_BUCKETS_H = [
    ("1.01–1.30", 1.01, 1.30),
    ("1.31–1.60", 1.31, 1.60),
    ("1.61–2.00", 1.61, 2.00),
    ("2.01–3.00", 2.01, 3.00),
    ("3.01–5.00", 3.01, 5.00),
    (">5.00",     5.01, 99.0),
]

_odds_patterns_cache = {"ts": 0, "data": None}

@app.route("/api/momentum/odds-patterns")
def api_odds_patterns():
    """Correlaciona faixas de odd de abertura (Casa 1X2) com resultados reais.
    Retorna edge vs. probabilidade implícita por faixa."""
    global _odds_patterns_cache
    if (time.time() - _odds_patterns_cache["ts"] < 1800
            and _odds_patterns_cache["data"] is not None):
        return jsonify(_odds_patterns_cache["data"])

    # {label: {n, casaV, emp, visV, o25, u25, sum_imp_h, sum_imp_x, sum_imp_a}}
    bkts = {
        lbl: {"n": 0, "casaV": 0, "emp": 0, "visV": 0,
              "o25": 0, "u25": 0,
              "sum_imp_h": 0.0, "sum_imp_x": 0.0, "sum_imp_a": 0.0}
        for lbl, _, _ in _ODDS_BUCKETS_H
    }
    total = 0

    for fpath in glob.glob(os.path.join(MOMENTUM_DIR, "*.json")):
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            oo = d.get("opening_odds", {})
            if not oo or not oo.get("h"):
                continue
            try:
                h_odd = float(oo["h"])
                x_odd = float(oo.get("x") or 0)
                a_odd = float(oo.get("a") or 0)
            except (ValueError, TypeError):
                continue
            if h_odd <= 0:
                continue

            goals = d.get("goals", [])
            gc = sum(1 for g in goals if g.get("team") == "home")
            gf = sum(1 for g in goals if g.get("team") == "away")
            outcome = "casaV" if gc > gf else "visV" if gf > gc else "emp"
            is_o25  = 1 if gc + gf > 2 else 0

            for lbl, lo, hi in _ODDS_BUCKETS_H:
                if lo <= h_odd <= hi:
                    b = bkts[lbl]
                    b["n"]       += 1
                    b[outcome]   += 1
                    b["o25"]     += is_o25
                    b["u25"]     += 1 - is_o25
                    b["sum_imp_h"] += (1 / h_odd * 100) if h_odd > 0 else 0
                    b["sum_imp_x"] += (1 / x_odd * 100) if x_odd > 0 else 0
                    b["sum_imp_a"] += (1 / a_odd * 100) if a_odd > 0 else 0
                    break
            total += 1
        except Exception:
            continue

    buckets_out = []
    for lbl, _, _ in _ODDS_BUCKETS_H:
        b = bkts[lbl]
        n = b["n"]
        if n == 0:
            continue
        imp_h = round(b["sum_imp_h"] / n, 1)
        imp_x = round(b["sum_imp_x"] / n, 1)
        imp_a = round(b["sum_imp_a"] / n, 1)
        buckets_out.append({
            "label":     lbl,
            "n":         n,
            "casaV":     b["casaV"],
            "emp":       b["emp"],
            "visV":      b["visV"],
            "casaV_pct": round(b["casaV"] / n * 100),
            "emp_pct":   round(b["emp"]   / n * 100),
            "visV_pct":  round(b["visV"]  / n * 100),
            "o25_pct":   round(b["o25"]   / n * 100),
            "u25_pct":   round(b["u25"]   / n * 100),
            "imp_h":     imp_h,
            "imp_x":     imp_x,
            "imp_a":     imp_a,
            "edge_h":    round(b["casaV"] / n * 100 - imp_h, 1),
            "edge_x":    round(b["emp"]   / n * 100 - imp_x, 1),
            "edge_a":    round(b["visV"]  / n * 100 - imp_a, 1),
        })

    result = {"total": total, "buckets": buckets_out, "computed_at": datetime.now().isoformat()}
    _odds_patterns_cache = {"ts": time.time(), "data": result}
    return jsonify(result)


# ── Pattern Tips — TIPs dinâmicos por sequência de sinais ───────────────────

_pattern_tips_cache = {"ts": 0, "data": None, "n_files": 0}
_PTIPS_TTL = 3600   # 1 hora (mas invalida se chegar novo arquivo)

@app.route("/api/momentum/pattern-tips")
def api_momentum_pattern_tips():
    """Analisa sequências de sinais históricos (C/V/G/N) e correlaciona com
    resultados finais de todos os mercados definidos.
    Re-aprende automaticamente sempre que um novo arquivo é salvo."""
    global _pattern_tips_cache
    current_n = len(glob.glob(os.path.join(MOMENTUM_DIR, "*.json")))
    cache_valid = (
        _pattern_tips_cache["data"] is not None
        and time.time() - _pattern_tips_cache["ts"] < _PTIPS_TTL
        and _pattern_tips_cache["n_files"] == current_n   # re-aprende se novo arquivo
    )
    if cache_valid:
        return jsonify(_pattern_tips_cache["data"])

    T = 8    # threshold fixo (equivalente ao global padrão)
    W = 8    # janela de pontos

    def _feats(w):
        if not w:
            return 0.0
        tail  = sum(w[-4:]) / max(len(w), 1)
        trend = (w[-1] - w[0]) if len(w) > 1 else 0.0
        peak  = max(w, key=abs)
        return tail + 0.3 * trend + 0.2 * peak

    def _sig(score):
        if score > T:              return 'C'   # Casa domina
        if score < -T:             return 'V'   # Visitante domina
        if abs(score) > T * 0.6:   return 'G'   # Possível gol
        return 'N'                              # Sem padrão

    # patterns_data[pat][mkt] = [total, count]
    patterns_data = {}
    files         = glob.glob(os.path.join(MOMENTUM_DIR, "*.json"))
    total_files   = 0

    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            pts   = sorted(d.get("graphPoints", []),
                           key=lambda p: float(p.get("minute", 0)))
            goals = d.get("goals", [])
            if len(pts) < W + 2:
                continue

            # ── Extrai sequência de sinais (transições) ───────────────────
            sig_seq = []
            last_sig = None
            for i in range(W, len(pts)):
                win   = [float(pts[j].get("value", 0)) for j in range(i - W, i)]
                score = _feats(win)
                sig   = _sig(score)
                if sig != last_sig:
                    sig_seq.append(sig)
                    last_sig = sig

            if len(sig_seq) < 2:
                continue

            # ── Calcula resultados do jogo ────────────────────────────────
            gh  = sum(1 for g in goals if g.get("team") == "home")
            ga  = sum(1 for g in goals if g.get("team") == "away")
            gh_ht = sum(1 for g in goals
                        if g.get("team") == "home"
                        and float(g.get("minute", 0)) <= 45)
            ga_ht = sum(1 for g in goals
                        if g.get("team") == "away"
                        and float(g.get("minute", 0)) <= 45)
            tot    = gh + ga
            tot_ht = gh_ht + ga_ht

            outcomes = {
                "casaV":  int(gh > ga),
                "visV":   int(ga > gh),
                "emp":    int(gh == ga),
                "o25":    int(tot > 2),
                "u25":    int(tot <= 2),
                "o15":    int(tot > 1),
                "u15":    int(tot <= 1),
                "btts":   int(gh >= 1 and ga >= 1),
                "nbtts":  int(not (gh >= 1 and ga >= 1)),
                "o05ht":  int(tot_ht >= 1),
                "u05ht":  int(tot_ht == 0),
                "u15ht":  int(tot_ht <= 1),
                "1x":     int(gh >= ga),
                "x2":     int(ga >= gh),
            }
            # Placares corretos (capped at 3)
            for h in range(4):
                for a in range(4):
                    outcomes[f"cs_{h}-{a}"] = int(gh == h and ga == a)

            # ── Gera sub-padrões de comprimento 2, 3, 4 ──────────────────
            for length in (2, 3, 4):
                for i in range(len(sig_seq) - length + 1):
                    pat = "".join(sig_seq[i : i + length])
                    pd  = patterns_data.setdefault(pat, {})
                    for mkt, val in outcomes.items():
                        rec = pd.setdefault(mkt, [0, 0])
                        rec[0] += 1
                        rec[1] += val

            total_files += 1
        except Exception:
            continue

    # ── Filtra padrões com n ≥ 15 amostras ───────────────────────────────
    MIN_N = 15
    result_patterns = {}
    for pat, mkts in patterns_data.items():
        pat_res = {}
        for mkt, (total, count) in mkts.items():
            if total >= MIN_N:
                pat_res[mkt] = {"rate": round(count / total * 100, 1), "n": total}
        if pat_res:
            result_patterns[pat] = pat_res

    result = {
        "patterns":     result_patterns,
        "total_files":  total_files,
        "n_files":      current_n,
        "computed_at":  datetime.now().isoformat(),
    }
    _pattern_tips_cache = {"ts": time.time(), "data": result, "n_files": current_n}
    return jsonify(result)


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

# Cache de odds do dia (60s TTL)
_uni_odds_cache = {"ts": 0, "data": {}}

def _uni_odds_today():
    """Retorna dict {eventId: {h, x, a, ah_line, ah_h, ah_a, ou_line, ou_over, ou_under}} (cache 60s)."""
    global _uni_odds_cache
    if time.time() - _uni_odds_cache["ts"] < 60 and _uni_odds_cache["data"]:
        return _uni_odds_cache["data"]
    try:
        date = datetime.now().strftime("%Y-%m-%d")
        r = http_req.get(
            f"{UNISCORE_BASE}/sport/football/odds/8/{date}/offset/180",
            headers=UNISCORE_HEADERS, timeout=8
        )
        raw = r.json().get("data", {}).get("odds", "")
        odds_map = {}
        for entry in raw.split("!"):
            parts = entry.split("^")
            if len(parts) < 5:
                continue
            eid = parts[0]
            def _parse(seg):
                v = seg.split(":")
                return v if len(v) >= 3 else ["", "", ""]
            ah  = _parse(parts[2])   # Asian Handicap: line, home, away
            fx2 = _parse(parts[3])   # 1X2: home, draw, away
            ou  = _parse(parts[4])   # Over/Under em HK odds → converte para decimal (+1)
            def hk2dec(v):
                try:
                    f = float(v)
                    return str(round(f + 1, 2)) if f < 1.0 else v
                except: return v
            if fx2[0]:
                odds_map[eid] = {
                    "h": fx2[0], "x": fx2[1], "a": fx2[2],
                    "ah_line": ah[0], "ah_h": ah[1], "ah_a": ah[2],
                    "ou_line":  ou[0],
                    "ou_over":  hk2dec(ou[1]),
                    "ou_under": hk2dec(ou[2]),
                }
        _uni_odds_cache = {"ts": time.time(), "data": odds_map}
        return odds_map
    except Exception:
        return {}

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
    home_tid = ev.get("homeTeam", {}).get("id", "")
    away_tid = ev.get("awayTeam", {}).get("id", "")

    # Odds do dia (cache 60s)
    odds = _uni_odds_today().get(eid, {})

    result = {
        "found":    True,
        "id":       eid,
        "home_tid": home_tid,
        "away_tid": away_tid,
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
        "odds": odds,  # {h, x, a, ah_line, ah_h, ah_a, ou_line, ou_over, ou_under}
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

    # Estatísticas detalhadas (chutes, posse, passes, duelos...)
    try:
        url_stats = f"{UNISCORE_BASE}/football/event/{eid}/home/{home_tid}/away/{away_tid}/statistics"
        r = http_req.get(url_stats, headers=UNISCORE_HEADERS, timeout=6)
        periods = r.json().get("data", {}).get("statistics", [])
        # Usa período ALL; se não existir, pega o primeiro disponível
        period_data = next((p for p in periods if p.get("period") == "ALL"), periods[0] if periods else None)
        if period_data:
            # Constrói dict {name: {home, away, homeValue, awayValue}} para fácil acesso
            stat_map = {}
            for grp in period_data.get("groups", []):
                for item in grp.get("statisticsItems", []):
                    if isinstance(item, dict):
                        stat_map[item.get("fields") or item.get("name")] = item
            result["statistics"] = stat_map
        else:
            result["statistics"] = {}
    except Exception:
        result["statistics"] = {}

    # Escalação
    try:
        r = http_req.get(f"{UNISCORE_BASE}/football/event/{eid}/lineups?language=pt-BR",
                         headers=UNISCORE_HEADERS, timeout=6)
        ld = r.json().get("data", {})
        def parse_side(side):
            sd = ld.get(side, {})
            def pp(p):
                pl = p.get("player", {})
                return {
                    "name":    pl.get("fullName") or pl.get("name"),
                    "number":  p.get("shirtNumber"),
                    "pos":     p.get("position"),
                    "captain": p.get("captain", False),
                    "rating":  p.get("rating"),
                }
            players = sd.get("players", [])
            return {
                "formation":  sd.get("formation", ""),
                "confirmed":  ld.get("confirmed", False),
                "titulares":  [pp(p) for p in players if not p.get("substitute", False)],
                "reservas":   [pp(p) for p in players if p.get("substitute", False)],
            }
        result["lineup_casa"] = parse_side("home")
        result["lineup_fora"] = parse_side("away")
    except Exception:
        result["lineup_casa"] = None
        result["lineup_fora"] = None

    # Gráfico de pressão (minuto a minuto)
    try:
        r = http_req.get(f"{UNISCORE_BASE}/football/event/{eid}/graph",
                         headers=UNISCORE_HEADERS, timeout=6)
        pts = r.json().get("data", {}).get("graphPoints", [])
        result["graph"] = [{"m": p.get("minute"), "v": p.get("value")} for p in pts]
    except Exception:
        result["graph"] = []

    # Odds ao vivo (movimentação de mercado)
    try:
        r = http_req.get(f"{UNISCORE_BASE}/sport/football/odd-live-change/8",
                         headers=UNISCORE_HEADERS, timeout=6)
        raw = r.json().get("data", {}).get("odds", "")
        for entry in raw.split("!"):
            parts = entry.split("^")
            if len(parts) < 4 or parts[0] != eid:
                continue
            def _p(seg):
                v = seg.split(":"); return v if len(v) >= 3 else ["","",""]
            fx2 = _p(parts[3])
            def hk2(v):
                try: f=float(v); return str(round(f+1,2)) if f<1.0 else v
                except: return v
            ou = _p(parts[4]) if len(parts) > 4 else ["","",""]
            result["live_odds"] = {
                "h": fx2[0], "x": fx2[1], "a": fx2[2],
                "ou_line": ou[0], "ou_over": hk2(ou[1]), "ou_under": hk2(ou[2]),
                "changed": parts[1] == "1",
            }
            break
    except Exception:
        pass

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
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
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


@app.route("/api/list-backup")
def api_list_backup():
    """Lista arquivos de momentum_history disponíveis no servidor."""
    token    = request.args.get("token", "")
    expected = os.environ.get("UPLOAD_TOKEN", "")
    if not expected or token != expected:
        return jsonify({"ok": False, "error": "Token inválido"}), 403

    files = sorted(glob.glob(os.path.join(MOMENTUM_DIR, "*.json")))
    names = [os.path.basename(f) for f in files]
    return jsonify({"ok": True, "files": names})


@app.route("/api/download-backup/<path:filename>")
def api_download_backup(filename):
    """Baixa um arquivo de momentum_history pelo nome."""
    token    = request.args.get("token", "")
    expected = os.environ.get("UPLOAD_TOKEN", "")
    if not expected or token != expected:
        return jsonify({"ok": False, "error": "Token inválido"}), 403

    # Segurança: só permite nomes de arquivo simples
    if "/" in filename or "\\" in filename or not filename.endswith(".json"):
        return jsonify({"ok": False, "error": "Arquivo inválido"}), 400

    return send_from_directory(MOMENTUM_DIR, filename)


@app.route("/api/upload-backup", methods=["POST"])
def api_upload_backup():
    """Recebe arquivos de backtest/momentum/predictions enviados do ambiente local.
    Requer ?token=UPLOAD_TOKEN no Railway Variables.
    """
    token    = request.args.get("token", "")
    expected = os.environ.get("UPLOAD_TOKEN", "")
    if not expected or token != expected:
        return jsonify({"ok": False, "error": "Token inválido"}), 403

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Nenhum arquivo enviado"}), 400

    f     = request.files["file"]
    fname = f.filename or ""

    # Determina destino pelo nome do arquivo
    if re.match(r'^\d{4}-\d{2}-\d{2}\.json$', fname):
        dest_dir      = BACKTEST_DIR
        remote_prefix = "backtest"
    elif re.match(r'^\d{4}-\d{2}-\d{2}_\d+\.json$', fname):
        dest_dir      = MOMENTUM_DIR
        remote_prefix = "momentum_history"
    elif fname in ("predictions_full.json", "predictions.json"):
        dest_dir      = DATA_DIR
        remote_prefix = ""
    else:
        return jsonify({"ok": False, "error": f"Nome de arquivo não reconhecido: {fname}"}), 400

    os.makedirs(dest_dir, exist_ok=True)
    local_path = os.path.join(dest_dir, fname)
    f.save(local_path)

    remote_path = f"{remote_prefix}/{fname}" if remote_prefix else fname
    github_storage.push_file_bg(local_path, remote_path)

    # Invalida caches se for predictions
    if fname.startswith("predictions"):
        global _pattern_tips_cache, _odds_patterns_cache, _stats_patterns_cache
        _pattern_tips_cache  = {"ts": 0, "data": None}
        _odds_patterns_cache = {"ts": 0, "data": None}
        _stats_patterns_cache = {"ts": 0, "data": None}

    return jsonify({"ok": True, "saved": fname, "path": local_path})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Servidor rodando em http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port, use_reloader=False)

"""
Servidor Flask — API + frontend para exibir dados do StatArea
"""
from flask import Flask, jsonify, send_from_directory, abort, request
import json, os, glob, re, threading, time, sqlite3, itertools
import requests as http_req
from datetime import datetime
import github_storage

FOTMOB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.fotmob.com/",
    "Accept":  "*/*",
}

app = Flask(__name__, static_folder="static", static_url_path="/static")
DATA_DIR     = os.path.dirname(__file__)
MOMENTUM_DIR = os.path.join(DATA_DIR, "momentum_history")
SHOTMAP_DIR  = os.path.join(DATA_DIR, "shotmap_history")
MAPA_CACHE_DIR = os.path.join(DATA_DIR, "mapa_cache")
os.makedirs(MOMENTUM_DIR, exist_ok=True)
os.makedirs(SHOTMAP_DIR,  exist_ok=True)
os.makedirs(MAPA_CACHE_DIR, exist_ok=True)

# ── Cache em memória dos arquivos de momentum_history — evita reler e reparsear os
# +2000 arquivos do disco a cada busca de padrão (aba Análise/CS do Ao Vivo).
# Reaproveita o que já foi parseado; só relê arquivos novos ou modificados (por mtime).
_momentum_files_cache = {}   # fpath -> {mtime, pt_list, goals, casa, fora, liga, date, shotmap, score}
_momentum_files_lock  = threading.Lock()

def _get_momentum_files_cached():
    """Retorna a lista de dados já parseados de todos os arquivos de momentum_history,
    reutilizando o cache em memória sempre que possível."""
    with _momentum_files_lock:
        files = sorted(glob.glob(os.path.join(MOMENTUM_DIR, "*.json")))
        result = []
        for fpath in files:
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                continue
            cached = _momentum_files_cache.get(fpath)
            if cached and cached["mtime"] == mtime:
                result.append(cached)
                continue
            try:
                with open(fpath, encoding="utf-8") as f:
                    d = json.load(f)
                pt_list = sorted(
                    [(float(p["minute"]), float(p["value"]))
                     for p in d.get("graphPoints", [])
                     if "minute" in p and "value" in p],
                    key=lambda x: x[0]
                )
                entry = {
                    "mtime":   mtime,
                    "pt_list": pt_list,
                    "goals":   d.get("goals", []),
                    "casa":    d.get("casa", "—"),
                    "fora":    d.get("fora", "—"),
                    "liga":    d.get("liga", ""),
                    "date":    d.get("date", ""),
                    "shotmap": d.get("shotmap", []),
                    "score":   d.get("score", {}),
                }
                _momentum_files_cache[fpath] = entry
                result.append(entry)
            except Exception:
                continue
        # Remove do cache arquivos que não existem mais
        stale = set(_momentum_files_cache) - set(files)
        for fpath in stale:
            _momentum_files_cache.pop(fpath, None)
        return result


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


APP_VERSION = "2026-05-18-v9"

@app.route("/version")
def version():
    return jsonify({"version": APP_VERSION, "ts": datetime.now().isoformat()})

@app.route("/")
def index():
    resp = send_from_directory("static", "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"]        = "no-cache"
    resp.headers["Expires"]       = "0"
    return resp


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
    last_updated = matches[0].get("data_coleta") if matches else None
    return jsonify({
        "total_partidas": len(matches),
        "com_detalhes": has_details,
        "source": source,
        "last_updated": last_updated,
    })


# ── SCRAPE REMOTO ────────────────────────────────────────────────────────────
_scrape_state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "result": None,   # "ok" | "error"
    "message": "",
    "total": 0,
}
_scrape_lock = threading.Lock()


def _run_scrape_bg(fetch_details: bool = False):
    """Raspa jogos do StatArea em background e salva predictions_full.json."""
    global _scrape_state
    with _scrape_lock:
        _scrape_state.update({"running": True, "started_at": datetime.now().isoformat(),
                              "finished_at": None, "result": None, "message": "Raspando...", "total": 0})
    try:
        from scraper import scrape_all, save_json
        matches = scrape_all(fetch_details=fetch_details, delay=0.5 if not fetch_details else 2.0)
        path = os.path.join(DATA_DIR, "predictions_full.json")
        save_json(matches, path)
        # Sobe para GitHub para persistir no próximo redeploy
        github_storage.push_file_bg(path, "predictions_full.json")
        with _scrape_lock:
            _scrape_state.update({
                "running": False, "finished_at": datetime.now().isoformat(),
                "result": "ok", "message": f"{len(matches)} partidas raspadas com sucesso.",
                "total": len(matches),
            })
        print(f"[scrape] ✓ {len(matches)} partidas salvas em predictions_full.json")
    except Exception as e:
        with _scrape_lock:
            _scrape_state.update({
                "running": False, "finished_at": datetime.now().isoformat(),
                "result": "error", "message": str(e), "total": 0,
            })
        print(f"[scrape] ✗ Erro: {e}")


@app.route("/api/scrape/now", methods=["POST"])
def api_scrape_now():
    """Dispara raspagem remota do StatArea (sem detalhes — rápida).
    Sem autenticação: raspa só dados públicos do StatArea, sem risco."""
    with _scrape_lock:
        if _scrape_state["running"]:
            return jsonify({"ok": False, "running": True,
                            "message": "Raspagem já em andamento — aguarde.", **_scrape_state})

    full = request.args.get("full", "0") == "1"
    threading.Thread(target=_run_scrape_bg, args=(full,), daemon=True).start()
    return jsonify({"ok": True, "running": True, "message": "Raspagem iniciada em background.",
                    "full": full})


@app.route("/api/scrape/status")
def api_scrape_status():
    """Retorna o estado atual da raspagem remota."""
    with _scrape_lock:
        return jsonify(dict(_scrape_state))


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


@app.route("/api/passadas")
def api_passadas():
    """Retorna todas as partidas passadas (todos os dias de backtest) em uma lista única,
    já com placar final e HT, pra alimentar a aba Partidas Passadas."""
    dates = load_backtest_dates()
    all_matches = []
    for date_str in dates:
        matches = load_backtest_day(date_str) or []
        for m in matches:
            if m.get("resultado"):
                m.setdefault("data_partida", date_str)
                all_matches.append(m)

    all_matches.sort(key=lambda m: (m.get("data_partida") or "", m.get("hora") or ""), reverse=True)

    decididos = [m for m in all_matches if m.get("tip_acertou") is not None]
    acertaram = [m for m in decididos if m.get("tip_acertou") is True]

    return jsonify({
        "total": len(all_matches),
        "dates": len(dates),
        "tip_acuracia": round(len(acertaram) / len(decididos) * 100, 1) if decididos else None,
        "matches": all_matches,
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


_uniscore_full_cache = {"ts": 0, "live": []}

_UNISCORE_PERIOD_OFFSET = {
    "1st_half": 0, "2nd_half": 45,
    "overtime1": 90, "overtime2": 105,
    "penalties": None, "halftime": None, "break_time": None,
}

def _uniscore_minuto(e):
    """Minuto real da partida, calculado a partir de time.currentPeriodStartTimestamp
    (quando o período atual começou) — o campo 'tempo'/status.description só traz o
    NOME do período (ex: '2nd_half'), não o minuto em si."""
    status_desc = e.get("status", {}).get("description", "")
    if status_desc not in _UNISCORE_PERIOD_OFFSET:
        return None
    offset = _UNISCORE_PERIOD_OFFSET[status_desc]
    if offset is None:
        return None  # intervalo/pênaltis — sem minuto corrido
    start_ts = e.get("time", {}).get("currentPeriodStartTimestamp")
    if not start_ts:
        return None
    elapsed_min = int((time.time() - start_ts) / 60)
    if elapsed_min < 0:
        return None
    return f"{offset + elapsed_min}'"

@app.route("/api/radar/live")
def api_radar_live():
    """Lista TODOS os jogos ao vivo via UniScore (todos os locales + paginação)."""
    # Cache de 90s para o endpoint público (mais curto que o cache interno)
    if time.time() - _uniscore_full_cache["ts"] < 90 and _uniscore_full_cache["live"]:
        live = _uniscore_full_cache["live"]
        return jsonify({"live": live, "total": len(live)})

    live = []
    all_by_id = {}
    for locale in _UNISCORE_LOCALES:
        page = 1
        while True:
            try:
                r = http_req.post(
                    f"{_UNISCORE_API}/sport/football/events/live-v2/locale/{locale}",
                    headers=_UNISCORE_HEADERS,
                    json={"page": page},
                    params={"language": "pt-BR"},
                    timeout=12,
                )
                if r.status_code not in (200, 201):
                    break
                data   = r.json().get("data", {})
                events = data.get("events", [])
                pag    = data.get("pagination", {})
                for e in events:
                    if e.get("status", {}).get("type") != "inprogress":
                        continue
                    eid = e["id"]
                    if eid in all_by_id:
                        continue
                    hs  = e.get("homeScore", {}) or {}
                    aws = e.get("awayScore", {}) or {}
                    all_by_id[eid] = {
                        "id":        eid,
                        "casa":      e.get("homeTeam", {}).get("name", ""),
                        "fora":      e.get("awayTeam", {}).get("name", ""),
                        "liga":      e.get("tournament", {}).get("name", ""),
                        "pais":      e.get("tournament", {}).get("category", {}).get("name", ""),
                        "tempo":     e.get("status", {}).get("description", ""),
                        "minuto":    _uniscore_minuto(e),
                        "golCasaFt": hs.get("current", 0),
                        "golForaFt": aws.get("current", 0),
                        "golCasaHt": hs.get("period1", 0),
                        "golForaHt": aws.get("period1", 0),
                        "cartaoCasa": 0,
                        "cartaoFora": 0,
                    }
                if not pag.get("hasNextPage"):
                    break
                page += 1
                if page > 5:
                    break
            except Exception as e:
                print(f"[live] Erro locale={locale}: {e}")
                break

    live = list(all_by_id.values())
    print(f"[live] {len(live)} jogos ao vivo retornados")

    # Se a fetch retornou 0 jogos mas o cache anterior tem dados recentes (< 5 min),
    # mantém o cache antigo para evitar sidebar vazia por falha temporária da API
    if not live and _uniscore_full_cache["live"] and (time.time() - _uniscore_full_cache["ts"] < 300):
        print(f"[live] API retornou 0 jogos — mantendo cache anterior com {len(_uniscore_full_cache['live'])} jogos")
        return jsonify({"live": _uniscore_full_cache["live"], "total": len(_uniscore_full_cache["live"]), "stale": True})

    _uniscore_full_cache["ts"]   = time.time()
    _uniscore_full_cache["live"] = live
    return jsonify({"live": live, "total": len(live), "stale": False})


# ── Cache simples de momentum em memória (evita abrir browser repetidamente) ──
_momentum_cache = {}
_momentum_lock  = threading.Lock()   # proteção para acesso concorrente

# ── Cache de shotmap ao vivo: acumula durante o jogo para não perder ao FT ──
_SHOTMAP_CACHE_FILE = os.path.join(DATA_DIR, ".shotmap_cache.json")
_shotmap_lock       = threading.Lock()

def _load_shotmap_cache() -> dict:
    """Carrega cache de shotmap do disco (sobrevive a restarts)."""
    try:
        if os.path.exists(_SHOTMAP_CACHE_FILE):
            with open(_SHOTMAP_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"[shotmap] Cache restaurado: {len(data)} jogo(s)")
            return data
    except Exception as e:
        print(f"[shotmap] Erro ao carregar cache: {e}")
    return {}

def _save_shotmap_cache(cache: dict):
    """Persiste cache de shotmap no disco."""
    try:
        with open(_SHOTMAP_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception as e:
        print(f"[shotmap] Erro ao salvar cache: {e}")

_shotmap_live_cache = _load_shotmap_cache()


def _fetch_live_matches_for_monitor():
    """Busca lista de jogos ao vivo via UniScore para o monitor de fundo."""
    matches = _get_uniscore_live_matches()
    return [
        {"id": m["id"], "casa": m["home"], "fora": m["away"], "liga": ""}
        for m in matches
    ]


_sofa_live_cache = {"ts": 0, "events": []}
_sofa_live_lock  = threading.Lock()

import unicodedata

def _norm(s: str) -> str:
    """Normaliza string: minúsculo, sem acento, sem caracteres especiais."""
    s = s.lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s

# Sufixos genéricos a ignorar na comparação de nomes de times
_TEAM_SUFFIXES = {
    "fc", "cf", "ac", "sc", "bc", "bk", "sk", "fk", "nk", "rk",
    "united", "utd", "city", "town", "rovers", "wanderers",
    "sporting", "sport", "club", "atletico", "atletico", "deportivo",
    "real", "de", "do", "da", "dos", "las", "los", "el",
}

def _strip_suffixes(words: set) -> set:
    """Remove palavras genéricas de um conjunto de tokens."""
    return {w for w in words if w not in _TEAM_SUFFIXES}

def _name_match(a: str, b: str) -> bool:
    """Verifica se dois nomes de times batem (fuzzy aprimorado)."""
    na, nb = _norm(a), _norm(b)

    # 1. Igualdade exata
    if na == nb:
        return True

    # 2. Substring direta
    if na in nb or nb in na:
        return True

    # 3. Palavras com >= 3 chars em comum (anterior era >= 4)
    words_a = {w for w in na.split() if len(w) >= 3}
    words_b = {w for w in nb.split() if len(w) >= 3}
    if words_a & words_b:
        return True

    # 4. Palavras sem sufixos genéricos — evita falso positivo por "FC"/"Sporting"
    core_a = _strip_suffixes(words_a)
    core_b = _strip_suffixes(words_b)
    if core_a and core_b and core_a & core_b:
        return True

    # 5. Nomes curtos (≤ 4 chars): exige igualdade exata entre os tokens curtos
    short_a = {w for w in na.split() if len(w) <= 4}
    short_b = {w for w in nb.split() if len(w) <= 4}
    if short_a and short_b and short_a == short_b and len(short_a) >= 1:
        return True

    return False


def _get_sofa_live_events():
    """Busca todos os jogos de futebol ao vivo do api.sofascore.com. Cache 2min."""
    with _sofa_live_lock:
        if time.time() - _sofa_live_cache["ts"] < 120:
            return _sofa_live_cache["events"]
    try:
        s = http_req.Session()
        s.headers.update(_SOFA_HEADERS)
        r = s.get("https://api.sofascore.com/api/v1/sport/football/events/live", timeout=12)
        if r.status_code == 200:
            events = r.json().get("events", [])
            parsed = [
                {
                    "id":   e["id"],
                    "home": e.get("homeTeam", {}).get("name", ""),
                    "away": e.get("awayTeam", {}).get("name", ""),
                }
                for e in events
            ]
            print(f"[sofa-live] {len(parsed)} jogos ao vivo")
            with _sofa_live_lock:
                _sofa_live_cache["ts"]     = time.time()
                _sofa_live_cache["events"] = parsed
            return parsed
    except Exception as e:
        print(f"[sofa-live] Erro: {e}")
    return []


def _find_sofa_event_id(casa: str, fora: str):
    """Encontra o ID correto do SofaScore pelo nome dos times."""
    if not casa or not fora:
        return None
    events = _get_sofa_live_events()
    for ev in events:
        if _name_match(casa, ev["home"]) and _name_match(fora, ev["away"]):
            print(f"[sofa-live] Match: {ev['home']} vs {ev['away']} id={ev['id']}")
            return ev["id"]
    return None

_SOFA_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":         "https://www.sofascore.com/",
    "Origin":          "https://www.sofascore.com",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Cache-Control":   "no-cache",
}


_fotmob_live_cache    = {"ts": 0, "matches": []}
_fotmob_live_lock     = threading.Lock()

_UNISCORE_HEADERS = {
    "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Origin":       "https://uniscore.com",
    "Referer":      "https://uniscore.com/pt-BR/football",
    "Accept":       "application/json, text/plain, */*",
    "Content-Type": "application/json",
}
_UNISCORE_API     = "https://api.unik8s.com/api/v2"
_uniscore_cache   = {"ts": 0, "matches": []}
_uniscore_lock    = threading.Lock()


_UNISCORE_LOCALES = ["BR", "EU", "AS", "AF", "NA", "SA", "OC"]

def _get_uniscore_live_matches():
    """Busca TODAS as partidas ao vivo do UniScore (todos os locales + paginação).
    Cache de 2 minutos. Retorna lista de {id, homeId, awayId, home, away}."""
    with _uniscore_lock:
        if time.time() - _uniscore_cache["ts"] < 120:
            return _uniscore_cache["matches"]

    all_by_id = {}
    for locale in _UNISCORE_LOCALES:
        page = 1
        while True:
            try:
                r = http_req.post(
                    f"{_UNISCORE_API}/sport/football/events/live-v2/locale/{locale}",
                    headers=_UNISCORE_HEADERS,
                    json={"page": page},
                    params={"language": "pt-BR"},
                    timeout=12,
                )
                if r.status_code not in (200, 201):
                    break
                data      = r.json().get("data", {})
                events    = data.get("events", [])
                pag       = data.get("pagination", {})
                for e in events:
                    if e.get("status", {}).get("type") == "inprogress":
                        eid = e["id"]
                        if eid not in all_by_id:
                            all_by_id[eid] = {
                                "id":     eid,
                                "homeId": e.get("homeTeam", {}).get("id", ""),
                                "awayId": e.get("awayTeam", {}).get("id", ""),
                                "home":   e.get("homeTeam", {}).get("name", ""),
                                "away":   e.get("awayTeam", {}).get("name", ""),
                            }
                if not pag.get("hasNextPage"):
                    break
                page += 1
                if page > 5:   # safety cap
                    break
            except Exception as e:
                print(f"[uniscore] Erro live locale={locale} page={page}: {e}")
                break

    matches = list(all_by_id.values())
    print(f"[uniscore] {len(matches)} partidas ao vivo (todos os locales)")
    with _uniscore_lock:
        _uniscore_cache["ts"]      = time.time()
        _uniscore_cache["matches"] = matches
    return matches


def _find_uniscore_id(casa, fora):
    """Encontra ID UniScore pelo nome dos times (fuzzy com normalização).
    Retorna dict {id, homeId, awayId} ou None."""
    if not casa or not fora:
        return None
    matches = _get_uniscore_live_matches()
    for m in matches:
        if _name_match(casa, m["home"]) and _name_match(fora, m["away"]):
            print(f"[uniscore] Match: {m['home']} vs {m['away']} id={m['id']}")
            return {"id": m["id"], "homeId": m["homeId"], "awayId": m["awayId"]}
    if matches:
        sample = [(m["home"], m["away"]) for m in matches[:5]]
        print(f"[uniscore] Sem match p/ '{casa}' vs '{fora}'. Amostra: {sample}")
    return None


def _uniscore_stats_to_flat(stats_list):
    """Converte lista de períodos UniScore para dict plano por período.
    Retorna {"ALL": {stat: {home,away,homeValue,awayValue}}, "1ST": {...}, "2ND": {...}}"""
    result = {}
    for period_data in (stats_list or []):
        period = period_data.get("period", "ALL")
        flat   = {}
        for group in period_data.get("groups", []):
            for item in group.get("statisticsItems", []):
                name = item.get("name")
                if name and name not in flat:
                    flat[name] = {
                        "home":      item.get("home", "0"),
                        "away":      item.get("away", "0"),
                        "homeValue": item.get("homeValue", 0),
                        "awayValue": item.get("awayValue", 0),
                    }
        result[period] = flat
    return result


def _fetch_uniscore_graph(uni_match):
    """Busca graphPoints + estatísticas por período do UniScore.
    uni_match = {id, homeId, awayId}"""
    uniscore_id = uni_match["id"]
    home_id     = uni_match.get("homeId", "")
    away_id     = uni_match.get("awayId", "")

    # Graph (momentum)
    r = http_req.get(
        f"{_UNISCORE_API}/football/event/{uniscore_id}/graph",
        headers=_UNISCORE_HEADERS, timeout=12,
    )
    r.raise_for_status()
    pts = r.json().get("data", {}).get("graphPoints", [])

    # Incidents (gols)
    goals = []
    try:
        ri = http_req.get(
            f"{_UNISCORE_API}/football/event/{uniscore_id}/incidents",
            headers=_UNISCORE_HEADERS, timeout=12,
        )
        if ri.status_code == 200:
            for inc in ri.json().get("data", {}).get("incidents", []):
                if inc.get("incidentType") == "goal":
                    player     = inc.get("player") or inc.get("scorer") or {}
                    added_time = inc.get("addedTime") or 0
                    goals.append({
                        "minute":     inc.get("time", 0),
                        "addedTime":  added_time,
                        "team":       "home" if inc.get("isHome") else "away",
                        "player":     player.get("shortName") or player.get("name") or "",
                        "ownGoal":    inc.get("incidentClass") == "ownGoal",
                    })
    except Exception:
        pass

    # Estatísticas por período (Todos / 1º / 2º)
    statistics_periods = {}
    if home_id and away_id:
        try:
            rs = http_req.get(
                f"{_UNISCORE_API}/football/event/{uniscore_id}/home/{home_id}/away/{away_id}/statistics",
                headers=_UNISCORE_HEADERS, timeout=12,
            )
            if rs.status_code == 200:
                stats_list = rs.json().get("data", {}).get("statistics", [])
                statistics_periods = _uniscore_stats_to_flat(stats_list)
                print(f"[uniscore] Estatísticas: {list(statistics_periods.keys())}")
        except Exception as es:
            print(f"[uniscore] Stats falhou: {es}")

    # Shotmap
    shotmap = []
    try:
        rsm = http_req.get(
            f"{_UNISCORE_API}/football/event/{uniscore_id}/shotmap",
            headers=_UNISCORE_HEADERS, timeout=12,
        )
        if rsm.status_code == 200:
            raw_shots = rsm.json().get("data", {}).get("shotmap", [])
            shotmap = [
                {
                    "id":        s.get("id"),
                    "minute":    s.get("time", 0),
                    "isHome":    s.get("isHome", True),
                    "shotType":  s.get("shotType", "miss"),
                    "bodyPart":  s.get("bodyPart", ""),
                    "situation": s.get("situation", ""),
                    "player":    s.get("player", {}).get("shortName", ""),
                    "x":         s.get("playerCoordinates", {}).get("x", 0),
                    "y":         s.get("playerCoordinates", {}).get("y", 0),
                }
                for s in raw_shots
            ]
            print(f"[uniscore] Shotmap: {len(shotmap)} chutes")
    except Exception as es:
        print(f"[uniscore] Shotmap falhou: {es}")

    # Status (FT?) + placar oficial do UniScore
    finished = False
    score_h  = None
    score_a  = None
    try:
        re = http_req.get(
            f"{_UNISCORE_API}/football/event/{uniscore_id}",
            headers=_UNISCORE_HEADERS,
            params={"language": "pt-BR"}, timeout=10,
        )
        if re.status_code == 200:
            ev_data  = re.json().get("data", {}).get("event", {})
            status   = ev_data.get("status", {})
            finished = status.get("type") == "finished"
            hs = ev_data.get("homeScore", {}) or {}
            as_ = ev_data.get("awayScore", {}) or {}
            score_h    = hs.get("current")
            score_a    = as_.get("current")
            score_ht_h = hs.get("period1")
            score_ht_a = as_.get("period1")
    except Exception:
        pass

    return {
        "graphPoints":        pts,
        "goals":              goals,
        "finished":           finished,
        "score_h":            score_h,
        "score_a":            score_a,
        "score_ht_h":         score_ht_h,
        "score_ht_a":         score_ht_a,
        "statistics":         statistics_periods.get("ALL", {}),
        "statistics_periods": statistics_periods,
        "shotmap":            shotmap,
        "source":             "uniscore",
    }


def _get_fotmob_live_matches():
    """Busca partidas ao vivo do FotMob. Cache de 2 minutos."""
    with _fotmob_live_lock:
        if time.time() - _fotmob_live_cache["ts"] < 120:
            return _fotmob_live_cache["matches"]
    try:
        today_str = datetime.now().strftime("%Y%m%d")
        r = http_req.get(
            "https://www.fotmob.com/api/matches",
            headers=FOTMOB_HEADERS,
            params={"date": today_str},
            timeout=12
        )
        r.raise_for_status()
        data = r.json()
        matches = []
        for league in data.get("leagues", []):
            for m in league.get("matches", []):
                st = m.get("status", {})
                if st.get("started") and not st.get("finished"):
                    home_name = m.get("home", {}).get("name", "").lower()
                    away_name = m.get("away", {}).get("name", "").lower()
                    matches.append({
                        "id":   str(m.get("id", "")),
                        "home": home_name,
                        "away": away_name,
                    })
        print(f"[fotmob] {len(matches)} partidas ao vivo encontradas")
        with _fotmob_live_lock:
            _fotmob_live_cache["ts"]      = time.time()
            _fotmob_live_cache["matches"] = matches
        return matches
    except Exception as e:
        print(f"[fotmob] Erro ao buscar live: {e}")
        return []


def _find_fotmob_id(casa, fora):
    """Encontra o ID do FotMob pelo nome dos times (busca fuzzy)."""
    if not casa or not fora:
        return None
    matches = _get_fotmob_live_matches()
    casa_l  = casa.lower()
    fora_l  = fora.lower()
    # Tenta match exato primeiro, depois substring
    for m in matches:
        if (casa_l in m["home"] or m["home"] in casa_l) and \
           (fora_l in m["away"] or m["away"] in fora_l):
            print(f"[fotmob] Match encontrado: {m['home']} vs {m['away']} (id={m['id']})")
            return m["id"]
    # Log para depuração quando não encontra
    if matches:
        sample = [(m["home"], m["away"]) for m in matches[:5]]
        print(f"[fotmob] Nenhum match para '{casa_l}' vs '{fora_l}'. Amostra: {sample}")
    else:
        print(f"[fotmob] Lista de partidas vazia ao buscar '{casa_l}' vs '{fora_l}'")
    return None


def _fetch_fotmob_momentum(fotmob_id):
    """Busca momentum do FotMob via API direta e converte para formato graphPoints."""
    r = http_req.get(
        "https://www.fotmob.com/api/matchDetails",
        params={"matchId": fotmob_id},
        headers=FOTMOB_HEADERS,
        timeout=15
    )
    r.raise_for_status()
    raw      = r.json()
    mom      = raw.get("content", {}).get("matchFacts", {}).get("momentum", {})
    mom_data = mom.get("main", {}).get("data", [])

    # Converte para formato graphPoints (compatível com o frontend)
    # FotMob: [{minute, value}] onde value > 0 = home, < 0 = away
    graph_points = []
    for pt in mom_data:
        val = pt.get("value", 0)
        minute = pt.get("minute", pt.get("min", 0))
        graph_points.append({
            "minute":    minute,
            "homeValue": max(0, val),
            "awayValue": min(0, val),
        })

    # Extrai gols dos incidents
    incidents_raw = raw.get("content", {}).get("matchFacts", {}).get("events", {})
    goals = []
    for ev in incidents_raw.get("events", []):
        if ev.get("type") in ("goal", "ownGoal"):
            goals.append({
                "minute": ev.get("time", 0),
                "team":   "home" if ev.get("isHome") else "away",
            })

    # Detecta FT
    status   = raw.get("header", {}).get("status", {})
    finished = status.get("finished", False)

    # Stats
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

    return {
        "graphPoints": graph_points,
        "goals":       goals,
        "finished":    finished,
        "statistics":  stats_out,
        "source":      "fotmob",
    }


def _pressure_summary(graph_points: list) -> dict:
    """Calcula métricas de pressão/dominância a partir dos graphPoints.
    Valor > 0 = home dominant, < 0 = away dominant.
    """
    if not graph_points:
        return {}
    vals = [p.get("value", 0) for p in graph_points]
    minutes = [p.get("minute", 0) for p in graph_points]
    max_min = max(minutes) if minutes else 90
    half = max_min / 2

    h1 = [v for p, v in zip(graph_points, vals) if p.get("minute", 0) <= half]
    h2 = [v for p, v in zip(graph_points, vals) if p.get("minute", 0) > half]

    def avg(lst): return round(sum(lst) / len(lst), 3) if lst else 0.0

    home_dom = sum(1 for v in vals if v > 0)
    swings = sum(1 for i in range(1, len(vals)) if (vals[i] > 0) != (vals[i-1] > 0))

    return {
        "overall_avg":        avg(vals),
        "h1_avg":             avg(h1),
        "h2_avg":             avg(h2),
        "home_dominance_pct": round(home_dom / len(vals) * 100, 1) if vals else 0.0,
        "max_home":           round(max((v for v in vals if v > 0), default=0.0), 3),
        "max_away":           round(abs(min((v for v in vals if v < 0), default=0.0)), 3),
        "momentum_swings":    swings,
        "total_points":       len(vals),
    }


def _calc_xg(stats_flat: dict) -> dict:
    """Estima xG usando TODOS os campos de estatísticas disponíveis.

    Prioridade:
      1. Campo xG direto do UniScore/SofaScore
      2. Fórmula enriquecida com todos os stats do painel ESTATÍSTICAS

    Pesos baseados em probabilidades de conversão da literatura de analytics:
      shots_on_target  ≈ 0.33  (1 em 3 chutes no alvo vira gol)
      big_chances      ≈ 0.38  (grandes chances têm alta conversão)
      shots_inside_box ≈ 0.09  (chutes dentro da área não no alvo)
      shots_outside    ≈ 0.025 (chutes de fora da área)
      corners          ≈ 0.026 (escanteios geram perigo de área)
      touches_in_box   ≈ 0.008 (toques na área → proximidade de gol)
      final_third      ≈ 0.004 (passes/entradas no terço final → pressão)
      freekicks        ≈ 0.012 (cobranças de falta em posição perigosa)
      saves (oponente) → proxy de chutes no alvo quando shots_on_target = 0
    """
    # Nomes alternativos: UniScore usa Title Case com espaços,
    # código interno usa snake_case — tentamos ambos
    _ALIASES = {
        "shots_on_target":     ["Shots on Target", "shotsOnTarget", "Shots On Target"],
        "shots_inside_box":    ["Shots Inside Box", "shots_inside_box"],
        "shots_outside_box":   ["Shots Outside Box", "shots_outside_box"],
        "big_chances":         ["Big Chances", "bigChancesCreated", "Big Chances Created"],
        "corner_kicks":        ["Corner Kicks", "cornerKicks", "Corners"],
        "touches_in_box":      ["Touches in Box", "Touches In Box", "touches_in_box"],
        "pass_in_final_third": ["Passes in Final Third", "Pass in Final Third", "pass_in_final_third"],
        "final_third_entries": ["Final Third Entries", "final_third_entries"],
        "saves":               ["Saves", "Goalkeeper Saves", "saves"],
        "freekicks":           ["Free Kicks", "Freekicks", "freekicks"],
        "shots":               ["Total Shots", "Shots", "totalShots", "shots"],
    }

    def _get(canonical, fallback=0.0):
        """Busca stat tentando snake_case + aliases UniScore."""
        keys_to_try = [canonical] + _ALIASES.get(canonical, [])
        for k in keys_to_try:
            item = stats_flat.get(k)
            if item and isinstance(item, dict):
                hv = item.get("homeValue")
                av = item.get("awayValue")
                # homeValue pode ser 0 legítimo — só pula se for None
                if hv is None: hv = item.get("home", 0)
                if av is None: av = item.get("away", 0)
                try:
                    h = float(str(hv).replace("%", "").strip() or 0)
                    a = float(str(av).replace("%", "").strip() or 0)
                    return h, a
                except Exception:
                    pass
        return fallback, fallback

    # ── 1. xG direto ─────────────────────────────────────────────────────────
    for key in ("Expected Goals", "xG", "expected_goals", "Expected goals"):
        item = stats_flat.get(key)
        if item and isinstance(item, dict):
            hv = item.get("homeValue") if item.get("homeValue") is not None else item.get("home", 0)
            av = item.get("awayValue") if item.get("awayValue") is not None else item.get("away", 0)
            try:
                return {"home": round(float(hv), 2), "away": round(float(av), 2), "source": "direct"}
            except Exception:
                pass

    # ── 2. Fórmula enriquecida com todos os stats ─────────────────────────────
    ontar_h,   ontar_a   = _get("shots_on_target")
    inside_h,  inside_a  = _get("shots_inside_box")
    outside_h, outside_a = _get("shots_outside_box")
    big_h,     big_a     = _get("big_chances")
    corners_h, corners_a = _get("corner_kicks")
    touches_h, touches_a = _get("touches_in_box")
    fp3_h,     fp3_a     = _get("pass_in_final_third")
    fte_h,     fte_a     = _get("final_third_entries")
    saves_h,   saves_a   = _get("saves")
    free_h,    free_a    = _get("freekicks")
    total_h,   total_a   = _get("shots")

    # Se shots_on_target = 0 mas saves do adversário está disponível,
    # usa saves como proxy (saves_adversario ≈ shots_on_target_proprio)
    eff_ontar_h = ontar_h if ontar_h > 0 else saves_a
    eff_ontar_a = ontar_a if ontar_a > 0 else saves_h

    # Se shots_inside_box = 0 mas total de chutes disponível, estima 60% dentro
    if inside_h == 0 and total_h > 0:
        inside_h = total_h * 0.60
    if inside_a == 0 and total_a > 0:
        inside_a = total_a * 0.60
    if outside_h == 0 and total_h > 0:
        outside_h = total_h * 0.40
    if outside_a == 0 and total_a > 0:
        outside_a = total_a * 0.40

    # Contribuição de pressão posicional (final third + passes finais)
    press_h = fp3_h + fte_h
    press_a = fp3_a + fte_a

    xg_h = round(
        0.33  * eff_ontar_h    # chutes no alvo (maior peso)
      + 0.38  * big_h          # grandes chances
      + 0.09  * inside_h       # chutes dentro da área (não no alvo)
      + 0.025 * outside_h      # chutes fora da área
      + 0.026 * corners_h      # escanteios → perigo de área
      + 0.008 * touches_h      # toques na área adversária
      + 0.004 * press_h        # pressão no terço final
      + 0.012 * free_h,        # cobranças de falta perigosas
    2)

    xg_a = round(
        0.33  * eff_ontar_a
      + 0.38  * big_a
      + 0.09  * inside_a
      + 0.025 * outside_a
      + 0.026 * corners_a
      + 0.008 * touches_a
      + 0.004 * press_a
      + 0.012 * free_a,
    2)

    # Quais campos contribuíram
    fields_used = []
    if eff_ontar_h > 0 or eff_ontar_a > 0:   fields_used.append("shots_on_target")
    if big_h > 0 or big_a > 0:                fields_used.append("big_chances")
    if inside_h > 0 or inside_a > 0:          fields_used.append("shots_inside_box")
    if outside_h > 0 or outside_a > 0:        fields_used.append("shots_outside_box")
    if corners_h > 0 or corners_a > 0:        fields_used.append("corners")
    if touches_h > 0 or touches_a > 0:        fields_used.append("touches_in_box")
    if press_h > 0 or press_a > 0:            fields_used.append("final_third")
    if free_h > 0 or free_a > 0:              fields_used.append("freekicks")

    if xg_h == 0.0 and xg_a == 0.0:
        return {}

    return {
        "home":         xg_h,
        "away":         xg_a,
        "source":       "estimated",
        "fields_used":  fields_used,
        "n_fields":     len(fields_used),
    }


def _extract_score(goals: list) -> dict:
    """Conta gols da lista de incidents para obter o placar final."""
    home = sum(1 for g in goals if g.get("team") == "home")
    away = sum(1 for g in goals if g.get("team") == "away")
    return {"home": home, "away": away}


def _build_save_payload(
    event_id, casa, fora, liga,
    graph_points, goals, stats_flat, stats_periods,
    opening_odds, source, shotmap=None
) -> dict:
    """Monta o payload completo para salvar no momentum_history."""
    today = datetime.now().strftime("%Y-%m-%d")
    return {
        # Identificação
        "event_id":  event_id,
        "date":      today,
        "saved_at":  datetime.now().isoformat(),
        "source":    source,
        "casa":      casa,
        "fora":      fora,
        "liga":      liga,
        # Dados brutos
        "graphPoints":        graph_points,
        "goals":              goals,
        "score":              _extract_score(goals),
        # Mapa de chutes
        "shotmap":            shotmap or [],
        # Estatísticas
        "statistics":         stats_flat,
        "statistics_periods": stats_periods,
        # Indicadores derivados
        "pressure_summary":   _pressure_summary(graph_points),
        "xg":                 _calc_xg(stats_flat),
        # Odds
        "opening_odds":       opening_odds,
    }


def _fetch_sofa_direct(event_id):
    """Busca dados do SofaScore via requests direto (sem Playwright).
    Tenta api.sofascore.com (sem Cloudflare) primeiro, fallback para www.
    Retorna (graph, incidents, statistics) ou levanta exceção."""
    # api.sofascore.com não tem Cloudflare — funciona de IPs de datacenter
    for base_url in [
        f"https://api.sofascore.com/api/v1/event/{event_id}",
        f"https://www.sofascore.com/api/v1/event/{event_id}",
    ]:
        try:
            s = http_req.Session()
            s.headers.update(_SOFA_HEADERS)
            graph      = s.get(f"{base_url}/graph",      timeout=12)
            incidents  = s.get(f"{base_url}/incidents",  timeout=12)
            statistics = s.get(f"{base_url}/statistics", timeout=12)
            # Verifica se retornou dados válidos (não bloqueio Cloudflare)
            g = graph.json()
            pts = g.get("graphPoints", [])
            print(f"[sofa] {base_url.split('/')[2]}: {len(pts)} graphPoints")
            if pts:
                return g, incidents.json(), statistics.json()
        except Exception as e:
            print(f"[sofa] Falhou {base_url.split('/')[2]}: {e}")
            continue
    raise RuntimeError(f"SofaScore: nenhuma fonte retornou graphPoints para event {event_id}")


def _fetch_sofa_playwright(event_id):
    """Fallback: busca dados via Playwright (headless Chromium)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"]
        )
        ctx  = browser.new_context(
            user_agent=_SOFA_HEADERS["User-Agent"],
            locale="pt-BR", timezone_id="America/Sao_Paulo",
        )
        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.goto("https://www.sofascore.com/", timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        result = page.evaluate(f"""async () => {{
            try {{
                const [rG, rI, rS] = await Promise.all([
                    fetch('/api/v1/event/{event_id}/graph'),
                    fetch('/api/v1/event/{event_id}/incidents'),
                    fetch('/api/v1/event/{event_id}/statistics')
                ]);
                return {{
                    status: 200,
                    graph:      rG.ok ? await rG.json() : {{}},
                    incidents:  rI.ok ? await rI.json() : {{}},
                    statistics: rS.ok ? await rS.json() : {{}}
                }};
            }} catch(e) {{ return {{error: e.message}}; }}
        }}""")
        browser.close()
    if result.get("status") != 200:
        raise RuntimeError(f"Playwright sem dados: {result.get('error')}")
    return result["graph"], result["incidents"], result["statistics"]


def _process_momentum(event_id, casa="", fora="", liga=""):
    """Busca momentum exclusivamente via UniScore (busca por nome de time).
    Cache de 90s para evitar chamadas repetidas.
    """
    global _pattern_tips_cache, _odds_patterns_cache, _stats_patterns_cache
    # Verifica cache primeiro
    with _momentum_lock:
        cached = _momentum_cache.get(event_id)
        if cached and time.time() - cached["ts"] < 90:
            return cached["data"]

    print(f"[momentum] Buscando '{casa}' vs '{fora}' via UniScore...")

    # ── Único source: UniScore (busca por nome) ───────────────────────────
    uni_match = _find_uniscore_id(casa, fora)
    if not uni_match:
        print(f"[momentum] UniScore: partida não encontrada para '{casa}' vs '{fora}'")
        with _momentum_lock:
            _momentum_cache[event_id] = {"ts": time.time(), "data": None}
        return None

    try:
        udata = _fetch_uniscore_graph(uni_match)
        pts   = udata.get("graphPoints", [])
        print(f"[momentum] UniScore OK: {len(pts)} graphPoints, finished={udata.get('finished')}")

        # ── Calcula xG e pressure_summary para o dado ao vivo ────────────
        # (normalmente só calculados no save; precisamos aqui para os indicadores)
        stats_live = udata.get("statistics", {})
        xg_live    = _calc_xg(stats_live) if stats_live else {}
        ps_live    = _pressure_summary(pts) if pts else {}

        data = {**udata, "saved": False,
                "xg": xg_live, "pressure_summary": ps_live}

        # ── Acumula shotmap ao vivo no cache separado ─────────────────────
        # O endpoint de shotmap só funciona durante o jogo; ao FT fica vazio.
        # Guardamos no disco para sobreviver a restarts/redeploys do Railway.
        live_shots = udata.get("shotmap", [])
        if live_shots:
            with _shotmap_lock:
                prev = _shotmap_live_cache.get(event_id, [])
                is_new_event = event_id not in _shotmap_live_cache
                if len(live_shots) != len(prev):   # só escreve se mudou
                    _shotmap_live_cache[event_id] = live_shots
                    _save_shotmap_cache(_shotmap_live_cache)
                    # Push pro GitHub quando é novo evento (sobrevive a restart mid-game)
                    if is_new_event:
                        github_storage.push_file_bg(
                            _SHOTMAP_CACHE_FILE, ".shotmap_cache.json"
                        )

        # ── Auto-save quando a partida termina ───────────────────────────
        if udata.get("finished"):
            today     = datetime.now().strftime("%Y-%m-%d")
            save_file = os.path.join(MOMENTUM_DIR, f"{today}_{event_id}.json")
            if not os.path.exists(save_file):
                # Odds de abertura
                opening_odds = {}
                try:
                    uni_odds_map = _uni_odds_today().get(uni_match["id"], {})
                    if uni_odds_map:
                        opening_odds = {
                            "h":        uni_odds_map.get("h"),
                            "x":        uni_odds_map.get("x"),
                            "a":        uni_odds_map.get("a"),
                            "ou_line":  uni_odds_map.get("ou_line"),
                            "ou_over":  uni_odds_map.get("ou_over"),
                            "ou_under": uni_odds_map.get("ou_under"),
                        }
                except Exception:
                    pass

                # Usa shotmap acumulado durante o jogo (o endpoint de FT fica vazio)
                with _shotmap_lock:
                    best_shotmap = _shotmap_live_cache.get(event_id) or udata.get("shotmap", [])

                payload = _build_save_payload(
                    event_id=event_id,
                    casa=casa, fora=fora, liga=liga,
                    graph_points=pts,
                    goals=udata.get("goals", []),
                    stats_flat=udata.get("statistics", {}),
                    stats_periods=udata.get("statistics_periods", {}),
                    opening_odds=opening_odds,
                    source="uniscore",
                    shotmap=best_shotmap,
                )
                with open(save_file, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                data["saved"] = True
                sm_count = len(best_shotmap)
                print(f"[momentum] Salvo: {save_file} | shotmap={sm_count} chutes")

                # ── Salva shotmap separadamente em shotmap_history/ ───────────
                if best_shotmap:
                    score_raw = payload.get("score", {})
                    sm_payload = {
                        "event_id":  event_id,
                        "date":      today,
                        "casa":      casa,
                        "fora":      fora,
                        "liga":      liga,
                        "score":     score_raw,
                        "total_shots": sm_count,
                        "shotmap":   best_shotmap,
                    }
                    sm_file = os.path.join(SHOTMAP_DIR, f"{today}_{event_id}.json")
                    with open(sm_file, "w", encoding="utf-8") as f:
                        json.dump(sm_payload, f, ensure_ascii=False, indent=2)
                    print(f"[shotmap] Salvo separado: {sm_file}")
                    github_storage.push_file_bg(sm_file, f"shotmap_history/{today}_{event_id}.json")

                # Limpa cache de shotmap (memória + disco)
                with _shotmap_lock:
                    _shotmap_live_cache.pop(event_id, None)
                    _save_shotmap_cache(_shotmap_live_cache)
                github_storage.push_file_bg(save_file, f"momentum_history/{today}_{event_id}.json")
                _pattern_tips_cache  = {"ts": 0, "data": None}
                _odds_patterns_cache = {"ts": 0, "data": None}
                _stats_patterns_cache = {"ts": 0, "data": None}
                threading.Thread(target=_rebuild_analysis_cache, daemon=True).start()
            else:
                data["saved"] = True

        with _momentum_lock:
            _momentum_cache[event_id] = {"ts": time.time(), "data": data}
        return data

    except Exception as e:
        print(f"[momentum] Erro UniScore event {event_id}: {e}")
        return None


# ── Monitor de fundo: verifica jogos ao vivo a cada 5 min e salva os encerrados ──
def _background_monitor():
    """Thread daemon que varre os jogos ao vivo (UniScore) e salva quando FT."""
    print("[monitor] Thread de monitoramento iniciada.")
    time.sleep(60)  # Aguarda Flask subir
    while True:
        try:
            today     = datetime.now().strftime("%Y-%m-%d")
            live_list = _fetch_live_matches_for_monitor()
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
                    time.sleep(2)
            else:
                print(f"[monitor] {len(live_list)} ao vivo, todos já salvos ou sem jogos.")
        except Exception as e:
            print(f"[monitor] Erro geral: {e}")
        time.sleep(300)


# Inicia a thread de monitoramento (daemon = morre junto com o Flask)
threading.Thread(target=_background_monitor, daemon=True, name="MomentumMonitor").start()

# Restaura dados do GitHub ao iniciar (backtest + momentum_history + shotmap_history + cache)
threading.Thread(
    target=github_storage.sync_on_startup,
    args=(MOMENTUM_DIR, BACKTEST_DIR, DATA_DIR, SHOTMAP_DIR),
    daemon=True,
    name="GitHubSync"
).start()

# Mapa de Sugestões: padrão do dia + lista de sugestões travada — sem isso, cada
# redeploy no Railway apagava a memória e escolhia um padrão novo do zero no meio
# do dia (mesmo problema do backtest2.db, resolvido do mesmo jeito: sempre baixa
# a versão mais recente do GitHub, sobrescrevendo qualquer coisa local).
threading.Thread(
    target=github_storage.pull_directory,
    args=("mapa_cache", MAPA_CACHE_DIR),
    daemon=True,
    name="GitHubSyncMapa"
).start()

# Sinais do Dia (substitui o Mapa de Sugestões): só protege contra reinício
# no MESMO dia — a checagem de data em _sinais_load_from_disk já ignora o
# arquivo se ele for de um dia anterior, então não força (force=False).
threading.Thread(
    target=github_storage.pull_file,
    args=("sinais_dia_cache.json", os.path.join(DATA_DIR, "sinais_dia_cache.json")),
    daemon=True,
    name="GitHubSyncSinais"
).start()


@app.route("/api/radar/momentum/<event_id>")
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


@app.route("/api/radar/shotmap/<event_id>")
def api_shotmap(event_id):
    """Retorna mapa de chutes via UniScore para uma partida.
    Query params: casa, fora (usados para encontrar o ID no UniScore).
    Tenta primeiro no arquivo salvo, depois busca ao vivo."""
    from flask import request as flask_req
    casa = flask_req.args.get("casa", "")
    fora = flask_req.args.get("fora", "")

    # 1. Tenta arquivo já salvo no momentum_history
    today = datetime.now().strftime("%Y-%m-%d")
    save_file = os.path.join(MOMENTUM_DIR, f"{today}_{event_id}.json")
    if os.path.exists(save_file):
        try:
            with open(save_file, encoding="utf-8") as f:
                saved = json.load(f)
            shots = saved.get("shotmap", [])
            if shots:
                return jsonify({"ok": True, "shots": shots, "source": "saved"})
        except Exception:
            pass

    # 2. Busca ao vivo via UniScore (match por nome)
    uni_match = _find_uniscore_id(casa, fora)
    if not uni_match:
        # Tenta usando o event_id diretamente (IDs são compatíveis)
        uni_match = {"id": event_id, "homeId": "", "awayId": ""}

    try:
        rsm = http_req.get(
            f"{_UNISCORE_API}/football/event/{uni_match['id']}/shotmap",
            headers=_UNISCORE_HEADERS, timeout=12,
        )
        if rsm.status_code == 200:
            raw = rsm.json().get("data", {}).get("shotmap", [])
            shots = [
                {
                    "id":        s.get("id"),
                    "minute":    s.get("time", 0),
                    "isHome":    s.get("isHome", True),
                    "shotType":  s.get("shotType", "miss"),
                    "bodyPart":  s.get("bodyPart", ""),
                    "situation": s.get("situation", ""),
                    "player":    s.get("player", {}).get("shortName", ""),
                    "x":         s.get("playerCoordinates", {}).get("x", 0),
                    "y":         s.get("playerCoordinates", {}).get("y", 0),
                }
                for s in raw
            ]
            return jsonify({"ok": True, "shots": shots, "source": "live"})
    except Exception as e:
        print(f"[shotmap] Erro: {e}")

    return jsonify({"ok": False, "shots": [], "source": "none"}), 200


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


def _momentum_detect_chance_spikes(points, chance_pct=0.8, over_pct=0.6):
    """Porta da parte 'Grande Chance' de _live2DetectPressureMarkers (JS) — pico
    pontual (1-2 pontos) de pressão >= chance_pct do máximo daquele jogo, fora de
    qualquer janela sustentada (Momento Over, >=4 pontos >= over_pct do máximo).
    chance_pct/over_pct parametrizados pra permitir a calibração por grid search."""
    n = len(points)
    if n < 4:
        return []
    max_val = max((abs(p.get("value") or 0) for p in points), default=1) or 1
    over_thresh    = max_val * over_pct
    chance_thresh  = max_val * chance_pct

    over_windows = []
    i = 0
    while i < n:
        if abs(points[i].get("value") or 0) >= over_thresh:
            j = i
            while j < n and abs(points[j].get("value") or 0) >= over_thresh:
                j += 1
            if j - i >= 4:
                over_windows.append((i, j - 1))
            i = j
        else:
            i += 1

    def in_over_window(idx):
        return any(s <= idx <= e for s, e in over_windows)

    spikes = []
    i = 0
    while i < n:
        v = abs(points[i].get("value") or 0)
        if v >= chance_thresh and not in_over_window(i):
            j = i
            while j < n and abs(points[j].get("value") or 0) >= chance_thresh and not in_over_window(j):
                j += 1
            if j - i <= 2:
                spikes.append({
                    "minute": int(points[i].get("minute") or 0),
                    "is_home": (points[i].get("value") or 0) >= 0,
                })
            i = j
        else:
            i += 1
    return spikes


_MOMENTUM_ALL_MATCHES_CACHE = {"ts": 0, "data": None}
_MOMENTUM_ALL_MATCHES_TTL = 10 * 60  # 10min — evita reler ~2500 arquivos do disco
# a cada request, já que o único consumidor hoje (Grande Chance) também tem seu
# próprio cache de 30min por cima disso

def _momentum_load_all_matches():
    """Carrega (graphPoints, goals) de todo o momentum_history — cacheado por
    10min, pra não reler os ~2500 arquivos do disco a cada request (recarregar
    tudo repetidamente já chegou a derrubar o worker do Railway por estourar o
    timeout do gunicorn com 1 worker só)."""
    now = time.time()
    if _MOMENTUM_ALL_MATCHES_CACHE["data"] is not None and (now - _MOMENTUM_ALL_MATCHES_CACHE["ts"]) < _MOMENTUM_ALL_MATCHES_TTL:
        return _MOMENTUM_ALL_MATCHES_CACHE["data"]

    matches = []
    for fpath in glob.glob(os.path.join(MOMENTUM_DIR, "*.json")):
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        points = d.get("graphPoints") or []
        goals  = d.get("goals") or []
        if len(points) >= 4:
            matches.append((points, goals))

    _MOMENTUM_ALL_MATCHES_CACHE["data"] = matches
    _MOMENTUM_ALL_MATCHES_CACHE["ts"] = now
    return matches


def _momentum_eval_pattern(matches, chance_pct, over_pct, window_min):
    """Roda a detecção de picos com um limiar/janela específicos em cima da base
    já carregada e mede o lift real (taxa de acerto vs taxa-base) dessa combinação."""
    total_spikes = hits = total_goals = total_minutes = 0
    for points, goals in matches:
        total_minutes += max((p.get("minute") or 0) for p in points)
        total_goals   += len(goals)
        for sp in _momentum_detect_chance_spikes(points, chance_pct, over_pct):
            total_spikes += 1
            team = "home" if sp["is_home"] else "away"
            saiu_gol = any(
                g.get("team") == team
                and 0 <= (g.get("minute") or 0) - sp["minute"] <= window_min
                for g in goals
            )
            if saiu_gol:
                hits += 1

    hit_rate = (hits / total_spikes) if total_spikes else 0.0
    # Taxa-base: probabilidade de sair gol de UM time específico numa janela aleatória
    # do mesmo tamanho — aproximação a partir da taxa média de gols/minuto da base,
    # dividida por 2 (metade das vezes o gol é do time "certo" por acaso)
    gols_por_minuto = (total_goals / total_minutes) if total_minutes else 0.0
    baseline_rate = (gols_por_minuto * window_min) / 2
    lift = (hit_rate / baseline_rate) if baseline_rate else 0.0

    return {
        "chance_pct":    chance_pct,
        "over_pct":      over_pct,
        "window_min":    window_min,
        "total_spikes":  total_spikes,
        "hits":          hits,
        "hit_rate":      round(hit_rate, 4),
        "baseline_rate": round(baseline_rate, 4),
        "lift":          round(lift, 3),
    }


_CHANCE_PATTERN_CACHE = {"ts": 0, "data": None}
_CHANCE_PATTERN_TTL = 30 * 60  # 30 minutos — reescanear/recalibrar a base inteira a cada request seria lento
_CHANCE_PATTERN_MIN_SAMPLE = 30
_CHANCE_PATTERN_MIN_LIFT = 1.3
# Grid de combinações testadas — quanto mais a base cresce, mais confiável fica a
# escolha da melhor combinação (thresholds mais "esquisitos"/específicos só ganham
# quando o tamanho da amostra sustenta, senão MIN_SAMPLE já descarta)
_CHANCE_PATTERN_THRESH_GRID = [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]
_CHANCE_PATTERN_WINDOW_GRID = [5, 8, 10, 12, 15]
# Limiar do "Momento Over" (janela sustentada) que delimita o que NÃO conta como pico
# pontual — antes ficava fixo em 0.6; agora também entra na busca, só descartando
# combinações onde over_pct >= chance_pct (não faria sentido: a janela sustentada
# "engoliria" o próprio pico antes dele contar como Grande Chance)
_CHANCE_PATTERN_OVER_GRID = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70]

@app.route("/api/momentum/chance_pattern_stats")
def api_momentum_chance_pattern_stats():
    """Calibra dinamicamente o indicador 'Grande Chance' (Ao Vivo 2) contra toda a
    base de jogos salvos em momentum_history: testa uma grade de combinações de
    limiar de pico (60%-90% do máximo), limiar da janela sustentada que separa o pico
    (45%-70%) e janela pós-pico (5-15min), e escolhe a combinação com o maior lift
    real (taxa de acerto vs taxa-base) entre as que têm amostra suficiente. Conforme
    mais jogos vão sendo salvos, essa escolha muda sozinha — não é um limiar fixo,
    é recalculado a cada 30min em cima da base atual."""
    now = time.time()
    if _CHANCE_PATTERN_CACHE["data"] and (now - _CHANCE_PATTERN_CACHE["ts"]) < _CHANCE_PATTERN_TTL:
        return jsonify(_CHANCE_PATTERN_CACHE["data"])

    matches = _momentum_load_all_matches()

    resultados = [
        _momentum_eval_pattern(matches, chance_pct, over_pct, window_min)
        for chance_pct in _CHANCE_PATTERN_THRESH_GRID
        for over_pct in _CHANCE_PATTERN_OVER_GRID
        for window_min in _CHANCE_PATTERN_WINDOW_GRID
        if over_pct < chance_pct
    ]

    candidatos = [r for r in resultados if r["total_spikes"] >= _CHANCE_PATTERN_MIN_SAMPLE]
    melhor = max(candidatos, key=lambda r: r["lift"]) if candidatos else None
    valido = bool(melhor and melhor["lift"] >= _CHANCE_PATTERN_MIN_LIFT)

    data = {
        "matches_used":  len(matches),
        "grid_tested":   len(resultados),
        "melhor":        melhor,
        "valido":        valido,
        # Campos usados pelo frontend pra desenhar o marcador com o limiar calibrado
        "chance_pct":    melhor["chance_pct"] if valido else 0.8,
        "over_pct":      melhor["over_pct"] if valido else 0.6,
        "window_min":    melhor["window_min"] if valido else 10,
        "lift":          melhor["lift"] if melhor else 0.0,
    }
    _CHANCE_PATTERN_CACHE["data"] = data
    _CHANCE_PATTERN_CACHE["ts"]   = now
    return jsonify(data)


@app.route("/api/momentum/history/<event_id>")
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


@app.route("/api/shotmap/history")
def api_shotmap_history():
    """Lista todos os shotmaps salvos em shotmap_history/."""
    files = sorted(glob.glob(os.path.join(SHOTMAP_DIR, "*.json")), reverse=True)
    result = []
    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            result.append({
                "event_id":    d.get("event_id"),
                "date":        d.get("date"),
                "casa":        d.get("casa"),
                "fora":        d.get("fora"),
                "liga":        d.get("liga"),
                "score":       d.get("score", {}),
                "total_shots": d.get("total_shots", len(d.get("shotmap", []))),
                "file":        os.path.basename(fpath),
            })
        except Exception:
            continue
    return jsonify({"total": len(result), "matches": result})


@app.route("/api/shotmap/history/<event_id>")
def api_shotmap_history_detail(event_id):
    """Retorna shotmap completo de uma partida específica."""
    files = glob.glob(os.path.join(SHOTMAP_DIR, f"*_{event_id}.json"))
    if not files:
        return jsonify({"error": "not found"}), 404
    try:
        with open(files[0], encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/shotmap/patterns")
def api_shotmap_patterns():
    """Agrega padrões de todos os shotmaps do momentum_history."""
    # Coleta todos os chutes de todos os arquivos
    all_shots = []
    match_count = 0
    seen_ids = set()

    for fpath in sorted(glob.glob(os.path.join(MOMENTUM_DIR, "*.json"))):
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            shots = d.get("shotmap", [])
            if not shots:
                continue
            event_id = d.get("event_id", "")
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            match_count += 1
            score = d.get("score", {})
            home_g = score.get("home", 0) or 0
            away_g = score.get("away", 0) or 0
            if home_g > away_g:
                match_result = "casa"
            elif away_g > home_g:
                match_result = "vis"
            else:
                match_result = "emp"
            for s in shots:
                all_shots.append({**s, "match_result": match_result})
        except Exception:
            pass

    # Também lê do shotmap_history (arquivos que podem não estar no momentum)
    for fpath in sorted(glob.glob(os.path.join(SHOTMAP_DIR, "*.json"))):
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            event_id = d.get("event_id", "")
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            shots = d.get("shotmap", [])
            if not shots:
                continue
            match_count += 1
            score = d.get("score", {})
            home_g = score.get("home", 0) or 0
            away_g = score.get("away", 0) or 0
            if home_g > away_g:
                match_result = "casa"
            elif away_g > home_g:
                match_result = "vis"
            else:
                match_result = "emp"
            for s in shots:
                all_shots.append({**s, "match_result": match_result})
        except Exception:
            pass

    if not all_shots:
        return jsonify({"total_shots": 0, "total_matches": 0})

    total = len(all_shots)

    # ── Por desfecho (shotType) ──────────────────────────────────────────
    from collections import defaultdict
    outcome_counts = defaultdict(int)
    for s in all_shots:
        outcome_counts[s.get("shotType", "unknown")] += 1

    # ── Por parte do corpo ───────────────────────────────────────────────
    body_counts = defaultdict(int)
    body_goals  = defaultdict(int)
    for s in all_shots:
        bp = s.get("bodyPart", "unknown")
        body_counts[bp] += 1
        if s.get("shotType") == "goal":
            body_goals[bp] += 1

    # ── Por situação ─────────────────────────────────────────────────────
    sit_counts = defaultdict(int)
    sit_goals  = defaultdict(int)
    for s in all_shots:
        st = s.get("situation", "unknown")
        sit_counts[st] += 1
        if s.get("shotType") == "goal":
            sit_goals[st] += 1

    # ── Casa vs Visitante ────────────────────────────────────────────────
    home_shots = [s for s in all_shots if s.get("isHome")]
    away_shots = [s for s in all_shots if not s.get("isHome")]
    home_goals = sum(1 for s in home_shots if s.get("shotType") == "goal")
    away_goals = sum(1 for s in away_shots if s.get("shotType") == "goal")

    # ── Por zona (x = distância do gol) ─────────────────────────────────
    def classify_zone(x):
        x = x or 0
        if x < 12:
            return "area_pequena"
        elif x < 32:
            return "area_grande"
        elif x < 55:
            return "meia_distancia"
        else:
            return "longa_distancia"

    zone_shots = defaultdict(int)
    zone_goals = defaultdict(int)
    for s in all_shots:
        z = classify_zone(s.get("x", 50))
        zone_shots[z] += 1
        if s.get("shotType") == "goal":
            zone_goals[z] += 1

    zone_labels = [
        ("area_pequena",    "Área Pequena",   "x < 12m"),
        ("area_grande",     "Área Grande",    "12-32m"),
        ("meia_distancia",  "Meia Distância", "32-55m"),
        ("longa_distancia", "Longa Distância","55m+"),
    ]
    by_zone = []
    for key, label, desc in zone_labels:
        n = zone_shots[key]
        g = zone_goals[key]
        by_zone.append({
            "key": key, "label": label, "desc": desc,
            "shots": n, "goals": g,
            "goal_pct": round(g / n * 100, 1) if n else 0,
        })

    # ── Por período (minuto) ─────────────────────────────────────────────
    buckets_def = [
        ("1–15", 1, 15), ("16–30", 16, 30), ("31–45", 31, 45),
        ("46–60", 46, 60), ("61–75", 61, 75), ("76–90+", 76, 200),
    ]
    by_minute = []
    for label, lo, hi in buckets_def:
        sl = [s for s in all_shots if lo <= (s.get("minute") or 0) <= hi]
        gl = [s for s in sl if s.get("shotType") == "goal"]
        by_minute.append({
            "label": label, "shots": len(sl), "goals": len(gl),
            "goal_pct": round(len(gl) / len(sl) * 100, 1) if sl else 0,
        })

    # ── Por resultado do jogo ────────────────────────────────────────────
    result_shots = defaultdict(int)
    result_goals = defaultdict(int)
    for s in all_shots:
        r = s.get("match_result", "emp")
        result_shots[r] += 1
        if s.get("shotType") == "goal":
            result_goals[r] += 1

    def _pct(a, b):
        return round(a / b * 100, 1) if b else 0

    return jsonify({
        "total_shots":   total,
        "total_matches": match_count,
        "by_outcome": dict(outcome_counts),
        "by_body_part": {
            bp: {"shots": body_counts[bp], "goals": body_goals[bp],
                 "goal_pct": _pct(body_goals[bp], body_counts[bp])}
            for bp in body_counts
        },
        "by_situation": {
            st: {"shots": sit_counts[st], "goals": sit_goals[st],
                 "goal_pct": _pct(sit_goals[st], sit_counts[st])}
            for st in sit_counts
        },
        "home_away": {
            "home": {"total": len(home_shots), "goals": home_goals,
                     "goal_pct": _pct(home_goals, len(home_shots))},
            "away": {"total": len(away_shots), "goals": away_goals,
                     "goal_pct": _pct(away_goals, len(away_shots))},
        },
        "by_zone":   by_zone,
        "by_minute": by_minute,
        "by_result": {
            r: {"shots": result_shots[r], "goals": result_goals[r],
                "goal_pct": _pct(result_goals[r], result_shots[r])}
            for r in result_shots
        },
    })


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
    """Busca partidas salvas com padrão de momentum similar ao atual.

    Usa uma comparação leve (soma/tendência da janela, não o vetor ponto-a-ponto
    completo) pra ficar rápido mesmo varrendo milhares de jogos salvos — comparar
    cada posição de cada arquivo com distância euclidiana completa era o gargalo
    que deixava a aba Análise lenta."""
    try:
        body    = request.get_json(force=True) or {}
        pts_raw = body.get("points", [])
        W       = int(body.get("window", 8))
        LOOKAHEAD = int(body.get("lookahead", 10))

        if len(pts_raw) < W:
            return jsonify({"similar": [], "total": 0, "goal_home": 0, "goal_away": 0, "goal_none": 0})

        cur_vals    = [float(p.get("value", 0)) for p in pts_raw[-W:]]
        cur_signal  = sum(cur_vals) / W                       # tendência média da janela
        cur_swing   = max(cur_vals) - min(cur_vals) if cur_vals else 0  # volatilidade

        STRIDE = 2  # varre de 2 em 2 minutos em vez de todo minuto — ~2x mais rápido

        similar = []
        for d in _get_momentum_files_cached():
            try:
                pt_list = d["pt_list"]
                goals   = d["goals"]
                if len(pt_list) < W + 2:
                    continue

                best_dist = float("inf")
                best_outcome = "none"
                best_min = 0

                for i in range(W, len(pt_list), STRIDE):
                    win = [pt_list[j][1] for j in range(i - W, i)]
                    win_signal = sum(win) / W
                    win_swing  = max(win) - min(win) if win else 0
                    dist = abs(win_signal - cur_signal) + 0.3 * abs(win_swing - cur_swing)
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
        total = len(similar)

        # Top 5 para exibição na lista
        top_display = similar[:5]

        # Estatísticas em TODAS as partidas com distância <= limiar (sem tamanho fixo) —
        # ou seja, pega quantas forem realmente parecidas com o padrão atual, nem mais nem menos.
        DIST_THRESHOLD = float(body.get("dist_threshold", 8))
        MIN_SAMPLE = 10  # piso pra evitar % instável com amostra minúscula
        top_stat = [s for s in similar if s["distance"] <= DIST_THRESHOLD]
        if len(top_stat) < MIN_SAMPLE:
            top_stat = similar[:MIN_SAMPLE]
        STAT_N = len(top_stat)

        return jsonify({
            "similar":    top_display,
            "total":      total,
            "stat_n":     STAT_N,
            "goal_home":  sum(1 for s in top_stat if s["outcome"] == "home"),
            "goal_away":  sum(1 for s in top_stat if s["outcome"] == "away"),
            "goal_none":  sum(1 for s in top_stat if s["outcome"] == "none"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/momentum/similar-scores", methods=["POST"])
def api_momentum_similar_scores():
    """Identifica o placar atual (casa/visitante) e busca no histórico partidas que
    tiveram esse MESMO placar em algum momento do jogo, informando os placares finais
    mais comuns entre elas. Sem análise de gráfico — só o placar, bem mais rápido."""
    try:
        body     = request.get_json(force=True) or {}
        cur_casa = int(body.get("cur_casa", 0))
        cur_fora = int(body.get("cur_fora", 0))

        counts = {}
        total_checked = 0
        for d in _get_momentum_files_cached():
            try:
                goals = sorted(d["goals"], key=lambda g: float(g.get("minute", 0)))
                score_final = d.get("score") or {}
                fh, fa = score_final.get("home"), score_final.get("away")
                if fh is None or fa is None:
                    continue
                total_checked += 1

                # Recria o placar minuto a minuto pra ver se em algum ponto bateu com o atual
                h = a = 0
                hit = (cur_casa == 0 and cur_fora == 0)  # todo jogo começa 0-0
                for g in goals:
                    if g.get("team") == "home": h += 1
                    elif g.get("team") == "away": a += 1
                    if h == cur_casa and a == cur_fora:
                        hit = True
                        break
                    if h > cur_casa or a > cur_fora:
                        break  # passou do placar atual sem bater — não teve esse momento

                if hit:
                    key = f"{fh}-{fa}"
                    counts[key] = counts.get(key, 0) + 1
            except Exception:
                continue

        stat_n = sum(counts.values())
        if stat_n == 0:
            return jsonify({"scores": [], "total": total_checked, "stat_n": 0})

        scores = sorted(
            [{"score": k, "count": v, "pct": round(v / stat_n * 100)} for k, v in counts.items()],
            key=lambda x: -x["count"]
        )[:4]

        return jsonify({"scores": scores, "total": total_checked, "stat_n": stat_n})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _shotmap_feature_vector(shots):
    """Converte uma lista de chutes num vetor de 10 posições: contagem por zona
    (Área Pequena / Área Grande / Meia Distância / Longa Distância) e chutes no
    alvo, separado por casa/visitante. Mesmas zonas usadas no mapa de chutes visual."""
    zones = {"casa": [0, 0, 0, 0], "fora": [0, 0, 0, 0]}
    on_target = {"casa": 0, "fora": 0}
    for s in shots:
        side = "casa" if s.get("isHome") else "fora"
        x = float(s.get("x", 50))
        if x <= 12:
            zi = 0
        elif x <= 32:
            zi = 1
        elif x <= 45:
            zi = 2
        else:
            zi = 3
        zones[side][zi] += 1
        if s.get("shotType") in ("goal", "save"):
            on_target[side] += 1
    vec = zones["casa"] + zones["fora"] + [on_target["casa"], on_target["fora"]]
    total = sum(abs(v) for v in vec) or 1
    return [v / total for v in vec]


@app.route("/api/shotmap/similar", methods=["POST"])
def api_shotmap_similar():
    """Busca partidas salvas com padrão de mapa de chutes parecido e informa
    a % de jogos em que casa/visitante marcou gol nesse padrão."""
    try:
        body  = request.get_json(force=True) or {}
        shots = body.get("shots", [])
        if not shots:
            return jsonify({"similar": [], "total": 0, "goal_home": 0, "goal_away": 0, "goal_none": 0})

        cur_vec = _shotmap_feature_vector(shots)

        similar = []
        for d in _get_momentum_files_cached():
            try:
                hist_shots = d["shotmap"]
                if not hist_shots:
                    continue
                score = d.get("score", {}) or {}
                gh, ga = score.get("home"), score.get("away")
                if gh is None or ga is None:
                    continue

                hist_vec = _shotmap_feature_vector(hist_shots)
                dist = sum((a - b) ** 2 for a, b in zip(cur_vec, hist_vec)) ** 0.5

                outcome = "home" if gh > 0 and ga == 0 else \
                          "away" if ga > 0 and gh == 0 else \
                          "both" if gh > 0 and ga > 0 else "none"

                similar.append({
                    "casa": d.get("casa", "—"), "fora": d.get("fora", "—"),
                    "liga": d.get("liga", ""), "date": d.get("date", ""),
                    "outcome": outcome, "distance": round(dist, 3),
                    "placar": f"{gh}-{ga}",
                    "home_scored": gh > 0, "away_scored": ga > 0,
                })
            except Exception:
                continue

        similar.sort(key=lambda x: x["distance"])
        total = len(similar)
        STAT_N = min(30, total)
        top_stat = similar[:STAT_N]

        return jsonify({
            "similar":   similar[:5],
            "total":     total,
            "stat_n":    STAT_N,
            "goal_home": sum(1 for s in top_stat if s["home_scored"]),
            "goal_away": sum(1 for s in top_stat if s["away_scored"]),
            "goal_none": sum(1 for s in top_stat if not s["home_scored"] and not s["away_scored"]),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Padrões de Estatísticas ──────────────────────────────────────────────────

# Estatísticas que queremos rastrear — suporta keys SofaScore (camelCase) e UniScore (snake_case)
_STAT_LABELS = {
    # ── Posse & Ataque ──────────────────────────────────────────────────────
    "ball_possession":        "Posse de Bola (%)",
    "ballPossession":         "Posse de Bola (%)",
    "shots":                  "Total de Chutes",
    "totalShots":             "Total de Chutes",
    "shots_on_target":        "Chutes no Alvo",
    "shotsOnTarget":          "Chutes no Alvo",
    "blocked_shots":          "Chutes Bloqueados",
    "shots_inside_box":       "Chutes Dentro da Área",
    "shots_outside_box":      "Chutes Fora da Área",
    "touches_in_box":         "Toques na Área Adversária",
    "big_chances":            "Grandes Chances",
    "bigChancesCreated":      "Grandes Chances",
    "corner_kicks":           "Escanteios",
    "cornerKicks":            "Escanteios",
    "freekicks":              "Cobranças de Falta",
    # ── Passes ──────────────────────────────────────────────────────────────
    "passes":                 "Total de Passes",
    "totalPasses":            "Total de Passes",
    "pass_in_final_third":    "Passes no Terço Final",
    "final_third_entries":    "Entradas no Último Terço",
    "long_balls":             "Lançamentos Longos",
    "crosses_accuracy":       "Cruzamentos",
    "throw_in":               "Arremessos Laterais",
    # ── Duelos & Dribles ────────────────────────────────────────────────────
    "duels":                  "Duelos Totais",
    "ground_duels":           "Duelos no Chão",
    "aerial_duels":           "Duelos Aéreos",
    "dribble":                "Dribles",
    "dispossessed":           "Perda de Posse (Dribble)",
    # ── Defesa ──────────────────────────────────────────────────────────────
    "tackles":                "Desarmes",
    "tacklesWon":             "Desarmes",
    "interceptions":          "Intercepções",
    "recoveries":             "Recuperações",
    "clearances":             "Rebotes/Afastamentos",
    # ── Goleiro ─────────────────────────────────────────────────────────────
    "saves":                  "Defesas (GK)",
    "goal_kicks":             "Tiros de Meta",
    # ── Disciplina ──────────────────────────────────────────────────────────
    "fouls":                  "Faltas Cometidas",
    "foulsCommitted":         "Faltas Cometidas",
    "was_fouled":             "Sofreu Falta",
    "yellow_cards":           "Cartões Amarelos",
    "yellowCards":            "Cartões Amarelos",
    # ── Perda de Posse ──────────────────────────────────────────────────────
    "poss_losts":             "Perda de Posse Total",
    # ── Indicadores derivados (calculados no save) ───────────────────────
    "xg":                     "xG (Gols Esperados)",
    "pressure_home_dom_pct":  "Dominância de Pressão (%)",
    "pressure_overall_avg":   "Pressão Média (saldo)",
    "pressure_momentum_swings": "Trocas de Dominância",
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


def _extract_stats(statistics_raw):
    """Extrai {key: (home_val, away_val)} do período ALL.
    Aceita dois formatos:
      - Novo (flat dict): {"ball_possession": {"homeValue": 55, "awayValue": 45, ...}, ...}
      - Antigo (lista SofaScore): [{"period":"ALL","groups":[{"statisticsItems":[...]}]}]
    """
    result = {}

    # ── Formato novo: dict flat ────────────────────────────────────────────
    if isinstance(statistics_raw, dict):
        for key, item in statistics_raw.items():
            if not isinstance(item, dict):
                continue
            hv = _parse_stat_val(item.get("homeValue") or item.get("home"))
            av = _parse_stat_val(item.get("awayValue") or item.get("away"))
            if hv is not None or av is not None:
                result[key] = (hv, av)
        return result

    # ── Formato antigo: lista com period/groups/statisticsItems ───────────
    if isinstance(statistics_raw, list):
        for period_data in statistics_raw:
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

            # ── Determina placar ──────────────────────────────────────────
            # Prefere score salvo, fallback para contar goals
            score = d.get("score", {})
            if score and "home" in score and "away" in score:
                gh = int(score["home"])
                ga = int(score["away"])
            else:
                goals = d.get("goals", [])
                gh = sum(1 for g in goals if g.get("team") == "home")
                ga = sum(1 for g in goals if g.get("team") == "away")
            tot = gh + ga

            # ── Estatísticas base ─────────────────────────────────────────
            stats_raw = d.get("statistics", [])
            stat_vals = _extract_stats(stats_raw) if stats_raw else {}

            # ── xG como métricas extras ───────────────────────────────────
            xg = d.get("xg", {})
            if xg and xg.get("home") is not None:
                stat_vals["xg"] = (float(xg["home"]), float(xg["away"]))

            # ── Pressure summary como métricas extras ─────────────────────
            ps = d.get("pressure_summary", {})
            if ps:
                if ps.get("home_dominance_pct") is not None:
                    stat_vals["pressure_home_dom_pct"] = (float(ps["home_dominance_pct"]), 100.0 - float(ps["home_dominance_pct"]))
                if ps.get("overall_avg") is not None:
                    stat_vals["pressure_overall_avg"] = (float(ps["overall_avg"]), None)
                if ps.get("momentum_swings") is not None:
                    stat_vals["pressure_momentum_swings"] = (float(ps["momentum_swings"]), None)

            if not stat_vals:
                continue

            active = set()
            if gh > ga:   active.add("casaV")
            elif ga > gh: active.add("visV")
            else:         active.add("emp")
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


# ── Indicadores ao vivo ──────────────────────────────────────────────────────

def _profile_similarity(current: dict, profile: dict) -> float | None:
    """Calcula similaridade 0-100 entre stats atuais e perfil histórico de um resultado.
    current = {key: (home_val, away_val)}
    profile = {key_h: avg, key_a: avg}  (formato de outcomes_result)
    """
    diffs = []
    for key, vals in current.items():
        hv, av = vals if isinstance(vals, tuple) else (vals, None)
        h_hist = profile.get(key + "_h")
        a_hist = profile.get(key + "_a")
        if h_hist is not None and hv is not None and h_hist > 0:
            diffs.append(abs(float(hv) - float(h_hist)) / float(h_hist))
        if a_hist is not None and av is not None and a_hist > 0:
            diffs.append(abs(float(av) - float(a_hist)) / float(a_hist))
    if not diffs:
        return None
    avg_diff = sum(diffs) / len(diffs)
    return round(1 / (1 + avg_diff) * 100, 1)


def _momentum_contrib(outcome: str, ps: dict) -> float:
    """Contribuição do momentum para um resultado (0-100)."""
    dom = ps.get("home_dominance_pct", 50.0)
    if outcome == "casaV":
        return round(min(dom, 100), 1)
    if outcome == "visV":
        return round(min(100 - dom, 100), 1)
    if outcome == "emp":
        return round(max(0, 100 - abs(dom - 50) * 2), 1)
    if outcome == "o25":
        swings = ps.get("momentum_swings", 0)
        return round(min(50 + swings * 2, 100), 1)
    if outcome == "u25":
        swings = ps.get("momentum_swings", 0)
        return round(max(0, 50 - swings * 2), 1)
    if outcome == "btts":
        return round(min(50 + abs(ps.get("overall_avg", 0)) * 30, 100), 1)
    return 50.0


def _xg_contrib(outcome: str, xg: dict) -> float | None:
    """Contribuição do xG para um resultado (0-100)."""
    if not xg or xg.get("home") is None:
        return None
    h, a = float(xg.get("home", 0) or 0), float(xg.get("away", 0) or 0)
    diff = h - a
    # sigmoid suavizada: 0 diff → 50, +2 diff → ~85, -2 diff → ~15
    import math
    sig = lambda x: 1 / (1 + math.exp(-x * 0.8))
    if outcome == "casaV":
        return round(sig(diff) * 100, 1)
    if outcome == "visV":
        return round(sig(-diff) * 100, 1)
    if outcome == "emp":
        return round((1 - abs(diff) / max(h + a + 0.01, 1)) * 100, 1)
    if outcome == "o25":
        total = h + a
        return round(min(total / 3 * 100, 100), 1)
    if outcome == "u25":
        total = h + a
        return round(max(0, (1 - total / 3) * 100), 1)
    if outcome == "btts":
        return round(min(h, 1) * min(a, 1) * 100, 1)
    return 50.0


@app.route("/api/momentum/indicators/<event_id>")
def api_indicators(event_id):
    """Indicadores dinâmicos ao vivo: perfil histórico + momentum + xG + value de odds."""
    from flask import request as flask_req
    casa = flask_req.args.get("casa", "")
    fora = flask_req.args.get("fora", "")

    # ── Dados atuais da partida ───────────────────────────────────────────
    with _momentum_lock:
        cached = _momentum_cache.get(event_id)
    mdata = (cached or {}).get("data") or {}

    if not mdata:
        # Tenta buscar se não estiver em cache
        mdata = _process_momentum(event_id, casa, fora) or {}

    stats_raw   = mdata.get("statistics", {})
    current     = _extract_stats(stats_raw) if stats_raw else {}
    ps          = mdata.get("pressure_summary", {})
    xg          = mdata.get("xg", {})
    open_odds   = mdata.get("opening_odds", {})
    goals       = mdata.get("goals", [])
    score_h     = sum(1 for g in goals if g.get("team") == "home")
    score_a     = sum(1 for g in goals if g.get("team") == "away")

    # Adiciona momentum e xG como pseudo-stats para o perfil
    if ps.get("home_dominance_pct") is not None:
        current["pressure_home_dom_pct"] = (ps["home_dominance_pct"], 100 - ps["home_dominance_pct"])
    if xg.get("home") is not None:
        current["xg"] = (float(xg["home"]), float(xg["away"]))

    # ── Padrões históricos ────────────────────────────────────────────────
    global _stats_patterns_cache, _odds_patterns_cache
    if _stats_patterns_cache["data"] is None:
        with app.test_request_context():
            api_stats_patterns()
    hist_data     = _stats_patterns_cache.get("data") or {}
    outcomes_hist = hist_data.get("outcomes", {})
    total_hist    = hist_data.get("total", 0)

    if _odds_patterns_cache["data"] is None:
        with app.test_request_context():
            api_odds_patterns()
    odds_data    = _odds_patterns_cache.get("data") or {}
    odds_buckets = odds_data.get("buckets", [])

    # ── Value Score (edge de odds) ────────────────────────────────────────
    value = {}
    if open_odds and open_odds.get("h"):
        try:
            h_odd = float(open_odds["h"])
            for b in odds_buckets:
                lbl = b["label"]
                # converte label "1.20-1.40" para faixa numérica
                parts = lbl.replace(">","").split("-")
                lo = float(parts[0])
                hi = float(parts[1]) if len(parts) > 1 else 99.0
                if lo <= h_odd <= hi:
                    value = {
                        "label":    lbl,
                        "n":        b["n"],
                        "edge_h":   b.get("edge_h", 0),
                        "edge_x":   b.get("edge_x", 0),
                        "edge_a":   b.get("edge_a", 0),
                        "casaV_pct": b.get("casaV_pct", 0),
                        "emp_pct":   b.get("emp_pct", 0),
                        "visV_pct":  b.get("visV_pct", 0),
                        "o25_pct":   b.get("o25_pct", 0),
                    }
                    break
        except Exception:
            pass

    # ── Peso dinâmico por volume de dados ────────────────────────────────
    # Com poucos dados, momentum/xG pesam mais; com muitos, perfil pesa mais
    w_profile  = min(0.6, max(0.15, total_hist / 200))   # 0.15 → 0.60
    w_momentum = 0.25
    w_xg       = max(0.15, 0.40 - w_profile * 0.4)
    w_value    = 1 - w_profile - w_momentum - w_xg
    w_value    = max(0, min(w_value, 0.20))

    # ── Calcula indicador composto por resultado ──────────────────────────
    OUTCOMES = {
        "casaV": "Casa Vence",
        "emp":   "Empate",
        "visV":  "Visitante",
        "o25":   "Over 2.5",
        "u25":   "Under 2.5",
        "btts":  "Ambos Marcam",
        "nbtts": "Não BTTS",
    }

    indicators = {}
    for oc, label in OUTCOMES.items():
        oc_hist = outcomes_hist.get(oc, {})
        n       = oc_hist.get("n", 0)

        # 1. Perfil
        profile_sim = _profile_similarity(current, oc_hist) if (n >= 3 and current) else None

        # 2. Momentum
        mom_score = _momentum_contrib(oc, ps) if ps else None

        # 3. xG
        xg_score = _xg_contrib(oc, xg) if xg else None

        # 4. Value edge normalizado 0-100 (edge de -20% a +20%)
        val_score = None
        if value:
            edge = value.get(f"edge_{oc[:4]}", value.get("edge_h" if oc == "casaV" else
                             "edge_x" if oc == "emp" else
                             "edge_a" if oc == "visV" else None))
            if edge is not None:
                val_score = round(min(100, max(0, 50 + edge * 2.5)), 1)

        # Composição ponderada com os componentes disponíveis
        components = []
        weights_used = []
        if profile_sim is not None:
            components.append(profile_sim * w_profile)
            weights_used.append(w_profile)
        if mom_score is not None:
            components.append(mom_score * w_momentum)
            weights_used.append(w_momentum)
        if xg_score is not None:
            components.append(xg_score * w_xg)
            weights_used.append(w_xg)
        if val_score is not None and value.get("n", 0) >= 5:
            components.append(val_score * w_value)
            weights_used.append(w_value)

        if components:
            w_sum   = sum(weights_used)
            composite = round(sum(components) / w_sum, 1)
        else:
            composite = None

        indicators[oc] = {
            "label":       label,
            "composite":   composite,
            "profile":     profile_sim,
            "momentum":    mom_score,
            "xg":          xg_score,
            "value_edge":  value.get("edge_h" if oc=="casaV" else "edge_x" if oc=="emp" else "edge_a", None) if value else None,
            "n_hist":      n,
        }

    return jsonify({
        "ok":            True,
        "event_id":      event_id,
        "total_hist":    total_hist,
        "weights":       {"profile": round(w_profile,2), "momentum": round(w_momentum,2),
                          "xg": round(w_xg,2), "value": round(w_value,2)},
        "score":         {"home": score_h, "away": score_a},
        "indicators":    indicators,
        "pressure":      ps,
        "xg":            xg,
        "value":         value,
        "has_live_data": bool(current),
    })


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
    """Retorna lista de eventos de futebol do dia — TODOS os locales + paginação,
    combinando os jogos AO VIVO (live-v2) com os AINDA NÃO COMEÇADOS (scheduled-events).
    O endpoint de scheduled-events sozinho não inclui partidas já em andamento, por isso
    a combinação — senão a maioria dos jogos ao vivo não é encontrada pra enriquecimento.
    Todas as chamadas (locale × fonte) rodam em PARALELO — sequencial chegava a travar
    dezenas de segundos numa única busca. Cache de 30s."""
    from concurrent.futures import ThreadPoolExecutor
    global _uni_events_cache
    if time.time() - _uni_events_cache["ts"] < 30 and _uni_events_cache["data"]:
        return _uni_events_cache["data"]
    today = datetime.now().strftime("%Y-%m-%d")
    all_by_id = {}
    lock = threading.Lock()

    def _paginate_locale(url_fmt, locale):
        page = 1
        while True:
            try:
                r = http_req.post(url_fmt.format(locale=locale), json={"page": page},
                                   headers=UNISCORE_HEADERS, timeout=6)
                data   = r.json().get("data", {})
                events = data.get("events", [])
                pag    = data.get("pagination", {})
                with lock:
                    for ev in events:
                        eid = ev.get("id")
                        if eid and eid not in all_by_id:
                            all_by_id[eid] = ev
                if not pag.get("hasNextPage"):
                    break
                page += 1
                if page > 4:   # safety cap (mais baixo — já roda em paralelo por locale)
                    break
            except Exception:
                break

    urls = [
        f"{UNISCORE_BASE}/sport/football/events/live-v2/locale/{{locale}}",
        f"{UNISCORE_BASE}/sport/football/scheduled-events-pagination-v2/{today}/locale/{{locale}}/type/all?language=pt-BR",
    ]
    tasks = [(url_fmt, locale) for url_fmt in urls for locale in _UNISCORE_LOCALES]
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = [ex.submit(_paginate_locale, url_fmt, locale) for url_fmt, locale in tasks]
        for fut in futures:
            try:
                fut.result(timeout=15)
            except Exception:
                pass

    events = list(all_by_id.values())
    _uni_events_cache = {"ts": time.time(), "data": events}
    print(f"[uniscore] {len(events)} eventos de hoje (ao vivo + agendados, todos os locales, em paralelo)")
    return events


# Seleções nacionais mudam MUITO de nome entre idiomas (ex: "Brasil"/"Brazil",
# "Alemanha"/"Germany") — ao contrário de clubes, que costumam ser parecidos. O Uniscore
# às vezes devolve o nome em inglês pra um evento e em português pra outro no mesmo
# request (mistura de locale), então sem esse mapa a busca por seleção falha silenciosamente.
_COUNTRY_ALIASES = {
    "brasil": "brazil", "alemanha": "germany", "espanha": "spain", "franca": "france",
    "inglaterra": "england", "italia": "italy", "holanda": "netherlands",
    "paises baixos": "netherlands", "belgica": "belgium", "suica": "switzerland",
    "suecia": "sweden", "noruega": "norway", "dinamarca": "denmark", "polonia": "poland",
    "austria": "austria", "escocia": "scotland", "irlanda": "ireland",
    "irlanda do norte": "northern ireland", "pais de gales": "wales", "gales": "wales",
    "russia": "russia", "ucrania": "ukraine", "sercia": "serbia", "servia": "serbia",
    "croacia": "croatia", "romenia": "romania", "grecia": "greece", "turquia": "turkey",
    "portugal": "portugal", "mexico": "mexico", "estados unidos": "united states",
    "eua": "united states", "canada": "canada", "argentina": "argentina",
    "uruguai": "uruguay", "paraguai": "paraguay", "chile": "chile", "colombia": "colombia",
    "equador": "ecuador", "peru": "peru", "venezuela": "venezuela", "bolivia": "bolivia",
    "japao": "japan", "coreia do sul": "south korea", "coreia do norte": "north korea",
    "china": "china", "australia": "australia", "arabia saudita": "saudi arabia",
    "ira": "iran", "iraque": "iraq", "egito": "egypt", "marrocos": "morocco",
    "argelia": "algeria", "tunisia": "tunisia", "nigeria": "nigeria", "senegal": "senegal",
    "camaroes": "cameroon", "gana": "ghana", "africa do sul": "south africa",
    "costa do marfim": "ivory coast", "cabo verde": "cape verde", "nova zelandia": "new zealand",
}


# ── Memória de apelidos Uniscore — toda vez que a busca fuzzy abaixo acha um evento com
# confiança boa, grava aqui o nome EXATO que o Uniscore usa pra aquele confronto
# (StatArea "Brasil"/"Noruega" → Uniscore "Brazil"/"Norway"). Da próxima vez que a mesma
# dupla de times aparecer, usamos esse nome direto (sem depender do dicionário de países
# nem da pontuação fuzzy), então o sistema "aprende" qualquer confronto que já resolveu,
# não só seleções que estão no dicionário manual. Persiste em disco entre reinícios. ──
_UNI_NAME_ALIASES_PATH = os.path.join(DATA_DIR, "uni_name_aliases.json")
_uni_name_aliases_cache = None

def _load_uni_name_aliases():
    global _uni_name_aliases_cache
    if _uni_name_aliases_cache is not None:
        return _uni_name_aliases_cache
    try:
        with open(_UNI_NAME_ALIASES_PATH, encoding="utf-8") as f:
            _uni_name_aliases_cache = json.load(f)
    except Exception:
        _uni_name_aliases_cache = {}
    return _uni_name_aliases_cache

def _save_uni_name_alias(casa, fora, uni_home, uni_away):
    aliases = _load_uni_name_aliases()
    key = f"{casa.strip().lower()}|{fora.strip().lower()}"
    if aliases.get(key) == [uni_home, uni_away]:
        return  # já está salvo, não regrava toda vez
    aliases[key] = [uni_home, uni_away]
    try:
        with open(_UNI_NAME_ALIASES_PATH, "w", encoding="utf-8") as f:
            json.dump(aliases, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _uni_find(casa, fora):
    """Encontra evento Uniscore pelo nome dos times (fuzzy com remoção de acentos)."""
    import unicodedata

    def norm(s):
        """Normaliza: minúsculo, sem acentos, sem pontuação, com apelidos de seleção traduzidos."""
        s = s.lower()
        s = unicodedata.normalize("NFD", s)
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")  # remove diacritics
        s = re.sub(r"[^a-z0-9 ]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return _COUNTRY_ALIASES.get(s, s)

    events = _uni_events_today()

    # 1. Já resolvemos essa dupla antes? Usa o nome exato do Uniscore que aprendemos,
    # sem precisar de fuzzy nem do dicionário de países.
    aliases = _load_uni_name_aliases()
    key = f"{casa.strip().lower()}|{fora.strip().lower()}"
    known = aliases.get(key)
    if known:
        uni_home, uni_away = known
        for ev in events:
            if ev.get("homeTeam", {}).get("name") == uni_home and ev.get("awayTeam", {}).get("name") == uni_away:
                return ev

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
    for ev in events:
        hn = ev.get("homeTeam", {}).get("name", "")
        an = ev.get("awayTeam", {}).get("name", "")
        sc = score_team(casa, hn) + score_team(fora, an)
        if sc > best_score:
            best_score = sc
            best = ev
    # Threshold mínimo: pelo menos um nome com score >= 2
    if best_score < 2.0:
        return None

    # 2. Achou com boa confiança — grava o apelido pra próxima vez nem precisar de fuzzy
    if best_score >= 3.0:
        _save_uni_name_alias(casa, fora, best.get("homeTeam", {}).get("name"), best.get("awayTeam", {}).get("name"))

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

    def _fetch_details():
        try:
            r = http_req.get(f"{UNISCORE_BASE}/football/event/{eid}?language=pt-BR",
                             headers=UNISCORE_HEADERS, timeout=6)
            d = r.json().get("data", {}).get("event", {})
            return {
                "clima":   d.get("environment"),
                "arbitro": d.get("referee", {}).get("name") if isinstance(d.get("referee"), dict) else d.get("referee"),
                "estadio": d.get("venue", {}).get("name") if isinstance(d.get("venue"), dict) else None,
            }
        except Exception:
            return {}

    def _fetch_incidents():
        try:
            r = http_req.get(f"{UNISCORE_BASE}/football/event/{eid}/incidents?language=pt-BR",
                             headers=UNISCORE_HEADERS, timeout=6)
            incs = r.json().get("data", {}).get("incidents", [])
            return {"incidents": [
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
            ]}
        except Exception:
            return {"incidents": []}

    def _fetch_form():
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
            return {
                "forma_casa": parse_form(d.get("home", {}).get("latest_matches", [])),
                "forma_fora": parse_form(d.get("away", {}).get("latest_matches", [])),
            }
        except Exception:
            return {"forma_casa": [], "forma_fora": []}

    def _fetch_top_players():
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
            return {
                "top_casa": parse_player(d.get("home_player")),
                "top_fora": parse_player(d.get("away_player")),
            }
        except Exception:
            return {"top_casa": None, "top_fora": None}

    # Estatísticas detalhadas (chutes, posse, passes, duelos...)
    # Mapeamento: nome original UniScore → chave snake_case usada no frontend
    _UNI_STAT_NORM = {
        "Ball Possession":          "ball_possession",
        "Total Shots":              "shots",
        "Shots":                    "shots",
        "Shots on Target":          "shots_on_target",
        "Blocked Shots":            "blocked_shots",
        "Shots Inside Box":         "shots_inside_box",
        "Shots Outside Box":        "shots_outside_box",
        "Touches In Box":           "touches_in_box",
        "Touches in Box":           "touches_in_box",
        "Big Chances":              "big_chances",
        "Big Chances Created":      "big_chances",
        "Corner Kicks":             "corner_kicks",
        "Corners":                  "corner_kicks",
        "Free Kicks":               "freekicks",
        "Passes":                   "passes",
        "Total Passes":             "passes",
        "Accurate Passes":          "passes",
        "Passes Accurate":          "passes",
        "Passes in Final Third":    "pass_in_final_third",
        "Pass in Final Third":      "pass_in_final_third",
        "Final Third Entries":      "final_third_entries",
        "Long Balls":               "long_balls",
        "Crosses":                  "crosses_accuracy",
        "Crosses Accurate":         "crosses_accuracy",
        "Duels":                    "duels",
        "Ground Duels":             "ground_duels",
        "Aerial Duels":             "aerial_duels",
        "Dribbles":                 "dribble",
        "Successful Dribbles":      "dribble",
        "Tackles":                  "tackles",
        "Tackles Won":              "tackles",
        "Interceptions":            "interceptions",
        "Recoveries":               "recoveries",
        "Clearances":               "clearances",
        "Saves":                    "saves",
        "Goalkeeper Saves":         "saves",
        "Goal Kicks":               "goal_kicks",
        "Yellow Cards":             "yellow_cards",
        "Fouls":                    "fouls",
        "Fouls Committed":          "fouls",
        "Was Fouled":               "was_fouled",
        "Possession Losses":        "poss_losts",
        "Total Possession Losses":  "poss_losts",
        "Expected Goals":           "expected_goals",
    }

    def _build_stat_map(period_data):
        """Constrói dict com chave original + alias snake_case."""
        stat_map = {}
        for grp in (period_data or {}).get("groups", []):
            for item in grp.get("statisticsItems", []):
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("fields") or ""
                if not name:
                    continue
                stat_map[name] = item                        # chave original
                snake = _UNI_STAT_NORM.get(name)
                if snake and snake not in stat_map:
                    stat_map[snake] = item                   # alias snake_case
        return stat_map

    def _fetch_statistics():
        try:
            url_stats = f"{UNISCORE_BASE}/football/event/{eid}/home/{home_tid}/away/{away_tid}/statistics"
            r = http_req.get(url_stats, headers=UNISCORE_HEADERS, timeout=6)
            periods = r.json().get("data", {}).get("statistics", [])
            stat_periods_out = {}
            for pd in periods:
                period_key = pd.get("period", "ALL")
                stat_periods_out[period_key] = _build_stat_map(pd)
            all_pd = next((p for p in periods if p.get("period") == "ALL"), periods[0] if periods else None)
            return {
                "statistics":         _build_stat_map(all_pd) if all_pd else {},
                "statistics_periods": stat_periods_out,
            }
        except Exception:
            return {"statistics": {}, "statistics_periods": {}}

    def _fetch_lineups():
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
                        "order":   p.get("counterOrder"),
                    }
                players = sd.get("players", [])
                titulares = [pp(p) for p in players if not p.get("substitute", False)]
                titulares.sort(key=lambda p: p.get("order") or 99)
                return {
                    "formation":  sd.get("formation", ""),
                    "confirmed":  ld.get("confirmed", False),
                    "titulares":  titulares,
                    "reservas":   [pp(p) for p in players if p.get("substitute", False)],
                }
            return {"lineup_casa": parse_side("home"), "lineup_fora": parse_side("away")}
        except Exception:
            return {"lineup_casa": None, "lineup_fora": None}

    def _fetch_graph():
        try:
            r = http_req.get(f"{UNISCORE_BASE}/football/event/{eid}/graph",
                             headers=UNISCORE_HEADERS, timeout=6)
            pts = r.json().get("data", {}).get("graphPoints", [])
            return {"graph": [{"m": p.get("minute"), "v": p.get("value")} for p in pts]}
        except Exception:
            return {"graph": []}

    # Todas as chamadas acima são independentes — rodam em paralelo em vez de uma
    # atrás da outra, senão uma única partida podia levar 8-12s pra carregar.
    from concurrent.futures import ThreadPoolExecutor as _TPE
    fetchers = [_fetch_details, _fetch_incidents, _fetch_form, _fetch_top_players,
                _fetch_statistics, _fetch_lineups, _fetch_graph]
    with _TPE(max_workers=len(fetchers)) as ex:
        for fut in [ex.submit(fn) for fn in fetchers]:
            try:
                result.update(fut.result(timeout=10))
            except Exception:
                pass

    # Odds ao vivo (mesma fonte das odds de abertura — o feed do dia já reflete
    # a movimentação de mercado durante o jogo, então serve pra odds ao vivo também)
    try:
        odds_today = _uni_odds_today()
        od = odds_today.get(eid)
        if od and od.get("h"):
            result["live_odds"] = {
                "h": od["h"], "x": od["x"], "a": od["a"],
                "ou_line": od.get("ou_line", ""),
                "ou_over": od.get("ou_over", ""),
                "ou_under": od.get("ou_under", ""),
                "changed": True,
            }
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
    with ThreadPoolExecutor(max_workers=16) as ex:
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
    """Busca jogos ao vivo diretamente via FotMob /api/matches (sem bloqueio)."""
    try:
        today_str = datetime.now().strftime("%Y%m%d")
        r = http_req.get(
            "https://www.fotmob.com/api/matches",
            headers=FOTMOB_HEADERS,
            params={"date": today_str},
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


# ── ODDSPEDIA (Ao Vivo 2 — experimental) ──────────────────────────────────────
ODDSPEDIA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

@app.route("/api/oddspedia/debug/<path:slug>")
def api_oddspedia_debug(slug):
    """DEBUG: abre a página da partida no Oddspedia via Playwright e intercepta TODAS
    as chamadas de rede que parecem trazer odds/dados de partida — usado só pra
    descobrir a URL real da API antes de fazer o scraper de verdade. slug = ex:
    'br/futebol/brasil-noruega-1982539' (sem o domínio, como aparece no link do site)."""
    from playwright.sync_api import sync_playwright

    captured = []
    KEYWORDS = ("odds", "bookmaker", "api", "market", "event", "match")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx  = browser.new_context(locale="pt-BR", user_agent=ODDSPEDIA_HEADERS["User-Agent"])
            page = ctx.new_page()

            def on_resp(resp):
                url = resp.url
                low = url.lower()
                if "oddspedia.com" not in low:
                    return
                if "/_nuxt/" in low or low.endswith((".js", ".css", ".woff2", ".png", ".svg", ".ico")):
                    return
                if not any(k in low for k in KEYWORDS):
                    return
                entry = {"url": url, "status": resp.status}
                try:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        body = resp.json()
                        entry["body_preview"] = json.dumps(body, ensure_ascii=False)[:1500]
                except Exception:
                    pass
                captured.append(entry)

            page.on("response", on_resp)
            page.goto(f"https://oddspedia.com/{slug}", wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(6000)
            browser.close()

        return jsonify({"slug": slug, "total_capturado": len(captured), "chamadas": captured})
    except Exception as e:
        return jsonify({"error": str(e)}), 503


# ── FLASHSCORE/SOCCERWAY (Ao Vivo 2 — experimental) ───────────────────────────
# O Soccerway roda em cima da mesma infraestrutura do Flashscore/Livesport. O feed
# devolve texto num formato próprio (blocos separados por "~", campos "chave÷valor"
# separados por "¬") — sem JSON, mas simples de parsear. Sem Cloudflare, só precisa
# de um Referer válido e um header x-fsign (não parece ser validado com rigor).
FS_BASE = "https://global.flashscore.ninja/2051/x/feed"
FS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://br.soccerway.com/",
    "x-fsign": "SW9D1eZo",
}

def _fs_get(path):
    r = http_req.get(f"{FS_BASE}/{path}", headers=FS_HEADERS, timeout=10)
    r.raise_for_status()
    return r.text

def _fs_blocks(text):
    return [b for b in text.split("~") if b.strip()]

def _fs_kv(block):
    d = {}
    for part in block.split("¬"):
        if "÷" in part:
            k, _, v = part.partition("÷")
            d[k] = v
    return d

_fs_live_cache = {"ts": 0, "data": []}

def _fs_all_matches():
    """Lista de TODAS as partidas do dia no feed (agendadas + ao vivo + encerradas),
    com liga/país (o feed lista um bloco "ZA" (liga) seguido dos jogos "AA" daquela
    liga, em ordem — então vamos guardando a liga atual enquanto percorremos)."""
    global _fs_live_cache
    if time.time() - _fs_live_cache["ts"] < 30 and _fs_live_cache["data"]:
        return _fs_live_cache["data"]
    try:
        text = _fs_get("f_1_0_-3_pt-br_1")
    except Exception:
        return _fs_live_cache["data"]
    matches = []
    liga_atual, pais_atual = "", ""
    for block in _fs_blocks(text):
        if block.startswith("ZA"):
            d = _fs_kv(block)
            liga_atual = d.get("ZA", "")
            pais_atual = d.get("ZY", "")
            continue
        if not block.startswith("AA"):
            continue
        d = _fs_kv(block)
        if not d.get("AE") or not d.get("AF"):
            continue
        # Escudos: link direto pro CDN de imagens da própria fonte (campos "OA"/"OB" do
        # feed) — não baixa/armazena nada aqui, só monta a URL e o navegador do usuário
        # carrega direto de lá quando renderizar o <img>
        escudo_casa = f"https://www.flashscore.com/res/image/data/{d['OA']}" if d.get("OA") else None
        escudo_fora = f"https://www.flashscore.com/res/image/data/{d['OB']}" if d.get("OB") else None
        matches.append({
            "id":          d.get("AA"),
            "home":        d.get("AE"),
            "away":        d.get("AF"),
            "home_score":  d.get("AG"),
            "away_score":  d.get("AH"),
            "status":      d.get("AB"),   # 1=agendado, 2=ao vivo, 3=encerrado (aproximado)
            "kickoff_ts":  d.get("AD"),
            "liga":        liga_atual,
            "pais":        pais_atual,
            "escudo_casa": escudo_casa,
            "escudo_fora": escudo_fora,
        })
    _fs_live_cache = {"ts": time.time(), "data": matches}
    return matches

# Mantém o nome antigo funcionando (usado pelo _fs_find_match) — mesmo dado, só outro nome
def _fs_live_matches():
    return _fs_all_matches()

def _fs_find_match(casa, fora):
    """Acha o jogo no feed pelo nome dos times (fuzzy, mesma técnica do _uni_find)."""
    import unicodedata

    def norm(s):
        s = (s or "").lower()
        s = unicodedata.normalize("NFD", s)
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        s = re.sub(r"[^a-z0-9 ]", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    def side_score(query, candidate):
        s = 0
        if len(query) >= 4 and candidate[:5] == query[:5]: s += 2
        if query[:6] and (query[:6] in candidate or candidate[:6] in query): s += 1
        return s

    nc, nf = norm(casa), norm(fora)
    best, best_score = None, 0
    for m in _fs_live_matches():
        h, a = norm(m["home"]), norm(m["away"])
        sc_home, sc_away = side_score(nc, h), side_score(nf, a)
        # Exige alguma confiança dos DOIS lados — senão um nome parecido de um só
        # time (ex: "Nautico" batendo com "Nautico Hacoaj") pode casar o jogo errado
        if sc_home == 0 or sc_away == 0:
            continue
        score = sc_home + sc_away
        if score > best_score:
            best_score = score
            best = m
    if best_score < 4:
        return None
    return best

def _fs_match_stats(event_id):
    """Estatísticas da partida (posse, chutes, xG, etc.), agrupadas por seção."""
    text = _fs_get(f"df_st_1_{event_id}")
    sections = []
    current = None
    for block in _fs_blocks(text):
        d = _fs_kv(block)
        if "SF" in d and "SG" not in d:
            current = {"title": d["SF"], "rows": []}
            sections.append(current)
        elif "SG" in d:
            if current is None:
                current = {"title": "Geral", "rows": []}
                sections.append(current)
            current["rows"].append({"label": d.get("SG", ""), "home": d.get("SH", ""), "away": d.get("SI", "")})
    return sections

def _fs_h2h(event_id):
    """Últimos jogos de cada time + confrontos diretos, agrupados por aba (campo KA:
    'Total' / 'Time A - Casa' / 'Time B - Fora') e por seção dentro da aba (campo KB)."""
    text = _fs_get(f"df_hh_1_{event_id}")
    RESULT_LABEL = {"w": "V", "d": "E", "l": "D"}
    tabs = []
    tab = None
    section = None
    for block in _fs_blocks(text):
        d = _fs_kv(block)
        if "KA" in d:
            tab = {"label": d["KA"], "sections": []}
            tabs.append(tab)
            section = None
            continue
        if "KB" in d:
            section = {"title": d["KB"], "rows": []}
            if tab is None:
                tab = {"label": "Total", "sections": []}
                tabs.append(tab)
            tab["sections"].append(section)
            continue
        if "KC" not in d or "KJ" not in d:
            continue
        if d.get("KP") == event_id:
            continue  # é a própria partida ainda não disputada, não um jogo passado
        if section is None:
            if tab is None:
                tab = {"label": "Total", "sections": []}
                tabs.append(tab)
            section = {"title": "Confrontos", "rows": []}
            tab["sections"].append(section)
        home = (d.get("KJ") or "").lstrip("*")
        away = (d.get("KK") or "").lstrip("*")
        section["rows"].append({
            "id":     d.get("KP", ""),
            "date":   d.get("KC", ""),
            "league": d.get("KF", ""),
            "pais":   d.get("KH", ""),
            "home":   home,
            "away":   away,
            "score":  d.get("KL", ""),
            "result": RESULT_LABEL.get(d.get("WIS", ""), ""),
        })
    return tabs

def _fs_half_time(event_id):
    """Placar do 1º tempo de uma partida já disputada (feed df_sui_, blocos 'AC': o
    primeiro bloco 'AC' é sempre o 1º tempo, com IG/IH = gols de casa/fora nesse tempo)."""
    text = _fs_get(f"df_sui_1_{event_id}")
    for block in _fs_blocks(text):
        d = _fs_kv(block)
        if "AC" in d and "1" in d["AC"]:
            return {"home": d.get("IG", "0"), "away": d.get("IH", "0")}
    return None

def _fs_goal_minutes(event_id):
    """Minutos de cada gol da partida, separados por time (casa/fora), usando o feed
    df_sui_ (resumo/timeline). Cada bloco de gol (IK='Gol') traz INX/IOX = placar da
    casa/fora IMEDIATAMENTE APÓS aquele gol — comparando com o placar anterior dá pra
    saber de qual time foi o gol."""
    text = _fs_get(f"df_sui_1_{event_id}")

    def _parse_minute(raw):
        # "26'" -> 26 ; "45+2'" -> 47 (soma o acréscimo)
        raw = (raw or "").rstrip("'")
        if "+" in raw:
            try:
                base, extra = raw.split("+")
                return int(base) + int(extra)
            except ValueError:
                return None
        try:
            return int(raw)
        except ValueError:
            return None

    home_minutes, away_minutes = [], []
    prev_home, prev_away = 0, 0
    for block in _fs_blocks(text):
        d = _fs_kv(block)
        if d.get("IK") != "Gol":
            continue
        try:
            cur_home, cur_away = int(d.get("INX", prev_home)), int(d.get("IOX", prev_away))
        except ValueError:
            continue
        minute = _parse_minute(d.get("IB"))
        if minute is not None:
            if cur_home > prev_home:
                home_minutes.append(minute)
            elif cur_away > prev_away:
                away_minutes.append(minute)
        prev_home, prev_away = cur_home, cur_away
    return {"home": home_minutes, "away": away_minutes}

def _fs_goal_events(event_id):
    """Igual a _fs_goal_minutes, mas guarda também quem marcou/tomou cartão — o feed
    já traz o nome do jogador em 'IF' (ex: 'Loupatty E.') em cada bloco de evento, só
    não era usado até agora. 'ICT' indica pênalti/gol contra quando presente (testado
    em jogos reais — normalmente vem vazio, então não força uma tag quando não vier).
    Pra gol, o lado (casa/fora) é derivado comparando INX/IOX com o placar anterior;
    pra cartão não tem placar pra comparar, então usa 'IA' direto (1=casa, 2=fora,
    confirmado testando contra gols onde os dois métodos batem)."""
    text = _fs_get(f"df_sui_1_{event_id}")

    def _parse_minute(raw):
        raw = (raw or "").rstrip("'")
        if "+" in raw:
            try:
                base, extra = raw.split("+")
                return int(base) + int(extra)
            except ValueError:
                return None
        try:
            return int(raw)
        except ValueError:
            return None

    events = []
    prev_home, prev_away = 0, 0
    for block in _fs_blocks(text):
        d = _fs_kv(block)
        ik = d.get("IK")
        minute = _parse_minute(d.get("IB"))
        if ik == "Gol":
            try:
                cur_home, cur_away = int(d.get("INX", prev_home)), int(d.get("IOX", prev_away))
            except ValueError:
                continue
            is_home = cur_home > prev_home
            if minute is not None and (is_home or cur_away > prev_away):
                events.append({
                    "type": "gol",
                    "minute": minute,
                    "minute_label": d.get("IB", ""),
                    "player": d.get("IF", ""),
                    "isHome": is_home,
                    "note": d.get("ICT", ""),  # ex: pênalti/gol contra, quando o feed manda
                })
            prev_home, prev_away = cur_home, cur_away
        elif ik in ("Cartão Vermelho", "Cartão Amarelo") and minute is not None:
            events.append({
                "type": "cartao_vermelho" if ik == "Cartão Vermelho" else "cartao_amarelo",
                "minute": minute,
                "minute_label": d.get("IB", ""),
                "player": d.get("IF", ""),
                "isHome": d.get("IA") == "1",
                "note": "",
            })
    return events

def _fs_standings(event_id, home_name="", away_name=""):
    """Tabela de classificação da liga da partida (posição, pontos, V/E/D, saldo)."""
    text = _fs_get(f"df_tl_1_{event_id}")
    rows = []

    def _norm(s):
        return (s or "").strip().lower()
    nh, na = _norm(home_name), _norm(away_name)

    for block in _fs_blocks(text):
        d = _fs_kv(block)
        if "TR" not in d or "TN" not in d:
            continue
        nome = d.get("TN", "")
        rows.append({
            "pos":     d.get("TR", ""),
            "team":    nome,
            "jogos":   d.get("TM", ""),
            "vitorias":  d.get("TW", ""),
            "empates":   d.get("TDR", ""),
            "derrotas":  d.get("TL", ""),
            "gols":    d.get("TG", ""),
            "pontos":  d.get("TP", ""),
            "destacado": _norm(nome) in (nh, na),
        })
    return rows

def _fs_odds(event_id, bookmaker_id=574, bet_type="HOME_DRAW_AWAY", bet_scope="FULL_TIME"):
    """Odds 1X2 de uma casa de apostas específica (bookmaker_id) via GraphQL da lsapp.eu.
    Timeout curto (4s) — em ligas menores, essa API às vezes trava ao invés de retornar
    404 rápido; com timeout de 10s e fallback de 3 casas, um jogo lento sozinho podia
    travar até 30s, e multiplicado por centenas de jogos no ranking do Backtest 2 isso
    inflava o tempo total demais."""
    url = ("https://global.ds.lsapp.eu/odds/pq_graphql"
           f"?_hash=ope2&eventId={event_id}&bookmakerId={bookmaker_id}"
           f"&betType={bet_type}&betScope={bet_scope}")
    r = http_req.get(url, headers=FS_HEADERS, timeout=4)
    r.raise_for_status()
    return r.json().get("data", {}).get("findPrematchOddsForBookmaker")

@app.route("/api/flashscore/today")
def api_flashscore_today():
    """Todas as partidas do dia no Soccerway/Flashscore (agendadas + ao vivo + encerradas),
    com liga/país — usado pela aba experimental 'Hoje 2'."""
    matches = _fs_all_matches()
    return jsonify({"total": len(matches), "matches": matches})

@app.route("/api/flashscore/match")
def api_flashscore_match():
    """Busca id/placar/status do jogo pelo nome dos times (casa/fora), sem estatísticas."""
    casa = request.args.get("casa", "")
    fora = request.args.get("fora", "")
    m = _fs_find_match(casa, fora)
    if not m:
        return jsonify({"found": False}), 404
    return jsonify({"found": True, **m})

@app.route("/api/flashscore/stats")
def api_flashscore_stats():
    """Estatísticas completas da partida (xG, posse, chutes, passes, defesa, etc.)."""
    casa = request.args.get("casa", "")
    fora = request.args.get("fora", "")
    m = _fs_find_match(casa, fora)
    if not m:
        return jsonify({"found": False}), 404
    try:
        sections = _fs_match_stats(m["id"])
    except Exception as e:
        return jsonify({"found": True, "match": m, "error": str(e)}), 503
    return jsonify({"found": True, "match": m, "sections": sections})

@app.route("/api/flashscore/odds")
def api_flashscore_odds():
    """Odds 1X2 (uma ou mais casas de apostas) da partida, pelo nome dos times."""
    casa = request.args.get("casa", "")
    fora = request.args.get("fora", "")
    m = _fs_find_match(casa, fora)
    if not m:
        return jsonify({"found": False}), 404
    # Algumas casas conhecidas nesse feed (bet365=16, Betano.br=574, ...) — tenta a
    # primeira que responder com dados válidos, sem travar em uma só
    BOOKMAKERS = [(16, "bet365"), (574, "Betano.br"), (49, "Tipsport")]
    for bid, name in BOOKMAKERS:
        try:
            odds = _fs_odds(m["id"], bookmaker_id=bid)
            if odds:
                return jsonify({"found": True, "match": m, "bookmaker": name, "odds": odds})
        except Exception:
            continue
    return jsonify({"found": True, "match": m, "odds": None})

FS_ODDS_MARKETS = [
    ("1x2", "HOME_DRAW_AWAY"),
    ("over_under", "OVER_UNDER"),
    ("ambos_marcam", "BOTH_TEAMS_TO_SCORE"),
    ("dupla_chance", "DOUBLE_CHANCE"),
    ("handicap_asiatico", "ASIAN_HANDICAP"),
    ("placar_exato", "CORRECT_SCORE"),
]

FS_ODDS_BOOKMAKERS = [(16, "bet365"), (574, "Betano.br"), (49, "Tipsport")]

from concurrent.futures import ThreadPoolExecutor

# Pools separados (mercados dentro de 1 jogo vs. vários jogos ao mesmo tempo) pra evitar
# que um pool único fique com todos os workers presos esperando sub-tarefas dele mesmo.
_fs_market_pool = ThreadPoolExecutor(max_workers=30)
_fs_event_pool = ThreadPoolExecutor(max_workers=12)

# Pools dedicados só pro "jogos que se encaixam na metodologia" (Backtest 2) — sem
# isso, essa busca rápida (poucos jogos) ficava presa na fila atrás do cálculo pesado
# do ranking (centenas de lookups de odds), que usa _fs_event_pool E _fs_market_pool
# por até 1 minuto. Isolando os dois níveis (evento e mercado), a busca continua
# rápida mesmo com um recálculo de ranking rodando ao mesmo tempo.
_bt2_matches_pool = ThreadPoolExecutor(max_workers=10)
_bt2_matches_market_pool = ThreadPoolExecutor(max_workers=20)

# Em dias com muitos jogos (200+), buscar odds de TODOS os agendados demora
# minutos (até 3 tentativas de casa de apostas x timeout por jogo, dividido
# entre poucos workers). Limita aos próximos N jogos por horário — cobre o
# que realmente dá pra apostar em breve, sem travar a busca.
_BT2_MATCHES_MAX_CANDIDATOS = 60


def _bt2_matches_candidatos(include_finished=False):
    """Jogos de hoje candidatos pros filtros (Metodologias/Parâmetros/Análise) —
    por padrão só agendados ("1"), já que os filtros dependem de odds. Passando
    include_finished=True, também inclui encerrados ("3") — as odds continuam
    disponíveis na Flashscore mesmo depois do jogo acabar (campo "opening" é a
    odd de abertura, "value" fica com o último valor antes do jogo travar)."""
    statuses = ("1", "3") if include_finished else ("1",)
    candidatos = [m for m in _fs_all_matches() if m.get("status") in statuses]
    candidatos.sort(key=lambda m: m.get("kickoff_ts") or "")
    return candidatos[:_BT2_MATCHES_MAX_CANDIDATOS]


_TODAY2_ODDS_SNAPSHOT_TTL = 300  # 5min — odds mudam pouco em poucos minutos
_today2_odds_snapshot_cache = {"ts": 0, "data": None}


def _today2_odds_snapshot(force=False):
    """Busca as odds de TODOS os mercados de cada jogo candidato de hoje (agendado
    ou encerrado) UMA VEZ SÓ, cacheada 5min — usada por vários consumidores (Filtro
    de Metodologias, Filtro de Parâmetros, odd média por bucket do Backtest CS).
    Sem esse cache compartilhado, cada checkbox marcado no Filtro de Metodologias
    disparava seu PRÓPRIO fetch completo (~70s, casa de aposta x mercado x jogo) —
    com várias metodologias marcadas ao mesmo tempo, elas competiam pelo mesmo pool
    de threads e o filtro parecia simplesmente não funcionar (ainda calculando
    minutos depois, sem indicação de carregamento)."""
    now = time.time()
    if not force and _today2_odds_snapshot_cache["data"] is not None and \
            (now - _today2_odds_snapshot_cache["ts"]) < _TODAY2_ODDS_SNAPSHOT_TTL:
        return _today2_odds_snapshot_cache["data"]

    candidatos = _bt2_matches_candidatos(include_finished=True)

    def _fetch_one(m):
        try:
            _, markets = _fs_odds_all_markets_any_bookmaker(m["id"], pool=_bt2_matches_market_pool)
        except Exception:
            markets = {}
        return m, markets

    snapshot = list(_bt2_matches_pool.map(_fetch_one, candidatos))
    _today2_odds_snapshot_cache["ts"] = now
    _today2_odds_snapshot_cache["data"] = snapshot
    return snapshot

def _fs_odds_has_data(key, odds):
    """A API às vezes retorna um objeto 'válido' mas vazio (ex: Placar Exato com
    items:[] quando essa casa não tem esse mercado pra esse jogo) — sem isso, o código
    tratava como 'achei dados' e parava de tentar outras casas de apostas."""
    if not odds:
        return False
    if key in ("over_under", "handicap_asiatico"):
        return bool(odds.get("opportunities"))
    if key == "placar_exato":
        return bool(odds.get("items"))
    if key == "1x2":
        return bool(odds.get("home") and odds.get("draw") and odds.get("away"))
    if key == "ambos_marcam":
        return bool(odds.get("yes") and odds.get("no"))
    if key == "dupla_chance":
        return bool(odds.get("homeOrDraw") and odds.get("homeOrAway") and odds.get("drawOrAway"))
    return True

def _fs_odds_all_markets(event_id, bookmaker_id=16, pool=None, markets_wanted=None):
    """Busca os mercados confirmados pra um event_id numa casa específica, em paralelo
    (uma requisição por mercado ao mesmo tempo). Retorna markets_dict (pode vir vazio).
    Aceita um pool alternativo (default: _fs_market_pool) pra isolar chamadas que não
    podem ficar presas atrás de cálculos pesados que também usam o pool padrão.
    markets_wanted (lista de keys, ex: ["1x2"]) restringe aos mercados pedidos em vez
    dos 6 confirmados — usado por quem só precisa de 1, evita chamadas à toa."""
    pool = pool or _fs_market_pool
    items = FS_ODDS_MARKETS if markets_wanted is None else [i for i in FS_ODDS_MARKETS if i[0] in markets_wanted]

    def _fetch_one(item):
        key, bet_type = item
        try:
            return key, _fs_odds(event_id, bookmaker_id=bookmaker_id, bet_type=bet_type)
        except Exception:
            return key, None
    markets = {}
    for key, odds in pool.map(_fetch_one, items):
        if _fs_odds_has_data(key, odds):
            markets[key] = odds
    return markets

def _fs_odds_all_markets_any_bookmaker(event_id, pool=None, markets_wanted=None):
    """Tenta bet365/Betano/Tipsport nessa ordem, MISTURANDO casas por mercado — a
    Betano.br, por exemplo, não expõe 'Ambos Marcam' nessa API pra praticamente
    nenhuma partida (testado e confirmado, não é bug de parsing: a API retorna
    null mesmo), então usar só a 1ª casa que respondeu deixava esse mercado
    faltando toda vez que o bet365 não tinha dados e caía pro Betano. Agora
    completa os mercados que faltarem com a próxima casa da lista. O nome
    retornado é da 1ª casa que contribuiu com algo (a mais completa via de
    regra), pros mercados que vieram de outra casa não tem atribuição individual
    no retorno — é uma simplificação aceitável já que o objetivo é preencher
    lacunas, não misturar odds de mercados que uma mesma casa já tem.
    markets_wanted restringe quais dos 6 mercados são buscados (default: todos) —
    quem só precisa de "1x2" (ex: Backtest CS) evita 5/6 das chamadas à toa."""
    bookmaker_name = None
    markets = {}
    wanted_keys = markets_wanted if markets_wanted is not None else [k for k, _ in FS_ODDS_MARKETS]
    for bid, name in FS_ODDS_BOOKMAKERS:
        faltando = [k for k in wanted_keys if k not in markets]
        if not faltando:
            break
        casa_markets = _fs_odds_all_markets(event_id, bookmaker_id=bid, pool=pool, markets_wanted=faltando)
        if casa_markets and bookmaker_name is None:
            bookmaker_name = name
        for k, v in casa_markets.items():
            markets.setdefault(k, v)
    return bookmaker_name, markets

@app.route("/api/flashscore/odds_all")
def api_flashscore_odds_all():
    """Todos os mercados de odds confirmados (1X2, Acima/Abaixo, Ambos Marcam,
    Dupla Chance, Handicap Asiático, Placar Exato) de uma casa de apostas, pelo
    nome dos times. Usado apenas na aba experimental 'Hoje 2'."""
    casa = request.args.get("casa", "")
    fora = request.args.get("fora", "")
    m = _fs_find_match(casa, fora)
    if not m:
        return jsonify({"found": False}), 404
    bookmaker_name, markets = _fs_odds_all_markets_any_bookmaker(m["id"])
    return jsonify({"found": True, "match": m, "bookmaker": bookmaker_name, "markets": markets})

@app.route("/api/flashscore/odds_all_batch")
def api_flashscore_odds_all_batch():
    """Odds (todos os mercados) de várias partidas JÁ DISPUTADAS de uma vez, direto
    pelo event_id de cada uma (sem precisar buscar por nome de time) — usado pela aba
    'Profit' pra calcular o profit histórico usando as odds de cada jogo passado do H2H.
    Tenta bet365/Betano/Tipsport (várias ligas menores só têm odds em uma delas) e
    busca todos os ids em paralelo. Limitado a 20 ids por chamada pra não sobrecarregar
    o feed."""
    ids = [i for i in request.args.get("ids", "").split(",") if i][:20]

    def _fetch_one(event_id):
        try:
            return event_id, _fs_odds_all_markets_any_bookmaker(event_id)
        except Exception:
            return event_id, (None, {})

    result = {}
    for event_id, (bookmaker_name, markets) in _fs_event_pool.map(_fetch_one, ids):
        if markets:
            result[event_id] = {"bookmaker": bookmaker_name, "markets": markets}
    return jsonify({"results": result})

@app.route("/api/flashscore/h2h")
def api_flashscore_h2h():
    """Últimos jogos de cada time + confrontos diretos, pelo nome dos times."""
    casa = request.args.get("casa", "")
    fora = request.args.get("fora", "")
    m = _fs_find_match(casa, fora)
    if not m:
        return jsonify({"found": False}), 404
    try:
        tabs = _fs_h2h(m["id"])
    except Exception as e:
        return jsonify({"found": True, "match": m, "error": str(e)}), 503
    return jsonify({"found": True, "match": m, "tabs": tabs})

@app.route("/api/flashscore/half_time_batch")
def api_flashscore_half_time_batch():
    """Placar do 1º tempo de várias partidas já disputadas de uma vez (usado para
    mostrar 'gols no 1º tempo' nos jogos passados listados no H2H). Limitado a 15
    ids por chamada pra não sobrecarregar o feed.
    Buscado em PARALELO (_fs_event_pool), igual odds_all_batch/goal_minutes_batch —
    antes era um `for` sequencial, um jogo de cada vez, e isso sozinho já explicava
    boa parte da lentidão ao abrir qualquer partida (a aba Jogo depende desse
    endpoint pra quase todo jogo do histórico)."""
    ids = [i for i in request.args.get("ids", "").split(",") if i][:15]

    def _fetch_one(event_id):
        try:
            return event_id, _fs_half_time(event_id)
        except Exception:
            return event_id, None

    result = {}
    for event_id, ht in _fs_event_pool.map(_fetch_one, ids):
        if ht:
            result[event_id] = ht
    return jsonify({"results": result})

@app.route("/api/flashscore/goal_minutes_batch")
def api_flashscore_goal_minutes_batch():
    """Minutos dos gols (por time) de várias partidas já disputadas de uma vez — usado
    pra calcular 'tempo sem marcar/sofrer gol' na aba Jogo. Busca em paralelo, limitado
    a 20 ids por chamada."""
    ids = [i for i in request.args.get("ids", "").split(",") if i][:20]

    def _fetch_one(event_id):
        try:
            return event_id, _fs_goal_minutes(event_id)
        except Exception:
            return event_id, None

    result = {}
    for event_id, gm in _fs_event_pool.map(_fetch_one, ids):
        if gm is not None:
            result[event_id] = gm
    return jsonify({"results": result})

@app.route("/api/flashscore/goal_events")
def api_flashscore_goal_events():
    """Quem marcou cada gol (e cartões vermelho/amarelo) e em que minuto, pra
    partidas já encerradas — usado no Hoje 2 pra mostrar os artilheiros/cartões
    (nome + minuto) igual ao placar final."""
    casa = request.args.get("casa", "")
    fora = request.args.get("fora", "")
    m = _fs_find_match(casa, fora)
    if not m:
        return jsonify({"found": False}), 404
    try:
        events = _fs_goal_events(m["id"])
    except Exception as e:
        return jsonify({"found": True, "match": m, "error": str(e)}), 503
    return jsonify({"found": True, "match": m, "events": events})

@app.route("/api/flashscore/standings")
def api_flashscore_standings():
    """Tabela de classificação da liga da partida, pelo nome dos times."""
    casa = request.args.get("casa", "")
    fora = request.args.get("fora", "")
    m = _fs_find_match(casa, fora)
    if not m:
        return jsonify({"found": False}), 404
    try:
        rows = _fs_standings(m["id"], home_name=m.get("home", ""), away_name=m.get("away", ""))
    except Exception as e:
        return jsonify({"found": True, "match": m, "error": str(e)}), 503
    return jsonify({"found": True, "match": m, "rows": rows})



# ── BACKTEST 2 — ranking global de metodologias ──────────────────────────────
# Roda a MESMA simulação de lucro da aba "Profit" (Hoje 2), mas agregando o
# histórico de TODOS os jogos de hoje de uma vez, pra ter amostra estatística
# grande o suficiente pra rankear mercado+seleção do mais pro menos lucrativo.
_bt2_cache = {"ts": 0, "data": None}
_BT2_CACHE_TTL = 30 * 60  # 30 minutos

_BT2_MARKET_LABELS = {
    "1x2": "1X2", "over_under": "Acima/Abaixo", "ambos_marcam": "Ambos Marcam",
    "dupla_chance": "Dupla Chance", "handicap_asiatico": "Handicap Asiático",
}
_BT2_MIN_SAMPLE = 15
_BT2_MAX_HISTORICAL_IDS = 400

# Faixas de odd — usadas pra segmentar o ranking por odd em vez de só juntar tudo
# (ex: "Casa" no 1X2 pode ter ROI bem diferente com odd 1.50 vs odd 3.50). A odd de
# cada aposta já é gravada em bt2_bets, então isso é calculado na hora da leitura,
# sem precisar de coluna nova nem migração de schema.
_BT2_ODD_RANGES = [
    (0.0, 1.5,  "1.01–1.50"),
    (1.5, 2.0,  "1.51–2.00"),
    (2.0, 3.0,  "2.01–3.00"),
    (3.0, 5.0,  "3.01–5.00"),
    (5.0, 9e9,  "5.01+"),
]
_BT2_MIN_SAMPLE_ODD_RANGE = 8  # amostra menor exigida pra cada faixa (o corte reparte a amostra total)

def _bt2_odd_range_label(odd):
    if not odd:
        return None
    for lo, hi, label in _BT2_ODD_RANGES:
        if lo < odd <= hi:
            return label
    return None


def _bt2_parse_score(s):
    m = re.match(r"^(\d+):(\d+)$", s or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _bt2_team_rows_from_section(tabs, team_home):
    """Porta do _profitTeamRowsFromSection (JS) pra Python: pega a seção "Últimos
    jogos: <time>" (não confrontos) do time casa/fora dentro da aba 'Total' (idx 0)
    e extrai {id,h,a,date} de cada jogo com placar válido (até 30 por time)."""
    if not tabs:
        return []
    tab = tabs[0]
    sections = [s for s in tab["sections"] if "confront" not in (s.get("title") or "").lower()]
    sec = sections[0] if team_home else (sections[1] if len(sections) > 1 else (sections[0] if sections else None))
    if not sec:
        return []
    rows = []
    for r in sec["rows"][:30]:
        sc = _bt2_parse_score(r.get("score"))
        if not sc:
            continue
        rows.append({"id": r.get("id"), "h": sc[0], "a": sc[1], "date": r.get("date"),
                     "home": r.get("home"), "away": r.get("away")})
    return rows


def _bt2_market_selections(market_key, o, h, a):
    """Porta do _profitMarketSelections (JS) pra Python — mesma lógica, mesmos 6
    mercados, mesmas condições de vitória por seleção."""
    if not o:
        return []
    total = h + a

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    if market_key == "1x2":
        return [
            {"label": "Casa", "odd": _f((o.get("home") or {}).get("value")), "won": h > a},
            {"label": "Empate", "odd": _f((o.get("draw") or {}).get("value")), "won": h == a},
            {"label": "Fora", "odd": _f((o.get("away") or {}).get("value")), "won": a > h},
        ]
    if market_key == "ambos_marcam":
        btts = h >= 1 and a >= 1
        return [
            {"label": "Sim", "odd": _f((o.get("yes") or {}).get("value")), "won": btts},
            {"label": "Não", "odd": _f((o.get("no") or {}).get("value")), "won": not btts},
        ]
    if market_key == "dupla_chance":
        return [
            {"label": "1X", "odd": _f((o.get("homeOrDraw") or {}).get("value")), "won": h >= a},
            {"label": "12", "odd": _f((o.get("homeOrAway") or {}).get("value")), "won": h != a},
            {"label": "X2", "odd": _f((o.get("drawOrAway") or {}).get("value")), "won": a >= h},
        ]
    if market_key == "over_under":
        sels = []
        for op in o.get("opportunities") or []:
            line = _f((op.get("handicap") or {}).get("value"))
            if line is None or total == line:
                continue
            sels.append({"label": f"Acima {line}", "odd": _f((op.get("over") or {}).get("value")), "won": total > line})
            sels.append({"label": f"Abaixo {line}", "odd": _f((op.get("under") or {}).get("value")), "won": total < line})
        return sels
    if market_key == "handicap_asiatico":
        sels = []
        for op in o.get("opportunities") or []:
            line = _f((op.get("handicap") or {}).get("value"))
            if line is None:
                continue
            adj_home = h + line
            if adj_home == a:
                continue
            sign = "+" if line >= 0 else ""
            sign2 = "+" if -line >= 0 else ""
            sels.append({"label": f"Casa ({sign}{line})", "odd": _f((op.get("home") or {}).get("value")), "won": adj_home > a})
            sels.append({"label": f"Fora ({sign2}{-line})", "odd": _f((op.get("away") or {}).get("value")), "won": adj_home < a})
        return sels
    return []


# ── BACKTEST 2 — persistência acumulada em SQLite ────────────────────────────
# Além do snapshot "últimos 30 jogos" (em memória, recalculado a cada 30min),
# guardamos cada aposta histórica individual num SQLite pra construir um ranking
# ACUMULADO que cresce com o tempo (não se limita ao histórico dos jogos de hoje).
_BT2_DB_PATH = os.path.join(DATA_DIR, "backtest2.db")
_bt2_db_lock = threading.Lock()


def _bt2_push_db_bg():
    """Envia o backtest2.db pro GitHub (branch 'data') em segundo plano — sem isso,
    o histórico acumulado (Backtest 2 e Backtest CS, que moram no mesmo arquivo)
    se perdia a cada redeploy no Railway, porque o disco local não sobrevive entre
    deploys. Restaurado de volta em github_storage.sync_on_startup."""
    github_storage.push_file_bg(_BT2_DB_PATH, "backtest2.db")


def _bt2_db_conn():
    conn = sqlite3.connect(_BT2_DB_PATH, timeout=30)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bt2_bets (
            event_id TEXT NOT NULL,
            market_key TEXT NOT NULL,
            selection TEXT NOT NULL,
            odd REAL NOT NULL,
            won INTEGER NOT NULL,
            profit REAL NOT NULL,
            match_date TEXT,
            inserted_at TEXT NOT NULL,
            PRIMARY KEY (event_id, market_key, selection)
        )
    """)
    return conn


def _bt2_persist_bets(rows):
    """Grava linhas [(event_id, market_key, selection, odd, won, profit, match_date), ...]
    no SQLite via INSERT OR IGNORE (chave event_id+market_key+selection evita
    duplicar apostas já processadas em execuções anteriores)."""
    if not rows:
        return
    now = datetime.utcnow().isoformat() + "Z"
    with _bt2_db_lock:
        conn = _bt2_db_conn()
        try:
            conn.executemany(
                """INSERT OR IGNORE INTO bt2_bets
                   (event_id, market_key, selection, odd, won, profit, match_date, inserted_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [(eid, mk, sel, odd, 1 if won else 0, profit, date, now) for (eid, mk, sel, odd, won, profit, date) in rows],
            )
            conn.commit()
        finally:
            conn.close()
    _bt2_push_db_bg()


def _bt2_compute_variance(profits):
    mean = sum(profits) / len(profits)
    variance = sum((p - mean) ** 2 for p in profits) / len(profits)
    stddev = variance ** 0.5
    cv = (stddev / abs(mean)) if mean != 0 else None
    return variance, stddev, cv


def _bt2_max_streaks(dates, wons):
    """Maior sequência de vitórias seguidas (max green) e maior sequência de
    derrotas seguidas (max red), na ordem cronológica das apostas (por
    match_date) — não é drawdown em unidades, é contagem de apostas."""
    pairs = sorted(zip(dates, wons), key=lambda x: (x[0] or ""))
    max_win = max_loss = cur_win = cur_loss = 0
    for _, won in pairs:
        if won:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        max_win = max(max_win, cur_win)
        max_loss = max(max_loss, cur_loss)
    return max_win, max_loss


def _bt2_odd_ranges_breakdown(odds, wons, profits):
    """A partir das odds/won/profit de cada aposta de uma seleção, agrupa por faixa
    de odd (ver _BT2_ODD_RANGES) e devolve a lista de faixas com amostra suficiente,
    ordenada na mesma ordem das faixas (menor odd primeiro)."""
    by_range = {}
    for odd, won, profit in zip(odds, wons, profits):
        label = _bt2_odd_range_label(odd)
        if not label:
            continue
        r = by_range.setdefault(label, {"bets": 0, "wins": 0, "profit": 0.0})
        r["bets"] += 1
        if won:
            r["wins"] += 1
        r["profit"] += profit

    order = [label for _, _, label in _BT2_ODD_RANGES]
    out = []
    for label in order:
        r = by_range.get(label)
        if not r or r["bets"] < _BT2_MIN_SAMPLE_ODD_RANGE:
            continue
        out.append({
            "faixa":   label,
            "bets":    r["bets"],
            "wins":    r["wins"],
            "winrate": round(r["wins"] / r["bets"], 4),
            "profit":  round(r["profit"], 4),
            "roi":     round(r["profit"] / r["bets"] * 100, 2),
        })
    return out


def _bt2_compute_ranking_acumulado():
    """Ranking acumulado: lê TODAS as apostas já persistidas em bt2_bets (não só as
    dos jogos de hoje) e agrega por mercado+seleção, com timeline cumulativa por
    match_date. Sem cache — leitura no SQLite é barata."""
    with _bt2_db_lock:
        conn = _bt2_db_conn()
        try:
            cur = conn.execute(
                "SELECT market_key, selection, odd, won, profit, match_date FROM bt2_bets ORDER BY match_date ASC"
            )
            all_rows = cur.fetchall()
            sample_matches_used = conn.execute("SELECT COUNT(DISTINCT event_id) FROM bt2_bets").fetchone()[0]
        finally:
            conn.close()

    agg = {}
    for market_key, selection, odd, won, profit, match_date in all_rows:
        bucket = agg.setdefault(market_key, {})
        r = bucket.setdefault(selection, {"bets": 0, "wins": 0, "profit": 0.0, "profits": [], "dates": [], "odds": [], "wons": []})
        r["bets"] += 1
        if won:
            r["wins"] += 1
        r["profit"] += profit
        r["profits"].append(profit)
        r["dates"].append(match_date or "")
        r["odds"].append(odd)
        r["wons"].append(won)

    methodologies = []
    for market_key, bucket in agg.items():
        market_label = _BT2_MARKET_LABELS.get(market_key, market_key)
        for selection, r in bucket.items():
            if r["bets"] < _BT2_MIN_SAMPLE:
                continue
            profits = r["profits"]
            variance, stddev, cv = _bt2_compute_variance(profits)
            max_win_streak, max_loss_streak = _bt2_max_streaks(r["dates"], r["wons"])

            pairs = sorted(zip(r["dates"], profits), key=lambda x: (x[0] or ""))
            cum = 0.0
            timeline = []
            for date, p in pairs:
                cum += p
                timeline.append({"date": date, "cumulative_profit": round(cum, 4)})

            methodologies.append({
                "market": market_key,
                "market_label": market_label,
                "selection": selection,
                "bets": r["bets"],
                "wins": r["wins"],
                "winrate": round(r["wins"] / r["bets"], 4),
                "profit": round(r["profit"], 4),
                "roi": round(r["profit"] / r["bets"] * 100, 2),
                "odd_ranges": _bt2_odd_ranges_breakdown(r["odds"], r["wons"], profits),
                "variance": round(variance, 4),
                "stddev": round(stddev, 4),
                "cv": round(cv, 4) if cv is not None else None,
                "avg_odd": round(sum(r["odds"]) / len(r["odds"]), 2) if r["odds"] else None,
                "max_win_streak": max_win_streak,
                "max_loss_streak": max_loss_streak,
                "timeline": timeline,
            })

    methodologies.sort(key=lambda x: x["profit"], reverse=True)

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sample_matches_used": sample_matches_used,
        "methodologies": methodologies,
    }


def _bt2_compute_ranking():
    """Algoritmo completo: coleta histórico de todos os jogos de hoje, busca odds em
    lote, agrega bets/wins/profit por mercado+seleção e rankeia por lucro."""
    today_matches = _fs_all_matches()

    # 1) Coleta ids históricos (até 400 distintos) percorrendo o H2H de cada jogo de hoje
    historical_by_id = {}
    for m in today_matches:
        if len(historical_by_id) >= _BT2_MAX_HISTORICAL_IDS:
            break
        try:
            tabs = _fs_h2h(m["id"])
        except Exception:
            continue
        for team_home in (True, False):
            for row in _bt2_team_rows_from_section(tabs, team_home):
                if not row["id"] or row["id"] in historical_by_id:
                    continue
                historical_by_id[row["id"]] = row
                if len(historical_by_id) >= _BT2_MAX_HISTORICAL_IDS:
                    break

    hist_ids = list(historical_by_id.keys())

    # 2) Busca odds de todos os jogos históricos em paralelo (reaproveita o pool existente)
    def _fetch_odds(event_id):
        try:
            return event_id, _fs_odds_all_markets_any_bookmaker(event_id)
        except Exception:
            return event_id, (None, {})

    odds_by_id = {}
    for event_id, (bookmaker_name, markets) in _fs_event_pool.map(_fetch_odds, hist_ids):
        if markets:
            odds_by_id[event_id] = markets

    # 3) Agrega globalmente por mercado+seleção
    agg = {}  # market_key -> { label -> {bets,wins,profit,profits:[],dates:[]} }
    sample_matches_used = 0
    persist_rows = []  # (event_id, market_key, selection, odd, won, profit, match_date) p/ SQLite
    for event_id, markets in odds_by_id.items():
        row = historical_by_id.get(event_id)
        if not row:
            continue
        sample_matches_used += 1
        for market_key, market_label in _BT2_MARKET_LABELS.items():
            sels = _bt2_market_selections(market_key, markets.get(market_key), row["h"], row["a"])
            for sel in sels:
                if not sel["odd"]:
                    continue
                bucket = agg.setdefault(market_key, {})
                r = bucket.setdefault(sel["label"], {"bets": 0, "wins": 0, "profit": 0.0, "profits": [], "dates": [], "odds": [], "wons": []})
                p = (sel["odd"] - 1) if sel["won"] else -1
                r["bets"] += 1
                if sel["won"]:
                    r["wins"] += 1
                r["profit"] += p
                r["profits"].append(p)
                r["dates"].append(row.get("date") or "")
                r["odds"].append(sel["odd"])
                r["wons"].append(sel["won"])
                persist_rows.append((str(event_id), market_key, sel["label"], sel["odd"], sel["won"], p, row.get("date") or ""))

    # Persiste cada aposta individual no SQLite (modo acumulado), sem afetar o
    # snapshot "últimos 30" retornado abaixo — é só um efeito colateral de gravação.
    try:
        _bt2_persist_bets(persist_rows)
    except Exception:
        pass

    # 4) Monta lista final com variância/roi/timeline, filtrando amostra mínima
    methodologies = []
    for market_key, bucket in agg.items():
        market_label = _BT2_MARKET_LABELS.get(market_key, market_key)
        for selection, r in bucket.items():
            if r["bets"] < _BT2_MIN_SAMPLE:
                continue
            profits = r["profits"]
            mean = sum(profits) / len(profits)
            variance = sum((p - mean) ** 2 for p in profits) / len(profits)
            stddev = variance ** 0.5
            cv = (stddev / abs(mean)) if mean != 0 else None
            max_win_streak, max_loss_streak = _bt2_max_streaks(r["dates"], r["wons"])

            # timeline: cumulativo ordenado por data ascendente (data no formato timestamp
            # unix em string, mesmo campo "KC" usado no resto do H2H)
            pairs = sorted(zip(r["dates"], profits), key=lambda x: (x[0] or ""))
            cum = 0.0
            timeline = []
            for date, p in pairs:
                cum += p
                timeline.append({"date": date, "cumulative_profit": round(cum, 4)})

            methodologies.append({
                "market": market_key,
                "market_label": market_label,
                "selection": selection,
                "bets": r["bets"],
                "wins": r["wins"],
                "winrate": round(r["wins"] / r["bets"], 4),
                "profit": round(r["profit"], 4),
                "roi": round(r["profit"] / r["bets"] * 100, 2),
                "odd_ranges": _bt2_odd_ranges_breakdown(r["odds"], r["wons"], profits),
                "variance": round(variance, 4),
                "stddev": round(stddev, 4),
                "cv": round(cv, 4) if cv is not None else None,
                "avg_odd": round(sum(r["odds"]) / len(r["odds"]), 2) if r["odds"] else None,
                "max_win_streak": max_win_streak,
                "max_loss_streak": max_loss_streak,
                "timeline": timeline,
            })

    methodologies.sort(key=lambda x: x["profit"], reverse=True)

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sample_matches_used": sample_matches_used,
        "methodologies": methodologies,
    }


@app.route("/api/backtest2/ranking")
def api_backtest2_ranking():
    """Ranking global de metodologias (mercado+seleção) por lucro, agregando a
    simulação de profit em cima do histórico de TODOS os jogos de hoje (não só um
    jogo). Cálculo caro (centenas de lookups de odds), então cacheia em memória por
    30 minutos — use ?refresh=1 pra forçar recálculo."""
    mode = request.args.get("mode") or "recente"
    if mode == "acumulado":
        return jsonify(_bt2_compute_ranking_acumulado())
    force = request.args.get("refresh") == "1"
    if not force and _bt2_cache["data"] is not None and (time.time() - _bt2_cache["ts"]) < _BT2_CACHE_TTL:
        return jsonify(_bt2_cache["data"])
    data = _bt2_compute_ranking()
    _bt2_cache["ts"] = time.time()
    _bt2_cache["data"] = data
    return jsonify(data)


@app.route("/api/backtest2/ranking_acumulado")
def api_backtest2_ranking_acumulado():
    """Ranking ACUMULADO: agrega TODAS as apostas históricas já persistidas em
    bt2_bets (cresce a cada vez que /api/backtest2/ranking roda), não só o
    histórico dos jogos de hoje. Sem cache — leitura SQLite é barata."""
    return jsonify(_bt2_compute_ranking_acumulado())


def _bt2_market_selection_labels(market_key, o):
    """Como _bt2_market_selections, mas SEM depender do placar final (jogo ainda não
    aconteceu) — não filtra linhas 'push' nem calcula 'won', só lista as seleções e
    odds disponíveis nesse mercado pra poder casar com o rótulo de uma metodologia."""
    if not o:
        return []

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    if market_key == "1x2":
        return [
            {"label": "Casa", "odd": _f((o.get("home") or {}).get("value"))},
            {"label": "Empate", "odd": _f((o.get("draw") or {}).get("value"))},
            {"label": "Fora", "odd": _f((o.get("away") or {}).get("value"))},
        ]
    if market_key == "ambos_marcam":
        return [
            {"label": "Sim", "odd": _f((o.get("yes") or {}).get("value"))},
            {"label": "Não", "odd": _f((o.get("no") or {}).get("value"))},
        ]
    if market_key == "dupla_chance":
        return [
            {"label": "1X", "odd": _f((o.get("homeOrDraw") or {}).get("value"))},
            {"label": "12", "odd": _f((o.get("homeOrAway") or {}).get("value"))},
            {"label": "X2", "odd": _f((o.get("drawOrAway") or {}).get("value"))},
        ]
    if market_key == "over_under":
        sels = []
        for op in o.get("opportunities") or []:
            line = _f((op.get("handicap") or {}).get("value"))
            if line is None:
                continue
            sels.append({"label": f"Acima {line}", "odd": _f((op.get("over") or {}).get("value"))})
            sels.append({"label": f"Abaixo {line}", "odd": _f((op.get("under") or {}).get("value"))})
        return sels
    if market_key == "handicap_asiatico":
        sels = []
        for op in o.get("opportunities") or []:
            line = _f((op.get("handicap") or {}).get("value"))
            if line is None:
                continue
            sign = "+" if line >= 0 else ""
            sign2 = "+" if -line >= 0 else ""
            sels.append({"label": f"Casa ({sign}{line})", "odd": _f((op.get("home") or {}).get("value"))})
            sels.append({"label": f"Fora ({sign2}{-line})", "odd": _f((op.get("away") or {}).get("value"))})
        return sels
    return []


@app.route("/api/backtest2/matches_for_methodology")
def api_backtest2_matches_for_methodology():
    """Jogos de HOJE (agendados ou já encerrados) onde a metodologia selecionada
    (mercado+seleção) está disponível nas odds — pra saber em quais partidas reais
    dá pra aplicar aquele sinal hoje, incluindo jogos que já encerraram (útil pra
    conferir quais bateram a metodologia mesmo depois do apito final)."""
    market_key = request.args.get("market", "")
    selection = request.args.get("selection", "")
    if not market_key or not selection:
        return jsonify({"matches": []}), 400

    resultado = []
    for m, markets in _today2_odds_snapshot():
        sels = _bt2_market_selection_labels(market_key, markets.get(market_key))
        for sel in sels:
            if sel["label"] == selection and sel["odd"]:
                resultado.append({
                    "id": m["id"], "home": m["home"], "away": m["away"],
                    "liga": m.get("liga", ""), "pais": m.get("pais", ""),
                    "kickoff_ts": m.get("kickoff_ts"), "odd": sel["odd"],
                })
                break
    resultado.sort(key=lambda x: x.get("kickoff_ts") or "")
    return jsonify({"matches": resultado})


# ── BACKTEST CS — ranking global dos 19 buckets de "placar exato" por TAXA DE
# ACERTO ────────────────────────────────────────────────────────────────────
# Irmã do Backtest 2, mas em vez de rankear mercado+seleção por LUCRO, rankeia
# os 19 buckets fixos de placar exato (0-0 .. 3-3 + 3 "qualquer outro") por
# quantas vezes o placar final de um jogo histórico realmente caiu ali. Não
# precisa de odds pro cálculo do ranking em si (o placar já vem no H2H), só
# reaproveita a coleta de jogos históricos já feita pro Backtest 2 — por isso é
# bem mais rápido. Odds só entram na hora de achar "jogos de hoje que se
# encaixam" num bucket específico.
_btcs_cache = {"ts": 0, "data": None}
_BTCS_CACHE_TTL = 30 * 60  # 30 minutos
_BTCS_MAX_HISTORICAL_IDS = _BT2_MAX_HISTORICAL_IDS

# Amostra mínima pra considerar o ranking como um todo minimamente confiável.
# Diferente do Backtest 2 (onde cada mercado+seleção tem sua própria amostra
# independente), aqui todos os 19 buckets compartilham a MESMA amostra total
# (cada jogo histórico contribui pra exatamente um bucket), então o gate
# relevante é sobre o TOTAL coletado, não por bucket. Ainda assim, exigimos
# pelo menos 1 acerto (hits >= 1) pra listar um bucket, senão buckets raros
# (tipo "3-3") apareceriam com 0% poluindo a tabela sem sinal nenhum.
_BTCS_MIN_TOTAL_SAMPLE = 15
_BTCS_MIN_HITS = 0  # 0 inclui buckets que NUNCA ocorreram — é justamente o que o
                     # filtro "⚡ Placares mais improváveis" precisa mostrar

# Porta de PLACAR_EXATO_BUCKETS_ORDER (JS, static/index.html) — mesma ordem.
PLACAR_EXATO_BUCKETS_ORDER = [
    "0-0", "0-1", "0-2", "0-3", "1-0", "1-1", "1-2", "1-3",
    "2-0", "2-1", "2-2", "2-3", "3-0", "3-1", "3-2", "3-3",
    "Qualquer outra vitória em casa", "Qualquer Outra Vitória de Visitante", "Qualquer outro empate",
]


def _btcs_bucket_key(h, a):
    """Porta exata de _placarExatoBucketKey (JS) pra Python."""
    if h <= 3 and a <= 3:
        return f"{h}-{a}"
    if h > a:
        return "Qualquer outra vitória em casa"
    if a > h:
        return "Qualquer Outra Vitória de Visitante"
    return "Qualquer outro empate"


# ── BACKTEST CS — persistência acumulada em SQLite (mesmo arquivo do Backtest 2,
# tabela própria) ─────────────────────────────────────────────────────────────
def _btcs_db_conn():
    conn = sqlite3.connect(_BT2_DB_PATH, timeout=30)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS btcs_results (
            event_id TEXT PRIMARY KEY,
            bucket TEXT NOT NULL,
            match_date TEXT,
            inserted_at TEXT NOT NULL
        )
    """)
    # Linhas por TIME (não só por jogo) — guarda o suficiente pra recalcular as
    # variáveis dos Padrões (média de gols, mandante etc.) em cima de TODO o
    # histórico já visto, não só os últimos 30 jogos do dia. Mesmo jogo pode
    # aparecer 2x (uma por time), então a chave inclui o time.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS btcs_pattern_rows (
            event_id TEXT NOT NULL,
            team_key TEXT NOT NULL,
            team_name TEXT NOT NULL,
            h INTEGER NOT NULL,
            a INTEGER NOT NULL,
            home_name TEXT,
            match_date TEXT,
            inserted_at TEXT NOT NULL,
            PRIMARY KEY (event_id, team_key)
        )
    """)
    # Migração pra bases já criadas antes da coluna "odd" existir — a odd 1X2 do
    # próprio time naquele jogo histórico, usada como variável de padrão (força do
    # time conforme o mercado precificava, não só o resultado em si)
    try:
        conn.execute("ALTER TABLE btcs_pattern_rows ADD COLUMN odd REAL")
    except sqlite3.OperationalError:
        pass  # coluna já existe
    # Migração pra base de "opp_odd" (odd do ADVERSÁRIO naquele jogo histórico) —
    # precisa das duas odds (própria + adversário) pra replicar as métricas da
    # aba Jogo (Valor do Gol/Ponto/Saldo, Custo do Gol 2.0) como variáveis de padrão
    try:
        conn.execute("ALTER TABLE btcs_pattern_rows ADD COLUMN opp_odd REAL")
    except sqlite3.OperationalError:
        pass  # coluna já existe
    return conn


def _btcs_persist_results(rows):
    """Grava [(event_id, bucket, match_date), ...] via INSERT OR IGNORE — chave
    é só event_id, então o mesmo jogo histórico reaparecendo em janelas futuras
    de 'últimos 30' não é contado duas vezes no acumulado."""
    if not rows:
        return
    now = datetime.utcnow().isoformat() + "Z"
    with _bt2_db_lock:
        conn = _btcs_db_conn()
        try:
            conn.executemany(
                """INSERT OR IGNORE INTO btcs_results
                   (event_id, bucket, match_date, inserted_at)
                   VALUES (?,?,?,?)""",
                [(eid, bucket, date, now) for (eid, bucket, date) in rows],
            )
            conn.commit()
        finally:
            conn.close()
    _bt2_push_db_bg()


def _btcs_persist_pattern_rows(rows):
    """Grava [(event_id, team_key, team_name, h, a, home_name, match_date, odd, opp_odd), ...]
    via upsert — chave é (event_id, team_key), então o mesmo jogo reaparecendo em
    janelas futuras de 'últimos 30' não duplica pro mesmo time. Em conflito,
    COALESCE mantém a odd/opp_odd já salva se a nova vier vazia (ex: rodada em
    que a busca de odds falhou), mas PREENCHE se a linha antiga não tinha esse
    dado ainda — sem isso, linhas salvas antes do opp_odd existir ficavam pra
    sempre sem esse dado (INSERT OR IGNORE nunca atualiza linha já existente)."""
    if not rows:
        return
    now = datetime.utcnow().isoformat() + "Z"
    with _bt2_db_lock:
        conn = _btcs_db_conn()
        try:
            conn.executemany(
                """INSERT INTO btcs_pattern_rows
                   (event_id, team_key, team_name, h, a, home_name, match_date, inserted_at, odd, opp_odd)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(event_id, team_key) DO UPDATE SET
                     odd = COALESCE(btcs_pattern_rows.odd, excluded.odd),
                     opp_odd = COALESCE(btcs_pattern_rows.opp_odd, excluded.opp_odd)""",
                [(eid, tk, tn, h, a, home, date, now, odd, opp_odd) for (eid, tk, tn, h, a, home, date, odd, opp_odd) in rows],
            )
            conn.commit()
        finally:
            conn.close()
    _bt2_push_db_bg()


def _btcs_build_methodologies(bucket_dates):
    """A partir de {bucket: [dates ordenáveis...]} (uma entrada por acerto,
    já na ordem de ocorrência) e do total_sample, monta a lista final ordenada
    por winrate desc, com timeline de hit count acumulado."""
    total_sample = sum(len(v) for v in bucket_dates.values())
    methodologies = []
    if total_sample >= _BTCS_MIN_TOTAL_SAMPLE:
        for bucket in PLACAR_EXATO_BUCKETS_ORDER:
            dates = sorted(bucket_dates.get(bucket, []), key=lambda d: d or "")
            hits = len(dates)
            if hits < _BTCS_MIN_HITS:
                continue
            cum = 0
            timeline = []
            for date in dates:
                cum += 1
                timeline.append({"date": date, "cumulative_hits": cum})
            methodologies.append({
                "bucket": bucket,
                "bets": total_sample,
                "wins": hits,
                "winrate": round(hits / total_sample, 4),
                "timeline": timeline,
            })
    methodologies.sort(key=lambda x: x["winrate"], reverse=True)
    return total_sample, methodologies


def _btcs_compute_ranking():
    """Coleta o histórico de todos os jogos de hoje (mesma lógica de
    _bt2_compute_ranking, sem precisar de odds) e rankeia os 19 buckets de
    placar exato por taxa de acerto."""
    today_matches = _fs_all_matches()

    historical_by_id = {}
    for m in today_matches:
        if len(historical_by_id) >= _BTCS_MAX_HISTORICAL_IDS:
            break
        try:
            tabs = _fs_h2h(m["id"])
        except Exception:
            continue
        for team_home in (True, False):
            for row in _bt2_team_rows_from_section(tabs, team_home):
                if not row["id"] or row["id"] in historical_by_id:
                    continue
                historical_by_id[row["id"]] = row
                if len(historical_by_id) >= _BTCS_MAX_HISTORICAL_IDS:
                    break

    bucket_dates = {}
    persist_rows = []
    for event_id, row in historical_by_id.items():
        bucket = _btcs_bucket_key(row["h"], row["a"])
        bucket_dates.setdefault(bucket, []).append(row.get("date") or "")
        persist_rows.append((str(event_id), bucket, row.get("date") or ""))

    try:
        _btcs_persist_results(persist_rows)
    except Exception:
        pass

    total_sample, methodologies = _btcs_build_methodologies(bucket_dates)

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sample_matches_used": total_sample,
        "methodologies": methodologies,
    }


def _btcs_compute_ranking_acumulado():
    """Ranking acumulado: lê TODAS as linhas já persistidas em btcs_results
    (dedupadas por event_id) e reaplica a mesma agregação por bucket."""
    with _bt2_db_lock:
        conn = _btcs_db_conn()
        try:
            cur = conn.execute("SELECT bucket, match_date FROM btcs_results ORDER BY match_date ASC")
            all_rows = cur.fetchall()
        finally:
            conn.close()

    bucket_dates = {}
    for bucket, match_date in all_rows:
        bucket_dates.setdefault(bucket, []).append(match_date or "")

    total_sample, methodologies = _btcs_build_methodologies(bucket_dates)

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sample_matches_used": total_sample,
        "methodologies": methodologies,
    }


# ── BACKUP DIÁRIO DO BACKTEST (Tradicional + CS) ────────────────────────────
# A aba Backtest foi removida do frontend (usuário não usa mais por enquanto,
# mas quer voltar quando tiver mais volume de dados histórico) — sem a aba,
# ninguém mais dispara o cálculo caro (_bt2_compute_ranking/_btcs_compute_ranking,
# que são os únicos jeitos de bt2_bets/btcs_results crescerem). Essa thread roda
# esse cálculo sozinha 1x por dia, sem depender de ninguém clicar em nada, e sobe
# o backtest2.db pro GitHub — mesmo padrão de persistência já usado pelo resto
# do app (github_storage), pra sobreviver a reinícios/redeploys do Railway.
def _background_backtest_daily_backup():
    while True:
        try:
            print("[backtest-backup] Rodando simulação diária do Backtest (Tradicional + CS)...")
            _bt2_compute_ranking()
            _btcs_compute_ranking()
            github_storage.push_file_bg(_BT2_DB_PATH, "backtest2.db")
            print("[backtest-backup] Concluído — backtest2.db atualizado e enviado pro GitHub.")
        except Exception as e:
            print(f"[backtest-backup] Erro: {e}")
        time.sleep(24 * 3600)


threading.Thread(target=_background_backtest_daily_backup, daemon=True, name="BacktestDailyBackup").start()


# ── BACKTEST CS — PADRÕES: em que condições dos times a taxa de acerto de um
# bucket de placar exato melhora vs a taxa geral (baseline) ─────────────────
# Reusa a MESMA coleta de histórico do ranking, mas mantendo o agrupamento POR
# TIME (o ranking achata tudo num dict global de event_id -> row; aqui
# precisamos saber a QUEM cada jogo pertence pra calcular médias/percentuais
# por time e então segmentar). Não busca odds — só o H2H já dá tudo que
# precisa, por isso é rápido como o ranking.
_btcs_patterns_cache = {"ts": 0, "data": None}

_BTCS_PATTERN_VARIABLES = {
    "avg_scored": "Média de gols marcados",
    "avg_conceded": "Média de gols sofridos",
    "pct_scored": "Chance de marcar gol",
    "pct_conceded": "Chance de sofrer gol",
    "pct_over25": "Over/Under 2.5",
    "pct_ambas_marcam": "Ambas Marcam",
    "saldo": "Saldo de Gols",
    "mandante": "Mandante",
    "odd": "Odd 1X2 (força do time)",
    # Row-level, só contam nos jogos com odd PRÓPRIA e do ADVERSÁRIO conhecidas
    # (opp_odd) — mesmas fórmulas da aba Jogo (Aulas 09/10 do curso: pondera o
    # resultado bruto pela força do adversário, medida pela odd dele).
    "valor_ponto": "Valor do Ponto",
    "valor_gol": "Valor do Gol",
    "valor_saldo": "Valor do Saldo",
    "custo_gol2": "Custo do Gol 2.0",
}

_BTCS_SALDO_RANGES = [(-0.3, "<-0.3"), (0.3, "-0.3–0.3"), (None, "≥0.3")]
_BTCS_VALOR_PONTO_RANGES = [(0.3, "<0.3"), (0.6, "0.3–0.6"), (1.0, "0.6–1.0"), (None, "≥1.0")]
_BTCS_VALOR_GOL_RANGES = [(0.15, "<0.15"), (0.3, "0.15–0.3"), (0.5, "0.3–0.5"), (None, "≥0.5")]
_BTCS_VALOR_SALDO_RANGES = [(-0.05, "<-0.05"), (0.05, "-0.05–0.05"), (None, "≥0.05")]
_BTCS_CUSTO_GOL2_RANGES = [(0.5, "<0.5"), (0.7, "0.5–0.7"), (0.9, "0.7–0.9"), (None, "≥0.9")]

_BTCS_PATTERN_MIN_SEGMENT_GAMES = 30
_BTCS_PATTERN_MIN_SEGMENT_HITS = 3

# Pares de variáveis combinadas (ex: "média de gols marcados" + "mandante") têm
# amostra naturalmente menor que uma variável isolada — exige menos jogos/acertos
# pra não descartar tudo, mas ainda o suficiente pra não virar coincidência.
_BTCS_COMBO_MIN_SEGMENT_GAMES = 20
_BTCS_COMBO_MIN_SEGMENT_HITS = 2
_BTCS_PATTERN_MIN_LIFT = 1.3
_BTCS_PATTERN_TOP_N = 30

# "Padrões que reduzem a chance" — o oposto: condições em que um placar fica
# AINDA MENOS provável que o normal (útil pra apostar CONTRA aquele placar
# com mais confiança). Aqui não exigimos um mínimo de acertos no segmento
# (0 acertos é o caso ideal), só amostra suficiente pra confiar no número.
_BTCS_PATTERN_MAX_LIFT_AVOID = 0.7
_BTCS_PATTERN_AVOID_TOP_N = 30

# Faixas de segmentação: lista de (limite_superior_exclusivo, label), em ordem
# crescente, o último item deve ter limite None (>= o penúltimo limite).
_BTCS_AVG_RANGES = [(1.5, "<1.5 gols/jogo"), (2.5, "1.5–2.5 gols/jogo"),
                     (3.5, "2.5–3.5 gols/jogo"), (None, "≥3.5 gols/jogo")]
_BTCS_PCT_RANGES = [(0.70, "<70%"), (0.90, "70–90%"), (None, "≥90%")]
_BTCS_OVER_RANGES = [(0.40, "<40%"), (0.60, "40–60%"), (None, "≥60%")]


def _btcs_norm_name(s):
    """Normaliza nome de time pra comparação (minúsculo, sem acento, só
    alfanumérico+espaço) — mesma técnica do norm() interno de _fs_find_match."""
    import unicodedata
    s = (s or "").lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _btcs_segment_range(value, ranges):
    for hi, label in ranges:
        if hi is None or value < hi:
            return label
    return ranges[-1][1]


def _btcs_build_patterns_from_teams(teams):
    """Núcleo compartilhado entre o modo 'últimos 30' e o 'acumulado': recebe
    {team_key: {"name":..., "rows":[{id,h,a,date,home}, ...]}} já montado (de
    onde vier — H2H de hoje ou SQLite acumulado) e calcula variáveis team-level
    (média de gols marcados/sofridos, chance de marcar/sofrer, over/under 2.5)
    + uma variável row-level (mandante), medindo o LIFT da taxa de acerto de
    cada bucket de placar exato dentro de cada segmento vs a taxa geral."""
    # Baseline: taxa de acerto geral de cada bucket sobre TODOS os jogos
    # coletados (mesma amostra usada pra formar os segmentos abaixo).
    baseline_bucket_counts = {}
    total_games = 0
    for entry in teams.values():
        for row in entry["rows"]:
            bucket = _btcs_bucket_key(row["h"], row["a"])
            baseline_bucket_counts[bucket] = baseline_bucket_counts.get(bucket, 0) + 1
            total_games += 1
    baseline_rate = {b: (c / total_games if total_games else 0.0) for b, c in baseline_bucket_counts.items()}

    # {variable: {segment: {bucket: hits, "_total": n_jogos_no_segmento}}}
    seg_data = {v: {} for v in _BTCS_PATTERN_VARIABLES}
    # {(var1,var2): {"segA + segB": {bucket: hits, "_total": n}}} — pares de
    # variáveis combinadas (ex: "2.5-3.5 gols/jogo" + "Jogando em casa")
    combo_seg_data = {}

    def _accum(variable, segment, bucket):
        seg = seg_data[variable].setdefault(segment, {"_total": 0})
        seg["_total"] += 1
        seg[bucket] = seg.get(bucket, 0) + 1

    def _accum_combo(var_combo, label_combo, bucket):
        seg = combo_seg_data.setdefault(var_combo, {})
        combo_label = " + ".join(label_combo)
        entry = seg.setdefault(combo_label, {"_total": 0})
        entry["_total"] += 1
        entry[bucket] = entry.get(bucket, 0) + 1

    _TEAM_LEVEL_VARS = ("avg_scored", "avg_conceded", "pct_scored", "pct_conceded", "pct_over25",
                         "pct_ambas_marcam", "saldo")
    # valor_ponto/valor_gol/valor_saldo/custo_gol2 são row-level como "odd" (só
    # contam nos jogos com odd própria E do adversário conhecidas — ver
    # _btcs_row_valor_segs) — mesmas fórmulas da aba Jogo (Aulas 09/10).
    _ALL_PATTERN_VARS = _TEAM_LEVEL_VARS + ("mandante", "odd", "valor_ponto", "valor_gol", "valor_saldo", "custo_gol2")
    # Combinações de 2 a 3 variáveis ao mesmo tempo (ex: média de gols marcados
    # + chance de sofrer gol + mandante). Com 11 variáveis disponíveis agora
    # (dobrou desde a versão original de 7), combos de tamanho 4 explodiriam o
    # tempo de cálculo (C(11,4) = 330 vs C(11,3) = 165) sem ganho relevante de
    # sinal — cortado por segurança de performance (mesma lição aprendida com o
    # travamento do fetch de odds do Mapa de Sugestões).
    _COMBO_SIZES = (2, 3)
    _VAR_COMBOS = [c for size in _COMBO_SIZES for c in itertools.combinations(_ALL_PATTERN_VARS, size)]

    def _btcs_row_valor_segs(own, opp, own_odd, opp_odd):
        """Segmentos das 4 variáveis dependentes de odd, ou None se faltar
        odd própria ou do adversário nesse jogo específico."""
        if not own_odd or not opp_odd:
            return None
        own_prob = 1.0 / own_odd
        opp_prob = 1.0 / opp_odd
        pontos = 3 if own > opp else (1 if own == opp else 0)
        saldo_row = own - opp
        return {
            "valor_ponto": _btcs_segment_range(pontos * opp_prob, _BTCS_VALOR_PONTO_RANGES),
            "valor_gol": _btcs_segment_range(own * opp_prob, _BTCS_VALOR_GOL_RANGES),
            "valor_saldo": _btcs_segment_range(saldo_row * opp_prob, _BTCS_VALOR_SALDO_RANGES),
            "custo_gol2": _btcs_segment_range((own / 2) + (own_prob / 2), _BTCS_CUSTO_GOL2_RANGES),
        }

    for entry in teams.values():
        rows = entry["rows"]
        n = len(rows)
        if n == 0:
            continue
        team_key = _btcs_norm_name(entry["name"])
        scored_total = conceded_total = 0
        games_scored = games_conceded = games_over25 = games_ambas = 0
        row_is_home = []
        for row in rows:
            row_home_key = _btcs_norm_name(row.get("home"))
            # Se por algum motivo o nome não bate com nenhum lado (dado
            # incompleto), assume casa como padrão conservador.
            is_home = (row_home_key == team_key) if row_home_key and team_key else True
            own = row["h"] if is_home else row["a"]
            opp = row["a"] if is_home else row["h"]
            scored_total += own
            conceded_total += opp
            if own >= 1:
                games_scored += 1
            if opp >= 1:
                games_conceded += 1
            if row["h"] + row["a"] >= 3:
                games_over25 += 1
            if own >= 1 and opp >= 1:
                games_ambas += 1
            row_is_home.append(is_home)

        team_segs = {
            "avg_scored": _btcs_segment_range(scored_total / n, _BTCS_AVG_RANGES),
            "avg_conceded": _btcs_segment_range(conceded_total / n, _BTCS_AVG_RANGES),
            "pct_scored": _btcs_segment_range(games_scored / n, _BTCS_PCT_RANGES),
            "pct_conceded": _btcs_segment_range(games_conceded / n, _BTCS_PCT_RANGES),
            "pct_over25": _btcs_segment_range(games_over25 / n, _BTCS_OVER_RANGES),
            "pct_ambas_marcam": _btcs_segment_range(games_ambas / n, _BTCS_PCT_RANGES),
            "saldo": _btcs_segment_range((scored_total - conceded_total) / n, _BTCS_SALDO_RANGES),
        }

        for row in rows:
            bucket = _btcs_bucket_key(row["h"], row["a"])
            for v in _TEAM_LEVEL_VARS:
                _accum(v, team_segs[v], bucket)

        # Mandante, Odd e as 4 variáveis de "valor" são row-level: os jogos de
        # um time podem se dividir entre segmentos diferentes jogo a jogo,
        # diferente das variáveis acima que atribuem o time inteiro a UM
        # segmento. Monta o perfil row-level UMA VEZ e reaproveita tanto pro
        # acúmulo individual quanto pras combinações abaixo.
        row_valor_segs = []
        for row, is_home in zip(rows, row_is_home):
            bucket = _btcs_bucket_key(row["h"], row["a"])
            segment = "Jogando em casa" if is_home else "Jogando fora"
            _accum("mandante", segment, bucket)
            odd_label = _bt2_odd_range_label(row.get("odd"))
            if odd_label:
                _accum("odd", odd_label, bucket)
            own = row["h"] if is_home else row["a"]
            opp = row["a"] if is_home else row["h"]
            valor_segs = _btcs_row_valor_segs(own, opp, row.get("odd"), row.get("opp_odd"))
            if valor_segs:
                for v, seg in valor_segs.items():
                    _accum(v, seg, bucket)
            row_valor_segs.append(valor_segs)

        # Combinações de 2-3 variáveis: monta o "perfil" completo de CADA jogo
        # (segmento de todas as variáveis team-level, que são as mesmas em
        # todos os jogos do time, + mandante/odd/valor daquele jogo específico)
        # e acumula em cada combinação de variáveis que existir. Jogos sem odd
        # (própria ou do adversário) só ficam de fora das combinações que
        # incluem essas variáveis.
        for row, is_home, valor_segs in zip(rows, row_is_home, row_valor_segs):
            bucket = _btcs_bucket_key(row["h"], row["a"])
            row_segs = dict(team_segs)
            row_segs["mandante"] = "Jogando em casa" if is_home else "Jogando fora"
            odd_label = _bt2_odd_range_label(row.get("odd"))
            if odd_label:
                row_segs["odd"] = odd_label
            if valor_segs:
                row_segs.update(valor_segs)
            for var_combo in _VAR_COMBOS:
                if any(v not in row_segs for v in var_combo):
                    continue
                _accum_combo(var_combo, [row_segs[v] for v in var_combo], bucket)

    patterns = []
    patterns_avoid = []
    for variable, segments in seg_data.items():
        variable_label = _BTCS_PATTERN_VARIABLES[variable]
        for segment, counts in segments.items():
            segment_games = counts["_total"]
            if segment_games < _BTCS_PATTERN_MIN_SEGMENT_GAMES:
                continue
            # Buckets que nunca acertaram nesse segmento não aparecem em
            # `counts` (só acumulamos quando há hit) — pra achar os padrões
            # de "reduz a chance" precisamos considerar TODOS os 19 buckets,
            # inclusive os com 0 acertos no segmento.
            for bucket in PLACAR_EXATO_BUCKETS_ORDER:
                hits = counts.get(bucket, 0)
                base = baseline_rate.get(bucket, 0.0)
                if base <= 0:
                    continue  # sem baseline confiável pra comparar, pula
                segment_rate = hits / segment_games
                lift = segment_rate / base
                row = {
                    "variable": variable,
                    "variable_label": variable_label,
                    "segment": segment,
                    "segment_label": segment,
                    "bucket": bucket,
                    "segment_games": segment_games,
                    "segment_hits": hits,
                    "segment_rate": round(segment_rate, 4),
                    "baseline_rate": round(base, 4),
                    "lift": round(lift, 3),
                    "combined": False,
                }
                if hits >= _BTCS_PATTERN_MIN_SEGMENT_HITS and lift >= _BTCS_PATTERN_MIN_LIFT:
                    patterns.append(row)
                elif lift <= _BTCS_PATTERN_MAX_LIFT_AVOID:
                    patterns_avoid.append(row)

    # Mesma lógica acima, mas pros pares de variáveis combinadas — limiares
    # de amostra/acertos mais baixos (_BTCS_COMBO_*) porque cada combinação
    # naturalmente reparte a amostra em fatias menores.
    for var_combo, segments in combo_seg_data.items():
        variable_label = " + ".join(_BTCS_PATTERN_VARIABLES[v] for v in var_combo)
        for combo_label, counts in segments.items():
            segment_games = counts["_total"]
            if segment_games < _BTCS_COMBO_MIN_SEGMENT_GAMES:
                continue
            for bucket in PLACAR_EXATO_BUCKETS_ORDER:
                hits = counts.get(bucket, 0)
                base = baseline_rate.get(bucket, 0.0)
                if base <= 0:
                    continue
                segment_rate = hits / segment_games
                lift = segment_rate / base
                row = {
                    "variable": "+".join(var_combo),
                    "variable_label": variable_label,
                    "segment": combo_label,
                    "segment_label": combo_label,
                    "bucket": bucket,
                    "segment_games": segment_games,
                    "segment_hits": hits,
                    "segment_rate": round(segment_rate, 4),
                    "baseline_rate": round(base, 4),
                    "lift": round(lift, 3),
                    "combined": True,
                    "combo_size": len(var_combo),
                }
                if hits >= _BTCS_COMBO_MIN_SEGMENT_HITS and lift >= _BTCS_PATTERN_MIN_LIFT:
                    patterns.append(row)
                elif lift <= _BTCS_PATTERN_MAX_LIFT_AVOID:
                    patterns_avoid.append(row)

    # "Aumenta a chance": ordena por lift desc; segment_hits como desempate
    # (entre lifts iguais/muito próximos, prefere o padrão com mais evidência).
    patterns.sort(key=lambda p: (p["lift"], p["segment_hits"]), reverse=True)
    patterns = patterns[:_BTCS_PATTERN_TOP_N]

    # "Reduz a chance": ordena por lift ASC (quanto mais perto de 0, mais forte
    # o sinal de que aquele placar não vai sair), segment_games como desempate
    # (entre lifts iguais, prefere o padrão com amostra maior/mais confiável).
    patterns_avoid.sort(key=lambda p: (p["lift"], -p["segment_games"]))

    # Melhor padrão (menor lift) de CADA um dos 19 placares, calculado sobre a
    # lista COMPLETA (antes do corte de top-30 abaixo) — assim cobre todo
    # placar que tiver algum padrão qualificado, não só os que entram no top-30.
    best_avoid_by_bucket = {}
    for p in patterns_avoid:
        cur = best_avoid_by_bucket.get(p["bucket"])
        if cur is None or p["lift"] < cur["lift"]:
            best_avoid_by_bucket[p["bucket"]] = p
    patterns_avoid_best_by_bucket = sorted(best_avoid_by_bucket.values(), key=lambda p: p["lift"])

    patterns_avoid = patterns_avoid[:_BTCS_PATTERN_AVOID_TOP_N]

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sample_teams_used": len(teams),
        "sample_matches_used": total_games,
        "patterns": patterns,
        "patterns_avoid": patterns_avoid,
        "patterns_avoid_best_by_bucket": patterns_avoid_best_by_bucket,
    }


def _btcs_compute_patterns():
    """Coleta o histórico dos times de hoje (últimos 30 jogos por time,
    mesma fonte do H2H) mantendo o agrupamento POR TIME, monta os padrões via
    _btcs_build_patterns_from_teams, e persiste cada linha (jogo+time) no
    SQLite como efeito colateral — é isso que alimenta o modo 'acumulado'."""
    today_matches = _fs_all_matches()

    teams = {}  # norm(nome) -> {"name": nome original, "rows": [...]}
    all_ids_seen = set()
    total_ids = 0
    for m in today_matches:
        if total_ids >= _BTCS_MAX_HISTORICAL_IDS:
            break
        try:
            tabs = _fs_h2h(m["id"])
        except Exception:
            continue
        for team_home in (True, False):
            if total_ids >= _BTCS_MAX_HISTORICAL_IDS:
                break
            team_name = m["home"] if team_home else m["away"]
            key = _btcs_norm_name(team_name)
            if not key:
                continue
            rows = _bt2_team_rows_from_section(tabs, team_home)
            if not rows:
                continue
            entry = teams.setdefault(key, {"name": team_name, "rows": []})
            existing_ids = {r["id"] for r in entry["rows"]}
            for row in rows:
                if not row["id"] or row["id"] in existing_ids or row["id"] in all_ids_seen:
                    continue
                entry["rows"].append(row)
                existing_ids.add(row["id"])
                all_ids_seen.add(row["id"])
                total_ids += 1
                if total_ids >= _BTCS_MAX_HISTORICAL_IDS:
                    break

    # Busca a odd 1X2 de cada jogo histórico único (mesmo pool/endpoint já usado
    # no Backtest Tradicional) e anexa a odd do PRÓPRIO time (Casa se ele jogou em
    # casa naquele jogo, Fora se jogou fora) em cada linha — alimenta a variável
    # de padrão "odd" (força do time conforme o mercado precificava). Só pede o
    # mercado "1x2" (markets_wanted) — é o único que essa função usa, e pedir os
    # outros 5 à toa (o padrão de _fs_odds_all_markets_any_bookmaker) foi o que
    # deixava isso ~700s pra 400 jogos: medido e confirmado que o gargalo real
    # era aqui, não no fetch de H2H (que sozinho leva só alguns segundos).
    def _fetch_odds_btcs(event_id):
        try:
            return event_id, _fs_odds_all_markets_any_bookmaker(event_id, markets_wanted=["1x2"])
        except Exception:
            return event_id, (None, {})

    odds_by_id = {}
    for event_id, (bookmaker_name, markets) in _fs_event_pool.map(_fetch_odds_btcs, list(all_ids_seen)):
        if markets:
            odds_by_id[event_id] = markets

    for key, entry in teams.items():
        for row in entry["rows"]:
            row_home_key = _btcs_norm_name(row.get("home"))
            is_home = (row_home_key == key) if row_home_key and key else True
            markets = odds_by_id.get(row["id"])
            row["odd"] = None
            row["opp_odd"] = None
            if markets:
                sels = _bt2_market_selections("1x2", markets.get("1x2"), row["h"], row["a"])
                target_label = "Casa" if is_home else "Fora"
                opp_label = "Fora" if is_home else "Casa"
                for sel in sels:
                    if sel["label"] == target_label:
                        row["odd"] = sel["odd"]
                    elif sel["label"] == opp_label:
                        row["opp_odd"] = sel["odd"]

    persist_rows = [
        (row["id"], key, entry["name"], row["h"], row["a"], row.get("home"), row.get("date"), row.get("odd"), row.get("opp_odd"))
        for key, entry in teams.items()
        for row in entry["rows"]
    ]
    try:
        _btcs_persist_pattern_rows(persist_rows)
    except Exception:
        pass

    return _btcs_build_patterns_from_teams(teams)


def _btcs_compute_patterns_acumulado():
    """Mesma lógica de _btcs_compute_patterns, mas monta 'teams' a partir de
    TODO o histórico já persistido em btcs_pattern_rows (não só os times de
    hoje nem os últimos 30 jogos) — cresce a cada vez que o modo 'últimos 30'
    roda. Sem cache — leitura no SQLite é barata."""
    with _bt2_db_lock:
        conn = _btcs_db_conn()
        try:
            cur = conn.execute(
                "SELECT event_id, team_key, team_name, h, a, home_name, match_date, odd, opp_odd FROM btcs_pattern_rows"
            )
            all_rows = cur.fetchall()
        finally:
            conn.close()

    teams = {}
    for event_id, team_key, team_name, h, a, home_name, match_date, odd, opp_odd in all_rows:
        entry = teams.setdefault(team_key, {"name": team_name, "rows": []})
        entry["rows"].append({"id": event_id, "h": h, "a": a, "home": home_name, "date": match_date, "odd": odd, "opp_odd": opp_odd})

    return _btcs_build_patterns_from_teams(teams)


# ── MAPA DE SUGESTÕES — motor genérico de metodologias — pra cada metodologia
# (Lay Casa, Lay Visitante, Casa Vence, Over/Under, Ambas Marcam, Lay Placar X,
# etc.) acha DINAMICAMENTE qual variável (das mesmas usadas na aba Jogo +
# Backtest CS: média/chance de gols, over/under, ambas marcam, saldo) melhor
# prediz o evento-alvo daquela metodologia, testando cada uma isoladamente
# contra o histórico do time relevante (mandante jogando em casa, ou
# visitante jogando fora) — sem limiar fixo escolhido por mim, o sistema
# escolhe sozinho a variável+faixa com maior lift e amostra suficiente, e
# aplica esse padrão validado às partidas de hoje pra gerar as sugestões.
# Cada metodologia tem seu próprio padrão (congelado 1x por dia) e sua
# própria lista de sugestões acumulada — ver notas em _mapa_compute().
_LAYCASA_MIN_SEGMENT_GAMES = 30
_LAYCASA_MIN_LIFT = 1.15
_LAYCASA_TTL = 30 * 60
_LAYCASA_SALDO_RANGES = [(-0.3, "<-0.3"), (0.3, "-0.3–0.3"), (None, "≥0.3")]

# cache de dados (30min) e cache do padrão do dia — ambos keyed por metodologia
_MAPA_CACHE = {}      # metodologia -> {"ts":..., "data":...}
_MAPA_DAY_CACHE = {}  # metodologia -> {"date":..., "melhor":..., "comparativo":..., "sugestoes_by_id":...}


def _mapa_cache_path(metodologia_key):
    return os.path.join(MAPA_CACHE_DIR, f"{metodologia_key}.json")


def _mapa_load_day_cache_from_disk(metodologia_key, today_str):
    """Tenta restaurar o padrão do dia + lista de sugestões travada, salvos por
    uma execução anterior (sobrevive a redeploy/reinício, que apaga a memória).
    Só usa o arquivo se for do dia de hoje — de outra forma, ignora (vira o dia,
    escolhe um padrão novo)."""
    path = _mapa_cache_path(metodologia_key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") != today_str:
            return None
        return data
    except Exception as e:
        print(f"[mapa] Erro lendo cache do disco ({metodologia_key}): {e}")
        return None


def _mapa_save_day_cache_to_disk(metodologia_key, day_cache):
    path = _mapa_cache_path(metodologia_key)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(day_cache, f, ensure_ascii=False)
        github_storage.push_file_bg(path, f"mapa_cache/{metodologia_key}.json")
    except Exception as e:
        print(f"[mapa] Erro salvando cache no disco ({metodologia_key}): {e}")


def _mapa_slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _mapa_variable_labels(side):
    suf = "em casa" if side == "casa" else "fora"
    return {
        "avg_scored": f"Média de gols marcados ({suf})",
        "avg_conceded": f"Média de gols sofridos ({suf})",
        "pct_scored": f"Chance de marcar ({suf})",
        "pct_conceded": f"Chance de sofrer gol ({suf})",
        "pct_over25": f"Over/Under 2.5 ({suf})",
        "pct_ambas_marcam": f"Ambas Marcam ({suf})",
        "saldo": f"Saldo de Gols ({suf})",
    }


_MAPA_METODOLOGIAS = {}


def _mapa_add(key, label, side, target_fn, needs_ht=False):
    _MAPA_METODOLOGIAS[key] = {"label": label, "side": side, "target_fn": target_fn, "needs_ht": needs_ht}


_mapa_add("lay_casa", "Lay Casa (contra o mandante vencer)", "casa", lambda r: r["own"] <= r["opp"])
_mapa_add("lay_visitante", "Lay Visitante (contra o visitante vencer)", "visitante", lambda r: r["own"] <= r["opp"])
_mapa_add("casa_vence", "Casa Vence", "casa", lambda r: r["own"] > r["opp"])
_mapa_add("visitante_vence", "Visitante Vence", "visitante", lambda r: r["own"] > r["opp"])
_mapa_add("empate", "Empate", "casa", lambda r: r["own"] == r["opp"])
_mapa_add("over_15", "Over 1.5 FT", "casa", lambda r: (r["own"] + r["opp"]) >= 2)
_mapa_add("over_25", "Over 2.5 FT", "casa", lambda r: (r["own"] + r["opp"]) >= 3)
_mapa_add("under_15", "Under 1.5 FT", "casa", lambda r: (r["own"] + r["opp"]) <= 1)
_mapa_add("under_25", "Under 2.5 FT", "casa", lambda r: (r["own"] + r["opp"]) <= 2)
# HT precisa de um fetch extra por jogo histórico (placar do intervalo não vem
# junto do H2H) — o target_fn retorna None quando não achou esse dado, e
# _mapa_compute() ignora linhas com None nas contagens (ver needs_ht abaixo).
_mapa_add("over_05ht", "Over 0.5 HT", "casa",
          lambda r: ((r["own_ht"] + r["opp_ht"]) >= 1) if "own_ht" in r else None, needs_ht=True)
_mapa_add("over_15ht", "Over 1.5 HT", "casa",
          lambda r: ((r["own_ht"] + r["opp_ht"]) >= 2) if "own_ht" in r else None, needs_ht=True)
_mapa_add("ambas_sim", "Ambas Marcam Sim", "casa", lambda r: r["own"] >= 1 and r["opp"] >= 1)
_mapa_add("ambas_nao", "Ambas Marcam Não", "casa", lambda r: not (r["own"] >= 1 and r["opp"] >= 1))
for _bucket in PLACAR_EXATO_BUCKETS_ORDER:
    _mapa_add(f"lay_placar_{_mapa_slug(_bucket)}", f"Lay Placar {_bucket}", "casa",
              (lambda bucket: (lambda r: _btcs_bucket_key(r["own"], r["opp"]) != bucket))(_bucket))


def _laycasa_home_rows(tabs):
    """Jogos do mandante jogando EM CASA (aba 'Casa' do H2H, índice 1) — mesma
    lógica de _jogoStatsRowsFromSection(tabs, 1, true) no frontend (aba Jogo).
    'own'/'opp' = gols do próprio mandante / do adversário nesse jogo passado."""
    if not tabs or len(tabs) < 2:
        return []
    tab = tabs[1]
    sections = [s for s in tab.get("sections", []) if "confront" not in (s.get("title") or "").lower()]
    sec = sections[0] if sections else None
    if not sec:
        return []
    rows = []
    for r in sec["rows"][:30]:
        sc = _bt2_parse_score(r.get("score"))
        if not sc:
            continue
        rows.append({"id": r.get("id"), "own": sc[0], "opp": sc[1], "date": r.get("date"),
                     "home": r.get("home"), "away": r.get("away")})
    return rows


def _laycasa_away_rows(tabs):
    """Jogos do visitante jogando FORA (aba 'Fora' do H2H, índice 2) — mesma
    lógica de _jogoStatsRowsFromSection(tabs, 2, false) no frontend (aba Jogo).
    'own'/'opp' = gols do próprio visitante / do adversário (mandante) nesse
    jogo passado — por isso invertido em relação ao placar h:a literal."""
    if not tabs or len(tabs) < 3:
        return []
    tab = tabs[2]
    sections = [s for s in tab.get("sections", []) if "confront" not in (s.get("title") or "").lower()]
    sec = sections[0] if sections else None
    if not sec:
        return []
    rows = []
    for r in sec["rows"][:30]:
        sc = _bt2_parse_score(r.get("score"))
        if not sc:
            continue
        rows.append({"id": r.get("id"), "own": sc[1], "opp": sc[0], "date": r.get("date"),
                     "home": r.get("home"), "away": r.get("away")})
    return rows


def _laycasa_team_metrics(rows):
    """Calcula as variáveis descritivas (sem depender de odds) pra UM time, só
    com os jogos em que ele jogou no papel relevante (casa ou fora). Retorna
    (segments_dict, n) ou (None, 0) se não tiver amostra suficiente pra
    confiar na média."""
    n = len(rows)
    if n < 5:
        return None, 0
    scored = sum(r["own"] for r in rows)
    conceded = sum(r["opp"] for r in rows)
    games_scored = sum(1 for r in rows if r["own"] >= 1)
    games_conceded = sum(1 for r in rows if r["opp"] >= 1)
    games_over25 = sum(1 for r in rows if r["own"] + r["opp"] >= 3)
    ambas_marcaram = sum(1 for r in rows if r["own"] >= 1 and r["opp"] >= 1)

    segs = {
        "avg_scored": _btcs_segment_range(scored / n, _BTCS_AVG_RANGES),
        "avg_conceded": _btcs_segment_range(conceded / n, _BTCS_AVG_RANGES),
        "pct_scored": _btcs_segment_range(games_scored / n, _BTCS_PCT_RANGES),
        "pct_conceded": _btcs_segment_range(games_conceded / n, _BTCS_PCT_RANGES),
        "pct_over25": _btcs_segment_range(games_over25 / n, _BTCS_OVER_RANGES),
        "pct_ambas_marcam": _btcs_segment_range(ambas_marcaram / n, _BTCS_PCT_RANGES),
        "saldo": _btcs_segment_range((scored - conceded) / n, _LAYCASA_SALDO_RANGES),
    }
    return segs, n


def _mapa_compute(metodologia_key):
    cfg = _MAPA_METODOLOGIAS.get(metodologia_key)
    if not cfg:
        return None
    side = cfg["side"]
    target_fn = cfg["target_fn"]
    rows_fn = _laycasa_home_rows if side == "casa" else _laycasa_away_rows
    team_field = "home" if side == "casa" else "away"
    var_labels = _mapa_variable_labels(side)

    cache = _MAPA_CACHE.setdefault(metodologia_key, {"ts": 0, "data": None})
    now = time.time()
    if cache["data"] and (now - cache["ts"]) < _LAYCASA_TTL:
        return cache["data"]

    today_str = datetime.now().strftime("%Y-%m-%d")
    day_cache = _MAPA_DAY_CACHE.setdefault(metodologia_key, {"date": None, "melhor": None, "comparativo": None, "sugestoes_by_id": {}})
    if day_cache["date"] != today_str:
        # virou o dia (ou 1ª execução deste processo) — antes de zerar tudo,
        # tenta restaurar do disco o que uma execução anterior já tinha travado
        # hoje (sobrevive a redeploy/reinício do Railway, que apaga a memória —
        # sem isso, cada deploy escolhia um padrão novo do zero no meio do dia).
        restored = _mapa_load_day_cache_from_disk(metodologia_key, today_str)
        if restored:
            day_cache["date"] = today_str
            day_cache["melhor"] = restored.get("melhor")
            day_cache["comparativo"] = restored.get("comparativo")
            day_cache["sugestoes_by_id"] = restored.get("sugestoes_by_id") or {}
        else:
            # virou o dia (ou primeira execução) — libera escolher um padrão novo e
            # zera a lista acumulada de sugestões
            day_cache["date"] = today_str
            day_cache["melhor"] = None
            day_cache["comparativo"] = None
            day_cache["sugestoes_by_id"] = {}

    # só entra gente NOVA na lista de sugestões na hora em que o padrão do dia é
    # escolhido pela primeira vez (nesta execução ou restaurado do disco já
    # travado) — depois disso a lista trava: só atualiza placar/resultado de
    # quem já está nela, nunca acrescenta partida nova (pedido do usuário, que
    # achava estranho a lista mudar toda vez que clicava em "Recalcular").
    pode_adicionar_novas = day_cache["melhor"] is None

    # Agendados (pra sugerir) + já encerrados hoje (pra mostrar o placar final e
    # se a sugestão teria acertado) — pools separados e cada um com seu próprio
    # teto, senão um dia com muitos jogos já encerrados "engoliria" as vagas dos
    # próximos jogos ainda por vir na lista de candidatos.
    scheduled = sorted((m for m in _fs_all_matches() if m.get("status") == "1"), key=lambda m: m.get("kickoff_ts") or "")
    finished_today = sorted((m for m in _fs_all_matches() if m.get("status") == "3"), key=lambda m: m.get("kickoff_ts") or "")
    candidatos = scheduled[:_BT2_MATCHES_MAX_CANDIDATOS] + finished_today[:_BT2_MATCHES_MAX_CANDIDATOS]
    today_matches = candidatos

    def _fetch_h2h(m):
        try:
            return m["id"], rows_fn(_fs_h2h(m["id"]))
        except Exception:
            return m["id"], []

    rows_by_match = dict(_fs_event_pool.map(_fetch_h2h, candidatos))

    teams = {}  # norm(nome do time relevante) -> {"name":..., "rows":[...]}
    all_ids_seen = set()
    total_ids = 0
    for m in candidatos:
        if total_ids >= _BTCS_MAX_HISTORICAL_IDS:
            break
        rows = rows_by_match.get(m["id"]) or []
        key = _btcs_norm_name(m[team_field])
        if not key or not rows:
            continue
        entry = teams.setdefault(key, {"name": m[team_field], "rows": []})
        existing_ids = {r["id"] for r in entry["rows"]}
        for row in rows:
            if total_ids >= _BTCS_MAX_HISTORICAL_IDS:
                break
            if not row["id"] or row["id"] in existing_ids or row["id"] in all_ids_seen:
                continue
            entry["rows"].append(row)
            existing_ids.add(row["id"])
            all_ids_seen.add(row["id"])
            total_ids += 1

    # Metodologias de 1º tempo (needs_ht) precisam do placar do intervalo de
    # cada jogo histórico, que NÃO vem junto do H2H — busca extra, um feed por
    # jogo, mas rápida em paralelo (~1s pra 20 jogos testado manualmente,
    # bem mais barato que o fetch de odds que trava com centenas de jogos).
    if cfg["needs_ht"] and all_ids_seen:
        ht_by_id = dict(_fs_event_pool.map(lambda eid: (eid, _fs_half_time(eid)), all_ids_seen))
        for entry in teams.values():
            for row in entry["rows"]:
                ht = ht_by_id.get(row["id"])
                if not ht:
                    continue
                try:
                    hs, as_ = int(ht["home"]), int(ht["away"])
                except (TypeError, ValueError, KeyError):
                    continue
                row["own_ht"] = hs if side == "casa" else as_
                row["opp_ht"] = as_ if side == "casa" else hs

    # baseline: taxa do evento-alvo sobre TODOS os jogos coletados — é contra
    # isso que o lift de cada segmento é medido
    baseline_total = baseline_hits = 0
    for entry in teams.values():
        for row in entry["rows"]:
            hit = target_fn(row)
            if hit is None:  # sem dado de HT pra essa linha (needs_ht) — ignora
                continue
            baseline_total += 1
            if hit:
                baseline_hits += 1
    baseline_rate = (baseline_hits / baseline_total) if baseline_total else 0.0

    seg_data = {}       # variável -> faixa -> {"total":n, "hits":n}
    team_profile = {}   # chave normalizada -> {"name":..., "segs":..., "n":...}
    for key, entry in teams.items():
        segs, n = _laycasa_team_metrics(entry["rows"])
        if not segs:
            continue
        team_profile[key] = {"name": entry["name"], "segs": segs, "n": n}
        for var, seg in segs.items():
            bucket = seg_data.setdefault(var, {}).setdefault(seg, {"total": 0, "hits": 0})
            for row in entry["rows"]:
                hit = target_fn(row)
                if hit is None:
                    continue
                bucket["total"] += 1
                if hit:
                    bucket["hits"] += 1

    # acha a MELHOR combinação variável+faixa (maior lift, com amostra
    # suficiente) — é isso que faz o sistema ser dinâmico em vez de eu fixar
    # manualmente um limiar. Só é escolhida UMA VEZ POR DIA (fica congelada em
    # day_cache) — sem isso, cada recálculo (a cada 30min) podia escolher uma
    # variável+faixa diferente e a grade de sugestões ficava trocando de jogos
    # o dia todo, o que não faz sentido pro usuário acompanhar ao longo do dia.
    if day_cache["melhor"] is not None:
        melhor = day_cache["melhor"]
        comparativo = day_cache["comparativo"]
    else:
        melhor = None
        for var, segs in seg_data.items():
            for seg_label, counts in segs.items():
                total = counts["total"]
                if total < _LAYCASA_MIN_SEGMENT_GAMES:
                    continue
                rate = counts["hits"] / total
                lift = (rate / baseline_rate) if baseline_rate else 0.0
                if lift < _LAYCASA_MIN_LIFT:
                    continue
                if melhor is None or lift > melhor["lift"]:
                    melhor = {
                        "variable": var, "variable_label": var_labels.get(var, var),
                        "segment": seg_label, "total": total, "rate": round(rate, 4),
                        "baseline_rate": round(baseline_rate, 4), "lift": round(lift, 3),
                    }

        # gráfico comparativo: taxa de acerto acumulada (geral vs com o filtro
        # validado acima) ao longo de todas as entradas históricas coletadas,
        # ordenadas por data — mesmo padrão do timeline de cumulative_hits usado
        # no Backtest CS, só que aqui é % de acerto acumulado, não contagem bruta
        comparativo = None
        if melhor:
            baseline_entries = []
            filtro_entries = []
            for key, entry in teams.items():
                profile = team_profile.get(key)
                in_segment = bool(profile) and profile["segs"].get(melhor["variable"]) == melhor["segment"]
                for row in entry["rows"]:
                    hit = target_fn(row)
                    if hit is None:
                        continue
                    baseline_entries.append((row.get("date") or "", hit))
                    if in_segment:
                        filtro_entries.append((row.get("date") or "", hit))

            def _cum_hitrate_timeline(entries):
                entries = sorted(entries, key=lambda e: e[0])
                timeline = []
                hits = 0
                for i, (date, hit) in enumerate(entries, start=1):
                    if hit:
                        hits += 1
                    timeline.append({"n": i, "date": date, "taxa_acerto": round(hits / i, 4)})
                return timeline

            comparativo = {
                "geral": _cum_hitrate_timeline(baseline_entries),
                "com_filtro": _cum_hitrate_timeline(filtro_entries),
            }

        day_cache["melhor"] = melhor
        day_cache["comparativo"] = comparativo

    # aplica o padrão validado às partidas de hoje: quais times relevantes têm
    # o PRÓPRIO segmento igual ao segmento validado acima? A lista TRAVA na
    # primeira vez que o padrão do dia é escolhido (pode_adicionar_novas) — só
    # entram partidas novas nesse momento. Depois disso, cada recálculo só
    # ATUALIZA placar/encerrado/acertou de quem já está na lista (nunca some,
    # nunca ganha gente nova) — evita a lista mudando toda vez que o usuário
    # clica em "Recalcular" ou o servidor reinicia no meio do dia.
    sugestoes_by_id = day_cache["sugestoes_by_id"]
    if melhor:
        for m in today_matches:
            key = _btcs_norm_name(m[team_field])
            id_str = str(m["id"])
            ja_esta_na_lista = id_str in sugestoes_by_id
            if not ja_esta_na_lista and not pode_adicionar_novas:
                continue
            profile = team_profile.get(key)
            if not profile:
                continue
            if profile["segs"].get(melhor["variable"]) == melhor["segment"]:
                status = m.get("status")
                item = {
                    "id": m["id"], "home": m["home"], "away": m["away"],
                    "liga": m.get("liga", ""), "pais": m.get("pais", ""),
                    "kickoff_ts": m.get("kickoff_ts"),
                    "amostra_time": profile["n"],
                    "encerrado": status == "3",
                }
                if status == "3":
                    hs, as_ = m.get("home_score"), m.get("away_score")
                    item["home_score"] = hs
                    item["away_score"] = as_
                    if hs is not None and as_ is not None:
                        try:
                            hs_i, as_i = int(hs), int(as_)
                            final_row = {
                                "own": hs_i if side == "casa" else as_i,
                                "opp": as_i if side == "casa" else hs_i,
                            }
                            if cfg["needs_ht"]:
                                ht = _fs_half_time(m["id"])
                                if ht:
                                    hs_ht, as_ht = int(ht["home"]), int(ht["away"])
                                    final_row["own_ht"] = hs_ht if side == "casa" else as_ht
                                    final_row["opp_ht"] = as_ht if side == "casa" else hs_ht
                            item["acertou"] = target_fn(final_row)
                        except (TypeError, ValueError):
                            pass
                sugestoes_by_id[id_str] = item
        _mapa_save_day_cache_to_disk(metodologia_key, day_cache)
    sugestoes = sorted(sugestoes_by_id.values(), key=lambda x: x.get("kickoff_ts") or "")

    data = {
        "metodologia": metodologia_key,
        "metodologia_label": cfg["label"],
        "side": side,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "matches_used": total_ids,
        "melhor_padrao": melhor,
        "valido": melhor is not None,
        "comparativo": comparativo,
        "sugestoes": sugestoes,
    }
    cache["data"] = data
    cache["ts"] = now
    return data


@app.route("/api/masterlist/metodologias")
def api_masterlist_metodologias():
    """Lista as metodologias disponíveis no Mapa de Sugestões, pra popular o
    dropdown do frontend."""
    return jsonify({"metodologias": [{"key": k, "label": v["label"]} for k, v in _MAPA_METODOLOGIAS.items()]})


@app.route("/api/masterlist/<metodologia>")
def api_masterlist_generic(metodologia):
    """'Mapa de Sugestões' — acha dinamicamente qual variável (das usadas na
    aba Jogo + Backtest CS) melhor prediz o evento-alvo da metodologia
    escolhida, valida contra o histórico, e aplica às partidas de hoje.
    Cacheado 30min (mesmo TTL do Backtest CS)."""
    data = _mapa_compute(metodologia)
    if data is None:
        return jsonify({"error": "metodologia desconhecida"}), 404
    return jsonify(data)


@app.route("/api/masterlist/<metodologia>/atualizar_dia", methods=["POST"])
def api_masterlist_atualizar_dia(metodologia):
    """Reconstrói o padrão do dia + lista de sugestões do ZERO pra essa
    metodologia — usado quando o Mapa fica "preso" mostrando jogos antigos
    (ex: virou o dia mas por algum motivo o cache não acompanhou). Diferente
    do recálculo normal (que só atualiza placar de quem já está na lista, de
    propósito, pra não ficar mudando a lista toda hora), esse endpoint apaga
    tudo e deixa escolher um padrão novo — por isso só permite 1x por dia,
    pra não reintroduzir o mesmo problema que a trava resolveu."""
    if metodologia not in _MAPA_METODOLOGIAS:
        return jsonify({"error": "metodologia desconhecida"}), 404

    today_str = datetime.now().strftime("%Y-%m-%d")
    day_cache = _MAPA_DAY_CACHE.get(metodologia)
    if day_cache and day_cache.get("manual_reset_date") == today_str:
        return jsonify({"error": "essa metodologia já foi atualizada manualmente hoje — só é permitido 1x por dia"}), 429

    _MAPA_DAY_CACHE[metodologia] = {
        "date": today_str, "melhor": None, "comparativo": None,
        "sugestoes_by_id": {}, "manual_reset_date": today_str,
    }
    _MAPA_CACHE[metodologia] = {"ts": 0, "data": None}
    path = _mapa_cache_path(metodologia)
    if os.path.exists(path):
        os.remove(path)

    data = _mapa_compute(metodologia)
    return jsonify(data)


# ── ABA JOGO — MÉDIAS GERAIS — baseline pra comparação: qual a média de cada
# estatística (vitórias, gols, ambas marcam, over/under...) entre TODOS os
# times de hoje, não só o time que o usuário está olhando. Reaproveita
# _laycasa_home_rows/_laycasa_away_rows (mesma extração de linhas do Mapa de
# Sugestões) — um único fetch de H2H por jogo já dá tanto a amostra "casa"
# quanto "fora". Recalculada 1x por dia (não faz sentido recalcular toda hora,
# a média de centenas de times não muda de uma hora pra outra).
_JOGO_MEDIAS_CACHE = {"date": None, "data": None}


def _jogo_fetch_extra(rows, side):
    """Busca minuto dos gols (pra 'minuto médio do 1º gol', gols por faixa de
    minuto e vencedor a cada checkpoint), placar do intervalo (pra 'metade com
    mais gols') e odd 1x2 própria/adversário (pra Valor do Gol/Ponto/Saldo,
    Custo do Gol 2.0) de cada linha, tudo numa passada só em paralelo. Só pede
    o mercado "1x2" (markets_wanted) — mesmo truque que já derrubou o tempo do
    Backtest CS de 706s pra 48s, evitando buscar os outros 5 mercados à toa.
    Sem esses dados a linha simplesmente não entra nas médias que dependem
    deles (ver _jogo_stats_from_rows / _jogo_goal_pattern_from_rows /
    _jogo_goal_diff_from_rows)."""
    def _fetch(row):
        eid = row["id"]
        if not eid:
            return row
        try:
            gm = _fs_goal_minutes(eid)
        except Exception:
            gm = None
        if gm:
            if side == "casa":
                row["gm_own"], row["gm_opp"] = gm.get("home", []), gm.get("away", [])
            else:
                row["gm_own"], row["gm_opp"] = gm.get("away", []), gm.get("home", [])
        try:
            ht = _fs_half_time(eid)
            hs, as_ = int(ht["home"]), int(ht["away"])
            row["ht_own"], row["ht_opp"] = (hs, as_) if side == "casa" else (as_, hs)
        except Exception:
            pass
        try:
            _, markets = _fs_odds_all_markets_any_bookmaker(eid, markets_wanted=["1x2"])
            h, a = (row["own"], row["opp"]) if side == "casa" else (row["opp"], row["own"])
            sels = _bt2_market_selections("1x2", markets.get("1x2"), h, a)
            target_label = "Casa" if side == "casa" else "Fora"
            opp_label = "Fora" if side == "casa" else "Casa"
            for sel in sels:
                if sel["label"] == target_label and sel["odd"]:
                    row["odd"] = sel["odd"]
                elif sel["label"] == opp_label and sel["odd"]:
                    row["opp_odd"] = sel["odd"]
        except Exception:
            pass
        return row

    return list(_fs_event_pool.map(_fetch, rows))


def _jogo_stats_from_rows(rows):
    n = len(rows)
    if n == 0:
        return None
    vit = sum(1 for r in rows if r["own"] > r["opp"])
    emp = sum(1 for r in rows if r["own"] == r["opp"])
    der = n - vit - emp
    marcou = sum(1 for r in rows if r["own"] >= 1)
    sofreu = sum(1 for r in rows if r["opp"] >= 1)
    ambas = sum(1 for r in rows if r["own"] >= 1 and r["opp"] >= 1)
    sem_sofrer = sum(1 for r in rows if r["opp"] == 0)
    sem_marcar = sum(1 for r in rows if r["own"] == 0)
    over25 = sum(1 for r in rows if (r["own"] + r["opp"]) > 2.5)

    def _ou_total(line):
        over = sum(1 for r in rows if (r["own"] + r["opp"]) > line)
        return {"sobre": round(over / n * 100, 1), "sob": round((n - over) / n * 100, 1)}

    def _ou_team(line):
        over = sum(1 for r in rows if r["own"] > line)
        return {"sobre": round(over / n * 100, 1), "sob": round((n - over) / n * 100, 1)}

    # Minuto médio do 1º gol — só nas linhas com dado de minutos de gol
    gm_rows = [r for r in rows if "gm_own" in r]
    tempo_marcado = tempo_sofrido = tempo_partida = None
    if gm_rows:
        def _first_min(mins):
            return min(mins) if mins else 90
        tempo_marcado = round(sum(_first_min(r["gm_own"]) for r in gm_rows) / len(gm_rows), 1)
        tempo_sofrido = round(sum(_first_min(r["gm_opp"]) for r in gm_rows) / len(gm_rows), 1)
        tempo_partida = round(sum(min(_first_min(r["gm_own"]), _first_min(r["gm_opp"])) for r in gm_rows) / len(gm_rows), 1)

    # Valor do Gol/Ponto/Saldo e Custo do Gol 2.0 — só nas linhas com odd
    # própria E do adversário conhecidas, mesmas fórmulas da aba Jogo
    odd_rows = [r for r in rows if r.get("odd") and r.get("opp_odd")]
    valor_gol_marcado = valor_gol_sofrido = custo_gol_marcado = custo_gol_sofrido = valor_saldo = valor_ponto = None
    if odd_rows:
        nn = len(odd_rows)
        vgm = vgs = cgm = cgs = vs = vp = 0.0
        for r in odd_rows:
            own_prob = 1.0 / r["odd"]
            opp_prob = 1.0 / r["opp_odd"]
            pontos = 3 if r["own"] > r["opp"] else (1 if r["own"] == r["opp"] else 0)
            saldo_row = r["own"] - r["opp"]
            vgm += r["own"] * opp_prob
            vgs += r["opp"] * own_prob
            cgm += (r["own"] / 2) + (own_prob / 2)
            cgs += (r["opp"] / 2) + (own_prob / 2)
            vs += saldo_row * opp_prob
            vp += pontos * opp_prob
        valor_gol_marcado, valor_gol_sofrido = round(vgm / nn, 3), round(vgs / nn, 3)
        custo_gol_marcado, custo_gol_sofrido = round(cgm / nn, 3), round(cgs / nn, 3)
        valor_saldo, valor_ponto = round(vs / nn, 3), round(vp / nn, 3)

    return {
        "n": n,
        "vitFT_pct": round(vit / n * 100, 1),
        "empFT_pct": round(emp / n * 100, 1),
        "derFT_pct": round(der / n * 100, 1),
        "mediaMarcados": round(sum(r["own"] for r in rows) / n, 2),
        "mediaSofridos": round(sum(r["opp"] for r in rows) / n, 2),
        "pctMarcar": round(marcou / n * 100, 1),
        "pctSofrer": round(sofreu / n * 100, 1),
        "pctAmbasMarcam": round(ambas / n * 100, 1),
        "semSofrer_pct": round(sem_sofrer / n * 100, 1),
        "semMarcar_pct": round(sem_marcar / n * 100, 1),
        "over25_pct": round(over25 / n * 100, 1),
        "under25_pct": round((n - over25) / n * 100, 1),
        "ou15_total": _ou_total(1.5),
        "ou25_total": _ou_total(2.5),
        "ou15_team": _ou_team(1.5),
        "ou25_team": _ou_team(2.5),
        "ou35_team": _ou_team(3.5),
        "saldo": round(sum(r["own"] - r["opp"] for r in rows) / n, 2),
        "tempoPrimeiroGolMarcado": tempo_marcado,
        "tempoPrimeiroGolSofrido": tempo_sofrido,
        "tempoPrimeiroGolPartida": tempo_partida,
        "valorGolMarcado": valor_gol_marcado,
        "valorGolSofrido": valor_gol_sofrido,
        "custoGol2Marcado": custo_gol_marcado,
        "custoGol2Sofrido": custo_gol_sofrido,
        "valorSaldo": valor_saldo,
        "valorPonto": valor_ponto,
    }


def _jogo_goal_pattern_from_rows(rows):
    """Porta pra Python de _jogoGoalPatternStats (JS) — faixas de gols, ambas
    marcam, par/ímpar, gols por faixa de 15min e minuto do 1º gol por faixa de
    10min — pra alimentar as médias gerais da seção 'Características gerais
    do gol' / 'Gols nos minutos entre' / 'Primeiro gol nas partidas'. Reusa o
    gm_own/gm_opp já anexado por _jogo_fetch_extra, sem fetch adicional."""
    n = len(rows)
    if n == 0:
        return None
    total_goals = [r["own"] + r["opp"] for r in rows]
    ambos = sum(1 for r in rows if r["own"] >= 1 and r["opp"] >= 1)
    apenas_um = sum(1 for r in rows if (r["own"] >= 1) != (r["opp"] >= 1))
    nenhum = sum(1 for r in rows if r["own"] == 0 and r["opp"] == 0)
    impar = sum(1 for g in total_goals if g % 2 == 1)

    gm_rows = [r for r in rows if "gm_own" in r]
    total_com_gm = len(gm_rows)

    def _bucket15(m):
        if m <= 15:
            return 0
        if m <= 30:
            return 1
        if m <= 45:
            return 2
        if m <= 60:
            return 3
        if m <= 75:
            return 4
        return 5

    def _bucket10(m):
        return min(8, max(0, (m - 1) // 10))

    all_buckets = [0] * 6
    team_buckets = [0] * 6
    all_count = team_count = 0
    primeiro_buckets = [0] * 9
    primeiro_team_buckets = [0] * 9
    sem_gol = sem_gol_team = time_primeiro = adversario_primeiro = 0

    for r in gm_rows:
        pro, contra = r["gm_own"], r["gm_opp"]
        for m in pro:
            mm = min(m, 90)
            all_buckets[_bucket15(mm)] += 1
            all_count += 1
            team_buckets[_bucket15(mm)] += 1
            team_count += 1
        for m in contra:
            mm = min(m, 90)
            all_buckets[_bucket15(mm)] += 1
            all_count += 1

        events = sorted([(m, True) for m in pro] + [(m, False) for m in contra])
        if not events:
            sem_gol += 1
            sem_gol_team += 1
            continue
        first_m, first_is_team = events[0]
        primeiro_buckets[_bucket10(min(first_m, 90))] += 1
        if first_is_team:
            time_primeiro += 1
            primeiro_team_buckets[_bucket10(min(first_m, 90))] += 1
        else:
            adversario_primeiro += 1
            sem_gol_team += 1

    def _pct_list(buckets, total):
        return [round(b / total * 100, 1) for b in buckets] if total else None

    return {
        "faixa01_pct": round(sum(1 for g in total_goals if g <= 1) / n * 100, 1),
        "faixa23_pct": round(sum(1 for g in total_goals if 2 <= g <= 3) / n * 100, 1),
        "faixa4mais_pct": round(sum(1 for g in total_goals if g >= 4) / n * 100, 1),
        "ambosMarcam_pct": round(ambos / n * 100, 1),
        "apenasUm_pct": round(apenas_um / n * 100, 1),
        "nenhum_pct": round(nenhum / n * 100, 1),
        "impar_pct": round(impar / n * 100, 1),
        "par_pct": round((n - impar) / n * 100, 1),
        "totalComGm": total_com_gm,
        "allGoalsBuckets_pct": _pct_list(all_buckets, all_count),
        "teamGoalsBuckets_pct": _pct_list(team_buckets, team_count),
        "primeiroGolBuckets_pct": _pct_list(primeiro_buckets, total_com_gm),
        "primeiroGolTeamBuckets_pct": _pct_list(primeiro_team_buckets, total_com_gm),
        "semGol_pct": round(sem_gol / total_com_gm * 100, 1) if total_com_gm else None,
        "semGolTeam_pct": round(sem_gol_team / total_com_gm * 100, 1) if total_com_gm else None,
        "timeMarcouPrimeiro_pct": round(time_primeiro / total_com_gm * 100, 1) if total_com_gm else None,
        "adversarioMarcouPrimeiro_pct": round(adversario_primeiro / total_com_gm * 100, 1) if total_com_gm else None,
    }


def _jogo_winner_at_minute_from_rows(rows):
    """Porta de _jogoWinnerAtMinuteStats (JS) — quem está na frente no placar
    a cada checkpoint (15/30/45/60/75/90min), usando gm_own/gm_opp já
    anexados por _jogo_fetch_extra."""
    gm_rows = [r for r in rows if "gm_own" in r]
    n = len(gm_rows)
    if n == 0:
        return None
    out = {}
    for cp in (15, 30, 45, 60, 75, 90):
        time_c = adv_c = emp_c = 0
        for r in gm_rows:
            gp = sum(1 for m in r["gm_own"] if m <= cp)
            gc = sum(1 for m in r["gm_opp"] if m <= cp)
            if gp > gc:
                time_c += 1
            elif gc > gp:
                adv_c += 1
            else:
                emp_c += 1
        out[str(cp)] = {
            "time_pct": round(time_c / n * 100, 1),
            "adversario_pct": round(adv_c / n * 100, 1),
            "empate_pct": round(emp_c / n * 100, 1),
        }
    return {"n": n, "checkpoints": out}


def _jogo_goal_diff_from_rows(rows):
    """Porta de _jogoGoalDiffStats (JS) — diferença de gols na partida e qual
    metade do jogo teve mais gols (usando ht_own/ht_opp já anexados por
    _jogo_fetch_extra)."""
    n = len(rows)
    if n == 0:
        return None
    diff_arr = [abs(r["own"] - r["opp"]) for r in rows]
    diff01 = sum(1 for d in diff_arr if d <= 1) / n * 100
    diff23 = sum(1 for d in diff_arr if 2 <= d <= 3) / n * 100
    diff4mais = sum(1 for d in diff_arr if d >= 4) / n * 100

    ht_rows = [r for r in rows if "ht_own" in r]
    n_ht = len(ht_rows)
    primeiro_tempo = segunda_metade = gravata = None
    if n_ht:
        pt = sm = gv = 0
        for r in ht_rows:
            gols_ht = r["ht_own"] + r["ht_opp"]
            gols_st = (r["own"] + r["opp"]) - gols_ht
            if gols_ht > gols_st:
                pt += 1
            elif gols_st > gols_ht:
                sm += 1
            else:
                gv += 1
        primeiro_tempo = round(pt / n_ht * 100, 1)
        segunda_metade = round(sm / n_ht * 100, 1)
        gravata = round(gv / n_ht * 100, 1)

    return {
        "diff01_pct": round(diff01, 1),
        "diff23_pct": round(diff23, 1),
        "diff4mais_pct": round(diff4mais, 1),
        "nHt": n_ht,
        "primeiroTempo_pct": primeiro_tempo,
        "segundaMetade_pct": segunda_metade,
        "gravata_pct": gravata,
    }


def _jogo_medias_gerais_compute():
    today_str = datetime.now().strftime("%Y-%m-%d")
    if _JOGO_MEDIAS_CACHE["date"] == today_str and _JOGO_MEDIAS_CACHE["data"] is not None:
        return _JOGO_MEDIAS_CACHE["data"]

    scheduled = sorted((m for m in _fs_all_matches() if m.get("status") == "1"), key=lambda m: m.get("kickoff_ts") or "")
    finished_today = sorted((m for m in _fs_all_matches() if m.get("status") == "3"), key=lambda m: m.get("kickoff_ts") or "")
    candidatos = scheduled[:_BT2_MATCHES_MAX_CANDIDATOS] + finished_today[:_BT2_MATCHES_MAX_CANDIDATOS]

    def _fetch(m):
        try:
            tabs = _fs_h2h(m["id"])
            return _laycasa_home_rows(tabs), _laycasa_away_rows(tabs)
        except Exception:
            return [], []

    casa_rows, fora_rows = [], []
    seen_casa, seen_fora = set(), set()
    for home_rows, away_rows in _fs_event_pool.map(_fetch, candidatos):
        for row in home_rows:
            if row["id"] and row["id"] not in seen_casa and len(casa_rows) < _BTCS_MAX_HISTORICAL_IDS:
                seen_casa.add(row["id"])
                casa_rows.append(row)
        for row in away_rows:
            if row["id"] and row["id"] not in seen_fora and len(fora_rows) < _BTCS_MAX_HISTORICAL_IDS:
                seen_fora.add(row["id"])
                fora_rows.append(row)

    casa_rows = _jogo_fetch_extra(casa_rows, "casa")
    fora_rows = _jogo_fetch_extra(fora_rows, "fora")
    geral_rows = casa_rows + fora_rows

    def _scope_data(rows):
        stats = _jogo_stats_from_rows(rows)
        if stats is None:
            return None
        for extra in (_jogo_goal_pattern_from_rows(rows), _jogo_goal_diff_from_rows(rows)):
            if extra:
                stats.update(extra)
        winner = _jogo_winner_at_minute_from_rows(rows)
        if winner:
            stats["vencedor"] = winner
        return stats

    data = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "casa": _scope_data(casa_rows),
        "fora": _scope_data(fora_rows),
        "geral": _scope_data(geral_rows),
    }
    _JOGO_MEDIAS_CACHE["date"] = today_str
    _JOGO_MEDIAS_CACHE["data"] = data
    return data


@app.route("/api/jogo/medias_gerais")
def api_jogo_medias_gerais():
    """Médias gerais (Geral/Casa/Fora) das estatísticas da aba Jogo, pra
    comparação — mesmos números que aparecem no card de cada time, só que
    calculados em cima de uma amostra ampla de times de hoje. Cacheado 1x por
    dia."""
    return jsonify(_jogo_medias_gerais_compute())


# ═══════════════════════════════════════════════════════════════════════════
# SINAIS DO DIA — substitui o Mapa de Sugestões (32 metodologias de variável+
# segmento) por 8 sinais narrativos, os mesmos usados na Leitura da Partida
# (🧠). Cada jogo de hoje que disparar um sinal vira uma sugestão; quando o
# jogo encerra, confere acerto/erro contra o placar final (e minutos de gol,
# quando precisar). Reaproveita toda a infraestrutura de cálculo já usada
# pelas médias gerais (_jogo_fetch_extra/_jogo_stats_from_rows/
# _jogo_goal_pattern_from_rows), só aplicada a UM time por vez em vez da
# amostra agregada.
#
# Diferente do Mapa antigo (padrão travado o dia todo, sobrevive a
# reinício): aqui a contagem é só do DIA — vira o dia, zera tudo e recomeça
# do zero com os jogos novos, por pedido do usuário. A persistência em disco
# só serve pra não perder o que já foi contabilizado HOJE se o servidor
# reiniciar no meio do dia (mesmo tipo de proteção usada em todo o app).
#
# "Momentum aos 75min" (existia na Leitura da Partida) foi deixado de fora a
# pedido do usuário. "Maior destaque acima da média" só considera as
# estatísticas "checáveis" contra o placar final (vitória/empate/derrota,
# marcar/sofrer gol, ambas marcam, clean sheet, over/under 2.5) — as que
# dependem de minuto exato (faixas de minuto, checkpoints) ficaram de fora
# dessa 1ª versão.
# ═══════════════════════════════════════════════════════════════════════════
_SINAIS_TTL = 30 * 60
_SINAIS_MAX_CANDIDATOS = 15  # cada candidato = ~2×30 jogos históricos pra enriquecer; mantém conservador na 1ª versão
_SINAIS_MIN_N = 8
_SINAIS_CACHE = {"ts": 0, "data": None}
_SINAIS_DAY_CACHE = {"date": None, "sugestoes": {}, "manual_reset_date": None}
_SINAIS_LOCK = threading.Lock()  # evita 2 requisições simultâneas refazendo o
# mesmo cálculo caro do zero cada uma (sem isso, 2 cliques próximos — ou o
# navegador tentando de novo — dobravam o trabalho em vez de a 2ª aproveitar
# o resultado da 1ª)

SINAIS_LABELS = {
    "primeiro_gol": "Primeiro Gol",
    "mandante_forte": "Mandante Forte",
    "visitante_fraco": "Visitante Fraco",
    "tendencia_gols": "Tendência de Gols",
    "padrao_valor": "Padrão de Valor",
    "placar_raro": "Placar Raro (Lay)",
    "clean_sheet": "Clean Sheet Cruzado",
    "maior_destaque": "Maior Destaque",
}


def _sinais_confianca(n, pct):
    """Selo de confiança por SUGESTÃO individual (não confundir com a taxa de
    acerto do DIA por tipo de sinal, que é outro número) — combina tamanho da
    amostra com a força do sinal (%), mesma régua já usada nos cards da
    Leitura da Partida (_today2Confidence no JS): amostra pequena ou sinal
    fraco nunca vira Alta, mesmo passando no limiar mínimo que já decidiu SE
    o sinal dispara."""
    if n >= 20 and pct >= 75:
        return {"label": "Alta confiança", "color": "#22c55e"}
    if n >= 12 and pct >= 62:
        return {"label": "Média confiança", "color": "#f59e0b"}
    return {"label": "Baixa confiança", "color": "#ef4444"}


def _sinais_cache_path():
    return os.path.join(DATA_DIR, "sinais_dia_cache.json")


def _sinais_load_from_disk(today_str):
    path = _sinais_cache_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") != today_str:
            return None
        return data
    except Exception as e:
        print(f"[sinais] Erro lendo cache do disco: {e}")
        return None


def _sinais_save_to_disk(day_cache):
    path = _sinais_cache_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(day_cache, f, ensure_ascii=False)
        github_storage.push_file_bg(path, "sinais_dia_cache.json")
    except Exception as e:
        print(f"[sinais] Erro salvando cache no disco: {e}")


def _sinais_fetch_extra_batch(rows_with_side):
    """Versão em LOTE de _jogo_fetch_extra, só que enxuta: os 8 sinais só
    precisam do minuto dos gols (gm_own/gm_opp, pra "totalComGm" e "quem
    marca primeiro") — nenhum deles usa HT nem odd histórica por jogo (essas
    duas só alimentam Valor do Gol/Ponto/Saldo e "metade com mais gols", que
    não fazem parte dos sinais). Cortar essas 2 buscas reduz o total de
    chamadas de rede em ~2/3 em relação a uma cópia fiel de
    _jogo_fetch_extra. Recebe [(row, side), ...] de VÁRIOS times/partidas de
    uma vez, num único _fs_event_pool.map (paralelismo máximo). Muta as rows
    in-place."""
    def _fetch(item):
        row, side = item
        eid = row["id"]
        if not eid:
            return row
        try:
            gm = _fs_goal_minutes(eid)
        except Exception:
            gm = None
        if gm:
            if side == "casa":
                row["gm_own"], row["gm_opp"] = gm.get("home", []), gm.get("away", [])
            else:
                row["gm_own"], row["gm_opp"] = gm.get("away", []), gm.get("home", [])
        return row

    return list(_fs_event_pool.map(_fetch, rows_with_side))


def _sinais_standings_pos(standings_rows, team_name):
    def _norm(s):
        return (s or "").strip().lower()
    tgt = _norm(team_name)
    for r in standings_rows:
        if _norm(r.get("team")) == tgt:
            try:
                return int(r["pos"]), len(standings_rows)
            except (TypeError, ValueError):
                return None, len(standings_rows)
    return None, len(standings_rows)


_SINAIS_DESTAQUE_KEYS = [
    ("vitFT_pct", "vitórias"), ("empFT_pct", "empates"), ("derFT_pct", "derrotas"),
    ("pctMarcar", "chance de marcar gol"), ("pctSofrer", "chance de sofrer gol"),
    ("pctAmbasMarcam", "chance de ambas equipes marcarem"),
    ("semSofrer_pct", "partidas sem sofrer gols"), ("semMarcar_pct", "partidas sem marcar gols"),
    ("over25_pct", "partidas com mais de 2,5 gols"), ("under25_pct", "partidas com menos de 2,5 gols"),
]


def _sinais_gerar_candidatas(m, home_stats, away_stats, home_rows, away_rows,
                              pos_home, pos_away, total_times, media_casa, media_fora, markets_today=None):
    out = []
    home, away = m.get("home", ""), m.get("away", "")
    markets_today = markets_today or {}

    # 1) Primeiro gol
    for team, stats, lado in ((home, home_stats, "casa"), (away, away_stats, "fora")):
        if stats and stats.get("totalComGm", 0) >= _SINAIS_MIN_N:
            pct = stats.get("timeMarcouPrimeiro_pct")
            if pct is not None and pct >= 60:
                out.append({
                    "signal": "primeiro_gol", "team": team, "lado": lado,
                    "headline": f"{team} costuma sair na frente jogando {'em casa' if lado == 'casa' else 'fora'}!",
                    "detail": f"Abriu o placar em {pct:.0f}% dos últimos jogos com dado de minutos de gol.",
                    "n": stats["totalComGm"], "pct": pct,
                })

    # 2) Mandante forte + posição boa
    if home_stats and home_stats["n"] >= _SINAIS_MIN_N and pos_home and total_times and total_times >= 6:
        if home_stats["vitFT_pct"] >= 60 and pos_home <= total_times / 2:
            out.append({
                "signal": "mandante_forte", "team": home, "lado": "casa",
                "headline": f"{home} manda bem em casa e ocupa a {pos_home}ª colocação!",
                "detail": f"Venceu {home_stats['vitFT_pct']:.0f}% dos últimos {home_stats['n']} jogos como mandante.",
                "n": home_stats["n"], "pct": home_stats["vitFT_pct"],
            })

    # 3) Visitante fraco + posição ruim
    if away_stats and away_stats["n"] >= _SINAIS_MIN_N and pos_away and total_times and total_times >= 6:
        if away_stats["vitFT_pct"] <= 25 and pos_away > total_times / 2:
            out.append({
                "signal": "visitante_fraco", "team": away, "lado": "fora",
                "headline": f"{away} é {pos_away}º colocado e rende pouco fora de casa!",
                "detail": f"Venceu só {away_stats['vitFT_pct']:.0f}% dos últimos {away_stats['n']} jogos como visitante.",
                "n": away_stats["n"], "pct": away_stats["vitFT_pct"],
            })

    # 4) Tendência de gols (Over/Under 2.5)
    for team, stats, lado in ((home, home_stats, "em casa"), (away, away_stats, "fora")):
        if stats and stats["n"] >= _SINAIS_MIN_N:
            ou = stats.get("ou25_total")
            if ou:
                lado_dom = "sobre" if ou["sobre"] >= ou["sob"] else "sob"
                pct_dom = max(ou["sobre"], ou["sob"])
                if pct_dom >= 70:
                    rotulo = "Mais de 2,5 gols" if lado_dom == "sobre" else "Menos de 2,5 gols"
                    out.append({
                        "signal": "tendencia_gols", "team": team, "lado": lado, "lado_dom": lado_dom,
                        "headline": f'Jogos de {team} {lado} pendem forte pra "{rotulo}"!',
                        "detail": f"{pct_dom:.0f}% dos últimos {stats['n']} jogos bateram esse padrão.",
                        "n": stats["n"], "pct": pct_dom,
                    })

    # 5) Padrão de valor — taxa histórica de vitória (1x2 própria) vs odd de hoje
    try:
        sels_today = _bt2_market_selections("1x2", markets_today.get("1x2"), 0, 0)
    except Exception:
        sels_today = []
    odd_casa = next((s["odd"] for s in sels_today if s["label"] == "Casa" and s["odd"]), None)
    odd_fora = next((s["odd"] for s in sels_today if s["label"] == "Fora" and s["odd"]), None)
    for team, stats, lado, odd in ((home, home_stats, "casa", odd_casa), (away, away_stats, "fora", odd_fora)):
        if stats and stats["n"] >= _SINAIS_MIN_N and odd:
            prob_implicita = 100 / odd
            taxa_hist = stats["vitFT_pct"]
            if taxa_hist > prob_implicita * 1.05:
                out.append({
                    "signal": "padrao_valor", "team": team, "lado": lado,
                    "headline": f"Padrão de valor encontrado: {team} pra vencer hoje!",
                    "detail": f"Venceu {taxa_hist:.0f}% dos últimos {stats['n']} jogos, mas a odd de hoje ({odd}) implica só {prob_implicita:.0f}%.",
                    "n": stats["n"], "pct": taxa_hist,
                })

    # 6) Placar raro (nunca ocorreu no histórico de nenhum dos dois times) —
    # sinal de Lay (aposta contra), só se a odd de hoje pra esse placar for curta
    AMOSTRA_MIN_RARO = 20

    def _bucket_freq(rows, side):
        counts = {}
        for r in rows:
            h, a = (r["own"], r["opp"]) if side == "casa" else (r["opp"], r["own"])
            key = _btcs_bucket_key(h, a)
            counts[key] = counts.get(key, 0) + 1
        return counts, len(rows)

    counts_home, n_home = _bucket_freq(home_rows, "casa")
    counts_away, n_away = _bucket_freq(away_rows, "fora")
    if n_home >= AMOSTRA_MIN_RARO and n_away >= AMOSTRA_MIN_RARO:
        nunca = [b for b in PLACAR_EXATO_BUCKETS_ORDER if counts_home.get(b, 0) == 0 and counts_away.get(b, 0) == 0]
        if nunca:
            try:
                bucket_odds = _btcs_bucket_odds((markets_today.get("placar_exato") or {}).get("items"))
            except Exception:
                bucket_odds = {}
            susp = [(b, bucket_odds[b]) for b in nunca if b in bucket_odds and bucket_odds[b] <= 10]
            if susp:
                b, odd = min(susp, key=lambda x: x[1])
                out.append({
                    "signal": "placar_raro", "team": None, "lado": None, "bucket": b,
                    "headline": f"Cuidado com o placar {b} no mercado de hoje!",
                    "detail": f"Esse placar nunca ocorreu em {min(n_home, n_away)}+ jogos de nenhum dos dois times, mas a odd oferecida hoje é de apenas {odd:.2f}.",
                    "n": min(n_home, n_away), "pct": 100,
                })

    # 7) Clean sheet cruzado
    if home_stats and away_stats and home_stats["n"] >= _SINAIS_MIN_N and away_stats["n"] >= _SINAIS_MIN_N:
        cs_home, sc_away = home_stats["semSofrer_pct"], away_stats["pctMarcar"]
        if cs_home >= 40 and sc_away <= 55:
            out.append({
                "signal": "clean_sheet", "team": home, "lado": "casa",
                "headline": f"{home} tem boas chances de sair sem sofrer gols em casa!",
                "detail": f"Não sofreu gols em {cs_home:.0f}% dos jogos em casa, enquanto {away} só marcou fora em {sc_away:.0f}% dos jogos.",
                "n": min(home_stats["n"], away_stats["n"]), "pct": (cs_home + (100 - sc_away)) / 2,
            })
        else:
            cs_away, sc_home = away_stats["semSofrer_pct"], home_stats["pctMarcar"]
            if cs_away >= 40 and sc_home <= 55:
                out.append({
                    "signal": "clean_sheet", "team": away, "lado": "fora",
                    "headline": f"{away} costuma segurar o resultado fora de casa!",
                    "detail": f"Não sofreu gols em {cs_away:.0f}% dos jogos fora, enquanto {home} só marcou em casa em {sc_home:.0f}% dos jogos.",
                    "n": min(home_stats["n"], away_stats["n"]), "pct": (cs_away + (100 - sc_home)) / 2,
                })

    # 8) Maior destaque acima da média (só estatísticas checáveis pelo placar final)
    melhor = None
    for team, stats, lado, media in (
        (home, home_stats, "jogando em casa", media_casa),
        (away, away_stats, "jogando fora", media_fora),
    ):
        if not stats or stats["n"] < _SINAIS_MIN_N or not media:
            continue
        for key, label in _SINAIS_DESTAQUE_KEYS:
            v, mv = stats.get(key), media.get(key)
            if v is None or mv is None or v < mv + 1:
                continue
            diff = v - mv
            if melhor is None or diff > melhor["diff"]:
                melhor = {"key": key, "label": label, "val": v, "media": mv, "diff": diff, "team": team, "lado": lado, "n": stats["n"]}
    if melhor:
        out.append({
            "signal": "maior_destaque", "team": melhor["team"], "lado": melhor["lado"], "key": melhor["key"],
            "headline": f'Maior destaque acima da média (aba Jogo): {melhor["team"]} em "{melhor["label"]}" {melhor["lado"]}!',
            "detail": f"{melhor['val']:.0f}% vs média de {melhor['media']:.0f}% entre os times de hoje ({melhor['n']} jogos analisados).",
            "n": melhor["n"], "pct": melhor["val"],
        })

    return out


def _sinais_check_acertou(item, m):
    """Confere acerto/erro contra o placar final (e minutos de gol de hoje,
    só quando precisar) — só chamado quando o jogo já encerrou."""
    try:
        hs, as_ = int(m.get("home_score")), int(m.get("away_score"))
    except (TypeError, ValueError):
        return None
    total = hs + as_
    sig = item["signal"]
    team_is_home = item.get("lado") == "casa"

    if sig == "primeiro_gol":
        try:
            gm = _fs_goal_minutes(m["id"]) or {}
        except Exception:
            return None
        home_min = min(gm.get("home") or [999])
        away_min = min(gm.get("away") or [999])
        if home_min == 999 and away_min == 999:
            return None
        scored_first_home = home_min <= away_min
        return scored_first_home if team_is_home else not scored_first_home
    if sig == "mandante_forte":
        return hs > as_
    if sig == "visitante_fraco":
        return hs >= as_
    if sig == "tendencia_gols":
        return (total > 2.5) if item.get("lado_dom") == "sobre" else (total <= 2.5)
    if sig == "padrao_valor":
        return (hs > as_) if team_is_home else (as_ > hs)
    if sig == "placar_raro":
        return _btcs_bucket_key(hs, as_) != item.get("bucket")
    if sig == "clean_sheet":
        return (as_ == 0) if team_is_home else (hs == 0)
    if sig == "maior_destaque":
        key = item.get("key")
        own, opp = (hs, as_) if team_is_home else (as_, hs)
        return {
            "vitFT_pct": own > opp, "empFT_pct": own == opp, "derFT_pct": own < opp,
            "pctMarcar": own >= 1, "pctSofrer": opp >= 1, "pctAmbasMarcam": own >= 1 and opp >= 1,
            "semSofrer_pct": opp == 0, "semMarcar_pct": own == 0,
            "over25_pct": total > 2.5, "under25_pct": total <= 2.5,
        }.get(key)
    return None


def _sinais_compute(permitir_calcular=True):
    """Wrapper com lock NÃO-BLOQUEANTE — nunca deixa uma requisição HTTP real
    esperando minutos pelo cálculo pesado. Isso já causou erro em produção:
    o proxy do Railway corta a conexão antes do Flask terminar, devolvendo
    "upstream error" (texto puro) em vez de JSON, e o navegador quebra
    tentando fazer JSON.parse nisso.

    Se o cache está quente: devolve na hora (rápido, é o caminho normal).
    Se está frio e ninguém mais calculando: essa chamada calcula (usado pela
    thread de aquecimento em segundo plano — ver _background_sinais_warmup).
    Se está frio e OUTRA thread já está calculando: não espera — devolve o
    cache antigo (se tiver, mesmo vencido) ou um sinalizador "calculando",
    pro frontend mostrar uma mensagem e tentar de novo em instantes."""
    now = time.time()
    if _SINAIS_CACHE["data"] and (now - _SINAIS_CACHE["ts"]) < _SINAIS_TTL:
        return _SINAIS_CACHE["data"]

    got_lock = _SINAIS_LOCK.acquire(blocking=False)
    if not got_lock:
        if _SINAIS_CACHE["data"]:
            return _SINAIS_CACHE["data"]  # vencido, mas melhor que nada / que travar
        return {"calculando": True, "sugestoes": [], "por_sinal": {}, "labels": SINAIS_LABELS}
    try:
        # reconfere depois de pegar o lock — outra thread pode ter acabado de
        # calcular enquanto esperávamos pra pegar o lock
        now = time.time()
        if _SINAIS_CACHE["data"] and (now - _SINAIS_CACHE["ts"]) < _SINAIS_TTL:
            return _SINAIS_CACHE["data"]
        if not permitir_calcular:
            return _SINAIS_CACHE["data"] or {"calculando": True, "sugestoes": [], "por_sinal": {}, "labels": SINAIS_LABELS}
        return _sinais_compute_locked()
    finally:
        _SINAIS_LOCK.release()


def _sinais_compute_locked():
    now = time.time()
    today_str = datetime.now().strftime("%Y-%m-%d")
    if _SINAIS_DAY_CACHE["date"] != today_str:
        restored = _sinais_load_from_disk(today_str)
        if restored:
            _SINAIS_DAY_CACHE["date"] = today_str
            _SINAIS_DAY_CACHE["sugestoes"] = restored.get("sugestoes") or {}
            _SINAIS_DAY_CACHE["manual_reset_date"] = restored.get("manual_reset_date")
        else:
            _SINAIS_DAY_CACHE["date"] = today_str
            _SINAIS_DAY_CACHE["sugestoes"] = {}
            _SINAIS_DAY_CACHE["manual_reset_date"] = None

    sugestoes = _SINAIS_DAY_CACHE["sugestoes"]

    scheduled = sorted((mm for mm in _fs_all_matches() if mm.get("status") == "1"), key=lambda mm: mm.get("kickoff_ts") or "")
    finished_today = sorted((mm for mm in _fs_all_matches() if mm.get("status") == "3"), key=lambda mm: mm.get("kickoff_ts") or "")
    candidatos = scheduled[:_SINAIS_MAX_CANDIDATOS] + finished_today[:_SINAIS_MAX_CANDIDATOS]

    media = _jogo_medias_gerais_compute()
    media_casa, media_fora = media.get("casa") or {}, media.get("fora") or {}

    # 1) H2H de TODOS os candidatos em paralelo (não um de cada vez)
    def _fetch_h2h(m):
        try:
            return m["id"], _fs_h2h(m["id"])
        except Exception:
            return m["id"], None
    h2h_by_id = dict(_fs_event_pool.map(_fetch_h2h, candidatos))

    # 2) monta as rows brutas (casa/fora) de cada partida, ainda sem HT/minutos/odds
    ctx = []  # [(m, home_rows, away_rows), ...]
    enrich_batch = []  # [(row, side), ...] de TODOS os times de TODOS os candidatos juntos
    for m in candidatos:
        tabs = h2h_by_id.get(m["id"])
        if not tabs:
            continue
        home_rows = _laycasa_home_rows(tabs)
        away_rows = _laycasa_away_rows(tabs)
        ctx.append((m, home_rows, away_rows))
        enrich_batch.extend((r, "casa") for r in home_rows)
        enrich_batch.extend((r, "fora") for r in away_rows)

    # 3) enriquece TODAS as rows de TODOS os candidatos numa ÚNICA passada em
    # paralelo (goal_minutes+HT+odd 1x2 de cada jogo histórico) — antes disso
    # era uma passada por TIME, uma de cada vez, o que multiplicava o tempo
    # total por ~2×nº de candidatos à toa (mesmo bug que já corrigimos antes
    # em outras partes do app, reintroduzido aqui na 1ª versão).
    _sinais_fetch_extra_batch(enrich_batch)

    # 4) classificação e odds de hoje (1x2 + placar exato) de cada candidato,
    # também em paralelo
    def _fetch_standings(m):
        try:
            return m["id"], _fs_standings(m["id"], m.get("home", ""), m.get("away", ""))
        except Exception:
            return m["id"], []
    standings_by_id = dict(_fs_event_pool.map(_fetch_standings, [c[0] for c in ctx]))

    def _fetch_markets_today(m):
        try:
            _, markets = _fs_odds_all_markets_any_bookmaker(m["id"], markets_wanted=["1x2", "placar_exato"])
            return m["id"], markets or {}
        except Exception:
            return m["id"], {}
    markets_by_id = dict(_fs_event_pool.map(_fetch_markets_today, [c[0] for c in ctx]))

    # 5) agora que tudo já foi buscado, só calcula (CPU local, sem rede) e gera sinais
    for m, home_rows, away_rows in ctx:
        home_stats = _jogo_stats_from_rows(home_rows)
        if home_stats:
            gp = _jogo_goal_pattern_from_rows(home_rows)
            if gp:
                home_stats.update(gp)
        away_stats = _jogo_stats_from_rows(away_rows)
        if away_stats:
            gp = _jogo_goal_pattern_from_rows(away_rows)
            if gp:
                away_stats.update(gp)

        standings = standings_by_id.get(m["id"], [])
        pos_home, total_times = _sinais_standings_pos(standings, m.get("home", ""))
        pos_away, _t2 = _sinais_standings_pos(standings, m.get("away", ""))

        candidatas = _sinais_gerar_candidatas(m, home_stats, away_stats, home_rows, away_rows,
                                               pos_home, pos_away, total_times, media_casa, media_fora,
                                               markets_by_id.get(m["id"]))
        for c in candidatas:
            uid = f"{c['signal']}__{m['id']}"
            if uid in sugestoes:
                continue
            sugestoes[uid] = {
                **c, "id": uid, "match_id": m["id"], "home": m.get("home"), "away": m.get("away"),
                "liga": m.get("liga", ""), "pais": m.get("pais", ""), "kickoff_ts": m.get("kickoff_ts"),
                "status": m.get("status"), "acertou": None,
                "confianca": _sinais_confianca(c.get("n") or 0, c.get("pct") or 0),
            }

    # Atualiza status/acertou de tudo que já está na lista (mesmo que o jogo
    # tenha saído da janela de candidatos acima)
    matches_by_id = {mm["id"]: mm for mm in _fs_all_matches()}
    for item in sugestoes.values():
        mm = matches_by_id.get(item["match_id"])
        if not mm:
            continue
        item["status"] = mm.get("status")
        if mm.get("status") == "3" and item.get("acertou") is None:
            item["home_score"] = mm.get("home_score")
            item["away_score"] = mm.get("away_score")
            item["acertou"] = _sinais_check_acertou(item, mm)

    _sinais_save_to_disk(_SINAIS_DAY_CACHE)

    por_sinal = {}
    for item in sugestoes.values():
        agg = por_sinal.setdefault(item["signal"], {"total": 0, "acertos": 0, "encerrados": 0})
        agg["total"] += 1
        if item.get("acertou") is not None:
            agg["encerrados"] += 1
            if item["acertou"]:
                agg["acertos"] += 1
    for agg in por_sinal.values():
        agg["taxa_acerto"] = round(agg["acertos"] / agg["encerrados"] * 100, 1) if agg["encerrados"] else None

    data = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "date": today_str,
        "sugestoes": sorted(sugestoes.values(), key=lambda x: x.get("kickoff_ts") or ""),
        "por_sinal": por_sinal,
        "labels": SINAIS_LABELS,
    }
    _SINAIS_CACHE["ts"] = now
    _SINAIS_CACHE["data"] = data
    return data


@app.route("/api/sinais_dia")
def api_sinais_dia():
    """'Sinais do Dia' — substitui o Mapa de Sugestões antigo. 8 sinais
    narrativos (os mesmos da Leitura da Partida) rodados em todos os jogos de
    hoje; taxa de acerto contabilizada só do dia (zera quando vira o dia).

    permitir_calcular=False de propósito: essa rota é chamada direto pelo
    navegador, então NUNCA dispara o cálculo pesado ela mesma (senão o
    proxy do Railway corta a conexão antes de terminar, quebrando o
    JSON.parse no frontend). Quem calcula de verdade é a thread de
    aquecimento em segundo plano (_background_sinais_warmup) — essa rota só
    lê o que já estiver pronto, ou devolve "calculando" se ainda não tiver
    nada."""
    return jsonify(_sinais_compute(permitir_calcular=False))


@app.route("/api/sinais_dia/atualizar_dia", methods=["POST"])
def api_sinais_dia_atualizar_dia():
    """Reconstrói os sinais do dia do ZERO. Só permite 1x por dia (mesma
    trava do Mapa antigo), pra não ficar mudando a lista toda hora."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    if _SINAIS_DAY_CACHE.get("date") == today_str and _SINAIS_DAY_CACHE.get("manual_reset_date") == today_str:
        return jsonify({"error": "os sinais de hoje já foram atualizados manualmente — só é permitido 1x por dia"}), 429
    _SINAIS_DAY_CACHE["date"] = today_str
    _SINAIS_DAY_CACHE["sugestoes"] = {}
    _SINAIS_DAY_CACHE["manual_reset_date"] = today_str
    _SINAIS_CACHE["ts"] = 0
    _SINAIS_CACHE["data"] = None
    path = _sinais_cache_path()
    if os.path.exists(path):
        os.remove(path)
    return jsonify(_sinais_compute())


# Mantém o cache dos Sinais do Dia sempre quente, computando em segundo
# plano periodicamente — é a ÚNICA coisa que dispara o cálculo pesado de
# verdade (a rota /api/sinais_dia nunca calcula, só lê o que essa thread já
# deixou pronto). Sem isso, a 1ª requisição do dia (ou depois do cache de
# 30min vencer) computava DENTRO do ciclo de request/response, e como pode
# levar vários minutos, o proxy do Railway cortava a conexão antes do Flask
# terminar — o navegador recebia "upstream error" (texto puro) em vez de
# JSON e quebrava. Roda a cada 25min (um pouco antes do TTL de 30min vencer,
# pra sempre ter algo pronto pouco depois de expirar).
def _background_sinais_warmup():
    while True:
        try:
            print("[sinais-warmup] Recalculando Sinais do Dia em segundo plano...")
            _sinais_compute()
            print("[sinais-warmup] Concluído.")
        except Exception as e:
            print(f"[sinais-warmup] Erro: {e}")
        time.sleep(25 * 60)


threading.Thread(target=_background_sinais_warmup, daemon=True, name="SinaisWarmup").start()


@app.route("/api/backtestcs/ranking")
def api_backtestcs_ranking():
    """Ranking global dos 19 buckets de placar exato por taxa de acerto,
    agregando o histórico de TODOS os jogos de hoje. Bem mais barato que o
    Backtest 2 (não busca odds), mas ainda cacheia 30min pra evitar recalcular
    o H2H de todo mundo a cada request — use ?refresh=1 pra forçar."""
    mode = request.args.get("mode") or "recente"
    if mode == "acumulado":
        return jsonify(_btcs_compute_ranking_acumulado())
    force = request.args.get("refresh") == "1"
    if not force and _btcs_cache["data"] is not None and (time.time() - _btcs_cache["ts"]) < _BTCS_CACHE_TTL:
        return jsonify(_btcs_cache["data"])
    data = _btcs_compute_ranking()
    _btcs_cache["ts"] = time.time()
    _btcs_cache["data"] = data
    return jsonify(data)


@app.route("/api/backtestcs/ranking_acumulado")
def api_backtestcs_ranking_acumulado():
    """Ranking ACUMULADO: agrega todos os resultados já persistidos em
    btcs_results (cresce a cada vez que /api/backtestcs/ranking roda)."""
    return jsonify(_btcs_compute_ranking_acumulado())


@app.route("/api/backtestcs/patterns")
def api_backtestcs_patterns():
    """Padrões: quais combinações (variável do time, segmento, bucket de placar
    exato) têm taxa de acerto significativamente melhor/pior que a geral.
    ?mode=acumulado usa TODO o histórico já persistido (sem cache, leitura
    SQLite é barata). Modo padrão ('últimos 30') cacheia 30min — ?refresh=1
    força recálculo."""
    mode = request.args.get("mode") or "recente"
    if mode == "acumulado":
        return jsonify(_btcs_compute_patterns_acumulado())
    force = request.args.get("refresh") == "1"
    if not force and _btcs_patterns_cache["data"] is not None and (time.time() - _btcs_patterns_cache["ts"]) < _BTCS_CACHE_TTL:
        return jsonify(_btcs_patterns_cache["data"])
    data = _btcs_compute_patterns()
    _btcs_patterns_cache["ts"] = time.time()
    _btcs_patterns_cache["data"] = data
    return jsonify(data)


def _btcs_bucket_odds(items):
    """Porta de _placarExatoBuckets (JS) pra Python: agrupa os placares crus
    do mercado 'placar_exato' nos 19 buckets fixos, odd combinada = 1/soma das
    probabilidades implícitas dos placares que caem em cada bucket."""
    prob_sum = {}
    for it in items or []:
        m = re.match(r"^(\d+):(\d+)$", it.get("score") or "")
        if not m:
            continue
        try:
            odd = float((it.get("item") or {}).get("value"))
        except (TypeError, ValueError):
            continue
        if not odd:
            continue
        key = _btcs_bucket_key(int(m.group(1)), int(m.group(2)))
        prob_sum[key] = prob_sum.get(key, 0.0) + 1 / odd
    return {key: 1 / prob_sum[key] for key in prob_sum}


_BTCS_BUCKET_ODDS_CACHE_TTL = 300  # 5min — odds mudam pouco em poucos minutos
_btcs_bucket_odds_cache = {"ts": 0, "data": None}


@app.route("/api/backtestcs/bucket_odds_today")
def api_backtestcs_bucket_odds_today():
    """Odd média ATUAL de cada um dos 19 buckets, calculada em cima dos jogos
    de HOJE que ainda não começaram (média entre os jogos que oferecem odd
    pra aquele bucket). Busca as odds de cada jogo de hoje UMA VEZ SÓ (não
    por bucket) e classifica em paralelo — bem mais barato que chamar
    matches_for_bucket bucket por bucket. Cacheado 5min."""
    force = request.args.get("refresh") == "1"
    if not force and _btcs_bucket_odds_cache["data"] is not None and \
            (time.time() - _btcs_bucket_odds_cache["ts"]) < _BTCS_BUCKET_ODDS_CACHE_TTL:
        return jsonify(_btcs_bucket_odds_cache["data"])

    odds_sum = {}
    odds_n = {}
    for m, markets in _today2_odds_snapshot(force=force):
        odds_by_bucket = _btcs_bucket_odds((markets.get("placar_exato") or {}).get("items"))
        for bucket, odd in odds_by_bucket.items():
            odds_sum[bucket] = odds_sum.get(bucket, 0.0) + odd
            odds_n[bucket] = odds_n.get(bucket, 0) + 1

    avg_odds = {b: round(odds_sum[b] / odds_n[b], 2) for b in odds_sum}
    data = {"avg_odds": avg_odds}
    _btcs_bucket_odds_cache["ts"] = time.time()
    _btcs_bucket_odds_cache["data"] = data
    return jsonify(data)


@app.route("/api/backtestcs/matches_for_bucket")
def api_backtestcs_matches_for_bucket():
    """Jogos de HOJE que ainda não começaram onde o bucket de placar exato
    selecionado tem odd disponível no mercado 'placar_exato' atual."""
    bucket = request.args.get("bucket", "")
    if not bucket:
        return jsonify({"matches": []}), 400

    resultado = []
    for m, markets in _today2_odds_snapshot():
        odds_by_bucket = _btcs_bucket_odds((markets.get("placar_exato") or {}).get("items"))
        odd = odds_by_bucket.get(bucket)
        if odd:
            resultado.append({
                "id": m["id"], "home": m["home"], "away": m["away"],
                "liga": m.get("liga", ""), "pais": m.get("pais", ""),
                "kickoff_ts": m.get("kickoff_ts"), "odd": odd,
            })
    resultado.sort(key=lambda x: x.get("kickoff_ts") or "")
    return jsonify({"matches": resultado})


# ── Classificação de jogos de hoje pelas odds atuais (Resultado Final / Gols /
# Ambas Marcam) — usado só pelo painel "Filtrar por Parâmetros" da aba experimental
# 'Hoje 2'. Cacheado 5min (mesmo padrão do _btcs_bucket_odds_cache) pra não
# reconsultar odds a cada clique de checkbox do usuário. ──
_TODAY2_CLASSIFICATION_CACHE_TTL = 300  # 5min — odds mudam pouco em poucos minutos
_today2_classification_cache = {"ts": 0, "data": None}


def _today2_classify_match(markets):
    """Classifica um jogo pelas odds atuais em Resultado Final / Gols / Ambas Marcam.
    Limiares de favoritismo (odd baixa = mais provável): <1.35 super favorito,
    1.35–1.80 favorito, ambos os lados entre 1.80–2.60 sem favorito claro = parelho.
    Fora dessas faixas (ex: um lado <1.80 e outro >2.60) não classifica resultado
    (fica None) pra não forçar rótulo em jogo sem padrão claro."""
    out = {"resultado": None, "favorito_lado": None, "gols": None, "ambas": None,
           "odd_casa": None, "odd_fora": None}

    m1x2 = markets.get("1x2") or {}
    odd_casa = (m1x2.get("home") or {}).get("value")
    odd_fora = (m1x2.get("away") or {}).get("value")
    try:
        odd_casa = float(odd_casa) if odd_casa is not None else None
    except (TypeError, ValueError):
        odd_casa = None
    try:
        odd_fora = float(odd_fora) if odd_fora is not None else None
    except (TypeError, ValueError):
        odd_fora = None
    out["odd_casa"] = odd_casa
    out["odd_fora"] = odd_fora

    if odd_casa and odd_fora:
        menor = min(odd_casa, odd_fora)
        lado = "casa" if odd_casa <= odd_fora else "fora"
        if menor < 1.35:
            out["resultado"] = "super_favorito"
            out["favorito_lado"] = lado
        elif menor <= 1.80:
            out["resultado"] = "favorito"
            out["favorito_lado"] = lado
        elif 1.80 <= odd_casa <= 2.60 and 1.80 <= odd_fora <= 2.60:
            out["resultado"] = "parelho"

    over_under = markets.get("over_under") or {}
    melhor_op = None
    melhor_dist = None
    for op in over_under.get("opportunities") or []:
        try:
            line = float((op.get("handicap") or {}).get("value"))
        except (TypeError, ValueError):
            continue
        dist = abs(line - 2.5)
        if melhor_dist is None or dist < melhor_dist:
            melhor_dist = dist
            melhor_op = op
    if melhor_op:
        try:
            odd_over = float((melhor_op.get("over") or {}).get("value"))
        except (TypeError, ValueError):
            odd_over = None
        try:
            odd_under = float((melhor_op.get("under") or {}).get("value"))
        except (TypeError, ValueError):
            odd_under = None
        if odd_over and odd_under:
            out["gols"] = "over" if odd_over <= odd_under else "under"

    ambos = markets.get("ambos_marcam") or {}
    try:
        odd_sim = float((ambos.get("yes") or {}).get("value"))
    except (TypeError, ValueError):
        odd_sim = None
    try:
        odd_nao = float((ambos.get("no") or {}).get("value"))
    except (TypeError, ValueError):
        odd_nao = None
    if odd_sim and odd_nao:
        out["ambas"] = "sim" if odd_sim <= odd_nao else "nao"

    return out


@app.route("/api/today2/match_classification")
def api_today2_match_classification():
    """Classifica os jogos de HOJE (ainda não iniciados, limitados aos próximos
    _BT2_MATCHES_MAX_CANDIDATOS) pelas odds atuais — Resultado Final, Gols e Ambas
    Marcam — pra alimentar o painel 'Filtrar por Parâmetros' da aba 'Hoje 2'.
    Reaproveita os pools isolados do Backtest 2 (_bt2_matches_pool /
    _bt2_matches_market_pool), não os pools pesados de ranking, pro filtro
    continuar rápido mesmo com um recálculo de ranking rodando."""
    force = request.args.get("refresh") == "1"
    if not force and _today2_classification_cache["data"] is not None and \
            (time.time() - _today2_classification_cache["ts"]) < _TODAY2_CLASSIFICATION_CACHE_TTL:
        return jsonify(_today2_classification_cache["data"])

    classifications = {}
    for m, markets in _today2_odds_snapshot(force=force):
        if not markets:
            continue
        c = _today2_classify_match(markets)
        if c["resultado"] or c["gols"] or c["ambas"]:
            classifications[str(m["id"])] = c

    data = {"classifications": classifications}
    _today2_classification_cache["ts"] = time.time()
    _today2_classification_cache["data"] = data
    return jsonify(data)


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


@app.route("/api/test-sofa/<event_id>")
def api_test_sofa(event_id):
    """Endpoint de diagnóstico — testa conexão com SofaScore."""
    import sys
    result = {"event_id": event_id, "method": None, "error": None, "data_preview": None}
    try:
        graph, incidents, statistics = _fetch_sofa_direct(event_id)
        pts = graph.get("graphPoints", [])
        result["method"]       = "requests_direto"
        result["graph_points"] = len(pts)
        result["incidents_ok"] = bool(incidents.get("incidents"))
        result["stats_ok"]     = bool(statistics.get("statistics"))
        print(f"[test-sofa] OK via requests: {event_id}, {len(pts)} pts", file=sys.stderr)
    except Exception as e:
        result["error_requests"] = str(e)
        print(f"[test-sofa] requests falhou: {e}", file=sys.stderr)
        try:
            graph, incidents, statistics = _fetch_sofa_playwright(event_id)
            pts = graph.get("graphPoints", [])
            result["method"]       = "playwright"
            result["graph_points"] = len(pts)
            print(f"[test-sofa] OK via playwright: {event_id}", file=sys.stderr)
        except Exception as e2:
            result["error_playwright"] = str(e2)
            print(f"[test-sofa] playwright falhou: {e2}", file=sys.stderr)
    return jsonify(result)


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
    elif re.match(r'^\d{4}-\d{2}-\d{2}_[\w]+\.json$', fname):
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

    # Invalida caches de análise sempre que chegar arquivo de histórico ou predictions
    if dest_dir in (MOMENTUM_DIR, BACKTEST_DIR) or fname.startswith("predictions"):
        global _pattern_tips_cache, _odds_patterns_cache, _stats_patterns_cache
        _pattern_tips_cache  = {"ts": 0, "data": None}
        _odds_patterns_cache = {"ts": 0, "data": None}
        _stats_patterns_cache = {"ts": 0, "data": None}
        # Reconstrói em background para a próxima requisição já ter os dados prontos
        threading.Thread(target=_rebuild_analysis_cache, daemon=True).start()

    return jsonify({"ok": True, "saved": fname, "path": local_path})


@app.route("/api/upload-backup-bulk", methods=["POST"])
def api_upload_backup_bulk():
    """Recebe múltiplos arquivos de momentum_history em uma só requisição.
    Mais eficiente que chamar /api/upload-backup N vezes.
    Requer ?token=UPLOAD_TOKEN.
    """
    token    = request.args.get("token", "")
    expected = os.environ.get("UPLOAD_TOKEN", "")
    if not expected or token != expected:
        return jsonify({"ok": False, "error": "Token inválido"}), 403

    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "error": "Nenhum arquivo enviado"}), 400

    saved, skipped, errors = [], [], []
    for f in files:
        fname = f.filename or ""
        if re.match(r'^\d{4}-\d{2}-\d{2}_[\w]+\.json$', fname):
            dest_dir      = MOMENTUM_DIR
            remote_prefix = "momentum_history"
        elif re.match(r'^\d{4}-\d{2}-\d{2}\.json$', fname):
            dest_dir      = BACKTEST_DIR
            remote_prefix = "backtest"
        else:
            skipped.append(fname)
            continue

        os.makedirs(dest_dir, exist_ok=True)
        local_path = os.path.join(dest_dir, fname)
        try:
            f.save(local_path)
            github_storage.push_file_bg(local_path, f"{remote_prefix}/{fname}")
            saved.append(fname)
        except Exception as e:
            errors.append({"file": fname, "error": str(e)})

    if saved:
        global _pattern_tips_cache, _odds_patterns_cache, _stats_patterns_cache
        _pattern_tips_cache  = {"ts": 0, "data": None}
        _odds_patterns_cache = {"ts": 0, "data": None}
        _stats_patterns_cache = {"ts": 0, "data": None}
        threading.Thread(target=_rebuild_analysis_cache, daemon=True).start()

    return jsonify({
        "ok":      True,
        "saved":   len(saved),
        "skipped": len(skipped),
        "errors":  errors,
        "files":   saved,
    })


TG_CONFIG_FILE = os.path.join(DATA_DIR, "telegram_config.json")
TG_DAILY_FILE  = os.path.join(DATA_DIR, "telegram_daily.json")

def _tg_load_config():
    try:
        with open(TG_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _tg_send_message(token, chat_id, message):
    r = http_req.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
        timeout=10,
    )
    return r.json()

def _tg_auto_send():
    """Envia as entradas do dia automaticamente via Telegram."""
    cfg = _tg_load_config()
    if not cfg.get("token") or not cfg.get("chat_id"):
        return
    try:
        with open(TG_DAILY_FILE, "r", encoding="utf-8") as f:
            daily = json.load(f)
    except Exception:
        daily = []
    if not daily:
        return
    hoje = datetime.now().strftime("%d/%m/%Y")
    msg  = f"🤖 <b>Gol em Números — {hoje}</b>\n"
    msg += f"📋 <b>{len(daily)} partida{'s' if len(daily)!=1 else ''}</b> nas metodologias configuradas\n\n"
    for item in daily[:25]:
        hora = f" · {item['hora']}" if item.get("hora") else ""
        msg += f"⚽ <b>{item['casa']} × {item['fora']}</b>{hora}\n"
        msg += f"🏆 {item.get('liga','')}\n"
        for fit in item.get("fits", [])[:3]:
            msg += f"  • {fit}\n"
        msg += "\n"
    if len(daily) > 25:
        msg += f"...e mais {len(daily)-25} partidas.\n"
    try:
        _tg_send_message(cfg["token"], cfg["chat_id"], msg)
        print(f"[Telegram] Envio automático: {len(daily)} partidas enviadas às {datetime.now().strftime('%H:%M')}")
    except Exception as e:
        print(f"[Telegram] Erro no envio automático: {e}")

def _tg_scheduler():
    """Background thread: envio diário no horário configurado."""
    sent_today = None
    while True:
        try:
            cfg = _tg_load_config()
            now       = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            # Envio diário no horário configurado
            if cfg.get("auto_send") and cfg.get("send_time"):
                hm = now.strftime("%H:%M")
                if hm == cfg["send_time"] and sent_today != today_str:
                    sent_today = today_str
                    _tg_auto_send()

        except Exception as e:
            print(f"[Telegram scheduler] {e}")
        time.sleep(30)

# Inicia background thread do scheduler
threading.Thread(target=_tg_scheduler, daemon=True).start()

@app.route("/api/telegram/send", methods=["POST"])
def api_telegram_send():
    """Envia mensagem via Telegram Bot API."""
    data    = request.json or {}
    token   = (data.get("token") or "").strip()
    chat_id = (data.get("chat_id") or "").strip()
    message = (data.get("message") or "").strip()
    if not token or not chat_id or not message:
        return jsonify({"ok": False, "error": "Campos obrigatórios: token, chat_id, message"}), 400
    try:
        return jsonify(_tg_send_message(token, chat_id, message))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/telegram/config", methods=["GET", "POST"])
def api_telegram_config():
    """Salva ou carrega configuração do Telegram."""
    if request.method == "POST":
        data = request.json or {}
        try:
            with open(TG_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    else:
        return jsonify(_tg_load_config())

@app.route("/api/telegram/daily", methods=["POST"])
def api_telegram_daily():
    """Salva a lista pré-computada de partidas do dia para envio automático."""
    data = request.json or {}
    matches = data.get("matches", [])
    try:
        with open(TG_DAILY_FILE, "w", encoding="utf-8") as f:
            json.dump(matches, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True, "saved": len(matches)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/telegram/send-now", methods=["POST"])
def api_telegram_send_now():
    """Dispara o envio automático imediatamente."""
    _tg_auto_send()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Servidor rodando em http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port, use_reloader=False, threaded=True)

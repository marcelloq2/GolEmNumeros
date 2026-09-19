"""
Servidor Flask — API + frontend para exibir dados do StatArea
"""
from flask import Flask, jsonify, send_from_directory, abort, request, session, redirect, make_response
import json, os, glob, re, threading, time, sqlite3, itertools, math, traceback, queue, sys
from functools import lru_cache
from collections import deque
import requests as http_req
from datetime import datetime, timedelta, date
from bs4 import BeautifulSoup
import github_storage

# O console do Windows usa cp1252 por padrão, que não cobre nomes de time com
# caracteres como ş/ğ/č/đ (comuns em ligas turcas, balcânicas etc) — qualquer
# print(f"...{nome_do_time}...") com um desses derrubava a requisição inteira
# com UnicodeEncodeError (achado testando o Scanner: _find_uniscore_id e
# _process_momentum já tinham prints assim). Forçar UTF-8 aqui corrige de vez
# pra qualquer print futuro também, em vez de remendar um por um. Sem efeito
# em produção (Railway/Linux já usa UTF-8 por padrão).
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Sentry — captura erro do backend automaticamente (exceção não tratada em
# qualquer rota vira um evento no Sentry, com traceback completo), sem
# depender de print() que pode se perder no buffer do log do Railway. Só liga
# se SENTRY_DSN estiver configurado (variável de ambiente no Railway) — sem
# isso, roda normal, sem captura nenhuma (não trava nada em dev local).
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.1,  # amostra 10% das requisições pra tracing de performance (fica dentro do free tier)
        environment=os.environ.get("RAILWAY_ENVIRONMENT_NAME", "production"),
    )

FOTMOB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.fotmob.com/",
    "Accept":  "*/*",
}

app = Flask(__name__, static_folder="static", static_url_path="/static")
# Sem SECRET_KEY configurada, gera uma aleatória a cada boot — sessão (login)
# expira em todo redeploy, mas nunca guarda um segredo fixo no código. Pra
# não deslogar todo mundo a cada deploy, configure SECRET_KEY fixa no
# Railway (qualquer string longa aleatória serve).
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)
DATA_DIR     = os.path.dirname(__file__)
MOMENTUM_DIR = os.path.join(DATA_DIR, "momentum_history")
SHOTMAP_DIR  = os.path.join(DATA_DIR, "shotmap_history")
MAPA_CACHE_DIR = os.path.join(DATA_DIR, "mapa_cache")
FORCA_HISTORY_DIR = os.path.join(DATA_DIR, "forca_history")
os.makedirs(MOMENTUM_DIR, exist_ok=True)
os.makedirs(SHOTMAP_DIR,  exist_ok=True)
os.makedirs(MAPA_CACHE_DIR, exist_ok=True)
os.makedirs(FORCA_HISTORY_DIR, exist_ok=True)

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


def load_predictions():
    # Prefere o arquivo full (com detalhes), senão usa o simples
    for fname in ["predictions_full.json", "predictions.json"]:
        path = os.path.join(DATA_DIR, fname)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f), fname
    return [], None


# ── CONFRONTO DIRETO (H2H) + SCORE DE CONVICÇÃO ──────────────────────────
# Reaproveitam só dados já coletados (m["detalhes"]), sem nenhuma chamada
# externa nova. Cálculo é uma soma ponderada simples sobre o objeto de cada
# partida já carregado em memória — sem impacto de performance perceptível.

CONVICCAO_PESOS = {
    "ofensivo": 0.25,
    "defensivo_adversario": 0.25,
    "forma_recente": 0.20,
    "casa_fora": 0.15,
    "h2h": 0.10,
    "contexto": 0.05,  # sem indicador de contexto implementado ainda — o peso é
                        # redistribuído proporcionalmente entre os presentes (ver abaixo)
}
CONVICCAO_PENALIDADE_DIVERGENCIA = 0.15  # TODO: recalibrar depois de acumular histórico real


def _convicao_pesos_efetivos(indicadores_presentes):
    """Remove 'contexto' (não implementado) e qualquer indicador ausente nessa
    partida, redistribuindo os pesos que sobrarem proporcionalmente entre os
    indicadores realmente presentes, mantendo a soma em 1.0."""
    pesos = {k: v for k, v in CONVICCAO_PESOS.items() if k != "contexto" and k in indicadores_presentes}
    soma = sum(pesos.values())
    if soma <= 0:
        return pesos
    falta = 1.0 - soma
    for k in pesos:
        pesos[k] += falta * (pesos[k] / soma)
    return pesos


def _h2h_norm_nome(s):
    import unicodedata
    s = (s or "").lower().strip()
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _h2h_confronto_direto(confrontos, home, fora):
    """Estatísticas do confronto direto entre os dois times de hoje, calculadas
    em cima de detalhes.confrontos_diretos (já coletado pelo scraper). Marca
    amostra insuficiente com menos de 3 jogos, conforme especificado."""
    confrontos = confrontos or []
    if len(confrontos) < 3:
        return {"amostra_suficiente": False, "total": len(confrontos)}

    home_n = _h2h_norm_nome(home).split(" ")[0] if _h2h_norm_nome(home) else ""
    vit_casa = vit_fora = empates = gols_totais = over25 = 0
    mm_vit_casa = mm_vit_fora = mm_empates = mm_total = 0

    for p in confrontos:
        try: gc = int(p.get("gols_casa") or 0)
        except (TypeError, ValueError): gc = 0
        try: gf = int(p.get("gols_fora") or 0)
        except (TypeError, ValueError): gf = 0
        p_casa_norm = _h2h_norm_nome(p.get("casa"))
        p_casa_eh_home = bool(home_n) and (home_n in p_casa_norm)
        gols_home = gc if p_casa_eh_home else gf
        gols_fora_time = gf if p_casa_eh_home else gc
        if gols_home > gols_fora_time: vit_casa += 1
        elif gols_home < gols_fora_time: vit_fora += 1
        else: empates += 1
        gols_totais += gc + gf
        if gc + gf > 2.5: over25 += 1
        if p_casa_eh_home:
            mm_total += 1
            if gols_home > gols_fora_time: mm_vit_casa += 1
            elif gols_home < gols_fora_time: mm_vit_fora += 1
            else: mm_empates += 1

    n = len(confrontos)
    resultado = {
        "amostra_suficiente": True,
        "total": n,
        "vitorias_casa": vit_casa,
        "vitorias_fora": vit_fora,
        "empates": empates,
        "media_gols_totais": round(gols_totais / n, 2),
        "pct_over_25": round(over25 / n * 100),
        "quem_abre_placar": None,  # dado não disponível no histórico coletado hoje
    }
    resultado["mesmo_mando"] = (
        {"total": mm_total, "vitorias_casa": mm_vit_casa, "vitorias_fora": mm_vit_fora, "empates": mm_empates}
        if mm_total >= 2 else None
    )
    return resultado


def _convicao_indicadores(m, is_home):
    """Indicadores normalizados 0-100, cada um alinhado a favor do time avaliado
    (is_home decide se 'vota' pelo mandante ou visitante) — mesma escala de
    pontos 0-100 já usada no Score do Time (frontend), não uma normalização nova."""
    d = m.get("detalhes") or {}
    team = m.get("casa") if is_home else m.get("fora")
    ind = {}

    last10 = d.get("ultimas_10_partidas") or {}
    team_data = last10.get(team)
    if team_data is None and len(last10) >= 2:
        keys = list(last10.keys())
        team_data = last10.get(keys[0] if is_home else keys[1])
    form = (team_data or {}).get("form") or ""
    if form:
        wins, draws = form.count("W"), form.count("D")
        ind["forma_recente"] = round((wins * 3 + draws) / (len(form) * 3) * 100)

    stats = d.get("estatisticas") or {}
    ts = stats.get(team)
    if ts is None and len(stats) >= 2:
        keys = list(stats.keys())
        ts = stats.get(keys[0] if is_home else keys[1])
    ts = ts or {}
    try: avg_scored = float(ts.get("Average scored goals per match") or 0)
    except (TypeError, ValueError): avg_scored = 0
    try: avg_conc = float(ts.get("Average conceded goals per match") or 0)
    except (TypeError, ValueError): avg_conc = 0
    if avg_scored > 0:
        ind["ofensivo"] = (100 if avg_scored >= 2.0 else 75 if avg_scored >= 1.5 else
                            50 if avg_scored >= 1.0 else 25 if avg_scored >= 0.5 else 10)
    if avg_conc > 0 or avg_scored > 0:
        ind["defensivo_adversario"] = (100 if avg_conc <= 0.7 else 75 if avg_conc <= 1.0 else
                                        50 if avg_conc <= 1.5 else 25 if avg_conc <= 2.0 else 10)

    # casa/fora — proxy: posição na tabela. O JSON de previsões não tem um
    # split casa/fora dedicado por time; classificação é o indicador de força
    # relativa mais próximo já calculado hoje (mesma lógica do Score do Time).
    standings = d.get("classificacao") or []
    hl = [r for r in standings if r.get("destacado")]
    if len(hl) >= 2:
        my_row = hl[0] if is_home else hl[1]
        try: pos = int(my_row.get("pos") or 0)
        except (TypeError, ValueError): pos = 0
        total = len(standings)
        if pos > 0 and total > 0:
            pct = pos / total
            ind["casa_fora"] = (100 if pct <= 0.20 else 80 if pct <= 0.40 else
                                 55 if pct <= 0.60 else 27 if pct <= 0.80 else 0)

    h2h = _h2h_confronto_direto(d.get("confrontos_diretos"), m.get("casa"), m.get("fora"))
    if h2h.get("amostra_suficiente"):
        vit = h2h["vitorias_casa"] if is_home else h2h["vitorias_fora"]
        ind["h2h"] = round(vit / h2h["total"] * 100)

    return ind, h2h


def _convicao_score(m, is_home):
    """Soma ponderada dos indicadores (pesos em CONVICCAO_PESOS), com penalização
    quando os indicadores divergem fortemente entre si. None se não houver
    nenhum indicador calculável pra essa partida ainda (detalhes incompletos)."""
    ind, h2h = _convicao_indicadores(m, is_home)
    if not ind:
        return None, h2h
    pesos = _convicao_pesos_efetivos(ind.keys())
    score = sum(pesos[k] * v for k, v in ind.items())
    if len(ind) >= 2:
        divergencia = max(ind.values()) - min(ind.values())
        score -= divergencia * CONVICCAO_PENALIDADE_DIVERGENCIA
    return round(min(100, max(0, score))), h2h


APP_VERSION = "2026-05-18-v9"

# ── PAINEL PRINCIPAL — jogos + odds (1X2 e Over/Under) do BetExplorer ──────────
# A listagem de jogos por liga do BetExplorer (homepage) já vem 100% renderizada
# em HTML no endpoint /gres/ajax/homepage-data.php (sem precisar de Selenium/JS).
# Cada chamada só traz UM tipo de aposta por vez (betType=1x2 ou betType=ou), então
# buscamos as duas e casamos os jogos pelo event-id pra ter 1/X/2 + Over/Under juntos.
BETEXPLORER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.betexplorer.com/br/",
    "Accept": "text/html, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}
BETEXPLORER_BASE = "https://www.betexplorer.com"
_PAINEL_CACHE_TTL = 60  # odds mudam, mas o BetExplorer passou a bloquear (429) com requests demais
_painel_cache = {}   # cache_key (data ou "today") -> {"ts":, "data":}
_painel_lock = threading.Lock()

# ── Rate limit — o BetExplorer começou a devolver 429 (Too Many Requests) quando
# batemos rápido demais (vários widgets abertos ao mesmo tempo, auto-refresh,
# múltiplos usuários). Serializa TODAS as chamadas ao BetExplorer com um espaço
# mínimo entre elas + retry com backoff quando toma 429, em vez de derrubar a
# aba na cara do usuário.
_be_rate_lock = threading.Lock()
_be_last_request_ts = 0.0
_BE_MIN_INTERVAL = 0.6  # segundos entre requests consecutivos ao BetExplorer


def _be_get(url, params=None, timeout=15, retries=3):
    """GET no BetExplorer com espaçamento mínimo entre chamadas e retry/backoff
    em cima de 429 — evita que um pico de acessos derrube a página pro usuário."""
    global _be_last_request_ts
    last_exc = None
    for attempt in range(retries):
        with _be_rate_lock:
            wait = _BE_MIN_INTERVAL - (time.time() - _be_last_request_ts)
            if wait > 0:
                time.sleep(wait)
            _be_last_request_ts = time.time()
        r = http_req.get(url, params=params, headers=BETEXPLORER_HEADERS, timeout=timeout)
        if r.status_code == 429:
            last_exc = RuntimeError("O BetExplorer está limitando as requisições no momento (429). Tente de novo em alguns segundos.")
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
        r.encoding = "utf-8"
        return r
    raise last_exc


def _be_fetch_bettype_html(bettype, date_params=None):
    """date_params, quando informado, é {"year":, "month":, "day":} — mesmo
    parâmetro que o calendário do BetExplorer usa pra navegar entre dias."""
    params = {"tab": "all", "betType": bettype, "lang": "br", "tz": "-3:00", "start": 0, "end": 300}
    if date_params:
        params.update(date_params)
    r = _be_get(f"{BETEXPLORER_BASE}/gres/ajax/homepage-data.php", params=params)
    return r.text


def _be_parse_bettype(html):
    """Parseia o fragment HTML do BetExplorer pra um tipo de aposta, retornando
    {event_id: {...}} e a lista de ligas na ordem em que aparecem na página."""
    soup = BeautifulSoup(html, "html.parser")
    matches = {}
    leagues_order = []
    for ul in soup.find_all("ul", class_="leagues-list"):
        country = ul.get("data-country", "")
        header_li = ul.find("li", class_="js-tournament")
        league_name, flag_url, ttid = "", "", None
        if header_li:
            name_tag = header_li.find("p", class_="leaguesNames")
            if name_tag:
                league_name = name_tag.get_text(strip=True)
            img_tag = header_li.find("img")
            if img_tag:
                flag_url = img_tag.get("data-src") or img_tag.get("src") or ""
            ttid = header_li.get("data-ttid")
        league_key = ttid or league_name
        leagues_order.append({"key": league_key, "country": country, "league_name": league_name, "flag_url": flag_url})

        for row in ul.find_all("li", class_="table-main__tournamentLiContent"):
            event_id = row.get("data-event-id")
            if not event_id:
                continue
            status_el = row.select_one(".matchDateStatus")
            status_text = status_el.get_text(strip=True) if status_el else ""

            participants = row.select(".table-main__truncate")
            home = participants[0].get_text(strip=True) if len(participants) > 0 else ""
            away = participants[1].get_text(strip=True) if len(participants) > 1 else ""

            logos = row.select(".table-main__participantLogo")
            home_logo = logos[0].get("data-src") or logos[0].get("src") if len(logos) > 0 else None
            away_logo = logos[1].get("data-src") or logos[1].get("src") if len(logos) > 1 else None
            if home_logo and home_logo.startswith("/"):
                home_logo = BETEXPLORER_BASE + home_logo
            if away_logo and away_logo.startswith("/"):
                away_logo = BETEXPLORER_BASE + away_logo

            score_home = score_away = None
            score_div = row.select_one(".mainResult.table-main__Bold.mobileHidden")
            if score_div:
                parts = [d.get_text(strip=True) for d in score_div.find_all("div")]
                digits = [p for p in parts if p and p not in ("-", ":")]
                if len(digits) >= 2:
                    score_home, score_away = digits[0], digits[1]

            link_tag = row.find("a", attrs={"data-live-cell": "matchlink"})
            match_url = link_tag.get("href") if link_tag else None

            odds_wrap = row.select_one(".oddsColumn")
            values, line = [], None
            if odds_wrap:
                line_div = odds_wrap.find("div", class_="table-main__oddOU")
                if line_div:
                    line = line_div.get_text(strip=True)
                for odd_div in odds_wrap.select(".table-main__odd"):
                    btn = odd_div.find(["button", "p"])
                    values.append(btn.get("data-odd") if btn else None)

            try:
                ts = int(row.get("data-ts") or 0)
            except (TypeError, ValueError):
                ts = 0

            matches[event_id] = {
                "event_id": event_id, "league_key": league_key, "ts": ts,
                "status_text": status_text, "home": home, "away": away,
                "home_logo": home_logo, "away_logo": away_logo,
                "score_home": score_home, "score_away": score_away,
                "match_url": match_url, "line": line, "odds": values,
            }
    return matches, leagues_order


# ── NowGoal — fonte alternativa dos jogos do dia (BetExplorer passou a bloquear
# com 429 com frequência demais). O NowGoal serve os jogos como um array JS puro
# (sem HTML pra parsear), só exige um cookie de sessão (LS_ACCESS_TOKEN) obtido
# visitando a home antes de acessar o feed de dados. Por ora só migramos jogos +
# placar + liga/país (odds e link de análise continuam vindo do BetExplorer, que
# ainda alimenta os widgets de Confronto Direto/Últimos Resultados/Classificações).
NOWGOAL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.nowgoal.net/",
    "Accept": "*/*",
}
NOWGOAL_BASE = "https://www.nowgoal.net"
_ng_cookie_cache = {"ts": 0.0, "jar": None}
_ng_cookie_lock = threading.Lock()
_NG_COOKIE_TTL = 3600  # 1h — token de sessão simples, não expira rápido, mas renovamos por segurança

_NG_STATUS_MAP = {
    "0": "Agendado", "1": "1º Tempo", "2": "Intervalo", "3": "2º Tempo",
    "4": "Prorrogação", "5": "Pênaltis", "-1": "Encerrado", "7": "Adiado", "8": "Cancelado",
}


def _ng_get_cookie_jar():
    """Visita a home do NowGoal pra pegar o cookie de sessão (LS_ACCESS_TOKEN) exigido
    pelo feed de dados — sem ele o endpoint devolve {"code":100401} em vez do array JS."""
    with _ng_cookie_lock:
        now = time.time()
        if _ng_cookie_cache["jar"] is not None and (now - _ng_cookie_cache["ts"]) < _NG_COOKIE_TTL:
            return _ng_cookie_cache["jar"]
        r = http_req.get(f"{NOWGOAL_BASE}/", headers=NOWGOAL_HEADERS, timeout=15)
        r.raise_for_status()
        _ng_cookie_cache["jar"] = r.cookies
        _ng_cookie_cache["ts"] = now
        return r.cookies


def _ng_split_js_array(content):
    """Faz o split de um literal de array JS tipo `1,'a, b',,'c'` respeitando aspas
    simples e elementos vazios (vírgulas seguidas) — não dá pra usar json.loads porque
    o NowGoal usa aspas simples e omite elementos nulos em vez de usar `null`."""
    tokens = []
    buf = ""
    in_str = False
    escape = False
    for ch in content:
        if in_str:
            if escape:
                buf += ch
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == "'":
                in_str = False
            else:
                buf += ch
            continue
        if ch == "'":
            in_str = True
            continue
        if ch == ",":
            tokens.append(buf.strip())
            buf = ""
            continue
        buf += ch
    tokens.append(buf.strip())
    return tokens


_NG_ODDS_COMPANY = "8"  # bookmaker usado como fonte de odds (handicap asiático/1x2/O-U) — ver goal{id}.xml


def _ng_fetch_odds():
    """Busca o feed de odds do NowGoal (handicap asiático + 1X2 + over/under) pra
    um bookmaker fixo. Formato por linha (dentro de <m>...</m>):
    match_id, ah_provider_id, ah_line, ah_home_odd, ah_away_odd,
    x12_provider_id, odd_1, odd_x, odd_2,
    ou_provider_id, ou_line, odd_over, odd_under, ...flags
    Retorna {match_id: {...}} — se falhar, retorna {} (odds ficam vazias, sem quebrar a listagem)."""
    try:
        jar = _ng_get_cookie_jar()
        r = http_req.get(
            f"{NOWGOAL_BASE}/gf/data/odds/en/goal{_NG_ODDS_COMPANY}.xml",
            headers=NOWGOAL_HEADERS, cookies=jar, timeout=15,
        )
        r.raise_for_status()
        text = r.text
    except Exception:
        return {}

    odds = {}
    for m in re.finditer(r"<m>(.*?)</m>", text):
        fields = m.group(1).split(",")
        if len(fields) < 13:
            continue
        match_id = fields[0]
        odds[match_id] = {
            "ah_line": fields[2] or None, "ah_home": fields[3] or None, "ah_away": fields[4] or None,
            "odd_1": fields[6] or None, "odd_x": fields[7] or None, "odd_2": fields[8] or None,
            "ou_line": fields[10] or None, "odd_over": fields[11] or None, "odd_under": fields[12] or None,
        }
    return odds


def _ng_fetch_today_matches():
    """Busca e parseia o feed de jogos do dia do NowGoal (array JS puro em vez de
    HTML). Retorna (matches: list[dict], leagues_info: {league_index: {...}})."""
    jar = _ng_get_cookie_jar()
    r = http_req.get(f"{NOWGOAL_BASE}/gf/data/bf_en-idn1.js", headers=NOWGOAL_HEADERS, cookies=jar, timeout=15)
    r.raise_for_status()
    text = r.text
    odds_by_match = _ng_fetch_odds()

    countries = {}   # idx -> nome do país
    for m in re.finditer(r"C\[(\d+)\]=\[(.*?)\];", text):
        idx = int(m.group(1))
        parts = _ng_split_js_array(m.group(2))
        countries[idx] = parts[1] if len(parts) > 1 else ""

    leagues = {}     # idx -> {"name":, "country":}
    for m in re.finditer(r"B\[(\d+)\]=\[(.*?)\];", text):
        idx = int(m.group(1))
        parts = _ng_split_js_array(m.group(2))
        name = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else "")
        country_idx = None
        try:
            country_idx = int(parts[10]) if len(parts) > 10 and parts[10] else None
        except ValueError:
            country_idx = None
        leagues[idx] = {"name": name, "country": countries.get(country_idx, "")}

    matches = []
    for m in re.finditer(r"A\[(\d+)\]=\[(.*?)\];", text):
        parts = _ng_split_js_array(m.group(2))
        if len(parts) < 11:
            continue
        try:
            match_id = parts[0]
            league_idx = int(parts[1]) if parts[1] else None
            home_name, away_name = parts[4], parts[5]
            kickoff = parts[6]
            status_code = parts[8]
            not_started = status_code == "0"  # antes do apito inicial o NowGoal já manda "0" em placar/HT/escanteio
            score_home = parts[9] if parts[9] != "" and not not_started else None
            score_away = parts[10] if parts[10] != "" and not not_started else None
            ht_home = parts[11] if len(parts) > 11 and parts[11] != "" and not not_started else None
            ht_away = parts[12] if len(parts) > 12 and parts[12] != "" and not not_started else None
            corner_home = parts[27] if len(parts) > 27 and parts[27] != "" and not not_started else None
            corner_away = parts[28] if len(parts) > 28 and parts[28] != "" and not not_started else None
        except (IndexError, ValueError):
            continue
        lg = leagues.get(league_idx, {"name": "", "country": ""})
        try:
            ts = int(datetime.strptime(kickoff, "%Y-%m-%d %H:%M:%S").timestamp())
        except ValueError:
            ts = 0

        minute = None
        if status_code in ("1", "3") and ts:
            elapsed = int((time.time() - ts) / 60)
            if status_code == "1":
                minute = max(0, min(elapsed, 45))
            else:  # 2º tempo — aproximado: desconta o intervalo (~15min) do tempo corrido
                minute = max(46, min(elapsed - 15, 90))

        odds = odds_by_match.get(match_id, {})
        matches.append({
            "event_id": match_id,
            "league_key": league_idx,
            "league_name": lg["name"], "country": lg["country"],
            "time": _NG_STATUS_MAP.get(status_code, status_code),
            "minute": minute,
            "home": home_name, "away": away_name,
            "home_logo": None, "away_logo": None,
            "score_home": score_home, "score_away": score_away,
            "ht_home": ht_home, "ht_away": ht_away,
            "corner_home": corner_home, "corner_away": corner_away,
            "match_url": None,
            "odd_1": odds.get("odd_1"), "odd_x": odds.get("odd_x"), "odd_2": odds.get("odd_2"),
            "ou_line": odds.get("ou_line"), "odd_over": odds.get("odd_over"), "odd_under": odds.get("odd_under"),
            "ah_line": odds.get("ah_line"), "ah_home": odds.get("ah_home"), "ah_away": odds.get("ah_away"),
            "ts": ts,
        })
    return matches


# ── "Comparação de força" (widget de análise pré-jogo do NowGoal) ─────────────
# O NowGoal já calcula tudo isso no client (grades/percentuais de H2H, Estado,
# Ataque, Defesa, Valor de mercado, Escanteios/Cartões/Faltas/Posse) a partir de
# dados embutidos na própria página (`battleData`, `lastMatchData`, `marketData`,
# `survayData`) e expõe o resultado pronto em `window._strength` depois que a
# página carrega. Em vez de reimplementar essa fórmula (script minificado de
# ~1MB, não vale o risco de divergir do site original), abrimos a página com
# Playwright (igual já fazemos pro contexto do BetExplorer) e lemos esse objeto
# já calculado direto do browser.
_ng_strength_cache = {}   # match_id -> {"ts":, "data": {...}}
_ng_strength_lock = threading.Lock()
_NG_STRENGTH_TTL = 900  # 15min — dado muda pouco entre atualizações

def _ng_strength_cache_prune():
    """Mesmo padrão de _momentum_cache_prune (achado na investigação de custo
    Railway de 2026-09-08): o TTL acima só decide se um hit de cache serve ou
    não, nunca REMOVE nada — sem isso, cada match_id que já passou pela
    Comparação de Força (aberta manualmente ou pelos exports em lote) ficava
    ocupando memória pra sempre."""
    if len(_ng_strength_cache) < 500:
        return
    now = time.time()
    stale = [mid for mid, v in _ng_strength_cache.items() if now - v.get("ts", 0) > _NG_STRENGTH_TTL * 4]
    for mid in stale:
        _ng_strength_cache.pop(mid, None)
_ng_playwright_semaphore = threading.Semaphore(2)


def _ng_sanitize_nan(obj):
    """O NowGoal calcula alguns percentuais como 0/0 => NaN (ex.: time sem jogos
    ainda nesse recorte). Python aceita NaN como literal JSON na serialização,
    mas isso não é JSON válido de verdade — o `fetch().json()` do navegador
    rejeita com "Unexpected token 'N'". Troca por None (vira null, JSON válido)."""
    if isinstance(obj, float) and (obj != obj):  # NaN nunca é igual a si mesmo
        return None
    if isinstance(obj, dict):
        return {k: _ng_sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_ng_sanitize_nan(v) for v in obj]
    return obj


_NG_MATCH_TABLE_JS = """
() => {
    function extract(n) {
        const q = (id) => { const el = document.getElementById(id); return el ? el.textContent.trim() : null; };
        const qv = (id) => { const el = document.querySelector('#' + id + ' .value'); return el ? el.textContent.trim() : null; };
        const table = document.getElementById('table_v' + n);
        if (!table) return null;
        const rows = [...table.querySelectorAll('tr[id^="tr' + n + '_"]')].map(tr => {
            const tds = tr.querySelectorAll('td');
            const g = (i) => tds[i] ? tds[i] : null;
            const scoreTd = g(3), cornerTd = g(5);
            const ft = scoreTd ? scoreTd.querySelector('[class^="fscore"]') : null;
            const ht = scoreTd ? scoreTd.querySelector('[class^="hscore"]') : null;
            const cFt = cornerTd ? cornerTd.querySelector('[class^="fcorner"]') : null;
            const cHt = cornerTd ? cornerTd.querySelector('[class^="hcorner"]') : null;
            const odd = (i) => { const td = g(i); return td ? td.getAttribute('data-o') : null; };
            return {
                league: g(0) ? (g(0).getAttribute('title') || g(0).textContent.trim()) : null,
                date: (() => { const s = g(1) ? g(1).querySelector('[data-t]') : null; return s ? s.getAttribute('data-t') : null; })(),
                home: g(2) ? g(2).textContent.trim() : null,
                score_ft: ft ? ft.textContent.trim() : null,
                score_ht: ht ? ht.textContent.replace(/[()]/g, '').trim() : null,
                away: g(4) ? g(4).textContent.trim() : null,
                corner_ft: cFt ? cFt.textContent.trim() : null,
                corner_ht: cHt ? cHt.textContent.replace(/[()]/g, '').trim() : null,
                odd_hw: odd(6), odd_d: odd(7), odd_aw: odd(8),
                wl_badge: g(9) ? g(9).textContent.trim() : null,
                odd_ah_home: odd(10), ah_line: odd(11), odd_ah_away: odd(12),
                ah_badge: g(13) ? g(13).textContent.trim() : null,
                ou_badge: g(14) ? g(14).textContent.trim() : null,
            };
        });
        return {
            rows,
            summary: {
                win: q('hW_v' + n), draw: q('d_v' + n), lose: q('gW_v' + n),
                goal_avg_home: q('hsAvg_v' + n), goal_avg_away: q('gsAvg_v' + n),
                ah_home_pct: qv('ahWBar_v' + n), ah_draw_pct: qv('ahDBar_v' + n), ah_away_pct: qv('ahLBar_v' + n),
                ah_count: q('ahCount_v' + n),
                ou_over_pct: qv('ouWBar_v' + n), ou_draw_pct: qv('ouDBar_v' + n), ou_under_pct: qv('ouLBar_v' + n),
                ou_count: q('ouCount_v' + n),
            },
        };
    }
    return { home: extract(1), away: extract(2), h2h: extract(3) };
}
"""

# "Estatísticas de probabilidades" (Win/Draw/Lose + Over/Draw/Under de todas as
# odds parecidas), "Distribuição de metas" (nº de gols / cronograma de gols /
# momento do 1º gol), "Meio período/Tempo integral" (matriz HT x FT) e
# "Diferença de gols HT x FT" — todos widgets prontos do NowGoal (ids oddsStat/
# goalStat/HFStat/GDStat), lidos direto do DOM já renderizado como os outros.
_NG_EXTRA_STATS_JS = """
() => {
    function readGroups(ul) {
        if (!ul) return null;
        // O NowGoal já embute TODAS as variantes de HT/HA-Igual no atributo "rate"
        // (por item, no oddsStat; no <ul> inteiro, no HFStat/goalStat) — os
        // checkboxes só trocam qual variante já calculada é exibida, sem nova
        // busca. Repassa o "rate" cru pro frontend poder alternar sem refazer
        // scraping nenhum.
        return {
            ul_rate: ul.getAttribute('rate'),
            groups: [...ul.querySelectorAll('li.group')].map(li => {
                const items = [...li.querySelectorAll('.item2')].map(it => ({
                    home_pct: it.querySelector('.home.bar') ? parseFloat(it.querySelector('.home.bar').style.height) : null,
                    away_pct: it.querySelector('.away.bar') ? parseFloat(it.querySelector('.away.bar').style.height) : null,
                    home_val: it.querySelector('.home .value') ? it.querySelector('.home .value').textContent.trim() : null,
                    away_val: it.querySelector('.away .value') ? it.querySelector('.away .value').textContent.trim() : null,
                    label: it.querySelector('.txt') ? it.querySelector('.txt').textContent.trim() : null,
                    rate: it.getAttribute('rate'),
                }));
                const titEl = li.querySelector('.tit');
                return { title: titEl ? titEl.textContent.replace(/\\s+/g, ' ').trim() : null, items };
            }),
        };
    }
    // "Momento do primeiro gol" (3ª sub-aba) usa o MESMO elemento #goalTimeStat
    // que "Cronograma de metas" — só troca via switchGoalStat(2), e o clique
    // síncrono não dá tempo do DOM re-renderizar antes da gente ler (a leitura
    // saía vazia). Em vez de depender desse timing, o rate de #goalTimeStat já
    // vem com as 4 variantes (time-normal, time-HA, primeiro gol-normal,
    // primeiro gol-HA) no mesmo atributo — só troca de sub-aba pra pegar os
    // rótulos certos (que são os mesmos nas duas abas) e decodifica o índice
    // 2/3 no frontend em vez de tentar reler o DOM depois do 3º clique.
    let goalNum = null, goalTime = null;
    if (typeof switchGoalStat === 'function' && document.getElementById('goalNumStat')) {
        switchGoalStat(0); goalNum = readGroups(document.getElementById('goalNumStat'));
        switchGoalStat(1); goalTime = readGroups(document.getElementById('goalTimeStat'));
    }
    return {
        odds_stat: readGroups(document.getElementById('panLuStat')),
        goal_num_stat: goalNum,
        goal_time_stat: goalTime,
        hf_stat: readGroups(document.getElementById('HFStat')),
        gd_stat: readGroups(document.getElementById('GDStat')),
    };
}
"""


def _ng_fetch_strength(match_id, _attempt=1):
    with _ng_strength_lock:
        cached = _ng_strength_cache.get(match_id)
        if cached and (time.time() - cached["ts"]) < _NG_STRENGTH_TTL:
            return cached["data"]

    from playwright.sync_api import sync_playwright

    data = None
    try:
        with _ng_playwright_semaphore:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                try:
                    page = browser.new_page(
                        user_agent=NOWGOAL_HEADERS["User-Agent"],
                        viewport={"width": 1280, "height": 900},
                    )
                    page.goto(f"{NOWGOAL_BASE}/match/h2h-{match_id}", wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_function(
                        "() => window._strength && window._strength.count !== undefined",
                        timeout=20000,
                    )
                    data = _ng_sanitize_nan(page.evaluate("() => window._strength"))
                    # As 3 tabelas de histórico (últimos resultados de casa/fora + confronto
                    # direto — table_v1/v2/v3) são preenchidas pelo NowGoal via JS depois que a
                    # página carrega, igual o _strength — extrai direto do DOM já renderizado em
                    # vez de tentar achar/replicar o endpoint que as alimenta.
                    tables = _ng_sanitize_nan(page.evaluate(_NG_MATCH_TABLE_JS))
                    data["last_results_home"] = tables.get("home")
                    data["last_results_away"] = tables.get("away")
                    data["h2h_table"] = tables.get("h2h")
                    # "Partidas históricas com as mesmas probabilidades" (AH/1X2/O-U) — o
                    # NowGoal também calcula isso no client e expõe pronto em window._sameOdds,
                    # mas isso carrega um pouco depois (via ajax assíncrono próprio) do que o
                    # _strength — sem esperar por ele especificamente, `data` ainda vem vazio.
                    try:
                        page.wait_for_function(
                            "() => window._sameOdds && window._sameOdds.data && window._sameOdds.data.AHAllSclass",
                            timeout=10000,
                        )
                        data["same_odds"] = _ng_sanitize_nan(page.evaluate("() => window._sameOdds"))
                    except Exception:
                        data["same_odds"] = None
                    extra = _ng_sanitize_nan(page.evaluate(_NG_EXTRA_STATS_JS))
                    data.update(extra)
                finally:
                    browser.close()
    except Exception:
        if _attempt < 2:
            time.sleep(1.5)
            return _ng_fetch_strength(match_id, _attempt=_attempt + 1)
        raise RuntimeError("Não foi possível carregar a Comparação de Força dessa partida no NowGoal.")

    if data is None:
        raise RuntimeError("Não foi possível carregar a Comparação de Força dessa partida no NowGoal.")

    with _ng_strength_lock:
        _ng_strength_cache[match_id] = {"ts": time.time(), "data": data}
        _ng_strength_cache_prune()
    return data


@app.route("/api/painel/ng_strength")
def api_painel_ng_strength():
    match_id = request.args.get("match_id", "")
    if not match_id or not match_id.isdigit():
        return jsonify({"error": "match_id inválido"}), 400
    try:
        data = _ng_fetch_strength(match_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(data)


# Grades por eixo (H2H/Estado/Ataque/Defesa/Valor) — só o suficiente pra
# calcular a "diferença de força" mostrada na listagem do Painel Principal.
_PAINEL_FORCA_RADAR_AXES = ("battle", "state", "attack", "defend", "market")


@app.route("/api/painel/ng_strength_cached", methods=["POST"])
def api_painel_ng_strength_cached():
    """Devolve a força (só os 5 eixos usados pro grade geral) dos match_ids
    que JÁ estiverem em cache (de alguém ter aberto a Comparação de Força
    antes) — nunca dispara um Playwright novo aqui. É o que permite mostrar
    a diferença de força na listagem inteira sem custo extra: os jogos ainda
    não abertos simplesmente não aparecem na resposta, e a listagem mostra
    "—" pra eles até alguém abrir o modal daquele jogo alguma vez."""
    body = request.get_json(force=True, silent=True) or {}
    match_ids = body.get("match_ids") or []
    if not isinstance(match_ids, list):
        return jsonify({}), 400
    now = time.time()
    result = {}
    with _ng_strength_lock:
        for mid in match_ids:
            mid = str(mid)
            cached = _ng_strength_cache.get(mid)
            if cached and (now - cached["ts"]) < _NG_STRENGTH_TTL:
                d = cached["data"]
                result[mid] = {k: d[k] for k in _PAINEL_FORCA_RADAR_AXES if k in d}
    return jsonify(result)


# ── Metodologias — ranking de tipsters do tips.nowgoal.net ────────────────────
# Ao contrário dos widgets de análise (window._strength etc), o ranking e os
# palpites de cada usuário são JSON puro servido direto pelo backend deles —
# não precisa de Playwright, só requests normal.
TIPS_BASE = "https://tips.nowgoal.net"
TIPS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://tips.nowgoal.net/",
}
_tips_ranking_cache = {}   # tipo -> {"ts":, "data":}
_tips_ranking_lock = threading.Lock()
_TIPS_RANKING_TTL = 600  # 10min

_tips_user_cache = {}   # user_id -> {"ts":, "data":}
_tips_user_lock = threading.Lock()
_TIPS_USER_TTL = 600

def _tips_user_cache_prune():
    if len(_tips_user_cache) < 500:
        return
    now = time.time()
    stale = [uid for uid, v in _tips_user_cache.items() if now - v.get("ts", 0) > _TIPS_USER_TTL * 4]
    for uid in stale:
        _tips_user_cache.pop(uid, None)


def _tips_fetch_ranking(kind):
    """kind: 1=Semana+Taxa de vitória, 2=Semana+ROI, 3=Mês+Taxa de vitória, 4=Mês+ROI
    (mapeamento confirmado testando os 2 seletores — Semana/Mês e Win Rate/ROI —
    no site original e comparando qual `type` cada combinação disparava)."""
    with _tips_ranking_lock:
        cached = _tips_ranking_cache.get(kind)
        if cached and (time.time() - cached["ts"]) < _TIPS_RANKING_TTL:
            return cached["data"]
    r = http_req.get(f"{TIPS_BASE}/home/getrankingjson", params={"type": kind}, headers=TIPS_HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    with _tips_ranking_lock:
        _tips_ranking_cache[kind] = {"ts": time.time(), "data": data}
    return data


_tips_article_cache = {}   # article_id -> {"ts":, "data":}
_tips_article_lock = threading.Lock()
_TIPS_ARTICLE_TTL = 3600  # 1h — o palpite de um artigo já publicado não muda mais

def _tips_article_cache_prune():
    """O palpite de um artigo publicado não muda mais (comentário acima), mas
    o site publica artigo novo o tempo todo — sem remover os antigos, essa
    cache cresce pra sempre (mesmo padrão de vazamento já corrigido 2x nesta
    sessão: TTL só decide se serve, nunca remove)."""
    if len(_tips_article_cache) < 2000:
        return
    now = time.time()
    stale = [aid for aid, v in _tips_article_cache.items() if now - v.get("ts", 0) > _TIPS_ARTICLE_TTL * 24]
    for aid in stale:
        _tips_article_cache.pop(aid, None)


_TIPS_PICK_RE = re.compile(
    r"var odds1 = changeOdds\(([\d.]+).*?"
    r"var odds2 = changeOdds\(([\d.]+).*?"
    r"var odds3 = changeOdds\(([\d.]+).*?"
    r"data-kind='(\d)'>(Home|Over) .*?"
    r"data-kind='\d'>(Away|Under)",
    re.S,
)
_TIPS_FORMAT_RE = re.compile(r'dv\.format\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\)')


def _tips_fetch_article_pick(article_id):
    """A maioria das dicas do tips.nowgoal.net é paga (o texto da análise vem
    trocado por um parágrafo de marketing genérico e idêntico em todas), mas o
    palpite em si (que lado, com qual odd) fica visível de graça — só o texto
    de análise fica bloqueado. O detalhe é que essa parte NÃO vem pronta no
    HTML: o servidor gera um trechinho de JS que monta a div na hora (usando
    .format() estilo Python pra decidir qual lado leva a classe "on", que é o
    que marca o palpite de fato) — então em vez de rodar esse JS (precisaria
    de Playwright), extrai os valores literais desse script com regex."""
    with _tips_article_lock:
        cached = _tips_article_cache.get(article_id)
        if cached and (time.time() - cached["ts"]) < _TIPS_ARTICLE_TTL:
            return cached["data"]
    pick = None
    try:
        r = http_req.get(f"{TIPS_BASE}/article/{article_id}", headers=TIPS_HEADERS, timeout=10)
        r.raise_for_status()
        m = _TIPS_PICK_RE.search(r.text)
        fmt = _TIPS_FORMAT_RE.search(r.text)
        if m and fmt:
            home_odd, line_val, away_odd, kind_code, home_label, away_label = m.groups()
            home_on, _, away_on = fmt.groups()
            pick = {
                "kind": "AH" if kind_code == "2" else "OU",
                "line": line_val,
                "home_label": home_label, "home_odd": home_odd, "home_pick": home_on.strip() == "on",
                "away_label": away_label, "away_odd": away_odd, "away_pick": away_on.strip() == "on",
            }
    except Exception:
        pick = None
    with _tips_article_lock:
        _tips_article_cache[article_id] = {"ts": time.time(), "data": pick}
        _tips_article_cache_prune()
    return pick


def _tips_fetch_user_tips_raw(user_id):
    """Só a listagem básica (sem buscar o palpite de cada artigo) — usada tanto
    como base pra `_tips_fetch_user_tips` quanto pro "Previsões mais acertivas"
    (que só precisa de okind/isWin/isEnd, já vêm de graça nessa chamada)."""
    with _tips_user_lock:
        cached = _tips_user_cache.get(user_id)
        if cached and (time.time() - cached["ts"]) < _TIPS_USER_TTL:
            return cached["data"]
    params = {"userid": user_id, "kind": 0, "pre_page": 0, "req_page": 1, "endid": 0, "minid": 0, "type": 0}
    r = http_req.get(f"{TIPS_BASE}/user/getusertopiclist", params=params, headers=TIPS_HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    with _tips_user_lock:
        _tips_user_cache[user_id] = {"ts": time.time(), "data": data}
        _tips_user_cache_prune()
    return data


_TIPS_MARKET_LABELS = {2: "Handicap Asiático", 3: "Over/Under", 1: "1X2"}


def _tips_compute_best_market(user_id):
    """Agrupa as dicas encerradas do tipster por mercado (okind) e devolve o
    mercado onde ele mais acerta (com pelo menos 3 dicas encerradas nesse
    mercado, senão o recorte é pequeno demais pra significar algo)."""
    try:
        data = _tips_fetch_user_tips_raw(user_id)
    except Exception:
        return None
    tips = data.get("list") or []
    by_market = {}
    for t in tips:
        if not t.get("isEnd"):
            continue
        k = t.get("okind")
        m = by_market.setdefault(k, {"wins": 0, "total": 0})
        m["total"] += 1
        if t.get("isWin"):
            m["wins"] += 1
    best = None
    for k, m in by_market.items():
        if m["total"] < 3:
            continue
        pct = m["wins"] / m["total"]
        if best is None or pct > best["pct"]:
            best = {"kind": k, "label": _TIPS_MARKET_LABELS.get(k, "Outros"), "pct": pct, "wins": m["wins"], "total": m["total"]}
    return best


def _tips_fetch_user_tips(user_id):
    data = _tips_fetch_user_tips_raw(user_id)

    # Busca o palpite de cada dica em paralelo (a página do artigo é lenta,
    # ~2-3s cada — sequencial levaria mais de 1min pras 50 dicas de uma vez).
    tips = data.get("list") or []
    if tips:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=20) as ex:
            picks = list(ex.map(lambda t: _tips_fetch_article_pick(t["id"]), tips))
        for t, pick in zip(tips, picks):
            t["pick"] = pick

    with _tips_user_lock:
        _tips_user_cache[user_id] = {"ts": time.time(), "data": data}
        _tips_user_cache_prune()
    return data


@app.route("/api/painel/tips_ranking")
def api_painel_tips_ranking():
    kind = request.args.get("type", "1")
    if kind not in ("1", "2", "3", "4"):
        return jsonify({"error": "type inválido"}), 400
    try:
        data = _tips_fetch_ranking(kind)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(data)


@app.route("/api/painel/tips_best_markets")
def api_painel_tips_best_markets():
    """Pro mesmo ranking (Semana/Mês × Taxa de Vitória/ROI já existente), busca
    em qual mercado (Handicap Asiático ou Over/Under) cada tipster mais acerta.
    Bem mais leve que /tips_user: só a listagem básica de cada um (sem os
    palpites por artigo), então dá pra buscar todo mundo do ranking em paralelo
    numa boa."""
    kind = request.args.get("type", "1")
    if kind not in ("1", "2", "3", "4"):
        return jsonify({"error": "type inválido"}), 400
    try:
        ranking = _tips_fetch_ranking(kind)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    users = ranking.get("list") or []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=20) as ex:
        best_markets = list(ex.map(lambda u: _tips_compute_best_market(u["uid"]), users))
    out = []
    for u, best in zip(users, best_markets):
        out.append({
            "uid": u["uid"], "uname": u["uname"], "uimg": u.get("uimg"), "rank": u["rank"], "rrc": u.get("rrc"),
            "best_market": best,
        })
    return jsonify({"list": out})


@app.route("/api/painel/tips_user")
def api_painel_tips_user():
    user_id = request.args.get("uid", "")
    if not user_id or not user_id.isdigit():
        return jsonify({"error": "uid inválido"}), 400
    try:
        data = _tips_fetch_user_tips(user_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(data)


def _painel_fetch_matches_nowgoal(force=False, date_str=None):
    """Versão NowGoal — só serve o dia atual por enquanto (o feed do NowGoal não
    tem parâmetro de data ainda descoberto; navegação por calendário fica limitada
    ao dia de hoje até isso ser mapeado)."""
    now = time.time()
    cache_key = date_str or "today"
    with _painel_lock:
        cached = _painel_cache.get(cache_key)
        if not force and cached is not None and (now - cached["ts"]) < _PAINEL_CACHE_TTL:
            return cached["data"]

    # A busca de rede roda FORA do lock, de propósito — antes ficava dentro, e
    # se o NowGoal travasse (sem estourar exceção, só sem responder) o
    # _painel_lock ficava preso PRA SEMPRE. Como esse mesmo lock é usado por
    # TODO mundo que chama essa função ou _painel_fetch_matches (Painel,
    # pré-carga de Força), as 4 threads do gunicorn (só isso no
    # total, servindo o site inteiro) iam todas ficar esperando esse lock e
    # travavam o site inteiro — sem se recuperar sozinho, porque o worker
    # gthread do gunicorn considera vivo mesmo com toda thread de request
    # travada (achado em produção, 2026-08-27: site inteiro fora do ar até o
    # próximo redeploy). Só a leitura/escrita do dict de cache fica sob lock.
    try:
        matches = _ng_fetch_today_matches()
    except Exception as e:
        with _painel_lock:
            cached = _painel_cache.get(cache_key)
        if cached is not None:
            stale = dict(cached["data"])
            stale["stale"] = True
            stale["stale_error"] = str(e)
            return stale
        return {"error": str(e), "leagues": [], "updated_at": now, "date": date_str}

    # Anexa (quando encontrado) o link direto pra Betfair Exchange / Bolsa de
    # Aposta daquela partida específica, reaproveitando os links que o
    # RadarFutebol já resolve no feed público dele (casando por nome dos
    # times). Ver _find_radar_links — nunca derruba o carregamento do painel
    # se o RadarFutebol estiver fora do ar, só fica sem os links dessa vez.
    try:
        for m in matches:
            lb, lba, lr = _find_radar_links(m.get("home"), m.get("away"), m.get("ts"))
            m["link_betfair"] = lb
            m["link_bolsa"] = lba
            m["link_radar"] = lr
    except Exception as e:
        print(f"[radar-links] Erro anexando links: {e}")

    leagues_map = {}
    for m in matches:
        key = m["league_key"]
        lg = leagues_map.setdefault(key, {
            "key": key, "country": m["country"], "league_name": m["league_name"],
            "flag_url": "", "matches": [],
        })
        lg["matches"].append({k: v for k, v in m.items() if k not in ("league_key", "league_name", "country")})

    leagues = [lg for lg in leagues_map.values() if lg["matches"]]
    for lg in leagues:
        lg["matches"].sort(key=lambda x: x["ts"])

    with _painel_lock:
        cached = _painel_cache.get(cache_key)
        # O NowGoal às vezes devolve uma página de bloqueio/verificação em vez do feed
        # de verdade — isso não estoura exceção (o request "funciona", só que o regex
        # não acha nenhum jogo pra extrair), e sem essa checagem o cache ficava com
        # "leagues: []" por 60s, mostrando "Nenhum jogo encontrado" com o app cheio de
        # jogos de verdade. Se veio vazio e já tínhamos dados bons antes, mantém os
        # dados antigos (marcados como stale) em vez de aceitar o vazio como válido.
        if not leagues and cached is not None and cached["data"].get("leagues"):
            stale = dict(cached["data"])
            stale["stale"] = True
            stale["stale_error"] = "NowGoal devolveu feed vazio (possível bloqueio temporário)"
            return stale

        data = {"leagues": leagues, "updated_at": now, "date": date_str}
        _painel_cache[cache_key] = {"ts": now, "data": data}
        return data


# ── Links diretos pra Betfair Exchange / Bolsa de Aposta ───────────────────────
# O RadarFutebol expõe publicamente (sem login) um feed SSE com os links já
# resolvidos pra cada partida — inclusive o "affid=radarfutebol" no link da
# Bolsa de Aposta (afiliado deles; usamos o mesmo código a pedido do usuário).
# Como não temos os IDs internos de cada partida nessas 2 plataformas, casar
# por nome dos times (mesmo _name_match usado pra SofaScore/Uniscore/FotMob)
# é o jeito de ligar nosso jogo ao link certo sem precisar integrar direto com
# Betfair/Bolsa (o Betfair, inclusive, bloqueia scraping direto por política).
_RADAR_LINKS_URL = (
    "https://www.radarfutebol.com/sse/home"
    "?idioma=pt-br&campoBusca=&somLigado=false&mostrarApenasJogosLive=false"
    "&mostrarApenasJogosFavoritos=false&countJogosMostrar=300"
    "&mostrarFiltroAcrescimo=false&filtroAcrescimoHt=1&filtroAcrescimoFt=1"
    "&filtroAcrescimoHtOperador=%3E%3D&filtroAcrescimoFtOperador=%3E%3D"
    "&filtroAcrescimoCondicao=ou&mostrarApenasJogosOraculo=false"
    "&mostrarApenasJogosBolsa=false&mostrarApenasJogosBetfair=false"
    "&mostrarApenasJogosOver=false&mostrarApenasJogosLayCs=false"
    "&favoritoVencendo=false&favoritoPerdendo=false&casaVencendo=false"
    "&visitanteVencendo=false&empatado=false&filtroAlertas=false"
    "&filtroDiferencaXg=false&ordemInicio=false"
)
_RADAR_LINKS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":    "https://www.radarfutebol.com/",
    "Accept":     "text/event-stream",
}
_RADAR_LINKS_TTL = 90  # feed muda pouco de um minuto pro outro, evita bater toda hora
_radar_links_cache = {"ts": 0.0, "events": []}
_radar_links_lock = threading.Lock()


def _get_radar_futebol_links():
    """Busca o feed público (SSE) do RadarFutebol e extrai, de cada partida,
    o link pronto pra Betfair Exchange e pra Bolsa de Aposta. Só lê a primeira
    linha 'data: {...}' do stream e fecha a conexão — não fica pendurado
    esperando os próximos eventos ao vivo do SSE.

    Bug crítico corrigido (2026-08-29): a checagem de cache exigia
    `_radar_links_cache["events"]` não-vazio pra "valer" — se o RadarFutebol
    caísse (ou devolvesse feed vazio), `ts` nunca era atualizado nos ramos de
    falha, e essa condição nunca batia. Resultado: TODA chamada tentava buscar
    de novo, sem nenhum intervalo entre tentativas. Como _find_radar_links (e
    por tabela esta função) é chamada UMA VEZ POR PARTIDA AO VIVO dentro de um
    loop, num dia com 700+ jogos isso virou milhares de tentativas de 15s cada
    empilhadas, uma atrás da outra — derrubou o site inteiro (as 4 threads do
    gunicorn ficaram presas nisso por horas). Agora `ts` marca a hora da
    ÚLTIMA TENTATIVA (sucesso ou falha), então uma falha só tenta de novo
    depois do TTL passar, nunca a cada chamada."""
    with _radar_links_lock:
        if time.time() - _radar_links_cache["ts"] < _RADAR_LINKS_TTL:
            return _radar_links_cache["events"]
    try:
        r = http_req.get(_RADAR_LINKS_URL, headers=_RADAR_LINKS_HEADERS, stream=True, timeout=15)
        # O servidor não declara charset no Content-Type do SSE, então o requests
        # cai no fallback ISO-8859-1 (padrão HTTP pra text/*) e decodifica errado
        # qualquer acento (ex: "Fenerbahçe" virava "FenerbahÃ§e") — isso quebrava
        # o casamento de nome pra times com acento/cedilha. Força UTF-8 (é o que
        # o feed realmente manda).
        r.encoding = "utf-8"
        payload = None
        # timeout=15 acima só limita cada LEITURA individual — se o servidor
        # (é um endpoint SSE, feito pra ficar aberto) mandar keepalives de vez
        # em quando sem nunca mandar a linha "data:" esperada, o loop reseta
        # esse timeout a cada leitura e pode ficar preso por muito tempo,
        # segurando uma das poucas threads do servidor. Prazo total próprio
        # (não só por-leitura) garante que isso nunca passe de ~10s de verdade.
        _radar_links_deadline = time.time() + 10
        for raw_line in r.iter_lines(decode_unicode=True):
            if raw_line and raw_line.startswith("data:"):
                payload = raw_line[len("data:"):].strip()
                break
            if time.time() > _radar_links_deadline:
                print("[radar-links] Prazo total estourado esperando a linha 'data:' do SSE — abortando essa busca.")
                break
        r.close()
        if not payload:
            with _radar_links_lock:
                _radar_links_cache["ts"] = time.time()
            return _radar_links_cache["events"]

        obj = json.loads(payload)
        events = []
        for camp in obj.get("campeonatos", []):
            for ev in (camp.get("eventos") or {}).values():
                link_betfair = ev.get("linkBetfair")
                link_bolsa = ev.get("linkBolsadeaposta")
                if not (link_betfair or link_bolsa):
                    continue
                ts = None
                inicio = ev.get("inicio")
                if inicio:
                    try:
                        ts = datetime.strptime(inicio, "%Y-%m-%d %H:%M:%S").timestamp()
                    except ValueError:
                        ts = None
                slug_evento = ev.get("slugEvento")
                id_evento = ev.get("idEvento")
                link_radar = f"https://www.radarfutebol.com/radar/{slug_evento}/{id_evento}" if slug_evento and id_evento else None
                events.append({
                    "home": ev.get("timeCasa") or "",
                    "away": ev.get("timeFora") or "",
                    "ts": ts,
                    "link_betfair": link_betfair,
                    "link_bolsa": link_bolsa,
                    "link_radar": link_radar,
                })

        with _radar_links_lock:
            _radar_links_cache["ts"] = time.time()
            _radar_links_cache["events"] = events
        print(f"[radar-links] {len(events)} jogos com link Betfair/Bolsa de Aposta")
        return events
    except Exception as e:
        print(f"[radar-links] Erro buscando feed do RadarFutebol: {e}")
        with _radar_links_lock:
            _radar_links_cache["ts"] = time.time()
        return _radar_links_cache["events"]


def _find_radar_links(home, away, ts=None):
    """Casa (home, away) do nosso feed com os eventos do RadarFutebol. Quando
    o nome bate em mais de uma partida (raro — 2 times com nome parecido
    jogando no mesmo dia), desempata pelo horário mais próximo; se mesmo assim
    a diferença passar de 3h, não arrisca linkar pro jogo errado."""
    if not home or not away:
        return None, None, None
    events = _get_radar_futebol_links()
    candidates = [ev for ev in events if _name_match(home, ev["home"]) and _name_match(away, ev["away"])]
    if not candidates:
        return None, None, None
    if len(candidates) > 1 and ts:
        candidates.sort(key=lambda ev: abs((ev["ts"] or 0) - ts))
        if abs((candidates[0]["ts"] or 0) - ts) > 3 * 3600:
            return None, None, None
    ev = candidates[0]
    return ev.get("link_betfair"), ev.get("link_bolsa"), ev.get("link_radar")


# ── Pré-carga de Força (versão leve) — enche o _ng_strength_cache sozinho, em
# segundo plano, pra coluna "Força" do Painel Principal não depender de
# alguém abrir a Comparação de força manualmente. Uma 1ª versão tentava cobrir
# TODOS os jogos do dia com 2 workers simultâneos e derrubou o app em produção
# (Chromium headless nas costas um do outro, sem pausa, estourou a memória do
# container do Railway). Essa versão é bem mais cautelosa: só 1 Playwright por
# vez (nunca mais que 1 rodando junto com o que alguém abrir na hora, então no
# pior caso são 2 simultâneos — dentro do limite já usado em todo o resto do
# sistema), com uma pausa entre cada partida, e só cobre uma JANELA de horário
# (jogos ao vivo + começando nas próximas horas) em vez do dia inteiro — jogo
# muito distante no futuro não interessa agora mesmo, e entra na janela
# conforme o horário dele se aproxima.
_NG_STRENGTH_PREFETCH_WINDOW_PAST = 2 * 3600    # cobre jogos que começaram até 2h atrás (ainda podem estar ao vivo)
_NG_STRENGTH_PREFETCH_WINDOW_FUTURE = 3 * 3600  # e que começam nas próximas 3h
_NG_STRENGTH_PREFETCH_DELAY = 6        # segundos de respiro entre uma partida e a próxima
_NG_STRENGTH_PREFETCH_CYCLE_GAP = 45   # segundos entre uma checagem "o que falta" e a próxima
_ng_strength_prefetch_queue = queue.Queue()
_ng_strength_prefetch_queued = set()  # dedup — evita enfileirar o mesmo jogo 2x antes dele ser processado
_ng_strength_prefetch_queued_lock = threading.Lock()


def _ng_strength_prefetch_worker():
    while True:
        mid = _ng_strength_prefetch_queue.get()
        try:
            with _ng_strength_lock:
                cached = _ng_strength_cache.get(mid)
                fresh = cached and (time.time() - cached["ts"]) < _NG_STRENGTH_TTL
            if not fresh:
                try:
                    _ng_fetch_strength(mid)
                except Exception as e:
                    print(f"[forca-prefetch] Erro no jogo {mid}: {e}")
        finally:
            with _ng_strength_prefetch_queued_lock:
                _ng_strength_prefetch_queued.discard(mid)
            _ng_strength_prefetch_queue.task_done()
        time.sleep(_NG_STRENGTH_PREFETCH_DELAY)


def _ng_strength_prefetch_filler_loop():
    _github_sync_done.wait(timeout=120)
    while True:
        try:
            matches_data = _painel_fetch_matches_nowgoal()
            now = time.time()
            added = 0
            for lg in matches_data.get("leagues", []):
                for m in lg["matches"]:
                    ts = m.get("ts")
                    if not ts or not (now - _NG_STRENGTH_PREFETCH_WINDOW_PAST <= ts <= now + _NG_STRENGTH_PREFETCH_WINDOW_FUTURE):
                        continue
                    mid = m.get("event_id")
                    if not mid:
                        continue
                    mid = str(mid)
                    with _ng_strength_lock:
                        cached = _ng_strength_cache.get(mid)
                        fresh = cached and (now - cached["ts"]) < _NG_STRENGTH_TTL
                    if fresh:
                        continue
                    with _ng_strength_prefetch_queued_lock:
                        if mid in _ng_strength_prefetch_queued:
                            continue
                        _ng_strength_prefetch_queued.add(mid)
                    _ng_strength_prefetch_queue.put(mid)
                    added += 1
            if added:
                print(f"[forca-prefetch] {added} jogo(s) da janela atual enfileirado(s)")
        except Exception as e:
            print(f"[forca-prefetch] Erro buscando jogos do dia: {e}")
        time.sleep(_NG_STRENGTH_PREFETCH_CYCLE_GAP)


# ── Backup de Força — arquiva em disco (JSON, um arquivo por jogo, sincronizado
# com o GitHub igual momentum_history/shotmap_history) os dados de H-T,
# escanteio, odds (1/X/2/Over/Under) e força (H2H/Estado/Ataque/Defesa/Valor)
# de todo jogo do dia que já ENCERROU. Diferente da pré-carga acima (que
# re-varria jogos ao vivo sem parar e derrubou o app em produção), aqui cada
# jogo só entra na fila UMA VEZ — placar de jogo encerrado não muda mais, e se
# o arquivo já existe no disco nem tenta de novo — então o volume total fica
# limitado a "quantos jogos terminam por dia", nunca cresce sem limite. Mesmo
# assim usa só 1 worker + pausa entre partidas, pela mesma cautela de sempre
# com o Playwright.
_FORCA_BACKUP_DELAY = 8           # segundos de respiro entre uma partida e a próxima
_FORCA_BACKUP_SCAN_INTERVAL = 120 # segundos entre uma varredura "quem terminou" e a próxima
_forca_backup_queue = queue.Queue()
_forca_backup_queued = set()   # event_id em fila — evita duplicar antes de processar
_forca_backup_queued_lock = threading.Lock()


def _forca_backup_path(date_str, event_id):
    return os.path.join(FORCA_HISTORY_DIR, f"{date_str}_{event_id}.json")


def _forca_backup_worker():
    while True:
        date_str, m = _forca_backup_queue.get()
        event_id = str(m.get("event_id"))
        try:
            path = _forca_backup_path(date_str, event_id)
            if not os.path.exists(path):
                # "forca" (axes battle/state/attack/defend/market) saía do
                # window._strength do NowGoal — sem equivalente desde a
                # migração pro Flashscore (2026-08-30), então fica de fora.
                # O resto (odds/HT/escanteio) continua vindo, agora da fonte
                # nova, e o Replay (_replayForca, index.html) continua
                # funcionando igual — só perde a parte de força.
                payload = {
                    "event_id": event_id, "date": date_str,
                    "league": m.get("league_name"), "country": m.get("country"),
                    "home": m.get("home"), "away": m.get("away"),
                    "score_home": m.get("score_home"), "score_away": m.get("score_away"),
                    "ht_home": m.get("ht_home"), "ht_away": m.get("ht_away"),
                    "corner_home": m.get("corner_home"), "corner_away": m.get("corner_away"),
                    "odd_1": m.get("odd_1"), "odd_x": m.get("odd_x"), "odd_2": m.get("odd_2"),
                    "odd_over": m.get("odd_over"), "odd_under": m.get("odd_under"),
                    "forca": {},
                }
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                github_storage.push_file_bg(path, f"forca_history/{date_str}_{event_id}.json")
                print(f"[forca-backup] Salvo: {date_str}_{event_id}.json ({m.get('home')} x {m.get('away')})")
        except Exception as e:
            print(f"[forca-backup] Erro no jogo {event_id}: {e}")
        finally:
            with _forca_backup_queued_lock:
                _forca_backup_queued.discard(event_id)
            _forca_backup_queue.task_done()
        time.sleep(_FORCA_BACKUP_DELAY)


def _forca_backup_scan_loop():
    _github_sync_done.wait(timeout=120)
    while True:
        try:
            data = _painel_fetch_matches_flashscore()
            date_str = datetime.now().strftime("%Y-%m-%d")
            added = 0
            for lg in data.get("leagues", []):
                for m in lg["matches"]:
                    if m.get("time") != "Encerrado":
                        continue
                    event_id = str(m.get("event_id") or "")
                    if not event_id:
                        continue
                    if os.path.exists(_forca_backup_path(date_str, event_id)):
                        continue
                    with _forca_backup_queued_lock:
                        if event_id in _forca_backup_queued:
                            continue
                        _forca_backup_queued.add(event_id)
                    _forca_backup_queue.put((date_str, m))
                    added += 1
            if added:
                print(f"[forca-backup] {added} jogo(s) finalizado(s) novo(s) enfileirado(s)")
        except Exception as e:
            print(f"[forca-backup] Erro escaneando jogos finalizados: {e}")
        time.sleep(_FORCA_BACKUP_SCAN_INTERVAL)


def _painel_odds_prewarm_loop():
    """Mantém _today2_odds_snapshot_cache sempre quente em background, LONGE
    de qualquer thread de requisição. _painel_fetch_matches_flashscore só LÊ
    esse cache (nunca chama _today2_odds_snapshot() direto — testado
    localmente: ~70s+ em cache frio pra cobrir os jogos candidatos, o
    suficiente pra travar uma das 4 threads do gunicorn por tempo demais,
    mesma causa dos 2 apagões desta sessão). Sem esse loop rodando à parte,
    o Painel só teria odds quando alguém, por acaso, tivesse acabado de usar
    o Filtro de Metodologias (o único outro lugar que aquece esse cache)."""
    _github_sync_done.wait(timeout=120)
    while True:
        try:
            _today2_odds_snapshot(force=True)
        except Exception as e:
            print(f"[painel-odds-prewarm] Erro: {e}")
        time.sleep(_TODAY2_ODDS_SNAPSHOT_TTL)


def _painel_fetch_matches(force=False, date_str=None):
    """date_str: "YYYY-MM-DD" opcional — qualquer dia navegável pelo calendário
    do BetExplorer. None/"" = dia atual (comportamento igual ao botão "Hoje")."""
    now = time.time()
    cache_key = date_str or "today"
    with _painel_lock:
        cached = _painel_cache.get(cache_key)
        if not force and cached is not None and (now - cached["ts"]) < _PAINEL_CACHE_TTL:
            return cached["data"]

    date_params = None
    if date_str:
        try:
            y, mo, d = date_str.split("-")
            date_params = {"year": y, "month": mo, "day": d}
        except ValueError:
            date_params = None

    # Busca de rede fora do lock — mesmo motivo de _painel_fetch_matches_nowgoal
    # (que usa esse MESMO _painel_lock): uma trava aqui dentro do lock travava
    # o site inteiro, não só o Painel. Ver nota lá.
    try:
        html_1x2 = _be_fetch_bettype_html("1x2", date_params)
        html_ou  = _be_fetch_bettype_html("ou", date_params)
        matches_1x2, leagues_order = _be_parse_bettype(html_1x2)
        matches_ou, _ = _be_parse_bettype(html_ou)
    except Exception as e:
        # BetExplorer fora do ar/bloqueando (429) — em vez de deixar a página vazia
        # com erro, serve o último resultado que já funcionou (mesmo vencido), com
        # um aviso de que os dados podem estar desatualizados. Só mostra erro puro
        # se nunca conseguimos buscar nada pra esse dia ainda.
        with _painel_lock:
            cached = _painel_cache.get(cache_key)
        if cached is not None:
            stale = dict(cached["data"])
            stale["stale"] = True
            stale["stale_error"] = str(e)
            return stale
        return {"error": str(e), "leagues": [], "updated_at": now, "date": date_str}

    leagues_map = {}
    for lg in leagues_order:
        leagues_map.setdefault(lg["key"], dict(lg, matches=[]))

    for event_id, m in matches_1x2.items():
        lg = leagues_map.get(m["league_key"])
        if lg is None:
            continue
        ou = matches_ou.get(event_id)
        odds = m["odds"] + [None] * (3 - len(m["odds"]))
        ou_odds = (ou["odds"] if ou else []) + [None, None]
        lg["matches"].append({
            "event_id": event_id,
            "time": m["status_text"],
            "home": m["home"], "away": m["away"],
            "home_logo": m.get("home_logo"), "away_logo": m.get("away_logo"),
            "score_home": m["score_home"], "score_away": m["score_away"],
            "match_url": (BETEXPLORER_BASE + m["match_url"]) if m["match_url"] else None,
            "odd_1": odds[0], "odd_x": odds[1], "odd_2": odds[2],
            "ou_line": ou["line"] if ou else None,
            "odd_over": ou_odds[0], "odd_under": ou_odds[1],
            "ts": m["ts"],
        })

    leagues = [lg for lg in leagues_map.values() if lg["matches"]]
    for lg in leagues:
        lg["matches"].sort(key=lambda x: x["ts"])

    data = {"leagues": leagues, "updated_at": now, "date": date_str}
    with _painel_lock:
        _painel_cache[cache_key] = {"ts": now, "data": data}
    return data


_FS_STATUS_MAP = {"1": "Agendado", "2": "Ao vivo", "3": "Encerrado"}
_FS_NOT_STARTED = "1"
# Teto de segurança pro lote de placar do intervalo — mesmo espírito de
# _BT2_MATCHES_MAX_CANDIDATOS.
_PAINEL_FS_HT_MAX = 80

# Placar do intervalo dos jogos de hoje, mantido quente em BACKGROUND (ver
# _painel_ht_prewarm_loop) — igual ao motivo das odds (_painel_odds_prewarm_
# loop): testado localmente, buscar HT de ~80 jogos (1 request cada) demora
# demais pra rodar na hora. _painel_fetch_matches_flashscore só LÊ esse
# cache, nunca busca isso na hora — senão vira mais um jeito de travar uma
# das 4 threads do gunicorn por tempo demais. Usa _painel_ht_pool (dedicado,
# ver definição perto de _fs_event_pool) em vez do pool geral — descoberto
# 2026-09-02 que dividir esse lote com Backtest/H2H/odds/ranking deixava a
# cobertura real bem abaixo do teto de 80 (só 4/209 em produção).
_painel_ht_cache = {}
_painel_ht_lock = threading.Lock()


def _painel_ht_prewarm_loop():
    _github_sync_done.wait(timeout=120)
    while True:
        try:
            # _fs_all_matches() sozinho só cobre 1 dia do feed (UTC) — igual ao
            # bug já corrigido no Painel principal (ver _fs_all_matches_brt),
            # a maior parte dos jogos "de hoje" em horário BRT cai no dia UTC
            # anterior ou seguinte. Usar só _fs_all_matches() aqui fazia esse
            # prewarm enxergar uns 7 jogos "Encerrado" de ~210 reais — achado
            # 2026-09-02 junto com a correção do pool acima.
            fs_matches = _fs_all_matches_brt(_brt_today())
            ids = [m["id"] for m in fs_matches if m.get("status") != _FS_NOT_STARTED and m.get("id")][:_PAINEL_FS_HT_MAX]

            def _fetch_ht(eid):
                try:
                    return eid, _fs_half_time(eid)
                except Exception:
                    return eid, None

            novo = {}
            for eid, ht in _painel_ht_pool.map(_fetch_ht, ids):
                if ht:
                    novo[eid] = ht
            with _painel_ht_lock:
                _painel_ht_cache.clear()
                _painel_ht_cache.update(novo)
        except Exception as e:
            print(f"[painel-ht-prewarm] Erro: {e}")
        time.sleep(_PAINEL_CACHE_TTL)


def _fs_extract_odds_fields(markets):
    """Extrai os campos de odds no formato que o Painel Principal já espera
    (odd_1/x/2, ou_line/odd_over/odd_under, ah_line/ah_home/ah_away) a partir
    do dict de mercados que _today2_odds_snapshot/_fs_odds_all_markets_any_
    bookmaker já devolvem. Pega a linha de Over/Under mais próxima de 2.5 e a
    de Handicap Asiático mais próxima de 0 (linha "principal") — mesmo
    critério já usado em _today2_classify_match/_jogo_medias_gerais_compute
    pra escolher 1 linha entre várias oferecidas."""
    def _num(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _melhor_linha(mercado, alvo):
        melhor, melhor_dist = None, None
        for op in (mercado.get("opportunities") or []):
            linha = _num((op.get("handicap") or {}).get("value"))
            if linha is None:
                continue
            dist = abs(linha - alvo)
            if melhor_dist is None or dist < melhor_dist:
                melhor, melhor_dist = op, dist
        return melhor

    m1x2 = markets.get("1x2") or {}
    ou = _melhor_linha(markets.get("over_under") or {}, 2.5) or {}
    ah = _melhor_linha(markets.get("handicap_asiatico") or {}, 0.0) or {}

    return {
        "odd_1": _num((m1x2.get("home") or {}).get("value")),
        "odd_x": _num((m1x2.get("draw") or {}).get("value")),
        "odd_2": _num((m1x2.get("away") or {}).get("value")),
        "ou_line": _num((ou.get("handicap") or {}).get("value")),
        "odd_over": _num((ou.get("over") or {}).get("value")),
        "odd_under": _num((ou.get("under") or {}).get("value")),
        "ah_line": _num((ah.get("handicap") or {}).get("value")),
        "ah_home": _num((ah.get("home") or {}).get("value")),
        "ah_away": _num((ah.get("away") or {}).get("value")),
    }


def _power_icon(value, higher_is_better, bom, ruim):
    """Porta de _today2PowerIcon (index.html) — ✓ bom / △ meio-termo / ▽ ruim."""
    bom_cond = value >= bom if higher_is_better else value <= bom
    ruim_cond = value <= ruim if higher_is_better else value >= ruim
    if bom_cond:
        return "✓"
    if ruim_cond:
        return "▽"
    return "△"


def _compute_power_index(standings_rows):
    """Porta de _today2PowerRankingRows (index.html) — Índice de Ataque/Defesa
    de cada time = gols marcados/sofridos por jogo ÷ média da liga. Ícones só com
    >= 5 jogos na tabela (amostra pequena demais falseia); pos/gf_avg/ga_avg saem
    desde o 1º jogo.
    Retorna {team_normalizado: {"ataque": icone, "defesa": icone, "pos": posição,
    "gf_avg": gols marcados/jogo, "ga_avg": gols sofridos/jogo}}.
    "pos"/"gf_avg"/"ga_avg" (2026-09-13, pedido do usuário: mostrar posição e
    média de gols da temporada ao lado do nome do time) reaproveitam o MESMO
    standings_rows já buscado aqui pro Ataque/Defesa — zero busca nova. A
    média pedida era "últimos 5 jogos", mas isso exigiria busca extra por
    time (mesma categoria de custo do H2H) — usuário topou a alternativa
    grátis (média da temporada inteira, mesma fonte que já alimenta
    Ataque/Defesa) em vez disso."""
    parsed = []
    for r in standings_rows:
        try:
            gf_str, ga_str = (r.get("gols") or "0:0").split(":")
            gf, ga = float(gf_str), float(ga_str)
        except (ValueError, AttributeError):
            continue
        try:
            jogos = int(r.get("jogos") or 0)
        except (ValueError, TypeError):
            jogos = 0
        if jogos <= 0:
            continue
        parsed.append({"team": r.get("team", ""), "jogos": jogos, "gf": gf, "ga": ga, "pos": r.get("pos")})
    if not parsed:
        return {}
    media_gf = sum(p["gf"] / p["jogos"] for p in parsed) / len(parsed)
    media_ga = sum(p["ga"] / p["jogos"] for p in parsed) / len(parsed)
    out = {}
    for p in parsed:
        if not p["team"]:
            continue
        # Posição e média de gols aparecem desde o 1º jogo (2026-09-19: no começo da
        # temporada a liga inteira ficava sem NADA por ter times com 4 jogos, ex:
        # Bundesliga em 4 rodadas); só os ícones de Ataque/Defesa, que comparam com a
        # média da liga, continuam exigindo 5 jogos (amostra menor falseia).
        if p["jogos"] >= 5:
            ataque = (p["gf"] / p["jogos"]) / media_gf if media_gf > 0 else 0
            defesa = (p["ga"] / p["jogos"]) / media_ga if media_ga > 0 else 0
            ic_ataque = _power_icon(ataque, True, 1.05, 0.90)
            ic_defesa = _power_icon(defesa, False, 0.95, 1.10)
        else:
            ic_ataque = ic_defesa = None
        out[p["team"].strip().lower()] = {
            "ataque": ic_ataque,
            "defesa": ic_defesa,
            "pos": p["pos"] or None,
            "gf_avg": round(p["gf"] / p["jogos"], 1),
            "ga_avg": round(p["ga"] / p["jogos"], 1),
        }
    return out


# Palavras que indicam competição de mata-mata/copa (sem tabela de liga que
# valha a pena mostrar como "posição") — pedido do usuário (2026-09-13):
# "a frente do nome das equipes adicione a posição na tabela se for copa nao
# adicione nada". Heurística por nome (a fonte não manda um campo explícito
# "é copa") — cobre os padrões vistos nas ligas/copas continentais e
# nacionais mais comuns; se aparecer um caso não coberto, é só adicionar a
# palavra aqui.
_CUP_NAME_KEYWORDS = (
    "copa", "cup", "taça", "taca", "trophy", "troféu", "trofeu",
    "playoff", "play-off", "play off",
    "champions league", "liga dos campeões", "liga dos campeoes",
    "libertadores", "sudamericana", "recopa", "supercopa", "super copa",
    "confederações", "confederacoes", "confederations",
)


def _is_cup_competition(liga_name):
    nome = (liga_name or "").strip().lower()
    return any(kw in nome for kw in _CUP_NAME_KEYWORDS)


# Ataque/Defesa (Power Ranking) de cada time. Chave "time|país" (não só o
# nome do time) pra reduzir colisão entre times de mesmo nome em países
# diferentes — ainda pode colidir entre 2 competições do MESMO país com time
# de nome igual (raro, aceito).
_painel_power_cache = {}
_painel_power_lock = threading.Lock()

# Persistência do cache (sobrevive a redeploy do Railway, que apaga o disco
# local) — mesmo padrão do _shotmap_live_cache: arquivo local pequeno restaurado
# do GitHub no boot (ver github_storage.sync_on_startup) e salvo de novo a cada
# ciclo. Sem isso, os ícones de Ataque/Defesa do Painel ficavam em branco por
# ~15-20min toda vez que um push disparava um redeploy (achado com o usuário,
# 2026-08-31, depois de 2 deploys seguidos zerarem o cache na mesma sessão).
_PAINEL_POWER_CACHE_FILE = os.path.join(DATA_DIR, ".painel_power_cache.json")

def _load_painel_power_cache() -> dict:
    try:
        if os.path.exists(_PAINEL_POWER_CACHE_FILE):
            with open(_PAINEL_POWER_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"[painel-power] Cache restaurado: {len(data)} time(s)")
            return data
    except Exception as e:
        print(f"[painel-power] Erro ao carregar cache: {e}")
    return {}

def _save_painel_power_cache(cache: dict):
    try:
        with open(_PAINEL_POWER_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception as e:
        print(f"[painel-power] Erro ao salvar cache: {e}")


def _painel_power_cache_load_once():
    """Carrega o cache persistido de Power Ranking (ícones Ataque/Defesa) uma
    única vez no boot, sem loop recorrente. Antes existia um
    `_painel_power_prewarm_loop` que recalculava isso do zero a cada 15min
    pra ~300 ligas, usando o mesmo pool de 12 workers (_fs_event_pool) que a
    Ao Vivo usa em tempo real pra odds/H2H/HT — competia direto com a Ao Vivo
    e ficou identificado como a maior fonte de congestionamento que sobrou
    depois dos outros 2 fixes de performance dessa sessão (lazy Tendências +
    filtro de competições). Removido a pedido do usuário (2026-09-12). Os
    ícones continuam sendo atualizados por _painel_shift_prewarm_sweep (3x/
    dia, só pros jogos do turno que vai começar, pool dedicado) — passa de
    "atualiza a cada 15min pra ~300 ligas" pra "atualiza algumas horas antes
    do jogo pras ligas que importam agora", bem mais barato pro mesmo efeito
    prático (ícone de Ataque/Defesa não muda de uma hora pra outra)."""
    _github_sync_done.wait(timeout=120)
    with _painel_power_lock:
        _painel_power_cache.update(_load_painel_power_cache())


# ── Pré-carga por TURNO da coluna "Forma" (Power Ranking) + odds 1X2 ─────────
# Pedido do usuário (2026-09-04): mesmo com _painel_power_prewarm_loop/
# _painel_odds_prewarm_loop já rodando continuamente, muita partida ainda
# aparecia com "Forma"/1X2 em "-" quando o horário dela chegava — causa: odds
# só entram no cache quando o jogo cai nos _BT2_MATCHES_MAX_CANDIDATOS=60 mais
# próximos por horário (_bt2_matches_candidatos), então um dia com muitos
# jogos deixa partidas de daqui a poucas horas fora dessa janela até quase a
# hora do apito. Em vez de tentar cobrir o dia inteiro de uma vez (mesma causa
# dos 2 apagões anteriores), o usuário propôs dividir o dia em turnos e
# pré-carregar cada um com ~1h de antecedência — carga fica pequena e
# previsível (só as partidas daquele turno) em vez de "todas as futuras".
# Horários fixos em BRT (fuso sem DST, ver _BRT_OFFSET).
#
# Reduzido de 5 pra 3 turnos (2026-09-08, pedido do usuário investigando
# custo alto do Railway — Memory Usage era 63% da fatura do mês) — corta
# ~40% da carga dessa parte específica mantendo a granularidade fina só no
# horário de pico (17h-05h BRT, ver [[feedback_ao_vivo_prioridade]]), onde a
# pré-carga com antecedência importa mais; o resto do dia (05h-17h, tráfego
# baixo) vira 1 turno só, mais largo.
_PAINEL_SHIFTS = [
    # (hora/min do gatilho, hora/min de início da janela, hora/min de fim da
    #  janela, início da janela cai no dia seguinte ao gatilho?, fim da
    #  janela cai no dia seguinte ao gatilho?)
    (5, 0,   5, 0,  17, 0, False, False),
    (16, 0,  17, 1, 21, 0, False, False),
    (20, 0,  21, 1, 4,  59, False, True),
]

_painel_shift_odds_cache = {}  # event_id -> markets (1x2), acumulado o dia todo
_painel_shift_odds_lock = threading.Lock()
_painel_shift_done = set()  # {(data_do_gatilho_iso, índice_do_turno)} já executados

_EPOCH = datetime(1970, 1, 1)


def _painel_shift_brt_to_ts(brt_naive_dt):
    return int((brt_naive_dt - _BRT_OFFSET - _EPOCH).total_seconds())


def _painel_shift_prewarm_sweep(window_start_brt, window_end_brt):
    ts_start = _painel_shift_brt_to_ts(window_start_brt)
    ts_end = _painel_shift_brt_to_ts(window_end_brt)

    dias = {window_start_brt.date(), window_end_brt.date()}
    fs_matches, seen_ids = [], set()
    for dia in dias:
        try:
            for m in _fs_all_matches_brt(dia):
                if m.get("id") not in seen_ids:
                    seen_ids.add(m.get("id"))
                    fs_matches.append(m)
        except Exception as e:
            print(f"[painel-shift-prewarm] Erro buscando jogos de {dia}: {e}")

    candidatos = []
    for m in fs_matches:
        try:
            ts = int(m.get("kickoff_ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts and ts_start <= ts < ts_end:
            candidatos.append(m)

    if not candidatos:
        print(f"[painel-shift-prewarm] Janela {window_start_brt.strftime('%H:%M')}-"
              f"{window_end_brt.strftime('%H:%M')}: nenhum jogo, pulando")
        return

    print(f"[painel-shift-prewarm] Janela {window_start_brt.strftime('%H:%M')}-"
          f"{window_end_brt.strftime('%H:%M')}: {len(candidatos)} jogo(s) — "
          f"buscando odds 1X2 e Power Ranking")

    # Ordem (2026-09-19): primeiro o Power Ranking (posição, média de gols e ícones,
    # ~1 requisição por liga) e SÓ DEPOIS as odds 1X2 de cada jogo (centenas de
    # requisições). Assim, logo após um restart, posição/gols voltam em ~1 min em vez
    # de esperar as odds da janela inteira terminarem.
    reps_by_league = {}
    for m in candidatos:
        lk = f"{m.get('pais', '')}|{m.get('liga', '')}"
        reps_by_league.setdefault(lk, (m.get("pais", ""), m.get("id")))

    def _fetch_power_one(item):
        country, event_id = item
        try:
            rows = _fs_standings(event_id)
            return country, _compute_power_index(rows)
        except Exception:
            return country, {}

    total = 0
    for country, indices in _fs_event_pool.map(_fetch_power_one, reps_by_league.values()):
        if not indices:
            continue
        pais_norm = (country or "").strip().lower()
        with _painel_power_lock:
            for team_norm, icons in indices.items():
                _painel_power_cache[f"{team_norm}|{pais_norm}"] = icons
        total += len(indices)
    if total:
        with _painel_power_lock:
            snapshot = dict(_painel_power_cache)
        _save_painel_power_cache(snapshot)
        github_storage.push_file_bg(_PAINEL_POWER_CACHE_FILE, ".painel_power_cache.json")

    def _fetch_odds_one(m):
        try:
            _, markets = _fs_odds_all_markets_any_bookmaker(
                m["id"], pool=_bt2_matches_market_pool, markets_wanted=["1x2"])
            return m["id"], markets
        except Exception:
            return m["id"], None

    for eid, markets in _bt2_matches_pool.map(_fetch_odds_one, candidatos):
        if markets:
            with _painel_shift_odds_lock:
                _painel_shift_odds_cache[eid] = markets

    print(f"[painel-shift-prewarm] Turno concluído: {len(candidatos)} jogo(s), "
          f"{total} time(s) com Power Ranking atualizado")


def _painel_shift_prewarm_loop():
    """Roda os 5 turnos definidos em _PAINEL_SHIFTS. Reavalia a cada 2min (em
    vez de dormir até o próximo horário exato) pra sobreviver a restart do
    processo sem perder o turno do dia — se o processo caiu e voltou depois do
    gatilho, ainda dá tempo de rodar com atraso (checa gatilhos de HOJE e de
    ONTEM, por causa do turno que atravessa a meia-noite)."""
    global _painel_shift_done
    _github_sync_done.wait(timeout=120)
    while True:
        try:
            now_brt = datetime.utcnow() + _BRT_OFFSET
            for dia_offset in (-1, 0):
                dia = (now_brt + timedelta(days=dia_offset)).date()
                for idx, (th, tm, wsh, wsm, weh, wem, wstart_next, wend_next) in enumerate(_PAINEL_SHIFTS):
                    trigger_dt = now_brt.replace(
                        year=dia.year, month=dia.month, day=dia.day,
                        hour=th, minute=tm, second=0, microsecond=0)
                    if now_brt < trigger_dt or now_brt - trigger_dt > timedelta(hours=6):
                        continue
                    key = (dia.isoformat(), idx)
                    if key in _painel_shift_done:
                        continue
                    if idx == 0:
                        # Bug real (2026-09-04 a 2026-09-08, achado investigando
                        # custo do Railway): _painel_shift_odds_cache nunca era
                        # limpo, crescia pra sempre (um event_id novo por jogo,
                        # todo santo dia) — vazamento de memória puro, sem
                        # ganho nenhum (jogo de dias atrás não serve mais pra
                        # nada aqui). Limpa 1x por dia, no 1º turno (05:00).
                        with _painel_shift_odds_lock:
                            _painel_shift_odds_cache.clear()
                    win_start_day = dia + timedelta(days=1) if wstart_next else dia
                    win_end_day = dia + timedelta(days=1) if wend_next else dia
                    window_start = now_brt.replace(
                        year=win_start_day.year, month=win_start_day.month, day=win_start_day.day,
                        hour=wsh, minute=wsm, second=0, microsecond=0)
                    window_end = now_brt.replace(
                        year=win_end_day.year, month=win_end_day.month, day=win_end_day.day,
                        hour=weh, minute=wem, second=0, microsecond=0)
                    _painel_shift_prewarm_sweep(window_start, window_end)
                    _painel_shift_done.add(key)
                    # limpa marcações com mais de 2 dias pra não crescer sem limite
                    _painel_shift_done = {
                        k for k in _painel_shift_done
                        if (now_brt.date() - date.fromisoformat(k[0])).days <= 2}
        except Exception as e:
            print(f"[painel-shift-prewarm] Erro: {e}")
        time.sleep(120)


def _painel_fetch_matches_flashscore(force=False, date_str=None):
    """Versão Flashscore/Soccerway — substitui o NowGoal (2026-08-30). Motivo:
    o feed do NowGoal não tem paginação e, além do caso raro de vir vazio (já
    tinha proteção pra isso, ver stale abaixo), às vezes simplesmente PARA de
    listar um jogo específico no meio do dia, sem aviso — o jogo some do
    Painel sem nenhum erro pra investigar. O Flashscore cobre muito mais jogos
    (checado ao vivo: 1036 vs 414 no mesmo dia) e mantém jogos encerrados na
    listagem. Mesmo contrato de saída de sempre (mesmos campos por jogo) —
    quem consome isso (linha do Painel, filtro de odds, exports, Ao Vivo)
    não precisa mudar nada.

    date_str (opcional, "YYYY-MM-DD"): dia diferente de hoje — ex: usado pelo
    seletor "Amanhã" do Painel Principal (pedido do usuário, 2026-08-31). É um
    dia CIVIL DE BRASÍLIA (o seletor no front monta essa string com o relógio
    local do navegador, que é o do próprio usuário/BR) -- resolvido via
    _fs_all_matches_brt, que já filtra pelo horário real de cada jogo em vez
    de confiar no agrupamento por dia (UTC) do feed. HT/odds/Power Ranking
    (caches quentes só de hoje) ficam vazios pra qualquer outro dia —
    aceitável, já que odds/ícone de forma não fazem sentido pra um jogo já
    encerrado há mais de um dia."""
    now = time.time()
    cache_key = date_str or "today"
    with _painel_lock:
        cached = _painel_cache.get(cache_key)
        if not force and cached is not None and (now - cached["ts"]) < _PAINEL_CACHE_TTL:
            return cached["data"]

    target_date = _brt_today()
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = _brt_today()

    # Mesma lição do NowGoal: toda busca de rede roda FORA do lock (ver
    # comentário grande na versão antiga acima) — travar aqui travaria as 4
    # threads do gunicorn inteiras atrás desse lock.
    try:
        fs_matches = _fs_all_matches_brt(target_date)
    except Exception as e:
        with _painel_lock:
            cached = _painel_cache.get(cache_key)
        if cached is not None:
            stale = dict(cached["data"])
            stale["stale"] = True
            stale["stale_error"] = str(e)
            return stale
        return {"error": str(e), "leagues": [], "updated_at": now, "date": date_str}

    with _painel_lock:
        cached = _painel_cache.get(cache_key)
        if not fs_matches and cached is not None and cached["data"].get("leagues"):
            stale = dict(cached["data"])
            stale["stale"] = True
            stale["stale_error"] = "Flashscore devolveu feed vazio (possível bloqueio temporário)"
            return stale

        # Incidente real em produção (2026-09-03/04): o feed não veio LITERALMENTE
        # vazio (a proteção acima não pegou), só devolveu uma fração minúscula do
        # normal — 22 partidas contra as 500+ de menos de 1min antes, ZERO delas
        # "Encerrado" — e isso silenciosamente derrubou "Todos os jogos" no
        # celular, "Baixar leitura" (achava 0 jogo pra exportar), a checagem de
        # acerto da aba Lista e o cálculo do Raio-X, sem nenhum aviso — o usuário
        # só percebeu pelo prejuízo. Causa provável: _fs_all_matches_brt junta 2
        # dias UTC (ver comentário lá) e cada um tem seu próprio cache de 30s
        # (_fs_live_cache) que, se a 1ª busca daquele dia falhar logo após um
        # restart/redeploy (processo novo, sem fallback prévio pra usar), volta
        # vazio SEM levantar exceção — um dos dois lados do "hoje BRT" pode sumir
        # inteiro (tipicamente o que carrega a maioria dos jogos já ao vivo/
        # encerrados) enquanto o outro (dia que acabou de começar em UTC, quase
        # sem jogo ainda) segue voltando normal. `cached_count >= 40` evita
        # disparar em horários com poucos jogos de verdade (madrugada) — só
        # dispara numa QUEDA REAL em menos de 1min, que não acontece por causa
        # natural (jogos não desaparecem aos montes de repente).
        if cached is not None:
            cached_count = sum(len(lg.get("matches") or []) for lg in (cached["data"].get("leagues") or []))
            if cached_count >= 40 and len(fs_matches) < cached_count * 0.3:
                stale = dict(cached["data"])
                stale["stale"] = True
                stale["stale_error"] = (
                    f"Flashscore devolveu só {len(fs_matches)} partida(s) — bem menos que as "
                    f"{cached_count} de ~1min atrás (provável falha parcial do feed, não fim "
                    f"real do dia). Mantendo o último resultado bom até normalizar."
                )
                print(f"[painel-flashscore] ⚠ Feed suspeito: {len(fs_matches)} partida(s) vs "
                      f"{cached_count} em cache — usando cache antigo em vez de sobrescrever.")
                return stale

    # Placar do intervalo — só LÊ o cache que _painel_ht_prewarm_loop mantém
    # quente em background (ver comentário lá em cima do motivo).
    with _painel_ht_lock:
        ht_by_id = dict(_painel_ht_cache)

    # Odds — LÊ (sem calcular) o mesmo snapshot cacheado que o Filtro de
    # Metodologias/Backtest CS já usa. Importante: NUNCA chama
    # _today2_odds_snapshot() aqui — testado localmente e essa função, em
    # cache frio, demora bem mais que 1 minuto pra fazer 60 jogos x 6
    # mercados x até 3 casas de apostas (o próprio comentário dela já avisa
    # "~70s"). Isso rodando na THREAD DA REQUISIÇÃO do Painel (só 4 threads
    # pro site inteiro, mesma causa dos 2 apagões desta sessão) seria um
    # terceiro incidente na certa. Em vez disso só lê o que já estiver pronto
    # (pode vir vazio logo depois de um restart) — quem mantém isso quente de
    # verdade é o _painel_odds_prewarm_loop, rodando em background.
    odds_snapshot = _today2_odds_snapshot_cache.get("data") or []
    odds_by_id = {m["id"]: markets for m, markets in odds_snapshot if markets}
    # Completa com o cache do pré-carregamento por turno (_painel_shift_prewarm_
    # loop) — cobre jogos que ainda não entraram nos 60 mais próximos do
    # snapshot acima, mas já tiveram o 1X2 buscado com antecedência pro turno
    # deles. setdefault: o snapshot de 5min (mais fresco, 6 mercados) tem
    # prioridade quando os dois têm o mesmo jogo.
    with _painel_shift_odds_lock:
        for eid, markets in _painel_shift_odds_cache.items():
            odds_by_id.setdefault(eid, markets)

    # Ataque/Defesa (Power Ranking) — mesma ideia de só LER o cache quente em
    # background (ver _painel_power_cache_load_once/_painel_shift_prewarm_
    # sweep), nunca calcular na hora.
    with _painel_power_lock:
        power_snapshot = dict(_painel_power_cache)

    # Odds AO VIVO (movimento em tempo real) — só LÊ o cache que
    # _live_odds_prewarm_loop mantém quente em background (ver comentário lá).
    with _live_odds_lock:
        live_odds_by_id = dict(_live_odds_cache)

    matches = []
    for m in fs_matches:
        status_code = m.get("status")
        not_started = status_code == _FS_NOT_STARTED
        eid = m.get("id")
        ht = ht_by_id.get(eid)
        odds_fields = _fs_extract_odds_fields(odds_by_id.get(eid) or {})
        try:
            ts = int(m.get("kickoff_ts") or 0)
        except (TypeError, ValueError):
            ts = 0

        pais_norm = (m.get("pais") or "").strip().lower()
        casa_power = power_snapshot.get(f"{(m.get('home') or '').strip().lower()}|{pais_norm}", {})
        fora_power = power_snapshot.get(f"{(m.get('away') or '').strip().lower()}|{pais_norm}", {})
        # Posição na tabela (2026-09-13) — some de propósito em copa/mata-mata
        # (_is_cup_competition, heurística por nome da liga: "posição" não
        # faz sentido numa chave eliminatória) e quando o Power Ranking ainda
        # não calculou esse time (mesmo "pos" da tabela, já vem ou não vem
        # junto do ataque/defesa acima, sem busca extra).
        e_copa = _is_cup_competition(m.get("liga", ""))
        casa_pos = None if e_copa else casa_power.get("pos")
        fora_pos = None if e_copa else fora_power.get("pos")
        # Média de gols da temporada (2026-09-13, pedido do usuário) — mesmo
        # gate de copa/mata-mata acima (ver comentário) e mesma fonte
        # (_compute_power_index), zero busca nova.
        casa_gf_avg = None if e_copa else casa_power.get("gf_avg")
        casa_ga_avg = None if e_copa else casa_power.get("ga_avg")
        fora_gf_avg = None if e_copa else fora_power.get("gf_avg")
        fora_ga_avg = None if e_copa else fora_power.get("ga_avg")

        matches.append({
            "event_id": eid,
            "league_key": f"{m.get('pais', '')}|{m.get('liga', '')}",
            "league_name": m.get("liga", ""), "country": m.get("pais", ""),
            "time": _FS_STATUS_MAP.get(status_code, status_code),
            "minute": None,  # Flashscore não manda minuto corrido no feed em lote
            "home": m.get("home"), "away": m.get("away"),
            "home_logo": m.get("escudo_casa"), "away_logo": m.get("escudo_fora"),
            "score_home": None if not_started else m.get("home_score"),
            "score_away": None if not_started else m.get("away_score"),
            "ht_home": (ht or {}).get("home"), "ht_away": (ht or {}).get("away"),
            "corner_home": None, "corner_away": None,  # sem fonte em lote no Flashscore
            "match_url": None,
            "live_odds": live_odds_by_id.get(eid),
            "casa_ataque_icon": casa_power.get("ataque"), "casa_defesa_icon": casa_power.get("defesa"),
            "fora_ataque_icon": fora_power.get("ataque"), "fora_defesa_icon": fora_power.get("defesa"),
            "casa_pos": casa_pos, "fora_pos": fora_pos,
            "casa_gf_avg": casa_gf_avg, "casa_ga_avg": casa_ga_avg,
            "fora_gf_avg": fora_gf_avg, "fora_ga_avg": fora_ga_avg,
            **odds_fields,
            "ts": ts,
        })

    # Mesmos links diretos pro Betfair Exchange / Bolsa de Aposta que a
    # versão NowGoal já anexava (via RadarFutebol, casando por nome dos
    # times) — nunca derruba o carregamento do painel se o RadarFutebol
    # estiver fora do ar, só fica sem os links dessa vez.
    try:
        for m in matches:
            lb, lba, lr = _find_radar_links(m.get("home"), m.get("away"), m.get("ts"))
            m["link_betfair"] = lb
            m["link_bolsa"] = lba
            m["link_radar"] = lr
    except Exception as e:
        print(f"[radar-links] Erro anexando links: {e}")

    # Minuto ao vivo — o feed em lote do Flashscore (_fs_all_matches) não manda
    # minuto corrido, só o status genérico "Ao vivo" (ver comentário em
    # "minute" acima). O UniScore (mesma fonte que já alimenta a aba Ao Vivo
    # top-level) manda — casa pelo nome dos times, mesma técnica de
    # _find_radar_links, só pros jogos que já estão "Ao vivo" aqui. Pedido do
    # usuário (2026-09-01): a aba "AO VIVO" do Painel mostrava o status mas
    # nunca o minuto.
    #
    # IMPORTANTE: só LÊ _uniscore_full_cache se estiver quente (<90s, mesmo
    # TTL que _radar_fetch_live_matches já usa) — NUNCA chama
    # _radar_fetch_live_matches() aqui. Testado localmente: essa função
    # escaneia até 7 locales x 5 páginas, 12s de timeout cada — em cache frio
    # (ex: logo após um deploy) isso trava minutos dentro da função mais
    # usada do site inteiro, mesma causa dos apagões já sofridos nesta sessão
    # com _today2_odds_snapshot()/HT em lote (ver comentários delas). Cache
    # frio aqui só significa "sem minuto por enquanto" — não vale o risco.
    # TTL segue o mesmo valor de _radar_fetch_live_matches (30s desde
    # 2026-09-01) — se um mudar, o outro tem que mudar junto.
    try:
        if time.time() - _uniscore_full_cache["ts"] < 30:
            live_uniscore = _uniscore_full_cache["live"] or []
            for m in matches:
                if m["time"] != "Ao vivo":
                    continue
                found = next((ev for ev in live_uniscore
                              if _name_match(m.get("home") or "", ev.get("casa") or "")
                              and _name_match(m.get("away") or "", ev.get("fora") or "")), None)
                minuto = found.get("minuto") if found else None
                # _uniscore_minuto já devolve com o apóstrofo (ex: "28'") — o
                # frontend (painel-match-minute) acrescenta o dele próprio,
                # então tira aqui pra não sair "28''" duplicado.
                if minuto:
                    m["minute"] = minuto.rstrip("'")
    except Exception as e:
        print(f"[painel-minuto] Erro anexando minuto ao vivo: {e}")

    leagues_map = {}
    for m in matches:
        key = m["league_key"]
        lg = leagues_map.setdefault(key, {
            "key": key, "country": m["country"], "league_name": m["league_name"],
            "flag_url": "", "matches": [],
        })
        lg["matches"].append({k: v for k, v in m.items() if k not in ("league_key", "league_name", "country")})

    leagues = [lg for lg in leagues_map.values() if lg["matches"]]
    for lg in leagues:
        lg["matches"].sort(key=lambda x: x["ts"])

    data = {"leagues": leagues, "updated_at": now, "date": date_str}
    with _painel_lock:
        _painel_cache[cache_key] = {"ts": now, "data": data}
    return data


@app.route("/api/painel/matches")
def api_painel_matches():
    force = request.args.get("force") == "1"
    date_str = request.args.get("date") or None  # "YYYY-MM-DD"
    return jsonify(_painel_fetch_matches_flashscore(force=force, date_str=date_str))


# ── Widget de análise — "Últimos resultados" de cada time (BetExplorer) ────────
# Essa seção do BetExplorer só é montada via JS depois que a página carrega (o
# endpoint /gres/ajax/match-content.php exige um token "ts" gerado no client,
# sem padrão fixo pra reproduzir com um simples requests.get). Por isso usamos o
# Playwright (headless) UMA VEZ por partida só pra "ler" da página já renderizada
# o token de torneio ("par") e o ID de cada time — depois disso, trocar entre
# 5/10/15/todos os resultados ou "só esse torneio"/"todos os torneios" é um
# requests.get direto em /res/ajax/team-matches.php (rápido, sem precisar mais
# de browser), então o cache do contexto vale a pena mesmo custando ~2-4s a mais
# na primeira vez que alguém abre a análise de um jogo.
_be_context_cache = {}   # match_url -> {"ts":, "data": {"par":, "home":{"id","name"}, "away":{...}}}
_be_context_lock = threading.Lock()
_BE_CONTEXT_TTL = 6 * 3600

def _be_context_cache_prune():
    """Mesmo padrão de _momentum_cache_prune (investigação de custo Railway,
    2026-09-08): o TTL só decide se um hit serve, nunca remove — cada partida
    cujo widget de análise já foi aberto uma vez (mesmo há semanas) ficava
    ocupando memória pra sempre."""
    if len(_be_context_cache) < 500:
        return
    now = time.time()
    stale = [url for url, v in _be_context_cache.items() if now - v.get("ts", 0) > _BE_CONTEXT_TTL * 2]
    for url in stale:
        _be_context_cache.pop(url, None)
_be_playwright_semaphore = threading.Semaphore(2)  # evita várias janelas headless simultâneas

# Abrir o widget de análise dispara 3 chamadas em paralelo (últimos resultados casa/
# fora + confronto direto) que TODAS precisam do mesmo contexto do jogo. Sem isso,
# as 3 viam o cache vazio ao mesmo tempo e cada uma abria seu próprio Chromium
# headless pra carregar a MESMA página — 3 sessões simultâneas na mesma URL, uma
# assinatura bem óbvia de bot pro BetExplorer. Esse lock por URL garante que só a
# primeira chamada realmente busca; as outras esperam e reaproveitam o resultado.
_be_context_inflight = {}   # match_url -> threading.Lock (só existe enquanto a busca está em andamento)
_be_context_inflight_guard = threading.Lock()


def _be_fetch_match_context(match_url, _attempt=1):
    with _be_context_lock:
        cached = _be_context_cache.get(match_url)
        if cached and (time.time() - cached["ts"]) < _BE_CONTEXT_TTL:
            return cached["data"]

    with _be_context_inflight_guard:
        lock = _be_context_inflight.get(match_url)
        is_leader = lock is None
        if is_leader:
            lock = threading.Lock()
            lock.acquire()
            _be_context_inflight[match_url] = lock

    if not is_leader:
        lock.acquire()  # espera o líder terminar
        lock.release()
        with _be_context_lock:
            cached = _be_context_cache.get(match_url)
        if cached:
            return cached["data"]
        raise RuntimeError("Não foi possível carregar os últimos resultados dessa partida.")

    try:
        return _be_fetch_match_context_uncached(match_url)
    finally:
        with _be_context_inflight_guard:
            _be_context_inflight.pop(match_url, None)
        lock.release()


def _be_fetch_match_context_uncached(match_url, _attempt=1):
    with _be_context_lock:
        cached = _be_context_cache.get(match_url)
        if cached and (time.time() - cached["ts"]) < _BE_CONTEXT_TTL:
            return cached["data"]

    from playwright.sync_api import sync_playwright

    par, teams = None, []
    try:
        with _be_playwright_semaphore:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                try:
                    page = browser.new_page(
                        user_agent=BETEXPLORER_HEADERS["User-Agent"],
                        viewport={"width": 1280, "height": 900},
                        locale="pt-BR",
                    )
                    page.goto(match_url, wait_until="domcontentloaded", timeout=30000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass  # segue mesmo se não ficar 100% ocioso — o seletor abaixo é o que realmente importa
                    page.wait_for_selector("[id^='lm_'][id$='_sel_type']", timeout=20000, state="attached")
                    selects = page.query_selector_all("[id^='lm_'][id$='_sel_type']")
                    headers = page.query_selector_all(".last-results__title .componentHeader")
                    for i, sel in enumerate(selects[:2]):
                        onchange = sel.get_attribute("onchange") or ""
                        m = re.search(r"match_change_team_matches\('([^']*)',\s*'([^']*)',\s*'([^']*)'", onchange)
                        if not m:
                            continue
                        par = m.group(1)
                        name = headers[i].inner_text() if i < len(headers) else ""
                        name = re.sub(r"^.*?:\s*", "", name).strip()
                        teams.append({"id": m.group(3), "name": name})
                finally:
                    browser.close()
    except Exception:
        if _attempt < 2:
            time.sleep(1.5)
            return _be_fetch_match_context_uncached(match_url, _attempt=_attempt + 1)
        raise RuntimeError("O BetExplorer não respondeu a tempo pra carregar os últimos resultados dessa partida. Tente novamente em instantes.")

    if par is None or len(teams) < 2:
        if _attempt < 2:
            time.sleep(1.5)
            return _be_fetch_match_context_uncached(match_url, _attempt=_attempt + 1)
        raise RuntimeError("Não foi possível carregar os últimos resultados dessa partida.")

    data = {"par": par, "home": teams[0], "away": teams[1]}
    with _be_context_lock:
        _be_context_cache[match_url] = {"ts": time.time(), "data": data}
        _be_context_cache_prune()
    return data


def _be_parse_team_matches(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for block in soup.select(".head-to-head__row"):
        date_span = block.select_one(".head-to-head__date .mobileHidden")
        date_text = date_span.get_text(strip=True) if date_span else ""

        home_el = block.select_one(".table-main__participantHome p, .table-main__participantHome div")
        away_el = block.select_one(".table-main__participantAway p, .table-main__participantAway div")
        home_name = home_el.get_text(strip=True) if home_el else ""
        away_name = away_el.get_text(strip=True) if away_el else ""
        home_logo_el = block.select_one(".homeImgMutual")
        away_logo_el = block.select_one(".awayImgMutual")
        home_logo = (BETEXPLORER_BASE + home_logo_el.get("src")) if home_logo_el and home_logo_el.get("src", "").startswith("/") else (home_logo_el.get("src") if home_logo_el else None)
        away_logo = (BETEXPLORER_BASE + away_logo_el.get("src")) if away_logo_el and away_logo_el.get("src", "").startswith("/") else (away_logo_el.get("src") if away_logo_el else None)

        result_div = None
        for d in block.select(".last-results__form-results"):
            if "desktopHidden" not in (d.get("class") or []):
                result_div = d
                break
        result, score_home, score_away = None, None, None
        if result_div:
            for c in (result_div.get("class") or []):
                if c.startswith("last-results__form-results-"):
                    result = c.rsplit("-", 1)[-1]  # W / D / L
            nums = [t for s in result_div.find_all("span") if (t := s.get_text(strip=True)) and t != ":"]
            if len(nums) >= 2:
                score_home, score_away = nums[0], nums[1]

        odds = []
        odds_wrap = block.select_one(".last-results__odds-align")
        if odds_wrap:
            for odd_el in odds_wrap.select(".table-main__odd"):
                span = odd_el.find("span")
                odds.append(span.get("data-odd") if span else None)

        link_tag = block.select_one("a[href]")
        match_href = (BETEXPLORER_BASE + link_tag.get("href")) if link_tag else None

        rows.append({
            "date": date_text, "home": home_name, "away": away_name,
            "home_logo": home_logo, "away_logo": away_logo,
            "score_home": score_home, "score_away": score_away,
            "result": result, "odds": odds, "match_url": match_href,
        })
    return rows


def _be_fetch_team_last_results(match_url, side, count=5, all_tournaments=False):
    ctx = _be_fetch_match_context(match_url)
    team = ctx.get(side)
    if not team:
        raise ValueError("side inválido (use 'home' ou 'away')")
    event_id = match_url.rstrip("/").split("/")[-1]
    params = {
        "par": ctx["par"], "event": event_id, "team": team["id"],
        "type": 2 if all_tournaments else 1, "count": count, "lang": "br",
    }
    r = _be_get(f"{BETEXPLORER_BASE}/res/ajax/team-matches.php", params=params)
    return {"team_name": team["name"], "rows": _be_parse_team_matches(r.text)}


def _be_country_league_path(match_url):
    """Extrai país/liga do caminho da URL do jogo — usado pra montar a URL da
    página de confrontos diretos (mutual-matches), que é por país+liga."""
    m = re.search(r"/football/([^/]+)/([^/]+)/", match_url)
    if not m:
        raise ValueError("Não foi possível identificar a liga a partir da URL do jogo.")
    return m.group(1), m.group(2)


def _be_extract_td_odd(td):
    classes = td.get("class") or []
    colored = "colored" in classes
    val = td.get("data-odd")
    if not val:
        inner = td.find(attrs={"data-odd": True})
        val = inner.get("data-odd") if inner else None
    return val, colored


def _be_parse_h2h(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="js-mutual-table")
    if not table:
        return []
    seasons = []
    current = None
    for tr in table.find_all("tr"):
        if "head-to-head__header" in (tr.get("class") or []):
            th = tr.find("th")
            link = th.find("a") if th else None
            current = {
                "season": link.get_text(strip=True) if link else (th.get_text(strip=True) if th else ""),
                "matches": [],
            }
            seasons.append(current)
            continue
        tds = tr.find_all("td")
        if len(tds) < 7 or current is None:
            continue
        home_name = tds[0].get_text(strip=True)
        away_name = tds[1].get_text(strip=True)
        score_link = tds[2].find("a")
        score_text = score_link.get_text(strip=True) if score_link else tds[2].get_text(strip=True)
        match_href = (BETEXPLORER_BASE + score_link.get("href")) if score_link and score_link.get("href") else None
        score_parts = re.split(r"[:\-]", score_text)
        score_home = score_parts[0].strip() if len(score_parts) == 2 else None
        score_away = score_parts[1].strip() if len(score_parts) == 2 else None
        odds = []
        for td in tds[3:6]:
            val, colored = _be_extract_td_odd(td)
            odds.append({"value": val, "won": colored})
        current["matches"].append({
            "home": home_name, "away": away_name,
            "score_home": score_home, "score_away": score_away,
            "match_url": match_href, "date": tds[6].get_text(strip=True), "odds": odds,
        })
    return seasons


def _be_h2h_summary(seasons, home_name, away_name):
    wins_home = wins_away = draws = 0
    for season in seasons:
        for m in season["matches"]:
            try:
                sh, sa = int(m["score_home"]), int(m["score_away"])
            except (TypeError, ValueError):
                continue
            if sh == sa:
                draws += 1
            else:
                winner = m["home"] if sh > sa else m["away"]
                if winner == home_name:
                    wins_home += 1
                elif winner == away_name:
                    wins_away += 1
    total = wins_home + wins_away + draws
    pct_home = round(100 * wins_home / total) if total else 0
    pct_away = round(100 * wins_away / total) if total else 0
    return {
        "wins_home": wins_home, "wins_away": wins_away, "draws": draws,
        "pct_home": pct_home, "pct_away": pct_away, "pct_draw": 100 - pct_home - pct_away if total else 0,
    }


def _be_fetch_h2h(match_url):
    ctx = _be_fetch_match_context(match_url)
    country, league = _be_country_league_path(match_url)
    r = _be_get(
        f"{BETEXPLORER_BASE}/br/football/{country}/{league}/mutual-matches/",
        params={"home": ctx["home"]["id"], "away": ctx["away"]["id"], "where": 0},
    )
    seasons = _be_parse_h2h(r.text)
    summary = _be_h2h_summary(seasons, ctx["home"]["name"], ctx["away"]["name"])
    return {
        "home_name": ctx["home"]["name"], "away_name": ctx["away"]["name"],
        "summary": summary, "seasons": seasons,
    }


@app.route("/api/painel/h2h")
def api_painel_h2h():
    match_url = request.args.get("match_url", "")
    if not match_url.startswith(BETEXPLORER_BASE):
        return jsonify({"error": "match_url inválido"}), 400
    try:
        data = _be_fetch_h2h(match_url)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(data)


# ── Estatísticas de Confronto Direto (aba Análise) — % vitórias/empates/derrotas,
# médias de gols, BTTS, clean sheets, over/under (tempo integral) ──────────────
# Removido de propósito o placar do intervalo (HT): ele não vem na lista de
# confrontos (mutual-matches), só o placar final — pra ter o HT seria preciso
# 1 requisição extra por jogo do filtro na página de cada confronto antigo, o
# que multiplicava bastante o tráfego pro BetExplorer. Ver histórico do commit
# se precisar recuperar essa lógica.


def _be_h2h_stats(match_url, tournament="1", count="5"):
    h2h = _be_fetch_h2h(match_url)
    home_name, away_name = h2h["home_name"], h2h["away_name"]

    seasons = h2h["seasons"]
    if tournament == "1":
        seasons = [s for s in seasons if not re.search(r"copa|cup", s["season"], re.I)]
    flat = [m for s in seasons for m in s["matches"]]  # mais recente primeiro

    wanted = len(flat) if count == "20" else int(count)
    if len(flat) < wanted:
        return {"enough": False, "available": len(flat), "wanted": wanted}
    used = flat[:wanted]

    # Só tempo integral (FT) — o placar do intervalo exigiria 1 requisição extra
    # por jogo do filtro na página de cada confronto antigo, o que multiplicava
    # bastante o tráfego pro BetExplorer (e contribuiu pro bloqueio 429 que a
    # gente teve). Removido de propósito: FT já vem de graça na mesma lista de
    # confrontos, sem nenhuma chamada adicional.
    n = len(used)
    wins = draws = losses = 0
    goals_for_ft = goals_against_ft = 0
    btts_yes = 0
    clean_sheets_ft = 0
    failed_to_score_ft = 0
    over25 = over15 = 0

    for m in used:
        try:
            sh, sa = int(m["score_home"]), int(m["score_away"])
        except (TypeError, ValueError):
            continue
        is_home_team = m["home"] == home_name
        gf = sh if is_home_team else sa
        ga = sa if is_home_team else sh
        goals_for_ft += gf
        goals_against_ft += ga
        if gf > ga:
            wins += 1
        elif gf == ga:
            draws += 1
        else:
            losses += 1
        if gf == 0:
            failed_to_score_ft += 1
        if ga == 0:
            clean_sheets_ft += 1
        if sh > 0 and sa > 0:
            btts_yes += 1
        total_goals = sh + sa
        if total_goals > 2.5:
            over25 += 1
        if total_goals > 1.5:
            over15 += 1

    def pct(x, total):
        return round(100 * x / total) if total else None

    def avg(x, total):
        return round(x / total, 2) if total else None

    # Cada estatística de time é espelhada pro lado oposto: numa H2H, a vitória de
    # um é a derrota do outro, o gol marcado por um é o gol sofrido pelo outro etc.
    # BTTS e Over/Under são do CONFRONTO (não têm "lado"), por isso ficam repetidos
    # dos dois lados.
    pct_wins_ft, pct_draws_ft, pct_losses_ft = pct(wins, n), pct(draws, n), pct(losses, n)
    avg_gf_ft, avg_ga_ft = avg(goals_for_ft, n), avg(goals_against_ft, n)
    pct_clean_ft = pct(clean_sheets_ft, n)
    pct_fts_ft = pct(failed_to_score_ft, n)

    home_stats = {
        "pct_wins_ft": pct_wins_ft, "pct_draws_ft": pct_draws_ft, "pct_losses_ft": pct_losses_ft,
        "avg_goals_for_ft": avg_gf_ft, "avg_goals_against_ft": avg_ga_ft,
        "pct_clean_sheet_ft": pct_clean_ft, "pct_failed_to_score_ft": pct_fts_ft,
    }
    away_stats = {
        "pct_wins_ft": pct_losses_ft, "pct_draws_ft": pct_draws_ft, "pct_losses_ft": pct_wins_ft,
        "avg_goals_for_ft": avg_ga_ft, "avg_goals_against_ft": avg_gf_ft,
        "pct_clean_sheet_ft": pct_fts_ft, "pct_failed_to_score_ft": pct_clean_ft,
    }

    return {
        "enough": True, "matches_used": n,
        "home_name": home_name, "away_name": away_name,
        "home": home_stats, "away": away_stats,
        "pct_btts_yes": pct(btts_yes, n), "pct_btts_no": pct(n - btts_yes, n),
        "pct_over25_ft": pct(over25, n), "pct_under25_ft": pct(n - over25, n),
        "pct_over15_ft": pct(over15, n), "pct_under15_ft": pct(n - over15, n),
    }


@app.route("/api/painel/h2h_stats")
def api_painel_h2h_stats():
    match_url = request.args.get("match_url", "")
    tournament = request.args.get("tournament", "1")
    count = request.args.get("count", "5")
    if not match_url.startswith(BETEXPLORER_BASE):
        return jsonify({"error": "match_url inválido"}), 400
    try:
        data = _be_h2h_stats(match_url, tournament=tournament, count=count)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(data)


@app.route("/api/painel/debug_be")
def api_painel_debug_be():
    """Diagnóstico temporário — testa cada etapa do acesso ao BetExplorer
    isoladamente (requests simples vs Playwright) pra descobrir exatamente
    onde está travando em produção, sem depender de logs do Railway."""
    import traceback
    result = {}

    t0 = time.time()
    try:
        r = http_req.get(f"{BETEXPLORER_BASE}/br/", headers=BETEXPLORER_HEADERS, timeout=15)
        result["plain_request"] = {"ok": True, "status": r.status_code, "seconds": round(time.time() - t0, 2), "len": len(r.text)}
    except Exception as e:
        result["plain_request"] = {"ok": False, "error": f"{type(e).__name__}: {e}", "seconds": round(time.time() - t0, 2)}

    match_url = request.args.get(
        "match_url", "https://www.betexplorer.com/br/football/brazil/serie-a-betano/coritiba-palmeiras/zmCty4Ji/"
    )

    t0 = time.time()
    try:
        from playwright.sync_api import sync_playwright
        # mesmo semáforo usado pelo resto do app — sem isso, um pico de chamadas a
        # esse endpoint de diagnóstico podia sozinho ocupar todas as threads do
        # gunicorn (só 1 worker/4 threads) e travar o site inteiro pra todo mundo.
        acquired = _be_playwright_semaphore.acquire(timeout=10)
        if not acquired:
            return jsonify({"error": "Playwright ocupado (semáforo cheio) — tente de novo em instantes."}), 503
        try:
            with sync_playwright() as p:
                t_launch = time.time()
                browser = p.chromium.launch(
                    headless=True, args=["--disable-blink-features=AutomationControlled"], timeout=15000,
                )
                result["playwright_launch"] = {"ok": True, "seconds": round(time.time() - t_launch, 2)}
                try:
                    page = browser.new_page(
                        user_agent=BETEXPLORER_HEADERS["User-Agent"], viewport={"width": 1280, "height": 900}, locale="pt-BR",
                    )
                    t_goto = time.time()
                    page.goto(match_url, wait_until="domcontentloaded", timeout=15000)
                    result["playwright_goto"] = {"ok": True, "seconds": round(time.time() - t_goto, 2), "title": page.title()}

                    t_sel = time.time()
                    try:
                        page.wait_for_selector("[id^='lm_'][id$='_sel_type']", timeout=10000, state="attached")
                        result["playwright_selector"] = {"ok": True, "seconds": round(time.time() - t_sel, 2)}
                    except Exception as e:
                        result["playwright_selector"] = {"ok": False, "error": f"{type(e).__name__}: {e}", "seconds": round(time.time() - t_sel, 2)}
                        # salva um pedaço do HTML pra ver se veio página de bloqueio/captcha
                        html = page.content()
                        result["page_snippet"] = html[:1500]
                        result["page_length"] = len(html)
                finally:
                    browser.close()
        except Exception as e:
            result["playwright_error"] = f"{type(e).__name__}: {e}"
            result["playwright_traceback"] = traceback.format_exc()
        finally:
            _be_playwright_semaphore.release()
    except Exception as e:
        result["playwright_error"] = f"{type(e).__name__}: {e}"
        result["playwright_traceback"] = traceback.format_exc()
    result["playwright_total_seconds"] = round(time.time() - t0, 2)

    return jsonify(result)


# ── Widget de análise — Classificações / Forma / Over-Under / HT-FT / Marcadores
# Diferente do "últimos resultados" (que precisa de Playwright pro token "ts" do
# JOGO), o "ts" da TABELA/liga já vem embutido direto no HTML estático da página
# do torneio (ex: /br/football/brazil/serie-a-betano/) — então dá pra pegar com
# um requests.get simples, sem precisar de browser headless.
_be_tournament_ts_cache = {}   # (country, league) -> {"ts":, "value": token}
_be_tournament_ts_lock = threading.Lock()
_BE_TOURNAMENT_TS_TTL = 6 * 3600
_BE_STANDINGS_CACHE_TTL = 300   # 5min — tabela de classificação não muda a cada minuto
_be_standings_cache = {}
_be_standings_lock = threading.Lock()


def _be_fetch_tournament_ts(country, league):
    key = (country, league)
    with _be_tournament_ts_lock:
        cached = _be_tournament_ts_cache.get(key)
        if cached and (time.time() - cached["ts"]) < _BE_TOURNAMENT_TS_TTL:
            return cached["value"]

    r = _be_get(f"{BETEXPLORER_BASE}/br/football/{country}/{league}/")
    m = re.search(r"standings/\?table=table&table_sub=&ts=([^&\"]+)", r.text)
    if not m:
        raise RuntimeError("Não foi possível encontrar o token de classificação dessa liga.")
    token = m.group(1)
    with _be_tournament_ts_lock:
        _be_tournament_ts_cache[key] = {"ts": time.time(), "value": token}
    return token


def _be_parse_standings_table(html):
    """Retorna {variant: {"columns":[...], "rows":[...]}}. `variant` é o sufixo
    do id do box (ex.: "10" pra Forma-10-jogos, "2.5" pra linha do Over/Under),
    ou "default" quando só existe uma tabela (Classificações, HT/FT)."""
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    for box in soup.select("[id^='box-table-type-']"):
        box_id = box.get("id", "")
        m = re.match(r"box-table-type-\d+-(.+)$", box_id)
        variant = m.group(1) if m else "default"
        table = box.find("table")
        if not table:
            continue

        columns = []
        thead = table.find("thead")
        if thead:
            for th in thead.find_all("th"):
                columns.append({
                    "key": th.get("data-type", ""), "label": th.get_text(strip=True), "title": th.get("title", ""),
                })

        rows = []
        tbody = table.find("tbody")
        for tr in (tbody.find_all("tr") if tbody else []):
            tds = tr.find_all("td")
            if not tds:
                continue
            logo_span = tr.select_one(".team-logo, .flag")
            logo_url = None
            if logo_span and logo_span.get("style"):
                mlogo = re.search(r"url\(([^)]+)\)", logo_span["style"])
                if mlogo:
                    src = mlogo.group(1)
                    logo_url = (BETEXPLORER_BASE + src) if src.startswith("/") else src

            cells = {}
            for col, td in zip(columns, tds):
                if col["key"] in ("form", "last_5"):
                    badges = []
                    for a in td.select("a[class*='form-']"):
                        cls = " ".join(a.get("class") or [])
                        mres = re.search(r"form-(w|d|l|s|over|under)\b", cls)
                        badges.append(mres.group(1) if mres else "s")
                    cells[col["key"]] = badges
                else:
                    cells[col["key"]] = td.get_text(strip=True)
            rows.append({"logo": logo_url, "cells": cells})
        result[variant] = {"columns": columns, "rows": rows}
    return result


def _be_fetch_standings(match_url, table, table_sub=""):
    country, league = _be_country_league_path(match_url)
    cache_key = (country, league, table, table_sub)
    now = time.time()
    with _be_standings_lock:
        cached = _be_standings_cache.get(cache_key)
        if cached and (now - cached["ts"]) < _BE_STANDINGS_CACHE_TTL:
            return cached["data"]

    token = _be_fetch_tournament_ts(country, league)
    r = _be_get(
        f"{BETEXPLORER_BASE}/br/football/{country}/{league}/standings/",
        params={"table": table, "table_sub": table_sub, "ts": token, "dcheck": 0, "as-ajax": 1, "l": "br"},
    )
    data = _be_parse_standings_table(r.text)
    with _be_standings_lock:
        _be_standings_cache[cache_key] = {"ts": now, "data": data}
    return data


@app.route("/api/painel/standings")
def api_painel_standings():
    match_url = request.args.get("match_url", "")
    table = request.args.get("table", "table")  # table | form | over_under | ht_ft | top_scorers
    table_sub = request.args.get("sub", "")      # "" | home | away
    if not match_url.startswith(BETEXPLORER_BASE):
        return jsonify({"error": "match_url inválido"}), 400
    if table not in ("table", "form", "over_under", "ht_ft", "top_scorers"):
        return jsonify({"error": "table inválido"}), 400
    try:
        data = _be_fetch_standings(match_url, table, table_sub)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(data)


@app.route("/api/painel/last_results")
def api_painel_last_results():
    match_url = request.args.get("match_url", "")
    side = request.args.get("side", "home")
    count = request.args.get("count", "5")
    all_tournaments = request.args.get("all") == "1"
    if not match_url.startswith(BETEXPLORER_BASE):
        return jsonify({"error": "match_url inválido"}), 400
    try:
        data = _be_fetch_team_last_results(match_url, side, count=count, all_tournaments=all_tournaments)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(data)


@app.route("/version")
def version():
    return jsonify({"version": APP_VERSION, "ts": datetime.now().isoformat()})


# ── Senha única do site (pedido do usuário, 2026-09-12: "travar o site pra
# visitantes aleatórios", sem conta por pessoa) — trava tudo atrás de UMA
# senha compartilhada, guardada em variável de ambiente (SITE_PASSWORD no
# Railway), nunca no código. Sem essa variável configurada, o gate fica
# DESLIGADO (comportamento de sempre) — assim dev local não exige setup
# extra pra rodar.
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "")

# Fica de fora do gate: a própria página de login (senão ninguém consegue
# nem chegar nela) e os arquivos estáticos (a página de login carrega sua
# própria imagem/CSS de /static/... — travar isso também criaria um
# problema de ovo-e-galinha). O index.html/JS do site também fica acessível
# como arquivo estático, mas sem dado nenhum: todo dado real vem de /api/*,
# que continua atrás do login. Os endpoints de upload/download de backup
# ficam de fora de propósito — são chamados pelo script local
# (upload_backup.py) via UPLOAD_TOKEN próprio, não por navegador logado.
_LOGIN_EXEMPT_PREFIXES = (
    "/login", "/static/", "/version", "/favicon.ico",
    "/api/upload-backup", "/api/list-backup", "/api/download-backup",
)

# Bloqueio por tentativas erradas (pedido do usuário, 2026-09-12: "se errar a
# senha +de 4x a conta é bloqueada e tem que aguardar 10 minutos"). Sem conta
# por pessoa (senha única), então quem identifica "de onde vêm as tentativas"
# é o IP — guardado só em memória (reinicia a cada deploy, sem problema: o
# objetivo é atrapalhar um brute-force na hora, não manter histórico).
_login_attempts = {}
_login_attempts_lock = threading.Lock()
_LOGIN_MAX_TENTATIVAS = 4       # erradas permitidas — a 5ª bloqueia
_LOGIN_BLOQUEIO_SEG = 10 * 60


def _login_client_ip():
    """IP de quem está tentando logar. Railway (como a maioria dos PaaS) fica
    atrás de um proxy reverso — sem olhar X-Forwarded-For, request.remote_addr
    sempre devolveria o IP interno do proxy, e todo visitante pareceria vir
    do mesmo lugar (1 pessoa errando a senha bloquearia todo mundo, dono do
    site incluído). O X-Forwarded-For carrega o IP original como 1º item."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "desconhecido"


def _login_lockout_minutos(ip):
    """None = liberado pra tentar. Caso contrário, minutos restantes de bloqueio (arredondado pra cima)."""
    with _login_attempts_lock:
        info = _login_attempts.get(ip)
        if not info or not info.get("locked_until"):
            return None
        restante = info["locked_until"] - time.time()
        if restante <= 0:
            _login_attempts.pop(ip, None)
            return None
        return math.ceil(restante / 60)


@app.before_request
def _exigir_login():
    if not SITE_PASSWORD:
        return
    if request.path.startswith(_LOGIN_EXEMPT_PREFIXES):
        return
    if session.get("logado"):
        return
    return redirect("/login")


@app.route("/favicon.ico")
def favicon():
    """Ícone do site (GN). Navegadores pedem /favicon.ico por conta própria, então
    a rota existe além do <link rel="icon"> das páginas."""
    return send_from_directory("static", "favicon.ico", mimetype="image/vnd.microsoft.icon", max_age=86400)


@app.route("/login", methods=["GET", "POST"])
def login():
    ip = _login_client_ip()
    if request.method == "POST":
        minutos = _login_lockout_minutos(ip)
        if minutos is not None:
            return redirect(f"/login?bloqueado=1&min={minutos}")

        senha = request.form.get("senha", "")
        if SITE_PASSWORD and senha == SITE_PASSWORD:
            with _login_attempts_lock:
                _login_attempts.pop(ip, None)
            # Sem session.permanent = True de propósito (pedido do usuário,
            # 2026-09-12): cookie de sessão "de navegador" — o navegador some
            # com ele quando fecha, então da próxima vez que abrir o site
            # pede login de novo. Com permanent=True (como era antes) o
            # cookie sobrevivia até 30 dias, mesmo fechando o navegador.
            session["logado"] = True
            # ?logged=1 avisa o front (index.html) que acabou de logar nessa
            # ABA/janela — ele marca isso no sessionStorage (que é por aba,
            # ao contrário do cookie, que vale pro navegador inteiro). Ver
            # comentário no início de static/index.html.
            return redirect("/?logged=1")

        with _login_attempts_lock:
            info = _login_attempts.setdefault(ip, {"count": 0, "locked_until": None})
            info["count"] += 1
            if info["count"] > _LOGIN_MAX_TENTATIVAS:
                info["locked_until"] = time.time() + _LOGIN_BLOQUEIO_SEG
                info["count"] = 0
                return redirect(f"/login?bloqueado=1&min={_LOGIN_BLOQUEIO_SEG // 60}")
            restam = _LOGIN_MAX_TENTATIVAS - info["count"]
        return redirect(f"/login?erro=1&restam={restam}")

    # GET — só reencaminha com o aviso de bloqueio se ainda não veio com ele
    # (evita loop: essa mesma rota redireciona pra ela mesma só 1x, com o
    # parâmetro; na 2ª vez (já com "bloqueado" na URL) só renderiza a página).
    if "bloqueado" not in request.args:
        minutos = _login_lockout_minutos(ip)
        if minutos is not None:
            return redirect(f"/login?bloqueado=1&min={minutos}")
    # Sem "if já logado, pula pro /" aqui de propósito — pedido do usuário
    # (2026-09-12): "quero que peça login quando eu fechar o SITE [a aba],
    # não só o navegador". O cookie sozinho não dá conta disso (é
    # compartilhado por todas as abas do navegador, sobrevive fechando só
    # uma aba); quem decide isso é o sessionStorage no index.html, que
    # redireciona pra cá quando a aba é nova/foi reaberta, mesmo com cookie
    # ainda válido. Se essa rota pulasse de volta pro / nesse caso, virava
    # loop: index manda pra /login, /login manda de volta pro index.
    return send_from_directory("static", "login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/")
def index():
    # Injeta uma flag no <head> avisando se a trava de senha está ativa —
    # index.html é servido como arquivo estático puro (sem Jinja), então o
    # jeito mais simples de passar essa 1 informação dinâmica é um replace
    # de string no HTML já pronto, sem virar um template inteiro por causa
    # disso. Usada pelo script de sessionStorage (ver início do arquivo).
    with open(os.path.join(DATA_DIR, "static", "index.html"), "r", encoding="utf-8") as f:
        html = f.read()
    flag = "true" if SITE_PASSWORD else "false"
    html = html.replace("<head>", f"<head><script>window.__SITE_LOGIN_ATIVO = {flag};</script>", 1)
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"]        = "no-cache"
    resp.headers["Expires"]       = "0"
    return resp


@app.route("/api/match/<path:match_id>")
def api_match_detail(match_id):
    matches, _ = load_predictions()
    for m in matches:
        mid = m.get("url_detalhes", "").split("/compare/teams/")[-1]
        if mid == match_id:
            conv_casa, h2h = _convicao_score(m, True)
            conv_fora, _ = _convicao_score(m, False)
            m = dict(m)
            m["convicao_casa"] = conv_casa
            m["convicao_fora"] = conv_fora
            m["h2h_confronto_direto"] = h2h
            return jsonify(m)
    abort(404)


BACKTEST_DIR = os.path.join(DATA_DIR, "backtest")


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

# Margem de gols no intervalo usada pelos indicadores do 2º tempo (ver
# _live_2t_indicadores) — pedido do usuário: destacar time que abre vantagem
# no intervalo e segura o resultado (não sofre mais) e time que fica atrás
# no intervalo e não consegue marcar. 2 gols segue a mesma referência já
# calibrada em outras metodologias desta base (ver "Vovô" na memória de
# trading esportivo — favorito com 2 gols de vantagem).
_LIVE_2T_MARGEM = 2

def _live_2t_indicadores(gol_casa_ht, gol_fora_ht, gol_casa_ft, gol_fora_ft):
    """Recalculado a cada poll (não é um estado salvo) — por isso os indicadores
    "ligam" e "desligam" sozinhos conforme o jogo evolui: assim que o time
    líder sofre um gol, ou o time atrás marca, a condição deixa de bater
    naturalmente na próxima chamada.
    Retorna dict com 'lider_sem_sofrer' e 'atras_sem_marcar' ('casa'|'fora'|None)
    e 'margem' (diferença de gols no intervalo, sempre >= 0)."""
    gch = gol_casa_ht or 0
    gfh = gol_fora_ht or 0
    gcf = gol_casa_ft or 0
    gff = gol_fora_ft or 0
    margem_casa   = gch - gfh          # positivo = casa liderava no intervalo
    sofridos_casa = gff - gfh          # gols que a casa tomou no 2ºT
    sofridos_fora = gcf - gch          # gols que o fora tomou no 2ºT
    marcados_casa = gcf - gch
    marcados_fora = gff - gfh

    lider_sem_sofrer = None
    if margem_casa >= _LIVE_2T_MARGEM and sofridos_casa == 0:
        lider_sem_sofrer = "casa"
    elif -margem_casa >= _LIVE_2T_MARGEM and sofridos_fora == 0:
        lider_sem_sofrer = "fora"

    atras_sem_marcar = None
    if margem_casa >= _LIVE_2T_MARGEM and marcados_fora == 0:
        atras_sem_marcar = "fora"
    elif -margem_casa >= _LIVE_2T_MARGEM and marcados_casa == 0:
        atras_sem_marcar = "casa"

    return {
        "lider_sem_sofrer": lider_sem_sofrer,
        "atras_sem_marcar": atras_sem_marcar,
        "margem": abs(margem_casa),
    }

# Uma busca por vez (2026-09-19): sem isso, cada requisição que achava o cache de
# 30s vencido refazia a varredura inteira — com o Ao Vivo atualizando a cada 20s e
# a varredura levando vários segundos, elas se empilhavam. Quem chega com uma em
# andamento recebe a lista anterior (marcada stale) em vez de esperar/duplicar.
_radar_live_lock = threading.Lock()


def _radar_fetch_live_matches():
    """Lista de jogos ao vivo (UniScore) — ver _radar_fetch_live_matches_impl."""
    if time.time() - _uniscore_full_cache["ts"] < 30 and _uniscore_full_cache["live"]:
        live = _uniscore_full_cache["live"]
        return {"live": live, "total": len(live)}
    if not _radar_live_lock.acquire(blocking=False):
        prev = _uniscore_full_cache["live"]
        if prev and time.time() - _uniscore_full_cache["ts"] < 600:
            return {"live": prev, "total": len(prev), "stale": True}
        _radar_live_lock.acquire()
    try:
        return _radar_fetch_live_matches_impl()
    finally:
        _radar_live_lock.release()


_MARCADOR_TIME = re.compile(r"\b(u-?\d{2}|sub-?\d{2}|w|women|fem\w*|ii|iii|b|res|reserves?)\b", re.I)


@lru_cache(maxsize=60000)
def _nome_forte_feats(nome):
    """(nome normalizado, marcadores, palavras próprias) de um time. Marcadores são
    U19/Sub-20/feminino/II/B/reservas; ficam FORA das palavras próprias — senão
    "Lecco U19" casaria com qualquer outro time U19 só pelo "u19"."""
    n = _norm(nome or "")
    marc = frozenset(m.lower().replace("-", "") for m in _MARCADOR_TIME.findall(n))
    proprias = _name_features(n)[1] - {m for m in re.findall(r"[a-z0-9]+", n) if m.replace("-", "") in marc}
    return n, marc, proprias


def _nome_forte(a, b):
    """Casamento de nomes de time mais rigoroso que _name_match, pra decisões que
    ESCONDEM jogo: _name_match casa "Redditch Utd" com "Oxford Utd" só porque as
    duas têm "utd". Aqui vale nome igual, um contido no outro, ou palavra própria
    em comum (sem "utd/city/fc..."), e os marcadores (U19, feminino, II, B...) têm
    que ser os mesmos — "Lecco U19" não é "Lecco"."""
    na, ma, pa = _nome_forte_feats(a)
    nb, mb, pb = _nome_forte_feats(b)
    if not na or not nb or ma != mb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return bool(pa & pb)


def _minuto_num(v):
    try:
        return int(str(v).replace("'", "").split("+")[0])
    except (TypeError, ValueError):
        return None


def _radar_tira_encerrados(live):
    """Tira do Ao Vivo os jogos que o Flashscore já marca como ENCERRADOS.
    O UniScore segue listando o jogo como "2º tempo" com 94' a 106' (ou preso em
    "intervalo") por bastante tempo depois do apito final — medido em 2026-09-19:
    cerca de 1/3 dos jogos "ao vivo" já estavam encerrados. Regra: os dois times
    casam (_nome_forte) com um jogo ENCERRADO do Flashscore E (o placar é o mesmo OU
    o jogo já passou de 88 min — em ligas pequenas o placar do UniScore fica
    atrasado, ex: 0-0 quando já acabou 3-0). Só afeta a lista da tela; o monitor de
    fundo continua vendo esses jogos pra gravá-los quando o UniScore os finalizar."""
    try:
        fs = _fs_all_matches()
    except Exception:
        return live
    enc = [m for m in fs if str(m.get("status")) == "3"]
    if not enc:
        return live
    por_placar = {}
    for c in enc:
        por_placar.setdefault((str(c.get("home_score")), str(c.get("away_score"))), []).append(c)
    vivos, tirados = [], 0
    for m in live:
        casa, fora = m.get("casa") or "", m.get("fora") or ""
        minuto = _minuto_num(m.get("minuto"))
        avancado = minuto is not None and minuto >= 88 and m.get("tempo") == "2nd_half"
        placar = (str(m.get("golCasaFt")), str(m.get("golForaFt")))
        candidatos = enc if avancado else por_placar.get(placar, ())
        if any(_nome_forte(casa, c["home"]) and _nome_forte(fora, c["away"]) for c in candidatos):
            tirados += 1
        else:
            vivos.append(m)
    if tirados:
        print(f"[live] {tirados} jogo(s) já encerrados no Flashscore tirados da lista ao vivo")
    return vivos


def _radar_fetch_live_matches_impl():
    """Busca a lista de jogos ao vivo via UniScore (mesma lógica de sempre, só
    sem o jsonify) — extraída pra ser reaproveitada por outros consumidores
    internos, não só pelo endpoint público /api/radar/live."""
    # Cache de 30s — reduzido de 90s a pedido do usuário (2026-09-01: "tem
    # como deixar ao vivo mais rápido? recebendo as informações quase em
    # tempo real?"). Triplica a frequência das buscas reais na UniScore (7
    # locales x até 5 páginas cada) — aceito conscientemente pelo usuário
    # depois de avisado do custo, ainda longe do padrão que já causou
    # apagão nesta sessão (aquele era síncrono dentro de handler quente sem
    # nenhum cache; aqui já existe cache, só ficou mais curto).
    if time.time() - _uniscore_full_cache["ts"] < 30 and _uniscore_full_cache["live"]:
        live = _uniscore_full_cache["live"]
        return {"live": live, "total": len(live)}

    live = []
    all_by_id = {}
    for events in _uniscore_live_events_raw():
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
                "priority":  e.get("tournament", {}).get("priority"),
                "tempo":     e.get("status", {}).get("description", ""),
                "minuto":    _uniscore_minuto(e),
                "golCasaFt": hs.get("current", 0),
                "golForaFt": aws.get("current", 0),
                "golCasaHt": hs.get("period1", 0),
                "golForaHt": aws.get("period1", 0),
                "cartaoCasa": 0,
                "cartaoFora": 0,
            }

    live = list(all_by_id.values())
    print(f"[live] {len(live)} jogos ao vivo retornados")
    live = _radar_tira_encerrados(live)

    # Mesmo link direto pra Betfair Exchange / Bolsa de Aposta usado no Painel
    # Principal (ver _find_radar_links) — aqui não temos horário de início (o
    # jogo já tá em andamento), então em caso raro de nome ambíguo fica com o
    # 1º candidato em vez de desempatar por horário.
    try:
        for m in live:
            lb, lba, lr = _find_radar_links(m.get("casa"), m.get("fora"))
            m["link_betfair"] = lb
            m["link_bolsa"] = lba
            m["link_radar"] = lr
    except Exception as e:
        print(f"[radar-links] Erro anexando links (ao vivo): {e}")

    # Reaproveita as mesmas odds 1x2/Over-Under do Painel Principal
    # (Flashscore) — casa (casa, fora) do Ao Vivo (Uniscore) com o jogo
    # equivalente lá pelo nome dos times, mesma técnica do _find_radar_links.
    # Busca a lista uma vez só (não por partida) porque
    # _painel_fetch_matches_flashscore já cacheia por conta própria, mas
    # repetir a chamada pra cada jogo ainda seria bater no lock/dict à toa
    # dezenas de vezes por request.
    try:
        painel_data = _painel_fetch_matches_flashscore()
        painel_matches = [pm for lg in painel_data.get("leagues", []) for pm in lg["matches"]]
    except Exception as e:
        print(f"[painel-odds] Erro buscando odds do Painel Principal: {e}")
        painel_matches = []
    for m in live:
        pm = next((p for p in painel_matches
                   if _name_match(m.get("casa") or "", p.get("home") or "")
                   and _name_match(m.get("fora") or "", p.get("away") or "")), None)
        m["odd_1"] = pm.get("odd_1") if pm else None
        m["odd_x"] = pm.get("odd_x") if pm else None
        m["odd_2"] = pm.get("odd_2") if pm else None
        m["odd_over"] = pm.get("odd_over") if pm else None
        m["odd_under"] = pm.get("odd_under") if pm else None
        # Linha do Over/Under (ex: "2.5") — faltava antes; sem ela não dá pra
        # saber a que total de gols o odd_over/odd_under se refere (usado pelo
        # modelo de probabilidade de placar da Ao Vivo, calculado no frontend).
        m["ou_line"] = pm.get("ou_line") if pm else None

    # Indicadores do 2º tempo (líder que não sofre mais / time atrás que não
    # marca) — só fazem sentido com o placar do intervalo já fechado, por
    # isso só calcula quando o período atual é literalmente "2nd_half"
    # (evita falso positivo com o HT ainda em 0-0 default de partida no 1ºT).
    for m in live:
        if m.get("tempo") == "2nd_half":
            ind = _live_2t_indicadores(m.get("golCasaHt"), m.get("golForaHt"),
                                        m.get("golCasaFt"), m.get("golForaFt"))
        else:
            ind = {"lider_sem_sofrer": None, "atras_sem_marcar": None, "margem": 0}
        m["ind2t_lider_sem_sofrer"] = ind["lider_sem_sofrer"]
        m["ind2t_atras_sem_marcar"] = ind["atras_sem_marcar"]
        m["ind2t_margem"] = ind["margem"]

    # Se a fetch retornou 0 jogos mas o cache anterior tem dados recentes (< 5 min),
    # mantém o cache antigo para evitar sidebar vazia por falha temporária da API
    if not live and _uniscore_full_cache["live"] and (time.time() - _uniscore_full_cache["ts"] < 300):
        print(f"[live] API retornou 0 jogos — mantendo cache anterior com {len(_uniscore_full_cache['live'])} jogos")
        return {"live": _uniscore_full_cache["live"], "total": len(_uniscore_full_cache["live"]), "stale": True}

    _uniscore_full_cache["ts"]   = time.time()
    _uniscore_full_cache["live"] = live
    return {"live": live, "total": len(live), "stale": False}


# ── Corte por prioridade da liga (2026-09-19, pedido do usuário) ──────────────
# O UniScore dá a cada torneio um "priority" (posição no ranking mundial deles:
# 11 = Premier League, 1000 = sem ranking). O usuário só opera as ligas de cima,
# então o Ao Vivo e o monitor de fundo (que grava a base) só trabalham com jogos
# de liga com priority <= corte, em ordem da mais importante pra menos. Ligas
# abaixo do corte não são exibidas NEM gravadas. O corte é ajustável na tela do
# Ao Vivo (campo "Prioridade ≤") e fica guardado num arquivo sincronizado com o
# GitHub pra sobreviver a deploy.
AO_VIVO_CFG_FILE = os.path.join(DATA_DIR, "ao_vivo_config.json")
_AO_VIVO_PRIORITY_PADRAO = 200
_ao_vivo_cfg = {"priority_max": None, "modo": "top"}
_ao_vivo_cfg_lock = threading.Lock()


def _prio_de(m):
    """priority do jogo como número; sem informação = 1000 (o mesmo que 'sem ranking')."""
    try:
        return int(m.get("priority") if m.get("priority") is not None else 1000)
    except (TypeError, ValueError):
        return 1000


def _ao_vivo_cfg_carrega():
    """Lê o arquivo de configuração 1x (sob o lock do chamador)."""
    if _ao_vivo_cfg["priority_max"] is not None:
        return
    pmax, modo = _AO_VIVO_PRIORITY_PADRAO, "top"
    try:
        with open(AO_VIVO_CFG_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        pmax = min(1000, max(1, int(d.get("priority_max"))))
        if d.get("modo") in ("top", "prioridade"):
            modo = d["modo"]
    except Exception:
        pass
    _ao_vivo_cfg["priority_max"], _ao_vivo_cfg["modo"] = pmax, modo


def _ao_vivo_priority_max():
    with _ao_vivo_cfg_lock:
        _ao_vivo_cfg_carrega()
        return _ao_vivo_cfg["priority_max"]


def _ao_vivo_modo():
    """'top' = só ligas do Top Scores do Livesport (padrão, pedido do usuário em
    2026-09-19); 'prioridade' = ligas até o corte de priority do UniScore."""
    with _ao_vivo_cfg_lock:
        _ao_vivo_cfg_carrega()
        return _ao_vivo_cfg["modo"]


# ── Top Scores do Livesport (futebol) ─────────────────────────────────────────
# A aba "Top Scores" do Livesport/Flashscore lê o feed fm_<dia>_<fuso>_<idioma>_1
# (sem login, mesmo tipo de feed que o site já usa): blocos "SA" abrem um esporte
# (1 = futebol), "ZA" abrem uma liga e "AA" são os jogos dela. Hoje o futebol dessa
# lista são as ligas marcadas "t" no feed (Premier League, LaLiga, Serie A,
# Bundesliga, Ligue 1, Brasileirão...). O ID do jogo no Flashscore não é o do
# UniScore (que fornece pressão/chutes), então o casamento é pelo NOME dos times.
_TOP_SCORES_URL = "https://global.flashscore.ninja/729/x/feed/fm_0_-3_pt-br_1"
_TOP_SCORES_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.livesport.com/",
    "x-fsign": "SW9D1eZo",
}
_TOP_SCORES_TTL = 5 * 60
_top_scores_cache = {"ts": 0, "jogos": None, "ligas": []}
_top_scores_lock = threading.Lock()


def _top_scores_parse(texto):
    """(jogos, ligas) do futebol: jogos = [(liga, casa, fora)], ligas = [nomes]."""
    def kv(bloco):
        return dict(x.split("÷", 1) for x in bloco.split("¬") if "÷" in x)
    esporte, liga, jogos, ligas = None, "", [], []
    for bloco in (b for b in texto.split("~") if b.strip()):
        if bloco.startswith("SA÷"):
            esporte = bloco.split("÷", 1)[1].strip("¬ ")
        elif bloco.startswith("ZA÷") and esporte == "1":
            liga = kv(bloco).get("ZA", "")
            ligas.append(liga)
        elif bloco.startswith("AA÷") and esporte == "1":
            d = kv(bloco)
            if d.get("AE") and d.get("AF"):
                jogos.append((liga, d["AE"], d["AF"]))
    return jogos, ligas


def _top_scores_futebol():
    """Jogos de futebol do Top Scores ([(liga, casa, fora)]) ou None se a fonte
    nunca respondeu (aí o Ao Vivo cai pro corte por prioridade em vez de esconder tudo).
    Cache de 5 min; em falha, mantém a última lista boa."""
    with _top_scores_lock:
        c = _top_scores_cache
        if c["jogos"] is not None and time.time() - c["ts"] < _TOP_SCORES_TTL:
            return c["jogos"]
    try:
        r = http_req.get(_TOP_SCORES_URL, headers=_TOP_SCORES_HEADERS, timeout=10)
        r.raise_for_status()
        jogos, ligas = _top_scores_parse(r.text)
        if not jogos and not ligas:
            raise ValueError("feed sem futebol")
        with _top_scores_lock:
            _top_scores_cache.update({"ts": time.time(), "jogos": jogos, "ligas": ligas})
        return jogos
    except Exception as e:
        print(f"[top-scores] falha ao ler o feed: {e}")
        with _top_scores_lock:
            if _top_scores_cache["jogos"] is not None:
                _top_scores_cache["ts"] = time.time() - _TOP_SCORES_TTL + 60   # tenta de novo em 1 min
            return _top_scores_cache["jogos"]


def _top_scores_ligas():
    with _top_scores_lock:
        return list(_top_scores_cache["ligas"])


def _ao_vivo_filtra_por_prioridade(matches, fav_ids=(), fav_nomes=()):
    """[jogos visíveis ordenados por prioridade, jogos ocultos].
    Critério: modo 'top' = só jogos que estão no Top Scores do Livesport; modo
    'prioridade' = liga até o corte de priority. Favoritos passam SEMPRE, qualquer
    que seja a liga: `fav_ids` são IDs de jogos já favoritados no Ao Vivo,
    `fav_nomes` são pares (casa, fora) de jogos favoritados em Próximos Jogos que
    ainda vão entrar ao vivo (o ID do Flashscore não é o do UniScore, então casa
    pelo nome)."""
    pmax = _ao_vivo_priority_max()
    fav_ids = set(fav_ids)
    top = _top_scores_futebol() if _ao_vivo_modo() == "top" else None
    usar_top = top is not None
    if _ao_vivo_modo() == "top" and not usar_top:
        print("[top-scores] sem lista disponível — usando o corte por prioridade")

    def nomes(m):
        return (m.get("casa") or m.get("home") or ""), (m.get("fora") or m.get("away") or "")

    def favorito(m):
        if str(m.get("id")) in fav_ids:
            return True
        c, f = nomes(m)
        return any(_name_match(c, h) and _name_match(f, a) for h, a in fav_nomes)

    def dentro(m):
        if usar_top:
            c, f = nomes(m)
            return any(_name_match(c, h) and _name_match(f, a) for _, h, a in top)
        return _prio_de(m) <= pmax

    vis, ocultos = [], []
    for m in matches:
        (vis if dentro(m) or favorito(m) else ocultos).append(m)
    # Mais importante primeiro; dentro da mesma prioridade, jogo com link da Betfair
    # ou da Bolsa de Aposta (ícones B/$) antes dos sem link — é onde dá pra operar
    # com certeza. Estável: o resto mantém a ordem da fonte.
    vis.sort(key=lambda m: (_prio_de(m), 0 if (m.get("link_betfair") or m.get("link_bolsa")) else 1))
    return vis, ocultos


def _ao_vivo_favoritos_da_requisicao():
    """Lê ?fav=id1,id2 e ?favn=[["casa","fora"],...] mandados pelo site (limitados)."""
    ids = [x.strip() for x in (request.args.get("fav") or "").split(",") if x.strip()][:80]
    nomes = []
    try:
        bruto = json.loads(request.args.get("favn") or "[]")
        for par in bruto[:40]:
            if isinstance(par, (list, tuple)) and len(par) == 2 and par[0] and par[1]:
                nomes.append((str(par[0])[:120], str(par[1])[:120]))
    except Exception:
        pass
    return ids, nomes


@app.route("/api/ao-vivo/config", methods=["GET", "POST"])
def api_ao_vivo_config():
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        pmax = _ao_vivo_priority_max()
        modo = _ao_vivo_modo()
        if d.get("priority_max") is not None:
            try:
                pmax = int(d.get("priority_max"))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "priority_max inválido"}), 400
            if not 1 <= pmax <= 1000:
                return jsonify({"ok": False, "error": "priority_max deve ficar entre 1 e 1000"}), 400
        if d.get("modo") is not None:
            if d.get("modo") not in ("top", "prioridade"):
                return jsonify({"ok": False, "error": "modo deve ser 'top' ou 'prioridade'"}), 400
            modo = d["modo"]
        with _ao_vivo_cfg_lock:
            _ao_vivo_cfg["priority_max"], _ao_vivo_cfg["modo"] = pmax, modo
            with open(AO_VIVO_CFG_FILE, "w", encoding="utf-8") as f:
                json.dump({"priority_max": pmax, "modo": modo}, f)
        github_storage.push_file_bg(AO_VIVO_CFG_FILE, "ao_vivo_config.json")
    return jsonify({"ok": True, "priority_max": _ao_vivo_priority_max(), "modo": _ao_vivo_modo()})


@app.route("/api/radar/live")
def api_radar_live():
    """Lista os jogos ao vivo via UniScore (todos os locales + paginação), só das
    ligas dentro do corte de prioridade, da mais importante pra menos."""
    res = _radar_fetch_live_matches()
    todos = res.get("live") or []
    fav_ids, fav_nomes = _ao_vivo_favoritos_da_requisicao()
    vis, ocultos = _ao_vivo_filtra_por_prioridade(todos, fav_ids, fav_nomes)
    resumo = {}
    for m in ocultos:
        k = (m.get("liga") or "", m.get("pais") or "", _prio_de(m))
        resumo[k] = resumo.get(k, 0) + 1
    ligas_ocultas = [{"liga": k[0], "pais": k[1], "priority": k[2], "jogos": n}
                     for k, n in sorted(resumo.items(), key=lambda kv: (kv[0][2], kv[0][0]))]
    return jsonify({**res, "live": vis, "total": len(vis), "total_ao_vivo": len(todos),
                    "ocultos": len(ocultos), "priority_max": _ao_vivo_priority_max(),
                    "modo": _ao_vivo_modo(), "top_scores_ligas": _top_scores_ligas(),
                    "ligas_ocultas": ligas_ocultas})


# ── Cache simples de momentum em memória (evita abrir browser repetidamente) ──
# Achado investigando custo do Railway (2026-09-08): TTL de 30s aqui é só pra
# decidir se REUSA a entrada — nunca removia entrada nenhuma do dict, então
# todo event_id que já passou por "Ao vivo" (centenas por dia, pra sempre)
# ficava ocupando memória até o próximo redeploy. _momentum_cache_prune corta
# entradas com mais de 2h (bem além de qualquer jogo ainda em andamento).
_momentum_cache = {}
_momentum_lock  = threading.Lock()   # proteção para acesso concorrente
_MOMENTUM_CACHE_MAX_AGE = 2 * 3600


def _momentum_cache_prune():
    """Chamado só quando o dict já cresceu bastante — evita custo de varrer
    tudo a cada chamada de _process_momentum (que roda o tempo todo)."""
    if len(_momentum_cache) < 500:
        return
    now = time.time()
    stale = [eid for eid, v in _momentum_cache.items() if now - v.get("ts", 0) > _MOMENTUM_CACHE_MAX_AGE]
    for eid in stale:
        _momentum_cache.pop(eid, None)
    if stale:
        print(f"[momentum-cache] Removidas {len(stale)} entrada(s) velha(s) — {len(_momentum_cache)} restante(s)")

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
    # Só as ligas dentro do corte de prioridade, da mais importante pra menos —
    # as demais nem são gravadas (decisão do usuário, 2026-09-19).
    vis, _ = _ao_vivo_filtra_por_prioridade(matches)
    return [
        {"id": m["id"], "casa": m["home"], "fora": m["away"], "liga": m.get("liga", "")}
        for m in vis
    ]


_sofa_live_cache = {"ts": 0, "events": []}
_sofa_live_lock  = threading.Lock()

import unicodedata

from functools import lru_cache


@lru_cache(maxsize=60000)
def _norm(s: str) -> str:
    """Normaliza string: minúsculo, sem acento, sem caracteres especiais.
    Memoizada (2026-09-19): o cruzamento de nomes do Ao Vivo (centenas de jogos x
    centenas de jogos do Painel, a cada 30s) chamava isso ~15 milhões de vezes por
    atualização com os MESMOS textos, prendendo a CPU do servidor por 10 a 20s."""
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

    words_a, core_a, short_a = _name_features(na)
    words_b, core_b, short_b = _name_features(nb)

    # 3. Palavras com >= 3 chars em comum (anterior era >= 4)
    if words_a & words_b:
        return True

    # 4. Palavras sem sufixos genéricos — evita falso positivo por "FC"/"Sporting"
    if core_a and core_b and core_a & core_b:
        return True

    # 5. Nomes curtos (≤ 4 chars): exige igualdade exata entre os tokens curtos
    if short_a and short_b and short_a == short_b and len(short_a) >= 1:
        return True

    return False


@lru_cache(maxsize=60000)
def _name_features(na: str):
    """(palavras >=3 chars, essas sem sufixos genéricos, palavras <=4 chars) de um
    nome já normalizado — calculado uma vez por nome, não a cada comparação."""
    toks = na.split()
    words = frozenset(w for w in toks if len(w) >= 3)
    return words, frozenset(_strip_suffixes(words)), frozenset(w for w in toks if len(w) <= 4)


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

# Uma busca por vez (2026-09-19). Antes, cada thread que achava o cache vencido
# refazia a varredura inteira (7 locales x até 5 páginas, sequencial) ao mesmo
# tempo que as outras; e quando o UniScore respondia 429 a lista vinha vazia e
# ERA cacheada por 2 min (ou nem cacheada, no caso de _uni_events_today) — cada
# chamada seguinte repetia tudo de novo, o que só piorava o 429 e deixava o Ao
# Vivo sem pressão/odds ("carregando…"). Agora: quem chega com a busca já em
# andamento usa a lista anterior (se houver) em vez de esperar/duplicar, e
# resultado vazio nunca substitui uma lista boa recente.
_uniscore_fetch_lock = threading.Lock()
_UNISCORE_STALE_MAX = 10 * 60   # lista antiga ainda serve de reserva por até 10 min


def _get_uniscore_live_matches():
    """Busca TODAS as partidas ao vivo do UniScore (todos os locales + paginação).
    Cache de 2 minutos. Retorna lista de {id, homeId, awayId, home, away}."""
    with _uniscore_lock:
        if time.time() - _uniscore_cache["ts"] < 120:
            return _uniscore_cache["matches"]
        prev, prev_ts = _uniscore_cache["matches"], _uniscore_cache["ts"]

    if not _uniscore_fetch_lock.acquire(blocking=False):
        if prev and time.time() - prev_ts < _UNISCORE_STALE_MAX:
            return prev            # outra thread já está buscando: não duplica
        _uniscore_fetch_lock.acquire()
    try:
        with _uniscore_lock:       # quem esperava o lock encontra o cache já preenchido
            if time.time() - _uniscore_cache["ts"] < 120:
                return _uniscore_cache["matches"]
        return _get_uniscore_live_matches_fetch(prev, prev_ts)
    finally:
        _uniscore_fetch_lock.release()


def _uniscore_live_events_raw():
    """Eventos ao vivo do UniScore de cada locale ([lista por locale], na ordem de
    _UNISCORE_LOCALES). Os 7 locales são buscados em PARALELO (2026-09-19): em
    sequência levava de 19 a 28s por varredura, e todo pedido do Ao Vivo que
    pegava o cache vencido esperava isso tudo."""
    from concurrent.futures import ThreadPoolExecutor

    def um_locale(locale):
        out, page = [], 1
        while True:
            try:
                r = http_req.post(
                    f"{_UNISCORE_API}/sport/football/events/live-v2/locale/{locale}",
                    headers=_UNISCORE_HEADERS, json={"page": page},
                    params={"language": "pt-BR"}, timeout=12,
                )
                if r.status_code not in (200, 201):
                    break
                data = r.json().get("data") or {}
                out.extend(data.get("events") or [])
                if not (data.get("pagination") or {}).get("hasNextPage"):
                    break
                page += 1
                if page > 5:   # safety cap
                    break
            except Exception as e:
                print(f"[uniscore] Erro live locale={locale} page={page}: {e}")
                break
        return out

    with ThreadPoolExecutor(max_workers=len(_UNISCORE_LOCALES)) as ex:
        return list(ex.map(um_locale, _UNISCORE_LOCALES))


def _get_uniscore_live_matches_fetch(prev, prev_ts):
    all_by_id = {}
    for events in _uniscore_live_events_raw():
        for e in events:
            if e.get("status", {}).get("type") != "inprogress":
                continue
            eid = e["id"]
            if eid in all_by_id:
                continue
            all_by_id[eid] = {
                "id":     eid,
                "homeId": e.get("homeTeam", {}).get("id", ""),
                "awayId": e.get("awayTeam", {}).get("id", ""),
                "home":   e.get("homeTeam", {}).get("name", ""),
                "away":   e.get("awayTeam", {}).get("name", ""),
                # Liga e "priority" (posição da liga no ranking mundial
                # do UniScore: 12 = La Liga, 1000 = sem ranking) — 2026-09-19,
                # pra medir depois quais ligas costumam ter mapa de chutes.
                "liga":     (e.get("tournament") or {}).get("name", "") or "",
                "pais":     ((e.get("tournament") or {}).get("country") or {}).get("name", "") or "",
                "priority": (e.get("tournament") or {}).get("priority"),
            }

    matches = list(all_by_id.values())
    print(f"[uniscore] {len(matches)} partidas ao vivo (todos os locales)")
    if not matches and prev and time.time() - prev_ts < _UNISCORE_STALE_MAX:
        # Busca falhou (429/rede): mantém a lista anterior e tenta de novo em ~20s
        with _uniscore_lock:
            _uniscore_cache["ts"] = time.time() - 100
        return prev
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
            # try/except só no print: nomes com caracteres fora do cp1252 (ş, Č
            # etc) derrubavam a requisição inteira com UnicodeEncodeError no
            # console do Windows local — é só diagnóstico, não pode quebrar o
            # endpoint por causa disso.
            try:
                print(f"[uniscore] Match: {m['home']} vs {m['away']} id={m['id']}")
            except UnicodeEncodeError:
                pass
            return {"id": m["id"], "homeId": m["homeId"], "awayId": m["awayId"],
                    "liga": m.get("liga", ""), "pais": m.get("pais", ""), "priority": m.get("priority")}
    if matches:
        sample = [(m["home"], m["away"]) for m in matches[:5]]
        try:
            print(f"[uniscore] Sem match p/ '{casa}' vs '{fora}'. Amostra: {sample}")
        except UnicodeEncodeError:
            pass
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


# Teto de segurança GLOBAL — no máximo essas tantas buscas completas de
# momentum (as 5 chamadas ao UniScore por partida, dentro de
# _fetch_uniscore_graph) rodando ao mesmo tempo no site inteiro, não importa
# quantas abas/usuários estejam pedindo ao mesmo tempo nem quantos jogos ao
# vivo existam. Achado com o usuário (2026-08-29, dia com 400+ jogos ao vivo):
# sem um limite assim, uma tentativa de acelerar essas buscas (rodando as 5
# chamadas de CADA partida em paralelo) na verdade piorou as coisas — sem
# nenhum teto no total, o número de conexões saindo pro UniScore ao mesmo
# tempo cresce junto com o tráfego, sem limite. Esse semáforo é o oposto:
# um teto fixo que NUNCA é ultrapassado, então o pior que acontece em dia de
# pico é ficar mais lento pra atualizar — nunca sobrecarrega de vez. Um
# pouco acima do número de threads do gunicorn (4) pra dar espaço também
# pros workers de fundo (backup, monitor) conseguirem sua vez.
_uniscore_momentum_semaphore = threading.Semaphore(5)


def _fetch_uniscore_graph(uni_match):
    """Busca graphPoints + estatísticas por período do UniScore.
    uni_match = {id, homeId, awayId}"""
    with _uniscore_momentum_semaphore:
        return _fetch_uniscore_graph_impl(uni_match)


def _fetch_uniscore_graph_impl(uni_match):
    from concurrent.futures import ThreadPoolExecutor
    uniscore_id = uni_match["id"]
    home_id     = uni_match.get("homeId", "")
    away_id     = uni_match.get("awayId", "")

    # As 5 chamadas de um jogo (gráfico, gols, estatísticas, chutes e detalhes)
    # saem EM PARALELO (2026-09-19): em sequência cada jogo levava de 4 a 10s, e
    # com 30 cards na tela a fila passava dos 12s que o site espera — os últimos
    # cards ficavam em "sem dados de pressão ainda".
    ev_url = f"{_UNISCORE_API}/football/event/{uniscore_id}"
    pedidos = {
        "graph": (f"{ev_url}/graph", 12, None),
        "inc":   (f"{ev_url}/incidents", 12, None),
        "shot":  (f"{ev_url}/shotmap", 12, None),
        "ev":    (ev_url, 10, {"language": "pt-BR"}),
    }
    if home_id and away_id:
        pedidos["stats"] = (f"{ev_url}/home/{home_id}/away/{away_id}/statistics", 12, None)

    def _get(url, timeout, params):
        try:
            return http_req.get(url, headers=_UNISCORE_HEADERS, params=params, timeout=timeout)
        except Exception as e:
            return e

    with ThreadPoolExecutor(max_workers=len(pedidos)) as ex:
        futs = {k: ex.submit(_get, *v) for k, v in pedidos.items()}
        resp = {k: f.result() for k, f in futs.items()}

    def _ok(k):
        x = resp.get(k)
        return x if x is not None and not isinstance(x, Exception) else None

    # Graph (momentum)
    r = resp["graph"]
    if isinstance(r, Exception):
        raise r
    r.raise_for_status()
    pts = r.json().get("data", {}).get("graphPoints", [])

    # Incidents (gols)
    goals = []
    try:
        ri = _ok("inc")
        if ri is not None and ri.status_code == 200:
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
            rs = _ok("stats")
            if rs is not None and rs.status_code == 200:
                stats_list = rs.json().get("data", {}).get("statistics", [])
                statistics_periods = _uniscore_stats_to_flat(stats_list)
                print(f"[uniscore] Estatísticas: {list(statistics_periods.keys())}")
        except Exception as es:
            print(f"[uniscore] Stats falhou: {es}")

    # Shotmap
    shotmap = []
    try:
        rsm = _ok("shot")
        if rsm is not None and rsm.status_code == 200:
            raw_shots = (rsm.json().get("data") or {}).get("shotmap") or []
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
    # (antes score_ht_* só nasciam dentro do `if` abaixo: se a chamada de detalhes
    # falhasse, o return quebrava com UnboundLocalError e o card ficava sem pressão)
    score_ht_h = None
    score_ht_a = None
    try:
        re = _ok("ev")
        if re is not None and re.status_code == 200:
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
    opening_odds, source, shotmap=None, odds_history=None, stats_history=None,
    liga_priority=None, pais=""
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
        # Posição da liga no ranking do UniScore (menor = mais importante; 1000 =
        # sem ranking) e país — base pra descobrir que tipo de liga tem chutes.
        "liga_priority": liga_priority,
        "pais":      pais,
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
        # Histórico de odds ao vivo (2026-09-14, pedido do usuário: base de
        # dados pra saber como a odd se move quando sai gol) — a mesma série
        # de pontos {ts, minuto, casa, empate, fora, ou_line, ou_over,
        # ou_under} que já alimenta o gráfico "Price Lines" sob demanda
        # (_live_odds_history em memória, populada pelo _live_odds_prewarm_loop
        # que já roda de qualquer jeito) — aqui só congela ela no arquivo
        # quando a partida termina, junto com "goals" acima (mesmo evento,
        # mesmo timestamp de partida) pra dar pra cruzar odd x minuto do gol
        # depois. Zero busca nova: só grava o que já estava em memória.
        "odds_history":       odds_history or [],
        # Histórico de estatísticas-chave ao vivo (2026-09-14, base pras 5
        # ideias de trading: xG, escanteios, chutes no alvo, toques na área,
        # bloqueados, defesas — ver _stats_history_point) — mesmo espírito
        # do odds_history acima, congela o que já estava em _stats_history
        # (populado de graça dentro de _process_momentum) no momento do save.
        "stats_history":      stats_history or [],
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


# ── Histórico ao vivo de estatísticas-chave (2026-09-14) ────────────────────
# Base pras 5 ideias de trading discutidas com o usuário (xG x movimento de
# odds, escanteios como sinal antecipado, goleiro "roubando" xG, domínio
# estéril vs real). Reaproveita 100% o `stats_live` que _process_momentum já
# busca a cada ciclo (via UniScore, mesmo dado que alimenta a tela
# "Estatísticas + Odds") — ZERO fetch novo, só guarda os poucos campos que as
# análises precisam (não o pacote inteiro de 40+ stats) pra não pesar memória
# à toa — Memória já era o maior item do custo no Railway na conversa em que
# isso foi combinado com o usuário.
_STATS_HISTORY_MAX_POINTS = 500
_STATS_HISTORY_MAX_AGE = 3 * 3600
_stats_history = {}   # event_id -> deque de pontos {ts, minuto, xg_casa/fora, escanteios_casa/fora, ...}
_stats_history_lock = threading.Lock()

_STAT_NAME_CANDIDATES = {
    "escanteios":  ["Corner Kicks", "Corners", "cornerKicks"],
    "chutes_alvo": ["Shots on Target", "Shots On Target", "shotsOnTarget"],
    "toques_area": ["Touches in Box", "Touches In Box", "touches_in_box"],
    "bloqueados":  ["Blocked Shots", "blockedShots"],
    "defesas":     ["Saves", "Goalkeeper Saves"],
    # Indicadores de RITMO do card (2026-09-19): finalizações de dentro da área e
    # chances claras, junto dos toques na área que já eram guardados.
    "fin_area":    ["Shots Inside Box", "shots_inside_box"],
    "chances":     ["Big Chances", "big_chances"],
}


def _stat_pair(stats_live, key):
    for name in _STAT_NAME_CANDIDATES[key]:
        item = stats_live.get(name)
        if item and isinstance(item, dict):
            hv = item.get("homeValue") if item.get("homeValue") is not None else item.get("home", 0)
            av = item.get("awayValue") if item.get("awayValue") is not None else item.get("away", 0)
            try:
                return float(str(hv).replace("%", "").strip() or 0), float(str(av).replace("%", "").strip() or 0)
            except (TypeError, ValueError):
                return None, None
    return None, None


def _stats_history_point(stats_live, xg_live, ts, minuto):
    ponto = {"ts": ts, "minuto": minuto,
             "xg_casa": xg_live.get("home"), "xg_fora": xg_live.get("away")}
    for key in _STAT_NAME_CANDIDATES:
        h, a = _stat_pair(stats_live, key)
        ponto[f"{key}_casa"] = h
        ponto[f"{key}_fora"] = a
    return ponto


_RITMO_METRICAS = ("toques_area", "fin_area", "chances")
_RITMO_JANELA_MIN = 10       # janela dos "últimos N minutos"
_RITMO_SPAN_MIN = 4          # observado por menos que isso, a janela não vale


def _ritmo_do_historico(event_id):
    """Ritmo por minuto de toques na área, finalizações de dentro da área e chances
    claras, calculado do histórico que _process_momentum já acumula a cada consulta
    (zero requisição nova). Só o TOTAL acumulado vem da fonte; o "por minuto" sai da
    diferença entre pontos. A janela usa o MINUTO DE JOGO (não o relógio), então o
    intervalo de 15 min não conta como jogo parado. Devolve None sem histórico."""
    with _stats_history_lock:
        pts = list(_stats_history.get(event_id, []))
    if not pts:
        return None
    ult = pts[-1]
    minuto = ult.get("minuto")
    out = {}
    for key in _RITMO_METRICAS:
        c, f = ult.get(f"{key}_casa"), ult.get(f"{key}_fora")
        if c is None or f is None:
            continue
        item = {"casa": c, "fora": f}
        if minuto and minuto > 0:
            item["med_casa"] = round(c / minuto, 2)
            item["med_fora"] = round(f / minuto, 2)
        if minuto is not None:
            base = next((p for p in pts if p.get("minuto") is not None
                         and p["minuto"] >= minuto - _RITMO_JANELA_MIN
                         and p.get(f"{key}_casa") is not None), None)
            if base is not None and minuto - base["minuto"] >= _RITMO_SPAN_MIN:
                item["j_casa"] = round(c - base[f"{key}_casa"], 1)
                item["j_fora"] = round(f - base[f"{key}_fora"], 1)
                item["j_min"] = minuto - base["minuto"]
        out[key] = item
    return out or None


def _stats_history_prune():
    if len(_stats_history) < 200:
        return
    now = time.time()
    stale = [eid for eid, pts in _stats_history.items()
             if not pts or now - pts[-1]["ts"] > _STATS_HISTORY_MAX_AGE]
    for eid in stale:
        _stats_history.pop(eid, None)


# UniScore devolveu 429: o monitor de fundo (o consumidor de menor prioridade —
# o Ao Vivo é a página principal) para de martelar por 90s pra deixar a cota livre.
_uniscore_backoff = {"until": 0}
_ao_vivo_ativo = {"ts": 0}   # atualizado por /api/radar/momentum (definido aqui pra o monitor enxergar)


def _process_momentum(event_id, casa="", fora="", liga=""):
    """Busca momentum exclusivamente via UniScore (busca por nome de time).
    Cache de 30s (reduzido de 90s a pedido do usuário, 2026-09-01, pra deixar
    o gráfico de pressão do Ao Vivo mais perto de tempo real) pra evitar
    chamadas repetidas.
    """
    global _pattern_tips_cache, _odds_patterns_cache, _stats_patterns_cache
    # Verifica cache primeiro
    with _momentum_lock:
        cached = _momentum_cache.get(event_id)
        if cached and time.time() - cached["ts"] < 30:
            return cached["data"]
        _momentum_cache_prune()

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

        # ── Acumula ponto no histórico de estatísticas (2026-09-14) ─────
        # Mesmo espírito do _live_odds_prewarm_loop pra odds: só guarda o
        # que já foi calculado acima, sem buscar nada a mais. Não salva se
        # já terminou (nesse caso a partida está indo pro save completo
        # logo abaixo, não faz sentido crescer o histórico depois do fim).
        if stats_live and not udata.get("finished"):
            minuto = None
            if pts:
                try:
                    minuto = int(pts[-1].get("minute") or 0)
                except (TypeError, ValueError):
                    minuto = None
            ponto_stats = _stats_history_point(stats_live, xg_live, time.time(), minuto)
            with _stats_history_lock:
                if event_id not in _stats_history:
                    _stats_history[event_id] = deque(maxlen=_STATS_HISTORY_MAX_POINTS)
                _stats_history[event_id].append(ponto_stats)
                _stats_history_prune()

        data = {**udata, "saved": False,
                "xg": xg_live, "pressure_summary": ps_live}

        # ── Acumula shotmap ao vivo no cache separado ─────────────────────
        # O endpoint de shotmap só funciona durante o jogo; ao FT fica vazio.
        # Guardamos no disco para sobreviver a restarts/redeploys do Railway.
        live_shots = udata.get("shotmap", [])
        # DIAGNÓSTICO (2026-08-23 a 2026-09-08) — investigando por que
        # shotmap_history/ tem tão poucas partidas salvas (12, depois 336 de
        # 4877 do momentum_history — 7%). Hipótese original (ligas pequenas
        # sem chute-a-chute) DESCARTADA: mesmo Premier League/Bundesliga/
        # Serie A aparecem repetidas vezes sem shotmap salvo. Achado um bug
        # real em vez disso: o cache abaixo SUBSTITUÍA `live_shots` inteiro a
        # cada poll (condição "só escreve se mudou A CONTAGEM"), sem nunca
        # MESCLAR com o que já tinha sido visto — se o Uniscore devolvesse
        # uma lista MENOR num poll seguinte (lag da fonte, ou só não é
        # estritamente cumulativa), o código jogava fora chutes já
        # capturados. Trocado por merge de verdade por `id` do chute — nunca
        # perde o que já foi visto, só cresce. Mantendo o log de diagnóstico
        # mais um tempo pra confirmar que `ja_teve_antes` fica True com mais
        # frequência agora — remover depois de confirmar a melhora.
        if not udata.get("finished"):
            ja_teve_chutes = event_id in _shotmap_live_cache
            print(f"[shotmap-diag] liga='{liga}' | {casa} x {fora} | chutes_agora={len(live_shots)} | ja_teve_antes={ja_teve_chutes}")
        if live_shots:
            with _shotmap_lock:
                prev = _shotmap_live_cache.get(event_id, [])
                is_new_event = event_id not in _shotmap_live_cache
                merged_by_id = {s.get("id"): s for s in prev}
                merged_by_id.update({s.get("id"): s for s in live_shots})
                merged = list(merged_by_id.values())
                if len(merged) != len(prev):   # só escreve se realmente cresceu
                    _shotmap_live_cache[event_id] = merged
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

                # Histórico de odds ao vivo acumulado durante o jogo (2026-09-14)
                # — mesmo dict em memória que já alimenta o Price Lines sob
                # demanda, só congela aqui no momento de salvar.
                odds_hist = _live_odds_history_for_teams(casa, fora)

                # Histórico de estatísticas-chave ao vivo (2026-09-14) — mesmo
                # espírito do odds_hist acima, congela o que já estava em
                # _stats_history (populado de graça dentro deste mesmo loop)
                # no momento do save.
                with _stats_history_lock:
                    stats_hist = list(_stats_history.get(event_id, []))

                liga = liga or uni_match.get("liga") or ""
                payload = _build_save_payload(
                    event_id=event_id,
                    liga_priority=uni_match.get("priority"), pais=uni_match.get("pais", ""),
                    casa=casa, fora=fora, liga=liga,
                    graph_points=pts,
                    goals=udata.get("goals", []),
                    stats_flat=udata.get("statistics", {}),
                    stats_periods=udata.get("statistics_periods", {}),
                    opening_odds=opening_odds,
                    source="uniscore",
                    shotmap=best_shotmap,
                    odds_history=odds_hist,
                    stats_history=stats_hist,
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
        if "429" in str(e):
            _uniscore_backoff["until"] = time.time() + 90
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
                    espera = _uniscore_backoff["until"] - time.time()
                    if espera > 0:
                        time.sleep(espera)
                    data = _process_momentum(m["id"], m["casa"], m["fora"], m["liga"])
                    if data and data.get("finished"):
                        print(f"[monitor] ✓ Encerrado e salvo: {m['casa']} x {m['fora']}")
                    # Com alguém no Ao Vivo agora, o monitor (menor prioridade) anda
                    # mais devagar pra não disputar a cota do UniScore com a tela.
                    time.sleep(6 if time.time() - _ao_vivo_ativo["ts"] < 90 else 2)
            else:
                print(f"[monitor] {len(live_list)} ao vivo, todos já salvos ou sem jogos.")
        except Exception as e:
            print(f"[monitor] Erro geral: {e}")
        time.sleep(300)


# Inicia a thread de monitoramento (daemon = morre junto com o Flask)
threading.Thread(target=_background_monitor, daemon=True, name="MomentumMonitor").start()

# Restaura dados do GitHub ao iniciar (backtest + momentum_history + shotmap_history + cache).
# _github_sync_done é usado por outros loops de prewarm (ex: odds ao vivo) pra
# esperar essa restauração terminar antes do 1º ciclo — sem isso, um ciclo que
# roda quase instantaneamente correria contra a restauração (mais lenta,
# sequencial) e poderia sobrescrever/pushar um estado local ainda incompleto.
_github_sync_done = threading.Event()


def _github_sync_on_startup_then_flag():
    try:
        github_storage.sync_on_startup(MOMENTUM_DIR, BACKTEST_DIR, DATA_DIR, SHOTMAP_DIR)
        github_storage.pull_directory("forca_history", FORCA_HISTORY_DIR)
    finally:
        _github_sync_done.set()


threading.Thread(
    target=_github_sync_on_startup_then_flag,
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

# Backup de Força — estava totalmente pronto desde a sessão em que foi desenhado,
# mas os threads nunca tinham sido iniciados (ficou parado, pasta forca_history/
# vazia). Ativado agora a pedido do usuário, pra alimentar odds pré-jogo no Replay.
# DESATIVADO DE EMERGÊNCIA (2026-08-30, logo após a migração pro Flashscore):
# o event_id do Flashscore é uma string diferente do NowGoal, então TODO
# jogo já encerrado passou a parecer "novo" pra esse backup — o scan
# enfileirou 785 jogos de uma vez (mesmo dia). Cada item aqui é leve
# sozinho (só grava arquivo + agenda 1 push em background pro GitHub), mas
# 785 pushes bg em sequência rápida pareceu coincidir com o site
# respondendo devagar/dando timeout em produção logo depois do deploy — Ao
# Vivo é a prioridade (ver memória), então desliguei até confirmar a causa
# raiz com calma e, se for isso mesmo, adicionar um corte por data (só
# faz backup de jogo ENCERRADO A PARTIR de quando o Flashscore virou fonte)
# antes de reativar.
# threading.Thread(target=_forca_backup_worker, daemon=True, name="ForcaBackupWorker").start()
# threading.Thread(target=_forca_backup_scan_loop, daemon=True, name="ForcaBackupScan").start()
# PainelOddsPrewarm começa mais abaixo no arquivo (ver _painel_odds_prewarm_loop) —
# precisa que _today2_odds_snapshot/_TODAY2_ODDS_SNAPSHOT_TTL já estejam
# definidos nesse ponto da carga do módulo (senão dá NameError na hora que a
# thread acorda, corrida que já aconteceu aqui: o `.wait()` do
# _github_sync_done retorna quase na hora quando GITHUB_TOKEN não está
# configurado, antes do resto do módulo terminar de carregar).


# Último retorno bom de pressão por jogo (só pra servir quando a fonte falha) e
# marca de "tem usuário com o Ao Vivo aberto agora" (o monitor de fundo, que é o
# consumidor de menor prioridade, desacelera enquanto isso é verdade).
_momentum_last_good = {}   # event_id -> {"ts":, "data":}
_momentum_last_good_lock = threading.Lock()
_MOMENTUM_LAST_GOOD_MAX_AGE = 30 * 60


def _momentum_last_good_remember(event_id, data):
    now = time.time()
    with _momentum_last_good_lock:
        _momentum_last_good[event_id] = {"ts": now, "data": data}
        if len(_momentum_last_good) > 400:
            for k in [k for k, v in _momentum_last_good.items() if now - v["ts"] > _MOMENTUM_LAST_GOOD_MAX_AGE]:
                _momentum_last_good.pop(k, None)


def _momentum_last_good_get(event_id):
    with _momentum_last_good_lock:
        v = _momentum_last_good.get(event_id)
    if v and time.time() - v["ts"] < _MOMENTUM_LAST_GOOD_MAX_AGE:
        return v["data"]
    return None


@app.route("/api/radar/momentum/<event_id>")
def api_radar_momentum(event_id):
    """Busca dados de Attack Momentum do SofaScore via Playwright.
    Query params opcionais: casa, fora, liga — usados ao salvar histórico.
    """
    from flask import request as flask_req

    casa = flask_req.args.get("casa", "")
    fora = flask_req.args.get("fora", "")
    liga = flask_req.args.get("liga", "")

    _ao_vivo_ativo["ts"] = time.time()   # alguém está com o Ao Vivo aberto (ver monitor de fundo)
    data = _process_momentum(event_id, casa, fora, liga)
    ritmo = _ritmo_do_historico(event_id)
    if data and data.get("graphPoints"):
        _momentum_last_good_remember(event_id, data)
        return jsonify({**data, "ritmo": ritmo})
    # Falha da fonte (429/timeout) ou jogo sem dado agora: serve o último gráfico
    # bom em vez de erro — o site trocava o gráfico que já estava na tela por
    # "sem dados de pressão" (2026-09-19).
    fb = _momentum_last_good_get(event_id)
    if fb is not None:
        return jsonify({**fb, "stale": True, "ritmo": ritmo})
    if data is None:
        return jsonify({"error": "Sem dados do SofaScore"}), 503
    return jsonify({**data, "ritmo": ritmo})


# Último mapa de chutes bom de cada jogo (2026-09-19). O Ao Vivo só mostra jogo
# com chute > 0, e essa rota devolvia shots=[] em qualquer falha do UniScore
# (429, timeout, resposta sem dados) — o site entendia "jogo sem chutes" e
# escondia TODOS os cards de uma vez até a próxima varredura dar certo (era o
# "os jogos aparecem, ficam uns minutos e somem todos"). Chutes de um jogo só
# crescem, então numa falha vale servir o último resultado bom.
_shotmap_api_last = {}   # event_id -> {"ts":, "shots":}
_shotmap_api_lock = threading.Lock()
_SHOTMAP_API_MAX_AGE = 4 * 3600


def _shotmap_api_remember(event_id, shots):
    now = time.time()
    with _shotmap_api_lock:
        _shotmap_api_last[event_id] = {"ts": now, "shots": shots}
        if len(_shotmap_api_last) > 600:
            for k in [k for k, v in _shotmap_api_last.items() if now - v["ts"] > _SHOTMAP_API_MAX_AGE]:
                _shotmap_api_last.pop(k, None)


def _shotmap_api_fallback(event_id):
    with _shotmap_api_lock:
        v = _shotmap_api_last.get(event_id)
    if v and v["shots"] and time.time() - v["ts"] < _SHOTMAP_API_MAX_AGE:
        return jsonify({"ok": True, "shots": v["shots"], "source": "cache"})
    return None


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
            # "shotmap" vem null (não lista) em jogo sem mapa de chutes — o `or []`
            # evita o TypeError "'NoneType' object is not iterable" dos logs.
            raw = ((rsm.json().get("data") or {}).get("shotmap")) or []
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
            if shots:
                _shotmap_api_remember(event_id, shots)
                return jsonify({"ok": True, "shots": shots, "source": "live"})
            # Lista vazia: pode ser jogo sem chutes ainda, ou resposta ruim da fonte
            fb = _shotmap_api_fallback(event_id)
            return fb if fb is not None else jsonify({"ok": True, "shots": [], "source": "live"})
    except Exception as e:
        print(f"[shotmap] Erro: {e}")

    # Falha real (429/timeout/erro): serve o último bom; senão avisa ok=False pro
    # site NÃO tratar como "jogo sem chutes".
    fb = _shotmap_api_fallback(event_id)
    return fb if fb is not None else (jsonify({"ok": False, "shots": [], "source": "none"}), 200)


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


def _momentum_detect_over_under_windows(points, over_pct=0.6, under_pct=0.15):
    """Porta das janelas sustentadas 'Momento Over'/'Momento Under' de
    _live2DetectPressureMarkers (JS) — >=4 pontos consecutivos acima (over) ou
    abaixo (under) do limiar, em % do pico daquele jogo. Ao contrário do 'Grande
    Chance', essas janelas não têm time associado no marcador (o Ao Vivo também
    não mostra time nelas) — o sinal é "pressão sustentada" ou "sem pressão",
    não "pressão de X"."""
    n = len(points)
    if n < 4:
        return [], []
    max_val = max((abs(p.get("value") or 0) for p in points), default=1) or 1
    over_thresh  = max_val * over_pct
    under_thresh = max_val * under_pct

    def find_windows(cond):
        windows = []
        i = 0
        while i < n:
            if cond(points[i]):
                j = i
                while j < n and cond(points[j]):
                    j += 1
                if j - i >= 4:
                    windows.append((i, j - 1))
                i = j
            else:
                i += 1
        return windows

    over_idx  = find_windows(lambda p: abs(p.get("value") or 0) >= over_thresh)
    under_idx = find_windows(lambda p: abs(p.get("value") or 0) <= under_thresh)

    def to_markers(idx_windows):
        out = []
        for s, e in idx_windows:
            m_start = points[s].get("minute") or 0
            m_end   = points[e].get("minute") or 0
            out.append({"minute": (m_start + m_end) / 2, "end_minute": m_end})
        return out

    return to_markers(over_idx), to_markers(under_idx)


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


_SIGNAL_STATS_MINUTE_CACHE = {}     # (tipo, time, window_min, bucket) -> {"ts":, "data":}
_SIGNAL_STATS_MINUTE_TTL = 10 * 60  # 10min — mesmo TTL do cache da base (_momentum_load_all_matches)

@app.route("/api/momentum/signal_stats_by_minute")
def api_momentum_signal_stats_by_minute():
    """Estatística de um tipo de sinal (Grande Chance / Momento Over / Momento
    Under) quebrada por faixa de minuto (buckets de N minutos, padrão 5) contra
    toda a base salva em momentum_history — sem combinar tipos de sinal entre si
    (decisão deliberada: combinações têm risco real de comparações múltiplas /
    achar padrão que não é real). Cada faixa já vem com as duas métricas juntas:

    - hits_gol/rate_gol: saiu gol dentro da janela pós-sinal (window_min).
    - hits_placar/rate_placar: o placar do momento exato do sinal (contando só
      os gols até ali) se manteve como placar FINAL da partida.

    Vêm as duas de uma vez pra poder mostrar o detalhe de uma faixa (ex: ao
    clicar numa barra do gráfico) sem precisar de uma segunda consulta."""
    tipo   = request.args.get("tipo", "chance")
    time_f = request.args.get("time", "")
    try:
        window_min = max(1, min(30, int(request.args.get("window", 10))))
    except ValueError:
        window_min = 10
    try:
        bucket = max(1, min(15, int(request.args.get("bucket", 5))))
    except ValueError:
        bucket = 5

    if tipo not in ("chance", "over", "under"):
        return jsonify({"error": "tipo inválido"}), 400

    cache_key = (tipo, time_f, window_min, bucket)
    now = time.time()
    cached = _SIGNAL_STATS_MINUTE_CACHE.get(cache_key)
    if cached and (now - cached["ts"]) < _SIGNAL_STATS_MINUTE_TTL:
        return jsonify(cached["data"])

    matches = _momentum_load_all_matches()
    cfg = _CHANCE_PATTERN_CACHE["data"] or {}
    chance_pct = cfg.get("chance_pct", 0.8)
    over_pct   = cfg.get("over_pct", 0.6)

    def placar_se_manteve(goals, ref_minute):
        return not any((g.get("minute") or 0) > ref_minute for g in goals)

    buckets = {}  # minute_start -> {"total":, "hits_gol":, "hits_placar":}
    def add(minute, hit_gol, hit_placar):
        b_start = (int(minute) // bucket) * bucket
        b = buckets.setdefault(b_start, {"total": 0, "hits_gol": 0, "hits_placar": 0})
        b["total"] += 1
        if hit_gol:
            b["hits_gol"] += 1
        if hit_placar:
            b["hits_placar"] += 1

    for points, goals in matches:
        if tipo == "chance":
            for sp in _momentum_detect_chance_spikes(points, chance_pct, over_pct):
                team = "home" if sp["is_home"] else "away"
                if time_f and team != time_f:
                    continue
                hit_gol = any(g.get("team") == team and 0 <= (g.get("minute") or 0) - sp["minute"] <= window_min for g in goals)
                hit_placar = placar_se_manteve(goals, sp["minute"])
                add(sp["minute"], hit_gol, hit_placar)
        else:
            over_mk, under_mk = _momentum_detect_over_under_windows(points, over_pct)
            for mk in (over_mk if tipo == "over" else under_mk):
                saiu_gol = any(0 <= (g.get("minute") or 0) - mk["end_minute"] <= window_min for g in goals)
                hit_gol = saiu_gol if tipo == "over" else not saiu_gol
                hit_placar = placar_se_manteve(goals, mk["end_minute"])
                add(mk["minute"], hit_gol, hit_placar)

    result = [
        {
            "minute_start": b_start, "minute_end": b_start + bucket - 1,
            "total": b["total"],
            "hits_gol": b["hits_gol"], "rate_gol": round(b["hits_gol"] / b["total"], 4) if b["total"] else 0.0,
            "hits_placar": b["hits_placar"], "rate_placar": round(b["hits_placar"] / b["total"], 4) if b["total"] else 0.0,
        }
        for b_start, b in sorted(buckets.items())
    ]

    data = {
        "tipo": tipo, "time": time_f or "qualquer",
        "window_min": window_min, "bucket_size": bucket, "matches_used": len(matches),
        "buckets": result,
    }
    _SIGNAL_STATS_MINUTE_CACHE[cache_key] = {"ts": now, "data": data}
    return jsonify(data)


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


@app.route("/api/forca/history/match")
def api_forca_history_match():
    """Busca odds pré-jogo/força salvos no Backup de Força casando por time+data.
    forca_history vem do NowGoal (event_id próprio dele) enquanto momentum/shotmap
    vêm de outra fonte — não dá pra confiar que o event_id bata entre os dois, então
    casa pelo nome dos times (mesmo critério fuzzy usado em _find_radar_links)."""
    casa = request.args.get("casa", "")
    fora = request.args.get("fora", "")
    date_str = request.args.get("date", "")
    if not casa or not fora or not date_str:
        return jsonify({"error": "casa, fora e date são obrigatórios"}), 400
    files = glob.glob(os.path.join(FORCA_HISTORY_DIR, f"{date_str}_*.json"))
    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            if _name_match(casa, d.get("home", "")) and _name_match(fora, d.get("away", "")):
                return jsonify(d)
        except Exception:
            continue
    return jsonify({"error": "not found"}), 404


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


# ── Reação da odd ao vivo quando sai gol (2026-09-14) ───────────────────────
# Pedido do usuário: "quero saber pra onde a odd vai se o time casa marcar
# gol, pra onde vai se tomar gol, o mesmo pro visitante — é mais pra eu saber
# meu possível lucro se pegar o gol e possível red se tomar o gol". Mesmo
# padrão arquitetural do bloco de cima (_odds_patterns_cache/_ODDS_BUCKETS_H):
# varre momentum_history, bucketiza, cacheia 30min — só que aqui o campo
# novo usado é "odds_history" (gravado a partir de 2026-09-14, ver
# _build_save_payload) cruzado com "goals" (minuto de cada gol), não mais
# "opening_odds" sozinho. Partidas salvas ANTES de 14/09 simplesmente não
# têm "odds_history" e são puladas (.get retorna [], não quebra nada).
_ODDS_LIVE_BUCKETS = [
    ("1.01–1.30", 1.01, 1.30),
    ("1.31–1.60", 1.31, 1.60),
    ("1.61–2.00", 1.61, 2.00),
    ("2.01–3.00", 2.01, 3.00),
    ("3.01–5.00", 3.01, 5.00),
    (">5.00",     5.01, 99.0),
]
# Janela de reação: olha a odd JUNTO do minuto do gol (pré) e de novo uns
# minutos depois (pós), pra deixar o mercado "assentar" em vez de pegar o
# pico de volatilidade do instante exato do gol. 8min de teto pra achar o
# ponto pós — gol muito perto do fim de tempo (ex: aos 89') não tem reação
# observável depois e é descartado pra essa amostra.
_ODDS_REACTION_MIN_AFTER = 2
_ODDS_REACTION_MAX_AFTER = 8

_odds_goal_reaction_cache = {"ts": 0, "data": None}


def _odds_bucket_label(odd):
    for lbl, lo, hi in _ODDS_LIVE_BUCKETS:
        if lo <= odd <= hi:
            return lbl
    return None


def _odds_reaction_points(odds_history, event_minute, min_after=None, max_after=None):
    """Acha o ponto de odds mais próximo ANTES/NO minuto de um evento (pré) e o
    primeiro disponível de min_after a max_after minutos depois (pós) — janela
    default é a de gol (_ODDS_REACTION_MIN_AFTER/_MAX_AFTER), mas outros sinais
    (2026-09-15: xG x odds, janela mais larga porque é sinal mais lento que gol)
    podem passar a própria janela. Retorna (pre, pos) ou (None, None) se não
    achar os dois."""
    min_after = _ODDS_REACTION_MIN_AFTER if min_after is None else min_after
    max_after = _ODDS_REACTION_MAX_AFTER if max_after is None else max_after
    pre = None
    for p in odds_history:
        m = p.get("minuto")
        if m is None or m > event_minute:
            continue
        if pre is None or m > pre.get("minuto", -1):
            pre = p
    pos = None
    for p in odds_history:
        m = p.get("minuto")
        if m is None:
            continue
        if event_minute + min_after <= m <= event_minute + max_after:
            if pos is None or m < pos.get("minuto", 999):
                pos = p
    return pre, pos


def _compute_odds_goal_reaction():
    # {(role, bucket_label): {"n": int, "sum_pct": float}} — role é "marcou"/"sofreu"
    # (1X2) ou "over"/"under" (Over/Under, 2026-09-15, pedido do usuário: "da
    # para utilizar tambem over e under?").
    acc = {}
    total_partidas = 0
    total_gols_usaveis = 0
    total_ou_gols_usaveis = 0

    for fpath in glob.glob(os.path.join(MOMENTUM_DIR, "*.json")):
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            odds_history = d.get("odds_history") or []
            goals = d.get("goals") or []
            if not odds_history or not goals:
                continue
            total_partidas += 1

            for g in goals:
                minute = g.get("minute")
                team = g.get("team")
                if minute is None or team not in ("home", "away"):
                    continue
                pre, pos = _odds_reaction_points(odds_history, minute)
                if not pre or not pos:
                    continue

                marcou_lado = "casa" if team == "home" else "fora"
                sofreu_lado = "fora" if team == "home" else "casa"

                for role, lado in (("marcou", marcou_lado), ("sofreu", sofreu_lado)):
                    pre_odd = pre.get(lado)
                    pos_odd = pos.get(lado)
                    if not pre_odd or not pos_odd or pre_odd <= 0:
                        continue
                    lbl = _odds_bucket_label(pre_odd)
                    if not lbl:
                        continue
                    pct = (pos_odd - pre_odd) / pre_odd * 100
                    key = (role, lbl)
                    if key not in acc:
                        acc[key] = {"n": 0, "sum_pct": 0.0}
                    acc[key]["n"] += 1
                    acc[key]["sum_pct"] += pct
                total_gols_usaveis += 1

                # Over/Under: só entra na amostra se a LINHA for a mesma antes
                # e depois do gol (_fs_live_odds_ou troca de linha sozinha
                # conforme o placar/tempo — ex: linha pula de 2.5 pra 3.5 já
                # com 0-0 no fim do jogo — comparar odd de linhas diferentes
                # não mostraria reação nenhuma de verdade, só ruído).
                ou_line_pre, ou_line_pos = pre.get("ou_line"), pos.get("ou_line")
                if ou_line_pre is None or ou_line_pre != ou_line_pos:
                    continue
                ou_usavel = False
                for role in ("over", "under"):
                    pre_odd = pre.get(f"ou_{role}")
                    pos_odd = pos.get(f"ou_{role}")
                    if not pre_odd or not pos_odd or pre_odd <= 0:
                        continue
                    lbl = _odds_bucket_label(pre_odd)
                    if not lbl:
                        continue
                    pct = (pos_odd - pre_odd) / pre_odd * 100
                    key = (role, lbl)
                    if key not in acc:
                        acc[key] = {"n": 0, "sum_pct": 0.0}
                    acc[key]["n"] += 1
                    acc[key]["sum_pct"] += pct
                    ou_usavel = True
                if ou_usavel:
                    total_ou_gols_usaveis += 1
        except Exception:
            continue

    buckets_out = []
    for lbl, lo, hi in _ODDS_LIVE_BUCKETS:
        marcou = acc.get(("marcou", lbl))
        sofreu = acc.get(("sofreu", lbl))
        row = {"label": lbl}
        row["marcou_n"]   = marcou["n"] if marcou else 0
        row["marcou_pct"] = round(marcou["sum_pct"] / marcou["n"], 1) if marcou and marcou["n"] else None
        row["sofreu_n"]   = sofreu["n"] if sofreu else 0
        row["sofreu_pct"] = round(sofreu["sum_pct"] / sofreu["n"], 1) if sofreu and sofreu["n"] else None
        buckets_out.append(row)

    ou_buckets_out = []
    for lbl, lo, hi in _ODDS_LIVE_BUCKETS:
        over = acc.get(("over", lbl))
        under = acc.get(("under", lbl))
        row = {"label": lbl}
        row["over_n"]    = over["n"] if over else 0
        row["over_pct"]  = round(over["sum_pct"] / over["n"], 1) if over and over["n"] else None
        row["under_n"]   = under["n"] if under else 0
        row["under_pct"] = round(under["sum_pct"] / under["n"], 1) if under and under["n"] else None
        ou_buckets_out.append(row)

    return {
        "total_partidas_com_dado": total_partidas,
        "total_gols_usaveis": total_gols_usaveis,
        "total_ou_gols_usaveis": total_ou_gols_usaveis,
        "buckets": buckets_out,
        "ou_buckets": ou_buckets_out,
        "computed_at": datetime.now().isoformat(),
    }


@app.route("/api/momentum/odds_goal_reaction")
def api_odds_goal_reaction():
    """Quanto a odd ao vivo costuma se mover, em %, quando um time marca (ou
    sofre) um gol — bucketizado pela odd do time NO MOMENTO do gol (campo
    "buckets", 1X2). "ou_buckets" traz o mesmo cálculo pro Over/Under (odd
    Over/Under NO MOMENTO do gol, só contando gol onde a linha não mudou
    entre antes/depois — ver comentário em _compute_odds_goal_reaction).
    Usado no Price Lines pra estimar lucro potencial (se marcar/pegar Over) /
    red potencial (se sofrer/pegar Under). Cache de 30min — mesmo padrão de
    /api/momentum/odds-patterns,
    o cálculo em si varre todo o momentum_history (pode crescer bastante),
    não pode rodar a cada request."""
    global _odds_goal_reaction_cache
    if (time.time() - _odds_goal_reaction_cache["ts"] < 1800
            and _odds_goal_reaction_cache["data"] is not None):
        return jsonify(_odds_goal_reaction_cache["data"])
    result = _compute_odds_goal_reaction()
    _odds_goal_reaction_cache = {"ts": time.time(), "data": result}
    return jsonify(result)


# ── 5 sinais de trading com stats ao vivo (2026-09-15) ───────────────────────
# Pedido do usuário em 2026-09-14 (a partir do modal "Estatísticas + Odds"):
# "da pra gente criar algumas coisas bacanas com essses dados no trade?".
# Aprovado com "sim" e, no dia seguinte, "tudo que vamos fazer sexta feira
# podemos criar logo agora?" — construído direto em vez de esperar a rotina
# agendada pra sexta (2026-09-18), que foi desativada nesse momento. Mesmo
# padrão arquitetural de tudo isso (scan momentum_history + cache 30min +
# piso mínimo de amostra no frontend), reaproveitando 100% dado que já era
# coletado (odds_history, stats_history, shotmap, statistics) — zero fetch
# novo. A base começou a coletar stats_history em 14/09, então a amostra
# ainda é pequena nessa data — cresce sozinha conforme partidas terminam.

def _stat_flat_pair(stats_flat, key_names, fallback=0.0):
    """Mesmo padrão de resolução de nome de stat usado em _calc_xg (UniScore
    usa Title Case com espaços, várias variações) — extraído aqui pra
    reaproveitar fora de _calc_xg."""
    for k in key_names:
        item = stats_flat.get(k)
        if item and isinstance(item, dict):
            hv = item.get("homeValue")
            av = item.get("awayValue")
            if hv is None: hv = item.get("home", 0)
            if av is None: av = item.get("away", 0)
            try:
                return (float(str(hv).replace("%", "").strip() or 0),
                        float(str(av).replace("%", "").strip() or 0))
            except (TypeError, ValueError):
                pass
    return fallback, fallback


# ── Ideia 1: scalping em "chute perigoso sem gol" ────────────────────────────
# Quando um time cria uma chance clara (chute de dentro da pequena área, "zone
# 0" — mesma zona já usada em _shotmap_feature_vector) e NÃO marca, o que
# costuma acontecer com a odd desse time logo depois? Reaproveita shotmap +
# odds_history (zero coleta nova) e a MESMA _odds_reaction_points de cima.
_SHOT_MISS_ZONE_MAX_X = 12  # "Área Pequena" — mesmo corte de _shotmap_feature_vector


def _compute_shot_miss_reaction():
    acc = {}  # bucket_label -> {"n":, "sum_pct":}
    total_partidas = 0
    total_chances_usaveis = 0
    for fpath in glob.glob(os.path.join(MOMENTUM_DIR, "*.json")):
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            odds_history = d.get("odds_history") or []
            shots = d.get("shotmap") or []
            if not odds_history or not shots:
                continue
            total_partidas += 1
            for s in shots:
                if s.get("shotType") == "goal":
                    continue
                try:
                    x = float(s.get("x", 50))
                except (TypeError, ValueError):
                    continue
                if x > _SHOT_MISS_ZONE_MAX_X:
                    continue
                minute = s.get("minute")
                if minute is None:
                    continue
                lado = "casa" if s.get("isHome") else "fora"
                pre, pos = _odds_reaction_points(odds_history, minute)
                if not pre or not pos:
                    continue
                pre_odd, pos_odd = pre.get(lado), pos.get(lado)
                if not pre_odd or not pos_odd or pre_odd <= 0:
                    continue
                lbl = _odds_bucket_label(pre_odd)
                if not lbl:
                    continue
                pct = (pos_odd - pre_odd) / pre_odd * 100
                if lbl not in acc:
                    acc[lbl] = {"n": 0, "sum_pct": 0.0}
                acc[lbl]["n"] += 1
                acc[lbl]["sum_pct"] += pct
                total_chances_usaveis += 1
        except Exception:
            continue

    buckets_out = []
    for lbl, lo, hi in _ODDS_LIVE_BUCKETS:
        b = acc.get(lbl)
        buckets_out.append({
            "label": lbl,
            "n":   b["n"] if b else 0,
            "pct": round(b["sum_pct"] / b["n"], 1) if b and b["n"] else None,
        })
    return {
        "total_partidas_com_dado": total_partidas,
        "total_chances_usaveis": total_chances_usaveis,
        "buckets": buckets_out,
    }


# ── Ideia 2: escanteios como sinal antecipado de gol ─────────────────────────
# Nos _CORNER_PRE_GOAL_WINDOW minutos antes de um gol, a taxa de escanteios do
# time que marcou fica acima da média dele no resto do jogo? stats_history
# guarda o TOTAL acumulado de escanteios a cada ciclo (não a contagem do
# intervalo), então a taxa da janela é a diferença entre os dois pontos mais
# próximos dela.
_CORNER_PRE_GOAL_WINDOW = 10


def _corner_value_at(stats_history, minute, lado):
    """Total acumulado de escanteios do lado no ponto mais próximo com
    minuto <= o pedido (None se não achar nenhum ponto tão cedo)."""
    melhor = None
    for p in stats_history:
        m = p.get("minuto")
        v = p.get(f"escanteios_{lado}")
        if m is None or v is None or m > minute:
            continue
        if melhor is None or m > melhor[0]:
            melhor = (m, v)
    return melhor[1] if melhor else None


def _compute_corner_goal_signal():
    ratios = []
    total_partidas = 0
    total_gols_usaveis = 0
    for fpath in glob.glob(os.path.join(MOMENTUM_DIR, "*.json")):
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            stats_history = d.get("stats_history") or []
            goals = d.get("goals") or []
            if not stats_history or not goals or len(stats_history) < 3:
                continue
            total_partidas += 1
            minutos = [p.get("minuto") for p in stats_history if p.get("minuto") is not None]
            if not minutos:
                continue
            fim = max(minutos)
            if fim <= 0:
                continue
            for g in goals:
                minute = g.get("minute")
                team = g.get("team")
                if minute is None or team not in ("home", "away") or minute < _CORNER_PRE_GOAL_WINDOW:
                    continue
                lado = "casa" if team == "home" else "fora"
                c_no_gol = _corner_value_at(stats_history, minute, lado)
                c_antes  = _corner_value_at(stats_history, minute - _CORNER_PRE_GOAL_WINDOW, lado)
                c_fim    = _corner_value_at(stats_history, fim, lado)
                c_inicio = _corner_value_at(stats_history, 0, lado) or 0
                if c_no_gol is None or c_antes is None or c_fim is None:
                    continue
                total_jogo = c_fim - c_inicio
                if total_jogo <= 0:
                    continue
                taxa_media = total_jogo / fim
                taxa_pre = (c_no_gol - c_antes) / _CORNER_PRE_GOAL_WINDOW
                if taxa_media <= 0:
                    continue
                ratios.append((taxa_pre - taxa_media) / taxa_media * 100)
                total_gols_usaveis += 1
        except Exception:
            continue

    n = len(ratios)
    return {
        "total_partidas_com_dado": total_partidas,
        "total_gols_usaveis": total_gols_usaveis,
        "n": n,
        "media_pct": round(sum(ratios) / n, 1) if n else None,
    }


# ── Ideia 3: xG x movimento de odds ──────────────────────────────────────────
# Quando um time abre uma vantagem de xG (>= _XG_DIVERGENCE_THRESHOLD) sobre o
# adversário PELA PRIMEIRA VEZ na partida, a odd dele continua encurtando nos
# minutos seguintes (mercado "correndo atrás" do domínio real) ou já tinha
# precificado isso? Janela de reação mais larga que gol (8-15min) porque é um
# sinal mais lento de formar (não é um evento instantâneo como um gol).
_XG_DIVERGENCE_THRESHOLD = 1.0
_XG_REACTION_MIN_AFTER = 8
_XG_REACTION_MAX_AFTER = 15


def _xg_lead_crossings(stats_history):
    """Primeiro minuto em que cada lado abre vantagem de xG >= threshold
    (só a primeira vez por lado, pra não contar o mesmo domínio repetido)."""
    eventos = []
    cruzou = {"casa": False, "fora": False}
    for p in sorted(stats_history, key=lambda p: p.get("minuto") if p.get("minuto") is not None else 9999):
        m = p.get("minuto")
        xc, xf = p.get("xg_casa"), p.get("xg_fora")
        if m is None or xc is None or xf is None:
            continue
        diff = xc - xf
        if diff >= _XG_DIVERGENCE_THRESHOLD and not cruzou["casa"]:
            eventos.append(("casa", m)); cruzou["casa"] = True
        if -diff >= _XG_DIVERGENCE_THRESHOLD and not cruzou["fora"]:
            eventos.append(("fora", m)); cruzou["fora"] = True
    return eventos


def _compute_xg_odds_divergence():
    acc = {}
    total_partidas = 0
    total_eventos_usaveis = 0
    for fpath in glob.glob(os.path.join(MOMENTUM_DIR, "*.json")):
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            stats_history = d.get("stats_history") or []
            odds_history = d.get("odds_history") or []
            if not stats_history or not odds_history:
                continue
            total_partidas += 1
            for lado, minute in _xg_lead_crossings(stats_history):
                pre, pos = _odds_reaction_points(odds_history, minute, _XG_REACTION_MIN_AFTER, _XG_REACTION_MAX_AFTER)
                if not pre or not pos:
                    continue
                pre_odd, pos_odd = pre.get(lado), pos.get(lado)
                if not pre_odd or not pos_odd or pre_odd <= 0:
                    continue
                lbl = _odds_bucket_label(pre_odd)
                if not lbl:
                    continue
                pct = (pos_odd - pre_odd) / pre_odd * 100
                if lbl not in acc:
                    acc[lbl] = {"n": 0, "sum_pct": 0.0}
                acc[lbl]["n"] += 1
                acc[lbl]["sum_pct"] += pct
                total_eventos_usaveis += 1
        except Exception:
            continue

    buckets_out = []
    for lbl, lo, hi in _ODDS_LIVE_BUCKETS:
        b = acc.get(lbl)
        buckets_out.append({
            "label": lbl,
            "n":   b["n"] if b else 0,
            "pct": round(b["sum_pct"] / b["n"], 1) if b and b["n"] else None,
        })
    return {
        "total_partidas_com_dado": total_partidas,
        "total_eventos_usaveis": total_eventos_usaveis,
        "buckets": buckets_out,
    }


# ── Ideia 4: goleiro "roubando" xG ───────────────────────────────────────────
# Quando um time tem MUITO mais defesas do que o xG sofrido explicaria
# (goleiro "roubando" resultado), qual a % desses momentos em que esse time
# acaba sofrendo gol nos _KEEPER_FOLLOWUP_WINDOW minutos seguintes — proxy de
# "sorte que tende a reverter". Só conta 1x por lado por partida (o primeiro
# momento em que cruza o limiar), senão um jogo com 1 goleiro inspirado a
# partida toda pesaria demais sozinho na amostra.
_KEEPER_LUCK_MIN_EXTRA = 1.5
_KEEPER_FOLLOWUP_WINDOW = 15


def _compute_keeper_overperform():
    total_partidas = 0
    eventos_sorte = 0
    sofreu_depois = 0
    for fpath in glob.glob(os.path.join(MOMENTUM_DIR, "*.json")):
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            stats_history = d.get("stats_history") or []
            goals = d.get("goals") or []
            if not stats_history:
                continue
            total_partidas += 1
            ja_contou = {"casa": False, "fora": False}
            for p in sorted(stats_history, key=lambda p: p.get("minuto") if p.get("minuto") is not None else 9999):
                m = p.get("minuto")
                if m is None:
                    continue
                for lado, adversario, quem_marcaria in (("casa", "fora", "away"), ("fora", "casa", "home")):
                    if ja_contou[lado]:
                        continue
                    defesas = p.get(f"defesas_{lado}")
                    xg_sofrido = p.get(f"xg_{adversario}")
                    if defesas is None or xg_sofrido is None:
                        continue
                    if defesas - xg_sofrido < _KEEPER_LUCK_MIN_EXTRA:
                        continue
                    ja_contou[lado] = True
                    eventos_sorte += 1
                    if any(g.get("team") == quem_marcaria and g.get("minute") is not None
                           and m < g.get("minute") <= m + _KEEPER_FOLLOWUP_WINDOW for g in goals):
                        sofreu_depois += 1
        except Exception:
            continue

    return {
        "total_partidas_com_dado": total_partidas,
        "eventos_sorte": eventos_sorte,
        "sofreu_depois": sofreu_depois,
        "pct_sofreu_depois": round(sofreu_depois / eventos_sorte * 100, 1) if eventos_sorte else None,
    }


# ── Ideia 5: domínio estéril vs real ─────────────────────────────────────────
# Times que terminam a partida com posse alta mas SEM criar mais chutes no
# alvo que o adversário — "dominaram" sem converter isso em perigo de verdade.
# Posse/chutes no alvo só existem como snapshot FINAL (campo "statistics",
# não têm série temporal no stats_history), então isso é uma análise
# pós-jogo (não dá pra virar sinal ao vivo com o dado atual — diferente das
# outras 4 ideias).
_STERILE_POSSESSION_MIN = 55.0


def _compute_sterile_dominance():
    total_partidas = 0
    total_dominios = 0
    nao_venceu = 0
    for fpath in glob.glob(os.path.join(MOMENTUM_DIR, "*.json")):
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            stats_flat = d.get("statistics") or {}
            score = d.get("score") or {}
            if not stats_flat or score.get("home") is None or score.get("away") is None:
                continue
            posse_h, posse_a = _stat_flat_pair(stats_flat, ["Ball Possession"])
            if posse_h <= 0 and posse_a <= 0:
                continue
            total_partidas += 1
            alvo_h, alvo_a = _stat_flat_pair(stats_flat, ["Shots on Target", "Shots On Target", "shotsOnTarget"])
            for lado, posse, alvo_proprio, alvo_adv, gols_proprio, gols_adv in (
                ("casa", posse_h, alvo_h, alvo_a, score["home"], score["away"]),
                ("fora", posse_a, alvo_a, alvo_h, score["away"], score["home"]),
            ):
                if posse < _STERILE_POSSESSION_MIN or alvo_proprio > alvo_adv:
                    continue
                total_dominios += 1
                if gols_proprio <= gols_adv:
                    nao_venceu += 1
        except Exception:
            continue

    return {
        "total_partidas_com_dado": total_partidas,
        "total_dominios_estereis": total_dominios,
        "nao_venceu_pct": round(nao_venceu / total_dominios * 100, 1) if total_dominios else None,
    }


# ── Semáforo de risco de gol contra a posição (2026-09-15) ───────────────────
# O usuário opera LAY na pior equipe (zebra) e o problema real dele é tomar gol
# contra a posição. Esta tabela responde, por estado de jogo (pressão dos
# últimos 5min orientada ao favorito + placar), qual a chance de cada lado
# marcar nos próximos 10min — medida em 3.974 partidas do histórico.
#
# A tabela é GERADA LOCALMENTE por gerar_tabela_risco.py e versionada como
# lay_risk_table.json (poucos KB). O servidor só lê o arquivo: varrer milhares
# de JSONs pra recalcular isso seria o tipo de carga que não vale rodar no
# Railway, e o número praticamente não muda com mais um dia de jogos.
_lay_risk_table_cache = {"ts": 0, "data": None}
_LAY_RISK_TABLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "lay_risk_table.json")


@app.route("/api/momentum/lay_risk_table")
def api_lay_risk_table():
    """Tabela de risco de gol por estado de jogo (ver comentário acima).
    Lida do disco 1x e mantida em memória — arquivo estático, some do disco só
    se alguém rodar o gerador de novo."""
    global _lay_risk_table_cache
    if _lay_risk_table_cache["data"] is not None:
        return jsonify(_lay_risk_table_cache["data"])
    try:
        with open(_LAY_RISK_TABLE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)}), 503
    _lay_risk_table_cache = {"ts": time.time(), "data": data}
    return jsonify(data)


_trading_signals_cache = {"ts": 0, "data": None}


@app.route("/api/momentum/trading_signals")
def api_trading_signals():
    """As 5 ideias de sinais de trading combinadas numa resposta só (menos
    round-trip que 5 rotas separadas) — cada uma varre momentum_history
    independente, cache de 30min (mesmo padrão de odds_goal_reaction)."""
    global _trading_signals_cache
    if (time.time() - _trading_signals_cache["ts"] < 1800
            and _trading_signals_cache["data"] is not None):
        return jsonify(_trading_signals_cache["data"])
    result = {
        "shot_miss":   _compute_shot_miss_reaction(),
        "corner_goal": _compute_corner_goal_signal(),
        "xg_odds":     _compute_xg_odds_divergence(),
        "keeper":      _compute_keeper_overperform(),
        "sterile":     _compute_sterile_dominance(),
        "computed_at": datetime.now().isoformat(),
    }
    _trading_signals_cache = {"ts": time.time(), "data": result}
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
    global _uni_events_cache
    ttl = lambda c: 30 if c["data"] else 10     # falha (lista vazia) só vale 10s
    c = _uni_events_cache
    if time.time() - c["ts"] < ttl(c):
        return c["data"]
    # Uma busca por vez (ver comentário em _uniscore_fetch_lock): sem isso, com o
    # UniScore em 429 cada chamada disparava 14 buscas paralelas de novo.
    if not _uni_events_fetch_lock.acquire(blocking=False):
        if c["data"] and time.time() - c["ts"] < _UNISCORE_STALE_MAX:
            return c["data"]
        _uni_events_fetch_lock.acquire()
    try:
        c = _uni_events_cache
        if time.time() - c["ts"] < ttl(c):
            return c["data"]
        return _uni_events_today_fetch()
    finally:
        _uni_events_fetch_lock.release()


_uni_events_fetch_lock = threading.Lock()


def _uni_events_today_fetch():
    from concurrent.futures import ThreadPoolExecutor
    global _uni_events_cache
    prev = _uni_events_cache
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
    print(f"[uniscore] {len(events)} eventos de hoje (ao vivo + agendados, todos os locales, em paralelo)")
    if not events and prev["data"] and time.time() - prev["ts"] < _UNISCORE_STALE_MAX:
        _uni_events_cache = {"ts": time.time() - 20, "data": prev["data"]}   # mantém a lista boa, retenta em ~10s
        return prev["data"]
    _uni_events_cache = {"ts": time.time(), "data": events}
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


# ── Aba "CD" (Sequências do time / confronto direto) + "Dados" do Uniscore ────
# Descoberto interceptando as chamadas reais do site uniscore.com (devtools):
# team-streaks = exatamente o card "Sequências do time" / "Sequências de
# confrontos diretos". "Dados" (Mais De 2.5, BTTS, Gols/Jogo...) não tem
# endpoint próprio — o site calcula na hora a partir do /recent-form de cada
# time, então aqui replicamos o mesmo cálculo (mesmo estilo já usado na aba
# Leitura do Painel Principal).
_UNI_STREAK_NAMED_LABELS = {
    "first_to_score": "Primeiro a marcar",
    "both_team_scoring": "Ambas as equipes marcando",
    "without_clean_sheet": "Sem goleiro sem sofrer gol",
    "no_wins": "Sem vitórias",
    "no_losses": "Sem derrotas",
    "no_draws": "Sem empates",
    "clean_sheet": "Sem sofrer gol",
    "failed_to_score": "Não marcou",
}
_UNI_STREAK_STAT_LABELS = {
    "more_goals": "Mais de {n} gols",
    "less_goals": "Menos de {n} gols",
    "more_corners": "Mais de {n} escanteios",
    "less_corners": "Menos de {n} escanteios",
    "more_cards": "Mais de {n} cartões",
    "less_cards": "Menos de {n} cartões",
}


def _uni_streak_label(name):
    if name in _UNI_STREAK_NAMED_LABELS:
        return _UNI_STREAK_NAMED_LABELS[name]
    m = re.match(r"(more|less)_(goals|corners|cards)_([\d.]+)", name or "")
    if m:
        kind, stat, n = m.groups()
        base = _UNI_STREAK_STAT_LABELS.get(f"{kind}_{stat}")
        if base:
            return base.format(n=n)
    return (name or "").replace("_", " ").capitalize()


def _uni_fetch_team_streaks(eid, home_tid, away_tid, start_ts):
    try:
        r = http_req.get(
            f"{UNISCORE_BASE}/football/event/{eid}/home/{home_tid}/away/{away_tid}/start-time/{start_ts}/team-streaks?language=pt-BR",
            headers=UNISCORE_HEADERS, timeout=6)
        d = r.json().get("data", {})
        def parse(items):
            out = []
            for it in (items or []):
                out.append({
                    "label": _uni_streak_label(it.get("name")),
                    "team": "Casa" if it.get("team") == "Home" else "Fora",
                    "value": it.get("value"),
                })
            return out
        return {"geral": parse(d.get("general")), "confronto_direto": parse(d.get("head2head"))}
    except Exception:
        return {"geral": [], "confronto_direto": []}


def _uni_fetch_analytics(eid):
    """Aba 'Dados' de verdade — achado interceptando a rede do uniscore.com. Vem
    pronto do próprio Uniscore (fonte deles é season-long, tipo FootyStats), não é
    cálculo nosso — por isso os números batem exatamente com o app deles."""
    try:
        r = http_req.get(f"{UNISCORE_BASE}/football/event/{eid}/analytics?language=pt-BR",
                         headers=UNISCORE_HEADERS, timeout=6)
        d = r.json().get("data", r.json())
        return {
            "home_ppg": d.get("home_ppg"), "away_ppg": d.get("away_ppg"),
            "over25_pct": d.get("o25_potential"), "over15_pct": d.get("o15_potential"),
            "btts_pct": d.get("btts_potential"), "gols_jogo": d.get("avg_potential"),
            "escanteios": d.get("corners_potential"), "cartoes": d.get("cards_potential"),
        }
    except Exception:
        return None


def _uni_cd_dados_one(casa, fora):
    ev = _uni_find(casa, fora)
    if not ev:
        return {"found": False}
    eid = ev.get("id")
    home_tid = ev.get("homeTeam", {}).get("id", "")
    away_tid = ev.get("awayTeam", {}).get("id", "")
    start_ts = ev.get("startTimestamp", 0)

    streaks = _uni_fetch_team_streaks(eid, home_tid, away_tid, start_ts)
    analytics = _uni_fetch_analytics(eid)

    # Cartões (e escanteios/placar) da PARTIDA em si já vêm de graça nesse
    # mesmo evento (achado investigando a metodologia das "sequências" pro
    # usuário) — o app já usava esse objeto só pra achar o id/times, sem
    # reparar que homeScore/awayScore trazem yellow_card/red_card prontos.
    # Só faz sentido pra jogo ENCERRADO (senão vem tudo None/0 mesmo).
    hs, as_ = ev.get("homeScore") or {}, ev.get("awayScore") or {}
    cartoes_partida = None
    if (ev.get("status") or {}).get("type") == "finished":
        cartoes_partida = {
            "casa": {"amarelo": hs.get("yellow_card"), "vermelho": hs.get("red_card")},
            "visitante": {"amarelo": as_.get("yellow_card"), "vermelho": as_.get("red_card")},
        }

    return {
        "found": True, "casa": ev.get("homeTeam", {}).get("name"), "fora": ev.get("awayTeam", {}).get("name"),
        "streaks": streaks, "dados": analytics, "cartoes_partida": cartoes_partida,
    }


@app.route("/api/uniscore/cd_dados")
def api_uniscore_cd_dados():
    casa = request.args.get("casa", "")
    fora = request.args.get("fora", "")
    if not casa or not fora:
        return jsonify({"error": "casa e fora obrigatórios"}), 400
    return jsonify(_uni_cd_dados_one(casa, fora))


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

_fs_live_cache = {}  # { day_offset: {"ts":, "data":} }

def _fs_all_matches(day_offset=0):
    """Lista de TODAS as partidas do dia no feed (agendadas + ao vivo + encerradas),
    com liga/país (o feed lista um bloco "ZA" (liga) seguido dos jogos "AA" daquela
    liga, em ordem — então vamos guardando a liga atual enquanto percorremos).

    day_offset: 0 = hoje (padrão, usado por tudo que já existia), -1 = ontem,
    +1 = amanhã etc. Descoberto testando ao vivo (2026-08-31): o path
    "f_1_{X}_{X}_pt-br_1" filtra pelo dia X dias a partir de hoje — sem isso,
    o Painel só conseguia mostrar "hoje" (usado pra atender pedido do usuário
    de ver os jogos de ontem)."""
    cache_entry = _fs_live_cache.get(day_offset, {"ts": 0, "data": []})
    if time.time() - cache_entry["ts"] < 30 and cache_entry["data"]:
        return cache_entry["data"]
    try:
        text = _fs_get(f"f_1_{day_offset}_{day_offset}_pt-br_1")
    except Exception:
        return cache_entry["data"]
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
    _fs_live_cache[day_offset] = {"ts": time.time(), "data": matches}
    return matches

_BRT_OFFSET = timedelta(hours=-3)  # Brasília não tem mais horário de verão desde 2019 -- fuso fixo

def _brt_today():
    return (datetime.utcnow() + _BRT_OFFSET).date()

def _fs_all_matches_brt(target_date):
    """Mesma ideia de _fs_all_matches, mas alinhada ao DIA CIVIL DE BRASÍLIA em
    vez do dia do feed (que segue UTC). Sem isso, a aba "Hoje" do Painel virava
    o dia 3h mais cedo que o Brasil: nas últimas ~3h de cada dia brasileiro
    (21h-meia-noite BRT = 00h-03h UTC do dia seguinte), "Hoje" já mostrava o
    próximo dia UTC inteiro (quase tudo "Agendado", sem placar) -- justo no
    horário de pico de uso do site (achado investigando reclamação do usuário,
    2026-08-31/09-01: 227 de 237 jogos de "Hoje" já eram do dia UTC seguinte
    às 21h50 BRT).

    Como o fuso é fixo (-3h, sem DST), um dia civil de Brasília sempre cai em
    exatamente 2 dias do feed (UTC): o dia UTC "equivalente" e o seguinte (o
    dia BRT começa às 03h UTC do próprio dia e termina às 02h59 UTC do dia
    seguinte). Busca os 2 (cada um já cacheado 30s por _fs_all_matches) e
    filtra pelo kickoff_ts real de cada partida -- mais correto que confiar no
    agrupamento por dia do próprio feed."""
    utc_now = datetime.utcnow()
    window_start_utc = datetime.combine(target_date, datetime.min.time()) - _BRT_OFFSET
    window_end_utc = window_start_utc + timedelta(days=1)
    offset_start = (window_start_utc.date() - utc_now.date()).days
    seen_ids = set()
    matches = []
    for off in (offset_start, offset_start + 1):
        for m in _fs_all_matches(off):
            try:
                ts = int(m.get("kickoff_ts") or 0)
            except (TypeError, ValueError):
                continue
            if not ts:
                continue
            match_dt_utc = datetime.utcfromtimestamp(ts)
            if not (window_start_utc <= match_dt_utc < window_end_utc):
                continue
            mid = m.get("id")
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            matches.append(m)
    return matches

def _fs_find_match(casa, fora):
    """Acha o jogo no feed pelo nome dos times (fuzzy, mesma técnica do _uni_find).
    Busca no pool de "hoje" em horário de Brasília (_fs_all_matches_brt, mesma
    fonte que o Painel usa pra decidir o que é "Hoje" -- ver comentário lá)
    e cai pro dia anterior se não achar, pra manter "Análise da partida"/H2H
    consistente com o que a lista principal do Painel está mostrando (sem
    isso, um jogo perto da virada do dia podia aparecer em "Hoje" no Painel
    mas o modal dizia "não achei", já que cada um olhava um recorte de dia
    diferente)."""
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

    def _search(pool):
        best, best_score = None, 0
        for m in pool:
            h, a = norm(m["home"]), norm(m["away"])
            # Testa nos dois sentidos (casa/fora direto E invertido) — o mesmo jogo
            # às vezes vem com mandante/visitante trocado entre a fonte do Ao Vivo
            # (NowGoal) e a do Flashscore (usada só aqui pra tabela/H2H), então uma
            # comparação só na ordem "direta" perdia esses jogos.
            sc_direto = side_score(nc, h) and side_score(nf, a) and (side_score(nc, h) + side_score(nf, a))
            sc_invertido = side_score(nc, a) and side_score(nf, h) and (side_score(nc, a) + side_score(nf, h))
            score = max(sc_direto or 0, sc_invertido or 0)
            if score == 0:
                continue
            if score > best_score:
                best_score = score
                best = m
        return best if best_score >= 4 else None

    hoje_brt = _brt_today()
    return _search(_fs_all_matches_brt(hoje_brt)) or _search(_fs_all_matches_brt(hoje_brt - timedelta(days=1)))

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

# Pool dedicado só pro prewarm de HT do Painel Principal (_painel_ht_prewarm_
# loop) — antes disputava _fs_event_pool com Backtest/H2H/odds/ranking, que
# usam esse pool por até 1 minuto de cada vez, deixando o lote de até 80 HTs
# quase sempre incompleto (medido em produção: só 4 de 209 jogos encerrados
# com HT preenchido). Isolado igual ao par acima, pra não voltar a acontecer.
_painel_ht_pool = ThreadPoolExecutor(max_workers=10)

# Pool dedicado pro prewarm de odds AO VIVO (_live_odds_prewarm_loop, mais
# abaixo) — mesmo motivo dos dois acima, um domínio novo (2.ds.lsapp.eu) que
# não tem nada a ver com o resto, sem disputar pool com ninguém.
_live_odds_pool = ThreadPoolExecutor(max_workers=8)


# Em dias com muitos jogos (200+), buscar odds de TODOS os agendados demora
# minutos (até 3 tentativas de casa de apostas x timeout por jogo, dividido
# entre poucos workers). Limita aos próximos N jogos por horário — cobre o
# que realmente dá pra apostar em breve, sem travar a busca.
_BT2_MATCHES_MAX_CANDIDATOS = 60


_BT2_LIVE_MAX_CANDIDATOS = 60


def _bt2_matches_candidatos(include_finished=False):
    """Jogos de hoje pra quem o servidor mantém odds quentes em segundo plano: os
    _BT2_MATCHES_MAX_CANDIDATOS próximos por horário (agendados de verdade).
    Com include_finished=True (nome antigo, mantido pelo chamador) entram também os
    jogos AO VIVO agora, pro Ao Vivo mostrar as odds deles.

    CORREÇÃO (2026-09-19): com include_finished=True esta função pegava agendados
    E ENCERRADOS e cortava os 60 primeiros por horário — como os encerrados do dia
    (900+) têm horário anterior, as 60 vagas ficavam TODAS com jogos já acabados
    (medido: 60 de 60 encerrados, nenhum por começar), e os Próximos Jogos nunca
    tinham odds (\"1 - X - 2 -\"), além de gastar 60 jogos x 6 mercados x 3 casas a
    cada 5 min à toa. Encerrados não entram mais."""
    now_ts = time.time()

    def _still_scheduled(m):
        # Jogo de liga menor às vezes fica preso em status "1" (Agendado) mesmo
        # horas depois do apito — o Flashscore não atualiza. Sem essa checagem, um
        # jogo assim (kickoff_ts no passado) ordena pra FRENTE e rouba vaga.
        try:
            return float(m.get("kickoff_ts") or 0) > now_ts
        except (TypeError, ValueError):
            return True

    def _ts(m):
        try:
            return float(m.get("kickoff_ts") or 0)
        except (TypeError, ValueError):
            return 0.0

    todos = _fs_all_matches()
    futuros = sorted((m for m in todos if m.get("status") == "1" and _still_scheduled(m)), key=_ts)
    candidatos = futuros[:_BT2_MATCHES_MAX_CANDIDATOS]
    if include_finished:
        ao_vivo = sorted((m for m in todos if m.get("status") == "2"), key=_ts)
        candidatos += ao_vivo[:_BT2_LIVE_MAX_CANDIDATOS]
    return candidatos


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
            # Só 1X2 e Over/Under: são os únicos que a tela usa (cards do Ao Vivo e
            # Próximos). Antes buscava os 6 mercados x até 3 casas por jogo.
            _, markets = _fs_odds_all_markets_any_bookmaker(
                m["id"], pool=_bt2_matches_market_pool, markets_wanted=["1x2", "over_under"])
        except Exception:
            markets = {}
        return m, markets

    # Publica em LOTES de 20 (2026-09-19): antes o cache só era gravado depois de
    # TODOS os jogos (cerca de 3 min em cache frio, ex: logo após um deploy), então
    # os Próximos Jogos ficavam sem odds esse tempo todo. Agora cada lote já fica
    # visível; o que existia do ciclo anterior continua valendo até ser refeito.
    antigo = list(_today2_odds_snapshot_cache["data"] or [])
    snapshot = []
    for i in range(0, len(candidatos), 20):
        snapshot.extend(_bt2_matches_pool.map(_fetch_one, candidatos[i:i + 20]))
        vistos = {m["id"] for m, _ in snapshot}
        _today2_odds_snapshot_cache["data"] = snapshot + [x for x in antigo if x[0]["id"] not in vistos]
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

# ── Odds AO VIVO (movimento em tempo real, não é a odds pré-jogo cacheada de
# FS_ODDS_MARKETS acima) — endpoint GraphQL separado (2.ds.lsapp.eu) que o
# próprio Flashscore usa pra alimentar a aba "Odds Ao Vivo" da página de
# partida. Devolve valor ATUAL + valor de ABERTURA + se subiu/desceu desde a
# última mudança, já calculado por eles (não precisamos comparar valor
# anterior nós mesmos). Achado inspecionando a rede do navegador numa partida
# ao vivo de verdade (2026-09-02) — pedido do usuário: "existe um site onde
# as odds se movimentem em tempo real?" -> "no flashscore tem odds ao vivo"
# -> "quero colocar aqui... pra criar indicadores usando elas" — confirmado
# só bet365 (mais simples) e só 1X2 + Over/Under, por enquanto.
_LIVE_ODDS_BASE = "https://2.ds.lsapp.eu/pq_graphql"
# Hash de "persisted query" do GraphQL (Automatic Persisted Queries) — não é
# um token de sessão (testado repetido em momentos diferentes, sempre
# funciona), mas é um id fixo do LADO DO SERVIDOR do Flashscore pra essa
# query específica. Se um dia eles trocarem a query (redeploy do site deles),
# esse hash pode parar de funcionar (a resposta vira 404 "Query not stored")
# sem nenhum aviso — só descobrindo de novo inspecionando a rede do navegador
# numa partida ao vivo real. _fs_live_odds_raw já trata isso como "sem dado"
# (exceção capturada, cache antigo permanece).
_LIVE_ODDS_HASH = "dloou"

def _fs_live_odds_raw(event_id, bet_type, bookmaker_id):
    r = http_req.get(_LIVE_ODDS_BASE, params={
        "_hash": _LIVE_ODDS_HASH, "eventId": event_id, "bookmakerId": bookmaker_id,
        "betType": bet_type, "betScope": "FULL_TIME",
    }, headers=FS_HEADERS, timeout=8)
    r.raise_for_status()
    ov = ((r.json().get("data") or {}).get("findEventById") or {}).get("updateLiveOddsOverview")
    return ov

def _live_odds_item(d):
    if not d:
        return None
    return {"value": d.get("value"), "opening": d.get("opening"), "change": (d.get("change") or {}).get("type")}

# Tenta bet365/Betano/Tipsport NESSA ORDEM (mesma lista/ordem de
# FS_ODDS_BOOKMAKERS, odds pré-jogo) e usa a 1ª que tiver odds ao vivo
# de verdade pra esse jogo — pedido do usuário (2026-09-02) depois de ver
# que, com só bet365, boa parte dos jogos de ligas menores (Brasil
# regional, Nicarágua, República Dominicana etc.) nunca mostrava nada:
# cada casa cobre um conjunto diferente de jogos com odds ao vivo, então
# a soma cobre mais que qualquer uma sozinha. Custo: até 3x mais chamadas
# por jogo ao vivo (aceito pelo usuário, sabendo do trade-off).
def _fs_live_odds_1x2(event_id):
    for bid, _name in FS_ODDS_BOOKMAKERS:
        try:
            ov = _fs_live_odds_raw(event_id, "HOME_DRAW_AWAY", bid)
        except Exception:
            continue
        if not ov:
            continue
        return {"casa": _live_odds_item(ov.get("home")), "empate": _live_odds_item(ov.get("draw")), "fora": _live_odds_item(ov.get("away"))}
    return None

def _fs_live_odds_ou(event_id, target_line=2.5):
    """Pega a linha de Over/Under mais próxima de 2.5 entre as oferecidas AGORA
    (mudam conforme o placar/tempo de jogo — ex: 0-0 no 2º tempo só costuma
    ter linhas baixas tipo 1.5/1.75) — mesmo critério já usado em
    _fs_extract_odds_fields pra odds pré-jogo. Mesmo fallback bet365/Betano/
    Tipsport de _fs_live_odds_1x2."""
    for bid, _name in FS_ODDS_BOOKMAKERS:
        try:
            ov = _fs_live_odds_raw(event_id, "OVER_UNDER", bid)
        except Exception:
            continue
        opps = (ov or {}).get("opportunities") or []
        if not opps:
            continue
        best, best_diff = None, None
        for o in opps:
            try:
                linha = float((o.get("handicap") or {}).get("value"))
            except (TypeError, ValueError):
                continue
            diff = abs(linha - target_line)
            if best_diff is None or diff < best_diff:
                best, best_diff = o, diff
        if not best:
            continue
        return {"line": (best.get("handicap") or {}).get("value"), "over": _live_odds_item(best.get("over")), "under": _live_odds_item(best.get("under"))}
    return None

# Cache de odds ao vivo, mantido quente em BACKGROUND (_live_odds_prewarm_loop)
# — mesmo motivo de sempre (_painel_ht_cache etc.): _painel_fetch_matches_
# flashscore só LÊ isso, nunca busca na hora.
_live_odds_cache = {}
_live_odds_lock = threading.Lock()
_LIVE_ODDS_TTL = 30  # mesma cadência "quase tempo real" já usada pro resto do Ao Vivo

# ── Histórico de odds ao vivo, SÓ SOB DEMANDA (2026-09-12, gráfico "Price
# Lines" — pedido do usuário depois de ler um artigo sobre trading na
# Betfair). Diferente da versão removida mais cedo nesta sessão: aqui ninguém
# busca isso automaticamente pra nenhum card — o front só chama o endpoint
# abaixo quando o usuário clica pra abrir o gráfico de UM jogo específico.
# A COLETA em si (esse loop, que já roda de qualquer jeito pra manter a
# pílula "ODDS AO VIVO" quente) só ganhou mais uma linha pra também guardar
# cada leitura num histórico limitado — não é uma chamada de rede a mais,
# só um dict a mais na memória.
_LIVE_ODDS_HISTORY_MAX_POINTS = 500   # 500 * 30s ≈ 4h10 — folga generosa até pro jogo mais demorado
_LIVE_ODDS_HISTORY_MAX_AGE = 3 * 3600  # partidas encerradas há mais de 3h saem do cache
_live_odds_history = {}    # event_id -> deque de pontos {ts, minuto, casa, empate, fora, ou_line, ou_over, ou_under}
_live_odds_history_lock = threading.Lock()
# event_id (Flashscore) -> (home, away). O histórico acima é indexado pelo ID do
# Flashscore, mas o salvamento da partida (auto-save em _process_momentum) só
# conhece o ID do UniScore — sem esse dicionário de nomes, a busca nunca
# achava nada e odds_history era gravado VAZIO em todo jogo (bug achado em
# 2026-09-18: 0 de 127 arquivos desde 14/09 tinham o histórico).
_live_odds_names = {}


def _live_odds_history_for_teams(casa, fora):
    """Histórico de odds ao vivo de uma partida achada pelo NOME dos times
    (ponte entre o ID do UniScore e o do Flashscore). Vazio se não achar."""
    with _live_odds_history_lock:
        for eid, (h, a) in _live_odds_names.items():
            if _name_match(casa or "", h or "") and _name_match(fora or "", a or ""):
                return list(_live_odds_history.get(eid, []))
    return []


def _live_odds_history_point(d, ts, kickoff_ts):
    """Monta 1 ponto da série a partir do mesmo dict {'1x2':..., 'ou':...} já
    calculado pro snapshot (_live_odds_cache) — não busca nada novo."""
    x1x2 = (d or {}).get("1x2") or {}
    ou    = (d or {}).get("ou") or {}

    def _val(x):
        if not x:
            return None
        try:
            return float(x.get("value"))
        except (TypeError, ValueError):
            return None

    try:
        minuto = int((ts - float(kickoff_ts)) / 60) if kickoff_ts else None
    except (TypeError, ValueError):
        minuto = None

    return {
        "ts": ts, "minuto": minuto,
        "casa":     _val(x1x2.get("casa")),
        "empate":   _val(x1x2.get("empate")),
        "fora":     _val(x1x2.get("fora")),
        "ou_line":  ou.get("line"),
        "ou_over":  _val(ou.get("over")),
        "ou_under": _val(ou.get("under")),
    }


def _live_odds_history_prune():
    """Só varre quando já cresceu bastante — remove partidas cujo último
    ponto é mais velho que o teto (jogo já encerrou há muito tempo, ninguém
    mais vai abrir o gráfico dele)."""
    if len(_live_odds_history) < 200:
        return
    now = time.time()
    stale = [eid for eid, pts in _live_odds_history.items()
             if not pts or now - pts[-1]["ts"] > _LIVE_ODDS_HISTORY_MAX_AGE]
    for eid in stale:
        _live_odds_history.pop(eid, None)
        _live_odds_names.pop(eid, None)


def _live_odds_prewarm_loop():
    """Mantém _live_odds_cache quente (usado pela pílula "ODDS AO VIVO" do
    card, via /api/painel/matches) e acumula cada leitura em _live_odds_history
    (usado só sob demanda pelo gráfico "Price Lines" — ver comentário acima)."""
    _github_sync_done.wait(timeout=120)
    while True:
        try:
            fs_matches = _fs_all_matches_brt(_brt_today())
            live_ids = [m["id"] for m in fs_matches if m.get("status") == "2" and m.get("id")]
            kickoff_by_id = {m["id"]: m.get("kickoff_ts") for m in fs_matches}
            names_by_id = {m["id"]: (m.get("home"), m.get("away")) for m in fs_matches}

            def _fetch(eid):
                try:
                    return eid, {"1x2": _fs_live_odds_1x2(eid), "ou": _fs_live_odds_ou(eid)}
                except Exception:
                    return eid, None

            novo = {}
            for eid, d in _live_odds_pool.map(_fetch, live_ids):
                if d and (d.get("1x2") or d.get("ou")):
                    novo[eid] = d
            with _live_odds_lock:
                _live_odds_cache.clear()
                _live_odds_cache.update(novo)

            ts_agora = time.time()
            with _live_odds_history_lock:
                for eid, d in novo.items():
                    ponto = _live_odds_history_point(d, ts_agora, kickoff_by_id.get(eid))
                    if eid not in _live_odds_history:
                        _live_odds_history[eid] = deque(maxlen=_LIVE_ODDS_HISTORY_MAX_POINTS)
                    _live_odds_history[eid].append(ponto)
                    _live_odds_names[eid] = names_by_id.get(eid) or ("", "")
                _live_odds_history_prune()
        except Exception as e:
            print(f"[live-odds-prewarm] Erro: {e}")
        time.sleep(_LIVE_ODDS_TTL)


@app.route("/api/live_odds_history/<event_id>")
def api_live_odds_history(event_id):
    """Série histórica de odds ao vivo (1X2 + Over/Under) pra essa partida —
    base do gráfico "Price Lines" (média móvel), chamado só quando o usuário
    abre o gráfico de um jogo específico, nunca automaticamente."""
    with _live_odds_history_lock:
        pts = list(_live_odds_history.get(event_id, []))
    return jsonify({"ok": True, "event_id": event_id, "points": pts})


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


# ── ABA JOGO — MÉDIAS GERAIS — baseline pra comparação: qual a média de cada
# estatística (vitórias, gols, ambas marcam, over/under...) entre TODOS os
# times de hoje, não só o time que o usuário está olhando. Reaproveita
# _laycasa_home_rows/_laycasa_away_rows (mesma extração de linhas do Mapa de
# Sugestões) — um único fetch de H2H por jogo já dá tanto a amostra "casa"
# quanto "fora". Recalculada 1x por dia (não faz sentido recalcular toda hora,
# a média de centenas de times não muda de uma hora pra outra).
# ── Utilitários compartilhados que sobraram do antigo Backtest/Mapa (2026-09-19) ──
# A aba Backtest, a simulação diária e a masterlist foram removidas por completo; só
# ficaram estas funções porque /api/jogo/medias_gerais (Tendências) ainda as usa.
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

_BT2_MAX_HISTORICAL_IDS = 400
_BTCS_MAX_HISTORICAL_IDS = _BT2_MAX_HISTORICAL_IDS

def _bt2_parse_score(s):
    m = re.match(r"^(\d+):(\d+)$", s or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


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
    # Pedido do usuário (2026-09-12): % de partidas em que o time não marcou
    # nenhum gol próprio depois do minuto 80 (inclui acréscimos — os minutos
    # já vêm normalizados tipo "45+2" -> 47 antes de chegar aqui, ver
    # _fs_goal_minutes). Mesmo cálculo do gêmeo em JS (_jogoGoalPatternStats).
    sem_gol_apos_80 = 0

    for r in gm_rows:
        pro, contra = r["gm_own"], r["gm_opp"]
        if not any(m > 80 for m in pro):
            sem_gol_apos_80 += 1
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
        "semGolApos80_pct": round(sem_gol_apos_80 / total_com_gm * 100, 1) if total_com_gm else None,
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


@app.route("/api/today2/odds_raw")
def api_today2_odds_raw():
    """Odds cruas (todos os mercados: 1x2, over_under, ambos_marcam, placar_exato
    etc) de todos os jogos de hoje, pro filtro por odds da lista da aba Hoje.
    Reaproveita o MESMO snapshot cacheado que já alimenta o Filtro de
    Metodologias e a classificação de Parâmetros (_today2_odds_snapshot) —
    não dispara nenhuma busca nova, só reexpõe os mercados crus em vez da
    versão já resumida em tiers (favorito/parelho etc)."""
    force = request.args.get("refresh") == "1"
    result = {}
    for m, markets in _today2_odds_snapshot(force=force):
        if markets:
            result[str(m["id"])] = markets
    return jsonify({"markets": result})


@app.route("/api/match/live/<path:match_id>")
def api_match_live(match_id):
    """Busca detalhes ao vivo do StatArea para qualquer partida. Se casa/fora
    forem passados via query string, também recalcula Convicção e Confronto
    Direto em cima desses detalhes recém-buscados (senão ficariam presos aos
    dados incompletos da 1ª resposta de /api/match/<id>)."""
    from scraper import fetch_match_details, create_session
    url = f"https://www.statarea.com/compare/teams/{match_id}"
    try:
        session = create_session()
        details = fetch_match_details(url, session=session)
        resp = {"detalhes": details, "url_detalhes": url}
        casa, fora = request.args.get("casa"), request.args.get("fora")
        if casa and fora:
            m = {"casa": casa, "fora": fora, "detalhes": details}
            resp["convicao_casa"], resp["h2h_confronto_direto"] = _convicao_score(m, True)
            resp["convicao_fora"], _ = _convicao_score(m, False)
        return jsonify(resp)
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


# ── Alerta de Telegram 15 min antes do jogo favoritado (2026-09-18) ──────────
# Pedido do usuário: favoritou um jogo em "Próximos Jogos" → avisa no Telegram
# quando faltar 15 minutos pro início. Não acompanha jogo ao vivo nem faz
# nenhuma busca externa: só compara o horário de início (que o site manda junto
# com o favorito) com o relógio, a cada 30s — custo praticamente zero.
# O favorito vive no SERVIDOR (arquivo sincronizado com o GitHub, sobrevive a
# deploy) porque o site guarda os favoritos só no localStorage do navegador e o
# aviso tem que sair mesmo com o site fechado.
# Token/chat do bot: variáveis TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no Railway
# (nunca no código). Cai pro telegram_config.json antigo se elas não existirem.
FAV_ALERTA_FILE = os.path.join(DATA_DIR, "telegram_favoritos.json")
_FAV_ALERTA_ANTECEDENCIA = 15 * 60
_fav_alerta_lock = threading.Lock()


def _tg_creds():
    cfg = _tg_load_config()
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("token") or "").strip()
    chat = str(os.environ.get("TELEGRAM_CHAT_ID") or cfg.get("chat_id") or "").strip()
    return token, chat


def _fav_alerta_load():
    try:
        with open(FAV_ALERTA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _fav_alerta_save(favs, push=True):
    with open(FAV_ALERTA_FILE, "w", encoding="utf-8") as f:
        json.dump(favs, f, ensure_ascii=False)
    if push:
        github_storage.push_file_bg(FAV_ALERTA_FILE, "telegram_favoritos.json")


def _fav_alerta_info(raw):
    """Saneia a "foto do card" que o site manda junto com o favorito (posição,
    forma, ícones, gols, confronto, odds) — vem do navegador, então nada é confiado."""
    if not isinstance(raw, dict):
        return {}

    def txt(v, n):
        return str(v or "")[:n]

    def num(v):
        try:
            return float(v) if v is not None and not isinstance(v, bool) else None
        except (TypeError, ValueError):
            return None

    def par(v, k):
        v = v if isinstance(v, (list, tuple)) else []
        return [num(v[i]) if i < len(v) else None for i in range(k)]

    h2h = raw.get("h2h")
    return {
        "pos_casa": int(num(raw.get("pos_casa"))) if num(raw.get("pos_casa")) is not None else None,
        "pos_fora": int(num(raw.get("pos_fora"))) if num(raw.get("pos_fora")) is not None else None,
        "forma_casa": "".join(c for c in txt(raw.get("forma_casa"), 5) if c in "VED"),
        "forma_fora": "".join(c for c in txt(raw.get("forma_fora"), 5) if c in "VED"),
        "atk_casa": txt(raw.get("atk_casa"), 2), "def_casa": txt(raw.get("def_casa"), 2),
        "atk_fora": txt(raw.get("atk_fora"), 2), "def_fora": txt(raw.get("def_fora"), 2),
        "gols_casa": par(raw.get("gols_casa"), 2), "gols_fora": par(raw.get("gols_fora"), 2),
        "h2h": [int(x) for x in par(h2h, 3) if x is not None] if isinstance(h2h, (list, tuple)) and len(h2h) == 3 else None,
        "odds": par(raw.get("odds"), 3),
    }


_FAV_FORMA_EMOJI = {"V": "🟢", "E": "🟡", "D": "🔴"}


def _fav_alerta_msg(f, falta):
    import html as _html
    from datetime import timezone
    esc = lambda v: _html.escape(str(v))
    brt = timezone(timedelta(hours=-3))
    hora = datetime.fromtimestamp(f["ts"], tz=brt).strftime("%H:%M")
    minutos = max(1, round(falta / 60))
    info = f.get("info") or {}
    linhas = [f"⏰ <b>Falta {minutos} min</b> — começa às {hora}"]
    if f.get("liga"):
        linhas.append(f"🏆 {esc(f['liga'])}")
    linhas.append("")

    def fmt(x):
        return f"{x:g}" if x is not None else "-"

    def time_bloco(icone, nome, pos, forma, atk, dfs, gols):
        cab = f"{icone} <b>{esc(nome or '?')}</b>" + (f"  #{pos}" if pos is not None else "")
        det = []
        if forma:
            det.append("Forma " + "".join(_FAV_FORMA_EMOJI.get(c, "") for c in forma))
        if atk or dfs:
            det.append(f"Ataque {atk or '-'} · Defesa {dfs or '-'}")
        if gols and any(g is not None for g in gols):
            det.append(f"Gols {fmt(gols[0])}/{fmt(gols[1])} (marc./sofr.)")
        return [cab] + [f"    {d}" for d in det]

    linhas += time_bloco("🏠", f.get("home"), info.get("pos_casa"), info.get("forma_casa"),
                         info.get("atk_casa"), info.get("def_casa"), info.get("gols_casa"))
    linhas += time_bloco("✈️", f.get("away"), info.get("pos_fora"), info.get("forma_fora"),
                         info.get("atk_fora"), info.get("def_fora"), info.get("gols_fora"))
    h2h = info.get("h2h")
    if h2h:
        linhas.append("")
        linhas.append(f"🆚 Confronto direto: {h2h[0]}V {h2h[1]}E {h2h[2]}V")
    odds = info.get("odds") or []
    if any(o is not None for o in odds):
        linhas.append("💰 Odds  1: <b>{}</b>  ·  X: <b>{}</b>  ·  2: <b>{}</b>".format(*[fmt(o) for o in (odds + [None] * 3)[:3]]))
    metodo = (f.get("metodologia") or "").strip()
    if metodo:
        linhas.append("")
        linhas.append(f"📋 <b>Metodologia:</b> {esc(metodo)}")
    return "\n".join(linhas)


def _fav_alerta_tick():
    now = time.time()
    devidos = []   # [(event_id, mensagem)]
    with _fav_alerta_lock:
        favs = _fav_alerta_load()
        if not favs:
            return
        mudou = False
        for eid, f in list(favs.items()):
            falta = (f.get("ts") or 0) - now
            if f.get("alertado"):
                if falta < -3 * 3600:      # já avisado e o jogo já passou: limpa
                    del favs[eid]; mudou = True
                continue
            if falta <= 0:                  # começou sem aviso (servidor fora do ar, etc)
                del favs[eid]; mudou = True
            elif falta <= _FAV_ALERTA_ANTECEDENCIA:
                devidos.append((eid, _fav_alerta_msg(f, falta)))
        if mudou:
            _fav_alerta_save(favs)
    if not devidos:
        return
    token, chat = _tg_creds()
    if not token or not chat:
        return                              # bot ainda não configurado: tenta de novo no próximo ciclo
    enviados = []
    for eid, msg in devidos:
        try:
            if _tg_send_message(token, chat, msg).get("ok"):
                enviados.append(eid)
            else:
                print(f"[fav-alerta] Telegram recusou o aviso do jogo {eid}")
        except Exception as e:
            print(f"[fav-alerta] Erro enviando aviso do jogo {eid}: {e}")
    if enviados:
        with _fav_alerta_lock:
            favs = _fav_alerta_load()
            for eid in enviados:
                if eid in favs:
                    favs[eid]["alertado"] = True
            _fav_alerta_save(favs)
        print(f"[fav-alerta] {len(enviados)} aviso(s) de 15min enviado(s)")


def _fav_alerta_loop():
    _github_sync_done.wait(timeout=120)   # espera restaurar o arquivo do GitHub antes do 1º ciclo
    while True:
        try:
            _fav_alerta_tick()
        except Exception as e:
            print(f"[fav-alerta] {e}")
        time.sleep(30)


threading.Thread(target=_fav_alerta_loop, daemon=True, name="FavAlerta15min").start()


@app.route("/api/favoritos/proximos", methods=["POST"])
def api_favoritos_proximos():
    """O site avisa quando o usuário favorita/desfavorita um jogo em Próximos
    Jogos. Idempotente: adicionar 2x o mesmo jogo não duplica nem reenvia aviso."""
    d = request.get_json(silent=True) or {}
    eid = str(d.get("event_id") or "").strip()
    if not eid or len(eid) > 64:
        return jsonify({"ok": False, "error": "event_id inválido"}), 400
    with _fav_alerta_lock:
        favs = _fav_alerta_load()
        if d.get("action") == "remove":
            if favs.pop(eid, None) is not None:
                _fav_alerta_save(favs)
            return jsonify({"ok": True})
        try:
            ts = int(d.get("ts") or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "ts inválido"}), 400
        if ts <= time.time():
            return jsonify({"ok": True, "ignorado": "jogo já começou"})
        atual = favs.get(eid, {})
        novo = {
            "home": str(d.get("home") or "")[:120], "away": str(d.get("away") or "")[:120],
            "liga": str(d.get("liga") or "")[:160], "ts": ts,
            "metodologia": str(d.get("metodologia") or "").strip()[:120],
            "info": _fav_alerta_info(d.get("info")) or atual.get("info") or {},
            "alertado": bool(atual.get("alertado")) and atual.get("ts") == ts,
        }
        favs[eid] = novo
        # O site reenvia a "foto do card" a cada atualização (odds mudam): isso só
        # grava no disco. Só sobe pro GitHub quando o favorito é novo ou a
        # metodologia/horário mudou — senão seria 1 commit por jogo por minuto.
        estrutural = (not atual or atual.get("metodologia") != novo["metodologia"]
                      or atual.get("ts") != ts)
        _fav_alerta_save(favs, push=estrutural)
    return jsonify({"ok": True})


@app.route("/api/telegram/teste")
def api_telegram_teste():
    """Manda uma mensagem de teste pro grupo — confere se token/chat_id estão certos."""
    token, chat = _tg_creds()
    faltam = [n for n, v in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat)) if not v]
    if faltam:
        return jsonify({"ok": False, "error": "faltam variáveis: " + ", ".join(faltam)}), 400
    try:
        res = _tg_send_message(token, chat, "✅ <b>Gol em Números</b>: alertas de 15 min conectados.")
    except Exception as e:
        return jsonify({"ok": False, "error": f"falha de rede: {type(e).__name__}"}), 502
    if res.get("ok"):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": res.get("description") or "Telegram recusou"}), 400


# ── Diário de operações (2026-09-15) ─────────────────────────────────────────
# O usuário disse que o maior inimigo dele não é técnico, é o emocional: perde
# a consistência, entra em tilt depois de um red e não enxerga o próprio
# padrão. O diário existe pra devolver isso em número — principalmente cruzar
# o resultado de cada operação com a COR DO SEMÁFORO no momento da entrada
# (ver lay_risk_table.json): é o que prova, com o dinheiro dele, se seguir o
# risco medido paga ou não.
#
# Um arquivo só, sincronizado com o GitHub igual ao resto (sobrevive a
# redeploy do Railway). Volume é irrisório: ~300 bytes por operação.
DIARIO_FILE = os.path.join(DATA_DIR, "diario_operacoes.json")


def _diario_load():
    if not os.path.exists(DIARIO_FILE):
        return []
    try:
        with open(DIARIO_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _diario_save(ops):
    with open(DIARIO_FILE, "w", encoding="utf-8") as f:
        json.dump(ops, f, ensure_ascii=False, indent=2)
    github_storage.push_file_bg(DIARIO_FILE, "diario_operacoes.json")


def _diario_stats(ops):
    """Estatísticas que o usuário não consegue enxergar sozinho no calor do
    jogo. O bloco `por_cor` é o mais importante: mostra se entrar no verde
    (risco baixo medido) realmente dá mais resultado que entrar no vermelho."""
    fechadas = [o for o in ops if o.get("status") == "fechada" and o.get("saida")]
    fechadas.sort(key=lambda o: o.get("criado_em") or "")

    def bloco(lista):
        n = len(lista)
        if not n:
            return {"n": 0, "greens": 0, "acerto_pct": None, "lucro": 0.0}
        greens = sum(1 for o in lista if (o["saida"].get("resultado") == "green"))
        lucro = sum(float(o["saida"].get("lucro") or 0) for o in lista)
        return {
            "n": n,
            "greens": greens,
            "acerto_pct": round(greens / n * 100, 1),
            "lucro": round(lucro, 2),
        }

    por_cor = {}
    for cor in ("verde", "amarelo", "vermelho", "sem_dado"):
        por_cor[cor] = bloco([o for o in fechadas
                              if (o.get("entrada") or {}).get("risco_cor", "sem_dado") == cor])

    # Tilt: como foi a operação IMEDIATAMENTE depois de um red.
    depois_de_red = []
    for i, o in enumerate(fechadas[1:], start=1):
        if fechadas[i - 1]["saida"].get("resultado") == "red":
            depois_de_red.append(o)

    por_minuto = {}
    for rotulo, lo, hi in (("0-30min", 0, 30), ("30-60min", 30, 60), ("60min+", 60, 200)):
        por_minuto[rotulo] = bloco([o for o in fechadas
                                    if lo <= ((o.get("entrada") or {}).get("minuto") or 0) < hi])

    # Sequência de reds do dia (base pra regra de parada)
    hoje = datetime.now().strftime("%Y-%m-%d")
    do_dia = [o for o in fechadas if (o.get("criado_em") or "").startswith(hoje)]
    reds_seguidos = 0
    for o in reversed(do_dia):
        if o["saida"].get("resultado") == "red":
            reds_seguidos += 1
        else:
            break

    return {
        "geral": bloco(fechadas),
        "por_cor": por_cor,
        "depois_de_red": bloco(depois_de_red),
        "por_minuto": por_minuto,
        "hoje": bloco(do_dia),
        "reds_seguidos_hoje": reds_seguidos,
        "abertas": sum(1 for o in ops if o.get("status") == "aberta"),
    }


@app.route("/api/diario", methods=["GET", "POST"])
def api_diario():
    ops = _diario_load()
    if request.method == "POST":
        d = request.json or {}
        op = {
            # timestamp + sufixo aleatório: só o timestamp em ms colide quando
            # duas operações são criadas no mesmo milissegundo (acontece em
            # teste automatizado, e colisão aqui faria uma operação fechar a
            # outra por engano).
            "id": f"{int(time.time() * 1000)}-{os.urandom(3).hex()}",
            "criado_em": datetime.now().isoformat(),
            "status": "aberta",
            "match_id": d.get("match_id"),
            "casa": d.get("casa"), "fora": d.get("fora"), "liga": d.get("liga"),
            "lay_time": d.get("lay_time"),
            "entrada": d.get("entrada") or {},
            "nota": d.get("nota") or "",
        }
        ops.append(op)
        try:
            _diario_save(ops)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, "operacao": op})
    return jsonify({"operacoes": ops, "stats": _diario_stats(ops)})


@app.route("/api/diario/<op_id>/fechar", methods=["POST"])
def api_diario_fechar(op_id):
    d = request.json or {}
    ops = _diario_load()
    for op in ops:
        if op.get("id") == op_id:
            op["status"] = "fechada"
            op["saida"] = {
                "minuto": d.get("minuto"),
                "odd": d.get("odd"),
                "resultado": d.get("resultado"),   # "green" | "red"
                "lucro": d.get("lucro"),
                "fechado_em": datetime.now().isoformat(),
            }
            if d.get("nota"):
                op["nota"] = d["nota"]
            try:
                _diario_save(ops)
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 500
            return jsonify({"ok": True, "operacao": op})
    return jsonify({"ok": False, "error": "operação não encontrada"}), 404


@app.route("/api/diario/<op_id>", methods=["DELETE"])
def api_diario_apagar(op_id):
    ops = _diario_load()
    novas = [o for o in ops if o.get("id") != op_id]
    if len(novas) == len(ops):
        return jsonify({"ok": False, "error": "operação não encontrada"}), 404
    try:
        _diario_save(novas)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})


# Só aqui embaixo (não perto dos outros threading.Thread(...).start() lá em
# cima) porque _painel_odds_prewarm_loop chama _today2_odds_snapshot, definida
# bem mais abaixo no arquivo — startar essa thread mais cedo deu NameError na
# hora que ela acordou (o .wait() do _github_sync_done retorna quase na hora
# quando GITHUB_TOKEN não está configurado, antes do resto do módulo terminar
# de carregar).
threading.Thread(target=_painel_odds_prewarm_loop, daemon=True, name="PainelOddsPrewarm").start()
threading.Thread(target=_painel_ht_prewarm_loop, daemon=True, name="PainelHtPrewarm").start()
threading.Thread(target=_live_odds_prewarm_loop, daemon=True, name="LiveOddsPrewarm").start()
threading.Thread(target=_painel_power_cache_load_once, daemon=True, name="PainelPowerCacheLoad").start()
threading.Thread(target=_painel_shift_prewarm_loop, daemon=True, name="PainelShiftPrewarm").start()
# Pré-carga de força (força-prefetch) DESATIVADA de novo — mesmo com só 1
# worker + pausa entre partidas, o Playwright rodando quase sem parar em
# segundo plano parece estar competindo por CPU com o resto do site num
# container com recursos limitados (site voltou a ficar lento, 15-40s pra
# responder, depois desse deploy). A coluna "Força" e o filtro continuam
# funcionando normalmente, só que cache-only (sem pré-carga automática).
# threading.Thread(target=_ng_strength_prefetch_filler_loop, daemon=True, name="ForcaPrefetchFiller").start()
# threading.Thread(target=_ng_strength_prefetch_worker, daemon=True, name="ForcaPrefetchWorker").start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Servidor rodando em http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port, use_reloader=False, threaded=True)

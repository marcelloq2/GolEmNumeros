"""
GitHub Storage — persiste backtest/ e momentum_history/ na branch 'data' do GitHub.
Garante que os dados não se percam quando o Railway reinicia ou faz redeploy.
"""
import os, base64, threading
import requests as http_req
from datetime import datetime

GITHUB_API  = "https://api.github.com"
DATA_BRANCH = "data"
_push_lock  = threading.Lock()


def is_configured() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN"))


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo() -> str:
    return os.environ.get("GITHUB_REPO", "marcelloq2/GolEmNumeros")


def _ensure_data_branch() -> bool:
    """Cria branch 'data' se não existir (baseada em flask-app ou main)."""
    repo = _repo()
    r = http_req.get(f"{GITHUB_API}/repos/{repo}/branches/{DATA_BRANCH}",
                     headers=_headers(), timeout=10)
    if r.status_code == 200:
        return True

    sha = None
    for base_branch in ("flask-app", "main"):
        r = http_req.get(f"{GITHUB_API}/repos/{repo}/git/refs/heads/{base_branch}",
                         headers=_headers(), timeout=10)
        if r.status_code == 200:
            sha = r.json()["object"]["sha"]
            break

    if not sha:
        print("[github] Não encontrou branch base para criar 'data'.")
        return False

    r = http_req.post(f"{GITHUB_API}/repos/{repo}/git/refs",
                      headers=_headers(),
                      json={"ref": f"refs/heads/{DATA_BRANCH}", "sha": sha},
                      timeout=10)
    if r.status_code in (201, 422):
        print(f"[github] Branch '{DATA_BRANCH}' {'criada' if r.status_code == 201 else 'já existia'}.")
        return True
    print(f"[github] Erro ao criar branch: {r.text[:200]}")
    return False


def push_file(local_path: str, remote_path: str = None) -> bool:
    """Push um arquivo local para GitHub (branch data). Thread-safe."""
    if not is_configured():
        return False
    if not os.path.exists(local_path):
        return False

    repo = _repo()
    if remote_path is None:
        base = os.path.dirname(os.path.abspath(__file__))
        remote_path = os.path.relpath(local_path, base).replace("\\", "/")

    try:
        with open(local_path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode()

        # O lock cobre da checagem do SHA até o PUT: se outra thread push desse
        # mesmo arquivo (ex: backtest2.db, pushado 3x seguidas por chamadores
        # diferentes) rodar entre o GET do SHA e o PUT, o GitHub rejeita o
        # segundo PUT (SHA divergente) e o push falha silenciosamente — foi o
        # que fazia o histórico acumulado do Backtest nunca sobreviver direito
        # a um redeploy do Railway. Serializando os dois passos junto, cada
        # push sempre PUT com o SHA mais recente.
        with _push_lock:
            for attempt in range(2):
                sha = None
                r = http_req.get(
                    f"{GITHUB_API}/repos/{repo}/contents/{remote_path}",
                    headers=_headers(), params={"ref": DATA_BRANCH}, timeout=10
                )
                if r.status_code == 200:
                    sha = r.json().get("sha")

                body = {
                    "message": f"data: {os.path.basename(remote_path)} [{datetime.now().strftime('%Y-%m-%d %H:%M')}]",
                    "content": content_b64,
                    "branch":  DATA_BRANCH,
                }
                if sha:
                    body["sha"] = sha

                # Timeout maior que o antigo (20s) — arquivos como backtest2.db
                # crescem pra dezenas de MB, e um PUT desse tamanho pode legitimamente
                # levar mais que 20s pra completar.
                r = http_req.put(
                    f"{GITHUB_API}/repos/{repo}/contents/{remote_path}",
                    headers=_headers(), json=body, timeout=60
                )

                if r.status_code in (200, 201):
                    print(f"[github] ✓ Push: {remote_path}")
                    return True
                if r.status_code in (409, 422) and attempt == 0:
                    # SHA ficou desatualizado entre o GET e o PUT (outra escrita
                    # concorrente na branch) — tenta de novo uma vez com SHA fresco.
                    continue
                print(f"[github] Erro push {remote_path}: {r.status_code} {r.text[:200]}")
                return False

    except Exception as e:
        print(f"[github] Exceção push {remote_path}: {e}")
        return False


def push_file_bg(local_path: str, remote_path: str = None):
    """Push em background — não bloqueia o servidor."""
    threading.Thread(
        target=push_file, args=(local_path, remote_path), daemon=True
    ).start()


def pull_directory(remote_dir: str, local_dir: str) -> int:
    """Baixa arquivos JSON de remote_dir (branch data) para local_dir.
    Não sobrescreve arquivos que já existem localmente."""
    if not is_configured():
        return 0

    repo = _repo()
    os.makedirs(local_dir, exist_ok=True)

    r = http_req.get(
        f"{GITHUB_API}/repos/{repo}/contents/{remote_dir}",
        headers=_headers(), params={"ref": DATA_BRANCH}, timeout=15
    )
    if r.status_code != 200:
        print(f"[github] '{remote_dir}' não encontrado na branch '{DATA_BRANCH}'.")
        return 0

    items = r.json()
    if not isinstance(items, list):
        return 0

    count = 0
    for item in items:
        if item.get("type") != "file" or not item["name"].endswith(".json"):
            continue
        local_path = os.path.join(local_dir, item["name"])
        if os.path.exists(local_path):
            continue  # já existe localmente, não sobrescreve
        try:
            fr = http_req.get(item["download_url"], timeout=15)
            if fr.status_code == 200:
                with open(local_path, "wb") as fp:
                    fp.write(fr.content)
                count += 1
        except Exception as e:
            print(f"[github] Erro download {item['name']}: {e}")

    print(f"[github] Pull '{remote_dir}': {count} arquivo(s) restaurado(s).")
    return count


def pull_file(remote_path: str, local_path: str, force: bool = False) -> bool:
    """Baixa um único arquivo da branch data para local_path.

    force=True → sobrescreve mesmo se o arquivo já existir localmente.

    Antes essa função falhava em SILÊNCIO em todo caminho de erro (sem
    print nenhum) — isso escondeu um bug real: se essa chamada falhasse
    (rede, GitHub fora do ar, o que for) durante o boot, o servidor seguia
    em frente com um backtest2.db vazio/desatualizado sem avisar ninguém, e
    qualquer push posterior sobrescrevia o backup bom no GitHub com esse
    estado pequeno — destruindo dado acumulado de verdade. Logar cada
    caminho de falha não previne o problema sozinho, mas garante que da
    próxima vez apareça nos logs em vez de sumir sem rastro."""
    if not is_configured():
        return False
    if not force and os.path.exists(local_path):
        return False

    repo = _repo()
    r = http_req.get(
        f"{GITHUB_API}/repos/{repo}/contents/{remote_path}",
        headers=_headers(), params={"ref": DATA_BRANCH}, timeout=10
    )
    if r.status_code != 200:
        print(f"[github] Falha ao baixar {remote_path}: GET contents retornou {r.status_code}")
        return False

    download_url = r.json().get("download_url")
    if not download_url:
        print(f"[github] Falha ao baixar {remote_path}: resposta sem download_url")
        return False

    fr = http_req.get(download_url, timeout=15)
    if fr.status_code == 200:
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        with open(local_path, "wb") as fp:
            fp.write(fr.content)
        action = "Pull (force)" if force else "Pull"
        print(f"[github] {action}: {remote_path} ({len(fr.content)} bytes)")
        return True
    print(f"[github] Falha ao baixar {remote_path}: download retornou {fr.status_code}")
    return False


# True só depois que o pull de backtest2.db no boot confirma sucesso. Enquanto
# for False, push_file_bg RECUSA subir backtest2.db (ver guard lá embaixo) —
# sem essa trava, um pull que falhasse silenciosamente deixava o container
# rodando com um banco vazio/velho, e o primeiro push depois sobrescrevia o
# backup bom no GitHub com esse estado pequeno. Foi exatamente isso que
# apagou dado real de verdade (achado investigando com o usuário em
# 2026-08-25) — o tamanho do arquivo na branch 'data' caiu de ~2.4MB pra
# ~900KB de um commit pro outro, sem nenhum push nosso no meio.
_backtest2_db_synced_ok = False


def backtest2_db_sync_ok() -> bool:
    return _backtest2_db_synced_ok


def sync_on_startup(momentum_dir: str, backtest_dir: str, data_dir: str, shotmap_dir: str = None):
    """Restaura todos os dados do GitHub ao iniciar o servidor."""
    global _backtest2_db_synced_ok
    if not is_configured():
        print("[github] GITHUB_TOKEN não configurado — persistência desabilitada.")
        return

    print("[github] Iniciando restauração de dados...")
    try:
        _ensure_data_branch()

        # ── Arquivos pequenos primeiro, de propósito ────────────────────────
        # Achado em produção (2026-08-26): com muitos deploys seguidos, os
        # pull_directory abaixo (momentum_history sozinho tem MILHARES de
        # arquivos, um GET por arquivo, sequencial) podiam levar minutos —
        # tempo de sobra pra outro deploy interromper o boot ANTES da
        # restauração chegar nos arquivos de configuração pequenos, que
        # ficavam nulos/vazios até o próximo boot ter sorte de terminar tudo.
        # Configs pequenas (poucos KB) restauram em milissegundos — não tem
        # motivo pra esperar a fila de milhares de arquivos grandes primeiro.
        for fname in ("predictions_full.json", "predictions.json"):
            pull_file(fname, os.path.join(data_dir, fname), force=True)
        # backtest2.db (SQLite do Backtest 2/CS acumulado): sempre baixa a
        # versão mais recente — sem isso, cada redeploy no Railway apagava o
        # disco local e o histórico acumulado voltava a zero.
        _backtest2_db_synced_ok = pull_file("backtest2.db", os.path.join(data_dir, "backtest2.db"), force=True)
        if not _backtest2_db_synced_ok:
            print("[github] ⚠ Não deu pra confirmar a restauração de backtest2.db — "
                  "pushes desse arquivo ficam BLOQUEADOS até o próximo boot bem-sucedido, "
                  "pra não arriscar sobrescrever o backup bom no GitHub com um estado ruim.")
        # Configuração do Lay Placar (importada pelo usuário) — sempre baixa a mais
        # recente, senão um redeploy no Railway apagava a configuração do servidor
        # e cada aparelho ia depender só do que tinha salvo localmente de novo.
        pull_file("lay_placar_config.json", os.path.join(data_dir, "lay_placar_config.json"), force=True)
        # Watchlist de tipsters (Telegram) — mesmo problema: sem isso, cada
        # redeploy apagava quem estava sendo acompanhado (o sino voltava a
        # aparecer desmarcado).
        pull_file("tipster_watch.db", os.path.join(data_dir, "tipster_watch.db"), force=True)
        # Configuração do Sinalizador (regras importadas do .txt) — mesmo
        # problema de novo: sem isso, cada redeploy apagava as regras
        # importadas.
        pull_file("sinalizador_config.json", os.path.join(data_dir, "sinalizador_config.json"), force=True)
        # Shotmap live cache: restaura cache ao vivo (evita perda de chutes em jogos mid-restart)
        pull_file(".shotmap_cache.json",
                  os.path.join(data_dir, ".shotmap_cache.json"), force=True)

        # ── Diretórios grandes por último (podem levar minutos) ─────────────
        pull_directory("momentum_history", momentum_dir)
        pull_directory("backtest", backtest_dir)
        if shotmap_dir:
            pull_directory("shotmap_history", shotmap_dir)
        print("[github] Restauração concluída.")
    except Exception as e:
        print(f"[github] Erro na restauração: {e}")

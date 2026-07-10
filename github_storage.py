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

        # Verifica SHA existente (necessário para update)
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

        with _push_lock:
            r = http_req.put(
                f"{GITHUB_API}/repos/{repo}/contents/{remote_path}",
                headers=_headers(), json=body, timeout=20
            )

        if r.status_code in (200, 201):
            print(f"[github] ✓ Push: {remote_path}")
            return True
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
    """
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
        return False

    download_url = r.json().get("download_url")
    if not download_url:
        return False

    fr = http_req.get(download_url, timeout=15)
    if fr.status_code == 200:
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        with open(local_path, "wb") as fp:
            fp.write(fr.content)
        action = "Pull (force)" if force else "Pull"
        print(f"[github] {action}: {remote_path}")
        return True
    return False


def sync_on_startup(momentum_dir: str, backtest_dir: str, data_dir: str, shotmap_dir: str = None):
    """Restaura todos os dados do GitHub ao iniciar o servidor."""
    if not is_configured():
        print("[github] GITHUB_TOKEN não configurado — persistência desabilitada.")
        return

    print("[github] Iniciando restauração de dados...")
    try:
        _ensure_data_branch()
        pull_directory("momentum_history", momentum_dir)
        pull_directory("backtest", backtest_dir)
        # Shotmap history: restaura partidas salvas separadamente
        if shotmap_dir:
            pull_directory("shotmap_history", shotmap_dir)
        # Shotmap live cache: restaura cache ao vivo (evita perda de chutes em jogos mid-restart)
        pull_file(".shotmap_cache.json",
                  os.path.join(data_dir, ".shotmap_cache.json"), force=True)
        # Predictions: SEMPRE baixa a versão mais recente do GitHub
        # (force=True garante que o Railway nunca fique com dados velhos após redeploy)
        for fname in ("predictions_full.json", "predictions.json"):
            pull_file(fname, os.path.join(data_dir, fname), force=True)
        # backtest2.db (SQLite do Backtest 2/CS acumulado): igual predictions, sempre
        # baixa a versão mais recente — sem isso, cada redeploy no Railway apagava o
        # disco local e o histórico acumulado voltava a zero.
        pull_file("backtest2.db", os.path.join(data_dir, "backtest2.db"), force=True)
        print("[github] Restauração concluída.")
    except Exception as e:
        print(f"[github] Erro na restauração: {e}")

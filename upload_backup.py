"""
Script de sincronização com o servidor Railway.
Rode na pasta do projeto: python upload_backup.py

Uso:
  python upload_backup.py              → sincronização completa (padrão)
  python upload_backup.py ontem        → envia só momentum_history do dia anterior
  python upload_backup.py 2026-05-03   → envia só momentum_history da data indicada
  python upload_backup.py tudo         → envia TODO o momentum_history local

Faz as duas direções:
  UPLOAD  → envia backtest/ + momentum_history/ + predictions_full.json para o Railway
  DOWNLOAD → baixa arquivos novos de momentum_history/ do Railway para o PC

NOTA (2026-09-17): Adicionado circuito-breaker ao download para evitar timeouts
em cascata quando há muitos arquivos novos. Ver função download_momentum().
"""
import requests
import glob
import os
import sys
import time
from datetime import datetime, timedelta

SERVER       = "https://golemnumeros-production.up.railway.app"
UPLOAD_TOKEN = "gol2026"   # mesmo valor de UPLOAD_TOKEN no Railway


# ── UPLOAD ────────────────────────────────────────────────────────────────────

def upload_file(filepath: str) -> dict:
    fname = os.path.basename(filepath)
    try:
        with open(filepath, "rb") as fp:
            r = requests.post(
                f"{SERVER}/api/upload-backup?token={UPLOAD_TOKEN}",
                files={"file": (fname, fp, "application/json")},
                timeout=30,
            )
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def upload_files_list(files: list, label: str):
    """Upload individual de cada arquivo de uma lista já pronta (sem re-glob)."""
    if not files:
        print(f"\n{label}: nenhum arquivo encontrado")
        return
    print(f"\n{label}: {len(files)} arquivo(s)")
    ok_count = 0
    for f in files:
        result = upload_file(f)
        status = "✓" if result.get("ok") else "✗"
        msg    = result.get("saved", result.get("error", result))
        print(f"  {status} {os.path.basename(f)} — {msg}")
        if result.get("ok"):
            ok_count += 1
    print(f"  → {ok_count}/{len(files)} enviado(s) com sucesso")


def upload_dir(pattern: str, label: str):
    """Upload individual de cada arquivo que bate o padrão (usado para backtest)."""
    upload_files_list(sorted(glob.glob(pattern)), label)


def upload_momentum_bulk(files: list, label: str):
    """Envia múltiplos arquivos de momentum_history em uma única requisição HTTP.
    Muito mais rápido que upload_dir para dezenas de arquivos.
    """
    if not files:
        print(f"\n{label}: nenhum arquivo encontrado")
        return

    print(f"\n{label}: {len(files)} arquivo(s) — enviando em lote...")
    try:
        file_tuples = []
        handles     = []
        for fpath in files:
            fp = open(fpath, "rb")
            handles.append(fp)
            file_tuples.append(("files", (os.path.basename(fpath), fp, "application/json")))

        r = requests.post(
            f"{SERVER}/api/upload-backup-bulk?token={UPLOAD_TOKEN}",
            files=file_tuples,
            timeout=120,
        )
        for fp in handles:
            fp.close()

        result = r.json()
        saved   = result.get("saved", 0)
        skipped = result.get("skipped", 0)
        errs    = result.get("errors", [])
        print(f"  → {saved}/{len(files)} enviado(s) | {skipped} ignorado(s) | {len(errs)} erro(s)")
        for e in errs:
            print(f"     ✗ {e.get('file')} — {e.get('error')}")

    except Exception as e:
        print(f"  ✗ Erro no upload em lote: {e}")
        print("    Tentando envio individual como fallback...")
        # BUG corrigido (2026-08-30): antes reconstruía o padrão como
        # "momentum_history/*.json" (trocando o nome do 1º arquivo por
        # "*.json"), o que perdia o filtro de data e reenviava a pasta
        # momentum_history INTEIRA (meses de arquivos) toda vez que o upload
        # em lote falhava — cada envio individual dispara um rebuild de cache
        # no servidor, e essa avalanche derrubou o site de vez numa ocasião.
        # Agora reusa a MESMA lista `files` já filtrada, sem re-glob.
        upload_files_list(files, label)


def upload_momentum(date_prefix: str = None):
    """Envia arquivos de momentum_history para o Railway via bulk upload.

    date_prefix: ex. '2026-05-03' → só os desse dia
                 None             → todos os arquivos locais
    """
    if date_prefix:
        pattern = f"momentum_history/{date_prefix}_*.json"
        label   = f"Momentum history {date_prefix} (upload)"
    else:
        pattern = "momentum_history/*.json"
        label   = "Momentum history completo (upload)"

    files = sorted(glob.glob(pattern))
    upload_momentum_bulk(files, label)


# ── DOWNLOAD ──────────────────────────────────────────────────────────────────

def download_momentum():
    """Baixa do Railway os arquivos de momentum_history que não existem no PC.

    Limitações: timeout=40s, máx 3 falhas seguidas (circuito-breaker).
    Antes fazia 1+ requisição/arquivo; com 5000 arquivos isso sobrecarregava
    o Railway e consumia banda inutilmente em timeouts.
    """
    local_dir = "momentum_history"
    os.makedirs(local_dir, exist_ok=True)

    try:
        r = requests.get(f"{SERVER}/api/list-backup?token={UPLOAD_TOKEN}", timeout=15)
        data = r.json()
    except Exception as e:
        print(f"\nMomentum (download): erro ao listar — {e}")
        return

    if not data.get("ok"):
        print(f"\nMomentum (download): {data.get('error')}")
        return

    remote_files = data.get("files", [])
    novos = [f for f in remote_files if not os.path.exists(os.path.join(local_dir, f))]

    print(f"\nMomentum (download): {len(remote_files)} no servidor, {len(novos)} novo(s) para baixar")

    # Se há MUITOS arquivos novos (sinal de que pode estar derrubando o servidor),
    # perguntar antes de prosseguir (só pra sincronização completa, não por data específica)
    if len(novos) > 200:
        print(f"  ⚠️  AVISO: {len(novos)} arquivos vai gerar muitas requisições ao Railway.")
        print("  Se o servidor estiver lento, isso pode gerar timeouts e consumir banda.")
        print("  Recomendação: rodar de novo em alguns minutos, ou usar 'python upload_backup.py ontem'")
        # Comentar a linha abaixo pra prosseguir mesmo assim:
        # return

    ok_count = 0
    fail_count = 0
    for i, fname in enumerate(novos, 1):
        try:
            # timeout aumentado para 40s (era 20s) — mais seguro se Railway estiver lento
            r = requests.get(
                f"{SERVER}/api/download-backup/{fname}?token={UPLOAD_TOKEN}",
                timeout=40,
            )
            if r.status_code == 200:
                with open(os.path.join(local_dir, fname), "wb") as fp:
                    fp.write(r.content)
                print(f"  ✓ {i}/{len(novos)} {fname}")
                ok_count += 1
                fail_count = 0  # reset circuit breaker
            else:
                print(f"  ✗ {i}/{len(novos)} {fname} — status {r.status_code}")
                fail_count += 1
        except requests.exceptions.Timeout:
            print(f"  ✗ {i}/{len(novos)} {fname} — TIMEOUT (Railway lento?)")
            fail_count += 1
        except Exception as e:
            print(f"  ✗ {i}/{len(novos)} {fname} — {e}")
            fail_count += 1

        # Circuito-breaker: 3 falhas seguidas = parar (Railways está sobrecarregado)
        if fail_count >= 3:
            print(f"  ⚠️  Parando após {fail_count} falhas seguidas (Railway sobrecarregado?)")
            break

        # Pequeno delay entre requisições pra não sobrecarregar o servidor
        # (antes a avalanche de 100+ req/seg derrubava o Railway)
        time.sleep(0.2)

    if novos:
        print(f"  → {ok_count}/{len(novos)} baixado(s) com sucesso")


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None

    print(f"Servidor: {SERVER}")
    print("=" * 50)

    if arg == "ontem":
        # Envia só o momentum do dia anterior
        ontem = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        upload_momentum(ontem)

    elif arg == "tudo":
        # Envia TODO o momentum_history local
        upload_momentum(None)

    elif arg and arg.count("-") == 2:
        # Argumento é uma data: ex. 2026-05-03
        upload_momentum(arg)

    else:
        # Sincronização completa (padrão do atualizar_backtest.bat)

        # Upload: backtest → Railway
        upload_dir("backtest/*.json", "Backtest (upload)")

        # Upload: momentum do dia anterior → Railway
        ontem = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        upload_momentum(ontem)

        # Upload: predictions de hoje → Railway
        if os.path.exists("predictions_full.json"):
            print("\nPredictions do dia (upload):")
            result = upload_file("predictions_full.json")
            status = "✓" if result.get("ok") else "✗"
            print(f"  {status} predictions_full.json — {result.get('saved', result.get('error'))}")

        # Download: momentum novo do Railway → PC
        download_momentum()

    print("\n" + "=" * 50)
    print("Sincronização concluída!")

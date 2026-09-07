#!/usr/bin/env python3
"""hub — o unico daemon. Roda todos os canais.

Loop (a cada 30s):
  1. recarrega canais/*.yaml (edicao entra sem restart)
  2. agenda jobs do dia a partir dos horarios de cada canal (idempotente por chave)
  3. agenda scan da pasta de entrada (a cada 5 min por canal)
  4. solta jobs travados (processo morreu no meio)
  5. executa jobs vencidos, um por vez, em ordem de run_at
"""
import fcntl
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import VERSION, canais as canais_mod, db, runner  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
INTERVALO = int(os.environ.get('HUB_INTERVALO', '30'))
DRY_RUN = os.environ.get('HUB_DRY_RUN', '') == '1'


def log(msg):
    print(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}', flush=True)


def agendar(con, canais):
    hoje = datetime.now().strftime('%Y-%m-%d')
    agora = db.now()
    for nome, c in canais.items():
        db.upsert_canal(con, c)
        if not c['ativo']:
            continue
        for hm in c['horarios']:
            run_at = f'{hoje} {hm}:00'
            try:
                atraso = (datetime.now() - datetime.strptime(run_at, '%Y-%m-%d %H:%M:%S')).total_seconds()
            except ValueError:
                log(f'[{nome}] horario invalido no YAML: {hm!r}')
                continue
            if atraso > 3600:
                continue  # horario ja passou ha mais de 1h (ex.: hub subiu tarde): nao publica retroativo
            db.add_job(con, nome, 'publicar_proximo', run_at, chave=f'pub:{nome}:{hoje} {hm}')
        slot = datetime.now().strftime('%Y-%m-%d %H:') + f'{(datetime.now().minute // 5) * 5:02d}'
        db.add_job(con, nome, 'scan_entrada', agora, chave=f'scan:{nome}:{slot}')


def tick(con):
    canais = canais_mod.load_all()
    agendar(con, canais)
    soltos = db.release_stuck_jobs(con)
    if soltos:
        log(f'{soltos} job(s) travado(s) voltaram pra fila')
    for job in db.due_jobs(con):
        if not db.claim_job(con, job['id']):
            continue
        if job['tipo'] != 'scan_entrada':
            log(f'job #{job["id"]} {job["tipo"]} [{job["canal"]}]')
        runner.run_job(con, job, canais, dry_run=DRY_RUN, log=log)


def lock():
    fd = os.open(os.path.join(ROOT, 'data', '.hub.lock'), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('outro hub ja esta rodando', file=sys.stderr)
        sys.exit(1)
    os.write(fd, str(os.getpid()).encode())
    return fd


def main():
    os.makedirs(os.path.join(ROOT, 'data'), exist_ok=True)
    _fd = lock()
    con = db.connect()
    log(f'hub v{VERSION} iniciado (intervalo {INTERVALO}s{" DRY_RUN" if DRY_RUN else ""})')
    if '--once' in sys.argv:
        tick(con)
        return
    while True:
        try:
            tick(con)
        except Exception as e:
            log(f'ERRO no tick: {type(e).__name__}: {e}')
        time.sleep(INTERVALO)


if __name__ == '__main__':
    main()

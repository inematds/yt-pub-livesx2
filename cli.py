#!/usr/bin/env python3
"""hub — CLI. Publicar direto, ver fila, status, canais.

  hub publicar <mp4> --canal X --titulo "T" [--descricao D] [--tags a,b] [--privacy public]
                     [--thumb capa.jpg] [--agora] [--dry-run]
  hub fila [canal]                 clips pendentes/erro
  hub status                       resumo por canal (fila, publicados, ultimo)
  hub canais                       lista canais + checagem de credenciais
  hub retry <clip_id>              volta clip com erro pra fila
  hub jobs [n]                     ultimos jobs
  hub tick                         roda 1 ciclo do daemon agora (sem servico)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import VERSION, canais as canais_mod, creds, db, runner  # noqa: E402


def cmd_publicar(a):
    canal = canais_mod.load(a.canal)
    con = db.connect()
    arquivo = os.path.abspath(a.arquivo)
    if not os.path.isfile(arquivo):
        sys.exit(f'arquivo nao existe: {arquivo}')
    clip_id = db.add_clip(con, canal=a.canal, arquivo=arquivo, titulo=a.titulo, descricao=a.descricao or '',
                          tags=a.tags or '', privacy=a.privacy or canal['privacy'],
                          thumb=os.path.abspath(a.thumb) if a.thumb else '', origem='cli')
    print(f'clip #{clip_id} na fila de {a.canal}')
    if a.agora or a.dry_run:
        job_id = db.add_job(con, a.canal, 'publicar_clip', db.now(), json.dumps({'clip_id': clip_id}))
        job = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        db.claim_job(con, job_id)
        vid = runner.run_job(con, job, {a.canal: canal}, dry_run=a.dry_run)
        if a.dry_run:
            con.execute('DELETE FROM clips WHERE id=?', (clip_id,))
            con.execute('DELETE FROM jobs WHERE id=?', (job_id,))
            print('dry-run: ok' if vid == 'DRY_RUN' else 'dry-run: FALHOU (ver acima)')
            sys.exit(0 if vid == 'DRY_RUN' else 1)
        sys.exit(0 if vid else 1)
    else:
        hs = ', '.join(canal['horarios']) or 'nenhum horario configurado!'
        print(f'sera publicado no proximo horario do canal ({hs})')


def cmd_fila(a):
    con = db.connect()
    q = "SELECT id,canal,status,tentativas,titulo,erro FROM clips WHERE status!='publicado'"
    args = ()
    if a.canal:
        q += ' AND canal=?'; args = (a.canal,)
    rows = con.execute(q + ' ORDER BY canal,id', args).fetchall()
    if not rows:
        print('fila vazia'); return
    for r in rows:
        extra = f'  <- {r["erro"][:60]}' if r['erro'] else ''
        print(f'#{r["id"]:<5} {r["canal"]:<14} {r["status"]:<11} t{r["tentativas"]} {r["titulo"][:55]}{extra}')


def cmd_status(a):
    con = db.connect()
    for c in canais_mod.load_all().values():
        db.upsert_canal(con, c)
    rows = db.resumo_canais(con)
    print(f'{"canal":<14} {"destino":<22} {"fila":>5} {"public.":>8} {"erros":>5}  ultimo')
    tf = tp = te = 0
    for r in rows:
        flag = '' if r['ativo'] else ' (inativo)'
        print(f'{r["nome"]:<14} {(r["destino_nome"] or "?")[:22]:<22} {r["fila"] or 0:>5} {r["publicados"] or 0:>8} '
              f'{r["erros"] or 0:>5}  {r["ultimo"] or "-"}{flag}')
        tf += r['fila'] or 0; tp += r['publicados'] or 0; te += r['erros'] or 0
    print(f'{len(rows)} canais | fila {tf} | publicados {tp} | erros {te} | hub v{VERSION}')


def cmd_canais(a):
    for c in canais_mod.load_all().values():
        chk = creds.check(c['config_dir']) if c['config_dir'] else {}
        ok = all(chk.values()) and bool(chk)
        print(f'{c["nome"]:<14} {"OK " if ok else "SEM"} creds  horarios={",".join(c["horarios"]) or "-"}  '
              f'privacy={c["privacy"]}  tg={len(c["telegram_chat_ids"])}  {c["config_dir"]}')
        if a.token and ok:
            try:
                creds.access_token(c['config_dir'], force=True)
                print('               token: OK')
            except Exception as e:
                print(f'               token: FALHOU — {e}')


def cmd_retry(a):
    con = db.connect()
    n = con.execute("UPDATE clips SET status='fila', erro='', tentativas=0 WHERE id=? AND status='erro'",
                    (a.clip_id,)).rowcount
    print('ok, voltou pra fila' if n else 'clip nao esta em erro')


def cmd_jobs(a):
    con = db.connect()
    for r in con.execute("SELECT id,canal,tipo,status,run_at,resultado,erro FROM jobs WHERE tipo!='scan_entrada' "
                         'ORDER BY id DESC LIMIT ?', (a.n,)).fetchall()[::-1]:
        print(f'#{r["id"]:<5} {r["run_at"]} {r["canal"]:<14} {r["tipo"]:<17} {r["status"]:<8} {r["resultado"] or r["erro"]}')


def cmd_tick(a):
    import hub
    hub.tick(db.connect())


def main():
    p = argparse.ArgumentParser(prog='hub', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--version', action='version', version=f'hub v{VERSION}')
    sp = p.add_subparsers(dest='cmd', required=True)
    x = sp.add_parser('publicar'); x.set_defaults(f=cmd_publicar)
    x.add_argument('arquivo'); x.add_argument('--canal', required=True); x.add_argument('--titulo', required=True)
    x.add_argument('--descricao'); x.add_argument('--tags'); x.add_argument('--privacy', choices=['public', 'unlisted', 'private'])
    x.add_argument('--thumb'); x.add_argument('--agora', action='store_true'); x.add_argument('--dry-run', action='store_true')
    x = sp.add_parser('fila'); x.add_argument('canal', nargs='?'); x.set_defaults(f=cmd_fila)
    sp.add_parser('status').set_defaults(f=cmd_status)
    x = sp.add_parser('canais'); x.add_argument('--token', action='store_true', help='testa refresh do OAuth'); x.set_defaults(f=cmd_canais)
    x = sp.add_parser('retry'); x.add_argument('clip_id', type=int); x.set_defaults(f=cmd_retry)
    x = sp.add_parser('jobs'); x.add_argument('n', nargs='?', type=int, default=20); x.set_defaults(f=cmd_jobs)
    sp.add_parser('tick').set_defaults(f=cmd_tick)
    a = p.parse_args()
    a.f(a)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""hub — CLI. Publicar direto, ver fila, status, canais, contas sociais.

  hub publicar <mp4> --canal X --titulo "T" [--descricao D] [--tags a,b] [--privacy public]
                     [--thumb capa.jpg] [--destinos youtube,uploadpost:tiktok] [--agora] [--dry-run]
  hub fila [canal]                 clips pendentes/erro (todos os destinos)
  hub status [--destinos]          resumo por canal, ou por canal e destino
  hub canais [--token]             canais, destinos e checagem de credenciais
  hub retry <clip_id>              volta clip com erro pra fila
  hub jobs [n]                     ultimos jobs
  hub social                       perfis e contas conectadas no Upload-Post
  hub social-link <canal>          link pro dono autorizar TikTok/Instagram daquele canal
  hub tick                         roda 1 ciclo do daemon agora (sem servico)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import VERSION, canais as canais_mod, creds, db, runner  # noqa: E402


def _destinos_pedidos(canal, arg):
    """--destinos filtra os destinos do canal; sem o argumento, usa todos."""
    if not arg:
        return canal['destinos']
    querem = [d.strip() for d in arg.split(',') if d.strip()]
    tem = {d['destino']: d for d in canal['destinos']}
    faltam = [d for d in querem if d not in tem]
    if faltam:
        sys.exit(f'canal {canal["nome"]} nao tem o destino {faltam} (tem: {list(tem)})')
    return [tem[d] for d in querem]


def cmd_publicar(a):
    canal = canais_mod.load(a.canal)
    con = db.connect()
    arquivo = os.path.abspath(a.arquivo)
    if not os.path.isfile(arquivo):
        sys.exit(f'arquivo nao existe: {arquivo}')
    destinos = _destinos_pedidos(canal, a.destinos)
    ids = db.enfileirar(con, a.canal, destinos, privacy=a.privacy, arquivo=arquivo, titulo=a.titulo,
                        descricao=a.descricao or '', tags=a.tags or '',
                        thumb=os.path.abspath(a.thumb) if a.thumb else '', origem='cli')
    print(f'na fila de {a.canal}: ' + ', '.join(f'{d}=#{i}' for d, i in ids.items()))
    if not (a.agora or a.dry_run):
        for d in destinos:
            hs = ', '.join(d['horarios']) or 'SEM HORARIO no YAML'
            print(f'  {d["destino"]}: sai em {hs}')
        return

    falhou = False
    for destino, clip_id in ids.items():
        job_id = db.add_job(con, a.canal, 'publicar_clip', db.now(),
                            json.dumps({'clip_id': clip_id, 'destino': destino}))
        job = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        db.claim_job(con, job_id)
        vid = runner.run_job(con, job, {a.canal: canal}, dry_run=a.dry_run)
        falhou = falhou or not vid
        if a.dry_run:
            con.execute('DELETE FROM clips WHERE id=?', (clip_id,))
            con.execute('DELETE FROM jobs WHERE id=?', (job_id,))
    if a.dry_run:
        print('dry-run: ok' if not falhou else 'dry-run: FALHOU (ver acima)')
    sys.exit(1 if falhou else 0)


def cmd_fila(a):
    con = db.connect()
    q = ("SELECT id,canal,destino,status,tentativas,titulo,erro FROM clips "
         "WHERE status NOT IN ('publicado','pulado')")
    args = ()
    if a.canal:
        q += ' AND canal=?'; args = (a.canal,)
    rows = con.execute(q + ' ORDER BY canal,destino,id', args).fetchall()
    if not rows:
        print('fila vazia'); return
    for r in rows:
        extra = f'  <- {r["erro"][:55]}' if r['erro'] else ''
        print(f'#{r["id"]:<5} {r["canal"]:<10} {r["destino"]:<22} {r["status"]:<11} '
              f't{r["tentativas"]} {r["titulo"][:42]}{extra}')


def cmd_status(a):
    con = db.connect()
    for c in canais_mod.load_all().values():
        db.upsert_canal(con, c)
    if a.destinos:
        print(f'{"canal":<12} {"destino":<24} {"fila":>5} {"public.":>8} {"erros":>5} {"pulad.":>6}  ultimo')
        for r in db.resumo_destinos(con):
            print(f'{r["canal"]:<12} {r["destino"]:<24} {r["fila"] or 0:>5} {r["publicados"] or 0:>8} '
                  f'{r["erros"] or 0:>5} {r["pulados"] or 0:>6}  {r["ultimo"] or "-"}')
        return
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
    social = {}
    if a.token:
        try:
            from lib import uploadpost
            social = uploadpost.perfis()[0]
        except Exception as e:
            print(f'(upload-post indisponivel: {e})')
    for c in canais_mod.load_all().values():
        chk = creds.check(c['config_dir']) if c['config_dir'] else {}
        ok = all(chk.values()) and bool(chk)
        dst = ' '.join(f'{d["destino"]}[{",".join(d["horarios"]) or "-"}]' for d in c['destinos'])
        print(f'{c["nome"]:<12} {"OK " if ok else "SEM"} creds  tg={len(c["telegram_chat_ids"])}  {dst}')
        if not a.token:
            continue
        if ok:
            try:
                creds.access_token(c['config_dir'], force=True)
                print('             youtube: token OK')
            except Exception as e:
                print(f'             youtube: token FALHOU — {e}')
        redes = [d['destino'].split(':')[1] for d in c['destinos'] if d['destino'].startswith('uploadpost:')]
        if redes and social:
            perfil = c['uploadpost_perfil']
            contas = social.get(perfil)
            if contas is None:
                print(f'             upload-post: perfil "{perfil}" NAO existe — rode hub social-link {c["nome"]}')
            else:
                falta = [r for r in redes if r not in contas]
                print(f'             upload-post[{perfil}]: ' +
                      (f'FALTA conectar {falta}' if falta else f'OK ({", ".join(contas)})'))


def cmd_retry(a):
    con = db.connect()
    n = con.execute("UPDATE clips SET status='fila', erro='', tentativas=0 "
                    "WHERE id=? AND status IN ('erro','pulado')", (a.clip_id,)).rowcount
    print('ok, voltou pra fila' if n else 'clip nao esta em erro nem pulado')


def cmd_jobs(a):
    con = db.connect()
    for r in con.execute("SELECT id,canal,tipo,status,run_at,payload,resultado,erro FROM jobs "
                         "WHERE tipo!='scan_entrada' ORDER BY id DESC LIMIT ?", (a.n,)).fetchall()[::-1]:
        alvo = json.loads(r['payload'] or '{}').get('destino', '')
        print(f'#{r["id"]:<5} {r["run_at"]} {r["canal"]:<10} {alvo:<22} {r["status"]:<8} '
              f'{r["resultado"] or r["erro"]}')


def cmd_social(a):
    from lib import uploadpost
    perfis, plano, limite = uploadpost.perfis()
    print(f'plano={plano} perfis={len(perfis)}/{limite}')
    for nome, contas in perfis.items():
        print(f'  {nome:<14} ' + (', '.join(f'{p}={c}' for p, c in contas.items()) or 'nenhuma conta conectada'))


def cmd_social_link(a):
    from lib import uploadpost
    canal = canais_mod.load(a.canal)
    redes = [d['destino'].split(':')[1] for d in canal['destinos'] if d['destino'].startswith('uploadpost:')]
    if not redes:
        redes = ['tiktok', 'instagram']
        print(f'(canal {a.canal} nao declara destino uploadpost:*; gerando link para {redes})')
    url = uploadpost.link_conexao(canal['uploadpost_perfil'], redes,
                                  titulo=f'{canal.get("destino_nome") or a.canal} — conectar contas')
    print(f'perfil: {canal["uploadpost_perfil"]}  redes: {", ".join(redes)}')
    print(url)
    print('(abra num navegador logado nas contas; validade 48h)')


def cmd_tick(a):
    import hub
    hub.tick(db.connect())


def main():
    p = argparse.ArgumentParser(prog='hub', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--version', action='version', version=f'hub v{VERSION}')
    sp = p.add_subparsers(dest='cmd', required=True)
    x = sp.add_parser('publicar'); x.set_defaults(f=cmd_publicar)
    x.add_argument('arquivo'); x.add_argument('--canal', required=True); x.add_argument('--titulo', required=True)
    x.add_argument('--descricao'); x.add_argument('--tags')
    x.add_argument('--privacy', choices=['public', 'unlisted', 'private'])
    x.add_argument('--destinos', help='subconjunto dos destinos do canal (ex.: uploadpost:instagram)')
    x.add_argument('--thumb'); x.add_argument('--agora', action='store_true'); x.add_argument('--dry-run', action='store_true')
    x = sp.add_parser('fila'); x.add_argument('canal', nargs='?'); x.set_defaults(f=cmd_fila)
    x = sp.add_parser('status'); x.add_argument('--destinos', action='store_true', help='quebra por destino')
    x.set_defaults(f=cmd_status)
    x = sp.add_parser('canais'); x.add_argument('--token', action='store_true', help='testa OAuth e contas sociais')
    x.set_defaults(f=cmd_canais)
    x = sp.add_parser('retry'); x.add_argument('clip_id', type=int); x.set_defaults(f=cmd_retry)
    x = sp.add_parser('jobs'); x.add_argument('n', nargs='?', type=int, default=20); x.set_defaults(f=cmd_jobs)
    sp.add_parser('social').set_defaults(f=cmd_social)
    x = sp.add_parser('social-link'); x.add_argument('canal'); x.set_defaults(f=cmd_social_link)
    sp.add_parser('tick').set_defaults(f=cmd_tick)
    a = p.parse_args()
    a.f(a)


if __name__ == '__main__':
    main()

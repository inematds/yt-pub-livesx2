"""Executa jobs. Unico lugar que chama o publisher e mexe no status dos clips.

Tipos:
  publicar_proximo   pega o 1o clip da fila do canal e publica (gerado pelos horarios do YAML)
  publicar_clip      publica um clip especifico (payload.clip_id) — CLI --agora
  scan_entrada       varre entrada/<canal>/ e poe MP4 novos na fila
"""
import json
import os
import shutil

from . import canais as canais_mod
from . import db, notify, youtube
from .entrada import scan_entrada

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLICADOS_DIR = os.environ.get('HUB_PUBLICADOS', os.path.join(ROOT, 'data', 'publicados'))


def _log_factory(prefix, log):
    return lambda m: log(f'{prefix} {m}')


def publicar_clip(con, canal, clip, dry_run=False, log=print):
    """Publica 1 clip ja 'claimed'. Atualiza clips, avisa Telegram. Retorna video_id ou None."""
    plog = _log_factory(f'  [{canal["nome"]}]', log)
    plog(f'publicando #{clip["id"]}: {clip["titulo"][:60]}')
    try:
        vid = youtube.publish(clip['arquivo'], clip['titulo'], clip['descricao'], canal['config_dir'],
                              tags=clip['tags'], privacy=clip['privacy'] or canal['privacy'],
                              dry_run=dry_run, log=plog)
    except youtube.PublishFailure as e:
        requeue = e.retriable and clip['tentativas'] < canal.get('max_tentativas', 3)
        db.finish_clip(con, clip['id'], erro=str(e), requeue=requeue)
        plog(f'FALHA ({"vai tentar de novo" if requeue else "definitiva"}): {e}')
        return None
    if dry_run:
        con.execute("UPDATE clips SET status='fila', tentativas=tentativas-1 WHERE id=?", (clip['id'],))
        return 'DRY_RUN'
    db.finish_clip(con, clip['id'], video_id=vid)
    if clip['thumb'] and os.path.isfile(clip['thumb']):
        try:
            youtube.set_thumbnail(vid, clip['thumb'], canal['config_dir'])
        except Exception as e:
            plog(f'thumb falhou (video publicado): {e}')
    row = con.execute('SELECT * FROM clips WHERE id=?', (clip['id'],)).fetchone()
    notify.publicado(canal, row, log=plog)
    _arquivar(clip['arquivo'], canal['nome'], plog)
    plog(f'OK -> {row["url"]}')
    return vid


def _arquivar(arquivo, canal, log):
    """Move o MP4 publicado de entrada/ para data/publicados/<canal>/ (so se veio da entrada)."""
    if not arquivo.startswith(os.path.join(ROOT, 'entrada') + os.sep):
        return
    dest = os.path.join(PUBLICADOS_DIR, canal)
    os.makedirs(dest, exist_ok=True)
    try:
        shutil.move(arquivo, os.path.join(dest, os.path.basename(arquivo)))
    except Exception as e:
        log(f'nao moveu {arquivo}: {e}')


def run_job(con, job, todos_canais=None, dry_run=False, log=print):
    """Executa um job ja 'claimed'. Sempre finaliza o job (ok/erro)."""
    todos_canais = todos_canais or canais_mod.load_all()
    payload = json.loads(job['payload'] or '{}')
    canal = todos_canais.get(job['canal'])
    try:
        if not canal:
            raise RuntimeError(f'canal {job["canal"]} nao existe mais')
        if job['tipo'] == 'scan_entrada':
            novos = scan_entrada(con, canal, log=log)
            db.finish_job(con, job['id'], True, f'{novos} novo(s)')
        elif job['tipo'] in ('publicar_proximo', 'publicar_clip'):
            if job['tipo'] == 'publicar_clip':
                clip = con.execute('SELECT * FROM clips WHERE id=?', (payload.get('clip_id'),)).fetchone()
            else:
                clip = db.next_clip(con, canal['nome'])
            if not clip:
                db.finish_job(con, job['id'], True, 'fila vazia')
                return 'fila vazia'
            if not db.claim_clip(con, clip['id']):
                db.finish_job(con, job['id'], True, f'clip {clip["id"]} ja em andamento')
                return 'ocupado'
            con.execute("UPDATE jobs SET payload=? WHERE id=?",
                        (json.dumps({**payload, 'clip_id': clip['id']}), job['id']))
            clip = con.execute('SELECT * FROM clips WHERE id=?', (clip['id'],)).fetchone()
            vid = publicar_clip(con, canal, clip, dry_run=dry_run, log=log)
            db.finish_job(con, job['id'], bool(vid), vid or clip['erro'] or 'erro')
            return vid
        else:
            raise RuntimeError(f'tipo desconhecido: {job["tipo"]}')
    except Exception as e:
        db.finish_job(con, job['id'], False, f'{type(e).__name__}: {e}')
        log(f'  job {job["id"]} ERRO: {e}')
        return None

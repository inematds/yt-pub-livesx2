"""Executa jobs. Unico lugar que chama um publisher e mexe no status dos clips.

Tipos:
  publicar_proximo   pega o 1o clip da fila do canal NAQUELE destino e publica
  publicar_clip      publica um clip especifico (payload.clip_id) — CLI --agora
  scan_entrada       varre entrada/<canal>/ e poe MP4 novos na fila de cada destino

Destinos: 'youtube' (nativo, via config_dir do canal) e 'uploadpost:<rede>'
(TikTok, Instagram e demais, via API do Upload-Post com o perfil do canal).
"""
import json
import os
import shutil

from . import canais as canais_mod
from . import db, notify, uploadpost, youtube
from .entrada import scan_entrada
from .youtube import PublishFailure

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLICADOS_DIR = os.environ.get('HUB_PUBLICADOS', os.path.join(ROOT, 'data', 'publicados'))


class FormatoIncompativel(Exception):
    """O arquivo nao serve pra essa rede (duracao). Vira 'pulado', nao erro."""


def _log_factory(prefix, log):
    return lambda m: log(f'{prefix} {m}')


def _id_da_url(url, rede, clip_id):
    """Id externo a partir da URL devolvida pela rede (ex.: .../p/ABC123 -> ABC123)."""
    seg = [s for s in str(url).split('?')[0].rstrip('/').split('/') if s]
    return seg[-1] if len(seg) > 2 else f'{rede}:{clip_id}'


def _publicar_no_destino(canal, clip, destino, dry_run, plog):
    """Chama o publisher do destino. Retorna (id_externo, url). Levanta PublishFailure."""
    if destino == 'youtube':
        vid = youtube.publish(clip['arquivo'], clip['titulo'], clip['descricao'], canal['config_dir'],
                              tags=clip['tags'], privacy=clip['privacy'] or canal['privacy'],
                              dry_run=dry_run, log=plog)
        return vid, ('DRY_RUN' if vid == 'DRY_RUN' else f'https://www.youtube.com/watch?v={vid}')

    if destino.startswith('uploadpost:'):
        rede = destino.split(':', 1)[1]
        ok, motivo = uploadpost.checar_formato(clip['arquivo'], rede, log=plog)
        if not ok:
            raise FormatoIncompativel(motivo)
        urls = uploadpost.publish(clip['arquivo'], clip['titulo'], clip['descricao'],
                                  canal.get('uploadpost_perfil') or canal['nome'], [rede],
                                  privacy=clip['privacy'] or canal['privacy'], dry_run=dry_run,
                                  thumb=clip['thumb'], external_id=f'hub-{clip["id"]}', log=plog)
        url = urls.get(rede, '')
        if url == 'DRY_RUN':
            return 'DRY_RUN', 'DRY_RUN'
        return _id_da_url(url, rede, clip['id']), url

    raise PublishFailure(f'destino desconhecido: {destino}', retriable=False)


def publicar_clip(con, canal, clip, dry_run=False, log=print):
    """Publica 1 clip ja 'claimed'. Atualiza clips, avisa Telegram. Retorna id externo ou None."""
    destino = clip['destino'] if 'destino' in clip.keys() else 'youtube'
    plog = _log_factory(f'  [{canal["nome"]}/{destino}]', log)
    plog(f'publicando #{clip["id"]}: {clip["titulo"][:60]}')
    try:
        ext_id, url = _publicar_no_destino(canal, clip, destino, dry_run, plog)
    except FormatoIncompativel as e:
        db.finish_clip(con, clip['id'], erro=str(e), pulado=True)
        plog(f'PULADO: {e}')
        return None
    except PublishFailure as e:
        requeue = e.retriable and clip['tentativas'] < canal.get('max_tentativas', 3)
        db.finish_clip(con, clip['id'], erro=str(e), requeue=requeue)
        plog(f'FALHA ({"vai tentar de novo" if requeue else "definitiva"}): {e}')
        return None
    except Exception as e:  # publisher quebrou de forma inesperada: nao perde o clip
        db.finish_clip(con, clip['id'], erro=f'{type(e).__name__}: {e}', requeue=False)
        plog(f'FALHA inesperada: {type(e).__name__}: {e}')
        return None

    if dry_run:
        con.execute("UPDATE clips SET status='fila', tentativas=tentativas-1 WHERE id=?", (clip['id'],))
        return 'DRY_RUN'

    db.finish_clip(con, clip['id'], video_id=ext_id, url=url)
    if destino == 'youtube' and clip['thumb'] and os.path.isfile(clip['thumb']):
        try:
            youtube.set_thumbnail(ext_id, clip['thumb'], canal['config_dir'])
        except Exception as e:
            plog(f'thumb falhou (video publicado): {e}')
    row = con.execute('SELECT * FROM clips WHERE id=?', (clip['id'],)).fetchone()
    notify.publicado(canal, row, destino=destino, log=plog)
    _arquivar_se_terminou(con, row, plog)
    plog(f'OK -> {row["url"]}')
    return ext_id


def _arquivar_se_terminou(con, clip, log):
    """Move o MP4 de entrada/ pra data/publicados/ so quando NENHUM destino ainda espera por ele."""
    arquivo = clip['arquivo']
    if not arquivo.startswith(os.path.join(ROOT, 'entrada') + os.sep) or not os.path.isfile(arquivo):
        return
    pendentes = con.execute("SELECT COUNT(*) FROM clips WHERE arquivo=? AND status IN ('fila','publicando')",
                            (arquivo,)).fetchone()[0]
    if pendentes:
        return
    dest = os.path.join(PUBLICADOS_DIR, clip['canal'])
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
                clip = db.next_clip(con, canal['nome'], payload.get('destino', 'youtube'))
            if not clip:
                db.finish_job(con, job['id'], True, 'fila vazia')
                return 'fila vazia'
            if not db.claim_clip(con, clip['id']):
                db.finish_job(con, job['id'], True, f'clip {clip["id"]} ja em andamento')
                return 'ocupado'
            con.execute('UPDATE jobs SET payload=? WHERE id=?',
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

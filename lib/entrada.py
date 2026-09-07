"""Pasta de entrada: entrada/<canal>/*.mp4 vira fila.

Metadados opcionais ao lado do MP4:
  <nome>.json   {"titulo","descricao","tags","privacy","thumb"}
  <nome>.jpg    thumbnail
Sem JSON: titulo = nome do arquivo limpo. Arquivo em escrita (mtime < 60s) e ignorado.
Arquivo entra na fila UMA vez (chave = caminho absoluto em clips.arquivo).
"""
import json
import os
import re
import time

from . import db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRADA_DIR = os.environ.get('HUB_ENTRADA', os.path.join(ROOT, 'entrada'))


def titulo_do_nome(fname):
    base = os.path.splitext(os.path.basename(fname))[0]
    base = re.sub(r'^(clip_\d+_|\d+[_\- ]+)', '', base)
    base = re.sub(r'[_\-]+', ' ', base).strip()
    return base[:100] or 'Sem titulo'


def meta_do_lado(mp4):
    stem = os.path.splitext(mp4)[0]
    meta = {}
    if os.path.isfile(stem + '.json'):
        try:
            with open(stem + '.json') as f:
                meta = json.load(f)
        except Exception:
            meta = {}
    for ext in ('.jpg', '.jpeg', '.png'):
        if os.path.isfile(stem + ext):
            meta.setdefault('thumb', stem + ext)
    return meta


def scan_entrada(con, canal, dir_=None, log=print):
    pasta = os.path.join(dir_ or ENTRADA_DIR, canal['nome'])
    if not os.path.isdir(pasta):
        return 0
    ja = {(r['arquivo'], r['destino']) for r in
          con.execute('SELECT arquivo, destino FROM clips WHERE canal=?', (canal['nome'],))}
    novos = 0
    for f in sorted(os.listdir(pasta)):
        if not f.lower().endswith(('.mp4', '.mov', '.mkv', '.webm')):
            continue
        path = os.path.abspath(os.path.join(pasta, f))
        if time.time() - os.path.getmtime(path) < 60:
            continue  # ainda pode estar sendo escrito
        destinos = canal.get('destinos') or [{'destino': 'youtube', 'privacy': canal.get('privacy', 'public')}]
        faltam = [d for d in destinos if (path, d['destino']) not in ja]
        if not faltam:
            continue
        m = meta_do_lado(path)
        tags = m.get('tags', '')
        if isinstance(tags, list):
            tags = ','.join(tags)
        db.enfileirar(con, canal['nome'], faltam, privacy=m.get('privacy'), arquivo=path,
                      titulo=m.get('titulo') or titulo_do_nome(f), descricao=m.get('descricao', ''),
                      tags=tags, thumb=m.get('thumb', ''), origem='entrada')
        log(f'  [{canal["nome"]}] entrada -> fila ({", ".join(d["destino"] for d in faltam)}): {f}')
        novos += 1
    return novos

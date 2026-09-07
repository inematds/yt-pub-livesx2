"""Canais = arquivos YAML em canais/<nome>.yaml. Um arquivo por canal, sem segredo dentro.

Campos:
  destino_id / destino_nome   canal YouTube que recebe os clips
  origem_id / origem_nome     canal de lives (opcional; so pra quem corta)
  config_dir                  pasta com .env + credentials.enc (v1 ou nova) — segredos ficam la
  lives_dir                   onde ficam lives baixadas/cortadas (opcional)
  horarios: [HH:MM, ...]      quando publicar o proximo da fila (1 clip por horario)
  privacy                     public | unlisted | private
  telegram_chat_ids: [...]    quem recebe aviso de publicacao
  ativo                       true/false
"""
import glob
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANAIS_DIR = os.environ.get('HUB_CANAIS', os.path.join(ROOT, 'canais'))

DEFAULTS = {
    'destino_id': '', 'destino_nome': '', 'origem_id': '', 'origem_nome': '',
    'config_dir': '', 'lives_dir': '', 'horarios': [], 'privacy': 'public',
    'telegram_chat_ids': [], 'ativo': True, 'max_tentativas': 3,
}


def _norm(nome, raw):
    c = dict(DEFAULTS)
    c.update({k: v for k, v in (raw or {}).items() if v is not None})
    c['nome'] = nome
    c['horarios'] = [str(h).strip() for h in (c['horarios'] or []) if str(h).strip()]
    ids = c['telegram_chat_ids']
    if isinstance(ids, (str, int)):
        ids = [x.strip() for x in str(ids).split(',') if x.strip()]
    c['telegram_chat_ids'] = [str(x) for x in ids]
    c['config_dir'] = os.path.expanduser(str(c['config_dir'] or ''))
    return c


def load_all(dir_=None):
    dir_ = dir_ or CANAIS_DIR
    out = {}
    for path in sorted(glob.glob(os.path.join(dir_, '*.yaml'))):
        nome = os.path.splitext(os.path.basename(path))[0]
        if nome.startswith('_'):
            continue  # _exemplo.yaml etc.
        with open(path) as f:
            out[nome] = _norm(nome, yaml.safe_load(f))
    return out


def load(nome, dir_=None):
    c = load_all(dir_).get(nome)
    if not c:
        raise KeyError(f'canal "{nome}" nao existe em {dir_ or CANAIS_DIR}')
    return c


def save(c, dir_=None):
    dir_ = dir_ or CANAIS_DIR
    os.makedirs(dir_, exist_ok=True)
    data = {k: v for k, v in c.items() if k != 'nome' and v != DEFAULTS.get(k, object())}
    path = os.path.join(dir_, f"{c['nome']}.yaml")
    with open(path, 'w') as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return path

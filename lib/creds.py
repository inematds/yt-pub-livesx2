"""Credenciais por canal — le do config_dir do canal, NUNCA copia.

Formato identico ao v1 (yt-auth): config_dir/.env com CLIENT_ID/CLIENT_SECRET/API_KEY,
config_dir/credentials.enc (AES-GCM, nonce 12 bytes + ciphertext) e config_dir/.encryption_key
(chave base64). Assim os 17 canais ja autenticados funcionam sem migrar nada.
"""
import base64
import json
import os
import time
import urllib.parse
import urllib.request

TOKEN_URL = 'https://oauth2.googleapis.com/token'
_token_cache = {}  # config_dir -> (access_token, expira_em)


def load_env(config_dir):
    """Le config_dir/.env em dict (sem exportar pro processo)."""
    env = {}
    path = os.path.join(config_dir, '.env')
    if not os.path.isfile(path):
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def load_refresh_token(config_dir):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    with open(os.path.join(config_dir, '.encryption_key')) as f:
        key = base64.b64decode(f.read().strip())
    with open(os.path.join(config_dir, 'credentials.enc'), 'rb') as f:
        data = f.read()
    creds = json.loads(AESGCM(key).decrypt(data[:12], data[12:], None))
    return creds['refresh_token']


def check(config_dir):
    """Diagnostico sem rede: quais arquivos existem."""
    files = ['.env', 'credentials.enc', '.encryption_key']
    return {f: os.path.isfile(os.path.join(config_dir, f)) for f in files}


def access_token(config_dir, force=False):
    """Access token valido para o canal (cache em memoria ate expirar)."""
    now = time.time()
    cached = _token_cache.get(config_dir)
    if cached and not force and cached[1] > now + 60:
        return cached[0]
    env = load_env(config_dir)
    if not env.get('CLIENT_ID') or not env.get('CLIENT_SECRET'):
        raise RuntimeError(f'CLIENT_ID/CLIENT_SECRET ausentes em {config_dir}/.env')
    body = urllib.parse.urlencode({
        'client_id': env['CLIENT_ID'],
        'client_secret': env['CLIENT_SECRET'],
        'refresh_token': load_refresh_token(config_dir),
        'grant_type': 'refresh_token',
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body)
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors='replace')[:200]
        raise RuntimeError(f'refresh_token recusado ({e.code}): {detail}') from None
    tok = resp['access_token']
    _token_cache[config_dir] = (tok, now + int(resp.get('expires_in', 3600)))
    return tok


def api_key(config_dir):
    return load_env(config_dir).get('API_KEY', '')

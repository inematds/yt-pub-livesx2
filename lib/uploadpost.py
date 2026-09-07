"""Publisher Upload-Post (TikTok, Instagram e outras) — stateless, mesma forma do lib/youtube.py.

API: POST https://api.upload-post.com/api/upload (multipart, arquivo local direto).
Key: UPLOADPOST_API_KEY lida em runtime de ~/projetos/wifi/.env (ou ambiente). Nunca copiada.
Perfil (`user`) = nome do canal no hub, criado no painel do Upload-Post ou via API.
Docs: https://docs.upload-post.com/api/upload-video/
"""
import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid

from .youtube import PublishFailure

API = 'https://api.upload-post.com/api'
KEY_FILES = [os.path.expanduser('~/projetos/wifi/.env'), os.path.expanduser('~/projetos/openpcbotv2/.env')]


def api_key():
    k = os.environ.get('UPLOADPOST_API_KEY', '')
    for path in KEY_FILES:
        if k:
            break
        if os.path.isfile(path):
            for line in open(path):
                if line.startswith('UPLOADPOST_API_KEY='):
                    k = line.split('=', 1)[1].strip().strip('"').strip("'")
                    break
    if not k:
        raise PublishFailure('UPLOADPOST_API_KEY ausente (~/projetos/wifi/.env)', retriable=False)
    return k


def _request(method, path, data=None, headers=None, timeout=60):
    req = urllib.request.Request(f'{API}{path}', data=data, method=method,
                                 headers={'Authorization': f'Apikey {api_key()}', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')
        retriable = e.code in (408, 429, 500, 502, 503, 504)
        raise PublishFailure(f'upload-post HTTP {e.code}: {body[:300]}', retriable=retriable, status=e.code) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise PublishFailure(f'rede: {e}', retriable=True) from None


def perfis():
    """Perfis e contas conectadas: ({nome: {plataforma: conta}}, plano, limite)."""
    _, d = _request('GET', '/uploadposts/users')
    return {p['username']: {k: v for k, v in (p.get('social_accounts') or {}).items() if v}
            for p in d.get('profiles', [])}, d.get('plan', '?'), d.get('limit')


def criar_perfil(nome):
    """Cria o perfil (idempotente: se ja existe, devolve False sem erro)."""
    if nome in perfis()[0]:
        return False
    body = json.dumps({'username': nome}).encode()
    try:
        _request('POST', '/uploadposts/users', data=body, headers={'Content-Type': 'application/json'})
        return True
    except PublishFailure as e:
        if 'already' in str(e).lower() or 'exist' in str(e).lower():
            return False
        raise


def link_conexao(perfil, redes, titulo='Conectar contas', redirect_url=''):
    """URL onde o DONO das contas autoriza (OAuth na propria rede). Validade ~48h."""
    payload = {'username': perfil, 'platforms': list(redes), 'connect_title': titulo, 'show_calendar': False}
    if redirect_url:
        payload['redirect_url'] = redirect_url
    _, d = _request('POST', '/uploadposts/users/generate-jwt', data=json.dumps(payload).encode(),
                    headers={'Content-Type': 'application/json'})
    url = d.get('access_url')
    if not url:
        raise PublishFailure(f'sem access_url: {json.dumps(d)[:200]}', retriable=False)
    return url


# Guarda de formato: so barra o que e claramente incompativel, pra nao gastar upload do plano.
# A plataforma continua sendo a autoridade final — estes numeros vem da doc e devem ser
# confirmados no primeiro teste real de cada rede.
LIMITES = {
    'instagram': {'dur_max': 900, 'nota': 'Reels'},      # 15 min
    'tiktok': {'dur_max': 600, 'nota': 'video'},         # 10 min
    'facebook': {'dur_max': 5400, 'nota': 'Reels/video'},
}


def checar_formato(arquivo, plataforma, log=print):
    """(ok, motivo). ok=False -> clip vira 'pulado' no destino, sem consumir upload."""
    lim = LIMITES.get(plataforma)
    if not lim:
        return True, ''
    from .youtube import duracao_segundos
    dur = duracao_segundos(arquivo)
    if dur and dur > lim['dur_max']:
        return False, f'{plataforma}: {dur}s passa do limite de {lim["dur_max"]}s ({lim["nota"]})'
    if plataforma in ('instagram', 'tiktok'):
        w, h = _dimensoes(arquivo)
        if w and h and w > h:
            log(f'    aviso: video horizontal ({w}x{h}) em {plataforma}; a rede vai cortar ou barrar')
    return True, ''


def _dimensoes(arquivo):
    import subprocess
    try:
        out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                              'stream=width,height', '-of', 'csv=p=0', arquivo],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        w, h = out.split(',')[:2]
        return int(w), int(h)
    except Exception:
        return 0, 0


def _multipart(fields, files):
    boundary = f'----hub{uuid.uuid4().hex}'
    out = bytearray()
    for k, v in fields:
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for k, path in files:
        ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{os.path.basename(path)}"\r\n'
                f'Content-Type: {ctype}\r\n\r\n').encode()
        with open(path, 'rb') as f:
            out += f.read()
        out += b'\r\n'
    out += f'--{boundary}--\r\n'.encode()
    return bytes(out), f'multipart/form-data; boundary={boundary}'


def publish(arquivo, titulo, descricao, perfil, plataformas, privacy='public', dry_run=False,
            thumb='', external_id='', log=print):
    """Sobe o arquivo para as plataformas do perfil. Retorna {plataforma: url}.
    dry_run: valida arquivo, key e contas conectadas; nao envia."""
    if not os.path.isfile(arquivo) or os.path.getsize(arquivo) == 0:
        raise PublishFailure(f'arquivo invalido: {arquivo}', retriable=False)
    if isinstance(plataformas, str):
        plataformas = [p.strip() for p in plataformas.split(',') if p.strip()]
    todos, plano, _ = perfis()
    contas = todos.get(perfil)
    if contas is None:
        raise PublishFailure(f'perfil "{perfil}" nao existe no Upload-Post', retriable=False)
    faltam = [p for p in plataformas if p not in contas]
    if faltam:
        raise PublishFailure(f'perfil {perfil} sem conta conectada em {faltam} (tem: {list(contas)})', retriable=False)
    log(f'    upload-post: perfil={perfil} plano={plano} -> {plataformas} ({os.path.getsize(arquivo)/1048576:.1f} MB)')
    if dry_run:
        log('    DRY RUN — parando antes do upload')
        return {p: 'DRY_RUN' for p in plataformas}

    fields = [('user', perfil), ('title', titulo[:150])]
    fields += [('platform[]', p) for p in plataformas]
    if descricao:
        fields.append(('description', descricao[:5000]))
    if 'instagram' in plataformas:
        fields += [('media_type', 'REELS'), ('share_to_feed', 'true')]
    if 'tiktok' in plataformas and privacy == 'private':
        fields.append(('privacy_level', 'SELF_ONLY'))
    if external_id:
        fields.append(('external_id', str(external_id)))
    files = [('video', arquivo)]
    if thumb and os.path.isfile(thumb) and 'instagram' in plataformas:
        files.append(('cover_image', thumb))
    body, ctype = _multipart(fields, files)
    headers = {'Content-Type': ctype, 'Idempotency-Key': f'hub-{external_id or uuid.uuid4().hex}'}
    status, d = _request('POST', '/upload', data=body, headers=headers, timeout=1800)
    if not d.get('success'):
        raise PublishFailure(f'upload-post recusou: {d.get("message") or d}', retriable=False)
    resultados = d.get('results') or {}
    if not resultados:  # assincrono: devolve request_id pra consultar depois
        rid = d.get('request_id') or d.get('job_id')
        log(f'    upload-post: em background, request_id={rid}')
        return {p: f'pending:{rid}' for p in plataformas}
    urls = {}
    for p, r in resultados.items():
        if r.get('success'):
            urls[p] = r.get('url') or 'ok'
        else:
            log(f'    upload-post {p}: {r.get("error")}')
    if not urls:
        raise PublishFailure(f'todas as plataformas falharam: {json.dumps(resultados)[:300]}', retriable=False)
    return urls


def status(request_id):
    _, d = _request('GET', f'/uploadposts/status?request_id={request_id}')
    return d

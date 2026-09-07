"""Publisher YouTube — upload resumable via Data API v3, stateless.

Recebe caminho + metadados + config_dir do canal. Nao toca no banco, nao decide horario.
Falha transitoria -> PublishFailure(retriable=True); permanente -> retriable=False.
Mesmo fluxo do scripts/yt-publish (bash), em Python, com --dry-run.
"""
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from . import creds

UPLOAD_URL = 'https://www.googleapis.com/upload/youtube/v3/videos'
API = 'https://www.googleapis.com/youtube/v3'


class PublishFailure(Exception):
    def __init__(self, msg, retriable=True, status=None):
        super().__init__(msg)
        self.retriable = retriable
        self.status = status


def build_metadata(titulo, descricao, tags='', privacy='public', category='27', language='pt'):
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(',') if t.strip()]
    return {
        'snippet': {
            'title': titulo[:100], 'description': descricao[:5000],
            'tags': tags[:30], 'categoryId': str(category),
            'defaultLanguage': language, 'defaultAudioLanguage': language,
        },
        'status': {'privacyStatus': privacy, 'selfDeclaredMadeForKids': False},
    }


def _http(req, timeout=60):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')
        retriable = e.code in (408, 429, 500, 502, 503, 504)
        raise PublishFailure(f'HTTP {e.code}: {body[:300]}', retriable=retriable, status=e.code) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise PublishFailure(f'rede: {e}', retriable=True) from None


def publish(arquivo, titulo, descricao, config_dir, tags='', privacy='public', dry_run=False, log=print):
    """Sobe o arquivo. Retorna video_id. Em dry_run: valida tudo, refresca token, para antes do upload
    e retorna 'DRY_RUN'."""
    if not os.path.isfile(arquivo):
        raise PublishFailure(f'arquivo nao existe: {arquivo}', retriable=False)
    size = os.path.getsize(arquivo)
    if size == 0:
        raise PublishFailure('arquivo vazio', retriable=False)
    if not titulo.strip():
        raise PublishFailure('titulo vazio', retriable=False)
    missing = [k for k, ok in creds.check(config_dir).items() if not ok]
    if missing:
        raise PublishFailure(f'faltam em {config_dir}: {missing}', retriable=False)

    log(f'    token: refresh via {config_dir}')
    try:
        token = creds.access_token(config_dir)
    except Exception as e:
        raise PublishFailure(f'oauth: {e}', retriable=False) from None
    meta = build_metadata(titulo, descricao, tags, privacy)
    log(f'    metadata: {meta["snippet"]["title"][:60]!r} privacy={privacy} {size/1048576:.1f} MB')
    if dry_run:
        log('    DRY RUN — parando antes do upload')
        return 'DRY_RUN'

    key = creds.api_key(config_dir)
    url = f'{UPLOAD_URL}?uploadType=resumable&part=snippet,status' + (f'&key={key}' if key else '')
    req = urllib.request.Request(url, data=json.dumps(meta).encode(), method='POST', headers={
        'Authorization': f'Bearer {token}', 'Content-Type': 'application/json; charset=UTF-8',
        'X-Upload-Content-Length': str(size), 'X-Upload-Content-Type': 'video/*'})
    _, headers, _ = _http(req)
    session = headers.get('Location') or headers.get('location')
    if not session:
        raise PublishFailure('sem Location no inicio do upload resumable', retriable=True)

    log('    enviando arquivo...')
    with open(arquivo, 'rb') as f:
        put = urllib.request.Request(session, data=f, method='PUT', headers={
            'Authorization': f'Bearer {token}', 'Content-Type': 'video/*', 'Content-Length': str(size)})
        _, _, body = _http(put, timeout=1800)
    resp = json.loads(body)
    vid = resp.get('id')
    if not vid:
        raise PublishFailure(f'resposta sem id: {body[:300]}', retriable=False)
    return vid


def set_thumbnail(video_id, thumb_path, config_dir):
    token = creds.access_token(config_dir)
    with open(thumb_path, 'rb') as f:
        data = f.read()
    req = urllib.request.Request(f'{UPLOAD_URL.replace("/videos", "/thumbnails/set")}?videoId={video_id}',
                                 data=data, method='POST', headers={
                                     'Authorization': f'Bearer {token}', 'Content-Type': 'image/jpeg'})
    _http(req, timeout=120)


def list_lives(origem_id, config_dir, max_results=20):
    """Lives ja encerradas do canal de origem (API key, sem OAuth)."""
    key = creds.api_key(config_dir)
    if not key:
        raise PublishFailure('API_KEY ausente', retriable=False)
    q = urllib.parse.urlencode({'part': 'snippet', 'channelId': origem_id, 'eventType': 'completed',
                                'type': 'video', 'order': 'date', 'maxResults': max_results, 'key': key})
    _, _, body = _http(urllib.request.Request(f'{API}/search?{q}'))
    out = []
    for it in json.loads(body).get('items', []):
        out.append({'video_id': it['id']['videoId'], 'titulo': it['snippet']['title'],
                    'data_live': it['snippet']['publishedAt'][:10]})
    return out


def duracao_segundos(arquivo):
    try:
        out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of',
                              'default=nw=1:nk=1', arquivo], capture_output=True, text=True, timeout=30)
        return int(float(out.stdout.strip() or 0))
    except Exception:
        return 0

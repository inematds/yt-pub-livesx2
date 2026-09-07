#!/usr/bin/env python3
"""Painel unico: todos os canais, fila, jobs. Uma porta (8200). Le hub.db; escreve so retry.

  GET  /                 pagina
  GET  /api/status       resumo por canal
  GET  /api/fila?canal=  clips nao publicados
  GET  /api/jobs         ultimos 50 jobs
  POST /api/retry/<id>   clip com erro volta pra fila
Senha: HUB_DASHBOARD_PASSWORD no ambiente ou <raiz>/.env (header X-Password ou ?senha=).
"""
import json
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lib import VERSION, canais as canais_mod, db  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def senha():
    s = os.environ.get('HUB_DASHBOARD_PASSWORD', '')
    if not s and os.path.isfile(os.path.join(ROOT, '.env')):
        for line in open(os.path.join(ROOT, '.env')):
            if line.startswith('HUB_DASHBOARD_PASSWORD='):
                s = line.split('=', 1)[1].strip().strip('"')
    return s


class H(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self, qs):
        s = senha()
        if not s:
            return True
        return self.headers.get('X-Password') == s or qs.get('senha', [''])[0] == s

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path == '/':
            self.path = '/index.html'
            self.directory = HERE
            return SimpleHTTPRequestHandler.do_GET(self)
        if not u.path.startswith('/api/'):
            return self._json({'erro': 'nao encontrado'}, 404)
        if not self._auth(qs):
            return self._json({'erro': 'senha'}, 401)
        con = db.connect()
        if u.path == '/api/status':
            for c in canais_mod.load_all().values():
                db.upsert_canal(con, c)
            return self._json({'versao': VERSION, 'canais': [dict(r) for r in db.resumo_canais(con)],
                               'destinos': [dict(r) for r in db.resumo_destinos(con)]})
        if u.path == '/api/fila':
            q = ("SELECT id,canal,destino,status,tentativas,titulo,origem,erro,criado_em FROM clips "
                 "WHERE status NOT IN ('publicado')")
            args = ()
            if qs.get('canal'):
                q += ' AND canal=?'; args = (qs['canal'][0],)
            return self._json([dict(r) for r in con.execute(q + ' ORDER BY canal,id LIMIT 500', args)])
        if u.path == '/api/jobs':
            return self._json([dict(r) for r in con.execute(
                "SELECT id,canal,tipo,status,run_at,payload,resultado,erro FROM jobs WHERE tipo!='scan_entrada' "
                'ORDER BY id DESC LIMIT 50')])
        return self._json({'erro': 'nao encontrado'}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        if not self._auth(parse_qs(u.query)):
            return self._json({'erro': 'senha'}, 401)
        if u.path.startswith('/api/retry/'):
            con = db.connect()
            n = con.execute("UPDATE clips SET status='fila', erro='', tentativas=0 "
                            "WHERE id=? AND status IN ('erro','pulado')",
                            (int(u.path.rsplit('/', 1)[1]),)).rowcount
            return self._json({'ok': bool(n)})
        return self._json({'erro': 'nao encontrado'}, 404)

    def directory_fix(self):
        pass


if __name__ == '__main__':
    port = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get('HUB_PORT', '8200'))
    os.chdir(HERE)
    print(f'hub dashboard v{VERSION} em http://0.0.0.0:{port}', flush=True)
    HTTPServer(('0.0.0.0', port), H).serve_forever()

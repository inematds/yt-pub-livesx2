"""Banco unico: data/hub.db. Tabelas: canais, clips, jobs, lives.

Regra: quem publica e o job. Um clip na fila so sai pela tabela jobs.
"""
import os
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get('HUB_DB', os.path.join(ROOT, 'data', 'hub.db'))

SCHEMA = """
CREATE TABLE IF NOT EXISTS canais (
    nome TEXT PRIMARY KEY,
    destino_id TEXT NOT NULL DEFAULT '',
    destino_nome TEXT NOT NULL DEFAULT '',
    origem_id TEXT NOT NULL DEFAULT '',
    config_dir TEXT NOT NULL DEFAULT '',
    ativo INTEGER NOT NULL DEFAULT 1,
    atualizado_em TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canal TEXT NOT NULL,
    arquivo TEXT NOT NULL DEFAULT '',
    titulo TEXT NOT NULL DEFAULT '',
    descricao TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    privacy TEXT NOT NULL DEFAULT 'public',
    thumb TEXT NOT NULL DEFAULT '',
    origem TEXT NOT NULL DEFAULT 'cli',      -- cli | entrada | live:<video_id> | v1
    status TEXT NOT NULL DEFAULT 'fila',     -- fila | publicando | publicado | erro
    video_id TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    tentativas INTEGER NOT NULL DEFAULT 0,
    erro TEXT NOT NULL DEFAULT '',
    criado_em TEXT NOT NULL DEFAULT '',
    publicado_em TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_clips_canal_status ON clips(canal, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_clips_video ON clips(canal, video_id) WHERE video_id != '';
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chave TEXT UNIQUE,                       -- idempotencia: pub:<canal>:<YYYY-MM-DD HH:MM>
    canal TEXT NOT NULL,
    tipo TEXT NOT NULL,                      -- publicar_proximo | publicar_clip | scan_entrada
    payload TEXT NOT NULL DEFAULT '{}',
    run_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pendente', -- pendente | rodando | ok | erro
    tentativas INTEGER NOT NULL DEFAULT 0,
    erro TEXT NOT NULL DEFAULT '',
    resultado TEXT NOT NULL DEFAULT '',
    criado_em TEXT NOT NULL DEFAULT '',
    iniciado_em TEXT NOT NULL DEFAULT '',
    fim_em TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, run_at);
CREATE TABLE IF NOT EXISTS lives (
    canal TEXT NOT NULL,
    video_id TEXT NOT NULL,
    titulo TEXT NOT NULL DEFAULT '',
    data_live TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pendente', -- pendente | cortada | erro
    qtd_clips INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (canal, video_id)
);
"""


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def connect(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=30000')
    con.executescript(SCHEMA)
    return con


# --- canais ---------------------------------------------------------------
def upsert_canal(con, c):
    con.execute("""INSERT INTO canais(nome,destino_id,destino_nome,origem_id,config_dir,ativo,atualizado_em)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(nome) DO UPDATE SET destino_id=excluded.destino_id,
        destino_nome=excluded.destino_nome, origem_id=excluded.origem_id, config_dir=excluded.config_dir,
        ativo=excluded.ativo, atualizado_em=excluded.atualizado_em""",
        (c['nome'], c.get('destino_id', ''), c.get('destino_nome', ''), c.get('origem_id', ''),
         c.get('config_dir', ''), 1 if c.get('ativo', True) else 0, now()))


# --- clips ----------------------------------------------------------------
def add_clip(con, **f):
    f.setdefault('criado_em', now())
    cols = ','.join(f); marks = ','.join('?' * len(f))
    return con.execute(f'INSERT INTO clips({cols}) VALUES({marks})', tuple(f.values())).lastrowid


def next_clip(con, canal):
    return con.execute("SELECT * FROM clips WHERE canal=? AND status='fila' ORDER BY id LIMIT 1",
                       (canal,)).fetchone()


def claim_clip(con, clip_id):
    """Marca 'publicando' so se ainda estiver na fila. True se conseguiu."""
    cur = con.execute("UPDATE clips SET status='publicando', tentativas=tentativas+1 "
                      "WHERE id=? AND status='fila'", (clip_id,))
    return cur.rowcount == 1


def finish_clip(con, clip_id, video_id=None, erro=None, requeue=False):
    if video_id:
        con.execute("UPDATE clips SET status='publicado', video_id=?, url=?, erro='', publicado_em=? WHERE id=?",
                    (video_id, f'https://www.youtube.com/watch?v={video_id}', now(), clip_id))
    else:
        status = 'fila' if requeue else 'erro'
        con.execute("UPDATE clips SET status=?, erro=? WHERE id=?", (status, (erro or '')[:500], clip_id))


def resumo_canais(con):
    return con.execute("""
        SELECT c.nome, c.destino_nome, c.ativo,
          SUM(CASE WHEN k.status='fila' THEN 1 ELSE 0 END) AS fila,
          SUM(CASE WHEN k.status='publicado' THEN 1 ELSE 0 END) AS publicados,
          SUM(CASE WHEN k.status='erro' THEN 1 ELSE 0 END) AS erros,
          MAX(CASE WHEN k.status='publicado' THEN k.publicado_em END) AS ultimo
        FROM canais c LEFT JOIN clips k ON k.canal=c.nome
        GROUP BY c.nome ORDER BY c.nome""").fetchall()


# --- jobs -----------------------------------------------------------------
def add_job(con, canal, tipo, run_at, payload='{}', chave=None):
    """Insere job; se chave ja existe, ignora (idempotente). Retorna id ou None."""
    cur = con.execute("INSERT OR IGNORE INTO jobs(chave,canal,tipo,payload,run_at,criado_em) VALUES(?,?,?,?,?,?)",
                      (chave, canal, tipo, payload, run_at, now()))
    return cur.lastrowid if cur.rowcount else None


def due_jobs(con, limit=10):
    return con.execute("SELECT * FROM jobs WHERE status='pendente' AND run_at<=? ORDER BY run_at LIMIT ?",
                       (now(), limit)).fetchall()


def claim_job(con, job_id):
    cur = con.execute("UPDATE jobs SET status='rodando', iniciado_em=?, tentativas=tentativas+1 "
                      "WHERE id=? AND status='pendente'", (now(), job_id))
    return cur.rowcount == 1


def finish_job(con, job_id, ok, texto=''):
    con.execute("UPDATE jobs SET status=?, fim_em=?, resultado=?, erro=? WHERE id=?",
                ('ok' if ok else 'erro', now(), texto[:500] if ok else '', '' if ok else texto[:500], job_id))


def release_stuck_jobs(con, max_min=30):
    """Jobs 'rodando' ha mais de max_min (processo morreu no meio) voltam a pendente."""
    cur = con.execute("""UPDATE jobs SET status='pendente'
        WHERE status='rodando' AND iniciado_em!='' AND
        (julianday(?) - julianday(iniciado_em)) * 1440 > ?""", (now(), max_min))
    con.execute("UPDATE clips SET status='fila' WHERE status='publicando' AND id NOT IN "
                "(SELECT CAST(json_extract(payload,'$.clip_id') AS INTEGER) FROM jobs WHERE status='rodando')")
    return cur.rowcount

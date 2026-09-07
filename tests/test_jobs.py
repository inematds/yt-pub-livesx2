"""Testes do nucleo sem rede: fila, claim atomico, jobs idempotentes, restart no meio, entrada.

  python3 -m pytest tests/ -q      ou      python3 tests/test_jobs.py
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lib import canais as canais_mod, db, entrada, runner, youtube  # noqa: E402

CANAL = {'nome': 't', 'destino_nome': 'Teste', 'config_dir': '/nao/existe', 'privacy': 'public',
         'horarios': ['10:00'], 'telegram_chat_ids': [], 'ativo': True, 'max_tentativas': 3}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.con = db.connect(os.path.join(self.tmp, 'hub.db'))
        db.upsert_canal(self.con, CANAL)
        self.mp4 = os.path.join(self.tmp, 'v.mp4')
        with open(self.mp4, 'wb') as f:
            f.write(b'0' * 1024)


class TestFila(Base):
    def test_claim_clip_uma_vez_so(self):
        cid = db.add_clip(self.con, canal='t', arquivo=self.mp4, titulo='a')
        self.assertTrue(db.claim_clip(self.con, cid))
        self.assertFalse(db.claim_clip(self.con, cid), 'segundo claim tem que falhar')

    def test_next_clip_ordem(self):
        a = db.add_clip(self.con, canal='t', arquivo=self.mp4, titulo='a')
        db.add_clip(self.con, canal='t', arquivo=self.mp4, titulo='b')
        self.assertEqual(db.next_clip(self.con, 't')['id'], a)

    def test_video_id_unico_por_canal(self):
        db.add_clip(self.con, canal='t', arquivo='', titulo='a', status='publicado', video_id='X1')
        with self.assertRaises(Exception):
            db.add_clip(self.con, canal='t', arquivo='', titulo='b', status='publicado', video_id='X1')


class TestJobs(Base):
    def test_chave_idempotente(self):
        self.assertIsNotNone(db.add_job(self.con, 't', 'publicar_proximo', '2026-01-01 10:00:00', chave='pub:t:2026-01-01 10:00'))
        self.assertIsNone(db.add_job(self.con, 't', 'publicar_proximo', '2026-01-01 10:00:00', chave='pub:t:2026-01-01 10:00'))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)

    def test_claim_job_uma_vez_so(self):
        jid = db.add_job(self.con, 't', 'publicar_proximo', '2000-01-01 00:00:00')
        self.assertEqual(len(db.due_jobs(self.con)), 1)
        self.assertTrue(db.claim_job(self.con, jid))
        self.assertFalse(db.claim_job(self.con, jid))
        self.assertEqual(len(db.due_jobs(self.con)), 0)

    def test_job_futuro_nao_vence(self):
        db.add_job(self.con, 't', 'publicar_proximo', '2999-01-01 00:00:00')
        self.assertEqual(len(db.due_jobs(self.con)), 0)

    def test_restart_no_meio_solta_job_e_clip(self):
        cid = db.add_clip(self.con, canal='t', arquivo=self.mp4, titulo='a')
        jid = db.add_job(self.con, 't', 'publicar_clip', '2000-01-01 00:00:00', json.dumps({'clip_id': cid}))
        db.claim_job(self.con, jid)
        db.claim_clip(self.con, cid)
        # simula processo morto ha 2h
        self.con.execute("UPDATE jobs SET iniciado_em=datetime('now','localtime','-2 hours') WHERE id=?", (jid,))
        self.assertEqual(db.release_stuck_jobs(self.con, max_min=30), 1)
        self.assertEqual(self.con.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0], 'pendente')
        self.assertEqual(self.con.execute('SELECT status FROM clips WHERE id=?', (cid,)).fetchone()[0], 'fila')

    def test_job_recente_rodando_nao_e_solto(self):
        cid = db.add_clip(self.con, canal='t', arquivo=self.mp4, titulo='a')
        jid = db.add_job(self.con, 't', 'publicar_clip', '2000-01-01 00:00:00', json.dumps({'clip_id': cid}))
        db.claim_job(self.con, jid); db.claim_clip(self.con, cid)
        self.assertEqual(db.release_stuck_jobs(self.con, max_min=30), 0)
        self.assertEqual(self.con.execute('SELECT status FROM clips WHERE id=?', (cid,)).fetchone()[0], 'publicando')


class TestRunner(Base):
    def test_publicar_proximo_fila_vazia(self):
        jid = db.add_job(self.con, 't', 'publicar_proximo', '2000-01-01 00:00:00')
        job = self.con.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
        db.claim_job(self.con, jid)
        self.assertEqual(runner.run_job(self.con, job, {'t': CANAL}, log=lambda m: None), 'fila vazia')
        self.assertEqual(self.con.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0], 'ok')

    def test_falha_permanente_sem_creds_vira_erro(self):
        cid = db.add_clip(self.con, canal='t', arquivo=self.mp4, titulo='a')
        jid = db.add_job(self.con, 't', 'publicar_proximo', '2000-01-01 00:00:00')
        job = self.con.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
        db.claim_job(self.con, jid)
        self.assertIsNone(runner.run_job(self.con, job, {'t': CANAL}, log=lambda m: None))
        clip = self.con.execute('SELECT status, erro FROM clips WHERE id=?', (cid,)).fetchone()
        self.assertEqual(clip['status'], 'erro')
        self.assertIn('faltam', clip['erro'])

    def test_falha_transitoria_volta_pra_fila(self):
        cid = db.add_clip(self.con, canal='t', arquivo=self.mp4, titulo='a')
        orig = youtube.publish
        youtube.publish = lambda *a, **k: (_ for _ in ()).throw(youtube.PublishFailure('503', retriable=True))
        try:
            db.claim_clip(self.con, cid)
            clip = self.con.execute('SELECT * FROM clips WHERE id=?', (cid,)).fetchone()
            runner.publicar_clip(self.con, CANAL, clip, log=lambda m: None)
        finally:
            youtube.publish = orig
        self.assertEqual(self.con.execute('SELECT status FROM clips WHERE id=?', (cid,)).fetchone()[0], 'fila')

    def test_metadata_limites(self):
        m = youtube.build_metadata('x' * 200, 'd', 'a,b', 'unlisted')
        self.assertEqual(len(m['snippet']['title']), 100)
        self.assertEqual(m['snippet']['tags'], ['a', 'b'])
        self.assertEqual(m['status']['privacyStatus'], 'unlisted')


class TestEntrada(Base):
    def test_scan_poe_na_fila_uma_vez(self):
        pasta = os.path.join(self.tmp, 'entrada', 't')
        os.makedirs(pasta)
        mp4 = os.path.join(pasta, 'clip_03_Meu Video-legal.mp4')
        with open(mp4, 'wb') as f:
            f.write(b'0')
        with open(os.path.join(pasta, 'clip_03_Meu Video-legal.json'), 'w') as f:
            json.dump({'descricao': 'desc', 'tags': ['a', 'b']}, f)
        os.utime(mp4, (0, 0))  # mtime antigo: nao esta "em escrita"
        n = entrada.scan_entrada(self.con, CANAL, dir_=os.path.join(self.tmp, 'entrada'), log=lambda m: None)
        self.assertEqual(n, 1)
        self.assertEqual(entrada.scan_entrada(self.con, CANAL, dir_=os.path.join(self.tmp, 'entrada'), log=lambda m: None), 0)
        c = self.con.execute('SELECT * FROM clips').fetchone()
        self.assertEqual(c['titulo'], 'Meu Video legal')
        self.assertEqual(c['tags'], 'a,b')
        self.assertEqual(c['descricao'], 'desc')

    def test_arquivo_recente_ignorado(self):
        pasta = os.path.join(self.tmp, 'entrada', 't'); os.makedirs(pasta)
        with open(os.path.join(pasta, 'novo.mp4'), 'wb') as f:
            f.write(b'0')
        self.assertEqual(entrada.scan_entrada(self.con, CANAL, dir_=os.path.join(self.tmp, 'entrada'), log=lambda m: None), 0)


class TestCanais(unittest.TestCase):
    def test_yaml_roundtrip(self):
        d = tempfile.mkdtemp()
        c = canais_mod._norm('abc', {'destino_nome': 'X', 'horarios': ['08:10', '16:10'], 'telegram_chat_ids': '1,2'})
        canais_mod.save(c, d)
        back = canais_mod.load('abc', d)
        self.assertEqual(back['horarios'], ['08:10', '16:10'])
        self.assertEqual(back['telegram_chat_ids'], ['1', '2'])
        self.assertEqual(back['privacy'], 'public')
        with open(os.path.join(d, '_exemplo.yaml'), 'w') as f:
            f.write('destino_nome: ignorado\n')
        self.assertNotIn('_exemplo', canais_mod.load_all(d))


if __name__ == '__main__':
    unittest.main(verbosity=1)

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

CANAL = canais_mod._norm('t', {'destino_nome': 'Teste', 'config_dir': '/nao/existe',
                               'privacy': 'public', 'horarios': ['10:00'], 'max_tentativas': 3})


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


CANAL_MULTI = dict(CANAL, uploadpost_perfil='t', destinos=[
    {'destino': 'youtube', 'horarios': ['10:00'], 'privacy': 'public'},
    {'destino': 'uploadpost:tiktok', 'horarios': ['19:00'], 'privacy': 'public'},
    {'destino': 'uploadpost:instagram', 'horarios': ['10:00'], 'privacy': 'public'},
])


class TestDestinos(Base):
    def test_enfileirar_uma_linha_por_destino_mesmo_grupo(self):
        ids = db.enfileirar(self.con, 't', CANAL_MULTI['destinos'], arquivo=self.mp4, titulo='a')
        self.assertEqual(len(ids), 3)
        rows = self.con.execute('SELECT destino, grupo FROM clips').fetchall()
        self.assertEqual({r['destino'] for r in rows}, set(ids))
        self.assertEqual(len({r['grupo'] for r in rows}), 1, 'todas as linhas no mesmo grupo')

    def test_fila_por_destino_e_independente(self):
        db.enfileirar(self.con, 't', CANAL_MULTI['destinos'], arquivo=self.mp4, titulo='a')
        yt = db.next_clip(self.con, 't', 'youtube')
        tk = db.next_clip(self.con, 't', 'uploadpost:tiktok')
        self.assertNotEqual(yt['id'], tk['id'])
        db.claim_clip(self.con, yt['id'])
        self.assertEqual(db.next_clip(self.con, 't', 'youtube'), None, 'youtube ocupado')
        self.assertIsNotNone(db.next_clip(self.con, 't', 'uploadpost:tiktok'), 'tiktok segue livre')

    def test_mesmo_video_id_em_destinos_diferentes(self):
        db.add_clip(self.con, canal='t', destino='youtube', status='publicado', video_id='X1')
        db.add_clip(self.con, canal='t', destino='uploadpost:tiktok', status='publicado', video_id='X1')
        with self.assertRaises(Exception):
            db.add_clip(self.con, canal='t', destino='youtube', status='publicado', video_id='X1')

    def test_privacy_explicito_vence_o_do_destino(self):
        dest = [{'destino': 'youtube', 'privacy': 'public'}]
        db.enfileirar(self.con, 't', dest, privacy='private', arquivo=self.mp4, titulo='a')
        self.assertEqual(self.con.execute('SELECT privacy FROM clips').fetchone()[0], 'private')

    def test_destino_desconhecido_vira_erro_definitivo(self):
        cid = db.add_clip(self.con, canal='t', destino='vimeo', arquivo=self.mp4, titulo='a')
        db.claim_clip(self.con, cid)
        clip = self.con.execute('SELECT * FROM clips WHERE id=?', (cid,)).fetchone()
        runner.publicar_clip(self.con, CANAL_MULTI, clip, log=lambda m: None)
        r = self.con.execute('SELECT status, erro FROM clips WHERE id=?', (cid,)).fetchone()
        self.assertEqual(r['status'], 'erro')
        self.assertIn('desconhecido', r['erro'])

    def test_formato_incompativel_vira_pulado_nao_erro(self):
        from lib import uploadpost
        cid = db.add_clip(self.con, canal='t', destino='uploadpost:instagram', arquivo=self.mp4, titulo='a')
        orig = uploadpost.checar_formato
        uploadpost.checar_formato = lambda *a, **k: (False, 'instagram: 3000s passa do limite')
        try:
            db.claim_clip(self.con, cid)
            clip = self.con.execute('SELECT * FROM clips WHERE id=?', (cid,)).fetchone()
            runner.publicar_clip(self.con, CANAL_MULTI, clip, log=lambda m: None)
        finally:
            uploadpost.checar_formato = orig
        r = self.con.execute('SELECT status, erro FROM clips WHERE id=?', (cid,)).fetchone()
        self.assertEqual(r['status'], 'pulado')
        self.assertIn('limite', r['erro'])

    def test_arquivo_so_e_arquivado_quando_todos_destinos_terminam(self):
        pasta = os.path.join(self.tmp, 'entrada', 't')
        os.makedirs(pasta)
        mp4 = os.path.join(pasta, 'v.mp4')
        with open(mp4, 'wb') as f:
            f.write(b'0')
        root, pub = runner.ROOT, runner.PUBLICADOS_DIR
        runner.ROOT, runner.PUBLICADOS_DIR = self.tmp, os.path.join(self.tmp, 'publicados')
        try:
            ids = db.enfileirar(self.con, 't', CANAL_MULTI['destinos'], arquivo=mp4, titulo='a')
            first = list(ids.values())[0]
            db.finish_clip(self.con, first, video_id='V1')
            row = self.con.execute('SELECT * FROM clips WHERE id=?', (first,)).fetchone()
            runner._arquivar_se_terminou(self.con, row, log=lambda m: None)
            self.assertTrue(os.path.isfile(mp4), 'nao pode mover: 2 destinos ainda na fila')
            for cid in list(ids.values())[1:]:
                db.finish_clip(self.con, cid, video_id=f'V{cid}')
            runner._arquivar_se_terminou(self.con, row, log=lambda m: None)
            self.assertFalse(os.path.isfile(mp4), 'todos terminaram: move')
            self.assertTrue(os.path.isfile(os.path.join(runner.PUBLICADOS_DIR, 't', 'v.mp4')))
        finally:
            runner.ROOT, runner.PUBLICADOS_DIR = root, pub

    def test_entrada_adiciona_destino_novo_sem_duplicar_os_antigos(self):
        pasta = os.path.join(self.tmp, 'entrada', 't')
        os.makedirs(pasta)
        mp4 = os.path.join(pasta, 'v.mp4')
        with open(mp4, 'wb') as f:
            f.write(b'0')
        os.utime(mp4, (0, 0))
        so_yt = dict(CANAL_MULTI, destinos=[CANAL_MULTI['destinos'][0]])
        entrada.scan_entrada(self.con, so_yt, dir_=os.path.join(self.tmp, 'entrada'), log=lambda m: None)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM clips').fetchone()[0], 1)
        entrada.scan_entrada(self.con, CANAL_MULTI, dir_=os.path.join(self.tmp, 'entrada'), log=lambda m: None)
        rows = self.con.execute('SELECT destino FROM clips ORDER BY destino').fetchall()
        self.assertEqual([r['destino'] for r in rows],
                         ['uploadpost:instagram', 'uploadpost:tiktok', 'youtube'])


class TestCanaisDestinos(unittest.TestCase):
    def test_sem_destinos_no_yaml_e_so_youtube(self):
        c = canais_mod._norm('x', {'horarios': ['08:00'], 'privacy': 'unlisted'})
        self.assertEqual(c['destinos'], [{'destino': 'youtube', 'horarios': ['08:00'], 'privacy': 'unlisted'}])
        self.assertEqual(c['uploadpost_perfil'], 'x')

    def test_lista_simples_herda_horarios_do_canal(self):
        c = canais_mod._norm('x', {'horarios': ['08:00'], 'destinos': ['youtube', 'uploadpost:tiktok']})
        self.assertEqual([d['destino'] for d in c['destinos']], ['youtube', 'uploadpost:tiktok'])
        self.assertTrue(all(d['horarios'] == ['08:00'] for d in c['destinos']))

    def test_destino_com_horario_proprio_e_duplicata_ignorada(self):
        c = canais_mod._norm('x', {'horarios': ['08:00'], 'destinos': [
            'youtube', {'destino': 'uploadpost:tiktok', 'horarios': ['19:00'], 'privacy': 'private'}, 'youtube']})
        self.assertEqual(len(c['destinos']), 2, 'youtube repetido entra uma vez so')
        self.assertEqual(c['destinos'][1]['horarios'], ['19:00'])
        self.assertEqual(c['destinos'][1]['privacy'], 'private')

    def test_save_nao_polui_yaml_no_caso_simples(self):
        d = tempfile.mkdtemp()
        c = canais_mod._norm('x', {'horarios': ['08:00']})
        canais_mod.save(c, d)
        with open(os.path.join(d, 'x.yaml')) as f:
            texto = f.read()
        self.assertNotIn('destinos', texto)
        self.assertNotIn('uploadpost_perfil', texto)
        c2 = canais_mod._norm('y', {'horarios': ['08:00'], 'destinos': ['youtube', 'uploadpost:tiktok']})
        canais_mod.save(c2, d)
        with open(os.path.join(d, 'y.yaml')) as f:
            self.assertIn('uploadpost:tiktok', f.read())
        self.assertEqual(len(canais_mod.load('y', d)['destinos']), 2)


class TestMigracao(unittest.TestCase):
    def test_banco_antigo_ganha_colunas_e_mantem_dados(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, 'velho.db')
        import sqlite3
        c = sqlite3.connect(path)
        c.executescript("""CREATE TABLE clips (id INTEGER PRIMARY KEY AUTOINCREMENT, canal TEXT NOT NULL,
            arquivo TEXT NOT NULL DEFAULT '', titulo TEXT NOT NULL DEFAULT '', descricao TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '', privacy TEXT NOT NULL DEFAULT 'public', thumb TEXT NOT NULL DEFAULT '',
            origem TEXT NOT NULL DEFAULT 'cli', status TEXT NOT NULL DEFAULT 'fila',
            video_id TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', tentativas INTEGER NOT NULL DEFAULT 0,
            erro TEXT NOT NULL DEFAULT '', criado_em TEXT NOT NULL DEFAULT '', publicado_em TEXT NOT NULL DEFAULT '');
            CREATE UNIQUE INDEX idx_clips_video ON clips(canal, video_id) WHERE video_id != '';
            INSERT INTO clips(canal,titulo,status,video_id) VALUES('lives8','antigo','publicado','ABC');""")
        c.commit(); c.close()
        con = db.connect(path)
        row = con.execute('SELECT * FROM clips').fetchone()
        self.assertEqual(row['titulo'], 'antigo')
        self.assertEqual(row['destino'], 'youtube', 'linha antiga vira destino youtube')
        # o indice unico agora inclui o destino: mesmo video_id em outra rede passa a ser permitido
        db.add_clip(con, canal='lives8', destino='uploadpost:tiktok', status='publicado', video_id='ABC')
        self.assertEqual(con.execute('SELECT COUNT(*) FROM clips').fetchone()[0], 2)


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

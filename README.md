# yt-pub-livesx2

Publicador de videos no YouTube para N canais: **1 daemon, 1 banco, 1 painel, N arquivos YAML**.
Versao 2 do [yt-pub-livesx](../yt-pub-livesx) — mesmo trabalho (clips de lives, imports, TikTok → YouTube),
sem os 35 processos, 18 portas e 17 copias do codigo.

- **Por que refazer:** [`docs/ANALISE.md`](docs/ANALISE.md) — numeros do v1, achados, os outros publicadores da casa, alternativas descartadas.
- **Como funciona:** [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) — fila de jobs, credenciais, migracao canal a canal, fase 2.
- **Publicar fora do YouTube:** [`docs/PUBLICADORES.md`](docs/PUBLICADORES.md) — Blotato, Upload-Post, Postiz, Zernio, Ayrshare lado a lado e o encaixe na v2.

## 📖 Guia de uso

Guia completo (landing + passo a passo): **https://inematds.github.io/yt-pub-livesx2/guia/**

## Em 60 segundos

```bash
cd ~/projetos/yt-pub-livesx2
pip install -r requirements.txt            # cryptography + PyYAML (ja instalados nesta maquina)

scripts/importar-historico --todos          # le as 17 instancias v1 (somente leitura) -> canais/*.yaml + historico
./hub status                                # 17 canais, 12.466 publicados
./hub canais --token                        # quais canais tem OAuth valido (hoje: 12 OK, 5 mortos)

./hub publicar video.mp4 --canal lives8 --titulo "Titulo" --dry-run   # valida tudo, nao sobe
./hub publicar video.mp4 --canal lives8 --titulo "Titulo" --agora     # sobe agora
./hub publicar video.mp4 --canal lives8 --titulo "Titulo"             # fila; sai no proximo horario do YAML
cp video.mp4 video.json entrada/lives8/                               # idem, sem CLI (n8n, scripts, humano)

python3 hub.py                              # daemon (ou systemd/yt-hub.service)
python3 dashboard/server.py 8200            # painel unico
```

## Estrutura

```
hub.py                 daemon: agenda pelos horarios do YAML, executa jobs vencidos (tick 30 s)
cli.py / ./hub         publicar | fila | status | canais [--token] | retry | jobs | tick
lib/
  db.py                schema + claim atomico de job/clip
  runner.py            unico executor; unico que publica
  youtube.py           publisher stateless: resumable upload, thumbnail, listar lives
  creds.py             .env + credentials.enc do config_dir do canal (formato v1, nada e copiado)
  entrada.py           entrada/<canal>/*.mp4 (+ .json/.jpg) -> fila
  notify.py            Telegram em processo apos publicar
  canais.py            canais/*.yaml
dashboard/             1 pagina, 1 porta, todos os canais, botao retry
scripts/
  importar-historico   v1 -> hub (read-only na origem, idempotente)
  yt-clip yt-thumbnail yt-publish yt-auth    copiados do v1 sem alteracao (fase 2)
canais/_exemplo.yaml   modelo de canal
systemd/               yt-hub.service, yt-hub-dashboard.service (2 servicos no total)
tests/test_jobs.py     15 testes sem rede
```

## Um canal

```yaml
# canais/lives8.yaml — gerado pelo importar-historico; edite a vontade, o hub recarrega sozinho
destino_id: UCJxL11aomXJiBZga-JXylgQ
destino_nome: Inema Vibe
config_dir: /home/nmaldaner/projetos/yt-pub-lives8/config   # .env + credentials.enc ficam AQUI
horarios: ["08:10", "16:10"]                                 # 1 clip da fila por horario
privacy: public
telegram_chat_ids: ["7852460115"]
ativo: false                                                 # true quando parar o yt-scheduler8
```

Segredo global unico: `TELEGRAM_NOTIFY_BOT_TOKEN` (e senha opcional do painel) em `<raiz>/.env` — ver `.env.example`.

## Estado (2026-09-06)

- Nucleo funcional e testado: fila, jobs idempotentes, restart no meio, entrada, publisher com dry-run real
  (decripta, refresca token, monta metadata, para antes do upload). Nenhum upload real foi feito.
- 17 canais importados, todos `ativo: false`: o v1 continua publicando ate voce migrar canal a canal
  (roteiro em `docs/ARQUITETURA.md`).
- Fase 2 (corte de lives, thumbnail, scan TikTok, alerta de erro) desenhada, nao construida.

Versao: `v2.0.0` (`lib/__init__.py`). Semver `vX.XX.YY`: minor carrega o patch; so major zera.

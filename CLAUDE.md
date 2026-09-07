# Instrucoes para Claude Code — yt-pub-livesx2

Versao 2 do yt-pub-livesx: **1 daemon (`hub.py`), 1 banco (`data/hub.db`), N canais (`canais/*.yaml`)**.
Ler `docs/ARQUITETURA.md` antes de mexer no nucleo; `docs/ANALISE.md` explica por que o v1 foi refeito.

## Regras

- **Mostrar erro + solucao antes de editar** codigo (mesma regra do v1). Excecao: docs e testes.
- **Segredos nunca entram neste repo.** `config_dir` de cada canal aponta pra fora (ex.: `yt-pub-livesN/config`).
  Nunca copiar `.env`, `credentials.enc`, `.encryption_key` pra ca. Nunca usar Write em `.env` — so Edit.
- **Instancias v1 (`~/projetos/yt-pub-livesN`) sao somente leitura** a partir daqui. O `importar-historico`
  abre o SQLite delas com `mode=ro`. Nao parar, nao editar, nao "consertar" v1 por este projeto.
- **Publicar = job.** Nada chama `lib/youtube.publish` fora de `lib/runner.py`. A CLI `--agora` cria um job e roda.
- **Teto de tamanho:** orquestracao (hub + cli + lib + dashboard) deve ficar abaixo de ~1500 linhas.
  Se uma feature estoura isso, ela vira script em `scripts/` chamado por um job, nao codigo no nucleo.
- **Testes:** `python3 tests/test_jobs.py` tem que passar antes de commitar.
- **Versao:** `lib/__init__.py` VERSION, semver `vX.XX.YY` (regra global: minor carrega o patch, so major zera).
- Commits com autor `inematds <inematds@gmail.com>`.

## Mapa

| arquivo | papel |
|---|---|
| `hub.py` | daemon: agenda jobs pelos horarios do YAML, executa vencidos |
| `cli.py` / `./hub` | publicar direto, fila, status, canais, retry, jobs, tick |
| `lib/db.py` | schema + operacoes atomicas (claim de job/clip) |
| `lib/runner.py` | unico executor de jobs; unico que publica |
| `lib/youtube.py` | publisher stateless (resumable upload, thumbnail, listar lives) |
| `lib/creds.py` | decrypt + refresh token lendo o `config_dir` do canal |
| `lib/entrada.py` | `entrada/<canal>/*.mp4` -> fila |
| `lib/notify.py` | Telegram em processo, apos publicar |
| `dashboard/` | 1 painel, 1 porta (8200), todos os canais |
| `scripts/importar-historico` | v1 -> hub (read-only na origem) |
| `scripts/yt-clip`, `yt-thumbnail`, `yt-publish`, `yt-auth` | copiados do v1 sem alteracao; fase 2 |

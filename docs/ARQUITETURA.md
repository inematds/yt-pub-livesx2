# Arquitetura — yt-pub-livesx2

Uma frase: **um daemon le `canais/*.yaml`, mantem uma fila de clips em `data/hub.db` e publica um clip
por horario em cada canal; as credenciais continuam onde estao.**

```
 produtores (qualquer coisa que gere MP4 + titulo)
 ┌──────────────────────────────────────────────────────────┐
 │ ./hub publicar arq.mp4 --canal lives8 --titulo "..."     │  CLI (direto ou na fila)
 │ entrada/lives8/*.mp4 (+ .json/.jpg ao lado)              │  drop folder (n8n, tktk, cortes, humano)
 │ [fase 2] job cortar_live -> scripts/yt-clip -> entrada/  │
 └──────────────┬───────────────────────────────────────────┘
                ▼
        data/hub.db :: clips (status=fila)
                ▲
                │ agenda: pub:<canal>:<dia HH:MM>   (idempotente)
 canais/*.yaml ─┤
                ▼
        data/hub.db :: jobs (pendente -> rodando -> ok|erro)
                │
        hub.py (1 processo, tick 30s) ── claim atomico ──> lib/runner.run_job
                                                              │
                                        lib/youtube.publish(arquivo, meta, config_dir)
                                                              │  token <- lib/creds (le config_dir/.env + credentials.enc)
                                                              ▼
                                                    YouTube Data API v3 (resumable)
                                                              │
                                   clips.status=publicado ─┬─ lib/notify (Telegram, em processo)
                                                           └─ move entrada/... -> data/publicados/<canal>/
        dashboard/server.py (porta 8200) ── le hub.db ── 1 pagina, todos os canais, botao retry
```

## Decisoes e por que

| decisao | motivo |
|---|---|
| **Credencial fica no `config_dir` do canal**, nunca no repo | e a unica coisa que realmente e por canal. Aponta pro `yt-pub-livesN/config` existente: 17 canais migram sem re-autenticar. Regra global "chaves carregam em runtime, nao se duplicam". |
| **Canal = YAML**, nao linha em tabela `config` | 90 chaves por canal no v1 viraram 10 campos legiveis. Edicao e `vim`, versionavel (fora do git por conter chat_id), sem UI de config. Hub recarrega a cada tick. |
| **Fila de jobs em SQLite** com `chave UNIQUE` | `pub:lives8:2026-09-06 08:10` so existe uma vez: reiniciar o hub nao dobra nem perde publicacao. Substitui 6 dicts `last_executed` + comparacao de string `HH:MM`. |
| **Claim atomico** (`UPDATE ... WHERE status='pendente'` e `rowcount==1`) | dois ticks, ou CLI + daemon, nunca publicam o mesmo clip. Testado (`test_claim_*`). |
| **`release_stuck_jobs`** | processo morto no meio de um upload: job volta a `pendente` e clip a `fila` apos 30 min. Antes, ficava `publicando` pra sempre. |
| **Publisher stateless** (`lib/youtube.py`) | copiado do contrato do inemars: nao ve banco, nao decide horario, levanta `PublishFailure(retriable)`. Transitorio (rede, 429, 5xx) -> volta pra fila ate `max_tentativas`; permanente (400 OAuth, arquivo inexistente) -> `erro` visivel. |
| **Notificacao em processo** | avisa logo apos `INSERT publicado`, com o dado na mao. Elimina o notifier de 918 linhas que lia 17 bancos alheios pra descobrir eventos. |
| **Um painel, uma porta** | so leitura + `retry`. Toda escrita de verdade passa pela CLI ou pelo YAML. O painel do v1 (4.559 linhas de HTML) era editor de config, editor de preset de thumbnail, OAuth e log viewer ao mesmo tempo. |
| **Scripts do v1 copiados sem alterar** (`yt-clip`, `yt-thumbnail`, `yt-publish`, `yt-auth`) | sao o que funciona. Na fase 2 viram tipos de job (`cortar_live`, `thumb`) chamados por subprocess, como hoje — o hub so troca o "quando" e o "estado". |
| **Sem Redis, sem MinIO, sem multi-tenant, sem Docker obrigatorio** | 1 maquina, 1 usuario, 17 canais, ~40 uploads/dia. SQLite em WAL e pasta no disco dao conta com folga. |
| **stdlib + cryptography + PyYAML** | mesmas deps do v1. Zero framework. |

## Banco (`data/hub.db`)

- `canais` — espelho do YAML (nome, destino, config_dir, ativo). Fonte da verdade e o YAML.
- `clips` — a fila. `status`: `fila | publicando | publicado | erro`. `origem`: `cli | entrada | live:<id> | v1`.
  `UNIQUE(canal, video_id)` impede importar/registrar o mesmo video 2x.
- `jobs` — o que rodar e quando. `tipo`: `publicar_proximo | publicar_clip | scan_entrada` (+ fase 2).
- `lives` — lives do canal de origem e status de corte (fase 2 usa; hoje so recebe o historico do v1).

## Ciclo do hub (`hub.tick`)

1. `canais.load_all()` — YAML novo/alterado entra sem restart. `_*.yaml` e ignorado.
2. `agendar()` — pra cada canal ativo e cada `horarios[i]`, `add_job(chave=pub:<canal>:<hoje HH:MM>)`.
   Horario que ja passou ha mais de 1 h nao e criado (subiu tarde ≠ publicar tudo de uma vez).
   `scan_entrada` a cada 5 min por canal (chave por slot de 5 min).
3. `release_stuck_jobs()`.
4. `due_jobs()` → `claim_job()` → `runner.run_job()`; um por vez, em ordem de `run_at`.

`HUB_DRY_RUN=1` faz tudo menos o upload (token e refrescado de verdade; clip volta pra fila).

## Publicar direto

```bash
./hub publicar video.mp4 --canal lives8 --titulo "Titulo" --descricao "..." --tags a,b --agora
./hub publicar video.mp4 --canal lives8 --titulo "Titulo"            # entra na fila, sai no proximo horario
./hub publicar video.mp4 --canal lives8 --titulo "T" --dry-run       # valida creds + token, nao sobe
cp video.mp4 video.json entrada/lives8/                              # mesmo efeito da 2a linha, sem CLI
```

`--agora` nao pula a fila por fora: cria um job `publicar_clip` e o executa na hora pelo mesmo `runner`.
Por isso o daemon e a CLI nunca colidem.

## Migracao v1 → v2, canal a canal (sem big bang)

1. `scripts/importar-historico --todos` — cria `canais/livesN.yaml` (**`ativo: false`**) e traz `publicados`.
   Idempotente; pode rodar de novo a qualquer hora. So le o v1.
2. Subir `yt-hub` + `yt-hub-dashboard`. Com todos inativos, o hub nao publica nada — so mostra.
3. Escolher 1 canal (sugestao: lives8, OAuth OK, 2 horarios). `systemctl --user stop yt-scheduler8`.
   Editar `canais/lives8.yaml`: `ativo: true`. O hub assume os horarios no proximo tick.
   O `yt-dashboard8` pode ficar de pe uns dias so pra consulta; depois `disable`.
4. Repetir por canal. Os 4 canais com OAuth morto (A1 na analise) precisam antes de
   `GWS_CONFIG_DIR=~/projetos/yt-pub-livesN/config scripts/yt-auth` — o hub nao substitui o consentimento.
5. Quando todos migrarem: parar `yt-master-dashboard`; as pastas `yt-pub-livesN/` ficam como
   **cofre de credenciais + lives em disco** (`config_dir`, `lives_dir`). Nao apagar.

## Fase 2 (nao construida; o desenho ja cabe no modelo)

| job | o que faz | de onde vem |
|---|---|---|
| `sync_lives` | `youtube.list_lives(origem_id)` → `lives` novas | ja existe `list_lives` em `lib/youtube.py` |
| `cortar_live` | subprocess `scripts/yt-clip --ai agnes-api -- <video_id>` com `GWS_CONFIG_DIR`/`LIVES_DIR` do canal; le `clips_manifest.json`; `add_clip` por clip com `origem=live:<id>` | `scheduler.run_corte` do v1, sem o dict de horarios |
| `thumb` | subprocess `scripts/yt-thumbnail --title ... --output` antes de publicar; grava `clips.thumb` | `scheduler.handle_thumbnail` (300 linhas de presets → 1 YAML por canal `thumb:` ou nenhum) |
| `scan_tiktok` | `tiktok_scanner.py` escrevendo em `entrada/<canal>/` | vira produtor, nao precisa entrar no hub |
| `alerta_erro` | Telegram pra um `admin_chat_id` global quando `clips.status='erro'` novo ou token falha | o "pendente" do v1 desde julho |

Teto: se o nucleo passar de ~1500 linhas, a feature vira script em `scripts/` chamado por job.

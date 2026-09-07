# Analise — yt-pub-livesx (v1) e os outros publicadores da casa

Data: 2026-09-06. Base: leitura do codigo em `~/projetos`, processos em execucao, bancos SQLite das
17 instancias (somente leitura). Nada foi alterado no v1.

## 1. O que existe hoje

### 1.1 yt-pub-livesx — o pipeline de lives → clips → YouTube

Funciona e publica muito: **12.466 videos** publicados nos 17 canais (contagem de `publicados` com
video_id valido, deduplicado). Mas a arquitetura cresceu por copia:

| medida | valor |
|---|---|
| processos python rodando so pra isso | **35** (17 schedulers + 17 dashboards + 1 master) |
| copias identicas do codigo em disco | 17 pastas + 2 templates (`yt-pub-livesx` e `yt-pub-livesx-master`) |
| linhas no template | 15.942 |
| das quais orquestracao (scheduler + dashboard + master + notifier) | **11.363** (71%) |
| das quais o trabalho de verdade (yt-clip, yt-publish, yt-thumbnail, import_worker, tiktok) | 3.022 |
| tabela `config` por instancia | ~90 chaves (54 sao design de thumbnail) |
| portas TCP abertas | 18 |

O problema nao e o pipeline. `yt-clip` (618 linhas), `yt-publish` (237) e `yt-thumbnail` (903) sao
scripts standalone, testados por 12 mil publicacoes, e sao **reaproveitados sem mudar** na v2.
O problema e o que esta em volta deles.

### 1.2 Como o v1 chegou aqui (e por que cada decisao fazia sentido)

- **"1 canal = 1 instancia"** foi a resposta correta a um fato real: OAuth do YouTube e por canal, cota da
  Data API e por projeto GCP. Mas a conclusao "entao 1 processo + 1 dashboard + 1 banco + 1 copia do codigo
  por canal" nao decorre disso. So a **credencial** precisa ser por canal.
- **Agendamento por `HH:MM` em string** (`pub_horarios=08:10,16:10`) comparado a cada 60 s com um dict
  `last_executed` em memoria. Reiniciou o processo no minuto errado, perdeu ou dobrou a publicacao. Nao ha
  fila de "o que deve rodar e quando"; ha 6 loops de horario (pub, corte, tiktok scan, tiktok pub, import,
  sync) cada um com seu dict.
- **Master le o banco dos outros pelo disco.** `master-dashboard/notifier.py` (918 linhas) abre o
  `lives.db` de cada instancia a cada 60 s pra descobrir o que foi publicado e avisar no Telegram. Isso e
  um segundo sistema de eventos inferido a partir do estado do primeiro.
- **Scheduler chama o proprio dashboard por HTTP** (`/api/sync` em `localhost:809N`) pra sincronizar
  lives. Dois processos, mesma pasta, conversando por rede pra compartilhar uma funcao.
- **`sync-instances` copia arquivos** pra 34 pastas alvo. Cada bug corrigido exige rodar o sync e reiniciar
  34 servicos; e cada instancia "decide se entra", entao ha instancias em versoes diferentes.

### 1.3 Achados concretos (documentados, **nao corrigidos** — v1 esta em producao e o usuario nao estava presente)

| # | achado | evidencia | impacto |
|---|---|---|---|
| A1 | **4 canais com OAuth morto publicando erro a cada horario e ninguem sabe.** lives1, lives2, lives3, lives12: `refresh_token recusado (400)`; ultimo sucesso 04/05-set (lives12: 31-ago). 39 `erro_upload` em 7 dias. | `./hub canais --token`; `journalctl --user -u yt-scheduler1` mostra `HTTP Error 400` as 17:15 de hoje | fila parada; v1 so notifica sucesso (o proprio `docs/notificacoes-telegram.md` lista "admin de erros" como pendente) |
| A2 | lives5 parou em 24-jul, lives32 nunca publicou (52 rows, todas `erro`, sem `canal_destino_*`) e ambas continuam com scheduler + dashboard rodando | `hub status`, tabela `publicados` | 4 processos ocupando porta e CPU pra nada |
| A3 | `sync-instances` tem `SOURCE=yt-pub-livesx-master`, nao o repo git `yt-pub-livesx` | `scripts/sync-instances:4` | dois templates divergindo; o que esta no git nao e o que roda |
| A4 | TARGETS lista 34 pastas; 17 nao existem (`lives1x..11x`, `21`, `25..29`) | `ls ~/projetos/yt-pub-lives*` | ruido, mas mascara esquecimentos (o commit f0539d1 e exatamente "faltava lives12") |
| A5 | Auto-sync deriva porta como `8090+N`, mas lives10/11 rodam em 8400/8401 e lives12/22/23/24/31/32 em 812x/813x | `scheduler.py` bloco Auto-sync vs `ps` | auto-sync dessas instancias bate em porta errada; falha silenciosa apos 3 tentativas |
| A6 | `publicados` tem o mesmo `clip_video_id` repetido: lives10 268 duplicatas em 538 rows, lives2 678 em 2501 | `count(*)-count(distinct)` | contadores do master inflados (varios commits "fix(master): contar pela fila real" sao sintoma disso) |
| A7 | 427 GB de lives em copia unica num HD USB; `move-lives-hd8t.sh` apaga a origem | `FALHAS.md` 2026-08-27 | ja registrado; v2 nao resolve, so nao piora (nao move nada) |
| A8 | Segredos misturados com codigo: `config/.env` dentro da pasta que recebe `cp` do sync; regra "nunca sobrescrever .env" no CLAUDE.md nasceu de um outage no lives4 | `CLAUDE.md` do v1 | risco operacional permanente |

### 1.4 Os outros publicadores em `~/projetos`

| projeto | o que e | estado | licao pra v2 |
|---|---|---|---|
| **inemars** (`imkt4`) | "Hub de publicacao multi-rede, multi-tenant": FastAPI + Redis + MinIO + KMS + Blotato + adapters nativos, roadmap F0–F6 | scaffold; adapters de 100–140 linhas em `DRY_RUN` quando falta credencial | a **ideia** (publisher stateless, `PublishFailure(retriable)`, "provider nunca consulta DB") e boa e foi copiada. A **infra** (Redis, MinIO, multi-tenant) e o motivo de nao ter saido do papel. v2 usa SQLite e stdlib. |
| **imkt5** | plataforma de workers HTTP por capability (`yt-publish`, `yt-source-ingest`) com gateway conversacional | workers existem, mesma dependencia de fila externa | mesmo padrao: generalizou antes de publicar |
| **timesmkt / imkt4 `publish_now.js`** | 456 linhas JS: `publishInstagram`, `publishThreads`, `publishYouTube` lendo `CREDENTIALS` de JSON | funciona por lote, sem fila, sem retry | e o "publicar direto" mais simples da casa. v2 tem o equivalente em `./hub publicar --agora`, com fila e retry por cima. |
| **pubmetricool** | publicar Reels/TikTok via MCP da Metricool a partir do `publicados` do livesx | testado ponta a ponta 1 vez (19-ago); doc propria conclui: **daemon nao fala MCP** e **volume nao cabe no plano Free** | Metricool nao e backend de daemon. YouTube fica nativo. TikTok/Instagram entram como publisher separado quando houver API/plano — a interface ja existe (`lib/youtube.py` e o modelo). |
| **tktk-sync / tktk** | copia do livesx (scheduler 300 + dashboard 597) que scaneia TikTok, baixa e sobe no YouTube | funcional, hoje absorvido como `tiktok_scanner.py` dentro do livesx | e um **produtor de fila** (poe MP4 + metadados). Em v2 e so escrever em `entrada/<canal>/`. |
| **yt-pub-lives-thumb** | gerador de thumbnail standalone com n8n (`workflow_yt_thumb.json`) | virou `scripts/yt-thumbnail` | nada a fazer |

Padrao: **cada tentativa de "sistema geral" (inemars, imkt5) travou na infraestrutura; cada coisa que
publica de verdade (livesx, publish_now.js) e um script simples com credencial em arquivo.** A v2 fica do
lado dos scripts simples e resolve so o que faltava: fila com estado, 1 processo, 1 painel, aviso de erro.

## 2. Alternativas consideradas (o que a v2 NAO e)

| alternativa | por que nao |
|---|---|
| Refatorar o v1 no lugar (tirar loops, unir dashboards) | 17 copias em producao; cada mudanca = sync + 34 restarts + risco de `.env`. Precisa de um lugar novo pra migrar canal a canal. |
| Terminar o inemars (Redis, MinIO, multi-tenant) | so ha um tenant (INEMA). Redis/MinIO sao 2 servicos a mais pra um problema que SQLite + pasta resolvem. |
| Metricool/Blotato/Zernio como publicador unico | custo por post, sem chamada de daemon (MCP), cota mensal < volume (12k videos). YouTube nativo e gratis via OAuth. |
| n8n / Make orquestrando | ja existe `docs/import-worker-spec.md` mostrando o n8n como **produtor** (poe manifest na pasta). Isso continua valendo: n8n escreve em `entrada/`, hub publica. Orquestrar o upload de 2 GB no n8n e pior. |
| Um scheduler por canal, mas compartilhando codigo (symlink) | resolve so o sync; continua 35 processos, 18 portas, N bancos, notifier lendo banco alheio. |
| **1 daemon, 1 banco, N YAML, fila de jobs** (escolhida) | credencial continua por canal (unica coisa que precisa); tudo o mais e compartilhado. 965 linhas de orquestracao vs 11.363. |

## 3. Numeros da v2 (medidos, nao estimados)

| | v1 | v2 |
|---|---|---|
| processos | 35 | 2 (hub + painel) — ou 1, sem painel |
| portas | 18 | 1 |
| bancos | 17 | 1 |
| copias de codigo | 19 | 1 |
| linhas de orquestracao | 11.363 | **965** (hub, cli, lib, dashboard/server) |
| linhas total do nucleo com pagina do painel e importador | — | 1.116 |
| testes automatizados | 0 | 15 (fila, claim atomico, idempotencia, restart no meio, entrada, YAML) |
| deteccao de OAuth morto | nenhuma (so avisa sucesso) | `./hub canais --token` (1 comando, 17 canais) + falha vai pra `clips.erro` visivel no painel |
| publicar um MP4 avulso | nao existia (so via `imports/` + manifest + esperar horario) | `./hub publicar video.mp4 --canal X --titulo T --agora` |

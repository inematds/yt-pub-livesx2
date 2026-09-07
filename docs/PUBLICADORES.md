# Publicadores diretos — Blotato e similares (pesquisa 2026-09-07)

Pergunta: o que existe, fora do YouTube nativo, pra publicar direto (API chamada por daemon/CLI) em
TikTok, Instagram, YouTube e afins, e o que disso serve pro `yt-pub-livesx2`.

Fontes: paginas publicas de preco/docs lidas hoje + o que ja estava em `~/projetos` (pubmetricool, inemars, N8Np).
Nada foi testado com dinheiro nesta rodada. Onde e declaracao do fornecedor, esta marcado.

## 1. O que ja tem em `~/projetos`

| projeto | relacao com publicar direto |
|---|---|
| `pubmetricool` | unico testado de verdade (19-ago): Reel + TikTok agendados via MCP da Metricool. Conclusao propria: daemon nao fala MCP, API REST so no plano Advanced, Free = 20 posts/mes. Tem `docs/comparativo-blotato.md` e `comparativo-zernio.md` (leitura de precos, nao teste). |
| `inemars/publishers/blotato.py` | adapter de 140 linhas pro `POST /v2/posts` da Blotato com `mediaUrls` publicas; nunca rodou fora de DRY_RUN. O **contrato** (stateless, `PublishFailure(retriable)`) e o que o `lib/youtube.py` da v2 copiou. |
| `N8Np` | biblioteca de workflows n8n de terceiros; 4.571 mencoes a Blotato, 889 a Postiz, 485 a upload-post. E o que a comunidade n8n usa; nao e codigo nosso. |
| `redessociais` / `redessociais2026` | "Publisher em TypeScript + Supabase + APIs diretas", 67 mencoes a Postiz nas docs. Nao publica nada hoje. |
| `timesmkt*/pipeline/publish_now.js` | publica direto na Graph API (Instagram/Threads) e YouTube com credenciais em JSON. Sem fila. E o precedente interno de "publicar direto sem intermediario". |

## 2. Os candidatos

### Blotato — https://www.blotato.com

- **API**: `https://backend.blotato.com/v2/...`, header `blotato-api-key`. `GET /v2/users/me/accounts` pra descobrir `accountId`; `POST /v2/posts` com `content.text`, `mediaUrls[]`, `target.targetType` e `scheduledTime` opcional.
- **Midia**: URL publica em `mediaUrls` **ou** *presigned upload* de arquivo local (`/v2/media`) — resolve o problema do "host publico" que o pubmetricool teve com o repo `inematds/midia`.
- **Plataformas**: Instagram, Facebook, LinkedIn, X, TikTok, YouTube, Threads, Pinterest, Bluesky.
- **Preco (declarado)**: Starter **US$ 29/mes**, 20 contas sociais, ate 900 posts TikTok/mes, API incluida em todo plano pago. Agency US$ 499. **API nao entra no trial**: gerar a chave encerra o trial e cobra o Starter.
- **Extras**: node oficial n8n/Make, MCP server (`mcp.blotato.com`, ~16 tools), geracao de video/imagem por creditos, DM automation.
- **Contra**: pra 17 canais YouTube + TikTok/Instagram passa de 20 contas; proximo plano (Creator) nao captei o preco. Reviews citam suporte lento e cobranca.

### Upload-Post — https://www.upload-post.com

- **API**: `POST /api/upload`, header `Authorization: Apikey ...`, **multipart com o arquivo local** (`video` = File **ou** URL), `platform[]`, `title`, `description`, `scheduled_date` ISO-8601, `first_comment`, `Idempotency-Key` (retry seguro, devolve o job existente). Parametros por plataforma (`youtube_title`, `tiktok_*` com `privacy_level`, capabilities por conta via `GET /api/uploadposts/users`).
- **Plataformas**: TikTok, Instagram, YouTube, LinkedIn, Facebook, X, Threads, Pinterest, Bluesky, Reddit, Discord, Telegram, Google Business, Mastodon, WordPress + outras (declara 22).
- **Preco (declarado)**: **Free 10 uploads/mes** (serve pra testar de verdade), Basic **US$ 16/mes** anual = 5 perfis, Professional **US$ 33/mes** anual = 25 perfis, **uploads ilimitados** nos pagos. "Perfil" = 1 conta por plataforma; 17 canais YouTube = 17 perfis → Professional.
- **Extras**: SDK Python oficial (`upload-post-pip`), fila com slots (`add_to_queue`), analytics, comentarios.
- **Contra**: empresa menor que Blotato; limite de tamanho de arquivo nao esta na pagina do endpoint (checar no teste).

### Postiz — https://github.com/gitroomhq/postiz-app (open source, AGPL)

- **API**: `https://api.postiz.com/public/v1/...` (ou o seu host), header `Authorization: <api-key>`, endpoints de integrations/posts/upload. Rate limit **90 req/h so no create post** (self-host ajusta via `API_LIMIT`). MCP server, CLI, node n8n.
- **Preco**: self-hosted **gratis**, paridade total com a cloud. 33 plataformas.
- **O detalhe que decide**: no self-host **voce cria os apps OAuth** em cada plataforma. Pro YouTube isso significa **um projeto GCP com cota de 10.000 unidades/dia** (upload = 1.600 unidades ≈ 6 uploads/dia pra todos os canais juntos). E exatamente o motivo pelo qual o v1 tem 17 projetos GCP. TikTok e Meta exigem app review proprio. Ou seja: Postiz self-host nao elimina a dor de credencial, so a muda de lugar.
- **Serve pra**: quem quer painel/calendario multi-rede sem pagar SaaS, com poucos canais.

### Mixpost — https://mixpost.app (open source, MIT)

- Mais estavel e convencional que o Postiz, mas a versao Lite so cobre Facebook, X e Mastodon; Pro e pago. Ultimo commit ha 5 meses (set/2026). Mesmo problema de OAuth proprio. **Nao e candidato.**

### Zernio (ex-Late / getlate.dev) — https://zernio.com

- `getlate.dev/pricing` hoje redireciona pra Zernio: a "Late" que aparece nas docs antigas (jcc, ruflo, N8Np) **e a Zernio**.
- **API-first**: REST `zernio.com/api/v1`, Bearer, SDKs, OpenAPI, `llms.txt`, **webhooks** de status (sem polling), `dryRun`. Upload proprio com presigned URL ate 5 GB. MCP hospedado + plugin oficial pro Claude Code.
- **Preco (declarado, rate card)**: por conta conectada/mes: 2 gratis, contas 3–10 **US$ 6**, 11–100 **US$ 3**, 101+ US$ 1. Tudo incluido, sem plano. Exemplo INEMA: 17 YouTube + 17 TikTok = 34 contas → 8×6 + 24×3 = **US$ 120/mes**. So TikTok/Instagram (17+17) idem. So 10 contas = US$ 48.
- **Contra**: caro por conta quando sao muitas; X cobrado por request.

### Ayrshare — https://www.ayrshare.com

- Referencia de mercado pra SaaS, 13 redes. **Sem plano gratis** em 2026; Premium US$ 149/mes = 1 perfil, Launch US$ 299 = 10, Business US$ 599 = 30. Cobra por perfil, nao por volume. **Fora de escala pro INEMA.**

### Metricool — ja avaliado em `pubmetricool`

- MCP funciona no Free (20 posts/mes), API REST so no Advanced. Otimo analytics, ruim como backend de daemon. Mantido como ferramenta de medicao, nao de publicacao.

## 3. Lado a lado (pro caso: 17 canais YouTube, ~40 uploads/dia, + TikTok/Instagram eventual)

| | Blotato | Upload-Post | Postiz self-host | Zernio | Ayrshare |
|---|---|---|---|---|---|
| chamada por daemon (REST) | sim | sim | sim | sim | sim |
| aceita arquivo local | presigned | **multipart direto** | sim | presigned 5 GB | URL |
| testar sem pagar | nao (API fora do trial) | **sim, 10/mes** | sim | sim, 2 contas | nao |
| custo p/ 17 YT + 17 TikTok | > Starter (20 contas) | **US$ 33/mes** (25 perfis) | 0 + seu OAuth/cota | US$ 120/mes | ≥ US$ 599 |
| limite de posts | 900 TikTok/mes no Starter | ilimitado | cota do seu app Google | ilimitado | ilimitado |
| idempotencia / retry seguro | id do post | **header Idempotency-Key** | — | webhooks | — |
| MCP / n8n | ambos, oficiais | n8n, MCP via SDK | ambos | MCP + plugin Claude Code | n8n |
| dor de credencial | zero | zero | **toda** (app review, cota GCP) | zero | zero |

## 4. O que isso muda (ou nao) na v2

1. **YouTube continua nativo.** Os 17 projetos GCP com OAuth ja aprovado sao um ativo: cota de 10k/dia **por canal**, custo zero, `lib/youtube.py` ja faz o upload. Nenhum agregador melhora isso; o Postiz self-host piora (1 cota pra todos).
2. **TikTok/Instagram entram por agregador, nao por API propria.** Meta exige App Review, TikTok exige audit + sandbox. E o que matou o `tktk` e o `redessociais`. Pagar US$ 16–33/mes e mais barato que uma semana de review.
3. **Candidato nº 1: Upload-Post.** Unico com free tier real pra API, arquivo local por multipart, uploads ilimitados, chave de idempotencia e preco que cabe nos 17+ perfis. **Nº 2: Blotato**, se o peso for MCP/n8n e geracao de conteudo no mesmo lugar. **Zernio** so se webhooks e messaging (DM/WhatsApp) virarem requisito.
4. **Encaixe no hub**: um `lib/uploadpost.py` com a mesma assinatura de `lib/youtube.publish(arquivo, titulo, descricao, config_dir, ...)`, e um campo no YAML do canal:
   ```yaml
   destinos:
     - youtube            # nativo, config_dir
     - uploadpost:tiktok  # api key em <raiz>/.env UPLOADPOST_API_KEY, user = nome do canal
   ```
   O `runner` publica em cada destino como um job separado; `clips.video_id` vira `clips.publicacoes` (1 linha por destino). Isso e fase 2 e nao muda o nucleo atual.
5. **Teste barato antes de decidir**: conta Free do Upload-Post, conectar 1 TikTok, `curl -F video=@clip.mp4 -F 'platform[]=tiktok' -F user=lives8 -F title=...`. Dez uploads gratis dao pra medir taxa de falha e tempo. Se passar, Professional anual.

## Fontes

- Blotato: https://help.blotato.com/api/start · https://www.blotato.com/pricing · https://postscheduler.com/tools/blotato · https://thatmarketingbuddy.com/mcp/blotato
- Upload-Post: https://docs.upload-post.com/api/upload-video/ · https://www.upload-post.com/pricing · https://github.com/Upload-Post/upload-post-pip
- Postiz: https://github.com/gitroomhq/postiz-app · https://docs.postiz.com/public-api · https://postiz.com/compare/postiz/mixpost
- Mixpost x Postiz: https://openalternative.co/compare/mixpost/vs/postiz · https://getopensaas.com/postiz-vs-mixpost-the-ultimate-guide-to-self-hosted-social-media-management-in-2026/
- Zernio / Late: https://zernio.com/pricing (getlate.dev/pricing redireciona) · https://zernio.com/blog/ayrshare-alternative
- Ayrshare: https://www.blotato.com/blog/ayrshare-pricing · https://social-api.ai/blog/ayrshare-pricing-social-media-api-2026
- Panorama: https://www.socialchamp.com/blog/best-social-media-apis/ · https://www.upload-post.com/best-social-media-apis/ · https://buffer.com/resources/social-media-api-multi-platform-posting/
- Open source avulsos (TikTok por sessao/Selenium, fragil): https://github.com/makiisthenes/TiktokAutoUploader · https://github.com/JohnKearney1/TikTokFarm · https://github.com/tsensei/OpenReels

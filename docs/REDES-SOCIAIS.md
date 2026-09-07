# TikTok e Instagram — como ligar (Upload-Post)

O hub publica no YouTube nativamente, com o OAuth de cada canal. Para TikTok, Instagram e outras
redes ele usa o **Upload-Post** como intermediario, porque publicar direto exigiria App Review da
Meta e audit do TikTok, um por canal. A comparacao que levou a essa escolha esta em
[`PUBLICADORES.md`](PUBLICADORES.md).

**O codigo esta pronto e testado.** Falta so o que ninguem pode fazer por voce: assinar e autorizar
as contas.

## O modelo

| conceito no hub | no Upload-Post |
|---|---|
| canal (`canais/lives8.yaml`) | **perfil** (`uploadpost_perfil`, default = nome do canal) |
| destino `uploadpost:tiktok` | conta TikTok conectada naquele perfil |
| destino `uploadpost:instagram` | conta Instagram conectada naquele perfil |

Um perfil aceita **uma conta por rede**. Entao 17 canais = 17 perfis, cada um com seu TikTok e seu
Instagram. O plano **Professional** (25 perfis, uploads ilimitados) cobre isso; o Basic (5 perfis)
nao. O plano Free tem 2 perfis e **nao publica no TikTok**.

Segredos: a API key fica em `~/projetos/wifi/.env` como `UPLOADPOST_API_KEY`, lida em runtime.
Nenhuma senha de rede social passa pelo hub — a autorizacao e OAuth na propria rede.

## Passo a passo

### 1. Assinar e conferir se cabe

```bash
scripts/setup-social --plano
# plano=professional  perfis usados=2  limite=25
# canais no hub=17  perfis a criar=16
# Cabe no plano atual.
```

### 2. Criar os perfis e declarar os destinos (automatico)

```bash
scripts/setup-social --preparar                          # todos os canais
scripts/setup-social --preparar --so lives2              # so um, pra testar primeiro
scripts/setup-social --preparar --horarios "10:00,19:00" # horario proprio das redes
```

Cria o perfil de cada canal e acrescenta `uploadpost:tiktok` e `uploadpost:instagram` no
`destinos:` do YAML. Nao mexe no que ja estava la, e pode rodar de novo sem duplicar.

### 3. Autorizar as contas — **so voce faz**

```bash
scripts/setup-social --links            # 1 link por canal
scripts/setup-social --links --so lives2
./hub social-link lives2                # o mesmo, por canal
```

Abra cada link num navegador logado nas contas **daquele** canal e autorize. Validade ~48 h.
Requisitos das redes:

- **Instagram**: conta profissional ou creator, ligada a uma pagina do Facebook. Conta pessoal a Meta recusa.
- **TikTok**: os primeiros posts de um app podem cair em revisao manual do lado deles.
- Uma conta social so pode estar ligada a **um** Upload-Post por vez. Se ja estiver em outra ferramenta, desconecte la antes.

### 4. Conferir e testar

```bash
scripts/setup-social --checar     # quais canais ja tem cada rede
./hub social                      # perfis e contas, direto da API
./hub canais --token              # OAuth do YouTube + contas sociais, canal a canal

./hub publicar clip.mp4 --canal lives2 --titulo "Teste" --destinos uploadpost:instagram --dry-run
./hub publicar clip.mp4 --canal lives2 --titulo "Teste" --destinos uploadpost:instagram --agora
```

Faca **um canal primeiro**. Se a legenda, o corte ou o formato sairem errados, corrige uma vez em
vez de dezessete.

### 5. Deixar no automatico

Com o destino no YAML e a conta conectada, o hub publica sozinho nos horarios. Nada mais a fazer.

## Como o hub trata varios destinos

- **Uma linha de fila por destino.** O mesmo MP4 vira N linhas em `clips`, com o mesmo `grupo`.
  Cada uma tem status, tentativas e erro proprios: falha no TikTok nao impede o YouTube.
- **Horario por destino.** `destinos:` aceita `horarios` proprios; sem isso, herda os do canal.
- **Guarda de formato antes de gastar upload.** Duracao acima do limite da rede vira `pulado`,
  nao erro, e nao consome cota. Video horizontal em Reels/TikTok gera aviso, nao bloqueio.
  Os limites estao em `LIMITES`, em `lib/uploadpost.py`, e sao conservadores de proposito:
  **a rede continua sendo a autoridade final** e os numeros devem ser confirmados no primeiro
  teste real de cada plataforma.
- **Idempotencia.** Cada upload leva um `Idempotency-Key` proprio, entao um retry depois de
  timeout nao publica duas vezes.
- **Arquivo so sai da pasta `entrada/` quando todos os destinos terminaram.**
- **Telegram por rede**, com o emoji da plataforma no aviso.
- **`hub status --destinos`** e o painel mostram fila, publicados, erros e pulados por canal e rede.

## O que ainda nao existe

- Legenda diferente por rede (hoje titulo e descricao sao os mesmos em todos os destinos).
  O campo por plataforma existe na API (`tiktok_title`, `instagram_title`); entra quando houver
  uso real que justifique.
- Corte automatico para 9:16 antes de mandar para Reels/TikTok. Hoje o hub avisa e publica.
- Leitura do status assincrono: quando a API responde em background, o hub grava `pending:<id>`
  e nao volta para confirmar. `lib/uploadpost.status()` ja consulta; falta o job que reconcilia.

# yt-pub-livesx2

**🇧🇷 [Português](README.md) · 🇺🇸 [English](README.en.md) · 🇪🇸 [Español](README.es.md)**

Publicador de videos en YouTube para N canales: **1 daemon, 1 base de datos, 1 panel, N archivos YAML**.
Versión 2 de [yt-pub-livesx](../yt-pub-livesx) — el mismo trabajo (clips de transmisiones en vivo, importaciones, TikTok → YouTube),
sin los 35 procesos, 18 puertos y 17 copias del código.

- **Por qué rehacerlo:** [`docs/ANALISE.md`](docs/ANALISE.md) — cifras de la v1, hallazgos, los otros publicadores de la casa, alternativas descartadas.
- **Cómo funciona:** [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) — cola de jobs, credenciales, migración canal por canal, fase 2.
- **Publicar fuera de YouTube:** [`docs/PUBLICADORES.md`](docs/PUBLICADORES.md) — Blotato, Upload-Post, Postiz, Zernio, Ayrshare lado a lado.
- **Conectar TikTok e Instagram:** [`docs/REDES-SOCIAIS.md`](docs/REDES-SOCIAIS.md) — paso a paso de los destinos mediante Upload-Post.

## 📖 Guía de uso

Guía completa (landing + paso a paso): **https://inematds.github.io/yt-pub-livesx2/guia/es/**

## En 60 segundos

```bash
cd ~/projetos/yt-pub-livesx2
pip install -r requirements.txt            # cryptography + PyYAML (ya instalados en esta máquina)

scripts/importar-historico --todos          # lee las 17 instancias v1 (solo lectura) -> canais/*.yaml + historico
./hub status                                # 17 canales, 12.466 publicados
./hub canais --token                        # qué canales tienen OAuth válido (hoy: 12 OK, 5 muertos)

./hub publicar video.mp4 --canal lives8 --titulo "Titulo" --dry-run   # valida todo, no sube
./hub publicar video.mp4 --canal lives8 --titulo "Titulo" --agora     # sube ahora
./hub publicar video.mp4 --canal lives8 --titulo "Titulo"             # cola; sale en el próximo horario del YAML
./hub publicar video.mp4 --canal lives8 --titulo "T" --destinos uploadpost:instagram   # solo una red
./hub status --destinos                     # cola y publicados por canal Y red
scripts/setup-social --plano                # TikTok/Instagram: ver docs/REDES-SOCIAIS.md
cp video.mp4 video.json entrada/lives8/                               # igual, sin CLI (n8n, scripts, persona)

python3 hub.py                              # daemon (o systemd/yt-hub.service)
python3 dashboard/server.py 8200            # panel único
```

## Estructura

```
hub.py                 daemon: programa según los horarios del YAML, ejecuta jobs vencidos (tick 30 s)
cli.py / ./hub         publicar | fila | status | canais [--token] | retry | jobs | tick
lib/
  db.py                schema + asignación atómica de job/clip
  runner.py            único ejecutor; único que publica
  youtube.py           publisher sin estado: carga reanudable, thumbnail, listar transmisiones en vivo
  creds.py             .env + credentials.enc del config_dir del canal (formato v1, no se copia nada)
  uploadpost.py        publisher de TikTok/Instagram y compañía (API de Upload-Post, archivo local)
  entrada.py           entrada/<canal>/*.mp4 (+ .json/.jpg) -> cola de cada destino
  notify.py            Telegram en proceso después de publicar
  canais.py            canais/*.yaml
dashboard/             1 página, 1 puerto, todos los canales, botón retry
scripts/
  importar-historico   v1 -> hub (origen en modo de solo lectura, idempotente)
  setup-social         crea perfiles, declara destinos y genera los enlaces de autorización de las redes
  yt-clip yt-thumbnail yt-publish yt-auth    copiados de v1 sin cambios (fase 2)
canais/_exemplo.yaml   modelo de canal
systemd/               yt-hub.service, yt-hub-dashboard.service (2 servicios en total)
tests/test_jobs.py     15 pruebas sin red
```

## Un canal

```yaml
# canais/lives8.yaml — generado por importar-historico; edítalo a gusto, el hub se recarga solo
destino_id: UCJxL11aomXJiBZga-JXylgQ
destino_nome: Inema Vibe
config_dir: /home/nmaldaner/projetos/yt-pub-lives8/config   # .env + credentials.enc quedan AQUÍ
horarios: ["08:10", "16:10"]                                 # 1 clip de la cola por horario
privacy: public
telegram_chat_ids: ["7852460115"]
ativo: false                                                 # true cuando detengas yt-scheduler8
destinos:                                                    # opcional; sin esto, solo YouTube
  - youtube
  - destino: uploadpost:instagram
    horarios: ["19:00"]
```

Secreto global único: `TELEGRAM_NOTIFY_BOT_TOKEN` (y contraseña opcional del panel) en `<raiz>/.env` — ver `.env.example`.

## Estado (2026-09-06)

- Núcleo funcional y probado: cola, jobs idempotentes, reinicio a mitad del proceso, entrada, publisher con dry-run real
  (descifra, renueva el token, prepara los metadatos, se detiene antes de la carga). No se hizo ninguna carga real.
- 17 canales importados, todos `ativo: false`: la v1 sigue publicando hasta que migres canal por canal
  (guía en `docs/ARQUITETURA.md`).
- Multidestino listo y probado (28 pruebas): TikTok e Instagram mediante Upload-Post, cola y horario
  por red. Falta contratar el plan y autorizar las cuentas — ver `docs/REDES-SOCIAIS.md`.
- Fase 2 restante (recorte de transmisiones en vivo, thumbnail, escaneo de TikTok, alerta de error) diseñada, no construida.

Versión: `v2.0.0` (`lib/__init__.py`). Semver `vX.XX.YY`: minor conserva el patch; solo major lo pone en cero.

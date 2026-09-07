"""Aviso no Telegram, em processo, logo apos publicar. Sem loop, sem ler banco alheio.

Token: TELEGRAM_NOTIFY_BOT_TOKEN em <raiz>/.env (ou no ambiente). Destinatarios: canal.telegram_chat_ids.
Falha de aviso nunca derruba a publicacao — so loga.
"""
import json
import os
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bot_token():
    tok = os.environ.get('TELEGRAM_NOTIFY_BOT_TOKEN', '')
    if tok:
        return tok
    path = os.path.join(ROOT, '.env')
    if os.path.isfile(path):
        for line in open(path):
            if line.startswith('TELEGRAM_NOTIFY_BOT_TOKEN='):
                return line.split('=', 1)[1].strip().strip('"')
    return ''


def send(chat_ids, texto, log=print):
    tok = bot_token()
    if not tok or not chat_ids:
        return 0
    ok = 0
    for cid in chat_ids:
        data = urllib.parse.urlencode({'chat_id': cid, 'text': texto, 'parse_mode': 'HTML',
                                       'disable_web_page_preview': 'false'}).encode()
        try:
            urllib.request.urlopen(f'https://api.telegram.org/bot{tok}/sendMessage', data=data, timeout=20)
            ok += 1
        except Exception as e:
            log(f'    telegram {cid}: {e}')
    return ok


def publicado(canal, clip, log=print):
    texto = (f"✅ <b>{canal.get('destino_nome') or canal['nome']}</b>\n"
             f"{clip['titulo']}\n{clip['url']}")
    return send(canal.get('telegram_chat_ids', []), texto, log)

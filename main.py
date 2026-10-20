import os
import re
import time
import requests
from html import escape
from dotenv import load_dotenv

load_dotenv()

# Токен берем из переменных окружения (в BotHost укажи BOT_TOKEN или SPB_HELPER_TOKEN)
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("SPB_HELPER_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("[-] ОШИБКА: Не задана переменная окружения BOT_TOKEN!")

BOT_TOKEN = BOT_TOKEN.strip()
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Фирменная подпись Дозора со ссылкой на канал СПб
FOOTER_HTML = (
    "\n\n📡 Дозор.ру | Питер и Ленинградская область - "
    "<a href='https://t.me/RadarLO_SPB'>Подписаться</a>"
)

# Шаблон чужих ссылок и хвостов, которые срезаются с конца сообщения
FOREIGN_FOOTER_LINE_RE = re.compile(
    r"@\w+|"
    r"https?://\S+|t\.me/\S+|max\.ru/\S+|clck\.ru/\S+|"
    r"наш\s+канал|канал\s+в\s+мах|подписаться|подписывайтесь|"
    r"радар\s+питер|мониторинг\s+питер|радар\.ру|локатор\s+россии|"
    r"предложить\s+новость|прислать\s+информацию|обратная\s+связь|"
    r"^[❗️❕📲📡👉🔗📢❤️🙌]+\s*",
    re.IGNORECASE
)

def replace_footer(raw_text: str) -> str:
    if not raw_text:
        return ""

    lines = raw_text.strip().splitlines()

    # Срезаем чужую подпись и пустые строки снизу вверх
    while lines:
        last_line = lines[-1].strip()
        if not last_line or FOREIGN_FOOTER_LINE_RE.search(last_line):
            lines.pop()
        else:
            break

    clean_body = "\n".join(lines).strip()
    if not clean_body:
        return ""

    return f"{escape(clean_body)}{FOOTER_HTML}"

def process_update(update: dict):
    msg = update.get("message")
    if not msg:
        return

    chat_id = msg["chat"]["id"]
    raw_text = msg.get("text") or msg.get("caption") or ""

    if raw_text.strip() == "/start":
        requests.post(
            f"{API_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "✅ Бот-репостер Дозор.ру (СПб) активен!\n\nКидай или пересылай сюда любой пост — я сразу обрежу чужие ссылки и выдам готовый текст с нашей подписью."
            },
            timeout=10
        )
        return

    final_text = replace_footer(raw_text)
    if not final_text:
        return

    # Если пост был с фото
    if "photo" in msg:
        file_id = msg["photo"][-1]["file_id"]
        requests.post(
            f"{API_URL}/sendPhoto",
            json={
                "chat_id": chat_id,
                "photo": file_id,
                "caption": final_text,
                "parse_mode": "HTML"
            },
            timeout=15
        )
    # Если пост был с видео
    elif "video" in msg:
        file_id = msg["video"]["file_id"]
        requests.post(
            f"{API_URL}/sendVideo",
            json={
                "chat_id": chat_id,
                "video": file_id,
                "caption": final_text,
                "parse_mode": "HTML"
            },
            timeout=15
        )
    # Обычный текст
    else:
        requests.post(
            f"{API_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": final_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=15
        )

def main():
    print("==================================================")
    print("✅ Дозор СПб Репостер успешно запущен!")
    print("==================================================")
    
    offset = 0
    while True:
        try:
            resp = requests.get(
                f"{API_URL}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35
            )
            if resp.status_code == 200:
                updates = resp.json().get("result", [])
                for upd in updates:
                    offset = upd["update_id"] + 1
                    process_update(upd)
            else:
                time.sleep(3)
        except Exception:
            time.sleep(3)

if __name__ == "__main__":
    main()

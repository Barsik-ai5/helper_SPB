import os
import re
import time
import requests
from html import escape
from dotenv import load_dotenv

load_dotenv()

# Токен берем из переменных окружения BotHost (BOT_TOKEN или SPB_HELPER_TOKEN)
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

# Шаблон чужих подписей, рекламных строк и ссылок в конце поста
FOREIGN_FOOTER_LINE_RE = re.compile(
    r"@\w+|"
    r"https?://\S+|t\.me/\S+|max\.ru/\S+|clck\.ru/\S+|"
    r"курс\s+на\s+макс|канал\s+в\s+мах|канал\s+в\s+макс|мы\s+в\s+мах|мы\s+в\s+макс|"
    r"наш\s+канал|резервн\w+\s+канал|подписаться|подписывайтесь|подпишись|"
    r"радар\s+питер|мониторинг\s+питер|радар\.ру|локатор\s+россии|дозор\.ру|"
    r"предложить\s+новость|прислать\s+информацию|обратная\s+связь|наш\s+бот|"
    r"^[❗️❕📲📡👉🔗📢❤️🙌🔹🔸▪️▫️📌➡️⬇️]+\s*",
    re.IGNORECASE
)

# Чужие эмодзи в самом начале текста, которые нужно убрать перед постановкой кружка
LEADING_SYMBOLS_RE = re.compile(
    r"^(?:[⚡️⚡❗️❕🔴🟡🟢⚪️⚪⚠️🚨💥🔥📢📌🔹🔸]+\s*)+"
)

# Память для хранения ожидающих выбора кружка сообщений: {prompt_msg_id: dict}
PENDING_POSTS = {}

CIRCLE_MAP = {
    "circle_red": "🔴",
    "circle_yellow": "🟡",
    "circle_green": "🟢",
    "circle_white": "⚪",
    "circle_none": ""
}

def _get_link_char_ranges(raw_text: str, entities: list) -> list:
    """Находит позиции всех скрытых ссылок (text_link, url, mention) в тексте."""
    if not raw_text or not entities:
        return []
    utf16_bytes = raw_text.encode("utf-16-le")
    ranges = []
    for ent in entities:
        if ent.get("type") in ("text_link", "url", "mention", "text_mention"):
            off = ent.get("offset", 0)
            length = ent.get("length", 0)
            start_char = len(utf16_bytes[:off * 2].decode("utf-16-le", errors="ignore"))
            end_char = len(utf16_bytes[:(off + length) * 2].decode("utf-16-le", errors="ignore"))
            ranges.append((start_char, end_char))
    return ranges

def clean_body_text(raw_text: str, entities: list = None) -> str:
    """Отрезает чужие подписи и скрытые ссылки снизу сообщения."""
    if not raw_text:
        return ""

    link_ranges = _get_link_char_ranges(raw_text, entities or [])
    lines = raw_text.splitlines()

    # Вычисляем символьные границы каждой строки для проверки скрытых ссылок
    line_bounds = []
    pos = 0
    for line in lines:
        start = pos
        end = pos + len(line)
        line_bounds.append((start, end))
        pos = end + 1  # +1 на символ переноса \n

    # Удаляем пустые строки и строки с чужими подписями/ссылками с конца
    while lines:
        last_line = lines[-1].strip()
        start_idx, end_idx = line_bounds[-1]
        has_entity_link = any(
            not (e_end <= start_idx or e_start >= end_idx)
            for e_start, e_end in link_ranges
        )

        if not last_line or FOREIGN_FOOTER_LINE_RE.search(last_line) or (has_entity_link and len(last_line) < 90):
            lines.pop()
            line_bounds.pop()
        else:
            break

    return "\n".join(lines).strip()

def build_final_post(clean_body: str, circle: str) -> str:
    if circle:
        # Убираем чужие молнии/восклицательные знаки/кружки в самом начале, чтобы не было дублей
        stripped_body = LEADING_SYMBOLS_RE.sub("", clean_body).strip()
        body_with_circle = f"{circle} {stripped_body}"
    else:
        body_with_circle = clean_body

    return f"{escape(body_with_circle)}{FOOTER_HTML}"

def send_circle_selector(chat_id: int, post_data: dict):
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🔴", "callback_data": "circle_red"},
                {"text": "🟡", "callback_data": "circle_yellow"},
                {"text": "🟢", "callback_data": "circle_green"},
                {"text": "⚪", "callback_data": "circle_white"}
            ],
            [
                {"text": "Без кружка", "callback_data": "circle_none"}
            ]
        ]
    }
    resp = requests.post(
        f"{API_URL}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": "Выбери кружок для начала сообщения:",
            "reply_markup": keyboard
        },
        timeout=10
    )
    if resp.status_code == 200:
        prompt_msg_id = resp.json().get("result", {}).get("message_id")
        if prompt_msg_id:
            post_data["ts"] = time.time()
            PENDING_POSTS[f"{chat_id}_{prompt_msg_id}"] = post_data

            # Очистка старых записей из памяти
            if len(PENDING_POSTS) > 200:
                cutoff = time.time() - 3600
                old_keys = [k for k, v in PENDING_POSTS.items() if v.get("ts", 0) < cutoff]
                for k in (old_keys or list(PENDING_POSTS.keys())[:50]):
                    PENDING_POSTS.pop(k, None)

def handle_message(msg: dict):
    chat_id = msg["chat"]["id"]
    raw_text = msg.get("text") or msg.get("caption") or ""
    entities = msg.get("entities") or msg.get("caption_entities") or []

    if raw_text.strip() == "/start":
        requests.post(
            f"{API_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": (
                    "✅ Бот-редактор Дозор.ру (СПб) активен!\n\n"
                    "Отправь или перешли сюда пост — затем выбери нужный кружок на кнопке."
                )
            },
            timeout=10
        )
        return

    clean_body = clean_body_text(raw_text, entities)
    if not clean_body:
        return

    media_type = "text"
    file_id = None
    if "photo" in msg:
        media_type = "photo"
        file_id = msg["photo"][-1]["file_id"]
    elif "video" in msg:
        media_type = "video"
        file_id = msg["video"]["file_id"]

    send_circle_selector(
        chat_id,
        {
            "clean_body": clean_body,
            "media_type": media_type,
            "file_id": file_id
        }
    )

def handle_callback(cb: dict):
    cb_id = cb.get("id")
    data = cb.get("data", "")
    msg = cb.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    prompt_msg_id = msg.get("message_id")

    if cb_id:
        requests.post(f"{API_URL}/answerCallbackQuery", json={"callback_query_id": cb_id}, timeout=5)

    if not chat_id or not prompt_msg_id or data not in CIRCLE_MAP:
        return

    key = f"{chat_id}_{prompt_msg_id}"
    post_data = PENDING_POSTS.pop(key, None)
    if not post_data:
        return

    # Удаляем сообщение с кнопками выбора кружка
    requests.post(
        f"{API_URL}/deleteMessage",
        json={"chat_id": chat_id, "message_id": prompt_msg_id},
        timeout=5
    )

    circle = CIRCLE_MAP[data]
    final_text = build_final_post(post_data["clean_body"], circle)
    media_type = post_data["media_type"]
    file_id = post_data["file_id"]

    if media_type == "photo" and file_id:
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
    elif media_type == "video" and file_id:
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

def process_update(update: dict):
    if "message" in update:
        handle_message(update["message"])
    elif "callback_query" in update:
        handle_callback(update["callback_query"])

def main():
    print("==================================================")
    print("✅ Дозор СПб Репостер V2 (с выбором кружка) запущен!")
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

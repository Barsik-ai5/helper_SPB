import os
import re
import time
import requests
from html import escape
from dotenv import load_dotenv

load_dotenv()

# Токен и канал
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("SPB_HELPER_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

if not BOT_TOKEN:
    raise RuntimeError("[-] ОШИБКА: Не задана переменная окружения BOT_TOKEN!")
if not CHANNEL_ID:
    raise RuntimeError("[-] ОШИБКА: Не задана переменная окружения CHANNEL_ID!")

BOT_TOKEN = BOT_TOKEN.strip()
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Фирменная подпись Дозора
FOOTER_HTML = (
    "\n\n📡 Дозор.ру | Питер и Ленинградская область - "
    "<a href='https://t.me/RadarLO_SPB'>Подписаться</a>"
)

# Регулярки
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

LEADING_SYMBOLS_RE = re.compile(
    r"^(?:[⚡️⚡❗️❕🔴🟡🟢⚪️⚪⚠️🚨💥🔥📢📌🔹🔸]+\s*)+"
)

# Память
PENDING_POSTS = {}
READY_POSTS = {}

CIRCLE_MAP = {
    "circle_red": "🔴",
    "circle_yellow": "🟡",
    "circle_green": "🟢",
    "circle_white": "⚪",
    "circle_none": ""
}

def cleanup_memory():
    """Очищает старые записи, чтобы память не переполнялась."""
    now = time.time()
    cutoff = now - 3600  # 1 час
    
    if len(PENDING_POSTS) > 200:
        for k in [k for k, v in PENDING_POSTS.items() if v.get("ts", 0) < cutoff]:
            PENDING_POSTS.pop(k, None)
            
    if len(READY_POSTS) > 200:
        for k in [k for k, v in READY_POSTS.items() if v.get("ts", 0) < cutoff]:
            READY_POSTS.pop(k, None)

def truncate_text(text: str, max_len: int) -> str:
    """Обрезает текст по границе предложения или пробела под лимиты Telegram."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    last_space = cut.rfind(" ")
    return cut[:last_space].strip() + "..." if last_space > 0 else cut.strip() + "..."

def _get_link_char_ranges(raw_text: str, entities: list) -> list:
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
    if not raw_text:
        return ""

    link_ranges = _get_link_char_ranges(raw_text, entities or [])
    lines = raw_text.splitlines()

    line_bounds = []
    pos = 0
    for line in lines:
        start = pos
        end = pos + len(line)
        line_bounds.append((start, end))
        pos = end + 1

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
        stripped_body = LEADING_SYMBOLS_RE.sub("", clean_body).strip()
        body_with_circle = f"{circle} {stripped_body}"
    else:
        body_with_circle = clean_body

    # Экранируем только тело, футер добавляем уже как HTML
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
            [{"text": "Без кружка", "callback_data": "circle_none"}]
        ]
    }
    resp = requests.post(
        f"{API_URL}/sendMessage",
        json={"chat_id": chat_id, "text": "Выбери кружок для начала сообщения:", "reply_markup": keyboard},
        timeout=10
    )
    if resp.status_code == 200:
        prompt_msg_id = resp.json().get("result", {}).get("message_id")
        if prompt_msg_id:
            post_data["ts"] = time.time()
            PENDING_POSTS[f"{chat_id}_{prompt_msg_id}"] = post_data
            cleanup_memory()

def handle_message(msg: dict):
    chat_id = msg["chat"]["id"]
    raw_text = msg.get("text") or msg.get("caption") or ""
    entities = msg.get("entities") or msg.get("caption_entities") or []

    if raw_text.strip() == "/start":
        requests.post(
            f"{API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": "✅ Бот-редактор Дозор.ру (СПб) V3 активен!\nОтправь или перешли пост."},
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
        {"clean_body": clean_body, "media_type": media_type, "file_id": file_id}
    )

def handle_callback(cb: dict):
    cb_id = cb.get("id")
    data = cb.get("data", "")
    msg = cb.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id")

    if cb_id:
        requests.post(f"{API_URL}/answerCallbackQuery", json={"callback_query_id": cb_id}, timeout=5)

    if not chat_id or not msg_id:
        return

    # ШАГ 1: Выбор кружка
    if data in CIRCLE_MAP:
        key = f"{chat_id}_{msg_id}"
        post_data = PENDING_POSTS.pop(key, None)
        if not post_data:
            return

        # Удаляем сообщение с кнопками выбора кружка
        requests.post(f"{API_URL}/deleteMessage", json={"chat_id": chat_id, "message_id": msg_id}, timeout=5)

        circle = CIRCLE_MAP[data]
        final_text = build_final_post(post_data["clean_body"], circle)
        media_type = post_data["media_type"]
        file_id = post_data["file_id"]

        # Создаем короткий ID для кнопки (Unix time в мс)
        post_key = str(int(time.time() * 1000))
        READY_POSTS[post_key] = {"final_text": final_text, "media_type": media_type, "file_id": file_id, "ts": time.time()}
        cleanup_memory()

        pub_keyboard = {"inline_keyboard": [[{"text": "📢 Опубликовать в канал", "callback_data": f"pub_{post_key}"}]]}

        # Отправляем превью админу
        if media_type == "photo" and file_id:
            requests.post(f"{API_URL}/sendPhoto", json={"chat_id": chat_id, "photo": file_id, "caption": final_text, "parse_mode": "HTML", "reply_markup": pub_keyboard}, timeout=15)
        elif media_type == "video" and file_id:
            requests.post(f"{API_URL}/sendVideo", json={"chat_id": chat_id, "video": file_id, "caption": final_text, "parse_mode": "HTML", "reply_markup": pub_keyboard}, timeout=15)
        else:
            requests.post(f"{API_URL}/sendMessage", json={"chat_id": chat_id, "text": final_text, "parse_mode": "HTML", "disable_web_page_preview": True, "reply_markup": pub_keyboard}, timeout=15)
        
        return

    # ШАГ 2: Публикация в канал
    if data.startswith("pub_"):
        post_key = data.replace("pub_", "")
        post_data = READY_POSTS.pop(post_key, None)
        
        if not post_data:
            requests.post(f"{API_URL}/answerCallbackQuery", json={"callback_query_id": cb_id, "text": "Пост устарел или уже опубликован", "show_alert": True}, timeout=5)
            return

        final_text = post_data["final_text"]
        media_type = post_data["media_type"]
        file_id = post_data["file_id"]

        success = False
        error_msg = ""

        # Проверка лимитов Telegram (4096 для текста, 1024 для подписи)
        if media_type in ("photo", "video"):
            safe_text = truncate_text(final_text, 1000) # 1000, чтобы точно влезло в 1024
            if media_type == "photo":
                resp = requests.post(f"{API_URL}/sendPhoto", json={"chat_id": CHANNEL_ID, "photo": file_id, "caption": safe_text, "parse_mode": "HTML"}, timeout=20)
            else:
                resp = requests.post(f"{API_URL}/sendVideo", json={"chat_id": CHANNEL_ID, "video": file_id, "caption": safe_text, "parse_mode": "HTML"}, timeout=20)
        else:
            safe_text = truncate_text(final_text, 4000) # 4000, чтобы точно влезло в 4096
            resp = requests.post(f"{API_URL}/sendMessage", json={"chat_id": CHANNEL_ID, "text": safe_text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)

        if resp.status_code == 200:
            success = True
        else:
            error_msg = resp.text

        # Редактируем кнопку у админа (убираем кнопку и ставим статус)
        if success:
            edit_markup = {"inline_keyboard": [[{"text": "✅ Опубликовано в канал", "callback_data": "none"}]]}
            requests.post(f"{API_URL}/editMessageReplyMarkup", json={"chat_id": chat_id, "message_id": msg_id, "reply_markup": edit_markup}, timeout=10)
        else:
            requests.post(f"{API_URL}/answerCallbackQuery", json={"callback_query_id": cb_id, "text": f"Ошибка публикации: {error_msg[:100]}", "show_alert": True}, timeout=5)

def process_update(update: dict):
    if "message" in update:
        handle_message(update["message"])
    elif "callback_query" in update:
        handle_callback(update["callback_query"])

def main():
    print("==================================================")
    print("✅ Дозор СПб Репостер V3 (с публикацией) запущен!")
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

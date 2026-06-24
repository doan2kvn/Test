import asyncio
import hashlib
import sqlite3
from datetime import timezone, timedelta

from telethon import TelegramClient, events

# ==========================
# CẤU HÌNH
# ==========================

API_ID = 31248277

API_HASH = "0afe58e4e67b1764886481cf38420983"

SESSION_NAME = "telegram_session"

CHANNELS = [
    "sansalehouse",
    "treckpee"

]

KEYWORDS = [
    "voucher 100k người mới",
    "mã người mới",
    "120k",
    "100k/0",
    "lẹ 100k người mới",
    "100k người mới"
]

SEND_TO = "me"

VN_TZ = timezone(timedelta(hours=7))

# ==========================
# SQLITE
# ==========================

conn = sqlite3.connect("telegram_monitor.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT,
    message_id INTEGER,
    content_hash TEXT,
    channel_name TEXT,
    keyword TEXT,
    message_text TEXT,
    created_at TEXT
)
""")

conn.commit()

# ==========================
# CLIENT
# ==========================

client = TelegramClient(
    SESSION_NAME,
    API_ID,
    API_HASH
)

# ==========================
# HÀM KIỂM TRA TRÙNG
# ==========================

def message_exists(chat_id, message_id):
    cursor.execute(
        "SELECT 1 FROM messages WHERE chat_id=? AND message_id=?",
        (str(chat_id), message_id)
    )
    return cursor.fetchone() is not None


def hash_exists(content_hash):
    cursor.execute(
        "SELECT 1 FROM messages WHERE content_hash=?",
        (content_hash,)
    )
    return cursor.fetchone() is not None


def save_message(
    chat_id,
    message_id,
    content_hash,
    channel_name,
    keyword,
    message_text,
    created_at
):
    cursor.execute(
        """
        INSERT INTO messages(
            chat_id,
            message_id,
            content_hash,
            channel_name,
            keyword,
            message_text,
            created_at
        )
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            str(chat_id),
            message_id,
            content_hash,
            channel_name,
            keyword,
            message_text,
            created_at
        )
    )
    conn.commit()

# ==========================
# LẮNG NGHE TIN NHẮN
# ==========================

@client.on(events.NewMessage(chats=CHANNELS))
async def new_message(event):

    try:

        text = event.raw_text.strip()

        if not text:
            return

        lower_text = text.lower()

        found_keyword = None

        for keyword in KEYWORDS:
            if keyword.lower() in lower_text:
                found_keyword = keyword
                break

        if not found_keyword:
            return

        chat_id = event.chat_id
        message_id = event.id

        if message_exists(chat_id, message_id):
            return

        content_hash = hashlib.md5(
            text.encode("utf-8")
        ).hexdigest()

        if hash_exists(content_hash):
            return

        vn_time = event.message.date.astimezone(VN_TZ)

        created_at = vn_time.strftime(
            "%d/%m/%Y %H:%M:%S"
        )

        channel_name = (
            event.chat.title
            if event.chat
            else "Unknown"
        )

        save_message(
            chat_id,
            message_id,
            content_hash,
            channel_name,
            found_keyword,
            text,
            created_at
        )

        notify = f"""
🔔 PHÁT HIỆN TỪ KHÓA

📢 Kênh: {channel_name}
🆔 Chat ID: {chat_id}
📨 Message ID: {message_id}

🔍 Từ khóa: {found_keyword}

🕒 Thời gian:
{created_at}

📄 Nội dung:
{text}
"""

        if event.message.media:
            await client.send_file(
                SEND_TO,
                event.message.media,
                caption=notify
            )
        else:
            await client.send_message(
                SEND_TO,
                notify
            )

        print(
            f"[+] {channel_name} | {found_keyword}"
        )

    except Exception as e:
        print("ERROR:", e)

# ==========================
# CHẠY BOT
# ==========================

async def main():

    print("=" * 50)
    print("BOT TELEGRAM THEO DÕI TỪ KHÓA")
    print("=" * 50)

    me = await client.get_me()

    print(f"Đăng nhập: {me.first_name}")

    print("Đang theo dõi:")

    for c in CHANNELS:
        print(" -", c)

    print("\nTừ khóa:")

    for k in KEYWORDS:
        print(" -", k)

    print("\nBot đang chạy...\n")

    await client.run_until_disconnected()

with client:
    client.loop.run_until_complete(main())

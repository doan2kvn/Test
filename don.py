from telethon import TelegramClient, events
from datetime import timezone, timedelta

# =========================
# CẤU HÌNH
# =========================

API_ID = 31248277

API_HASH = "0afe58e4e67b1764886481cf38420983"

GROUP_ID = -1004465989044

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

VN_TZ = timezone(timedelta(hours=7))

client = TelegramClient(
    "session",
    API_ID,
    API_HASH
)

# Lưu các tin đã xử lý
processed = set()

@client.on(events.NewMessage(chats=CHANNELS))
async def handler(event):

    try:
        text = event.raw_text or ""

        if not text:
            return

        lower_text = text.lower()

        found = None

        for keyword in KEYWORDS:
            if keyword.lower() in lower_text:
                found = keyword
                break

        if not found:
            return

        unique_id = f"{event.chat_id}_{event.id}"

        if unique_id in processed:
            return

        processed.add(unique_id)

        vn_time = event.message.date.astimezone(VN_TZ)

        channel_name = (
            event.chat.title
            if event.chat
            else "Unknown"
        )

        msg = f"""
🔔 PHÁT HIỆN TỪ KHÓA

📢 Kênh: {channel_name}
🔍 Từ khóa: {found}
🕒 Thời gian: {vn_time.strftime('%d/%m/%Y %H:%M:%S')}

📄 Nội dung:

{text}
"""

        if event.message.media:
            await client.send_file(
                GROUP_ID,
                event.message.media,
                caption=msg
            )
        else:
            await client.send_message(
                GROUP_ID,
                msg
            )

        print(
            f"[+] {channel_name} | {found}"
        )

    except Exception as e:
        print("ERROR:", e)

async def main():

    me = await client.get_me()

    print("=" * 50)
    print("ĐĂNG NHẬP THÀNH CÔNG")
    print("Tên:", me.first_name)
    print("=" * 50)

    print("Đang theo dõi:")

    for c in CHANNELS:
        print("-", c)

    print("\nĐang chạy...\n")

    await client.run_until_disconnected()

with client:
    client.loop.run_until_complete(main())

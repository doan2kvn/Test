import telebot
import requests
import urllib3

urllib3.disable_warnings()

TOKEN = "7679157857:AAECtsrfc8lKyKk5ZsGDpNIZoel6rNYsNeA"

bot = telebot.TeleBot(TOKEN)

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(
        message,
        "📦 Gửi link Shopee để xem thông tin sản phẩm"
    )


@bot.message_handler(func=lambda m: True)
def shopee(message):

    try:
        wait = bot.reply_to(
            message,
            "🔍 Đang xử lý..."
        )

        # Bóc link rút gọn
        r = requests.get(
            message.text.strip(),
            allow_redirects=True,
            headers=HEADERS,
            timeout=20,
            verify=False
        )

        final_url = r.url

        # API AddLiveTag
        api = requests.get(
            "https://data.addlivetag.com/product-data/product-data.php",
            params={
                "url": final_url
            },
            headers=HEADERS,
            timeout=30,
            verify=False
        )

        data = api.json()

        if data.get("status") != "success":
            bot.edit_message_text(
                "❌ Không lấy được dữ liệu sản phẩm",
                message.chat.id,
                wait.message_id
            )
            return

        p = data["productInfo"]

        product_name = p.get("productName", "Không rõ")
        shop_name = p.get("shopName", "Không rõ")
        item_id = p.get("itemId", "0")
        price = p.get("price", 0)
        sales = p.get("sales", 0)
        rating = p.get("rating", "0")
        image = p.get("imageUrl", "")

        text = f"""
📦 <b>{product_name}</b>

🏪 Shop: {shop_name}
🆔 Item ID: <code>{item_id}</code>

💰 Giá: {price:,} VNĐ
⭐ Đánh giá: {rating}
📈 Đã bán: {sales}

🔗 <a href="{final_url}">Mở sản phẩm</a>
"""

        try:
            bot.delete_message(
                message.chat.id,
                wait.message_id
            )
        except:
            pass

        if image:
            bot.send_photo(
                message.chat.id,
                image,
                caption=text,
                parse_mode="HTML"
            )
        else:
            bot.send_message(
                message.chat.id,
                text,
                parse_mode="HTML",
                disable_web_page_preview=False
            )

    except requests.exceptions.Timeout:
        bot.reply_to(
            message,
            "❌ Hết thời gian kết nối"
        )

    except Exception as e:
        bot.reply_to(
            message,
            f"❌ Lỗi:\n{str(e)}"
        )


print("Bot đang chạy...")

bot.infinity_polling(
    timeout=30,
    long_polling_timeout=30
)

import logging
import sqlite3
import asyncio
import requests
import json
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ⚠️ KHUYẾN KHÍCH: Đổi token mới nếu token này đã bị lộ công khai
TOKEN = "8912685699:AAEvRqPHX_C915_owUyUVXzP97hanfUpme0"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Cấu hình giao diện phím bấm nhanh
BTN_ADD = "➕ Thêm đơn"
BTN_LIST = "📋 Danh sách đơn"
BTN_DELETE = "🗑 Xóa đơn"

keyboard = [[BTN_ADD, BTN_LIST], [BTN_DELETE]]
markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

DB_NAME = "spx_bot_av3.db"

# ================= 🗄️ DATABASE LAYER =================

def init_db():
    """Khởi tạo database với cấu trúc JSON tối ưu chống lỗi lệch cột"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders(
        chat_id TEXT NOT NULL,
        tracking TEXT NOT NULL,
        json_data TEXT, 
        PRIMARY KEY(chat_id, tracking)
    )
    """)
    conn.commit()
    conn.close()

# ================= 🌐 API LAYER (SPX) =================

def check_spx_status(tracking):
    """Bóc tách dữ liệu từ API SPX dựa trên cấu trúc JSON thực tế"""
    url = (
        "https://spx.vn/shipment/order/open/order/get_order_info"
        f"?spx_tn={tracking}&language_code=vi"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": f"https://spx.vn/track/{tracking}",
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None

        data = r.json()
        if data.get("message") != "success":
            return None

        # Đi theo đúng cấu trúc: data -> sls_tracking_info -> records
        records = (
            data.get("data", {})
            .get("sls_tracking_info", {})
            .get("records", [])
        )
        if not records:
            return None

        # Bản ghi records[0] luôn là trạng thái mới nhất
        latest = records[0]
        status = (
            latest.get("buyer_description")
            or latest.get("description")
            or "Không rõ trạng thái"
        )

        # Xử lý convert timestamp sang định dạng ngày giờ VN
        actual_time = latest.get("actual_time")
        if actual_time:
            dt = datetime.fromtimestamp(int(actual_time))
            status += f" *({dt.strftime('%d/%m/%Y %H:%M')})*"

        return status
    except Exception as e:
        logging.error(f"Lỗi gọi API SPX cho mã {tracking}: {e}")
        return None

# ================= ⚡ BOT COMMANDS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /start khởi động bot"""
    context.user_data.clear()
    msg = (
        "👋 *Chào mừng bạn đến với Bot theo dõi đơn hàng SPX!*\n\n"
        "Sử dụng các phím chức năng bên dưới để điều khiển hệ thống nhanh chóng."
    )
    await update.message.reply_text(msg, reply_markup=markup, parse_mode="Markdown")

# ================= 💬 MESSAGE HANDLER & STATE MACHINE =================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    chat_id = str(update.effective_chat.id)
    state = context.user_data.get("state")

    # ----- ➕ CHỨC NĂNG: THÊM ĐƠN HÀNG -----
    if text == BTN_ADD:
        context.user_data["state"] = "waiting_tracking"
        await update.message.reply_text(
            "📦 *Gửi mã vận đơn SPX cần theo dõi:*", 
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove() # Ẩn bàn phím tạm thời để tiện nhập liệu
        )
        return

    if state == "waiting_tracking":
        tracking = text.upper()
        await update.message.reply_text("🔍 _Đang kiểm tra mã vận đơn trên hệ thống SPX..._", parse_mode="Markdown")
        
        status = await asyncio.to_thread(check_spx_status, tracking)

        if not status:
            await update.message.reply_text(
                "❌ *Không tìm thấy thông tin mã vận đơn này!*\nVui lòng kiểm tra lại mã hoặc thử lại sau.", 
                parse_mode="Markdown",
                reply_markup=markup
            )
            context.user_data.clear()
            return

        context.user_data["tracking"] = tracking
        context.user_data["status"] = status
        context.user_data["state"] = "waiting_name"
        await update.message.reply_text("📝 *Nhập tên gợi nhớ cho đơn hàng này:* \n_(Ví dụ: Áo khoác, Giày Nike...)_", parse_mode="Markdown")
        return

    if state == "waiting_name":
        tracking = context.user_data["tracking"]
        status = context.user_data["status"]
        name = text

        # Tạo object JSON lưu trữ
        order_dict = {
            "name": name,
            "tracking": tracking,
            "last_status": status,
            "created_at": datetime.now().strftime('%d/%m/%Y %H:%M')
        }
        json_string = json.dumps(order_dict, ensure_ascii=False)

        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO orders (chat_id, tracking, json_data) VALUES (?, ?, ?)",
            (chat_id, tracking, json_string),
        )
        conn.commit()
        conn.close()

        context.user_data.clear()
        
        reply_msg = (
            "✅ *Đã thêm đơn hàng thành công!*\n\n"
            f"📌 *Tên gợi nhớ:* {name}\n"
            f"📦 *Mã vận đơn:* `{tracking}` _(Chạm để copy)_\n"
            f"🚚 *Trạng thái:* {status}"
        )
        await update.message.reply_text(reply_msg, reply_markup=markup, parse_mode="Markdown")
        return

    # ----- 📋 CHỨC NĂNG: XEM DANH SÁCH -----
    if text == BTN_LIST:
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT json_data FROM orders WHERE chat_id=?", (chat_id,))
        rows = cur.fetchall()
        conn.close()

        if not rows:
            await update.message.reply_text("📭 *Danh sách trống.* Bạn chưa thêm đơn hàng nào!", parse_mode="Markdown")
            return

        msg = "📋 *DANH SÁCH ĐƠN HÀNG CỦA BẠN:*\n"
        msg += "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        
        for i, row in enumerate(rows, start=1):
            order_data = json.loads(row[0])
            msg += (
                f"{i}. 📌 *{order_data['name']}*\n"
                f"   📦 Mã: `{order_data['tracking']}`\n"
                f"   🔹 Trạng thái: {order_data['last_status']}\n\n"
            )

        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # ----- 🗑 CHỨC NĂNG: XÓA ĐƠN HÀNG -----
    if text == BTN_DELETE:
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT json_data FROM orders WHERE chat_id=?", (chat_id,))
        rows = cur.fetchall()
        conn.close()

        if not rows:
            await update.message.reply_text("📭 Không có đơn hàng nào để xóa.", reply_markup=markup)
            return

        msg = "🗑 *Chọn hoặc nhập chính xác mã vận đơn muốn xóa dưới đây:*\n\n"
        for row in rows:
            order_data = json.loads(row[0])
            msg += f"• {order_data['name']}: `{order_data['tracking']}`\n"

        context.user_data["state"] = "delete"
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return

    if state == "delete":
        tracking = text.upper()
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("DELETE FROM orders WHERE chat_id=? AND tracking=?", (chat_id, tracking))
        deleted = cur.rowcount
        conn.commit()
        conn.close()

        context.user_data.clear()
        if deleted:
            await update.message.reply_text("✅ *Đã xóa đơn hàng khỏi danh sách theo dõi.*", reply_markup=markup, parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ *Không tìm thấy mã vận đơn này.*", reply_markup=markup, parse_mode="Markdown")
        return

# ================= ⏳ AUTOMATIC STATUS CHECK JOB =================

async def auto_check_job(context: ContextTypes.DEFAULT_TYPE):
    """Tiến trình ngầm tự động quét và gửi thông báo khi có trạng thái mới"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT chat_id, tracking, json_data FROM orders")
    rows = cur.fetchall()

    for chat_id, tracking, json_str in rows:
        order_data = json.loads(json_str)
        old_status = order_data.get("last_status")
        
        # Gọi API lấy trạng thái mới nhất
        new_status = await asyncio.to_thread(check_spx_status, tracking)

        if new_status and new_status != old_status:
            # Cập nhật trạng thái mới vào Object JSON
            order_data["last_status"] = new_status
            new_json_str = json.dumps(order_data, ensure_ascii=False)

            cur.execute(
                "UPDATE orders SET json_data=? WHERE chat_id=? AND tracking=?",
                (new_json_str, chat_id, tracking),
            )
            conn.commit()

            # Gửi thông báo trực quan tới người dùng
            try:
                alert_msg = (
                    "🔔 *CẬP NHẬT TRẠNG THÁI ĐƠN HÀNG!*\n"
                    "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                    f"📌 *Đơn:* {order_data['name']}\n"
                    f"📦 *Mã:* `{tracking}`\n"
                    f"🚚 *Trạng thái mới:* {new_status}"
                )
                await context.bot.send_message(chat_id=chat_id, text=alert_msg, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Không thể gửi thông báo cho chat_id {chat_id}: {e}")

        # Giãn cách 1.5 giây mỗi request để bảo vệ IP không bị SPX chặn (Rate Limit)
        await asyncio.sleep(1.5)
        
    conn.close()

# ================= ⚙️ MAIN BOOTSTRAP =================

def main():
    # Khởi tạo DB mới an toàn
    init_db()

    app = Application.builder().token(TOKEN).build()

    # Điều hướng bộ lọc nhận diện lệnh/tin nhắn
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Lịch trình chạy quét tự động (Ví dụ: 900 giây = 15 phút một lần)
    app.job_queue.run_repeating(auto_check_job, interval=900, first=15)

    print("🚀 [SUCCESS] SPX Telegram Bot v3 JSON-Engine is running smoothly...")
    app.run_polling()

if __name__ == "__main__":
    main()

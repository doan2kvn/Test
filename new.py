import logging
import sqlite3
import asyncio
import requests
import json
from datetime import datetime, time
import pytz  # Thư viện xử lý múi giờ chuẩn

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ⚠️ KHUYẾN KHÍCH: Đổi token mới nếu token này đã bị lộ công khai
TOKEN = "8912685699:AAE6bx4ijvqwjM_x45BstJnXQcpwkQ7T0-g"

# Cấu hình múi giờ Hồ Chí Minh
VN_TZ = pytz.timezone("Asia/Ho_Chi_Minh")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Cấu hình giao diện phím bấm nhanh (Thêm nút Thống kê)
BTN_ADD = "➕ Thêm đơn"
BTN_LIST = "📋 Danh sách đơn"
BTN_STAT = "📊 Thống kê đơn"
BTN_DELETE = "🗑 Xóa đơn"

keyboard = [[BTN_ADD, BTN_LIST], [BTN_STAT, BTN_DELETE]]
markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

DB_NAME = "spx_bot_v3.db"

# ================= 🗄️ DATABASE LAYER =================

def init_db():
    """Khởi tạo database với cấu trúc lưu trữ đơn và bảng thống kê tổng số lượng"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    # Bảng lưu đơn hiện tại đang theo dõi
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders(
        chat_id TEXT NOT NULL,
        tracking TEXT NOT NULL,
        json_data TEXT, 
        PRIMARY KEY(chat_id, tracking)
    )
    """)
    # Bảng thống kê lịch sử đếm số lượng đơn (chỉ lưu số đếm)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS statistics(
        chat_id TEXT NOT NULL,
        delivered_count INTEGER DEFAULT 0,
        delivering_count INTEGER DEFAULT 0,
        cancelled_count INTEGER DEFAULT 0,
        PRIMARY KEY(chat_id)
    )
    """)
    conn.commit()
    conn.close()

def update_stat(chat_id, status_type, delta=1):
    """Cập nhật tăng/giảm số lượng đếm trong bảng thống kê"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    # Khởi tạo dòng nếu chưa có
    cur.execute("INSERT OR IGNORE INTO statistics (chat_id) VALUES (?)", (chat_id,))
    
    if status_type == "delivered":
        cur.execute("UPDATE statistics SET delivered_count = delivered_count + ? WHERE chat_id = ?", (delta, chat_id))
    elif status_type == "delivering":
        cur.execute("UPDATE statistics SET delivering_count = delivering_count + ? WHERE chat_id = ?", (delta, chat_id))
    elif status_type == "cancelled":
        cur.execute("UPDATE statistics SET cancelled_count = cancelled_count + ? WHERE chat_id = ?", (delta, chat_id))
        
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

        records = (
            data.get("data", {})
            .get("sls_tracking_info", {})
            .get("records", [])
        )
        if not records:
            return None

        latest = records[0]
        status = (
            latest.get("buyer_description")
            or latest.get("description")
            or "Không rõ trạng thái"
        )

        # Xử lý lấy thời gian theo múi giờ VN chuẩn
        actual_time = latest.get("actual_time")
        if actual_time:
            dt = datetime.fromtimestamp(int(actual_time), tz=VN_TZ)
            status += f" *({dt.strftime('%d/%m/%Y %H:%M')})*"

        return status
    except Exception as e:
        logging.error(f"Lỗi gọi API SPX cho mã {tracking}: {e}")
        return None

def detect_status_type(status_text):
    """Phân tích chuỗi trạng thái để phân loại nhóm đơn hàng"""
    text_lower = status_text.lower()
    if any(keyword in text_lower for keyword in ["thành công", "đã giao", "hoàn thành"]):
        return "delivered"
    elif any(keyword in text_lower for keyword in ["hủy", "không thành công", "trả hàng", "bị trả", "sự cố"]):
        return "cancelled"
    else:
        return "delivering"

# ================= ⚡ BOT COMMANDS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /start khởi động bot"""
    context.user_data.clear()
    msg = (
        "👋 *Chào mừng bạn đến với Bot theo dõi đơn hàng SPX!*\n\n"
        "Hệ thống đã cấu hình múi giờ *Hồ Chí Minh (GMT+7)* và tự động dọn dẹp các đơn hoàn thành/hủy vào lúc 00:00 hàng ngày."
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
            reply_markup=ReplyKeyboardRemove()
        )
        return

    if state == "waiting_tracking":
        tracking = text.upper()
        await update.message.reply_text("🔍 _Đang kiểm tra mã vận đơn trên hệ thống SPX..._", parse_mode="Markdown")
        
        status = await asyncio.to_thread(check_spx_status, tracking)

        if not status:
            await update.message.reply_text(
                "❌ *Không tìm thấy thông tin mã vận đơn này!*\nVui lòng kiểm tra lại mã hoặc thử lại sau.", 
                reply_markup=markup,
                parse_mode="Markdown"
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

        now_vn = datetime.now(VN_TZ)
        order_dict = {
            "name": name,
            "tracking": tracking,
            "last_status": status,
            "created_at": now_vn.strftime('%d/%m/%Y %H:%M')
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

        # Thống kê: Ghi nhận đơn mới thuộc nhóm trạng thái nào
        status_type = detect_status_type(status)
        update_stat(chat_id, status_type, delta=1)

        context.user_data.clear()
        
        # UI MỚI: Đưa tên gợi nhớ lên tiêu đề kèm icon thích hợp
        reply_msg = (
            f"📦 *ĐƠN HÀNG: {name.upper()}*\n"
            "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            "✅ *Đã thêm vào hệ thống theo dõi thành công!*\n\n"
            f"🏷 *Mã vận đơn:* `{tracking}` _(Chạm để copy)_\n"
            f"🚚 *Trạng thái hiện tại:* {status}"
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

        msg = "📋 *DANH SÁCH ĐƠN HÀNG HIỆN TẠI:*\n"
        msg += "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        
        for i, row in enumerate(rows, start=1):
            order_data = json.loads(row[0])
            msg += (
                f"{i}. 📦 *{order_data['name'].upper()}*\n"
                f"   🔹 Mã: `{order_data['tracking']}`\n"
                f"   🚚 Trạng thái: {order_data['last_status']}\n\n"
            )

        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # ----- 📊 CHỨC NĂNG: THỐNG KÊ (MỚI) -----
    if text == BTN_STAT:
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute(
            "SELECT delivered_count, delivering_count, cancelled_count FROM statistics WHERE chat_id=?", 
            (chat_id,)
        )
        row = cur.fetchone()
        conn.close()

        # Nếu chưa từng có bản ghi thống kê thì mặc định bằng 0
        delivered = row[0] if row else 0
        delivering = row[1] if row else 0
        cancelled = row[2] if row else 0

        stat_msg = (
            "📊 *BÁO CÁO THỐNG KÊ SỐ LƯỢNG ĐƠN HÀNG*\n"
            "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"🟢 *Đơn thành công (Đã giao):* {delivered} đơn\n"
            f"🟡 *Đơn đang trong quá trình giao:* {delivering} đơn\n"
            f"🔴 *Đơn đã hủy / Giao lỗi:* {cancelled} đơn\n"
            "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            "ℹ️ _Số liệu được tích lũy tự động từ lúc bạn thêm đơn vào hệ thống._"
        )
        await update.message.reply_text(stat_msg, parse_mode="Markdown", reply_markup=markup)
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
        
        # Lấy thông tin đơn trước khi xóa để giảm số lượng trong thống kê tương ứng nếu cần
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT json_data FROM orders WHERE chat_id=? AND tracking=?", (chat_id, tracking))
        row = cur.fetchone()
        
        if row:
            order_data = json.loads(row[0])
            status_type = detect_status_type(order_data["last_status"])
            # Giảm 1 đơn trong thống kê trạng thái hiện tại vì người dùng chủ động xóa hoàn toàn đơn này
            update_stat(chat_id, status_type, delta=-1)

            cur.execute("DELETE FROM orders WHERE chat_id=? AND tracking=?", (chat_id, tracking))
            conn.commit()
            conn.close()
            context.user_data.clear()
            await update.message.reply_text("✅ *Đã xóa đơn hàng khỏi danh sách theo dõi.*", reply_markup=markup, parse_mode="Markdown")
        else:
            conn.close()
            context.user_data.clear()
            await update.message.reply_text("❌ *Không tìm thấy mã vận đơn này.*", reply_markup=markup, parse_mode="Markdown")
        return


# ================= ⏳ AUTOMATIC STATUS CHECK JOB =================

async def auto_check_job(context: ContextTypes.DEFAULT_TYPE):
    """Tiến trình ngầm quét trạng thái mới và đồng bộ phân loại thống kê khi thay đổi trạng thái"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT chat_id, tracking, json_data FROM orders")
    rows = cur.fetchall()

    for chat_id, tracking, json_str in rows:
        order_data = json.loads(json_str)
        old_status = order_data.get("last_status")
        
        new_status = await asyncio.to_thread(check_spx_status, tracking)

        if new_status and new_status != old_status:
            old_type = detect_status_type(old_status)
            new_type = detect_status_type(new_status)

            # Nếu đơn hàng chuyển dịch trạng thái (Ví dụ: Từ Đang giao -> Thành công)
            if old_type != new_type:
                update_stat(chat_id, old_type, delta=-1)  # Giảm nhóm cũ
                update_stat(chat_id, new_type, delta=1)   # Tăng nhóm mới

            order_data["last_status"] = new_status
            new_json_str = json.dumps(order_data, ensure_ascii=False)

            cur.execute(
                "UPDATE orders SET json_data=? WHERE chat_id=? AND tracking=?",
                (new_json_str, chat_id, tracking),
            )
            conn.commit()

            try:
                # UI MỚI: Tên gợi nhớ đưa lên tiêu đề kết hợp Icon
                alert_msg = (
                    f"🔔 *CẬP NHẬT ĐƠN HÀNG: {order_data['name'].upper()}*\n"
                    "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                    f"📦 *Mã vận đơn:* `{tracking}`\n"
                    f"🚚 *Trạng thái mới:* {new_status}"
                )
                await context.bot.send_message(chat_id=chat_id, text=alert_msg, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Không thể gửi thông báo cho chat_id {chat_id}: {e}")

        await asyncio.sleep(1.5)
        
    conn.close()


# ================= 🕛 DAILY CLEANUP JOB (MỚI) =================

async def daily_cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    """Hành động tự động xóa đơn 'Thành công' và 'Hủy/Lỗi' đúng 00:00:00 đêm theo múi giờ VN"""
    logging.info("Bắt đầu tiến trình tự động dọn dẹp đơn hàng lúc 00:00...")
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT chat_id, tracking, json_data FROM orders")
    rows = cur.fetchall()

    deleted_count = 0
    for chat_id, tracking, json_str in rows:
        order_data = json.loads(json_str)
        status = order_data.get("last_status", "")
        status_type = detect_status_type(status)

        # Nếu là đơn thành công (delivered) hoặc đơn lỗi/hủy (cancelled) -> Tiến hành xóa bỏ
        if status_type in ["delivered", "cancelled"]:
            cur.execute("DELETE FROM orders WHERE chat_id=? AND tracking=?", (chat_id, tracking))
            deleted_count += 1
            
            # Gửi tin nhắn thông báo nhẹ cho người dùng biết hệ thống đã tự dọn dẹp
            try:
                clean_msg = (
                    f"🧹 *HỆ THỐNG TỰ ĐỘNG DỌN DẸP LÚC 00:00*\n"
                    f"Đơn hàng *{order_data['name']}* (`{tracking}`) đã hoàn thành/hủy, "
                    f"hệ thống đã xóa khỏi danh sách theo dõi định kỳ để tránh làm chật bộ nhớ của bạn."
                )
                await context.bot.send_message(chat_id=chat_id, text=clean_msg, parse_mode="Markdown")
            except Exception:
                pass

    conn.commit()
    conn.close()
    logging.info(f"Đã tự động xóa thành công {deleted_count} đơn hàng hoàn tất/lỗi vào cuối ngày.")


# ================= ⚙️ MAIN BOOTSTRAP =================

def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 1. Quét cập nhật trạng thái liên tục sau mỗi 15 phút (900 giây)
    app.job_queue.run_repeating(auto_check_job, interval=900, first=15)

    # 2. Hẹn lịch chạy dọn dẹp tự động đúng vào 00h:00m:00s mỗi ngày theo đúng múi giờ VN
    target_time = time(hour=0, minute=0, second=0, tzinfo=VN_TZ)
    app.job_queue.run_daily(daily_cleanup_job, time=target_time)

    print("🚀 [SUCCESS] SPX Telegram Bot v3 (Cải tiến Múi giờ VN + Thống kê + Tự xóa lúc 00h) đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()

import logging
import sqlite3
import asyncio
import requests
import json
from datetime import datetime, time
import pytz  # Thư viện xử lý múi giờ chuẩn quốc tế

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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Đấu nối cấu hình múi giờ Hồ Chí Minh (GMT+7)
TZ_HCM = pytz.timezone("Asia/Ho_Chi_Minh")

# Cấu hình giao diện bàn phím điều khiển nhanh
BTN_ADD = "➕ Thêm đơn"
BTN_LIST = "📋 Danh sách đơn"
BTN_DELETE = "🗑 Xóa đơn"
BTN_STATS = "📊 Thống kê đơn"

keyboard = [[BTN_ADD, BTN_LIST], [BTN_DELETE, BTN_STATS]]
markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

DB_NAME = "spx_bot_v3.db"

# ================= 🗄️ DATABASE LAYER =================

def init_db():
    """Khởi tạo cấu trúc các bảng Database (Bảng theo dõi & Bảng số liệu thống kê)"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    # Bảng 1: Lưu trữ các đơn hàng đang trong danh sách theo dõi
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders(
        chat_id TEXT NOT NULL,
        tracking TEXT NOT NULL,
        json_data TEXT, 
        PRIMARY KEY(chat_id, tracking)
    )
    """)
    
    # Bảng 2: Lưu trữ số lượng tích lũy (Chỉ lưu số để thống kê, không bị xóa)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS statistics(
        chat_id TEXT PRIMARY KEY,
        delivered INTEGER DEFAULT 0,
        delivering INTEGER DEFAULT 0,
        canceled INTEGER DEFAULT 0
    )
    """)
    conn.commit()
    conn.close()

def update_stats(chat_id, status_type):
    """Cộng dồn số liệu thống kê (+1 đơn) cho người dùng tương ứng"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    # Khởi tạo bản ghi mặc định cho người dùng nếu họ lần đầu sử dụng hệ thống
    cur.execute("INSERT OR IGNORE INTO statistics (chat_id) VALUES (?)", (chat_id,))
    
    if status_type == "delivered":
        cur.execute("UPDATE statistics SET delivered = delivered + 1 WHERE chat_id = ?", (chat_id,))
    elif status_type == "delivering":
        cur.execute("UPDATE statistics SET delivering = delivering + 1 WHERE chat_id = ?", (chat_id,))
    elif status_type == "canceled":
        cur.execute("UPDATE statistics SET canceled = canceled + 1 WHERE chat_id = ?", (chat_id,))
        
    conn.commit()
    conn.close()

# ================= 🌐 API LAYER (SPX) =================

def get_status_icon_and_type(status_text):
    """Phân tích nội dung trạng thái từ SPX để trả về Icon và Nhóm phân loại chính xác"""
    text_lower = status_text.lower()
    
    # Nhóm 1: Thành công / Đã giao hàng thành công
    if any(x in text_lower for x in ["thành công", "đã giao", "hoàn thành"]):
        return "✅", "delivered"
    # Nhóm 2: Đơn bị lỗi, tổng đài hủy hoặc hoàn trả hàng về nơi gửi
    elif any(x in text_lower for x in ["hủy", "lỗi", "không thành công", "trả hàng"]):
        return "❌", "canceled"
    # Nhóm 3: Các trạng thái trung gian (Đang vận chuyển, đang điều phối, lưu kho...)
    else:
        return "🚚", "delivering"

def check_spx_status(tracking):
    """Bóc tách dữ liệu từ API công khai của hệ thống SPX Express"""
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

        # Ép múi giờ Asia/Ho_Chi_Minh khi giải mã timestamp từ API trả về
        actual_time = latest.get("actual_time")
        if actual_time:
            dt = datetime.fromtimestamp(int(actual_time), tz=TZ_HCM)
            status += f" *({dt.strftime('%d/%m/%Y %H:%M')})*"

        return status
    except Exception as e:
        logging.error(f"Lỗi gọi API SPX cho mã {tracking}: {e}")
        return None

# ================= ⚡ BOT COMMANDS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kích hoạt khởi động Bot bằng lệnh /start"""
    context.user_data.clear()
    msg = (
        "👋 *Chào mừng bạn đến với Bot theo dõi đơn hàng SPX Express!*\n\n"
        "Sử dụng các phím bấm chức năng tiện ích bên dưới để điều hướng hệ thống."
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

        now_hcm = datetime.now(TZ_HCM)
        order_dict = {
            "name": name,
            "tracking": tracking,
            "last_status": status,
            "created_at": now_hcm.strftime('%d/%m/%Y %H:%M')
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

        # Đăng ký đếm số lượng thống kê khởi tạo ban đầu cho đơn mới
        _, status_type = get_status_icon_and_type(status)
        update_stats(chat_id, status_type)

        context.user_data.clear()
        
        icon, _ = get_status_icon_and_type(status)
        reply_msg = (
            f"{icon} *Đã thêm đơn hàng thành công!*\n\n"
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
            icon, _ = get_status_icon_and_type(order_data['last_status'])
            msg += (
                f"{i}. {icon} *{order_data['name']}*\n"
                f"   📦 Mã: `{order_data['tracking']}`\n"
                f"   🔹 Trạng thái: {order_data['last_status']}\n\n"
            )

        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # ----- 🗑 CHỨC NĂNG: XÓA ĐƠN HÀNG THỦ CÔNG -----
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

    # ----- 📊 CHỨC NĂNG: XEM BÁO CÁO THỐNG KÊ -----
    if text == BTN_STATS:
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT delivered, delivering, canceled FROM statistics WHERE chat_id=?", (chat_id,))
        row = cur.fetchone()
        conn.close()

        if row:
            delivered, delivering, canceled = row
        else:
            delivered, delivering, canceled = 0, 0, 0

        total = delivered + delivering + canceled
        msg = (
            "📊 *THỐNG KÊ ĐƠN HÀNG TÍCH LŨY*\n"
            "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"✅ *Thành công (Đã giao):* {delivered} đơn\n"
            f"🚚 *Đang vận chuyển (Xử lý):* {delivering} đơn\n"
            f"❌ *Đã hủy / Đơn báo lỗi:* {canceled} đơn\n"
            "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"🧮 *Tổng số đơn từng quản lý:* {total} đơn"
        )
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=markup)
        return

# ================= ⏳ AUTOMATIC STATUS CHECK JOB =================

async def auto_check_job(context: ContextTypes.DEFAULT_TYPE):
    """Tiến trình chạy ngầm quét kiểm tra biến động trạng thái từ hệ thống API"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT chat_id, tracking, json_data FROM orders")
    rows = cur.fetchall()

    for chat_id, tracking, json_str in rows:
        order_data = json.loads(json_str)
        old_status = order_data.get("last_status")
        
        new_status = await asyncio.to_thread(check_spx_status, tracking)

        if new_status and new_status != old_status:
            # Lấy thông tin nhóm trạng thái cũ và mới
            _, old_type = get_status_icon_and_type(old_status)
            icon, new_type = get_status_icon_and_type(new_status)

            # Nếu phát hiện dịch chuyển trạng thái (Ví dụ từ Đang giao -> Thành công)
            if old_type != new_type:
                update_stats(chat_id, new_type)

            order_data["last_status"] = new_status
            new_json_str = json.dumps(order_data, ensure_ascii=False)

            cur.execute(
                "UPDATE orders SET json_data=? WHERE chat_id=? AND tracking=?",
                (new_json_str, chat_id, tracking),
            )
            conn.commit()

            try:
                # Thiết kế tiêu đề chứa: Icon + [Tên đơn hàng gợi nhớ] lên đầu tin nhắn
                alert_msg = (
                    f"{icon} *[{order_data['name']}] CẬP NHẬT TRẠNG THÁI MỚI!*\n"
                    "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                    f"📦 *Mã vận đơn:* `{tracking}`\n"
                    f"🚚 *Chi tiết:* {new_status}"
                )
                await context.bot.send_message(chat_id=chat_id, text=alert_msg, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Không thể gửi thông báo cho chat_id {chat_id}: {e}")

        # Độ trễ bảo vệ tránh bị quét dính chặn IP từ hệ thống máy chủ
        await asyncio.sleep(1.5)
        
    conn.close()

# ================= 🧹 DAILY AUTO CLEANUP JOB (00:00) =================

async def daily_cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    """Quét dọn tự động định kỳ chính xác vào lúc 00:00 hàng ngày theo múi giờ Việt Nam"""
    logging.info("Bắt đầu tiến trình quét dọn tự động định kỳ vào cuối ngày...")
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT chat_id, tracking, json_data FROM orders")
    rows = cur.fetchall()

    deleted_count = 0
    for chat_id, tracking, json_str in rows:
        order_data = json.loads(json_str)
        status = order_data.get("last_status", "")
        
        _, status_type = get_status_icon_and_type(status)
        
        # Chỉ xóa những đơn đã Giao thành công (delivered) hoặc đơn bị Hủy/Lỗi (canceled)
        if status_type in ["delivered", "canceled"]:
            cur.execute("DELETE FROM orders WHERE chat_id=? AND tracking=?", (chat_id, tracking))
            deleted_count += 1
            
            try:
                # Gửi thông báo xác nhận tiến trình dọn dẹp cho người dùng
                clean_msg = (
                    f"🧹 *[Hệ thống tự động]* Đơn hàng *{order_data['name']}* (`{tracking}`) "
                    f"đã hoàn tất/hủy bỏ. Hệ thống đã xóa khỏi danh sách theo dõi thực tế và giữ số liệu lưu trữ bảo toàn tại mục 📊 Thống kê đơn."
                )
                await context.bot.send_message(chat_id=chat_id, text=clean_msg, parse_mode="Markdown")
            except Exception:
                pass

    conn.commit()
    conn.close()
    logging.info(f"Đã dọn dẹp thành công {deleted_count} đơn hàng hoàn tất vào lúc 00:00.")

# ================= ⚙️ MAIN BOOTSTRAP =================

def main():
    # Khởi tạo tệp tin dữ liệu SQLite cục bộ an toàn
    init_db()

    app = Application.builder().token(TOKEN).build()

    # Bộ định tuyến cấu trúc xử lý dòng văn bản
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 1. Cấu hình kiểm tra trạng thái đơn: Quét định kỳ 15 phút một lần (900 giây)
    app.job_queue.run_repeating(auto_check_job, interval=900, first=15)

    # 2. Cấu hình xóa dọn dẹp cuối ngày: Chạy chuẩn lúc 00:00:00 dựa trên múi giờ Hồ Chí Minh
    midnight_time = time(hour=0, minute=0, second=0, tzinfo=TZ_HCM)
    app.job_queue.run_daily(daily_cleanup_job, time=midnight_time)

    print("🚀 [SUCCESS] SPX Telegram Bot v3.5 (Enhanced) is running smoothly...")
    app.run_polling()

if __name__ == "__main__":
    main()

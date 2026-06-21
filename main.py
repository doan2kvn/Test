import logging
import sqlite3
import asyncio
import requests  # Giữ nguyên cấu trúc gọi sync qua asyncio.to_thread giống check.py
import json
from datetime import datetime, time
import pytz  # Ép buộc xử lý chuẩn múi giờ Châu Á/Hồ_Chí_Minh chống lệch giờ VPS

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)

# ⚠️ CẤU HÌNH BẮT BUỘC ĐỂ BOT HOẠT ĐỘNG
TOKEN = "8912685699:AAE6bx4ijvqwjM_x45BstJnXQcpwkQ7T0-g"
ADMIN_ID = 5475751501  # ID quản trị viên của sếp

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Cấu hình giao diện phím bấm nhanh ở menu chính
BTN_ADD = "➕ Thêm đơn"
BTN_LIST = "📋 Danh sách đơn"
BTN_STAT = "📊 Thống kê"
BTN_DELETE = "🗑 Xóa đơn"

keyboard = [[BTN_ADD, BTN_LIST], [BTN_STAT, BTN_DELETE]]
markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

DB_NAME = "spx_bot_v4_pro.db"

# Định nghĩa múi giờ Việt Nam toàn cục
VN_TZ = pytz.timezone("Asia/Ho_Chi_Minh")

# ================= 🗄️ DATABASE LAYER =================

def init_db():
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
    """Bóc tách dữ liệu từ API SPX và ép chuẩn múi giờ Việt Nam"""
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

        # Ép timestamp sang đúng múi giờ Asia/Ho_Chi_Minh của Việt Nam
        actual_time = latest.get("actual_time")
        if actual_time:
            dt_utc = datetime.fromtimestamp(int(actual_time), tz=pytz.utc)
            dt_vn = dt_utc.astimezone(VN_TZ)
            status += f" *({dt_vn.strftime('%d/%m/%Y %H:%M')})*"

        return status
    except Exception as e:
        logging.error(f"Lỗi gọi API SPX cho mã {tracking}: {e}")
        return None

# ================= 🛡️ ADMIN SECURITY CHECK =================

def is_admin(update: Update) -> bool:
    if update.effective_user and update.effective_user.id == ADMIN_ID:
        return True
    return False

async def reject_non_admin(update: Update):
    msg = "🔏 *Hệ thống bảo mật:* Bot này được cấu hình riêng tư công việc. Bạn không có quyền sử dụng!"
    if update.message:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    elif update.callback_query:
        await update.callback_query.answer("Bạn không có quyền quản trị viên!", show_alert=True)

# ================= ⚡ BOT COMMANDS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await reject_non_admin(update)
        return

    context.user_data.clear()
    msg = (
        "👋 *Chào mừng Sếp quay trở lại với Hệ thống SPX Pro!*\n\n"
        "Hệ thống tự động theo dõi, báo cáo thống kê và dọn dẹp đơn hàng đã sẵn sàng hoạt động."
    )
    await update.message.reply_text(msg, reply_markup=markup, parse_mode="Markdown")

# ================= 💬 MESSAGE HANDLER & STATE MACHINE =================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await reject_non_admin(update)
        return

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

    # 🛠️ ĐÂY LÀ ĐOẠN FIX LUỒNG: Giống 100% logic check.py và ảnh minh họa của sếp
    if state == "waiting_tracking":
        tracking = text.upper()
        await update.message.reply_text("🔍 _Đang kiểm tra mã vận đơn trên hệ thống SPX..._", parse_mode="Markdown")
        
        # Gọi luồng check an toàn
        status = await asyncio.to_thread(check_spx_status, tracking)

        # Nếu sai mã/không tìm thấy -> Báo lỗi và hủy luồng, trả về menu luôn
        if not status:
            context.user_data.clear()  # Xóa trạng thái, trả bot về trạng thái tự do
            await update.message.reply_text(
                "❌ *Không tìm thấy thông tin mã vận đơn này!*\nVui lòng kiểm tra lại mã hoặc thử lại sau.", 
                parse_mode="Markdown",
                reply_markup=markup
            )
            return

        # Nếu đúng mã -> Lưu thông tin tạm thời rồi mới hỏi tên gợi nhớ
        context.user_data["tracking"] = tracking
        context.user_data["status"] = status
        context.user_data["state"] = "waiting_name"  # Chuyển trạng thái chờ nhập tên
        await update.message.reply_text("📝 *Nhập tên gợi nhớ cho đơn hàng này:* \n_(Ví dụ: Áo khoác, Giày Nike...)_", parse_mode="Markdown")
        return

    if state == "waiting_name":
        tracking = context.user_data["tracking"]
        status = context.user_data["status"]
        name = text

        order_dict = {
            "name": name,
            "tracking": tracking,
            "last_status": status,
            "created_at": datetime.now(VN_TZ).strftime('%d/%m/%Y %H:%M')
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
            f"📦 *Mã vận đơn:* `{tracking}`\n"
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
            await update.message.reply_text("📭 *Danh sách trống.* Sếp chưa thêm đơn hàng nào!", parse_mode="Markdown")
            return

        await update.message.reply_text("📋 *DANH SÁCH ĐƠN HÀNG ĐANG THEO DÕI:*", parse_mode="Markdown")
        
        for row in rows:
            order_data = json.loads(row[0])
            t_code = order_data['tracking']
            
            msg = (
                f"📌 *{order_data['name']}*\n"
                f"📦 Mã: `{t_code}`\n"
                f"🔹 Trạng thái: {order_data['last_status']}\n"
            )
            inline_kb = [
                [
                    InlineKeyboardButton("🔄 Cập nhật ngay", callback_data=f"refresh_{t_code}"),
                    InlineKeyboardButton("🗑 Xóa nhanh", callback_data=f"quickdel_{t_code}")
                ]
            ]
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_kb))
        return

    # ----- 📊 CHỨC NĂNG: THỐNG KÊ ĐƠN HÀNG -----
    if text == BTN_STAT:
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT json_data FROM orders WHERE chat_id=?", (chat_id,))
        rows = cur.fetchall()
        conn.close()

        if not rows:
            await update.message.reply_text("📭 Hiện tại không có dữ liệu đơn hàng nào để thống kê.", parse_mode="Markdown")
            return

        total_orders = len(rows)
        delivering = 0     
        sorting_hub = 0    
        waiting_pickup = 0 
        others = 0         

        for row in rows:
            order_data = json.loads(row[0])
            status_lower = order_data.get("last_status", "").lower()

            if "đang giao" in status_lower or "shout" in status_lower or "shipper" in status_lower:
                delivering += 1
            elif "kho" in status_lower or "phân loại" in status_lower or "trung chuyển" in status_lower:
                sorting_hub += 1
            elif "chuẩn bị" in status_lower or "chờ" in status_lower:
                waiting_pickup += 1
            else:
                others += 1

        stat_msg = (
            "📊 *BÁO CÁO THỐNG KÊ VẬN ĐƠN SPX*\n"
            "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"📦 Tổng số đơn theo dõi: *{total_orders} đơn*\n\n"
            f"🛵 Đang đi giao: *{delivering} đơn*\n"
            f"🏢 Đang ở kho phân loại: *{sorting_hub} đơn*\n"
            f"⏳ Người gửi đang chuẩn bị/Chờ lấy: *{waiting_pickup} đơn*\n"
            f"🔄 Trạng thái khác: *{others} đơn*\n"
            "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            "💡 _Hệ thống tự động cập nhật ngầm định kỳ 15 phút một lần._"
        )
        await update.message.reply_text(stat_msg, parse_mode="Markdown")
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

        inline_buttons = []
        for row in rows:
            order_data = json.loads(row[0])
            btn_text = f"❌ {order_data['name']} ({order_data['tracking']})"
            inline_buttons.append([InlineKeyboardButton(btn_text, callback_data=f"delete_{order_data['tracking']}")])
        
        inline_buttons.append([InlineKeyboardButton("🔙 Hủy bỏ", callback_data="cancel_action")])

        await update.message.reply_text(
            "🗑 *Chọn đơn hàng Sếp muốn xóa khỏi danh sách dưới đây:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_buttons)
        )
        return

# ================= 🎛️ INLINE BUTTON CALLBACK HANDLER =================

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await reject_non_admin(update)
        return

    query = update.callback_query
    await query.answer()

    chat_id = str(update.effective_chat.id)
    data = query.data

    if data.startswith("refresh_"):
        tracking = data.replace("refresh_", "")
        
        original_text = query.message.text
        await query.edit_message_text(f"{original_text}\n\n🔄 _Đang quét dữ liệu thời gian thực..._", parse_mode="Markdown")

        new_status = await asyncio.to_thread(check_spx_status, tracking)

        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT json_data FROM orders WHERE chat_id=? AND tracking=?", (chat_id, tracking))
        row = cur.fetchone()

        if row:
            order_data = json.loads(row[0])
            if new_status:
                order_data["last_status"] = new_status
                cur.execute(
                    "UPDATE orders SET json_data=? WHERE chat_id=? AND tracking=?",
                    (json.dumps(order_data, ensure_ascii=False), chat_id, tracking)
                )
                conn.commit()
            
            conn.close()

            updated_msg = (
                f"📌 *{order_data['name']}*\n"
                f"📦 Mã: `{order_data['tracking']}`\n"
                f"🔹 Trạng thái: {order_data['last_status']}\n"
            )
            inline_kb = [
                [
                    InlineKeyboardButton("🔄 Cập nhật ngay", callback_data=f"refresh_{tracking}"),
                    InlineKeyboardButton("🗑 Xóa nhanh", callback_data=f"quickdel_{tracking}")
                ]
            ]
            await query.edit_message_text(updated_msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_kb))
        else:
            conn.close()
            await query.edit_message_text("❌ Đơn hàng không tồn tại trong hệ thống dữ liệu.")

    elif data.startswith("delete_") or data.startswith("quickdel_"):
        tracking = data.replace("delete_", "").replace("quickdel_", "")
        
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("DELETE FROM orders WHERE chat_id=? AND tracking=?", (chat_id, tracking))
        conn.commit()
        conn.close()

        await query.edit_message_text(f"✅ *Đã xóa mã vận đơn `{tracking}` thành công.*", parse_mode="Markdown")

    elif data == "cancel_action":
        await query.edit_message_text("❌ Đã hủy thao tác.")

# ================= ⏳ AUTOMATIC STATUS CHECK JOB =================

async def auto_check_job(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT chat_id, tracking, json_data FROM orders")
    rows = cur.fetchall()

    for chat_id, tracking, json_str in rows:
        order_data = json.loads(json_str)
        old_status = order_data.get("last_status")
        
        new_status = await asyncio.to_thread(check_spx_status, tracking)

        if new_status and new_status != old_status:
            order_data["last_status"] = new_status
            new_json_str = json.dumps(order_data, ensure_ascii=False)

            cur.execute(
                "UPDATE orders SET json_data=? WHERE chat_id=? AND tracking=?",
                (new_json_str, chat_id, tracking),
            )
            conn.commit()

            try:
                name_upper = order_data['name'].upper()
                alert_msg = (
                    f"🔔 *⚡ THÔNG BÁO: {name_upper} ⚡*\n"
                    "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                    f"📦 *Mã vận đơn:* `{tracking}`\n"
                    f"🚚 *Hành trình mới:* {new_status}\n\n"
                    "👉 _Nhấn phím chức năng để kiểm tra các đơn hàng khác._"
                )
                await context.bot.send_message(chat_id=chat_id, text=alert_msg, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Không thể gửi thông báo cho chat_id {chat_id}: {e}")

        await asyncio.sleep(1.5)
        
    conn.close()

# ================= ⏳ DAILY CLEANUP JOB =================

async def auto_clean_completed_orders_job(context: ContextTypes.DEFAULT_TYPE):
    logging.info("🧹 Bắt đầu tiến trình tự động dọn dẹp đơn hàng lúc 00:00...")
    
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT chat_id, tracking, json_data FROM orders")
    rows = cur.fetchall()

    deleted_count = 0

    for chat_id, tracking, json_str in rows:
        try:
            order_data = json.loads(json_str)
            status_lower = order_data.get("last_status", "").lower()

            if "thành công" in status_lower or "hủy" in status_lower:
                cur.execute("DELETE FROM orders WHERE chat_id=? AND tracking=?", (chat_id, tracking))
                deleted_count += 1
                logging.info(f"🗑 Đã tự động xoá đơn: {order_data.get('name')} | Mã: {tracking}")
        except Exception as ex:
            logging.error(f"Lỗi khi kiểm tra xoá đơn {tracking}: {ex}")

    conn.commit()
    conn.close()
    logging.info(f"🏁 Hoàn tất dọn dẹp. Tổng số đơn đã xoá tự động: {deleted_count}")

# ================= ⚙️ MAIN BOOTSTRAP =================

def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    
    # Lịch quét tự động (15 phút/lần)
    app.job_queue.run_repeating(auto_check_job, interval=900, first=15)

    # Lịch dọn dẹp lúc 00:00:00 hằng ngày theo múi giờ Việt Nam
    midnight_time = time(hour=0, minute=0, second=0, tzinfo=VN_TZ)
    app.job_queue.run_daily(auto_clean_completed_orders_job, time=midnight_time)

    print("🚀 [SUCCESS] SPX Bot Premium System v4 is fully active...")
    app.run_polling()

if __name__ == "__main__":
    main()

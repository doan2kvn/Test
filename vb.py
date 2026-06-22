import logging
import sqlite3
import asyncio
import requests
import json
import time
import re
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)

# ================= ⚙️ CẤU HÌNH HỆ THỐNG =================
TOKEN = "7984355682:AAHM9klmuopni-HhxJhoFvZlGBlp54xBNL0"
ADMIN_ID = "5475751501"  # ID Telegram của Admin (Bot sẽ gửi yêu cầu duyệt vào đây)

# --- CẤU HÌNH NGÂN HÀNG CỦA BẠN ĐỂ TẠO MÃ QR VÀ QUÉT API ---
BANK_ID = "bidv"             # Mã ngân hàng viết thường (bidv, mbbank, vcb, icb, tcb...)
ACCOUNT_NO = "1234567890"      # Số tài khoản ngân hàng của bạn
ACCOUNT_NAME = "NGUYEN VAN A"  # Tên chủ tài khoản (Viết hoa không dấu)
BANK_API_URL = "https://api.sieuthicode.net/historyapibidvv2/TokenBIDV" # Link API tự động (Vẫn giữ song song)

# Token API của các ĐVVC (Nếu có)
GHN_TOKEN = "b7570169-6be1-11f1-9c9d-72e5c527c8b8"
VIETTEL_TOKEN = "677801995BCB4A176262EB7F93C27D7F"
GHTK_TOKEN = "TOKEN_GHTK"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --- MENU PHÍM BẤM HỆ THỐNG ---
BTN_TRACK = "🚚 Theo Dõi Đơn"
BTN_SHOP = "🛒 Mua Tài Khoản"
BTN_DEPOSIT = "💳 Nạp Tiền Tự Động"
BTN_INFO = "👤 Tài Khoản Của Tôi"

BTN_ADMIN_STAT = "📊 Thống Kê"
BTN_ADMIN_ADD_MONEY = "💵 Cộng Tiền"
BTN_ADMIN_ADD_ACC = "📦 Thêm Tài Khoản"
BTN_ADMIN_EXIT = "🔙 Thoát Admin"

user_keyboard = [[BTN_TRACK, BTN_SHOP], [BTN_DEPOSIT, BTN_INFO]]
user_markup = ReplyKeyboardMarkup(user_keyboard, resize_keyboard=True)

admin_keyboard = [[BTN_ADMIN_STAT, BTN_ADMIN_ADD_MONEY], [BTN_ADMIN_ADD_ACC, BTN_ADMIN_EXIT]]
admin_markup = ReplyKeyboardMarkup(admin_keyboard, resize_keyboard=True)

DB_NAME = "premium_automation_bot.db"

# ================= 🗄️ DATABASE LAYER =================

def get_db():
    return sqlite3.connect(DB_NAME, check_same_thread=False)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders(
        chat_id TEXT NOT NULL, tracking TEXT NOT NULL, json_data TEXT, PRIMARY KEY(chat_id, tracking)
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        chat_id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0, joined_at TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, account_info TEXT, price INTEGER, is_sold INTEGER DEFAULT 0
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS processed_transactions(
        transaction_id TEXT PRIMARY KEY, processed_at TEXT
    )""")
    conn.commit()
    conn.close()

def register_user(chat_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (chat_id, balance, joined_at) VALUES (?, 0, ?)", 
                (chat_id, datetime.now().strftime('%d/%m/%Y %H:%M')))
    conn.commit()
    conn.close()

def get_user_balance(chat_id):
    conn = get_db()
    res = conn.cursor().execute("SELECT balance FROM users WHERE chat_id=?", (chat_id,)).fetchone()
    conn.close()
    return res[0] if res else 0

# ================= 🧠 LOGIC KIỂM TRA ĐƠN HÀNG ĐVVC =================

def detect_courier(tracking):
    tracking = tracking.strip().upper()
    if tracking.startswith("SPX") or tracking.startswith("VN"): return "SPX"
    if tracking.isdigit() and (8 <= len(tracking) <= 12): return "VIETTEL"
    if "." in tracking or (tracking.isdigit() and len(tracking) > 12): return "GHTK"
    return "GHN"

def query_courier_status(courier_type, tracking):
    try:
        if courier_type == "SPX":
            url = f"https://spx.vn/shipment/order/open/order/get_order_info?spx_tn={tracking}&language_code=vi"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json()
            records = r.get("data", {}).get("sls_tracking_info", {}).get("records", [])
            if records: return records[0].get("buyer_description") or records[0].get("description")
            
        elif courier_type == "GHN":
            url = "https://online-gateway.ghn.vn/shiip/public-api/v2/shipping-order/detail-by-client-code"
            headers = {"Token": GHN_TOKEN, "Content-Type": "application/json"}
            r = requests.post(url, json={"client_order_code": "", "order_code": tracking}, headers=headers, timeout=5).json()
            if r.get("code") == 200:
                status_code = r.get("data", {}).get("status", "")
                ghn_map = {"ready_to_pick": "Chờ lấy hàng", "picking": "Đang lấy hàng", "storing": "Đã nhập kho", "delivering": "Đang giao hàng", "delivered": "Đã giao thành công", "cancel": "Đã hủy"}
                return ghn_map.get(status_code, f"Trạng thái: {status_code}")
                
        elif courier_type == "VIETTEL":
            url = f"https://partner.viettelpost.vn/v2/order/tracking?OrderNumber={tracking}"
            r = requests.get(url, headers={"Token": VIETTEL_TOKEN, "Content-Type": "application/json"}, timeout=5).json()
            if isinstance(r, list) and len(r) > 0: return r[0].get("status_name", "Đang xử lý")
            
        elif courier_type == "GHTK":
            url = f"https://services.giaohangtietkiem.vn/services/shipment/v2/{tracking}"
            r = requests.get(url, headers={"Token": GHTK_TOKEN}, timeout=5).json()
            if r.get("success"): return r.get("order", {}).get("status_text", "Đang xử lý")
    except:
        pass
    return "Đang chờ bưu cục cập nhật dữ liệu"

# ================= 🏧 LAYER NẠP TIỀN TỰ ĐỘNG QUA API =================

async def auto_banking_job(context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    cur = conn.cursor()
    try:
        response = requests.get(BANK_API_URL, timeout=10)
        if response.status_code != 200: return
        data = response.json()
        if data.get("status") != "success": return
        
        transactions = data.get("transactions", [])
        for tx in transactions:
            if tx.get("type") != "IN": continue
            
            tx_id = tx.get("transactionID")
            amount = int(tx.get("amount", 0))
            description = tx.get("description", "").upper()
            
            cur.execute("SELECT transaction_id FROM processed_transactions WHERE transaction_id=?", (tx_id,))
            if cur.fetchone(): continue
            
            match = re.search(r"NAP\s+(\d+)", description)
            if match:
                user_id = match.group(1)
                cur.execute("SELECT chat_id FROM users WHERE chat_id=?", (user_id,))
                if cur.fetchone():
                    cur.execute("UPDATE users SET balance = balance + ? WHERE chat_id=?", (amount, user_id))
                    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    cur.execute("INSERT INTO processed_transactions (transaction_id, processed_at) VALUES (?, ?)", (tx_id, now_str))
                    conn.commit()
                    
                    success_msg = (
                        f"💳 *THÔNG BÁO NẠP TIỀN TỰ ĐỘNG*\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                        f"✅ Hệ thống ghi nhận cổng thanh toán thành công.\n\n"
                        f"💰 Số tiền: +`{amount:,}đ`\n🆔 Mã GD: `{tx_id}`\n\n"
                        f"Số dư tài khoản của bạn đã được cập nhật!"
                    )
                    try: await context.bot.send_message(chat_id=user_id, text=success_msg, parse_mode="Markdown")
                    except: pass
    except Exception as e:
        logging.error(f"Lỗi cổng Auto Banking: {e}")
    finally:
        conn.close()

# ================= 🎛️ CALLBACK QUERY HANDLER (XỬ LÝ TOÀN BỘ NÚT INLINE) =================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = str(update.effective_chat.id)
    data = query.data
    
    # --- 🛒 LUỒNG MUA TÀI KHOẢN ---
    if data.startswith("buy_"):
        category_name = data.replace("buy_", "")
        conn = get_db()
        prod = conn.cursor().execute("SELECT id, price, account_info FROM products WHERE category=? AND is_sold=0 LIMIT 1", (category_name,)).fetchone()
        
        if not prod:
            await query.edit_message_text("❌ Xin lỗi, mặt hàng này vừa mới hết sạch trong kho!")
            conn.close()
            return
            
        prod_id, price, account_info = prod
        user_balance = get_user_balance(chat_id)
        
        if user_balance < price:
            await query.edit_message_text(f"❌ Số dư không đủ!\n• Giá: `{price:,}đ`\n• Bạn có: `{user_balance:,}đ`", parse_mode="Markdown")
            conn.close()
            return
            
        cur = conn.cursor()
        cur.execute("UPDATE users SET balance = balance - ? WHERE chat_id=?", (price, chat_id))
        cur.execute("UPDATE products SET is_sold=1 WHERE id=?", (prod_id,))
        conn.commit()
        conn.close()
        
        await query.edit_message_text(
            f"🎉 *GIAO DỊCH HOÀN TẤT!*\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"📦 Loại tài khoản: *{category_name}*\n💵 Chi phí: -`{price:,}đ`\n\n"
            f"🔑 *Thông tin tài khoản:*\n`{account_info}`", 
            parse_mode="Markdown"
        )
        return

    # --- 🏦 LUỒNG DUYỆT TIỀN THỦ CÔNG CỦA ADMIN ---
    if data.startswith("admin_approve_") or data.startswith("admin_reject_"):
        # Chỉ cho phép ID Admin thực hiện thao tác bấm nút duyệt
        if chat_id != ADMIN_ID:
            return

        if data.startswith("admin_approve_"):
            # Cấu trúc: admin_approve_USERID_AMOUNT
            details = data.replace("admin_approve_", "").split("_")
            target_user_id = details[0]
            amount = int(details[1])

            conn = get_db()
            conn.cursor().execute("UPDATE users SET balance = balance + ? WHERE chat_id=?", (amount, target_user_id))
            conn.commit()
            conn.close()

            # Cập nhật trạng thái tin nhắn phía Admin công khai
            await query.edit_message_text(f"✅ *Đã Duyệt Đơn Thành Công!*\n💵 Đã cộng `+{amount:,}đ` cho User `{target_user_id}`.", parse_mode="Markdown")
            
            # Gửi thông báo trực tiếp cho khách hàng
            try:
                msg_to_user = (
                    f"🔔 *THÔNG BÁO DUYỆT TIỀN THÀNH CÔNG*\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                    f"✅ Admin đã xác nhận hóa đơn chuyển khoản của bạn.\n"
                    f"💰 Số tiền cộng ví: +`{amount:,}đ`\n\n"
                    f"Chúc bạn mua sắm vui vẻ!"
                )
                await context.bot.send_message(chat_id=target_user_id, text=msg_to_user, parse_mode="Markdown")
            except: pass

        elif data.startswith("admin_reject_"):
            details = data.replace("admin_reject_", "").split("_")
            target_user_id = details[0]
            
            await query.edit_message_text(f"❌ *Đã Từ Chối Đơn Nạp* của User `{target_user_id}`.", parse_mode="Markdown")
            try:
                await context.bot.send_message(chat_id=target_user_id, text="❌ *Hóa đơn nạp tiền của bạn bị từ chối.* Vui lòng kiểm tra lại số tiền hoặc liên hệ trực tiếp Admin hỗ trợ.", parse_mode="Markdown")
            except: pass

# ================= 💬 MESSAGE HANDLER & STATE MACHINE =================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    text = update.message.text.strip()
    chat_id = str(update.effective_chat.id)
    state = context.user_data.get("state")
    
    register_user(chat_id)

    if text == "🔙 Quay Lại":
        context.user_data.clear()
        await update.message.reply_text("Đã quay lại menu chính.", reply_markup=user_markup)
        return

    if text == BTN_INFO:
        balance = get_user_balance(chat_id)
        await update.message.reply_text(f"👤 *THÔNG TIN TÀI KHOẢN*\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n• ID Telegram: `{chat_id}`\n• Số dư tài khoản: *{balance:,}đ*", parse_mode="Markdown")
        return

    # --- LUỒNG NẠP TIỀN ĐIỀN SỐ TIỀN ---
    if text == BTN_DEPOSIT:
        context.user_data["state"] = "waiting_deposit_amount"
        await update.message.reply_text(
            "💳 *NẠP TIỀN TỰ ĐỘNG*\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            "👉 Vui lòng nhập số tiền bạn muốn nạp (Đơn vị: VNĐ).\n"
            "Ví dụ: `50000` hoặc `100000`", 
            reply_markup=ReplyKeyboardMarkup([["🔙 Quay Lại"]], resize_keyboard=True), 
            parse_mode="Markdown"
        )
        return

    if state == "waiting_deposit_amount":
        clean_amount_str = text.replace(".", "").replace(",", "").replace("đ", "").replace("Đ", "").strip()
        if not clean_amount_str.isdigit():
            await update.message.reply_text("❌ Số tiền không hợp lệ! Vui lòng chỉ nhập các chữ số:")
            return
            
        amount = int(clean_amount_str)
        if amount < 1000:
            await update.message.reply_text("❌ Số tiền nạp tối thiểu phải từ `1,000đ`.", parse_mode="Markdown")
            return

        context.user_data["deposit_amount"] = amount
        context.user_data["state"] = "waiting_deposit_confirm"
        
        await update.message.reply_text(
            f"❓ *XÁC NHẬN THÔNG TIN NẠP TIỀN*\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"💰 Số tiền muốn nạp: *{amount:,}đ*\n\n"
            f"Bạn có chắc chắn muốn tạo mã QR thanh toán cho số tiền này không?",
            reply_markup=ReplyKeyboardMarkup([["✅ Xác Nhận Tạo Mã QR"], ["🔙 Quay Lại"]], resize_keyboard=True),
            parse_mode="Markdown"
        )
        return

    if state == "waiting_deposit_confirm" and text == "✅ Xác Nhận Tạo Mã QR":
        amount = context.user_data.get("deposit_amount", 0)
        content = f"NAP {chat_id}"
        qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{ACCOUNT_NO}-compact2.jpg?amount={amount}&text={content}&accountName={ACCOUNT_NAME}"

        # 1. Gửi ảnh QR cho khách hàng thanh toán
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=qr_url,
            caption=(
                f"💳 *MÃ QR NẠP TIỀN ĐÃ ĐƯỢC KHỞI TẠO*\n"
                f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                f"🏦 Ngân hàng: {BANK_ID.upper()}\n"
                f"🔢 Số tài khoản: `{ACCOUNT_NO}`\n"
                f"💰 Số tiền cần chuyển: `{amount:,}đ`\n"
                f"🔤 Nội dung chuyển khoản: `{content}`\n\n"
                f"📱 Mở ứng dụng ngân hàng và quét mã QR phía trên để thanh toán nhanh.\n\n"
                f"⏳ Yêu cầu nạp đã được gửi lên hệ thống quản trị để kiểm tra duyệt tiền!"
            ),
            reply_markup=user_markup,
            parse_mode="Markdown"
        )

        # 2. GỬI THÔNG BÁO KÈM NÚT BẤM CHO ADMIN DUYỆT THỦ CÔNG
        admin_buttons = [
            [
                InlineKeyboardButton(text="🟢 Duyệt Ngay", callback_data=f"admin_approve_{chat_id}_{amount}"),
                InlineKeyboardButton(text="🔴 Hủy Đơn", callback_data=f"admin_reject_{chat_id}")
            ]
        ]
        admin_markup_inline = InlineKeyboardMarkup(admin_buttons)
        
        admin_alert_msg = (
            f"🔔 *CÓ YÊU CẦU NẠP TIỀN MỚI*\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"👤 Khách hàng (ID): `{chat_id}`\n"
            f"💰 Số tiền nạp: *{amount:,}đ*\n"
            f"🔤 Nội dung chuẩn: `NAP {chat_id}`\n\n"
            f"👉 Hãy kiểm tra tài khoản ngân hàng xem tiền đã về chưa rồi ấn nút hành động dưới đây:"
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_alert_msg, reply_markup=admin_markup_inline, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Lỗi gửi tin nhắn duyệt cho Admin: {e}")

        context.user_data.clear()
        return

    # --- CHỨC NĂNG THEO DÕI ĐƠN HÀNG ĐVVC ---
    if text == BTN_TRACK:
        context.user_data["state"] = "waiting_tracking"
        await update.message.reply_text("📥 Vui lòng gửi **Mã vận đơn** bạn muốn kích hoạt theo dõi ngầm:", reply_markup=ReplyKeyboardMarkup([["🔙 Quay Lại"]], resize_keyboard=True), parse_mode="Markdown")
        return

    if state == "waiting_tracking":
        tracking = text.upper().replace(" ", "")
        courier = detect_courier(tracking)
        status = await asyncio.to_thread(query_courier_status, courier, tracking)
        
        order_dict = {"name": f"Đơn hàng {tracking[:5]}", "tracking": tracking, "courier": courier, "last_status": status}
        conn = get_db()
        conn.cursor().execute("INSERT OR REPLACE INTO orders (chat_id, tracking, json_data) VALUES (?, ?, ?)", (chat_id, tracking, json.dumps(order_dict, ensure_ascii=False)))
        conn.commit()
        conn.close()
        
        context.user_data.clear()
        await update.message.reply_text(f"✅ *Đã đưa đơn vào hệ thống giám sát!*\n🚚 Hãng: {courier}\n📦 Trạng thái hiện tại: _{status}_", reply_markup=user_markup, parse_mode="Markdown")
        return

    # --- 🛒 CHỨC NĂNG MUA TÀI KHOẢN ---
    if text == BTN_SHOP:
        conn = get_db()
        categories = conn.cursor().execute("SELECT category, price, COUNT(*) FROM products WHERE is_sold=0 GROUP BY category").fetchall()
        conn.close()
        
        if not categories:
            await update.message.reply_text("Hiện tại kho hàng đang hết sản phẩm, Admin sẽ sớm bổ sung thêm.")
            return
            
        msg = "🛒 *DANH SÁCH TÀI KHOẢN TRONG KHO*\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        inline_buttons = []
        
        for cat, price, count in categories:
            msg += f"• *{cat}* — Giá: `{price:,}đ` (Còn: {count})\n"
            button_text = f"Mua {cat} ({price:,}đ)"
            inline_buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"buy_{cat}")])
            
        msg += "\n👇 *Bấm trực tiếp vào nút dưới đây để mua hàng:* "
        reply_markup_inline = InlineKeyboardMarkup(inline_buttons)
        await update.message.reply_text(msg, reply_markup=reply_markup_inline, parse_mode="Markdown")
        return

    # ================= 🛠️ ADMIN CONTROL PANEL =================
    if chat_id == ADMIN_ID:
        if text == BTN_ADMIN_ADD_MONEY:
            context.user_data["state"] = "admin_adding_money"
            await update.message.reply_text("💵 Nhập thông tin cộng tiền theo mẫu cấu trúc:\n`ID_Telegram  SoTien` \n\nVí dụ: `5475751501 100000`", parse_mode="Markdown")
            return
            
        if state == "admin_adding_money":
            try:
                target_id, money = text.split()
                money = int(money)
                conn = get_db()
                conn.cursor().execute("UPDATE users SET balance = balance + ? WHERE chat_id=?", (money, target_id))
                conn.commit()
                conn.close()
                await context.bot.send_message(chat_id=target_id, text=f"🔔 *Admin đã cộng tiền thủ công vào ví của bạn:* +`{money:,}đ`", parse_mode="Markdown")
                await update.message.reply_text("✅ Đã cộng số dư tài khoản thành công!", reply_markup=admin_markup)
            except:
                await update.message.reply_text("Cú pháp sai, thao tác thất bại.")
            context.user_data.clear()
            return

        if text == BTN_ADMIN_ADD_ACC:
            context.user_data["state"] = "admin_adding_acc"
            await update.message.reply_text("📦 Nhập tài khoản vào kho theo định dạng gạch đứng:\n`TênDanhMục | GiáTiền | TàiKhoản:MậtKhẩu`\n\nVí dụ: `NETFLIX | 50000 | user@netflix.com:abc123`", parse_mode="Markdown")
            return
            
        if state == "admin_adding_acc":
            try:
                parts = text.split("|")
                cat = parts[0].strip()
                price = int(parts[1].strip())
                acc_info = parts[2].strip()
                
                conn = get_db()
                conn.cursor().execute("INSERT INTO products (category, price, account_info) VALUES (?, ?, ?)", (cat, price, acc_info))
                conn.commit()
                conn.close()
                await update.message.reply_text("✅ Đã đẩy tài khoản vào cơ sở dữ liệu kho sản phẩm!", reply_markup=admin_markup)
            except:
                await update.message.reply_text("Cấu trúc chuỗi nhập vào không hợp lệ!")
            context.user_data.clear()
            return

        if text == BTN_ADMIN_STAT:
            conn = get_db()
            u_count = conn.cursor().execute("SELECT COUNT(*) FROM users").fetchone()[0]
            o_count = conn.cursor().execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            p_count = conn.cursor().execute("SELECT COUNT(*) FROM products WHERE is_sold=0").fetchone()[0]
            conn.close()
            await update.message.reply_text(f"📊 *THỐNG KÊ HỆ THỐNG BOT*\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n• Tổng số người dùng: {u_count}\n• Đơn hàng đang giám sát: {o_count}\n• Tài khoản trong kho chưa bán: {p_count}", parse_mode="Markdown")
            return

        if text == BTN_ADMIN_EXIT:
            context.user_data.clear()
            await update.message.reply_text("Đã thoát trình quản lý Admin.", reply_markup=user_markup)
            return

# ================= ⏳ AUTOMATIC QUÉT BIẾN ĐỘNG ĐƠN HÀNG =================

async def auto_check_job(context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT chat_id, tracking, json_data FROM orders")
    rows = cur.fetchall()

    for chat_id, tracking, json_str in rows:
        order_data = json.loads(json_str)
        old_status = order_data.get("last_status")
        courier = order_data.get("courier", "SPX")

        new_status = await asyncio.to_thread(query_courier_status, courier, tracking)
        if not new_status or new_status == old_status:
            continue

        order_data["last_status"] = new_status
        cur.execute("UPDATE orders SET json_data=? WHERE chat_id=? AND tracking=?", (json.dumps(order_data, ensure_ascii=False), chat_id, tracking))
        conn.commit()

        status_lower = new_status.lower()
        if any(x in status_lower for x in ["thành công", "đã giao", "delivered"]):
            icon, title = "🟢", "ĐƠN HÀNG GIAO THÀNH CÔNG"
        elif any(x in status_lower for x in ["phân loại", "đang giao", "kho", "trạm", "hub", "bưu cục"]):
            icon, title = "🚚", "ĐƠN HÀNG CÓ CẬP NHẬT LỘ TRÌNH MỚI"
        else:
            icon, title = "🔄", "BIẾN ĐỘNG TRẠNG THÁI MỚI"

        alert_msg = (
            f"{icon} *{title}* {icon}\n"
            f"📦 *Mã vận đơn:* `{tracking}` ({courier})\n"
            f"📍 *Chi tiết lộ trình:* _{new_status}_"
        )
        try: await context.bot.send_message(chat_id=chat_id, text=alert_msg, parse_mode="Markdown")
        except: pass
        await asyncio.sleep(1)
    conn.close()

# ================= ⚡ CHẠY HỆ THỐNG BOT =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    register_user(chat_id)
    await update.message.reply_text("🤖 **HỆ THỐNG BOT TRACKING & SHOP PREMIUM AUTOMATION**", reply_markup=user_markup, parse_mode="Markdown")

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) == ADMIN_ID:
        await update.message.reply_text("🛠️ Kích hoạt Menu quản trị dành cho Admin.", reply_markup=admin_markup)

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CallbackQueryHandler(handle_callback)) 
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    if app.job_queue:
        app.job_queue.run_repeating(auto_check_job, interval=600, first=5)
        app.job_queue.run_repeating(auto_banking_job, interval=30, first=10)
        
    print("🚀 Bot Đa Năng [Admin Approve Integrated] Hoàn Chỉnh Đang Hoạt Động!")
    app.run_polling()

if __name__ == "__main__":
    main()

import os
import sqlite3
import telebot
import requests
import urllib3
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# Tắt cảnh báo SSL
urllib3.disable_warnings()

# ==================== CẤU HÌNH HỆ THỐNG ====================
===== CẤU HÌNH CƠ SỞ DỮ LIỆU SQLITE ====================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Bảng người dùng
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            balance INTEGER DEFAULT 0
        )
    ''')
    
    # Bảng Voucher hệ thống
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vouchers (
            code TEXT PRIMARY KEY,
            discount_amount INTEGER DEFAULT 0
        )
    ''')
    
    # Bảng đơn hàng đặt hộ
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_name TEXT,
            price INTEGER,
            voucher_code TEXT,
            voucher_discount INTEGER,
            tien_cod INTEGER,
            link TEXT,
            status TEXT DEFAULT 'Chờ duyệt',
            ma_van_don TEXT DEFAULT '',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tự động cập nhật thêm cột ma_van_don nếu database cũ đã tồn tại mà chưa có cột này
    try:
        cursor.execute("ALTER TABLE orders ADD COLUMN ma_van_don TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
        
    # Bảng lịch sử nạp tiền chung
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

# --- HÀM TƯƠNG TÁC DATABASE ---
def db_get_user(user_id, full_name=""):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        cursor.execute("INSERT INTO users (user_id, full_name, balance) VALUES (?, ?, ?)", (user_id, full_name, 0))
        conn.commit()
        balance = 0
    else:
        balance = row[0]
    conn.close()
    return {"balance": balance}

def db_update_balance(user_id, amount):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def db_add_history(user_id, content):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO history (user_id, content) VALUES (?, ?)", (user_id, content))
    conn.commit()
    conn.close()

def db_get_history(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT content, timestamp FROM history WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [f"[{row[1]}] {row[0]}" for row in rows]

# --- QUẢN LÝ VOUCHER ---
def db_add_voucher(code, discount):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO vouchers (code, discount_amount) VALUES (?, ?)", (code.upper(), discount))
        conn.commit()
        s = True
    except:
        cursor.execute("UPDATE vouchers SET discount_amount = ? WHERE code = ?", (discount, code.upper()))
        conn.commit()
        s = True
    conn.close()
    return s

def db_get_vouchers():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT code, discount_amount FROM vouchers")
    rows = cursor.fetchall()
    conn.close()
    return [{"code": r[0], "discount": r[1]} for r in rows]

def db_delete_voucher(code):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM vouchers WHERE code = ?", (code.upper(),))
    conn.commit()
    conn.close()

# --- QUẢN LÝ ĐƠN HÀNG ---
def db_create_order(user_id, prod_name, price, vc_code, vc_discount, tien_cod, link):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO orders (user_id, product_name, price, voucher_code, voucher_discount, tien_cod, link) 
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, prod_name, price, vc_code, vc_discount, tien_cod, link))
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return order_id

def db_get_user_orders(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT order_id, product_name, tien_cod, status, ma_van_don FROM orders WHERE user_id = ? ORDER BY order_id DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def db_get_order_details(order_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT order_id, user_id, product_name, price, voucher_code, voucher_discount, tien_cod, link, status, ma_van_don FROM orders WHERE order_id = ?", (order_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def db_update_order_status(order_id, status):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id))
    conn.commit()
    conn.close()

def db_update_order_tracking(order_id, tracking_code):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET ma_van_don = ? WHERE order_id = ?", (tracking_code, order_id))
    conn.commit()
    conn.close()

# MOD: Hàm lấy thông số thống kê nâng cao từ Database
def db_get_stats():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. Tổng số thành viên
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    # 2. Đếm số lượng đơn theo từng trạng thái
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status='Chờ duyệt'")
    pending_orders = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status='Đang mua'")
    processing_orders = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status='Đã mua thành công'")
    success_orders = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status='Bị hủy'")
    canceled_orders = cursor.fetchone()[0]
    
    # 3. Tính tổng số đơn hàng
    total_orders = pending_orders + processing_orders + success_orders + canceled_orders
    
    # 4. Tính tổng doanh thu dịch vụ (chỉ tính trên đơn Đã mua thành công)
    revenue_fee = success_orders * PHI_DAT_HO
    
    conn.close()
    return total_users, total_orders, pending_orders, processing_orders, success_orders, canceled_orders, revenue_fee

# ==================== GIAO DIỆN MENU BÀN PHÍM CHÍNH ====================
def main_menu_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("💰 Nạp Tiền"), KeyboardButton("🛒 Mua Hàng"),
        KeyboardButton("📞 Hỗ Trợ"), KeyboardButton("📜 Lịch Sử"),
        KeyboardButton("👤 Mục Tôi")
    )
    return markup

# ==================== XỬ LÝ LỆNH /START & /ADMIN ====================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    db_get_user(user_id, message.from_user.full_name)
    USER_STATES.pop(user_id, None)
    
    # Xử lý tránh lỗi ký tự đặc biệt
    user_name = message.from_user.full_name.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
    
    # Thay "https://youtube.com/..." bằng link thật của bạn ở bên dưới nhé
    welcome_text = (
        f"👋 *Chào mừng {user_name} đến với Hệ Thống Đặt Hộ\\!*\n\n"
        f"🛍️ *Mua sắm thả ga – Không lo thủ tục*\n"
        f"Hệ thống hỗ trợ bạn đặt hàng tự động từ các sàn TMĐT về tận tay một cách nhanh chóng\\.\n\n"
        f"📺 *Bạn là người mới?* "
        f"[▶️ Xem Video Hướng Dẫn Tại Đây](https://youtube.com/link-video-cua-ban)\n\n"
        f"👇 *Vui lòng chọn chức năng dưới bàn phím để bắt đầu:*"
    )
    
    bot.send_message(
        chat_id=message.chat.id, 
        text=welcome_text, 
        parse_mode="MarkdownV2", 
        reply_markup=main_menu_keyboard()
    )

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Bạn không phải Quản trị viên.")
        return
    show_admin_main(message.chat.id)

# MOD: Hàm hiển thị bảng thống kê doanh thu và đơn hàng chi tiết cho Admin
def show_admin_main(chat_id):
    total_u, total_o, pending_o, proc_o, succ_o, cancel_o, revenue = db_get_stats()
    
    thong_ke_text = f"""🛠️ <b>HỆ THỐNG QUẢN TRỊ ADMIN PANEL</b>
----------------------------------
👥 <b>Khách hàng:</b> <code>{total_u}</code> thành viên

📊 <b>THỐNG KÊ ĐƠN HÀNG:</b>
📦 Tổng số đơn phát sinh: <b>{total_o}</b> đơn
⏳ Đơn chờ duyệt: <code>{pending_o}</code>
🔄 Đơn đang mua: <code>{proc_o}</code>
✅ Đơn thành công: <code>{succ_o}</code>
❌ Đơn bị hủy: <code>{cancel_o}</code>

💰 <b>THỐNG KÊ DOANH THU:</b>
💵 Phí dịch vụ thu được: <b>{revenue:,} VNĐ</b>
----------------------------------
<i>Nhấn vào các nút bên dưới để quản lý hệ thống:</i>"""

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(f"📦 Đơn Hàng Đang Chờ ({pending_o})", callback_data="adm_manage_orders"),
        InlineKeyboardButton("🎟️ Quản Lý Mã Voucher Bot", callback_data="adm_manage_vouchers"),
        InlineKeyboardButton("❌ Đóng Menu Admin", callback_data="adm_close")
    )
    bot.send_message(chat_id, thong_ke_text, parse_mode="HTML", reply_markup=markup)

# ==================== ĐIỀU HƯỚNG MENU BÀN PHÍM CHÍNH ====================
@bot.message_handler(func=lambda message: message.text in ["💰 Nạp Tiền", "🛒 Mua Hàng", "📞 Hỗ Trợ", "📜 Lịch Sử", "👤 Mục Tôi"])
def handle_menu_click(message):
    user_id = message.from_user.id
    db_get_user(user_id, message.from_user.full_name)
    USER_STATES.pop(user_id, None)

    if message.text == "💰 Nạp Tiền":
        msg = bot.send_message(message.chat.id, "💰 Nhập số tiền bạn muốn nạp vào ví Bot:")
        bot.register_next_step_handler(msg, xu_ly_nap_tien)
    elif message.text == "🛒 Mua Hàng":
        USER_ORDERS[user_id] = {}
        USER_STATES[user_id] = "WAIT_LINK"
        bot.send_message(message.chat.id, "📦 <b>Vui lòng gửi Link Shopee sản phẩm bạn muốn đặt hộ:</b>", parse_mode="HTML")
    elif message.text == "📞 Hỗ Trợ":
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("💬 Zalo Admin", url="https://zalo.me/0383835032"), InlineKeyboardButton("✈️ Telegram Admin", url="https://t.me/tranbadoan"))
        bot.send_message(message.chat.id, "Mọi vấn đề cần hỗ trợ vui lòng liên hệ Admin:", reply_markup=markup)
    elif message.text == "📜 Lịch Sử":
        orders = db_get_user_orders(user_id)
        history_list = db_get_history(user_id)
        
        text = "📦 <b>TRẠNG THÁI ĐƠN HÀNG ĐẶT HỘ CỦA BẠN:</b>\n"
        if not orders:
            text += "<i>Chưa có đơn đặt hàng nào.</i>\n"
        else:
            for o in orders[:5]:
                mvd_text = f" | 🚚 MVĐ: <code>{o[4]}</code>" if o[4] else ""
                text += f"• <b>Đơn #{o[0]}:</b> {o[1][:15]}... | COD: <b>{o[2]:,}đ</b> ➡️ Trạng thái: <b>[{o[3]}]</b>{mvd_text}\n"
                
        text += "\n💸 <b>LỊCH SỬ GIAO DỊCH VÍ:</b>\n"
        if not history_list:
            text += "<i>Chưa có biến động số dư.</i>"
        else:
            text += "\n".join(history_list)
            
        bot.send_message(message.chat.id, text, parse_mode="HTML")
    elif message.text == "👤 Mục Tôi":
        user_info = db_get_user(user_id)
        bot.send_message(message.chat.id, f"👤 <b>THÔNG TIN TÀI KHOẢN</b>\n\n🆔 ID: <code>{user_id}</code>\n💰 Số dư ví cá nhân trên Bot: <b>{user_info['balance']:,} VND</b>\n<i>(Phí đặt hộ sẽ trừ 30,000 VNĐ từ nguồn ví này)</i>", parse_mode="HTML")

# ==================== LUỒNG NẠP TIỀN TỰ ĐỘNG ====================
def xu_ly_nap_tien(message):
    user_id = message.from_user.id
    if message.text in ["💰 Nạp Tiền", "🛒 Mua Hàng", "📞 Hỗ Trợ", "📜 Lịch Sử", "👤 Mục Tôi"]:
        handle_menu_click(message)
        return
    if not message.text.strip().isdigit():
        msg = bot.reply_to(message, "❌ Số tiền không hợp lệ, vui lòng nhập lại:")
        bot.register_next_step_handler(msg, xu_ly_nap_tien)
        return
    amount = int(message.text.strip())
    noi_dung_ck = f"NAP {user_id}"
    vietqr_url = f"https://img.vietqr.io/image/{BANK_NAME}-{BANK_STK}-qr_only.jpg?amount={amount}&addInfo={noi_dung_ck}&accountName={BANK_OWNER}"
    bot.send_photo(message.chat.id, vietqr_url, caption=f"🏦 <b>THÔNG TIN CHUYỂN KHOẢN</b>\n\n🔹 Ngân hàng: {BANK_NAME}\n🔹 STK: <code>{BANK_STK}</code>\n🔹 Tên Tài Khoản: {BANK_OWNER}\n🔹 Số tiền: {amount:,} VND\n🔹 Nội dung: <code>{noi_dung_ck}</code>", parse_mode="HTML")
    
    admin_markup = InlineKeyboardMarkup()
    admin_markup.add(InlineKeyboardButton("✅ Duyệt", callback_data=f"dep_duyet_{user_id}_{amount}"), InlineKeyboardButton("❌ Hủy", callback_data=f"dep_huy_{user_id}"))
    bot.send_message(ADMIN_ID, f"🔔 Yêu cầu nạp tiền từ: {message.from_user.full_name} ({user_id}) - Số tiền: {amount:,} VND", reply_markup=admin_markup)

# ==================== LUỒNG ĐẶT HÀNG TUẦN TỰ ====================
@bot.message_handler(func=lambda message: message.from_user.id in USER_STATES)
def handle_shopping_steps(message):
    user_id = message.from_user.id
    state = USER_STATES[user_id]
    if message.text in ["💰 Nạp Tiền", "🛒 Mua Hàng", "📞 Hỗ Trợ", "📜 Lịch Sử", "👤 Mục Tôi"]:
        handle_menu_click(message)
        return

    if state == "WAIT_LINK":
        wait = bot.reply_to(message, "🔍 Đang check giá sản phẩm Shopee qua API...")
        try:
            r = requests.get(message.text.strip(), allow_redirects=True, headers=HEADERS, timeout=20, verify=False)
            api = requests.get("https://data.addlivetag.com/product-data/product-data.php", params={"url": r.url}, headers=HEADERS, timeout=30, verify=False).json()
            if api.get("status") != "success":
                bot.edit_message_text("❌ Lỗi không lấy được dữ liệu giá từ link này. Vui lòng thử lại link khác:", message.chat.id, wait.message_id)
                return
            p = api["productInfo"]
            USER_ORDERS[user_id] = {"product_name": p.get("productName", "Không rõ"), "price": p.get("price", 0), "image": p.get("imageUrl", ""), "link": r.url}
            bot.delete_message(message.chat.id, wait.message_id)
            
            USER_STATES[user_id] = "WAIT_VOUCHER_CHOICE"
            v_list = db_get_vouchers()
            markup = InlineKeyboardMarkup(row_width=1)
            for v in v_list:
                markup.add(InlineKeyboardButton(f"🎟️ Mã {v['code']} (Giảm {v['discount']:,}đ)", callback_data=f"sel_vc_{v['code']}_{v['discount']}"))
            markup.add(InlineKeyboardButton("⏩ Không sử dụng mã Voucher", callback_data="sel_vc_NONE_0"))
            
            bot.send_message(message.chat.id, f"🔍 <b>SẢN PHẨM:</b> {USER_ORDERS[user_id]['product_name']}\n💰 Giá gốc Shopee: <b>{USER_ORDERS[user_id]['price']:,} VNĐ</b>\n\n👉 <b>Vui lòng CHỌN Voucher giảm giá hệ thống dưới đây:</b>", parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            bot.edit_message_text("❌ Lỗi kết nối API Shopee. Vui lòng thử lại:", message.chat.id, wait.message_id)

    elif state == "WAIT_ADDRESS":
        USER_ORDERS[user_id]["address"] = message.text.strip()
        USER_STATES[user_id] = "WAIT_PHONE"
        bot.send_message(message.chat.id, "📞 <b>Vui lòng nhập Số Điện Thoại người nhận hàng:</b>", parse_mode="HTML")

    elif state == "WAIT_PHONE":
        USER_ORDERS[user_id]["phone"] = message.text.strip()
        USER_STATES[user_id] = "WAIT_NAME"
        bot.send_message(message.chat.id, "👤 <b>Vui lòng nhập Họ tên người nhận hàng:</b>", parse_mode="HTML")

    elif state == "WAIT_NAME":
        USER_ORDERS[user_id]["name"] = message.text.strip()
        order = USER_ORDERS[user_id]
        
        gia_shopee = order["price"]
        voucher_discount = order["voucher_discount"]
        
        tien_cod_thuc_te = gia_shopee - voucher_discount
        if tien_cod_thuc_te < 0: tien_cod_thuc_te = 0
        USER_ORDERS[user_id]["tien_cod"] = tien_cod_thuc_te

        confirm_markup = InlineKeyboardMarkup()
        confirm_markup.add(
            InlineKeyboardButton("✅ Chắc Chắn Mua", callback_data="confirm_order_yes"),
            InlineKeyboardButton("❌ Hủy Đơn", callback_data="confirm_order_no")
        )

        preview_text = f"""📊 <b>BẢNG XÁC NHẬN ĐƠN ĐẶT HỘ SHOPEE</b>

📦 Sản phẩm: <a href="{order['link']}">{order['product_name']}</a>
💰 Giá trên Shopee: <code>{gia_shopee:,}</code> VNĐ
🎟️ Voucher đã chọn: <code>-{voucher_discount:,}</code> VNĐ ({order['voucher_code']})
----------------------------------
💳 <b>Phí dịch vụ trừ vào Ví Bot:</b> <code>-{PHI_DAT_HO:,}</code> VNĐ
🚚 <b>Số tiền phải trả khi nhận hàng (COD):</b> <b>{tien_cod_thuc_te:,} VNĐ</b>

📍 Người nhận: {order['name']} - {order['phone']}
🏠 Địa chỉ: {order['address']}

⚠️ Hệ thống chỉ trừ đúng {PHI_DAT_HO:,}đ tiền phí đặt hộ trong Ví Bot sau khi bạn bấm xác nhận."""

        bot.send_message(message.chat.id, preview_text, parse_mode="HTML", reply_markup=confirm_markup, disable_web_page_preview=True)
        USER_STATES.pop(user_id, None)

# ==================== CALLBACK XỬ LÝ TOÀN BỘ NÚT BẤM (INLINE) ====================
@bot.callback_query_handler(func=lambda call: True)
def handle_all_callbacks(call):
    data = call.data
    user_id = call.from_user.id
    
    # --- 1. DUYỆT TIỀN NẠP ---
    if data.startswith("dep_"):
        bot.answer_callback_query(call.id)
        info = data.split("_")
        action, target_id = info[1], int(info[2])
        if action == "duyet":
            amount = int(info[3])
            db_update_balance(target_id, amount)
            db_add_history(target_id, f"Nạp tiền hệ thống: +{amount:,}đ")
            bot.edit_message_text(f"✅ Đã duyệt nạp {amount:,}đ cho ID {target_id}", call.message.chat.id, call.message.message_id)
            try: bot.send_message(target_id, f"🎉 Ví Bot của bạn đã được cộng +{amount:,} VND thành công!")
            except: pass
        elif action == "huy":
            bot.edit_message_text(f"❌ Đã từ chối lệnh nạp tiền của ID {target_id}", call.message.chat.id, call.message.message_id)

    # --- 2. KHÁCH CHỌN MÃ VOUCHER ---
    elif data.startswith("sel_vc_"):
        bot.answer_callback_query(call.id)
        info = data.split("_")
        code, discount = info[2], int(info[3])
        if user_id in USER_ORDERS:
            USER_ORDERS[user_id]["voucher_code"] = code
            USER_ORDERS[user_id]["voucher_discount"] = discount
            USER_STATES[user_id] = "WAIT_ADDRESS"
            bot.edit_message_text(f"✅ Đã chọn Voucher: <b>{code}</b> (Giảm {discount:,}đ)\n\n📍 <b>Vui lòng nhập Địa Chỉ cụ thể nhận hàng:</b>", call.message.chat.id, call.message.message_id, parse_mode="HTML")

    # --- 3. XÁC NHẬN MUA ĐƠN HÀNG ---
    elif data.startswith("confirm_order_"):
        bot.answer_callback_query(call.id)
        action = data.replace("confirm_order_", "")
        if action == "no":
            USER_ORDERS.pop(user_id, None)
            bot.edit_message_text("❌ Đơn đặt hộ đã được hủy bỏ.", call.message.chat.id, call.message.message_id)
            return
        if action == "yes":
            if user_id not in USER_ORDERS: return
            user_info = db_get_user(user_id)
            if user_info["balance"] < PHI_DAT_HO:
                bot.edit_message_text(f"❌ <b>Thất bại!</b> Ví Bot của bạn có {user_info['balance']:,}đ, không đủ {PHI_DAT_HO:,}đ phí dịch vụ. Hãy nạp tiền thêm.", call.message.chat.id, call.message.message_id, parse_mode="HTML")
                USER_ORDERS.pop(user_id, None)
                return
            
            order = USER_ORDERS[user_id]
            db_update_balance(user_id, -PHI_DAT_HO)
            order_id = db_create_order(user_id, order["product_name"], order["price"], order["voucher_code"], order["voucher_discount"], order["tien_cod"], order["link"])
            db_add_history(user_id, f"Đặt hộ đơn #{order_id} | Phí trừ ví: -{PHI_DAT_HO:,}đ")

            bot.edit_message_text(f"🎉 <b>Đặt đơn hộ thành công! (Mã đơn #{order_id})</b>\n\n💰 Đã trừ cọc ví bot: -{PHI_DAT_HO:,}đ\n🚚 Shipper thu COD lúc nhận hàng: <b>{order['tien_cod']:,} VNĐ</b>\n\n<i>Đơn hàng đang chờ Admin kiểm tra và mua hộ trên hệ thống!</i>", call.message.chat.id, call.message.message_id, parse_mode="HTML")
            
            send_admin_order_notification(order_id)
            USER_ORDERS.pop(user_id, None)

    # --- 4. ADMIN PANEL: XỬ LÝ ĐIỀU HƯỚNG ---
    elif data == "adm_close":
        bot.answer_callback_query(call.id)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        
    elif data == "adm_manage_vouchers":
        bot.answer_callback_query(call.id)
        show_admin_vouchers(call.message.chat.id, call.message.message_id)
        
    elif data == "adm_add_vc_btn":
        bot.answer_callback_query(call.id)
        ADMIN_STATES[user_id] = "INPUT_VOUCHER"
        bot.send_message(call.message.chat.id, "📝 Vui lòng nhập thông tin Voucher theo cú pháp:\n`TENMA SO_TIEN_GIAM`\nVí dụ: `GIAM50K 50000`", parse_mode="Markdown")

    elif data.startswith("adm_del_vc_"):
        bot.answer_callback_query(call.id)
        vc_code = data.replace("adm_del_vc_", "")
        db_delete_voucher(vc_code)
        show_admin_vouchers(call.message.chat.id, call.message.message_id)

    elif data == "adm_manage_orders":
        bot.answer_callback_query(call.id)
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT order_id, product_name FROM orders WHERE status='Chờ duyệt' ORDER BY order_id DESC LIMIT 5")
        rows = cursor.fetchall()
        conn.close()
        
        markup = InlineKeyboardMarkup()
        if not rows:
            markup.add(InlineKeyboardButton("⬅️ Quay Lại", callback_data="adm_back_main"))
            bot.edit_message_text("🙌 Hiện tại không có đơn hàng nào đang ở trạng thái Chờ Duyệt.", call.message.chat.id, call.message.message_id, reply_markup=markup)
        else:
            for r in rows:
                markup.add(InlineKeyboardButton(f"📦 Đơn #{r[0]} - {r[1][:15]}...", callback_data=f"adm_view_ord_{r[0]}"))
            markup.add(InlineKeyboardButton("⬅️ Quay Lại", callback_data="adm_back_main"))
            bot.edit_message_text("📋 Danh sách các đơn đặt hộ đang chờ xử lý:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "adm_back_main":
        bot.answer_callback_query(call.id)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_admin_main(call.message.chat.id)

    # --- 5. ADMIN XEM CHI TIẾT & SET TRẠNG THÁI ---
    elif data.startswith("adm_view_ord_"):
        bot.answer_callback_query(call.id)
        ord_id = int(data.replace("adm_view_ord_", ""))
        show_admin_order_panel(call.message.chat.id, ord_id, call.message.message_id)

    elif data.startswith("adm_set_status_"):
        bot.answer_callback_query(call.id)
        info = data.split("_")
        status_text = info[3] 
        ord_id = int(info[4])
        
        if status_text == "Đã mua thành công":
            ADMIN_STATES[user_id] = f"WAIT_TRACKING_CODE_{ord_id}"
            bot.send_message(call.message.chat.id, f"🚚 <b>Vui lòng gửi Mã vận đơn (hoặc mã vận chuyển Shopee) cho Đơn hàng #{ord_id}:</b>", parse_mode="HTML")
            return
            
        db_update_order_status(ord_id, status_text)
        ord_details = db_get_order_details(ord_id)
        buyer_id = ord_details[1]
        
        try:
            bot.send_message(buyer_id, f"🔔 <b>THÔNG BÁO CẬP NHẬT ĐƠN HÀNG ĐẶT HỘ</b>\n\n📦 Đơn hàng <b>#{ord_id}</b> của bạn đã được Admin chuyển sang trạng thái: <b>[{status_text}]</b>", parse_mode="HTML")
        except: pass
        show_admin_order_panel(call.message.chat.id, ord_id, call.message.message_id)

# --- CÁC HÀM PHỤ TRỢ INTERFACE ADMIN ---
def show_admin_vouchers(chat_id, msg_id):
    v_list = db_get_vouchers()
    markup = InlineKeyboardMarkup(row_width=1)
    text = "🎟️ <b>DANH SÁCH VOUCHER TRÊN BOT:</b>\n\n"
    if not v_list:
        text += "<i>Hiện hệ thống chưa tạo mã voucher giảm giá nào.</i>"
    else:
        for v in v_list:
            text += f"• Mã: <code>{v['code']}</code> | Giảm: <b>{v['discount']:,}đ</b>\n"
            markup.add(InlineKeyboardButton(f"❌ Xóa mã {v['code']}", callback_data=f"adm_del_vc_{v['code']}"))
            
    markup.add(InlineKeyboardButton("➕ Thêm Voucher Mới", callback_data="adm_add_vc_btn"))
    markup.add(InlineKeyboardButton("⬅️ Quay Lại Menu Chính", callback_data="adm_back_main"))
    bot.edit_message_text(text, chat_id, msg_id, parse_mode="HTML", reply_markup=markup)

def send_admin_order_notification(order_id):
    ord = db_get_order_details(order_id)
    admin_text = f"🛍️ <b>ĐƠN ĐẶT HỘ MỚI PHÁT SINH (#{ord[0]})</b>\n\n👤 Khách hàng ID: <code>{ord[1]}</code>\n📦 Sản phẩm: <a href='{ord[7]}'>{ord[2]}</a>\n💰 Giá Shopee: {ord[3]:,}đ\n🎟️ Voucher: {ord[4]} (-{ord[5]:,}đ)\n🚚 <b>CÀI THU COD SHIPPER: {ord[6]:,} VNĐ</b>\n\n🚦 Trạng thái hiện tại: <b>[{ord[8]}]</b>"
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("⚙️ Quản lý & Cài đặt trạng thái đơn này", callback_data=f"adm_view_ord_{ord[0]}"))
    bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML", reply_markup=markup)

def show_admin_order_panel(chat_id, order_id, msg_id):
    ord = db_get_order_details(order_id)
    mvd_info = ord[9] if ord[9] else "Chưa cập nhật"
    text = f"⚙️ <b>QUẢN LÝ ĐƠN HÀNG ĐẶT HỘ #{ord[0]}</b>\n\n👤 Khách hàng: ID <code>{ord[1]}</code>\n📦 Tên SP: {ord[2]}\n💰 Giá Shopee: {ord[3]:,}đ\n🎟️ Voucher: {ord[4]} (-{ord[5]:,}đ)\n🚚 <b>TIỀN THU COD: {ord[6]:,} VNĐ</b>\n🌐 Link: <a href='{ord[7]}'>Bấm để mở Shopee</a>\n🚚 <b>Mã vận đơn:</b> <code>{mvd_info}</code>\n\n🚦 Trạng thái hiện tại: <b>[{ord[8]}]</b>\n\n👉 <i>Hãy nhấn các nút bên dưới để thiết lập trạng thái đơn hàng bằng tay:</i>"
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("⏳ Set trạng thái: ĐANG MUA", callback_data=f"adm_set_status_Đang mua_{ord[0]}"),
        InlineKeyboardButton("✅ Set trạng thái: THÀNH CÔNG (+ Nhập MVĐ)", callback_data=f"adm_set_status_Đã mua thành công_{ord[0]}"),
        InlineKeyboardButton("❌ Set trạng thái: HỦY ĐƠN", callback_data=f"adm_set_status_Bị hủy_{ord[0]}"),
        InlineKeyboardButton("⬅️ Quay lại danh sách đơn", callback_data="adm_manage_orders")
    )
    bot.edit_message_text(text, chat_id, msg_id, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)

# --- XỬ LÝ NHẬP LIỆU TEXT TỪ ADMIN ---
@bot.message_handler(func=lambda message: message.from_user.id == ADMIN_ID and message.from_user.id in ADMIN_STATES)
def handle_admin_inputs(message):
    user_id = message.from_user.id
    state = ADMIN_STATES[user_id]
    
    if state.startswith("WAIT_TRACKING_CODE_"):
        ord_id = int(state.replace("WAIT_TRACKING_CODE_", ""))
        tracking_code = message.text.strip()
        
        db_update_order_status(ord_id, "Đã mua thành công")
        db_update_order_tracking(ord_id, tracking_code)
        ADMIN_STATES.pop(user_id, None)
        
        ord_details = db_get_order_details(ord_id)
        buyer_id = ord_details[1]
        
        bot.send_message(message.chat.id, f"✅ Đã cập nhật trạng thái đơn thành công & Lưu Mã vận đơn: <code>{tracking_code}</code> cho đơn #{ord_id}!", parse_mode="HTML")
        
        try:
            bot.send_message(buyer_id, f"🎉 <b>THÔNG BÁO: ĐƠN ĐẶT HỘ ĐÃ ĐƯỢC MUA THÀNH CÔNG</b>\n\n📦 Đơn hàng <b>#{ord_id}</b> của bạn đã được Admin mua hộ hoàn tất.\n🚚 Mã vận đơn: <code>{tracking_code}</code>\n<i>(Bạn có thể sao chép mã này để kiểm tra hành trình đơn hàng trên Shopee hoặc trong Bot @checkmadonbot)</i>", parse_mode="HTML")
        except: pass
        
        show_admin_main(message.chat.id)
        return

    if state == "INPUT_VOUCHER":
        text_split = message.text.split()
        if len(text_split) < 2 or not text_split[1].isdigit():
            bot.reply_to(message, "❌ Cú pháp sai, hãy nhập lại (Ví dụ: `GIAM30K 30000`):", parse_mode="Markdown")
            return
            
        vc_code = text_split[0].upper().strip()
        discount = int(text_split[1].strip())
        
        db_add_voucher(vc_code, discount)
        ADMIN_STATES.pop(user_id, None)
        bot.send_message(message.chat.id, f"✅ Đã lưu Voucher <code>{vc_code}</code> giảm <b>{discount:,}đ</b> vào cơ sở dữ liệu Bot thành công!", parse_mode="HTML")
        show_admin_main(message.chat.id)

# ==================== KHỞI CHẠY HỆ THỐNG ====================
if __name__ == "__main__":
    init_db()
    print("🗄️ Khởi tạo Hệ thống DB Đặt Hộ đa trạng thái & Admin Panel thành công!")
    print("🤖 Bot đang chạy hoàn hảo...")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)

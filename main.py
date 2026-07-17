import os
import sys
import logging
import time
import json
import threading
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

from config import Config
from database import Database

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize database
db = Database(Config.DATABASE_PATH)

# Initialize bot
bot = telebot.TeleBot(Config.BOT_TOKEN, parse_mode='HTML')

# Constants
ADMIN_IDS = Config.ADMIN_IDS
WELCOME_IMAGE = db.get_setting('welcome_image') or Config.DEFAULT_WELCOME_IMAGE
WELCOME_TEXT = db.get_setting('welcome_text') or Config.DEFAULT_WELCOME_TEXT
QR_CODE = db.get_setting('qr_code') or Config.DEFAULT_QR
UPI_ID = db.get_setting('upi_id') or Config.DEFAULT_UPI
DELIVERY_LINK = db.get_setting('delivery_link') or Config.DEFAULT_DELIVERY_LINK

# User data storage for temporary states
user_data = {}

# Bot running flag
bot_running = True

# ==================== HTTP Server for Health Checks ====================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        elif self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Bot Running')
        else:
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Not Found')
    
    def log_message(self, format, *args):
        # Suppress HTTP server logs to keep console clean
        pass

def run_http_server():
    """Run HTTP server for health checks on Railway port"""
    port = Config.PORT
    
    # Try multiple ports if needed
    ports_to_try = [port, 8080, 8000, 3000]
    
    for try_port in ports_to_try:
        try:
            server = HTTPServer(('0.0.0.0', try_port), HealthCheckHandler)
            logger.info(f"HTTP server started successfully on port {try_port}")
            server.serve_forever()
            break
        except OSError as e:
            logger.warning(f"Could not bind to port {try_port}: {e}")
            continue
        except Exception as e:
            logger.error(f"Error starting HTTP server on port {try_port}: {e}")
            continue
    else:
        logger.error("Failed to start HTTP server on any available port")

def run_bot():
    """Run the Telegram bot with auto-reconnect"""
    while bot_running:
        try:
            logger.info("Starting Telegram bot polling...")
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
            if bot_running:
                logger.info("Reconnecting in 5 seconds...")
                time.sleep(5)
            else:
                break

# ==================== Helper Functions ====================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS or db.is_admin(user_id)

def safe_edit_message(chat_id: int, message_id: int, text: str, **kwargs):
    try:
        bot.edit_message_text(text, chat_id, message_id, **kwargs)
    except Exception as e:
        logger.error(f"Error editing message: {e}")

def safe_delete_message(chat_id: int, message_id: int):
    try:
        bot.delete_message(chat_id, message_id)
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

def safe_send_message(chat_id: int, text: str, **kwargs) -> Optional[types.Message]:
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

def safe_send_photo(chat_id: int, photo: str, caption: str = '', **kwargs) -> Optional[types.Message]:
    try:
        return bot.send_photo(chat_id, photo, caption=caption, **kwargs)
    except Exception as e:
        logger.error(f"Error sending photo: {e}")
        return None

def safe_send_video(chat_id: int, video: str, caption: str = '', **kwargs) -> Optional[types.Message]:
    try:
        return bot.send_video(chat_id, video, caption=caption, **kwargs)
    except Exception as e:
        logger.error(f"Error sending video: {e}")
        return None

def safe_send_document(chat_id: int, document: str, caption: str = '', **kwargs) -> Optional[types.Message]:
    try:
        return bot.send_document(chat_id, document, caption=caption, **kwargs)
    except Exception as e:
        logger.error(f"Error sending document: {e}")
        return None

def format_plan_text(plan: Dict[str, Any]) -> str:
    text = f"<b>{plan['name']}</b>\n\n"
    text += f"{plan['description']}\n\n"
    text += f"💰 Price: <b>₹{plan['price']}</b>\n"
    text += f"📅 Validity: <b>{plan['validity_days']} days</b>\n\n"
    
    media_list = json.loads(plan['media_json']) if plan['media_json'] else []
    if media_list:
        text += "📎 <b>Content includes:</b>\n"
        media_counts = {}
        for media in media_list:
            media_type = media['type']
            media_counts[media_type] = media_counts.get(media_type, 0) + 1
        
        for media_type, count in media_counts.items():
            text += f"  • {count} {media_type.title()}\n"
    
    return text

def create_main_keyboard(user_id: int) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("📋 Choose Plan", callback_data="show_plans"),
        types.InlineKeyboardButton("ℹ️ My Subscription", callback_data="my_subscription")
    )
    
    if is_admin(user_id):
        keyboard.add(
            types.InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")
        )
    
    return keyboard

def create_plans_keyboard(plans: List[Dict[str, Any]]) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    
    for plan in plans:
        keyboard.add(
            types.InlineKeyboardButton(f"{plan['name']} - ₹{plan['price']}", 
                                     callback_data=f"view_plan_{plan['plan_id']}")
        )
    
    keyboard.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
    return keyboard

def create_plan_detail_keyboard(plan_id: int, user_id: int) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    
    keyboard.add(
        types.InlineKeyboardButton("🛒 Buy Plan", callback_data=f"buy_plan_{plan_id}")
    )
    
    # Preview media if available
    plan = db.get_plan(plan_id)
    if plan:
        media_list = json.loads(plan['media_json']) if plan['media_json'] else []
        if media_list:
            keyboard.add(
                types.InlineKeyboardButton("📺 Preview Content", callback_data=f"preview_plan_{plan_id}")
            )
    
    keyboard.add(types.InlineKeyboardButton("🔙 Back to Plans", callback_data="show_plans"))
    return keyboard

def create_payment_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("💳 Pay with QR", callback_data="pay_with_qr"),
        types.InlineKeyboardButton("💰 Pay with UPI", callback_data="pay_with_upi"),
        types.InlineKeyboardButton("🔙 Cancel", callback_data="back_to_plans")
    )
    return keyboard

def create_admin_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    
    keyboard.row(
        types.InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
        types.InlineKeyboardButton("👥 Users", callback_data="admin_users")
    )
    
    keyboard.row(
        types.InlineKeyboardButton("📋 Plans", callback_data="admin_plans"),
        types.InlineKeyboardButton("💳 Payments", callback_data="admin_payments")
    )
    
    keyboard.row(
        types.InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings"),
        types.InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")
    )
    
    keyboard.row(
        types.InlineKeyboardButton("💾 Backup", callback_data="admin_backup"),
        types.InlineKeyboardButton("📥 Restore", callback_data="admin_restore")
    )
    
    keyboard.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
    return keyboard

def create_plans_management_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    
    plans = db.get_all_plans()
    if plans:
        for plan in plans:
            keyboard.add(
                types.InlineKeyboardButton(f"📝 {plan['name']}", callback_data=f"admin_edit_plan_{plan['plan_id']}")
            )
    
    keyboard.row(
        types.InlineKeyboardButton("➕ Add Plan", callback_data="admin_add_plan"),
        types.InlineKeyboardButton("🗑️ Delete Plan", callback_data="admin_delete_plan")
    )
    
    keyboard.add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel"))
    return keyboard

def create_payment_management_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    
    pending = db.get_pending_payments()
    if pending:
        for payment in pending:
            user_name = payment.get('username') or payment.get('first_name', 'Unknown')
            keyboard.add(
                types.InlineKeyboardButton(
                    f"🕐 {user_name} - ₹{payment['amount']}", 
                    callback_data=f"admin_view_payment_{payment['payment_id']}"
                )
            )
    else:
        keyboard.add(types.InlineKeyboardButton("✅ No Pending Payments", callback_data="noop"))
    
    keyboard.add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel"))
    return keyboard

def create_settings_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("🖼️ Set Welcome Image", callback_data="admin_set_welcome_image"),
        types.InlineKeyboardButton("📝 Set Welcome Text", callback_data="admin_set_welcome_text"),
        types.InlineKeyboardButton("📱 Set QR Code", callback_data="admin_set_qr"),
        types.InlineKeyboardButton("💰 Set UPI ID", callback_data="admin_set_upi"),
        types.InlineKeyboardButton("🔗 Set Delivery Link", callback_data="admin_set_delivery_link")
    )
    keyboard.add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel"))
    return keyboard

# ==================== Start Command ====================

@bot.message_handler(commands=['start'])
def start_command(message: types.Message):
    user_id = message.from_user.id
    
    # Register user
    db.add_user(
        user_id,
        message.from_user.username or '',
        message.from_user.first_name or '',
        message.from_user.last_name or ''
    )
    
    # Check if admin
    if user_id in ADMIN_IDS:
        db.set_admin(user_id, True)
    
    # Send welcome
    if WELCOME_IMAGE:
        safe_send_photo(
            user_id,
            WELCOME_IMAGE,
            caption=WELCOME_TEXT,
            reply_markup=create_main_keyboard(user_id)
        )
    else:
        safe_send_message(
            user_id,
            WELCOME_TEXT,
            reply_markup=create_main_keyboard(user_id)
        )

# ==================== Callback Query Handler ====================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call: types.CallbackQuery):
    user_id = call.from_user.id
    data = call.data
    
    try:
        # Handle main menu callbacks
        if data == "back_to_main":
            if WELCOME_IMAGE:
                safe_edit_message(
                    call.message.chat.id,
                    call.message.message_id,
                    WELCOME_TEXT,
                    reply_markup=create_main_keyboard(user_id)
                )
                # Re-send image if needed
                if WELCOME_IMAGE:
                    safe_send_photo(user_id, WELCOME_IMAGE, caption=WELCOME_TEXT, 
                                  reply_markup=create_main_keyboard(user_id))
                    safe_delete_message(call.message.chat.id, call.message.message_id)
            else:
                safe_edit_message(
                    call.message.chat.id,
                    call.message.message_id,
                    WELCOME_TEXT,
                    reply_markup=create_main_keyboard(user_id)
                )
            
        elif data == "show_plans":
            show_plans(call)
            
        elif data == "my_subscription":
            show_subscription(call)
            
        elif data.startswith("view_plan_"):
            plan_id = int(data.split("_")[2])
            view_plan(call, plan_id)
            
        elif data.startswith("preview_plan_"):
            plan_id = int(data.split("_")[2])
            preview_plan(call, plan_id)
            
        elif data.startswith("buy_plan_"):
            plan_id = int(data.split("_")[2])
            buy_plan(call, plan_id)
            
        elif data == "pay_with_qr":
            pay_with_qr(call)
            
        elif data == "pay_with_upi":
            pay_with_upi(call)
            
        elif data == "back_to_plans":
            show_plans(call)
            
        # Admin panel callbacks
        elif data == "admin_panel":
            if is_admin(user_id):
                show_admin_panel(call)
            else:
                bot.answer_callback_query(call.id, "Unauthorized access!")
                
        elif data == "admin_stats":
            if is_admin(user_id):
                show_admin_stats(call)
                
        elif data == "admin_users":
            if is_admin(user_id):
                show_admin_users(call)
                
        elif data == "admin_plans":
            if is_admin(user_id):
                show_admin_plans(call)
                
        elif data == "admin_payments":
            if is_admin(user_id):
                show_admin_payments(call)
                
        elif data == "admin_settings":
            if is_admin(user_id):
                show_admin_settings(call)
                
        elif data == "admin_broadcast":
            if is_admin(user_id):
                start_broadcast(call)
                
        elif data == "admin_backup":
            if is_admin(user_id):
                backup_database(call)
                
        elif data == "admin_restore":
            if is_admin(user_id):
                start_restore(call)
                
        elif data.startswith("admin_edit_plan_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[3])
                show_plan_edit(call, plan_id)
                
        elif data == "admin_add_plan":
            if is_admin(user_id):
                start_add_plan(call)
                
        elif data == "admin_delete_plan":
            if is_admin(user_id):
                start_delete_plan(call)
                
        elif data.startswith("admin_delete_plan_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[3])
                confirm_delete_plan(call, plan_id)
                
        elif data.startswith("admin_confirm_delete_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[3])
                delete_plan(call, plan_id)
                
        elif data.startswith("admin_view_payment_"):
            if is_admin(user_id):
                payment_id = int(data.split("_")[3])
                view_payment_detail(call, payment_id)
                
        elif data.startswith("admin_approve_"):
            if is_admin(user_id):
                payment_id = int(data.split("_")[2])
                approve_payment(call, payment_id)
                
        elif data.startswith("admin_reject_"):
            if is_admin(user_id):
                payment_id = int(data.split("_")[2])
                reject_payment(call, payment_id)
                
        elif data == "admin_set_welcome_image":
            if is_admin(user_id):
                set_welcome_image(call)
                
        elif data == "admin_set_welcome_text":
            if is_admin(user_id):
                set_welcome_text(call)
                
        elif data == "admin_set_qr":
            if is_admin(user_id):
                set_qr_code(call)
                
        elif data == "admin_set_upi":
            if is_admin(user_id):
                set_upi_id(call)
                
        elif data == "admin_set_delivery_link":
            if is_admin(user_id):
                set_delivery_link(call)
                
        elif data == "noop":
            bot.answer_callback_query(call.id)
            
        elif data.startswith("plan_edit_name_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[3])
                edit_plan_name(call, plan_id)
                
        elif data.startswith("plan_edit_desc_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[3])
                edit_plan_description(call, plan_id)
                
        elif data.startswith("plan_edit_price_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[3])
                edit_plan_price(call, plan_id)
                
        elif data.startswith("plan_edit_validity_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[3])
                edit_plan_validity(call, plan_id)
                
        elif data.startswith("plan_add_media_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[3])
                add_plan_media(call, plan_id)
                
        elif data.startswith("plan_delete_media_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[3])
                delete_plan_media(call, plan_id)
                
        else:
            bot.answer_callback_query(call.id, "Unknown action!")
            
    except Exception as e:
        logger.error(f"Error handling callback {data}: {e}")
        bot.answer_callback_query(call.id, "An error occurred!")

# ==================== Feature Functions ====================

def show_plans(call: types.CallbackQuery):
    user_id = call.from_user.id
    plans = db.get_all_plans()
    
    if not plans:
        safe_edit_message(
            call.message.chat.id,
            call.message.message_id,
            "❌ No plans available at the moment. Please try again later.",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("🔙 Back", callback_data="back_to_main")
            )
        )
        return
    
    text = "📋 <b>Available Plans</b>\n\n"
    text += "Choose a plan that suits you best:\n\n"
    
    for plan in plans:
        text += f"<b>{plan['name']}</b>\n"
        text += f"💰 ₹{plan['price']} · {plan['validity_days']} days\n"
        text += f"📎 {len(json.loads(plan['media_json']) if plan['media_json'] else [])} items\n\n"
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=create_plans_keyboard(plans)
    )

def view_plan(call: types.CallbackQuery, plan_id: int):
    user_id = call.from_user.id
    plan = db.get_plan(plan_id)
    
    if not plan:
        bot.answer_callback_query(call.id, "Plan not found!")
        return
    
    text = format_plan_text(plan)
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=create_plan_detail_keyboard(plan_id, user_id)
    )

def preview_plan(call: types.CallbackQuery, plan_id: int):
    user_id = call.from_user.id
    plan = db.get_plan(plan_id)
    
    if not plan:
        bot.answer_callback_query(call.id, "Plan not found!")
        return
    
    media_list = json.loads(plan['media_json']) if plan['media_json'] else []
    
    if not media_list:
        bot.answer_callback_query(call.id, "No media in this plan!")
        return
    
    # Send first 5 media items as preview
    sent = 0
    for media in media_list[:5]:
        try:
            if media['type'] == 'photo':
                bot.send_photo(user_id, media['file_id'], caption=f"📎 {plan['name']} - Preview")
            elif media['type'] == 'video':
                bot.send_video(user_id, media['file_id'], caption=f"📎 {plan['name']} - Preview")
            elif media['type'] == 'document':
                bot.send_document(user_id, media['file_id'], caption=f"📎 {plan['name']} - Preview")
            elif media['type'] == 'audio':
                bot.send_audio(user_id, media['file_id'], caption=f"📎 {plan['name']} - Preview")
            elif media['type'] == 'voice':
                bot.send_voice(user_id, media['file_id'], caption=f"📎 {plan['name']} - Preview")
            elif media['type'] == 'animation':
                bot.send_animation(user_id, media['file_id'], caption=f"📎 {plan['name']} - Preview")
            elif media['type'] == 'sticker':
                bot.send_sticker(user_id, media['file_id'])
            sent += 1
        except Exception as e:
            logger.error(f"Error sending preview media: {e}")
    
    if sent == 0:
        bot.send_message(user_id, "❌ Could not preview media. Some files may be unavailable.")
    
    bot.answer_callback_query(call.id, f"Showing preview of {sent} items")

def buy_plan(call: types.CallbackQuery, plan_id: int):
    user_id = call.from_user.id
    plan = db.get_plan(plan_id)
    
    if not plan:
        bot.answer_callback_query(call.id, "Plan not found!")
        return
    
    # Store selected plan for payment
    user_data[user_id] = {'selected_plan': plan_id}
    
    text = f"🛒 <b>Payment for {plan['name']}</b>\n\n"
    text += f"💰 Amount: <b>₹{plan['price']}</b>\n"
    text += f"📅 Validity: {plan['validity_days']} days\n\n"
    text += "Please choose your payment method:"
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=create_payment_keyboard()
    )

def pay_with_qr(call: types.CallbackQuery):
    user_id = call.from_user.id
    plan_id = user_data.get(user_id, {}).get('selected_plan')
    
    if not plan_id:
        bot.answer_callback_query(call.id, "Please select a plan first!")
        return
    
    plan = db.get_plan(plan_id)
    if not plan:
        bot.answer_callback_query(call.id, "Plan not found!")
        return
    
    global QR_CODE
    QR_CODE = db.get_setting('qr_code') or QR_CODE
    
    if not QR_CODE:
        bot.answer_callback_query(call.id, "QR code not configured by admin!")
        return
    
    text = f"📱 <b>Pay with QR Code</b>\n\n"
    text += f"Plan: {plan['name']}\n"
    text += f"Amount: ₹{plan['price']}\n\n"
    text += "Scan the QR code below to pay:\n\n"
    text += "After payment, send the screenshot using the button below."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("📤 Upload Screenshot", callback_data=f"upload_screenshot_{plan_id}")
    )
    keyboard.add(
        types.InlineKeyboardButton("🔙 Cancel", callback_data="back_to_plans")
    )
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )
    
    # Send QR code
    safe_send_photo(user_id, QR_CODE, caption="📱 Scan to pay")

def pay_with_upi(call: types.CallbackQuery):
    user_id = call.from_user.id
    plan_id = user_data.get(user_id, {}).get('selected_plan')
    
    if not plan_id:
        bot.answer_callback_query(call.id, "Please select a plan first!")
        return
    
    plan = db.get_plan(plan_id)
    if not plan:
        bot.answer_callback_query(call.id, "Plan not found!")
        return
    
    global UPI_ID
    UPI_ID = db.get_setting('upi_id') or UPI_ID
    
    if not UPI_ID:
        bot.answer_callback_query(call.id, "UPI ID not configured by admin!")
        return
    
    text = f"💰 <b>Pay with UPI</b>\n\n"
    text += f"Plan: {plan['name']}\n"
    text += f"Amount: ₹{plan['price']}\n\n"
    text += f"Send payment to this UPI ID:\n<b>{UPI_ID}</b>\n\n"
    text += "After payment, send the screenshot using the button below."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("📤 Upload Screenshot", callback_data=f"upload_screenshot_{plan_id}")
    )
    keyboard.add(
        types.InlineKeyboardButton("🔙 Cancel", callback_data="back_to_plans")
    )
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def show_subscription(call: types.CallbackQuery):
    user_id = call.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        bot.answer_callback_query(call.id, "User not found!")
        return
    
    if user.get('subscription_plan_id'):
        plan = db.get_plan(user['subscription_plan_id'])
        expiry = user.get('subscription_expiry', '')
        
        if plan:
            text = f"ℹ️ <b>Your Subscription</b>\n\n"
            text += f"📋 Plan: {plan['name']}\n"
            text += f"📅 Expires: {expiry[:10] if expiry else 'N/A'}\n"
            
            # Check if subscription is active
            if expiry and datetime.fromisoformat(expiry) > datetime.now():
                text += "✅ Status: <b>Active</b>\n\n"
                
                # Show delivery link
                global DELIVERY_LINK
                DELIVERY_LINK = db.get_setting('delivery_link') or DELIVERY_LINK
                if DELIVERY_LINK:
                    text += f"🔗 <a href='{DELIVERY_LINK}'>Access Content</a>\n"
            else:
                text += "❌ Status: <b>Expired</b>\n\n"
                text += "Please renew your subscription by choosing a plan below."
            
            keyboard = types.InlineKeyboardMarkup(row_width=1)
            if not (expiry and datetime.fromisoformat(expiry) > datetime.now()):
                keyboard.add(
                    types.InlineKeyboardButton("🔄 Renew Subscription", callback_data="show_plans")
                )
            keyboard.add(
                types.InlineKeyboardButton("🔙 Back", callback_data="back_to_main")
            )
            
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                text,
                reply_markup=keyboard,
                disable_web_page_preview=True
            )
            return
    
    text = "ℹ️ <b>Your Subscription</b>\n\n"
    text += "You don't have an active subscription.\n"
    text += "Choose a plan to get started!"
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("📋 Choose Plan", callback_data="show_plans"),
            types.InlineKeyboardButton("🔙 Back", callback_data="back_to_main")
        )
    )

# ==================== Upload Screenshot Handler ====================

@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("upload_screenshot_"))
def handle_upload_screenshot(call: types.CallbackQuery):
    user_id = call.from_user.id
    plan_id = int(call.data.split("_")[2])
    
    plan = db.get_plan(plan_id)
    if not plan:
        bot.answer_callback_query(call.id, "Plan not found!")
        return
    
    # Store plan_id for the screenshot
    user_data[user_id] = {'screenshot_plan': plan_id, 'payment_amount': plan['price']}
    
    text = f"📤 <b>Upload Payment Screenshot</b>\n\n"
    text += f"Please send the payment screenshot for:\n\n"
    text += f"Plan: {plan['name']}\n"
    text += f"Amount: ₹{plan['price']}\n\n"
    text += "Simply send the image in this chat. It will be forwarded to admin for approval."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="back_to_plans"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

# ==================== Message Handlers ====================

@bot.message_handler(content_types=['photo'])
def handle_photo(message: types.Message):
    user_id = message.from_user.id
    
    # Check if user is uploading screenshot
    if user_id in user_data and 'screenshot_plan' in user_data[user_id]:
        plan_id = user_data[user_id]['screenshot_plan']
        amount = user_data[user_id]['payment_amount']
        
        photo_file_id = message.photo[-1].file_id
        
        # Add payment to database
        payment_id = db.add_payment(user_id, plan_id, amount, photo_file_id)
        
        # Notify user
        bot.reply_to(message, "✅ Screenshot received! Admin will review it shortly.")
        
        # Notify admins
        notify_admins_of_payment(payment_id, user_id, plan_id, photo_file_id)
        
        # Clean up
        del user_data[user_id]
        return
    
    # Handle admin settings uploads
    if user_id in user_data and 'setting_type' in user_data[user_id]:
        setting_type = user_data[user_id]['setting_type']
        file_id = message.photo[-1].file_id
        
        if setting_type == 'welcome_image':
            db.set_setting('welcome_image', file_id)
            global WELCOME_IMAGE
            WELCOME_IMAGE = file_id
            bot.reply_to(message, "✅ Welcome image updated successfully!")
            
        elif setting_type == 'qr_code':
            db.set_setting('qr_code', file_id)
            global QR_CODE
            QR_CODE = file_id
            bot.reply_to(message, "✅ QR code updated successfully!")
            
        elif setting_type == 'plan_media':
            plan_id = user_data[user_id].get('plan_id')
            if plan_id:
                db.add_media_to_plan(plan_id, 'photo', file_id)
                bot.reply_to(message, "✅ Media added to plan successfully!")
        
        del user_data[user_id]
        return
    
    # Handle admin restore
    if user_id in user_data and user_data[user_id].get('restoring'):
        bot.reply_to(message, "Please upload the database file as a document.")
        return

@bot.message_handler(content_types=['document'])
def handle_document(message: types.Message):
    user_id = message.from_user.id
    
    # Handle admin restore
    if user_id in user_data and user_data[user_id].get('restoring'):
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            db.restore_database(downloaded_file)
            bot.reply_to(message, "✅ Database restored successfully!")
            
            del user_data[user_id]
        except Exception as e:
            logger.error(f"Error restoring database: {e}")
            bot.reply_to(message, "❌ Failed to restore database. Please check the file format.")
        return
    
    # Handle plan media upload
    if user_id in user_data and user_data[user_id].get('plan_media_document'):
        plan_id = user_data[user_id].get('plan_id')
        if plan_id:
            db.add_media_to_plan(plan_id, 'document', message.document.file_id)
            bot.reply_to(message, "✅ Document added to plan successfully!")
            del user_data[user_id]

@bot.message_handler(content_types=['video'])
def handle_video(message: types.Message):
    user_id = message.from_user.id
    
    # Handle plan media upload
    if user_id in user_data and user_data[user_id].get('plan_media_video'):
        plan_id = user_data[user_id].get('plan_id')
        if plan_id:
            db.add_media_to_plan(plan_id, 'video', message.video.file_id)
            bot.reply_to(message, "✅ Video added to plan successfully!")
            del user_data[user_id]

@bot.message_handler(content_types=['audio'])
def handle_audio(message: types.Message):
    user_id = message.from_user.id
    
    # Handle plan media upload
    if user_id in user_data and user_data[user_id].get('plan_media_audio'):
        plan_id = user_data[user_id].get('plan_id')
        if plan_id:
            db.add_media_to_plan(plan_id, 'audio', message.audio.file_id)
            bot.reply_to(message, "✅ Audio added to plan successfully!")
            del user_data[user_id]

@bot.message_handler(content_types=['voice'])
def handle_voice(message: types.Message):
    user_id = message.from_user.id
    
    # Handle plan media upload
    if user_id in user_data and user_data[user_id].get('plan_media_voice'):
        plan_id = user_data[user_id].get('plan_id')
        if plan_id:
            db.add_media_to_plan(plan_id, 'voice', message.voice.file_id)
            bot.reply_to(message, "✅ Voice added to plan successfully!")
            del user_data[user_id]

@bot.message_handler(content_types=['animation'])
def handle_animation(message: types.Message):
    user_id = message.from_user.id
    
    # Handle plan media upload
    if user_id in user_data and user_data[user_id].get('plan_media_animation'):
        plan_id = user_data[user_id].get('plan_id')
        if plan_id:
            db.add_media_to_plan(plan_id, 'animation', message.animation.file_id)
            bot.reply_to(message, "✅ Animation added to plan successfully!")
            del user_data[user_id]

@bot.message_handler(content_types=['sticker'])
def handle_sticker(message: types.Message):
    user_id = message.from_user.id
    
    # Handle plan media upload
    if user_id in user_data and user_data[user_id].get('plan_media_sticker'):
        plan_id = user_data[user_id].get('plan_id')
        if plan_id:
            db.add_media_to_plan(plan_id, 'sticker', message.sticker.file_id)
            bot.reply_to(message, "✅ Sticker added to plan successfully!")
            del user_data[user_id]

# ==================== Notification Functions ====================

def notify_admins_of_payment(payment_id: int, user_id: int, plan_id: int, screenshot_file_id: str):
    payment = db.get_payment(payment_id)
    if not payment:
        return
    
    user = db.get_user(user_id)
    plan = db.get_plan(plan_id)
    
    if not user or not plan:
        return
    
    text = f"💳 <b>New Payment Received</b>\n\n"
    text += f"Payment ID: #{payment_id}\n"
    text += f"User: {user.get('first_name', 'Unknown')} (@{user.get('username', 'N/A')})\n"
    text += f"Plan: {plan['name']}\n"
    text += f"Amount: ₹{plan['price']}\n\n"
    text += "Please verify the screenshot and approve or reject."
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        types.InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_{payment_id}"),
        types.InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{payment_id}")
    )
    
    for admin_id in ADMIN_IDS:
        try:
            safe_send_photo(admin_id, screenshot_file_id, caption=text, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Error notifying admin {admin_id}: {e}")

# ==================== Admin Panel Functions ====================

def show_admin_panel(call: types.CallbackQuery):
    user_id = call.from_user.id
    
    text = "⚙️ <b>Admin Panel</b>\n\n"
    text += "Manage your bot settings and content here."
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=create_admin_keyboard()
    )

def show_admin_stats(call: types.CallbackQuery):
    stats = db.get_stats()
    
    text = "📊 <b>Bot Statistics</b>\n\n"
    text += f"👥 Total Users: {stats['total_users']}\n"
    text += f"✅ Active Subscriptions: {stats['active_subscriptions']}\n"
    text += f"📋 Total Plans: {stats['total_plans']}\n"
    text += f"🕐 Pending Payments: {stats['pending_payments']}\n"
    text += f"✅ Approved Payments: {stats['approved_payments']}\n"
    text += f"❌ Rejected Payments: {stats['rejected_payments']}"
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def show_admin_users(call: types.CallbackQuery):
    users = db.get_all_users()
    
    text = "👥 <b>Users</b>\n\n"
    text += f"Total users: {len(users)}\n\n"
    
    # Show first 20 users
    for user in users[:20]:
        name = user.get('first_name', 'Unknown')
        username = user.get('username', '')
        expiry = user.get('subscription_expiry', 'None')
        text += f"👤 {name}"
        if username:
            text += f" (@{username})"
        text += f"\n   📅 Exp: {expiry[:10] if expiry else 'None'}\n\n"
    
    if len(users) > 20:
        text += f"... and {len(users) - 20} more users"
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def show_admin_plans(call: types.CallbackQuery):
    text = "📋 <b>Plan Management</b>\n\n"
    text += "Manage your subscription plans here."
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=create_plans_management_keyboard()
    )

def show_plan_edit(call: types.CallbackQuery, plan_id: int):
    plan = db.get_plan(plan_id)
    if not plan:
        bot.answer_callback_query(call.id, "Plan not found!")
        return
    
    text = f"📝 <b>Edit Plan: {plan['name']}</b>\n\n"
    text += format_plan_text(plan)
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("✏️ Edit Name", callback_data=f"plan_edit_name_{plan_id}"),
        types.InlineKeyboardButton("✏️ Edit Description", callback_data=f"plan_edit_desc_{plan_id}"),
        types.InlineKeyboardButton("💰 Edit Price", callback_data=f"plan_edit_price_{plan_id}"),
        types.InlineKeyboardButton("📅 Edit Validity", callback_data=f"plan_edit_validity_{plan_id}"),
        types.InlineKeyboardButton("📎 Add Media", callback_data=f"plan_add_media_{plan_id}"),
        types.InlineKeyboardButton("🗑️ Delete Media", callback_data=f"plan_delete_media_{plan_id}"),
        types.InlineKeyboardButton("🔙 Back to Plans", callback_data="admin_plans")
    )
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def show_admin_payments(call: types.CallbackQuery):
    text = "💳 <b>Payment Management</b>\n\n"
    
    pending = db.get_pending_payments()
    if pending:
        text += f"📌 Pending Payments: {len(pending)}\n\n"
        text += "Click on a payment to review:"
    else:
        text += "✅ No pending payments to review."
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=create_payment_management_keyboard()
    )

def view_payment_detail(call: types.CallbackQuery, payment_id: int):
    payment = db.get_payment(payment_id)
    if not payment:
        bot.answer_callback_query(call.id, "Payment not found!")
        return
    
    user = db.get_user(payment['user_id'])
    plan = db.get_plan(payment['plan_id'])
    
    text = f"💳 <b>Payment Details</b>\n\n"
    text += f"Payment ID: #{payment_id}\n"
    text += f"User: {user.get('first_name', 'Unknown')} (@{user.get('username', 'N/A')})\n"
    text += f"Plan: {plan['name'] if plan else 'Unknown'}\n"
    text += f"Amount: ₹{payment['amount']}\n"
    text += f"Status: {payment['status'].title()}\n"
    text += f"Date: {payment['created_at'][:16]}\n\n"
    text += "Review the screenshot below and take action."
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    if payment['status'] == 'pending':
        keyboard.row(
            types.InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_{payment_id}"),
            types.InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{payment_id}")
        )
    keyboard.add(types.InlineKeyboardButton("🔙 Back to Payments", callback_data="admin_payments"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )
    
    # Send screenshot
    if payment.get('screenshot_file_id'):
        safe_send_photo(call.from_user.id, payment['screenshot_file_id'], 
                      caption="📱 Payment Screenshot")

def approve_payment(call: types.CallbackQuery, payment_id: int):
    user_id = call.from_user.id
    
    db.approve_payment(payment_id, f"Approved by admin")
    
    # Get payment details
    payment = db.get_payment(payment_id)
    if payment:
        # Notify user
        user = db.get_user(payment['user_id'])
        plan = db.get_plan(payment['plan_id'])
        
        if user and plan:
            global DELIVERY_LINK
            DELIVERY_LINK = db.get_setting('delivery_link') or DELIVERY_LINK
            
            text = f"✅ <b>Payment Approved!</b>\n\n"
            text += f"Your payment for {plan['name']} has been approved.\n"
            text += f"Your subscription is now active for {plan['validity_days']} days.\n\n"
            
            if DELIVERY_LINK:
                text += f"🔗 <a href='{DELIVERY_LINK}'>Access Your Content</a>"
            else:
                text += "📱 You will receive the delivery link shortly."
            
            safe_send_message(payment['user_id'], text, disable_web_page_preview=True)
    
    bot.answer_callback_query(call.id, "Payment approved!")
    
    # Go back to payment list
    show_admin_payments(call)

def reject_payment(call: types.CallbackQuery, payment_id: int):
    user_id = call.from_user.id
    
    # Ask for rejection reason
    user_data[user_id] = {'rejecting_payment': payment_id}
    
    bot.answer_callback_query(call.id, "Please send the rejection reason as a message.")

def show_admin_settings(call: types.CallbackQuery):
    text = "⚙️ <b>Settings</b>\n\n"
    text += "Configure your bot settings here.\n\n"
    
    global WELCOME_IMAGE, WELCOME_TEXT, QR_CODE, UPI_ID, DELIVERY_LINK
    
    text += f"🖼️ Welcome Image: {'✅ Set' if WELCOME_IMAGE else '❌ Not Set'}\n"
    text += f"📝 Welcome Text: {len(WELCOME_TEXT)} characters\n"
    text += f"📱 QR Code: {'✅ Set' if QR_CODE else '❌ Not Set'}\n"
    text += f"💰 UPI ID: {UPI_ID if UPI_ID else '❌ Not Set'}\n"
    text += f"🔗 Delivery Link: {DELIVERY_LINK if DELIVERY_LINK else '❌ Not Set'}"
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=create_settings_keyboard()
    )

def set_welcome_image(call: types.CallbackQuery):
    user_id = call.from_user.id
    user_data[user_id] = {'setting_type': 'welcome_image'}
    
    text = "🖼️ <b>Set Welcome Image</b>\n\n"
    text += "Please send the new welcome image as a photo."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_settings"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def set_welcome_text(call: types.CallbackQuery):
    user_id = call.from_user.id
    user_data[user_id] = {'setting_type': 'welcome_text'}
    
    text = "📝 <b>Set Welcome Text</b>\n\n"
    text += "Please send the new welcome text as a message."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_settings"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def set_qr_code(call: types.CallbackQuery):
    user_id = call.from_user.id
    user_data[user_id] = {'setting_type': 'qr_code'}
    
    text = "📱 <b>Set QR Code</b>\n\n"
    text += "Please send the new QR code as a photo."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_settings"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def set_upi_id(call: types.CallbackQuery):
    user_id = call.from_user.id
    user_data[user_id] = {'setting_type': 'upi_id'}
    
    text = "💰 <b>Set UPI ID</b>\n\n"
    text += "Please send the new UPI ID as a message."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_settings"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def set_delivery_link(call: types.CallbackQuery):
    user_id = call.from_user.id
    user_data[user_id] = {'setting_type': 'delivery_link'}
    
    text = "🔗 <b>Set Delivery Link</b>\n\n"
    text += "Please send the new delivery link as a message."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_settings"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def start_broadcast(call: types.CallbackQuery):
    user_id = call.from_user.id
    user_data[user_id] = {'broadcast_type': 'message'}
    
    text = "📢 <b>Broadcast Message</b>\n\n"
    text += "Please send the message you want to broadcast to all users.\n\n"
    text += "You can send:\n"
    text += "• Text messages\n"
    text += "• Photos\n"
    text += "• Videos\n"
    text += "• Documents\n\n"
    text += "⚠️ This will be sent to ALL users."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def backup_database(call: types.CallbackQuery):
    try:
        data = db.backup_database()
        
        # Send backup file
        bot.send_document(
            call.from_user.id,
            ('backup.db', data),
            caption="💾 <b>Database Backup</b>\n\nBackup completed successfully!"
        )
        
        bot.answer_callback_query(call.id, "Backup created!")
    except Exception as e:
        logger.error(f"Backup error: {e}")
        bot.answer_callback_query(call.id, "Backup failed!")

def start_restore(call: types.CallbackQuery):
    user_id = call.from_user.id
    user_data[user_id] = {'restoring': True}
    
    text = "📥 <b>Restore Database</b>\n\n"
    text += "Please send the database backup file as a document.\n\n"
    text += "⚠️ This will replace the current database completely!"
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def start_add_plan(call: types.CallbackQuery):
    user_id = call.from_user.id
    user_data[user_id] = {'adding_plan': True, 'step': 'name'}
    
    text = "➕ <b>Add New Plan</b>\n\n"
    text += "Step 1/4: Enter the plan name."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_plans"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def start_delete_plan(call: types.CallbackQuery):
    plans = db.get_all_plans()
    
    if not plans:
        bot.answer_callback_query(call.id, "No plans to delete!")
        return
    
    text = "🗑️ <b>Delete Plan</b>\n\n"
    text += "Select a plan to delete:"
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for plan in plans:
        keyboard.add(
            types.InlineKeyboardButton(f"❌ {plan['name']}", callback_data=f"admin_delete_plan_{plan['plan_id']}")
        )
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_plans"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def confirm_delete_plan(call: types.CallbackQuery, plan_id: int):
    plan = db.get_plan(plan_id)
    if not plan:
        bot.answer_callback_query(call.id, "Plan not found!")
        return
    
    text = f"🗑️ <b>Delete Plan?</b>\n\n"
    text += f"Are you sure you want to delete this plan?\n\n"
    text += f"<b>{plan['name']}</b>\n"
    text += f"💰 ₹{plan['price']}\n"
    text += f"📅 {plan['validity_days']} days\n\n"
    text += "⚠️ This action cannot be undone!"
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        types.InlineKeyboardButton("✅ Yes, Delete", callback_data=f"admin_confirm_delete_{plan_id}"),
        types.InlineKeyboardButton("❌ No, Cancel", callback_data="admin_plans")
    )
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def delete_plan(call: types.CallbackQuery, plan_id: int):
    db.delete_plan(plan_id)
    bot.answer_callback_query(call.id, "Plan deleted successfully!")
    show_admin_plans(call)

def edit_plan_name(call: types.CallbackQuery, plan_id: int):
    user_id = call.from_user.id
    user_data[user_id] = {'editing_plan': plan_id, 'edit_field': 'name'}
    
    text = "✏️ <b>Edit Plan Name</b>\n\n"
    text += "Please send the new name for this plan."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data=f"admin_edit_plan_{plan_id}"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def edit_plan_description(call: types.CallbackQuery, plan_id: int):
    user_id = call.from_user.id
    user_data[user_id] = {'editing_plan': plan_id, 'edit_field': 'description'}
    
    text = "✏️ <b>Edit Plan Description</b>\n\n"
    text += "Please send the new description for this plan."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data=f"admin_edit_plan_{plan_id}"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def edit_plan_price(call: types.CallbackQuery, plan_id: int):
    user_id = call.from_user.id
    user_data[user_id] = {'editing_plan': plan_id, 'edit_field': 'price'}
    
    text = "✏️ <b>Edit Plan Price</b>\n\n"
    text += "Please send the new price (in ₹) for this plan."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data=f"admin_edit_plan_{plan_id}"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def edit_plan_validity(call: types.CallbackQuery, plan_id: int):
    user_id = call.from_user.id
    user_data[user_id] = {'editing_plan': plan_id, 'edit_field': 'validity'}
    
    text = "✏️ <b>Edit Plan Validity</b>\n\n"
    text += "Please send the new validity (in days) for this plan."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data=f"admin_edit_plan_{plan_id}"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def add_plan_media(call: types.CallbackQuery, plan_id: int):
    user_id = call.from_user.id
    user_data[user_id] = {'adding_media_to_plan': plan_id}
    
    text = "📎 <b>Add Media to Plan</b>\n\n"
    text += "Send any of the following to add to this plan:\n\n"
    text += "• Photos\n"
    text += "• Videos\n"
    text += "• Documents\n"
    text += "• Audio files\n"
    text += "• Voice messages\n"
    text += "• Animations/GIFs\n"
    text += "• Stickers\n\n"
    text += "The media will be added automatically."
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data=f"admin_edit_plan_{plan_id}"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

def delete_plan_media(call: types.CallbackQuery, plan_id: int):
    plan = db.get_plan(plan_id)
    if not plan:
        bot.answer_callback_query(call.id, "Plan not found!")
        return
    
    media_list = json.loads(plan['media_json']) if plan['media_json'] else []
    
    if not media_list:
        bot.answer_callback_query(call.id, "No media to delete!")
        return
    
    text = f"🗑️ <b>Delete Media from {plan['name']}</b>\n\n"
    text += "Select the media item to delete:\n\n"
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for idx, media in enumerate(media_list):
        media_type = media['type'].title()
        added_at = media.get('added_at', 'Unknown')[:10]
        keyboard.add(
            types.InlineKeyboardButton(
                f"❌ {media_type} - {added_at}", 
                callback_data=f"plan_confirm_delete_media_{plan_id}_{idx}"
            )
        )
    keyboard.add(types.InlineKeyboardButton("🔙 Cancel", callback_data=f"admin_edit_plan_{plan_id}"))
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard
    )

# ==================== Message Handlers for Admin Actions ====================

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_text_messages(message: types.Message):
    user_id = message.from_user.id
    
    # Handle rejection reason
    if user_id in user_data and 'rejecting_payment' in user_data[user_id]:
        payment_id = user_data[user_id]['rejecting_payment']
        reason = message.text
        
        db.reject_payment(payment_id, reason)
        
        # Notify user
        payment = db.get_payment(payment_id)
        if payment:
            user = db.get_user(payment['user_id'])
            plan = db.get_plan(payment['plan_id'])
            
            if user and plan:
                text = f"❌ <b>Payment Rejected</b>\n\n"
                text += f"Your payment for {plan['name']} was rejected.\n"
                text += f"Reason: {reason}\n\n"
                text += "Please try again with a valid payment screenshot."
                
                safe_send_message(payment['user_id'], text)
        
        bot.reply_to(message, "✅ Payment rejected and user notified.")
        del user_data[user_id]
        return
    
    # Handle adding plan
    if user_id in user_data and user_data[user_id].get('adding_plan'):
        step = user_data[user_id].get('step')
        
        if step == 'name':
            user_data[user_id]['plan_name'] = message.text
            user_data[user_id]['step'] = 'description'
            
            text = "➕ <b>Add New Plan</b>\n\n"
            text += f"Plan Name: {message.text}\n\n"
            text += "Step 2/4: Enter the plan description."
            
            bot.reply_to(message, text)
            
        elif step == 'description':
            user_data[user_id]['plan_description'] = message.text
            user_data[user_id]['step'] = 'price'
            
            text = "➕ <b>Add New Plan</b>\n\n"
            text += f"Plan Name: {user_data[user_id]['plan_name']}\n"
            text += f"Description: {message.text[:50]}...\n\n"
            text += "Step 3/4: Enter the plan price (in ₹)."
            
            bot.reply_to(message, text)
            
        elif step == 'price':
            try:
                price = float(message.text)
                user_data[user_id]['plan_price'] = price
                user_data[user_id]['step'] = 'validity'
                
                text = "➕ <b>Add New Plan</b>\n\n"
                text += f"Plan Name: {user_data[user_id]['plan_name']}\n"
                text += f"Description: {user_data[user_id]['plan_description'][:50]}...\n"
                text += f"Price: ₹{price}\n\n"
                text += "Step 4/4: Enter the plan validity (in days)."
                
                bot.reply_to(message, text)
            except ValueError:
                bot.reply_to(message, "❌ Please enter a valid number for price.")
                
        elif step == 'validity':
            try:
                validity = int(message.text)
                
                # Create the plan
                plan_id = db.add_plan(
                    user_data[user_id]['plan_name'],
                    user_data[user_id]['plan_description'],
                    user_data[user_id]['plan_price'],
                    validity
                )
                
                text = "✅ <b>Plan Created!</b>\n\n"
                text += f"Plan: {user_data[user_id]['plan_name']}\n"
                text += f"Price: ₹{user_data[user_id]['plan_price']}\n"
                text += f"Validity: {validity} days\n\n"
                text += "You can now add media to this plan using the edit option."
                
                bot.reply_to(message, text)
                
                del user_data[user_id]
            except ValueError:
                bot.reply_to(message, "❌ Please enter a valid number for validity.")
        return
    
    # Handle editing plan
    if user_id in user_data and 'editing_plan' in user_data[user_id]:
        plan_id = user_data[user_id]['editing_plan']
        field = user_data[user_id]['edit_field']
        
        if field == 'name':
            db.update_plan(plan_id, name=message.text)
            bot.reply_to(message, f"✅ Plan name updated to: {message.text}")
            
        elif field == 'description':
            db.update_plan(plan_id, description=message.text)
            bot.reply_to(message, "✅ Plan description updated!")
            
        elif field == 'price':
            try:
                price = float(message.text)
                db.update_plan(plan_id, price=price)
                bot.reply_to(message, f"✅ Plan price updated to: ₹{price}")
            except ValueError:
                bot.reply_to(message, "❌ Please enter a valid number for price.")
                
        elif field == 'validity':
            try:
                validity = int(message.text)
                db.update_plan(plan_id, validity_days=validity)
                bot.reply_to(message, f"✅ Plan validity updated to: {validity} days")
            except ValueError:
                bot.reply_to(message, "❌ Please enter a valid number for validity.")
        
        del user_data[user_id]
        return
    
    # Handle settings
    if user_id in user_data and user_data[user_id].get('setting_type'):
        setting_type = user_data[user_id]['setting_type']
        
        if setting_type == 'welcome_text':
            db.set_setting('welcome_text', message.text)
            global WELCOME_TEXT
            WELCOME_TEXT = message.text
            bot.reply_to(message, "✅ Welcome text updated successfully!")
            
        elif setting_type == 'upi_id':
            db.set_setting('upi_id', message.text)
            global UPI_ID
            UPI_ID = message.text
            bot.reply_to(message, "✅ UPI ID updated successfully!")
            
        elif setting_type == 'delivery_link':
            db.set_setting('delivery_link', message.text)
            global DELIVERY_LINK
            DELIVERY_LINK = message.text
            bot.reply_to(message, "✅ Delivery link updated successfully!")
        
        del user_data[user_id]
        return
    
    # Handle broadcast
    if user_id in user_data and user_data[user_id].get('broadcast_type'):
        users = db.get_all_users()
        sent_count = 0
        
        for user in users:
            try:
                safe_send_message(user['user_id'], message.text)
                sent_count += 1
                time.sleep(0.05)
            except Exception as e:
                logger.error(f"Error broadcasting to {user['user_id']}: {e}")
        
        bot.reply_to(message, f"✅ Broadcast sent to {sent_count} users.")
        del user_data[user_id]
        return

@bot.message_handler(content_types=['text', 'photo', 'video', 'document'])
def handle_broadcast_media(message: types.Message):
    user_id = message.from_user.id
    
    if user_id in user_data and user_data[user_id].get('broadcast_type'):
        users = db.get_all_users()
        sent_count = 0
        
        for user in users:
            try:
                if message.content_type == 'photo':
                    bot.send_photo(user['user_id'], message.photo[-1].file_id, caption=message.caption)
                elif message.content_type == 'video':
                    bot.send_video(user['user_id'], message.video.file_id, caption=message.caption)
                elif message.content_type == 'document':
                    bot.send_document(user['user_id'], message.document.file_id, caption=message.caption)
                sent_count += 1
                time.sleep(0.05)
            except Exception as e:
                logger.error(f"Error broadcasting to {user['user_id']}: {e}")
        
        bot.reply_to(message, f"✅ Broadcast sent to {sent_count} users.")
        del user_data[user_id]
        return
    
    # Handle media addition to plan
    if user_id in user_data and 'adding_media_to_plan' in user_data[user_id]:
        plan_id = user_data[user_id]['adding_media_to_plan']
        
        if message.content_type == 'photo':
            db.add_media_to_plan(plan_id, 'photo', message.photo[-1].file_id)
            bot.reply_to(message, "✅ Photo added to plan!")
        elif message.content_type == 'video':
            db.add_media_to_plan(plan_id, 'video', message.video.file_id)
            bot.reply_to(message, "✅ Video added to plan!")
        elif message.content_type == 'document':
            db.add_media_to_plan(plan_id, 'document', message.document.file_id)
            bot.reply_to(message, "✅ Document added to plan!")
        elif message.content_type == 'audio':
            db.add_media_to_plan(plan_id, 'audio', message.audio.file_id)
            bot.reply_to(message, "✅ Audio added to plan!")
        elif message.content_type == 'voice':
            db.add_media_to_plan(plan_id, 'voice', message.voice.file_id)
            bot.reply_to(message, "✅ Voice added to plan!")
        elif message.content_type == 'animation':
            db.add_media_to_plan(plan_id, 'animation', message.animation.file_id)
            bot.reply_to(message, "✅ Animation added to plan!")
        elif message.content_type == 'sticker':
            db.add_media_to_plan(plan_id, 'sticker', message.sticker.file_id)
            bot.reply_to(message, "✅ Sticker added to plan!")
        
        return

@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("plan_confirm_delete_media_"))
def handle_delete_plan_media(call: types.CallbackQuery):
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    parts = call.data.split("_")
    plan_id = int(parts[4])
    media_index = int(parts[5])
    
    db.delete_media_from_plan(plan_id, media_index)
    bot.answer_callback_query(call.id, "Media deleted successfully!")
    
    # Refresh the plan edit view
    show_plan_edit(call, plan_id)

# ==================== Main Function ====================

def main():
    logger.info("Starting Telegram Premium Subscription Bot...")
    
    try:
        # Validate config
        Config.validate()
        logger.info("Configuration validated successfully")
        
        # Test bot connection
        bot.get_me()
        logger.info("Bot connected successfully")
        
        # Start HTTP server in a separate thread
        http_thread = threading.Thread(target=run_http_server, daemon=True)
        http_thread.start()
        logger.info(f"HTTP server thread started")
        
        # Start bot in main thread
        run_bot()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        global bot_running
        bot_running = False
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
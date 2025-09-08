# myxl_bot.py
# Sebuah bot MyXL Telegram yang berjalan bersamaan dengan server web Flask.
# Kode ini telah diubah untuk menggunakan Updater dan berjalan secara sinkron.

import os
import sys
import threading
from flask import Flask
from datetime import datetime
from telegram import Update, ForceReply
from telegram.ext import Updater, CommandHandler, MessageHandler, filters, CallbackContext
from dotenv import load_dotenv

# Impor fungsi-fungsi dari skrip Anda
# Catatan: Asumsi skrip-skrip ini ada di direktori yang sama
try:
    from api_request import get_otp, submit_otp, get_balance, get_package, purchase_package
    from auth_helper import AuthInstance
    from ui import show_package_menu
    from util import ensure_api_key
except ImportError as e:
    print(f"Error: Gagal mengimpor modul. Pastikan file api_request.py, auth_helper.py, ui.py, dan util.py ada.")
    sys.exit(1)

# Muat variabel lingkungan
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Penyimpanan sementara untuk data pengguna
user_data = {}

# ==============================================================================
# Handlers Bot Telegram (Sinkron)
# ==============================================================================

def start(update: Update, context: CallbackContext) -> None:
    """Kirim pesan saat perintah /start dikeluarkan."""
    user = update.effective_user
    update.message.reply_html(
        f"Hai {user.mention_html()}! Selamat datang di Bot MyXL. Gunakan /help untuk melihat daftar perintah.",
        reply_markup=ForceReply(selective=True),
    )

def help_command(update: Update, context: CallbackContext) -> None:
    """Kirim pesan saat perintah /help dikeluarkan."""
    help_text = (
        "Berikut adalah daftar perintah yang tersedia:\n"
        "/start - Mulai bot\n"
        "/login - Login ke akun MyXL dengan nomor HP\n"
        "/balance - Cek pulsa dan masa aktif akun aktif\n"
        "/buy_xut - Tampilkan menu paket XUT\n"
        "/my_packages - Lihat paket saya\n"
        "/ganti_akun - Ganti akun aktif\n"
        "/list_accounts - Tampilkan daftar akun yang tersimpan\n"
    )
    update.message.reply_text(help_text)

def login_command(update: Update, context: CallbackContext) -> None:
    """Memulai alur login dengan meminta nomor HP."""
    update.message.reply_text("Silakan masukkan nomor XL Prabayar Anda (Contoh: 6281234567890).")
    user_data[update.effective_user.id] = {"state": "waiting_for_phone_number"}

def handle_message(update: Update, context: CallbackContext) -> None:
    """Menangani pesan dari pengguna berdasarkan state."""
    user_id = update.effective_user.id
    text = update.message.text

    if user_id not in user_data:
        update.message.reply_text("Silakan gunakan perintah /login atau /ganti_akun untuk memulai.")
        return

    state = user_data[user_id].get("state")

    if state == "waiting_for_phone_number":
        phone_number = text
        if not phone_number.startswith("628") or len(phone_number) < 10 or len(phone_number) > 14:
            update.message.reply_text("Nomor tidak valid. Mohon masukkan kembali nomor XL Prabayar yang benar.")
            return

        update.message.reply_text(f"Meminta OTP untuk nomor {phone_number}...")

        try:
            subscriber_id = get_otp(phone_number)
            if not subscriber_id:
                raise Exception("Failed to get OTP.")

            user_data[user_id]["phone_number"] = phone_number
            user_data[user_id]["state"] = "waiting_for_otp"
            update.message.reply_text("OTP berhasil dikirim. Silakan masukkan 6 digit OTP.")
        except Exception as e:
            update.message.reply_text(f"Gagal mengirim OTP. Error: {str(e)}")
            user_data.pop(user_id, None)

    elif state == "waiting_for_otp":
        otp = text
        phone_number = user_data[user_id]["phone_number"]

        if not otp.isdigit() or len(otp) != 6:
            update.message.reply_text("OTP tidak valid. Mohon masukkan 6 digit angka.")
            return

        update.message.reply_text("Memverifikasi OTP...")
        try:
            tokens = submit_otp(AuthInstance.api_key, phone_number, otp)
            if not tokens:
                raise Exception("Failed to submit OTP.")

            AuthInstance.add_refresh_token(int(phone_number), tokens["refresh_token"])
            AuthInstance.set_active_user(int(phone_number))

            update.message.reply_text("Login berhasil! Akun Anda telah disimpan.")
            user_data.pop(user_id, None)
        except Exception as e:
            update.message.reply_text(f"Gagal login. Error: {str(e)}")
            user_data.pop(user_id, None)
    
    elif state == "waiting_for_account_number":
        try:
            phone_number = int(text)
            AuthInstance.set_active_user(phone_number)
            update.message.reply_text(f"Akun berhasil diganti ke nomor {phone_number}.")
        except Exception:
            update.message.reply_text("Nomor akun tidak valid. Silakan coba lagi.")
        finally:
            user_data.pop(user_id, None)

def balance_command(update: Update, context: CallbackContext) -> None:
    """Mengecek pulsa dan masa aktif."""
    active_user = AuthInstance.get_active_user()
    if not active_user:
        update.message.reply_text("Tidak ada akun yang login. Gunakan /login untuk masuk.")
        return

    tokens = AuthInstance.get_active_tokens()
    balance = get_balance(AuthInstance.api_key, tokens["id_token"])

    if balance:
        balance_remaining = balance.get("remaining")
        balance_expired_at = balance.get("expired_at")
        expired_at_dt = datetime.fromtimestamp(balance_expired_at).strftime("%Y-%m-%d %H:%M:%S")

        response = (
            f"Informasi Akun:\n"
            f"Nomor: {active_user['number']}\n"
            f"Pulsa: Rp {balance_remaining}\n"
            f"Masa Aktif: {expired_at_dt}"
        )
        update.message.reply_text(response)
    else:
        update.message.reply_text("Gagal mengambil informasi pulsa. Mungkin token Anda kedaluwarsa.")

def list_accounts(update: Update, context: CallbackContext) -> None:
    """Menampilkan daftar akun yang tersimpan."""
    AuthInstance.load_tokens()
    users = AuthInstance.refresh_tokens
    active_user = AuthInstance.get_active_user()

    if not users:
        update.message.reply_text("Tidak ada akun tersimpan. Gunakan /login untuk menambah akun.")
        return

    list_text = "Akun Tersimpan:\n"
    for idx, user in enumerate(users):
        is_active = active_user and user["number"] == active_user["number"]
        active_marker = " (Aktif)" if is_active else ""
        list_text += f"{idx + 1}. {user['number']}{active_marker}\n"

    update.message.reply_text(list_text)

def switch_account_command(update: Update, context: CallbackContext) -> None:
    """Memulai alur ganti akun."""
    AuthInstance.load_tokens()
    users = AuthInstance.refresh_tokens

    if not users:
        update.message.reply_text("Tidak ada akun tersimpan.")
        return

    list_text = "Pilih akun untuk diganti:\n"
    for idx, user in enumerate(users):
        list_text += f"{idx + 1}. {user['number']}\n"

    update.message.reply_text(list_text + "Silakan masukkan nomor akun yang ingin Anda gunakan.")
    user_data[update.effective_user.id] = {"state": "waiting_for_account_number"}

def buy_xut_command(update: Update, context: CallbackContext) -> None:
    """Menampilkan menu paket XUT."""
    active_user = AuthInstance.get_active_user()
    if not active_user:
        update.message.reply_text("Tidak ada akun yang login. Gunakan /login untuk masuk.")
        return

    update.message.reply_text("Fungsi ini belum diimplementasikan untuk bot.")

def error_handler(update: Update, context: CallbackContext):
    """Handler untuk menangani kesalahan."""
    print(f'Update {update} menyebabkan error {context.error}')

# ==============================================================================
# Fungsi Utama Bot
# ==============================================================================

def run_bot():
    """Menginisialisasi dan menjalankan bot Telegram."""
    # Pastikan API key sudah ada
    try:
        AuthInstance.api_key = ensure_api_key()
        AuthInstance.load_tokens()
        if not AuthInstance.active_user and AuthInstance.refresh_tokens:
            first_rt = AuthInstance.refresh_tokens[0]
            AuthInstance.set_active_user(first_rt["number"])
    except Exception as e:
        print(f"Gagal menginisialisasi. Error: {e}")
        return

    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN tidak ditemukan di file .env. Pastikan Anda sudah mengaturnya.")
        return

    # Buat Updater dan berikan token bot
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Tambahkan handler perintah
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("login", login_command))
    dp.add_handler(CommandHandler("balance", balance_command))
    dp.add_handler(CommandHandler("list_accounts", list_accounts))
    dp.add_handler(CommandHandler("ganti_akun", switch_account_command))
    dp.add_handler(CommandHandler("buy_xut", buy_xut_command))


    # Tambahkan handler pesan
    dp.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Tambahkan handler error
    dp.add_error_handler(error_handler)

    # Jalankan bot sampai pengguna menekan Ctrl-C
    print("Bot sedang berjalan...")
    updater.start_polling()
    updater.idle()


# ==============================================================================
# Aplikasi Flask
# ==============================================================================
app = Flask(__name__)

@app.route('/')
def home():
    """Halaman beranda sederhana untuk server web."""
    return "Bot sedang aktif!"

# ==============================================================================
# Main
# ==============================================================================

if __name__ == '__main__':
    # Menjalankan bot Telegram di thread terpisah agar tidak memblokir Flask
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Menjalankan aplikasi Flask di thread utama
    print("Memulai server Flask di http://0.0.0.0:8000")
    app.run(host='0.0.0.0', port=8000)


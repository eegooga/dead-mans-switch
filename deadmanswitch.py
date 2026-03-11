import os
import time
import smtplib
import logging
import telegram
from dotenv import load_dotenv
from email.mime.text import MIMEText
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackContext
import threading
import asyncio
import glob

# ----------------------------- Logging -----------------------------
logging.basicConfig(
    filename="./logs/deadmanswitch.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# ----------------------------- Config ------------------------------
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_SECURITY = os.getenv("EMAIL_SECURITY", "auto").strip().lower()
MY_EMAIL   = os.getenv("MY_EMAIL")
EMAIL_NAME = os.getenv("EMAIL_NAME")
LOCK_DIR = "lock"
FINAL_MAIL_FLAG = os.path.join(LOCK_DIR, "final_mail_sent.txt")
MESSAGES_DIR    = "messages"

bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
last_response_time = time.time()

# Default intervals
check_interval   = 7  * 86400  # 7 days
warning_interval = 14 * 86400  # 14 days
final_interval   = 21 * 86400  # 21 days

lock = threading.Lock()

# Flags to prevent duplicate notifications
check_sent   = False
warning_sent = False
final_sent   = False

# Asyncio event loop for Telegram messages sent from the background thread
loop = asyncio.new_event_loop()

os.makedirs(LOCK_DIR, exist_ok=True)

logging.info("Dead Man's Switch started.")

# ----------------------------- Helpers -----------------------------
def is_authorized(update: Update) -> bool:
    return str(update.effective_chat.id) == TELEGRAM_CHAT_ID

def send_telegram_message(message: str):
    async def async_send_message():
        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        except Exception as e:
            logging.error(f"Error while sending Telegram message: {e}")

    asyncio.run_coroutine_threadsafe(async_send_message(), loop)

def send_email(subject: str, body: str, recipients: list[str]):
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = f"{EMAIL_NAME} <{EMAIL_USER}>" if EMAIL_NAME else EMAIL_USER
    msg["To"] = ", ".join(recipients)

    try:
        use_ssl = EMAIL_SECURITY == "ssl" or (EMAIL_SECURITY == "auto" and EMAIL_PORT == 465)
        use_starttls = EMAIL_SECURITY == "starttls" or (EMAIL_SECURITY == "auto" and EMAIL_PORT != 465)

        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_class(EMAIL_HOST, EMAIL_PORT) as server:
            if use_starttls:
                server.ehlo()
                server.starttls()
                server.ehlo()

            if EMAIL_USER and EMAIL_PASS:
                server.login(EMAIL_USER, EMAIL_PASS)

            sender = EMAIL_USER or MY_EMAIL
            server.sendmail(sender, recipients, msg.as_string())
        logging.info(f"Email sent: '{subject}' to {', '.join(recipients)}")
    except Exception as e:
        logging.error(f"Error while sending email: {e}")

def load_messages_from_files():
    messages = []
    try:
        for file_path in glob.glob(f"{MESSAGES_DIR}/*.txt"):
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
                if len(lines) < 3:
                    logging.error(f"Message file has too few lines: {file_path}")
                    continue

                recipients = [r.strip() for r in lines[0].split(",") if r.strip()]
                subject_line = lines[1].strip()
                if subject_line.lower().startswith("subject:"):
                    subject = subject_line[len("subject:"):].strip()
                elif subject_line.lower().startswith("onderwerp:"):
                    subject = subject_line[len("onderwerp:"):].strip()
                else:
                    subject = "A final letter"

                message = "\n".join(lines[2:]).strip()

                if recipients and message:
                    messages.append((recipients, subject, message))
                else:
                    logging.error(f"Empty recipients or message in: {file_path}")
        return messages
    except Exception as e:
        logging.error(f"Error while reading messages: {e}")
        return []

def send_warning_mail():
    body = (
        "⚠️ Dead Man's Switch is active!\n\n"
        "No activity has been detected for a while.\n\n"
        "➡️ Use the /reset command in Telegram to restart the timer.\n"
        "If you don't, your messages will be sent automatically after the configured interval."
    )
    send_email("⚠️ Warning: Dead Man's Switch is active", body, [MY_EMAIL])

async def send_final_notice(total_recipients):
    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"📩 All final emails have been sent ({total_recipients} recipients).\n"
                 "The Dead Man's Switch script will now stop permanently."
        )
    except Exception as e:
        logging.error(f"Error while sending final Telegram message: {e}")

def send_final_mail():
    global final_sent
    if os.path.exists(FINAL_MAIL_FLAG):
        logging.info("Final flag already exists; stopping without re-sending.")
        os._exit(0)

    messages = load_messages_from_files()
    if not messages:
        logging.error("No messages found in 'messages/'.")
        return

    total_recipients = 0
    for recipients, subject, message in messages:
        send_email(subject, message, recipients)
        total_recipients += len(recipients)

    open(FINAL_MAIL_FLAG, "w").write("sent")
    final_sent = True
    logging.info(f"Total of {total_recipients} recipients emailed. Flag created and script is shutting down.")

    # 👉 Send final message synchronously before shutting down
    asyncio.run(send_final_notice(total_recipients))

    os._exit(0)

# ----------------------------- Telegram Commands -----------------------------
async def set_interval(update: Update, context: CallbackContext, interval_type: str):
    if not is_authorized(update):
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return
    global check_interval, warning_interval, final_interval
    try:
        input_value = context.args[0]
        if input_value.endswith("d"):
            new_interval = float(input_value[:-1]) * 86400
        elif input_value.endswith("h"):
            new_interval = float(input_value[:-1]) * 3600
        elif input_value.endswith("m"):
            new_interval = float(input_value[:-1]) * 60
        else:
            new_interval = float(input_value) * 86400

        with lock:
            if interval_type == "check":
                check_interval = new_interval
            elif interval_type == "warning":
                warning_interval = new_interval
            elif interval_type == "final":
                final_interval = new_interval

        logging.info(f"/set{interval_type} set to {input_value}.")
        await update.message.reply_text(f"{interval_type.capitalize()} interval set to {input_value}.")
    except Exception:
        await update.message.reply_text(f"Usage: /set{interval_type} <time> (e.g. '1d', '2h', '30m')")

async def reset_timer(update: Update, context: CallbackContext):
    if not is_authorized(update):
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return
    global last_response_time, check_sent, warning_sent, final_sent
    with lock:
        last_response_time = time.time()
        check_sent = False
        warning_sent = False
        final_sent = False
    logging.info("/reset received → timer reset.")
    await update.message.reply_text("✅ Timer reset.")

async def show_status(update: Update, context: CallbackContext):
    if not is_authorized(update):
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return
    with lock:
        next_check = time.strftime('%d-%m-%Y %H:%M:%S', time.localtime(last_response_time + check_interval))
    logging.info("/status requested.")
    await update.message.reply_text(f"Next check: {next_check}")

async def show_help(update: Update, context: CallbackContext):
    if not is_authorized(update):
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return
    help_text = """
📌 Commands:
/status - Next check
/setcheck <time> - Check interval
/setwarning <time> - Warning interval
/setfinal  <time> - Final email interval
/reset - Reset timer
/help  - Show this list
"""
    await update.message.reply_text(help_text)

# ----------------------------- Background Task -----------------------------
def start_background_task():
    def timer_checker():
        global check_sent, warning_sent, final_sent
        while True:
            time.sleep(60)
            with lock:
                elapsed_time = time.time() - last_response_time
                if elapsed_time >= check_interval and not check_sent:
                    send_telegram_message(
                        "⚠️ Dead Man's Switch is active!\n"
                        "No activity has been detected for a while.\n\n"
                        "➡️ Use the /reset command in Telegram to restart the timer.\n"
                        "If you don't, your messages will be sent automatically after the configured interval."
                    )
                    check_sent = True
                    logging.info("Check message sent via Telegram.")

                if elapsed_time >= warning_interval and not warning_sent:
                    send_warning_mail()
                    warning_sent = True

                if elapsed_time >= final_interval and not final_sent:
                    send_final_mail()

    threading.Thread(target=timer_checker, daemon=True).start()

def start_event_loop():
    asyncio.set_event_loop(loop)
    loop.run_forever()

# ----------------------------- Main -----------------------------
if os.path.exists(FINAL_MAIL_FLAG):
    logging.info("Final flag already exists at startup → script exits immediately.")
    os._exit(0)

threading.Thread(target=start_event_loop, daemon=True).start()
start_background_task()

application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
application.add_handler(CommandHandler("status",   show_status))
application.add_handler(CommandHandler("setcheck", lambda u, c: set_interval(u, c, "check")))
application.add_handler(CommandHandler("setwarning", lambda u, c: set_interval(u, c, "warning")))
application.add_handler(CommandHandler("setfinal", lambda u, c: set_interval(u, c, "final")))
application.add_handler(CommandHandler("reset",    reset_timer))
application.add_handler(CommandHandler("help",     show_help))
application.run_polling()

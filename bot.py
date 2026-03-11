import os
import asyncio
import threading
import requests
from flask import Flask
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes
)

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# -----------------------------
# Railway health server
# -----------------------------
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return "Coin Direction Bot Running"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()


# -----------------------------
# Upbit API
# -----------------------------
def get_price(symbol):
    try:
        r = requests.get(
            "https://api.upbit.com/v1/ticker",
            params={"markets": f"KRW-{symbol}"},
            timeout=10
        ).json()[0]

        price = r["trade_price"]
        chg   = r["signed_change_rate"] * 100

        return price, chg
    except:
        return None, None


# -----------------------------
# Telegram commands
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Coin Direction Bot\n\n"
        "명령어:\n"
        "/btc\n"
        "/doge\n"
        "/eth\n"
        "/menu"
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 메뉴\n"
        "/btc 비트코인\n"
        "/doge 도지\n"
        "/eth 이더리움"
    )


async def btc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price, chg = get_price("BTC")

    if price:
        await update.message.reply_text(
            f"₿ BTC\n"
            f"가격: {price:,.0f}원\n"
            f"변동: {chg:+.2f}%"
        )
    else:
        await update.message.reply_text("데이터 수집 실패")


async def doge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price, chg = get_price("DOGE")

    if price:
        await update.message.reply_text(
            f"🐕 DOGE\n"
            f"가격: {price:,.0f}원\n"
            f"변동: {chg:+.2f}%"
        )
    else:
        await update.message.reply_text("데이터 수집 실패")


async def eth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price, chg = get_price("ETH")

    if price:
        await update.message.reply_text(
            f"Ξ ETH\n"
            f"가격: {price:,.0f}원\n"
            f"변동: {chg:+.2f}%"
        )
    else:
        await update.message.reply_text("데이터 수집 실패")


# -----------------------------
# Auto monitor
# -----------------------------
async def auto_monitor(app):
    print("자동 모니터 시작")

    while True:
        try:
            price, chg = get_price("BTC")

            if price and abs(chg) > 3:
                await app.bot.send_message(
                    CHAT_ID,
                    f"🚨 BTC 변동\n"
                    f"{price:,.0f}원\n"
                    f"{chg:+.2f}%"
                )

        except Exception as e:
            print("monitor error:", e)

        await asyncio.sleep(300)


# -----------------------------
# main
# -----------------------------
async def main():

    if not BOT_TOKEN:
        print("TOKEN 없음")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("btc", btc))
    app.add_handler(CommandHandler("doge", doge))
    app.add_handler(CommandHandler("eth", eth))

    async def post_init(application):
        asyncio.create_task(auto_monitor(application))

    app.post_init = post_init

    print("봇 시작")

    await app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    asyncio.run(main())

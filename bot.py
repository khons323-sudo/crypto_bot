import os
import asyncio
import requests
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

UPBIT_TICKER = "https://api.upbit.com/v1/ticker"
UPBIT_MARKETS = "https://api.upbit.com/v1/market/all"


# -----------------------------
# Upbit API
# -----------------------------
def get_price(symbol):
    try:
        r = requests.get(
            UPBIT_TICKER,
            params={"markets": f"KRW-{symbol}"},
            timeout=10
        ).json()[0]

        price = r["trade_price"]
        change = r["signed_change_rate"] * 100

        return price, change

    except Exception as e:
        print("price error:", e)
        return None, None


def get_top_volume():
    try:
        markets = requests.get(UPBIT_MARKETS, timeout=10).json()
        krw = [m["market"] for m in markets if m["market"].startswith("KRW-")]

        tickers = requests.get(
            UPBIT_TICKER,
            params={"markets": ",".join(krw)},
            timeout=10
        ).json()

        top = sorted(
            tickers,
            key=lambda x: x["acc_trade_price_24h"],
            reverse=True
        )[:5]

        msg = "🔥 업비트 거래대금 TOP5\n\n"

        for i, d in enumerate(top, 1):
            name = d["market"].replace("KRW-", "")
            chg = d["signed_change_rate"] * 100
            vol = d["acc_trade_price_24h"] / 1e12

            msg += f"{i}. {name} {chg:+.2f}%\n"
            msg += f"   거래대금 {vol:.2f}조\n"

        return msg

    except Exception as e:
        print("volume error:", e)
        return "데이터 조회 실패"


# -----------------------------
# Telegram commands
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Coin Direction Bot\n\n"
        "/menu 메뉴\n"
        "/btc 비트코인\n"
        "/eth 이더리움\n"
        "/doge 도지\n"
        "/top 거래대금 TOP5"
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 메뉴\n\n"
        "/btc\n"
        "/eth\n"
        "/doge\n"
        "/top"
    )


async def btc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price, chg = get_price("BTC")

    if price:
        await update.message.reply_text(
            f"₿ BTC\n가격 {price:,.0f}원\n변동 {chg:+.2f}%"
        )


async def eth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price, chg = get_price("ETH")

    if price:
        await update.message.reply_text(
            f"Ξ ETH\n가격 {price:,.0f}원\n변동 {chg:+.2f}%"
        )


async def doge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price, chg = get_price("DOGE")

    if price:
        await update.message.reply_text(
            f"🐕 DOGE\n가격 {price:,.0f}원\n변동 {chg:+.2f}%"
        )


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = get_top_volume()
    await update.message.reply_text(msg)


# -----------------------------
# Auto Monitor
# -----------------------------
async def auto_monitor(app):
    print("자동 모니터 시작")

    while True:
        try:
            price, chg = get_price("BTC")

            if price and abs(chg) >= 3:
                await app.bot.send_message(
                    CHAT_ID,
                    f"🚨 BTC 급변\n\n{price:,.0f}원\n{chg:+.2f}%"
                )

        except Exception as e:
            print("monitor error:", e)

        await asyncio.sleep(300)


# -----------------------------
# main
# -----------------------------
def main():

    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN 없음")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("btc", btc))
    app.add_handler(CommandHandler("eth", eth))
    app.add_handler(CommandHandler("doge", doge))
    app.add_handler(CommandHandler("top", top))

    async def post_init(application):
        asyncio.create_task(auto_monitor(application))

    app.post_init = post_init

    print("🤖 Coin Direction 봇 시작")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

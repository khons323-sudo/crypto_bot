"""
bot.py  ─  코인·유가 텔레그램 모니터링 봇 (CoinGecko 버전)
실행: python bot.py
"""
import os, asyncio, requests, pytz
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

load_dotenv()

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")
CHECK_SEC  = int(os.getenv("PRICE_CHECK_INTERVAL", 60))
BRIEF_SEC  = int(os.getenv("BRIEF_INTERVAL", 3600))

KST = pytz.timezone("Asia/Seoul")

cfg = {
    "btc_high":  float(os.getenv("BTC_ALERT_HIGH",  70800)),
    "btc_low":   float(os.getenv("BTC_ALERT_LOW",   65600)),
    "doge_high": float(os.getenv("DOGE_ALERT_HIGH", 0.103)),
    "doge_low":  float(os.getenv("DOGE_ALERT_LOW",  0.080)),
    "oil_high":  float(os.getenv("OIL_ALERT_HIGH",  95)),
    "oil_low":   float(os.getenv("OIL_ALERT_LOW",   80)),
}
cooldown = {}
COOLDOWN_SEC = 1800


# ════════════════════════════════════════════
# 1. 데이터 수집 (CoinGecko - 차단 없음)
# ════════════════════════════════════════════

def fetch_prices() -> dict:
    """CoinGecko 무료 API - Railway 차단 없음"""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "bitcoin,dogecoin,ethereum,solana",
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_24hr_vol": "true",
            "include_high_low": "true",
        }
        r = requests.get(url, params=params, timeout=15).json()

        def parse(coin_id, name):
            d = r.get(coin_id, {})
            return {
                "price": d.get("usd", 0),
                "chg":   round(d.get("usd_24h_change", 0), 2),
                "vol":   d.get("usd_24h_vol", 0),
                "high":  d.get("usd_24h_high", 0),
                "low":   d.get("usd_24h_low", 0),
            }

        return {
            "BTC":  parse("bitcoin",  "BTC"),
            "DOGE": parse("dogecoin", "DOGE"),
            "ETH":  parse("ethereum", "ETH"),
            "SOL":  parse("solana",   "SOL"),
        }
    except Exception as e:
        return {"_err": str(e)}


def fetch_oil() -> dict:
    """Yahoo Finance - WTI / 브렌트"""
    result = {}
    for name, ticker in [("WTI","CL=F"), ("Brent","BZ=F")]:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            r = requests.get(url,
                headers={"User-Agent":"Mozilla/5.0"}, timeout=10).json()
            meta = r["chart"]["result"][0]["meta"]
            p  = meta.get("regularMarketPrice", 0)
            pc = meta.get("previousClose", p)
            result[name] = {
                "price": p,
                "chg":   round((p - pc) / pc * 100, 2) if pc else 0,
            }
        except:
            result[name] = {"price": 0, "chg": 0}
    return result


def fetch_fear_greed() -> dict:
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=1", timeout=10
        ).json()["data"][0]
        return {"val": int(r["value"]), "label": r["value_classification"]}
    except:
        return {"val": 50, "label": "N/A"}


def fetch_long_short(symbol="BTC") -> dict:
    """Binance 선물 롱숏 - 실패 시 기본값 반환"""
    try:
        r = requests.get(
            "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
            params={"symbol": f"{symbol}USDT", "period": "1h", "limit": 1},
            timeout=10
        ).json()
        d = r[0]
        lp = float(d["longAccount"]) * 100
        sp = float(d["shortAccount"]) * 100
        return {"long": round(lp,1), "short": round(sp,1)}
    except:
        # 차단 시 기본값
        return {"long": 50.0, "short": 50.0}


# ════════════════════════════════════════════
# 2. 메시지 포맷터
# ════════════════════════════════════════════

def arrow(chg): return "🟢" if chg >= 0 else "🔴"
def fmt_coin(p, sym):
    return f"${p:,.4f}" if p < 1 else f"${p:,.0f}"

def ls_bar(long_pct: float) -> str:
    filled = round(long_pct / 10)
    return "🟩" * filled + "🟥" * (10 - filled)

def fg_emoji(v):
    if v <= 20: return "😱"
    if v <= 40: return "😨"
    if v <= 60: return "😐"
    if v <= 75: return "😄"
    return "🤑"

def build_full_brief() -> str:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    c   = fetch_prices()
    o   = fetch_oil()
    fg  = fetch_fear_greed()
    bl  = fetch_long_short("BTC")
    dl  = fetch_long_short("DOGE")

    def cline(name):
        d = c.get(name)
        if not d or "_err" in c or d.get("price", 0) == 0:
            return f"  {name}: 수집 실패"
        a = arrow(d["chg"])
        return f"  {a} *{name}*: {fmt_coin(d['price'], name)} ({d['chg']:+.2f}%)"

    def oline(name):
        d = o.get(name, {})
        p = d.get("price", 0)
        ch = d.get("chg", 0)
        if p == 0:
            return f"  {name}: 수집 실패"
        a = arrow(ch)
        return f"  {a} *{name}*: ${p:.2f} ({ch:+.2f}%)"

    wti_p = o.get("WTI", {}).get("price", 0)
    oil_warn = ""
    if wti_p >= cfg["oil_high"]:
        oil_warn = "\n  🚨 *유가 경보! 코인 하방 압력*"
    elif 0 < wti_p <= cfg["oil_low"]:
        oil_warn = "\n  ✅ *유가 진정! 반등 기대*"

    v = fg["val"]
    return (
        f"📊 *코인·시장 브리핑*\n"
        f"🕐 {now}\n"
        f"{'─'*28}\n"
        f"💰 *암호화폐*\n"
        f"{cline('BTC')}\n"
        f"{cline('DOGE')}\n"
        f"{cline('ETH')}\n"
        f"{cline('SOL')}\n\n"
        f"📊 *롱숏 비율*\n"
        f"  BTC  {ls_bar(bl['long'])} {bl['long']}%롱\n"
        f"  DOGE {ls_bar(dl['long'])} {dl['long']}%롱\n\n"
        f"{'─'*28}\n"
        f"🛢 *유가*\n"
        f"{oline('WTI')}\n"
        f"{oline('Brent')}{oil_warn}\n\n"
        f"{'─'*28}\n"
        f"{fg_emoji(v)} *공포탐욕지수*: {v}/100  _{fg['label']}_"
    )


def build_btc_detail() -> str:
    c  = fetch_prices()
    ls = fetch_long_short("BTC")
    d  = c.get("BTC", {})
    if not d or d.get("price", 0) == 0:
        return "❌ BTC 데이터 수집 실패\n잠시 후 다시 시도해주세요"
    p  = d["price"]
    ch = d["chg"]
    return (
        f"{'🚀' if ch>=0 else '📉'} *BTC 상세*\n"
        f"현재가: *${p:,.0f}*\n"
        f"24H 변동: {ch:+.2f}%\n"
        f"고가: ${d['high']:,.0f}  저가: ${d['low']:,.0f}\n"
        f"거래대금: ${d['vol']/1e9:.2f}B\n\n"
        f"📊 롱숏: {ls_bar(ls['long'])}\n"
        f"롱 {ls['long']}% / 숏 {ls['short']}%\n\n"
        f"🟢 저항: $68,683 → $70,800 → $74,100\n"
        f"🔴 지지: $65,600 → $62,300 → $59,500"
    )


def build_doge_detail() -> str:
    c  = fetch_prices()
    ls = fetch_long_short("DOGE")
    d  = c.get("DOGE", {})
    if not d or d.get("price", 0) == 0:
        return "❌ DOGE 데이터 수집 실패\n잠시 후 다시 시도해주세요"
    p  = d["price"]
    ch = d["chg"]
    signal = ""
    if p >= cfg["doge_high"]:
        signal = "\n🚀 *$0.10 돌파! 목표: $0.103 → $0.11*"
    elif p <= cfg["doge_low"]:
        signal = "\n⚠️ *$0.08 이탈 위험! 추가 하락 주의*"
    return (
        f"{'🐕🚀' if ch>=0 else '🐕📉'} *DOGE 상세*\n"
        f"현재가: *${p:.4f}*\n"
        f"24H 변동: {ch:+.2f}%\n"
        f"고가: ${d['high']:.4f}  저가: ${d['low']:.4f}\n\n"
        f"📊 롱숏: {ls_bar(ls['long'])}\n"
        f"롱 {ls['long']}% / 숏 {ls['short']}%\n\n"
        f"🟢 목표: $0.10 → $0.103 → $0.11\n"
        f"🔴 지지: $0.086 → $0.080"
        f"{signal}\n\n"
        f"💡 머스크 SNS: https://x.com/elonmusk"
    )


# ════════════════════════════════════════════
# 3. 자동 알림 체크
# ════════════════════════════════════════════

def cd_ok(key: str) -> bool:
    now = datetime.now().timestamp()
    if key in cooldown and now - cooldown[key] < COOLDOWN_SEC:
        return False
    cooldown[key] = now
    return True


def check_alerts(c: dict, o: dict, fg: dict, ls: dict) -> list:
    alerts = []
    btc  = c.get("BTC",  {})
    doge = c.get("DOGE", {})
    wti  = o.get("WTI",  {})
    v    = fg.get("val", 50)

    if btc.get("price", 0) >= cfg["btc_high"] and cd_ok("btc_high"):
        alerts.append(
            f"🚀 *BTC 상단 돌파!*\n"
            f"${btc['price']:,.0f} (≥ ${cfg['btc_high']:,.0f})\n"
            f"다음 저항: $70,906 → $74,100"
        )
    elif 0 < btc.get("price", 99999) <= cfg["btc_low"] and cd_ok("btc_low"):
        alerts.append(
            f"⚠️ *BTC 하단 이탈!*\n"
            f"${btc['price']:,.0f} (≤ ${cfg['btc_low']:,.0f})\n"
            f"다음 지지: $62,300 → $59,500"
        )

    if doge.get("price", 0) >= cfg["doge_high"] and cd_ok("doge_high"):
        alerts.append(
            f"🐕🚀 *DOGE $0.10 돌파!*\n"
            f"${doge['price']:.4f}\n"
            f"목표: $0.103 → $0.11"
        )
    elif 0 < doge.get("price", 1) <= cfg["doge_low"] and cd_ok("doge_low"):
        alerts.append(
            f"🐕🔴 *DOGE $0.08 이탈!*\n"
            f"${doge['price']:.4f}"
        )

    if wti.get("price", 0) >= cfg["oil_high"] and cd_ok("oil_high"):
        alerts.append(
            f"🛢🚨 *WTI 유가 급등!*\n"
            f"${wti['price']:.2f} → 코인 하방 압력!"
        )
    elif 0 < wti.get("price", 999) <= cfg["oil_low"] and cd_ok("oil_low"):
        alerts.append(
            f"🛢✅ *WTI 유가 진정!*\n"
            f"${wti['price']:.2f} → 반등 기대 🟢"
        )

    if v <= 15 and cd_ok("fg_fear"):
        alerts.append(f"😱 *극단적 공포!* 지수: {v}/100")
    elif v >= 80 and cd_ok("fg_greed"):
        alerts.append(f"🤑 *극단적 탐욕!* 지수: {v}/100")

    long_p = ls.get("long", 50)
    if long_p >= 75 and cd_ok("ls_long"):
        alerts.append(f"📊 *BTC 롱 과열!* {long_p}%")
    elif long_p <= 35 and cd_ok("ls_short"):
        alerts.append(f"📊 *BTC 숏 과열!* {long_p}%롱 → 반등 가능")

    return alerts


# ════════════════════════════════════════════
# 4. 명령어 핸들러
# ════════════════════════════════════════════

async def cmd_start(u: Update, _):
    await u.message.reply_text(
        "🤖 *Coin Direction 봇*\n\n"
        "/now   전체 시황 브리핑\n"
        "/btc   BTC 상세\n"
        "/doge  DOGE 상세\n"
        "/oil   유가 현황\n"
        "/ls    롱숏 비율\n"
        "/fg    공포탐욕지수\n"
        "/alert 알림 임계값\n"
        "/setalert KEY VALUE\n\n"
        "⏰ 자동알림: 임계값 돌파 즉시\n"
        "⏰ 정기브리핑: 1시간마다",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_now(u: Update, _):
    await u.message.reply_text("⏳ 수집 중...", parse_mode=ParseMode.MARKDOWN)
    await u.message.reply_text(build_full_brief(), parse_mode=ParseMode.MARKDOWN)

async def cmd_btc(u: Update, _):
    await u.message.reply_text(build_btc_detail(), parse_mode=ParseMode.MARKDOWN)

async def cmd_doge(u: Update, _):
    await u.message.reply_text(build_doge_detail(), parse_mode=ParseMode.MARKDOWN)

async def cmd_oil(u: Update, _):
    o = fetch_oil()
    wti   = o.get("WTI",   {})
    brent = o.get("Brent", {})
    wti_p = wti.get("price", 0)
    warn  = ""
    if wti_p >= 100:
        warn = "\n\n🚨 *$100 돌파! 스태그플레이션 경보*"
    elif 0 < wti_p <= 80:
        warn = "\n\n✅ *$80 이하! 위험자산 반등 기대*"
    await u.message.reply_text(
        f"🛢 *유가 현황*\n"
        f"WTI:   ${wti_p:.2f} ({wti.get('chg',0):+.2f}%)\n"
        f"Brent: ${brent.get('price',0):.2f} ({brent.get('chg',0):+.2f}%)\n\n"
        f"📌 레벨\n"
        f"  🚨 위험: $100↑\n"
        f"  🟡 중립: $80~95\n"
        f"  ✅ 안전: $75↓"
        f"{warn}",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_ls(u: Update, _):
    bl = fetch_long_short("BTC")
    dl = fetch_long_short("DOGE")
    await u.message.reply_text(
        f"📊 *롱숏 비율*\n\n"
        f"*BTC*\n"
        f"{ls_bar(bl['long'])}\n"
        f"롱 {bl['long']}% / 숏 {bl['short']}%\n\n"
        f"*DOGE*\n"
        f"{ls_bar(dl['long'])}\n"
        f"롱 {dl['long']}% / 숏 {dl['short']}%\n\n"
        f"💡 롱 75%↑ → 과열\n"
        f"💡 숏 65%↑ → 쇼트스퀴즈 가능",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_fg(u: Update, _):
    fg = fetch_fear_greed()
    v  = fg["val"]
    await u.message.reply_text(
        f"{fg_emoji(v)} *공포탐욕지수*\n\n"
        f"현재: *{v}/100*\n"
        f"상태: {fg['label']}\n\n"
        f"😱 0~25  극단적 공포\n"
        f"😨 26~40 공포\n"
        f"😐 41~60 중립\n"
        f"😄 61~75 탐욕\n"
        f"🤑 76~100 극단적 탐욕",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_alert(u: Update, _):
    await u.message.reply_text(
        f"⚙️ *현재 알림 임계값*\n\n"
        f"BTC 상단:  ${cfg['btc_high']:,.0f}\n"
        f"BTC 하단:  ${cfg['btc_low']:,.0f}\n"
        f"DOGE 상단: ${cfg['doge_high']:.3f}\n"
        f"DOGE 하단: ${cfg['doge_low']:.3f}\n"
        f"WTI 상단:  ${cfg['oil_high']}\n"
        f"WTI 하단:  ${cfg['oil_low']}\n\n"
        f"변경: `/setalert BTC_HIGH 72000`",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_setalert(u: Update, _):
    try:
        parts = u.message.text.split()
        key, val = parts[1].upper(), float(parts[2])
        mapping = {
            "BTC_HIGH":"btc_high","BTC_LOW":"btc_low",
            "DOGE_HIGH":"doge_high","DOGE_LOW":"doge_low",
            "OIL_HIGH":"oil_high","OIL_LOW":"oil_low",
        }
        if key in mapping:
            cfg[mapping[key]] = val
            await u.message.reply_text(f"✅ {key} = {val} 변경 완료!")
        else:
            await u.message.reply_text(f"❌ 사용가능 키:\n{', '.join(mapping.keys())}")
    except:
        await u.message.reply_text(
            "사용법: `/setalert BTC_HIGH 72000`",
            parse_mode=ParseMode.MARKDOWN
        )


# ════════════════════════════════════════════
# 5. 백그라운드 자동 모니터링
# ════════════════════════════════════════════

async def auto_monitor(bot: Bot):
    counter = 0
    print(f"✅ 자동 모니터링 시작 (체크:{CHECK_SEC}초 / 브리핑:{BRIEF_SEC}초)")
    while True:
        try:
            c  = fetch_prices()
            o  = fetch_oil()
            fg = fetch_fear_greed()
            ls = fetch_long_short("BTC")

            for msg in check_alerts(c, o, fg, ls):
                await bot.send_message(CHAT_ID, msg, parse_mode=ParseMode.MARKDOWN)

            counter += CHECK_SEC
            if counter >= BRIEF_SEC:
                await bot.send_message(
                    CHAT_ID,
                    f"⏰ *정기 브리핑*\n{build_full_brief()}",
                    parse_mode=ParseMode.MARKDOWN
                )
                counter = 0

        except Exception as e:
            print(f"[모니터 오류] {e}")

        await asyncio.sleep(CHECK_SEC)


# ════════════════════════════════════════════
# 6. 메인
# ════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN 없음")
        return
    if not CHAT_ID:
        print("❌ TELEGRAM_CHAT_ID 없음")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    for cmd, fn in [
        ("start",    cmd_start),
        ("now",      cmd_now),
        ("btc",      cmd_btc),
        ("doge",     cmd_doge),
        ("oil",      cmd_oil),
        ("ls",       cmd_ls),
        ("fg",       cmd_fg),
        ("alert",    cmd_alert),
        ("setalert", cmd_setalert),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.post_init = lambda a: asyncio.get_event_loop().create_task(
        auto_monitor(a.bot)
    )

    print("🤖 Coin Direction 봇 시작!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

"""
bot.py  ─  코인·유가 텔레그램 모니터링 봇 (업비트 KRW 버전)
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
    "btc_high":  float(os.getenv("BTC_ALERT_HIGH",  105000000)),  # 원
    "btc_low":   float(os.getenv("BTC_ALERT_LOW",    95000000)),  # 원
    "doge_high": float(os.getenv("DOGE_ALERT_HIGH",     150)),    # 원
    "doge_low":  float(os.getenv("DOGE_ALERT_LOW",      115)),    # 원
    "oil_high":  float(os.getenv("OIL_ALERT_HIGH",       95)),    # USD
    "oil_low":   float(os.getenv("OIL_ALERT_LOW",        80)),    # USD
}
cooldown = {}
COOLDOWN_SEC = 1800


# ════════════════════════════════════════════
# 1. 데이터 수집
# ════════════════════════════════════════════

def fetch_upbit_prices() -> dict:
    """업비트 공개 API - KRW 마켓 (무료/키 불필요)"""
    markets = {
        "BTC":  "KRW-BTC",
        "DOGE": "KRW-DOGE",
        "ETH":  "KRW-ETH",
        "SOL":  "KRW-SOL",
        "XRP":  "KRW-XRP",
    }
    try:
        codes = ",".join(markets.values())
        url = f"https://api.upbit.com/v1/ticker?markets={codes}"
        r = requests.get(url,
            headers={"Accept": "application/json"}, timeout=10).json()
        result = {}
        market_map = {v: k for k, v in markets.items()}
        for d in r:
            name = market_map.get(d["market"], d["market"])
            result[name] = {
                "price":   d["trade_price"],
                "chg":     round(d["signed_change_rate"] * 100, 2),
                "chg_amt": d["signed_change_price"],
                "high":    d["high_price"],
                "low":     d["low_price"],
                "vol_krw": d["acc_trade_price_24h"],
                "prev":    d["prev_closing_price"],
            }
        return result
    except Exception as e:
        return {"_err": str(e)}


def fetch_usd_krw() -> float:
    """달러/원 환율"""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X"
        r = requests.get(url,
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
        return r["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except:
        return 1300.0


def fetch_oil() -> dict:
    """Yahoo Finance - WTI / 브렌트"""
    result = {}
    for name, ticker in [("WTI", "CL=F"), ("Brent", "BZ=F")]:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            r = requests.get(url,
                headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
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
    try:
        r = requests.get(
            "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
            params={"symbol": f"{symbol}USDT", "period": "1h", "limit": 1},
            timeout=10
        ).json()
        d = r[0]
        lp = float(d["longAccount"]) * 100
        sp = float(d["shortAccount"]) * 100
        return {"long": round(lp, 1), "short": round(sp, 1)}
    except:
        return {"long": 50.0, "short": 50.0}


# ════════════════════════════════════════════
# 2. 포맷터
# ════════════════════════════════════════════

def arrow(chg): return "🟢" if chg >= 0 else "🔴"

def fmt_krw(price: float) -> str:
    if price >= 100_000_000:
        return f"{price/100_000_000:.2f}억원"
    elif price >= 10_000:
        return f"{price/10_000:.0f}만원"
    else:
        return f"{price:,.0f}원"

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
    now  = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    c    = fetch_upbit_prices()
    o    = fetch_oil()
    fg   = fetch_fear_greed()
    bl   = fetch_long_short("BTC")
    dl   = fetch_long_short("DOGE")
    rate = fetch_usd_krw()

    def cline(name):
        d = c.get(name)
        if not d or "_err" in c or d.get("price", 0) == 0:
            return f"  {name}: 수집 실패"
        a = arrow(d["chg"])
        return f"  {a} *{name}*: {fmt_krw(d['price'])} ({d['chg']:+.2f}%)"

    def oline(name):
        d  = o.get(name, {})
        p  = d.get("price", 0)
        ch = d.get("chg", 0)
        if p == 0: return f"  {name}: 수집 실패"
        return f"  {arrow(ch)} *{name}*: ${p:.2f} ({ch:+.2f}%)"

    wti_p    = o.get("WTI", {}).get("price", 0)
    oil_warn = ""
    if wti_p >= cfg["oil_high"]:
        oil_warn = "\n  🚨 *유가 경보! 코인 하방 압력*"
    elif 0 < wti_p <= cfg["oil_low"]:
        oil_warn = "\n  ✅ *유가 진정! 반등 기대*"

    v = fg["val"]
    return (
        f"📊 *Coin Direction 브리핑*\n"
        f"🕐 {now}\n"
        f"💱 환율: ₩{rate:,.0f}/USD\n"
        f"{'─'*28}\n"
        f"💰 *암호화폐 (업비트 KRW)*\n"
        f"{cline('BTC')}\n"
        f"{cline('DOGE')}\n"
        f"{cline('ETH')}\n"
        f"{cline('SOL')}\n"
        f"{cline('XRP')}\n\n"
        f"📊 *롱숏 비율*\n"
        f"  BTC  {ls_bar(bl['long'])} {bl['long']}%롱\n"
        f"  DOGE {ls_bar(dl['long'])} {dl['long']}%롱\n\n"
        f"{'─'*28}\n"
        f"🛢 *유가 (USD)*\n"
        f"{oline('WTI')}\n"
        f"{oline('Brent')}{oil_warn}\n\n"
        f"{'─'*28}\n"
        f"{fg_emoji(v)} *공포탐욕지수*: {v}/100  _{fg['label']}_"
    )


def build_btc_detail() -> str:
    c  = fetch_upbit_prices()
    ls = fetch_long_short("BTC")
    d  = c.get("BTC", {})
    if not d or d.get("price", 0) == 0:
        return "❌ BTC 데이터 수집 실패"
    p  = d["price"]
    ch = d["chg"]
    sign = "+" if d["chg_amt"] >= 0 else ""
    return (
        f"{'🚀' if ch>=0 else '📉'} *BTC (업비트 KRW)*\n"
        f"현재가: *{fmt_krw(p)}*\n"
        f"변동: {ch:+.2f}% ({sign}{fmt_krw(d['chg_amt'])})\n"
        f"고가: {fmt_krw(d['high'])}\n"
        f"저가: {fmt_krw(d['low'])}\n"
        f"전일: {fmt_krw(d['prev'])}\n"
        f"거래대금: {d['vol_krw']/1e12:.2f}조원\n\n"
        f"📊 롱숏: {ls_bar(ls['long'])}\n"
        f"롱 {ls['long']}% / 숏 {ls['short']}%\n\n"
        f"🟢 저항: 1.00억 → 1.05억 → 1.10억\n"
        f"🔴 지지: 9,500만 → 9,000만 → 8,500만"
    )


def build_doge_detail() -> str:
    c  = fetch_upbit_prices()
    ls = fetch_long_short("DOGE")
    d  = c.get("DOGE", {})
    if not d or d.get("price", 0) == 0:
        return "❌ DOGE 데이터 수집 실패"
    p  = d["price"]
    ch = d["chg"]
    signal = ""
    if p >= cfg["doge_high"]:
        signal = f"\n🚀 *{fmt_krw(cfg['doge_high'])} 돌파! 강세*"
    elif p <= cfg["doge_low"]:
        signal = f"\n⚠️ *{fmt_krw(cfg['doge_low'])} 이탈! 주의*"
    return (
        f"{'🐕🚀' if ch>=0 else '🐕📉'} *DOGE (업비트 KRW)*\n"
        f"현재가: *{fmt_krw(p)}*\n"
        f"변동: {ch:+.2f}%\n"
        f"고가: {fmt_krw(d['high'])}\n"
        f"저가: {fmt_krw(d['low'])}\n"
        f"전일: {fmt_krw(d['prev'])}\n\n"
        f"📊 롱숏: {ls_bar(ls['long'])}\n"
        f"롱 {ls['long']}% / 숏 {ls['short']}%\n\n"
        f"🟢 저항: {fmt_krw(150)} → {fmt_krw(160)}\n"
        f"🔴 지지: {fmt_krw(115)} → {fmt_krw(110)}"
        f"{signal}\n\n"
        f"💡 머스크 SNS: https://x.com/elonmusk"
    )


# ════════════════════════════════════════════
# 3. 자동 알림
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
            f"{fmt_krw(btc['price'])} (≥ {fmt_krw(cfg['btc_high'])})\n"
            f"변동: {btc.get('chg',0):+.2f}%"
        )
    elif 0 < btc.get("price", 999e6) <= cfg["btc_low"] and cd_ok("btc_low"):
        alerts.append(
            f"⚠️ *BTC 하단 이탈!*\n"
            f"{fmt_krw(btc['price'])} (≤ {fmt_krw(cfg['btc_low'])})\n"
            f"변동: {btc.get('chg',0):+.2f}%"
        )

    if doge.get("price", 0) >= cfg["doge_high"] and cd_ok("doge_high"):
        alerts.append(
            f"🐕🚀 *DOGE 상단 돌파!*\n"
            f"{fmt_krw(doge['price'])} (≥ {fmt_krw(cfg['doge_high'])})"
        )
    elif 0 < doge.get("price", 999) <= cfg["doge_low"] and cd_ok("doge_low"):
        alerts.append(
            f"🐕🔴 *DOGE 하단 이탈!*\n"
            f"{fmt_krw(doge['price'])} (≤ {fmt_krw(cfg['doge_low'])})"
        )

    if wti.get("price", 0) >= cfg["oil_high"] and cd_ok("oil_high"):
        alerts.append(f"🛢🚨 *WTI 급등!* ${wti['price']:.2f}\n코인 하방 압력!")
    elif 0 < wti.get("price", 999) <= cfg["oil_low"] and cd_ok("oil_low"):
        alerts.append(f"🛢✅ *WTI 진정!* ${wti['price']:.2f}\n반등 기대 🟢")

    if v <= 15 and cd_ok("fg_fear"):
        alerts.append(f"😱 *극단적 공포!* {v}/100\n매수 기회 신호")
    elif v >= 80 and cd_ok("fg_greed"):
        alerts.append(f"🤑 *극단적 탐욕!* {v}/100\n과열 주의")

    long_p = ls.get("long", 50)
    if long_p >= 75 and cd_ok("ls_long"):
        alerts.append(f"📊 *BTC 롱 과열!* {long_p}%\n하락 위험")
    elif long_p <= 35 and cd_ok("ls_short"):
        alerts.append(f"📊 *BTC 숏 과열!* {long_p}%롱\n반등 가능")

    return alerts


# ════════════════════════════════════════════
# 4. 명령어 핸들러
# ════════════════════════════════════════════

async def cmd_start(u: Update, _):
    await u.message.reply_text(
        "🤖 *Coin Direction 봇* (업비트 KRW)\n\n"
        "/now   전체 시황 브리핑\n"
        "/btc   BTC 상세\n"
        "/doge  DOGE 상세\n"
        "/oil   유가 현황\n"
        "/ls    롱숏 비율\n"
        "/fg    공포탐욕지수\n"
        "/rate  달러 환율\n"
        "/alert 알림 임계값\n"
        "/setalert KEY VALUE\n\n"
        "⏰ 자동알림: 임계값 돌파 즉시\n"
        "⏰ 정기브리핑: 1시간마다",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_now(u: Update, _):
    await u.message.reply_text("⏳ 업비트 데이터 수집 중...", parse_mode=ParseMode.MARKDOWN)
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
    if wti_p >= 100: warn = "\n\n🚨 *$100 돌파! 스태그플레이션 경보*"
    elif 0 < wti_p <= 80: warn = "\n\n✅ *$80 이하! 반등 기대*"
    await u.message.reply_text(
        f"🛢 *유가 현황 (USD)*\n"
        f"WTI:   ${wti_p:.2f} ({wti.get('chg',0):+.2f}%)\n"
        f"Brent: ${brent.get('price',0):.2f} ({brent.get('chg',0):+.2f}%)\n\n"
        f"🚨 위험: $100↑  🟡 중립: $80~95  ✅ 안전: $75↓"
        f"{warn}",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_ls(u: Update, _):
    bl = fetch_long_short("BTC")
    dl = fetch_long_short("DOGE")
    await u.message.reply_text(
        f"📊 *롱숏 비율*\n\n"
        f"*BTC*\n{ls_bar(bl['long'])}\n"
        f"롱 {bl['long']}% / 숏 {bl['short']}%\n\n"
        f"*DOGE*\n{ls_bar(dl['long'])}\n"
        f"롱 {dl['long']}% / 숏 {dl['short']}%\n\n"
        f"💡 롱 75%↑ → 과열  |  숏 65%↑ → 반등 가능",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_fg(u: Update, _):
    fg = fetch_fear_greed()
    v  = fg["val"]
    await u.message.reply_text(
        f"{fg_emoji(v)} *공포탐욕지수*\n\n"
        f"현재: *{v}/100*  {fg['label']}\n\n"
        f"😱 0~25 극단공포  😨 26~40 공포\n"
        f"😐 41~60 중립  😄 61~75 탐욕\n"
        f"🤑 76~100 극단탐욕",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_rate(u: Update, _):
    rate = fetch_usd_krw()
    await u.message.reply_text(
        f"💱 *달러/원 환율*\n\n*₩{rate:,.0f} / USD*",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_alert(u: Update, _):
    await u.message.reply_text(
        f"⚙️ *알림 임계값 (KRW/USD)*\n\n"
        f"BTC 상단:  {fmt_krw(cfg['btc_high'])}\n"
        f"BTC 하단:  {fmt_krw(cfg['btc_low'])}\n"
        f"DOGE 상단: {fmt_krw(cfg['doge_high'])}\n"
        f"DOGE 하단: {fmt_krw(cfg['doge_low'])}\n"
        f"WTI 상단:  ${cfg['oil_high']} USD\n"
        f"WTI 하단:  ${cfg['oil_low']} USD\n\n"
        f"`/setalert BTC_HIGH 110000000`\n"
        f"`/setalert DOGE_HIGH 160`",
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
            await u.message.reply_text(f"✅ {key} = {val:,} 변경 완료!")
        else:
            await u.message.reply_text(f"❌ 사용가능: {', '.join(mapping.keys())}")
    except:
        await u.message.reply_text(
            "사용법: `/setalert BTC_HIGH 110000000`",
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
            c  = fetch_upbit_prices()
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
        print("❌ TELEGRAM_BOT_TOKEN 없음"); return
    if not CHAT_ID:
        print("❌ TELEGRAM_CHAT_ID 없음"); return

    app = Application.builder().token(BOT_TOKEN).build()
    for cmd, fn in [
        ("start","cmd_start"),("now","cmd_now"),("btc","cmd_btc"),
        ("doge","cmd_doge"),("oil","cmd_oil"),("ls","cmd_ls"),
        ("fg","cmd_fg"),("rate","cmd_rate"),
        ("alert","cmd_alert"),("setalert","cmd_setalert"),
    ]:
        app.add_handler(CommandHandler(cmd, eval(fn)))

    app.post_init = lambda a: asyncio.get_event_loop().create_task(
        auto_monitor(a.bot)
    )
    print("🤖 Coin Direction 봇 시작! (업비트 KRW)")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

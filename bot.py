"""
bot.py  ─  Coin Direction 텔레그램 봇
           업비트 KRW + 다중 알림 + 인라인 버튼
실행: python bot.py
"""
import os, asyncio, requests, pytz
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from telegram.constants import ParseMode

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
CHECK_SEC = int(os.getenv("PRICE_CHECK_INTERVAL", 60))
BRIEF_SEC = int(os.getenv("BRIEF_INTERVAL", 3600))
KST = pytz.timezone("Asia/Seoul")

cfg = {
    "btc_high":  float(os.getenv("BTC_ALERT_HIGH",  105000000)),
    "btc_low":   float(os.getenv("BTC_ALERT_LOW",    95000000)),
    "oil_high":  float(os.getenv("OIL_ALERT_HIGH",         95)),
    "oil_low":   float(os.getenv("OIL_ALERT_LOW",          80)),
}
multi_alerts: dict = {
    "BTC":[], "DOGE":[], "ETH":[], "SOL":[], "XRP":[],
}
cooldown: dict = {}
COOLDOWN_SEC = 1800


# ════════════════════════════════════════════
# 1. 데이터 수집
# ════════════════════════════════════════════

def fetch_upbit_prices() -> dict:
    markets = {
        "BTC":"KRW-BTC","DOGE":"KRW-DOGE","ETH":"KRW-ETH",
        "SOL":"KRW-SOL","XRP":"KRW-XRP",
    }
    try:
        codes = ",".join(markets.values())
        r = requests.get(
            f"https://api.upbit.com/v1/ticker?markets={codes}",
            headers={"Accept":"application/json"}, timeout=10
        ).json()
        result = {}
        mmap = {v:k for k,v in markets.items()}
        for d in r:
            name = mmap.get(d["market"], d["market"])
            result[name] = {
                "price":   d["trade_price"],
                "chg":     round(d["signed_change_rate"]*100, 2),
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
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X",
            headers={"User-Agent":"Mozilla/5.0"}, timeout=10
        ).json()
        return r["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except:
        return 1300.0

def fetch_oil() -> dict:
    result = {}
    for name, ticker in [("WTI","CL=F"),("Brent","BZ=F")]:
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                headers={"User-Agent":"Mozilla/5.0"}, timeout=10
            ).json()
            meta = r["chart"]["result"][0]["meta"]
            p  = meta.get("regularMarketPrice",0)
            pc = meta.get("previousClose",p)
            result[name] = {"price":p, "chg":round((p-pc)/pc*100,2) if pc else 0}
        except:
            result[name] = {"price":0,"chg":0}
    return result

def fetch_fear_greed() -> dict:
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=1", timeout=10
        ).json()["data"][0]
        return {"val":int(r["value"]),"label":r["value_classification"]}
    except:
        return {"val":50,"label":"N/A"}

def fetch_long_short(symbol="BTC") -> dict:
    try:
        r = requests.get(
            "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
            params={"symbol":f"{symbol}USDT","period":"1h","limit":1},
            timeout=10
        ).json()
        lp = float(r[0]["longAccount"])*100
        sp = float(r[0]["shortAccount"])*100
        return {"long":round(lp,1),"short":round(sp,1)}
    except:
        return {"long":50.0,"short":50.0}


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

def ls_bar(lp: float) -> str:
    f = round(lp/10)
    return "🟩"*f + "🟥"*(10-f)

def fg_emoji(v):
    if v<=20: return "😱"
    if v<=40: return "😨"
    if v<=60: return "😐"
    if v<=75: return "😄"
    return "🤑"


# ════════════════════════════════════════════
# 3. 인라인 키보드 메뉴
# ════════════════════════════════════════════

def main_keyboard() -> InlineKeyboardMarkup:
    """메인 버튼 메뉴"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 전체 브리핑", callback_data="now"),
            InlineKeyboardButton("💱 환율",        callback_data="rate"),
        ],
        [
            InlineKeyboardButton("₿ BTC",   callback_data="btc"),
            InlineKeyboardButton("🐕 DOGE",  callback_data="doge"),
            InlineKeyboardButton("Ξ ETH",   callback_data="eth"),
        ],
        [
            InlineKeyboardButton("◎ SOL",   callback_data="sol"),
            InlineKeyboardButton("✕ XRP",   callback_data="xrp"),
            InlineKeyboardButton("🛢 유가",  callback_data="oil"),
        ],
        [
            InlineKeyboardButton("📊 롱숏",      callback_data="ls"),
            InlineKeyboardButton("😱 공포지수",  callback_data="fg"),
        ],
        [
            InlineKeyboardButton("🔔 알림목록",  callback_data="listalert_all"),
            InlineKeyboardButton("⚙️ 알림설정",  callback_data="alert_help"),
        ],
    ])

def coin_keyboard(coin: str) -> InlineKeyboardMarkup:
    """코인 상세 화면 버튼"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 새로고침",          callback_data=coin.lower()),
            InlineKeyboardButton("🔔 알림목록",          callback_data=f"listalert_{coin}"),
        ],
        [
            InlineKeyboardButton("🟢 상향알림 추가",     callback_data=f"add_up_{coin}"),
            InlineKeyboardButton("🔴 하향알림 추가",     callback_data=f"add_down_{coin}"),
        ],
        [
            InlineKeyboardButton("◀ 메인메뉴",           callback_data="menu"),
        ],
    ])

def back_keyboard() -> InlineKeyboardMarkup:
    """뒤로가기 버튼"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 새로고침", callback_data="now"),
            InlineKeyboardButton("◀ 메인메뉴", callback_data="menu"),
        ]
    ])


# ════════════════════════════════════════════
# 4. 컨텐츠 빌더
# ════════════════════════════════════════════

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
        if not d or "_err" in c or not d.get("price"):
            return f"  {name}: 수집 실패"
        return f"  {arrow(d['chg'])} *{name}*: {fmt_krw(d['price'])} ({d['chg']:+.2f}%)"

    def oline(name):
        d = o.get(name,{}); p = d.get("price",0); ch = d.get("chg",0)
        if not p: return f"  {name}: 수집 실패"
        return f"  {arrow(ch)} *{name}*: ${p:.2f} ({ch:+.2f}%)"

    wti_p = o.get("WTI",{}).get("price",0)
    oil_warn = ""
    if wti_p >= cfg["oil_high"]:      oil_warn = "\n  🚨 *유가 경보!*"
    elif 0 < wti_p <= cfg["oil_low"]: oil_warn = "\n  ✅ *유가 진정!*"

    v = fg["val"]
    return (
        f"📊 *Coin Direction 브리핑*\n"
        f"🕐 {now}\n💱 환율: ₩{rate:,.0f}/USD\n"
        f"{'─'*26}\n"
        f"💰 *암호화폐 (업비트 KRW)*\n"
        f"{cline('BTC')}\n{cline('DOGE')}\n"
        f"{cline('ETH')}\n{cline('SOL')}\n{cline('XRP')}\n\n"
        f"📊 *롱숏*\n"
        f"  BTC  {ls_bar(bl['long'])} {bl['long']}%\n"
        f"  DOGE {ls_bar(dl['long'])} {dl['long']}%\n\n"
        f"{'─'*26}\n"
        f"🛢 *유가*\n{oline('WTI')}\n{oline('Brent')}{oil_warn}\n\n"
        f"{'─'*26}\n"
        f"{fg_emoji(v)} *공포탐욕지수*: {v}/100 _{fg['label']}_"
    )


def build_coin_detail(coin: str) -> str:
    c  = fetch_upbit_prices()
    ls = fetch_long_short(coin)
    d  = c.get(coin, {})
    if not d or not d.get("price"):
        return f"❌ {coin} 데이터 수집 실패"

    p  = d["price"]; ch = d["chg"]
    sign = "+" if d["chg_amt"] >= 0 else ""
    fmt = fmt_krw

    # 다중 알림 목록
    al = multi_alerts.get(coin, [])
    al_str = "\n\n🔔 *등록된 알림 없음*"
    if al:
        al_str = "\n\n🔔 *등록된 알림*\n"
        for a in sorted(al, key=lambda x: x["price"], reverse=True):
            d_icon = "🟢↑" if a["dir"]=="up" else "🔴↓"
            lbl    = f" [{a['label']}]" if a.get("label") else ""
            done   = " ✅" if (a["dir"]=="up" and p>=a["price"]) else \
                     " 🔴" if (a["dir"]=="down" and p<=a["price"]) else ""
            al_str += f"  {d_icon} {fmt(a['price'])}{lbl}{done}\n"

    emoji = {"BTC":"₿","DOGE":"🐕","ETH":"Ξ","SOL":"◎","XRP":"✕"}.get(coin, coin)
    return (
        f"{emoji} *{coin} 상세 (업비트 KRW)*\n"
        f"현재가: *{fmt(p)}*\n"
        f"변동: {ch:+.2f}% ({sign}{fmt(d['chg_amt'])})\n"
        f"고가: {fmt(d['high'])}  저가: {fmt(d['low'])}\n"
        f"전일: {fmt(d['prev'])}\n"
        f"거래대금: {d['vol_krw']/1e12:.2f}조원\n\n"
        f"📊 롱숏: {ls_bar(ls['long'])} {ls['long']}%롱"
        f"{al_str}"
    )


def build_listalert(coin: str = None) -> str:
    if coin and coin in multi_alerts:
        al = multi_alerts[coin]
        c  = fetch_upbit_prices()
        cur = c.get(coin, {}).get("price", 0)
        if not al:
            return (
                f"🔔 *{coin} 등록된 알림 없음*\n\n"
                f"아래 버튼으로 추가하세요 👇"
            )
        msg = f"🔔 *{coin} 알림 목록*\n"
        if cur: msg += f"현재가: {fmt_krw(cur)}\n"
        msg += "─"*20 + "\n"
        for a in sorted(al, key=lambda x: x["price"], reverse=True):
            d_icon = "🟢↑" if a["dir"]=="up" else "🔴↓"
            lbl    = f" [{a['label']}]" if a.get("label") else ""
            done   = " ✅달성" if (a["dir"]=="up" and cur >= a["price"]) else \
                     " 🔴이탈" if (a["dir"]=="down" and cur <= a["price"]) else ""
            msg += f"{d_icon} {fmt_krw(a['price'])}{lbl}{done}\n"
        msg += f"\n총 {len(al)}개"
        return msg
    else:
        msg = "🔔 *전체 알림 현황*\n\n"
        total = 0
        for cn, al in multi_alerts.items():
            if al:
                msg += f"*{cn}* ({len(al)}개)\n"
                for a in sorted(al, key=lambda x: x["price"], reverse=True):
                    d_icon = "🟢↑" if a["dir"]=="up" else "🔴↓"
                    lbl    = f" [{a['label']}]" if a.get("label") else ""
                    msg += f"  {d_icon} {fmt_krw(a['price'])}{lbl}\n"
                total += len(al)
        if total == 0:
            msg += "등록된 알림 없음\n\n/addalert DOGE 130 상향 1차목표"
        return msg


# ════════════════════════════════════════════
# 5. 콜백 핸들러 (버튼 클릭)
# ════════════════════════════════════════════

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # 로딩 스피너 제거
    data  = query.data

    # ── 메인 메뉴 ──
    if data == "menu":
        await query.edit_message_text(
            "🤖 *Coin Direction*\n원하는 항목을 선택하세요:",
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )

    # ── 전체 브리핑 ──
    elif data == "now":
        await query.edit_message_text(
            "⏳ 수집 중...",
            reply_markup=None,
            parse_mode=ParseMode.MARKDOWN
        )
        text = build_full_brief()
        await query.edit_message_text(
            text,
            reply_markup=back_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )

    # ── 환율 ──
    elif data == "rate":
        rate = fetch_usd_krw()
        await query.edit_message_text(
            f"💱 *달러/원 환율*\n\n*₩{rate:,.0f} / USD*",
            reply_markup=back_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )

    # ── 코인 상세 ──
    elif data in ["btc","doge","eth","sol","xrp"]:
        coin = data.upper()
        await query.edit_message_text(
            "⏳ 수집 중...", parse_mode=ParseMode.MARKDOWN
        )
        text = build_coin_detail(coin)
        await query.edit_message_text(
            text,
            reply_markup=coin_keyboard(coin),
            parse_mode=ParseMode.MARKDOWN
        )

    # ── 유가 ──
    elif data == "oil":
        o = fetch_oil()
        wti = o.get("WTI",{}); brent = o.get("Brent",{})
        wti_p = wti.get("price",0)
        warn = ""
        if wti_p >= 100: warn = "\n\n🚨 *$100 돌파! 스태그플레이션 경보*"
        elif 0 < wti_p <= 80: warn = "\n\n✅ *$80 이하! 반등 기대*"
        await query.edit_message_text(
            f"🛢 *유가 현황 (USD)*\n"
            f"WTI:   ${wti_p:.2f} ({wti.get('chg',0):+.2f}%)\n"
            f"Brent: ${brent.get('price',0):.2f} ({brent.get('chg',0):+.2f}%)\n\n"
            f"🚨 위험: $100↑  🟡 중립: $80~95  ✅ 안전: $75↓{warn}",
            reply_markup=back_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )

    # ── 롱숏 ──
    elif data == "ls":
        bl = fetch_long_short("BTC"); dl = fetch_long_short("DOGE")
        await query.edit_message_text(
            f"📊 *롱숏 비율*\n\n"
            f"*BTC*\n{ls_bar(bl['long'])}\n롱 {bl['long']}% / 숏 {bl['short']}%\n\n"
            f"*DOGE*\n{ls_bar(dl['long'])}\n롱 {dl['long']}% / 숏 {dl['short']}%\n\n"
            f"💡 롱 75%↑ → 과열  |  숏 65%↑ → 반등 가능",
            reply_markup=back_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )

    # ── 공포지수 ──
    elif data == "fg":
        fg = fetch_fear_greed(); v = fg["val"]
        await query.edit_message_text(
            f"{fg_emoji(v)} *공포탐욕지수*\n\n"
            f"현재: *{v}/100*  {fg['label']}\n\n"
            f"😱 0~25 극단공포  😨 26~40 공포\n"
            f"😐 41~60 중립  😄 61~75 탐욕  🤑 76~100 극단탐욕",
            reply_markup=back_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )

    # ── 알림 목록 ──
    elif data == "listalert_all":
        text = build_listalert()
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("◀ 메인메뉴", callback_data="menu")
        ]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("listalert_"):
        coin = data.replace("listalert_","")
        text = build_listalert(coin)
        await query.edit_message_text(
            text,
            reply_markup=coin_keyboard(coin),
            parse_mode=ParseMode.MARKDOWN
        )

    # ── 알림 추가 안내 ──
    elif data.startswith("add_up_") or data.startswith("add_down_"):
        direction = "상향" if data.startswith("add_up_") else "하향"
        coin = data.split("_")[-1]
        d_cmd = "상향" if direction == "상향" else "하향"
        example_prices = {
            "BTC":  ["100000000", "105000000", "110000000"],
            "DOGE": ["130", "150", "170"],
            "ETH":  ["3000000", "3500000", "4000000"],
            "SOL":  ["200000", "250000", "300000"],
            "XRP":  ["1000", "1500", "2000"],
        }.get(coin, ["100", "200", "300"])
        examples = "\n".join([
            f"`/addalert {coin} {p} {d_cmd} {i+1}차목표`"
            for i, p in enumerate(example_prices)
        ])
        await query.edit_message_text(
            f"🔔 *{coin} {direction} 알림 추가*\n\n"
            f"아래 명령어를 입력하세요:\n\n"
            f"{examples}\n\n"
            f"형식: `/addalert {coin} [가격] {d_cmd} [이름]`",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀ 돌아가기", callback_data=coin.lower())
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )

    # ── 알림 설정 도움말 ──
    elif data == "alert_help":
        await query.edit_message_text(
            "⚙️ *알림 설정 명령어*\n\n"
            "📌 *다중 알림 추가*\n"
            "`/addalert DOGE 130 상향 1차목표`\n"
            "`/addalert DOGE 110 하향 지지선`\n"
            "`/addalert BTC 100000000 상향 1억`\n\n"
            "📋 *알림 목록*\n"
            "`/listalert DOGE`\n"
            "`/listalert` (전체)\n\n"
            "🗑️ *알림 삭제*\n"
            "`/delalert DOGE 130`\n"
            "`/clearalert DOGE` (전체삭제)\n\n"
            "📌 *기본 BTC/유가 알림*\n"
            "`/setalert BTC_HIGH 110000000`\n"
            "`/setalert OIL_HIGH 100`",
            reply_markup=back_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )


# ════════════════════════════════════════════
# 6. 텍스트 명령어 핸들러
# ════════════════════════════════════════════

async def cmd_start(u: Update, _):
    await u.message.reply_text(
        "🤖 *Coin Direction* (업비트 KRW)\n\n"
        "아래 버튼을 눌러 바로 확인하세요! 👇",
        reply_markup=main_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_menu(u: Update, _):
    await u.message.reply_text(
        "📊 *메인 메뉴*\n원하는 항목을 선택하세요:",
        reply_markup=main_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_now(u: Update, _):
    msg = await u.message.reply_text("⏳ 수집 중...", parse_mode=ParseMode.MARKDOWN)
    text = build_full_brief()
    await msg.edit_text(text, reply_markup=back_keyboard(), parse_mode=ParseMode.MARKDOWN)

async def cmd_btc(u: Update, _):
    text = build_coin_detail("BTC")
    await u.message.reply_text(text, reply_markup=coin_keyboard("BTC"), parse_mode=ParseMode.MARKDOWN)

async def cmd_doge(u: Update, _):
    text = build_coin_detail("DOGE")
    await u.message.reply_text(text, reply_markup=coin_keyboard("DOGE"), parse_mode=ParseMode.MARKDOWN)

async def cmd_eth(u: Update, _):
    text = build_coin_detail("ETH")
    await u.message.reply_text(text, reply_markup=coin_keyboard("ETH"), parse_mode=ParseMode.MARKDOWN)

async def cmd_sol(u: Update, _):
    text = build_coin_detail("SOL")
    await u.message.reply_text(text, reply_markup=coin_keyboard("SOL"), parse_mode=ParseMode.MARKDOWN)

async def cmd_oil(u: Update, _):
    o = fetch_oil()
    wti = o.get("WTI",{}); brent = o.get("Brent",{})
    wti_p = wti.get("price",0)
    warn = ""
    if wti_p >= 100: warn = "\n\n🚨 *$100 돌파! 스태그플레이션 경보*"
    elif 0 < wti_p <= 80: warn = "\n\n✅ *$80 이하! 반등 기대*"
    await u.message.reply_text(
        f"🛢 *유가 현황 (USD)*\n"
        f"WTI:   ${wti_p:.2f} ({wti.get('chg',0):+.2f}%)\n"
        f"Brent: ${brent.get('price',0):.2f} ({brent.get('chg',0):+.2f}%){warn}",
        reply_markup=back_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_addalert(u: Update, _):
    try:
        parts = u.message.text.split()
        coin  = parts[1].upper()
        price = float(parts[2])
        direction = parts[3] if len(parts) > 3 else "상향"
        label = parts[4] if len(parts) > 4 else ""

        if coin not in multi_alerts:
            await u.message.reply_text(f"❌ 지원 코인: {', '.join(multi_alerts.keys())}"); return

        dir_en = "up" if direction in ["상향","up","위","상"] else "down"
        dir_kr = "🟢↑ 상향" if dir_en=="up" else "🔴↓ 하향"

        for a in multi_alerts[coin]:
            if a["price"]==price and a["dir"]==dir_en:
                await u.message.reply_text(f"⚠️ 이미 등록: {coin} {dir_kr} {fmt_krw(price)}"); return

        multi_alerts[coin].append({"price":price,"dir":dir_en,"label":label})
        lbl_str = f" [{label}]" if label else ""
        await u.message.reply_text(
            f"✅ *{coin} 알림 추가!*\n"
            f"{dir_kr}: {fmt_krw(price)}{lbl_str}\n"
            f"총 {len(multi_alerts[coin])}개 등록",
            reply_markup=coin_keyboard(coin),
            parse_mode=ParseMode.MARKDOWN
        )
    except:
        await u.message.reply_text(
            "사용법: `/addalert DOGE 130 상향 1차목표`",
            parse_mode=ParseMode.MARKDOWN
        )

async def cmd_listalert(u: Update, _):
    parts = u.message.text.split()
    coin  = parts[1].upper() if len(parts)>1 else None
    text  = build_listalert(coin)
    kb = coin_keyboard(coin) if coin else back_keyboard()
    await u.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def cmd_delalert(u: Update, _):
    try:
        parts = u.message.text.split()
        coin  = parts[1].upper(); price = float(parts[2])
        if coin not in multi_alerts:
            await u.message.reply_text(f"❌ 지원 코인: {', '.join(multi_alerts.keys())}"); return
        before = len(multi_alerts[coin])
        multi_alerts[coin] = [a for a in multi_alerts[coin] if a["price"]!=price]
        after = len(multi_alerts[coin])
        if before > after:
            await u.message.reply_text(
                f"🗑️ *{coin} {fmt_krw(price)} 삭제 완료*\n남은 알림: {after}개",
                reply_markup=coin_keyboard(coin),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await u.message.reply_text(f"⚠️ {fmt_krw(price)} 알림 없음")
    except:
        await u.message.reply_text("사용법: `/delalert DOGE 130`", parse_mode=ParseMode.MARKDOWN)

async def cmd_clearalert(u: Update, _):
    try:
        coin = u.message.text.split()[1].upper()
        if coin not in multi_alerts:
            await u.message.reply_text(f"❌ 지원 코인: {', '.join(multi_alerts.keys())}"); return
        count = len(multi_alerts[coin])
        multi_alerts[coin] = []
        await u.message.reply_text(
            f"🗑️ *{coin} 알림 전체 삭제*\n{count}개 삭제 완료",
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
    except:
        await u.message.reply_text("사용법: `/clearalert DOGE`", parse_mode=ParseMode.MARKDOWN)

async def cmd_setalert(u: Update, _):
    try:
        parts = u.message.text.split()
        key, val = parts[1].upper(), float(parts[2])
        mapping = {
            "BTC_HIGH":"btc_high","BTC_LOW":"btc_low",
            "OIL_HIGH":"oil_high","OIL_LOW":"oil_low",
        }
        if key in mapping:
            cfg[mapping[key]] = val
            await u.message.reply_text(f"✅ {key} = {val:,.0f} 변경 완료!")
        else:
            await u.message.reply_text(f"❌ 사용가능: {', '.join(mapping.keys())}")
    except:
        await u.message.reply_text("사용법: `/setalert BTC_HIGH 110000000`", parse_mode=ParseMode.MARKDOWN)


# ════════════════════════════════════════════
# 7. 자동 모니터링
# ════════════════════════════════════════════

def cd_ok(key: str) -> bool:
    now = datetime.now().timestamp()
    if key in cooldown and now-cooldown[key] < COOLDOWN_SEC: return False
    cooldown[key] = now
    return True

def check_all_alerts(c, o, fg) -> list:
    alerts = []
    btc = c.get("BTC",{}); wti = o.get("WTI",{}); v = fg.get("val",50)

    if btc.get("price",0) >= cfg["btc_high"] and cd_ok("btc_high"):
        alerts.append(f"🚀 *BTC 상단!* {fmt_krw(btc['price'])} ({btc.get('chg',0):+.2f}%)")
    elif 0 < btc.get("price",999e6) <= cfg["btc_low"] and cd_ok("btc_low"):
        alerts.append(f"⚠️ *BTC 하단!* {fmt_krw(btc['price'])} ({btc.get('chg',0):+.2f}%)")
    if wti.get("price",0) >= cfg["oil_high"] and cd_ok("oil_high"):
        alerts.append(f"🛢🚨 *WTI 급등!* ${wti['price']:.2f}")
    elif 0 < wti.get("price",999) <= cfg["oil_low"] and cd_ok("oil_low"):
        alerts.append(f"🛢✅ *WTI 진정!* ${wti['price']:.2f}")
    if v <= 15 and cd_ok("fg_fear"):
        alerts.append(f"😱 *극단 공포!* {v}/100")
    elif v >= 80 and cd_ok("fg_greed"):
        alerts.append(f"🤑 *극단 탐욕!* {v}/100")

    # 다중 알림
    for coin, al in multi_alerts.items():
        cur = c.get(coin,{}).get("price",0)
        if not cur: continue
        for a in al:
            key = f"multi_{coin}_{a['price']}"
            lbl = f" [{a['label']}]" if a.get("label") else ""
            if a["dir"]=="up" and cur >= a["price"] and cd_ok(key):
                alerts.append(
                    f"🚀 *{coin} 상향 돌파!{lbl}*\n"
                    f"{fmt_krw(cur)} ≥ {fmt_krw(a['price'])} ✅"
                )
            elif a["dir"]=="down" and cur <= a["price"] and cd_ok(key):
                alerts.append(
                    f"⚠️ *{coin} 하향 이탈!{lbl}*\n"
                    f"{fmt_krw(cur)} ≤ {fmt_krw(a['price'])} 🔴"
                )
    return alerts


async def auto_monitor(bot: Bot):
    counter = 0
    print(f"✅ 모니터링 시작 (체크:{CHECK_SEC}초 / 브리핑:{BRIEF_SEC}초)")
    while True:
        try:
            c  = fetch_upbit_prices()
            o  = fetch_oil()
            fg = fetch_fear_greed()
            for msg in check_all_alerts(c, o, fg):
                await bot.send_message(
                    CHAT_ID, msg,
                    reply_markup=main_keyboard(),
                    parse_mode=ParseMode.MARKDOWN
                )
            counter += CHECK_SEC
            if counter >= BRIEF_SEC:
                await bot.send_message(
                    CHAT_ID,
                    f"⏰ *정기 브리핑*\n{build_full_brief()}",
                    reply_markup=main_keyboard(),
                    parse_mode=ParseMode.MARKDOWN
                )
                counter = 0
        except Exception as e:
            print(f"[오류] {e}")
        await asyncio.sleep(CHECK_SEC)


# ════════════════════════════════════════════
# 8. 메인
# ════════════════════════════════════════════

def main():
    if not BOT_TOKEN: print("❌ TELEGRAM_BOT_TOKEN 없음"); return
    if not CHAT_ID:   print("❌ TELEGRAM_CHAT_ID 없음");   return

    app = Application.builder().token(BOT_TOKEN).build()

    for cmd, fn in [
        ("start",      cmd_start),
        ("menu",       cmd_menu),
        ("now",        cmd_now),
        ("btc",        cmd_btc),
        ("doge",       cmd_doge),
        ("eth",        cmd_eth),
        ("sol",        cmd_sol),
        ("oil",        cmd_oil),
        ("addalert",   cmd_addalert),
        ("listalert",  cmd_listalert),
        ("delalert",   cmd_delalert),
        ("clearalert", cmd_clearalert),
        ("setalert",   cmd_setalert),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    # 버튼 클릭 핸들러
    app.add_handler(CallbackQueryHandler(button_handler))

    app.post_init = lambda a: asyncio.get_event_loop().create_task(
        auto_monitor(a.bot)
    )

    print("🤖 Coin Direction 봇 시작! (버튼 메뉴 + 다중 알림)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

"""
bot.py  ─  Coin Direction 텔레그램 봇
           업비트 KRW + 다중 알림 + 인라인 버튼
           수정: edit 실패시 새 메시지로 fallback
"""
import os, asyncio, requests, pytz, traceback
from flask import Flask
import threading
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError

load_dotenv()

# Railway health server
app = Flask(__name__)
@app.route("/")
def home():
    return "Coin Direction Bot Running"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web).start()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
CHECK_SEC = int(os.getenv("PRICE_CHECK_INTERVAL", 60))
BRIEF_SEC = int(os.getenv("BRIEF_INTERVAL", 3600))
KST = pytz.timezone("Asia/Seoul")

cfg = {
    "btc_high": float(os.getenv("BTC_ALERT_HIGH", 105000000)),
    "btc_low":  float(os.getenv("BTC_ALERT_LOW",   95000000)),
    "oil_high": float(os.getenv("OIL_ALERT_HIGH",        95)),
    "oil_low":  float(os.getenv("OIL_ALERT_LOW",         80)),
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
        mmap = {v:k for k,v in markets.items()}
        result = {}
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
        print(f"[upbit 오류] {e}")
        return {}

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
            p  = meta.get("regularMarketPrice", 0)
            pc = meta.get("previousClose", p)
            result[name] = {
                "price": p,
                "chg":   round((p-pc)/pc*100, 2) if pc else 0
            }
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
# 2. 유틸리티
# ════════════════════════════════════════════

def arrow(chg): return "+" if chg >= 0 else "-"  # 마크다운 안전 문자

def fmt_krw(price: float) -> str:
    if price >= 100_000_000:
        return f"{price/100_000_000:.2f}억원"
    elif price >= 10_000:
        return f"{price/10_000:.0f}만원"
    else:
        return f"{price:,.0f}원"

def chg_icon(chg): return "🟢" if chg >= 0 else "🔴"

def ls_bar(lp: float) -> str:
    f = max(0, min(10, round(lp/10)))
    return "🟩"*f + "🟥"*(10-f)

def fg_emoji(v):
    if v<=20: return "😱"
    if v<=40: return "😨"
    if v<=60: return "😐"
    if v<=75: return "😄"
    return "🤑"

def cd_ok(key: str) -> bool:
    now = datetime.now().timestamp()
    if key in cooldown and now-cooldown[key] < COOLDOWN_SEC:
        return False
    cooldown[key] = now
    return True


# ════════════════════════════════════════════
# safe_edit: 실패하면 새 메시지로 fallback
# ════════════════════════════════════════════

async def safe_edit(query, text: str, keyboard=None):
    """edit 실패시 새 메시지로 자동 전송"""
    try:
        await query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML   # ★ HTML 모드 사용 (마크다운 오류 방지)
        )
    except BadRequest as e:
        err = str(e).lower()
        if "not modified" in err:
            return  # 동일 내용 - 무시
        # 그 외 오류 → 새 메시지로 전송
        print(f"[edit 실패→새메시지] {e}")
        try:
            await query.message.reply_text(
                text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
        except Exception as e2:
            print(f"[새메시지도 실패] {e2}")
    except Exception as e:
        print(f"[safe_edit 오류] {e}")
        try:
            await query.message.reply_text(
                text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
        except:
            pass


# ════════════════════════════════════════════
# 3. 키보드 정의
# ════════════════════════════════════════════

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 전체브리핑", callback_data="now"),
            InlineKeyboardButton("💱 환율",       callback_data="rate"),
        ],
        [
            InlineKeyboardButton("₿ BTC",  callback_data="coin_BTC"),
            InlineKeyboardButton("🐕 DOGE", callback_data="coin_DOGE"),
            InlineKeyboardButton("Ξ ETH",  callback_data="coin_ETH"),
        ],
        [
            InlineKeyboardButton("◎ SOL",  callback_data="coin_SOL"),
            InlineKeyboardButton("✕ XRP",  callback_data="coin_XRP"),
            InlineKeyboardButton("🛢 유가", callback_data="oil"),
        ],
        [
            InlineKeyboardButton("📊 롱숏",     callback_data="ls"),
            InlineKeyboardButton("😱 공포지수", callback_data="fg"),
        ],
        [
            InlineKeyboardButton("🔔 알림목록",   callback_data="listalert_ALL"),
            InlineKeyboardButton("⚙️ 알림도움말", callback_data="alert_help"),
        ],
    ])

def kb_coin(coin: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 새로고침",      callback_data=f"coin_{coin}"),
            InlineKeyboardButton("🔔 알림목록",      callback_data=f"listalert_{coin}"),
        ],
        [
            InlineKeyboardButton("🟢 상향알림 추가", callback_data=f"addup_{coin}"),
            InlineKeyboardButton("🔴 하향알림 추가", callback_data=f"adddown_{coin}"),
        ],
        [InlineKeyboardButton("◀ 메인메뉴", callback_data="menu")],
    ])

def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 새로고침", callback_data="now"),
        InlineKeyboardButton("◀ 메인메뉴", callback_data="menu"),
    ]])

def kb_alert_list(coin: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 상향추가", callback_data=f"addup_{coin}"),
            InlineKeyboardButton("🔴 하향추가", callback_data=f"adddown_{coin}"),
        ],
        [InlineKeyboardButton(f"🗑 {coin} 전체삭제", callback_data=f"clearalert_{coin}")],
        [InlineKeyboardButton("◀ 돌아가기", callback_data=f"coin_{coin}")],
    ])


# ════════════════════════════════════════════
# 4. 컨텐츠 빌더 (HTML 태그 사용)
# ════════════════════════════════════════════

def build_brief() -> str:
    now  = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    c    = fetch_upbit_prices()
    o    = fetch_oil()
    fg   = fetch_fear_greed()
    bl   = fetch_long_short("BTC")
    dl   = fetch_long_short("DOGE")
    rate = fetch_usd_krw()

    def cline(name):
        d = c.get(name)
        if not d or not d.get("price"):
            return f"  {name}: 수집 실패"
        ic = chg_icon(d['chg'])
        return f"  {ic} <b>{name}</b>: {fmt_krw(d['price'])} ({d['chg']:+.2f}%)"

    def oline(name):
        d = o.get(name,{}); p = d.get("price",0); ch = d.get("chg",0)
        if not p: return f"  {name}: 수집 실패"
        return f"  {chg_icon(ch)} <b>{name}</b>: ${p:.2f} ({ch:+.2f}%)"

    wti_p = o.get("WTI",{}).get("price",0)
    oil_warn = ""
    if wti_p >= cfg["oil_high"]:       oil_warn = "\n  🚨 유가 경보!"
    elif 0 < wti_p <= cfg["oil_low"]:  oil_warn = "\n  ✅ 유가 진정!"
    v = fg["val"]
    return (
        f"<b>📊 Coin Direction 브리핑</b>\n"
        f"🕐 {now}\n"
        f"💱 환율: ₩{rate:,.0f}/USD\n"
        f"{'─'*24}\n"
        f"<b>💰 암호화폐 (업비트 KRW)</b>\n"
        f"{cline('BTC')}\n{cline('DOGE')}\n"
        f"{cline('ETH')}\n{cline('SOL')}\n{cline('XRP')}\n\n"
        f"<b>📊 롱숏</b>\n"
        f"  BTC  {ls_bar(bl['long'])} {bl['long']}%\n"
        f"  DOGE {ls_bar(dl['long'])} {dl['long']}%\n\n"
        f"{'─'*24}\n"
        f"<b>🛢 유가</b>\n{oline('WTI')}\n{oline('Brent')}{oil_warn}\n\n"
        f"{fg_emoji(v)} 공포탐욕: <b>{v}/100</b> {fg['label']}"
    )

def build_coin(coin: str) -> str:
    c  = fetch_upbit_prices()
    ls = fetch_long_short(coin)
    d  = c.get(coin, {})
    if not d or not d.get("price"):
        return f"❌ {coin} 데이터 수집 실패\n잠시 후 새로고침 해주세요"
    p = d["price"]; ch = d["chg"]
    sign = "+" if d["chg_amt"]>=0 else ""
    al = multi_alerts.get(coin, [])
    al_str = "\n\n🔔 <b>등록된 알림 없음</b>\n아래 버튼으로 추가하세요"
    if al:
        al_str = "\n\n🔔 <b>등록 알림</b>\n"
        for a in sorted(al, key=lambda x:x["price"], reverse=True):
            icon = "🟢↑" if a["dir"]=="up" else "🔴↓"
            lbl  = f" [{a['label']}]" if a.get("label") else ""
            done = " ✅" if (a["dir"]=="up" and p>=a["price"]) else \
                   " 🔴" if (a["dir"]=="down" and p<=a["price"]) else ""
            al_str += f"  {icon} {fmt_krw(a['price'])}{lbl}{done}\n"
    emoji = {"BTC":"₿","DOGE":"🐕","ETH":"Ξ","SOL":"◎","XRP":"✕"}.get(coin, coin)
    return (
        f"{emoji} <b>{coin} (업비트 KRW)</b>\n"
        f"현재가: <b>{fmt_krw(p)}</b>\n"
        f"변동: {ch:+.2f}% ({sign}{fmt_krw(d['chg_amt'])})\n"
        f"고가: {fmt_krw(d['high'])}  저가: {fmt_krw(d['low'])}\n"
        f"전일: {fmt_krw(d['prev'])}\n"
        f"거래대금: {d['vol_krw']/1e12:.2f}조원\n\n"
        f"📊 롱숏: {ls_bar(ls['long'])} {ls['long']}%롱"
        f"{al_str}"
    )

def build_listalert(coin: str) -> str:
    if coin == "ALL":
        msg = "<b>🔔 전체 알림 현황</b>\n\n"
        total = 0
        for cn, al in multi_alerts.items():
            if al:
                msg += f"<b>{cn}</b> ({len(al)}개)\n"
                for a in sorted(al, key=lambda x:x["price"], reverse=True):
                    icon = "🟢↑" if a["dir"]=="up" else "🔴↓"
                    lbl  = f" [{a['label']}]" if a.get("label") else ""
                    msg += f"  {icon} {fmt_krw(a['price'])}{lbl}\n"
                total += len(al)
        if total == 0:
            msg += "등록된 알림 없음\n\n/addalert DOGE 130 상향 1차목표"
        return msg
    al  = multi_alerts.get(coin, [])
    c   = fetch_upbit_prices()
    cur = c.get(coin,{}).get("price",0)
    if not al:
        return f"🔔 <b>{coin} 등록 알림 없음</b>\n아래 버튼으로 추가하세요 👇"
    msg = f"🔔 <b>{coin} 알림 목록</b>\n"
    if cur: msg += f"현재가: {fmt_krw(cur)}\n"
    msg += "─"*16 + "\n"
    for a in sorted(al, key=lambda x:x["price"], reverse=True):
        icon = "🟢↑" if a["dir"]=="up" else "🔴↓"
        lbl  = f" [{a['label']}]" if a.get("label") else ""
        done = " ✅달성" if (a["dir"]=="up" and cur and cur>=a["price"]) else \
               " 🔴이탈" if (a["dir"]=="down" and cur and cur<=a["price"]) else ""
        msg += f"{icon} {fmt_krw(a['price'])}{lbl}{done}\n"
    msg += f"\n총 {len(al)}개"
    return msg


# ════════════════════════════════════════════
# 5. 콜백 핸들러
# ════════════════════════════════════════════

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data
    print(f"[버튼] {data}")

    try:
        if data == "menu":
            await safe_edit(query,
                "🤖 <b>Coin Direction</b>\n원하는 항목을 선택하세요 👇",
                kb_main()
            )

        elif data == "now":
            await safe_edit(query, "⏳ 데이터 수집 중...", None)
            await safe_edit(query, build_brief(), kb_back())

        elif data == "rate":
            rate = fetch_usd_krw()
            await safe_edit(query,
                f"💱 <b>달러/원 환율</b>\n\n<b>₩{rate:,.0f} / USD</b>",
                kb_back()
            )

        elif data.startswith("coin_"):
            coin = data.replace("coin_", "")
            await safe_edit(query, f"⏳ {coin} 조회 중...", None)
            await safe_edit(query, build_coin(coin), kb_coin(coin))

        elif data == "oil":
            o = fetch_oil()
            wti = o.get("WTI",{}); brent = o.get("Brent",{})
            wti_p = wti.get("price",0)
            warn = ""
            if wti_p >= 100: warn = "\n🚨 $100 돌파! 스태그플레이션 경보"
            elif 0 < wti_p <= 80: warn = "\n✅ $80 이하! 반등 기대"
            await safe_edit(query,
                f"🛢 <b>유가 (USD)</b>\n\n"
                f"WTI:   ${wti_p:.2f} ({wti.get('chg',0):+.2f}%)\n"
                f"Brent: ${brent.get('price',0):.2f} ({brent.get('chg',0):+.2f}%)\n\n"
                f"🚨 위험: $100↑  🟡 중립: $80~95  ✅ 안전: $75↓{warn}",
                kb_back()
            )

        elif data == "ls":
            bl = fetch_long_short("BTC"); dl = fetch_long_short("DOGE")
            await safe_edit(query,
                f"<b>📊 롱숏 비율</b>\n\n"
                f"BTC\n{ls_bar(bl['long'])} {bl['long']}%롱\n\n"
                f"DOGE\n{ls_bar(dl['long'])} {dl['long']}%롱\n\n"
                f"롱 75%↑ → 과열  |  숏 65%↑ → 반등 가능",
                kb_back()
            )

        elif data == "fg":
            fg = fetch_fear_greed(); v = fg["val"]
            await safe_edit(query,
                f"{fg_emoji(v)} <b>공포탐욕지수</b>\n\n"
                f"현재: <b>{v}/100</b>  {fg['label']}\n\n"
                f"😱 0~25 극단공포\n😨 26~40 공포\n"
                f"😐 41~60 중립\n😄 61~75 탐욕\n🤑 76~100 극단탐욕",
                kb_back()
            )

        elif data.startswith("listalert_"):
            coin = data.replace("listalert_", "")
            text = build_listalert(coin)
            kb   = kb_alert_list(coin) if coin != "ALL" else kb_back()
            await safe_edit(query, text, kb)

        elif data.startswith("addup_") or data.startswith("adddown_"):
            direction = "상향" if data.startswith("addup_") else "하향"
            coin = data.split("_")[1]
            examples = {
                "BTC":  [("100000000","1억"), ("105000000","1.05억"), ("110000000","1.1억")],
                "DOGE": [("130","130원"), ("150","150원"), ("170","170원")],
                "ETH":  [("3000000","300만"), ("3500000","350만"), ("4000000","400만")],
                "SOL":  [("200000","20만"), ("250000","25만"), ("300000","30만")],
                "XRP":  [("1000","1천원"), ("1500","1.5천원"), ("2000","2천원")],
            }.get(coin, [("100","100원")])
            ex_str = "\n".join([
                f"/addalert {coin} {p} {direction} {lbl}"
                for p, lbl in examples
            ])
            await safe_edit(query,
                f"🔔 <b>{coin} {direction} 알림 추가</b>\n\n"
                f"채팅창에 아래 명령어를 입력하세요:\n\n"
                f"<code>{ex_str}</code>\n\n"
                f"형식:\n<code>/addalert {coin} [가격] {direction} [이름]</code>",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀ 돌아가기", callback_data=f"coin_{coin}")
                ]])
            )

        elif data.startswith("clearalert_"):
            coin = data.replace("clearalert_", "")
            count = len(multi_alerts.get(coin, []))
            multi_alerts[coin] = []
            await safe_edit(query,
                f"🗑 <b>{coin} 알림 전체 삭제 완료</b>\n{count}개 삭제됨",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀ 돌아가기", callback_data=f"coin_{coin}")
                ]])
            )

        elif data == "alert_help":
            await safe_edit(query,
                "<b>⚙️ 알림 명령어 안내</b>\n\n"
                "<b>다중 알림 추가</b>\n"
                "<code>/addalert DOGE 130 상향 1차목표</code>\n"
                "<code>/addalert DOGE 110 하향 지지선</code>\n"
                "<code>/addalert BTC 100000000 상향 1억</code>\n\n"
                "<b>알림 목록</b>\n"
                "<code>/listalert DOGE</code>\n\n"
                "<b>알림 삭제</b>\n"
                "<code>/delalert DOGE 130</code>\n"
                "<code>/clearalert DOGE</code>\n\n"
                "<b>BTC/유가 기본 알림</b>\n"
                "<code>/setalert BTC_HIGH 110000000</code>",
                kb_back()
            )

        else:
            print(f"[미처리 콜백] {data}")

    except Exception as e:
        print(f"[버튼 오류] {data}: {e}")
        traceback.print_exc()
        # 최후 수단: 오류 메시지라도 전송
        try:
            await query.message.reply_text(
                f"⚠️ 오류 발생\n{str(e)[:100]}\n\n/menu 로 다시 시도해주세요"
            )
        except:
            pass


# ════════════════════════════════════════════
# 6. 텍스트 명령어
# ════════════════════════════════════════════

async def cmd_start(u: Update, _):
    await u.message.reply_text(
        "🤖 <b>Coin Direction</b> (업비트 KRW)\n아래 버튼을 눌러 확인하세요 👇",
        reply_markup=kb_main(),
        parse_mode=ParseMode.HTML
    )

async def cmd_menu(u: Update, _):
    await u.message.reply_text(
        "📊 <b>메인 메뉴</b>",
        reply_markup=kb_main(),
        parse_mode=ParseMode.HTML
    )

async def cmd_now(u: Update, _):
    msg = await u.message.reply_text("⏳ 수집 중...", parse_mode=ParseMode.HTML)
    await msg.edit_text(build_brief(), reply_markup=kb_back(), parse_mode=ParseMode.HTML)

async def cmd_btc(u: Update, _):
    await u.message.reply_text(build_coin("BTC"), reply_markup=kb_coin("BTC"), parse_mode=ParseMode.HTML)

async def cmd_doge(u: Update, _):
    await u.message.reply_text(build_coin("DOGE"), reply_markup=kb_coin("DOGE"), parse_mode=ParseMode.HTML)

async def cmd_eth(u: Update, _):
    await u.message.reply_text(build_coin("ETH"), reply_markup=kb_coin("ETH"), parse_mode=ParseMode.HTML)

async def cmd_sol(u: Update, _):
    await u.message.reply_text(build_coin("SOL"), reply_markup=kb_coin("SOL"), parse_mode=ParseMode.HTML)

async def cmd_oil(u: Update, _):
    o = fetch_oil(); wti = o.get("WTI",{}); brent = o.get("Brent",{})
    await u.message.reply_text(
        f"🛢 WTI: ${wti.get('price',0):.2f} ({wti.get('chg',0):+.2f}%)\n"
        f"Brent: ${brent.get('price',0):.2f} ({brent.get('chg',0):+.2f}%)",
        reply_markup=kb_back(), parse_mode=ParseMode.HTML
    )

async def cmd_addalert(u: Update, _):
    try:
        parts = u.message.text.split()
        coin  = parts[1].upper()
        price = float(parts[2])
        direction = parts[3] if len(parts)>3 else "상향"
        label = " ".join(parts[4:]) if len(parts)>4 else ""
        if coin not in multi_alerts:
            await u.message.reply_text(f"❌ 지원: {', '.join(multi_alerts.keys())}"); return
        dir_en = "up" if direction in ["상향","up","위","상"] else "down"
        dir_kr = "🟢 상향" if dir_en=="up" else "🔴 하향"
        for a in multi_alerts[coin]:
            if a["price"]==price and a["dir"]==dir_en:
                await u.message.reply_text(f"⚠️ 이미 등록: {coin} {dir_kr} {fmt_krw(price)}"); return
        multi_alerts[coin].append({"price":price,"dir":dir_en,"label":label})
        lbl_str = f" [{label}]" if label else ""
        await u.message.reply_text(
            f"✅ <b>{coin} 알림 추가!</b>\n{dir_kr}: {fmt_krw(price)}{lbl_str}\n총 {len(multi_alerts[coin])}개",
            reply_markup=kb_coin(coin), parse_mode=ParseMode.HTML
        )
    except:
        await u.message.reply_text(
            "사용법: <code>/addalert DOGE 130 상향 1차목표</code>",
            parse_mode=ParseMode.HTML
        )

async def cmd_listalert(u: Update, _):
    parts = u.message.text.split()
    coin  = parts[1].upper() if len(parts)>1 else "ALL"
    kb    = kb_alert_list(coin) if coin!="ALL" else kb_back()
    await u.message.reply_text(build_listalert(coin), reply_markup=kb, parse_mode=ParseMode.HTML)

async def cmd_delalert(u: Update, _):
    try:
        parts = u.message.text.split()
        coin  = parts[1].upper(); price = float(parts[2])
        before = len(multi_alerts.get(coin,[]))
        multi_alerts[coin] = [a for a in multi_alerts.get(coin,[]) if a["price"]!=price]
        after = len(multi_alerts[coin])
        msg = f"🗑 {coin} {fmt_krw(price)} 삭제 완료" if before>after else f"⚠️ {fmt_krw(price)} 알림 없음"
        await u.message.reply_text(msg, reply_markup=kb_coin(coin), parse_mode=ParseMode.HTML)
    except:
        await u.message.reply_text(
            "사용법: <code>/delalert DOGE 130</code>", parse_mode=ParseMode.HTML
        )

async def cmd_clearalert(u: Update, _):
    try:
        coin = u.message.text.split()[1].upper()
        count = len(multi_alerts.get(coin,[]))
        multi_alerts[coin] = []
        await u.message.reply_text(
            f"🗑 {coin} {count}개 전체 삭제",
            reply_markup=kb_main(), parse_mode=ParseMode.HTML
        )
    except:
        await u.message.reply_text(
            "사용법: <code>/clearalert DOGE</code>", parse_mode=ParseMode.HTML
        )

async def cmd_setalert(u: Update, _):
    try:
        parts = u.message.text.split(); key = parts[1].upper(); val = float(parts[2])
        mapping = {
            "BTC_HIGH":"btc_high","BTC_LOW":"btc_low",
            "OIL_HIGH":"oil_high","OIL_LOW":"oil_low",
        }
        if key in mapping:
            cfg[mapping[key]] = val
            await u.message.reply_text(f"✅ {key} = {val:,.0f} 변경 완료!")
        else:
            await u.message.reply_text(f"❌ 키: {', '.join(mapping.keys())}")
    except:
        await u.message.reply_text(
            "사용법: <code>/setalert BTC_HIGH 110000000</code>", parse_mode=ParseMode.HTML
        )


# ════════════════════════════════════════════
# 7. 자동 모니터링
# ════════════════════════════════════════════

async def auto_monitor(bot: Bot):
    counter = 0
    print(f"✅ 모니터링 시작 (체크:{CHECK_SEC}초 / 브리핑:{BRIEF_SEC}초)")
    while True:
        try:
            c  = fetch_upbit_prices()
            o  = fetch_oil()
            fg = fetch_fear_greed()
            alerts = []

            btc = c.get("BTC",{}); wti = o.get("WTI",{}); v = fg.get("val",50)

            if btc.get("price",0) >= cfg["btc_high"] and cd_ok("btc_high"):
                alerts.append(
                    f"🚀 <b>BTC 상단 돌파!</b>\n"
                    f"현재: {fmt_krw(btc['price'])} ({btc.get('chg',0):+.2f}%)\n"
                    f"임계값: {fmt_krw(cfg['btc_high'])} 이상"
                )
            elif 0 < btc.get("price",999e6) <= cfg["btc_low"] and cd_ok("btc_low"):
                alerts.append(
                    f"⚠️ <b>BTC 하단 이탈!</b>\n"
                    f"현재: {fmt_krw(btc['price'])} ({btc.get('chg',0):+.2f}%)\n"
                    f"임계값: {fmt_krw(cfg['btc_low'])} 이하"
                )
            if wti.get("price",0) >= cfg["oil_high"] and cd_ok("oil_high"):
                alerts.append(f"🛢🚨 <b>WTI 급등!</b> ${wti['price']:.2f} → 코인 하방 압력")
            elif 0 < wti.get("price",999) <= cfg["oil_low"] and cd_ok("oil_low"):
                alerts.append(f"🛢✅ <b>WTI 진정!</b> ${wti['price']:.2f} → 반등 기대")
            if v <= 15 and cd_ok("fg_fear"):
                alerts.append(f"😱 <b>극단 공포!</b> {v}/100\n매수 기회 신호일 수 있음")
            elif v >= 80 and cd_ok("fg_greed"):
                alerts.append(f"🤑 <b>극단 탐욕!</b> {v}/100\n과열 주의")

            for coin, al in multi_alerts.items():
                cur = c.get(coin,{}).get("price",0)
                if not cur: continue
                for a in al:
                    key = f"multi_{coin}_{a['price']}"
                    lbl = f" [{a['label']}]" if a.get("label") else ""
                    if a["dir"]=="up" and cur>=a["price"] and cd_ok(key):
                        alerts.append(
                            f"🚀 <b>{coin} 상향 돌파!{lbl}</b>\n"
                            f"현재: {fmt_krw(cur)}\n목표: {fmt_krw(a['price'])} ✅"
                        )
                    elif a["dir"]=="down" and cur<=a["price"] and cd_ok(key):
                        alerts.append(
                            f"⚠️ <b>{coin} 하향 이탈!{lbl}</b>\n"
                            f"현재: {fmt_krw(cur)}\n경계: {fmt_krw(a['price'])} 🔴"
                        )

            # 알림: 버튼 없이 텍스트만
            for msg in alerts:
                await bot.send_message(CHAT_ID, msg, parse_mode=ParseMode.HTML)

            # 정기 브리핑: 메인 메뉴 버튼 첨부
            counter += CHECK_SEC
            if counter >= BRIEF_SEC:
                await bot.send_message(
                    CHAT_ID,
                    f"⏰ <b>정기 브리핑</b>\n{build_brief()}",
                    reply_markup=kb_main(),
                    parse_mode=ParseMode.HTML
                )
                counter = 0

        except Exception as e:
            print(f"[모니터 오류] {e}")

        await asyncio.sleep(CHECK_SEC)


# ════════════════════════════════════════════
# 8. 메인
# ════════════════════════════════════════════

async def main():
    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN 없음")
        return
    if not CHAT_ID:
        print("❌ TELEGRAM_CHAT_ID 없음")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    for cmd, fn in [
        ("start", cmd_start),
        ("menu", cmd_menu),
        ("now", cmd_now),
        ("btc", cmd_btc),
        ("doge", cmd_doge),
        ("eth", cmd_eth),
        ("sol", cmd_sol),
        ("oil", cmd_oil),
        ("addalert", cmd_addalert),
        ("listalert", cmd_listalert),
        ("delalert", cmd_delalert),
        ("clearalert", cmd_clearalert),
        ("setalert", cmd_setalert),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(CallbackQueryHandler(button_handler))

    async def start_tasks(application):
        asyncio.create_task(auto_monitor(application.bot))

    app.post_init = start_tasks

    print("🤖 Coin Direction 봇 시작!")
    await app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
CoinDCX Trading Telegram Bot (single-file)
- Uses environment variables for credentials.
- Commands: /buy /sell /profit /status /stop /stopall
- Requires: python-telegram-bot==13.14, requests
"""
from __future__ import annotations

import os
import json
import time
import hmac
import hashlib
import logging
import threading
from typing import Optional, Dict, Any
import requests

from telegram import Update, ParseMode
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)

# Config from env
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
API_KEY = os.getenv("COINDCX_API_KEY")
API_SECRET = os.getenv("COINDCX_API_SECRET")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Set TELEGRAM_TOKEN environment variable.")
if not API_KEY or not API_SECRET:
    raise RuntimeError("Set COINDCX_API_KEY and COINDCX_API_SECRET environment variables.")

SECRET_BYTES = API_SECRET.encode("utf-8")
API_BASE = "https://api.coindcx.com"
PUBLIC_ORDERBOOK = "https://public.coindcx.com/market_data/orderbook?pair=B-{coin}_INR"

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("coin_dc_bot")

# session store
user_sessions: Dict[int, Dict[str, Any]] = {}
session_lock = threading.Lock()
SESSION_COUNTER = 0

# Conversation states
(BUY_COIN, BUY_PRICE, BUY_INVESTMENT, BUY_PRECISION, BUY_MODE) = range(5)
(SELL_COIN, SELL_PRICE, SELL_INVESTMENT, SELL_PRECISION, SELL_MODE) = range(5, 10)
PROFIT_COIN = 10

def sign_payload(body: dict) -> str:
    payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    signature = hmac.new(SECRET_BYTES, payload.encode(), hashlib.sha256).hexdigest()
    return signature

def post_signed(path: str, body: dict, timeout: float = 10.0) -> Optional[dict]:
    url = API_BASE + path
    payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    signature = hmac.new(SECRET_BYTES, payload.encode(), hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": API_KEY,
        "X-AUTH-SIGNATURE": signature,
    }
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.warning("POST %s failed: %s", url, e)
        return None
    except ValueError:
        logger.warning("POST %s returned non-JSON", url)
        return None

def get_public_orderbook(coin: str, timeout: float = 8.0) -> Optional[dict]:
    url = PUBLIC_ORDERBOOK.format(coin=coin.upper())
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning("Orderbook fetch failed for %s: %s", coin, e)
        return None

def round_price(price: float, precision: int) -> float:
    return round(price, precision)

def next_session_id() -> int:
    global SESSION_COUNTER
    with session_lock:
        SESSION_COUNTER += 1
        return SESSION_COUNTER

# API primitives
def create_limit_order(side: str, coin: str, price: float, quantity: float) -> Optional[dict]:
    timestamp = int(round(time.time() * 1000))
    body = {
        "side": side,
        "market": f"{coin}INR",
        "timestamp": timestamp,
        "price_per_unit": price,
        "total_quantity": quantity,
        "order_type": "limit",
    }
    return post_signed("/exchange/v1/orders/create", body)

def edit_order(order_id: str, price: float) -> Optional[dict]:
    timestamp = int(round(time.time() * 1000))
    body = {"id": str(order_id), "timestamp": timestamp, "price_per_unit": price}
    return post_signed("/exchange/v1/orders/edit", body)

def cancel_order(order_id: str) -> Optional[dict]:
    timestamp = int(round(time.time() * 1000))
    body = {"id": str(order_id), "timestamp": timestamp}
    return post_signed("/exchange/v1/orders/cancel", body)

# Background worker
def start_continuous_edit(user_id: int, session_id: int, session_obj: dict):
    logger.info("Starting session %s for user %s", session_id, user_id)
    backoff = 1.0
    bot = session_obj["bot"]
    chat_id = session_obj["chat_id"]
    while not session_obj["stop_event"].is_set():
        try:
            coin = session_obj["coin"]
            precision = session_obj["precision"]
            tick = session_obj["tick_size"]
            mode = session_obj["mode"]
            min_volume = session_obj.get("min_volume", 50.0)
            ob = get_public_orderbook(coin)
            if ob is None:
                backoff = min(backoff * 1.5, 30.0)
                time.sleep(session_obj["loop_delay"] + backoff)
                continue
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            def extract_best(side, pick_max):
                prices = []
                if isinstance(side, dict):
                    for k in side.keys():
                        try:
                            prices.append(float(k))
                        except Exception:
                            continue
                elif isinstance(side, list):
                    for item in side:
                        try:
                            prices.append(float(item[0]))
                        except Exception:
                            try:
                                prices.append(float(item))
                            except Exception:
                                continue
                if not prices:
                    return None
                return max(prices) if pick_max else min(prices)
            best_bid = extract_best(bids, True)
            best_ask = extract_best(asks, False)

            if mode == "buy":
                candidate = None
                items = bids.items() if isinstance(bids, dict) else bids
                for entry in items:
                    try:
                        if isinstance(entry, tuple):
                            p_str, q = entry
                        else:
                            p_str, q = entry[0], entry[1]
                        p = float(p_str); q = float(q)
                    except Exception:
                        continue
                    if p * q <= min_volume:
                        continue
                    if p < session_obj["limit_price"]:
                        candidate = p; break
                if candidate is None and best_bid and best_bid < session_obj["limit_price"]:
                    candidate = best_bid
                if candidate is None:
                    backoff = min(backoff * 1.5, 30.0)
                    time.sleep(session_obj["loop_delay"] + backoff)
                    continue
                new_price = round_price(candidate + tick, precision)
            else:
                candidate = None
                items = asks.items() if isinstance(asks, dict) else asks
                for entry in items:
                    try:
                        if isinstance(entry, tuple):
                            p_str, q = entry
                        else:
                            p_str, q = entry[0], entry[1]
                        p = float(p_str); q = float(q)
                    except Exception:
                        continue
                    if p * q <= min_volume:
                        continue
                    if p > session_obj["limit_price"]:
                        candidate = p; break
                if candidate is None and best_ask and best_ask > session_obj["limit_price"]:
                    candidate = best_ask
                if candidate is None:
                    backoff = min(backoff * 1.5, 30.0)
                    time.sleep(session_obj["loop_delay"] + backoff)
                    continue
                new_price = round_price(candidate - tick, precision)

            order_id = session_obj.get("order_id")
            if not order_id:
                if session_obj.get("investment_inr"):
                    qty = session_obj["investment_inr"] / new_price
                else:
                    qty = float(session_obj.get("quantity", 0.0))
                create_resp = create_limit_order(session_obj["mode"], session_obj["coin"], new_price, qty)
                if create_resp and isinstance(create_resp, dict):
                    oid = create_resp.get("id") or (create_resp.get("orders") and create_resp["orders"][0].get("id"))
                    if oid:
                        session_obj["order_id"] = str(oid)
                        backoff = 1.0
                    else:
                        backoff = min(backoff * 1.5, 30.0)
                        time.sleep(session_obj["loop_delay"] + backoff)
                        continue
                else:
                    backoff = min(backoff * 1.5, 30.0)
                    time.sleep(session_obj["loop_delay"] + backoff)
                    continue
            else:
                edit_resp = edit_order(session_obj["order_id"], new_price)
                if not edit_resp:
                    session_obj.pop("order_id", None)
                    backoff = min(backoff * 1.5, 30.0)
                    time.sleep(session_obj["loop_delay"] + backoff)
                    continue
                backoff = 1.0

            now = time.time()
            if now - session_obj.get("last_notify", 0) > session_obj.get("notify_interval", 15):
                try:
                    bot.send_message(chat_id, f"✅ Session {session_id}: order updated to price {new_price} ({mode.upper()}) for {coin}")
                except Exception:
                    logger.exception("Notify failed for session %s", session_id)
                session_obj["last_notify"] = now

            time.sleep(session_obj["loop_delay"])
        except Exception as e:
            logger.exception("Session worker error: %s", e)
            time.sleep(min(backoff*2, 60.0))

    # cleanup
    if session_obj.get("order_id") and session_obj.get("cancel_on_stop", True):
        try:
            cancel_order(session_obj["order_id"])
        except Exception:
            pass
    with session_lock:
        u = user_sessions.get(user_id, {})
        u.pop(str(session_id), None)
    logger.info("Session %s ended", session_id)

# Telegram handlers
def start(update: Update, context: CallbackContext):
    msg = (
        "🤖 CoinDCX Bot ready.\n"
        "Commands: /buy /sell /profit /status /stop <id> /stopall /help\n"
    )
    update.message.reply_text(msg)

def buy_start(update: Update, context: CallbackContext):
    update.message.reply_text("**Starting Buy Order Setup**\nEnter the coin name (e.g., BTC, ETH):", parse_mode=ParseMode.MARKDOWN)
    return BUY_COIN

def buy_coin(update: Update, context: CallbackContext):
    coin = update.message.text.strip().upper()
    context.user_data["buy_coin"] = coin
    update.message.reply_text(f"✅ Coin: {coin}\nEnter maximum buy price (limit price).")
    return BUY_PRICE

def buy_price(update: Update, context: CallbackContext):
    try:
        p = float(update.message.text.strip())
        context.user_data["buy_price"] = p
        update.message.reply_text("Enter investment amount in INR (e.g., 1000):")
        return BUY_INVESTMENT
    except Exception:
        update.message.reply_text("Invalid price. Enter numeric value.")
        return BUY_PRICE

def buy_investment(update: Update, context: CallbackContext):
    try:
        inv = float(update.message.text.strip())
        context.user_data["buy_investment"] = inv
        update.message.reply_text("Enter decimal precision (0-10):")
        return BUY_PRECISION
    except Exception:
        update.message.reply_text("Invalid amount.")
        return BUY_INVESTMENT

def buy_precision(update: Update, context: CallbackContext):
    try:
        prec = int(update.message.text.strip())
        if prec < 0 or prec > 10: raise ValueError
        context.user_data["buy_precision"] = prec
        update.message.reply_text("Mode? 'once' or 'continuous':")
        return BUY_MODE
    except Exception:
        update.message.reply_text("Invalid precision.")
        return BUY_PRECISION

def buy_mode(update: Update, context: CallbackContext):
    mode = update.message.text.strip().lower()
    if mode not in ("once","continuous"):
        update.message.reply_text("Type 'once' or 'continuous'")
        return BUY_MODE
    coin = context.user_data["buy_coin"]
    limit_price = float(context.user_data["buy_price"])
    investment = float(context.user_data["buy_investment"])
    precision = int(context.user_data["buy_precision"])
    if mode == "once":
        qty = round(investment / limit_price, precision+6)
        resp = create_limit_order("buy", coin, round_price(limit_price, precision), qty)
        if resp:
            update.message.reply_text(f"✅ One-shot buy placed for {coin} @{limit_price} qty {qty}")
        else:
            update.message.reply_text("❌ Failed to create buy order.")
        return ConversationHandler.END
    else:
        sid = next_session_id()
        stop_evt = threading.Event()
        session_obj = {
            "mode":"buy","coin":coin,"limit_price":limit_price,"precision":precision,
            "tick_size":1/(10**precision),"investment_inr":investment,"loop_delay":2.0,
            "stop_event":stop_evt,"order_id":None,"chat_id":update.effective_chat.id,"bot":context.bot,
            "last_notify":0,"notify_interval":15,"min_volume":50.0,"cancel_on_stop":True
        }
        thr = threading.Thread(target=start_continuous_edit, args=(update.effective_user.id, sid, session_obj), daemon=True)
        session_obj["thread"] = thr
        with session_lock:
            user_map = user_sessions.setdefault(update.effective_user.id, {})
            user_map[str(sid)] = session_obj
        thr.start()
        update.message.reply_text(f"✅ Started continuous BUY session id {sid} for {coin}")
        return ConversationHandler.END

# Sell conversation handlers (mirror buy flow)
def sell_start(update: Update, context: CallbackContext):
    update.message.reply_text("**Starting Sell Order Setup**\nEnter the coin name (e.g., BTC, ETH):", parse_mode=ParseMode.MARKDOWN)
    return SELL_COIN

def sell_coin(update: Update, context: CallbackContext):
    coin = update.message.text.strip().upper()
    context.user_data["sell_coin"] = coin
    update.message.reply_text(f"✅ Coin: {coin}\nEnter minimum sell price (limit price).")
    return SELL_PRICE

def sell_price(update: Update, context: CallbackContext):
    try:
        p = float(update.message.text.strip())
        context.user_data["sell_price"] = p
        update.message.reply_text("Enter quantity to SELL (coin units) or type 'inr:<amount>' to specify INR amount to sell (e.g., inr:1000):")
        return SELL_INVESTMENT
    except Exception:
        update.message.reply_text("Invalid price. Enter numeric value.")
        return SELL_PRICE

def sell_investment(update: Update, context: CallbackContext):
    text = update.message.text.strip().lower()
    if text.startswith("inr:"):
        try:
            inr = float(text.split(":",1)[1])
            context.user_data["sell_inr"] = inr
        except Exception:
            update.message.reply_text("Invalid INR format. Use inr:1000")
            return SELL_INVESTMENT
    else:
        try:
            qty = float(text)
            context.user_data["sell_qty"] = qty
        except Exception:
            update.message.reply_text("Invalid quantity. Enter number or inr:<amount>")
            return SELL_INVESTMENT
    update.message.reply_text("Enter decimal precision (0-10):")
    return SELL_PRECISION

def sell_precision(update: Update, context: CallbackContext):
    try:
        prec = int(update.message.text.strip())
        if prec < 0 or prec > 10: raise ValueError
        context.user_data["sell_precision"] = prec
        update.message.reply_text("Mode? 'once' or 'continuous':")
        return SELL_MODE
    except Exception:
        update.message.reply_text("Invalid precision.")
        return SELL_PRECISION

def sell_mode(update: Update, context: CallbackContext):
    mode = update.message.text.strip().lower()
    if mode not in ("once","continuous"):
        update.message.reply_text("Type 'once' or 'continuous'")
        return SELL_MODE
    coin = context.user_data["sell_coin"]
    limit_price = float(context.user_data["sell_price"])
    precision = int(context.user_data["sell_precision"])
    qty = context.user_data.get("sell_qty")
    inr = context.user_data.get("sell_inr")
    if mode == "once":
        if inr:
            qty_calc = inr / limit_price
        else:
            qty_calc = qty
        if not qty_calc:
            update.message.reply_text("❌ Could not determine quantity to sell.")
        else:
            resp = create_limit_order("sell", coin, round_price(limit_price, precision), float(qty_calc))
            if resp:
                update.message.reply_text(f"✅ One-shot sell placed for {coin} @{limit_price} qty {qty_calc}")
            else:
                update.message.reply_text("❌ Failed to create sell order.")
        return ConversationHandler.END
    else:
        sid = next_session_id()
        stop_evt = threading.Event()
        session_obj = {
            "mode":"sell","coin":coin,"limit_price":limit_price,"precision":precision,
            "tick_size":1/(10**precision),"investment_inr":inr if inr else None,"quantity":qty if qty else None,
            "loop_delay":2.0,"stop_event":stop_evt,"order_id":None,"chat_id":update.effective_chat.id,"bot":context.bot,
            "last_notify":0,"notify_interval":15,"min_volume":200.0,"cancel_on_stop":True
        }
        thr = threading.Thread(target=start_continuous_edit, args=(update.effective_user.id, sid, session_obj), daemon=True)
        session_obj["thread"] = thr
        with session_lock:
            user_map = user_sessions.setdefault(update.effective_user.id, {})
            user_map[str(sid)] = session_obj
        thr.start()
        update.message.reply_text(f"✅ Started continuous SELL session id {sid} for {coin}")
        return ConversationHandler.END

def profit_start(update: Update, context: CallbackContext):
    update.message.reply_text("Enter coin for profit calc (e.g., BTC):")
    return PROFIT_COIN

def profit_coin(update: Update, context: CallbackContext):
    coin = update.message.text.strip().upper()
    data = get_public_orderbook(coin)
    if not data:
        update.message.reply_text("Failed to fetch orderbook.")
        return ConversationHandler.END
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    def best_from(side, pick_max):
        prices = []
        if isinstance(side, dict):
            for k in side.keys():
                try: prices.append(float(k))
                except: pass
        elif isinstance(side, list):
            for item in side:
                try: prices.append(float(item[0]))
                except:
                    try: prices.append(float(item))
                    except: pass
        if not prices: return None
        return max(prices) if pick_max else min(prices)
    best_bid = best_from(bids, True)
    best_ask = best_from(asks, False)
    if not best_bid or not best_ask:
        update.message.reply_text("Could not determine best bid/ask.")
        return ConversationHandler.END
    inv = 1000.0
    qty = inv / best_ask
    sell_revenue = qty * best_bid
    fee = 0.001
    profit = sell_revenue - inv - (inv*fee + sell_revenue*fee)
    update.message.reply_text(f"Coin: {coin}\nBuy@{best_ask}\nSell@{best_bid}\nProfit(after fees): Rs.{profit:.2f}")
    return ConversationHandler.END

def status(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    with session_lock:
        s = user_sessions.get(uid, {})
        if not s:
            update.message.reply_text("No active trading sessions found.")
            return
        lines = []
        for sid, o in s.items():
            lines.append(f"ID {sid} | {o['mode'].upper()} {o['coin']} | limit {o['limit_price']} | prec {o['precision']} | order_id {o.get('order_id') or 'N/A'}")
    update.message.reply_text("\n".join(lines))

def stop(update: Update, context: CallbackContext):
    args = context.args
    if not args:
        update.message.reply_text("Usage: /stop <session_id>")
        return
    sid = args[0]
    uid = update.effective_user.id
    with session_lock:
        s = user_sessions.get(uid, {})
        session = s.get(sid) if s else None
        if not session:
            update.message.reply_text("No such active session.")
            return
        session["stop_event"].set()
    update.message.reply_text(f"Stopping session {sid}...")

def stopall(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    with session_lock:
        s = user_sessions.get(uid, {})
        if not s:
            update.message.reply_text("No active sessions to stop.")
            return
        for v in list(s.values()):
            v["stop_event"].set()
    update.message.reply_text("Stopping all sessions...")

def unknown(update: Update, context: CallbackContext):
    update.message.reply_text("Unknown command. Use /help")

def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", start))
    dp.add_handler(CommandHandler("status", status))
    dp.add_handler(CommandHandler("stop", stop))
    dp.add_handler(CommandHandler("stopall", stopall))
    # Buy conv
    buy_conv = ConversationHandler(
        entry_points=[CommandHandler("buy", buy_start)],
        states={
            BUY_COIN: [MessageHandler(Filters.text & ~Filters.command, buy_coin)],
            BUY_PRICE: [MessageHandler(Filters.text & ~Filters.command, buy_price)],
            BUY_INVESTMENT: [MessageHandler(Filters.text & ~Filters.command, buy_investment)],
            BUY_PRECISION: [MessageHandler(Filters.text & ~Filters.command, buy_precision)],
            BUY_MODE: [MessageHandler(Filters.text & ~Filters.command, buy_mode)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: (u.message.reply_text("Cancelled."), ConversationHandler.END)[1])],
        allow_reentry=True,
    )
    dp.add_handler(buy_conv)

    # Sell conv
    sell_conv = ConversationHandler(
        entry_points=[CommandHandler("sell", sell_start)],
        states={
            SELL_COIN: [MessageHandler(Filters.text & ~Filters.command, sell_coin)],
            SELL_PRICE: [MessageHandler(Filters.text & ~Filters.command, sell_price)],
            SELL_INVESTMENT: [MessageHandler(Filters.text & ~Filters.command, sell_investment)],
            SELL_PRECISION: [MessageHandler(Filters.text & ~Filters.command, sell_precision)],
            SELL_MODE: [MessageHandler(Filters.text & ~Filters.command, sell_mode)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: (u.message.reply_text("Cancelled."), ConversationHandler.END)[1])],
        allow_reentry=True,
    )
    dp.add_handler(sell_conv)

    # Profit conv
    profit_conv = ConversationHandler(entry_points=[CommandHandler("profit", profit_start)],
        states={PROFIT_COIN: [MessageHandler(Filters.text & ~Filters.command, profit_coin)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: (u.message.reply_text("Cancelled."), ConversationHandler.END)[1])])
    dp.add_handler(profit_conv)

    dp.add_handler(MessageHandler(Filters.command, unknown))

    updater.start_polling()
    logger.info("Bot started.")
    updater.idle()

if __name__ == "__main__":
    main()
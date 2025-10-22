#!/usr/bin/env python3
"""
Demo simulator for the bot flows (no Telegram, no API keys).
Run: python demo_simulator.py
"""
import threading
import time

sessions = {}
session_lock = threading.Lock()
counter = 0

def next_id():
    global counter
    counter += 1
    return str(counter)

def background_sim(sid, mode, coin, limit, prec):
    tick = 1 / (10**prec)
    i = 0
    while True:
        with session_lock:
            s = sessions.get(sid)
            if not s or s.get("stopped"):
                print(f"[sim] Session {sid} stopped.")
                return
        mock_price = limit + (0.02 * ((i % 5) - 2))
        if mode == "buy":
            new_price = round(mock_price + tick, prec)
        else:
            new_price = round(mock_price - tick, prec)
        print(f"[sim] Session {sid} ({mode}) new order price: {new_price} for {coin}")
        i += 1
        time.sleep(3)

def cmd_buy():
    coin = input("Coin symbol: ").strip().upper()
    price = float(input("Max buy price (limit): ").strip())
    inv = float(input("Investment INR: ").strip())
    prec = int(input("Precision (0-10): ").strip())
    mode = input("Mode once/continuous: ").strip().lower()
    if mode == "once":
        qty = round(inv/price, prec+6)
        print(f"Placed one-shot BUY {coin} @{price} qty {qty}")
    else:
        sid = next_id()
        with session_lock:
            sessions[sid] = {"mode":"buy","coin":coin,"limit":price,"prec":prec,"stopped":False}
        t = threading.Thread(target=background_sim, args=(sid,"buy",coin,price,prec), daemon=True)
        t.start()
        print(f"Started continuous BUY session id {sid} for {coin}")

def cmd_sell():
    coin = input("Coin symbol: ").strip().upper()
    price = float(input("Min sell price (limit): ").strip())
    q = input("Quantity or inr:<amount>: ").strip().lower()
    if q.startswith("inr:"):
        inr = float(q.split(":",1)[1])
        qty = round(inr/price,8)
    else:
        qty = float(q)
    prec = int(input("Precision (0-10): ").strip())
    mode = input("Mode once/continuous: ").strip().lower()
    if mode == "once":
        print(f"Placed one-shot SELL {coin} @{price} qty {qty}")
    else:
        sid = next_id()
        with session_lock:
            sessions[sid] = {"mode":"sell","coin":coin,"limit":price,"prec":prec,"stopped":False}
        t = threading.Thread(target=background_sim, args=(sid,"sell",coin,price,prec), daemon=True)
        t.start()
        print(f"Started continuous SELL session id {sid} for {coin}")

def cmd_profit():
    coin = input("Coin symbol: ").strip().upper()
    # dummy example
    ask = 200.5
    bid = 199.2
    inv = 1000.0
    qty = inv / ask
    rev = qty * bid
    fee_rate = 0.001
    buy_fee = inv * fee_rate
    sell_fee = rev * fee_rate
    profit = rev - inv - (buy_fee + sell_fee)
    print(f"Coin {coin} Buy@{ask} Sell@{bid} Profit(after fees): Rs.{profit:.2f}")

def cmd_status():
    with session_lock:
        if not sessions:
            print("No active sessions.")
            return
        for sid, s in sessions.items():
            print(f"ID {sid} | {s['mode']} {s['coin']} | limit {s['limit']} | prec {s['prec']} | stopped={s['stopped']}")

def cmd_stop():
    sid = input("Session id to stop: ").strip()
    with session_lock:
        s = sessions.get(sid)
        if not s:
            print("No session with id", sid)
            return
        s["stopped"] = True
        sessions.pop(sid, None)
    print("Stopping", sid)

def repl():
    print("Demo simulator. Commands: buy, sell, profit, status, stop, exit")
    while True:
        cmd = input(">> ").strip().lower()
        if cmd == "buy": cmd_buy()
        elif cmd == "sell": cmd_sell()
        elif cmd == "profit": cmd_profit()
        elif cmd == "status": cmd_status()
        elif cmd == "stop": cmd_stop()
        elif cmd == "exit": break
        else: print("Unknown. Use buy/sell/profit/status/stop/exit")

if __name__ == "__main__":
    repl()
import datetime
import os
import time
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import requests

app = FastAPI(title="SMC Shadow AI - Live Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
SYMBOL = "XAU/USD"
INTERVAL = "15min"

state = {
    "hunter_mode": True,
    "scientist_mode": True,
    "starting_balance": 10000.0,
    "virtual_balance": 10000.0,
    "daily_pnl": 0.0,
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "win_rate": 0.0,
    "active_trade": None,
    "trade_history": [],
    "last_fetch_ts": 0.0,
    "last_analysis": {
        "status": "Weekend Mode - Market Paused",
        "market_structure": "WEEKEND_CLOSED",
        "liquidity_pool": "Waiting for Monday Open",
        "order_block": "Demand: $4164.00 | Supply: $4165.00",
        "bias": "MARKET_CLOSED_NO_TRADES",
        "last_price": 4164.76,
        "updated_at": "Paused",
    },
}


def open_trade(trade_type, entry, sl, tp, reason):
    state["active_trade"] = {
        "type": trade_type,
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "tp": round(tp, 2),
        "reason": reason,
        "floating_pnl": 0.0,
        "opened_at": datetime.datetime.utcnow().strftime("%H:%M UTC"),
    }


def close_trade(result, exit_price, multiplier=20.0):
    active = state["active_trade"]
    if not active:
        return
    if active["type"] == "BUY":
        pnl = round((exit_price - active["entry"]) * multiplier, 2)
    else:
        pnl = round((active["entry"] - exit_price) * multiplier, 2)

    state["virtual_balance"] = round(state["virtual_balance"] + pnl, 2)
    state["daily_pnl"] = round(state["daily_pnl"] + pnl, 2)
    state["total_trades"] += 1
    if pnl >= 0:
        state["wins"] += 1
    else:
        state["losses"] += 1
    state["win_rate"] = round(
        (state["wins"] / state["total_trades"]) * 100, 1
    )

    state["trade_history"].insert(
        0,
        {
            "type": active["type"],
            "entry": active["entry"],
            "exit": round(exit_price, 2),
            "pnl": pnl,
            "result": result,
            "closed_at": datetime.datetime.utcnow().strftime("%H:%M UTC"),
        },
    )
    state["trade_history"] = state["trade_history"][:10]
    state["active_trade"] = None


def sync_market(force=False):
    now = time.time()
    if (
        not force
        and (now - state["last_fetch_ts"] < 30)
        and state["last_analysis"]["last_price"] > 0
    ):
        return

    if not API_KEY:
        state["last_analysis"]["status"] = "Missing TWELVEDATA_API_KEY"
        return

    url = f"https://api.twelvedata.com/time_series?symbol={SYMBOL}&interval={INTERVAL}&outputsize=25&apikey={API_KEY}"
    try:
        res = requests.get(url, timeout=8).json()
        if "values" not in res:
            # Keep last known safe price during weekend
            return

        state["last_fetch_ts"] = now
        candles = [
            {
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            }
            for c in reversed(res["values"])
        ]

        cur = candles[-1]
        prev = candles[:-1]
        price = cur["close"]

        recent_high = max(c["high"] for c in prev[-15:])
        recent_low = min(c["low"] for c in prev[-15:])
        ob_low = round(recent_low + (recent_high - recent_low) * 0.18, 2)
        ob_high = round(recent_high - (recent_high - recent_low) * 0.18, 2)

        # Check if today is Saturday or Sunday -> Pause Auto-Trading
        weekday = datetime.datetime.utcnow().weekday()
        is_weekend = weekday >= 5  # 5 is Saturday, 6 is Sunday

        active = state["active_trade"]
        if active:
            mult = 20.0
            if active["type"] == "BUY":
                active["floating_pnl"] = round(
                    (price - active["entry"]) * mult, 2
                )
            else:
                active["floating_pnl"] = round(
                    (active["entry"] - price) * mult, 2
                )

        structure = "RANGE_BOUND"
        bias = "WEEKEND_PAUSED"

        # Only allow auto-trades if market is open (Mon-Fri)
        if state["hunter_mode"] and state["active_trade"] is None and not is_weekend:
            if cur["low"] <= ob_low and price > ob_low:
                sl = round(cur["low"] - 2.5, 2)
                tp = round(price + max(price - sl, 1.5) * 2.5, 2)
                open_trade(
                    "BUY", price, sl, tp, "BEAR-TRAP BUY (M15 Sweep)"
                )
                bias = "BULLISH_SNIPER_ENTRY"
            elif cur["high"] >= ob_high and price < ob_high:
                sl = round(cur["high"] + 2.5, 2)
                tp = round(price - max(sl - price, 1.5) * 2.5, 2)
                open_trade(
                    "SELL", price, sl, tp, "BULL-TRAP SELL (M15 Sweep)"
                )
                bias = "BEARISH_SNIPER_ENTRY"
        elif is_weekend:
            bias = "MARKET_CLOSED_WEEKEND"

        state["last_analysis"] = {
            "status": "Weekend Mode (Live Feed Paused)",
            "market_structure": structure,
            "liquidity_pool": f"${recent_low:.2f} - ${recent_high:.2f}",
            "order_block": f"Demand: ${ob_low:.2f} | Supply: ${ob_high:.2f}",
            "bias": "WEEKEND_SAFE_MODE" if is_weekend else bias,
            "last_price": round(price, 2),
            "updated_at": datetime.datetime.utcnow().strftime("%H:%M:%S UTC"),
        }
    except Exception as e:
        pass


@app.get("/api/status")
def get_status():
    sync_market()
    return state


@app.post("/api/force_demo_trade")
def force_demo_trade():
    sync_market()
    price = state["last_analysis"]["last_price"]
    if price > 0 and state["active_trade"] is None:
        open_trade(
            "BUY",
            price,
            round(price - 3.0, 2),
            round(price + 7.5, 2),
            "MANUAL_TEST_ENTRY",
        )
    return state


@app.post("/api/close_active_trade")
def close_active():
    price = state["last_analysis"]["last_price"]
    if state["active_trade"] and price > 0:
        close_trade("MANUAL_CLOSE", price, 20.0)
    return state
    

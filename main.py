import os
import time
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests

app = FastAPI(title="SMC Shadow AI Core")

# Frontend connection ke liye CORS allow karein
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
SYMBOL = "XAU/USD"
TIMEFRAME = "15min"

# Bot State & Memory (30-40 Day Self-Learning Simulator)
bot_state = {
    "hunter_mode": True,
    "scientist_mode": True,
    "virtual_balance": 10000.0,
    "starting_balance": 10000.0,
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "win_rate": 0.0,
    "active_trade": None,
    "trade_history": [],
    "last_analysis": {
        "status": "Analyzing M15 Structure",
        "market_structure": "BULLISH_ORDER_FLOW",
        "liquidity_pool": "4155.00 - 4158.00",
        "order_block": "4162.50",
        "bias": "WAITING_CONFIRMATION",
        "last_price": 4168.77
    }
}

def fetch_m15_candles():
    """TwelveData se 15-min Gold candles fetch karna"""
    if not API_KEY:
        return []
    try:
        url = f"https://api.twelvedata.com/time_series?symbol={SYMBOL}&interval={TIMEFRAME}&outputsize=30&apikey={API_KEY}"
        res = requests.get(url, timeout=10)
        data = res.json()
        if "values" in data:
            return data["values"]
    except Exception as e:
        print(f"Error fetching data: {e}")
    return []

def analyze_smc(candles):
    """Smart Money Concepts (SMC) Logic: Sweeps, FVG & Order Blocks"""
    if len(candles) < 5:
        return

    latest = float(candles[0]["close"])
    bot_state["last_analysis"]["last_price"] = latest

    highs = [float(c["high"]) for c in candles[:10]]
    lows = [float(c["low"]) for c in candles[:10]]
    prev_high = max(highs[1:])
    prev_low = min(lows[1:])

    # Current Candle Wick Analysis (Liquidity Sweep)
    curr_high = float(candles[0]["high"])
    curr_low = float(candles[0]["low"])

    signal = "NEUTRAL"

    # Bullish Liquidity Sweep (Asian/London Low Swept + Quick Rejection)
    if curr_low < prev_low and latest > prev_low:
        signal = "BUY_SWEEP"
        bot_state["last_analysis"]["bias"] = "STRONG_BUY_HUNTER"
        bot_state["last_analysis"]["order_block"] = f"{curr_low:.2f}"
        bot_state["last_analysis"]["liquidity_pool"] = f"Sell-side Swept @ {prev_low:.2f}"

    # Bearish Liquidity Sweep (Highs Swept + Quick Rejection)
    elif curr_high > prev_high and latest < prev_high:
        signal = "SELL_SWEEP"
        bot_state["last_analysis"]["bias"] = "STRONG_SELL_HUNTER"
        bot_state["last_analysis"]["order_block"] = f"{curr_high:.2f}"
        bot_state["last_analysis"]["liquidity_pool"] = f"Buy-side Swept @ {prev_high:.2f}"

    # Execute Virtual Trade agar Hunter Mode ON ho aur koi trade active na ho
    if bot_state["hunter_mode"] and not bot_state["active_trade"]:
        if signal == "BUY_SWEEP":
            sl = curr_low - 3.0
            tp = latest + ((latest - sl) * 2.5)  # 1:2.5 RR
            bot_state["active_trade"] = {
                "type": "BUY",
                "entry": latest,
                "sl": sl,
                "tp": tp,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        elif signal == "SELL_SWEEP":
            sl = curr_high + 3.0
            tp = latest - ((sl - latest) * 2.5)
            bot_state["active_trade"] = {
                "type": "SELL",
                "entry": latest,
                "sl": sl,
                "tp": tp,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

    # Active Trade Management (Check TP / SL)
    if bot_state["active_trade"]:
        trade = bot_state["active_trade"]
        pnl = 0
        hit = False

        if trade["type"] == "BUY":
            if latest >= trade["tp"]:
                pnl = (trade["tp"] - trade["entry"]) * 10
                hit = True
            elif latest <= trade["sl"]:
                pnl = (trade["sl"] - trade["entry"]) * 10
                hit = True
        elif trade["type"] == "SELL":
            if latest <= trade["tp"]:
                pnl = (trade["entry"] - trade["tp"]) * 10
                hit = True
            elif latest >= trade["sl"]:
                pnl = (trade["entry"] - trade["sl"]) * 10
                hit = True

        if hit:
            bot_state["virtual_balance"] += pnl
            bot_state["total_trades"] += 1
            if pnl > 0:
                bot_state["wins"] += 1
            else:
                bot_state["losses"] += 1

            bot_state["win_rate"] = round((bot_state["wins"] / bot_state["total_trades"]) * 100, 2)
            bot_state["trade_history"].append({**trade, "exit": latest, "pnl": round(pnl, 2)})
            bot_state["active_trade"] = None

@app.get("/")
def home():
    return {"status": "SMC Shadow Core Online", "timeframe": TIMEFRAME, "symbol": SYMBOL}

@app.get("/api/status")
def get_status():
    candles = fetch_m15_candles()
    if candles:
        analyze_smc(candles)
    return bot_state

@app.post("/api/toggle-hunter")
def toggle_hunter():
    bot_state["hunter_mode"] = not bot_state["hunter_mode"]
    return {"hunter_mode": bot_state["hunter_mode"]}
          

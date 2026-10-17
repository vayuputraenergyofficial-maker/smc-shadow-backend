import asyncio
import datetime
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import requests

app = FastAPI(title="SMC Shadow AI - Live Demo Trading Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
SYMBOL = "XAU/USD"
INTERVAL = "15min"

# Live Paper Trading State ($10,000 Demo Account)
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
    "active_trade": None,  # Stores current live open trade
    "trade_history": [],
    "candles": [],
    "last_analysis": {
        "status": "Initializing Live M15 Scanner...",
        "market_structure": "SCANNING",
        "liquidity_pool": "Calculating...",
        "order_block": "Calculating...",
        "bias": "NEUTRAL",
        "last_price": 0.0,
        "updated_at": "Just now",
    },
}


def fetch_and_analyze():
    """Fetches real M15 Gold data from Twelve Data & executes SMC Demo Trades"""
    if not API_KEY:
        state["last_analysis"]["status"] = "ERROR: Missing TWELVEDATA_API_KEY"
        return

    url = f"https://api.twelvedata.com/time_series?symbol={SYMBOL}&interval={INTERVAL}&outputsize=30&apikey={API_KEY}"
    try:
        res = requests.get(url, timeout=10).json()
        if "values" not in res:
            state["last_analysis"][
                "status"
            ] = f"API Wait: {res.get('message', 'Rate limit or market closed')}"
            return

        raw_candles = res["values"]
        # Reverse so oldest is first, newest is last
        candles = [
            {
                "datetime": c["datetime"],
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            }
            for c in reversed(raw_candles)
        ]

        state["candles"] = candles[-12:]  # Keep last 12 candles for live chart
        current_candle = candles[-1]
        prev_candles = candles[:-1]
        current_price = current_candle["close"]

        # Calculate Recent Liquidity Pools (Last 15 candles High/Low)
        recent_high = max(c["high"] for c in prev_candles[-15:])
        recent_low = min(c["low"] for c in prev_candles[-15:])

        # Order Block approximation
        ob_low = round(recent_low + (recent_high - recent_low) * 0.15, 2)
        ob_high = round(recent_high - (recent_high - recent_low) * 0.15, 2)

        # 1. Check Active Trade (SL / TP Hit Management)
        active = state["active_trade"]
        if active:
            # Update live floating PnL (1 standard lot simulation = $100 per $1 move)
            lot_multiplier = 20.0  # 0.20 Lot size
            if active["type"] == "BUY":
                active["floating_pnl"] = round(
                    (current_price - active["entry"]) * lot_multiplier, 2
                )
                if current_price >= active["tp"]:
                    close_trade("WIN", active["tp"], lot_multiplier)
                elif current_price <= active["sl"]:
                    close_trade("LOSS", active["sl"], lot_multiplier)
            elif active["type"] == "SELL":
                active["floating_pnl"] = round(
                    (active["entry"] - current_price) * lot_multiplier, 2
                )
                if current_price <= active["tp"]:
                    close_trade("WIN", active["tp"], lot_multiplier)
                elif current_price >= active["sl"]:
                    close_trade("LOSS", active["sl"], lot_multiplier)

        # 2. Check SMC Setup for New Demo Trade (If Hunter Mode ON and no active trade)
        bias = "WAITING_CONFIRMATION"
        structure = (
            "BULLISH_ORDER_FLOW"
            if current_price > candles[-10]["close"]
            else "BEARISH_ORDER_FLOW"
        )

        # Bullish Stop-Hunt (Swept recent low or rejected from lower zone)
        wick_bottom = (
            min(current_candle["open"], current_candle["close"])
            - current_candle["low"]
        )
        wick_top = current_candle["high"] - max(
            current_candle["open"], current_candle["close"]
        )

        if state["hunter_mode"] and state["active_trade"] is None:
            # Condition A: Bullish Sweep / Pin Rejection near Support
            if current_candle["low"] <= ob_low and current_price > ob_low:
                sl = round(current_candle["low"] - 2.50, 2)
                risk = max(abs(current_price - sl), 1.50)
                tp = round(current_price + (risk * 2.5), 2)
                open_trade(
                    "BUY",
                    current_price,
                    sl,
                    tp,
                    "BEAR-TRAP BUY (Liquidity Sweep)",
                )
                bias = "BULLISH_SNIPER_ENTRY"

            # Condition B: Bearish Sweep / Pin Rejection near Resistance
            elif current_candle["high"] >= ob_high and current_price < ob_high:
                sl = round(current_candle["high"] + 2.50, 2)
                risk = max(abs(sl - current_price), 1.50)
                tp = round(current_price - (risk * 2.5), 2)
                open_trade(
                    "SELL",
                    current_price,
                    sl,
                    tp,
                    "BULL-TRAP SELL (Liquidity Sweep)",
                )
                bias = "BEARISH_SNIPER_ENTRY"

        now_str = datetime.datetime.utcnow().strftime("%H:%M:%S UTC")
        state["last_analysis"] = {
            "status": "Live M15 TwelveData Connected",
            "market_structure": structure,
            "liquidity_pool": f"${recent_low:.2f} -${recent_high:.2f}",
            "order_block": f"Demand: ${ob_low:.2f} \vert{} Supply:${ob_high:.2f}",
            "bias": bias if not state["active_trade"] else "IN_ACTIVE_TRADE",
            "last_price": round(current_price, 2),
            "updated_at": now_str,
        }

    except Exception as e:
        state["last_analysis"]["status"] = f"Sync Error: {str(e)}"


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


def close_trade(result, exit_price, multiplier):
    active = state["active_trade"]
    if not active:
        return
    pnl = (
        (exit_price - active["entry"]) * multiplier
        if active["type"] == "BUY"
        else (active["entry"] - exit_price) * multiplier
    )
    pnl = round(pnl, 2)

    state["virtual_balance"] = round(state["virtual_balance"] + pnl, 2)
    state["daily_pnl"] = round(state["daily_pnl"] + pnl, 2)
    state["total_trades"] += 1
    if pnl > 0:
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


@app.on_event("startup")
async def startup_event():
    fetch_and_analyze()

    async def loop():
        while True:
            await asyncio.sleep(60)  # Fetch live price every 60 seconds
            fetch_and_analyze()

    asyncio.create_task(loop())


@app.get("/api/status")
def get_status():
    return state


@app.post("/api/force_demo_trade")
def force_demo_trade():
    """Button to trigger an instant live demo trade at current market price for testing"""
    price = state["last_analysis"]["last_price"]
    if price <= 0:
        return {"error": "Waiting for live price"}
    if state["active_trade"] is None:
        sl = round(price - 3.0, 2)
        tp = round(price + 7.5, 2)
        open_trade("BUY", price, sl, tp, "MANUAL_SNIPER_TEST (M15 Live)")
    return state


@app.post("/api/close_active_trade")
def manual_close():
    """Closes active trade at current live price"""
    price = state["last_analysis"]["last_price"]
    if state["active_trade"] and price > 0:
        close_trade("MANUAL_EXIT", price, 20.0)
    return state


@app.get("/", response_class=HTMLResponse)
def live_dashboard():
    """Serves the Live Connected SMC Dashboard directly!"""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SMC Shadow AI - Live Cloud Engine</title>
    <style>
        body { margin: 0; font-family: 'Segoe UI', system-ui, sans-serif; background: #070b14; color: #e2e8f0; padding: 14px; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 14px; margin-bottom: 14px; box-shadow: 0 4px 20px rgba(0,0,0,0.4); }
        .header { display: flex; justify-content: space-between; align-items: center; }
        .badge-live { background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid #10b981; padding: 5px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; }
        .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 12px; }
        .stat-box { background: #0b1120; border: 1px solid #1e293b; padding: 10px; border-radius: 10px; }
        .stat-label { font-size: 11px; color: #94a3b8; }
        .stat-val { font-size: 17px; font-weight: 800; margin-top: 4px; }
        .price-big { font-size: 28px; font-weight: 900; color: #fbbf24; text-align: right; }
        .btn { padding: 10px 14px; border-radius: 9px; font-weight: 700; border: none; cursor: pointer; font-size: 13px; width: 100%; }
        .btn-gold { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid #f59e0b; }
        .btn-red { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid #ef4444; }
        .trade-box { background: rgba(16, 185, 129, 0.08); border: 1px solid #10b981; border-radius: 10px; padding: 12px; margin-top: 10px; }
        .row { display: flex; justify-content: space-between; align-items: center; margin: 6px 0; font-size: 13px; }
        .green { color: #10b981; } .red { color: #f87171; } .gold { color: #fbbf24; }
    </style>
</head>
<body>
    <div class="card header">
        <div>
            <div style="font-weight:900; font-size:18px; color:#fbbf24;">⚡ SMC SHADOW AI <span style="font-size:11px; color:#94a3b8;">v3.0 CLOUD LIVE</span></div>
            <div id="conn-status" style="font-size:11px; color:#94a3b8;">Connecting to Twelve Data M15...</div>
        </div>
        <div class="badge-live">● LIVE M15</div>
    </div>

    <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <div>
                <div style="font-size:12px; color:#94a3b8;">SYMBOL & TIMEFRAME</div>
                <div style="font-size:18px; font-weight:800;">XAU/USD (Gold • 15m)</div>
            </div>
            <div>
                <div id="live-price" class="price-big">$0.00</div>
                <div id="updated-time" style="font-size:11px; color:#10b981; text-align:right;">Syncing...</div>
            </div>
        </div>

        <div class="grid-3">
            <div class="stat-box">
                <div class="stat-label">Demo Equity</div>
                <div id="balance" class="stat-val">$10,000.00</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Realized P&L</div>
                <div id="daily-pnl" class="stat-val green">$0.00</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Win Rate</div>
                <div id="win-rate" class="stat-val gold">0W / 0L (0%)</div>
            </div>
        </div>
    </div>

    <div class="card">
        <div style="font-weight:800; font-size:14px; margin-bottom:8px; color:#93c5fd;">📡 REAL-TIME SMC M15 SCANNER</div>
        <div class="row"><span>Market Structure:</span> <strong id="structure" class="gold">---</strong></div>
        <div class="row"><span>Liquidity Pool (15m):</span> <strong id="liquidity">---</strong></div>
        <div class="row"><span>Order Block Zones:</span> <strong id="ob">---</strong></div>
        <div class="row"><span>AI Sniper Bias:</span> <strong id="bias" class="green">---</strong></div>
    </div>

    <div class="card">
        <div style="font-weight:800; font-size:14px; margin-bottom:8px;">🎯 ACTIVE DEMO TRADE (PAPER ENGINE)</div>
        <div id="active-trade-container">
            <div style="color:#94a3b8; font-size:13px; padding:10px 0;">No open trade. AI is scanning M15 Liquidity Sweeps for auto-entry...</div>
        </div>
        <div style="display:flex; gap:10px; margin-top:10px;">
            <button class="btn btn-gold" onclick="triggerTestTrade()">⚡ Force Live Demo Trade (Test)</button>
            <button class="btn btn-red" onclick="closeActiveTrade()">✖ Close Active Trade</button>
        </div>
    </div>

    <div class="card">
        <div style="font-weight:800; font-size:14px; margin-bottom:8px;">📜 CLOSED TRADE HISTORY</div>
        <div id="history-container" style="font-size:12px; color:#94a3b8;">No closed trades yet.</div>
    </div>

    <script>
        async function loadStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();

                document.getElementById('conn-status').innerText = data.last_analysis.status;
                document.getElementById('live-price').innerText = '$' + data.last_analysis.last_price.toFixed(2);
                document.getElementById('updated-time').innerText = 'Last Tick: ' + data.last_analysis.updated_at;
                document.getElementById('balance').innerText = '$' + data.virtual_balance.toFixed(2);

                const pnlEl = document.getElementById('daily-pnl');
                pnlEl.innerText = (data.daily_pnl >= 0 ? '+$' : '-$') + Math.abs(data.daily_pnl).toFixed(2);
                pnlEl.className = 'stat-val ' + (data.daily_pnl >= 0 ? 'green' : 'red');

                document.getElementById('win-rate').innerText = `${data.wins}W / ${data.losses}L (${data.win_rate}%)`;
                document.getElementById('structure').innerText = data.last_analysis.market_structure;
                document.getElementById('liquidity').innerText = data.last_analysis.liquidity_pool;
                document.getElementById('ob').innerText = data.last_analysis.order_block;
                document.getElementById('bias').innerText = data.last_analysis.bias;

                const tradeBox = document.getElementById('active-trade-container');
                if (data.active_trade) {
                    const t = data.active_trade;
                    const pnlColor = t.floating_pnl >= 0 ? 'green' : 'red';
                    tradeBox.innerHTML = `
                        <div class="trade-box">
                            <div class="row"><strong>${t.type} XAU/USD (0.20 Lot)</strong> <span class="${pnlColor}" style="font-weight:900; font-size:16px;">Floating: $${t.floating_pnl.toFixed(2)}</span></div>
                            <div class="row"><span>Entry: <b>$${t.entry}</b></span> <span>SL: <b class="red">$${t.sl}</b></span> <span>TP: <b class="green">$${t.tp}</b></span></div>
                            <div style="font-size:11px; color:#94a3b8; margin-top:4px;">Reason: ${t.reason} • Opened: ${t.opened_at}</div>
                        </div>
                    `;
                } else {
                    tradeBox.innerHTML = `<div style="color:#94a3b8; font-size:13px; padding:6px 0;">Waiting for M15 Stop-Hunt Sweep... (Or tap 'Force Live Demo Trade' below to test)</div>`;
                }

                const histBox = document.getElementById('history-container');
                if (data.trade_history && data.trade_history.length > 0) {
                    histBox.innerHTML = data.trade_history.map(h => `
                        <div class="row" style="border-bottom:1px solid #1f2937; padding:6px 0;">
                            <span><b>${h.type}</b> ($${h.entry} ➔ $${h.exit})</span>
                            <span class="${h.pnl >= 0 ? 'green' : 'red'}" style="font-weight:700;">${h.pnl >= 0 ? '+$' : '-$'}${Math.abs(h.pnl)} (${h.result})</span>
                        </div>
                    `).join('');
                }
            } catch (e) {
                console.error(e);
            }
        }

        async function triggerTestTrade() {
            await fetch('/api/force_demo_trade', { method: 'POST' });
            loadStatus();
        }

        async function closeActiveTrade() {
            await fetch('/api/close_active_trade', { method: 'POST' });
            loadStatus();
        }

        loadStatus();
        setInterval(loadStatus, 5000);
    </script>
</body>
</html>
"""
        

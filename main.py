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
        "status": "Ready - Connecting TwelveData...",
        "market_structure": "SCANNING",
        "liquidity_pool": "Calculating...",
        "order_block": "Calculating...",
        "bias": "WAITING_CONFIRMATION",
        "last_price": 0.0,
        "updated_at": "Just now",
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
    # Fetch from TwelveData at most once every 30 seconds to protect free API quota
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
            state["last_analysis"][
                "status"
            ] = f"API Note: {res.get('message', 'Wait 1 min')}"
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

        active = state["active_trade"]
        if active:
            mult = 20.0
            if active["type"] == "BUY":
                active["floating_pnl"] = round(
                    (price - active["entry"]) * mult, 2
                )
                if price >= active["tp"]:
                    close_trade("TP_HIT", active["tp"], mult)
                elif price <= active["sl"]:
                    close_trade("SL_HIT", active["sl"], mult)
            else:
                active["floating_pnl"] = round(
                    (active["entry"] - price) * mult, 2
                )
                if price <= active["tp"]:
                    close_trade("TP_HIT", active["tp"], mult)
                elif price >= active["sl"]:
                    close_trade("SL_HIT", active["sl"], mult)

        structure = (
            "BULLISH_ORDER_FLOW"
            if price >= candles[-8]["close"]
            else "BEARISH_ORDER_FLOW"
        )
        bias = "WAITING_CONFIRMATION"

        if state["hunter_mode"] and state["active_trade"] is None:
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

        state["last_analysis"] = {
            "status": "Live M15 TwelveData Active",
            "market_structure": structure,
            "liquidity_pool": f"${recent_low:.2f} - ${recent_high:.2f}",
            "order_block": f"Demand: ${ob_low:.2f} | Supply: ${ob_high:.2f}",
            "bias": "IN_ACTIVE_TRADE" if state["active_trade"] else bias,
            "last_price": round(price, 2),
            "updated_at": datetime.datetime.utcnow().strftime("%H:%M:%S UTC"),
        }
    except Exception as e:
        state["last_analysis"]["status"] = f"Sync error: {str(e)}"


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
            "LIVE_SNIPER_DEMO_ENTRY",
        )
    return state


@app.post("/api/close_active_trade")
def close_active():
    price = state["last_analysis"]["last_price"]
    if state["active_trade"] and price > 0:
        close_trade("MANUAL_CLOSE", price, 20.0)
    return state


HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SMC Shadow AI Live</title>
<style>
body { margin:0; font-family:system-ui,sans-serif; background:#070b14; color:#e2e8f0; padding:12px; }
.card { background:#111827; border:1px solid #1f2937; border-radius:12px; padding:14px; margin-bottom:12px; }
.row { display:flex; justify-content:space-between; align-items:center; margin:6px 0; font-size:13px; }
.grid3 { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin-top:10px; }
.box { background:#0b1120; border:1px solid #1e293b; padding:10px; border-radius:8px; }
.lbl { font-size:11px; color:#94a3b8; }
.val { font-size:16px; font-weight:800; margin-top:4px; }
.btn { padding:10px; border-radius:8px; font-weight:700; border:none; cursor:pointer; width:100%; font-size:12px; }
.btn-g { background:#f59e0b22; color:#fbbf24; border:1px solid #f59e0b; }
.btn-r { background:#ef444422; color:#f87171; border:1px solid #ef4444; }
.green { color:#10b981; } .red { color:#f87171; } .gold { color:#fbbf24; }
</style>
</head>
<body>
<div class="card row">
  <div>
    <div style="font-weight:900;font-size:17px;color:#fbbf24;">⚡ SMC SHADOW AI v3.0</div>
    <div id="st" style="font-size:11px;color:#94a3b8;">Syncing Live Feed...</div>
  </div>
  <div style="background:#10b98122;color:#10b981;border:1px solid #10b981;padding:4px 10px;border-radius:16px;font-size:11px;font-weight:700;">● LIVE M15</div>
</div>

<div class="card">
  <div class="row">
    <div><div class="lbl">ASSET</div><div style="font-size:17px;font-weight:800;">XAU/USD (Gold 15m)</div></div>
    <div style="text-align:right;">
      <div id="prc" style="font-size:26px;font-weight:900;color:#fbbf24;">$0.00</div>
      <div id="tm" style="font-size:10px;color:#10b981;">Updating...</div>
    </div>
  </div>
  <div class="grid3">
    <div class="box"><div class="lbl">Demo Equity</div><div id="bal" class="val">$10000.00</div></div>
    <div class="box"><div class="lbl">Realized P&L</div><div id="pnl" class="val green">$0.00</div></div>
    <div class="box"><div class="lbl">Win Rate</div><div id="wr" class="val gold">0W/0L</div></div>
  </div>
</div>

<div class="card">
  <div style="font-weight:800;font-size:13px;color:#93c5fd;margin-bottom:6px;">📡 LIVE M15 SMC SCANNER</div>
  <div class="row"><span>Structure:</span><b id="ms" class="gold">---</b></div>
  <div class="row"><span>Liquidity Pool:</span><b id="lp">---</b></div>
  <div class="row"><span>Order Blocks:</span><b id="ob">---</b></div>
  <div class="row"><span>Sniper Status:</span><b id="bs" class="green">---</b></div>
</div>

<div class="card">
  <div style="font-weight:800;font-size:13px;margin-bottom:6px;">🎯 ACTIVE DEMO POSITION</div>
  <div id="act" style="font-size:13px;color:#94a3b8;padding:6px 0;">No active trade. Scanning M15 sweeps...</div>
  <div style="display:flex;gap:8px;margin-top:8px;">
    <button class="btn btn-g" onclick="act('/api/force_demo_trade')">⚡ Open Live Demo Trade</button>
    <button class="btn btn-r" onclick="act('/api/close_active_trade')">✖ Close Trade</button>
  </div>
</div>

<div class="card">
  <div style="font-weight:800;font-size:13px;margin-bottom:6px;">📜 TRADE HISTORY</div>
  <div id="hst" style="font-size:12px;color:#94a3b8;">No closed trades yet.</div>
</div>

<script>
async function load() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    document.getElementById('st').innerText = d.last_analysis.status;
    document.getElementById('prc').innerText = '$' + d.last_analysis.last_price.toFixed(2);
    document.getElementById('tm').innerText = 'Tick: ' + d.last_analysis.updated_at;
    document.getElementById('bal').innerText = '$' + d.virtual_balance.toFixed(2);
    const p = document.getElementById('pnl');
    p.innerText = (d.daily_pnl>=0?'+$':'-$') + Math.abs(d.daily_pnl).toFixed(2);
    p.className = 'val ' + (d.daily_pnl>=0?'green':'red');
    document.getElementById('wr').innerText = d.wins+'W/'+d.losses+'L ('+d.win_rate+'%)';
    document.getElementById('ms').innerText = d.last_analysis.market_structure;
    document.getElementById('lp').innerText = d.last_analysis.liquidity_pool;
    document.getElementById('ob').innerText = d.last_analysis.order_block;
    document.getElementById('bs').innerText = d.last_analysis.bias;

    const ab = document.getElementById('act');
    if (d.active_trade) {
      const t = d.active_trade;
      const c = t.floating_pnl>=0?'green':'red';
      ab.innerHTML = '<div style="background:#10b98115;border:1px solid #10b981;padding:10px;border-radius:8px;">' +
        '<div class="row"><b>'+t.type+' XAU/USD (0.20 Lot)</b><b class="'+c+'" style="font-size:15px;">PnL: $'+t.floating_pnl.toFixed(2)+'</b></div>' +
        '<div class="row"><span>Entry: $'+t.entry+'</span><span class="red">SL: $'+t.sl+'</span><span class="green">TP: $'+t.tp+'</span></div>' +
        '<div style="font-size:11px;color:#94a3b8;">'+t.reason+' • '+t.opened_at+'</div></div>';
    } else {
      ab.innerHTML = 'Waiting for M15 Stop-Hunt Sweep... (Tap Open Live Demo Trade to test)';
    }

    const hb = document.getElementById('hst');
    if (d.trade_history && d.trade_history.length > 0) {
      hb.innerHTML = d.trade_history.map(h =>
        '<div class="row" style="border-bottom:1px solid #1f2937;padding:4px 0;">' +
        '<span><b>'+h.type+'</b> ($'+h.entry+' ➔ $'+h.exit+')</span>' +
        '<b class="'+(h.pnl>=0?'green':'red')+'">'+(h.pnl>=0?'+$':'-$')+Math.abs(h.pnl)+' ('+h.result+')</b></div>'
      ).join('');
    }
  } catch(e) {}
}
async function act(u) { await fetch(u, {method:'POST'}); load(); }
load();
setInterval(load, 6000);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML_PAGE
    

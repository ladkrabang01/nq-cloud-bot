import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone, timedelta

# --- CONFIGURATION (Defaulted to your Bot & Chat ID) ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8502955901:AAGZQiWhtsQthBD1R1cem2tu6UJLzVKe35I")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7196322811")
PORT = int(os.getenv("PORT", 10000))

# Timezone Thailand UTC+7
TZ_THAI = timezone(timedelta(hours=7))

# Dummy HTTP server to satisfy Render.com Free Web Service health check
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"NQ Master Cloud Bot is running 24/7 on Render.com!")
    def log_message(self, format, *args):
        return # Silence HTTP server logs

def start_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print(f"[LOG ONLY] {message}")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending telegram: {e}")

# State tracker
state = {
    "current_day": None,
    "asia_high": 0.0,
    "asia_low": 999999.0,
    "swept_high": False,
    "swept_low": False,
    "sweep_ext_h": 0.0,
    "sweep_ext_l": 999999.0,
    "active_trade": None,  # dict: {type, entry, sl, tp, be_price, be_triggered}
    "last_bar_time": None
}

def run_strategy_cycle():
    now_thai = datetime.now(TZ_THAI)
    today_str = now_thai.strftime("%Y-%m-%d")
    hour = now_thai.hour

    # Reset at start of new day (06:00 Thai)
    if state["current_day"] != today_str and hour >= 6:
        state["current_day"] = today_str
        state["asia_high"] = 0.0
        state["asia_low"] = 999999.0
        state["swept_high"] = False
        state["swept_low"] = False
        state["sweep_ext_h"] = 0.0
        state["sweep_ext_l"] = 999999.0
        state["active_trade"] = None
        send_telegram(f"🌅 *[NQ MASTER 25K]* ระบบเริ่มเฝ้ากราฟประจำวัน: {today_str}\nช่วงเวลาเทรด: 07:00 - 19:00 น. (ปิดคอมได้เลย)")

    # Only trade within 07:00 - 19:00 Thai Time
    in_trading_window = (hour >= 7 and hour < 19)
    in_asia_window    = (hour >= 6 and hour < 14)
    in_london_window  = (hour >= 14 and hour < 19)

    # Fetch 5m NQ data (using NQ=F from Yahoo Finance)
    try:
        ticker = yf.Ticker("NQ=F")
        df = ticker.history(period="2d", interval="5m")
        if df.empty or len(df) < 5:
            return
    except Exception as e:
        print(f"Fetch error: {e}")
        return

    # Convert timestamps to Thai
    df.index = df.index.tz_convert("Asia/Bangkok")
    latest_bar_time = df.index[-1]
    is_new_bar = (latest_bar_time != state["last_bar_time"])

    # Update Asian Range
    if in_asia_window:
        highs = df.between_time("06:00", "14:00")["High"]
        lows  = df.between_time("06:00", "14:00")["Low"]
        if not highs.empty:
            state["asia_high"] = float(highs.max())
            state["asia_low"]  = float(lows.min())

    cur_price = float(df["Close"].iloc[-1])

    # 1. CHECK BREAKEVEN & EXITS ON ACTIVE TRADE
    trade = state["active_trade"]
    if trade is not None:
        # Check Breakeven (+1.4R)
        if not trade["be_triggered"]:
            if trade["type"] == "BUY" and cur_price >= trade["be_price"]:
                trade["be_triggered"] = True
                trade["sl"] = trade["entry"] + 2.0  # Move SL to entry + 2 ticks
                send_telegram(
                    f"🛡️ *[APEX 25K] BREAKEVEN TRIGGERED!*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"ราคาวิ่งบวก +1.4R แล้ว! (ราคาปัจจุบัน: `{cur_price:.1f}`)\n"
                    f"👉 *คำแนะนำ:* เลื่อน Stop Loss มาบังหน้าไม้ที่ทุน (`{trade['entry']:.1f}`) ทันที เพื่อป้องกันกำไร!"
                )
            elif trade["type"] == "SELL" and cur_price <= trade["be_price"]:
                trade["be_triggered"] = True
                trade["sl"] = trade["entry"] - 2.0
                send_telegram(
                    f"🛡️ *[APEX 25K] BREAKEVEN TRIGGERED!*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"ราคาวิ่งบวก +1.4R แล้ว! (ราคาปัจจุบัน: `{cur_price:.1f}`)\n"
                    f"👉 *คำแนะนำ:* เลื่อน Stop Loss มาบังหน้าไม้ที่ทุน (`{trade['entry']:.1f}`) ทันที เพื่อป้องกันกำไร!"
                )

        # Check TP or SL exit
        if trade["type"] == "BUY":
            if cur_price >= trade["tp"]:
                send_telegram(f"🎯 *[APEX 25K] TAKE PROFIT HIT!* (+{trade['tp']-trade['entry']:.1f} pts) เข้าเป้าเรียบร้อย! 🎉")
                state["active_trade"] = None
            elif cur_price <= trade["sl"]:
                msg = "🛑 *[APEX 25K] STOP LOSS HIT*" if not trade["be_triggered"] else "🛡️ *[APEX 25K] ชนหน้าไม้ Breakeven เสมอตัว ไม่ขาดทุน*"
                send_telegram(msg)
                state["active_trade"] = None
        elif trade["type"] == "SELL":
            if cur_price <= trade["tp"]:
                send_telegram(f"🎯 *[APEX 25K] TAKE PROFIT HIT!* (+{trade['entry']-trade['tp']:.1f} pts) เข้าเป้าเรียบร้อย! 🎉")
                state["active_trade"] = None
            elif cur_price >= trade["sl"]:
                msg = "🛑 *[APEX 25K] STOP LOSS HIT*" if not trade["be_triggered"] else "🛡️ *[APEX 25K] ชนหน้าไม้ Breakeven เสมอตัว ไม่ขาดทุน*"
                send_telegram(msg)
                state["active_trade"] = None

    # 2. CHECK NEW ENTRY SIGNALS (Only when flat and in trading window)
    if is_new_bar and in_trading_window and state["active_trade"] is None:
        state["last_bar_time"] = latest_bar_time
        bar = df.iloc[-2] # Closed bar
        c_high = float(bar["High"])
        c_low  = float(bar["Low"])
        c_close = float(bar["Close"])
        prev_close = float(df.iloc[-3]["Close"])

        # London Judas Sweep
        if in_london_window and state["asia_high"] > 0:
            if not state["swept_high"] and c_high >= (state["asia_high"] + 15.0):
                state["swept_high"] = True
                state["sweep_ext_h"] = c_high
            if state["swept_high"]:
                state["sweep_ext_h"] = max(state["sweep_ext_h"], c_high)
                # Judas Short: Closed back below Asian High
                if c_close < state["asia_high"] and prev_close >= state["asia_high"]:
                    sl_dist = min(35.0, max(12.0, (state["sweep_ext_h"] + 4.0) - c_close))
                    entry = c_close
                    sl = entry + sl_dist
                    tp = entry - (sl_dist * 2.2)
                    be = entry - (sl_dist * 1.4)
                    state["active_trade"] = {"type": "SELL", "entry": entry, "sl": sl, "tp": tp, "be_price": be, "be_triggered": False}
                    state["swept_high"] = False
                    send_telegram(
                        f"🔴 *[APEX 25K] SELL SIGNAL (Judas Short)*\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📍 *Entry:* `{entry:.1f}` (ออก 2 MNQ)\n"
                        f"🛑 *Stop Loss:* `{sl:.1f}` (-{sl_dist:.1f} pts / -$140)\n"
                        f"🎯 *Take Profit:* `{tp:.1f}` (+{sl_dist*2.2:.1f} pts / +$308)\n"
                        f"🛡️ *จุดกันหน้าไม้:* `{be:.1f}` (เมื่อถึง 1.4R จะมีเตือนอีกครั้ง)\n"
                        f"⏰ *เวลา:* {now_thai.strftime('%H:%M:%S')}"
                    )

            if not state["swept_low"] and c_low <= (state["asia_low"] - 15.0):
                state["swept_low"] = True
                state["sweep_ext_l"] = c_low
            if state["swept_low"]:
                state["sweep_ext_l"] = min(state["sweep_ext_l"], c_low)
                # Judas Long: Closed back above Asian Low
                if c_close > state["asia_low"] and prev_close <= state["asia_low"]:
                    sl_dist = min(35.0, max(12.0, c_close - (state["sweep_ext_l"] - 4.0)))
                    entry = c_close
                    sl = entry - sl_dist
                    tp = entry + (sl_dist * 2.2)
                    be = entry + (sl_dist * 1.4)
                    state["active_trade"] = {"type": "BUY", "entry": entry, "sl": sl, "tp": tp, "be_price": be, "be_triggered": False}
                    state["swept_low"] = False
                    send_telegram(
                        f"🟢 *[APEX 25K] BUY SIGNAL (Judas Long)*\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📍 *Entry:* `{entry:.1f}` (ออก 2 MNQ)\n"
                        f"🛑 *Stop Loss:* `{sl:.1f}` (-{sl_dist:.1f} pts / -$140)\n"
                        f"🎯 *Take Profit:* `{tp:.1f}` (+{sl_dist*2.2:.1f} pts / +$308)\n"
                        f"🛡️ *จุดกันหน้าไม้:* `{be:.1f}` (เมื่อถึง 1.4R จะมีเตือนอีกครั้ง)\n"
                        f"⏰ *เวลา:* {now_thai.strftime('%H:%M:%S')}"
                    )

if __name__ == "__main__":
    # Start background HTTP server for Render Free Web Service
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
    
    send_telegram("🚀 *NQ Master Cloud Alert Bot เริ่มต้นทำงานบน Render.com เรียบร้อยแล้ว! (เฝ้า 07:00 - 19:00 น. ปิดคอมได้ 100%)*")
    
    while True:
        try:
            run_strategy_cycle()
        except Exception as e:
            print(f"Cycle error: {e}")
        time.sleep(20) # Check every 20 seconds

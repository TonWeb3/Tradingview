# BINARY OP

A Polymarket-style **up/down window** simulator. Marks the open price at the
start of each 5-minute or 15-minute window and predicts whether the window will
close above or below that open — using **live Binance data** for price/candles
and **TradingView TA** for the directional signal.

It is a **paper simulator**: it tracks hypothetical win/loss, it does not place
real orders anywhere.

```bash
pip install -r requirements.txt
python app.py            # or: uvicorn app:app --port 5000
# open http://127.0.0.1:5000
```

## How it works

**The window (the "market").** Every 5m or 15m, Binance opens a new candle. Its
open price is marked. When the candle closes, `close > open` = the window went
**UP** (green), `close < open` = **DOWN** (red) — exactly the Polymarket BTC
up/down market. Live price, the delta vs open, and a countdown are shown live.

**The signal.** On every *new candle* of each selected **signal timeframe**, the
app calls tradingview_ta for the symbol and reads the rating you asked for
(Recommendation, Moving Averages, and/or Oscillators). Each
`(timeframe × indicator)` cell resolves to a direction:

| Rating | Direction |
|---|---|
| BUY / STRONG_BUY | UP |
| SELL / STRONG_SELL | DOWN |
| NEUTRAL | — (forces WAIT) |

**Unanimous rule.** The combined signal fires only when **every** selected cell
agrees:
- all UP → take an **UP** position
- all DOWN → take a **DOWN** position
- anything neutral or mixed → **WAIT** (no trade)

This holds for any mix: one timeframe or several, one indicator or all three —
they must *all* agree.

**Entry & settlement.** The first time the signal aligns during an open window,
a position is entered (once per window). At window close, `close vs open`
decides the outcome: win if your predicted direction matches the candle color.
No trade if the signal never aligned that window.

**Fresh signals only.** A position is entered only with a signal that was
re-evaluated *after* the current window opened. Since every evaluation waits
`signal_delay` after a candle closes, a new window never enters on a stale,
carried-over signal — it holds until a fresh reading arrives.

**Neutral close.** If an open position's signal breaks back to neutral (the
selected timeframes/indicators stop agreeing), the position is closed
immediately mid-window and logged as a **neutral close** — a distinct outcome
that is *not* counted as a win or loss.

**Data sources.**
- Binance **websocket** (`stream.binance.com`) — live price + candle open/close
  + new-candle events, for the window and every signal timeframe.
- **tradingview_ta** — the signal, called on each closed signal candle (after a
  short delay so its feed has published the candle; it lags ~15–40s).

History starts empty and fills only as real windows settle after launch.

## Settings (`/settings`)

- **Market window** — 5m or 15m.
- **Symbol** — any Binance pair (default BTCUSDT).
- **Signal timeframes** — 1m / 5m / 15m / 30m / 1h; pick one or several.
- **Indicators to require** — Recommendation / Moving Averages / Oscillators;
  tick any combination.
- **Signal delay** — seconds to wait after a candle closes before calling TA.

Saving restarts the engine (fresh window, fresh history, new Binance
subscription) and persists to `config.json`.

### config.json

All settings live in `config.json`, which is the single source of truth:

- **read** at startup — the engine boots with whatever is in the file;
- **written** whenever you save on the settings page;
- **editable by hand** — change the file and restart (`python app.py`) to apply.

```json
{
  "symbol": "BTCUSDT",
  "window": "5m",
  "signal_timeframes": ["5m", "15m"],
  "use_recommendation": true,
  "use_ma": false,
  "use_oscillator": false,
  "signal_delay_sec": 20
}
```

## Files

| File | Role |
|---|---|
| `app.py` | FastAPI: serves the pages, `/ws` state stream, `/api/config` |
| `engine.py` | Binance ws, window lifecycle, per-candle evaluation, settlement |
| `signals.py` | tradingview_ta fetch + the unanimous-agreement combiner |
| `config.py` | config dataclass + `config.json` persistence |
| `static/` | dashboard + settings pages (self-contained HTML) |

## Deploy to Railway

The included `Dockerfile` is all Railway needs.

1. Push this `BINARY OP` folder to a GitHub repo (or a subfolder of one).
2. On [railway.app](https://railway.app): **New Project → Deploy from GitHub**.
3. If the repo has other folders, set the service **Root Directory** to the
   `BINARY OP` folder so Railway uses this Dockerfile.
4. Deploy. Railway injects `$PORT`; the app binds `0.0.0.0:$PORT` automatically.
   The container `EXPOSE`s 5000 and defaults to 5000 locally.

**Important — Binance geo-blocking.** Binance blocks some regions (US IPs get
HTTP 451). If the logs show the websocket failing to connect, change the Railway
service **region** to a non-US one (e.g. EU West or Southeast Asia) under
service → Settings → Region.

**Persistence.** Railway's filesystem is ephemeral, so `config.json` written via
the Settings page resets on redeploy. To keep settings across deploys either
(a) commit your `config.json`, or (b) attach a Railway **Volume** mounted at
`/app`.

Run the image locally the same way Railway does:

```bash
docker build -t binaryop .
docker run -p 5000:5000 binaryop      # open http://localhost:5000
```

## Notes & caveats

- **TA lag matters.** Because tradingview_ta trails the candle by tens of
  seconds, when a signal timeframe equals the window, that signal effectively
  predicts the *next* window. With a shorter signal timeframe than the window,
  entries happen within the current window. This is expected.
- **One entry per window**, no flipping mid-window — a clean binary-option entry.
- **Rate limits.** tradingview_ta shares TradingView's undocumented endpoint;
  keep the number of signal timeframes reasonable. The engine calls it once per
  closed candle per timeframe.
- Paper only — no exchange keys, no orders, no funds.

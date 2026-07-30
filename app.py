"""BINARY OP — FastAPI server.

A Polymarket-style up/down window simulator on Binance data, with signals from
tradingview_ta.

    pip install -r requirements.txt
    python app.py            # or: uvicorn app:app --port 5000
    # open http://127.0.0.1:5000
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Make sibling modules importable whether launched via `python app.py` or uvicorn.
sys.path.insert(0, str(Path(__file__).parent))

import csv
import io
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

import config as config_mod
from engine import Engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("binaryop")

HERE = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = config_mod.load()
    config_mod.save(cfg)                 # ensure config.json exists for editing
    app.state.engine = Engine(cfg)
    await app.state.engine.start()
    log.info("engine started with %s", config_mod.CONFIG_PATH.name)
    try:
        yield
    finally:
        await app.state.engine.stop()
        log.info("engine stopped")


app = FastAPI(title="BINARY OP", lifespan=lifespan)


class ConfigIn(BaseModel):
    symbol: str = "BTCUSDT"
    window: str = "15m"
    signal_timeframes: list[str] = []
    use_recommendation: bool = True
    use_ma: bool = False
    use_oscillator: bool = False
    close_on_neutral: bool = True
    close_on_reversal: bool = False
    signal_delay_sec: int = 20


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/settings")
def settings_page():
    return FileResponse(HERE / "static" / "settings.html")


@app.get("/api/config")
def get_config():
    c = app.state.engine.cfg
    return {
        "symbol": c.symbol, "window": c.window,
        "signal_timeframes": c.signal_timeframes,
        "use_recommendation": c.use_recommendation,
        "use_ma": c.use_ma, "use_oscillator": c.use_oscillator,
        "close_on_neutral": c.close_on_neutral,
        "close_on_reversal": c.close_on_reversal,
        "signal_delay_sec": c.signal_delay_sec,
        "options": {
            "windows": config_mod.WINDOWS,
            "signal_timeframes": config_mod.SIGNAL_TIMEFRAMES,
        },
    }


@app.post("/api/config")
async def set_config(body: ConfigIn):
    cfg = config_mod.Config(**body.model_dump()).normalized()
    errs = cfg.validate()
    if errs:
        return JSONResponse(status_code=400, content={"errors": errs})
    log.info("reconfigure: %s", body.model_dump())
    await app.state.engine.reconfigure(cfg)
    return {"ok": True}


@app.get("/api/state")
def get_state():
    return app.state.engine.snapshot()


@app.get("/api/history.csv")
def history_csv():
    """Download the current session's window history as CSV. History resets
    whenever settings change, so this always reflects the running config."""
    eng = app.state.engine
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time_utc", "open_time_ms", "open", "close", "result", "prediction", "outcome"])
    for h in eng.history:
        ts = datetime.fromtimestamp(h["openTime"] / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        w.writerow([ts, h["openTime"], h["open"], h["close"], h["result"],
                    h.get("prediction") or "", h["outcome"]])
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=binary_op_history.csv"},
    )


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    try:
        while True:
            await sock.send_json(app.state.engine.snapshot())
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning("ws client error: %s", e)


if __name__ == "__main__":
    import os
    import uvicorn

    # Railway (and most PaaS) inject the port to bind via $PORT; bind 0.0.0.0 so
    # the container is reachable. Locally these default to a friendly localhost.
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    print(f"\n  BINARY OP running on {host}:{port}\n  (local: http://127.0.0.1:{port})  Ctrl+C to stop.\n")
    uvicorn.run(app, host=host, port=port)

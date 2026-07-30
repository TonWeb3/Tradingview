"""The BINARY OP engine.

Streams Binance klines over websocket, keeps a Polymarket-style up/down window
(5m or 15m), and on every closed signal-timeframe candle asks tradingview_ta for
a fresh reading. When every selected (timeframe x indicator) cell agrees, it
takes an UP or DOWN position for the current window; at window close the price
vs the window-open decides win or loss.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque

import websockets

import config as config_mod
import signals as sig

log = logging.getLogger("binaryop")

WS_BASE = "wss://stream.binance.com:9443/stream?streams="


def now_ms() -> int:
    return int(time.time() * 1000)


class Engine:
    def __init__(self, cfg: config_mod.Config):
        self.cfg = cfg.normalized()
        self.live_price: float | None = None
        self.analyses: dict[str, dict] = {}          # tf -> groups dict
        self.signal = {"signal": sig.WAIT, "cells": [], "ready": False}
        self.signal_time: int = 0                    # when the signal was last refreshed (ms)
        self.win: dict | None = None                 # current window
        self.history: deque = deque(maxlen=200)
        self.stats = {"trades": 0, "wins": 0, "losses": 0, "flat": 0,
                      "neutral": 0, "reversal": 0}
        self.error: str | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._pending_tf: set[str] = set()

    # ---- lifecycle -----------------------------------------------------
    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def reconfigure(self, cfg: config_mod.Config):
        await self.stop()
        self.__init__(cfg)          # reset all state
        config_mod.save(self.cfg)
        await self.start()

    # ---- streams -------------------------------------------------------
    def _streams(self) -> str:
        sym = self.cfg.symbol.lower()
        wanted = [f"{sym}@kline_{self.cfg.window}"]
        for tf in self.cfg.signal_timeframes:
            s = f"{sym}@kline_{tf}"
            if s not in wanted:
                wanted.append(s)
        return "/".join(wanted)

    async def _run(self):
        # Seed the initial TA reading in the background so live price/window
        # (from the websocket) appear instantly instead of waiting on TA.
        asyncio.create_task(self._seed_safe())

        streams = self._streams()
        while not self._stop.is_set():
            try:
                log.info("connecting Binance ws: %s", streams)
                async with websockets.connect(WS_BASE + streams, ping_interval=20,
                                               close_timeout=5, open_timeout=10) as ws:
                    self.error = None
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        self._on_message(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.error = f"ws: {e}"
                log.error("websocket error: %s — reconnecting in 2s", e)
                await asyncio.sleep(2)

    # ---- seeding -------------------------------------------------------
    async def _seed_safe(self):
        try:
            await self._seed()
        except Exception as e:
            log.warning("seed failed: %s", e)

    async def _seed(self):
        """Fetch an initial tradingview_ta reading for every signal timeframe so
        the signal panel isn't blank at launch. History stays empty and only
        fills as real windows settle after the engine starts."""
        for tf in self.cfg.signal_timeframes:
            a = await asyncio.to_thread(sig.fetch_analysis, self.cfg.tv_symbol(), tf)
            self.analyses[tf] = sig.read_groups(a)
        self._recompute()

    # ---- message handling ---------------------------------------------
    def _on_message(self, msg: dict):
        k = msg.get("data", {}).get("k")
        if not k:
            return
        interval = k["i"]
        if interval == self.cfg.window:
            self._on_window_kline(k)
        if interval in self.cfg.signal_timeframes and k["x"]:
            self._schedule_eval(interval)

    def _on_window_kline(self, k: dict):
        t, T = k["t"], k["T"]
        o, c, closed = float(k["o"]), float(k["c"]), k["x"]
        self.live_price = c

        if self.win is None or self.win["openTime"] != t:
            # New window started — settle a lingering unsettled one first.
            if self.win and not self.win.get("settled"):
                self._settle(self.win)
            self.win = {
                "openTime": t, "endTime": T, "open": o, "close": c,
                "prediction": None, "entryPrice": None, "entryTime": None,
                "entryCells": None, "settled": False,
            }
            self._try_enter()          # maybe enter immediately on the fresh window
        else:
            self.win["close"] = c

        if closed and not self.win.get("settled"):
            self.win["close"] = c
            self._settle(self.win)

    def _settle(self, w: dict):
        o, c = w["open"], w["close"]
        result = "UP" if c > o else "DOWN" if c < o else "FLAT"
        pred = w.get("prediction")
        outcome = "none"
        if pred:
            if result == "FLAT":
                outcome = "flat"; self.stats["flat"] += 1
            elif pred == result:
                outcome = "win"; self.stats["wins"] += 1; self.stats["trades"] += 1
            else:
                outcome = "loss"; self.stats["losses"] += 1; self.stats["trades"] += 1
        self.history.append({
            "openTime": w["openTime"], "open": o, "close": c, "result": result,
            "prediction": pred, "outcome": outcome,
        })
        w["settled"] = True
        log.info("window settled: open=%s close=%s result=%s pred=%s -> %s",
                 o, c, result, pred, outcome)

    # ---- signal evaluation --------------------------------------------
    def _schedule_eval(self, tf: str):
        if tf in self._pending_tf:
            return
        self._pending_tf.add(tf)
        asyncio.create_task(self._eval_tf(tf))

    async def _eval_tf(self, tf: str):
        try:
            # let tradingview_ta publish the just-closed candle
            await asyncio.sleep(self.cfg.signal_delay_sec)
            a = await asyncio.to_thread(sig.fetch_analysis, self.cfg.tv_symbol(), tf)
            self.analyses[tf] = sig.read_groups(a)
            self._recompute()
            self._on_signal()
            log.info("TA refreshed [%s]: %s", tf, self.analyses[tf])
        finally:
            self._pending_tf.discard(tf)

    def _recompute(self):
        self.signal = sig.combine(
            self.analyses, self.cfg.signal_timeframes, self.cfg.indicators())
        # Stamp when the signal was (re)computed from a fresh TA fetch. Every
        # caller of _recompute runs right after fetching, and each fetch waits
        # signal_delay first — so this timestamp marks a delay-gated fresh read.
        self.signal_time = now_ms()

    def _on_signal(self):
        """React to a freshly computed signal: while in a position, maybe close
        it (reversal or neutral); otherwise consider entering."""
        w, s = self.win, self.signal
        if not w or w.get("settled"):
            return
        pred = w.get("prediction")
        if pred:
            direction = s["signal"]
            # Reversal: the signal flipped to the opposite direction. Neutral
            # does NOT count here — only an actual opposite buy/sell signal.
            if (self.cfg.close_on_reversal and s.get("ready")
                    and direction in (sig.UP, sig.DOWN) and direction != pred):
                self._close_position(w, "reversal")
                return
            # Neutral: agreement broke back to neutral/mixed.
            if self.cfg.close_on_neutral and s.get("ready") and direction == sig.WAIT:
                self._close_position(w, "neutral")
                return
            return                                  # same direction (or toggles off) -> hold
        self._try_enter()

    def _try_enter(self):
        """Enter the current window once, and only with a *fresh* signal.

        The signal must have been refreshed after this window opened; otherwise
        we'd be entering on a stale reading carried over from a previous candle.
        Because every refresh waits signal_delay, this also means the window has
        waited out the delay before any entry.
        """
        w, s = self.win, self.signal
        if not w or w.get("settled") or w.get("prediction"):
            return
        if self.signal_time < w["openTime"]:
            return                                  # signal predates this window — too old
        if s["signal"] in (sig.UP, sig.DOWN):
            w["prediction"] = s["signal"]
            w["entryPrice"] = self.live_price
            w["entryTime"] = now_ms()
            w["entryCells"] = s["cells"]
            log.info("ENTER %s @ %s (window open %s, signal age %.0fs)",
                     s["signal"], self.live_price, w["open"], (now_ms() - self.signal_time) / 1000)

    def _close_position(self, w: dict, reason: str):
        """Close an open position mid-window. `reason` is 'neutral' (signal went
        neutral) or 'reversal' (signal flipped to the opposite direction).
        Either way it's an early exit — not counted as a win or a loss."""
        price = self.live_price or w["close"]
        o = w["open"]
        result = "UP" if price > o else "DOWN" if price < o else "FLAT"
        self.stats[reason] += 1
        self.history.append({
            "openTime": w["openTime"], "open": o, "close": price, "result": result,
            "prediction": w["prediction"], "outcome": reason,
        })
        w["settled"] = True          # done trading this window; no re-entry, no double settle
        w["earlyClosed"] = True
        w["closeReason"] = reason
        log.info("%s CLOSE: pred=%s exit=%s (window open %s)",
                 reason.upper(), w["prediction"], price, o)

    # ---- snapshot for the UI ------------------------------------------
    def snapshot(self) -> dict:
        w = self.win
        win_out = None
        if w:
            o = w["open"]
            price = self.live_price or w["close"]
            delta = price - o
            win_out = {
                "open": o, "openTime": w["openTime"], "endTime": w["endTime"],
                "price": price, "delta": delta,
                "deltaPct": (delta / o * 100) if o else 0,
                "liveResult": "UP" if price > o else "DOWN" if price < o else "FLAT",
                "prediction": w.get("prediction"),
                "entryPrice": w.get("entryPrice"), "entryTime": w.get("entryTime"),
                "earlyClosed": bool(w.get("earlyClosed")),
                "closeReason": w.get("closeReason"),
                # True once a fresh (post-open, delay-gated) signal exists for this window.
                "signalFresh": self.signal_time >= w["openTime"],
            }
        trades = self.stats["trades"]
        winrate = (self.stats["wins"] / trades * 100) if trades else None
        return {
            "running": self._task is not None and not self._task.done(),
            "error": self.error,
            "serverTime": now_ms(),
            "config": {
                "symbol": self.cfg.symbol, "window": self.cfg.window,
                "signal_timeframes": self.cfg.signal_timeframes,
                "indicators": self.cfg.indicators(),
                "signal_delay_sec": self.cfg.signal_delay_sec,
            },
            "price": self.live_price,
            "window": win_out,
            "signal": self.signal,
            "stats": {**self.stats, "winrate": winrate},
            "history": list(self.history)[-30:][::-1],
        }

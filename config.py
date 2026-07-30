"""Runtime configuration for BINARY OP, persisted to config.json."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

# The market window — the binary-option period, Polymarket-style.
WINDOWS = ["5m", "15m"]

# tradingview_ta intervals allowed as signal timeframes.
SIGNAL_TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h"]

# The three rating groups a signal can be built from.
INDICATORS = ["recommendation", "ma", "oscillator"]


@dataclass
class Config:
    symbol: str = "BTCUSDT"                 # Binance symbol (also -> BINANCE:SYMBOL for TA)
    window: str = "15m"                     # market window: 5m or 15m
    signal_timeframes: list[str] = field(default_factory=lambda: ["5m", "15m"])
    use_recommendation: bool = True
    use_ma: bool = False
    use_oscillator: bool = False
    # Close an open position mid-window if the signal breaks back to neutral.
    close_on_neutral: bool = True
    # Close an open position mid-window if the signal reverses to the opposite
    # direction (UP position + DOWN signal, or vice versa). Neutral does not
    # trigger this — that's handled by close_on_neutral.
    close_on_reversal: bool = False
    # Seconds to wait after a candle closes before calling tradingview_ta, so the
    # scanner has time to publish the just-closed candle (its feed lags ~15-40s).
    signal_delay_sec: int = 20

    # ---- helpers -------------------------------------------------------
    def indicators(self) -> list[str]:
        picked = []
        if self.use_recommendation:
            picked.append("recommendation")
        if self.use_ma:
            picked.append("ma")
        if self.use_oscillator:
            picked.append("oscillator")
        return picked

    def tv_symbol(self) -> str:
        return f"BINANCE:{self.symbol.upper()}"

    def validate(self) -> list[str]:
        """Return a list of problems; empty means valid."""
        errs = []
        if self.window not in WINDOWS:
            errs.append(f"window must be one of {WINDOWS}")
        tfs = [t for t in self.signal_timeframes if t in SIGNAL_TIMEFRAMES]
        if not tfs:
            errs.append("select at least one signal timeframe")
        if not self.indicators():
            errs.append("tick at least one of recommendation / ma / oscillator")
        if not self.symbol.strip():
            errs.append("symbol is required")
        return errs

    def normalized(self) -> "Config":
        """A copy with fields cleaned to canonical form."""
        tfs = [t for t in SIGNAL_TIMEFRAMES if t in self.signal_timeframes]  # keep order
        return Config(
            symbol=self.symbol.strip().upper(),
            window=self.window,
            signal_timeframes=tfs or ["5m"],
            use_recommendation=self.use_recommendation,
            use_ma=self.use_ma,
            use_oscillator=self.use_oscillator,
            close_on_neutral=self.close_on_neutral,
            close_on_reversal=self.close_on_reversal,
            signal_delay_sec=max(0, min(120, int(self.signal_delay_sec))),
        )


def load() -> Config:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            return Config(**{k: data[k] for k in data if k in Config.__dataclass_fields__})
        except Exception:
            pass
    return Config()


def save(cfg: Config) -> None:
    CONFIG_PATH.write_text(json.dumps(asdict(cfg), indent=2))

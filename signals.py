"""tradingview_ta signal fetching and the unanimous-agreement combiner.

A signal is built from a set of (timeframe x indicator-group) cells. Each cell
resolves to UP / DOWN / NEUTRAL. The combined signal is:
  - UP    if every selected cell is UP
  - DOWN  if every selected cell is DOWN
  - WAIT  otherwise (any NEUTRAL, or a mix of UP and DOWN)
"""

from __future__ import annotations

from tradingview_ta import get_multiple_analysis

UP, DOWN, NEUTRAL, WAIT = "UP", "DOWN", "NEUTRAL", "WAIT"

# tradingview_ta interval constants keyed by our short codes.
TV_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h"}

# Which summary block each indicator group reads.
_GROUP_ATTR = {"recommendation": "summary", "ma": "moving_averages", "oscillator": "oscillators"}


def direction_of(label: str) -> str:
    """Map a TradingView rating label to a market direction."""
    if label in ("BUY", "STRONG_BUY"):
        return UP
    if label in ("SELL", "STRONG_SELL"):
        return DOWN
    return NEUTRAL


def fetch_analysis(tv_symbol: str, interval: str, timeout: int = 20):
    """Blocking tradingview_ta call for one symbol on one timeframe.

    Returns the Analysis object, or None on failure / no data.
    """
    try:
        res = get_multiple_analysis(
            screener="crypto", interval=TV_INTERVAL[interval],
            symbols=[tv_symbol], timeout=timeout,
        )
        return res.get(tv_symbol)
    except Exception:
        return None


def read_groups(analysis) -> dict:
    """Extract the three rating labels from an Analysis object."""
    if analysis is None:
        return {}
    return {
        "recommendation": analysis.summary.get("RECOMMENDATION"),
        "ma": analysis.moving_averages.get("RECOMMENDATION"),
        "oscillator": analysis.oscillators.get("RECOMMENDATION"),
    }


def combine(analyses_by_tf: dict, timeframes: list[str], indicators: list[str]) -> dict:
    """Build the combined signal from cached per-timeframe analyses.

    `analyses_by_tf` maps timeframe -> groups dict (from read_groups).
    Returns {signal, cells, ready} where cells is a list of per-(tf,group)
    detail for display.
    """
    cells, dirs, missing = [], [], False
    for tf in timeframes:
        groups = analyses_by_tf.get(tf) or {}
        for grp in indicators:
            label = groups.get(grp)
            d = direction_of(label) if label else None
            if d is None:
                missing = True
            else:
                dirs.append(d)
            cells.append({"tf": tf, "group": grp, "label": label, "direction": d})

    if missing or not dirs:
        signal = WAIT
    elif all(d == UP for d in dirs):
        signal = UP
    elif all(d == DOWN for d in dirs):
        signal = DOWN
    else:
        signal = WAIT

    return {"signal": signal, "cells": cells, "ready": not missing and bool(dirs)}

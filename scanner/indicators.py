import pandas as pd


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def adx(high, low, close, period=14):
    """Calculate Wilder-style ADX from high, low, and close series."""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    previous_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ], axis=1).max(axis=1)
    smoothing = {"alpha": 1 / period, "adjust": False, "min_periods": period}
    smoothed_tr = true_range.ewm(**smoothing).mean()
    plus_di = 100 * plus_dm.ewm(**smoothing).mean() / smoothed_tr
    minus_di = 100 * minus_dm.ewm(**smoothing).mean() / smoothed_tr
    denominator = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / denominator
    dx = dx.mask(denominator.eq(0), 0.0)
    return dx.ewm(**smoothing).mean()

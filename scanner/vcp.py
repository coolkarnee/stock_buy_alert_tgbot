import numpy as np
from scanner.indicators import atr
from config import settings

def detect_vcp(df):
    empty = {
        'original_vcp': False, 'optimized_vcp': False, 'true_vcp': False,
        'breakout': False, 'original_breakout': False, 'optimized_breakout': False,
        'contractions': 0, 'pivot': np.nan, 'tightness': np.nan,
        'volume_ratio': np.nan, 'atr_ratio': np.nan, 'quality_score': 0,
        'reasons': ['insufficient_data']
    }
    if len(df) < settings.VCP_BASE_LEN + settings.VCP_SEGMENT * 2 + 10:
        return empty

    x = df.copy()
    n = settings.VCP_SEGMENT

    # Original VCP path:
    # prior 3-day average volume is below the prior 20-day average volume.
    # Exclude the current/breakout bar so the setup is evaluated before entry.
    prior3 = x.volume.iloc[-4:-1].mean()
    prior20 = x.volume.iloc[-21:-1].mean()
    original_vcp = bool(np.isfinite(prior3) and np.isfinite(prior20) and prior20 > 0 and
                        prior3 < prior20 * settings.ORIGINAL_VCP_VOLUME_RATIO)

    # Optimized VCP path.
    x['range_pct'] = (x['high'] - x['low']) / x['close'] * 100
    x['atr14'] = atr(x, 14)

    s1 = x['range_pct'].iloc[-(n*2+1):-(n+1)].mean()
    s2 = x['range_pct'].iloc[-(n+1):-1].mean()
    s3 = x['range_pct'].iloc[-n:].mean()
    c1 = np.isfinite(s1) and np.isfinite(s2) and s2 < s1 * settings.VCP_RANGE_SHRINK
    c2 = np.isfinite(s2) and np.isfinite(s3) and s3 < s2 * settings.VCP_RANGE_SHRINK
    contractions = int(c1) + int(c2)

    base_atr = x['atr14'].iloc[-settings.VCP_BASE_LEN:].mean()
    recent_atr = x['atr14'].iloc[-n:].mean()
    ar = recent_atr / base_atr if base_atr > 0 else np.nan

    base_vol = x['volume'].iloc[-20:].mean()
    vr = x['volume'].iloc[-n:].mean() / base_vol if base_vol > 0 else np.nan

    fh = x['high'].iloc[-n:].max()
    fl = x['low'].iloc[-n:].min()
    fc = x['close'].iloc[-1]
    tight = (fh - fl) / fc * 100 if fc else np.nan

    bh = x['high'].iloc[-settings.VCP_BASE_LEN-1:-1].max()
    bl = x['low'].iloc[-settings.VCP_BASE_LEN-1:-1].min()
    w = bh - bl
    upper = w > 0 and (fc - bl) / w >= settings.VCP_UPPER_BASE_RATIO

    checks = [
        ('2_contractions', contractions >= 2),
        ('range_shrink', c1 and c2),
        ('atr_contracts', np.isfinite(ar) and ar < settings.VCP_ATR_RATIO),
        ('volume_dryup', np.isfinite(vr) and vr < settings.VCP_VOLUME_RATIO),
        ('tight_final', np.isfinite(tight) and tight <= settings.VCP_MAX_TIGHTNESS),
        ('upper_base', upper),
    ]
    quality_score = sum(bool(v) for _, v in checks)
    optimized_vcp = quality_score >= settings.VCP_MIN_QUALITY_SCORE
    reasons = [name for name, ok in checks if not ok]

    # Either VCP definition may independently provide an entry path.
    # The VCP itself must be accompanied by a breakout through the relevant pivot.
    original_pivot = max(float(x.high.iloc[-1]), float(x.close.iloc[-1]))
    original_breakout = original_vcp and fc >= original_pivot * (
        1 + settings.BREAKOUT_BUFFER_PCT / 100
    )
    optimized_breakout = optimized_vcp and fc >= bh * (
        1 + settings.BREAKOUT_BUFFER_PCT / 100
    )

    return {
        'original_vcp': original_vcp,
        'optimized_vcp': optimized_vcp,
        'true_vcp': bool(original_vcp or optimized_vcp),
        'breakout': bool(original_breakout or optimized_breakout),
        'original_breakout': bool(original_breakout),
        'optimized_breakout': bool(optimized_breakout),
        'contractions': contractions,
        'original_pivot': float(original_pivot),
        'pivot': float(bh),
        'tightness': float(tight),
        'volume_ratio': float(vr),
        'atr_ratio': float(ar),
        'quality_score': quality_score,
        'reasons': reasons,
    }

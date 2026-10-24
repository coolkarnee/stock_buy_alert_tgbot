"""TradingView India market breadth: scraping, text alerts, charts, and MA history."""

import html
import io
import re
from datetime import timedelta
from pathlib import Path

import pandas as pd
import requests

from scanner.alerts import send, send_photo

TRADINGVIEW_INDIA_URL = "https://www.tradingview.com/markets/india/#stocks"
BREADTH_HISTORY_FILE = Path("artifacts/market_breadth.csv")
BREADTH_HISTORY_DAYS = 61
BREADTH_TREND_SESSIONS = 7
BREADTH_FLAT_POINTS = 3
MA_LABELS = ("20-day MA", "50-day MA", "200-day MA")

# TradingView dark theme colors.
BG = "#131722"
PANEL = "#1E222D"
TRACK = "#2A2E39"
TEXT = "#D1D4DC"
MUTED = "#787B86"
GREEN = "#089981"
RED = "#F23645"
ORANGE = "#FF9800"
BLUE = "#2962FF"
MA_COLORS = {"20-day MA": "#2962FF", "50-day MA": "#FF9800", "200-day MA": "#AB47BC"}


def _clean(fragment):
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    text = re.sub(r"[\u202a\u202c]", "", text)
    text = text.replace("\u2212", "-").replace("\u202f", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _block(page, start_id, end_id=None):
    start = page.find(f'data-qa-id="{start_id}"')
    if start < 0:
        return ""
    end = page.find(f'data-qa-id="{end_id}"', start) if end_id else -1
    return page[start:end] if end > start else page[start:start + 20000]


def _number(text):
    match = re.search(r"(-?[\d.,]+)\s*([KMB]?)", text or "")
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    return value * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[match.group(2)]


def _compact(value):
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            return f"{value / size:.2f}".rstrip("0").rstrip(".") + suffix
    return f"{value:.0f}"


def parse_moving_averages(page):
    block = _block(page, "market-breadth-above-ma", "market-breadth-outperforming")
    values = dict.fromkeys(MA_LABELS)
    for days, value in re.findall(
        r'progress-row-label">(\d+)-day</span>.*?aria-valuenow="([\d.]+)"', block, flags=re.S
    ):
        label = f"{days}-day MA"
        if label in values:
            values[label] = float(value)
    return values


def parse_advance_decline(page):
    block = _block(page, "market-breadth-advance-decline", "market-breadth-highs-lows")
    sides = []
    for raw in re.findall(r'segmented-horizontal-bar-value">(.*?)</span>', block):
        match = re.search(r"([\d.,]+\s*[KMB]?)\s*·\s*([\d.]+)%", _clean(raw))
        if match:
            sides.append((_number(match.group(1)), float(match.group(2))))
    if len(sides) != 2:
        return None

    def stat(qa_id):
        match = re.search(rf'{qa_id}">(.*?)</div>', block, flags=re.S)
        return _clean(match.group(1)) if match else ""

    volume = [_number(part) for part in stat("market-breadth-volume-shares-value").split("/")]
    return {
        "advancing": sides[0][0],
        "advancing_pct": sides[0][1],
        "declining": sides[1][0],
        "declining_pct": sides[1][1],
        "net_advances": _number(stat("market-breadth-net-advances-value")),
        "ad_ratio": _number(stat("market-breadth-ad-ratio-value")),
        "up_volume": volume[0] if len(volume) == 2 else None,
        "down_volume": volume[1] if len(volume) == 2 else None,
    }


def advance_decline_read(ad):
    """Return the short breadth conclusion used by persisted market context."""
    if not ad or ad.get("ad_ratio") is None:
        return None
    ratio = ad["ad_ratio"]
    return "advancers lead" if ratio > 1 else "decliners lead" if ratio < 1 else "balanced"


def highs_lows_read(highs_lows):
    """Return the new-highs/lows conclusion for the longest available period."""
    if not highs_lows:
        return None
    period = list(highs_lows)[-1]
    row = highs_lows[period]
    if row["highs"] > row["lows"]:
        conclusion = "more new highs than lows"
    elif row["highs"] < row["lows"]:
        conclusion = "more new lows than highs"
    else:
        conclusion = "balanced"
    return f"Read ({period}): {conclusion}"


def parse_highs_lows(page):
    """Rebuild counts from bar heights; TradingView renders this chart without numeric labels."""
    block = _block(page, "market-breadth-highs-lows", "market-breadth-above-ma")
    periods = re.findall(r">\s*(\d+[DWMY])\s*</div>", block)
    bars = re.findall(
        r'height:max\(([\d.]+)%[^"]*?--inner-bar-color:#([0-9A-Fa-f]{6})', block
    )
    axis = [
        abs(int(text.replace(",", "")))
        for text in (_clean(raw) for raw in re.findall(r"<div[^>]*>([^<]*)</div>", block))
        if re.fullmatch(r"-?[\d,]+", text)
    ]
    if not periods or not bars or not axis or max(axis) == 0:
        return None
    scale = max(axis) / 50
    highs = [round(float(h) * scale) for h, color in bars if color.upper() == GREEN[1:]]
    lows = [round(float(h) * scale) for h, color in bars if color.upper() == RED[1:]]
    if not (len(periods) == len(highs) == len(lows)):
        return None
    return {period: {"highs": hi, "lows": lo} for period, hi, lo in zip(periods, highs, lows)}


def scrape_tradingview_breadth(url=TRADINGVIEW_INDIA_URL):
    """Return ({'ma', 'ad', 'highs_lows'}, error) from the TradingView India page."""
    data = {"ma": dict.fromkeys(MA_LABELS), "ad": None, "highs_lows": None}
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; market-monitor/1.0)"},
            timeout=20,
        )
        response.raise_for_status()
        page = response.text
    except Exception as exc:
        return data, str(exc)
    for key, parser in (("ma", parse_moving_averages), ("ad", parse_advance_decline),
                        ("highs_lows", parse_highs_lows)):
        try:
            data[key] = parser(page)
        except Exception as exc:
            print(f"TradingView breadth {key} parse failed: {exc}")
    return data, None


def update_breadth_history(values, today, path=BREADTH_HISTORY_FILE):
    """Upsert today's MA breadth row, keep about two months, and return rows oldest first."""
    columns = ["date", *values.keys()]
    try:
        history = pd.read_csv(path, dtype={"date": str})
    except (FileNotFoundError, pd.errors.EmptyDataError):
        history = pd.DataFrame(columns=columns)
    except Exception as exc:
        print(f"Breadth history unreadable; not saving today to avoid overwriting it: {exc}")
        return None
    if "date" not in history.columns:
        print("Breadth history has no date column; not saving today to avoid overwriting it.")
        return None
    history = history[history["date"] != today.isoformat()]
    if any(value is not None for value in values.values()):
        row = pd.DataFrame([{"date": today.isoformat(), **values}])
        history = row if history.empty else pd.concat([history, row], ignore_index=True)
    cutoff = (today - timedelta(days=BREADTH_HISTORY_DAYS)).isoformat()
    history = history[history["date"] >= cutoff].sort_values("date").reset_index(drop=True)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        history.reindex(columns=columns).to_csv(path, index=False)
    except OSError as exc:
        print(f"Breadth history save failed: {exc}")
    return history.reindex(columns=columns)


def breadth_judgement(values):
    lines = []
    ma200 = values.get("200-day MA")
    if ma200 is not None:
        lines.append("200-day MA: broader bull market" if ma200 > 50 else "200-day MA: broader bear market")
    for label in ("50-day MA", "200-day MA"):
        value = values.get(label)
        if value is not None and value <= 20:
            lines.append(f"{label}: highly oversold, bounce may come soon")
        elif value is not None and value >= 80:
            lines.append(f"{label}: highly overbought, correction may come soon")
    return lines


def breadth_trend(history, labels):
    recent = history.tail(BREADTH_TREND_SESSIONS)
    if recent.empty:
        return [f"Trend unavailable (0/{BREADTH_TREND_SESSIONS} trading sessions recorded)."]
    if len(recent) < 2:
        return [
            f"Trend unavailable ({len(recent)}/{BREADTH_TREND_SESSIONS} trading sessions recorded; "
            "need at least 2)."
        ]
    qualifier = "" if len(recent) == BREADTH_TREND_SESSIONS else "; provisional"
    lines = [
        f"Trend (last {len(recent)}/{BREADTH_TREND_SESSIONS} trading sessions{qualifier}):"
    ]
    for label in labels:
        series = pd.to_numeric(recent[label], errors="coerce").dropna()
        if len(series) < 2:
            lines.append(f"{label}: NA")
            continue
        change = series.iloc[-1] - series.iloc[0]
        if change >= BREADTH_FLAT_POINTS:
            direction = "going up"
        elif change <= -BREADTH_FLAT_POINTS:
            direction = "going down"
        else:
            direction = "flat"
        path = " > ".join(f"{value:.0f}" for value in series)
        lines.append(f"{label}: {path} ({change:+.0f} pts, {direction})")
    return lines


def fmt_breadth_alert(values, error=None, history=None):
    lines = [
        "📊 MARKET BREADTH - ABOVE MOVING AVERAGES",
        "Stocks trading above moving average:",
        *(f"{label}: {value:.0f}%" if value is not None else f"{label}: NA"
          for label, value in values.items()),
    ]
    judgement = breadth_judgement(values)
    if judgement:
        lines.extend(["", "Judgement:", *judgement])
    if history is not None:
        trend = breadth_trend(history, list(values))
        if trend:
            lines.extend(["", *trend])
    fetched = sum(value is not None for value in values.values())
    if fetched < len(values):
        lines.extend(["", f"Breadth data fetched: {fetched}/{len(values)}"])
    if error:
        lines.extend(["", f"TradingView breadth fetch error: {error}"])
    lines.append(f"Source: {TRADINGVIEW_INDIA_URL}")
    return "\n".join(lines)


def fmt_advance_decline_alert(ad, error=None):
    lines = ["📈 MARKET BREADTH - ADVANCING & DECLINING"]
    if not ad:
        lines.append("Data unavailable")
    else:
        lines.extend([
            f"Advancing: {_compact(ad['advancing'])} ({ad['advancing_pct']:.0f}%)",
            f"Declining: {_compact(ad['declining'])} ({ad['declining_pct']:.0f}%)",
        ])
        if ad["net_advances"] is not None:
            lines.append(f"Net advances: {ad['net_advances']:+.0f}")
        if ad["ad_ratio"] is not None:
            lines.append(f"A/D ratio: {ad['ad_ratio']:.2f}")
        if ad["up_volume"] is not None and ad["down_volume"] is not None:
            lines.append(f"Up / down volume: {_compact(ad['up_volume'])} / {_compact(ad['down_volume'])}")
        read = advance_decline_read(ad)
        if read:
            lines.extend(["", f"Read: {read}"])
    if error:
        lines.extend(["", f"TradingView breadth fetch error: {error}"])
    lines.append(f"Source: {TRADINGVIEW_INDIA_URL}")
    return "\n".join(lines)


def fmt_highs_lows_alert(highs_lows, error=None):
    lines = ["🆕 MARKET BREADTH - NEW HIGHS & LOWS"]
    if not highs_lows:
        lines.append("Data unavailable")
    else:
        for period, row in highs_lows.items():
            net = row["highs"] - row["lows"]
            lines.append(f"{period}: Highs {row['highs']} | Lows {row['lows']} | Net {net:+d}")
        read = highs_lows_read(highs_lows)
        lines.extend(["", read])
    if error:
        lines.extend(["", f"TradingView breadth fetch error: {error}"])
    lines.append(f"Source: {TRADINGVIEW_INDIA_URL}")
    return "\n".join(lines)


def _figure(height):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "text.color": TEXT,
        "axes.labelcolor": MUTED,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
    })
    fig = plt.figure(figsize=(8, height), dpi=150, facecolor=BG)
    return plt, fig


def _style_axis(ax):
    ax.set_facecolor(BG)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)


def _png(plt, fig):
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=BG, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return buffer.getvalue()


def _header(fig, title, subtitle, date_text):
    fig.text(0.04, 0.95, title, fontsize=16, fontweight="bold", va="top")
    fig.text(0.04, 0.885, subtitle, fontsize=9.5, color=MUTED, va="top")
    fig.text(0.96, 0.95, date_text, fontsize=9.5, color=MUTED, va="top", ha="right")


def render_advance_decline(ad, date_text):
    plt, fig = _figure(4.2)
    _header(fig, "Advancing and declining", "TradingView India stocks", date_text)

    ax = fig.add_axes([0.04, 0.50, 0.92, 0.12])
    _style_axis(ax)
    adv = ad["advancing_pct"]
    ax.barh(0, adv, color=GREEN, height=1)
    ax.barh(0, 100 - adv, left=adv, color=RED, height=1)
    ax.axvline(50, color=BG, linewidth=1.5, linestyle=":")
    ax.set_xlim(0, 100)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.text(0.04, 0.70, "Advancing", fontsize=11, color=GREEN, fontweight="bold")
    fig.text(0.96, 0.70, "Declining", fontsize=11, color=RED, fontweight="bold", ha="right")
    fig.text(0.04, 0.40, f"{_compact(ad['advancing'])}  ·  {adv:.0f}%", fontsize=12, va="top")
    fig.text(0.96, 0.40, f"{_compact(ad['declining'])}  ·  {ad['declining_pct']:.0f}%",
             fontsize=12, va="top", ha="right")

    stats = []
    if ad["net_advances"] is not None:
        net = ad["net_advances"]
        stats.append(("Net advances", f"{net:+.0f}", GREEN if net > 0 else RED if net < 0 else TEXT))
    if ad["ad_ratio"] is not None:
        ratio = ad["ad_ratio"]
        stats.append(("A/D ratio", f"{ratio:.2f}", GREEN if ratio > 1 else RED if ratio < 1 else TEXT))
    if ad["up_volume"] is not None and ad["down_volume"] is not None:
        stats.append(("Up / down volume",
                      f"{_compact(ad['up_volume'])} / {_compact(ad['down_volume'])}", TEXT))
    for index, (label, value, color) in enumerate(stats):
        from matplotlib.patches import Rectangle

        x = 0.04 + index * 0.31
        fig.add_artist(Rectangle((x, 0.04), 0.28, 0.2, transform=fig.transFigure,
                                 facecolor=PANEL, edgecolor="none"))
        fig.text(x + 0.02, 0.19, label, fontsize=9, color=MUTED, va="center")
        fig.text(x + 0.02, 0.09, value, fontsize=14, color=color, fontweight="bold", va="center")
    return _png(plt, fig)


def render_highs_lows(highs_lows, date_text):
    plt, fig = _figure(4.6)
    _header(fig, "New highs and lows", "Stocks at new highs / lows per lookback period", date_text)

    periods = list(highs_lows)
    highs = [highs_lows[p]["highs"] for p in periods]
    lows = [highs_lows[p]["lows"] for p in periods]
    nets = [h - l for h, l in zip(highs, lows)]
    limit = max(highs + lows + [1]) * 1.3

    ax = fig.add_axes([0.08, 0.10, 0.88, 0.70])
    _style_axis(ax)
    x = range(len(periods))
    ax.bar(x, highs, width=0.5, color=GREEN, label="New highs")
    ax.bar(x, [-v for v in lows], width=0.5, color=RED, label="New lows")
    ax.plot(x, nets, color=ORANGE, marker="D", markersize=6, linewidth=1.5, label="Net highs")
    ax.axhline(0, color=MUTED, linewidth=0.8)
    for i, (h, l, n) in enumerate(zip(highs, lows, nets)):
        ax.text(i, h + limit * 0.03, f"{h:,}", ha="center", va="bottom", fontsize=9.5, color=GREEN)
        ax.text(i, -l - limit * 0.03, f"{l:,}", ha="center", va="top", fontsize=9.5, color=RED)
        ax.text(i + 0.3, n, f"{n:+,}", ha="left", va="center", fontsize=8.5, color=ORANGE)
    ax.set_xticks(list(x))
    ax.set_xticklabels(periods, fontsize=11, color=TEXT)
    ax.set_ylim(-limit, limit)
    ax.grid(axis="y", color=TRACK, linewidth=0.6)
    ax.set_axisbelow(True)
    from matplotlib.ticker import FuncFormatter

    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{abs(v):,.0f}"))
    legend = ax.legend(loc="upper right", ncol=3, frameon=False, fontsize=9,
                       bbox_to_anchor=(1, 1.12))
    for text in legend.get_texts():
        text.set_color(TEXT)
    return _png(plt, fig)


def render_moving_averages(values, history, date_text):
    series = history.copy() if history is not None else pd.DataFrame()
    show_history = len(series) >= 2
    plt, fig = _figure(6.6 if show_history else 3.6)
    _header(fig, "Above moving averages", "% of stocks trading above their moving average", date_text)

    bars_top = 0.80 if not show_history else 0.86
    bars_height = 0.62 if not show_history else 0.30
    ax = fig.add_axes([0.16, bars_top - bars_height, 0.72, bars_height * 0.85])
    _style_axis(ax)
    labels = [label for label in MA_LABELS if label in values]
    for row, label in enumerate(reversed(labels)):
        value = values[label]
        ax.barh(row, 100, color=TRACK, height=0.45)
        if value is not None:
            color = RED if value <= 20 else GREEN if value >= 80 else MA_COLORS[label]
            ax.barh(row, value, color=color, height=0.45)
        ax.text(-2, row, label.replace(" MA", ""), ha="right", va="center", fontsize=11, color=TEXT)
        ax.text(102, row, f"{value:.0f}%" if value is not None else "NA",
                ha="left", va="center", fontsize=12, fontweight="bold", color=TEXT)
    for level, color in ((20, RED), (50, MUTED), (80, GREEN)):
        ax.axvline(level, color=color, linewidth=0.8, linestyle="--", alpha=0.7)
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.6, len(labels) - 0.4)
    ax.set_yticks([])
    ax.set_xticks([0, 20, 50, 80, 100])
    ax.set_xticklabels(["0", "20 oversold", "50", "80 overbought", "100"], fontsize=8)

    if show_history:
        hx = fig.add_axes([0.08, 0.08, 0.86, 0.38])
        _style_axis(hx)
        dates = pd.to_datetime(series["date"]).reset_index(drop=True)
        sessions = list(range(len(dates)))
        hx.axhspan(0, 20, color=RED, alpha=0.10)
        hx.axhspan(80, 100, color=GREEN, alpha=0.10)
        hx.axhline(50, color=MUTED, linewidth=0.8, linestyle="--")
        for label in labels:
            points = pd.to_numeric(series[label], errors="coerce").reset_index(drop=True)
            hx.plot(sessions, points, color=MA_COLORS[label], linewidth=2,
                    marker="o", markersize=3, label=label.replace(" MA", ""))
        hx.set_ylim(0, 100)
        hx.set_yticks([0, 20, 50, 80, 100])
        hx.grid(axis="y", color=TRACK, linewidth=0.6)
        hx.set_axisbelow(True)
        step = max(len(sessions) // 7, 1)
        ticks = sessions[::step]
        hx.set_xticks(ticks)
        hx.set_xticklabels([dates[i].strftime("%d %b") for i in ticks])
        hx.set_xlim(-0.5, len(sessions) - 0.5)
        hx.tick_params(axis="x", labelsize=8)
        hx.set_title(f"Last {len(series)} sessions", fontsize=10, color=MUTED, loc="left")
        legend = hx.legend(loc="upper right", ncol=3, frameon=False, fontsize=9,
                           bbox_to_anchor=(1, 1.15))
        for text in legend.get_texts():
            text.set_color(TEXT)
    return _png(plt, fig)


def _send_with_chart(text, render, *args):
    try:
        image = render(*args)
    except Exception as exc:
        print(f"Breadth chart render failed ({render.__name__}): {exc}")
        send(text)
        return
    send_photo(image, text, filename="breadth.png")


def send_breadth_alerts(today):
    """Send the three TradingView breadth alerts and return the MA percentages."""
    data, error = scrape_tradingview_breadth()
    date_text = today.strftime("%d %b %Y")

    ad_text = fmt_advance_decline_alert(data["ad"], error)
    if data["ad"]:
        _send_with_chart(ad_text, render_advance_decline, data["ad"], date_text)
    else:
        send(ad_text)

    hl_text = fmt_highs_lows_alert(data["highs_lows"], error)
    if data["highs_lows"]:
        _send_with_chart(hl_text, render_highs_lows, data["highs_lows"], date_text)
    else:
        send(hl_text)

    values = data["ma"]
    history = update_breadth_history(values, today)
    ma_text = fmt_breadth_alert(values, error, history)
    if any(value is not None for value in values.values()):
        _send_with_chart(ma_text, render_moving_averages, values, history, date_text)
    else:
        send(ma_text)
    # Keep the existing MA mapping intact for callers while exposing the
    # conclusion needed to backfill alerts emitted before the delayed scrape.
    ma_judgement = next(
        (line for line in breadth_judgement(values) if line.startswith("200-day MA:")),
        None,
    )
    return {
        **values,
        "advance_decline_read": advance_decline_read(data["ad"]),
        "highs_lows_read": highs_lows_read(data["highs_lows"]),
        "ma_judgement": ma_judgement,
    }

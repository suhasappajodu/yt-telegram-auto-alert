# report_generator.py
# Generates daily HTML report with embedded sparkline PNGs and pushes to gh-pages (workflow handles push)
# Requirements: requests, feedparser, yfinance, pandas, matplotlib

import os, json, requests, feedparser, re, base64
from io import BytesIO
from pathlib import Path
from datetime import datetime
import yfinance as yf
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Files (repo root)
CHANNELS_FILE = "channels.json"
YT_STATE_FILE = "yt_state.json"
PORTFOLIO_FILE = "portfolio.json"
TG_STATE_FILE = "tg_state.json"

HEADERS = {"User-Agent":"Mozilla/5.0"}

def load_json(p, default):
    path = Path(p)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except:
        return default

def save_file(path, content):
    Path(path).write_text(content, encoding="utf-8")

# YouTube: check feeds and return new videos (like main.py) but do not update yt_state here
def collect_new_videos(channels, yt_state):
    new = []
    for name, rss in channels.items():
        try:
            feed = feedparser.parse(rss)
        except:
            continue
        if not feed.entries:
            continue
        latest = feed.entries[0]
        vid = latest.get("yt_videoid") or latest.get("id") or latest.get("link")
        if yt_state.get(rss) != vid:
            new.append({
                "channel": name,
                "title": latest.get("title",""),
                "link": latest.get("link",""),
                "published": latest.get("published","")
            })
    return new

# Small helper to generate sparkline base64 for a series
def sparkline_base64(series, width=4, height=0.8):
    fig, ax = plt.subplots(figsize=(width, height), dpi=100)
    ax.plot(series, linewidth=1)
    ax.fill_between(range(len(series)), series, alpha=0.1)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    buf = BytesIO()
    fig.savefig(buf, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"

# Fetch latest price series for tickers using yfinance
def fetch_price_series(ticker, period="7d", interval="1h"):
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period=period, interval=interval)
        if hist.empty:
            return []
        return list(hist['Close'].dropna().astype(float).values)
    except Exception:
        return []

# Metals using Yahoo futures
def fetch_metals():
    names = {"Gold":"GC=F", "Silver":"SI=F"}
    out = {}
    for n, t in names.items():
        vals = fetch_price_series(t, period="2d", interval="1h")
        last = vals[-1] if vals else None
        out[n] = {"ticker": t, "series": vals, "last": last}
    return out

# Portfolio snapshot: returns last price and series
def portfolio_snapshot(portfolio):
    tickers = list(portfolio.keys())
    snap = {}
    for tk in tickers:
        last = None
        series = fetch_price_series(tk, period="7d", interval="1h")
        if series:
            last = series[-1]
        info = portfolio.get(tk,{})
        snap[tk] = {"name": info.get("name",tk), "qty": info.get("qty",0), "avg": info.get("avg",0), "last": last, "series": series}
    return snap

# Build HTML with tabs and embedded images
def build_html(date_str, new_videos, portfolio_snap, metals):
    html = []
    html.append("<!doctype html><html><head><meta charset='utf-8'><title>Daily Report</title>")
    html.append("<style>body{font-family:Arial;padding:10px} .tabs{display:flex;gap:8px;margin-bottom:10px}.tab{padding:8px 12px;background:#eee;border-radius:6px;cursor:pointer}.tab.active{background:#2b8aef;color:#fff}.panel{display:none}.panel.active{display:block} table{width:100%;border-collapse:collapse} th,td{border:1px solid #ddd;padding:8px;text-align:left}</style>")
    html.append("</head><body>")
    html.append(f"<h2>Daily Report — {date_str}</h2>")
    html.append("<div class='tabs'><div class='tab active' data-tab='yt'>YouTube</div><div class='tab' data-tab='inv'>Investments</div><div class='tab' data-tab='met'>Metals</div></div>")

    # YouTube panel
    html.append("<div id='yt' class='panel active'>")
    if new_videos:
        html.append("<h3>New Videos</h3><table><tr><th>Channel</th><th>Title</th><th>Published</th></tr>")
        for v in new_videos:
            html.append(f"<tr><td>{v['channel']}</td><td><a href='{v['link']}' target='_blank'>{v['title']}</a></td><td>{v.get('published','')}</td></tr>")
        html.append("</table>")
    else:
        html.append("<p>No new videos since last run.</p>")
    html.append("</div>")

    # Investments
    html.append("<div id='inv' class='panel'>")
    html.append("<h3>Portfolio</h3>")
    if portfolio_snap:
        html.append("<table><tr><th>Ticker</th><th>Name</th><th>Qty</th><th>Avg</th><th>Last</th><th>P/L</th><th>Trend</th></tr>")
        for tk,info in portfolio_snap.items():
            last = info.get("last")
            qty = info.get("qty",0)
            avg = info.get("avg",0) or 0
            pl = ""
            last_str = "N/A"
            if last is not None:
                pl = (last - avg) * qty
                last_str = f"{last:.2f}"
                pl = f"{pl:.2f}"
            trend_img = ""
            if info.get("series"):
                trend_img = f"<img src='{sparkline_base64(info['series'])}' alt='trend'/>"
            html.append(f"<tr><td>{tk}</td><td>{info.get('name')}</td><td>{qty}</td><td>{avg}</td><td>{last_str}</td><td>{pl}</td><td>{trend_img}</td></tr>")
        html.append("</table>")
    else:
        html.append("<p>No stocks in portfolio.</p>")
    html.append("</div>")

    # Metals
    html.append("<div id='met' class='panel'>")
    for mname,m in metals.items():
        last = m.get("last")
        series = m.get("series") or []
        img = f"<img src='{sparkline_base64(series)}'/>" if series else ""
        html.append(f"<h4>{mname} ({m.get('ticker')}) — Last: {last if last is not None else 'N/A'}</h4>{img}")
    html.append("</div>")

    html.append("<script>document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',function(){document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active')); document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active')); this.classList.add('active'); document.getElementById(this.getAttribute('data-tab')).classList.add('active');}));</script>")
    html.append("</body></html>")
    return "\n".join(html)

def main():
    channels = load_json_safe(CHANNELS_FILE := CHANNELS_FILE)
    yt_state = load_json_safe(YT_STATE_FILE := YT_STATE_FILE)
    portfolio = load_json_safe(PORTFOLIO_FILE := PORTFOLIO_FILE)

def load_json_safe(p):
    return load_json(p, {})

# Run sequence
def run():
    channels = load_json(CHANNELS_FILE, {})
    yt_state = load_json(YT_STATE_FILE, {})
    portfolio = load_json(PORTFOLIO_FILE, {})

    new_videos = collect_new_videos(channels, yt_state)
    metals = fetch_metals()
    port_snap = portfolio_snapshot(portfolio)

    # Build HTML
    date_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    html = build_html(date_str, new_videos, port_snap, metals)
    fname = f"report_{datetime.utcnow().strftime('%Y%m%d')}.html"
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    out_path = reports_dir / fname
    save_file(out_path, html)
    print("Wrote report:", out_path)
    return str(out_path), fname

if __name__ == "__main__":
    run()

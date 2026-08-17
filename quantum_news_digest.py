#!/usr/bin/env python3
"""
Weekly Quantum Computing Digest — Finance-First Edition
=========================================================
Leads with stock snapshots and analyst/investment news for
QUBT, IONQ, RGTI, QBTS, then company announcements and science
news underneath. Outputs a styled HTML file with clickable
source links.

Dependencies: feedparser, yfinance
    pip install feedparser yfinance
"""

import feedparser
import re
import io
import base64
import os
import requests
from datetime import datetime, timedelta, timezone
from html import unescape, escape

try:
    import yfinance as yf
    HAVE_YFINANCE = True
except ImportError:
    HAVE_YFINANCE = False

try:
    import matplotlib
    matplotlib.use("Agg")  # no display needed, just render to bytes
    import matplotlib.pyplot as plt
    HAVE_MATPLOTLIB = True
except ImportError:
    HAVE_MATPLOTLIB = False

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

LOOKBACK_DAYS = 7
TICKERS = ["QUBT", "IONQ", "RGTI", "QBTS"]

# Email delivery via Resend (resend.com) — free tier, no personal email
# account needed. Sender uses Resend's built-in generic address unless
# you verify your own domain with them later.
EMAIL_RECIPIENT = "rehaozyukseler@gmail.com"
EMAIL_SENDER = "Quantum Digest <onboarding@resend.dev>"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")

# Analyst target price / rating come from Finnhub's free tier (not
# Yahoo's .info, which increasingly gates this data behind rate limits
# that look like a premium wall). Free key: finnhub.io/register
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")

COMPANY_FEEDS = {
    "IonQ":              "https://ionq.com/feed",
    "Rigetti":           "https://www.rigetti.com/feed",
    "D-Wave":            "https://www.dwavesys.com/feed/",
    "Quantum Computing Inc.": "https://quantumcomputinginc.com/feed",
    "IBM Quantum":       "https://www.ibm.com/quantum/blog/feed",
    "Xanadu":            "https://www.xanadu.ai/blog/rss.xml",
}

SCIENCE_FEEDS = {
    "arXiv quant-ph":        "http://export.arxiv.org/rss/quant-ph",
    "Nature Quantum Info":   "https://www.nature.com/npjqi.rss",
    "Physical Review Quantum": "https://journals.aps.org/prxquantum/rss/recent.xml",
}

NEWS_QUERIES = ["quantum computing stock", "quantum computing analyst rating"]
GOOGLE_NEWS_TEMPLATE = "https://news.google.com/rss/search?q={query}+when:7d&hl=en-US&gl=US&ceid=US:en"

# Skip links to sites that paywall articles after a headline/teaser —
# no point surfacing a link you can't actually read for free.
PAYWALLED_DOMAINS = [
    "wsj.com", "barrons.com", "ft.com", "bloomberg.com",
    "seekingalpha.com", "morningstar.com", "investors.com",
    "economist.com", "businessinsider.com",
]


# ---------------------------------------------------------------------------
# FETCH HELPERS
# ---------------------------------------------------------------------------

def is_paywalled(link: str) -> bool:
    link = (link or "").lower()
    return any(domain in link for domain in PAYWALLED_DOMAINS)


def clean_text(s: str) -> str:
    s = unescape(s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def within_lookback(entry, cutoff) -> bool:
    for field in ("published_parsed", "updated_parsed"):
        t = entry.get(field)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc) >= cutoff
    return True


def fetch_feed(name, url, cutoff, limit=6):
    items = []
    try:
        parsed = feedparser.parse(url)
        for entry in parsed.entries[:limit]:
            if not within_lookback(entry, cutoff):
                continue
            link = entry.get("link", "")
            if is_paywalled(link):
                continue
            items.append({
                "source": name,
                "title": clean_text(entry.get("title", "")),
                "link": link,
                "summary": clean_text(entry.get("summary", ""))[:220],
            })
    except Exception:
        pass  # skip broken feeds silently rather than cluttering the digest
    return items


def fetch_ticker_news(ticker, cutoff, limit=5):
    items = []
    if not HAVE_YFINANCE:
        return items
    try:
        news = yf.Ticker(ticker).news or []
        for n in news[:limit]:
            content = n.get("content", n)
            title = content.get("title") or n.get("title", "")
            link = (content.get("canonicalUrl") or {}).get("url") or n.get("link", "")
            if title and link and not is_paywalled(link):
                items.append({"source": f"{ticker}", "title": clean_text(title), "link": link, "summary": ""})
    except Exception:
        pass
    return items


def make_sparkline_b64(closes):
    """Render a minimal price sparkline and return it as a base64 PNG string."""
    if not HAVE_MATPLOTLIB or closes is None or len(closes) < 2:
        return None
    try:
        up = closes[-1] >= closes[0]
        color = "#12813f" if up else "#c4341f"
        fig, ax = plt.subplots(figsize=(2.6, 0.7), dpi=120)
        ax.plot(closes, color=color, linewidth=1.8)
        ax.fill_between(range(len(closes)), closes, min(closes), color=color, alpha=0.08)
        ax.axis("off")
        fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", transparent=True)
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("ascii")
    except Exception:
        return None


def fetch_analyst_data(ticker):
    """Analyst price target and a simple rating label, via Finnhub's free tier."""
    result = {"target": None, "rating": None}
    if not FINNHUB_API_KEY:
        return result
    try:
        pt = requests.get(
            "https://finnhub.io/api/v1/stock/price-target",
            params={"symbol": ticker, "token": FINNHUB_API_KEY}, timeout=10
        ).json()
        if pt.get("targetMean"):
            result["target"] = pt["targetMean"]
    except Exception:
        pass
    try:
        rec = requests.get(
            "https://finnhub.io/api/v1/stock/recommendation",
            params={"symbol": ticker, "token": FINNHUB_API_KEY}, timeout=10
        ).json()
        if rec:
            latest = rec[0]  # most recent period, Finnhub returns newest first
            buy_side = latest.get("strongBuy", 0) + latest.get("buy", 0)
            sell_side = latest.get("strongSell", 0) + latest.get("sell", 0)
            hold = latest.get("hold", 0)
            if buy_side > hold and buy_side > sell_side:
                result["rating"] = "Buy"
            elif sell_side > hold and sell_side > buy_side:
                result["rating"] = "Sell"
            else:
                result["rating"] = "Hold"
    except Exception:
        pass
    return result


def fetch_ticker_snapshot(ticker):
    """Price, monthly change, volume, sparkline (via yfinance) plus
    analyst target/rating (via Finnhub) — best effort."""
    snap = {"ticker": ticker, "price": None, "change_pct": None, "volume": None,
            "target": None, "rating": None, "sparkline_b64": None}
    if HAVE_YFINANCE:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="1mo")  # extra history so the sparkline shows a real trend
            if not hist.empty:
                closes = hist["Close"].tolist()
                last_close = closes[-1]
                week_ago_close = hist["Close"].iloc[-6] if len(closes) >= 6 else closes[0]
                snap["price"] = round(float(last_close), 2)
                snap["change_pct"] = round((last_close - week_ago_close) / week_ago_close * 100, 2)
                snap["volume"] = int(hist["Volume"].iloc[-1])
                snap["sparkline_b64"] = make_sparkline_b64(closes)
        except Exception:
            pass

    analyst = fetch_analyst_data(ticker)
    snap["target"] = analyst["target"]
    snap["rating"] = analyst["rating"]
    return snap


# ---------------------------------------------------------------------------
# BUILD
# ---------------------------------------------------------------------------

def build_digest():
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    snapshots = [fetch_ticker_snapshot(t) for t in TICKERS]

    investment_news = []
    for t in TICKERS:
        investment_news += fetch_ticker_news(t, cutoff)
    for q in NEWS_QUERIES:
        url = GOOGLE_NEWS_TEMPLATE.format(query=q.replace(" ", "+"))
        investment_news += fetch_feed(q, url, cutoff, limit=6)

    company_news = []
    for name, url in COMPANY_FEEDS.items():
        company_news += fetch_feed(name, url, cutoff)

    science_news = []
    for name, url in SCIENCE_FEEDS.items():
        science_news += fetch_feed(name, url, cutoff, limit=4)

    def dedupe(items):
        seen, out = set(), []
        for it in items:
            key = it["title"].lower().strip()
            if key and key not in seen:
                seen.add(key)
                out.append(it)
        return out

    return {
        "snapshots": snapshots,
        "investment_news": dedupe(investment_news),
        "company_news": dedupe(company_news),
        "science_news": dedupe(science_news),
    }


# ---------------------------------------------------------------------------
# RENDER — styled HTML
# ---------------------------------------------------------------------------

def render_snapshot_card(s):
    ticker = s["ticker"]
    tradingview_url = f"https://www.tradingview.com/symbols/{ticker}/"
    investing_url = f"https://www.investing.com/search/?q={ticker}"
    yahoo_url = f"https://finance.yahoo.com/quote/{ticker}"
    links_html = f'''
      <div class="chart-links">
        <a href="{yahoo_url}" target="_blank" rel="noopener">Yahoo</a>
        <a href="{tradingview_url}" target="_blank" rel="noopener">TradingView</a>
        <a href="{investing_url}" target="_blank" rel="noopener">Investing.com</a>
      </div>'''

    if s["price"] is None:
        return f'<div class="ticker-card"><div class="ticker-symbol">{ticker}</div><div class="no-data">Price data unavailable</div>{links_html}</div>'

    change = s["change_pct"]
    change_class = "up" if (change or 0) >= 0 else "down"
    arrow = "▲" if (change or 0) >= 0 else "▼"
    target_line = f'<div class="target">Analyst target: ${s["target"]:.2f}</div>' if s["target"] else ""
    rating_line = f'<div class="rating">Rating: {escape(s["rating"].capitalize())}</div>' if s["rating"] else ""
    sparkline_html = (f'<img class="sparkline" src="data:image/png;base64,{s["sparkline_b64"]}" alt="{ticker} 1-month trend">'
                       if s["sparkline_b64"] else "")

    return f'''
    <div class="ticker-card">
      <div class="ticker-symbol">{ticker}</div>
      <div class="price">${s["price"]:.2f}</div>
      <div class="change {change_class}">{arrow} {abs(change):.2f}% (7d)</div>
      {sparkline_html}
      {target_line}
      {rating_line}
      {links_html}
    </div>'''


def render_news_item(it):
    summary_html = f'<div class="item-summary">{escape(it["summary"])}</div>' if it["summary"] else ""
    return f'''
    <div class="news-item">
      <span class="badge">{escape(it["source"])}</span>
      <a class="item-title" href="{escape(it["link"])}" target="_blank" rel="noopener">{escape(it["title"])}</a>
      {summary_html}
    </div>'''


def render_html(data):
    today = datetime.now().strftime("%B %d, %Y")

    ticker_cards = "".join(render_snapshot_card(s) for s in data["snapshots"])
    investment_items = "".join(render_news_item(it) for it in data["investment_news"]) or '<p class="empty">No new investment news this week.</p>'
    company_items = "".join(render_news_item(it) for it in data["company_news"]) or '<p class="empty">No new company announcements this week.</p>'
    science_items = "".join(render_news_item(it) for it in data["science_news"]) or '<p class="empty">No new publications this week.</p>'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Quantum Computing Weekly Digest — {today}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: #f5f6f8; margin: 0; padding: 24px; color: #1a1a1a; }}
  .container {{ max-width: 760px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .date {{ color: #666; font-size: 14px; margin-bottom: 24px; }}
  h2 {{ font-size: 16px; text-transform: uppercase; letter-spacing: 0.5px; color: #444; border-bottom: 2px solid #e0e0e0; padding-bottom: 6px; margin-top: 32px; }}
  .ticker-row {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }}
  .ticker-card {{ background: white; border-radius: 10px; padding: 14px 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); min-width: 140px; flex: 1; }}
  .ticker-symbol {{ font-weight: 700; font-size: 15px; color: #333; }}
  .price {{ font-size: 22px; font-weight: 700; margin-top: 2px; }}
  .change {{ font-size: 13px; font-weight: 600; margin-top: 2px; }}
  .change.up {{ color: #12813f; }}
  .change.down {{ color: #c4341f; }}
  .target, .rating {{ font-size: 12px; color: #777; margin-top: 4px; }}
  .no-data {{ color: #999; font-size: 13px; }}
  .sparkline {{ display: block; width: 100%; height: 26px; margin-top: 6px; }}
  .chart-links {{ display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }}
  .chart-links a {{ font-size: 11px; font-weight: 600; color: #4f6bd9; text-decoration: none; border: 1px solid #dbe1fa; padding: 3px 8px; border-radius: 6px; }}
  .chart-links a:hover {{ background: #edf0fd; }}
  .news-item {{ background: white; border-radius: 8px; padding: 12px 16px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.06); }}
  .badge {{ display: inline-block; font-size: 11px; font-weight: 600; color: #4f6bd9; background: #edf0fd; padding: 2px 8px; border-radius: 12px; margin-bottom: 4px; }}
  .item-title {{ display: block; font-size: 14.5px; font-weight: 600; color: #1a1a1a; text-decoration: none; line-height: 1.4; }}
  .item-title:hover {{ text-decoration: underline; color: #2952d9; }}
  .item-summary {{ font-size: 13px; color: #666; margin-top: 4px; line-height: 1.4; }}
  .empty {{ color: #999; font-size: 13px; font-style: italic; }}
</style>
</head>
<body>
<div class="container">
  <h1>🔬 Quantum Computing Weekly Digest</h1>
  <div class="date">{today} · covering the last {LOOKBACK_DAYS} days</div>

  <h2>Stock Snapshot</h2>
  <div class="ticker-row">{ticker_cards}</div>

  <h2>Investment &amp; Analyst News</h2>
  {investment_items}

  <h2>Company Announcements</h2>
  {company_items}

  <h2>Scientific Publications</h2>
  {science_items}
</div>
</body>
</html>'''


def send_email(html_body, subject):
    if not RESEND_API_KEY:
        print("Email not sent: RESEND_API_KEY not set in environment.")
        return False
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={
                "from": EMAIL_SENDER,
                "to": [EMAIL_RECIPIENT],
                "subject": subject,
                "html": html_body,
            },
            timeout=20,
        )
        if resp.status_code in (200, 201):
            print(f"Email sent to {EMAIL_RECIPIENT}")
            return True
        else:
            print(f"Email send failed: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"Email send failed: {e}")
        return False


if __name__ == "__main__":
    data = build_digest()
    html = render_html(data)
    filename = f"quantum_digest_{datetime.now().strftime('%Y%m%d')}.html"
    with open(filename, "w") as f:
        f.write(html)
    print(f"Digest written to {filename}")

    subject = f"Quantum Computing Weekly Digest — {datetime.now().strftime('%B %d, %Y')}"
    send_email(html, subject)

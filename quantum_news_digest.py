#!/usr/bin/env python3
"""
Weekly Quantum Computing News Digest
=====================================
Pulls recent items from company blogs, scientific journals, news
aggregators, and investment/financial sources, then compiles a
deduplicated markdown digest.

Run on a schedule (cron / GitHub Actions / Task Scheduler) — see
notes at the bottom of this file for setup.

Dependencies: feedparser, yfinance
    pip install feedparser yfinance
"""

import feedparser
import re
from datetime import datetime, timedelta, timezone
from html import unescape

try:
    import yfinance as yf
    HAVE_YFINANCE = True
except ImportError:
    HAVE_YFINANCE = False

# ---------------------------------------------------------------------------
# CONFIG — edit these lists to add/remove sources or tickers
# ---------------------------------------------------------------------------

LOOKBACK_DAYS = 7

# Company / lab blogs and press pages with RSS feeds
COMPANY_FEEDS = {
    "IonQ":              "https://ionq.com/feed",
    "Rigetti":           "https://www.rigetti.com/feed",
    "D-Wave":            "https://www.dwavesys.com/feed/",
    "Quantum Computing Inc.": "https://quantumcomputinginc.com/feed",
    "IBM Quantum":       "https://www.ibm.com/quantum/blog/feed",
    "Xanadu":            "https://www.xanadu.ai/blog/rss.xml",
}

# Scientific sources
SCIENCE_FEEDS = {
    "arXiv quant-ph":        "http://export.arxiv.org/rss/quant-ph",
    "Nature Quantum Info":   "https://www.nature.com/npjqi.rss",
    "Physical Review Quantum": "https://journals.aps.org/prxquantum/rss/recent.xml",
}

# General news aggregators — Google News RSS supports arbitrary queries
NEWS_QUERIES = [
    "quantum computing",
    "quantum computer stocks",
    "quantum error correction",
]
GOOGLE_NEWS_TEMPLATE = "https://news.google.com/rss/search?q={query}+when:7d&hl=en-US&gl=US&ceid=US:en"

# Investment-side: pull via yfinance's news API (not scraping analyst
# sites directly — most of those block scraping in their ToS)
TICKERS = ["QUBT", "IONQ", "RGTI", "QBTS"]


# ---------------------------------------------------------------------------

def clean_text(s: str) -> str:
    s = unescape(s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def within_lookback(entry, cutoff) -> bool:
    for field in ("published_parsed", "updated_parsed"):
        t = entry.get(field)
        if t:
            dt = datetime(*t[:6], tzinfo=timezone.utc)
            return dt >= cutoff
    return True  # if no date, include it and let a human judge


def fetch_feed(name, url, cutoff, limit=8):
    items = []
    try:
        parsed = feedparser.parse(url)
        for entry in parsed.entries[:limit]:
            if not within_lookback(entry, cutoff):
                continue
            items.append({
                "source": name,
                "title": clean_text(entry.get("title", "")),
                "link": entry.get("link", ""),
                "summary": clean_text(entry.get("summary", ""))[:280],
            })
    except Exception as e:
        items.append({"source": name, "title": f"[fetch error: {e}]", "link": url, "summary": ""})
    return items


def fetch_ticker_news(ticker, cutoff, limit=5):
    items = []
    if not HAVE_YFINANCE:
        return items
    try:
        news = yf.Ticker(ticker).news or []
        for n in news[:limit]:
            content = n.get("content", n)  # yfinance schema varies by version
            title = content.get("title") or n.get("title", "")
            link = (content.get("canonicalUrl") or {}).get("url") or n.get("link", "")
            items.append({
                "source": f"{ticker} (Yahoo Finance)",
                "title": clean_text(title),
                "link": link,
                "summary": "",
            })
    except Exception as e:
        items.append({"source": f"{ticker} (Yahoo Finance)", "title": f"[fetch error: {e}]", "link": "", "summary": ""})
    return items


def dedupe(items):
    seen, out = set(), []
    for it in items:
        key = it["title"].lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    return out


def build_digest():
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    sections = {"Company & Lab Announcements": [], "Scientific Publications": [],
                "General News": [], "Investment / Stock News": []}

    for name, url in COMPANY_FEEDS.items():
        sections["Company & Lab Announcements"] += fetch_feed(name, url, cutoff)

    for name, url in SCIENCE_FEEDS.items():
        sections["Scientific Publications"] += fetch_feed(name, url, cutoff)

    for q in NEWS_QUERIES:
        url = GOOGLE_NEWS_TEMPLATE.format(query=q.replace(" ", "+"))
        sections["General News"] += fetch_feed(q, url, cutoff, limit=6)

    for t in TICKERS:
        sections["Investment / Stock News"] += fetch_ticker_news(t, cutoff)

    for k in sections:
        sections[k] = dedupe(sections[k])

    return sections


def render_markdown(sections):
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# Quantum Computing Weekly Digest — {today}", ""]
    for section, items in sections.items():
        lines.append(f"## {section}")
        if not items:
            lines.append("_No new items this week._\n")
            continue
        for it in items:
            lines.append(f"- **[{it['source']}]** [{it['title']}]({it['link']})")
            if it["summary"]:
                lines.append(f"  {it['summary']}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    digest = render_markdown(build_digest())
    filename = f"quantum_digest_{datetime.now().strftime('%Y%m%d')}.md"
    with open(filename, "w") as f:
        f.write(digest)
    print(f"Digest written to {filename}")
    print(digest[:2000])

# ---------------------------------------------------------------------------
# SETUP NOTES
# ---------------------------------------------------------------------------
# 1. Some RSS URLs above (company blogs especially) change or get retired —
#    verify each feed resolves before relying on it; swap in the current
#    URL from the site's footer/RSS icon if one 404s.
# 2. Scheduling options:
#      - Local cron:        0 8 * * MON  python3 quantum_news_digest.py
#      - GitHub Actions:    schedule: cron: '0 8 * * 1'  (weekly, Monday 8am)
#      - Windows Task Scheduler: weekly trigger, run python.exe with this script
# 3. For paywalled/ToS-restricted sites (Seeking Alpha, TipRanks, Benzinga),
#    don't scrape directly — either use their official APIs if you have
#    access, or keep those as a manual weekly check.
# 4. To get notified rather than just a file: pipe the digest into an
#    email step (smtplib) or a Slack webhook POST at the end of __main__.

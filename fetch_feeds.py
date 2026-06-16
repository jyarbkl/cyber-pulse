#!/usr/bin/env python3
"""
fetch_feeds.py — Cyber Pulse feed fetcher
Reads feeds.json, fetches each feed, scrapes SANS blog,
and writes all articles to feeds-data.json.
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

FEEDS_CONFIG = "feeds.json"
OUTPUT_FILE  = "feeds-data.json"
MAX_ARTICLES = 50        # Max articles to keep per feed
REQUEST_TIMEOUT = 15     # Seconds before giving up on a feed

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CyberPulse/1.0; RSS reader)"
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_date(date_str):
    """Parse a date string into an ISO 8601 UTC string. Returns empty string on failure."""
    if not date_str:
        return ""
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    # Fallback: try common ISO formats
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str[:19], fmt[:len(date_str)])
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
    return ""


def clean_html(raw):
    """Strip HTML tags and return plain text, truncated to 300 chars."""
    if not raw:
        return ""
    text = BeautifulSoup(raw, "lxml").get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300] + ("…" if len(text) > 300 else "")


def fetch_rss_feed(feed_config):
    """Fetch and parse a standard RSS/Atom feed. Returns list of article dicts."""
    url    = feed_config["url"]
    source = feed_config["name"]
    articles = []

    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        parsed = feedparser.parse(response.content)

        for entry in parsed.entries[:MAX_ARTICLES]:
            title   = entry.get("title", "").strip()
            link    = entry.get("link", "").strip()
            summary = entry.get("summary", "") or entry.get("description", "")
            date    = parse_date(entry.get("published", "") or entry.get("updated", ""))

            if not title or not link:
                continue

            articles.append({
                "title":   title,
                "link":    link,
                "summary": clean_html(summary),
                "date":    date,
                "source":  source,
            })

    except Exception as e:
        print(f"  ERROR fetching {source} ({url}): {e}")

    return articles


def scrape_sans_blog():
    """
    Scrape the SANS Institute blog (https://www.sans.org/blog) since it
    does not publish an RSS feed. Extracts article cards from the listing page.
    Returns list of article dicts.
    """
    url    = "https://www.sans.org/blog"
    source = "SANS Blog"
    articles = []

    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "lxml")

        # SANS blog cards — find article elements or blog post links
        # The page uses a card-based layout; we look for article links with titles
        seen = set()

        # Strategy: find all links that look like blog post URLs
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]

            # SANS blog posts follow the pattern /blog/<slug>
            if not re.match(r"^/blog/[a-z0-9\-]+$", href):
                continue

            full_url = "https://www.sans.org" + href
            if full_url in seen:
                continue
            seen.add(full_url)

            # Get the title — prefer the link text, fall back to parent heading
            title = a_tag.get_text(separator=" ").strip()
            if not title or len(title) < 10:
                # Try parent element for a heading
                parent = a_tag.find_parent(["h2", "h3", "h4"])
                if parent:
                    title = parent.get_text(separator=" ").strip()

            if not title or len(title) < 10:
                continue

            # Try to find a summary near this link
            summary = ""
            card = a_tag.find_parent(["article", "div", "li"])
            if card:
                p_tags = card.find_all("p")
                for p in p_tags:
                    text = p.get_text(separator=" ").strip()
                    if len(text) > 40:
                        summary = text[:300] + ("…" if len(text) > 300 else "")
                        break

            # Try to find a date near this link
            date = ""
            if card:
                time_tag = card.find("time")
                if time_tag:
                    date = parse_date(
                        time_tag.get("datetime", "") or time_tag.get_text().strip()
                    )

            articles.append({
                "title":   title,
                "link":    full_url,
                "summary": summary,
                "date":    date,
                "source":  source,
            })

            if len(articles) >= MAX_ARTICLES:
                break

    except Exception as e:
        print(f"  ERROR scraping {source}: {e}")

    return articles


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Load feeds config
    if not os.path.exists(FEEDS_CONFIG):
        print(f"ERROR: {FEEDS_CONFIG} not found.")
        return

    with open(FEEDS_CONFIG, "r") as f:
        config = json.load(f)

    feeds      = config.get("feeds", [])
    all_data   = {}
    fetch_time = datetime.now(timezone.utc).isoformat()

    print(f"Fetching {len(feeds)} feeds at {fetch_time}\n")

    for feed in feeds:
        name = feed["name"]
        print(f"  Fetching: {name}")

        if feed.get("type") == "scrape":
            articles = scrape_sans_blog()
        else:
            articles = fetch_rss_feed(feed)

        all_data[name] = {
            "name":     name,
            "url":      feed.get("url", ""),
            "articles": articles,
        }

        print(f"    → {len(articles)} articles")
        time.sleep(1)   # Be polite — small delay between requests

    output = {
        "generated_at": fetch_time,
        "feeds":        all_data,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total = sum(len(v["articles"]) for v in all_data.values())
    print(f"\nDone. {total} total articles written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

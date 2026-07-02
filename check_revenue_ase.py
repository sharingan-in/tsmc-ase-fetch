"""
Checks the ASE Technology Holding monthly revenues page for a "June" entry.
If found (and we haven't already notified about it), posts a notification to
a Discord webhook and records that fact in state.json so we don't spam the
channel on every subsequent 10-minute run.

Designed to run via GitHub Actions on a schedule (see .github/workflows/check-revenue.yml).
The Discord webhook URL is read from the DISCORD_WEBHOOK_URL environment variable / secret —
never hardcode it in this file.
"""

import json
import os
import sys
import requests
from bs4 import BeautifulSoup

URL = "https://ir.aseglobal.com/html/ir_revenues.php"
HOMEPAGE_URL = "https://ir.aseglobal.com/html/index.php"
KEYWORD = "June"
STATE_FILE = "state.json"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def fetch_page(url: str) -> str:
    """
    Fetch the target page using a session that first visits the site's homepage.
    This picks up cookies (e.g. WAF/CDN challenge cookies) the way a real browser
    would, before requesting the actual page we care about.
    """
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    # Warm-up request: establishes cookies and looks like normal navigation.
    session.get(HOMEPAGE_URL, timeout=30)

    resp = session.get(url, timeout=30, headers={"Referer": HOMEPAGE_URL})
    resp.raise_for_status()
    return resp.text


def check_for_keyword(html: str, keyword: str) -> bool:
    """Look for the keyword specifically inside the revenues table's month column."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="revenues-table")
    if not table:
        # Fallback: search the whole page if the table structure changes
        return keyword.lower() in soup.get_text().lower()

    rows = table.find_all("tr")
    for row in rows:
        cells = row.find_all("td")
        if cells and keyword.lower() in cells[0].get_text(strip=True).lower():
            return True
    return False


def notify_discord(webhook_url: str, message: str) -> None:
    resp = requests.post(webhook_url, json={"content": message}, timeout=30)
    resp.raise_for_status()


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"notified": False}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("ERROR: DISCORD_WEBHOOK_URL environment variable not set.", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    html = fetch_page(URL)
    found = check_for_keyword(html, KEYWORD)

    if found and not state.get("notified"):
        message = f"✅ '{KEYWORD}' revenue entry detected on {URL}"
        notify_discord(webhook_url, message)
        state["notified"] = True
        save_state(state)
        print(f"Keyword '{KEYWORD}' found — Discord notified and state saved.")
    elif found and state.get("notified"):
        print(f"Keyword '{KEYWORD}' found but already notified previously. Skipping.")
    else:
        # Keyword not present. If it previously was (e.g. page reverted), reset state
        # so a future re-appearance triggers a fresh notification.
        if state.get("notified"):
            state["notified"] = False
            save_state(state)
            print(f"Keyword '{KEYWORD}' no longer present — state reset.")
        else:
            print(f"Keyword '{KEYWORD}' not found yet. No action taken.")


if __name__ == "__main__":
    main()

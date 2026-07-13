"""
Monitors multiple investor-relations pages for the appearance of July revenue data
and posts a notification to a Discord webhook when found.

Targets:
- ASE Technology Holding monthly revenues page: a "July" row appears in the
  table once published (previously blank rows simply don't exist yet).
- TSMC monthly revenue page: all month rows (Jan-Dec) are always listed;
  what changes is whether the Net Revenue figure next to "Jul." is filled in.

Designed to run via GitHub Actions on a schedule (see .github/workflows/check-revenue.yml).
The Discord webhook URL is read from the DISCORD_WEBHOOK_URL environment variable / secret —
never hardcode it in this file.

Dedupe: each target's notified state is tracked separately in state.json so we
only post once per target when its July data first appears, not on every run.
"""

import json
import os
import re
import sys
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

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

def fetch_page(url: str, homepage_url: str) -> str:
    """
    Fetch a page using a session that first visits the site's homepage to pick
    up cookies (e.g. WAF/CDN challenge cookies) the way a real browser would.
    Works for sites that gate on cookies/headers but don't require a JS challenge.
    """
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    session.get(homepage_url, timeout=30)
    resp = session.get(url, timeout=30, headers={"Referer": homepage_url})
    resp.raise_for_status()
    return resp.text

def fetch_page_browser(url: str, homepage_url: str) -> str:
    """
    Fetch a page using a real headless Chromium browser via Playwright.
    Needed for sites protected by a JS-executing bot-detection challenge
    (e.g. Cloudflare Bot Management), which plain HTTP requests can never
    satisfy since no JavaScript actually runs. This solves the challenge and
    picks up a fresh, valid cookie (e.g. __cf_bm) automatically on every run —
    no manual cookie capture or rotation needed.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=BROWSER_HEADERS["User-Agent"],
            locale="en-US",
        )
        page = context.new_page()
        # Warm-up navigation, same idea as the requests-based approach:
        # visiting the homepage first lets the challenge resolve naturally.
        page.goto(homepage_url, wait_until="networkidle", timeout=45000)
        page.goto(url, wait_until="networkidle", timeout=45000)
        content = page.content()
        browser.close()
        return content

def check_ase_july(html: str) -> bool:
    """ASE: a row for July simply appears in the table once published."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="revenues-table")
    if not table:
        return "july" in soup.get_text().lower()

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if cells and "july" in cells[0].get_text(strip=True).lower():
            return True
    return False

def check_tsmc_july(html: str) -> bool:
    """
    TSMC: the "Jul." row always exists (Jan-Dec are pre-listed). We look for
    that row and check whether its Net Revenue cell actually has a number in it.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            month_text = cells[0].get_text(strip=True).lower()
            if month_text.startswith("jul"):
                for cell in cells[1:]:
                    text = cell.get_text(strip=True)
                    if re.search(r"\d", text):
                        return True
                return False  # row found but still blank
    return False

TARGETS = [
    {
        "key": "ase",
        "label": "ASE Technology Holding",
        "url": "https://ir.aseglobal.com/html/ir_revenues.php",
        "homepage": "https://ir.aseglobal.com/html/index.php",
        "fetch_method": "requests",
        "check": check_ase_july,
    },
    {
        "key": "tsmc",
        "label": "TSMC",
        "url": "https://investor.tsmc.com/english/monthly-revenue/2026",
        "homepage": "https://investor.tsmc.com/english",
        "fetch_method": "browser",
        "check": check_tsmc_july,
    },
]

def notify_discord(webhook_url: str, message: str) -> None:
    resp = requests.post(webhook_url, json={"content": message}, timeout=30)
    resp.raise_for_status()

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("ERROR: DISCORD_WEBHOOK_URL environment variable not set.", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    state_changed = False

    for target in TARGETS:
        key = target["key"]
        target_state = state.get(key, {"notified": False})

        try:
            if target["fetch_method"] == "browser":
                html = fetch_page_browser(target["url"], target["homepage"])
            else:
                html = fetch_page(target["url"], target["homepage"])
            found = target["check"](html)
        except Exception as e:
            print(f"[{target['label']}] Fetch failed: {e}", file=sys.stderr)
            continue

        if found and not target_state.get("notified"):
            message = f"✅ July revenue data detected for {target['label']}: {target['url']}"
            notify_discord(webhook_url, message)
            target_state["notified"] = True
            state[key] = target_state
            state_changed = True
            print(f"[{target['label']}] July found — Discord notified and state saved.")
        elif found and target_state.get("notified"):
            print(f"[{target['label']}] July found but already notified previously. Skipping.")
        else:
            if target_state.get("notified"):
                target_state["notified"] = False
                state[key] = target_state
                state_changed = True
                print(f"[{target['label']}] July no longer present — state reset.")
            else:
                print(f"[{target['label']}] July not found yet. No action taken.")

    if state_changed:
        save_state(state)

if __name__ == "__main__":
    main()

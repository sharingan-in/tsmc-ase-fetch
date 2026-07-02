"""
Checks the ASE Technology Holding monthly revenues page for a "June" entry.
If found, posts a notification to a Discord webhook.

Designed to run via GitHub Actions on a schedule (see .github/workflows/check-revenue.yml).
The Discord webhook URL is read from the DISCORD_WEBHOOK_URL environment variable / secret —
never hardcode it in this file.
"""

import os
import sys
import requests
from bs4 import BeautifulSoup

URL = "https://ir.aseglobal.com/html/ir_revenues.php"
KEYWORD = "June"


def fetch_page(url: str) -> str:
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
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


def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("ERROR: DISCORD_WEBHOOK_URL environment variable not set.", file=sys.stderr)
        sys.exit(1)

    html = fetch_page(URL)
    found = check_for_keyword(html, KEYWORD)

    if found:
        message = f"✅ '{KEYWORD}' revenue entry detected on {URL}"
        notify_discord(webhook_url, message)
        print(f"Keyword '{KEYWORD}' found — Discord notified.")
    else:
        print(f"Keyword '{KEYWORD}' not found yet. No action taken.")


if __name__ == "__main__":
    main()

"""Test: extract sport_category + event_slug from bet modal DOM (no new tab).
Run with live Edge CDP on port 9222.
"""

import time, re, json, sys, os
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

CDP_PORT = 9222


class Tee:
    """Write to both console and a log file."""
    def __init__(self, filepath):
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        self.file = open(filepath, "w", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, data):
        self.stdout.write(data)
        self.file.write(data)
        self.file.flush()

    def flush(self):
        self.stdout.flush()
        self.file.flush()

    def close(self):
        self.file.close()
TARGET_URL = "https://stake.com/sports/live"


def connect_cdp():
    """Connect via CDP to existing Edge."""
    stealth = Stealth(
        chrome_runtime=True,
        navigator_webdriver=True,
        navigator_permissions=True,
        navigator_plugins=True,
    )
    pw = sync_playwright()
    managed = stealth.use_sync(pw)
    playwright = managed.__enter__()
    browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
    print(f"CDP connected, version: {browser.version}")
    contexts = browser.contexts
    context = contexts[0]
    pages = context.pages
    page = pages[-1] if pages else context.new_page()
    print(f"Using page: {page.url}")
    return pw, managed, playwright, browser, context, page


def find_first_bet_row(page):
    """Find the first bet feed table row and return its trigger info."""
    return page.evaluate("""() => {
        const rows = document.querySelectorAll('tr');
        for (let i = 0; i < rows.length; i++) {
            const tds = rows[i].querySelectorAll('td');
            if (tds.length < 5) continue;
            const texts = Array.from(tds).map(td =>
                (td.innerText || td.textContent || '').trim()
            ).filter(t => t.length > 0);
            if (texts.length < 5) continue;
            const firstTd = tds[0];
            const btn = firstTd.querySelector('button');
            if (btn) {
                return {
                    rowIndex: i, trigger: 'button',
                    btnText: (btn.textContent || '').trim().substring(0, 40),
                    cells: texts.slice(0, 5),
                };
            }
            const anchor = firstTd.querySelector('a');
            if (anchor) {
                return {
                    rowIndex: i, trigger: 'anchor',
                    anchorHref: anchor.getAttribute('href') || '',
                    cells: texts.slice(0, 5),
                };
            }
            return {rowIndex: i, trigger: 'td', cells: texts.slice(0, 5)};
        }
        return null;
    }""")


def click_row(page, row_info):
    """Click the trigger element of the found row."""
    idx = row_info['rowIndex']
    trigger = row_info['trigger']
    try:
        if trigger == 'button':
            page.locator("tr").nth(idx).locator("td").first.locator("button").first.click(timeout=5000)
        elif trigger == 'anchor':
            page.locator("tr").nth(idx).locator("td").first.locator("a").first.click(timeout=5000)
        else:
            page.locator("tr").nth(idx).locator("td").first.click(timeout=5000)
        return True
    except Exception as e:
        print(f"Click failed: {e}")
        return False


def wait_for_modal(page, timeout=20):
    """Wait for bet modal to appear (containing 'ID' text)."""
    for _ in range(timeout * 2):
        found = page.evaluate("""() => {
            const modals = document.querySelectorAll(
                '[class*="fixed"][class*="justify-center"]'
            );
            for (const m of modals) {
                if ((m.innerText || '').includes('ID')) return true;
            }
            return false;
        }""")
        if found:
            return True
        time.sleep(0.5)
    return False


def try_strategy_href(page):
    """Strategy 1: find <a> with /sports/ href in modal."""
    return page.evaluate("""() => {
        const modals = document.querySelectorAll(
            '[class*="fixed"][class*="justify-center"]'
        );
        for (const modal of modals) {
            const text = (modal.innerText || '').trim();
            if (!text.includes('ID')) continue;
            const anchors = modal.querySelectorAll('a[href]');
            const results = [];
            for (const a of anchors) {
                const href = a.getAttribute('href') || '';
                if (href.includes('/sports/')) {
                    results.push(href);
                }
            }
            return {found: results.length, urls: results};
        }
        return {found: 0, urls: []};
    }""")


def try_strategy_innerhtml(page):
    """Strategy 2: search modal outerHTML for /sports/ pattern."""
    return page.evaluate("""() => {
        const modals = document.querySelectorAll(
            '[class*="fixed"][class*="justify-center"]'
        );
        for (const modal of modals) {
            const text = (modal.innerText || '').trim();
            if (!text.includes('ID')) continue;
            const html = modal.outerHTML || '';
            const matches = [];
            const re = /\\/sports\\/([^\\/]+)\\/(?:[^\\/]+\\/)?([^\\/]+)\\//g;
            let m;
            while ((m = re.exec(html)) !== null) {
                matches.push({sport_category: m[1], event_slug: m[2]});
            }
            return {found: matches.length, matches: matches};
        }
        return {found: 0, matches: []};
    }""")


def try_strategy_svg(page):
    """Strategy 3: check SVG data-ds-icon for sport category."""
    return page.evaluate("""() => {
        const modals = document.querySelectorAll(
            '[class*="fixed"][class*="justify-center"]'
        );
        for (const modal of modals) {
            const text = (modal.innerText || '').trim();
            if (!text.includes('ID')) continue;
            const svgs = modal.querySelectorAll('svg[data-ds-icon]');
            const icons = [];
            svgs.forEach(s => icons.push(s.getAttribute('data-ds-icon')));
            return {found: icons.length, icons: icons};
        }
        return {found: 0, icons: []};
    }""")


def try_strategy_next_data(page):
    """Strategy 4: check __NEXT_DATA__ for sport/event routing info."""
    return page.evaluate("""() => {
        const keys = ['__NEXT_DATA__', '__NEXT_DATA_V2__', '__NUXT__', '__INITIAL_STATE__'];
        for (const key of keys) {
            if (window[key]) {
                const s = JSON.stringify(window[key]);
                if (s.includes('sport') || s.includes('category')) {
                    return {found: true, key: key, snippet: s.substring(0, 500)};
                }
            }
        }
        return {found: false};
    }""")


def try_strategy_all_links(page):
    """Strategy 5: enumerate ALL <a> hrefs in modal."""
    return page.evaluate("""() => {
        const modals = document.querySelectorAll(
            '[class*="fixed"][class*="justify-center"]'
        );
        for (const modal of modals) {
            const text = (modal.innerText || '').trim();
            if (!text.includes('ID')) continue;
            const anchors = modal.querySelectorAll('a');
            const hrefs = [];
            anchors.forEach(a => {
                const h = a.getAttribute('href');
                if (h) hrefs.push(h);
            });
            return {count: anchors.length, hrefs: hrefs};
        }
        return {count: 0, hrefs: []};
    }""")


def try_strategy_data_attrs(page):
    """Strategy 6: collect data-* attributes from modal elements."""
    return page.evaluate("""() => {
        const modals = document.querySelectorAll(
            '[class*="fixed"][class*="justify-center"]'
        );
        for (const modal of modals) {
            const text = (modal.innerText || '').trim();
            if (!text.includes('ID')) continue;
            const all = modal.querySelectorAll('*');
            const attrs = new Set();
            for (const el of all) {
                for (const attr of el.attributes) {
                    if (attr.name.startsWith('data-')) {
                        attrs.add(attr.name);
                    }
                }
            }
            return {found: attrs.size, attrs: Array.from(attrs).sort()};
        }
        return {found: 0, attrs: []};
    }""")


def get_modal_text(page):
    """Get the full innerText of the modal."""
    return page.evaluate("""() => {
        const modals = document.querySelectorAll(
            '[class*="fixed"][class*="justify-center"]'
        );
        for (const modal of modals) {
            const text = (modal.innerText || '').trim();
            if (text.includes('ID')) return text;
        }
        return '';
    }""")


def get_modal_html(page):
    """Get outerHTML of the modal (first 5000 chars)."""
    return page.evaluate("""() => {
        const modals = document.querySelectorAll(
            '[class*="fixed"][class*="justify-center"]'
        );
        for (const modal of modals) {
            const text = (modal.innerText || '').trim();
            if (text.includes('ID')) return modal.outerHTML.substring(0, 5000);
        }
        return '';
    }""")


def parse_event_url(url):
    """Parse sport_category and event_slug from event page URL."""
    if not url or '/sports/' not in url:
        return {}
    m = re.search(r'/sports/([^/]+)/(?:[^/]+/)?([^/]+)/', url)
    if m:
        return {"sport_category": m.group(1), "event_slug": m.group(2)}
    return {}


def main():
    log_path = f"log/test_modal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    tee = Tee(log_path)
    sys.stdout = tee
    try:
        pw = managed = playwright = browser = context = None
        pw, managed, playwright, browser, context, page = connect_cdp()

        # Navigate to bet feed if needed
        if "sports/live" not in page.url:
            page.goto(TARGET_URL, timeout=30000, wait_until="domcontentloaded")
            time.sleep(3)
            # Wait for bet feed table to render
            page.wait_for_selector('tr td', timeout=15000)
            print("Page ready, waiting for bet feed data...")
            time.sleep(5)

        # Find first bet row (retry up to 10 times)
        row = None
        for attempt in range(10):
            row = find_first_bet_row(page)
            if row:
                break
            print(f"Waiting for bet rows... ({attempt + 1}/10)")
            time.sleep(3)
        if not row:
            print("ERROR: No bet rows found after retries")
            return
        print(f"\nFirst row: idx={row['rowIndex']}, trigger={row['trigger']}")
        print(f"Cells: {row['cells']}")

        # Click to open modal
        if not click_row(page, row):
            return
        print("Clicked row, waiting for modal...")

        if not wait_for_modal(page):
            print("ERROR: Modal did not appear")
            return
        print("Modal appeared!")

        # Run all strategies
        strategies = [
            ("href a[href*='/sports/']", try_strategy_href),
            ("innerHTML /sports/ regex", try_strategy_innerhtml),
            ("SVG data-ds-icon", try_strategy_svg),
            ("__NEXT_DATA__ / __INITIAL_STATE__", try_strategy_next_data),
            ("ALL <a> hrefs enum", try_strategy_all_links),
            ("data-* attributes", try_strategy_data_attrs),
        ]

        print("\n=== Strategy Results ===")
        for name, func in strategies:
            result = func(page)
            print(f"\n[{name}]")
            print(json.dumps(result, indent=2, ensure_ascii=False))

        # Display modal info
        print("\n=== Modal innerText ===")
        print(get_modal_text(page))

        print("\n=== Modal outerHTML (first 5000) ===")
        print(get_modal_html(page))

    finally:
        try:
            if managed and playwright:
                managed.__exit__(None, None, None)
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass
        print("\nDone.")
        sys.stdout = tee.stdout
        tee.close()


if __name__ == "__main__":
    main()

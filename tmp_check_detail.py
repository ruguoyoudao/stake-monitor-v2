"""点击风云榜赛事名链接获取详情"""
import logging, time
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
import yaml
with open("config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

from scraper import StakeScraper
scraper = StakeScraper(config)
scraper.start()
time.sleep(3)

# 点击风云榜 + 等待
scraper.page.evaluate("""() => {
    const buttons = document.querySelectorAll('button');
    for (const btn of buttons) {
        if ((btn.textContent || '').trim() === '风云榜') {
            btn.scrollIntoView({block: 'center'});
            break;
        }
    }
}""")
scraper.page.click("button:has-text('风云榜')", timeout=3000)
print("Waiting 25s...")
time.sleep(25)

# 找第一列中可点击的链接
links = scraper.page.evaluate("""() => {
    const found = [];

    // 找风云榜表格行的第一列中的链接
    document.querySelectorAll('tr').forEach(tr => {
        const tds = tr.querySelectorAll('td');
        if (tds.length < 4) return;

        // 第一列找链接
        const first = tds[0];
        const anchors = first.querySelectorAll('a');
        const buttons = first.querySelectorAll('button');

        anchors.forEach(a => {
            found.push({
                type: 'a',
                text: a.textContent.trim(),
                href: a.href,
                class: a.className.toString().substring(0, 50)
            });
        });

        buttons.forEach(b => {
            found.push({
                type: 'button',
                text: b.textContent.trim(),
                class: b.className.toString().substring(0, 50)
            });
        });

        // 整个td可点击
        if (first.onclick || first.getAttribute('role') === 'button') {
            found.push({
                type: 'td',
                text: first.innerText.trim().substring(0, 60),
                clickable: true
            });
        }
    });

    return found.slice(0, 10);
}""")

print(f"\n=== Clickable elements in event column: {len(links)} ===")
for l in links:
    print(f"  [{l['type']}] text={l.get('text','')[:60]} href={l.get('href','')[:80]}")

# 尝试点击链接
if links:
    try:
        first_link = links[0]
        if first_link.get('href'):
            print(f"\n=== Clicking link: {first_link['href'][:80]} ===")
            scraper.page.click(f"a[href='{first_link['href']}']", timeout=3000)
        else:
            print("\n=== Clicking first td ===")
            scraper.page.evaluate("""() => {
                const trs = document.querySelectorAll('tr');
                for (const tr of trs) {
                    const tds = tr.querySelectorAll('td');
                    if (tds.length >= 4) {
                        tds[0].click();
                        return true;
                    }
                }
                return false;
            }""")
        time.sleep(5)
    except Exception as e:
        print(f"Click failed: {e}")

# 检查打开的新页面/面板
detail = scraper.page.evaluate("""() => {
    const info = {url: location.href, title: document.title, panels: []};

    // 找新出现的内容面板
    document.querySelectorAll('[class*="drawer"], [class*="sheet"], [class*="panel"], [class*="detail"], [class*="bet-slip"]').forEach(el => {
        const text = el.innerText.trim();
        if (text && text.length > 30) {
            info.panels.push({class: el.className.toString().substring(0, 60), text: text.substring(0, 500)});
        }
    });

    return info;
}""")

print(f"\nURL after click: {detail['url']}")
print(f"\nPanels: {len(detail['panels'])}")
for p in detail['panels']:
    print(f"  [{p['class'][:40]}]")
    for line in p['text'].split('\n')[:20]:
        if line.strip():
            print(f"    {line.strip()[:100]}")

scraper.screenshot("event_link_clicked.png")
scraper.stop()

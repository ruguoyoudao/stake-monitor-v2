# 代码审查: stake-monitor

按照 Sentry 工程实践规范审查。文件: `run_monitor.py`、`scraper.py`、`forex.py`、`notifier.py`、`config.yaml`。

---

## 安全问题（高优先级）

### 1. Webhook URL 明文硬编码在 config.yaml (`config.yaml:44`)
```yaml
webhook_url: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=b2832668-cf86-442c-92bf-f0dbc6adae51"
```
Webhook 密钥直接写在版本控制文件中。如果此仓库推送到公开平台，密钥将泄露。

**建议修复：**
- 使用环境变量：`webhook_url: ${WECOM_WEBHOOK_URL}`
- 或将敏感配置放入 `.env` 文件，并在 `.gitignore` 中排除
- 当前仓库应立即轮换已暴露的 Webhook 密钥

### 2. `requests.post` 无超时默认值兜底 (`notifier.py:49,126`)
`timeout=10` 已设置，但如果 `config` 中用户填入非标准 URL（如内网地址），所有投注数据（含玩家名、赛事、金额）会被 POST 到该地址，无域名白名单校验。

**建议修复：** 添加 URL 域名校验：
```python
ALLOWED_DOMAINS = ["qyapi.weixin.qq.com", "oapi.dingtalk.com"]
from urllib.parse import urlparse
def _validate_webhook(url):
    domain = urlparse(url).hostname
    return any(domain and domain.endswith(d) for d in ALLOWED_DOMAINS)
```

### 3. 全局 `seen_bets` 集合无持久化，重启后重发通知 (`run_monitor.py:40`)
`seen_bets = set()` 每次启动为空，所有已通知过的大额投注会在重启后重新发送一遍。

**建议修复：** 使用文件持久化（JSON 或 SQLite），或在启动时做首次静默轮询（首次不算 new_bets）：
```python
import json, os
SEEN_FILE = "seen_bets.json"
if os.path.exists(SEEN_FILE):
    seen_bets = set(json.load(open(SEEN_FILE, encoding="utf-8")))
# 每轮结束时保存
with open(SEEN_FILE, "w", encoding="utf-8") as f:
    json.dump(list(seen_bets), f)
```

---

## 运行时错误（中高优先级）

### 4. `_find_bet_row` 返回索引后 DOM 可能已变化 (`scraper.py:303-364`)
`_find_bet_row` 通过 `page.evaluate` 返回 `rowIndex`，然后 `_open_bet_detail` 再用 `self.page.locator("tr").nth(row_idx)` 定位行。两次 JS 调用之间 DOM 可能已因页面更新而变化，索引指向错误行。

**建议修复：** 在 `page.evaluate` 中直接返回行的唯一标识（如前4列文本拼接），或使用 Playwright 的 locator 链来做一次性定位，避免索引跨越两次 evaluate 调用。

### 5. 异常检测计数器只在 `==` 阈值时触发，永不重复告警 (`run_monitor.py:134,145,156`)
```python
if scrape_err_streak == ANOMALY_THRESHOLD:  # 仅在恰好 == 10 时触发
```
如果 streak 达到 10 后继续增长到 11、12...不会再发通知。更严重的是，如果因为网络波动某轮成功后就归零，下次又要累积到恰好10才会通知。

**建议修复：** 改为 `>=` 并增加去重：
```python
if scrape_err_streak >= ANOMALY_THRESHOLD and scrape_err_streak % ANOMALY_THRESHOLD == 0:
```

### 6. `_managed_pw.__exit__` 在 `stop()` 中手动调用 (`scraper.py:822-823`)
```python
if self._managed_pw:
    self._managed_pw.__exit__(None, None, None)
```
`self._managed_pw` 是 `Stealth.use_sync()` 返回的上下文管理器，在 `start()` 中通过 `__enter__` 启动。手动调用 `__exit__` 是可行的，但缺少对应的 `__enter__`/`__exit__` 配对保障。如果 `start()` 抛异常但已创建 `self._managed_pw`，`__exit__` 不会被调用。

**建议修复：** 使用 `try/finally` 或上下文管理器模式包裹整个生命周期：
```python
def __enter__(self):
    self.start()
    return self

def __exit__(self, *exc):
    self.stop()
```

### 7. CDP 模式下创建新页面而非复用已有页面 (`scraper.py:49`)
```python
self.page = self._context.new_page()
```
CDP 连接后，`contexts[0]` 是已有上下文，但 `new_page()` 创建新标签页而非导航已有页面。在某些情况下（如 Edge 已打开 stake.com），用户可能期望复用当前活跃页面。

**建议修复：** 在 CDP 模式下优先复用已有页面：
```python
if cdp_port > 0:
    pages = self._context.pages
    self.page = pages[0] if pages else self._context.new_page()
```

### 8. `headless` 和 `poll_interval` 被硬编码覆盖 (`run_monitor.py:21-22`)
```python
config["browser"]["headless"] = False
config["scraper"]["poll_interval"] = 30
```
这两个值直接覆盖了 `config.yaml` 中的用户配置，用户在 YAML 中修改 `headless: true` 或 `poll_interval: 10` 不会生效。

**建议修复：** 删除这两行硬编码覆盖，或在覆盖处添加注释说明原因。

---

## 性能问题（中优先级）

### 9. `_extract_bet_feed` 全表扫描无作用域限定 (`scraper.py:230-277`)
`document.querySelectorAll('tr')` 扫描全页所有 `<tr>` 元素。在体育直播页面上，可能包含排行榜、赔率表等大量无关行，每30秒一扫。

**建议修复：** 限定到风云榜容器内：
```javascript
const container = document.querySelector('[class*="leaderboard"]') || document;
const rows = container.querySelectorAll('tr');
```

### 10. `extract_details_for_bets` 逐条串行处理 (`scraper.py:656-678`)
每条大额投注需要：dismiss → find → click → wait modal → verify → get link → dismiss。5条投注 = 至少 5×(0.3+0.5+0.3+0.5+0.3) ≈ 9.5秒串行等待。在3分钟内有大量大额投注时会严重滞后。

**建议修复：** 目前难以并行（DOM 单线程），但可以优化等待时间：将轮询等待 `time.sleep(0.5)` 改为 Playwright 的 `page.wait_for_selector` 等智能等待，减少固定延迟。

### 11. 硬编码加密货币回退价格过期 (`forex.py:56-57`)
```python
_crypto_cache = {"BTC": 85000, "ETH": 1800}
```
BTC/ETH 价格波动巨大（BTC 8.5万 → 可能到10万或跌至6万），回退价格会产生严重偏差。

**建议修复：** 添加过期时间戳，超过24小时后标记为"数据可能不准确"并在通知中标注，或使用更长缓存时限但拒绝回退太久的数据。

---

## 设计评估（中优先级）

### 12. `SYMBOL_MAP` 中 `฿` 重复定义为 THB 和 BTC (`forex.py:18-19`)
```python
"₿": "BTC", "฿": "THB",  # 第18行
"฿": "THB",               # 第19行——与第18行重复
```
Python 字典中后面的键会覆盖前面的，所以这里没有 bug。但 `"฿"` (Baht 符号) 和 `"₿"` (Bitcoin 符号) 不应混淆，注释掉或删除重复行更清晰。

### 13. `parse_amount` 符号前缀匹配顺序脆弱 (`forex.py:116-124`)
Python 3.7+ 字典保持插入顺序，但 `SYMBOL_MAP` 中短符号如 `"C$"` (2字符) 可能被长字符串的相同前缀先匹配。例如 `"CA$1,445"` 会先匹配 `"C$"` → CAD 而非 `"CA$"` → CAD（结果相同但原理不严谨）。

更严重的是 `"kr"` 可能匹配到 `"kronor 500"` 这种文本。

**建议修复：** 按 key 长度降序排序后匹配：
```python
_sorted_symbols = sorted(SYMBOL_MAP.items(), key=lambda x: len(x[0]), reverse=True)
```

### 14. `to_cny` 仅支持 3 种币种，丢弃其他货币投注 (`forex.py:200-201`)
```python
if cur != "USD" and cur not in ("BTC", "ETH"):
    return 0
```
欧元、英镑、加元等在 `SYMBOL_MAP` 中有定义，`_fetch_rates()` 也返回了它们的汇率，但 `to_cny()` 直接返回0，导致这些币种的大额投注被静默跳过。

**建议修复：** 利用已获取的法币汇率做通用转换：
```python
if cur != "USD" and cur not in ("BTC", "ETH"):
    rate = rates.get(cur)
    if rate:
        return round(val * rate / rates.get("CNY", 6.84) * rates["CNY"], 2)  
        # 等价于: val * (rate / USD_to_CNY_rate)
    return 0
```
实际上更简单的公式：
```python
# val 以 cur 计价，rate 是 1 USD = rate cur
# val_cur_in_cny = val * (1/rate) * cny_per_usd
if cur != "USD" and cur not in ("BTC", "ETH"):
    rate = rates.get(cur)
    if rate:
        return round(val * rates["CNY"] / rate, 2)
    return 0
```

### 15. `_format_one_bet` 中 `odds_color` 逻辑与"整数x"后缀判定不一致 (`notifier.py:84-110`)
```python
odds_color = "comment"   # <1.2 灰
odds_color = "info"      # 1.2~1.4 蓝  
odds_color = "warning"   # >=1.4 红
```
当 `odds_raw` 为空字符串时，`float('' or '0')` → 0，会显示 `"0x"`。应额外处理 `odds_raw` 为空的情况。

**建议修复：**
```python
odds_display = f"{odds_raw}x" if odds_val > 0 else (odds_raw or "-")
```

### 16. 配置文件中 `headless: true` 被 Python 代码强制覆盖 (`run_monitor.py:21`)
```python
config["browser"]["headless"] = False
```
用户改为 `headless: true` 做无头部署也不会生效。这是一个"配置陷阱"。

### 17. `notifier.py` 中 `_format_wecom_md` 方法未被调用 (`notifier.py:112-117`)
`_format_wecom_md` 是完整版 Markdown 格式化方法，但实际发送路径使用 `_split_wecom_chunks` + `_format_one_bet`，此方法为死代码。

**建议修复：** 删除未使用的方法，或添加注释说明保留原因。

---

## 测试覆盖（低优先级）

无测试文件。核心逻辑（`parse_amount`、`to_cny`、`_split_wecom_chunks`）为纯函数，非常适合单元测试。

**建议修复：** 添加 `tests/test_forex.py` 和 `tests/test_notifier.py`：
```python
# tests/test_forex.py
def test_parse_amount_usd():
    assert parse_amount("$1,234") == (1234.0, "USD")

def test_parse_amount_btc():
    assert parse_amount("₿0.05") == (0.05, "BTC")

def test_to_cny_skips_unknown():
    assert to_cny("€500", "") == 0  # EUR unsupported → skip
```

---

## 总结

| 类别 | 严重程度 | 数量 |
|------|---------|------|
| 安全（密钥泄露、Webhook无校验、通知重放） | 高 | 3 |
| 运行时错误（DOM过期、告警遗漏、硬编码覆盖） | 中高 | 5 |
| 性能（全表扫描、串行处理、过期价格） | 中 | 3 |
| 设计（重复键、脆弱匹配、币种限制、死代码） | 中 | 6 |
| 测试覆盖 | 低 | 0 |

**最紧急的3项改动：**
1. **轮换并移除 config.yaml 中的 Webhook 密钥**，改用环境变量或 `.env` 文件
2. **初始化 `seen_bets` 时做静默轮询**，防止重启后重发通知
3. **修复 `to_cny` 支持更多币种**，利用已获取的法币汇率做通用转换，避免静默丢弃 EUR/GBP 等常见货币的大额投注
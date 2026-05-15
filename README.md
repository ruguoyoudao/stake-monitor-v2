# Stake.com 风云榜大额监控 + 跟注聚类预警

通过 CDP 连接 Edge 浏览器，实时监控 Stake.com 风云榜投注数据。大额下注本地存储，同赛事同玩法同结果的跟注聚集自动企业微信通知。

## 功能

- **CDP 连接**：连接已有 Edge 浏览器，复用登录态
- **风云榜监控**：提取赛事、玩家、时间、赔率、投注额
- **币种识别**：SVG 图标 + 文本解析双通道，仅监控 USDT / USDC / BTC / ETH
- **汇率转换**：USDT/USDC 按 USD 换算；BTC/ETH 对接 CoinGecko 实时价格
- **过滤规则**：赛事含"复式/多项"跳过；仅白名单项目类别记录；默认排除滚球盘(实时)；未知币种跳过
- **弹窗采集**：大额投注自动点击弹窗，获取分享链接 + 玩法(market) + 下注结果(outcome)
- **URL解析**：从弹窗 DOM 直接提取赛事页面 URL，解析 sport_category 和 event_slug
- **本地存储**：大额投注保存到 `large_bets.json`（含时间戳）
- **跟注聚类**：同一(event+market+outcome)累积 ≥3 条大额下注时，企业微信推送跟注预警
- **单笔大额通知**：单笔 ≥ 100,000 CNY 独立推送企业微信
- **异常告警**：连续5轮异常时刷新页面并企业微信通知
- **数据可视化**：Streamlit Web 交互式大额投注看板，支持筛选(币种/赔率/赛事搜索/时间范围) + 图表分析(金额分布/赔率vs金额/赛事热度/时间线/赛事玩法分析) + 聚类结果展示
- **弹窗批量采集**：`capture_bets.py` 可一次性采集所有风云榜投注的完整弹窗信息

## 安装

```bash
pip install -r requirements.txt
playwright install chromium
```

## 配置

编辑 `config.yaml`：

```yaml
target:
  url: "https://stake.com/sports/live"

browser:
  cdp_port: 9222

scraper:
  poll_interval: 30

notifications:
  rules:
    cny_threshold: 20000       # 大额记录阈值
    single_notify_threshold: 100000  # 单笔独立通知阈值
  wecom:
    enabled: true
    webhook_url: "${WECOM_WEBHOOK_URL}"

filters:
  sport_categories:
    - "counter-strike"
    - "dota-2"
    - "league-of-legends"
  exclude_live: true

clustering:
  enabled: true
  min_count: 3                 # 同(event+market+outcome)达N条通知
  step: 1                      # 之后每增加N条再通知
  window_hours: 24             # 仅统计最近N小时内，0=不限
```

## 使用

```powershell
# 方式一：批处理一键启动（推荐）
start_monitor.bat

# 方式二：手动启动
# 1. 以调试模式启动 Edge
taskkill /F /IM msedge.exe
start msedge --remote-debugging-port=9222 https://stake.com/sports/live
# 2. 在浏览器中登录 stake.com
# 3. 启动监控
python run_monitor.py
```

## 输出

### 大额投注 (`large_bets.json`)
```json
[
  {
    "event": "埃弗顿 - 曼城",
    "player": "Jesusitoln",
    "time": "上午2:02",
    "odds": "1.49",
    "amount": "USDT 10,033",
    "amount_cny": 68626,
    "market": "胜平负",
    "outcome": "曼城",
    "share_link": "...",
    "sport_category": "soccer",
    "event_slug": "premier-league",
    "is_live": false,
    "saved_at": "2026-05-05T02:02:00"
  }
]
```

### 跟注聚类预警（企业微信）

```
## 跟注预警 - 埃弗顿 - 曼城

> 项目: soccer
> 赛事: 埃弗顿 - 曼城
> 玩法: 胜平负
> 结果: 曼城
> 累积: 3 条大额下注
> 玩家: caulvi247, DonnetteKok0, Jesusitoln
> 赔率: 1.49x
> 总金额 CNY: 183,055
```

### 数据可视化

```powershell
streamlit run visualize.py
# 交互式看板
```

### 弹窗批量采集

```powershell
python capture_bets.py
# 依次打开风云榜每条投注弹窗，保存完整文本/HTML 到 captured_bets.json
# 支持断点续采（capture_progress.json）
```

## 项目结构

| 文件 | 说明 |
|------|------|
| `run_monitor.py` | 主监控入口（轮询 + 大额过滤 + 弹窗采集 + 本地存储 + 聚类检测） |
| `capture_bets.py` | 批量采集：一次打开所有投注弹窗，保存完整信息 |
| `scraper.py` | 浏览器控制 + DOM 数据提取 + 弹窗采集 + 弹窗 DOM URL提取 |
| `forex.py` | 币种解析 + 汇率转换 |
| `notifier.py` | 企业微信/钉钉通知（聚类预警专用格式） |
| `config.yaml` | 配置文件 |
| `docs.md` | 模块详细文档 |
| `NOTIFY_RULES.md` | 企业微信通知条件与格式 |
| `test_modal_sport.py` | 弹窗 DOM 提取测试脚本 |
| `large_bets.json` | 大额投注记录（累积追加） |
| `seen_bets.json` | 已处理投注 key（去重） |
| `visualize.py` | Streamlit 数据可视化看板 |
| `cluster_alerts.json` | 聚类通知记录（{key: last_notified_count}） |
| `captured_bets.json` | 批量采集结果（含弹窗完整文本） |

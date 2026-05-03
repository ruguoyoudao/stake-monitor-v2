# Stake.com 风云榜大额监控（>= ¥500,000）

通过 CDP 连接 Edge 浏览器，实时监控 Stake.com 风云榜投注数据，大额下注自动通过企业微信推送通知（含一键分享链接）。

## 功能

- **CDP 连接**：连接已有 Edge 浏览器，复用登录态
- **风云榜监控**：提取赛事、玩家、时间、赔率、投注额（`event|player|time|amount` 去重）
- **币种识别**：SVG 图标 + 文本解析双通道，仅监控 USDT / USDC / BTC / ETH 四种币种
- **汇率转换**：USDT/USDC 按 USD 直接换算；BTC/ETH 对接 CoinGecko 实时价格，3 次重试 + 硬编码回退
- **通知过滤**：赛事名含"复式"自动跳过；非 USDT/USDC/BTC/ETH 币种自动跳过；仅以 CNY >= ¥500,000 为准触发通知
- **分享链接提取**：大额投注自动弹出详情弹窗，拦截剪贴板获取 `modal=bet` 分享链接
- **大额通知**：投注额 >= ¥500,000 时通过企业微信 Markdown 推送（字段加粗、赔率三色显示 `<1.2`灰/`1.2~1.4`蓝/`>=1.4`红、金额红色、BTC/ETH 保留 2 位小数、含分享链接）
- **分条发送**：单条消息超过 4096 字节自动拆为 `(1/N)` 多条

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
  cdp_port: 9222                # CDP 调试端口

scraper:
  poll_interval: 30             # 轮询间隔(秒)

notifications:
  rules:
    cny_threshold: 500000       # 大额阈值(人民币)
  wecom:
    enabled: true
    webhook_url: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
  dingtalk:
    enabled: false
    webhook_url: "https://oapi.dingtalk.com/robot/send?access_token=xxx"
```

## 使用

```powershell
# 方式一：批处理一键启动（推荐）
start_monitor.bat

# 方式二：手动启动
# 1. 以调试模式启动 Edge 浏览器
start msedge --remote-debugging-port=9222 https://stake.com/sports/live

# 2. 在浏览器中登录 stake.com

# 3. 启动监控
python run_monitor.py
```

`start_monitor.bat` 自动完成：关闭旧 Edge → 启动 CDP 调试 Edge → 运行监控。

监控数据实时输出到 `monitor.log`：

```
[20:21:09] #1 bets=40(new=40)
  --- 风云榜 ---
  [下午8:20] 隐身 | 1.05x | 10203.79 (CNY69,794) >>>     # >>> 表示大额
  [下午8:18] Kackiii | 3.04x | USDT 2,346 (CNY16,045) >>>  # >>> 表示大额
      skip(复式): 复式投注 (2)                              # 复式过滤
  [下午9:16] Eufel | 1.27x | BTC 0.03 (CNY16,064) >>>      # BTC 实时价格转换
  [下午9:16] 隐身 | 1.90x | ETH 0.51 (CNY7,979)             # ETH 实时价格转换
  ...正在提取分享链接...
  [INFO] 获取分享链接: /sports/home?iid=sport%3A573772418&modal=bet
   [INFO] 企业微信通知发送成功: 大额下注通知 (1/2)
   [INFO] 企业微信通知发送成功: 大额下注通知 (2/2)
  >>> 已发送 20 条大额通知
```

## 通知格式

企业微信收到的大额通知示例：

> **赛事**: LPL 2026 — Weibo Gaming vs Top Esports
> **玩家**: 隐身
> **时间**: 下午6:18
> **赔率**: <font color="warning">2.10x</font>
> **金额**: <font color="warning">USDT 75,000</font>
> **CNY**: <font color="warning">513,000</font>
> **分享**: https://stake.com/sports/home?iid=sport%3A573697820&modal=bet

## 项目结构

| 文件 | 说明 |
|------|------|
| `run_monitor.py` | 主监控入口（去重 + 复式过滤 + 分享链接提取 + 通知推送） |
| `scraper.py` | 浏览器控制 + 风云榜数据提取 + SVG 币种识别 + 剪贴板拦截 |
| `forex.py` | 币种解析 + 汇率转换（USDT→USD + CoinGecko BTC/ETH 实时价格） |
| `notifier.py` | 企微/钉钉 Webhook 通知（Markdown 格式化 + 赔率三色 + 4096 分条） |
| `config.yaml` | 配置文件 |
| `docs.md` | 模块详细文档（库引用、接口说明、函数详解） |
| `tmp_check_detail.py` | 调试脚本：探索风云榜行按钮和弹窗结构 |

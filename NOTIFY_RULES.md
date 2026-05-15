# 企业微信通知规则

---

## 通知条件

### ① 跟注聚类预警 `notifier.send_cluster_alert()`

| 项 | 配置 | 说明 |
|------|------|------|
| 配置路径 | `clustering` | config.yaml |
| 启用开关 | `enabled: true` | |
| 触发条数 | `min_count: 3` | 同一(event+market+outcome)累积 N 条 |
| 递增步长 | `step: 1` | 之后每增加 step 条再通知 |
| 时间窗口 | `window_hours: 24` | 仅统计最近 N 小时内，0=不限 |
| 触发条件 | 24h 内 ≥3 条同事件同玩法同结果大额投注 | |
| 后续通知 | 每增 1 条再次通知 | |

**推送格式：**

```
## 跟注预警 - Team A vs Team B

> 项目: dota-2
> 赛事: Team A vs Team B
> 玩法: 获胜
> 结果: Team A
> 累积: 3 条大额下注
> 玩家: player1, player2, player3
> 赔率: 1.95x
> 总金额 CNY: 150,000
```

---

### ② 单笔大额通知 `notifier.send()`

| 项 | 配置 | 说明 |
|------|------|------|
| 配置路径 | `notifications.rules` | config.yaml |
| 阈值 | `single_notify_threshold: 100000` | CNY |
| 触发条件 | 单笔 `amount_cny >= 100,000` | |

**推送格式：**

```
## 大额下注警告 - 22:33:46

> 项目: league-of-legends
> 赛事: LGD Gaming - LNG Esports
> 玩法: 获胜
> 结果: LNG Esports
> 玩家: Wika1997
> 赔率: 1.50x
> 金额: USDT 4,000
> CNY: 102,300
> 分享: https://stake.com/...
```

---

## 过滤规则（两种通知均遵循）

| 优先级 | 条件 | 动作 | 配置 |
|--------|------|------|------|
| 1 | `event` 含 "复式" 或 "多项" | skip | 代码硬编码 |
| 2 | 未知币种（非 USDT/USDC/BTC/ETH） | skip | 代码硬编码 |
| 3 | `amount_cny < 20,000` | 不记录大额 | `notifications.rules.cny_threshold` |
| 4 | `sport_category` 不在白名单 | skip | `filters.sport_categories` |
| 5 | `is_live == True`（滚球盘） | skip | `filters.exclude_live: true` |

---

## 补充：异常系统告警（非投注相关）

| 异常类型 | 阈值 | 行为 |
|----------|------|------|
| 数据提取失败 | 连续 5 轮 | 刷新网页 → 企业微信通知 |
| 无投注数据 | 连续 5 轮 | 刷新网页 → 企业微信通知 |
| Feed 停滞 | 连续 5 轮 | 刷新网页 → 企业微信通知 |

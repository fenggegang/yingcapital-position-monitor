# 币安八交易员公开操作记录监控

本项目每 2 分钟在同一个 GitHub Actions 工作流中读取已启用交易员的网页“最新操作记录”。交易员名单只保存在 [`traders.json`](traders.json)：

| 交易员 | 项目 ID |
|---|---|
| 熬鹰资本 | 5075281354358777856 |
| 请填写昵称 | 5296057151807357697 |
| 万箭穿心BTC | 5247756265911980544 |
| Sky株式会社 | 4532994172262753536 |
| 发烧好了呢 | 5271501457743196416 |
| 勤劳的小蜜蜂_ | 5307033607493882624 |
| Luke2019 | 5293752834070546944 |
| 先及格 | 5279472349303626752 |

## 以后只需修改 traders.json

在 GitHub 仓库打开 `traders.json`，点击铅笔图标编辑并提交即可。Cloudflare 下一次触发同一个工作流时会自动读取新名单，不需要修改 Python、GitHub Actions、Worker 或 Secrets。

每项只有三个公开字段：

```json
{
  "nickname": "显示昵称",
  "portfolio_id": "币安项目 ID",
  "enabled": true
}
```

- **添加**：在数组末尾逗号分隔地追加一项。项目 ID 必须放在英文双引号中，新项目首次成功读取只建立基准。
- **暂停**：把该项的 `"enabled": true` 改成 `"enabled": false`。
- **恢复**：把 `false` 改回 `true`。程序继续使用原项目 ID 的历史状态和去重记录。
- **修改昵称**：只改 `nickname`。昵称是 微信提醒中的显示名称，项目 ID 与状态文件不变。
- **删除**：从数组删除整项。工作流仍保留该项目的历史状态文件；以后用相同项目 ID 重新添加时可继续原状态。

`enabled` 必须使用不带引号的小写 `true` 或 `false`；项目 ID 不得重复。配置格式、字段类型或重复 ID 有误时，程序会停止并标记工作流失败，不会产生交易提醒。配置文件不得放 Token、密码或其他敏感信息。

识别规则：

- BUY + LONG：开多
- SELL + SHORT：开空
- SELL + LONG：平多
- BUY + SHORT：平空

`positionSide=BOTH` 表示单向持仓模式。公开操作记录没有 `reduceOnly`、`OPEN/CLOSE` 或成交前后仓位字段；`totalPnl` 也不能可靠区分开仓和平仓。为与币安网页一致，BUY 显示“做多（开平待确认）”，SELL 显示“做空（开平待确认）”，绝不把做多/做空猜成开仓。

程序没有下单、撤单、账户或资金接口，不需要币安 API Key，不调用 OpenAI API、ChatGPT、Codex 或任何付费模型。

## 独立状态与通知保证

当前八位交易员分别使用以下状态文件：

    .runtime/5075281354358777856.json
    .runtime/5296057151807357697.json
    .runtime/5247756265911980544.json
    .runtime/4532994172262753536.json
    .runtime/5271501457743196416.json
    .runtime/5307033607493882624.json
    .runtime/5293752834070546944.json
    .runtime/5279472349303626752.json

每份文件分别保存基准、最新操作时间、最新操作指纹、币安接口游标、去重指纹和待发送事件，互不覆盖。

1. 缺少某位交易员的状态文件时，第一次成功读取只建立该交易员的基准，不通知历史记录。
2. 升级前熬鹰资本的 state.json 会迁移到其项目 ID 文件，保留原基准和去重信息。
3. 新事件先写入对应状态文件再发送；微信临时失败时只重试该交易员的待发事件。
4. 一个接口失败不会阻止其他交易员继续检查，但工作流最终会标记失败以便发现问题。
5. positionShow 不控制操作记录监控。仓位隐藏时，只要独立操作记录接口有效，仍正常处理。
6. data=null、HTTP 错误或格式异常时保留该交易员原状态，不产生虚假提醒。
7. 暂停或从名单删除只停止检查，不删除 `monitor-state` 分支中的历史状态。

微信通知包含交易员昵称、交易对、开多/开空/平多/平空；单向持仓则显示币安网页的“做多/做空”方向并注明开平仓未公开。通知还包含成交价格、成交数量、名义价值、操作时间、订单类型和项目 ID。

“操作时间”使用币安同一条操作记录的 `orderUpdateTime`（成交/最新操作时间），与网页显示一致；仅当该字段缺失或无效时才回退到 `orderTime`（原始下单时间）。时间戳按 UTC 解析后通过 IANA `America/New_York` 时区转换，自动处理夏令时。

## 已验证接口

八个项目均通过以下币安网页公开只读接口验证：

    POST /bapi/futures/v1/friendly/future/copy-trade/lead-portfolio/order-history

2026-11-15 至 2026-11-16 实测：

- 熬鹰资本：接口有效；positionShow=false 不影响操作记录。
- 请填写昵称：SIGNAL 项目，接口有效，读取到真实 UNITREE、SAMSUNG、SPCX 操作。
- 万箭穿心BTC：SIGNAL 项目，接口有效，读取到真实 BTCUSDT 操作。
- Sky株式会社：PUBLIC 项目，positionShow=false，但接口有效，读取到真实 TSLA、BTC、NVDA 操作。
- 发烧好了呢：SIGNAL 项目，接口有效，读取到真实 SOXS、AMD、MUU、MU 操作。
- 勤劳的小蜜蜂_：PUBLIC 项目，接口有效，读取到真实 ZECUSDT 操作。
- Luke2019：SIGNAL 项目，接口有效，读取到真实 HYPE、ETH、AAVE 操作；使用 `BOTH` 单向持仓模式。
- 先及格：SIGNAL 项目，positionShow=false，但操作记录接口有效，读取到真实 ZECUSDT 操作。

只验证接口而不发消息：

    PYTHONPATH=src python -m yingcapital_monitor --check-api

运行测试：

    python -m unittest discover -s tests -v

## GitHub Actions

只保留 `.github/workflows/monitor.yml` 一个工作流，由现有 Cloudflare Worker Cron Trigger 每 2 分钟通过 `workflow_dispatch` 触发；工作流仍可手动运行。

监控程序通过 HTTPS 调用腾讯 iLink，交易员去重状态保存在 `monitor-state` 分支。线上工作流按网页保存的订阅名单发送。

### 网页管理微信订阅

管理入口为 <https://yingcapital-subscriptions.gang24518.workers.dev>。管理员使用本机保管的管理密钥登录。“绑定微信”会生成二维码；使用收提醒的微信扫码确认，并把网页给出的绑定短语发给机器人。新账号初始暂停，可勾选“接收提醒”，选择全部或部分交易员后保存。重新绑定同一个账号会更新会话并保留偏好。

静态文件与绑定 API 由独立的 Cloudflare Worker 提供，会话和设置持久保存在其 Durable Object 中。GitHub Actions 每轮使用独立的 `SUBSCRIPTIONS_KEY` 读取配置，继续从 GitHub 托管出口查询币安。新事件按当时的收件人名单排队，持久化各自的发送状态；新增订阅不会回放旧队列，已暂停或取消订阅的收件人会从待重试事件中移除。仓库状态分支只保存不透明收件人标识，不保存账号凭据。

### 单账号本地运行

以下步骤用于独立运行的单账号模式。已启用网页管理的工作流通过管理页绑定与刷新微信。


1. 在你自己的电脑上，从本仓库目录运行以下命令。使用自己的微信扫码，然后在该机器人私聊里发送终端给出的绑定短语。以下命令使用 uv 临时加载二维码显示库，在终端直接显示二维码。也可用安装了 `qrencode` 的环境执行 `PYTHONPATH=src python3 -m yingcapital_monitor.bind_weixin`。

   ```sh
   PYTHONPATH=src uv run --no-project --with qrcode python -m yingcapital_monitor.bind_weixin
   ```

2. 命令将账号和会话保存在 `~/.config/yingcapital-monitor/weixin.json`（仅当前用户可读写）。在**你自己运行该工作流的仓库**配置同名 Secret，替换下面的 `OWNER/REPO`。文件通过标准输入提交，不会打印令牌。

   ```sh
   gh secret set WEIXIN_SESSION --repo OWNER/REPO < "$HOME/.config/yingcapital-monitor/weixin.json"
   ```

3. 在 Actions 手动运行监控工作流，勾选 `test_notification`，确认微信收到 TESTUSDT 测试消息。也可以只在本机测试：

   ```sh
   WEIXIN_SESSION="$(cat "$HOME/.config/yingcapital-monitor/weixin.json")" PYTHONPATH=src python3 -m yingcapital_monitor --test-notification
   ```

微信可能在长时间没有互动后拒绝主动通知。重跑步骤 1 会复用已有登录，并从你的新消息更新会话令牌；随后重做步骤 2 即可让 Actions 使用新会话，下一次正常监控会重试待发消息。如果账号登录本身已失效，将该私密文件移至安全的备份位置后重新绑定。切换时旧 Telegram 已送达记录会跳过，未送达的由微信接续。不要把凭据放入 `traders.json`、`.runtime`、仓库或运行日志。

协议依据见[腾讯 iLink 中文协议文档](https://github.com/Tencent/openclaw-weixin/blob/ffc7ae1686e84da944579af37f4fbef522183688/docs/protocol_zh_CN.md)。

公开仓库使用标准 ubuntu-slim GitHub-hosted runner。Cloudflare 只负责触发同一个 GitHub Actions 工作流；监控程序仍通过独立去重状态补抓最新记录窗口内的交易。

程序按顺序对每位启用交易员调用项目详情和操作记录两个公开接口。遇到 429 或 418 时暂停全部交易员查询，跨轮次遵循 Retry-After；未给出有效等待时间时分别冷却 15 分钟或 1 小时。临时 5xx 错误采用退避重试；工作流超时为 5 分钟。交易时间使用同一操作行的 `orderUpdateTime`，按 `America/New_York` 转换并自动处理夏令时。

本项目不会启用收费服务，也不会进行任何真实交易。

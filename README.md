# 链析 · 币圈行情分析师 Agent

一个**可直接运行**的行情分析师 Agent：规则引擎把公开数据加工成可解释的"事实"，大模型只负责撰写"叙事"。核心设计原则是**计算与叙事分离**——指标、评分、支撑阻力全部由代码算出，LLM 不参与算术，因此不会出现数字幻觉。

## 一、角色设计

| 项目 | 定义 |
| --- | --- |
| 名称 | 链析（ChainLens） |
| 定位 | 专注加密货币市场的行情分析师，服务需要做交易决策的个人投资者 |
| 分析框架 | 日线定方向 → 4h 定节奏 → 1h 找入场；量价验证；持仓拥挤度；链上流动性；宏观风险偏好 |
| 输出规范 | 先结论后依据；每条依据必须带具体数值；必须给关键支撑/阻力/改变观点的触发条件 |
| 边界 | 不喊单、不承诺收益、不给杠杆倍数建议、数据缺失必须显式声明 |

## 二、能力矩阵

| 维度 | 具体数据 | 数据源 |
| --- | --- | --- |
| 技术面 | 1h/4h/1d K线、EMA20/50/200、RSI、MACD、ATR、布林带位置、量比、分形结构、摆动支撑阻力 | Gate.io / Binance 现货（可切换） |
| 持仓面 | 资金费率及年化、90日分位、持仓量及1h/24h变化、账户多空比、大户多空比、爆仓量 | Gate.io / Binance 永续（可切换） |
| 链上流动性 | 稳定币总供应及 1d/7d/30d 变化、BTC 算力/出块/内存池/手续费/难度调整、ETH gas/base fee/区块利用率 | DefiLlama、blockchain.info、mempool.space、Ethereum 公共 RPC |
| 情绪面 | 恐慌贪婪指数（当前值、1d/7d 变化、30日均值）+ 加密快讯（英文 RSS + 金色财经中文资讯/快讯） | alternative.me、Cointelegraph/Decrypt RSS、jinse.com.cn |
| 宏观联动 | 美元指数、纳斯达克100、标普500 的点位与 1d/5d 涨跌 | 东方财富公开行情 |

交互与推送通道全部实现：

- **定时简报** `brief`：生成 Markdown 简报并落盘，结论速览自动带「较上次 / 较昨日」评分与状态变化；
- **对话问答** `ask`：LLM 自主调用 7 个数据工具后作答；`ask -i` 进入多轮对话，上下文保留最近 3 轮；
- **仪表盘** `dashboard`：把落盘快照渲染成静态 HTML（评分走势 + 五维拆解），本地直接打开；
- **异动告警** `watch`：阈值触发 + 冷却去重 + 5 分钟聚合窗口与分级升级 + Webhook 推送，覆盖 24h 异动 / RSI 超买卖 / 支撑阻力测试与突破 / 资金费率拥挤与极端分位 / 持仓量异动 / 量能放大 / 评分状态切换 / 恐慌贪婪极端值 / 稳定币流出；
- **本地 API** `api`：`/health`、`/snapshot`、`/dashboard`、`/brief`、`/ask` 五个接口，供本地页面或脚本调用；
- **飞书双向** `feishu-hook`：自建应用订阅消息事件，群内 @机器人 直接提问并回复；
- **飞书推送** `push-test` / `brief --push`：支持飞书自定义机器人 text 与 interactive 卡片消息，可选 HmacSHA256 加签。

## 三、系统架构

```
采集层 providers/           计算层                    叙事层                输出层
market.py    ─┐
onchain.py   ─┼─> indicators.py ─> analysis.py ─┬─> report.py  ──> Markdown 简报
sentiment.py ─┤     纯 Python 指标    五维评分/状态  │
macro.py     ─┘                                   ├─> alerts.py  ──> 终端/Webhook
                                                  └─> agent.py   ──> 问答（Function Calling）
                                                        ↑
                                                   tools.py（7 个工具）
```

关键设计：

1. **事实层可独立运行**：没有 LLM Key 也能输出完整简报（`--no-llm`）；
2. **数据源全部免 Key**：按国内网络可达性选型，已逐一实测；
3. **缓存与降级**：HTTP 层带磁盘缓存 + 重试；单一数据源失败只记入"数据缺口"，不中断整体流程；
4. **可解释评分**：每一分都有明确规则来源，便于回测和调参。

目录结构：

```
币分析助手/
├─ main.py                  # 入口
├─ requirements.txt
├─ crypto_analyst/
│  ├─ config.py             # 环境变量与权重配置
│  ├─ net.py                # HTTP + 缓存 + 重试
│  ├─ indicators.py         # 纯 Python 技术指标
│  ├─ analysis.py           # 快照与五维评分
│  ├─ history.py            # 评分快照落盘（回测数据源，带 source 标记）
│  ├─ backtest.py           # 非重叠窗口回测（综合分 vs 未来收益）
│  ├─ dashboard.py          # 静态 HTML 仪表盘（内联 SVG，无外部依赖）
│  ├─ tools.py              # Agent 工具层（支持 ask --symbols 作用域）
│  ├─ agent.py              # LLM 工具调用循环
│  ├─ prompts.py            # 人格与分析规范
│  ├─ report.py             # Markdown 简报
│  ├─ alerts.py             # 告警引擎 + 飞书推送
│  ├─ briefing.py           # 简报链路（CLI / 调度共用）
│  ├─ scheduler.py          # 内置调度（watch + 每日简报）
│  ├─ api.py                # 本地 HTTP API
│  ├─ feishu_bot.py         # 飞书双向机器人（事件回调）
│  ├─ llm.py                # OpenAI 兼容客户端
│  ├─ cli.py                # 命令行子命令
│  └─ providers/            # 四类数据源
│     └─ exchanges/         # 交易所适配：gate.py / binance.py，按 EXCHANGE 切换
├─ scripts/                 # 服务守护脚本（watch + 定时简报）
├─ tests/                   # pytest 单测（指标 / 回测 / 评分 / 告警 / 简报 / Agent / 仪表盘）
└─ reports/                 # 简报、告警与运行日志（自动创建）
```

## 四、评分模型

综合分 = 技术面 40% + 持仓面 20% + 流动性 15% + 情绪 15% + 宏观 10%。

| 维度 | 打分逻辑（0-100，50 为中性） |
| --- | --- |
| 技术面 | 按 1h/4h/1d 权重（0.2/0.35/0.45）累加：均线排列 ±18、MACD 柱方向与动能 ±9、结构 ±5、RSI 分区 ±8、放量确认 ±4 |
| 持仓面 | 基准 50；资金费率年化 >80% 扣 25、>30% 扣 15、>10% 扣 5、0~10% 加 5（依次对应多头拥挤程度）、负费率加 10（空头拥挤）；持仓量与价格同向加 8，增仓下跌扣 8；账户多空比 ≥2.2 扣 8；大户持仓偏空扣 5 |
| 流动性 | 基准 50；稳定币 7d 变化 ×8（±20 封顶），30d 变化 ×3（±10 封顶） |
| 情绪 | 恐慌贪婪指数原值；≥85 额外扣 10（追高风险），≤15 加 8（反向机会） |
| 宏观 | 基准 50；美元指数 5d 反向 ×6（±15），纳指 5d ×2（±15），标普 ×1.5（±8） |

状态映射：≥75 强势多头 / 62-75 偏多 / 45-62 震荡 / 32-45 偏空 / <32 弱势空头。

情绪与资金费率的极端阈值统一来自 `config.THRESHOLDS`（`fng_extreme_greed=85`、`fng_extreme_fear=15`、`rsi_overbought=78` 等），评分、flags、告警共用同一份配置，调参只改一处。

## 五、Agent 工具清单

| 工具 | 用途 |
| --- | --- |
| `get_market_overview` | 全部标的价格、评分、状态、异动 + 市场情绪/宏观/链上概览 |
| `get_symbol_analysis` | 单币种三周期指标 + 持仓结构 + 五维评分 |
| `get_klines` | 指定周期K线形态摘要与最近 20 根收盘价 |
| `get_derivatives` | 资金费率、分位、持仓量变化、多空比、爆仓 |
| `get_liquidity` | 稳定币供应变化 + BTC 网络活动 |
| `get_sentiment_news` | 恐慌贪婪指数 + 8 条快讯 |
| `get_macro` | 美元指数 / 纳指 / 标普的 1d、5d 涨跌 |

Agent 采用原生 Function Calling 循环（最多 6 步），工具异常会以 `{"error": ...}` 回传给模型，不会中断对话。

## 六、快速开始

```powershell
cd 币分析助手
uv venv .venv
uv pip install -r requirements.txt
copy .env.example .env    # 按需填写 LLM_API_KEY
```

```powershell
# 1) 结构化快照（调试用，不调用 LLM）
.venv\Scripts\python.exe main.py snapshot --symbols BTC

# 2) 生成简报（默认写 reports/）
.venv\Scripts\python.exe main.py brief --stdout

# 2.0) 临时切换到 Binance 数据源
.venv\Scripts\python.exe main.py brief --exchange binance

# 2.1) 生成简报并推送到飞书（需先配置 WEBHOOK_URL）
.venv\Scripts\python.exe main.py brief --push

# 3) 对话问答（需 LLM Key）
.venv\Scripts\python.exe main.py ask "BTC 现在能追多吗？关键价位在哪"
.venv\Scripts\python.exe main.py ask "ETH 和 SOL 哪个更强" --symbols ETH,SOL
.venv\Scripts\python.exe main.py ask -i                       # 多轮对话：/reset 清空上下文，/exit 退出

# 4) 异动监控（先跑一轮验证）
.venv\Scripts\python.exe main.py watch --once
.venv\Scripts\python.exe main.py watch --interval 300

# 4.1) 内置调度：异动监控 + 每日定时简报推送（替代 Windows 计划任务）
.venv\Scripts\python.exe main.py serve --once              # 跑一轮验证
.venv\Scripts\python.exe main.py serve --interval 300      # 常驻

# 5) 推送链路自检
.venv\Scripts\python.exe main.py push-test          # 纯文本
.venv\Scripts\python.exe main.py push-test --card   # 飞书卡片

# 6) 评分回测（离线，用历史落盘快照检验"综合分 vs 未来收益"）
.venv\Scripts\python.exe main.py backtest                    # 默认 24h 窗口、只统计 watch 来源
.venv\Scripts\python.exe main.py backtest --hours 4,24,72    # 多窗口对比
.venv\Scripts\python.exe main.py backtest --sweep            # 附加权重敏感性扫描
.venv\Scripts\python.exe main.py backtest --recommend        # 按各维度 IC 给出推荐权重（需 ≥30 个窗口）
.venv\Scripts\python.exe main.py backtest --recommend --apply   # 推荐权重直接写入 config.py（自动备份为 config.py.bak）
.venv\Scripts\python.exe main.py backtest --strategy           # 附加策略回测：评分阈值做多/做空，输出净值、最大回撤、夏普比率
.venv\Scripts\python.exe main.py backtest --replay           # 历史重放：用近 28 天真实数据重建评分样本再回测（需联网）
.venv\Scripts\python.exe main.py backtest --source all       # 全量来源（含 brief / ask / 调试快照）

# 7) 静态 HTML 仪表盘（生成后直接双击打开，无需服务器）
.venv\Scripts\python.exe main.py dashboard                   # 默认回看 7 天，输出 reports/dashboard.html
.venv\Scripts\python.exe main.py dashboard --days 3 --source watch

# 8) 本地 HTTP API（默认仅监听 127.0.0.1:8080）
.venv\Scripts\python.exe main.py api
#    GET  /health | /snapshot | /dashboard | /brief
#    POST /ask   {"question": "BTC 能追多吗", "symbols": ["BTC"]}

# 9) 飞书双向机器人（自建应用 + 事件订阅，回调地址 /feishu/event）
.venv\Scripts\python.exe main.py feishu-hook --port 8787
```

每次 `brief` / `watch` / `snapshot` / `ask` 运行后，评分快照会自动追加到 `.state/snapshots/YYYYMMDD.jsonl`（每个标的一行，并带 `source` 标记区分来源）。

回测采用**非重叠窗口采样**：每个 `horizon` 窗口只取该窗口起点的一条记录作为样本，未来价格必须落在目标时刻 ±0.5×horizon 内，数据断档的窗口直接丢弃。因此样本量口径是"有效窗口数"而非"记录配对数"——挂 `watch` 时每标的每 24h 最多 1 个样本，攒够 30 个窗口（约 10 天 × 3 标的）再解读相关系数才有意义。

`--replay` 不等落盘数据，直接按同一套评分规则重建历史样本：

```powershell
.venv\Scripts\python.exe main.py backtest --replay --days 28 --step 4 --hours 4,24 --recommend
```

- 技术面：按 1h/4h/1d 截止该时刻的 K 线重算，完全可复现；
- 持仓面：只有资金费率可重建（Gate 约 90 条 / 30 天），历史 OI 与多空比接口只保留约 24h，回放按中性处理；
- 流动性：DeFiLlama 稳定币供应历史；情绪：alternative.me 恐慌贪婪历史；宏观：东方财富指数日线（用上一交易日收盘，避免未来函数）；
- 重放只用于本次计算，不写入 `.state/snapshots`。

输出包含五块：

1. **状态桶**：按 `REGIME_THRESHOLDS` 分桶，看"偏多/偏空"是否对应正/负收益；
2. **分位桶**：按综合分排序五等分（Q1 最低 → Q5 最高），样本不足时也不会出现空桶，用于检查收益单调性；
3. **收益质量**：Pearson r、Spearman IC（秩相关，抗极端值）、整体胜率、均盈/均亏、盈亏比、平均最大不利偏移（MAE，窗口内最低价相对入场价）；
4. **权重敏感性**（`--sweep`）：各维度权重 ±5pp、其余维度等比缩放后重算总分，观察相关系数变化；某方向能明显提升 r，说明该维度权重偏低；
5. **权重建议**（`--recommend`）：用同一批非重叠样本分别计算每个维度分数与未来收益的 Spearman IC。只有 IC 明显超出抽样噪声带（`1/√n`，且至少再高 5pp）的维度才参与权重分配，其余维度保留 5% 下限、单维度上限 50%，并输出可直接替换的 `DIMENSION_WEIGHTS` 代码块。有效窗口不足 30 个、或所有维度都只有噪声级 IC 时，只展示 IC 而不给建议。
6. **策略回测**（`--strategy`）：按评分阈值模拟做多/做空（含止损比例与最长持仓限制），输出净值曲线、最大回撤、年化夏普、交易次数与胜率，用于回答"按综合分变色能不能赚钱"。

## 七、配置项（.env）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SYMBOLS` | `BTC,ETH,SOL` | 观察列表 |
| `EXCHANGE` | `gate` | 行情/永续数据源：`gate` 或 `binance` |
| `BINANCE_SPOT_BASE` | `https://data-api.binance.vision` | Binance 现货接口地址 |
| `BINANCE_FUTURES_BASE` | `https://fapi.binance.com` | Binance 永续接口地址，不可达时可指向自建代理 |
| `TIMEFRAMES` | `1h,4h,1d` | 分析周期 |
| `QUOTE` | `USDT` | 计价货币（影响交易对拼装与 symbol 归一化） |
| `KLINE_LIMIT` | `300` | 每次拉取的 K 线根数，越大指标越稳、请求越重 |
| `HTTP_TIMEOUT` | `12` | 单次 HTTP 请求超时（秒） |
| `CACHE_DIR` | `.cache` | HTTP 磁盘缓存目录 |
| `CACHE_MAX_AGE_DAYS` | `7` | 缓存文件保留天数，超过自动清理 |
| `CACHE_MAX_FILES` | `3000` | 缓存文件数上限，超出按最旧优先删除 |
| `STATE_DIR` | `.state` | 告警冷却状态、评分快照目录 |
| `REPORT_DIR` | `reports` | 简报、告警日志输出目录 |
| `LLM_BASE_URL` | `https://api.deepseek.com` | 任意 OpenAI 兼容接口 |
| `LLM_API_KEY` | 空 | 留空则只跑规则引擎 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `ALERT_INTERVAL` | 300 | 轮询间隔（秒） |
| `ALERT_COOLDOWN` | 1800 | 同类告警冷却（秒），按 `交易所:标的:类型` 独立计时 |
| `ALERT_STATE_TTL_HOURS` | 168 | 冷却记录保留时长（小时），过期自动清理 |
| `ALERT_QUIET_HOURS` | 空 | 夜间静默时段，如 `23:00-07:00`（支持跨零点）；期间告警只写 `alerts.log` 不推送 |
| `ALERT_MIN_LEVEL` | `info` | 最低推送级别：`info` 全推；`warning` 只推 warning，info 仍落盘 |
| `BRIEF_PUSH_TIMES` | `09:00,21:00` | `serve` 每日简报推送时间点，24 小时制、逗号分隔 |
| `WEBHOOK_URL` | 空 | 飞书/钉钉自定义机器人 Webhook，留空不推送；含 `feishu` 或 `larksuite` 判定为飞书 |
| `WEBHOOK_SECRET` | 空 | 飞书机器人开启「签名校验」时填入 secret，未开启留空 |
| `API_HOST` | `127.0.0.1` | 本地 API 监听地址；对外暴露时改成 `0.0.0.0` 并配置 token |
| `API_PORT` | `8080` | 本地 API 端口 |
| `API_TOKEN` | 空 | 为空不鉴权；配置后请求需带 `X-API-Token` 或 `Authorization: Bearer <token>` |
| `FEISHU_BASE_URL` | `https://open.feishu.cn` | 飞书开放平台地址，Lark 国际版可改 |
| `FEISHU_APP_ID` | 空 | 自建应用 App ID（双向机器人用，不是 Webhook） |
| `FEISHU_APP_SECRET` | 空 | 自建应用 App Secret |
| `FEISHU_VERIFY_TOKEN` | 空 | 事件订阅的 Verification Token，用于校验回调来源 |
| `FEISHU_BOT_HOST` | `127.0.0.1` | 事件回调监听地址 |
| `FEISHU_BOT_PORT` | `8787` | 事件回调端口 |
| `LOG_LEVEL` | `INFO` | 日志级别（DEBUG/INFO/WARNING/ERROR）；daemon 日志输出到 reports/watch.log |

### 交易所切换

默认使用 Gate.io（国内直连，现货与永续均可取）。切换到 Binance 有两种方式：

1. 长期生效：`.env` 中设 `EXCHANGE=binance`；
2. 单次生效：`main.py brief --exchange binance`，`snapshot` / `ask` / `watch` 同样支持该参数。

完整的 Binance 模式配置模板见 `.env.binance.example`（合并进现有 `.env`，不要整文件覆盖）。

Binance 说明：

- 现货走 `data-api.binance.vision`（国内可直连）；永续走 `fapi.binance.com`，国内多数网络不可达；
- 永续不可达时持仓面按 50 分中性处理，简报的数据源一行会标注「永续接口不可达」，不会静默丢数据；
- 若需要完整持仓结构，可将 `BINANCE_FUTURES_BASE` 指向可访问的代理地址；
- Binance 公共 REST 不提供逐笔爆仓量，`long_liq_usd_1m` / `short_liq_usd_1m` 为空；
- 资金费率按 Binance 永续默认 8 小时结算周期折算年化。

**代理配置**：把 `HTTPS_PROXY` / `HTTP_PROXY` 写入 `.env`（完整示例见 `.env.example`）即可让请求走本地代理；只代理 Binance 时用 `NO_PROXY` 排除境内数据源；socks5 代理需先 `pip install pysocks`。也可以把 `BINANCE_FUTURES_BASE` 指向自建反向代理。

### 飞书机器人接入步骤

1. 飞书群里 `设置 → 群机器人 → 添加机器人 → 自定义机器人`，复制 Webhook 地址填入 `.env` 的 `WEBHOOK_URL`；
2. 若创建时勾选「签名校验」，把 secret 填入 `WEBHOOK_SECRET`；
3. 跑 `main.py push-test` 发一条文本验证连通，再跑 `main.py push-test --card` 验证卡片样式；
4. 日常使用：
   - `brief --push` 把行情简报以卡片形式推送到群里（含结论速览 / 分析师点评 / 温度计 / 关键价位与异动 / 数据缺口）；
   - `watch` 监控触发阈值时自动推送文本告警，享受冷却去重；告警同时追加到 `reports/alerts.log`，推送失败也能回溯；
   - 非飞书机器人（如钉钉）自动降级为纯文本。

### 本地 API

`api` 用标准库起的服务，默认 `127.0.0.1:8080`，不依赖额外包；`/ask` 设有并发上限与超时保护，LLM 长耗时不会阻塞其他接口：

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/health` | GET | 健康检查，返回当前交易所与标的 |
| `/snapshot` | GET | 结构化快照 JSON；`symbols=BTC,ETH`、`news=1` 可选 |
| `/dashboard` | GET | 动态渲染仪表盘 HTML；`days=7`、`source=watch` 可选 |
| `/brief` | GET/POST | 生成简报并返回 Markdown；`llm=0` 关闭点评 |
| `/ask` | POST | `{"question":"...","symbols":["BTC"]}`，返回 Agent 回答 |

```powershell
curl.exe "http://127.0.0.1:8080/health"
curl.exe "http://127.0.0.1:8080/snapshot?symbols=BTC"
curl.exe -X POST "http://127.0.0.1:8080/ask" -H "Content-Type: application/json" -d '{"question":"BTC 现在能追多吗"}'
```

> 默认只监听本机。需要从其他设备访问时用 `--host 0.0.0.0` 并在 `.env` 配置 `API_TOKEN`（请求带 `X-API-Token` 或 `Authorization: Bearer <token>`）。

### 飞书双向机器人

与「自定义机器人 Webhook」不同，双向问答需要**自建应用 + 事件订阅**：

1. 飞书开放平台创建企业自建应用，开启「机器人」能力；
2. 申请权限：接收消息（`im:message`）、以应用身份发消息（`im:message:send_as_bot`）；
3. 事件订阅添加 `im.message.receive_v1`（接收消息），请求地址填 `https://你的域名/feishu/event`；本机可用内网穿透映射到 `8787`；
4. 把 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_VERIFY_TOKEN` 填入 `.env`，启动 `main.py feishu-hook --port 8787`；
5. 群里 @机器人 提问即可收到回答，单聊直接发消息也会回复；同一事件重推会自动去重；同一会话（按 chat_id）保留上下文 30 分钟，可连续追问，超时未活跃自动清理。

> 回调需要公网可达；LLM 回答通常超过飞书 3 秒超时，服务会先返回受理、再后台回答并回帖。暂不支持加密回调，请在后台关闭 Encrypt Key。

### 维护与清理

- 每次 CLI 启动自动清理 `.cache`：超过 `CACHE_MAX_AGE_DAYS` 的文件删除，超过 `CACHE_MAX_FILES` 时按最旧优先删除；
- 告警冷却记录超过 `ALERT_STATE_TTL_HOURS` 自动从 `.state/alerts.json` 移除，避免长期运行无限膨胀；
- 两个清理都跳过失败项，不影响主流程。

### 定时推送（内置调度 `serve`）

定时简报由常驻进程 `serve` 负责，不再依赖 Windows 计划任务：

- 一个进程同时跑「异动监控（默认 300s/轮）」与「每日定时简报推送」；
- 推送时间读 `BRIEF_PUSH_TIMES`（默认 `09:00,21:00`），也可用 `serve --times 08:30,20:30` 临时覆盖；
- 每个时间点触发后写入 `.state/serve_slots.json`，进程重启不会重复推送；错过时间点 30 分钟内仍会补发，超过则跳过；
- 单轮采集/简报失败只打印日志，不影响后续轮询。

```powershell
# 前台试跑一轮（监控 + 到点的简报），便于验证
.venv\Scripts\python.exe main.py serve --once

# 常驻运行（Ctrl+C 退出）
.venv\Scripts\python.exe main.py serve --interval 300 --times 09:00,21:00
```

日常推荐用 `scripts\watch_start.ps1` 在后台拉起守护进程，daemon 内部执行的就是 `main.py serve`，因此告警与定时简报共用同一份进程状态，不会重复采集。

### 后台守护（长期采集，攒回测样本）

`serve`（内含 watch 轮询）需要长时间挂起才能攒够回测样本（≥30 个有效窗口，约 10 天 × 3 标的）。仓库提供 4 个脚本：

| 脚本 | 用途 |
| --- | --- |
| [scripts/watch_daemon.ps1](scripts/watch_daemon.ps1) | 守护循环本体：执行 `main.py serve`，自动重启 + 日志轮转 + PID 管理 + 单实例保护 |
| [scripts/watch_start.ps1](scripts/watch_start.ps1) | 以隐藏窗口启动 daemon（当前会话内有效） |
| [scripts/watch_stop.ps1](scripts/watch_stop.ps1) | 停止 daemon 及其全部子孙进程 |
| [scripts/watch_register_task.ps1](scripts/watch_register_task.ps1) | 注册"登录自启"；权限不足时自动降级到 Startup 文件夹快捷方式 |

**典型用法**：

```powershell
# 当前会话临时跑（关闭当前 PowerShell 窗口即停止）
powershell -File scripts\watch_start.ps1

# 注册登录自启（推荐，开机/重登后自动拉起）
powershell -File scripts\watch_register_task.ps1

# 查看运行状态
Get-Content reports\watch.log -Tail 20

# 停止
powershell -File scripts\watch_stop.ps1
```

**守护参数**（默认值见脚本头部）：`-Interval 300` 轮询间隔；`-MaxLogMB 5` 日志轮转阈值；`-MaxCrashPerHour 6` 崩溃熔断。

**已知限制**：在 Trae / VS Code 集成终端里启动的 daemon 会随终端会话一起被回收，因此请务必在**独立的 Windows PowerShell 窗口**里运行 `watch_start.ps1`，或直接依赖 Startup 快捷方式实现开机自启。

## 八、扩展方式

- **加币种**：改 `SYMBOLS` 即可，须为当前交易所（`EXCHANGE`）现货 `XXX/USDT` 且有 USDT 永续（无永续或永续不可达时持仓面自动降级为中性）。
- **加交易所**：在 `providers/exchanges/` 新增模块，实现 `pair/candles/ticker/funding_history/derivatives/source_note` 并注册到 `exchanges/__init__.py` 的 `_IMPLS`。
- **加数据源**：在 `providers/` 新增模块，返回普通 dict，在 `analysis.build_snapshot` 中并入并在 `report`/`tools` 中消费。
- **调权重**：`config.DIMENSION_WEIGHTS` 与 `TIMEFRAME_WEIGHTS`。
- **调阈值**：`config.THRESHOLDS`（RSI、资金费率、持仓量、量比、贴近价位、恐慌贪婪等信号/告警/评分共用，改一处全链路生效）。
- **加告警规则**：在 `alerts.evaluate` 里 `add(key, level, message)`，自动享受冷却去重与推送。
- **加新闻源**：`providers/sentiment.py` 中 `RSS_FEEDS`（RSS/Atom）、`JINSE_INFORMATION_URLS`（JSON API）、`JINSE_LIVES_URL`（HTML 快讯），解析逻辑分别在 `_parse_feed` / `_parse_jinse_information` / `_parse_jinse_lives`。

## 八、Docker 部署

仓库提供 `Dockerfile` 与 `docker-compose.yml`，可一键拉起常驻服务。镜像基于 `python:3.12-slim`，无需额外系统依赖。

### 服务组成

| 服务 | 命令 | 端口 | 说明 |
| --- | --- | --- | --- |
| `serve` | `main.py serve` | — | 异动监控 + 每日定时简报推送（主进程） |
| `api` | `main.py api` | 8080 | 本地 HTTP API，带 `/health` 健康检查 |
| `feishu` | `main.py feishu-hook` | 8787 | 飞书双向机器人回调（按需启用） |

三个服务共享 `.cache` / `.state` / `reports` 三个命名卷，重启不丢数据。

### 使用步骤

```powershell
# 1) 准备配置（从模板复制，按需填写 WEBHOOK_URL / LLM_API_KEY 等）
copy .env.example .env

# 2) 构建并启动（默认起 serve + api；feishu 按需启用）
docker compose up -d --build

# 3) 只启动飞书机器人
docker compose up -d feishu

# 4) 查看日志
docker compose logs -f serve
docker compose logs -f api

# 5) 验证 API 健康
curl.exe http://127.0.0.1:8080/health

# 6) 停止
docker compose down
```

### 注意事项

- 时区默认 `Asia/Shanghai`，与 `BRIEF_PUSH_TIMES` 的本地时间一致；
- `API_HOST` / `FEISHU_BOT_HOST` 在 compose 中已设为 `0.0.0.0`，容器内监听；对外暴露前务必在 `.env` 设置 `API_TOKEN`；
- `/health` 接口始终公开（无需 token），便于容器健康检查与外部探活；
- 若需访问 Binance 永续，在 `.env` 配置 `HTTPS_PROXY` 指向宿主机代理（注意 `127.0.0.1` 在容器内指向容器自身，需用宿主机局域网 IP 或 `host.docker.internal`）。

## 九、已知边界

- **交易所净流入/流出**需要 CryptoQuant / Glassnode 付费 Key，当前用"稳定币供应 + 持仓结构"近似替代，需接入时在 `providers/onchain.py` 增加即可；
- 新闻来源为英文 RSS（Cointelegraph/Decrypt）+ 金色财经中文资讯与快讯，中文快讯通过 HTML 解析获取，页面结构变更可能导致解析失效（失败时自动跳过，不影响其他源）；
- ETH 网络数据（gas/base fee）来自公共 RPC `ethereum-rpc.publicnode.com`，无 Key 但可能存在速率限制或短暂不可用，失败时该字段为空；
- 资金费率分位基于当前所选交易所（Gate / Binance）永续近 90 天结算数据，不含其他交易所；
- 回测基于落盘快照做"评分 vs 未来收益"的分桶统计与相关性检验，采用非重叠窗口采样（每窗口一个样本、允许 0.5×horizon 的价格滞后、断档窗口丢弃）。它只能回答"评分当时是否有预测力"，不能替代逐根 K 线的策略回测；后者可在 `backtest.py` 之上扩展。
- 分位桶在样本 <5 个窗口时不输出，`--sweep` 在带分维度分数的快照 <3 条或无法算出基线 r 时直接跳过——样本越多，权重扫描的结论才越可信。
- 回测只统计 `source=watch` 的记录（可用 `--source all` 放开），因此样本积累速度受 watch 运行时长限制：3 个标的每天最多 3 个 24h 窗口。
- 多轮对话为进程内上下文，`Ctrl+C` / `/exit` 后不持久化；超过 25 条消息时从倒数第 3 轮开始截断，避免打断 tool_calls 与 tool 结果的配对。
- 仪表盘只渲染落盘评分快照（综合分序列），不含 K 线行情；页面为单文件静态 HTML，内联 SVG，可离线归档。
- 打分前会校验各周期 K 线最后时间戳的新鲜度，数据源返回过期缓存时按"数据缺口"处理并显式声明，不会用过期数据静默打分。

## 免责声明

本项目仅用于研究与技术演示，不构成投资建议。加密市场波动剧烈，任何交易决策请自行评估风险并做好仓位管理。

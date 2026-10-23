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
| 链上流动性 | 稳定币总供应及 1d/7d/30d 变化、BTC 算力/出块/内存池/手续费 | DefiLlama、blockchain.info、mempool.space |
| 情绪面 | 恐慌贪婪指数（当前值、1d/7d 变化、30日均值）+ 加密快讯 | alternative.me、Cointelegraph/Decrypt RSS |
| 宏观联动 | 美元指数、纳斯达克100、标普500 的点位与 1d/5d 涨跌 | 东方财富公开行情 |

三类交互方式 + 一条推送通道全部实现：

- **定时简报** `brief`：生成 Markdown 简报并落盘，可接系统计划任务；
- **对话问答** `ask`：LLM 自主调用 7 个数据工具后作答；
- **异动告警** `watch`：阈值触发 + 冷却去重 + Webhook 推送，覆盖 24h 异动 / RSI 超买卖 / 支撑阻力测试与突破 / 资金费率拥挤与极端分位 / 持仓量异动 / 量能放大 / 评分状态切换 / 恐慌贪婪极端值 / 稳定币流出；
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
│  ├─ tools.py              # Agent 工具层（支持 ask --symbols 作用域）
│  ├─ agent.py              # LLM 工具调用循环
│  ├─ prompts.py            # 人格与分析规范
│  ├─ report.py             # Markdown 简报
│  ├─ alerts.py             # 告警引擎 + 飞书推送
│  ├─ llm.py                # OpenAI 兼容客户端
│  ├─ cli.py                # 命令行子命令
│  └─ providers/            # 四类数据源
│     └─ exchanges/         # 交易所适配：gate.py / binance.py，按 EXCHANGE 切换
├─ scripts/                 # watch 守护进程 / 简报计划任务批处理
├─ tests/                   # pytest 单测（indicators / backtest）
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

# 4) 异动监控（先跑一轮验证）
.venv\Scripts\python.exe main.py watch --once
.venv\Scripts\python.exe main.py watch --interval 300

# 5) 推送链路自检
.venv\Scripts\python.exe main.py push-test          # 纯文本
.venv\Scripts\python.exe main.py push-test --card   # 飞书卡片

# 6) 评分回测（离线，用历史落盘快照检验"综合分 vs 未来收益"）
.venv\Scripts\python.exe main.py backtest                    # 默认 24h 窗口、只统计 watch 来源
.venv\Scripts\python.exe main.py backtest --hours 4          # 换 4h 窗口
.venv\Scripts\python.exe main.py backtest --source all       # 全量来源（含 brief / ask / 调试快照）
```

每次 `brief` / `watch` / `snapshot` / `ask` 运行后，评分快照会自动追加到 `.state/snapshots/YYYYMMDD.jsonl`（每个标的一行，并带 `source` 标记区分来源）。

回测采用**非重叠窗口采样**：每个 `horizon` 窗口只取该窗口起点的一条记录作为样本，未来价格必须落在目标时刻 ±0.5×horizon 内，数据断档的窗口直接丢弃。因此样本量口径是"有效窗口数"而非"记录配对数"——挂 `watch` 时每标的每 24h 最多 1 个样本，攒够 30 个窗口（约 10 天 × 3 标的）再解读 Pearson 相关系数才有意义。

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
| `STATE_DIR` | `.state` | 告警冷却状态、评分快照目录 |
| `REPORT_DIR` | `reports` | 简报、告警日志输出目录 |
| `LLM_BASE_URL` | `https://api.deepseek.com` | 任意 OpenAI 兼容接口 |
| `LLM_API_KEY` | 空 | 留空则只跑规则引擎 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `ALERT_INTERVAL` | 300 | 轮询间隔（秒） |
| `ALERT_COOLDOWN` | 1800 | 同类告警冷却（秒） |
| `WEBHOOK_URL` | 空 | 飞书/钉钉自定义机器人 Webhook，留空不推送；含 `feishu` 或 `larksuite` 判定为飞书 |
| `WEBHOOK_SECRET` | 空 | 飞书机器人开启「签名校验」时填入 secret，未开启留空 |

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

### 定时推送（Windows 计划任务）

仓库自带 [scripts/brief_push_task.bat](scripts/brief_push_task.bat) 作为任务入口（**任务本身的注册属于本机状态，不在版本库内**）。本机已注册两条每日任务：

- `CryptoAnalyst\BriefPushMorning`：每日 09:00
- `CryptoAnalyst\BriefPushEvening`：每日 21:00

执行日志写到 `reports\brief_task.log`。

> **注意：** 这两条任务当前的登录模式是 `Interactive only`（`schtasks /Query /V` 可见），**用户未登录时不会执行**。需要真正无人值守时，要改成"不管用户是否登录都运行"并在任务里配置凭据。

管理命令：

```powershell
schtasks /Query /TN "CryptoAnalyst\BriefPushMorning"        # 查看状态
schtasks /Run /TN "CryptoAnalyst\BriefPushMorning"          # 立即触发一次
schtasks /Delete /TN "CryptoAnalyst\BriefPushMorning" /F    # 删除
```

退出码约定：`0` 表示简报+推送全部成功；`3` 表示简报已落盘但推送失败；`1`/`2` 表示简报本身失败。

### watch 常驻（攒回测样本）

`watch` 需要长时间挂起才能攒够回测样本（≥30 个有效窗口，约 10 天 × 3 标的）。仓库提供 4 个脚本：

| 脚本 | 用途 |
| --- | --- |
| [scripts/watch_daemon.ps1](scripts/watch_daemon.ps1) | 守护循环本体：自动重启 + 日志轮转 + PID 管理 + 单实例保护 |
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
- **换中文新闻源**：替换 `providers/sentiment.py` 的 `RSS_FEEDS` 即可，解析逻辑通用。

## 九、已知边界

- **交易所净流入/流出**需要 CryptoQuant / Glassnode 付费 Key，当前用"稳定币供应 + 持仓结构"近似替代，需接入时在 `providers/onchain.py` 增加即可；
- 新闻为英文 RSS（国内中文快讯接口多数需要签名鉴权），标题可直接交给 LLM 翻译与筛选；
- 资金费率分位基于当前所选交易所（Gate / Binance）永续近 90 天结算数据，不含其他交易所；
- 回测基于落盘快照做"评分 vs 未来收益"的分桶统计与相关性检验，采用非重叠窗口采样（每窗口一个样本、允许 0.5×horizon 的价格滞后、断档窗口丢弃）。它只能回答"评分当时是否有预测力"，不能替代逐根 K 线的策略回测；后者可在 `backtest.py` 之上扩展。
- 回测只统计 `source=watch` 的记录（可用 `--source all` 放开），因此样本积累速度受 watch 运行时长限制：3 个标的每天最多 3 个 24h 窗口。

## 免责声明

本项目仅用于研究与技术演示，不构成投资建议。加密市场波动剧烈，任何交易决策请自行评估风险并做好仓位管理。

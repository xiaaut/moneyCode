# 项目进度与技术手册（PROGRESS.md）

> 生成时间：2026-11-06 · 当前状态：**前后端已全面切换到 FastAPI + NAS PostgreSQL，已接入 AKShare 全市场数据底座**

本文档是系统的完整技术索引，包含：目录树、数据库表结构、接口路由清单、后台异步任务机制、本地启动命令。

---

## 1. 架构总览

```
┌───────────────┐        /api/*（Vite proxy）        ┌──────────────────┐        ┌──────────────────┐
│  stock-web    │ ─────────────────────────────────▶ │   stock-service   │ ─────▶ │  NAS PostgreSQL   │
│  Vue3 + Vite  │   http://127.0.0.1:8000            │  FastAPI + async  │  async │  portfolio_db:5434 │
│  Element Plus │                                    │  SQLAlchemy 2.0   │  pg    │  （Docker 外部映射）│
└───────────────┘                                    └──────────────────┘        └──────────────────┘
                                                            │
                              ┌─────────────────────────────┼──────────────────────────────┐
                              ▼                             ▼                              ▼
                       同花顺 F10 / 东财 F10        东财 push2 / 腾讯行情            国资委央企名录
                       （实控人穿透）                （现价 / PE / PB / ROE）          （集团 → 央企映射）
```

- 前端 `stock-web/`：Vue3 + Vite + Element Plus，所有数据读写统一走 `/api/*`，由 `server.proxy` 转发到 FastAPI。
- 后端 `stock-service/`：FastAPI + SQLAlchemy 2.0（async + asyncpg），业务数据落 NAS PostgreSQL，行情/实控人按需抓取上游并缓存。
- 数据库：NAS `192.168.31.122:5434`，库名 `portfolio_db`（Docker 容器内 5432 → 宿主机 5434）。

---

## 2. 完整目录树

```
my-finance-system/
├── stock-service/                    # FastAPI 后端
│   ├── requirements.txt              # 依赖清单
│   ├── .env.example                  # 环境变量模板（.env 已 gitignore）
│   ├── init_db.py                    # 建表脚本（幂等，打印建表结果）
│   ├── migrate_json.py               # 历史 JSON 清洗导入脚本（幂等，支持 --dry-run）
│   ├── migrate_stocks.py             # stocks 表字段平滑扩展（ALTER TABLE ADD COLUMN IF NOT EXISTS，幂等）
│   ├── migrate_strategy.py           # strategy_config 表建表（CREATE TABLE IF NOT EXISTS，幂等）
│   ├── migrate_fund_flow.py          # 资金流/龙虎榜建表（CREATE TABLE IF NOT EXISTS，幂等）
│   ├── migrate_system_meta.py        # system_meta 表建表（CREATE TABLE IF NOT EXISTS，幂等）
│   ├── migrate_kline.py              # stock_kline_daily 表建表（CREATE TABLE IF NOT EXISTS，幂等）
│   ├── migrate_financials.py         # 财报三表/笔记表建表（stock_financial_statements + stock_analysis_notes）
│   ├── migrate_rebalance.py          # rebalance_plans 表建表（调仓工单，CREATE TABLE IF NOT EXISTS，幂等）
│   ├── migrate_positions_updated_at.py # positions.updated_at 隔离迁移（存量锚定 2026-01-01，幂等）
│   └── app/
│       ├── main.py                   # 入口：lifespan + CORS + 路由挂载 + 统一异常
│       ├── core/config.py            # pydantic-settings 配置（NAS 连接、连接池、同花顺数据源）
│       ├── db/
│       │   ├── base.py               # DeclarativeBase + 约束命名规范
│       │   └── session.py            # async engine + AsyncSession + get_db
│       ├── adapters/                 # 数据源适配器层（主备双引擎）
│       │   ├── ths_client.py         #   同花顺主通道（行情/估值/财务指标/基金，代码双向转换）
│       │   ├── free_client.py        #   免费热备通道（东财 push2/腾讯/东财单股财务）
│       │   └── data_router.py        #   主备路由器（ths_first 优先 + 异常自动降级 + source 标注）
│       ├── models/                   # SQLAlchemy 2.0 模型（Mapped / mapped_column）
│       │   ├── stock.py              #   stocks
│       │   ├── portfolio.py          #   positions / trade_history
│       │   ├── grid.py               #   grid_strategies / grid_trade_records
│       │   ├── watchlist.py          #   watchlist_items
│       │   ├── msci_meta.py          #   msci_meta（单例）
│       │   ├── strategy.py           #   strategy_config（选股策略配置，单例）
│       │   ├── fund_flow.py          #   stock_fund_flow_daily / stock_lhb_daily
│       │   ├── system_meta.py        #   system_meta（系统元信息，单例）
│       │   ├── kline.py              #   stock_kline_daily（全市场复权日线）
│       │   ├── financial.py          #   stock_financial_statements / stock_analysis_notes
│       │   └── rebalance.py          #   rebalance_plans（调仓工单）
│       ├── schemas/                  # Pydantic 模型（camelCase 对齐前端）
│       │   ├── common.py             #   CamelModel 基类 + 脏值转换工具
│       │   ├── portfolio.py / grid.py / stocks.py / quote.py / strategy.py
│       ├── services/
│       │   ├── quotes.py             # 东财 push2 行情 + 腾讯行情 + ROE（TTL 内存缓存）
│       │   ├── ths.py                # 同花顺/东财实控人抓取 + 国企关键字分类 + 央企名录
│       │   ├── msci_parse.py         # Excel(TXT/CSV) 解析（openpyxl + xlrd）
│       │   ├── akshare_sync.py       # AKShare 全市场同步引擎（asyncio.to_thread + 分批 upsert）
│       │   ├── fund_flow.py          # 资金流/龙虎榜采集（to_thread + 防封限流 + 席位明细）
│       │   ├── daily_sync.py         # 收盘一键更新（五合一异步流水线编排）
│       │   ├── writeback.py          # 统一回写 stocks（防零值 + name 兜底 + coalesce）
│       │   ├── kline_sync.py         # K线 Parquet 流式入库 + ETF 前复权日线补偿
│       │   ├── grid_backtest.py      # 网格历史回测（防未来函数撮合 + 绩效指标）
│       │   ├── financial_analysis.py # 张新民财报分析（核心指标 + 六大雷区 + 健康分）
│       │   ├── rebalance_service.py  # 四维联动调仓雷达（持仓/自选/K线/财报聚合 + 信号 + 工单 CRUD）
│       │   ├── onboarding.py         # 新增股票自动入驻（日线 + 财报三表体检，挂新增持仓/自选钩子）
│       │   └── jobs.py               # 后台任务（国企扫描 / MSCI 分析）+ JobState
│       └── api/v1/
│           ├── router.py             # 路由聚合
│           └── endpoints/
│               ├── portfolio.py      # 持仓 / 自选 / 清仓
│               ├── stocks.py         # 股票池筛选 / 快速检索 / 单股刷新
│               ├── grid.py           # 网格策略
│               ├── quote.py          # 行情 / ROE / 名称
│               ├── controller.py     # 实际控制人
│               ├── msci.py           # MSCI 成份股
│               ├── state_owned.py    # 国资扫描
│               ├── strategy.py       # 选股策略配置
│               ├── fund_flow.py      # 资金流 / 龙虎榜
│               ├── kline.py          # K线同步（全市场 Parquet / ETF 日线）
│               ├── financials.py     # 财报透视与排雷报告 / 复盘笔记
│               ├── rebalance.py      # 四维调仓雷达总览 / 调仓工单 CRUD
│               └── system.py         # 收盘一键更新
│
└── stock-web/                        # Vue3 前端
    ├── vite.config.ts                # 仅剩 Vite 配置 + /api 代理（旧中间件已全部移除）
    ├── package.json / index.html
    ├── data/                         # 历史 JSON 数据（已迁移进 PG，保留作为迁移源）
    └── src/
        ├── App.vue                   # 布局 + 观察列表/持仓加载
        ├── utils/holdingsImport.js / gridCalculator.js
        └── components/
            ├── Positions.vue         # 持仓股票
            ├── ClosedPositions.vue   # 已清仓
            ├── WatchList.vue         # 观察列表
            ├── Strategy.vue          # 选股策略（Davis 双因子）
            ├── GridTrading.vue       # 网格交易
            ├── MsciData.vue          # MSCI 数据
            ├── StateOwned.vue        # 国资控股
            └── AStockMarket.vue      # A股全市场（AKShare 展示面板）
            ├── FundFlow.vue          # 资金与异动（抢筹榜 + 龙虎榜双 Tab）
            ├── FundFlowDrawer.vue    # 个股资金与异动详情抽屉（3 图）
            └── FundFlowSpark.vue     # 近5日主力迷你柱状图（内嵌持仓/自选）
```

---

## 3. NAS 数据库表结构（`portfolio_db`，14 张表）

### 3.1 stocks（≈5900 行）— 股票主数据（多来源合并统一视图）

| 字段 | 类型 | 说明 |
|---|---|---|
| code | varchar | **主键**，6 位证券代码 |
| name | varchar | 名称（index） |
| industry / board | varchar | 行业 / 板块（沪主板·深主板·北交所） |
| controller | varchar | 实际控制人 |
| controller_category | varchar | 实控人类型：sasac-central/sasac-local/government/state-finance/state-group/collective/person/none/other/unknown |
| pe_ttm / pb / roe | numeric | 滚动市盈率 / 市净率 / 净资产收益率 |
| eps_current / eps_forecast | numeric | 当前 / 预测 EPS |
| price / change_pct / market_cap_yi | numeric | 现价 / 涨跌幅 / 总市值（亿） |
| volume / turnover_amount / turnover_rate | numeric | 成交量（手）/ 成交额（元）/ 换手率(%) |
| circ_mv / change_60d / change_ytd | numeric | 流通市值（元）/ 60 日涨跌幅(%) / 年初至今涨跌幅(%) |
| revenue / revenue_yoy | numeric | 营业总收入（元）/ 营收同比增长(%) |
| net_profit / net_profit_yoy | numeric | 净利润（元）/ 净利润同比增长(%) |
| gross_margin / report_period | numeric / varchar | 销售毛利率(%) / 最新财报期（如 2026Q3） |
| is_watchlist | bool | 是否自选 |
| **in_msci** | bool | 是否 MSCI 成份股（index） |
| **state_owned** | bool | 是否国企/政府控股（index） |
| extra | jsonb | 未结构化指标（source/note/穿透明细/controller_detail…） |
| msci_extra | jsonb | MSCI 导入行原始列值（如「权重」「序号」） |
| created_at / updated_at | timestamptz | 时间戳 |

> 行情与财报字段由 `akshare_sync.py` 写入（AKShare `stock_zh_a_spot_em` + `stock_yjbb_em`）；字段扩展走 `migrate_stocks.py`（`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`），保留原有 controller / state_owned / in_msci 数据。

### 3.2 positions（22 行）— 当前持仓

`id`(bigint PK) · `code`(varchar **unique**+index) · `name` · `shares`(int) · `cost_price` · `current_price` · `market_value` · `profit_amount` · `profit_ratio` · `sector` · `position_ratio` · `extra`(jsonb) · `created_at` · `updated_at`。
> 市值/盈亏/占比为按现价的快照值；同一代码多条持仓在迁移时按股数加权合并成本。
>
> **updated_at 隔离铁律**：仅用户主动「新增持仓 `POST /api/portfolio/positions` / 修改持仓 `PUT /api/portfolio/positions/{code}`」时刷新为当前时间；后台「刷新现价 / 刷新 ROE / 收盘一键更新」只更新 stocks/kline/financials，**严禁触碰 positions.updated_at**（模型已移除 `onupdate`，整数组覆盖 `POST /api/portfolio` 也不更新该字段）。存量记录由 `migrate_positions_updated_at.py` 统一锚定为 `2026-01-01 00:00:00+08`，便于区分「尚未整理」。

### 3.3 trade_history（0 行）— 已清仓/历史成交

`id`(bigint PK) · `code`(index) · `name` · `quantity` · `buy_price`/`sell_price`/`fee` · `buy_date`/`sell_date` · `hold_days` · `profit_amount`/`profit_ratio` · `note` · `source_key`(varchar **unique**，迁移幂等键) · `created_at`。

### 3.4 grid_strategies（1 行）— 网格策略

`id`(varchar PK，如 `grid_1793558185068`) · `code`(index) · `name` · `base_price`/`upper_price`/`lower_price` · `mode`(arithmetic/geometric) · `grid_count` · `step_value`(等差步长) / `step_pct`(等比每档%) · `grid_amount`(单格资金) · `shares_per_grid` · `initial_shares`/`initial_cost` · `commission_rate`/`stamp_duty_rate`/`free_five` · `last_price` · `status`(active/paused) · `current_shares` · `diluted_cost`(动态摊薄成本) · `realized_profit` · `note` · 时间戳。

### 3.5 grid_trade_records（0 行）— 网格档位成交

`id`(varchar PK) · `strategy_id`(FK→grid_strategies **ON DELETE CASCADE**, index) · `code` · `executed_at` · `side`(buy/sell) · `price`/`shares`/`fee`/`realized_profit` · `level_index` · `created_at`。

### 3.6 watchlist_items（3 行）— 观察列表

`id`(bigint PK，前端毫秒时间戳) · `stock_code`(index) · `stock_name` · `current_eps`/`target_eps` · `current_pe`/`historical_avg_pe`/`mean_pe` · `hold_years` · `g_percent`/`peg`/`annualized_return_percent` · `pass_count`/`total_conditions`/`all_passed` · `details`(jsonb) · `note` · `created_at`。

### 3.7 msci_meta（1 行，单例 id=1）— MSCI 数据集元信息

`file_name` · `imported_at`/`updated_at`/`analyzed_at` · `columns`(jsonb 额外列名)。

### 3.8 strategy_config（0~1 行，单例 id=1）— 选股策略配置

`stock_name`/`stock_code` · `current_eps`/`target_eps` · `current_pe`/`historical_avg_pe`/`mean_pe` · `hold_years` · `portfolio_count`/`max_industry_count` · `updated_at`。
> 对应前端 Strategy.vue「Davis 双因子选股策略器」的输入参数，跨设备持久化；`epsYears`(3 年) 为前端硬编码常量不入库。由 `migrate_strategy.py` 建表（CREATE TABLE IF NOT EXISTS）。

### 3.9 stock_fund_flow_daily（个股资金流向日表）

联合主键 `code`(varchar) + `trade_date`(date) · `close_price`/`change_pct` · `main_net_inflow`/`main_net_ratio` · `super_net_inflow`/`large_net_inflow`/`medium_net_inflow`/`small_net_inflow` · `updated_at`。

### 3.10 stock_lhb_daily（龙虎榜每日明细）

联合主键 `code` + `trade_date` + `reason`(上榜原因) · `stock_name` · `lhb_net_buy`/`lhb_buy_amount`/`lhb_sell_amount` · `turnover_ratio`(成交占比%) · `inst_net_buy`(机构净买) · `seat_details`(jsonb，买卖前五席位+机构/北向标签) · `updated_at`。

> 由 `migrate_fund_flow.py` 建表（CREATE TABLE IF NOT EXISTS）；席位明细仅对持仓/自选上榜个股按需拉取，机构净买额按「名称+买卖金额」三元组去重后对「机构专用」席位净额求和。

### 3.11 system_meta（1 行，单例 id=1）— 系统元信息

`last_daily_sync_at`（最近一次收盘一键更新完成时间）· `updated_at`。

> 持久化「收盘一键更新」完成时间，避免进程重启后内存态丢失；由 `migrate_system_meta.py` 建表。`GET /api/system/daily-sync/progress` 按「内存 → system_meta → stocks 表 max(updated_at)」三级兜底返回 `last_completed_at`。

### 3.12 stock_kline_daily（全市场复权日线）

联合主键 `code`(varchar) + `trade_date`(date)，另建 `trade_date` 索引 · `open`/`high`/`low`/`close`/`pre_close`(NUMERIC 10,3) · `volume`/`amount`(NUMERIC 18,2) · `turnover_ratio`(NUMERIC 6,3) · `adjust_factor`(NUMERIC 10,4) · `qfq_close`(前复权收盘) · `updated_at`。

> 由 `migrate_kline.py` 建表。A 股全市场来自同花顺 market-dumps Parquet（`/api/dump/market-dumps/{daily-k,daily-k-10d}/download-url` 拿预签名链接，pyarrow `iter_batches` 分块流式入库）；**持仓/自选批量日线**走 `/api/a-share/prices/historical-batch`（`adjust=forward` 前复权，每批 10 只，`ths_client.chunk_symbols` 切片）；ETF 来自 `/api/fund/market/historical`（前复权日线）。批量 upsert 单批 ≤2500 行（每行 10 字段 = 25000 参数，规避 asyncpg 32767 上限）。

### 3.13 stock_financial_statements（财报三表 + 张氏衍生指标）

联合主键 `code`(varchar) + `report_date`(date) · `report_label`/`report_type`/`source` · 利润表 21 字段 + 资产负债表 38 字段 + 现金流量表 10 字段（均 NUMERIC(20,2)）· `derived_metrics`(jsonb，张新民核心利润/两头吃/有息负债率/获现率等) · `updated_at`。

> 由 `migrate_financials.py` 建表。三表经 `data_router.get_financial_statements` 主备双通道拉取：同花顺三表接口字段有限（资产负债表仅 7 字段），张氏分析缺关键字段时自动降级东财 F10（`RPT_F10_FINANCE_GINCOME/GBALANCE/GCASHFLOW`，完整字段）补全。

### 3.14 stock_analysis_notes（个人财报复盘笔记）

`code`(varchar) 主键 · `notes`(text) · `tags`(varchar) · `rating`(varchar) · `updated_at`。

### 3.15 rebalance_plans（调仓工单）

`id`(serial 主键) · `code`/`name`/`account_tag`(多账户标记) · `action_type`(BUY_ADD/SELL_PROFIT/SELL_RISK/GRID_T) · `trigger_condition`(<=/>=) · `target_price`/`plan_shares`/`current_cost` · `reason_tag`/`notes` · `status`(PENDING/TRIGGERED/DONE) · `created_at`/`updated_at`。

> 由 `migrate_rebalance.py` 建表。`GET /api/rebalance/dashboard` 时自动对 PENDING 工单做到价判定（BUY_ADD/GRID_T 现价<=触发价 或 SELL_* 现价>=触发价 → TRIGGERED）。

---

## 4. FastAPI 接口路由清单

路由同时挂载在 **`/api/v1`**（进 OpenAPI 文档）与 **`/api`**（兼容前端历史路径，`include_in_schema=False`），两条入口路径内容完全一致，Vite 代理原样透传、无需 rewrite。

### 4.1 持仓 / 自选 / 清仓（portfolio.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/portfolio` | 持仓列表（纯数组，camelCase） |
| POST | `/api/portfolio` | 整数组覆盖持仓（按 code upsert + 删除多余） |
| POST | `/api/portfolio/positions` | 新增/修改单条持仓（刷新 updated_at，异步触发 auto_onboard_stock） |
| PUT | `/api/portfolio/positions/{code}` | 按代码修改持仓（刷新 updated_at） |
| DELETE | `/api/portfolio/{code}` | 删除持仓（`archive=true` 时写入 trade_history） |
| GET | `/api/portfolio/positions/{code}/dividend-preview` | 分红除权成本试算（`current_cost=`/`current_shares=` 可选，主通道同花顺 adjustment-factors + 东财 RPT_SHAREBONUS_DET 备用） |
| GET | `/api/watchlist` | 观察列表 |
| POST | `/api/watchlist` | 整数组覆盖观察列表 |
| POST | `/api/watchlist/items` | 新增单条观察项 |
| DELETE | `/api/watchlist/{item_id}` | 删除观察项 |
| GET | `/api/closed-positions` | 清仓记录列表 |
| POST | `/api/closed-positions` | 整数组覆盖清仓记录（缺字段服务端兜底推导盈亏/持有天数） |

### 4.2 股票池筛选 / AKShare 全市场（stocks.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/stocks` | 多条件筛选 + 后端分页：`keyword/industry/controller_category/board/state_owned/in_msci/watchlist_only/pe_min/pe_max/roe_min/roe_max/mv_min/sort_by/sort_order/page/page_size`，返回 `total/items/industries/summary`（summary=总家数/涨跌家数/国企数/MSCI数/最后同步时间） |
| GET | `/api/stocks/filters` | 筛选项下拉数据（行业/控制人类型/板块） |
| GET | `/api/stocks/lookup` | 快速检索：`q=` 代码/名称模糊匹配（前 15 条），同时返回持仓/自选快捷列表 |
| POST | `/api/stocks/refresh-single/{code}` | 实时拉取单股行情（腾讯）+财报（业绩报表）并写回 stocks，返回最新字段 |
| POST | `/api/stocks/sync-akshare` | 启动后台单例同步任务（AKShare 全市场入库，重入 409） |
| GET | `/api/stocks/sync-progress` | 同步进度（`job` + `updatedAt` + `updated`） |
| GET | `/api/stocks/{code}` | 单只详情 |

### 4.3 网格（grid.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/grid` | 策略列表（trades 内嵌，camelCase） |
| POST | `/api/grid` | 对象=按 id upsert；数组=整体覆盖；自动重算 step/摊薄成本/套利 |
| POST | `/api/grid/{strategy_id}/trades` | 档位成交打卡（重算 current_shares/diluted_cost/realized_profit） |
| DELETE | `/api/grid/{strategy_id}` | 删除策略（级联删成交） |
| POST | `/api/grid/backtest` | 网格历史回测（code/start_date/end_date/initial_cash/base_price/step_pct/grid_amount/max_grids/commission_rate），返回绩效指标 + 每日净值 + 交易流水 |

### 4.4 K 线同步（kline.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/kline/sync-historical` | 全市场 10 年日 K Parquet 全量同步（后台单例，重入 409） |
| GET | `/api/kline/sync-historical/progress` | 全量同步进度（phase/done/total/耗时） |
| POST | `/api/kline/sync-incremental` | 近 10 交易日日 K 增量同步 |
| POST | `/api/kline/sync-etf/{code}` | 单只 ETF 前复权日线补偿（`years=` 默认 5） |
| POST | `/api/kline/sync-portfolio` | 批量同步持仓+自选 22 只标的 5 年前复权日线（A 股走 `historical-batch` 每批 10 只，ETF 走 `fund-market/historical`） |
| GET | `/api/kline/drawer/{code}` | 详情抽屉 K 线透视舱：`period=` 3m/6m/1y/3y/5y，返回日K+MA20/60/250（完整序列算 MA 再截取）+ position_info + rebalance_plans + financial_summary |

### 4.5 财报透视与排雷（financials.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/financials/report/{code}` | 财报透视报告（`refresh=` 强制重拉三表），返回张氏衍生指标 + 六大雷区 + 健康分 + 一句话透视 + 笔记 |
| POST | `/api/financials/notes/{code}` | 保存个人复盘笔记与评级 |
| POST | `/api/financials/batch-prewarm` | 批量补齐持仓/自选 A 股三表体检（过滤 ETF，每只间隔 0.25s 礼貌限流，`force=` 全量重算） |

> 读库缓存分支（`refresh=false`）用库内原始三表字段 `financial_analysis.analyze()` 全量重算（含驱动类型），确保 `compute_derived`/`_driver_type` 逻辑升级后旧库数据即时反映新定性；顶层另返回 `driver_type`、`bargaining_label`（净占款>0 → 产业链两头吃）。
>
> **张氏驱动类型定性**：① 内部造血(经营负债+留存收益)/总资产 ≥50%，或 ② 留存收益>股东入资 且 净有息负债率((有息债-类现金)/总资产)<20%，即判「经营与盈利驱动型」。**雷区4 标题动态化**：仅有息负债率 ≥50%（danger）显示「刚性偿债与违约前兆」，30%~50% 且类现金可覆盖有息债（≥80%）且获现率≥0.9 时 warn 文案「无实质违约风险」，其余 warn/safe 一律「刚性偿债」（避免对白马股误报违约前兆）。

### 4.6 行情（quote.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/quote` | 批量行情（`secids=` 或 `symbol=`），东财 push2，30s TTL 缓存，不可用时 502/stale |
| GET | `/api/roe` | 批量加权 ROE（`codes=SH600519`），7 天缓存 |
| GET | `/api/stock-name` | 腾讯行情快照（名称/现价/PE/PB/市值） |

### 4.5 实际控制人（controller.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/controller` | `codes=SH600519,SZ000725&refresh=1`，同花顺穿透 + 东财兜底，回写 stocks.controller/controller_category/state_owned |

### 4.6 MSCI（msci.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/msci` | 从 stocks(in_msci=True) + msci_meta 组装回显 |
| POST | `/api/msci` | 保存导入结果（重置 in_msci 后标记新成份股） |
| POST | `/api/msci/parse` | 解析上传 Base64（TXT/CSV/Excel） |
| POST | `/api/msci/analyze` | 后台补全行业/PE/ROE |
| GET | `/api/msci/progress` | 分析进度 |
| POST | `/api/msci/refresh` | 刷新行情 |

### 4.7 国资扫描（state_owned.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/state-owned` | 从 stocks 表组装全市场国资数据 |
| POST | `/api/state-owned/start` | `{force,limit}` 启动后台全量扫描（重入 409） |
| GET | `/api/state-owned/progress` | 扫描进度 |

### 4.8 选股策略配置（strategy.py，单例 id=1）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/strategy-config` | 读取配置（无记录时返回前端默认值） |
| PUT / POST | `/api/strategy-config` | 整体 upsert（幂等，POST 为 PUT 别名） |
| DELETE | `/api/strategy-config` | 重置为默认值 |

### 4.9 资金流 / 龙虎榜（fund_flow.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/fund-flow/sync-portfolio` | 同步持仓+自选资金流（后台单例，重入 409） |
| GET | `/api/fund-flow/sync-progress` | 资金流同步进度 |
| GET | `/api/fund-flow/stock/{code}` | 单股近 60 日资金流 + 龙虎榜（缺/旧自动补拉） |
| GET | `/api/fund-flow/mini-summary` | `codes=` 近 5 日主力净流入数组 + 5日累计 + 龙虎榜徽章 |
| GET | `/api/fund-flow/market-rank` | `indicator=今日/5日/10日` 全市场主力净流入排行 |
| POST | `/api/lhb/sync` | 龙虎榜同步（汇总 + 持仓/自选席位明细，重入 409） |
| GET | `/api/lhb/progress` | 龙虎榜同步进度 |
| GET | `/api/lhb/daily` | `instOnly=` 龙虎榜每日总览（含机构净买） |

### 4.10 系统（system.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/system/daily-sync` | 启动收盘一键更新（五合一流水线：刷现价→全市场→资金流→龙虎榜→持仓日线增量，重入 409） |
| GET | `/api/system/daily-sync/progress` | 流水线进度（running/current_step/percent/steps[]/last_completed_at） |
| GET | `/` | 服务信息 |
| GET | `/api/v1/health` | 健康检查（真实 `SELECT 1`，DB 不可用时 503） |

### 4.11 四维调仓雷达（rebalance.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/rebalance/dashboard` | 四维聚合总览：持仓/自选 + 5 年分位/MA20/MA250 + 张氏健康分/雷达 + 4 类调仓信号 + 工单（自动到价判定） |
| POST | `/api/rebalance/plans` | 新增调仓工单 |
| PUT | `/api/rebalance/plans/{plan_id}` | 更新调仓工单（含 status） |
| DELETE | `/api/rebalance/plans/{plan_id}` | 删除调仓工单 |
| POST | `/api/rebalance/plans/{plan_id}/done` | 标记工单已执行 |

---

## 5. 后台异步任务机制

两个后台任务均由 `app/services/jobs.py` 管理，进程内**单例**，接口用 `asyncio.create_task` 派发，前端轮询进度。

### 5.1 Job 状态机（`JobState`，camelCase 序列化）

```json
{ "running": false, "phase": "idle", "label": "空闲", "done": 0, "total": 0,
  "message": "", "startedAt": null, "finishedAt": null, "error": "" }
```

`phase` 取值：`idle → universe/industry → quotes → controllers/roe → save → done | error`。`launch_*` 入口先**同步置 running=true** 再 `create_task`，保证 `/start` 立即返回可见的 `running` 状态。

### 5.2 全市场国企扫描（`run_state_owned_job`）

1. **universe**：翻页拉取东财「公司概况」（500/页），过滤沪深主板+北交所、剔除 ST/退市。
2. **quotes**：腾讯行情校验上市状态、取现价。
3. **controllers**：先国资委央企名录匹配「国有法人→央企」，剩余用同花顺 F10 穿透（**2 并发** + 同花顺 **0.9s 最小间隔**防封，命中 30 天内存缓存）。
4. **save**：汇总分类，**分批 upsert**（500 行/批，规避 asyncpg 32767 参数上限），写回 stocks 的 controller/controller_category/state_owned/extra。

### 5.3 MSCI 分析（`run_msci_analyze_job`）

1. 取 stocks(in_msci=True) 代码 → 2. 行业（东财 EM2016，2 天缓存）→ 3. PE/PB/现价（腾讯）→ 4. ROE（东财批量 + 逐只兜底）→ 5. 分批 upsert 回 stocks + 更新 msci_meta.analyzed_at。

### 5.4 AKShare 全市场同步（`run_akshare_sync`）

单例任务 `akshare_job`（复用 `JobState`），`phase` 取值：`spot → financial → save → done | error`。akshare 是同步阻塞库，所有抓取经 `asyncio.to_thread` 放入线程池，严禁阻塞主事件循环。

1. **spot**：`ak.stock_zh_a_spot_em()` 抓 5900+ 只 A 股实时行情（现价/涨跌幅/成交量/成交额/换手率/市盈率动态/市净率/总市值/流通市值/60 日涨跌幅/年初至今涨跌幅）。
2. **financial**：自动推算最近 4 个财报期（如 20260930/20260630/20260331/20251231），逐期 `ak.stock_yjbb_em(date=…)` 抓 ROE/EPS/营收/净利/毛利率，最新报告期优先。
3. **save**：清洗 NaN/Inf/'-'→None、代码统一 6 位，分批 upsert（**≤400 条/批**，规避 asyncpg 32767 参数上限），`ON CONFLICT (code)` 仅更新行情与财报字段，`coalesce` 保留 controller/state_owned/in_msci。

> 避坑：① PostgreSQL `ORDER BY ... DESC` 默认 NULL 排最前，按市值/PE/ROE 降序时需显式 `.nulls_last()`；② `stock_yjbb_em` 的半年报/年报会额外包含约 5700 只新三板/北交所挂牌公司（代码 43x/83x/87x），合并时以 spot 全市场代码为准自然剔除。

### 5.5 收盘一键更新（`run_daily_sync`，四合一流水线）

单例状态 `daily_state`，`current_step` 1~4，`percent` 按权重累加（15/35/30/20）。单步报错记录 warning 但不中断后续步骤：

1. **Step 1 (15%)**：腾讯行情刷新持仓现价，重算 positions 的 market_value / profit_amount / profit_ratio。
2. **Step 2 (35%)**：`akshare_sync.run_akshare_sync()` 同步全市场行情与季报业绩。
3. **Step 3 (30%)**：`fund_flow.run_fund_flow_sync()` 同步持仓/自选主力资金流。
4. **Step 4 (20%)**：`fund_flow.run_lhb_sync()` 同步龙虎榜汇总与席位明细。

> 前端 App.vue 侧边栏「⚡ 收盘一键更新」轮询 `/api/system/daily-sync/progress`，完成后经 `provide/inject` 的 `appRefreshSignal` 触发持仓/全市场/资金异动三页自动重载。

### 5.6 缓存策略（进程内存，等价原 JSON 缓存文件）

| 数据 | TTL |
|---|---|
| 行情（push2） | 30 秒 |
| ROE | 7 天 |
| 实际控制人（同花顺/东财） | 30 天 |
| 央企名录 | 5 天 |
| 行业分类（EM2016） | 2 天 |

> 已知限制：单实例部署下这些缓存与 Job 状态均在内存中，进程重启即失效（`state_owned` 的 `sourceStats` 也会丢失，`updatedAt` 有 `max(updated_at)` 兜底）。

### 5.7 数据源主备双引擎（`app/adapters/`）

同花顺官方 API（`fuyao.aicubes.cn`）为主通道，东财/腾讯/AKShare 免费接口为备用通道，实现「主备双活，主通道优先 + 异常自动降级」。

- **配置**（`.env`）：`THS_API_KEY`、`THS_BASE_URL`（默认 `https://fuyao.aicubes.cn`）、`DATA_SOURCE_MODE`（`ths_first` 默认 / `free_only`）。
- **主通道 `ths_client.py`**：行情快照 `prices/snapshot` + 估值快照 `valuations/snapshot`（pe_ttm/pb_mrq/name）+ 财务指标 `financials/indicators`（ROE/营收同比/净利同比/毛利率）+ 利润表 `income-statements`（basic_eps）+ 基金快照 `fund/market/snapshot`（ETF/LOF）。代码进出自动转换后缀（6/5→.SH，0/3/15/16/18→.SZ，4/8/92→.BJ），自动区分 A 股与场内基金。错误码 `2001/2003/3001/3004/4001/429/5001/5002/5003` 触发降级，`4001/5002/5003/429` 指数退避重试 3 次。
- **热备 `free_client.py`**：封装原东财 push2/腾讯 qt.gtimg/东财 datacenter 财务；单股财务用东财 `RPT_F10_FINANCE_MAINFINADATA` 单股接口（EPSJB/ROEJQ/TOTALOPERATEREVETZ/PARENTNETPROFITTZ/XSMLL），替代原先「为 1 只股票拉全市场 5500 行」的慢逻辑。
- **路由器 `data_router.py`**：暴露 `get_quotes / get_stock_name / get_single_financial_indicators`，按 `DATA_SOURCE_MODE` 执行主通道优先 + 降级，返回项标注 `source`（`ths` / `free_fallback`）。
- **统一回写 `writeback.py`**：`/api/quote` 与 `/api/stocks/refresh-single/{code}` 获取有效行情（price>0）与 PE/PB/ROE/EPS 后统一回写 stocks 表，name 用 `nullif+coalesce` 兜底（新代码 INSERT 用 code、已有代码保留库内 name），彻底消除持仓/全市场/选股策略三页数据不一致。

> 避坑：① 财务指标接口实际返回的 `index_id` 带 `calculate_`/`parent_holder_` 前缀（与文档示例略异），需用包含匹配；② 估值快照无市值、行情快照无 name/PE，需跨接口拼接；③ 基金快照不含名称，ETF 名称由 stocks 表已有值兜底。

---

## 6. 本地启动命令

```bash
# 0) 首次准备（已做过可跳过）
cd stock-service
cp .env.example .env        # 填入 NAS 真实 IP 与 POSTGRES_PASSWORD
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 1) 建表 + 迁移（幂等，可重复执行）
python init_db.py
python migrate_json.py      # 可选 --dry-run 先看清洗统计
python migrate_stocks.py    # stocks 表字段平滑扩展（AKShare 行情/财报字段）
python migrate_strategy.py  # strategy_config 表建表（选股策略配置）
python migrate_fund_flow.py # 资金流/龙虎榜建表（stock_fund_flow_daily + stock_lhb_daily）
python migrate_system_meta.py # system_meta 表建表（系统元信息，持久化收盘更新完成时间）
python migrate_kline.py     # stock_kline_daily 表建表（全市场复权日线）
python migrate_financials.py # 财报三表/笔记表建表（stock_financial_statements + stock_analysis_notes）
python migrate_rebalance.py # rebalance_plans 表建表（调仓工单）

# 2) 启动后端（端口 8000）
uvicorn app.main:app --reload --port 8000
# API 文档：http://127.0.0.1:8000/docs   健康检查：/api/v1/health

# 3) 启动前端（另开终端，端口 3000）
cd ../stock-web
pnpm install                # 首次
pnpm dev
# 打开 http://localhost:3000

# 后端换端口时前端代理可覆盖：
VITE_BACKEND_ORIGIN=http://127.0.0.1:9000 pnpm dev
```

> 提示：前端依赖后端已在 8000 端口运行；若 8000 被旧进程占用需先重启该进程。

---

## 7. 数据来源与迁移

`stock-web/data/` 下的历史 JSON（`state-owned.json`、`msci.json`、`portfolio.json`、`watchlist.json`、`grid-strategies.json`、`closed-positions.json`）已通过 `migrate_json.py` 清洗并入 PG：

- `state-owned.json` → stocks（板块/控制人/分类）
- `msci.json` → stocks(in_msci=True, msci_extra) + msci_meta
- `portfolio.json` → positions（同代码按股数加权合并成本）+ stocks 补全
- `watchlist.json` → watchlist_items
- `grid-strategies.json` → grid_strategies + grid_trade_records（trades 拆表）
- `closed-positions.json` → trade_history

迁移可重复执行（`ON CONFLICT DO UPDATE` + `coalesce` 保留已有值），不会产生重复数据。

## 8. 存储统一排查结论（2026-11-06）

全量排查 `stock-web/src/` 后确认：**前端已无任何 `localStorage`/`sessionStorage`/`indexedDB` 引用**，所有业务数据统一走 `/api/*` 落 NAS PostgreSQL：

| 侧边栏模块 | 组件 | 数据来源 |
|---|---|---|
| 持仓股票 | Positions.vue | `/api/portfolio` |
| 已清仓 | ClosedPositions.vue | `/api/closed-positions` + `/api/portfolio` |
| A股全市场 | AStockMarket.vue | `/api/stocks/*` |
| MSCI数据 | MsciData.vue | `/api/msci` |
| 国资控股 | StateOwned.vue | `/api/state-owned` |
| 选股策略 | Strategy.vue | `/api/strategy-config`（本次接入，自动保存） |
| 观察列表 | WatchList.vue | `/api/watchlist`（App 管理） |
| 网格交易 | GridTrading.vue | `/api/grid` |
| 待调仓股票 | 无组件（占位） | 无业务数据 |
| 分析报告 | 无组件（占位） | 无业务数据 |

- `stock-web/data/*.json` 与 `data/cache/*.json` 均为历史迁移源文件，`src/` 已无任何引用，仅作备份保留，可安全删除。
- 「待调仓股票」「分析报告」两个菜单目前是占位项（无组件、无数据），本次不涉及迁移。

---

## 9. 数据库主备角色互换（2026-11-12）

**背景**：飞牛 NAS 不再常开，日常运行必须脱离 NAS。

| 角色 | 位置 | 连接串 | 用途 |
|---|---|---|---|
| 主库 Primary | Mac 本地 PostgreSQL 17.11 | `127.0.0.1:5432/portfolio_db` | 日常运行 / 开发，NAS 关机 100% 可用 |
| 冷备 Backup | 飞牛 NAS PostgreSQL 17.5 | `192.168.31.122:5434/portfolio_db` | 按需开机，仅 `push-to-nas` 备份时连接 |

### 9.1 Mac 本地 PostgreSQL 部署（无 Homebrew 环境）

本机没有 Homebrew、无 `sudo` 权限、GitHub 直连超时，`brew install postgresql@17` 无法执行，改用 **micromamba + conda-forge 官方 PostgreSQL 17.11 二进制**（用户级安装，效果等价）：

```bash
# 已完成的安装步骤（重装时参考）
curl -sSL -o mm.tar.bz2 https://micro.mamba.pm/api/micromamba/osx-arm64/latest
tar -xjf mm.tar.bz2 bin/micromamba && mv bin/micromamba ~/.local/bin/
export MAMBA_ROOT_PREFIX=$HOME/.local/mamba
~/.local/bin/micromamba create -y -p $HOME/.local/pg17 -c conda-forge "postgresql=17"
~/.local/pg17/bin/initdb -D ~/.local/pgdata17 -U postgres --encoding=UTF8 --locale=C -A trust
```

- 二进制：`~/.local/pg17/bin`（psql / pg_ctl / pg_dump 等）
- 数据目录：`~/.local/pgdata17`，日志 `~/.local/pgdata17/server.log`
- 认证：`pg_hba.conf` 对 `127.0.0.1` / `::1` 为 **trust 免密**；`postgres` 角色密码同时设为 `mypg123456`，两种方式都能连
- 自启：**刻意不设任何系统级自启**（不改 `~/.zshrc`、不装 launchd），需要时手动拉起，不用时彻底停掉
- 启停脚本：`./dev.sh {start|stop|restart|status}`（项目根，一键管「库 + 后端 + 前端」）；只操作数据库时用 `cd stock-service && ./pg_local.sh {start|stop|status}`

### 9.2 配置分层（.env）

```dotenv
# 主库：日常默认
DATABASE_URL=postgresql+asyncpg://postgres:mypg123456@127.0.0.1:5432/portfolio_db
# 冷备：NAS 开机时才用
NAS_DATABASE_URL=postgresql+asyncpg://postgres:mypg123456@192.168.31.122:5434/portfolio_db
NAS_DB_CONNECT_TIMEOUT=3
```

- `app/core/config.py`：`ASYNC_DATABASE_URL` 由 `DATABASE_URL` 派生；配置后自动回填 `POSTGRES_HOST/PORT/DB`，`/health` 展示的永远是真实主库。
- `POSTGRES_*` 仅作兜底（`DATABASE_URL` 留空时生效），`.env.example` 中已注释。
- **运行期除 `db_sync.py` 外无任何 NAS 依赖**（全仓已核查无硬编码 NAS IP）。

### 9.3 db_sync.py 用法

```bash
cd stock-service

python db_sync.py pull-from-nas   # NAS -> Mac：首次迁移 / 从冷备恢复
python db_sync.py push-to-nas     # Mac -> NAS：NAS 开机后推送备份（离线自动安全退出）
python db_sync.py status          # 双库逐表行数对比（NAS 离线自动降级）
python db_sync.py pull-from-nas --no-verify   # 跳过内容指纹校验，更快
```

实现要点：

1. **表结构对齐**：以 `Base.metadata` 为准 `create_all`，再逐列 `ALTER TABLE ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`，自动吃到所有 `migrate_*.py` 里的最新字段与索引，新库无需跑 migrate。
2. **数据搬运**：asyncpg `COPY ... TO STDOUT`（CSV）→ 临时文件 → `COPY ... FROM STDIN`，**单事务**内 `TRUNCATE ... RESTART IDENTITY CASCADE` + 逐表搬运，失败整单回滚，42k 行约 1 秒。
3. **序列校准**：对每个 SERIAL / IDENTITY 列执行 `setval(pg_get_serial_sequence(...), MAX(col))`，Mac 新增记录不会主键冲突。
4. **结果校验**：逐表行数 + 内容指纹（`ROW(显式列序)::text` 的 md5 聚合，规避两端物理列序不同导致的误报）。
5. **离线处理**：NAS 探活 2~3 秒超时，`status` 打印「NAS 当前离线（Mac 本地主库独立运行中）」，`push-to-nas` 打印「NAS 当前离线，已安全退出」并返回码 1，主库不受影响。

### 9.4 首次迁移结果（2026-11-12）

- 迁移 15 张表 / 42,587 行，耗时 0.87 s，行数与内容指纹 **100% 一致**
- `push-to-nas` 反向验证通过（1.62 s），NAS 端序列同样校准
- 后端切库后 `/health` 返回 `host=127.0.0.1 port=5432 latency≈3ms`
- 前端实机（headless Chrome）逐页验证：持仓股票(25 行)、已清仓、A股全市场(5940 只)、MSCI数据、国资控股、资金与异动、选股策略、待调仓股票(21 信号)、观察列表、网格交易、分析报告(健康分 76)、详情抽屉 K 线全部正常
- 已知外部限制：`/api/fund-flow/market-rank` 依赖东方财富 push2，触发 IP 限流时前端显示「读取排行榜失败：HTTP 500」，与数据库无关，约 5 分钟后自恢复

### 9.5 日常操作约定

| 场景 | 操作 |
|---|---|
| 每天开工（一条命令起全部） | `cd my-finance-system && ./dev.sh start`（数据库 → 后端 8000 → 前端 3000） |
| 收工 / 不看盘（0 残留） | `./dev.sh stop`（后端 + 前端 + 数据库全部停止，实测残留进程 0 个） |
| 改完代码要生效 | `./dev.sh restart`（后端未开 `--reload`，不常驻扫描 .py） |
| 看三者状态 | `./dev.sh status` |
| NAS 开机后想备份 | `cd stock-service && python db_sync.py push-to-nas` |
| 换了新 Mac / 重装 | `python db_sync.py pull-from-nas`（自动建表 + 校准序列） |
| 只想看差异 | `python db_sync.py status` |
| 本地库异常 | `cd stock-service && ./pg_local.sh status`，日志 `~/.local/pgdata17/server.log` |


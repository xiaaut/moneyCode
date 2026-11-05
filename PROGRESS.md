# 项目进度与技术手册（PROGRESS.md）

> 生成时间：2026-11-06 · 当前状态：**前后端已全面切换到 FastAPI + NAS PostgreSQL**

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
│   └── app/
│       ├── main.py                   # 入口：lifespan + CORS + 路由挂载 + 统一异常
│       ├── core/config.py            # pydantic-settings 配置（NAS 连接、连接池）
│       ├── db/
│       │   ├── base.py               # DeclarativeBase + 约束命名规范
│       │   └── session.py            # async engine + AsyncSession + get_db
│       ├── models/                   # SQLAlchemy 2.0 模型（Mapped / mapped_column）
│       │   ├── stock.py              #   stocks
│       │   ├── portfolio.py          #   positions / trade_history
│       │   ├── grid.py               #   grid_strategies / grid_trade_records
│       │   ├── watchlist.py          #   watchlist_items
│       │   └── msci_meta.py          #   msci_meta（单例）
│       ├── schemas/                  # Pydantic 模型（camelCase 对齐前端）
│       │   ├── common.py             #   CamelModel 基类 + 脏值转换工具
│       │   ├── portfolio.py / grid.py / stocks.py / quote.py
│       ├── services/
│       │   ├── quotes.py             # 东财 push2 行情 + 腾讯行情 + ROE（TTL 内存缓存）
│       │   ├── ths.py                # 同花顺/东财实控人抓取 + 国企关键字分类 + 央企名录
│       │   ├── msci_parse.py         # Excel(TXT/CSV) 解析（openpyxl + xlrd）
│       │   └── jobs.py               # 后台任务（国企扫描 / MSCI 分析）+ JobState
│       └── api/v1/
│           ├── router.py             # 路由聚合
│           └── endpoints/
│               ├── portfolio.py      # 持仓 / 自选 / 清仓
│               ├── stocks.py         # 股票池筛选
│               ├── grid.py           # 网格策略
│               ├── quote.py          # 行情 / ROE / 名称
│               ├── controller.py     # 实际控制人
│               ├── msci.py           # MSCI 成份股
│               └── state_owned.py    # 国资扫描
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
            └── StateOwned.vue        # 国资控股
```

---

## 3. NAS 数据库表结构（`portfolio_db`，7 张表）

### 3.1 stocks（3597 行）— 股票主数据（多来源合并统一视图）

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
| is_watchlist | bool | 是否自选 |
| **in_msci** | bool | 是否 MSCI 成份股（index） |
| **state_owned** | bool | 是否国企/政府控股（index） |
| extra | jsonb | 未结构化指标（source/note/穿透明细/controller_detail…） |
| msci_extra | jsonb | MSCI 导入行原始列值（如「权重」「序号」） |
| created_at / updated_at | timestamptz | 时间戳 |

### 3.2 positions（22 行）— 当前持仓

`id`(bigint PK) · `code`(varchar **unique**+index) · `name` · `shares`(int) · `cost_price` · `current_price` · `market_value` · `profit_amount` · `profit_ratio` · `sector` · `position_ratio` · `extra`(jsonb) · 时间戳。
> 市值/盈亏/占比为按现价的快照值；同一代码多条持仓在迁移时按股数加权合并成本。

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

---

## 4. FastAPI 接口路由清单

路由同时挂载在 **`/api/v1`**（进 OpenAPI 文档）与 **`/api`**（兼容前端历史路径，`include_in_schema=False`），两条入口路径内容完全一致，Vite 代理原样透传、无需 rewrite。

### 4.1 持仓 / 自选 / 清仓（portfolio.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/portfolio` | 持仓列表（纯数组，camelCase） |
| POST | `/api/portfolio` | 整数组覆盖持仓（按 code upsert + 删除多余） |
| POST | `/api/portfolio/positions` | 新增/修改单条持仓 |
| PUT | `/api/portfolio/positions/{code}` | 按代码修改持仓 |
| DELETE | `/api/portfolio/{code}` | 删除持仓（`archive=true` 时写入 trade_history） |
| GET | `/api/watchlist` | 观察列表 |
| POST | `/api/watchlist` | 整数组覆盖观察列表 |
| POST | `/api/watchlist/items` | 新增单条观察项 |
| DELETE | `/api/watchlist/{item_id}` | 删除观察项 |
| GET | `/api/closed-positions` | 清仓记录列表 |
| POST | `/api/closed-positions` | 整数组覆盖清仓记录（缺字段服务端兜底推导盈亏/持有天数） |

### 4.2 股票池筛选（stocks.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/stocks` | 多条件筛选：`keyword/industry/controller_category/board/state_owned/in_msci/watchlist_only/pe_min/pe_max/roe_min/roe_max/sort/order/page/page_size` |
| GET | `/api/stocks/filters` | 筛选项下拉数据（行业/控制人类型/板块） |
| GET | `/api/stocks/{code}` | 单只详情 |

### 4.3 网格（grid.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/grid` | 策略列表（trades 内嵌，camelCase） |
| POST | `/api/grid` | 对象=按 id upsert；数组=整体覆盖；自动重算 step/摊薄成本/套利 |
| POST | `/api/grid/{strategy_id}/trades` | 档位成交打卡（重算 current_shares/diluted_cost/realized_profit） |
| DELETE | `/api/grid/{strategy_id}` | 删除策略（级联删成交） |

### 4.4 行情（quote.py）

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

### 4.8 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 服务信息 |
| GET | `/api/v1/health` | 健康检查（真实 `SELECT 1`，DB 不可用时 503） |

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

### 5.4 缓存策略（进程内存，等价原 JSON 缓存文件）

| 数据 | TTL |
|---|---|
| 行情（push2） | 30 秒 |
| ROE | 7 天 |
| 实际控制人（同花顺/东财） | 30 天 |
| 央企名录 | 5 天 |
| 行业分类（EM2016） | 2 天 |

> 已知限制：单实例部署下这些缓存与 Job 状态均在内存中，进程重启即失效（`state_owned` 的 `sourceStats` 也会丢失，`updatedAt` 有 `max(updated_at)` 兜底）。

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

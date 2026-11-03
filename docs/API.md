# 接口技术字典（API Dictionary）

> 代码位置：`vite.config.ts` → `portfolioApiPlugin()`（第 1571–1974 行）及上游抓取函数（第 71–1569 行）。
> 生成时间：2026-11-03。改动 `vite.config.ts` 的上游地址、字段、缓存或路由时必须同步本文件。
> 使用说明见 [README.md](../README.md)，进度见 [PROGRESS.md](PROGRESS.md)。

**一句话架构**：没有独立后端。Vite 插件在 `configureServer` / `configurePreviewServer` 里挂一段 Connect 中间件，同时负责静态页面和 `/api/*`；数据落 `data/*.json`，上游抓取结果落 `data/cache/*.json`。

> 重要边界：`/api/*` 只在 `vite dev` 与 `vite preview` 下存在。`dist` 交给静态服务器后，这些路由全部失效。
> 未匹配的 `/api/*` 会 `return next()`，最终由 Vite 的 SPA fallback 返回 `index.html`（HTTP 200 + HTML），排查时不要把它当成「接口返回空」。

---

# 一、外部上游数据源字典

| # | 数据源 | 域名 | 编码 | 用途 | 缓存 |
| --- | --- | --- | --- | --- | --- |
| 1 | 东方财富 push2 行情 | `push2.eastmoney.com` | UTF-8 JSON | 持仓/网格/自选的现价与估值 | `cache/quote.json`，30s |
| 2 | 东方财富数据中心 RPT_F10 | `datacenter-web.eastmoney.com` | UTF-8 JSON | 全市场公司概况、行业分类、批量 ROE | `cache/org-info.json` 2 天；ROE 并入 `roe.json` 7 天 |
| 2b | 东方财富 F10 网页接口 | `emweb.securities.eastmoney.com` | UTF-8 JSON | 单只 ROE、实际控制人兜底 | `cache/roe.json`、`cache/controller.json` |
| 3 | 腾讯行情 | `qt.gtimg.cn` | **GBK** 文本 | 批量行情校验、MSCI、股票名称 | 无落盘缓存 |
| 4 | 国务院国资委央企名录 | `www.sasac.gov.cn` | UTF-8 HTML | 央企集团 → 国务院国资委 | `cache/central-soe.json`，5 天 |
| 5 | 同花顺 F10 公司概况 | `basic.10jqka.com.cn` | **GBK** HTML | 最终控制人穿透 | `cache/controller.json`，30 天 |

## 1. 东方财富 push2 行情

### 1.1 请求模板

```http
GET https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&fields=<QUOTE_FIELDS>&secids=<secid,secid,...>
```

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `fltt` | `2` | 返回浮点价格（不再除以 100） |
| `invt` | `2` | 固定值 |
| `fields` | `f2,f3,f4,f5,f6,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f100,f115` | 常量 `QUOTE_FIELDS`，多取字段不影响解析，少取会导致映射为空 |
| `secids` | `1.600519,0.000725` | `市场.6位代码`；`1`=沪市（6/5/9 开头），`0`=深市与北交所（含 43/83/87/92） |

`secid` 生成规则见 `secidOf()`：`/^(43|83|87|92)/ → 0.xxxxxx`，`/^[659]/ → 1.xxxxxx`，其余 `0.xxxxxx`。

### 1.2 请求头

| Header | 值 |
| --- | --- |
| `User-Agent` | `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36` |
| `Accept` | `application/json, text/plain, */*` |
| `Accept-Language` | `zh-CN,zh;q=0.9` |
| `Referer` | `https://quote.eastmoney.com/`（每次调用单独覆盖） |

`UPSTREAM_HEADERS` 是全局默认头，`fetchBufferByHttps(url, extraHeaders)` 用对象展开覆盖/追加；**未设置 `Host`**，由 Node `https` 模块按 URL 自动生成。

### 1.3 原始字段 → 项目字段映射

| 原始字段 | 含义 | 项目字段 | 消费方 |
| --- | --- | --- | --- |
| `f2` | 最新价（元） | `price` / `currentPrice` | 持仓、网格、MSCI |
| `f3` | 涨跌幅（%） | `changePct` | 持仓、网格 |
| `f4` | 涨跌额（元） | `changeAmount` | MSCI 快照 |
| `f5` | 成交量（手） | `volume` | MSCI 快照 |
| `f6` | 成交额（元） | `amount` | MSCI 快照 |
| `f8` | 换手率（%） | `turnover` | MSCI 快照 |
| `f9` | 市盈率（动态） | `peDynamic` | MSCI 快照 |
| `f10` | 量比 | 未落库 | — |
| `f12` | 6 位代码 | `code` | 全部 |
| `f13` | 市场标记（0 深/北，1 沪） | `secid` 拼接 | 缓存键 |
| `f14` | 证券名称 | `name` | 持仓、MSCI |
| `f15` | 最高价 | 未落库 | — |
| `f16` | 最低价 | 未落库 | — |
| `f17` | 今开 | 未落库 | — |
| `f18` | 昨收 | 未落库 | — |
| `f20` | 总市值（元） | `totalMarketValue` | MSCI 快照 |
| `f21` | 流通市值（元） | `floatMarketValue` | MSCI 快照 |
| `f23` | 市净率 PB | `row.pb` | 持仓表、MSCI |
| `f100` | 所属行业（EM2016 板块名） | `industry` | MSCI 快照 |
| `f115` | 市盈率 TTM | `row.pe` / `peTtm` | 持仓表、MSCI |

> 注意：持仓表展示的 `PE` 取的是 `f115`（TTM），不是 `f9`（动态）；`f9` 只在 MSCI 快照里作为 `peDynamic` 保留。

### 1.4 限流、缓存与降级

| 项 | 规则 |
| --- | --- |
| 缓存文件 | `data/cache/quote.json`，结构 `{ "1.600519": { value: {...}, ts: 1767... } }` |
| 缓存 Key | `secid`（`市场.代码`） |
| TTL | `QUOTE_TTL_MS = 30_000`（30 秒） |
| 节流 | `QUOTE_MIN_INTERVAL_MS = 1000`，`QUOTE_MAX_PER_MINUTE = 60` |
| 批量分片 | `chunkArray(missing, 100)`，一次最多 100 个 secid |
| 超时 | 12 秒（`fetchBufferByHttps` 默认值） |
| 上游报错 | 逐片 `catch`：把该片所有 `secid` 的旧缓存塞回结果并置 `stale=true`；一个都没有则 `/api/quote` 返回 **502** |
| 落盘 | 每片成功后 `persistCache('quote')`，写入串行化，避免并发覆盖 |

## 2. 东方财富数据中心 RPT_F10

### 2.1 全市场公司概况（含实际控制人）

```http
GET https://datacenter-web.eastmoney.com/api/data/v1/get
    ?reportName=RPT_F10_BASIC_ORGINFO
    &columns=SECURITY_CODE,SECURITY_NAME_ABBR,SECURITY_TYPE,ACTUAL_HOLDER
    &pageNumber=<n>&pageSize=500
Referer: https://data.eastmoney.com/
```

- 翻页：读 `result.pages`，`do…while (page <= pages)`，每页 500 条；失败直接抛出，由 Job 置 `error`。
- 过滤：`SECURITY_TYPE` 必须属于 `上交所主板A股 / 深交所主板A股 / 北京证券交易所A股`；名称含 `ST` 或 `退` 的剔除；代码必须 6 位且去重。
- 字段映射：

| 原始字段 | 含义 | 项目字段 |
| --- | --- | --- |
| `SECURITY_CODE` | 6 位代码 | `code` |
| `SECURITY_NAME_ABBR` | 简称 | `name` |
| `SECURITY_TYPE` | 板块类型 | `board`（`沪主板/深主板/北交所`） |
| `ACTUAL_HOLDER` | 实际控制人文本 | `actualHolder`（走 `classifyBulkHolder` 分类） |

### 2.2 全市场行业分类（EM2016）

```http
GET .../api/data/v1/get?reportName=RPT_F10_BASIC_ORGINFO
    &columns=SECURITY_CODE,SECURITY_NAME_ABBR,EM2016
    &pageNumber=<n>&pageSize=500
Referer: https://data.eastmoney.com/
```

| 原始字段 | 项目字段 | 说明 |
| --- | --- | --- |
| `SECURITY_CODE` | `map[code]` 键 | 只保留 `/^\d{6}$/` |
| `SECURITY_NAME_ABBR` | `map[code].name` | 名称兜底 |
| `EM2016` | `map[code].industry` | 形如 `食品饮料-饮料-白酒` |

缓存：`data/cache/org-info.json`，结构 `{ fetchedAt, map }`，TTL **2 天**（`ORG_INFO_CACHE_TTL_MS`）。内存里还有一层 `orgInfoCache`，同进程内不重复读盘。

### 2.3 批量 ROE（主要财务指标）

```http
GET .../api/data/v1/get?reportName=RPT_F10_FINANCE_MAINFINADATA
    &columns=SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,REPORT_DATE_NAME,ROEJQ
    &filter=(SECURITY_CODE in ("600519","000725",...))
    &sortColumns=REPORT_DATE&sortTypes=-1
    &pageNumber=<n>&pageSize=500
Referer: https://data.eastmoney.com/
```

- `filter` 拼接：`(SECURITY_CODE in ("600519","000725"))`，整体 `encodeURIComponent` 后拼进 URL。
- 分批：`chunkArray(codes, 80)`，每批最多翻 4 页（`page <= pages && page <= 4`）。
- 取数：按 `REPORT_DATE` 倒序，`result.has(code)` 去重 → **每只股票第一条即最新报告期**。
- 字段：

| 原始字段 | 含义 | 项目字段 |
| --- | --- | --- |
| `SECURITY_CODE` | 代码 | `code` |
| `ROEJQ` | 加权 ROE（%） | `roe` |
| `REPORT_DATE_NAME` | 报告期名称（如 `2025-09-30`） | `roeReport` |

- 失败：单批 `catch` 后 `console.warn`，继续下一批；缺失的代码再走单只 F10 兜底（见 2.4）。

### 2.4 东财 F10 网页接口（兜底）

| 用途 | URL | 返回路径 | 限流 |
| --- | --- | --- | --- |
| 单只 ROE | `https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=0&code=SH600519` | `data[0].ROEJQ` | `roe` 桶 500ms / 90 每分钟 |
| 实际控制人兜底 | `https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code=SH600519` | `sjkzr[0].HOLDER_NAME`、`HOLD_RATIO` | `em` 桶 400ms / 60 每分钟 |

两者 `Referer` 都是 `https://emweb.securities.eastmoney.com/`。

## 3. 腾讯行情 qt.gtimg.cn

### 3.1 请求模板

```http
GET http://qt.gtimg.cn/q=sh600519,sz000725,bj430047
Referer: http://gu.qq.com/
```

- 前缀：`/^(43|83|87|92)/ → bj`，`/^[659]/ → sh`，其余 `sz`。
- **必须用 GBK 解码**：`decodeGbk(buffer)`（复用一个 `TextDecoder('gbk')` 实例）。
- 超时 15 秒；批量 60 只/次；每次之间 `sleep(120)`。
- 响应为多行 `v_sh600519="1~贵州茅台~600519~1392.05~...";`，正则 `v_(\w+)="([^"]*)"` 逐行提取，`match[1].slice(2)` 得 6 位代码。

### 3.2 `~` 分隔数组下标映射

| 下标 | 含义 | 项目字段 | 备注 |
| --- | --- | --- | --- |
| 0 | 市场标识 | — | 未使用 |
| 1 | 证券名称 | `name` | 名称为空则整行跳过 |
| 2 | 6 位代码 | `code`（来自 `v_` 前缀） | — |
| 3 | 当前价 | `price` | `>0` 才采用 |
| 4 | 昨收 | `prevClose` | 用于算涨跌幅 |
| 32 | 涨跌幅（%） | — | 源码未取，改用 `(price-prevClose)/prevClose*100` 自行计算 |
| 38 | 换手率（%） | `turnover` | — |
| 39 | 市盈率 TTM | `peTtm` | — |
| 45 | 总市值（亿元） | `marketCapYi` | — |
| 46 | 市净率 | `pb` | `>0` 才采用 |

> 其余下标（买卖五档、成交量、成交额等）未解析。字段个数随品种变化，取值前都做了 `Number.isFinite` 判断。

### 3.3 限流与降级

| 项 | 规则 |
| --- | --- |
| 缓存 | **无**（不落 `data/cache/`） |
| 节流 | 不走 `acquireUpstreamSlot`，只按分片 `sleep(120)` |
| 批量 | 60 只/请求 |
| 报错 | 单批 `catch` 后写 `stateOwnedJob.message = "腾讯行情请求失败：…"`，继续下一批；**调用方拿到的是空 Map，不会抛错** |

## 4. 国务院国资委央企名录

```http
GET http://www.sasac.gov.cn/n2588045/n27271785/n27271792/
Referer: http://www.sasac.gov.cn/
```

- 编码 UTF-8，超时 20 秒。
- 提取正则：`/<td[^>]*align="left"[^>]*>\s*<a[^>]*>([^<]+)<\/a>/g`。
- 别名处理：名称里的 `[xxx]` 既保留主名（去括号），也把括号内名称作为别名一并入列。
- 缓存：`data/cache/central-soe.json`，结构 `{ fetchedAt, names: string[] }`，TTL **5 天**（`CENTRAL_SOE_TTL_MS`）。
- 失败：`console.warn` 后返回 `[]`，后续「央企集团映射」整段跳过，不阻塞扫描。
- 匹配：`matchCentralSoe()` 先 `normalizeCorpName()` 去掉括号、`有限公司/集团/控股` 等后缀，再做双向 `includes` 比较。

## 5. 同花顺 F10 公司概况

```http
GET https://basic.10jqka.com.cn/600519/company.html
Referer: https://basic.10jqka.com.cn/
```

- **GBK 解码**；超时 15 秒；返回长度 < 1000 直接判为异常。
- 名称：`/<title>([\s\S]*?)<\/title>/i` → 再匹配 `/^([^<(（]+)[(（]\d{6}/`。
- 字段提取规则（`parseThsField(html, label)`）：

| 步骤 | 规则 |
| --- | --- |
| 定位 | 查找 `最终控制人：</strong>` / `实际控制人：</strong>` / `控股股东：</strong>` |
| 截窗 | 标记之后取 40,000 字符 |
| 去悬浮框 | `stripTipboxBlocks()` 用**配对 `<div>` 计数**删掉 `<div class="tipbox...">`，否则「旗下上市公司」表格里的 `<td>` 会截断错误 |
| 截单元格 | 取第一个 `</td>` 之前的内容（找不到则取前 2000 字符） |
| 清洗 | 去标签、`&nbsp;` 转空格、压缩空白 |
| 持股比例 | `/比例[：:]\s*([^）)]+)/` |
| 名称 | 去掉 `持有…` 之后的内容与首尾括号、标点 |

- 输出：`{ name, finalController: {name, ratio}, actualController: {...}, controllingHolder: {...}, sourceUrl }`。
- 缓存：`data/cache/controller.json`，Key 为带前缀代码（`SH600519`），`{value, ts}`，TTL **30 天**。
- 节流：`THS_MIN_INTERVAL_MS = 900`，`THS_MAX_PER_MINUTE = 40`。
- 降级：同花顺拿不到 `finalController` 时 → 东财 `PageAjax` 兜底；两者都失败 → `category = unknown` 并写 `note`。

---

# 二、本地中间件路由字典

## 总表

| 模块 | 路由 | 方法 | 落盘文件 | 上游函数 |
| --- | --- | --- | --- | --- |
| 行情 | `/api/quote` | GET | `cache/quote.json` | `resolveQuotes` → push2 |
| 财务 | `/api/roe` | GET | `cache/roe.json` | `resolveRoe` → 东财 F10 |
| 财务 | `/api/stock-name` | GET | 无 | `fetchTencentQuotes` |
| MSCI | `/api/msci` | GET / POST | `data/msci.json` | — |
| MSCI | `/api/msci/parse` | POST | 无（只解析） | `readSheetMatrix` / `normalizeMsciMatrix` |
| MSCI | `/api/msci/analyze` | POST | `data/msci.json` | `runMsciAnalyzeJob` |
| MSCI | `/api/msci/progress` | GET | 无 | — |
| MSCI | `/api/msci/refresh` | POST | `data/msci.json` | `enrichMsciRows` → push2 |
| 国资 | `/api/controller` | GET | `cache/controller.json` | `resolveController` → 同花顺/东财 |
| 国资 | `/api/state-owned` | GET | `data/state-owned.json` | — |
| 国资 | `/api/state-owned/progress` | GET | 无 | — |
| 国资 | `/api/state-owned/start` | POST | `data/state-owned.json`（Job 完成后） | `runStateOwnedJob` |
| 账本 | `/api/portfolio` | GET / POST | `data/portfolio.json` | — |
| 账本 | `/api/watchlist` | GET / POST | `data/watchlist.json` | — |
| 账本 | `/api/closed-positions` | GET / POST | `data/closed-positions.json` | — |
| 网格 | `/api/grid` | GET / POST | `data/grid-strategies.json` | — |

公共响应头（`sendJson`）：`Content-Type: application/json; charset=utf-8`、`Cache-Control: no-store`。
`/api/portfolio`、`/api/watchlist` 的 GET 直接回文件原文，只设 `Content-Type`。

---

## 1. 持仓与账本

### 1.1 `GET /api/portfolio`、`GET /api/watchlist`

- 入参：无。
- 正常：`200`，直接返回文件内容（数组原文，不包 `{data}`）。
- 文件不存在：自动 `mkdir -p data` 并写 `[]`，再返回 `[]`。
- 其他读错误：`500 {"message":"..."}`。
- 编码：读取后 `stripBom()` 再回传，浏览器侧可直接 `JSON.parse`。

### 1.2 `POST /api/portfolio`、`POST /api/watchlist`

```json
[
  { "id": 1, "code": "600519", "name": "贵州茅台", "quantity": 100, "costPrice": 1500 }
]
```

- 入参必须是**数组**（整体覆盖，不做字段白名单，不合并）。
- 正常：`200 {"ok":true}`
- 非数组：`400 {"message":"payload 必须是数组"}`
- 落盘：`JSON.stringify(parsed, null, 2) + "\n"`。

### 1.3 `GET /api/closed-positions`

- 正常：`200` + 数组（空文件/文件损坏/不存在都返回 `[]`，不报错）。
- 说明：这是「静默兜底」——文件损坏时页面表现为「没有记录」，排查时先看 `data/closed-positions.json` 是否是合法 JSON。

### 1.4 `POST /api/closed-positions`

```json
[
  {
    "id": 1710000000000,
    "code": "600519",
    "name": "贵州茅台",
    "quantity": 100,
    "buyPrice": 1500,
    "sellPrice": 1650,
    "fee": 15,
    "buyDate": "2026-01-05",
    "sellDate": "2026-02-10",
    "profitAmount": 14985,
    "profitRatio": 0.0999,
    "holdDays": 36,
    "note": ""
  }
]
```

- 归一化：字段逐个转类型，`code` 不匹配 `/^\d{6}$/` 的整行丢弃。
- 正常：`200 {"ok":true,"count":1}`；非数组/解析失败：`400`；其他方法：`405`。

### 1.5 `GET /api/grid`

- 正常：`200` + 策略数组。
- 文件不存在：自动创建 `[]` 后返回 `[]`。
- 文件损坏：`500 {"message":"..."}`（**不静默**，与 closed-positions 不同）。

### 1.6 `POST /api/grid`（两种语义）

**A. 数组 = 整体覆盖**

```json
[
  { "id": "grid_1710000000000", "symbol": "600519", "name": "贵州茅台", "basePrice": 1392, "upperPrice": 1550, "lowerPrice": 1250, "mode": "geometric", "gridCount": 10, "sharesPerGrid": 100, "initialShares": 500, "initialCost": 1460, "trades": [] }
]
```

**B. 单个对象 = 按 `id` upsert**（`id` 不存在则追加）

```json
{
  "id": "grid_1710000000000",
  "symbol": "600519",
  "name": "贵州茅台",
  "basePrice": 1392,
  "upperPrice": 1550,
  "lowerPrice": 1250,
  "mode": "arithmetic",
  "gridCount": 10,
  "sharesPerGrid": 100,
  "initialShares": 500,
  "initialCost": 1460,
  "commissionRate": 0.0001,
  "stampDutyRate": 0.0005,
  "freeFive": false,
  "lastPrice": 1392.05,
  "trades": [
    {
      "id": "t_1",
      "timestamp": "2026-11-02 10:30:00",
      "type": "sell",
      "price": 1420,
      "shares": 100,
      "fee": 5.71,
      "realizedProfit": 0
    }
  ]
}
```

- 两种模式都先过 `normalizeGridStrategy()`：`id` 转字符串、`gridCount` 上限 200、`sharesPerGrid` 取整、`trades` 最多 500 条、`mode` 只认 `geometric`，其余归 `arithmetic`。
- `updatedAt` 每次写入刷新；upsert 时保留原 `createdAt`。
- 正常：`200 {"ok":true,"count":1,"data":[...]}`；payload 非法：`400`；其他方法：`405`。
- 结构化字段说明见 README「网格交易」。

---

## 2. 行情与财务

### 2.1 `GET /api/quote`

| 参数 | 示例 | 说明 |
| --- | --- | --- |
| `secids` | `1.600519,0.000725` | 标准写法，必须匹配 `/^[01]\.\d{6}$/` |
| `symbol` | `600519,000725` 或 `SH600519` | 便捷写法，服务端转 secid；`SH` 或 6/5/9 开头 → `1.` |

两个参数可同时传，服务端合并去重。

```json
{
  "diff": [
    { "f2": 1392.05, "f3": 2.81, "f4": 38.05, "f12": "600519", "f13": 1, "f14": "贵州茅台", "f23": 6.42, "f115": 21.18 }
  ],
  "fetchedAt": 1793557157089,
  "stale": false,
  "message": ""
}
```

- `400`：参数格式错误。
- `502`：`{"message":"上游接口暂不可用...","diff":[]}`，表示没有一条可用（连缓存都没有）。
- 缓存命中时 `stale=false` 且 `message=""`；回退旧缓存时 `stale=true`，`message="行情接口暂不可用，已使用缓存数据（...）"`。
- `405`：非 GET。

### 2.2 `GET /api/roe`

| 参数 | 示例 |
| --- | --- |
| `codes` / `code` | `SH600519,SZ000725`（必须带 SH/SZ 前缀） |

```json
{
  "items": {
    "SH600519": { "roe": 26.31, "ts": 1793557157089, "fromCache": true },
    "SZ000725": { "roe": null, "error": "上游接口调用过于频繁，已自动暂停，请一分钟后再试" }
  },
  "fetchedAt": 1793557157089,
  "message": ""
}
```

- `400`：无参数或格式不符（正则 `/^(SH|SZ)\d{6}$/`，**不支持 BJ**）。
- `message` 只在触发每分钟上限时有值。
- 单只失败不拖垮整批：命中缓存则 `stale:true` 回退，否则 `roe:null` + `error`。

### 2.3 `GET /api/stock-name`

| 参数 | 示例 | 约束 |
| --- | --- | --- |
| `codes` | `600519,000725` | 纯 6 位代码，最多 50 个 |

```json
{
  "items": {
    "600519": { "name": "贵州茅台", "price": 1392.05, "changePct": 2.81, "peTtm": 21.18, "pb": 6.42, "marketCapYi": 17401.76, "turnover": 0.48 }
  }
}
```

- 数据全部来自腾讯行情，**无缓存**；重复调用会持续打上游。
- `400`：无有效代码。

---

## 3. MSCI 分析

### 3.1 `GET /api/msci`

```json
{
  "fileName": "msci.txt",
  "importedAt": "2026-10-20T02:10:00.000Z",
  "updatedAt": null,
  "analyzedAt": null,
  "columns": ["权重"],
  "rows": [{ "code": "600519", "name": "贵州茅台", "extra": { "权重": "1.2%" }, "quote": null }]
}
```

文件缺失/损坏 → 全部字段为空值兜底，不报错。

### 3.2 `POST /api/msci`

```json
{
  "fileName": "msci.txt",
  "importedAt": "2026-10-20T02:10:00.000Z",
  "columns": ["权重"],
  "rows": [{ "code": "600519", "name": "贵州茅台", "extra": { "权重": "1.2%" } }]
}
```

- 响应：`200 {"ok":true,"count":N}`；非 6 位代码的行被过滤。
- 注意：**整体覆盖**，且只保留 `code / name / extra / quote` 四个字段——`roe`、`industry`、`peTtm` 等分析结果会被丢弃；如果客户端没有回传 `quote`，原有行情也一并丢失，需要重新分析或刷新。

### 3.3 `POST /api/msci/parse`

```json
{ "fileName": "msci.xlsx", "dataBase64": "<base64>" }
```

- 支持 `.xlsx/.xls/.xlsm`（按扩展名或 ZIP/OLE 魔数识别）与 `.txt/.csv`；文本先试 UTF-8，出现替换字符再按 GBK 解码。
- 分隔符自动探测：`\t , ， ; ； |`，取前 10 行中「最小列数」最大的那个。
- 表头识别：首行含 6 位代码则视为无表头；否则首行为表头。
- 代码列：统计前 500 行每列命中 6 位代码次数，取最高列。
- 名称列：表头含 `名称/简称/股票名` 优先，否则取中文最多的非代码列（命中数需 ≥ `min(3, 行数)`）。
- 上限：单次最多 3000 条，超出截断并写 `warnings`。
- 正常：

```json
{ "ok": true, "fileName": "msci.xlsx", "columns": ["权重"], "rows": [{ "code": "600519", "name": "贵州茅台", "extra": { "权重": "1.2%" } }], "warnings": [], "total": 1 }
```

- `400`：无 `dataBase64`、内容为空、解析不出代码。

### 3.4 `POST /api/msci/analyze`

- 入参：无（可选 body，未使用）。
- 正常：`200 {"started":true,"job":{...}}`
- 重入：`409 {"message":"分析任务正在进行中","job":{...}}`
- 前置条件：`data/msci.json` 已有 `rows`，否则 Job 直接进入 `error`（`还没有导入 MSCI 数据`）。

### 3.5 `GET /api/msci/progress`

```json
{
  "job": {
    "running": true,
    "phase": "roe",
    "label": "拉取 ROE",
    "done": 120,
    "total": 300,
    "message": "逐只补查 ROE 12/80",
    "startedAt": "2026-11-03T01:20:00.000Z",
    "finishedAt": null,
    "error": ""
  }
}
```

### 3.6 `POST /api/msci/refresh`

- 入参：无。
- 行为：只刷新行情（`enrichMsciRows` → push2），不动行业与 ROE。
- 正常：`200 {"ok":true,"updatedAt":"...","total":300,"message":""}`
- `400`：还没有导入数据；`500`：写入/抓取异常。

---

## 4. 国资扫描

### 4.1 `GET /api/controller`

| 参数 | 示例 | 说明 |
| --- | --- | --- |
| `codes` | `SH600519,SZ000725` | 支持 `SH/SZ/BJ` 前缀 |
| `refresh` | `1` | 跳过 30 天缓存强制重抓 |

```json
{
  "items": {
    "SH600519": {
      "code": "SH600519",
      "name": "贵州茅台",
      "finalController": "贵州省人民政府国有资产监督管理委员会",
      "finalControllerRatio": "90%",
      "actualController": "贵州省国资委",
      "controllingHolder": "中国贵州茅台酒厂（集团）有限责任公司",
      "eastmoneyController": "",
      "category": "sasac-local",
      "categoryLabel": "地方国资委",
      "source": "同花顺 F10·公司资料",
      "sourceUrl": "https://basic.10jqka.com.cn/600519/company.html",
      "note": "",
      "fetchedAt": "2026-11-03T01:20:00.000Z",
      "fromCache": true
    }
  },
  "message": ""
}
```

- `400`：格式错误；`405`：非 GET。
- 单只异常不返回 5xx：包成 `{category:"unknown", note:"抓取失败"}`。

### 4.2 `GET /api/state-owned`

```json
{
  "updatedAt": "2026-10-22T17:51:31.442Z",
  "total": 3480,
  "counts": { "sasac-central": 355, "sasac-local": 641, "person": 2003 },
  "sourceStats": { "orgInfoRows": 24817, "afterBoardAndFilter": 3485, "liveRows": 3480, "refinedRows": 139, "centralSoeNames": 102, "centralMatched": 204 },
  "rows": [{ "code": "000001", "name": "平安银行", "board": "深主板", "category": "person", "categoryLabel": "非国资（自然人）", "actualHolder": "中国平安保险（集团）股份有限公司", "price": 11.2, "changePct": 0.5 }],
  "job": { "running": false, "phase": "done", "label": "完成", "done": 3480, "total": 3480 }
}
```

文件缺失/损坏 → `{updatedAt:null,total:0,counts:{},sourceStats:{},rows:[]}`。

### 4.3 `GET /api/state-owned/progress`

`{"job":{...}}`，字段同 MSCI Job。

### 4.4 `POST /api/state-owned/start`

```json
{ "force": false, "limit": 200 }
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `force` | boolean | 为 `true` 时忽略 30 天控制人缓存，重新穿透（约 5–8 分钟） |
| `limit` | number | 只扫描前 N 只，调试用；不传或 ≤0 表示全量 |

- 正常：`200 {"started":true,"force":false,"limit":null,"job":{...}}`
- 重入：`409 {"message":"刷新任务正在进行中，请等待完成","job":{...}}`
- body 解析失败不报错，按 `{}` 处理（即 `force=false`、全量）。

---

# 三、异步作业与公共底层机制

## 3.1 Job 状态机

两个 Job 结构一致，都是**模块内存变量**（不是类，也不是数据库状态），进程重启即丢失，多进程/多实例之间不共享。

```ts
type JobState = {
  running: boolean;
  phase: string;        // idle | universe | industry | quotes | controllers | roe | save | done | error
  label: string;        // 给人看的中文阶段名
  done: number;
  total: number;
  message: string;
  startedAt: string | null;
  finishedAt: string | null;
  error: string;
};
```

**MSCI 分析 Job**

| 阶段 | phase | 做什么 |
| --- | --- | --- |
| 1 | `industry` | 读 `msci.json`，加载全市场 EM2016 行业映射 |
| 2 | `quotes` | 腾讯行情批量取 PE(TTM)/现价/市值 |
| 3 | `roe` | 东财批量 ROE（80 只/批），缺失的逐只 F10 兜底 |
| 4 | `save` | 合并行业/行情/ROE 写回 `msci.json` |
| 5 | `done` / `error` | 结束，`finally` 里置 `running=false`、写 `finishedAt` |

**国资扫描 Job**

| 阶段 | phase | 做什么 |
| --- | --- | --- |
| 1 | `universe` | 东财公司概况全量翻页（500/页），过滤主板/北交所、剔除 ST/退 |
| 2 | `quotes` | 腾讯行情批量校验上市状态并取现价 |
| 3 | `controllers` | 先用国资委央企名录映射；剩余国有法人逐只穿透同花顺 |
| 4 | `save` | 分类汇总、写 `state-owned.json` |
| 5 | `done` / `error` | 结束 |

**启动方式**：路由里 `void runXxxJob()` 触发，**不 await**，立即返回 `{started:true}`。异常都在 Job 内部 `try/catch`，不会变成未处理的 Promise rejection。

**轮询方式**：前端每 1–2 秒 `GET /api/msci/progress` 或 `/api/state-owned/progress`，读 `job.done / job.total` 画进度条；`job.running === false` 时停表。

**防重入**：入口处判断 `if (job.running) return 409`。这是**唯一**的并发保护——没有队列、没有锁、没有任务 ID，两个浏览器标签页同时点也只有第一个生效。

## 3.2 JSON 读写防呆

| 机制 | 实现 | 覆盖范围 |
| --- | --- | --- |
| 去 BOM | `stripBom(text)` = `text.replace(/^\uFEFF/, '')` | 所有 `data/*.json` 读取 + 所有 POST body |
| 统一读取 | `readJsonFile(file)` = 读 UTF-8 → 去 BOM → `JSON.parse` | `quote/roe/controller/msci/state-owned/closed-positions/grid` |
| 安全兜底 | `readJsonFileSafe(file, fallback)` = 失败返回默认值 | `grid` 的 upsert 前读取 |
| 文件不存在 | 读侧 `ENOENT` → 建目录 + 写 `[]` 或返回默认结构 | portfolio、watchlist、grid、closed-positions |
| 写入格式 | `JSON.stringify(data, null, 2) + "\n"`，UTF-8 | 除 `state-owned.json`（单行紧凑）与缓存文件外 |
| 写串行化 | `writeQueues[name]` 链式 Promise，避免并发写坏文件 | `quote.json`、`roe.json`、`controller.json` |
| 上传大小 | `readRequestBody(req, 20MB)` 超限抛错 | 所有 POST |
| 网格数据 | `normalizeGridStrategy` / `normalizeGridTrade` 逐字段转类型、限制 500 笔、`id` 去重 | `/api/grid` |

## 3.3 已发现的实现瑕疵（未修复，记录备查）

| 问题 | 位置 | 影响 |
| --- | --- | --- |
| ROE 缓存 Key 不统一 | `runMsciAnalyzeJob` 写纯代码 `600519`；`resolveRoe` 写带前缀 `SH600519` | 同一个文件里两套 Key，MSCI 写入的缓存单只查询命中不到，只会多打一次上游，不会算错 |
| 腾讯行情错误污染国资 Job | `fetchTencentQuotes` 内部直接写 `stateOwnedJob.message` | MSCI / `/api/stock-name` 调用失败时，国资页面的进度文案也会被改 |
| `/api/stock-name` 无缓存 | 每次最多 50 只直连腾讯 | 频繁失焦触发会持续打上游 |
| 未知 `/api/*` 不返回 404 | 中间件 `return next()` | 最终落到 SPA fallback，返回 `index.html`（200 / HTML），容易误判 |
| 只支持单进程 | Job 与缓存都在模块内存 | 多实例部署时状态不共享 |

---

## 附：关键常量速查

| 常量 | 值 |
| --- | --- |
| `QUOTE_TTL_MS` | 30 秒 |
| `ROE_TTL_MS` | 7 天 |
| `CONTROLLER_TTL_MS` | 30 天 |
| `CENTRAL_SOE_TTL_MS` | 5 天 |
| `ORG_INFO_CACHE_TTL_MS` | 2 天 |
| `QUOTE_MIN_INTERVAL_MS` / `QUOTE_MAX_PER_MINUTE` | 1000ms / 60 |
| `ROE_MIN_INTERVAL_MS` / `ROE_MAX_PER_MINUTE` | 500ms / 90 |
| `THS_MIN_INTERVAL_MS` / `THS_MAX_PER_MINUTE` | 900ms / 40 |
| `EM_F10_MIN_INTERVAL_MS` / `EM_F10_MAX_PER_MINUTE` | 400ms / 60 |
| push2 分片 | 100 secid / 请求 |
| 腾讯分片 | 60 只 / 请求，片间 120ms |
| 批量 ROE 分片 | 80 只 / 批，每批最多 4 页 |
| 上传上限 | 20 MB |
| MSCI 单次导入上限 | 3000 行 |

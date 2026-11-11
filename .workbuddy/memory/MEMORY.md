# 项目长期笔记（交接摘要）

> 新对话恢复上下文：读本文件 + `PROGRESS.md`（全貌/表结构/路由）+ `.workbuddy/memory/` 最近几天日志，即可无缝接上。

## 项目定位
A 股个人投研系统（my-finance-system），Monorepo 双端：
- `stock-service/` — FastAPI 后端（Python 3.13，SQLAlchemy 2.0 async + asyncpg）
- `stock-web/` — Vue 3 + Vite + Element Plus 前端
- 数据库：NAS PostgreSQL（库名 `portfolio_db`），连接串在 `stock-service/.env`（已 gitignore）

## 数据源架构（核心约定）
主备双引擎，**严禁删除免费通道逻辑**：
- 主通道：同花顺官方 API `https://fuyao.aicubes.cn`（`THS_API_KEY` + `DATA_SOURCE_MODE=ths_first` 默认）
- 备用通道：东财 push2/datacenter、腾讯 qt.gtimg、AKShare（`DATA_SOURCE_MODE=free_only` 可切纯免费）
- 适配器层 `app/adapters/`：`ths_client.py`（主）/ `free_client.py`（备）/ `data_router.py`（路由器，异常自动降级 + source 标注）

## 已建成模块
1. 持仓/自选/清仓（portfolio，含分红除权试算）
2. 全市场股票池筛选（stocks + akshare_sync）
3. 资金流 / 龙虎榜（fund_flow）
4. 网格交易 + 历史回测（grid + grid_backtest；`stock_kline_daily` 存 5 年前复权日线）
5. 张新民财报透视排雷（financials + financial_analysis，六大雷区 + 健康分）
6. 四维调仓雷达 + 补仓沙盘（rebalance + rebalance_service）
7. 收盘一键更新（daily_sync 五合一流水线：现价→全市场→资金流→龙虎榜→K线增量+财报自愈）
8. 国企扫描 / MSCI 分析（state_owned / msci）

## 启动与迁移
- 后端：`cd stock-service && .venv/bin/uvicorn app.main:app --reload --port 8000`
- 前端：`cd stock-web && npm run dev`（3000，vite 代理到 8000）
- 迁移脚本：`stock-service/migrate_*.py`（全部幂等，可直接重跑）

## 关键避坑（务必记住）
- 本环境 Grep 工具 / `grep` 对中文 pattern 常返回空，定位代码优先用 Read + 英文关键词。
- playwright 需 `1.64.1`（1.65 在 macOS 报 libc++ 符号错误），验完记得卸载。
- 东财分红 `PRETAX_BONUS_RMB`/`IT_RATIO` 是「每 10 股」单位，换算每股须 /10。
- `positions.updated_at` 隔离铁律：仅用户主动新增/修改持仓才刷新；后台「刷新现价/收盘更新」严禁触碰（模型已去 onupdate，整数组覆盖不刷）。
- 同花顺三表字段严重不足（资产负债表仅 7 字段），张氏财报分析实际走东财 F10 完整字段自动降级补全。
- Vue3 `<script setup>` 与 options API 混用会导致 this 访问不到 setup 变量，须全部移到 script setup。

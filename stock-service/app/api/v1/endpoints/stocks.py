"""股票池筛选接口。

数据来自 NAS 的 stocks 表（state-owned.json + msci.json + 持仓 + 自选 合并后的统一视图），
替代原来前端直接读 data/*.json 的做法。

    GET /api/stocks          多条件分页筛选
    GET /api/stocks/filters  筛选项下拉数据
    GET /api/stocks/{code}   单只详情
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Stock
from app.schemas.stocks import (
    StockFilterOptions,
    StockListResponse,
    StockOut,
    StockSummary,
)
from app.services import akshare_sync

router = APIRouter(tags=["stocks"])

# 国资口径：这几类算「国企属性」
STATE_OWNED_CATEGORIES = (
    "sasac-central",
    "sasac-local",
    "state-group",
    "government",
    "state-finance",
    "collective",
)

CATEGORY_LABELS = {
    "sasac-central": "央企（国务院国资委）",
    "sasac-local": "地方国企（地方国资委）",
    "state-group": "国有集团",
    "government": "政府/事业单位",
    "state-finance": "国有金融",
    "collective": "集体企业",
    "person": "非国资（自然人）",
    "none": "无实际控制人",
    "other": "其他",
    "unknown": "未知",
}

SORTABLE = {
    "code": Stock.code,
    "name": Stock.name,
    "industry": Stock.industry,
    "pe_ttm": Stock.pe_ttm,
    "pb": Stock.pb,
    "roe": Stock.roe,
    "market_cap_yi": Stock.market_cap_yi,
    "price": Stock.price,
    "change_pct": Stock.change_pct,
    "eps_current": Stock.eps_current,
    "revenue": Stock.revenue,
    "revenue_yoy": Stock.revenue_yoy,
    "net_profit": Stock.net_profit,
    "net_profit_yoy": Stock.net_profit_yoy,
    "gross_margin": Stock.gross_margin,
    "volume": Stock.volume,
    "turnover_amount": Stock.turnover_amount,
    "turnover_rate": Stock.turnover_rate,
    "circ_mv": Stock.circ_mv,
    "change_60d": Stock.change_60d,
    "change_ytd": Stock.change_ytd,
}


def _stock_to_out(row: Stock) -> StockOut:
    def f(value: Decimal | None) -> float | None:
        return float(value) if value is not None else None

    return StockOut(
        code=row.code,
        name=row.name,
        industry=row.industry,
        board=row.board,
        controller=row.controller,
        controller_category=row.controller_category,
        pe_ttm=f(row.pe_ttm),
        pb=f(row.pb),
        roe=f(row.roe),
        eps_current=f(row.eps_current),
        eps_forecast=f(row.eps_forecast),
        price=f(row.price),
        change_pct=f(row.change_pct),
        market_cap_yi=f(row.market_cap_yi),
        volume=f(row.volume),
        turnover_amount=f(row.turnover_amount),
        turnover_rate=f(row.turnover_rate),
        circ_mv=f(row.circ_mv),
        change_60d=f(row.change_60d),
        change_ytd=f(row.change_ytd),
        revenue=f(row.revenue),
        revenue_yoy=f(row.revenue_yoy),
        net_profit=f(row.net_profit),
        net_profit_yoy=f(row.net_profit_yoy),
        gross_margin=f(row.gross_margin),
        report_period=row.report_period,
        is_watchlist=bool(row.is_watchlist),
        in_msci=bool(row.in_msci),
        state_owned=bool(row.state_owned),
    )


def _apply_filters(
    statement: Select,
    *,
    keyword: str | None,
    industry: list[str] | None,
    controller_category: list[str] | None,
    board: list[str] | None,
    state_owned: bool | None,
    in_msci: bool | None,
    watchlist_only: bool,
    pe_min: float | None,
    pe_max: float | None,
    roe_min: float | None,
    roe_max: float | None,
    mv_min: float | None,
) -> Select:
    if keyword:
        pattern = f"%{keyword.strip()}%"
        statement = statement.where(or_(Stock.code.ilike(pattern), Stock.name.ilike(pattern)))
    if industry:
        statement = statement.where(Stock.industry.in_(industry))
    if controller_category:
        statement = statement.where(Stock.controller_category.in_(controller_category))
    if board:
        statement = statement.where(Stock.board.in_(board))
    if state_owned is True:
        statement = statement.where(Stock.controller_category.in_(STATE_OWNED_CATEGORIES))
    elif state_owned is False:
        statement = statement.where(
            or_(Stock.controller_category.is_(None), Stock.controller_category.notin_(STATE_OWNED_CATEGORIES))
        )
    if in_msci is not None:
        statement = statement.where(Stock.in_msci.is_(in_msci))
    if watchlist_only:
        statement = statement.where(Stock.is_watchlist.is_(True))
    if pe_min is not None:
        statement = statement.where(Stock.pe_ttm >= Decimal(str(pe_min)))
    if pe_max is not None:
        statement = statement.where(Stock.pe_ttm <= Decimal(str(pe_max)))
    if roe_min is not None:
        statement = statement.where(Stock.roe >= Decimal(str(roe_min)))
    if roe_max is not None:
        statement = statement.where(Stock.roe <= Decimal(str(roe_max)))
    if mv_min is not None:
        statement = statement.where(Stock.market_cap_yi >= Decimal(str(mv_min)))
    return statement


@router.get("/stocks/filters", response_model=StockFilterOptions, summary="筛选项下拉数据")
async def stock_filter_options(db: AsyncSession = Depends(get_db)) -> StockFilterOptions:
    industries = (
        await db.scalars(
            select(Stock.industry)
            .where(Stock.industry.is_not(None))
            .distinct()
            .order_by(Stock.industry)
        )
    ).all()
    categories = (
        await db.scalars(
            select(Stock.controller_category)
            .where(Stock.controller_category.is_not(None))
            .distinct()
        )
    ).all()
    boards = (
        await db.scalars(
            select(Stock.board).where(Stock.board.is_not(None)).distinct().order_by(Stock.board)
        )
    ).all()
    total = await db.scalar(select(func.count()).select_from(Stock)) or 0

    return StockFilterOptions(
        industries=[item for item in industries if item],
        controller_categories=[
            {"value": item, "label": CATEGORY_LABELS.get(item, item)}
            for item in sorted(categories)
            if item
        ],
        boards=[item for item in boards if item],
        total=int(total),
    )


@router.get("/stocks", response_model=StockListResponse, summary="股票池多条件筛选（含全市场统计摘要）")
async def list_stocks(
    db: AsyncSession = Depends(get_db),
    keyword: str | None = Query(None, description="代码或名称模糊匹配"),
    industry: list[str] | None = Query(None, description="行业，可重复传"),
    controller_category: list[str] | None = Query(None, description="实控人类型，可重复传"),
    board: list[str] | None = Query(None, description="板块，可重复传"),
    state_owned: bool | None = Query(None, description="true=仅国企，false=仅非国企"),
    in_msci: bool | None = Query(None, description="true=仅 MSCI 成份股"),
    watchlist_only: bool = Query(False, description="仅自选股"),
    pe_min: float | None = Query(None, ge=-10000),
    pe_max: float | None = Query(None, le=100000),
    roe_min: float | None = Query(None, ge=-10000),
    roe_max: float | None = Query(None, le=10000),
    mv_min: float | None = Query(None, ge=0, description="总市值下限（亿元）"),
    sort: str = Query("code", description=f"排序字段：{','.join(SORTABLE)}"),
    sort_by: str | None = Query(None, description="排序字段（别名，优先于 sort）"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    sort_order: str | None = Query(None, pattern="^(asc|desc)$", description="排序方向（别名，优先于 order）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> StockListResponse:
    filters = {
        "keyword": keyword,
        "industry": industry,
        "controller_category": controller_category,
        "board": board,
        "state_owned": state_owned,
        "in_msci": in_msci,
        "watchlist_only": watchlist_only,
        "pe_min": pe_min,
        "pe_max": pe_max,
        "roe_min": roe_min,
        "roe_max": roe_max,
        "mv_min": mv_min,
    }

    count_stmt = _apply_filters(select(func.count()).select_from(Stock), **filters)
    total = int(await db.scalar(count_stmt) or 0)

    sort_field = sort_by or sort
    sort_dir = sort_order or order
    column = SORTABLE.get(sort_field, Stock.code)
    # 空值一律排在最后，避免按市值/PE/ROE 降序时 NULL 值占据榜首
    ordering = column.desc().nulls_last() if sort_dir == "desc" else column.asc().nulls_last()

    stmt = _apply_filters(select(Stock), **filters).order_by(ordering, Stock.code)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.scalars(stmt)).all()

    # 行业下拉（全量，不受分页影响）
    industries = (
        await db.scalars(
            select(Stock.industry)
            .where(Stock.industry.is_not(None), Stock.industry != "")
            .distinct()
            .order_by(Stock.industry)
        )
    ).all()

    return StockListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[_stock_to_out(row) for row in rows],
        industries=[item for item in industries if item],
        summary=await _market_summary(db),
    )


async def _market_summary(db: AsyncSession) -> StockSummary:
    """全市场统计摘要（不受筛选条件影响）。"""
    total_all, up, down, so, msci = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(Stock.change_pct > 0),
                func.count().filter(Stock.change_pct < 0),
                func.count().filter(Stock.state_owned.is_(True)),
                func.count().filter(Stock.in_msci.is_(True)),
            ).select_from(Stock)
        )
    ).one()

    last_sync_at = akshare_sync.akshare_meta.get("updatedAt")
    if not last_sync_at:
        latest = await db.scalar(
            select(func.max(Stock.updated_at)).where(Stock.volume.is_not(None))
        )
        last_sync_at = latest.isoformat() if latest else None

    return StockSummary(
        total=int(total_all or 0),
        up_count=int(up or 0),
        down_count=int(down or 0),
        flat_count=int(total_all or 0) - int(up or 0) - int(down or 0),
        state_owned_count=int(so or 0),
        msci_count=int(msci or 0),
        last_sync_at=last_sync_at,
    )


@router.post("/stocks/sync-akshare", summary="启动 AKShare 全市场同步")
async def sync_akshare(response: Response) -> dict:
    if akshare_sync.akshare_job.running:
        response.status_code = status.HTTP_409_CONFLICT
        return {
            "message": "同步任务正在进行中，请等待完成",
            "job": akshare_sync.akshare_job.model_dump(by_alias=True),
        }

    akshare_sync.launch_akshare_sync()
    return {
        "started": True,
        "job": akshare_sync.akshare_job.model_dump(by_alias=True),
    }


@router.get("/stocks/sync-progress", summary="AKShare 同步进度")
async def sync_progress() -> dict:
    return {
        "job": akshare_sync.akshare_job.model_dump(by_alias=True),
        "updatedAt": akshare_sync.akshare_meta.get("updatedAt"),
        "updated": akshare_sync.akshare_job.done,
    }


@router.get("/stocks/{code}", response_model=StockOut, summary="单只股票详情")
async def get_stock(code: str, db: AsyncSession = Depends(get_db)) -> StockOut:
    row = await db.scalar(select(Stock).where(Stock.code == code))
    if row is None:
        raise HTTPException(status_code=404, detail=f"股票不存在：{code}")
    return _stock_to_out(row)

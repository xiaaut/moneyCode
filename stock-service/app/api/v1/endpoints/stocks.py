"""股票池筛选接口。

数据来自 NAS 的 stocks 表（state-owned.json + msci.json + 持仓 + 自选 合并后的统一视图），
替代原来前端直接读 data/*.json 的做法。

    GET /api/stocks          多条件分页筛选
    GET /api/stocks/filters  筛选项下拉数据
    GET /api/stocks/{code}   单只详情
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Stock
from app.schemas.stocks import StockFilterOptions, StockListResponse, StockOut

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
    "pe_ttm": Stock.pe_ttm,
    "roe": Stock.roe,
    "market_cap_yi": Stock.market_cap_yi,
    "price": Stock.price,
}


def _stock_to_out(row: Stock) -> StockOut:
    return StockOut(
        code=row.code,
        name=row.name,
        industry=row.industry,
        board=row.board,
        controller=row.controller,
        controller_category=row.controller_category,
        pe_ttm=float(row.pe_ttm) if row.pe_ttm is not None else None,
        pb=float(row.pb) if row.pb is not None else None,
        roe=float(row.roe) if row.roe is not None else None,
        eps_current=float(row.eps_current) if row.eps_current is not None else None,
        eps_forecast=float(row.eps_forecast) if row.eps_forecast is not None else None,
        price=float(row.price) if row.price is not None else None,
        change_pct=float(row.change_pct) if row.change_pct is not None else None,
        market_cap_yi=float(row.market_cap_yi) if row.market_cap_yi is not None else None,
        is_watchlist=bool(row.is_watchlist),
        in_msci=bool(row.in_msci),
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


@router.get("/stocks", response_model=StockListResponse, summary="股票池多条件筛选")
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
    sort: str = Query("code", description=f"排序字段：{','.join(SORTABLE)}"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
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
    }

    count_stmt = _apply_filters(select(func.count()).select_from(Stock), **filters)
    total = int(await db.scalar(count_stmt) or 0)

    column = SORTABLE.get(sort, Stock.code)
    ordering = column.desc() if order == "desc" else column.asc()

    stmt = _apply_filters(select(Stock), **filters).order_by(ordering, Stock.code)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.scalars(stmt)).all()

    return StockListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[_stock_to_out(row) for row in rows],
    )


@router.get("/stocks/{code}", response_model=StockOut, summary="单只股票详情")
async def get_stock(code: str, db: AsyncSession = Depends(get_db)) -> StockOut:
    row = await db.scalar(select(Stock).where(Stock.code == code))
    if row is None:
        raise HTTPException(status_code=404, detail=f"股票不存在：{code}")
    return _stock_to_out(row)

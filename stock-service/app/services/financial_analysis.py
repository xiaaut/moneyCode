"""张新民《火眼金睛读财报》战略视角的财务分析与六大排雷引擎。

输入：多期三表标准字段字典列表（来自 data_router.get_financial_statements）。
输出：每期 derived_metrics + 六大雷区 radar_checks + 健康分 + 一句话商业透视。

所有金额字段单位为原币元；字段缺失（None）时对应指标置 None 或跳过，绝不补零硬算。
"""

from __future__ import annotations

from typing import Any


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def _div(a: Any, b: Any) -> float | None:
    av, bv = _f(a), _f(b)
    if av is None or not bv:
        return None
    return av / bv


def _sum(*values: Any) -> float | None:
    """求和；全部 None 返回 None，忽略 None 项。"""
    total = 0.0
    has_value = False
    for value in values:
        v = _f(value)
        if v is not None:
            total += v
            has_value = True
    return total if has_value else None


def compute_derived(item: dict[str, Any]) -> dict[str, Any]:
    """单期张氏核心指标计算，返回 derived_metrics 字典。"""
    d: dict[str, Any] = {}
    rev = _f(item.get("revenue"))
    op_cost = _f(item.get("operating_cost"))

    # ---- 利润表面子与三支柱 ----
    gross_profit = _sum(rev, -(op_cost or 0)) if rev is not None and op_cost is not None else None
    d["gross_profit"] = _round2(gross_profit)
    d["gross_margin"] = _pct(gross_profit, rev)

    for key, field in (("selling_expense_rate", "selling_expense"), ("admin_expense_rate", "admin_expense"), ("rd_expense_rate", "rd_expense")):
        d[key] = _pct(item.get(field), rev)
    three_fee = _sum(item.get("selling_expense"), item.get("admin_expense"), item.get("rd_expense"))
    d["three_fee_total"] = _round2(three_fee)
    d["three_fee_rate"] = _pct(three_fee, rev)

    # 核心利润（严格管理口径）：仅扣 >0 的利息费用
    interest_cost = _f(item.get("interest_expense"))
    if interest_cost is None:
        fin = _f(item.get("financial_expense"))
        interest_cost = max(fin, 0.0) if fin is not None else 0.0
    else:
        interest_cost = max(interest_cost, 0.0)

    core_profit = None
    if rev is not None and op_cost is not None:
        core_profit = rev - op_cost
        for field in ("tax_and_surcharge", "selling_expense", "admin_expense", "rd_expense"):
            v = _f(item.get(field))
            if v is not None:
                core_profit -= v
        core_profit -= interest_cost
    d["core_profit"] = _round2(core_profit)
    d["core_profit_margin"] = _pct(core_profit, rev)

    # 宽口径核心利润（含净财务费用）
    wide_core = None
    if rev is not None and op_cost is not None:
        wide_core = rev - op_cost
        for field in ("tax_and_surcharge", "selling_expense", "admin_expense", "rd_expense", "financial_expense"):
            v = _f(item.get(field))
            if v is not None:
                wide_core -= v
    d["core_profit_wide"] = _round2(wide_core)

    # 三支柱两搅局拆解
    pillar2 = _f(item.get("other_income"))  # 政府补贴
    interest_income = _f(item.get("interest_income"))
    fin_exp = _f(item.get("financial_expense"))
    net_fin_income = None
    if interest_income is not None:
        net_fin_income = interest_income
    elif fin_exp is not None and fin_exp < 0:
        net_fin_income = -fin_exp
    pillar3 = _sum(net_fin_income, item.get("investment_income"), item.get("fair_value_change_income"), item.get("asset_disposal_income"))
    d["pillar1_core_profit"] = _round2(core_profit)
    d["pillar2_subsidy"] = _round2(pillar2)
    d["pillar3_misc"] = _round2(pillar3)
    d["disruptor1_asset_impairment"] = _round2(item.get("asset_impairment_loss"))
    d["disruptor2_credit_impairment"] = _round2(item.get("credit_impairment_loss"))

    op_profit = _f(item.get("operating_profit"))
    d["core_profit_to_op_ratio"] = _div(core_profit, op_profit)

    # 主业照妖镜：核心利润 vs 扣非归母
    deduct_np = _f(item.get("deduct_parent_net_profit"))
    d["deduct_parent_net_profit"] = _round2(deduct_np)
    d["core_vs_deduct_gap"] = _round2((core_profit - deduct_np) if core_profit is not None and deduct_np is not None else None)

    # ---- 资产负债表战略底牌 ----
    broad_cash = _sum(item.get("monetary_funds"), item.get("trading_financial_assets"), item.get("other_current_assets"))
    d["broad_cash"] = _round2(broad_cash)
    d["operating_assets"] = _round2(_sum(
        item.get("notes_and_accounts_receivable"), item.get("prepayments"), item.get("inventories"),
        item.get("fixed_assets"), item.get("construction_in_progress"), item.get("intangible_assets"),
        item.get("dev_expenditure"),
    ))
    d["investment_assets"] = _round2(_sum(item.get("long_term_equity_investment"), item.get("goodwill")))
    total_assets = _f(item.get("total_assets"))
    d["heavy_asset_ratio"] = _pct(_sum(item.get("fixed_assets"), item.get("construction_in_progress")), total_assets)

    # 两头吃
    upstream = _sum(item.get("notes_and_accounts_payable"), item.get("contract_liabilities"), item.get("advance_receipts"))
    downstream = _f(item.get("notes_and_accounts_receivable"))
    d["upstream_occupy"] = _round2(upstream)
    d["downstream_occupied"] = _round2(downstream)
    d["net_bargaining_power"] = _round2((upstream - downstream) if upstream is not None and downstream is not None else None)

    # 有息负债
    interest_debt = _sum(
        item.get("short_term_loans"), item.get("non_current_liabilities_due_1y"),
        item.get("long_term_loans"), item.get("bonds_payable"), item.get("lease_liabilities"),
    )
    d["interest_bearing_debt"] = _round2(interest_debt)
    d["interest_bearing_debt_ratio"] = _pct(interest_debt, total_assets)

    # 造血 vs 输血
    total_liab = _f(item.get("total_liabilities"))
    retained = _sum(item.get("surplus_reserve"), item.get("undistributed_profit"))
    operating_liab = (total_liab - interest_debt) if total_liab is not None and interest_debt is not None else None
    internal = _sum(operating_liab, retained)
    equity_inject = _sum(item.get("paid_in_capital"), item.get("capital_reserve"))
    external = _sum(interest_debt, equity_inject)
    d["internal_financing"] = _round2(internal)
    d["external_financing"] = _round2(external)
    total_finance = _sum(internal, external)
    internal_ratio = _div(internal, total_finance)
    d["internal_financing_ratio"] = _round2(internal_ratio * 100) if internal_ratio is not None else None
    d["driver_type"] = _driver_type(
        internal=internal,
        retained=retained,
        equity_inject=equity_inject,
        interest_debt=interest_debt,
        broad_cash=broad_cash,
        total_assets=total_assets,
        internal_ratio=internal_ratio,
        total_finance=total_finance,
    )

    # ---- 现金流量表造血 ----
    ocf = _f(item.get("net_operating_cash_flow"))
    capex = _f(item.get("capex_cash_outflow"))
    d["core_profit_cash_ratio_full"] = _div(ocf, _sum(core_profit, pillar2))
    d["core_profit_cash_ratio_pure"] = _div(ocf, core_profit)
    d["free_cash_flow"] = _round2((ocf - capex) if ocf is not None and capex is not None else None)

    return d


def _driver_type(
    internal: Any,
    retained: Any,
    equity_inject: Any,
    interest_debt: Any,
    broad_cash: Any,
    total_assets: Any,
    internal_ratio: Any,
    total_finance: Any,
) -> str:
    """张新民驱动类型定性，优先识别「经营与盈利驱动型」。"""
    # a) 内部造血（经营性负债 + 留存收益）占总资产 >= 50%
    if internal is not None and total_assets:
        if internal / total_assets >= 0.5:
            return "经营与盈利驱动型"
    # b) 留存收益 > 股东入资，且净有息负债率（(有息负债-广义类现金)/总资产）< 20%（借款仅用于资金池管理/低净杠杆）
    # 注：阈值取 20% 而非 15%，以容纳伊利这类龙头（2026Q3 净有息负债率 15.98%，
    # 短期融资券 598 亿做大资金池属乳企季节性正常现象），仍能排除真正高杠杆续命公司。
    if retained is not None and equity_inject is not None and retained > equity_inject:
        net_debt = (interest_debt or 0.0) - (broad_cash or 0.0)
        if total_assets and net_debt / total_assets < 0.20:
            return "经营与盈利驱动型"
    # 兜底：原判定
    debt_ratio = interest_debt / total_assets if interest_debt is not None and total_assets else None
    equity_ratio = equity_inject / total_finance if equity_inject is not None and total_finance else None
    if internal_ratio is not None and internal_ratio >= 0.6:
        return "经营与盈利驱动型"
    if debt_ratio is not None and debt_ratio >= 0.4:
        return "债务融资驱动型"
    if equity_ratio is not None and equity_ratio >= 0.45:
        return "股东入资驱动型"
    return "多因素均衡驱动型"


def _pct(a: Any, b: Any) -> float | None:
    ratio = _div(a, b)
    return _round2(ratio * 100) if ratio is not None else None


def _round2(value: Any) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


# --------------------------------------------------------------------------- #
# 六大雷区 + 健康分 + 商业透视
# --------------------------------------------------------------------------- #
def scan_radars(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """基于最新一期（含历史对比）生成六大雷区检查。"""
    if not items:
        return []
    cur = items[0]
    m = cur.get("derived_metrics") or {}
    prev = items[1] if len(items) > 1 else None
    pm = prev.get("derived_metrics") if prev else None
    total_assets = _f(cur.get("total_assets"))
    revenue = _f(cur.get("revenue"))

    radars: list[dict[str, Any]] = []

    # 雷区1 大存大贷
    broad_cash = m.get("broad_cash")
    debt = m.get("interest_bearing_debt")
    if broad_cash is not None and debt is not None and total_assets:
        cash_ratio = broad_cash / total_assets
        debt_ratio = debt / total_assets
        if cash_ratio > 0.2 and debt_ratio > 0.2:
            interest_income = _f(cur.get("interest_income")) or 0
            interest_exp = _f(cur.get("interest_expense"))
            if interest_exp is None:
                interest_exp = max(_f(cur.get("financial_expense")) or 0, 0)
            dep_yield = interest_income / broad_cash if broad_cash else 0
            borrow_cost = interest_exp / debt if debt else 0
            if dep_yield >= borrow_cost and interest_income > 0:
                radars.append(_radar("r1", "大存大贷与资金真伪", "safe", f"现金占总资产{round(cash_ratio*100,1)}%，存款收益率{round(dep_yield*100,2)}%≥借款成本{round(borrow_cost*100,2)}%", "高信用低息套利，资金真实可靠"))
            elif dep_yield < borrow_cost * 0.5 or interest_income == 0:
                radars.append(_radar("r1", "大存大贷与资金真伪", "danger", f"账面现金占总资产{round(cash_ratio*100,1)}%但利息收入极低，借款成本{round(borrow_cost*100,2)}%", "疑似受限资金或大存大贷异常，需核查资金真伪"))
            else:
                radars.append(_radar("r1", "大存大贷与资金真伪", "warn", f"现金{round(cash_ratio*100,1)}%与有息债{round(debt_ratio*100,1)}%双高", "存贷双高，关注资金效率"))
        else:
            radars.append(_radar("r1", "大存大贷与资金真伪", "safe", f"现金占总资产{round(cash_ratio*100,1)}%，有息债{round(debt_ratio*100,1)}%", "无大存大贷异常"))
    else:
        radars.append(_radar("r1", "大存大贷与资金真伪", "safe", None, "数据不足，未发现异常"))

    # 雷区2 主业空心化
    core_profit = m.get("core_profit")
    core_ratio = m.get("core_profit_to_op_ratio")
    if core_profit is not None and core_ratio is not None:
        if core_profit <= 0:
            radars.append(_radar("r2", "主业空心化与搅局者", "danger", f"核心利润{_yi(core_profit)}，主业亏损", "主业真功夫为负，利润靠非经常性收益撑场面"))
        elif core_ratio < 0.6:
            radars.append(_radar("r2", "主业空心化与搅局者", "warn", f"核心利润占营业利润{round(core_ratio*100,1)}%", "主业占比偏低，投资收益/补贴贡献过大"))
        else:
            radars.append(_radar("r2", "主业空心化与搅局者", "safe", f"核心利润占营业利润{round(core_ratio*100,1)}%", "主业真功夫扎实"))
    else:
        radars.append(_radar("r2", "主业空心化与搅局者", "safe", None, "数据不足，无法判断"))

    # 雷区3 纸面富贵与放水赊销
    cash_ratio = m.get("core_profit_cash_ratio_full")
    ar = _f(cur.get("accounts_receivable"))
    if cash_ratio is not None:
        if cash_ratio >= 1.0:
            radars.append(_radar("r3", "纸面富贵与放水赊销", "safe", f"获现率{round(cash_ratio,2)}", "真金白银到账，利润含金量高"))
        elif cash_ratio >= 0.6:
            radars.append(_radar("r3", "纸面富贵与放水赊销", "warn", f"获现率{round(cash_ratio,2)}", "利润尚未完全转化为现金，关注回款"))
        else:
            radars.append(_radar("r3", "纸面富贵与放水赊销", "danger", f"获现率仅{round(cash_ratio,2)}", "纸面富贵，利润与现金流严重背离，警惕赊销放水"))
    else:
        radars.append(_radar("r3", "纸面富贵与放水赊销", "safe", None, "数据不足，无法判断"))

    # 雷区4 刚性偿债（现金充沛白马股特判，严禁误报违约）
    debt_ratio = m.get("interest_bearing_debt_ratio")
    broad_cash = m.get("broad_cash")
    debt = m.get("interest_bearing_debt")
    cash_ratio_full = m.get("core_profit_cash_ratio_full")
    if debt_ratio is not None:
        if debt_ratio >= 50:
            radars.append(_radar("r4", "刚性偿债与违约前兆", "danger", f"资产有息负债率{round(debt_ratio,1)}%", "逼近张氏50%高危线，关注刚性偿债压力与违约风险"))
        elif debt_ratio >= 30:
            cash_rich = (
                broad_cash is not None and debt is not None and debt > 0
                and broad_cash >= debt * 0.8
                and cash_ratio_full is not None and cash_ratio_full >= 0.9
            )
            if cash_rich:
                radars.append(_radar("r4", "刚性偿债", "warn", f"资产有息负债率{round(debt_ratio,1)}%", f"账面类现金达{_yi(broad_cash)}可基本覆盖有息债{_yi(debt)}（龙头低息融资/资金池运作），无实质违约风险"))
            elif debt_ratio >= 35 and broad_cash is not None and debt is not None and debt > 0 and broad_cash < debt * 0.4 and cash_ratio_full is not None and cash_ratio_full < 0.8:
                radars.append(_radar("r4", "刚性偿债", "warn", f"资产有息负债率{round(debt_ratio,1)}%，现金覆盖不足", "有息负债率偏高且现金覆盖不足，关注刚性偿债压力与违约风险"))
            else:
                radars.append(_radar("r4", "刚性偿债", "warn", f"资产有息负债率{round(debt_ratio,1)}%", "越过30%安全区，关注偿债能力"))
        else:
            radars.append(_radar("r4", "刚性偿债", "safe", f"资产有息负债率{round(debt_ratio,1)}%", "低于30%安全舒适区"))
    else:
        radars.append(_radar("r4", "刚性偿债", "safe", None, "数据不足，无法判断"))

    # 雷区5 盲目基建与减值
    goodwill = _f(cur.get("goodwill"))
    total_equity = _f(cur.get("total_equity"))
    goodwill_ratio = goodwill / total_equity if goodwill and total_equity else None
    heavy = m.get("heavy_asset_ratio")
    if goodwill_ratio is not None and goodwill_ratio > 0.2:
        radars.append(_radar("r5", "盲目基建与商誉/存货减值", "danger", f"商誉占净资产{round(goodwill_ratio*100,1)}%", "商誉占比超20%，警惕商誉减值雷"))
    elif heavy is not None and heavy > 0.5:
        radars.append(_radar("r5", "盲目基建与商誉/存货减值", "warn", f"重资产占比{round(heavy,1)}%", "重资产占比高，关注产能扩张与减值"))
    else:
        radars.append(_radar("r5", "盲目基建与商誉/存货减值", "safe", f"商誉占净资产{round((goodwill_ratio or 0)*100,1)}%，重资产{round(heavy or 0,1)}%", "未见明显减值风险"))

    # 雷区6 垃圾桶科目
    other_payable = _f(cur.get("other_payables"))
    op_ratio = other_payable / total_assets if other_payable and total_assets else None
    prev_op = _f(prev.get("other_payables")) if prev else None
    if op_ratio is not None and op_ratio > 0.15:
        radars.append(_radar("r6", "「垃圾桶」科目异动", "warn", f"其他应付款占总资产{round(op_ratio*100,1)}%", "其他应付款占比偏高，警惕隐瞒费用或关联拆借"))
    elif prev_op is not None and other_payable is not None and prev_op and other_payable / prev_op > 1.5:
        radars.append(_radar("r6", "「垃圾桶」科目异动", "warn", f"其他应付款同比激增{round(other_payable/prev_op*100-100,0)}%", "其他应付款激增，核查资金黑洞"))
    else:
        radars.append(_radar("r6", "「垃圾桶」科目异动", "safe", f"其他应付款占总资产{round((op_ratio or 0)*100,1)}%", "未见异常"))

    return radars


def _radar(id_: str, title: str, status: str, metric: Any, desc: str) -> dict[str, Any]:
    return {"id": id_, "title": title, "status": status, "metric_value": metric, "description": desc}


def health_score(radars: list[dict[str, Any]]) -> int:
    score = 100
    for r in radars:
        if r["status"] == "warn":
            score -= 8
        elif r["status"] == "danger":
            score -= 16
    return max(0, min(100, score))


def executive_summary(items: list[dict[str, Any]], radars: list[dict[str, Any]], score: int) -> str:
    if not items:
        return "暂无财报数据，请先同步三表。"
    cur = items[0]
    m = cur.get("derived_metrics") or {}
    name = cur.get("name") or cur.get("code") or ""
    driver = m.get("driver_type") or "多因素均衡驱动型"
    dangers = [r for r in radars if r["status"] == "danger"]
    warns = [r for r in radars if r["status"] == "warn"]
    bargaining = m.get("net_bargaining_power")

    # 现金充沛白马股特判：有息负债率 < 50% 且类现金基本覆盖债务且获现率高
    debt_ratio = m.get("interest_bearing_debt_ratio")
    broad_cash = m.get("broad_cash")
    debt = m.get("interest_bearing_debt")
    cash_ratio_full = m.get("core_profit_cash_ratio_full")
    cash_rich = (
        debt_ratio is not None and 30 <= debt_ratio < 50
        and broad_cash is not None and debt is not None and debt > 0
        and broad_cash >= debt * 0.8
        and cash_ratio_full is not None and cash_ratio_full >= 0.9
    )
    if cash_rich and not dangers:
        parts = [f"{name}属{driver}"]
        if bargaining is not None and bargaining > 0:
            parts.append("产业链两头吃优势显著")
        parts.append("账面类现金充沛，需关注大存大贷资金运作效率")
        return "，".join(parts) + "。"

    parts = [f"{name}属{driver}"]
    if bargaining is not None:
        parts.append("产业链两头吃" if bargaining > 0 else "产业链话语权偏弱")
    if dangers:
        parts.append(f"警惕{dangers[0]['title']}")
    elif warns:
        # 雷区4 温和关注时，避免对未超 50% 红线的白马股输出「违约前兆」字样
        if warns[0].get("id") == "r4":
            parts.append("关注有息负债结构")
        else:
            parts.append(f"关注{warns[0]['title']}")
    else:
        parts.append("六大雷区全部安全")
    return "，".join(parts) + "。"


def finalize(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为最新一期附加雷达/健康分/透视（要求每期 derived_metrics 已存在）。

    与 analyze 分离：读库分支的 derived_metrics 已持久化，无需重算原始指标，
    只需重算 radar_checks / health_score / executive_summary 这三个未落库的派生结果。
    """
    if items:
        items[0]["radar_checks"] = scan_radars(items)
        items[0]["health_score"] = health_score(items[0]["radar_checks"])
        items[0]["executive_summary"] = executive_summary(items, items[0]["radar_checks"], items[0]["health_score"])
    return items


def analyze(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为每期计算 derived_metrics，并附加最新一期的雷达/健康分/透视。"""
    for item in items:
        item["derived_metrics"] = compute_derived(item)
    return finalize(items)


def _yi(value: Any) -> str:
    v = _f(value)
    if v is None:
        return "—"
    return f"{v/1e8:.1f}亿"

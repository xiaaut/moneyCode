"""财报三表核心数据与衍生指标、个人复盘笔记。"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 利润表字段 -> 注释
INCOME_FIELDS = {
    "total_revenue": "营业总收入",
    "revenue": "营业收入",
    "operating_cost": "营业成本",
    "tax_and_surcharge": "税金及附加",
    "selling_expense": "销售费用",
    "admin_expense": "管理费用",
    "rd_expense": "研发费用",
    "financial_expense": "财务费用",
    "interest_expense": "利息费用",
    "interest_income": "利息收入",
    "other_income": "其他收益/政府补贴",
    "investment_income": "投资收益",
    "fair_value_change_income": "公允价值变动收益",
    "asset_disposal_income": "资产处置收益",
    "asset_impairment_loss": "资产减值损失",
    "credit_impairment_loss": "信用减值损失",
    "operating_profit": "营业利润",
    "total_profit": "利润总额",
    "net_profit": "净利润",
    "parent_net_profit": "归母净利润",
    "deduct_parent_net_profit": "扣非归母净利润",
}

BALANCE_FIELDS = {
    "monetary_funds": "货币资金",
    "trading_financial_assets": "交易性金融资产",
    "other_current_assets": "其他流动资产",
    "notes_and_accounts_receivable": "应收票据及账款",
    "accounts_receivable": "应收账款",
    "notes_receivable": "应收票据",
    "prepayments": "预付款项",
    "other_receivables": "其他应收款",
    "inventories": "存货",
    "fixed_assets": "固定资产",
    "construction_in_progress": "在建工程",
    "intangible_assets": "无形资产",
    "dev_expenditure": "开发支出",
    "long_term_equity_investment": "长期股权投资",
    "goodwill": "商誉",
    "total_current_assets": "流动资产合计",
    "total_assets": "资产总计",
    "short_term_loans": "短期借款",
    "notes_and_accounts_payable": "应付票据及账款",
    "contract_liabilities": "合同负债",
    "advance_receipts": "预收款项",
    "employee_benefits_payable": "应付职工薪酬",
    "taxes_payable": "应交税费",
    "other_payables": "其他应付款",
    "interest_payable": "应付利息",
    "non_current_liabilities_due_1y": "一年内到期的非流动负债",
    "long_term_loans": "长期借款",
    "bonds_payable": "应付债券",
    "lease_liabilities": "租赁负债",
    "total_current_liabilities": "流动负债合计",
    "total_liabilities": "负债合计",
    "paid_in_capital": "实收资本/股本",
    "capital_reserve": "资本公积",
    "surplus_reserve": "盈余公积",
    "undistributed_profit": "未分配利润",
    "minority_equity": "少数股东权益",
    "parent_equity": "归母权益",
    "total_equity": "所有者权益合计",
}

CASHFLOW_FIELDS = {
    "operating_cash_inflow": "经营活动现金流入",
    "operating_cash_outflow": "经营活动现金流出",
    "net_operating_cash_flow": "经营活动现金流净额",
    "capex_cash_outflow": "购建固定资产等支付现金",
    "net_investing_cash_flow": "投资活动现金流净额",
    "borrowings_cash_inflow": "取得借款收到的现金",
    "debt_repayment_cash_outflow": "偿还债务支付的现金",
    "dividend_interest_cash_outflow": "分配股利利润或偿付利息支付现金",
    "net_financing_cash_flow": "筹资活动现金流净额",
    "net_cash_increase": "现金及现金等价物净增加额",
}

ALL_MONEY_FIELDS = {**INCOME_FIELDS, **BALANCE_FIELDS, **CASHFLOW_FIELDS}


def _money_columns() -> dict:
    return {
        name: mapped_column(Numeric(20, 2), comment=comment)
        for name, comment in ALL_MONEY_FIELDS.items()
    }


class StockFinancialStatement(Base):
    __tablename__ = "stock_financial_statements"

    code: Mapped[str] = mapped_column(String(20), primary_key=True, comment="证券代码")
    report_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="报告期末")
    report_label: Mapped[str | None] = mapped_column(String(20), comment="报告期标签，如 2025年报")
    report_type: Mapped[str | None] = mapped_column(String(10), comment="annual / periodic")
    source: Mapped[str | None] = mapped_column(String(20), comment="数据来源：ths / free")

    derived_metrics: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"), comment="张新民衍生指标"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# 为全部金额字段挂上 Numeric(20,2) 列
for _name, _column in _money_columns().items():
    setattr(StockFinancialStatement, _name, _column)


class StockAnalysisNote(Base):
    __tablename__ = "stock_analysis_notes"

    code: Mapped[str] = mapped_column(String(20), primary_key=True, comment="证券代码")
    notes: Mapped[str | None] = mapped_column(Text, comment="复盘笔记")
    tags: Mapped[str | None] = mapped_column(String(200), comment="标签")
    rating: Mapped[str | None] = mapped_column(String(20), comment="评级")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

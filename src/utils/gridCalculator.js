// 网格交易测算：纯函数，不依赖 Vue / DOM，可直接在 Node 里跑断言。
// 约定：格线价格是买入触发价，成交后挂到上一格卖出；单格收益已扣双边佣金和卖出印花税。
// 底仓按基准价（当前价）计价，基准价及以上的档位视为底仓分批卖出，基准价以下的档位视为下跌逐格买入。

const MAX_GRID_COUNT = 200;
export const LOT_SIZE = 100;
export const DEFAULT_COMMISSION_RATE = 0.0001; // 万一
export const DEFAULT_MIN_COMMISSION = 5; // 券商单笔最低佣金，免五时按 0 计
export const DEFAULT_STAMP_DUTY_RATE = 0.0005; // 卖出印花税 0.05%
export const GRID_MODES = ['arithmetic', 'geometric'];
export const GRID_MODE_LABELS = { arithmetic: '等差', geometric: '等比' };

function roundTo(value, digits = 2) {
  const factor = 10 ** digits;
  return Math.round((Number(value) + Number.EPSILON) * factor) / factor;
}

function toNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

/**
 * 单笔委托费用。
 * @param {number} amount 成交金额
 * @param {{ commissionRate?: number, freeFive?: boolean, stampDutyRate?: number, side?: 'buy' | 'sell' }} options
 * @returns {{ commission: number, stampDuty: number, total: number }}
 */
export function calcOrderFees(amount, options = {}) {
  const value = Math.max(0, toNumber(amount) ?? 0);
  const commissionRate = toNumber(options.commissionRate) ?? DEFAULT_COMMISSION_RATE;
  const stampDutyRate = toNumber(options.stampDutyRate) ?? DEFAULT_STAMP_DUTY_RATE;
  const minCommission = options.freeFive ? 0 : DEFAULT_MIN_COMMISSION;
  const commission = value > 0 ? roundTo(Math.max(value * commissionRate, minCommission)) : 0;
  const stampDuty = options.side === 'sell' ? roundTo(value * stampDutyRate) : 0;
  return { commission, stampDuty, total: roundTo(commission + stampDuty) };
}

/**
 * 校验并归一化入参。网格数量与步长百分比二选一，同时给出时以网格数量为准。
 * @param {object} input
 * @returns {{ ok: boolean, errors: string[], warnings: string[], config: object | null }}
 */
export function validateGridConfig(input = {}) {
  const errors = [];
  const warnings = [];

  const basePrice = toNumber(input.basePrice);
  const upperPrice = toNumber(input.upperPrice);
  const lowerPrice = toNumber(input.lowerPrice);

  if (!(basePrice > 0)) errors.push('基准价必须是大于 0 的数字');
  if (!(upperPrice > 0)) errors.push('区间上限必须是大于 0 的数字');
  if (!(lowerPrice > 0)) errors.push('区间下限必须是大于 0 的数字');
  if (lowerPrice > 0 && upperPrice > 0 && lowerPrice >= upperPrice) {
    errors.push('区间下限必须小于区间上限');
  }

  const mode = input.mode === undefined || input.mode === null || input.mode === '' ? 'arithmetic' : String(input.mode);
  if (!GRID_MODES.includes(mode)) errors.push('网格模式只支持 arithmetic（等差）或 geometric（等比）');

  const sharesPerGrid = toNumber(input.sharesPerGrid);
  if (sharesPerGrid === null || !Number.isInteger(sharesPerGrid) || sharesPerGrid <= 0) {
    errors.push('每格委托股数必须是正整数');
  } else if (sharesPerGrid % LOT_SIZE !== 0) {
    errors.push(`每格委托股数必须是 ${LOT_SIZE} 股的整数倍`);
  }

  const commissionRate =
    input.commissionRate === undefined || input.commissionRate === null || input.commissionRate === ''
      ? DEFAULT_COMMISSION_RATE
      : toNumber(input.commissionRate);
  if (commissionRate === null || commissionRate < 0 || commissionRate > 0.01) {
    errors.push('佣金率需在 0 ~ 0.01 之间（万一 = 0.0001）');
  }

  const stampDutyRate =
    input.stampDutyRate === undefined || input.stampDutyRate === null || input.stampDutyRate === ''
      ? DEFAULT_STAMP_DUTY_RATE
      : toNumber(input.stampDutyRate);
  if (stampDutyRate === null || stampDutyRate < 0 || stampDutyRate > 0.01) {
    errors.push('卖出印花税率需在 0 ~ 0.01 之间（默认 0.0005）');
  }

  let gridCount = toNumber(input.gridCount);
  const stepPercent = toNumber(input.stepPercent);
  if (gridCount !== null) {
    if (!Number.isInteger(gridCount) || gridCount < 1) errors.push('网格数量必须是大于 0 的整数');
    else if (gridCount > MAX_GRID_COUNT) errors.push(`网格数量不能超过 ${MAX_GRID_COUNT}`);
    if (stepPercent > 0) warnings.push('同时提供了网格数量与步长百分比，按网格数量计算');
  } else if (!(stepPercent > 0)) {
    errors.push('网格数量与步长百分比至少填一个');
  }

  if (errors.length) return { ok: false, errors, warnings, config: null };

  const priceRangeValid = lowerPrice < upperPrice;
  if (priceRangeValid && basePrice < lowerPrice) warnings.push('基准价低于区间下限，全部档位按底仓测算');
  if (priceRangeValid && basePrice > upperPrice) warnings.push('基准价高于区间上限，全部档位为待买入档，无需底仓');

  if (gridCount === null) {
    if (mode === 'geometric') {
      const ratio = 1 + stepPercent / 100;
      gridCount = Math.round(Math.log(upperPrice / lowerPrice) / Math.log(ratio));
    } else {
      gridCount = Math.round((upperPrice - lowerPrice) / ((basePrice * stepPercent) / 100));
    }
    gridCount = Math.max(1, gridCount);
    if (gridCount > MAX_GRID_COUNT) {
      warnings.push(`按步长推导出的网格数量为 ${gridCount}，已按上限 ${MAX_GRID_COUNT} 截断`);
      gridCount = MAX_GRID_COUNT;
    }
  }

  return {
    ok: true,
    errors,
    warnings,
    config: {
      basePrice,
      upperPrice,
      lowerPrice,
      mode,
      gridCount,
      sharesPerGrid,
      commissionRate,
      stampDutyRate,
      freeFive: Boolean(input.freeFive),
      stepPercent: stepPercent ?? 0,
    },
  };
}

/**
 * 生成格线价格（含上下限，升序）。
 * @param {{ lowerPrice: number, upperPrice: number, mode: string, gridCount: number }} config
 * @returns {number[]}
 */
export function buildGridLines({ lowerPrice, upperPrice, mode, gridCount }) {
  const lines = [];
  if (mode === 'geometric') {
    const ratio = Math.pow(upperPrice / lowerPrice, 1 / gridCount);
    for (let i = 0; i <= gridCount; i += 1) {
      lines.push(roundTo(i === gridCount ? upperPrice : lowerPrice * Math.pow(ratio, i)));
    }
    return lines;
  }

  const step = (upperPrice - lowerPrice) / gridCount;
  for (let i = 0; i <= gridCount; i += 1) {
    lines.push(roundTo(i === gridCount ? upperPrice : lowerPrice + step * i));
  }
  return lines;
}

/**
 * 构建完整网格方案。
 * @param {object} input basePrice / upperPrice / lowerPrice / mode / gridCount / stepPercent / sharesPerGrid / commissionRate / freeFive / stampDutyRate
 * @returns {{
 *   ok: boolean, message: string, errors: string[], warnings: string[],
 *   config: object | null, lines: number[], levels: Array<object>, summary: object | null
 * }}
 */
export function buildGridPlan(input = {}) {
  const validation = validateGridConfig(input);
  if (!validation.ok) {
    return {
      ok: false,
      message: validation.errors[0],
      errors: validation.errors,
      warnings: validation.warnings,
      config: null,
      lines: [],
      levels: [],
      summary: null,
    };
  }

  const config = validation.config;
  const warnings = [...validation.warnings];

  // 四舍五入可能让相邻格线重叠（低价股 + 密网格），去重后按实际档位测算
  const prices = [];
  for (const price of buildGridLines(config)) {
    if (!prices.length || price > prices[prices.length - 1]) prices.push(price);
  }
  if (prices.length < 2) {
    return {
      ok: false,
      message: '区间过窄，四舍五入后不足两个有效格线，请放宽区间或减少网格数量',
      errors: ['区间过窄，无法生成有效格线'],
      warnings,
      config,
      lines: prices,
      levels: [],
      summary: null,
    };
  }

  const actualGridCount = prices.length - 1;
  if (actualGridCount !== config.gridCount) {
    warnings.push(`相邻格线四舍五入后重复，实际档位由 ${config.gridCount} 调整为 ${actualGridCount}`);
    config.gridCount = actualGridCount;
  }

  const { sharesPerGrid, commissionRate, stampDutyRate, freeFive, basePrice } = config;
  const feeOptions = { commissionRate, stampDutyRate, freeFive };

  if (!prices.some(price => Math.abs(price - basePrice) < 0.005)) {
    warnings.push('基准价不在格线上，底仓与买入档按相邻格线近似划分');
  }

  const levels = [];
  for (let i = 0; i < prices.length - 1; i += 1) {
    const buyPrice = prices[i];
    const sellPrice = prices[i + 1];
    const belowBase = buyPrice < basePrice;
    const costPrice = belowBase ? buyPrice : basePrice;
    // buyAmount 是该档占用的资金：买入档为买入金，底仓档为基准价建仓成本
    const buyAmount = roundTo(costPrice * sharesPerGrid);
    const sellAmount = roundTo(sellPrice * sharesPerGrid);
    const buyFees = calcOrderFees(buyAmount, { ...feeOptions, side: 'buy' });
    const sellFees = calcOrderFees(sellAmount, { ...feeOptions, side: 'sell' });
    // 单格套利只算一格价差，底仓档不把「基准价到当前格」的涨幅计入单格利润
    const spreadProfit = roundTo((sellPrice - buyPrice) * sharesPerGrid);
    const netProfit = roundTo(spreadProfit - buyFees.total - sellFees.total);

    levels.push({
      index: i + 1,
      type: belowBase ? 'grid' : 'base',
      label: belowBase ? `第 ${i + 1} 档` : `底仓 ${i + 1} 档`,
      price: buyPrice,
      buyPrice,
      sellPrice,
      costPrice,
      shares: sharesPerGrid,
      buyAmount,
      sellAmount,
      spreadProfit,
      profitVsBase: belowBase
        ? null
        : roundTo((sellPrice - basePrice) * sharesPerGrid - buyFees.total - sellFees.total),
      buyCommission: buyFees.commission,
      sellCommission: sellFees.commission,
      stampDuty: sellFees.stampDuty,
      feeTotal: roundTo(buyFees.total + sellFees.total),
      netProfit,
      returnRate: buyAmount > 0 ? roundTo(netProfit / buyAmount, 6) : 0,
      priceGapPercent: buyPrice > 0 ? roundTo(((sellPrice - buyPrice) / buyPrice) * 100, 4) : 0,
      belowBase,
      warning: netProfit <= 0 ? '单格净收益不足以覆盖手续费' : '',
    });
  }

  const gridLevels = levels.filter(level => level.type === 'grid');
  const baseLevels = levels.filter(level => level.type === 'base');

  const baseShares = baseLevels.length * sharesPerGrid;
  const baseAmount = roundTo(baseShares * basePrice);
  const baseCommission = baseShares > 0 ? calcOrderFees(baseAmount, { ...feeOptions, side: 'buy' }).commission : 0;
  const maxAddShares = gridLevels.length * sharesPerGrid;
  const maxAddAmount = roundTo(gridLevels.reduce((sum, level) => sum + level.buyAmount + level.buyCommission, 0));
  const maxTotalCapital = roundTo(baseAmount + baseCommission + maxAddAmount);
  const totalNetProfit = roundTo(levels.reduce((sum, level) => sum + level.netProfit, 0));
  const avgReturnRate = levels.length
    ? roundTo(levels.reduce((sum, level) => sum + level.returnRate, 0) / levels.length, 6)
    : 0;
  const avgGapPercent = roundTo(
    levels.reduce((sum, level) => sum + level.priceGapPercent, 0) / levels.length,
    4,
  );
  const minLevel = levels.reduce((worst, level) => (level.netProfit < worst.netProfit ? level : worst), levels[0]);
  const baseUpperFees =
    baseShares > 0
      ? calcOrderFees(roundTo(config.upperPrice * baseShares), { ...feeOptions, side: 'sell' }).total
      : 0;

  const summary = {
    basePrice,
    upperPrice: config.upperPrice,
    lowerPrice: config.lowerPrice,
    mode: config.mode,
    modeLabel: GRID_MODE_LABELS[config.mode],
    gridCount: levels.length,
    sharesPerGrid,
    avgGapPercent,
    baseLevelCount: baseLevels.length,
    gridLevelCount: gridLevels.length,
    baseShares,
    baseAmount,
    baseCommission,
    baseBuyCost: roundTo(baseAmount + baseCommission),
    maxAddShares,
    maxAddAmount,
    maxTotalCapital,
    maxShares: baseShares + maxAddShares,
    totalNetProfit,
    avgReturnRate,
    baseProfitAtUpper: roundTo((config.upperPrice - basePrice) * baseShares - baseUpperFees),
    minLevelProfit: minLevel.netProfit,
    minLevelIndex: minLevel.index,
    profitableLevelCount: levels.filter(level => level.netProfit > 0).length,
    lossLevelCount: levels.filter(level => level.netProfit <= 0).length,
  };

  const message = `已生成 ${levels.length} 档${GRID_MODE_LABELS[config.mode]}网格，底仓 ${baseShares} 股，极限追加资金 ${maxAddAmount} 元`;

  return { ok: true, message, errors: [], warnings, config, lines: prices, levels, summary };
}

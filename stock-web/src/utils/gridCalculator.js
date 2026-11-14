// 网格交易测算：纯函数，不依赖 Vue / DOM，可直接在 Node 里跑断言。
//
// 金融口径（与券商交割单对齐）：
// 1) 每一档的买入本金 = 该档自身的「买入触发价 buyPrice × 每格股数」，绝不用顶部基准价代替；
// 2) 买入手续费 = max(buyPrice × 股数 × 佣金率, 单笔最低佣金)；
// 3) 卖出手续费 = max(sellPrice × 股数 × 佣金率, 单笔最低佣金) + sellPrice × 股数 × 印花税率；
// 4) 单格净利 = (sellPrice - buyPrice) × 股数 - 买入费 - 卖出费；
// 5) 单格收益率 = 单格净利 / (buyPrice × 股数 + 买入费)。
//
// 场内 ETF（51/15/56/58 开头）免征印花税，印花税强制为 0。

const MAX_GRID_COUNT = 200;
export const LOT_SIZE = 100;
export const DEFAULT_COMMISSION_RATE = 0.0001; // 万一
export const DEFAULT_MIN_COMMISSION = 0.2; // 单笔最低佣金（元），ETF 常见 0.20
export const DEFAULT_STAMP_DUTY_RATE = 0.0005; // A 股卖出印花税 0.05%
export const ETF_CODE_PREFIXES = ['51', '15', '56', '58'];
export const GRID_MODES = ['arithmetic', 'geometric'];
export const GRID_MODE_LABELS = { arithmetic: '等差', geometric: '等比' };
export const GEN_MODES = ['range', 'step'];
export const GEN_MODE_LABELS = { range: '按区间等分生成', step: '按基准价步长生成' };
export const STEP_TYPES = ['amount', 'percent'];
export const STEP_TYPE_LABELS = { amount: '固定差价 元', percent: '百分比 %' };

function roundTo(value, digits = 2) {
  const factor = 10 ** digits;
  return Math.round((Number(value) + Number.EPSILON) * factor) / factor;
}

function toNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

/** 场内 ETF 判定：51 / 15 / 56 / 58 开头（如 510300、159915、588000）。 */
export function isEtfCode(code) {
  const digits = String(code ?? '').replace(/\D/g, '');
  if (digits.length !== 6) return false;
  return ETF_CODE_PREFIXES.some(prefix => digits.startsWith(prefix));
}

/** 按标的类型给出默认费率：ETF 免印花税，A 股 0.05%。 */
export function defaultRatesFor(code) {
  return {
    stampDutyRate: isEtfCode(code) ? 0 : DEFAULT_STAMP_DUTY_RATE,
    minCommission: DEFAULT_MIN_COMMISSION,
    commissionRate: DEFAULT_COMMISSION_RATE,
  };
}

/**
 * 单笔委托费用（佣金按分四舍五入，最低佣金兜底；卖出加印花税）。
 * @param {number} amount 成交金额
 * @param {{ commissionRate?: number, minCommission?: number, stampDutyRate?: number, side?: 'buy'|'sell', freeFive?: boolean }} options
 * @returns {{ commission: number, stampDuty: number, total: number }}
 */
export function calcOrderFees(amount, options = {}) {
  const value = Math.max(0, toNumber(amount) ?? 0);
  const commissionRate = toNumber(options.commissionRate) ?? DEFAULT_COMMISSION_RATE;
  const stampDutyRate = toNumber(options.stampDutyRate) ?? DEFAULT_STAMP_DUTY_RATE;
  // freeFive 为旧参数：免五等价于最低佣金 0；新的 minCommission 优先
  const minCommission =
    toNumber(options.minCommission) ?? (options.freeFive ? 0 : DEFAULT_MIN_COMMISSION);
  const commission =
    value > 0 ? roundTo(Math.max(value * commissionRate, Math.max(0, minCommission)), 2) : 0;
  const stampDuty = options.side === 'sell' ? roundTo(value * stampDutyRate, 2) : 0;
  return { commission, stampDuty, total: roundTo(commission + stampDuty, 2) };
}

/** 按区间等分 / 等比生成格线（含上下限，升序）。 */
export function buildGridLines({ lowerPrice, upperPrice, mode, gridCount }) {
  const lines = [];
  if (mode === 'geometric') {
    const ratio = Math.pow(upperPrice / lowerPrice, 1 / gridCount);
    for (let i = 0; i <= gridCount; i += 1) {
      lines.push(roundTo(i === gridCount ? upperPrice : lowerPrice * Math.pow(ratio, i), 3));
    }
    return lines;
  }

  const step = (upperPrice - lowerPrice) / gridCount;
  for (let i = 0; i <= gridCount; i += 1) {
    lines.push(roundTo(i === gridCount ? upperPrice : lowerPrice + step * i, 3));
  }
  return lines;
}

/**
 * 以基准价为 0 号中轴线，按步长向上/向下生成格线（升序）。
 *
 * 正交组合（P0 = 基准价，r = 步长百分比，V = 固定差价）：
 * - 等比 + 百分比：P(k) = P0 × (1 + r)^k           —— 复利递进，单格收益率恒定
 * - 等差 + 百分比：δ = round(P0 × r, 3)，P(k) = P0 + k × δ —— 单利等差，每格差固定元数
 * - 等差 + 固定元：P(k) = P0 + k × V
 * （等比 + 固定元为非正交组合，UI 会联动回等差；此处仍按等比较为等效处理）
 *
 * @param {{ basePrice: number, mode?: 'arithmetic'|'geometric', stepType: 'amount'|'percent', stepValue: number, gridsUp: number, gridsDown: number }} input
 */
export function buildStepLines({ basePrice, mode = 'geometric', stepType, stepValue, gridsUp, gridsDown }) {
  const up = Math.max(0, Math.floor(toNumber(gridsUp) ?? 0));
  const down = Math.max(0, Math.floor(toNumber(gridsDown) ?? 0));
  const value = toNumber(stepValue) ?? 0;
  if (!(basePrice > 0) || !(value > 0)) return [];

  const geometric = mode !== 'arithmetic';
  // 等比只用百分比；等差既可用百分比（换算成固定价差）也可直接用固定元数
  const delta = !geometric
    ? stepType === 'amount'
      ? roundTo(value, 3)
      : roundTo((basePrice * value) / 100, 3)
    : 0;

  const priceAt = offset => {
    if (geometric) return roundTo(basePrice * Math.pow(1 + value / 100, offset), 3);
    // 先固定单格价差再乘以格数，避免逐档四舍五入导致相邻差价抖动
    return roundTo(basePrice + offset * delta, 3);
  };

  const lines = [];
  for (let i = down; i >= 1; i -= 1) lines.push(priceAt(-i));
  lines.push(roundTo(basePrice, 3));
  for (let i = 1; i <= up; i += 1) lines.push(priceAt(i));
  return lines.filter((price, index, arr) => price > 0 && (index === 0 || price > arr[index - 1]));
}

/** 单格价差（元）：用于列表展示与「差价是否恒定」自检 */
export function stepGapOf({ basePrice, mode, stepType, stepValue }) {
  const value = toNumber(stepValue) ?? 0;
  if (!(basePrice > 0) || !(value > 0)) return 0;
  if (mode !== 'arithmetic') return roundTo((basePrice * value) / 100, 3);
  return stepType === 'amount' ? roundTo(value, 3) : roundTo((basePrice * value) / 100, 3);
}

/**
 * 校验并归一化入参。
 * @returns {{ ok: boolean, errors: string[], warnings: string[], config: object|null }}
 */
export function validateGridConfig(input = {}) {
  const errors = [];
  const warnings = [];

  const genMode = input.genMode === 'step' ? 'step' : 'range';
  // 兼容历史值 fixed/pct
  const rawStepType = String(input.stepType ?? '').toLowerCase();
  let stepType = rawStepType === 'amount' || rawStepType === 'fixed' ? 'amount' : 'percent';
  const basePrice = toNumber(input.basePrice);

  if (!(basePrice > 0)) errors.push('基准价必须是大于 0 的数字');

  const mode =
    input.mode === undefined || input.mode === null || input.mode === ''
      ? 'arithmetic'
      : String(input.mode);
  if (!GRID_MODES.includes(mode)) errors.push('网格模式只支持 arithmetic（等差）或 geometric（等比）');

  // 正交约束：等比网格只能用百分比步长
  if (genMode === 'step' && mode === 'geometric' && stepType === 'amount') {
    stepType = 'percent';
  }

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

  const minCommission =
    input.minCommission === undefined || input.minCommission === null || input.minCommission === ''
      ? DEFAULT_MIN_COMMISSION
      : toNumber(input.minCommission);
  if (minCommission === null || minCommission < 0 || minCommission > 100) {
    errors.push('单笔最低佣金需在 0 ~ 100 元之间（免五填 0）');
  }

  const stampDutyRate =
    input.stampDutyRate === undefined || input.stampDutyRate === null || input.stampDutyRate === ''
      ? DEFAULT_STAMP_DUTY_RATE
      : toNumber(input.stampDutyRate);
  if (stampDutyRate === null || stampDutyRate < 0 || stampDutyRate > 0.01) {
    errors.push('卖出印花税率需在 0 ~ 0.01 之间（默认 0.0005，ETF 为 0）');
  }

  let gridCount = toNumber(input.gridCount);
  let stepValue = toNumber(input.stepValue);
  const gridsUp = Math.max(0, Math.floor(toNumber(input.gridsUp) ?? 0));
  const gridsDown = Math.max(0, Math.floor(toNumber(input.gridsDown) ?? 0));

  let upperPrice = toNumber(input.upperPrice);
  let lowerPrice = toNumber(input.lowerPrice);

  if (genMode === 'range') {
    if (!(upperPrice > 0)) errors.push('区间上限必须是大于 0 的数字');
    if (!(lowerPrice > 0)) errors.push('区间下限必须是大于 0 的数字');
    if (lowerPrice > 0 && upperPrice > 0 && lowerPrice >= upperPrice) {
      errors.push('区间下限必须小于区间上限');
    }
    if (lowerPrice > 0 && upperPrice > lowerPrice && basePrice > 0) {
      if (!(basePrice > lowerPrice) || !(basePrice < upperPrice)) {
        errors.push('基准价必须落在区间内（价格下限 < 基准价 < 价格上限）');
      }
    }
    const stepPercent = toNumber(input.stepPercent);
    if (gridCount !== null) {
      if (!Number.isInteger(gridCount) || gridCount < 1) errors.push('网格数量必须是大于 0 的整数');
      else if (gridCount > MAX_GRID_COUNT) errors.push(`网格数量不能超过 ${MAX_GRID_COUNT}`);
      if (stepPercent > 0) warnings.push('同时提供了网格数量与步长百分比，按网格数量计算');
    } else if (!(stepPercent > 0)) {
      errors.push('网格数量与步长百分比至少填一个');
    }
  } else {
    if (!(stepValue > 0)) errors.push('步长值必须大于 0');
    if (gridsUp + gridsDown < 1) errors.push('向上/向下格数至少填一格');
    if (gridsUp + gridsDown > MAX_GRID_COUNT) errors.push(`总格数不能超过 ${MAX_GRID_COUNT}`);
    if (stepType === 'percent' && stepValue > 0 && stepValue >= 50) {
      errors.push('百分比步长需小于 50%');
    }
    if (stepType === 'amount' && mode === 'geometric') {
      errors.push('等比网格必须使用百分比步长');
    }
  }

  if (errors.length) return { ok: false, errors, warnings, config: null };

  if (genMode === 'range' && gridCount === null) {
    const stepPercent = toNumber(input.stepPercent) ?? 0;
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

  if (genMode === 'step') {
    const lines = buildStepLines({ basePrice, mode, stepType, stepValue, gridsUp, gridsDown });
    if (lines.length < 2) {
      return { ok: false, errors: ['区间过窄，无法生成有效格线'], warnings, config: null };
    }
    lowerPrice = lines[0];
    upperPrice = lines[lines.length - 1];
    gridCount = lines.length - 1;
    stepValue = stepValue ?? 0;
  }

  if (genMode === 'range' && upperPrice > lowerPrice) {
    if (basePrice < lowerPrice) warnings.push('基准价低于区间下限，全部档位按底仓测算');
    if (basePrice > upperPrice) warnings.push('基准价高于区间上限，全部档位为待买入档');
  }

  return {
    ok: true,
    errors,
    warnings,
    config: {
      genMode,
      stepType,
      stepValue: stepValue ?? 0,
      gridsUp,
      gridsDown,
      basePrice,
      upperPrice,
      lowerPrice,
      mode,
      gridCount,
      sharesPerGrid,
      initialShares: Math.max(0, Math.floor(toNumber(input.initialShares) ?? 0)),
      // 初始底仓真实成本（含底仓占用资金按此计算，不能用档位理论买入价）
      initialCost: Math.max(0, toNumber(input.initialCost) ?? 0),
      commissionRate,
      minCommission,
      stampDutyRate,
      freeFive: Boolean(input.freeFive),
      stepPercent: toNumber(input.stepPercent) ?? 0,
    },
  };
}

/**
 * 构建完整网格方案。
 * @param {object} input basePrice / genMode / stepType / stepValue / gridsUp / gridsDown /
 *   upperPrice / lowerPrice / mode / gridCount / sharesPerGrid / initialShares /
 *   commissionRate / minCommission / stampDutyRate
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

  const rawLines = config.genMode === 'step' ? buildStepLines(config) : buildGridLines(config);

  // 四舍五入可能让相邻格线重叠（低价股 + 密网格），去重后按实际档位测算
  const prices = [];
  for (const price of rawLines) {
    if (!(price > 0)) continue;
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
  if (config.genMode === 'range' && actualGridCount !== config.gridCount) {
    warnings.push(`相邻格线四舍五入后重复，实际档位由 ${config.gridCount} 调整为 ${actualGridCount}`);
  }
  config.gridCount = actualGridCount;
  config.lowerPrice = prices[0];
  config.upperPrice = prices[prices.length - 1];

  const {
    sharesPerGrid,
    commissionRate,
    minCommission,
    stampDutyRate,
    basePrice,
    initialShares,
    initialCost,
  } = config;
  const feeOptions = { commissionRate, minCommission, stampDutyRate };

  const levels = [];
  for (let i = 0; i < prices.length - 1; i += 1) {
    const buyPrice = prices[i];
    const sellPrice = prices[i + 1];
    // 关键口径：本金按该档自身的买入触发价计算，不用基准价
    const buyAmount = roundTo(buyPrice * sharesPerGrid, 2);
    const sellAmount = roundTo(sellPrice * sharesPerGrid, 2);
    const buyFees = calcOrderFees(buyAmount, { ...feeOptions, side: 'buy' });
    const sellFees = calcOrderFees(sellAmount, { ...feeOptions, side: 'sell' });
    const spreadProfit = roundTo((sellPrice - buyPrice) * sharesPerGrid, 2);
    const netProfit = roundTo(spreadProfit - buyFees.total - sellFees.total, 2);
    const costWithFee = roundTo(buyAmount + buyFees.total, 2);

    levels.push({
      index: i + 1,
      price: buyPrice,
      buyPrice,
      sellPrice,
      shares: sharesPerGrid,
      buyAmount,
      sellAmount,
      spreadProfit,
      buyCommission: buyFees.commission,
      sellCommission: sellFees.commission,
      stampDuty: sellFees.stampDuty,
      buyFee: buyFees.total,
      sellFee: sellFees.total,
      feeTotal: roundTo(buyFees.total + sellFees.total, 2),
      netProfit,
      costWithFee,
      returnRate: costWithFee > 0 ? roundTo(netProfit / costWithFee, 6) : 0,
      priceGapPercent: buyPrice > 0 ? roundTo(((sellPrice - buyPrice) / buyPrice) * 100, 3) : 0,
      aboveBase: buyPrice >= basePrice,
      hasPosition: false,
      warning: netProfit <= 0 ? '单格净收益不足以覆盖手续费' : '',
    });
  }

  // 底仓覆盖：高于（含等于）基准价的档位，按 floor(初始底仓股数 / 每格股数) 从高到低标记为「已持仓」
  const coverable = sharesPerGrid > 0 ? Math.floor(initialShares / sharesPerGrid) : 0;
  const sellZone = levels.filter(level => level.aboveBase).sort((a, b) => b.buyPrice - a.buyPrice);
  sellZone.forEach((level, rank) => {
    level.hasPosition = rank < coverable;
  });

  const holdingLevels = levels.filter(level => level.hasPosition);
  const pendingLevels = levels.filter(level => !level.hasPosition);

  const pendingBuyAmount = roundTo(
    pendingLevels.reduce((sum, level) => sum + level.buyAmount + level.buyFee, 0),
    2,
  );
  const holdingAmount = roundTo(
    holdingLevels.reduce((sum, level) => sum + level.buyAmount + level.buyFee, 0),
    2,
  );
  // 含底仓最大占用 = 初始建仓均价 × 初始建仓股数 + 下方待买入极限备用金
  // 有底仓时用真实成本，无底仓信息时退回档位理论买入本金
  const baseCostAmount =
    initialShares > 0 && initialCost > 0
      ? roundTo(initialCost * initialShares, 2)
      : holdingAmount;
  const totalNetProfit = roundTo(levels.reduce((sum, level) => sum + level.netProfit, 0), 2);
  const avgNetProfit = levels.length ? roundTo(totalNetProfit / levels.length, 2) : 0;
  const avgReturnRate = levels.length
    ? roundTo(levels.reduce((sum, level) => sum + level.returnRate, 0) / levels.length, 6)
    : 0;
  const avgGapPercent = roundTo(
    levels.reduce((sum, level) => sum + level.priceGapPercent, 0) / levels.length,
    3,
  );
  const minLevel = levels.reduce(
    (worst, level) => (level.netProfit < worst.netProfit ? level : worst),
    levels[0],
  );

  const summary = {
    genMode: config.genMode,
    genModeLabel: GEN_MODE_LABELS[config.genMode],
    basePrice,
    upperPrice: config.upperPrice,
    lowerPrice: config.lowerPrice,
    mode: config.mode,
    modeLabel: GRID_MODE_LABELS[config.mode],
    gridCount: levels.length,
    sharesPerGrid,
    initialShares,
    coverable,
    avgGapPercent,
    baseLevelCount: levels.filter(level => level.aboveBase).length,
    gridLevelCount: levels.filter(level => !level.aboveBase).length,
    holdingLevelCount: holdingLevels.length,
    pendingLevelCount: pendingLevels.length,
    baseShares: holdingLevels.length * sharesPerGrid,
    baseAmount: roundTo(holdingLevels.reduce((sum, level) => sum + level.buyAmount, 0), 2),
    baseCommission: roundTo(holdingLevels.reduce((sum, level) => sum + level.buyFee, 0), 2),
    baseBuyCost: holdingAmount,
    maxAddShares: pendingLevels.length * sharesPerGrid,
    // 极限备用金 = 仅「待买入」档位的买入本金 + 买入手续费
    maxAddAmount: pendingBuyAmount,
    pendingBuyAmount,
    pendingBuyShares: pendingLevels.length * sharesPerGrid,
    holdingAmount,
    initialCost,
    baseCostAmount,
    maxTotalCapital: roundTo(baseCostAmount + pendingBuyAmount, 2),
    maxShares: levels.length * sharesPerGrid,
    totalNetProfit,
    avgNetProfit,
    avgReturnRate,
    minLevelProfit: minLevel.netProfit,
    minLevelIndex: minLevel.index,
    profitableLevelCount: levels.filter(level => level.netProfit > 0).length,
    lossLevelCount: levels.filter(level => level.netProfit <= 0).length,
  };

  const message = `已生成 ${levels.length} 档 ${
    GEN_MODE_LABELS[config.genMode]
  }网格，待买入 ${pendingLevels.length} 档需 ${roundTo(pendingBuyAmount, 2)} 元`;

  return { ok: true, message, errors: [], warnings, config, lines: prices, levels, summary };
}

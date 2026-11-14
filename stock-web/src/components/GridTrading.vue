<script setup>

import { computed, onMounted, reactive, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Delete, Plus, Refresh, TrendCharts } from '@element-plus/icons-vue';
import { buildGridPlan, calcOrderFees, defaultRatesFor, isEtfCode } from '../utils/gridCalculator.js';

const props = defineProps({
  portfolio: { type: Array, default: () => [] },
});

const GRID_API = '/api/grid';

const strategies = ref([]);
const selectedId = ref('');
const loading = ref(false);
const saving = ref(false);
const refreshing = ref(false);
const latestPrice = ref(0);
const changePct = ref(0);
const trades = ref([]);
const cycles = ref([]);
const ledgerView = ref('cycles');
const cycleFilter = ref('all');
const activeTab = ref('levels');
const importCode = ref('');

const form = reactive({
  symbol: '',
  name: '',
  basePrice: 0,
  upperPrice: 0,
  lowerPrice: 0,
  genMode: 'step',
  mode: 'geometric',
  gridCount: 10,
  stepType: 'percent',
  stepInput: 1.5,
  gridsUp: 5,
  gridsDown: 6,
  sharesPerGrid: 100,
  initialShares: 0,
  initialCost: 0,
  commissionRate: 0.00005,
  minCommission: 0.2,
  stampDutyRate: 0.0005,
  freeFive: false,
});

const tradeDialog = reactive({ visible: false, manual: false });
const tradeForm = reactive({
  type: 'buy',
  timestamp: '',
  price: 0,
  shares: 100,
  fee: 0,
  levelIndex: null,
  pairCycleId: '',
});
const tradeSubmitting = ref(false);

/* ---------------- 基础工具 ---------------- */

const num = value => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};
const round2 = value => Math.round((num(value) + Number.EPSILON) * 100) / 100;
const round3 = value => Math.round((num(value) + Number.EPSILON) * 1000) / 1000;
const fmtPrice = value => (num(value) > 0 ? num(value).toFixed(3) : '--');
const fmtMoney = value => {
  const amount = num(value);
  const sign = amount < 0 ? '-' : '';
  return `${sign}${Math.abs(amount).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};
const fmtPct = (value, digits = 2) => `${(num(value) * 100).toFixed(digits)}%`;

function nowText() {
  const date = new Date();
  const pad = value => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}:${pad(date.getSeconds())}`;
}

function feeOptions(side) {
  return {
    commissionRate: num(form.commissionRate),
    minCommission: num(form.minCommission),
    stampDutyRate: num(form.stampDutyRate),
    side,
  };
}

function estimateFee(price, shares, side) {
  return calcOrderFees(num(price) * num(shares), feeOptions(side)).total;
}

/* ---------------- 费率与标的联动 ---------------- */

const isEtf = computed(() => isEtfCode(form.symbol));

const commissionWan = computed({
  get: () => round2(num(form.commissionRate) * 10000),
  set: value => {
    form.commissionRate = num(value) / 10000;
  },
});

const stampWan = computed({
  get: () => round2(num(form.stampDutyRate) * 10000),
  set: value => {
    form.stampDutyRate = num(value) / 10000;
  },
});

/** 切换标的后按品种套用费率：ETF 免印花税并锁定，A 股恢复万 5 */
function applyRatesForSymbol(code) {
  const rates = defaultRatesFor(code);
  form.minCommission = rates.minCommission;
  if (isEtfCode(code)) {
    form.stampDutyRate = 0;
  } else if (!(num(form.stampDutyRate) > 0)) {
    form.stampDutyRate = rates.stampDutyRate;
  }
}

/** 价格上下限围绕现价自动铺开，保证现价落在区间内 */
function applyDefaultRange(price, cost = 0) {
  const current = num(price);
  if (!(current > 0)) return;
  const lower = round3(current * 0.85);
  const upper = round3(current * 1.15);
  form.lowerPrice = lower;
  form.upperPrice = upper;
  const preferCost = num(cost);
  form.basePrice = preferCost > lower && preferCost < upper ? round3(preferCost) : round3(current);
}

/* ---------------- 派生数据 ---------------- */

const currentStrategy = computed(() => strategies.value.find(item => item.id === selectedId.value) || null);

const effectivePrice = computed(() => {
  const quote = num(latestPrice.value);
  return quote > 0 ? quote : num(form.basePrice);
});

const plan = computed(() =>
  buildGridPlan({
    genMode: form.genMode,
    stepType: form.stepType,
    stepValue: num(form.stepInput),
    gridsUp: num(form.gridsUp),
    gridsDown: num(form.gridsDown),
    basePrice: num(form.basePrice),
    upperPrice: num(form.upperPrice),
    lowerPrice: num(form.lowerPrice),
    mode: form.mode,
    gridCount: Math.round(num(form.gridCount)),
    sharesPerGrid: Math.round(num(form.sharesPerGrid)),
    initialShares: Math.round(num(form.initialShares)),
    initialCost: num(form.initialCost) || num(form.basePrice),
    commissionRate: num(form.commissionRate),
    minCommission: num(form.minCommission),
    stampDutyRate: num(form.stampDutyRate),
  }),
);

const levels = computed(() => (plan.value.ok ? plan.value.levels : []));
const planError = computed(() => (plan.value.ok ? '' : plan.value.message));
const summary = computed(() => (plan.value.ok ? plan.value.summary : null));

// 步长模式：上下限由档位自动推算并实时回填（输入框禁用，仅作展示）
watch(
  () => [plan.value.config?.lowerPrice, plan.value.config?.upperPrice, form.genMode],
  () => {
    if (form.genMode !== 'step' || !plan.value.ok) return;
    const cfg = plan.value.config;
    if (num(cfg.lowerPrice) > 0) form.lowerPrice = num(cfg.lowerPrice);
    if (num(cfg.upperPrice) > 0) form.upperPrice = num(cfg.upperPrice);
  },
  { immediate: true },
);
const isFormEmpty = computed(
  () =>
    !String(form.symbol || '').trim() &&
    !(num(form.basePrice) > 0) &&
    !(num(form.upperPrice) > 0) &&
    !(num(form.lowerPrice) > 0),
);

const initialCost = computed(() => num(form.initialCost) || num(form.basePrice));
const initialShares = computed(() => Math.max(0, Math.round(num(form.initialShares))));
const initialAmount = computed(() => round2(initialCost.value * initialShares.value));

const buyTrades = computed(() => trades.value.filter(item => item.type === 'buy'));
const sellTrades = computed(() => trades.value.filter(item => item.type === 'sell'));
const sortedTrades = computed(() =>
  [...trades.value].sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp))),
);

// 累计已实现网格净利：只汇总 CLOSED 回合（未闭环的单边买入/高抛绝不计入）
const closedCycles = computed(() => cycles.value.filter(c => c.status === 'CLOSED'));
const openCycles = computed(() => cycles.value.filter(c => c.status !== 'CLOSED'));
const totalRealized = computed(() => {
  if (cycles.value.length) {
    return round2(closedCycles.value.reduce((sum, c) => sum + num(c.netProfit), 0));
  }
  // 兼容尚未生成回合的历史数据
  return round2(trades.value.reduce((sum, item) => sum + num(item.realizedProfit), 0));
});
const cycleProfitPerShare = computed(() => {
  const shares = initialShares.value;
  return shares > 0 ? totalRealized.value / shares : 0;
});
const closedCount = computed(() => closedCycles.value.length);
const waitingRebuyCycles = computed(() => openCycles.value.filter(c => c.status === 'WAITING_REBUY'));
const holdingCycles = computed(() => openCycles.value.filter(c => c.status === 'HOLDING_LONG'));
const totalFee = computed(() => round2(trades.value.reduce((sum, item) => sum + num(item.fee), 0)));

const currentShares = computed(() => {
  const bought = buyTrades.value.reduce((sum, item) => sum + num(item.shares), 0);
  const sold = sellTrades.value.reduce((sum, item) => sum + num(item.shares), 0);
  return Math.max(0, initialShares.value + bought - sold);
});

// 买入含费用、卖出扣费用的真实现金流
const buyCash = computed(() =>
  buyTrades.value.reduce((sum, item) => sum + num(item.price) * num(item.shares) + num(item.fee), 0),
);
const sellCash = computed(() =>
  sellTrades.value.reduce((sum, item) => sum + num(item.price) * num(item.shares) - num(item.fee), 0),
);
const netInvested = computed(() => round2(initialAmount.value + buyCash.value - sellCash.value));

// 动态摊薄成本（口径按需求）：(初始底仓成本价 × 初始底仓股数 - 累计已实现网格净利) / 初始底仓股数
const dilutedCost = computed(() => {
  if (initialShares.value > 0 && initialCost.value > 0) {
    return Math.max(0, (initialCost.value * initialShares.value - totalRealized.value) / initialShares.value);
  }
  // 无底仓时退化为「净投入 / 当前持仓」
  if (currentShares.value > 0) return Math.max(0, netInvested.value / currentShares.value);
  return 0;
});

const costDropPct = computed(() => {
  const base = initialCost.value;
  const cost = dilutedCost.value;
  if (!(base > 0) || !(cost > 0)) return 0;
  return (cost - base) / base;
});

// 第 4 张卡：距破网下限的安全垫 = (现价 - 下限) / 现价
const drawdownBuffer = computed(() => {
  const price = effectivePrice.value;
  const lower = num(form.lowerPrice);
  if (!(price > 0) || !(lower > 0)) return null;
  return (price - lower) / price;
});

// 浮亏总额（按摊薄成本衡量），用于换算「还需套利多少格」
const floatingLoss = computed(() => {
  const price = effectivePrice.value;
  const cost = dilutedCost.value;
  const shares = currentShares.value;
  if (!(price > 0) || !(cost > 0) || !(shares > 0)) return 0;
  return Math.max(0, (cost - price) * shares);
});

const avgGridProfit = computed(() => {
  const value = summary.value?.avgNetProfit || 0;
  return num(value);
});

const gridsToBreakeven = computed(() => {
  if (floatingLoss.value <= 0) return 0;
  if (!(avgGridProfit.value > 0)) return null;
  return floatingLoss.value / avgGridProfit.value;
});

const pendingBuyAmount = computed(() => num(summary.value?.pendingBuyAmount));
const pendingLevelCount = computed(() => num(summary.value?.pendingLevelCount));
const maxTotalCapital = computed(() => num(summary.value?.maxTotalCapital));
// 含底仓占用里的「真实底仓成本」（初始建仓均价 × 股数，未填时退回档位理论买入本金）
const baseCostAmount = computed(() => num(summary.value?.baseCostAmount));

// 距基准价偏离度（与当日涨跌幅严格区分）
const toBasePct = computed(() => {
  const base = num(form.basePrice);
  const price = effectivePrice.value;
  if (!(base > 0) || !(price > 0)) return null;
  return ((price - base) / base) * 100;
});

// 底仓分配提示：向上格数 × 每格股数 与初始建仓股数不一致时给出温和建议
const baseAllocationHint = computed(() => {
  if (form.genMode !== 'step') return null;
  const per = Math.round(num(form.sharesPerGrid));
  const init = Math.round(num(form.initialShares));
  if (!(per > 0) || !(init > 0)) return null;
  const up = Math.max(0, Math.round(num(form.gridsUp)));
  const allocated = up * per;
  if (allocated === init) return null;
  const suggest = Math.max(1, Math.ceil(init / per));
  const diff = Math.abs(init - allocated);
  return {
    up,
    allocated,
    suggest,
    over: allocated > init,
    text:
      allocated < init
        ? `当前上方 ${up} 格共分配 ${allocated.toLocaleString('zh-CN')} 股底仓（剩余 ${diff.toLocaleString('zh-CN')} 股未挂网，可将向上卖出格数设为 ${suggest} 格以全部分配）`
        : `当前上方 ${up} 格需 ${allocated.toLocaleString('zh-CN')} 股底仓（已超出 ${diff.toLocaleString('zh-CN')} 股，可减少向上卖出格数至 ${suggest} 格）`,
  };
});

/* ---- 档位持仓状态：底仓覆盖 + 成交滚动，标尺与表格共用同一份数据 ---- */

const levelHoldings = computed(() => {
  const map = new Map();
  for (const level of levels.value) {
    map.set(level.index, level.hasPosition ? Math.round(num(form.sharesPerGrid)) : 0);
  }
  // 回合口径：低吸持仓 +N，底仓高抛待接回 -N（比按成交流水滚动更贴近真实持仓）
  for (const cycle of cycles.value) {
    const index = Number(cycle.gridLevel);
    if (!map.has(index)) continue;
    const shares = num(cycle.openShares);
    if (cycle.status === 'HOLDING_LONG') {
      map.set(index, (map.get(index) || 0) + shares);
    } else if (cycle.status === 'WAITING_REBUY') {
      map.set(index, Math.max(0, (map.get(index) || 0) - shares));
    }
  }
  return map;
});

function levelStatus(level) {
  const held = levelHoldings.value.get(level.index) || 0;
  if (held > 0) return { label: '已持仓 · 待卖出', type: 'warning', code: 'holding', held };
  return { label: '待买入', type: 'primary', code: 'idle', held: 0 };
}

/* ---- 回合账本：筛选、排序与配对候选 ---- */

const cycleTypeLabel = cycle => (cycle.cycleType === 'SHORT_FIRST' ? '底仓高抛倒差价' : '低吸先买后卖');
const cycleTypeClass = cycle => (cycle.cycleType === 'SHORT_FIRST' ? 'cycle-tag-short' : 'cycle-tag-long');

const filteredCycles = computed(() => {
  const list = [...(cycles.value || [])];
  const filtered =
    cycleFilter.value === 'open' ? list.filter(c => c.status !== 'CLOSED')
    : cycleFilter.value === 'closed' ? list.filter(c => c.status === 'CLOSED')
    : list;
  return filtered.sort((a, b) => String(b.openTime || '').localeCompare(String(a.openTime || '')));
});

const cycleOpenCount = computed(() => openCycles.value.length);
const cycleClosedCount = computed(() => closedCycles.value.length);

/** 指定档位 + 平仓方向可配对的未结清回合（LIFO：最近的优先） */
function pairCandidates(direction, levelIndex) {
  const wanted = direction === 'buy' ? 'WAITING_REBUY' : 'HOLDING_LONG';
  return openCycles.value
    .filter(cycle => cycle.status === wanted)
    .filter(cycle => levelIndex === null || levelIndex === undefined || Number(cycle.gridLevel) === Number(levelIndex))
    .sort((a, b) => String(b.openTime || '').localeCompare(String(a.openTime || '')));
}

const tradePairCandidates = computed(() => pairCandidates(tradeForm.type, tradeForm.levelIndex));

const selectedPairCycle = computed(
  () => tradePairCandidates.value.find(c => c.id === tradeForm.pairCycleId) || null,
);

/** 弹窗实时预览：闭环净利 / 目标价 / 接回锁利 */
const tradePreview = computed(() => {
  const price = num(tradeForm.price);
  const shares = Math.round(num(tradeForm.shares));
  const fee = num(tradeForm.fee);
  if (!(price > 0) || !(shares > 0)) return null;
  const pair = selectedPairCycle.value;
  const level = matchedTradeLevel.value;

  if (pair) {
    const openPrice = num(pair.openPrice);
    const openFee = num(pair.openFee) * (shares / Math.max(1, num(pair.openShares)));
    const gross = tradeForm.type === 'buy' ? (openPrice - price) * shares : (price - openPrice) * shares;
    const net = round2(gross - openFee - fee);
    return {
      mode: 'close',
      pair,
      net,
      perShare: initialShares.value > 0 ? net / initialShares.value : 0,
    };
  }

  if (tradeForm.type === 'buy') {
    const target = level ? level.sellPrice : null;
    const est = target ? round2((target - price) * shares - fee * 2) : null;
    return { mode: 'open-long', target, est };
  }
  const target = level ? level.buyPrice : null;
  const est = target ? round2((price - target) * shares - fee * 2) : null;
  return { mode: 'open-short', target, est };
});

/** 未结清回合距目标价还差多少（%） */
function cycleTargetDiff(cycle) {
  const open = num(cycle.openPrice);
  const target = num(cycle.targetClosePrice);
  if (!(open > 0) || !(target > 0)) return null;
  return ((target - open) / open) * 100;
}

/** 未结清回合按目标价成交的预计净利 */
function cycleEstimatedProfit(cycle) {
  const open = num(cycle.openPrice);
  const target = num(cycle.targetClosePrice);
  const shares = num(cycle.openShares);
  const fee = num(cycle.openFee);
  if (!(open > 0) || !(target > 0) || !(shares > 0)) return null;
  const sell = cycle.openDirection === 'BUY' ? target : open;
  const buy = cycle.openDirection === 'BUY' ? open : target;
  return round2((sell - buy) * shares - fee * 2);
}

/** 撤销：闭环的撤平仓腿，未闭环的撤开仓腿，服务端会重算回合状态 */
async function revokeCycle(cycle) {
  const tradeId = cycle.status === 'CLOSED' ? cycle.closeTradeId : cycle.openTradeId;
  if (!tradeId) return;
  try {
    await ElMessageBox.confirm(
      cycle.status === 'CLOSED'
        ? `撤销该回合的平仓腿后，回合将退回「${cycle.openDirection === 'BUY' ? '持仓待卖' : '高抛待接回'}」，确定？`
        : '撤销开仓腿将删除该回合及其成交记录，确定？',
      '撤销记录',
      { type: 'warning', confirmButtonText: '撤销', cancelButtonText: '取消' },
    );
  } catch {
    return;
  }
  const previous = trades.value;
  trades.value = trades.value.filter(item => item.id !== tradeId);
  const ok = await saveStrategy(true);
  if (!ok) {
    trades.value = previous;
    return;
  }
  ElMessage.success('已撤销，回合状态已回退');
}

/* ---- 现价分割线 + 倒序行 ---- */

const priceDivider = computed(() => {
  const price = effectivePrice.value;
  if (!(price > 0) || !levels.value.length) return null;
  // 最近买档：只在「待买入」档位里取最大的买入触发价
  const buyCandidates = levels.value.filter(
    level => levelStatus(level).code === 'idle' && level.buyPrice <= price,
  );
  // 最近卖档：只在「已持仓·待卖出」档位里取最小的卖出触发价（严禁取未持仓档的卖出价）
  const sellCandidates = levels.value.filter(
    level => levelStatus(level).code === 'holding' && level.sellPrice >= price,
  );
  const nearestBuy = buyCandidates.length
    ? buyCandidates.reduce((best, level) => (level.buyPrice > best.buyPrice ? level : best))
    : null;
  const nearestSell = sellCandidates.length
    ? sellCandidates.reduce((best, level) => (level.sellPrice < best.sellPrice ? level : best))
    : null;
  const toBuy = nearestBuy ? ((price - nearestBuy.buyPrice) / price) * 100 : null;
  const toSell = nearestSell ? ((nearestSell.sellPrice - price) / price) * 100 : null;
  return {
    price,
    basePrice: num(form.basePrice),
    toBuy,
    toSell,
    nearestBuyIndex: nearestBuy ? nearestBuy.index : null,
    nearestSellIndex: nearestSell ? nearestSell.index : null,
    text: `>>> 当前现价 ¥${price.toFixed(3)}（基准价 ¥${num(form.basePrice).toFixed(3)} | 距离最近买档 ${
      toBuy === null ? '--' : `-${Math.abs(toBuy).toFixed(2)}%`
    } / 距离最近卖档 ${toSell === null ? '--' : `+${Math.abs(toSell).toFixed(2)}%`}）<<<`,
  };
});

// 从高到低排列，并在现价所在相邻两档之间插入分割提示行
const levelRows = computed(() => {
  const ordered = [...levels.value].sort((a, b) => b.buyPrice - a.buyPrice);
  const price = effectivePrice.value;
  const divider = priceDivider.value;
  const rows = [];
  let placed = false;
  for (const level of ordered) {
    if (!placed && divider && price > 0 && price >= level.buyPrice) {
      rows.push({ __divider: true, __key: 'divider', text: divider.text });
      placed = true;
    }
    rows.push({ ...level, __key: `level-${level.index}` });
  }
  if (!placed && divider) {
    rows.push({ __divider: true, __key: 'divider', text: divider.text });
  }
  return rows;
});

const LEVEL_TABLE_COLUMNS = 10;

function levelSpanMethod({ row, columnIndex }) {
  if (row.__divider) {
    return columnIndex === 0 ? { rowspan: 1, colspan: LEVEL_TABLE_COLUMNS } : { rowspan: 1, colspan: 0 };
  }
  return { rowspan: 1, colspan: 1 };
}

function levelRowClass({ row }) {
  if (row.__divider) return 'price-divider-row';
  const divider = priceDivider.value;
  if (!divider) return '';
  // 现价相邻的一买一卖（盘口最近两档）淡色高亮，方便实盘盯价
  if (row.index === divider.nearestSellIndex) return 'row-nearest-sell';
  if (row.index === divider.nearestBuyIndex) return 'row-nearest-buy';
  return '';
}

/* ---- 价格标尺（圆点状态与表格完全一致） ---- */

const ruler = computed(() => {
  // 步长模式的上下限由档位派生，优先用 plan 的实际区间
  const lower = plan.value.ok ? num(plan.value.config.lowerPrice) : num(form.lowerPrice);
  const upper = plan.value.ok ? num(plan.value.config.upperPrice) : num(form.upperPrice);
  const price = effectivePrice.value;
  if (!(lower > 0) || !(upper > lower)) return null;

  const left = 54;
  const right = 846;
  const axisY = 96;
  const span = right - left;
  const xOf = value => left + ((num(value) - lower) / (upper - lower)) * span;
  const clampX = value => Math.min(right, Math.max(left, value));

  // 圆点锚点：待买入画在买入触发价，已持仓画在卖出触发价（右端不出现空白）
  const dots = levels.value.map(level => {
    const status = levelStatus(level);
    const holding = status.code === 'holding';
    const anchor = holding ? level.sellPrice : level.buyPrice;
    return {
      key: `lv-${level.index}`,
      index: level.index,
      price: anchor,
      x: clampX(xOf(anchor)),
      status: status.code,
      label: `第 ${level.index} 档 ${anchor.toFixed(3)}${holding ? ' · 已持仓待卖出' : ' · 待买入'}`,
    };
  });

  const ticks = (plan.value.ok ? plan.value.lines : [lower, upper]).map((value, index) => ({
    key: `tick-${index}-${value}`,
    price: value,
    x: xOf(value),
  }));

  const priceX = price > 0 ? clampX(xOf(price)) : null;
  const cost = dilutedCost.value;
  const costX = cost > 0 ? clampX(xOf(cost)) : null;
  const baseX = clampX(xOf(num(form.basePrice)));

  return {
    left,
    right,
    axisY,
    dots,
    ticks,
    priceX,
    costX,
    baseX,
    showBaseLabel: baseX - left > 70 && right - baseX > 70 && (costX === null || Math.abs(costX - baseX) > 64),
    showCostLabel: costX !== null && Math.abs(costX - baseX) > 64 && costX - left > 40 && right - costX > 40,
    lower,
    upper,
    outOfRange: price > 0 && (price < lower || price > upper),
  };
});

const changeClass = computed(() => {
  if (changePct.value > 0) return 'up';
  if (changePct.value < 0) return 'down';
  return 'flat';
});

/* ---------------- 策略读写 ---------------- */

function applyStrategy(item) {
  if (!item) return;
  selectedId.value = item.id;
  // 步长单位由网格模式唯一决定：等比→百分比(%)，等差→固定差价(元)
  const savedType = item.stepType === 'amount' || item.stepType === 'fixed' ? 'amount' : 'percent';
  const stepType = item.mode === 'geometric' ? 'percent' : 'amount';
  const savedBase = num(item.basePrice);
  let stepInput = num(item.stepInput) || 1.5;
  // 历史数据单位与新模式不一致时按基准价换算，避免 1.5% 被当成 1.5 元
  if (savedBase > 0 && savedType !== stepType && stepInput > 0) {
    stepInput =
      stepType === 'percent'
        ? Number(((stepInput / savedBase) * 100).toFixed(3))
        : Number(((savedBase * stepInput) / 100).toFixed(3));
  }
  Object.assign(form, {
    symbol: String(item.symbol || ''),
    name: String(item.name || ''),
    basePrice: num(item.basePrice),
    upperPrice: num(item.upperPrice),
    lowerPrice: num(item.lowerPrice),
    genMode: item.genMode === 'step' ? 'step' : 'range',
    mode: item.mode === 'geometric' ? 'geometric' : 'arithmetic',
    gridCount: Math.max(1, Math.round(num(item.gridCount)) || 10),
    stepType,
    stepInput,
    gridsUp: Math.max(0, Math.round(num(item.gridsUp)) || 5),
    gridsDown: Math.max(0, Math.round(num(item.gridsDown)) || 6),
    sharesPerGrid: Math.max(100, Math.round(num(item.sharesPerGrid)) || 100),
    initialShares: Math.max(0, Math.round(num(item.initialShares))),
    initialCost: num(item.initialCost) || num(item.basePrice),
    commissionRate: item.commissionRate === undefined ? 0.00005 : num(item.commissionRate),
    minCommission:
      item.minCommission === undefined || item.minCommission === null
        ? item.freeFive
          ? 0
          : 0.2
        : num(item.minCommission),
    stampDutyRate:
      item.stampDutyRate === undefined ? (isEtfCode(item.symbol) ? 0 : 0.0005) : num(item.stampDutyRate),
    freeFive: Boolean(item.freeFive),
  });
  trades.value = (Array.isArray(item.trades) ? item.trades : []).map(trade => ({ ...trade }));
  cycles.value = (Array.isArray(item.cycles) ? item.cycles : []).map(cycle => ({ ...cycle }));
  latestPrice.value = num(item.lastPrice) || num(item.basePrice);
  changePct.value = 0;
  importCode.value = '';
  activeTab.value = 'levels';

  if (isEtfCode(form.symbol)) form.stampDutyRate = 0;
  if (/^\d{6}$/.test(form.symbol)) void refreshPrice({ silent: true });
}

function startNewStrategy() {
  selectedId.value = '';
  Object.assign(form, {
    symbol: '',
    name: '',
    basePrice: 0,
    upperPrice: 0,
    lowerPrice: 0,
    genMode: 'step',
    mode: 'geometric',
    gridCount: 10,
    stepType: 'percent',
    stepInput: 1.5,
    gridsUp: 5,
    gridsDown: 6,
    sharesPerGrid: 100,
    initialShares: 0,
    initialCost: 0,
    commissionRate: 0.00005,
    minCommission: 0.2,
    stampDutyRate: 0.0005,
    freeFive: false,
  });
  trades.value = [];
  cycles.value = [];
  latestPrice.value = 0;
  changePct.value = 0;
  importCode.value = '';
  activeTab.value = 'levels';
}

async function loadStrategies() {
  loading.value = true;
  try {
    const resp = await fetch(GRID_API, { method: 'GET' });
    const data = await resp.json().catch(() => []);
    if (!resp.ok) throw new Error(data?.message || `HTTP ${resp.status}`);
    strategies.value = Array.isArray(data) ? data : [];
    if (!strategies.value.length) {
      startNewStrategy();
      return;
    }
    const kept = strategies.value.find(item => item.id === selectedId.value);
    applyStrategy(kept || strategies.value[0]);
  } catch (error) {
    ElMessage.error(`加载网格策略失败：${error.message}`);
  } finally {
    loading.value = false;
  }
}

async function saveStrategy(silent = false) {
  if (!/^\d{6}$/.test(String(form.symbol || '').trim())) {
    ElMessage.error('请填写 6 位股票代码');
    return false;
  }
  if (!plan.value.ok) {
    ElMessage.error(plan.value.message);
    return false;
  }

  const id = selectedId.value || `grid_${Date.now()}`;
  const payload = {
    id,
    symbol: String(form.symbol).trim(),
    name: String(form.name || '').trim() || String(form.symbol).trim(),
    basePrice: num(form.basePrice),
    upperPrice: num(form.upperPrice),
    lowerPrice: num(form.lowerPrice),
    genMode: form.genMode,
    mode: form.mode,
    gridCount: Math.round(num(form.gridCount)),
    stepType: form.stepType,
    stepInput: num(form.stepInput),
    gridsUp: Math.round(num(form.gridsUp)),
    gridsDown: Math.round(num(form.gridsDown)),
    sharesPerGrid: Math.round(num(form.sharesPerGrid)),
    initialShares: Math.max(0, Math.round(num(form.initialShares))),
    initialCost: num(form.initialCost) || num(form.basePrice),
    commissionRate: num(form.commissionRate),
    minCommission: num(form.minCommission),
    stampDutyRate: num(form.stampDutyRate),
    freeFive: num(form.minCommission) === 0,
    lastPrice: effectivePrice.value,
    trades: trades.value,
  };

  saving.value = true;
  try {
    const resp = await fetch(GRID_API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(json?.message || `HTTP ${resp.status}`);

    const saved = Array.isArray(json?.data) ? json.data.find(item => item.id === id) : null;
    const next = saved || payload;
    strategies.value = [...strategies.value.filter(item => item.id !== id), next];
    selectedId.value = id;
    // 服务端会重建回合账本并把闭环净利回写到平仓腿，这里以服务端结果为准
    if (saved) {
      trades.value = (Array.isArray(saved.trades) ? saved.trades : []).map(t => ({ ...t }));
      cycles.value = (Array.isArray(saved.cycles) ? saved.cycles : []).map(c => ({ ...c }));
    }
    if (!silent) ElMessage.success('策略已保存');
    return true;
  } catch (error) {
    ElMessage.error(`保存策略失败：${error.message}`);
    return false;
  } finally {
    saving.value = false;
  }
}

async function removeStrategy() {
  if (!currentStrategy.value) return;
  try {
    await ElMessageBox.confirm(
      `确定删除策略「${currentStrategy.value.name || currentStrategy.value.symbol}」及其全部成交记录吗？`,
      '删除策略',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    );
  } catch {
    return;
  }

  const remaining = strategies.value.filter(item => item.id !== selectedId.value);
  try {
    const resp = await fetch(GRID_API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(remaining),
    });
    if (!resp.ok) {
      const json = await resp.json().catch(() => ({}));
      throw new Error(json?.message || `HTTP ${resp.status}`);
    }
    strategies.value = remaining;
    if (remaining.length) applyStrategy(remaining[0]);
    else startNewStrategy();
    ElMessage.success('策略已删除');
  } catch (error) {
    ElMessage.error(`删除失败：${error.message}`);
  }
}

/* ---------------- 行情 ---------------- */

let lastSymbol = '';

async function refreshPrice(options = {}) {
  const code = String(form.symbol || '').trim();
  if (!/^\d{6}$/.test(code)) {
    if (!options.silent) ElMessage.warning('请先填写 6 位股票代码');
    return;
  }

  refreshing.value = true;
  try {
    const resp = await fetch(`/api/quote?symbol=${encodeURIComponent(code)}`, { method: 'GET' });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(json?.message || `HTTP ${resp.status}`);
    const item = Array.isArray(json?.diff) ? json.diff[0] : null;
    if (!item) throw new Error(json?.message || '行情数据为空');

    const price = Number(item.f2);
    if (price > 0) latestPrice.value = round3(price);
    changePct.value = Number.isFinite(Number(item.f3)) ? Number(item.f3) : 0;
    if (!form.name && item.f14) form.name = String(item.f14);

    if (json?.message) {
      if (!options.silent) ElMessage.warning(json.message);
    } else if (!options.silent) {
      ElMessage.success(`现价已更新：${fmtPrice(latestPrice.value)}`);
    }
  } catch (error) {
    if (!options.silent) ElMessage.error(`刷新现价失败：${error.message}`);
  } finally {
    refreshing.value = false;
  }
}

async function handleSymbolBlur() {
  const code = String(form.symbol || '').trim();
  if (!/^\d{6}$/.test(code)) return;
  form.symbol = code;

  const switched = code !== lastSymbol;
  lastSymbol = code;
  applyRatesForSymbol(code);

  try {
    const resp = await fetch(`/api/stock-name?codes=${code}`, { method: 'GET' });
    const json = await resp.json().catch(() => ({}));
    const info = json?.items?.[code];
    if (info?.name) form.name = String(info.name);
    if (Number(info?.price) > 0) latestPrice.value = round3(Number(info.price));
  } catch {
    // 名称拉取失败不阻塞后续操作
  }

  await refreshPrice({ silent: true });
  // 切换标的时按现价重铺区间，避免现价飞出默认区间
  if (switched && num(latestPrice.value) > 0) applyDefaultRange(latestPrice.value);
  if (form.name) ElMessage.success(`已识别：${form.name}`);
}

function handlePortfolioImport(code) {
  const row = props.portfolio.find(item => String(item.code) === String(code));
  if (!row) return;

  form.symbol = String(row.code || '');
  form.name = String(row.name || form.name || '');
  lastSymbol = form.symbol;
  applyRatesForSymbol(form.symbol);

  const price = num(row.currentPrice);
  const cost = num(row.costPrice);
  if (price > 0) latestPrice.value = price;
  applyDefaultRange(price, cost);
  form.initialShares = Math.max(0, Math.round(num(row.quantity)));
  form.initialCost = cost || price;
  // 导入即视为新计划：统一回到默认组合（按基准价步长 + 等比网格 → 百分比步长）
  const prevType = form.stepType;
  const prevStep = num(form.stepInput);
  const importBase = num(form.basePrice);
  form.genMode = 'step';
  form.mode = 'geometric';
  form.stepType = 'percent';
  if (prevType === 'amount' && importBase > 0 && prevStep > 0) {
    // 固定差价换百分比，保持原有网格密度
    form.stepInput = Number(((prevStep / importBase) * 100).toFixed(3));
  } else if (!(prevStep > 0)) {
    form.stepInput = 1.5;
  }

  ElMessage.success(`已导入 ${row.name || row.code}：${form.initialShares} 股 / 成本 ${fmtPrice(form.initialCost)}`);
}

function setBasePriceToLatest() {
  const price = effectivePrice.value;
  if (!(price > 0)) {
    ElMessage.warning('还没有可用的现价');
    return;
  }
  form.basePrice = round3(price);
}

function handlePreview() {
  if (!plan.value.ok) {
    ElMessage.error(plan.value.message);
    return;
  }
  void refreshPrice({ silent: true });
  ElMessage.success(
    `共 ${levels.value.length} 档，待买入 ${pendingLevelCount.value} 档需 ${fmtMoney(pendingBuyAmount.value)} 元`,
  );
}

/* ---------------- 成交记录 ---------------- */

const levelsDescending = computed(() => [...levels.value].sort((a, b) => b.buyPrice - a.buyPrice));

function resolveTradeLevel() {
  if (tradeForm.levelIndex !== null && tradeForm.levelIndex !== undefined && tradeForm.levelIndex !== '') {
    return levels.value.find(level => level.index === Number(tradeForm.levelIndex)) || null;
  }
  const price = num(tradeForm.price);
  if (!(price > 0) || !levels.value.length) return null;
  // 自动匹配：买入看买入触发价，卖出看卖出触发价
  return levels.value.reduce((best, level) => {
    const target = tradeForm.type === 'buy' ? level.buyPrice : level.sellPrice;
    const bestTarget = tradeForm.type === 'buy' ? best.buyPrice : best.sellPrice;
    return Math.abs(target - price) < Math.abs(bestTarget - price) ? level : best;
  }, levels.value[0]);
}

const matchedTradeLevel = computed(() => resolveTradeLevel());

function openTradeDialog(type, level = null, cycle = null) {
  if (!selectedId.value) {
    ElMessage.warning('请先保存策略，再记录实盘成交');
    return;
  }
  tradeDialog.manual = !level;
  tradeForm.type = type;
  tradeForm.timestamp = nowText();
  tradeForm.levelIndex = level ? level.index : cycle ? Number(cycle.gridLevel) : null;
  tradeForm.price = level
    ? type === 'buy'
      ? level.buyPrice
      : level.sellPrice
    : cycle
      ? num(cycle.targetClosePrice) || num(cycle.openPrice)
      : effectivePrice.value || 0;
  tradeForm.shares = level
    ? level.shares
    : cycle
      ? Math.round(num(cycle.openShares))
      : Math.max(100, Math.round(num(form.sharesPerGrid)));
  // 默认选中同档最近的未结清回合（与后端 LIFO 自动配对保持一致）
  const candidates = pairCandidates(type, tradeForm.levelIndex);
  tradeForm.pairCycleId = cycle ? cycle.id : candidates.length ? candidates[0].id : '';
  tradeForm.fee = estimateFee(tradeForm.price, tradeForm.shares, type);
  tradeDialog.visible = true;
}

/** 一键填入平仓：锁定回合、目标价与股数 */
function fillCloseFromCycle(cycle) {
  const type = cycle.openDirection === 'BUY' ? 'sell' : 'buy';
  const level = levels.value.find(item => item.index === Number(cycle.gridLevel)) || null;
  openTradeDialog(type, level, cycle);
}

watch(
  () => [tradeForm.price, tradeForm.shares, tradeForm.type],
  () => {
    if (!tradeDialog.visible) return;
    tradeForm.fee = estimateFee(tradeForm.price, tradeForm.shares, tradeForm.type);
  },
);

// 步长单位与网格模式一一绑定：等比→百分比(%)，等差→固定差价(元)
// 切换模式时按基准价换算数值，保证换算法后网格密度基本不变
watch(
  () => form.mode,
  mode => {
    const nextType = mode === 'geometric' ? 'percent' : 'amount';
    if (form.stepType === nextType) return;
    const base = num(form.basePrice);
    const value = num(form.stepInput);
    if (base > 0 && value > 0) {
      form.stepInput =
        nextType === 'percent'
          ? Number(((value / base) * 100).toFixed(3))
          : Number(((base * value) / 100).toFixed(3));
    }
    form.stepType = nextType;
  },
);

// 初始建仓均价默认跟随基准价，用户手动改过就不再覆盖
watch(
  () => form.basePrice,
  value => {
    if (!(num(form.initialCost) > 0)) form.initialCost = num(value);
  },
);

async function submitTrade() {
  const price = num(tradeForm.price);
  const shares = Math.round(num(tradeForm.shares));
  const fee = Math.max(0, num(tradeForm.fee));

  if (!(price > 0)) {
    ElMessage.error('成交价必须大于 0');
    return;
  }
  if (!(shares > 0) || shares % 100 !== 0) {
    ElMessage.error('成交股数必须是 100 的整数倍');
    return;
  }
  if (!tradeForm.timestamp) {
    ElMessage.error('请填写成交时间');
    return;
  }

  const level = resolveTradeLevel();
  const record = {
    id: `t_${Date.now()}`,
    timestamp: tradeForm.timestamp,
    type: tradeForm.type,
    price,
    shares,
    fee,
    realizedProfit: 0,
    levelIndex: level ? level.index : null,
    // 为空则交给后端按 LIFO 自动配对；指定则强制与该回合闭环
    pairCycleId: tradeForm.pairCycleId || null,
  };

  const previous = trades.value;
  trades.value = [...trades.value, record].sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)));

  tradeSubmitting.value = true;
  const ok = await saveStrategy(true);
  tradeSubmitting.value = false;

  if (!ok) {
    trades.value = previous;
    return;
  }

  tradeDialog.visible = false;
  activeTab.value = 'trades';
  const closed = cycles.value.find(c => c.closeTradeId === record.id);
  ElMessage.success(
    record.type === 'buy'
      ? closed
        ? `已接回平仓，本回合落袋 ${fmtMoney(closed.netProfit)} 元`
        : `已记录买入 ${shares} 股 @ ${fmtPrice(price)}（等待涨到目标价卖出）`
      : closed
        ? `已卖出平仓，本回合落袋 ${fmtMoney(closed.netProfit)} 元`
        : `已记录卖出 ${shares} 股 @ ${fmtPrice(price)}（等待回落接回）`,
  );
}

async function deleteTrade(row) {
  try {
    await ElMessageBox.confirm(
      `删除 ${row.timestamp} 的${row.type === 'buy' ? '买入' : '卖出'} ${row.shares} 股记录？`,
      '删除成交',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    );
  } catch {
    return;
  }

  const previous = trades.value;
  trades.value = trades.value.filter(item => item.id !== row.id);
  const ok = await saveStrategy(true);
  if (!ok) {
    trades.value = previous;
    return;
  }
  ElMessage.success('已删除该笔成交');
}

/* ---------------- 历史回测 ---------------- */

const backtestDialog = reactive({ visible: false });
const backtestForm = reactive({
  code: '',
  basePrice: 0,
  stepPct: 1.0,
  gridType: 'geometric',
  stepType: 'percent',
  stepValue: 0.05,
  gridAmount: 10000,
  maxGrids: 10,
  initialCash: 100000,
  commissionRate: 0.00005,
  minCommission: 0.2,
  stampDutyRate: 0.0005,
  range: '2y',
  startDate: '',
  endDate: '',
});
const backtestLoading = ref(false);
const backtestResult = ref(null);

const backtestIsEtf = computed(() => isEtfCode(backtestForm.code));

/* 回测可选标的：持仓列表（code + name），支持下拉选择任意持仓代码 */
const portfolioOptions = computed(() =>
  (props.portfolio || [])
    .map(item => ({ code: String(item?.code || '').trim(), name: String(item?.name || '') }))
    .filter(item => /^\d{6}$/.test(item.code)),
);

watch(
  () => backtestForm.code,
  code => {
    if (!/^\d{6}$/.test(String(code || ''))) return;
    const rates = defaultRatesFor(code);
    backtestForm.stampDutyRate = rates.stampDutyRate;
    backtestForm.minCommission = rates.minCommission;
  },
);

function pad2(value) {
  return String(value).padStart(2, '0');
}
function formatDateStr(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function setBacktestRange(range) {
  backtestForm.range = range;
  if (range === 'custom') return;
  const end = new Date();
  const years = range === '1y' ? 1 : range === '2y' ? 2 : 3;
  const start = new Date(end.getTime());
  start.setFullYear(start.getFullYear() - years);
  backtestForm.startDate = formatDateStr(start);
  backtestForm.endDate = formatDateStr(end);
}

function openBacktest() {
  const code = String(form.symbol || '').trim();
  // 预填当前策略代码（若有），否则留空由用户在抽屉中选择/输入
  backtestForm.code = /^\d{6}$/.test(code) ? code : '';
  backtestForm.basePrice = num(form.basePrice) || num(effectivePrice.value) || 0;
  backtestForm.gridType = form.mode;
  // 回测步长单位同样绑定算法：等比=%（复利），等差=元（固定差价）
  const btPercent = form.mode === 'geometric';
  backtestForm.stepType = btPercent ? 'percent' : 'amount';
  backtestForm.stepPct = btPercent ? num(form.stepInput) : 1.0;
  backtestForm.stepValue = btPercent ? 0.05 : num(form.stepInput);
  backtestForm.gridAmount = 10000;
  backtestForm.maxGrids = Math.max(1, Math.round(num(form.gridCount))) || 10;
  backtestForm.initialCash = 100000;
  backtestForm.commissionRate = num(form.commissionRate) || 0.00005;
  backtestForm.minCommission = num(form.minCommission);
  backtestForm.stampDutyRate = isEtfCode(code) ? 0 : num(form.stampDutyRate);
  setBacktestRange('2y');
  backtestResult.value = null;
  backtestDialog.visible = true;
}

async function runBacktest() {
  if (!/^\d{6}$/.test(String(backtestForm.code || '').trim())) {
    ElMessage.warning('请选择或输入 6 位股票代码');
    return;
  }
  if (!(num(backtestForm.basePrice) > 0)) {
    ElMessage.warning('请填写基准价');
    return;
  }
  backtestLoading.value = true;
  backtestResult.value = null;
  try {
    const resp = await fetch('/api/grid/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code: backtestForm.code,
        start_date: backtestForm.startDate,
        end_date: backtestForm.endDate,
        initial_cash: num(backtestForm.initialCash),
        base_price: num(backtestForm.basePrice),
        step_pct: backtestForm.gridType === 'geometric' ? num(backtestForm.stepPct) : 1.0,
        grid_type: backtestForm.gridType,
        step_type: backtestForm.gridType === 'geometric' ? 'percent' : 'amount',
        step_value: backtestForm.gridType === 'geometric' ? 0 : num(backtestForm.stepValue),
        grid_amount: num(backtestForm.gridAmount),
        max_grids: Math.round(num(backtestForm.maxGrids)),
        commission_rate: num(backtestForm.commissionRate),
        min_commission: num(backtestForm.minCommission),
        stamp_duty_rate: num(backtestForm.stampDutyRate),
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data?.detail || `HTTP ${resp.status}`);
    backtestResult.value = data;
  } catch (error) {
    ElMessage.error(`回测失败：${error.message || error}`);
  } finally {
    backtestLoading.value = false;
  }
}

const backtestChart = computed(() => {
  if (!backtestResult.value) return null;
  const eq = backtestResult.value.equity || [];
  const bench = backtestResult.value.benchmark_equity || [];
  if (!eq.length) return null;
  const base = eq[0].total_equity || 1;
  const W = 660;
  const H = 280;
  const PAD = { l: 44, r: 12, t: 14, b: 26 };
  const innerW = W - PAD.l - PAD.r;
  const innerH = H - PAD.t - PAD.b;
  const gridVals = eq.map(e => e.total_equity / base);
  const benchVals = bench.map(e => e.total_equity / base);
  const all = benchVals.length ? [...gridVals, ...benchVals] : gridVals;
  const minV = Math.min(...all);
  const maxV = Math.max(...all);
  const range = maxV - minV || 1;
  const x = i => PAD.l + (i / Math.max(eq.length - 1, 1)) * innerW;
  const y = v => PAD.t + (1 - (v - minV) / range) * innerH;
  const gridPath = gridVals.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const benchPath = benchVals.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  return { W, H, gridPath, benchPath, minV, maxV, hasBench: !!benchVals.length };
});

onMounted(loadStrategies);

</script>

<template>
  <div v-loading="loading" class="grid-page">
    <!-- 顶部操作栏 -->
    <section class="grid-toolbar">
      <div class="toolbar-group">
        <el-select
          v-model="selectedId"
          class="strategy-select"
          placeholder="选择已保存的策略"
          :disabled="!strategies.length"
          @change="id => applyStrategy(strategies.find(item => item.id === id))"
        >
          <el-option
            v-for="item in strategies"
            :key="item.id"
            :label="`${item.name || item.symbol || '未命名'} · ${item.symbol || '-'}`"
            :value="item.id"
          />
        </el-select>
        <el-button :icon="Plus" @click="startNewStrategy">新建策略</el-button>
        <el-button :icon="Delete" type="danger" plain :disabled="!currentStrategy" @click="removeStrategy">
          删除当前策略
        </el-button>
      </div>

      <div class="toolbar-group">
        <div class="price-chip">
          <span class="chip-name">{{ form.name || form.symbol || '未选择标的' }}</span>
          <strong :class="['chip-price', changeClass]">{{ fmtPrice(effectivePrice) }}</strong>
          <span :class="['chip-change', changeClass]">
            {{ changePct > 0 ? '+' : '' }}{{ num(changePct).toFixed(2) }}%
          </span>
        </div>
        <el-button :icon="Refresh" type="success" plain :loading="refreshing" @click="refreshPrice()">
          刷新现价
        </el-button>
      </div>
    </section>

    <!-- 参数配置 -->
    <el-card shadow="never" class="panel">
      <template #header>
        <div class="panel-header">
          <div class="panel-header-left">
            <span>策略参数</span>
            <span v-if="currentStrategy" class="panel-tag">已保存 · {{ currentStrategy.symbol }}</span>
          </div>
          <el-button class="backtest-trigger" size="small" type="warning" plain @click="openBacktest">
            📊 历史回测验证
          </el-button>
        </div>
      </template>

      <div class="form-grid">
        <div class="field">
          <label>股票代码</label>
          <el-input v-model="form.symbol" maxlength="6" placeholder="600519" @blur="handleSymbolBlur" />
        </div>
        <div class="field">
          <label>股票名称</label>
          <el-input v-model="form.name" placeholder="输入代码后自动填充" />
        </div>
        <div class="field">
          <label>最新现价</label>
          <el-input :model-value="fmtPrice(effectivePrice)" readonly />
        </div>
        <div class="field">
          <label>从现有持仓导入</label>
          <el-select v-model="importCode" placeholder="选择持仓标的" clearable @change="handlePortfolioImport">
            <el-option
              v-for="row in portfolio"
              :key="row.code"
              :label="`${row.name || row.code}（${row.code}）`"
              :value="row.code"
            />
          </el-select>
        </div>

        <div class="field">
          <div class="field-head">
            <label>价格上限（元）</label>
            <span v-if="form.genMode === 'step'" class="auto-tag">自动推算</span>
          </div>
          <el-input-number
            v-model="form.upperPrice"
            :min="0"
            :precision="3"
            :step="0.1"
            :disabled="form.genMode === 'step'"
            controls-position="right"
          />
        </div>
        <div class="field">
          <div class="field-head">
            <label>价格下限（元）</label>
            <span v-if="form.genMode === 'step'" class="auto-tag">自动推算</span>
          </div>
          <el-input-number
            v-model="form.lowerPrice"
            :min="0"
            :precision="3"
            :step="0.1"
            :disabled="form.genMode === 'step'"
            controls-position="right"
          />
        </div>
        <div class="field">
          <div class="field-head">
            <label>基准价（元）</label>
            <el-button link type="primary" size="small" @click="setBasePriceToLatest">用现价</el-button>
          </div>
          <el-input-number v-model="form.basePrice" :min="0" :precision="2" :step="0.1" controls-position="right" />
        </div>
        <div class="field">
          <label>网格生成方式</label>
          <el-radio-group v-model="form.genMode">
            <el-radio-button value="range">按区间等分</el-radio-button>
            <el-radio-button value="step">按基准价步长</el-radio-button>
          </el-radio-group>
        </div>

        <div class="field">
          <label>网格模式</label>
          <el-radio-group v-model="form.mode">
            <el-radio-button value="arithmetic">等差网格</el-radio-button>
            <el-radio-button value="geometric">等比网格</el-radio-button>
          </el-radio-group>
        </div>

        <div v-if="form.genMode === 'range'" class="field">
          <label>网格数量（格）</label>
          <el-input-number v-model="form.gridCount" :min="1" :max="200" :step="1" step-strictly controls-position="right" />
        </div>

        <template v-if="form.genMode === 'step'">
          <div class="field">
            <label>{{ form.mode === 'geometric' ? '网格步长 (%)' : '网格步长 (元)' }}</label>
            <el-input-number
              v-model="form.stepInput"
              :min="0"
              :precision="form.mode === 'geometric' ? 2 : 3"
              :step="form.mode === 'geometric' ? 0.1 : 0.005"
              controls-position="right"
            />
          </div>
          <div class="field">
            <label>向上卖出格数</label>
            <el-input-number v-model="form.gridsUp" :min="0" :max="200" :step="1" step-strictly controls-position="right" />
          </div>
          <div class="field">
            <label>向下买入格数</label>
            <el-input-number v-model="form.gridsDown" :min="0" :max="200" :step="1" step-strictly controls-position="right" />
          </div>
        </template>

        <div class="field">
          <label>每格委托股数（股）</label>
          <el-input-number v-model="form.sharesPerGrid" :min="100" :step="100" step-strictly controls-position="right" />
        </div>
        <div class="field">
          <label>佣金（万分之）</label>
          <el-input-number v-model="commissionWan" :min="0" :max="100" :precision="2" :step="0.1" controls-position="right" />
        </div>
        <div class="field">
          <label>单笔最低佣金（元）</label>
          <el-input-number v-model="form.minCommission" :min="0" :max="100" :precision="2" :step="0.1" controls-position="right" />
        </div>
        <div class="field">
          <div class="field-head">
            <label>印花税（万分之，卖出）</label>
            <span v-if="isEtf" class="lock-tag">ETF 免征</span>
          </div>
          <el-input-number
            v-model="stampWan"
            :min="0"
            :max="100"
            :precision="2"
            :step="1"
            :disabled="isEtf"
            controls-position="right"
          />
        </div>

        <div class="field">
          <label>初始建仓均价（元）</label>
          <el-input-number
            v-model="form.initialCost"
            :min="0"
            :precision="3"
            :step="0.1"
            :placeholder="fmtPrice(form.basePrice)"
            controls-position="right"
          />
        </div>
        <div class="field">
          <label>初始建仓股数（股）</label>
          <el-input-number v-model="form.initialShares" :min="0" :step="100" step-strictly controls-position="right" />
          <div v-if="baseAllocationHint" :class="['field-hint', baseAllocationHint.over ? 'field-hint-warn' : '']">
            💡 {{ baseAllocationHint.text }}
          </div>
        </div>
      </div>

      <div class="form-actions">
        <el-button type="primary" :icon="TrendCharts" @click="handlePreview">计算档位与预览</el-button>
        <el-button type="success" :loading="saving" @click="saveStrategy()">保存策略</el-button>
        <span v-if="isFormEmpty" class="plan-hint muted-hint">填写标的与价格区间后自动生成档位预览</span>
        <span v-else-if="planError" class="plan-hint error">{{ planError }}</span>
        <span v-else class="plan-hint">
          {{ levels.length }} 档 · 最大占用资金 {{ fmtMoney(maxTotalCapital) }} 元
        </span>
      </div>
    </el-card>

    <!-- 动态摊薄成本看板 -->
    <section class="kpi-grid">
      <article class="kpi-card">
        <div class="kpi-label">最新价 / 涨跌幅</div>
        <div :class="['kpi-value', changeClass]">{{ fmtPrice(effectivePrice) }}</div>
        <div :class="['kpi-sub', changeClass]">
          今日 {{ changePct > 0 ? '+' : '' }}{{ num(changePct).toFixed(2) }}%
          <span class="muted">|</span>
          距基准
          <span :class="toBasePct === null ? '' : toBasePct >= 0 ? 'profit-up' : 'profit-down'">
            {{ toBasePct === null ? '--' : `${toBasePct > 0 ? '+' : ''}${toBasePct.toFixed(2)}%` }}
          </span>
          <span class="muted">({{ fmtPrice(form.basePrice) }})</span>
        </div>
      </article>

      <article class="kpi-card">
        <div class="kpi-label">累计网格套利净额</div>
        <div :class="['kpi-value', totalRealized > 0 ? 'up' : totalRealized < 0 ? 'down' : '']">
          ¥ {{ fmtMoney(totalRealized) }}
        </div>
        <div class="kpi-sub">
          成交 {{ trades.length }} 笔 · 买 {{ buyTrades.length }} / 卖 {{ sellTrades.length }}
          <span class="muted">· 手续费 {{ fmtMoney(totalFee) }}</span>
        </div>
      </article>

      <article class="kpi-card">
        <div class="kpi-label">动态摊薄成本</div>
        <div class="kpi-value">{{ dilutedCost > 0 ? fmtPrice(dilutedCost) : '--' }}</div>
        <div class="kpi-sub">
          初始 {{ fmtPrice(initialCost) }} ➔ 当前 {{ dilutedCost > 0 ? fmtPrice(dilutedCost) : '--' }}
          <span class="muted">|</span>
          <span class="badge badge-down">
            💡 已完成 {{ closedCount }} 次闭环套利，累计抵扣底仓 ¥{{ fmtMoney(totalRealized) }}（每股已降 ¥{{ cycleProfitPerShare.toFixed(3) }}）
          </span>
          <el-tooltip
            placement="top"
            effect="light"
            content="采用网格回合独立核算（LIFO），避免券商 App 移动加权平均法将低吸筹码混入高成本底仓导致的账面虚假亏损"
          >
            <span class="help-dot">?</span>
          </el-tooltip>
        </div>
      </article>

      <article class="kpi-card">
        <div class="kpi-label">安全垫与回本进度</div>
        <div :class="['kpi-value', drawdownBuffer === null ? '' : drawdownBuffer >= 0 ? 'up' : 'down']">
          {{ drawdownBuffer === null ? '--' : fmtPct(drawdownBuffer) }}
        </div>
        <div class="kpi-sub">
          距破网下限 {{ fmtPrice(form.lowerPrice) }} 安全垫
          <span class="muted">·</span>
          <span v-if="floatingLoss <= 0" class="badge badge-down">已全面盈利</span>
          <span v-else-if="gridsToBreakeven === null" class="badge badge-flat">单格净利 ≤ 0，无法靠套利回本</span>
          <span v-else class="badge badge-up">距回本还需套利 {{ gridsToBreakeven.toFixed(1) }} 格</span>
        </div>
        <div class="kpi-sub muted">
          浮亏 {{ fmtMoney(floatingLoss) }} · 单格均利 {{ fmtMoney(avgGridProfit) }} · 持仓 {{ currentShares }} 股
        </div>
      </article>

      <article class="kpi-card">
        <div class="kpi-label">极限备用金（下方待买入）</div>
        <div class="kpi-value">¥ {{ fmtMoney(pendingBuyAmount) }}</div>
        <div class="kpi-sub">
          仅计 {{ pendingLevelCount }} 个待买入档的本金 + 买入手续费
        </div>
        <div class="kpi-sub muted">
          累计最大占用 {{ fmtMoney(maxTotalCapital) }} 元（{{
            initialShares > 0 ? `底仓成本 ${fmtMoney(baseCostAmount)}` : `档位本金 ${fmtMoney(baseCostAmount)}`
          }}
          + 备用金）
        </div>
      </article>
    </section>

    <!-- 价格标尺 -->
    <el-card shadow="never" class="panel">
      <template #header>
        <div class="panel-header">
          <span>价格分布标尺</span>
          <span class="panel-tag">{{ form.mode === 'geometric' ? '等比' : '等差' }} · {{ levels.length }} 档</span>
        </div>
      </template>

      <div v-if="ruler" class="ruler-wrap">
        <svg class="price-ruler" viewBox="0 0 900 150" role="img" aria-label="网格价格分布标尺">
          <rect :x="ruler.left" :y="ruler.axisY - 52" :width="Math.max(0, (ruler.priceX ?? ruler.left) - ruler.left)" height="58" fill="#eaf3ff" />
          <rect
            :x="ruler.priceX ?? ruler.left"
            :y="ruler.axisY - 52"
            :width="Math.max(0, ruler.right - (ruler.priceX ?? ruler.left))"
            height="58"
            fill="#fff4e6"
          />
          <line :x1="ruler.left" :x2="ruler.right" :y1="ruler.axisY" :y2="ruler.axisY" stroke="#c9d8e8" stroke-width="2" />

          <g v-for="tick in ruler.ticks" :key="tick.key">
            <line
              :x1="tick.x"
              :x2="tick.x"
              :y1="ruler.axisY - 12"
              :y2="ruler.axisY + 6"
              stroke="#9fb3c8"
              stroke-width="1.2"
            />
            <title>格线 {{ tick.price.toFixed(3) }}</title>
          </g>

          <g v-for="dot in ruler.dots" :key="dot.key">
            <circle
              :cx="dot.x"
              :cy="ruler.axisY - 24"
              r="6"
              :fill="dot.status === 'holding' ? '#f0a020' : '#4a90e2'"
              :stroke="dot.status === 'holding' ? '#c67c00' : '#2f6fbf'"
              stroke-width="1"
            />
            <text :x="dot.x" :y="ruler.axisY - 34" text-anchor="middle" class="ruler-dot-label">
              {{ dot.index }}
            </text>
            <title>{{ dot.label }}</title>
          </g>

          <line
            v-if="ruler.costX !== null"
            :x1="ruler.costX"
            :x2="ruler.costX"
            :y1="ruler.axisY - 40"
            :y2="ruler.axisY + 8"
            stroke="#7a8fa6"
            stroke-width="1.5"
            stroke-dasharray="5 4"
          />
          <text
            v-if="ruler.showCostLabel"
            :x="ruler.costX"
            :y="ruler.axisY + 26"
            text-anchor="middle"
            class="ruler-label muted-label"
          >
            摊薄成本
          </text>

          <text :x="ruler.left" :y="ruler.axisY + 26" text-anchor="start" class="ruler-label">下限 {{ ruler.lower.toFixed(2) }}</text>
          <text :x="ruler.right" :y="ruler.axisY + 26" text-anchor="end" class="ruler-label">上限 {{ ruler.upper.toFixed(2) }}</text>
          <text
            v-if="ruler.showBaseLabel"
            :x="ruler.baseX"
            :y="ruler.axisY + 26"
            text-anchor="middle"
            class="ruler-label base-label"
          >
            基准 {{ num(form.basePrice).toFixed(2) }}
          </text>

          <g v-if="ruler.priceX !== null">
            <line
              :x1="ruler.priceX"
              :x2="ruler.priceX"
              :y1="18"
              :y2="ruler.axisY - 8"
              stroke="#1f5fbf"
              stroke-width="1.5"
              stroke-dasharray="4 3"
            />
            <path
              :d="`M ${ruler.priceX - 7} ${ruler.axisY - 20} L ${ruler.priceX + 7} ${ruler.axisY - 20} L ${ruler.priceX} ${ruler.axisY - 6} Z`"
              fill="#1f5fbf"
            />
            <circle :cx="ruler.priceX" :cy="ruler.axisY" r="5" fill="#ffffff" stroke="#1f5fbf" stroke-width="3" />
            <text :x="ruler.priceX" y="14" text-anchor="middle" class="ruler-price">
              现价 {{ fmtPrice(effectivePrice) }}
            </text>
          </g>
        </svg>

        <div class="ruler-legend">
          <span><i class="dot buy-dot" />待买入（低吸区）</span>
          <span><i class="dot sell-dot" />已持仓 · 待卖出（高抛区）</span>
          <span><i class="dot cost-dot" />动态摊薄成本</span>
          <span v-if="ruler.outOfRange" class="legend-warning">现价已超出区间</span>
        </div>
      </div>
      <el-empty
        v-else
        :description="form.genMode === 'step' ? '填写基准价与步长后展示价格标尺' : '填写上下限后展示价格标尺'"
        :image-size="72"
      />
    </el-card>

    <!-- 档位与流水 -->
    <el-card shadow="never" class="panel">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="网格档位明细" name="levels">
          <el-table
            v-if="levels.length"
            :data="levelRows"
            border
            size="small"
            row-key="__key"
            :row-class-name="levelRowClass"
            :span-method="levelSpanMethod"
            style="width: 100%"
          >
            <el-table-column label="档位" width="82" align="center">
              <template #default="{ row }">
                <span v-if="row.__divider" class="divider-text">{{ row.text }}</span>
                <span v-else>{{ row.index }}</span>
              </template>
            </el-table-column>
            <el-table-column label="买入触发价" width="104" align="right">
              <template #default="{ row }">{{ row.__divider ? '' : fmtPrice(row.buyPrice) }}</template>
            </el-table-column>
            <el-table-column label="卖出触发价" width="104" align="right">
              <template #default="{ row }">{{ row.__divider ? '' : fmtPrice(row.sellPrice) }}</template>
            </el-table-column>
            <el-table-column label="单格股数" width="88" align="right">
              <template #default="{ row }">{{ row.__divider ? '' : row.shares }}</template>
            </el-table-column>
            <el-table-column label="单格本金（元）" width="118" align="right">
              <template #default="{ row }">{{ row.__divider ? '' : fmtMoney(row.buyAmount) }}</template>
            </el-table-column>
            <el-table-column label="双边手续费" width="104" align="right">
              <template #default="{ row }">{{ row.__divider ? '' : fmtMoney(row.feeTotal) }}</template>
            </el-table-column>
            <el-table-column label="单格净利（元）" width="118" align="right">
              <template #default="{ row }">
                <span v-if="!row.__divider" :class="row.netProfit > 0 ? 'profit-up' : 'profit-down'">
                  {{ fmtMoney(row.netProfit) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="单格收益率" width="98" align="right">
              <template #default="{ row }">{{ row.__divider ? '' : fmtPct(row.returnRate) }}</template>
            </el-table-column>
            <el-table-column label="当前状态" width="132" align="center">
              <template #default="{ row }">
                <el-tag v-if="!row.__divider" :type="levelStatus(row).type" size="small" effect="light">
                  {{ levelStatus(row).label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" min-width="168" align="center">
              <template #default="{ row }">
                <template v-if="!row.__divider">
                  <el-button
                    link
                    type="primary"
                    size="small"
                    :disabled="levelStatus(row).code === 'holding'"
                    @click="openTradeDialog('buy', row)"
                  >
                    记录买入
                  </el-button>
                  <el-button
                    link
                    type="warning"
                    size="small"
                    :disabled="levelStatus(row).code === 'idle'"
                    @click="openTradeDialog('sell', row)"
                  >
                    记录卖出
                  </el-button>
                </template>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="参数填写完整后自动生成档位，或点击「计算档位与预览」" :image-size="72" />
        </el-tab-pane>

        <el-tab-pane label="成交与配对" name="trades">
          <div class="ledger-switch">
            <button :class="['ledger-tab', ledgerView === 'cycles' ? 'active' : '']" @click="ledgerView = 'cycles'">
              🔄 网格配对小账本（回合制）
            </button>
            <button :class="['ledger-tab', ledgerView === 'trades' ? 'active' : '']" @click="ledgerView = 'trades'">
              📋 原始交割流水（单边明细）
            </button>
          </div>

          <template v-if="ledgerView === 'cycles'">
            <div class="trades-toolbar">
              <el-radio-group v-model="cycleFilter" size="small">
                <el-radio-button value="all">全部回合 ({{ cycles.length }})</el-radio-button>
                <el-radio-button value="open">⏳ 待平仓/待接回 ({{ cycleOpenCount }})</el-radio-button>
                <el-radio-button value="closed">✅ 已闭环结清 ({{ cycleClosedCount }})</el-radio-button>
              </el-radio-group>
              <el-button type="primary" plain size="small" @click="openTradeDialog('buy')">手动补录成交</el-button>
              <span class="trades-summary">
                累计已实现
                <strong :class="totalRealized >= 0 ? 'profit-up' : 'profit-down'">¥ {{ fmtMoney(totalRealized) }}</strong>
                · 闭环 {{ cycleClosedCount }} 次 · 未结清 {{ cycleOpenCount }} 笔
              </span>
            </div>

            <el-table v-if="filteredCycles.length" :data="filteredCycles" border size="small" style="width: 100%">
              <el-table-column label="档位 & 模式" width="150">
                <template #default="{ row }">
                  <div class="cycle-level">第 {{ row.gridLevel }} 档</div>
                  <span :class="['cycle-tag', cycleTypeClass(row)]">{{ cycleTypeLabel(row) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="开仓腿（起点）" width="210">
                <template #default="{ row }">
                  <div class="cycle-leg">
                    <el-tag :type="row.openDirection === 'BUY' ? 'danger' : 'success'" size="small" effect="dark">
                      {{ row.openDirection === 'BUY' ? '买入' : '卖出' }}
                    </el-tag>
                    <strong>¥{{ fmtPrice(row.openPrice) }}</strong>
                    <span class="muted">× {{ row.openShares }} 股</span>
                  </div>
                  <div class="cycle-leg-sub muted">费 ¥{{ fmtMoney(row.openFee) }} · {{ row.openTime }}</div>
                </template>
              </el-table-column>
              <el-table-column label="当前状态 & 目标" min-width="240">
                <template #default="{ row }">
                  <template v-if="row.status !== 'CLOSED'">
                    <el-tag :type="row.status === 'HOLDING_LONG' ? 'warning' : 'primary'" size="small" effect="light">
                      {{ row.status === 'HOLDING_LONG' ? '持仓待卖' : '高抛待接回' }}
                    </el-tag>
                    <span class="cycle-target">
                      ➔ 目标 ¥{{ fmtPrice(row.targetClosePrice) }}
                      <span :class="(cycleTargetDiff(row) || 0) >= 0 ? 'profit-up' : 'profit-down'">
                        ({{ (cycleTargetDiff(row) || 0) >= 0 ? '+' : '' }}{{ (cycleTargetDiff(row) || 0).toFixed(2) }}%)
                      </span>
                    </span>
                    <el-button link type="primary" size="small" @click="fillCloseFromCycle(row)">⚡ 一键填入平仓</el-button>
                  </template>
                  <el-tag v-else type="success" size="small" effect="dark">✅ 已完成套利闭环</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="平仓腿（终点）" width="210">
                <template #default="{ row }">
                  <template v-if="row.status === 'CLOSED'">
                    <div class="cycle-leg">
                      <el-tag :type="row.openDirection === 'BUY' ? 'success' : 'danger'" size="small" effect="dark">
                        {{ row.openDirection === 'BUY' ? '卖出' : '买入' }}
                      </el-tag>
                      <strong>¥{{ fmtPrice(row.closePrice) }}</strong>
                      <span class="muted">× {{ row.closeShares }} 股</span>
                    </div>
                    <div class="cycle-leg-sub muted">费 ¥{{ fmtMoney(row.closeFee) }} · {{ row.closeTime }}</div>
                  </template>
                  <span v-else class="muted">-- 待触发 --</span>
                </template>
              </el-table-column>
              <el-table-column label="本回合净利 / 摊薄贡献" width="180" align="right">
                <template #default="{ row }">
                  <template v-if="row.status === 'CLOSED'">
                    <div :class="row.netProfit >= 0 ? 'profit-up' : 'profit-down'" class="cycle-profit">
                      {{ row.netProfit >= 0 ? '+' : '' }}¥{{ fmtMoney(row.netProfit) }}
                    </div>
                    <div class="cycle-leg-sub muted">
                      底仓每股降 ¥{{ (num(row.netProfit) / Math.max(1, initialShares)).toFixed(3) }}
                    </div>
                  </template>
                  <span v-else class="muted">预计 +¥{{ fmtMoney(cycleEstimatedProfit(row)) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="96" align="center">
                <template #default="{ row }">
                  <el-button link type="danger" size="small" @click="revokeCycle(row)">
                    {{ row.status === 'CLOSED' ? '撤销平仓' : '撤销开仓' }}
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else description="还没有配对回合，补录成交后自动生成" :image-size="72" />
          </template>

          <template v-else>
            <div class="trades-toolbar">
              <span class="trades-summary">
                单边交割流水（按时间倒序）· 手续费合计 {{ fmtMoney(totalFee) }} · 当前持仓 {{ currentShares }} 股
              </span>
            </div>
            <el-table v-if="sortedTrades.length" :data="sortedTrades" border size="small" style="width: 100%">
            <el-table-column prop="timestamp" label="成交时间" width="180" align="center" />
            <el-table-column label="方向" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="row.type === 'buy' ? 'danger' : 'success'" size="small" effect="dark">
                  {{ row.type === 'buy' ? '买入' : '卖出' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="成交单价" width="110" align="right">
              <template #default="{ row }">{{ fmtPrice(row.price) }}</template>
            </el-table-column>
            <el-table-column label="成交股数" width="100" align="right">
              <template #default="{ row }">{{ row.shares }}</template>
            </el-table-column>
            <el-table-column label="手续费" width="110" align="right">
              <template #default="{ row }">{{ fmtMoney(row.fee) }}</template>
            </el-table-column>
            <el-table-column label="套利收益" width="120" align="right">
              <template #default="{ row }">
                <span v-if="row.type === 'sell'" :class="row.realizedProfit >= 0 ? 'profit-up' : 'profit-down'">
                  {{ fmtMoney(row.realizedProfit) }}
                </span>
                <span v-else class="muted">--</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" min-width="90" align="center">
              <template #default="{ row }">
                <el-button link type="danger" size="small" @click="deleteTrade(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
            <el-empty v-if="!sortedTrades.length" description="还没有成交记录" :image-size="72" />
          </template>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 成交录入 -->
    <el-dialog v-model="tradeDialog.visible" :title="tradeDialog.manual ? '手动补录成交' : '快捷记录成交'" width="440px">
      <el-form label-width="96px">
        <el-form-item label="成交方向">
          <el-radio-group v-model="tradeForm.type" :disabled="!tradeDialog.manual">
            <el-radio-button value="buy">买入</el-radio-button>
            <el-radio-button value="sell">卖出</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="关联档位">
          <el-select v-model="tradeForm.levelIndex" clearable placeholder="自动匹配最接近档位" style="width: 100%">
            <el-option
              v-for="level in levelsDescending"
              :key="level.index"
              :label="`第 ${level.index} 档 · 买 ${level.buyPrice.toFixed(3)} / 卖 ${level.sellPrice.toFixed(3)}`"
              :value="level.index"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="成交时间">
          <el-date-picker
            v-model="tradeForm.timestamp"
            type="datetime"
            value-format="YYYY-MM-DD HH:mm:ss"
            placeholder="选择成交时间"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="成交单价">
          <el-input-number v-model="tradeForm.price" :min="0" :precision="3" :step="0.01" style="width: 100%" />
        </el-form-item>
        <el-form-item label="成交股数">
          <el-input-number v-model="tradeForm.shares" :min="100" :step="100" step-strictly style="width: 100%" />
        </el-form-item>
        <el-form-item label="手续费">
          <el-input-number v-model="tradeForm.fee" :min="0" :precision="2" :step="0.1" style="width: 100%" />
        </el-form-item>
        <el-form-item v-if="matchedTradeLevel" label="匹配档位">
          <span class="muted">
            第 {{ matchedTradeLevel.index }} 档（{{ fmtPrice(matchedTradeLevel.buyPrice) }} ~
            {{ fmtPrice(matchedTradeLevel.sellPrice) }}）
          </span>
        </el-form-item>
        <el-form-item label="配对动作">
          <el-select v-model="tradeForm.pairCycleId" placeholder="🆕 新开仓回合（不配对）" style="width: 100%">
            <el-option label="🆕 新开仓回合（不配对）" value="" />
            <el-option
              v-for="cycle in tradePairCandidates"
              :key="cycle.id"
              :value="cycle.id"
              :label="
                tradeForm.type === 'buy'
                  ? `🔗 平仓接回：第 ${cycle.gridLevel} 档 ¥${num(cycle.openPrice).toFixed(3)} 卖出的 ${cycle.openShares} 股`
                  : `🔗 平仓止盈：第 ${cycle.gridLevel} 档 ¥${num(cycle.openPrice).toFixed(3)} 买入的 ${cycle.openShares} 股`
              "
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="tradePreview" label="实时预览">
          <div
            :class="[
              'preview-box',
              tradePreview.mode === 'close'
                ? 'preview-close'
                : tradePreview.mode === 'open-short'
                  ? 'preview-short'
                  : 'preview-open',
            ]"
          >
            <template v-if="tradePreview.mode === 'close'">
              🎯 本回合闭环预计净赚：<strong>{{ tradePreview.net >= 0 ? '+' : '' }}¥{{ fmtMoney(tradePreview.net) }}</strong>
              （底仓成本将再降 ¥{{ tradePreview.perShare.toFixed(3) }}/股）
            </template>
            <template v-else-if="tradePreview.mode === 'open-long'">
              🎯 对应目标卖出价：<strong>¥{{ fmtPrice(tradePreview.target) }}</strong>
              （届时平仓预计净赚 {{ tradePreview.est >= 0 ? '+' : '' }}¥{{ fmtMoney(tradePreview.est) }}）
            </template>
            <template v-else>
              📤 底仓高抛开仓（待回落至 ¥{{ fmtPrice(tradePreview.target) }} 接回），接回后预计锁利
              {{ tradePreview.est >= 0 ? '+' : '' }}¥{{ fmtMoney(tradePreview.est) }}
            </template>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="tradeDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="tradeSubmitting" @click="submitTrade">确认记录</el-button>
      </template>
    </el-dialog>

    <!-- 历史回测抽屉 -->
    <el-drawer v-model="backtestDialog.visible" title="网格历史回测" size="700px">
      <div class="backtest-body">
        <el-form label-width="88px" size="small" class="backtest-form">
          <el-form-item label="标的代码">
            <el-select
              v-model="backtestForm.code"
              filterable
              allow-create
              default-first-option
              placeholder="选择持仓或输入代码"
              style="width: 220px"
            >
              <el-option
                v-for="p in portfolioOptions"
                :key="p.code"
                :value="p.code"
                :label="`${p.code} ${p.name}`"
              />
            </el-select>
          </el-form-item>
          <div class="backtest-grid">
            <el-form-item label="基准价(元)">
              <el-input-number v-model="backtestForm.basePrice" :min="0" :precision="3" :step="0.01" style="width: 100%" />
            </el-form-item>
            <el-form-item label="网格算法">
              <el-radio-group v-model="backtestForm.gridType" size="small">
                <el-radio-button value="arithmetic">等差</el-radio-button>
                <el-radio-button value="geometric">等比</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item :label="backtestForm.gridType === 'geometric' ? '步长(%)' : '步长(元)'">
              <el-input-number
                v-if="backtestForm.gridType === 'geometric'"
                v-model="backtestForm.stepPct"
                :min="0.1"
                :precision="2"
                :step="0.1"
                style="width: 100%"
              />
              <el-input-number
                v-else
                v-model="backtestForm.stepValue"
                :min="0.001"
                :precision="3"
                :step="0.01"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="单格金额(元)">
              <el-input-number v-model="backtestForm.gridAmount" :min="100" :step="1000" style="width: 100%" />
            </el-form-item>
            <el-form-item label="最大网格数">
              <el-input-number v-model="backtestForm.maxGrids" :min="1" :max="100" :step="1" style="width: 100%" />
            </el-form-item>
            <el-form-item label="初始资金(元)">
              <el-input-number v-model="backtestForm.initialCash" :min="1000" :step="10000" style="width: 100%" />
            </el-form-item>
            <el-form-item label="佣金费率">
              <el-input-number v-model="backtestForm.commissionRate" :min="0" :precision="5" :step="0.00001" style="width: 100%" />
            </el-form-item>
            <el-form-item label="最低佣金(元)">
              <el-input-number v-model="backtestForm.minCommission" :min="0" :precision="2" :step="0.1" style="width: 100%" />
            </el-form-item>
            <el-form-item label="印花税(卖出)">
              <el-input-number
                v-model="backtestForm.stampDutyRate"
                :min="0"
                :precision="5"
                :step="0.0001"
                :disabled="backtestIsEtf"
                style="width: 100%"
              />
              <span v-if="backtestIsEtf" class="lock-tag">ETF 免征</span>
            </el-form-item>
          </div>
          <el-form-item label="时间范围">
            <el-radio-group v-model="backtestForm.range" @change="setBacktestRange">
              <el-radio-button value="1y">近1年</el-radio-button>
              <el-radio-button value="2y">近2年</el-radio-button>
              <el-radio-button value="3y">近3年</el-radio-button>
              <el-radio-button value="custom">自定义</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="起止日期">
            <el-date-picker v-model="backtestForm.startDate" type="date" value-format="YYYY-MM-DD" placeholder="开始" style="width: 150px" @change="backtestForm.range = 'custom'" />
            <span class="backtest-sep">~</span>
            <el-date-picker v-model="backtestForm.endDate" type="date" value-format="YYYY-MM-DD" placeholder="结束" style="width: 150px" @change="backtestForm.range = 'custom'" />
          </el-form-item>
        </el-form>

        <div class="backtest-actions">
          <el-button type="primary" :loading="backtestLoading" @click="runBacktest">开始回测</el-button>
          <span v-if="backtestLoading" class="backtest-loading-tip">正在读取日线并逐日撮合…</span>
        </div>

        <div v-loading="backtestLoading" class="backtest-result">
          <template v-if="backtestResult">
            <div class="backtest-cards">
              <div class="backtest-card">
                <div class="btc-label">网格累计收益率</div>
                <div :class="['btc-value', backtestResult.metrics.total_return_pct >= 0 ? 'profit-up' : 'profit-down']">
                  {{ backtestResult.metrics.total_return_pct > 0 ? '+' : '' }}{{ backtestResult.metrics.total_return_pct }}%
                </div>
                <div class="btc-sub">基准 {{ backtestResult.metrics.benchmark_return_pct > 0 ? '+' : '' }}{{ backtestResult.metrics.benchmark_return_pct }}%</div>
              </div>
              <div class="backtest-card">
                <div class="btc-label">年化收益率</div>
                <div :class="['btc-value', backtestResult.metrics.annualized_return_pct >= 0 ? 'profit-up' : 'profit-down']">
                  {{ backtestResult.metrics.annualized_return_pct > 0 ? '+' : '' }}{{ backtestResult.metrics.annualized_return_pct }}%
                </div>
                <div class="btc-sub">超额 {{ backtestResult.metrics.excess_return_pct > 0 ? '+' : '' }}{{ backtestResult.metrics.excess_return_pct }}%</div>
              </div>
              <div class="backtest-card">
                <div class="btc-label">最大回撤</div>
                <div class="btc-value profit-down">-{{ backtestResult.metrics.max_drawdown_pct }}%</div>
                <div class="btc-sub">基准 -{{ backtestResult.metrics.benchmark_max_drawdown_pct }}%</div>
              </div>
              <div class="backtest-card">
                <div class="btc-label">成功套利次数</div>
                <div class="btc-value">{{ backtestResult.metrics.arbitrage_count }}</div>
                <div class="btc-sub">落袋利润 ¥{{ fmtMoney(backtestResult.metrics.total_profit) }}</div>
              </div>
            </div>

            <div v-if="backtestChart" class="backtest-chart">
              <div class="backtest-chart-title">净值对比（网格 vs 买入持有）</div>
              <svg :viewBox="`0 0 ${backtestChart.W} ${backtestChart.H}`" class="backtest-svg" preserveAspectRatio="xMidYMid meet">
                <path v-if="backtestChart.hasBench" :d="backtestChart.benchPath" fill="none" stroke="#9aa7b5" stroke-width="1.5" stroke-dasharray="5,4" />
                <path :d="backtestChart.gridPath" fill="none" stroke="#e34d4d" stroke-width="2" />
              </svg>
              <div class="backtest-legend">
                <span><i class="dot grid"></i>网格策略</span>
                <span v-if="backtestChart.hasBench"><i class="dot bench"></i>买入持有</span>
              </div>
            </div>

            <div v-if="backtestResult.trades && backtestResult.trades.length" class="backtest-trades">
              <div class="backtest-chart-title">交易流水明细（{{ backtestResult.trades.length }} 笔）</div>
              <el-table :data="backtestResult.trades" size="small" border max-height="260">
                <el-table-column prop="date" label="日期" width="110" />
                <el-table-column label="方向" width="70" align="center">
                  <template #default="scope">
                    <span :class="scope.row.side === 'BUY' ? 'trade-buy' : 'trade-sell'">{{ scope.row.side === 'BUY' ? '买入' : '卖出' }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="price" label="成交价" width="90" align="right" />
                <el-table-column prop="shares" label="股数" width="90" align="right" />
                <el-table-column prop="amount" label="成交金额" width="110" align="right" />
                <el-table-column prop="fee" label="手续费" width="80" align="right" />
                <el-table-column label="层级" width="60" align="center" prop="level" />
                <el-table-column label="本笔获利" min-width="90" align="right">
                  <template #default="scope">
                    <span v-if="scope.row.profit != null" :class="scope.row.profit >= 0 ? 'profit-up' : 'profit-down'">¥{{ fmtMoney(scope.row.profit) }}</span>
                    <span v-else>—</span>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </template>
          <el-empty v-else-if="!backtestLoading" description="填写参数后点击「开始回测」" :image-size="60" />
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.grid-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.grid-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid #d8e2ec;
  border-radius: 10px;
  background: #ffffff;
}

.toolbar-group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.strategy-select {
  width: 260px;
}

.price-chip {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 8px;
  background: #f4f8fd;
  border: 1px solid #e1ebf6;
}

.chip-name {
  font-size: 0.92rem;
  color: #45596d;
}

.chip-price {
  font-size: 1.16rem;
  font-weight: 700;
}

.chip-change {
  font-size: 0.86rem;
  font-weight: 600;
}

.panel {
  border: 1px solid #d8e2ec;
  border-radius: 10px;
}

.panel :deep(.el-card__header) {
  padding: 10px 16px;
  background: #f7fafd;
  border-bottom: 1px solid #e3ebf4;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 1.02rem;
  font-weight: 700;
  color: #2b3f55;
}

.panel-header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.panel-tag {
  font-size: 0.82rem;
  font-weight: 500;
  color: #6b8099;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px 16px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.field > label,
.field-head > label {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
  font-size: 0.86rem;
  font-weight: 600;
  color: #4a6076;
}

.field-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 24px;
}

.field :deep(.el-input-number) {
  width: 100%;
}

.field :deep(.el-select) {
  width: 100%;
}

.field-switch {
  justify-content: flex-start;
  padding-top: 6px;
}

.form-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px dashed #e0e9f2;
}

.plan-hint {
  font-size: 0.85rem;
  color: #5a738c;
}

.plan-hint.error {
  color: #e64545;
}

.muted-hint {
  color: #8a9db0;
}

.field :deep(.el-radio-group) {
  flex-wrap: wrap;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.kpi-card {
  padding: 14px 16px;
  border: 1px solid #d8e2ec;
  border-radius: 10px;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
  box-shadow: 0 1px 2px rgba(32, 54, 79, 0.04);
}

.kpi-label {
  font-size: 0.84rem;
  color: #6b8099;
  margin-bottom: 6px;
}

.kpi-value {
  font-size: 1.42rem;
  font-weight: 700;
  color: #22374d;
  line-height: 1.25;
}

.kpi-sub {
  margin-top: 6px;
  font-size: 0.82rem;
  color: #5a738c;
}

.muted {
  color: #9aaec2;
}

.up {
  color: #e64545;
}

.down {
  color: #1fa971;
}

.flat {
  color: #5a738c;
}

.badge {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 6px;
  font-size: 0.76rem;
  font-weight: 600;
}

.badge-down {
  color: #1fa971;
  background: #e9f8f1;
}

.badge-flat {
  color: #8a9db0;
  background: #f1f5f9;
}

.help-dot {
  display: inline-flex;
  width: 15px;
  height: 15px;
  margin-left: 4px;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: #e8eef6;
  color: #5a738c;
  font-size: 10px;
  font-weight: 700;
  cursor: help;
  vertical-align: middle;
}

.ledger-switch {
  display: flex;
  gap: 8px;
  padding: 2px 0 12px;
}

.ledger-tab {
  padding: 6px 14px;
  border: 1px solid #d8e2ec;
  border-radius: 999px;
  background: #ffffff;
  color: #5a738c;
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.15s ease;
}

.ledger-tab:hover {
  border-color: #9cc2f0;
  color: #1f5fbf;
}

.ledger-tab.active {
  border-color: #1f5fbf;
  background: #1f5fbf;
  color: #ffffff;
  font-weight: 600;
  box-shadow: 0 2px 6px rgba(31, 95, 191, 0.25);
}

.cycle-level {
  font-weight: 600;
  color: #2b3f55;
}

.cycle-tag {
  display: inline-block;
  margin-top: 3px;
  padding: 1px 7px;
  border-radius: 6px;
  font-size: 0.72rem;
}

.cycle-tag-long {
  color: #1f5fbf;
  background: #eaf3ff;
}

.cycle-tag-short {
  color: #7b4bd1;
  background: #f3ecff;
}

.cycle-leg {
  display: flex;
  align-items: center;
  gap: 6px;
}

.cycle-leg-sub {
  margin-top: 2px;
  font-size: 0.74rem;
}

.cycle-target {
  display: inline-block;
  margin: 0 6px;
  font-size: 0.8rem;
  color: #5a738c;
}

.cycle-profit {
  font-size: 1rem;
  font-weight: 700;
}

.preview-box {
  width: 100%;
  padding: 8px 10px;
  border-radius: 8px;
  font-size: 0.84rem;
  line-height: 1.5;
}

.preview-close {
  border: 1px solid #bfe9d6;
  background: #e9f8f1;
  color: #157a52;
}

.preview-open {
  border: 1px solid #c7ddf7;
  background: #eaf3ff;
  color: #1f5fbf;
}

.preview-short {
  border: 1px solid #ddd0f7;
  background: #f3ecff;
  color: #6b3fc0;
}

.lock-tag {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 600;
  color: #b26a00;
  background: #fff4e6;
}

.ruler-dot-label {
  font-size: 10px;
  fill: #7a8fa6;
}

.divider-text {
  display: block;
  color: #1f5fbf;
  font-weight: 700;
  font-size: 0.82rem;
  letter-spacing: 0.2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

:deep(.price-divider-row) td {
  background: #eef6ff !important;
  border-top: 1px dashed #9cc2f0 !important;
  border-bottom: 1px dashed #9cc2f0 !important;
}

/* 现价相邻两档：极淡底色高亮（一买淡蓝 / 一卖淡橙） */
:deep(.row-nearest-buy) td {
  background: #f6fbff !important;
}

:deep(.row-nearest-sell) td {
  background: #fffbf5 !important;
}

.auto-tag {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 999px;
  background: #eef3f8;
  color: #7a8fa6;
  font-size: 0.68rem;
  font-weight: 500;
}

.field-hint {
  margin-top: 4px;
  padding: 4px 6px;
  border-radius: 6px;
  background: #f5f9ff;
  color: #5a738c;
  font-size: 0.72rem;
  line-height: 1.45;
}

.field-hint-warn {
  background: #fff8ec;
  color: #9a6a1f;
}

.badge-up {
  color: #e64545;
  background: #fdecec;
}

.profit-up {
  color: #e64545;
  font-weight: 600;
}

.profit-down {
  color: #1fa971;
  font-weight: 600;
}

.ruler-wrap {
  padding: 4px 4px 0;
}

.price-ruler {
  width: 100%;
  height: auto;
  display: block;
}

.ruler-label {
  font-size: 12px;
  fill: #5a738c;
}

.base-label {
  fill: #2b3f55;
  font-weight: 600;
}

.muted-label {
  fill: #8a9db0;
}

.ruler-price {
  font-size: 13px;
  font-weight: 700;
  fill: #1f5fbf;
}

.ruler-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  padding: 4px 12px 2px;
  font-size: 0.8rem;
  color: #6b8099;
}

.dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  margin-right: 5px;
  border-radius: 50%;
  vertical-align: middle;
}

.buy-dot {
  background: #bcd9ff;
}

.sell-dot {
  background: #ffd9ad;
}

.cost-dot {
  background: #7a8fa6;
}

.legend-warning {
  color: #e6a23c;
  font-weight: 600;
}

.trades-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

.trades-summary {
  font-size: 0.84rem;
  color: #5a738c;
}

:deep(.level-current-row) {
  background: #eef5ff !important;
}

:deep(.level-current-row td) {
  background: #eef5ff !important;
  font-weight: 600;
}

@media (max-width: 900px) {
  .strategy-select {
    width: 100%;
  }

  .grid-toolbar {
    align-items: flex-start;
  }
}

/* ---- 历史回测 ---- */
.backtest-body {
  padding: 0 4px;
}

.backtest-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 18px;
}

.backtest-sep {
  margin: 0 8px;
  color: #9aa7b5;
}

.backtest-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 8px 0 16px;
}

.backtest-loading-tip {
  font-size: 0.82rem;
  color: #6b8099;
}

.backtest-result {
  min-height: 120px;
}

.backtest-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-bottom: 16px;
}

.backtest-card {
  padding: 12px;
  border: 1px solid #e2eaf2;
  border-radius: 8px;
  background: #f8fafc;
}

.btc-label {
  font-size: 0.78rem;
  color: #6b8099;
}

.btc-value {
  font-size: 1.25rem;
  font-weight: 700;
  margin: 4px 0 2px;
}

.btc-sub {
  font-size: 0.76rem;
  color: #8a9db0;
}

.backtest-chart {
  margin-bottom: 16px;
}

.backtest-chart-title {
  font-size: 0.9rem;
  font-weight: 600;
  color: #2b3f55;
  margin-bottom: 8px;
}

.backtest-svg {
  width: 100%;
  height: auto;
  border: 1px solid #e6edf4;
  border-radius: 6px;
  background: #fff;
}

.backtest-legend {
  display: flex;
  gap: 16px;
  margin-top: 6px;
  font-size: 0.8rem;
  color: #5a738c;
}

.dot.grid {
  background: #e34d4d;
}

.dot.bench {
  background: #9aa7b5;
}

.trade-buy {
  color: #e64545;
  font-weight: 600;
}

.trade-sell {
  color: #1fa971;
  font-weight: 600;
}

@media (max-width: 900px) {
  .backtest-cards {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>

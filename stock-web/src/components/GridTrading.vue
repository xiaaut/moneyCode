<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Delete, Plus, Refresh, TrendCharts } from '@element-plus/icons-vue';
import { buildGridPlan, calcOrderFees } from '../utils/gridCalculator.js';

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
const activeTab = ref('levels');
const importCode = ref('');

const form = reactive({
  symbol: '',
  name: '',
  basePrice: 0,
  upperPrice: 0,
  lowerPrice: 0,
  mode: 'arithmetic',
  gridCount: 10,
  sharesPerGrid: 100,
  initialShares: 0,
  initialCost: 0,
  commissionRate: 0.0001,
  stampDutyRate: 0.0005,
  freeFive: false,
});

const tradeDialog = reactive({ visible: false, manual: false });
const tradeForm = reactive({ type: 'buy', timestamp: '', price: 0, shares: 100, fee: 0 });
const tradeSubmitting = ref(false);

/* ---------------- 基础工具 ---------------- */

const num = value => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};
const round2 = value => Math.round((num(value) + Number.EPSILON) * 100) / 100;
const fmtPrice = value => (num(value) > 0 ? num(value).toFixed(2) : '--');
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

function estimateFee(price, shares, side) {
  const amount = num(price) * num(shares);
  return calcOrderFees(amount, {
    commissionRate: num(form.commissionRate),
    stampDutyRate: num(form.stampDutyRate),
    freeFive: form.freeFive,
    side,
  }).total;
}

/* ---------------- 派生数据 ---------------- */

const currentStrategy = computed(() => strategies.value.find(item => item.id === selectedId.value) || null);

const effectivePrice = computed(() => {
  const quote = num(latestPrice.value);
  return quote > 0 ? quote : num(form.basePrice);
});

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

const plan = computed(() =>
  buildGridPlan({
    basePrice: num(form.basePrice),
    upperPrice: num(form.upperPrice),
    lowerPrice: num(form.lowerPrice),
    mode: form.mode,
    gridCount: Math.round(num(form.gridCount)),
    sharesPerGrid: Math.round(num(form.sharesPerGrid)),
    commissionRate: num(form.commissionRate),
    stampDutyRate: num(form.stampDutyRate),
    freeFive: form.freeFive,
  }),
);

const levels = computed(() => (plan.value.ok ? plan.value.levels : []));
const planError = computed(() => (plan.value.ok ? '' : plan.value.message));
const isFormEmpty = computed(
  () =>
    !String(form.symbol || '').trim() &&
    !(num(form.basePrice) > 0) &&
    !(num(form.upperPrice) > 0) &&
    !(num(form.lowerPrice) > 0),
);

const activeLevelIndex = computed(() => {
  const price = effectivePrice.value;
  if (!levels.value.length || !(price > 0)) return 0;
  const hit = levels.value.find(level => price >= level.buyPrice && price < level.sellPrice);
  if (hit) return hit.index;
  const last = levels.value[levels.value.length - 1];
  return price >= last.sellPrice ? last.index : 1;
});

function levelStatus(level) {
  const price = effectivePrice.value;
  if (!(price > 0) || price < level.buyPrice) return { label: '待买入', type: 'info' };
  if (price >= level.sellPrice) return { label: '持有待卖', type: 'warning' };
  return { label: '当前运行区间', type: 'primary' };
}

function levelRowClass({ row }) {
  return row.index === activeLevelIndex.value ? 'level-current-row' : '';
}

const buyTrades = computed(() => trades.value.filter(item => item.type === 'buy'));
const sellTrades = computed(() => trades.value.filter(item => item.type === 'sell'));
const sortedTrades = computed(() =>
  [...trades.value].sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp))),
);

const totalRealized = computed(() =>
  round2(trades.value.reduce((sum, item) => sum + num(item.realizedProfit), 0)),
);
const totalFee = computed(() => round2(trades.value.reduce((sum, item) => sum + num(item.fee), 0)));

const initialCost = computed(() => num(form.initialCost) || num(form.basePrice));
const initialShares = computed(() => Math.max(0, Math.round(num(form.initialShares))));
const initialAmount = computed(() => round2(initialCost.value * initialShares.value));

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

// 券商口径的摊薄成本 =（初始建仓金额 + 累计买入含费 - 累计卖出净回款）/ 当前持仓股数
// 直接用「初始金额 - 套利净额」只在股数不变时成立，减仓后会把成本算高
const dilutedCost = computed(() => {
  const shares = currentShares.value;
  if (!(shares > 0)) return 0;
  return Math.max(0, netInvested.value / shares);
});

const costDropPct = computed(() => {
  const base = initialCost.value;
  const cost = dilutedCost.value;
  if (!(base > 0) || !(cost > 0)) return 0;
  return (cost - base) / base;
});

const safetyMargin = computed(() => {
  const cost = dilutedCost.value;
  const price = effectivePrice.value;
  if (!(cost > 0) || !(price > 0)) return null;
  return (price - cost) / cost;
});

const costBasisForTrade = computed(() => {
  const cost = dilutedCost.value;
  if (cost > 0) return cost;
  const fallback = num(form.initialCost) || num(form.basePrice);
  return fallback > 0 ? fallback : num(tradeForm.price);
});

const pendingProfit = computed(() => {
  if (tradeForm.type !== 'sell') return 0;
  const price = num(tradeForm.price);
  const shares = Math.round(num(tradeForm.shares));
  return round2((price - costBasisForTrade.value) * shares - num(tradeForm.fee));
});

const maxAddAmount = computed(() => (plan.value.ok ? plan.value.summary.maxAddAmount : 0));
const maxTotalCapital = computed(() => (plan.value.ok ? plan.value.summary.maxTotalCapital : 0));

const changeClass = computed(() => {
  if (changePct.value > 0) return 'up';
  if (changePct.value < 0) return 'down';
  return 'flat';
});

const ruler = computed(() => {
  const lower = num(form.lowerPrice);
  const upper = num(form.upperPrice);
  if (!(lower > 0) || !(upper > lower)) return null;

  const left = 54;
  const right = 846;
  const axisY = 96;
  const span = right - left;
  const xOf = price => left + ((num(price) - lower) / (upper - lower)) * span;
  const clampX = value => Math.min(right, Math.max(left, value));

  const lines = plan.value.ok && plan.value.lines.length ? plan.value.lines : [lower, upper];
  const ticks = lines.map((price, index) => ({
    key: `${price}-${index}`,
    price,
    x: xOf(price),
    active: index + 1 === activeLevelIndex.value,
  }));

  const price = effectivePrice.value;
  const priceX = price > 0 ? clampX(xOf(price)) : null;
  const cost = dilutedCost.value;
  const costX = cost > 0 ? clampX(xOf(cost)) : null;
  const baseX = clampX(xOf(num(form.basePrice)));

  return {
    left,
    right,
    axisY,
    ticks,
    priceX,
    costX,
    baseX,
    // 标签挤在一起时只保留关键信息，避免文字重叠
    showBaseLabel: baseX - left > 70 && right - baseX > 70 && (costX === null || Math.abs(costX - baseX) > 64),
    showCostLabel: costX !== null && Math.abs(costX - baseX) > 64 && costX - left > 40 && right - costX > 40,
    lower,
    upper,
    outOfRange: price > 0 && (price < lower || price > upper),
  };
});

/* ---------------- 策略读写 ---------------- */

function applyStrategy(item) {
  if (!item) return;
  selectedId.value = item.id;
  Object.assign(form, {
    symbol: String(item.symbol || ''),
    name: String(item.name || ''),
    basePrice: num(item.basePrice),
    upperPrice: num(item.upperPrice),
    lowerPrice: num(item.lowerPrice),
    mode: item.mode === 'geometric' ? 'geometric' : 'arithmetic',
    gridCount: Math.max(1, Math.round(num(item.gridCount)) || 10),
    sharesPerGrid: Math.max(100, Math.round(num(item.sharesPerGrid)) || 100),
    initialShares: Math.max(0, Math.round(num(item.initialShares))),
    initialCost: num(item.initialCost) || num(item.basePrice),
    commissionRate: item.commissionRate === undefined ? 0.0001 : num(item.commissionRate),
    stampDutyRate: item.stampDutyRate === undefined ? 0.0005 : num(item.stampDutyRate),
    freeFive: Boolean(item.freeFive),
  });
  trades.value = (Array.isArray(item.trades) ? item.trades : []).map(trade => ({ ...trade }));
  latestPrice.value = num(item.lastPrice) || num(item.basePrice);
  changePct.value = 0;
  importCode.value = '';
  activeTab.value = 'levels';

  // 切换策略后静默补一次现价，涨跌幅与标尺游标才不会停在旧值
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
    mode: 'arithmetic',
    gridCount: 10,
    sharesPerGrid: 100,
    initialShares: 0,
    initialCost: 0,
    commissionRate: 0.0001,
    stampDutyRate: 0.0005,
    freeFive: false,
  });
  trades.value = [];
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
    mode: form.mode,
    gridCount: Math.round(num(form.gridCount)),
    sharesPerGrid: Math.round(num(form.sharesPerGrid)),
    initialShares: Math.max(0, Math.round(num(form.initialShares))),
    initialCost: num(form.initialCost) || num(form.basePrice),
    commissionRate: num(form.commissionRate),
    stampDutyRate: num(form.stampDutyRate),
    freeFive: Boolean(form.freeFive),
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
    if (price > 0) latestPrice.value = Number(price.toFixed(2));
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

  try {
    const resp = await fetch(`/api/stock-name?codes=${code}`, { method: 'GET' });
    const json = await resp.json().catch(() => ({}));
    const info = json?.items?.[code];
    if (info?.name) form.name = String(info.name);
    if (Number(info?.price) > 0 && !(num(latestPrice.value) > 0)) {
      latestPrice.value = Number(Number(info.price).toFixed(2));
    }
  } catch {
    // 名称拉取失败不阻塞后续操作
  }

  await refreshPrice({ silent: true });
  if (form.name) ElMessage.success(`已识别：${form.name}`);
}

function handlePortfolioImport(code) {
  const row = props.portfolio.find(item => String(item.code) === String(code));
  if (!row) return;

  form.symbol = String(row.code || '');
  form.name = String(row.name || form.name || '');
  const price = num(row.currentPrice);
  if (price > 0) latestPrice.value = price;
  if (!(num(form.basePrice) > 0) && price > 0) form.basePrice = price;
  if (!(num(form.upperPrice) > 0) && price > 0) form.upperPrice = round2(price * 1.2);
  if (!(num(form.lowerPrice) > 0) && price > 0) form.lowerPrice = round2(price * 0.8);
  form.initialShares = Math.max(0, Math.round(num(row.quantity)));
  form.initialCost = num(row.costPrice) || price;

  ElMessage.success(`已导入 ${row.name || row.code}：${form.initialShares} 股 / 成本 ${fmtPrice(form.initialCost)}`);
}

function setBasePriceToLatest() {
  const price = effectivePrice.value;
  if (!(price > 0)) {
    ElMessage.warning('还没有可用的现价');
    return;
  }
  form.basePrice = price;
}

function handlePreview() {
  if (!plan.value.ok) {
    ElMessage.error(plan.value.message);
    return;
  }
  void refreshPrice({ silent: true });
  ElMessage.success(
    `共 ${levels.value.length} 档，底仓 ${plan.value.summary.baseShares} 股，极限追加资金 ${fmtMoney(
      plan.value.summary.maxAddAmount,
    )} 元`,
  );
}

/* ---------------- 成交记录 ---------------- */

function openTradeDialog(type, level = null) {
  if (!selectedId.value) {
    ElMessage.warning('请先保存策略，再记录实盘成交');
    return;
  }
  tradeDialog.manual = !level;
  tradeForm.type = type;
  tradeForm.timestamp = nowText();
  tradeForm.price = level ? (type === 'buy' ? level.buyPrice : level.sellPrice) : effectivePrice.value || 0;
  tradeForm.shares = level ? level.shares : Math.max(100, Math.round(num(form.sharesPerGrid)));
  tradeForm.fee = estimateFee(tradeForm.price, tradeForm.shares, type);
  tradeDialog.visible = true;
}

watch(
  () => [tradeForm.price, tradeForm.shares, tradeForm.type],
  () => {
    if (!tradeDialog.visible) return;
    tradeForm.fee = estimateFee(tradeForm.price, tradeForm.shares, tradeForm.type);
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

  const record = {
    id: `t_${Date.now()}`,
    timestamp: tradeForm.timestamp,
    type: tradeForm.type,
    price,
    shares,
    fee,
    realizedProfit:
      tradeForm.type === 'sell' ? round2((price - costBasisForTrade.value) * shares - fee) : 0,
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
  ElMessage.success(
    record.type === 'buy'
      ? `已记录买入 ${shares} 股 @ ${fmtPrice(price)}`
      : `已记录卖出，本笔套利 ${fmtMoney(record.realizedProfit)} 元`,
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
  gridAmount: 10000,
  maxGrids: 10,
  initialCash: 100000,
  commissionRate: 0.0001,
  range: '2y',
  startDate: '',
  endDate: '',
});
const backtestLoading = ref(false);
const backtestResult = ref(null);

/* 回测可选标的：持仓列表（code + name），支持下拉选择任意持仓代码 */
const portfolioOptions = computed(() =>
  (props.portfolio || [])
    .map(item => ({ code: String(item?.code || '').trim(), name: String(item?.name || '') }))
    .filter(item => /^\d{6}$/.test(item.code)),
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
  backtestForm.stepPct = 1.0;
  backtestForm.gridAmount = 10000;
  backtestForm.maxGrids = Math.max(1, Math.round(num(form.gridCount))) || 10;
  backtestForm.initialCash = 100000;
  backtestForm.commissionRate = num(form.commissionRate) || 0.0001;
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
        step_pct: num(backtestForm.stepPct),
        grid_amount: num(backtestForm.gridAmount),
        max_grids: Math.round(num(backtestForm.maxGrids)),
        commission_rate: num(backtestForm.commissionRate),
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
          <label>价格上限（元）</label>
          <el-input-number v-model="form.upperPrice" :min="0" :precision="2" :step="0.1" controls-position="right" />
        </div>
        <div class="field">
          <label>价格下限（元）</label>
          <el-input-number v-model="form.lowerPrice" :min="0" :precision="2" :step="0.1" controls-position="right" />
        </div>
        <div class="field">
          <div class="field-head">
            <label>基准价（元）</label>
            <el-button link type="primary" size="small" @click="setBasePriceToLatest">用现价</el-button>
          </div>
          <el-input-number v-model="form.basePrice" :min="0" :precision="2" :step="0.1" controls-position="right" />
        </div>
        <div class="field">
          <label>网格模式</label>
          <el-radio-group v-model="form.mode">
            <el-radio-button value="arithmetic">等差网格</el-radio-button>
            <el-radio-button value="geometric">等比网格</el-radio-button>
          </el-radio-group>
        </div>

        <div class="field">
          <label>网格数量（格）</label>
          <el-input-number v-model="form.gridCount" :min="1" :max="200" :step="1" step-strictly controls-position="right" />
        </div>
        <div class="field">
          <label>每格委托股数（股）</label>
          <el-input-number v-model="form.sharesPerGrid" :min="100" :step="100" step-strictly controls-position="right" />
        </div>
        <div class="field">
          <label>佣金（万分之）</label>
          <el-input-number v-model="commissionWan" :min="0" :max="100" :precision="2" :step="0.5" controls-position="right" />
        </div>
        <div class="field">
          <label>印花税（万分之，卖出）</label>
          <el-input-number v-model="stampWan" :min="0" :max="100" :precision="2" :step="1" controls-position="right" />
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
        </div>
        <div class="field field-switch">
          <label>佣金最低 5 元</label>
          <el-switch v-model="form.freeFive" active-text="免五" inactive-text="不免" />
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
          {{ changePct > 0 ? '+' : '' }}{{ num(changePct).toFixed(2) }}%
          <span class="muted">· 基准 {{ fmtPrice(form.basePrice) }}</span>
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
          初始 {{ initialCost > 0 ? fmtPrice(initialCost) : '--' }} → 摊薄
          {{ dilutedCost > 0 ? fmtPrice(dilutedCost) : '--' }}
          <span
            v-if="initialCost > 0 && dilutedCost > 0"
            :class="['badge', costDropPct < 0 ? 'badge-down' : costDropPct > 0 ? 'badge-up' : 'badge-flat']"
          >
            {{ costDropPct < 0 ? `已摊薄 ${fmtPct(costDropPct)}` : costDropPct > 0 ? `成本上升 ${fmtPct(costDropPct)}` : '未摊薄' }}
          </span>
        </div>
      </article>

      <article class="kpi-card">
        <div class="kpi-label">安全垫距离</div>
        <div :class="['kpi-value', safetyMargin === null ? '' : safetyMargin >= 0 ? 'up' : 'down']">
          {{ safetyMargin === null ? '--' : fmtPct(safetyMargin) }}
        </div>
        <div class="kpi-sub">
          摊薄保本价 {{ dilutedCost > 0 ? fmtPrice(dilutedCost) : '--' }} · 当前持仓 {{ currentShares }} 股
        </div>
      </article>

      <article class="kpi-card">
        <div class="kpi-label">极限备用金</div>
        <div class="kpi-value">¥ {{ fmtMoney(maxAddAmount) }}</div>
        <div class="kpi-sub">
          跌至下限 {{ fmtPrice(form.lowerPrice) }} 需追加 · 最大占用 {{ fmtMoney(maxTotalCapital) }} 元
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
              :y1="ruler.axisY - (tick.active ? 26 : 16)"
              :y2="ruler.axisY + 6"
              :stroke="tick.active ? '#1f5fbf' : '#9fb3c8'"
              :stroke-width="tick.active ? 2.5 : 1.5"
            />
            <title>档位 {{ tick.index + 1 }} · {{ tick.price.toFixed(2) }}</title>
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
          <span><i class="dot buy-dot" />买入区（低于现价）</span>
          <span><i class="dot sell-dot" />卖出区（高于现价）</span>
          <span><i class="dot cost-dot" />动态摊薄成本</span>
          <span v-if="ruler.outOfRange" class="legend-warning">现价已超出区间</span>
        </div>
      </div>
      <el-empty v-else description="填写上下限后展示价格标尺" :image-size="72" />
    </el-card>

    <!-- 档位与流水 -->
    <el-card shadow="never" class="panel">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="网格档位明细" name="levels">
          <el-table
            v-if="levels.length"
            :data="levels"
            border
            size="small"
            :row-class-name="levelRowClass"
            style="width: 100%"
          >
            <el-table-column prop="index" label="档位" width="64" align="center" />
            <el-table-column label="格线价格" width="100" align="right">
              <template #default="{ row }">{{ fmtPrice(row.price) }}</template>
            </el-table-column>
            <el-table-column label="买入触发价" width="100" align="right">
              <template #default="{ row }">{{ fmtPrice(row.buyPrice) }}</template>
            </el-table-column>
            <el-table-column label="卖出触发价" width="100" align="right">
              <template #default="{ row }">{{ fmtPrice(row.sellPrice) }}</template>
            </el-table-column>
            <el-table-column label="单格股数" width="88" align="right">
              <template #default="{ row }">{{ row.shares }}</template>
            </el-table-column>
            <el-table-column label="单格净利（元）" width="120" align="right">
              <template #default="{ row }">
                <span :class="row.netProfit > 0 ? 'profit-up' : 'profit-down'">{{ fmtMoney(row.netProfit) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="单格收益率" width="96" align="right">
              <template #default="{ row }">{{ fmtPct(row.returnRate) }}</template>
            </el-table-column>
            <el-table-column label="当前状态" width="118" align="center">
              <template #default="{ row }">
                <el-tag :type="levelStatus(row).type" size="small" effect="light">{{ levelStatus(row).label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" min-width="168" align="center">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openTradeDialog('buy', row)">记录买入</el-button>
                <el-button link type="warning" size="small" @click="openTradeDialog('sell', row)">记录卖出</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="参数填写完整后自动生成档位，或点击「计算档位与预览」" :image-size="72" />
        </el-tab-pane>

        <el-tab-pane label="实盘成交流水" name="trades">
          <div class="trades-toolbar">
            <el-button type="primary" plain @click="openTradeDialog('buy')">手动补录成交</el-button>
            <span class="trades-summary">
              累计套利 <strong :class="totalRealized >= 0 ? 'profit-up' : 'profit-down'">¥ {{ fmtMoney(totalRealized) }}</strong>
              · 手续费 {{ fmtMoney(totalFee) }} · 当前持仓 {{ currentShares }} 股
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
          <el-empty v-else description="还没有成交记录，点击「手动补录成交」或从档位表快速打卡" :image-size="72" />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 成交录入 -->
    <el-dialog v-model="tradeDialog.visible" :title="tradeDialog.manual ? '手动补录成交' : '快捷记录成交'" width="440px">
      <el-form label-width="88px">
        <el-form-item label="成交方向">
          <el-radio-group v-model="tradeForm.type" :disabled="!tradeDialog.manual">
            <el-radio-button value="buy">买入</el-radio-button>
            <el-radio-button value="sell">卖出</el-radio-button>
          </el-radio-group>
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
          <el-input-number v-model="tradeForm.fee" :min="0" :precision="2" :step="1" style="width: 100%" />
        </el-form-item>
        <el-form-item v-if="tradeForm.type === 'sell'" label="预估套利">
          <span :class="pendingProfit >= 0 ? 'profit-up' : 'profit-down'">¥ {{ fmtMoney(pendingProfit) }}</span>
          <span class="muted">（按摊薄成本 {{ fmtPrice(costBasisForTrade) }} 计）</span>
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
            <el-form-item label="步长(%)">
              <el-input-number v-model="backtestForm.stepPct" :min="0.1" :precision="2" :step="0.1" style="width: 100%" />
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
              <el-input-number v-model="backtestForm.commissionRate" :min="0" :precision="4" :step="0.0001" style="width: 100%" />
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

<script setup>
import { computed, inject, onMounted, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { mergeHoldings, parseHoldingsText } from '../utils/holdingsImport.js';
import FundFlowSpark from './FundFlowSpark.vue';
import FundFlowDrawer from './FundFlowDrawer.vue';

const PORTFOLIO_API = '/api/portfolio';
const IMPORT_PLACEHOLDER =
  '证券代码\t证券名称\t股票余额\t成本价\t现价\n600519\t贵州茅台\t100\t1500.00\t1680.00';

const defaultTableData = [
  {
    id: 1,
    code: '603288',
    name: '海天味业',
    sector: '调味发酵品',
    costPrice: 54.948,
    quantity: 480,
    currentPrice: 40.34,
    totalCost: 26375.04,
    marketValue: 19363.2,
    profitAmount: -7011.84,
    profitRatio: '-26.59%',
    positionRatio: '15.67%',
    pb: 5.47,
    pe: 31.57,
    roe: '15.46%',
    avgPe: '-',
    eps: '-',
    eps3y: '-',
    g: '-',
    peg: '-',
    expectedReturn: '-',
  },
  {
    id: 2,
    code: '600298',
    name: '安琪酵母',
    sector: '调味发酵品',
    costPrice: 37.295,
    quantity: 200,
    currentPrice: 41.56,
    totalCost: 7459,
    marketValue: 8312,
    profitAmount: 853,
    profitRatio: '11.44%',
    positionRatio: '6.73%',
    pb: 3.07,
    pe: 23.92,
    roe: '9.90%',
    avgPe: '-',
    eps: '-',
    eps3y: '-',
    g: '-',
    peg: '-',
    expectedReturn: '-',
  },
  {
    id: 4,
    code: '002507',
    name: '涪陵榨菜',
    sector: '调味发酵品',
    costPrice: 18.595,
    quantity: 390,
    currentPrice: 12.46,
    totalCost: 7252.05,
    marketValue: 4859.4,
    profitAmount: -2392.65,
    profitRatio: '-32.99%',
    positionRatio: '3.93%',
    pb: 1.69,
    pe: 18.77,
    roe: '7.58%',
    avgPe: '-',
    eps: '-',
    eps3y: '-',
    g: '-',
    peg: '-',
    expectedReturn: '-',
  },
  {
    id: 7,
    code: '600929',
    name: '雪天盐业',
    sector: '化学原料',
    costPrice: 7.618,
    quantity: 200,
    currentPrice: 6.03,
    totalCost: 1523.6,
    marketValue: 1206,
    profitAmount: -317.6,
    profitRatio: '-20.85%',
    positionRatio: '0.98%',
    pb: 1.28,
    pe: '亏损',
    roe: '0.43%',
    avgPe: '-',
    eps: '-',
    eps3y: '-',
    g: '-',
    peg: '-',
    expectedReturn: '-',
  },
  {
    id: 10,
    code: '000423',
    name: '东阿阿胶',
    sector: '中药',
    costPrice: 44.982,
    quantity: 100,
    currentPrice: 57.5,
    totalCost: 4498.2,
    marketValue: 5750,
    profitAmount: 1251.8,
    profitRatio: '27.83%',
    positionRatio: '4.65%',
    pb: 3.06,
    pe: 18.14,
    roe: '12.14%',
    avgPe: '-',
    eps: '-',
    eps3y: '-',
    g: '-',
    peg: '-',
    expectedReturn: '-',
  },
];

const tableData = ref([...defaultTableData]);

const addDialogVisible = ref(false);
const editDialogVisible = ref(false);
const importDialogVisible = ref(false);
const importText = ref('');
const importPreview = ref(null);
const importMessage = ref('');
const importMode = ref('merge');
const editingRowId = ref(null);
const refreshingPrice = ref(false);
const refreshingFundamental = ref(false);
const syncingFundFlow = ref(false);
const fundSummary = ref({});
const drawerVisible = ref(false);
const drawerCode = ref('');
const drawerName = ref('');
const drawerValuation = ref(null);
const addingStock = ref(false);
const pieTipSector = ref({ visible: false, x: 0, y: 0, text: '' });
const pieTipStock = ref({ visible: false, x: 0, y: 0, text: '' });

const addForm = ref({ code: '', costPrice: 0, quantity: 0 });
const editForm = ref({
  id: 0,
  code: '',
  name: '',
  costPrice: 0,
  quantity: 0,
  avgPe: '-',
  eps: '-',
  eps3y: '-',
});

const redColumnsWhenProfitPositive = new Set(['code', 'name', 'sector', 'costPrice', 'quantity', 'currentPrice', 'totalCost', 'marketValue', 'profitAmount', 'profitRatio', 'positionRatio']);

const tableCellClassName = ({ row, column }) => {
  if (row.profitAmount > 0 && redColumnsWhenProfitPositive.has(column.property)) {
    return 'profit-positive-cell';
  }
  return '';
};

const tableRowClassName = ({ row }) => {
  const gText = String(row.g || '')
    .replace('%', '')
    .trim();
  const rText = String(row.expectedReturn || '')
    .replace('%', '')
    .trim();
  const gNum = Number(gText);
  const rNum = Number(rText);
  if ((Number.isFinite(gNum) && gNum < 10) || (Number.isFinite(rNum) && rNum <= 0)) {
    return 'risk-row';
  }
  return '';
};

const formatMoney = n => Number(n).toFixed(2);
const formatPercent = n => `${n.toFixed(2)}%`;
const formatThreeDecimalCell = (_, __, value) => {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(3) : value;
};
const STRATEGY_EPS_YEARS = 3;
const STRATEGY_HOLD_YEARS = 3;
const piePalette = ['#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#a855f7', '#14b8a6', '#f97316', '#64748b', '#ec4899', '#06b6d4'];

const totalMarketValueNum = computed(() => tableData.value.reduce((sum, row) => sum + (Number(row.marketValue) || 0), 0));

const sectorBreakdown = computed(() => {
  const groups = new Map();
  tableData.value.forEach(row => {
    const sector = row.sector || '未分类';
    if (!groups.has(sector)) {
      groups.set(sector, {
        sector,
        totalQty: 0,
        totalMarketValue: 0,
        stocks: [],
      });
    }
    const group = groups.get(sector);
    const qty = Number(row.quantity) || 0;
    const mv = Number(row.marketValue) || 0;
    group.totalQty += qty;
    group.totalMarketValue += mv;
    group.stocks.push({
      name: row.name || '-',
      quantity: qty,
      ratio: totalMarketValueNum.value > 0 ? (mv / totalMarketValueNum.value) * 100 : 0,
    });
  });

  const result = Array.from(groups.values()).map(group => ({
    ...group,
    ratio: totalMarketValueNum.value > 0 ? (group.totalMarketValue / totalMarketValueNum.value) * 100 : 0,
  }));
  result.sort((a, b) => b.ratio - a.ratio);
  return result;
});

const stockBreakdown = computed(() => {
  const rows = tableData.value.map(row => {
    const mv = Number(row.marketValue) || 0;
    const ratio = totalMarketValueNum.value > 0 ? (mv / totalMarketValueNum.value) * 100 : 0;
    return {
      name: row.name || row.code || '-',
      ratio,
      sector: row.sector || '未分类',
    };
  });
  rows.sort((a, b) => b.ratio - a.ratio);
  return rows;
});

const grandTotals = computed(() => ({
  totalQty: tableData.value.reduce((sum, row) => sum + (Number(row.quantity) || 0), 0),
  totalRatio: sectorBreakdown.value.reduce((sum, item) => sum + item.ratio, 0),
}));

function toPolar(cx, cy, r, angleDeg) {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return {
    x: cx + r * Math.cos(rad),
    y: cy + r * Math.sin(rad),
  };
}

function describeDonutArc(startAngle, endAngle, outerR = 82, innerR = 48) {
  const largeArc = endAngle - startAngle > 180 ? 1 : 0;
  const outerStart = toPolar(0, 0, outerR, startAngle);
  const outerEnd = toPolar(0, 0, outerR, endAngle);
  const innerStart = toPolar(0, 0, innerR, endAngle);
  const innerEnd = toPolar(0, 0, innerR, startAngle);
  return [`M ${outerStart.x} ${outerStart.y}`, `A ${outerR} ${outerR} 0 ${largeArc} 1 ${outerEnd.x} ${outerEnd.y}`, `L ${innerStart.x} ${innerStart.y}`, `A ${innerR} ${innerR} 0 ${largeArc} 0 ${innerEnd.x} ${innerEnd.y}`, 'Z'].join(' ');
}

function buildPieSlices(items) {
  if (!items.length) return [];
  const total = items.reduce((sum, item) => sum + Number(item.ratio || 0), 0);
  if (total <= 0) return [];

  let cursor = 0;
  return items.map((item, index) => {
    const safeRatio = Number(item.ratio || 0);
    const angle = index === items.length - 1 ? 360 - cursor : (safeRatio / total) * 360;
    const startAngle = cursor;
    const endAngle = cursor + angle;
    cursor = endAngle;
    return {
      ...item,
      startAngle,
      endAngle,
    };
  });
}

function handlePieMove(event, item, type) {
  const target = type === 'sector' ? pieTipSector : pieTipStock;
  target.value = {
    visible: true,
    x: event.clientX + 12,
    y: event.clientY + 12,
    text: `${item.label}: ${item.ratio.toFixed(2)}%`,
  };
}

function handlePieLeave(type) {
  const target = type === 'sector' ? pieTipSector : pieTipStock;
  target.value.visible = false;
}

const sectorPieData = computed(() => {
  const rawItems = sectorBreakdown.value
    .filter(item => item.ratio > 0)
    .map((item, index) => ({
      label: item.sector,
      ratio: Number(item.ratio.toFixed(2)),
      color: piePalette[index % piePalette.length],
    }));
  const items = buildPieSlices(rawItems);
  return {
    items,
  };
});

const stockPieData = computed(() => {
  const topStocks = stockBreakdown.value.filter(item => item.ratio > 0).slice(0, 10);
  const rawItems = topStocks.map((item, index) => ({
    label: item.name,
    ratio: Number(item.ratio.toFixed(2)),
    color: piePalette[index % piePalette.length],
  }));
  const items = buildPieSlices(rawItems);
  return {
    items,
  };
});

function recalcRow(row) {
  const quantity = Number(row.quantity) || 0;
  const costPrice = Number(row.costPrice) || 0;
  const currentPrice = Number(row.currentPrice) || 0;
  const totalCost = costPrice * quantity;
  const marketValue = currentPrice * quantity;
  const profitAmount = marketValue - totalCost;
  const profitRatio = totalCost > 0 ? (profitAmount / totalCost) * 100 : 0;

  row.totalCost = Number(formatMoney(totalCost));
  row.marketValue = Number(formatMoney(marketValue));
  row.profitAmount = Number(formatMoney(profitAmount));
  row.profitRatio = formatPercent(profitRatio);
}

function toNumberOrNull(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function recalcStrategyMetrics(row) {
  const peTtm = toNumberOrNull(row.pe);
  const avgPe = toNumberOrNull(row.avgPe);
  const eps = toNumberOrNull(row.eps);
  const eps3y = toNumberOrNull(row.eps3y);

  let gDecimal = null;
  if (eps !== null && eps3y !== null && eps > 0 && eps3y > 0) {
    gDecimal = Math.pow(eps3y / eps, 1 / STRATEGY_EPS_YEARS) - 1;
    row.g = `${(gDecimal * 100).toFixed(2)}%`;
  } else {
    row.g = '-';
  }

  const gPercent = gDecimal === null ? null : gDecimal * 100;
  if (peTtm !== null && peTtm > 0 && gPercent !== null && gPercent > 0) {
    row.peg = (peTtm / gPercent).toFixed(2);
  } else {
    row.peg = '-';
  }

  if (peTtm !== null && peTtm > 0 && avgPe !== null && avgPe > 0 && gDecimal !== null && gDecimal > -1) {
    const annualizedReturn = Math.pow(avgPe / peTtm, 1 / STRATEGY_HOLD_YEARS) * (1 + gDecimal) - 1;
    row.expectedReturn = `${(annualizedReturn * 100).toFixed(2)}%`;
  } else {
    row.expectedReturn = '-';
  }
}

function recalcAllPositionRatios() {
  const totalMarketValue = tableData.value.reduce((sum, row) => sum + (Number(row.marketValue) || 0), 0);
  tableData.value.forEach(row => {
    const ratio = totalMarketValue > 0 ? (Number(row.marketValue) / totalMarketValue) * 100 : 0;
    row.positionRatio = formatPercent(ratio);
  });
}

function refreshDerivedFields(shouldSave = true) {
  tableData.value.forEach(row => {
    if (row.avgPe === undefined || row.avgPe === null || row.avgPe === '') row.avgPe = '-';
    if (row.eps === undefined || row.eps === null || row.eps === '') row.eps = '-';
    if (row.eps3y === undefined || row.eps3y === null || row.eps3y === '') row.eps3y = '-';
    recalcStrategyMetrics(row);
    recalcRow(row);
  });
  recalcAllPositionRatios();
  if (shouldSave) {
    void saveTableData();
  }
}

async function saveTableData() {
  try {
    const resp = await fetch(PORTFOLIO_API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(tableData.value),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  } catch (error) {
    console.error('保存到文件失败:', error);
    ElMessage.error('保存失败：持仓数据没有写入文件，请确认开发服务仍在运行');
  }
}

async function loadTableDataFromFile() {
  try {
    const resp = await fetch(PORTFOLIO_API, { method: 'GET' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (Array.isArray(data) && data.length > 0) {
      tableData.value = data;
    } else {
      tableData.value = [...defaultTableData];
      await saveTableData();
    }
  } catch (error) {
    console.error('读取文件失败，回退默认数据:', error);
    tableData.value = [...defaultTableData];
  }
}

function parseCode(rawCode) {
  const value = String(rawCode || '')
    .trim()
    .toUpperCase();
  const match = value.match(/(\d{6})/);
  if (!match) return null;
  const code = match[1];
  const suffix = value.replace(code, '').replace('.', '');
  let market = '';
  if (suffix.includes('SH')) market = '1';
  if (suffix.includes('SZ')) market = '0';
  if (!market) {
    market = code.startsWith('6') || code.startsWith('5') || code.startsWith('9') ? '1' : '0';
  }
  return {
    pureCode: code,
    secid: `${market}.${code}`,
    market,
  };
}

function getPriceFromQuoteItem(item) {
  const priceRaw = Number(item.f2);
  if (!Number.isFinite(priceRaw) || priceRaw <= 0) return null;
  return Number(priceRaw.toFixed(2));
}

function buildRoeCodes(rows) {
  return Array.from(
    new Set(
      rows
        .map(row => {
          const parsed = parseCode(row.code);
          if (!parsed) return null;
          return `${parsed.market === '1' ? 'SH' : 'SZ'}${parsed.pureCode}`;
        })
        .filter(Boolean),
    ),
  );
}

async function fetchQuoteByCodes(rows) {
  const secids = Array.from(new Set(rows.map(row => parseCode(row.code)?.secid).filter(Boolean)));
  if (!secids.length) return { quoteMap: new Map(), stale: false, message: '' };

  // 统一走服务端 /api/quote：由它做缓存与节流，避免浏览器直连被风控
  const resp = await fetch(`/api/quote?secids=${encodeURIComponent(secids.join(','))}`, { method: 'GET' });
  const json = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(json?.message || `行情请求失败：HTTP ${resp.status}`);

  const diff = Array.isArray(json?.diff) ? json.diff : [];
  if (!diff.length) throw new Error(json?.message || '行情数据为空');

  const quoteMap = new Map();
  diff.forEach(item => {
    const code = String(item.f12 || '').trim();
    if (code) quoteMap.set(code, item);
  });
  return { quoteMap, stale: Boolean(json?.stale), message: String(json?.message || '') };
}

async function fetchRoeBatch(rows) {
  const codes = buildRoeCodes(rows);
  if (!codes.length) return { roeMap: new Map(), message: '' };

  // 服务端按 500ms 间隔逐只拉取并缓存 7 天，重复刷新不会再打上游
  const resp = await fetch(`/api/roe?codes=${encodeURIComponent(codes.join(','))}`, { method: 'GET' });
  const json = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(json?.message || `ROE 请求失败：HTTP ${resp.status}`);

  const roeMap = new Map();
  Object.entries(json?.items || {}).forEach(([secCode, info]) => {
    const pureCode = secCode.replace(/^(SH|SZ)/, '');
    const roeNum = Number(info?.roe);
    if (Number.isFinite(roeNum)) roeMap.set(pureCode, `${roeNum.toFixed(2)}%`);
  });
  return { roeMap, message: String(json?.message || '') };
}

async function refreshCurrentPrices() {
  if (!tableData.value.length) {
    ElMessage.warning('当前没有可刷新的股票');
    return;
  }

  refreshingPrice.value = true;
  try {
    const { quoteMap, stale, message } = await fetchQuoteByCodes(tableData.value);
    let updatedCount = 0;

    tableData.value.forEach(row => {
      const pureCode = parseCode(row.code)?.pureCode;
      const quote = pureCode ? quoteMap.get(pureCode) : null;
      if (!quote) return;

      // 名称 / 板块 / PB 缺失时顺带补全（批量导入进来的股票就靠这一步补齐）
      const quoteName = String(quote.f14 || '').trim();
      if (quoteName && (!row.name || row.name === row.code)) row.name = quoteName;

      const quoteSector = String(quote.f100 || '').trim();
      if (quoteSector && (!row.sector || row.sector === '-' || row.sector === '未分类')) row.sector = quoteSector;

      if (row.pb === '-' || row.pb === undefined || row.pb === null || row.pb === '') {
        const pbNum = Number(quote.f23);
        if (Number.isFinite(pbNum)) row.pb = Number(pbNum.toFixed(2));
      }

      // 绿色按钮同步刷新 PE(TTM)：与现价同源于行情接口
      if (Number.isFinite(Number(quote.f115))) {
        row.pe = Number(Number(quote.f115).toFixed(2));
      } else {
        row.pe = '亏损';
      }

      const price = getPriceFromQuoteItem(quote);
      if (price === null) return;
      row.currentPrice = price;
      updatedCount += 1;
    });

    refreshDerivedFields();
    if (message) ElMessage.warning(message);
    else if (stale) ElMessage.warning('行情接口暂时不可用，已使用本地缓存');
    else if (updatedCount > 0) ElMessage.success(`现价刷新完成：已更新 ${updatedCount} 只`);
    else ElMessage.warning('未匹配到可更新的现价数据');
  } catch (error) {
    ElMessage.error(`刷新失败：${error.message || '请稍后重试'}`);
  } finally {
    refreshingPrice.value = false;
  }
}

async function refreshFundamentals() {
  if (!tableData.value.length) {
    ElMessage.warning('当前没有可刷新的股票');
    return;
  }

  refreshingFundamental.value = true;
  try {
    // 只刷新财报口径的 ROE；PE(TTM) 与现价同源于行情接口，由「刷新现价」一并更新。
    // 一次性把所有代码发给服务端，由它按 500ms 间隔逐只拉取（缓存 7 天内的直接返回）。
    const { roeMap, message } = await fetchRoeBatch(tableData.value);
    let updatedCount = 0;
    tableData.value.forEach(row => {
      const pureCode = parseCode(row.code)?.pureCode;
      const roe = pureCode ? roeMap.get(pureCode) : null;
      if (roe) {
        row.roe = roe;
        updatedCount += 1;
      }
    });

    refreshDerivedFields();
    if (message) ElMessage.warning(message);
    else if (updatedCount > 0) ElMessage.success(`ROE 刷新完成：已更新 ${updatedCount} 只`);
    else ElMessage.warning('未获取到可更新的财报数据');
  } catch (error) {
    ElMessage.error(`刷新失败：${error.message || '请稍后重试'}`);
  } finally {
    refreshingFundamental.value = false;
  }
}

// ---------- 主力资金流（内嵌微图 + 详情抽屉） ----------
async function loadFundSummary() {
  const codes = tableData.value.map(row => String(row.code || '').trim()).filter(Boolean);
  if (!codes.length) {
    fundSummary.value = {};
    return;
  }
  try {
    const resp = await fetch(`/api/fund-flow/mini-summary?codes=${encodeURIComponent(codes.join(','))}`);
    if (!resp.ok) return;
    const data = await resp.json();
    fundSummary.value = data?.items || {};
  } catch {
    // 忽略
  }
}

async function syncFundFlow() {
  syncingFundFlow.value = true;
  try {
    const resp = await fetch('/api/fund-flow/sync-portfolio', { method: 'POST' });
    const json = await resp.json().catch(() => ({}));
    if (resp.status === 409) {
      ElMessage.warning(json?.message || '资金流同步正在进行中');
      return;
    }
    if (!resp.ok) throw new Error(json?.message || `HTTP ${resp.status}`);
    ElMessage.success('主力资金同步已启动，约 30 秒后自动刷新');
    for (let i = 0; i < 120; i++) {
      await new Promise(r => setTimeout(r, 1500));
      try {
        const pr = await fetch('/api/fund-flow/sync-progress');
        const pd = await pr.json();
        if (!pd?.job?.running) {
          if (pd?.job?.phase === 'error') ElMessage.error(pd.job.error || '同步失败');
          break;
        }
      } catch {
        break;
      }
    }
    await loadFundSummary();
  } catch (error) {
    ElMessage.error(`同步失败：${error.message || '请稍后重试'}`);
  } finally {
    syncingFundFlow.value = false;
  }
}

function openDrawer(row) {
  drawerCode.value = String(row.code || '').trim();
  drawerName.value = row.name || '';
  drawerValuation.value = {
    pb: row.pb,
    pe: row.pe,
    roe: row.roe,
    avgPe: row.avgPe,
    eps: row.eps,
    eps3y: row.eps3y,
    g: row.g,
    peg: row.peg,
    expectedReturn: row.expectedReturn,
  };
  drawerVisible.value = true;
}

function openImportDialog() {
  importDialogVisible.value = true;
  importText.value = '';
  importPreview.value = null;
  importMessage.value = '';
}

function parseImportText() {
  const result = parseHoldingsText(importText.value);
  importMessage.value = result.message;
  importPreview.value = result.ok ? result.rows : null;
  if (!result.ok) {
    ElMessage.warning(result.message);
    return;
  }
  const valid = result.rows.filter(row => !row.error).length;
  ElMessage.success(`解析到 ${result.rows.length} 行，其中 ${valid} 行可导入`);
}

async function confirmImport() {
  if (!importPreview.value) {
    ElMessage.warning('请先点击「解析预览」');
    return;
  }
  const validRows = importPreview.value.filter(row => !row.error);
  if (!validRows.length) {
    ElMessage.warning('没有可导入的有效行');
    return;
  }

  if (importMode.value === 'overwrite') {
    try {
      await ElMessageBox.confirm(
        `将用这 ${validRows.length} 只股票替换当前全部 ${tableData.value.length} 条持仓，确定吗？`,
        '覆盖导入',
        { confirmButtonText: '覆盖', cancelButtonText: '取消', type: 'warning' },
      );
    } catch {
      return;
    }
  }

  const baseRows = importMode.value === 'overwrite' ? [] : tableData.value;
  const { data, added, updated, ignored } = mergeHoldings(validRows, baseRows);
  tableData.value = data;
  refreshDerivedFields();
  importDialogVisible.value = false;
  ElMessage.success(`导入完成：新增 ${added} 只，更新 ${updated} 只${ignored ? `，忽略 ${ignored} 行` : ''}`);

  // 名称 / 板块 / 现价 / PE 用行情接口补齐
  void refreshCurrentPrices();
}

function openAddDialog() {
  addForm.value = { code: '', costPrice: 0, quantity: 0 };
  addDialogVisible.value = true;
}

async function confirmAdd() {
  const parsed = parseCode(addForm.value.code);
  if (!parsed) {
    ElMessage.warning('请输入正确的 6 位股票代码');
    return;
  }
  if (!(Number(addForm.value.costPrice) > 0) || !(Number(addForm.value.quantity) > 0)) {
    ElMessage.warning('请填写有效的成本价和持仓数量');
    return;
  }

  addingStock.value = true;
  try {
    const { quoteMap } = await fetchQuoteByCodes([{ code: parsed.pureCode }]);
    const quote = quoteMap.get(parsed.pureCode);
    if (!quote) {
      ElMessage.warning('未查到该股票行情，请检查代码');
      return;
    }

    const currentPrice = getPriceFromQuoteItem(quote) ?? 0;
    const name = String(quote.f14 || parsed.pureCode);
    const sector = String(quote.f100 || '-');
    const pb = Number.isFinite(Number(quote.f23)) ? Number(Number(quote.f23).toFixed(2)) : '-';
    let pe = '亏损';
    if (Number.isFinite(Number(quote.f115))) pe = Number(Number(quote.f115).toFixed(2));

    let roe = '-';
    const { roeMap } = await fetchRoeBatch([{ code: parsed.pureCode }]);
    const latestRoe = roeMap.get(parsed.pureCode);
    if (latestRoe) roe = latestRoe;

    const maxId = tableData.value.reduce((max, row) => Math.max(max, row.id), 0);
    tableData.value.push({
      id: maxId + 1,
      code: parsed.pureCode,
      name,
      sector,
      costPrice: Number(addForm.value.costPrice),
      quantity: Number(addForm.value.quantity),
      currentPrice,
      totalCost: 0,
      marketValue: 0,
      profitAmount: 0,
      profitRatio: '0.00%',
      positionRatio: '0.00%',
      pb,
      pe,
      roe,
      avgPe: '-',
      eps: '-',
      eps3y: '-',
    });

    refreshDerivedFields();
    addDialogVisible.value = false;
    ElMessage.success('新增成功，已自动获取行情与财报数据');
  } catch (error) {
    ElMessage.error(`新增失败：${error.message || '请稍后重试'}`);
  } finally {
    addingStock.value = false;
  }
}

function openEditDialog(row) {
  editingRowId.value = row.id;
  editForm.value = {
    id: row.id,
    code: row.code,
    name: row.name,
    costPrice: Number(row.costPrice) || 0,
    quantity: Number(row.quantity) || 0,
    avgPe: String(row.avgPe ?? '-'),
    eps: String(row.eps ?? '-'),
    eps3y: String(row.eps3y ?? '-'),
  };
  editDialogVisible.value = true;
}

function confirmEdit() {
  const idx = tableData.value.findIndex(item => item.id === editingRowId.value);
  if (idx < 0) return;
  tableData.value[idx].costPrice = Number(editForm.value.costPrice) || 0;
  tableData.value[idx].quantity = Number(editForm.value.quantity) || 0;
  tableData.value[idx].avgPe = String(editForm.value.avgPe || '').trim() || '-';
  tableData.value[idx].eps = String(editForm.value.eps || '').trim() || '-';
  tableData.value[idx].eps3y = String(editForm.value.eps3y || '').trim() || '-';
  refreshDerivedFields();
  editDialogVisible.value = false;
  ElMessage.success('修改成功');
}

async function deleteRow(row) {
  try {
    await ElMessageBox.confirm(`确定删除 ${row.name}（${row.code}）吗？`, '删除确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    });
    tableData.value = tableData.value.filter(item => item.id !== row.id);
    refreshDerivedFields();
    ElMessage.success('删除成功');
  } catch {
    // 用户取消
  }
}

const getSummaries = ({ columns, data }) => {
  const sums = [];
  const totalCost = data.reduce((sum, row) => sum + (Number(row.totalCost) || 0), 0);
  const totalMarketValue = data.reduce((sum, row) => sum + (Number(row.marketValue) || 0), 0);
  const totalProfit = data.reduce((sum, row) => sum + (Number(row.profitAmount) || 0), 0);
  const totalQty = data.reduce((sum, row) => sum + (Number(row.quantity) || 0), 0);
  const totalProfitRatio = totalCost > 0 ? (totalProfit / totalCost) * 100 : 0;

  columns.forEach((column, index) => {
    if (index === 0) {
      sums[index] = '汇总';
      return;
    }

    switch (column.property) {
      case 'quantity':
        sums[index] = formatMoney(totalQty);
        break;
      case 'totalCost':
        sums[index] = formatMoney(totalCost);
        break;
      case 'marketValue':
        sums[index] = formatMoney(totalMarketValue);
        break;
      case 'profitAmount':
        sums[index] = formatMoney(totalProfit);
        break;
      case 'profitRatio':
        sums[index] = formatPercent(totalProfitRatio);
        break;
      case 'positionRatio':
        sums[index] = '100.00%';
        break;
      default:
        sums[index] = '-';
    }
  });

  return sums;
};

onMounted(() => {
  void (async () => {
    await loadTableDataFromFile();
    refreshDerivedFields(false);
    void loadFundSummary();
  })();
});

// 收盘一键更新完成后自动刷新
const appRefreshSignal = inject('appRefreshSignal', ref(0));
const openAnalysisReport = inject('openAnalysisReport', () => {});
watch(appRefreshSignal, () => {
  void loadTableDataFromFile().then(() => {
    refreshDerivedFields(false);
    void loadFundSummary();
  });
});
</script>

<template>
  <div class="portfolio-container">
    <div class="toolbar">
      <el-button type="primary" @click="openAddDialog">新增股票</el-button>
      <el-button @click="openImportDialog">批量导入</el-button>
      <el-button type="success" :loading="refreshingPrice" @click="refreshCurrentPrices">刷新当前股票现价</el-button>
      <el-button type="warning" :loading="refreshingFundamental" @click="refreshFundamentals">刷新 ROE（财报）</el-button>
      <el-button type="danger" plain :loading="syncingFundFlow" @click="syncFundFlow">同步主力资金</el-button>
    </div>

    <el-table :data="tableData" border style="width: 100%" max-height="620" :cell-class-name="tableCellClassName" :row-class-name="tableRowClassName" show-summary :summary-method="getSummaries" size="small" @row-click="openDrawer">
      <el-table-column type="index" label="序号" width="60" align="center" fixed />
      <el-table-column prop="code" label="股票代码" width="100" align="center" fixed />
      <el-table-column prop="name" label="股票名称" width="150" align="center" fixed>
        <template #default="scope">
          <span class="name-cell" @click="openAnalysisReport(scope.row.code)" title="点击查看财报透视">
            {{ scope.row.name }}
            <span class="name-report-icon">📊</span>
          </span>
          <el-tag v-if="fundSummary[scope.row.code]?.lhb" size="small" type="danger" style="margin-left:4px">🔥龙虎榜</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="sector" label="所属板块" width="120" align="center" />
      <el-table-column prop="costPrice" label="成本价" width="100" align="right" />
      <el-table-column prop="quantity" label="持仓数量" width="100" align="right" />
      <el-table-column prop="currentPrice" label="现价" width="100" align="right" :formatter="formatThreeDecimalCell" />
      <el-table-column prop="totalCost" label="成本总金额" width="130" align="right" :formatter="formatThreeDecimalCell" />
      <el-table-column prop="marketValue" label="持仓市值" width="130" align="right" :formatter="formatThreeDecimalCell" />
      <el-table-column prop="profitAmount" label="盈亏金额" width="110" align="right" :formatter="formatThreeDecimalCell" />
      <el-table-column prop="profitRatio" label="盈亏比例 (%)" width="120" align="right" />
      <el-table-column prop="positionRatio" label="仓位占比 (%)" width="120" align="right" />

      <el-table-column label="近5日主力动向" width="130" align="center">
        <template #default="scope">
          <FundFlowSpark :values="fundSummary[scope.row.code]?.mainNetInflow5 || []" :sum="fundSummary[scope.row.code]?.sum5 || 0" />
        </template>
      </el-table-column>

      <el-table-column label="操作" width="140" fixed="right" align="center">
        <template #default="scope">
          <el-button link type="primary" @click.stop="openEditDialog(scope.row)">修改</el-button>
          <el-button link type="danger" @click.stop="deleteRow(scope.row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <section class="allocation-card">
      <h3>仓位分类</h3>
      <div class="allocation-grid">
        <div class="allocation-table-wrap">
          <table class="allocation-table">
            <thead>
              <tr>
                <th>所属板块</th>
                <th>股票名称</th>
                <th>持仓数量</th>
                <th>仓位占比（%）</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="sector in sectorBreakdown" :key="sector.sector">
                <tr v-for="(stock, idx) in sector.stocks" :key="`${sector.sector}-${stock.name}-${idx}`">
                  <td>{{ idx === 0 ? sector.sector : '' }}</td>
                  <td>{{ stock.name }}</td>
                  <td class="num">{{ stock.quantity }}</td>
                  <td class="num">{{ stock.ratio.toFixed(2) }}%</td>
                </tr>
                <tr class="subtotal">
                  <td>{{ sector.sector }} 汇总</td>
                  <td>-</td>
                  <td class="num">{{ sector.totalQty }}</td>
                  <td class="num">{{ sector.ratio.toFixed(2) }}%</td>
                </tr>
              </template>
              <tr class="grand-total">
                <td>总计</td>
                <td>-</td>
                <td class="num">{{ grandTotals.totalQty }}</td>
                <td class="num">{{ grandTotals.totalRatio.toFixed(2) }}%</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="pie-panel">
          <div class="pie-card">
            <h4>板块占比</h4>
            <svg class="pie-svg" viewBox="0 0 220 220" aria-label="板块占比饼图">
              <g transform="translate(110,110)">
                <path v-for="item in sectorPieData.items" :key="`sector-slice-${item.label}`" :d="describeDonutArc(item.startAngle, item.endAngle, 82, 48)" :fill="item.color" @mousemove="handlePieMove($event, item, 'sector')" @mouseleave="handlePieLeave('sector')" />
              </g>
            </svg>
            <div v-if="pieTipSector.visible" class="pie-tip" :style="{ left: `${pieTipSector.x}px`, top: `${pieTipSector.y}px` }">
              {{ pieTipSector.text }}
            </div>
            <ul class="legend">
              <li v-for="item in sectorPieData.items" :key="`sector-${item.label}`">
                <i :style="{ backgroundColor: item.color }"></i>
                <span>{{ item.label }}</span>
                <strong>{{ item.ratio.toFixed(2) }}%</strong>
              </li>
            </ul>
          </div>
          <div class="pie-card">
            <h4>股票占比（Top10）</h4>
            <svg class="pie-svg" viewBox="0 0 220 220" aria-label="股票占比饼图">
              <g transform="translate(110,110)">
                <path v-for="item in stockPieData.items" :key="`stock-slice-${item.label}`" :d="describeDonutArc(item.startAngle, item.endAngle, 82, 48)" :fill="item.color" @mousemove="handlePieMove($event, item, 'stock')" @mouseleave="handlePieLeave('stock')" />
              </g>
            </svg>
            <div v-if="pieTipStock.visible" class="pie-tip" :style="{ left: `${pieTipStock.x}px`, top: `${pieTipStock.y}px` }">
              {{ pieTipStock.text }}
            </div>
            <ul class="legend">
              <li v-for="item in stockPieData.items" :key="`stock-${item.label}`">
                <i :style="{ backgroundColor: item.color }"></i>
                <span>{{ item.label }}</span>
                <strong>{{ item.ratio.toFixed(2) }}%</strong>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </section>
  </div>

  <el-dialog v-model="addDialogVisible" title="新增股票" width="420px">
    <el-form :model="addForm" label-width="110px">
      <el-form-item label="股票代码">
        <el-input v-model.trim="addForm.code" placeholder="例如 600519 或 600519.SH" />
      </el-form-item>
      <el-form-item label="成本价">
        <el-input-number v-model="addForm.costPrice" :min="0" :step="0.001" :precision="3" />
      </el-form-item>
      <el-form-item label="持仓数量">
        <el-input-number v-model="addForm.quantity" :min="100" :step="10" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="addDialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="addingStock" @click="confirmAdd">确定</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="editDialogVisible" title="修改持仓" width="420px">
    <el-form :model="editForm" label-width="110px">
      <el-form-item label="股票代码">
        <el-input v-model="editForm.code" disabled />
      </el-form-item>
      <el-form-item label="股票名称">
        <el-input v-model="editForm.name" disabled />
      </el-form-item>
      <el-form-item label="成本价">
        <el-input-number v-model="editForm.costPrice" :min="0" :step="0.001" :precision="3" />
      </el-form-item>
      <el-form-item label="持仓数量">
        <el-input-number v-model="editForm.quantity" :min="0" :step="10" />
      </el-form-item>
      <el-form-item label="平均pe">
        <el-input v-model.trim="editForm.avgPe" />
      </el-form-item>
      <el-form-item label="eps">
        <el-input v-model.trim="editForm.eps" />
      </el-form-item>
      <el-form-item label="eps（3年）">
        <el-input v-model.trim="editForm.eps3y" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="editDialogVisible = false">取消</el-button>
      <el-button type="primary" @click="confirmEdit">保存</el-button>
    </template>
  </el-dialog>
  <el-dialog v-model="importDialogVisible" title="批量导入持仓" width="780px">
    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="把同花顺 / 券商客户端里的「持仓」表格连表头一起复制，直接粘贴到下面"
      description="需要包含 代码 / 数量 / 成本价 三列；支持 Tab、逗号、空格分隔，Excel 复制出来的也能用，「合计」行会自动跳过"
      class="import-tip"
    />
    <div class="import-mode">
      <span class="import-mode-label">导入方式</span>
      <el-radio-group v-model="importMode">
        <el-radio value="merge">合并（按代码更新已有持仓）</el-radio>
        <el-radio value="overwrite">覆盖（清空现有持仓后导入）</el-radio>
      </el-radio-group>
    </div>
    <el-input v-model="importText" type="textarea" :rows="8" :placeholder="IMPORT_PLACEHOLDER" class="import-input" />
    <div class="import-actions">
      <el-button @click="parseImportText">解析预览</el-button>
      <span class="import-message">{{ importMessage }}</span>
    </div>
    <el-table v-if="importPreview" :data="importPreview" size="small" border max-height="240">
      <el-table-column prop="code" label="代码" width="90" />
      <el-table-column prop="name" label="名称" width="110" />
      <el-table-column prop="quantity" label="数量" width="90" align="right" />
      <el-table-column prop="costPrice" label="成本价" width="90" align="right" />
      <el-table-column prop="currentPrice" label="现价" width="90" align="right" />
      <el-table-column label="状态" min-width="110">
        <template #default="scope">
          <span :class="scope.row.error ? 'import-bad' : 'import-good'">{{ scope.row.error || '可导入' }}</span>
        </template>
      </el-table-column>
    </el-table>
    <template #footer>
      <el-button @click="importDialogVisible = false">取消</el-button>
      <el-button type="primary" @click="confirmImport">导入</el-button>
    </template>
  </el-dialog>

  <FundFlowDrawer v-model="drawerVisible" :code="drawerCode" :name="drawerName" :valuation="drawerValuation" />
</template>

<style scoped>

.portfolio-container {
  padding: 0;
}

.toolbar {
  margin-bottom: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.allocation-card {
  margin-top: 16px;
  padding: 14px;
  border: 1px solid #d8e2ec;
  border-radius: 10px;
  background: #ffffff;
}

.allocation-card h3 {
  margin: 0 0 12px;
  color: #2b3f55;
  font-size: 1.02rem;
  font-weight: 700;
}

.allocation-grid {
  display: grid;
  grid-template-columns: 1.45fr 1fr;
  gap: 14px;
}

.allocation-table-wrap {
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  overflow: visible;
}

.allocation-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}

.allocation-table th,
.allocation-table td {
  border-bottom: 1px solid #e8edf3;
  padding: 8px 10px;
  text-align: left;
  white-space: nowrap;
}

.allocation-table thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #edf4fb;
  color: #33475b;
  font-weight: 700;
}

.allocation-table td.num {
  text-align: right;
}

.allocation-table tr.subtotal td {
  font-weight: 700;
  color: #1f3d5c;
  background: #f8fbff;
}

.allocation-table tr.grand-total td {
  font-weight: 800;
  color: #16395c;
  background: #eef5ff;
  border-top: 2px solid #c6d8ef;
}

.pie-panel {
  display: grid;
  gap: 14px;
}

.pie-card {
  position: relative;
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  padding: 10px;
  background: #fbfdff;
}

.pie-card h4 {
  margin: 0 0 8px;
  font-size: 0.95rem;
  color: #33475b;
}

.pie-svg {
  width: 190px;
  height: 190px;
  display: block;
  margin: 8px auto 12px;
}

.pie-svg path {
  transition:
    transform 0.15s ease,
    opacity 0.15s ease;
  transform-origin: center;
  cursor: pointer;
}

.pie-svg path:hover {
  opacity: 0.9;
  transform: scale(1.03);
}

.pie-tip {
  position: fixed;
  z-index: 9999;
  pointer-events: none;
  padding: 6px 10px;
  border-radius: 6px;
  color: #fff;
  font-size: 12px;
  background: rgba(17, 24, 39, 0.9);
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.2);
}

.legend {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
}

.legend li {
  display: grid;
  grid-template-columns: 12px 1fr auto;
  align-items: center;
  gap: 8px;
  font-size: 0.86rem;
  color: #445a73;
}

.legend i {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  display: inline-block;
}

.legend strong {
  color: #1f3d5c;
}

/* 调大表格主体内容字体 */
:deep(.el-table) {
  font-size: 16px; /* 默认是14px，改为16px或者更大 */
}

:deep(.el-table .profit-positive-cell) {
  color: #f56c6c;
}

:deep(.el-table .risk-row td.el-table__cell) {
  background-color: #fff7d6 !important;
}

:deep(.el-table th.el-table__cell) {
  /* 表头稍微比内容大一点 */
  font-size: 18px;
  background-color: #e3f2fd;
  color: #333;
  font-weight: bold;
}

/* 如果觉得字体变大后行高太挤，可以调大单元格的 padding */
:deep(.el-table .el-table__cell) {
  padding: 8px 0; /* 增加上下内边距，让行变宽 */
}

:deep(.el-table__footer-wrapper tbody td.el-table__cell) {
  font-weight: bold;
  background-color: #f5f7fa;
}

@media (max-width: 960px) {
  .allocation-grid {
    grid-template-columns: 1fr;
  }
}

.import-tip {
  margin-bottom: 12px;
}

.import-mode {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.import-mode-label {
  color: #445a73;
  font-size: 0.9rem;
}

.import-input {
  margin-bottom: 10px;
}

.import-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.import-message {
  color: #6b7c93;
  font-size: 0.86rem;
}

.import-good {
  color: #1eb866;
}

.import-bad {
  color: #d17057;
}

.name-cell {
  cursor: pointer;
  font-weight: 600;
  color: #21384f;
}
.name-cell:hover {
  color: #1d4ed8;
}
.name-report-icon {
  font-size: 0.72rem;
  opacity: 0.7;
  margin-left: 2px;
}

</style>

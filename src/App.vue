<template>
  <div class="app-shell">
    <aside class="left-nav">
      <div class="brand">投资面板</div>
      <ul class="menu-list">
        <li v-for="item in sideMenus" :key="item.key" :class="{ active: activeMenu === item.key }" @click="activeMenu = item.key">
          {{ item.label }}
        </li>
      </ul>
    </aside>

    <section class="content-area">
      <header class="content-header">
        <h2>{{ menuTitleMap[activeMenu] }}</h2>
      </header>

      <main class="content-body">
        <template v-if="activeMenu === 'strategy'">
          <Strategy @add-watchlist="handleAddWatchlist" />
        </template>

        <template v-else-if="activeMenu === 'positions'">
          <div class="portfolio-container">
            <div class="toolbar">
              <el-button type="primary" @click="openAddDialog">新增股票</el-button>
              <el-button type="success" :loading="refreshingPrice" @click="refreshCurrentPrices">刷新当前股票现价</el-button>
              <el-button type="warning" :loading="refreshingFundamental" @click="refreshFundamentals">刷新 PE/ROE（财报）</el-button>
            </div>

            <el-table :data="tableData" border style="width: 100%" :cell-class-name="tableCellClassName" :row-class-name="tableRowClassName" show-summary :summary-method="getSummaries" size="small">
              <el-table-column type="index" label="序号" width="60" align="center" />
              <el-table-column prop="code" label="股票代码" width="100" align="center" />
              <el-table-column prop="name" label="股票名称" width="120" align="center" />
              <el-table-column prop="sector" label="所属板块" width="120" align="center" />
              <el-table-column prop="costPrice" label="成本价" width="100" align="right" />
              <el-table-column prop="quantity" label="持仓数量" width="100" align="right" />
              <el-table-column prop="currentPrice" label="现价" width="100" align="right" :formatter="formatThreeDecimalCell" />
              <el-table-column prop="totalCost" label="成本总金额" width="130" align="right" :formatter="formatThreeDecimalCell" />
              <el-table-column prop="marketValue" label="持仓市值" width="130" align="right" :formatter="formatThreeDecimalCell" />
              <el-table-column prop="profitAmount" label="盈亏金额" width="110" align="right" :formatter="formatThreeDecimalCell" />
              <el-table-column prop="profitRatio" label="盈亏比例 (%)" width="120" align="right" />
              <el-table-column prop="positionRatio" label="仓位占比 (%)" width="120" align="right" />
              <el-table-column prop="pb" label="PB" width="80" align="right" />
              <el-table-column prop="pe" label="PE (TTM)" width="100" align="right" />
              <el-table-column prop="roe" label="ROE (加权)" width="120" align="right" />
              <el-table-column prop="avgPe" label="平均pe" width="90" align="right" />
              <el-table-column prop="eps" label="eps" width="90" align="right" />
              <el-table-column prop="eps3y" label="eps（3年）" width="110" align="right" />
              <el-table-column prop="g" label="g" width="80" align="right" />
              <el-table-column prop="peg" label="peg" width="80" align="right" />
              <el-table-column prop="expectedReturn" label="预估收益率" width="110" align="right" />

              <el-table-column label="操作" width="140" fixed="right" align="center">
                <template #default="scope">
                  <el-button link type="primary" @click="openEditDialog(scope.row)">修改</el-button>
                  <el-button link type="danger" @click="deleteRow(scope.row)">删除</el-button>
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
        </template>

        <template v-else-if="activeMenu === 'watchlist'">
          <WatchList :items="watchlistData" @remove-item="removeWatchlistItem" @clear-all="clearWatchlist" />
        </template>

        <template v-else>
          <div class="placeholder">
            <p>{{ menuTitleMap[activeMenu] }}模块待完善</p>
            <span>当前“持仓股票”模块已接入完整增删改查与刷新能力。</span>
          </div>
        </template>
      </main>
    </section>

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
  </div>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import Strategy from './components/Strategy.vue';
import WatchList from './components/WatchList.vue';

const PORTFOLIO_API = '/api/portfolio';
const WATCHLIST_API = '/api/watchlist';

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
const watchlistData = ref([]);
const activeMenu = ref('positions');
const sideMenus = [
  { key: 'positions', label: '持仓股票' },
  { key: 'strategy', label: '选股策略' },
  { key: 'rebalance', label: '待调仓股票' },
  { key: 'watchlist', label: '观察列表' },
  { key: 'reports', label: '分析报告' },
];
const menuTitleMap = {
  strategy: '选股策略',
  positions: '持仓股票',
  rebalance: '待调仓股票',
  watchlist: '观察列表',
  reports: '分析报告',
};

const addDialogVisible = ref(false);
const editDialogVisible = ref(false);
const editingRowId = ref(null);
const refreshingPrice = ref(false);
const refreshingFundamental = ref(false);
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
    await fetch(PORTFOLIO_API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(tableData.value),
    });
  } catch (error) {
    console.error('保存到文件失败:', error);
  }
}

async function saveWatchlistData() {
  try {
    await fetch(WATCHLIST_API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(watchlistData.value),
    });
  } catch (error) {
    console.error('保存观察列表失败:', error);
  }
}

async function loadWatchlistData() {
  try {
    const resp = await fetch(WATCHLIST_API, { method: 'GET' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    watchlistData.value = Array.isArray(data) ? data : [];
  } catch (error) {
    console.error('读取观察列表失败:', error);
    watchlistData.value = [];
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

async function fetchQuoteByCodes(rows) {
  const secids = Array.from(new Set(rows.map(row => parseCode(row.code)?.secid).filter(Boolean)));
  if (!secids.length) return new Map();

  const url = `https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&fields=f2,f3,f115,f12,f13,f14,f15,f16,f17,f18,f23,f100&secids=${encodeURIComponent(secids.join(','))}`;
  const resp = await fetch(url, { method: 'GET' });
  if (!resp.ok) throw new Error(`行情请求失败：HTTP ${resp.status}`);
  const json = await resp.json();
  const diff = json?.data?.diff;
  if (!Array.isArray(diff)) throw new Error('行情数据为空');

  const quoteMap = new Map();
  diff.forEach(item => {
    const code = String(item.f12 || '').trim();
    if (code) quoteMap.set(code, item);
  });
  return quoteMap;
}

async function fetchRoeByCode(code) {
  const parsed = parseCode(code);
  if (!parsed) return null;

  const marketPrefix = parsed.market === '1' ? 'SH' : 'SZ';
  const secCode = `${marketPrefix}${parsed.pureCode}`;
  const url = `/api/roe?code=${secCode}`;

  const resp = await fetch(url, { method: 'GET' });
  if (!resp.ok) return null;
  const json = await resp.json();
  const roeRaw = json?.roe;
  const roeNum = Number(roeRaw);
  if (!Number.isFinite(roeNum)) return null;
  return `${roeNum.toFixed(2)}%`;
}

async function refreshCurrentPrices() {
  if (!tableData.value.length) {
    ElMessage.warning('当前没有可刷新的股票');
    return;
  }

  refreshingPrice.value = true;
  try {
    const quoteMap = await fetchQuoteByCodes(tableData.value);
    let updatedCount = 0;

    tableData.value.forEach(row => {
      const pureCode = parseCode(row.code)?.pureCode;
      const quote = pureCode ? quoteMap.get(pureCode) : null;
      if (!quote) return;

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
    if (updatedCount > 0) ElMessage.success(`现价刷新完成：已更新 ${updatedCount} 只`);
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
    // 如果不再需要 PE，其实连 fetchQuoteByCodes 都可以优化掉，但为了保持结构简单，这里仅注释 PE 更新逻辑
    // const quoteMap = await fetchQuoteByCodes(tableData.value);
    let updatedCount = 0;
    for (const row of tableData.value) {
      // const pureCode = parseCode(row.code)?.pureCode;
      // const quote = pureCode ? quoteMap.get(pureCode) : null;
      // const pe = quote && Number.isFinite(Number(quote.f115)) ? Number(Number(quote.f115).toFixed(2)) : null;
      // if (pe !== null) row.pe = pe;

      const roe = await fetchRoeByCode(row.code);
      // if (roe !== null) row.roe = roe;
      // if (roe !== null || pe !== null) updatedCount += 1;
      if (roe !== null) {
        row.roe = roe;
        updatedCount += 1; // ✅ 只有当 ROE 成功更新时才计数
      }
    }

    refreshDerivedFields();
    if (updatedCount > 0) ElMessage.success(`PE/ROE 刷新完成：已更新 ${updatedCount} 只`);
    else ElMessage.warning('未获取到可更新的财报数据');
  } catch (error) {
    ElMessage.error(`刷新失败：${error.message || '请稍后重试'}`);
  } finally {
    refreshingFundamental.value = false;
  }
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
    const quoteMap = await fetchQuoteByCodes([{ code: parsed.pureCode }]);
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
    const latestRoe = await fetchRoeByCode(parsed.pureCode);
    if (latestRoe !== null) roe = latestRoe;

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

async function handleAddWatchlist(snapshot) {
  const nextItem = {
    ...snapshot,
    id: snapshot?.id || Date.now(),
  };
  watchlistData.value.unshift(nextItem);
  await saveWatchlistData();
  ElMessage.success('已加入观察列表');
}

async function removeWatchlistItem(id) {
  watchlistData.value = watchlistData.value.filter(item => item.id !== id);
  await saveWatchlistData();
  ElMessage.success('已移除该观察项');
}

async function clearWatchlist() {
  try {
    await ElMessageBox.confirm('确定清空观察列表吗？', '提示', {
      confirmButtonText: '清空',
      cancelButtonText: '取消',
      type: 'warning',
    });
    watchlistData.value = [];
    await saveWatchlistData();
    ElMessage.success('观察列表已清空');
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
    await loadWatchlistData();
    refreshDerivedFields(false);
  })();
});
</script>

<style scoped>
.app-shell {
  height: 100vh;
  display: grid;
  grid-template-columns: 220px 1fr;
  overflow: hidden;
  background: #f2f5f9;
}

.left-nav {
  position: sticky;
  top: 0;
  height: 100vh;
  background: linear-gradient(180deg, #20364f, #253f5d 70%, #2a4b6f);
  color: #d7e7fb;
  border-right: 1px solid rgba(255, 255, 255, 0.08);
  overflow-y: auto;
}

.brand {
  height: 56px;
  display: flex;
  align-items: center;
  padding: 0 18px;
  font-size: 1.05rem;
  font-weight: 700;
  color: #f2f8ff;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.menu-list {
  list-style: none;
  margin: 0;
  padding: 10px 0;
}

.menu-list li {
  margin: 4px 10px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.94rem;
  transition:
    background-color 0.2s ease,
    color 0.2s ease;
}

.menu-list li:hover {
  background: rgba(115, 172, 255, 0.14);
  color: #ffffff;
}

.menu-list li.active {
  background: linear-gradient(90deg, #409eff, #66b1ff);
  color: #fff;
  font-weight: 600;
}

.content-area {
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100vh;
  overflow: hidden;
}

.content-header {
  height: 56px;
  display: flex;
  align-items: center;
  padding: 0 18px;
  border-bottom: 1px solid #d9e2ec;
  background: #fff;
}

.content-header h2 {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 600;
  color: #2b3f55;
}

.content-body {
  padding: 16px;
  overflow-y: auto;
}

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

.placeholder {
  min-height: 320px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border: 1px dashed #bfd2e6;
  border-radius: 10px;
  color: #53708e;
  background: #fbfdff;
}

.placeholder p {
  margin: 0 0 10px;
  font-size: 1.08rem;
  font-weight: 600;
}

.placeholder span {
  font-size: 0.92rem;
}

@media (max-width: 960px) {
  .app-shell {
    grid-template-columns: 1fr;
  }

  .left-nav {
    border-right: 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  }

  .menu-list {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 6px;
    padding: 10px;
  }

  .menu-list li {
    margin: 0;
  }

  .allocation-grid {
    grid-template-columns: 1fr;
  }
}
</style>

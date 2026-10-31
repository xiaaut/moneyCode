<script setup>
import { computed, onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';

const API = '/api/closed-positions';
const loading = ref(false);
const saving = ref(false);
const records = ref([]);
const portfolio = ref([]);

const viewMode = ref('detail'); // detail | summary
const keyword = ref('');
const profitFilter = ref('all'); // all | profit | loss

const dialogVisible = ref(false);
const editingId = ref(null);
const form = ref(emptyForm());
const lookupLoading = ref(false);
const fromPortfolio = ref(false);
const portfolioCode = ref('');

function emptyForm() {
  const today = new Date().toISOString().slice(0, 10);
  return {
    code: '',
    name: '',
    quantity: 100,
    buyPrice: 0,
    sellPrice: 0,
    fee: 0,
    buyDate: '',
    sellDate: today,
    note: '',
  };
}

const stats = computed(() => {
  const list = records.value;
  const profits = list.map(row => Number(row.profitAmount) || 0);
  const total = profits.reduce((sum, value) => sum + value, 0);
  const win = profits.filter(value => value > 0).length;
  const lose = profits.filter(value => value < 0).length;
  const holdList = list.filter(row => row.holdDays != null && Number.isFinite(Number(row.holdDays)));
  const avgHold = holdList.length
    ? holdList.reduce((sum, row) => sum + Number(row.holdDays), 0) / holdList.length
    : 0;
  const best = profits.length ? Math.max(...profits) : 0;
  const worst = profits.length ? Math.min(...profits) : 0;
  return {
    total,
    count: list.length,
    win,
    lose,
    flat: list.length - win - lose,
    winRate: list.length ? (win / list.length) * 100 : 0,
    avg: list.length ? total / list.length : 0,
    avgHold,
    best,
    worst,
  };
});

const summaryRows = computed(() => {
  const map = new Map();
  for (const row of records.value) {
    const key = row.code;
    if (!map.has(key)) {
      map.set(key, {
        code: row.code,
        name: row.name,
        trades: 0,
        quantity: 0,
        cost: 0,
        profit: 0,
        win: 0,
        lastSellDate: '',
      });
    }
    const item = map.get(key);
    item.trades += 1;
    item.quantity += Number(row.quantity) || 0;
    item.cost += (Number(row.buyPrice) || 0) * (Number(row.quantity) || 0);
    item.profit += Number(row.profitAmount) || 0;
    if (Number(row.profitAmount) > 0) item.win += 1;
    if (row.sellDate && row.sellDate > item.lastSellDate) item.lastSellDate = row.sellDate;
    if (!item.name && row.name) item.name = row.name;
  }
  return Array.from(map.values())
    .map(item => ({
      ...item,
      profitRatio: item.cost > 0 ? (item.profit / item.cost) * 100 : 0,
      winRate: item.trades ? (item.win / item.trades) * 100 : 0,
    }))
    .sort((a, b) => b.profit - a.profit);
});

const filteredRecords = computed(() => {
  let list = records.value;
  if (profitFilter.value === 'profit') list = list.filter(row => Number(row.profitAmount) > 0);
  if (profitFilter.value === 'loss') list = list.filter(row => Number(row.profitAmount) < 0);

  const key = keyword.value.trim();
  if (key) {
    list = list.filter(row =>
      [row.code, row.name, row.note].filter(Boolean).some(text => String(text).includes(key)),
    );
  }
  return [...list].sort((a, b) => String(b.sellDate || '').localeCompare(String(a.sellDate || '')));
});

const filteredSummary = computed(() => {
  const key = keyword.value.trim();
  if (!key) return summaryRows.value;
  return summaryRows.value.filter(row =>
    [row.code, row.name].filter(Boolean).some(text => String(text).includes(key)),
  );
});

const preview = computed(() => {
  const quantity = Number(form.value.quantity) || 0;
  const buyPrice = Number(form.value.buyPrice) || 0;
  const sellPrice = Number(form.value.sellPrice) || 0;
  const fee = Number(form.value.fee) || 0;
  const profit = (sellPrice - buyPrice) * quantity - fee;
  const cost = buyPrice * quantity;
  const ratio = cost > 0 ? (profit / cost) * 100 : 0;
  const holdDays = calcHoldDays(form.value.buyDate, form.value.sellDate);
  return { profit, ratio, holdDays };
});

function calcHoldDays(buyDate, sellDate) {
  if (!buyDate || !sellDate) return null;
  const buy = new Date(buyDate);
  const sell = new Date(sellDate);
  if (Number.isNaN(buy.getTime()) || Number.isNaN(sell.getTime())) return null;
  return Math.max(0, Math.round((sell.getTime() - buy.getTime()) / 86400000));
}

function formatMoney(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '-';
  return `${num > 0 ? '+' : ''}${num.toFixed(2)}`;
}

function formatPercent(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '-';
  return `${num > 0 ? '+' : ''}${num.toFixed(2)}%`;
}

function formatPrice(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(3) : '-';
}

function profitClass(value) {
  const num = Number(value);
  if (!Number.isFinite(num) || num === 0) return 'flat';
  return num > 0 ? 'up' : 'down';
}

async function loadData() {
  loading.value = true;
  try {
    const [recordResp, portfolioResp] = await Promise.all([fetch(API), fetch('/api/portfolio')]);
    const recordData = recordResp.ok ? await recordResp.json() : [];
    const portfolioData = portfolioResp.ok ? await portfolioResp.json() : [];
    records.value = Array.isArray(recordData) ? recordData : [];
    portfolio.value = Array.isArray(portfolioData) ? portfolioData : [];
  } catch (error) {
    ElMessage.error(`读取清仓记录失败：${error.message || '请稍后重试'}`);
  } finally {
    loading.value = false;
  }
}

async function saveAll() {
  saving.value = true;
  try {
    const resp = await fetch(API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(records.value),
    });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(json?.message || `保存失败：HTTP ${resp.status}`);
  } catch (error) {
    ElMessage.error(error.message || '保存失败');
  } finally {
    saving.value = false;
  }
}

function openAddDialog() {
  editingId.value = null;
  form.value = emptyForm();
  fromPortfolio.value = false;
  portfolioCode.value = '';
  dialogVisible.value = true;
}

function openEditDialog(row) {
  editingId.value = row.id;
  form.value = {
    code: row.code,
    name: row.name || '',
    quantity: Number(row.quantity) || 0,
    buyPrice: Number(row.buyPrice) || 0,
    sellPrice: Number(row.sellPrice) || 0,
    fee: Number(row.fee) || 0,
    buyDate: row.buyDate || '',
    sellDate: row.sellDate || '',
    note: row.note || '',
  };
  fromPortfolio.value = false;
  portfolioCode.value = '';
  dialogVisible.value = true;
}

function fillFromPortfolio(code) {
  const row = portfolio.value.find(item => String(item.code) === String(code));
  if (!row) return;
  form.value.code = String(row.code);
  form.value.name = String(row.name || '');
  form.value.buyPrice = Number(row.costPrice) || 0;
  form.value.quantity = Number(row.quantity) || 0;
  form.value.sellPrice = Number(row.currentPrice) || 0;
  form.value.sellDate = new Date().toISOString().slice(0, 10);
  form.value.buyDate = '';
  form.value.note = '';
  fromPortfolio.value = true;
}

async function lookupName() {
  const code = String(form.value.code || '').trim();
  if (!/^\d{6}$/.test(code)) {
    ElMessage.warning('请输入 6 位股票代码');
    return;
  }
  lookupLoading.value = true;
  try {
    const resp = await fetch(`/api/stock-name?codes=${code}`);
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(json?.message || `HTTP ${resp.status}`);
    const info = json?.items?.[code];
    if (info?.name) form.value.name = info.name;
    if (!(Number(form.value.sellPrice) > 0) && Number(info?.price) > 0) form.value.sellPrice = info.price;
    ElMessage.success(info?.name ? `已获取：${info.name}` : '未查到名称，可手动填写');
  } catch (error) {
    ElMessage.error(`查询失败：${error.message || '请稍后重试'}`);
  } finally {
    lookupLoading.value = false;
  }
}

async function submitForm() {
  const code = String(form.value.code || '').trim();
  if (!/^\d{6}$/.test(code)) {
    ElMessage.warning('请输入正确的 6 位股票代码');
    return;
  }
  const quantity = Number(form.value.quantity) || 0;
  const buyPrice = Number(form.value.buyPrice) || 0;
  const sellPrice = Number(form.value.sellPrice) || 0;
  if (!(quantity > 0)) {
    ElMessage.warning('数量必须大于 0');
    return;
  }
  if (!(buyPrice >= 0) || !(sellPrice >= 0)) {
    ElMessage.warning('买入价 / 卖出价不能为负数');
    return;
  }

  const fee = Number(form.value.fee) || 0;
  const profitAmount = (sellPrice - buyPrice) * quantity - fee;
  const profitRatio = buyPrice * quantity > 0 ? (profitAmount / (buyPrice * quantity)) * 100 : 0;
  const record = {
    id: editingId.value ?? Date.now(),
    code,
    name: String(form.value.name || '').trim(),
    quantity,
    buyPrice,
    sellPrice,
    fee,
    buyDate: form.value.buyDate || '',
    sellDate: form.value.sellDate || '',
    profitAmount,
    profitRatio,
    holdDays: calcHoldDays(form.value.buyDate, form.value.sellDate),
    note: String(form.value.note || '').trim(),
    createdAt: new Date().toISOString(),
  };

  if (editingId.value != null) {
    records.value = records.value.map(item => (item.id === editingId.value ? { ...item, ...record } : item));
  } else {
    records.value = [record, ...records.value];
  }
  await saveAll();
  dialogVisible.value = false;
  ElMessage.success(editingId.value != null ? '已保存修改' : '已记入清仓记录');

  if (fromPortfolio.value && portfolio.value.some(item => String(item.code) === code)) {
    try {
      await ElMessageBox.confirm('是否同时从「持仓股票」中移除该股票？', '同步持仓', {
        confirmButtonText: '移除',
        cancelButtonText: '保留',
        type: 'warning',
      });
      const nextPortfolio = portfolio.value.filter(item => String(item.code) !== code);
      const resp = await fetch('/api/portfolio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(nextPortfolio),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      portfolio.value = nextPortfolio;
      ElMessage.success('已从持仓股票中移除');
    } catch {
      // 保留持仓
    }
  }
}

async function deleteRecord(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除 ${row.name || row.code} 的这笔清仓记录吗？`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    );
    records.value = records.value.filter(item => item.id !== row.id);
    await saveAll();
    ElMessage.success('已删除');
  } catch {
    // 取消
  }
}

onMounted(loadData);
</script>

<template>
  <div class="closed-page">
    <div class="page-head">
      <div>
        <h3>已清仓（已实现盈亏）</h3>
        <p class="meta">
          记录每笔清仓的买入价 / 卖出价 / 数量，自动计算已实现盈亏与持有天数；同一只股票可记多笔，汇总按股票合计。
        </p>
      </div>
      <div class="toolbar">
        <el-button type="primary" @click="openAddDialog">＋ 记一笔清仓</el-button>
      </div>
    </div>

    <div class="stats-row">
      <div class="stat-card highlight">
        <span>累计已实现盈亏</span>
        <strong :class="profitClass(stats.total)">{{ formatMoney(stats.total) }}</strong>
      </div>
      <div class="stat-card">
        <span>清仓笔数</span>
        <strong>{{ stats.count }}</strong>
      </div>
      <div class="stat-card">
        <span>胜率（盈利笔数）</span>
        <strong>{{ stats.winRate.toFixed(1) }}%</strong>
      </div>
      <div class="stat-card">
        <span>平均每笔盈亏</span>
        <strong :class="profitClass(stats.avg)">{{ formatMoney(stats.avg) }}</strong>
      </div>
      <div class="stat-card">
        <span>平均持有天数</span>
        <strong>{{ stats.avgHold ? stats.avgHold.toFixed(1) : '-' }}</strong>
      </div>
      <div class="stat-card">
        <span>最大盈利 / 最大亏损</span>
        <strong>
          <i class="up">{{ formatMoney(stats.best) }}</i> /
          <i class="down">{{ formatMoney(stats.worst) }}</i>
        </strong>
      </div>
    </div>

    <div class="filter-row">
      <el-radio-group v-model="viewMode">
        <el-radio-button value="detail">明细列表</el-radio-button>
        <el-radio-button value="summary">按股票汇总</el-radio-button>
      </el-radio-group>
      <el-select v-model="profitFilter" style="width: 130px">
        <el-option value="all" label="全部盈亏" />
        <el-option value="profit" label="只看盈利" />
        <el-option value="loss" label="只看亏损" />
      </el-select>
      <el-input v-model="keyword" placeholder="按代码 / 名称 / 备注搜索" clearable style="width: 240px" />
      <span class="result-count">共 {{ viewMode === 'detail' ? filteredRecords.length : filteredSummary.length }} 条</span>
    </div>

    <el-table
      v-if="viewMode === 'detail'"
      v-loading="loading"
      :data="filteredRecords"
      border
      size="small"
      style="width: 100%"
      max-height="600"
      empty-text="还没有清仓记录，点右上角「＋ 记一笔清仓」"
      :default-sort="{ prop: 'sellDate', order: 'descending' }"
    >
      <el-table-column type="index" label="#" width="50" align="center" />
      <el-table-column prop="code" label="股票代码" width="95" align="center" />
      <el-table-column prop="name" label="股票名称" width="110" align="center" />
      <el-table-column prop="quantity" label="数量" width="90" align="right" />
      <el-table-column label="买入价" width="95" align="right">
        <template #default="scope">{{ formatPrice(scope.row.buyPrice) }}</template>
      </el-table-column>
      <el-table-column label="卖出价" width="95" align="right">
        <template #default="scope">{{ formatPrice(scope.row.sellPrice) }}</template>
      </el-table-column>
      <el-table-column prop="buyDate" label="买入日期" width="110" align="center" />
      <el-table-column prop="sellDate" label="卖出日期" width="110" align="center" sortable />
      <el-table-column label="持有天数" width="95" align="center">
        <template #default="scope">{{ scope.row.holdDays == null ? '-' : `${scope.row.holdDays} 天` }}</template>
      </el-table-column>
      <el-table-column label="手续费" width="90" align="right">
        <template #default="scope">{{ formatPrice(scope.row.fee) }}</template>
      </el-table-column>
      <el-table-column label="已实现盈亏" width="120" align="right" sortable :sort-by="row => Number(row.profitAmount) || 0">
        <template #default="scope">
          <strong :class="profitClass(scope.row.profitAmount)">{{ formatMoney(scope.row.profitAmount) }}</strong>
        </template>
      </el-table-column>
      <el-table-column label="盈亏比例" width="105" align="right">
        <template #default="scope">
          <span :class="profitClass(scope.row.profitRatio)">{{ formatPercent(scope.row.profitRatio) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip />
      <el-table-column label="操作" width="110" align="center" fixed="right">
        <template #default="scope">
          <el-button link type="primary" @click="openEditDialog(scope.row)">修改</el-button>
          <el-button link type="danger" @click="deleteRecord(scope.row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-table
      v-else
      v-loading="loading"
      :data="filteredSummary"
      border
      size="small"
      style="width: 100%"
      max-height="600"
      empty-text="还没有清仓记录"
      :default-sort="{ prop: 'profit', order: 'descending' }"
    >
      <el-table-column type="index" label="#" width="55" align="center" />
      <el-table-column prop="code" label="股票代码" width="100" align="center" />
      <el-table-column prop="name" label="股票名称" width="130" align="center" />
      <el-table-column prop="trades" label="清仓次数" width="100" align="center" />
      <el-table-column prop="quantity" label="累计数量" width="110" align="right" />
      <el-table-column label="买入成本合计" width="140" align="right">
        <template #default="scope">{{ formatPrice(scope.row.cost) }}</template>
      </el-table-column>
      <el-table-column label="累计已实现盈亏" width="150" align="right" sortable :sort-by="row => Number(row.profit) || 0">
        <template #default="scope">
          <strong :class="profitClass(scope.row.profit)">{{ formatMoney(scope.row.profit) }}</strong>
        </template>
      </el-table-column>
      <el-table-column label="盈亏比例" width="110" align="right">
        <template #default="scope">
          <span :class="profitClass(scope.row.profitRatio)">{{ formatPercent(scope.row.profitRatio) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="胜率" width="100" align="center">
        <template #default="scope">{{ scope.row.winRate.toFixed(0) }}%</template>
      </el-table-column>
      <el-table-column prop="lastSellDate" label="最近清仓日期" width="130" align="center" />
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editingId != null ? '修改清仓记录' : '记一笔清仓'" width="620px">
      <el-form :model="form" label-width="100px">
        <el-form-item v-if="portfolio.length" label="从持仓带入">
          <el-select
            v-model="portfolioCode"
            placeholder="选择持仓股票，自动填入成本价 / 数量 / 现价"
            clearable
            style="width: 100%"
            @change="fillFromPortfolio"
          >
            <el-option
              v-for="item in portfolio"
              :key="`${item.id}-${item.code}`"
              :label="`${item.name}（${item.code}） 成本 ${item.costPrice} × ${item.quantity}`"
              :value="String(item.code)"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="股票代码">
          <div class="code-row">
            <el-input v-model.trim="form.code" placeholder="6 位代码，如 600519" maxlength="6" />
            <el-button :loading="lookupLoading" @click="lookupName">查询名称 / 现价</el-button>
          </div>
        </el-form-item>
        <el-form-item label="股票名称">
          <el-input v-model.trim="form.name" placeholder="可自动获取，也可手动填写" />
        </el-form-item>
        <el-form-item label="数量">
          <el-input-number v-model="form.quantity" :min="1" :step="100" style="width: 200px" />
          <span class="form-hint">股</span>
        </el-form-item>
        <el-form-item label="买入价">
          <el-input-number v-model="form.buyPrice" :min="0" :step="0.001" :precision="3" style="width: 200px" />
          <span class="form-hint">元</span>
        </el-form-item>
        <el-form-item label="卖出价">
          <el-input-number v-model="form.sellPrice" :min="0" :step="0.001" :precision="3" style="width: 200px" />
          <span class="form-hint">元</span>
        </el-form-item>
        <el-form-item label="手续费">
          <el-input-number v-model="form.fee" :min="0" :step="1" :precision="2" style="width: 200px" />
          <span class="form-hint">元（可选，默认 0）</span>
        </el-form-item>
        <el-form-item label="买入日期">
          <el-date-picker v-model="form.buyDate" type="date" value-format="YYYY-MM-DD" placeholder="可选" />
        </el-form-item>
        <el-form-item label="卖出日期">
          <el-date-picker v-model="form.sellDate" type="date" value-format="YYYY-MM-DD" placeholder="可选" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model.trim="form.note" placeholder="可选，例如：短线打板 / 止损" />
        </el-form-item>
        <el-form-item label="预计盈亏">
          <div class="preview-box">
            <strong :class="profitClass(preview.profit)">{{ formatMoney(preview.profit) }} 元</strong>
            <span :class="profitClass(preview.ratio)">{{ formatPercent(preview.ratio) }}</span>
            <span v-if="preview.holdDays != null" class="form-hint">持有 {{ preview.holdDays }} 天</span>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.closed-page {
  background: #fff;
  border: 1px solid #d9e2ec;
  border-radius: 10px;
  padding: 14px;
}

.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.page-head h3 {
  margin: 0 0 4px;
  color: #2b3f55;
}

.meta {
  margin: 0;
  color: #6b7c93;
  font-size: 0.85rem;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.stat-card {
  border: 1px solid #dbe6f2;
  border-radius: 8px;
  padding: 10px 12px;
  background: linear-gradient(180deg, #fbfdff, #f5f9fe);
}

.stat-card.highlight {
  border-color: #f0c27b;
  background: linear-gradient(180deg, #fffaf0, #fff3dc);
}

.stat-card span {
  display: block;
  color: #5a7188;
  font-size: 0.82rem;
  margin-bottom: 4px;
}

.stat-card strong {
  color: #22476d;
  font-size: 1.15rem;
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.result-count {
  color: #6b7c93;
  font-size: 0.85rem;
}

.up {
  color: #e34d4d;
}

.down {
  color: #1aa261;
}

.flat {
  color: #5a7188;
}

.code-row {
  display: flex;
  gap: 8px;
  width: 100%;
}

.form-hint {
  margin-left: 8px;
  color: #6b7c93;
  font-size: 0.85rem;
}

.preview-box {
  display: flex;
  align-items: center;
  gap: 12px;
}

.preview-box strong {
  font-size: 1.05rem;
}
</style>

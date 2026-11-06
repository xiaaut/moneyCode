<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';

const emit = defineEmits(['add-watchlist']);

const loading = ref(false);
const rows = ref([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(50);
const industries = ref([]);
const summary = ref({
  total: 0,
  upCount: 0,
  downCount: 0,
  flatCount: 0,
  stateOwnedCount: 0,
  msciCount: 0,
  lastSyncAt: null,
});

const filters = ref({
  keyword: '',
  industry: '',
  stateOwned: false,
  inMsci: false,
  peMin: null,
  peMax: null,
  roeMin: null,
  mvMin: null,
});

const sortBy = ref('');
const sortOrder = ref('');

const job = ref({ running: false, phase: 'idle', label: '', done: 0, total: 0, message: '', error: '' });
const polling = ref(false);
let pollTimer = null;

// 后端字段 -> 表格排序字段映射
const SORT_MAP = {
  changePct: 'change_pct',
  marketCapYi: 'market_cap_yi',
  peTtm: 'pe_ttm',
  pb: 'pb',
  roe: 'roe',
  epsCurrent: 'eps_current',
  revenueYoy: 'revenue_yoy',
  netProfitYoy: 'net_profit_yoy',
  price: 'price',
};

const progressPercent = computed(() => {
  const j = job.value;
  if (!j.running) return j.phase === 'done' ? 100 : 0;
  if (j.phase === 'spot') return 15;
  if (j.phase === 'financial') return j.total ? 15 + Math.round((j.done / j.total) * 45) : 30;
  if (j.phase === 'save') return 90;
  return 5;
});

function buildQuery() {
  const params = new URLSearchParams();
  params.set('page', String(page.value));
  params.set('page_size', String(pageSize.value));
  const f = filters.value;
  if (f.keyword.trim()) params.set('keyword', f.keyword.trim());
  if (f.industry) params.set('industry', f.industry);
  if (f.stateOwned) params.set('state_owned', 'true');
  if (f.inMsci) params.set('in_msci', 'true');
  if (num(f.peMin) !== null) params.set('pe_min', String(num(f.peMin)));
  if (num(f.peMax) !== null) params.set('pe_max', String(num(f.peMax)));
  if (num(f.roeMin) !== null) params.set('roe_min', String(num(f.roeMin)));
  if (num(f.mvMin) !== null) params.set('mv_min', String(num(f.mvMin)));
  if (sortBy.value) params.set('sort_by', sortBy.value);
  if (sortOrder.value) params.set('sort_order', sortOrder.value);
  return params;
}

function num(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

async function loadData() {
  loading.value = true;
  try {
    const resp = await fetch(`/api/stocks?${buildQuery().toString()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    rows.value = Array.isArray(data?.items) ? data.items : [];
    total.value = Number(data?.total) || 0;
    industries.value = Array.isArray(data?.industries) ? data.industries : [];
    if (data?.summary) summary.value = data.summary;
  } catch (error) {
    ElMessage.error(`读取全市场数据失败：${error.message || '请稍后重试'}`);
  } finally {
    loading.value = false;
  }
}

function onSearch() {
  page.value = 1;
  loadData();
}

function onSortChange({ prop, order }) {
  sortBy.value = order ? SORT_MAP[prop] || '' : '';
  sortOrder.value = order === 'ascending' ? 'asc' : order === 'descending' ? 'desc' : '';
  page.value = 1;
  loadData();
}

function onPageSizeChange() {
  page.value = 1;
  loadData();
}

async function startSync() {
  if (job.value.running) return;
  try {
    const resp = await fetch('/api/stocks/sync-akshare', { method: 'POST' });
    const json = await resp.json().catch(() => ({}));
    if (resp.status === 409) {
      ElMessage.warning(json?.message || '同步任务正在进行中');
      if (json?.job) job.value = json.job;
      startPolling();
      return;
    }
    if (!resp.ok) throw new Error(json?.message || `HTTP ${resp.status}`);
    if (json?.job) job.value = json.job;
    job.value.running = true;
    ElMessage.success('AKShare 全市场同步已启动，约 1~2 分钟');
    startPolling();
  } catch (error) {
    ElMessage.error(`启动同步失败：${error.message || '请稍后重试'}`);
  }
}

async function loadProgress() {
  try {
    const resp = await fetch('/api/stocks/sync-progress');
    if (!resp.ok) return;
    const data = await resp.json();
    if (data?.job) job.value = data.job;
  } catch {
    // 忽略轮询错误
  }
}

function startPolling() {
  if (polling.value) return;
  polling.value = true;
  pollTimer = setInterval(async () => {
    await loadProgress();
    if (!job.value.running) {
      stopPolling();
      await loadData();
      if (job.value.phase === 'error') ElMessage.error(job.value.error || '同步失败');
      else ElMessage.success(job.value.message || '同步完成');
    }
  }, 1500);
}

function stopPolling() {
  polling.value = false;
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function addToWatchlist(row) {
  emit('add-watchlist', {
    id: Date.now(),
    stockCode: row.code,
    stockName: row.name,
    createdAt: new Date().toISOString(),
    currentEps: Number(row.epsCurrent) || null,
    currentPe: Number(row.peTtm) || null,
  });
  ElMessage.success(`已加入观察列表：${row.name}（${row.code}）`);
}

function resetFilters() {
  filters.value = {
    keyword: '',
    industry: '',
    stateOwned: false,
    inMsci: false,
    peMin: null,
    peMax: null,
    roeMin: null,
    mvMin: null,
  };
  sortBy.value = '';
  sortOrder.value = '';
  onSearch();
}

/* ---------- 格式化 ---------- */
function formatDateTime(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '-';
  return d.toLocaleString('zh-CN', { hour12: false });
}

function formatPrice(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n.toFixed(2) : '-';
}

function formatSigned(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '-';
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`;
}

function changeClass(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return 'flat';
  return n > 0 ? 'up' : 'down';
}

function formatMarketCap(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return '-';
  if (n >= 10000) return `${(n / 10000).toFixed(2)} 万亿`;
  return `${n.toFixed(2)} 亿`;
}

function formatNum(value, digits = 2) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : '-';
}

function peText(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '-';
  if (n <= 0) return '亏损';
  return n.toFixed(2);
}

function controllerText(row) {
  return row.controller || '-';
}

onMounted(async () => {
  await loadData();
  // 若后台已有同步任务在跑，恢复进度轮询
  await loadProgress();
  if (job.value.running) startPolling();
});
onBeforeUnmount(stopPolling);
</script>

<template>
  <div class="market-page">
    <div class="page-head">
      <div>
        <h3>A股全市场</h3>
        <p class="meta">
          数据源：AKShare（东方财富）· 收录 {{ summary.total }} 只 A 股 ·
          最近同步：{{ formatDateTime(summary.lastSyncAt) }}
        </p>
      </div>
      <div class="toolbar">
        <el-button type="primary" :loading="job.running" @click="startSync">
          {{ job.running ? '同步中…' : 'AKShare 一键同步全市场' }}
        </el-button>
      </div>
    </div>

    <div v-if="job.running" class="progress-box">
      <el-progress :percentage="progressPercent" :stroke-width="12" striped striped-flow />
      <p class="progress-text">
        <strong>{{ job.label }}</strong>
        <span v-if="job.message"> · {{ job.message }}</span>
        <span v-if="job.phase === 'financial' && job.total">（{{ job.done }} / {{ job.total }}）</span>
      </p>
    </div>

    <el-alert
      v-if="job.phase === 'error'"
      type="error"
      :closable="false"
      show-icon
      :title="`上次同步失败：${job.error}`"
      class="notice"
    />

    <div class="stats-row">
      <div class="stat-card highlight">
        <span>全市场收录</span>
        <strong>{{ summary.total }}</strong>
      </div>
      <div class="stat-card up-card">
        <span>上涨家数</span>
        <strong>{{ summary.upCount }}</strong>
      </div>
      <div class="stat-card down-card">
        <span>下跌家数</span>
        <strong>{{ summary.downCount }}</strong>
      </div>
      <div class="stat-card">
        <span>平盘家数</span>
        <strong>{{ summary.flatCount }}</strong>
      </div>
      <div class="stat-card">
        <span>国企 / 国资控股</span>
        <strong>{{ summary.stateOwnedCount }}</strong>
      </div>
      <div class="stat-card">
        <span>MSCI 成份</span>
        <strong>{{ summary.msciCount }}</strong>
      </div>
      <div class="stat-card">
        <span>最近更新时间</span>
        <strong class="small">{{ formatDateTime(summary.lastSyncAt) }}</strong>
      </div>
    </div>

    <div class="filter-row">
      <el-input
        v-model="filters.keyword"
        placeholder="代码 / 名称搜索"
        clearable
        style="width: 200px"
        @keyup.enter="onSearch"
        @clear="onSearch"
      />
      <el-select v-model="filters.industry" placeholder="全部行业" clearable filterable style="width: 190px" @change="onSearch">
        <el-option v-for="item in industries" :key="item" :label="item" :value="item" />
      </el-select>
      <el-checkbox v-model="filters.stateOwned" @change="onSearch">仅国资控股</el-checkbox>
      <el-checkbox v-model="filters.inMsci" @change="onSearch">仅 MSCI</el-checkbox>
      <div class="range-group">
        <span class="range-label">PE</span>
        <el-input v-model="filters.peMin" placeholder="最小" type="number" style="width: 78px" @keyup.enter="onSearch" />
        <span class="range-sep">~</span>
        <el-input v-model="filters.peMax" placeholder="最大" type="number" style="width: 78px" @keyup.enter="onSearch" />
      </div>
      <div class="range-group">
        <span class="range-label">ROE≥</span>
        <el-input v-model="filters.roeMin" placeholder="%" type="number" style="width: 74px" @keyup.enter="onSearch" />
      </div>
      <div class="range-group">
        <span class="range-label">市值≥</span>
        <el-input v-model="filters.mvMin" placeholder="亿" type="number" style="width: 74px" @keyup.enter="onSearch" />
      </div>
      <el-button type="primary" plain @click="onSearch">查询</el-button>
      <el-button text @click="resetFilters">重置</el-button>
      <span class="result-count">共 {{ total }} 只</span>
    </div>

    <el-table
      v-loading="loading"
      :data="rows"
      border
      size="small"
      style="width: 100%"
      max-height="600"
      @sort-change="onSortChange"
      empty-text="暂无数据，点击右上角「AKShare 一键同步全市场」拉取"
    >
      <el-table-column prop="code" label="代码" width="90" align="center" />
      <el-table-column prop="name" label="名称" width="110" align="center" fixed show-overflow-tooltip>
        <template #default="scope">
          <span class="stock-name">{{ scope.row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column label="最新价" width="86" align="right" sortable="custom" prop="price">
        <template #default="scope">{{ formatPrice(scope.row.price) }}</template>
      </el-table-column>
      <el-table-column label="涨跌幅" width="92" align="right" sortable="custom" prop="changePct">
        <template #default="scope">
          <span :class="changeClass(scope.row.changePct)">{{ formatSigned(scope.row.changePct) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="总市值" width="108" align="right" sortable="custom" prop="marketCapYi">
        <template #default="scope">{{ formatMarketCap(scope.row.marketCapYi) }}</template>
      </el-table-column>
      <el-table-column label="PE(TTM)" width="92" align="right" sortable="custom" prop="peTtm">
        <template #default="scope">{{ peText(scope.row.peTtm) }}</template>
      </el-table-column>
      <el-table-column label="PB" width="80" align="right" sortable="custom" prop="pb">
        <template #default="scope">{{ formatNum(scope.row.pb) }}</template>
      </el-table-column>
      <el-table-column label="ROE(%)" width="92" align="right" sortable="custom" prop="roe">
        <template #default="scope">{{ formatNum(scope.row.roe) }}</template>
      </el-table-column>
      <el-table-column label="EPS" width="82" align="right" sortable="custom" prop="epsCurrent">
        <template #default="scope">{{ formatNum(scope.row.epsCurrent) }}</template>
      </el-table-column>
      <el-table-column label="营收同比" width="96" align="right" sortable="custom" prop="revenueYoy">
        <template #default="scope">
          <span :class="changeClass(scope.row.revenueYoy)">{{ formatSigned(scope.row.revenueYoy) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="净利同比" width="96" align="right" sortable="custom" prop="netProfitYoy">
        <template #default="scope">
          <span :class="changeClass(scope.row.netProfitYoy)">{{ formatSigned(scope.row.netProfitYoy) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="industry" label="行业" width="120" align="center" show-overflow-tooltip>
        <template #default="scope">{{ scope.row.industry || '-' }}</template>
      </el-table-column>
      <el-table-column label="标签" width="128" align="center">
        <template #default="scope">
          <div class="tag-cell">
            <el-tag v-if="scope.row.stateOwned" type="warning" size="small">国资</el-tag>
            <el-tag v-if="scope.row.inMsci" type="danger" size="small">MSCI</el-tag>
            <span v-if="!scope.row.stateOwned && !scope.row.inMsci">-</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="实际控制人" min-width="140" show-overflow-tooltip>
        <template #default="scope">{{ controllerText(scope.row) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="118" align="center" fixed="right">
        <template #default="scope">
          <el-button size="small" type="primary" plain @click="addToWatchlist(scope.row)">加观察</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager-row">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[20, 50, 100, 200]"
        :total="total"
        layout="total, sizes, prev, pager, next, jumper"
        background
        @current-change="loadData"
        @size-change="onPageSizeChange"
      />
    </div>
  </div>
</template>

<style scoped>
.market-page {
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

.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.notice {
  margin-bottom: 12px;
}

.progress-box {
  border: 1px solid #cfe3f7;
  background: #f5faff;
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 12px;
}

.progress-text {
  margin: 8px 0 0;
  color: #2b3f55;
  font-size: 0.86rem;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
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
  border-color: #a7c7ec;
  background: linear-gradient(180deg, #f0f7ff, #e3f0ff);
}

.stat-card.up-card strong {
  color: #e34d4d;
}

.stat-card.down-card strong {
  color: #1aa261;
}

.stat-card span {
  display: block;
  color: #5a7188;
  font-size: 0.8rem;
  margin-bottom: 4px;
}

.stat-card strong {
  color: #22476d;
  font-size: 1.15rem;
}

.stat-card strong.small {
  font-size: 0.9rem;
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.range-group {
  display: flex;
  align-items: center;
  gap: 4px;
}

.range-label {
  color: #5a7188;
  font-size: 0.82rem;
  white-space: nowrap;
}

.range-sep {
  color: #9aa9ba;
}

.result-count {
  color: #6b7c93;
  font-size: 0.85rem;
  margin-left: auto;
}

.stock-name {
  font-weight: 600;
  color: #21384f;
}

.tag-cell {
  display: flex;
  gap: 4px;
  justify-content: center;
  align-items: center;
}

.up {
  color: #e34d4d;
  font-weight: 600;
}

.down {
  color: #1aa261;
  font-weight: 600;
}

.flat {
  color: #5a7188;
}

.pager-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>

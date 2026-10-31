<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';

const loading = ref(false);
const saved = ref({ updatedAt: null, total: 0, counts: {}, sourceStats: {}, rows: [] });
const job = ref({ running: false, phase: 'idle', label: '', done: 0, total: 0, message: '', error: '' });
const forceRefresh = ref(false);
const polling = ref(false);
let pollTimer = null;

const categoryFilter = ref('state');
const boardFilter = ref('all');
const keyword = ref('');
const page = ref(1);
const pageSize = ref(100);

const STATE_KEYS = ['sasac-central', 'sasac-local', 'government', 'state-finance'];

const CATEGORY_OPTIONS = [
  { value: 'state', label: '国资 / 政府控股（全部）' },
  { value: 'sasac-central', label: '国务院国资委' },
  { value: 'sasac-local', label: '地方国资委' },
  { value: 'government', label: '政府 / 财政 / 事业单位' },
  { value: 'state-finance', label: '国有资本运营主体' },
  { value: 'state-group', label: '国有法人（待确认）' },
  { value: 'collective', label: '集体所有制' },
  { value: 'person', label: '非国资（自然人）' },
  { value: 'none', label: '无实际控制人' },
  { value: 'unknown', label: '未获取' },
  { value: 'all', label: '全部股票' },
];

const BOARD_OPTIONS = [
  { value: 'all', label: '全部板块' },
  { value: '沪主板', label: '沪主板' },
  { value: '深主板', label: '深主板' },
  { value: '北交所', label: '北交所' },
];

const counts = computed(() => saved.value.counts || {});
const stateTotal = computed(() => STATE_KEYS.reduce((sum, key) => sum + (counts.value[key] || 0), 0));

const progressPercent = computed(() => {
  const current = job.value;
  if (!current.running) return 0;
  if (current.phase === 'universe') return current.total ? Math.round((current.done / current.total) * 15) : 3;
  if (current.phase === 'quotes') return 20;
  if (current.phase === 'controllers') {
    return current.total ? 45 + Math.round((current.done / current.total) * 50) : 50;
  }
  if (current.phase === 'save') return 98;
  return 5;
});

const filteredRows = computed(() => {
  let rows = saved.value.rows || [];
  if (categoryFilter.value === 'state') rows = rows.filter(row => STATE_KEYS.includes(row.category));
  else if (categoryFilter.value !== 'all') rows = rows.filter(row => row.category === categoryFilter.value);

  if (boardFilter.value !== 'all') rows = rows.filter(row => row.board === boardFilter.value);

  const key = keyword.value.trim();
  if (key) {
    rows = rows.filter(row =>
      [row.code, row.name, row.finalController, row.actualHolder, row.controllingHolder]
        .filter(Boolean)
        .some(text => String(text).includes(key)),
    );
  }
  return rows;
});

const pagedRows = computed(() => {
  const start = (page.value - 1) * pageSize.value;
  return filteredRows.value.slice(start, start + pageSize.value);
});

watch([categoryFilter, boardFilter, keyword, pageSize], () => {
  page.value = 1;
});

function formatDateTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString('zh-CN', { hour12: false });
}

function tagType(category) {
  if (category === 'sasac-central') return 'danger';
  if (category === 'sasac-local') return 'warning';
  if (category === 'government') return 'warning';
  if (category === 'state-finance') return 'info';
  if (category === 'state-group') return 'info';
  if (category === 'person') return 'success';
  return 'info';
}

async function loadSaved() {
  loading.value = true;
  try {
    const resp = await fetch('/api/state-owned');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    saved.value = {
      updatedAt: data?.updatedAt || null,
      total: Number(data?.total) || 0,
      counts: data?.counts && typeof data.counts === 'object' ? data.counts : {},
      sourceStats: data?.sourceStats && typeof data.sourceStats === 'object' ? data.sourceStats : {},
      rows: Array.isArray(data?.rows) ? data.rows : [],
    };
    if (data?.job?.running) {
      job.value = data.job;
      startPolling();
    } else if (data?.job) {
      job.value = data.job;
    }
  } catch (error) {
    ElMessage.error(`读取国资数据失败：${error.message || '请稍后重试'}`);
  } finally {
    loading.value = false;
  }
}

async function loadProgress() {
  try {
    const resp = await fetch('/api/state-owned/progress');
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
      await loadSaved();
      if (job.value.phase === 'error') ElMessage.error(job.value.error || '刷新失败');
      else ElMessage.success(job.value.message || '数据已更新');
    }
  }, 2000);
}

function stopPolling() {
  polling.value = false;
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function startUpdate() {
  if (job.value.running) return;
  try {
    const resp = await fetch('/api/state-owned/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force: forceRefresh.value }),
    });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(json?.message || `HTTP ${resp.status}`);
    if (json?.job) job.value = json.job;
    job.value.running = true;
    ElMessage.success('刷新任务已启动，首次约 5~8 分钟（之后走缓存 1~2 分钟）');
    startPolling();
  } catch (error) {
    ElMessage.error(`启动失败：${error.message || '请稍后重试'}`);
  }
}

onMounted(loadSaved);
onBeforeUnmount(stopPolling);
</script>

<template>
  <div class="state-page">
    <div class="page-head">
      <div>
        <h3>国资 / 政府控股（全市场）</h3>
        <p class="meta">
          <template v-if="saved.updatedAt">
            数据更新时间：{{ formatDateTime(saved.updatedAt) }} · 覆盖 {{ saved.total }} 只沪深主板 + 北交所股票
            <span v-if="saved.sourceStats?.orgInfoRows"> · 原始公司记录 {{ saved.sourceStats.orgInfoRows }} 条</span>
          </template>
          <template v-else>尚未生成数据，点击右侧「更新数据」开始全市场扫描</template>
        </p>
      </div>
      <div class="toolbar">
        <el-checkbox v-model="forceRefresh" :disabled="job.running">强制全量刷新（忽略缓存）</el-checkbox>
        <el-button type="primary" :loading="job.running" @click="startUpdate">
          {{ job.running ? '扫描中…' : '更新数据' }}
        </el-button>
      </div>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="扫描范围：沪深主板 + 北交所，已排除创业板、科创板、ST / 退市股"
      description="数据源：东方财富数据中心「公司概况」全量实际控制人（约 50 页/20 秒）+ 东方财富行情（校验上市状态）+ 对「国有法人」用同花顺 F10 穿透最终控制人。首次约 5~8 分钟，之后更新走 30 天缓存约 1~2 分钟；勾选「强制全量刷新」会重新穿透。"
      class="notice"
    />

    <div v-if="job.running" class="progress-box">
      <el-progress :percentage="progressPercent" :stroke-width="12" striped striped-flow />
      <p class="progress-text">
        <strong>{{ job.label }}</strong>
        <span v-if="job.message"> · {{ job.message }}</span>
        <span v-if="job.phase === 'controllers' && job.total">
          （{{ job.done }} / {{ job.total }}，每只约 0.9 秒）
        </span>
      </p>
    </div>

    <el-alert
      v-if="job.phase === 'error'"
      type="error"
      :closable="false"
      show-icon
      :title="`上次刷新失败：${job.error}`"
      class="notice"
    />

    <div class="stats-row">
      <div class="stat-card highlight">
        <span>国资 / 政府控股</span>
        <strong>{{ stateTotal }}</strong>
      </div>
      <div class="stat-card">
        <span>国务院国资委</span>
        <strong>{{ counts['sasac-central'] || 0 }}</strong>
      </div>
      <div class="stat-card">
        <span>地方国资委</span>
        <strong>{{ counts['sasac-local'] || 0 }}</strong>
      </div>
      <div class="stat-card">
        <span>政府 / 财政 / 事业单位</span>
        <strong>{{ counts.government || 0 }}</strong>
      </div>
      <div class="stat-card">
        <span>国有资本运营主体</span>
        <strong>{{ counts['state-finance'] || 0 }}</strong>
      </div>
      <div class="stat-card">
        <span>国有法人（待确认）</span>
        <strong>{{ counts['state-group'] || 0 }}</strong>
      </div>
      <div class="stat-card">
        <span>非国资 / 无实控人</span>
        <strong>{{ (counts.person || 0) + (counts.none || 0) }}</strong>
      </div>
      <div class="stat-card">
        <span>全市场股票数</span>
        <strong>{{ saved.total }}</strong>
      </div>
    </div>

    <div class="filter-row">
      <el-select v-model="categoryFilter" style="width: 230px">
        <el-option v-for="option in CATEGORY_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
      </el-select>
      <el-select v-model="boardFilter" style="width: 130px">
        <el-option v-for="option in BOARD_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
      </el-select>
      <el-input v-model="keyword" placeholder="按代码 / 名称 / 控制人搜索" clearable style="width: 260px" />
      <span class="result-count">共 {{ filteredRows.length }} 条</span>
    </div>

    <el-table
      v-loading="loading"
      :data="pagedRows"
      border
      size="small"
      style="width: 100%"
      empty-text="还没有数据，点击右上角「更新数据」开始扫描全市场"
    >
      <el-table-column prop="code" label="股票代码" width="95" align="center" />
      <el-table-column prop="name" label="股票名称" width="110" align="center" />
      <el-table-column prop="board" label="板块" width="85" align="center" />
      <el-table-column prop="categoryLabel" label="判定" width="165" align="center">
        <template #default="scope">
          <el-tag :type="tagType(scope.row.category)" size="small">{{ scope.row.categoryLabel }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="最终控制人（穿透）" min-width="220" show-overflow-tooltip>
        <template #default="scope">{{ scope.row.finalController || '-' }}</template>
      </el-table-column>
      <el-table-column label="实际控制人（东财原始）" min-width="200" show-overflow-tooltip>
        <template #default="scope">{{ scope.row.actualHolder || scope.row.controllingHolder || '-' }}</template>
      </el-table-column>
      <el-table-column label="来源" width="150" align="center">
        <template #default="scope">
          <a v-if="scope.row.sourceUrl" :href="scope.row.sourceUrl" target="_blank" rel="noreferrer">
            {{ scope.row.source }}
          </a>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="note" label="备注" min-width="180" show-overflow-tooltip />
    </el-table>

    <div class="pager-row">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[50, 100, 200, 500]"
        :total="filteredRows.length"
        layout="total, sizes, prev, pager, next"
        background
      />
    </div>
  </div>
</template>

<style scoped>
.state-page {
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
  gap: 12px;
  flex-wrap: wrap;
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
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
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

.pager-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>

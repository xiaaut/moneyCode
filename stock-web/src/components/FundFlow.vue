<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import FundFlowDrawer from './FundFlowDrawer.vue';

const activeTab = ref('rank');

// 抢筹榜
const indicator = ref('今日');
const rankRows = ref([]);
const rankLoading = ref(false);

// 龙虎榜
const instOnly = ref(false);
const lhbRows = ref([]);
const lhbLoading = ref(false);
const lhbJob = ref({ running: false, phase: 'idle', label: '', done: 0, total: 0, message: '', error: '' });
const polling = ref(false);
let pollTimer = null;

// 抽屉
const drawerVisible = ref(false);
const drawerCode = ref('');
const drawerName = ref('');

function formatAmount(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '-';
  const abs = Math.abs(v);
  const sign = v > 0 ? '+' : v < 0 ? '-' : '';
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(1)}万`;
  return `${sign}${abs.toFixed(0)}`;
}
function formatPct(n) {
  const v = Number(n);
  return Number.isFinite(v) ? `${v > 0 ? '+' : ''}${v.toFixed(2)}%` : '-';
}
function pctClass(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v === 0) return 'flat';
  return v > 0 ? 'up' : 'down';
}

async function loadRank() {
  rankLoading.value = true;
  try {
    const resp = await fetch(`/api/fund-flow/market-rank?indicator=${encodeURIComponent(indicator.value)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    rankRows.value = Array.isArray(data?.rows) ? data.rows : [];
  } catch (error) {
    ElMessage.error(`读取排行失败：${error.message || '请稍后重试'}`);
  } finally {
    rankLoading.value = false;
  }
}

async function loadLhb() {
  lhbLoading.value = true;
  try {
    const resp = await fetch(`/api/lhb/daily?inst_only=${instOnly.value}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    lhbRows.value = Array.isArray(data?.rows) ? data.rows : [];
  } catch (error) {
    ElMessage.error(`读取龙虎榜失败：${error.message || '请稍后重试'}`);
  } finally {
    lhbLoading.value = false;
  }
}

async function startLhbSync() {
  if (lhbJob.value.running) return;
  try {
    const resp = await fetch('/api/lhb/sync', { method: 'POST' });
    const json = await resp.json().catch(() => ({}));
    if (resp.status === 409) {
      ElMessage.warning(json?.message || '龙虎榜同步进行中');
      if (json?.job) lhbJob.value = json.job;
      startPolling();
      return;
    }
    if (!resp.ok) throw new Error(json?.message || `HTTP ${resp.status}`);
    if (json?.job) lhbJob.value = json.job;
    lhbJob.value.running = true;
    ElMessage.success('龙虎榜同步已启动');
    startPolling();
  } catch (error) {
    ElMessage.error(`启动同步失败：${error.message || '请稍后重试'}`);
  }
}

async function loadLhbProgress() {
  try {
    const resp = await fetch('/api/lhb/progress');
    if (!resp.ok) return;
    const data = await resp.json();
    if (data?.job) lhbJob.value = data.job;
  } catch {
    // 忽略
  }
}

function startPolling() {
  if (polling.value) return;
  polling.value = true;
  pollTimer = setInterval(async () => {
    await loadLhbProgress();
    if (!lhbJob.value.running) {
      stopPolling();
      await loadLhb();
      if (lhbJob.value.phase === 'error') ElMessage.error(lhbJob.value.error || '同步失败');
      else ElMessage.success(lhbJob.value.message || '同步完成');
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

function openDrawer(row) {
  drawerCode.value = row.code;
  drawerName.value = row.name || row.stockName || '';
  drawerVisible.value = true;
}

function onIndicatorChange() {
  loadRank();
}

onMounted(() => {
  loadRank();
  loadLhb();
});
onBeforeUnmount(stopPolling);
</script>

<template>
  <div class="ff-page">
    <div class="page-head">
      <div>
        <h3>资金与异动</h3>
        <p class="meta">全市场主力资金排行与每日龙虎榜 · 点击任意行查看个股资金与席位详情</p>
      </div>
      <div class="toolbar">
        <el-button type="primary" :loading="lhbJob.running" @click="startLhbSync">
          {{ lhbJob.running ? '同步中…' : '同步龙虎榜' }}
        </el-button>
      </div>
    </div>

    <div v-if="lhbJob.running" class="progress-box">
      <el-progress :percentage="lhbJob.phase === 'seats' ? 60 : lhbJob.phase === 'done' ? 100 : 40" :stroke-width="10" striped striped-flow />
      <p class="progress-text"><strong>{{ lhbJob.label }}</strong><span v-if="lhbJob.message"> · {{ lhbJob.message }}</span></p>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="全市场主力抢筹榜" name="rank">
        <div class="filter-row">
          <el-radio-group v-model="indicator" @change="onIndicatorChange">
            <el-radio-button value="今日">今日</el-radio-button>
            <el-radio-button value="5日">5日</el-radio-button>
            <el-radio-button value="10日">10日</el-radio-button>
          </el-radio-group>
          <span class="result-count">共 {{ rankRows.length }} 只</span>
        </div>
        <el-table v-loading="rankLoading" :data="rankRows" border size="small" max-height="620" @row-click="openDrawer" row-class-name="clickable-row">
          <el-table-column type="index" label="#" width="52" align="center" />
          <el-table-column prop="code" label="代码" width="90" align="center" />
          <el-table-column prop="name" label="名称" width="110" align="center" />
          <el-table-column label="最新价" width="86" align="right">
            <template #default="scope">{{ Number(scope.row.price).toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="涨跌幅" width="90" align="right">
            <template #default="scope"><span :class="pctClass(scope.row.changePct)">{{ formatPct(scope.row.changePct) }}</span></template>
          </el-table-column>
          <el-table-column label="主力净流入" width="110" align="right">
            <template #default="scope"><span :class="pctClass(scope.row.mainNetInflow)">{{ formatAmount(scope.row.mainNetInflow) }}</span></template>
          </el-table-column>
          <el-table-column label="主力净占比" width="100" align="right">
            <template #default="scope">{{ formatPct(scope.row.mainNetRatio) }}</template>
          </el-table-column>
          <el-table-column label="超大单" width="100" align="right">
            <template #default="scope"><span :class="pctClass(scope.row.superNetInflow)">{{ formatAmount(scope.row.superNetInflow) }}</span></template>
          </el-table-column>
          <el-table-column label="大单" width="100" align="right">
            <template #default="scope"><span :class="pctClass(scope.row.largeNetInflow)">{{ formatAmount(scope.row.largeNetInflow) }}</span></template>
          </el-table-column>
          <el-table-column label="中单" width="100" align="right">
            <template #default="scope"><span :class="pctClass(scope.row.mediumNetInflow)">{{ formatAmount(scope.row.mediumNetInflow) }}</span></template>
          </el-table-column>
          <el-table-column label="小单" width="100" align="right">
            <template #default="scope"><span :class="pctClass(scope.row.smallNetInflow)">{{ formatAmount(scope.row.smallNetInflow) }}</span></template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="每日龙虎榜总览" name="lhb">
        <div class="filter-row">
          <el-checkbox v-model="instOnly" @change="loadLhb">仅看机构净买</el-checkbox>
          <span class="result-count">共 {{ lhbRows.length }} 条</span>
        </div>
        <el-table v-loading="lhbLoading" :data="lhbRows" border size="small" max-height="620" @row-click="openDrawer" row-class-name="clickable-row">
          <el-table-column prop="tradeDate" label="上榜日" width="105" align="center" />
          <el-table-column prop="code" label="代码" width="90" align="center" />
          <el-table-column label="名称" width="105" align="center">
            <template #default="scope">
              <span>{{ scope.row.stockName }}</span>
              <el-tag v-if="scope.row.instNetBuy != null && scope.row.instNetBuy > 0" size="small" type="danger" style="margin-left:4px">机构净买</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reason" label="上榜原因" min-width="180" show-overflow-tooltip />
          <el-table-column label="龙虎榜净买" width="110" align="right">
            <template #default="scope"><span :class="pctClass(scope.row.lhbNetBuy)">{{ formatAmount(scope.row.lhbNetBuy) }}</span></template>
          </el-table-column>
          <el-table-column label="买入额" width="100" align="right">
            <template #default="scope">{{ formatAmount(scope.row.lhbBuyAmount) }}</template>
          </el-table-column>
          <el-table-column label="卖出额" width="100" align="right">
            <template #default="scope">{{ formatAmount(scope.row.lhbSellAmount) }}</template>
          </el-table-column>
          <el-table-column label="成交占比" width="90" align="right">
            <template #default="scope">{{ scope.row.turnoverRatio != null ? scope.row.turnoverRatio + '%' : '-' }}</template>
          </el-table-column>
          <el-table-column label="机构净买" width="110" align="right">
            <template #default="scope">
              <span v-if="scope.row.instNetBuy != null" :class="pctClass(scope.row.instNetBuy)">{{ formatAmount(scope.row.instNetBuy) }}</span>
              <span v-else>-</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <FundFlowDrawer v-model="drawerVisible" :code="drawerCode" :name="drawerName" />
  </div>
</template>

<style scoped>
.ff-page {
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
  gap: 8px;
}
.progress-box {
  border: 1px solid #cfe3f7;
  background: #f5faff;
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 12px;
}
.progress-text {
  margin: 8px 0 0;
  color: #2b3f55;
  font-size: 0.86rem;
}
.filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.result-count {
  color: #6b7c93;
  font-size: 0.85rem;
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
:deep(.clickable-row) {
  cursor: pointer;
}
</style>

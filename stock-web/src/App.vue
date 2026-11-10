<template>
  <div class="app-shell">
    <aside class="left-nav">
      <div class="brand">投资面板</div>
      <div class="daily-sync">
        <el-button class="sync-btn" :loading="dailySync.running" @click="startDailySync">
          ⚡ 收盘一键更新
        </el-button>
        <div v-if="dailySync.running" class="sync-running">
          <el-progress :percentage="dailySync.percent" :stroke-width="6" :show-text="false" color="#66b1ff" />
          <span class="sync-step">{{ dailySync.current_step }}/4 {{ dailySync.step_label }}…</span>
        </div>
        <div v-else-if="dailySync.last_completed_at" class="sync-last">
          上次更新 {{ formatLastCompleted(dailySync.last_completed_at) }}
        </div>
      </div>
      <ul class="menu-list">
        <li v-for="item in sideMenus" :key="item.key" :class="{ active: activeMenu === item.key }" @click="activeMenu = item.key">
          <el-icon class="menu-icon"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
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
          <Positions />
        </template>

        <template v-else-if="activeMenu === 'closed'">
          <ClosedPositions />
        </template>

        <template v-else-if="activeMenu === 'market'">
          <AStockMarket @add-watchlist="handleAddWatchlist" />
        </template>

        <template v-else-if="activeMenu === 'msci'">
          <MsciData />
        </template>

        <template v-else-if="activeMenu === 'controller'">
          <StateOwned />
        </template>

        <template v-else-if="activeMenu === 'fundflow'">
          <FundFlow />
        </template>

        <template v-else-if="activeMenu === 'watchlist'">
          <WatchList :items="watchlistData" @remove-item="removeWatchlistItem" @clear-all="clearWatchlist" />
        </template>

        <template v-else-if="activeMenu === 'grid'">
          <GridTrading :portfolio="portfolio" />
        </template>

        <template v-else-if="activeMenu === 'reports'">
          <AnalysisReport :initial-code="analysisCode" />
        </template>

        <template v-else>
          <div class="placeholder">
            <p>{{ menuTitleMap[activeMenu] }}模块待完善</p>
            <span>当前“持仓股票”模块已接入完整增删改查与刷新能力。</span>
          </div>
        </template>
      </main>
    </section>
  </div>
</template>

<script setup>
import { onMounted, provide, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  Grid,
  Wallet,
  Files,
  DataAnalysis,
  OfficeBuilding,
  TrendCharts,
  Sort,
  Star,
  Document,
  Histogram,
  Coin,
} from '@element-plus/icons-vue';
import Strategy from './components/Strategy.vue';
import Positions from './components/Positions.vue';
import ClosedPositions from './components/ClosedPositions.vue';
import MsciData from './components/MsciData.vue';
import StateOwned from './components/StateOwned.vue';
import WatchList from './components/WatchList.vue';
import GridTrading from './components/GridTrading.vue';
import AStockMarket from './components/AStockMarket.vue';
import FundFlow from './components/FundFlow.vue';
import AnalysisReport from './components/AnalysisReport.vue';

const WATCHLIST_API = '/api/watchlist';
const PORTFOLIO_API = '/api/portfolio';

const watchlistData = ref([]);
const portfolio = ref([]);

// 分析报告跳转：持仓/策略点击股票时切到分析报告页
const analysisCode = ref('');
function openAnalysisReport(code) {
  analysisCode.value = String(code || '').trim();
  activeMenu.value = 'reports';
}
provide('openAnalysisReport', openAnalysisReport);
provide('analysisCode', analysisCode);

// 全局刷新信号：收盘更新完成后自增，各页面监听后重载数据
const refreshSignal = ref(0);
provide('appRefreshSignal', refreshSignal);

// 收盘一键更新
const dailySync = ref({
  running: false,
  current_step: 0,
  step_label: '',
  percent: 0,
  steps: [],
  last_completed_at: null,
  error: '',
});
let dailyPollTimer = null;

const activeMenu = ref('positions');
const sideMenus = [
  { key: 'positions', label: '持仓股票', icon: Wallet },
  { key: 'closed', label: '已清仓', icon: Files },
  { key: 'market', label: 'A股全市场', icon: Histogram },
  { key: 'msci', label: 'MSCI数据', icon: DataAnalysis },
  { key: 'controller', label: '国资控股', icon: OfficeBuilding },
  { key: 'fundflow', label: '资金与异动', icon: Coin },
  { key: 'strategy', label: '选股策略', icon: TrendCharts },
  { key: 'rebalance', label: '待调仓股票', icon: Sort },
  { key: 'watchlist', label: '观察列表', icon: Star },
  { key: 'grid', label: '网格交易', icon: Grid },
  { key: 'reports', label: '分析报告', icon: Document },
];
const menuTitleMap = {
  strategy: '选股策略',
  positions: '持仓股票',
  closed: '已清仓',
  market: 'A股全市场',
  msci: 'MSCI数据',
  controller: '国资控股',
  fundflow: '资金与异动',
  rebalance: '待调仓股票',
  watchlist: '观察列表',
  grid: '网格交易',
  reports: '分析报告',
};

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

async function loadPortfolioData() {
  try {
    const resp = await fetch(PORTFOLIO_API, { method: 'GET' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    portfolio.value = Array.isArray(data) ? data : [];
  } catch (error) {
    console.error('读取持仓数据失败:', error);
    portfolio.value = [];
  }
}

// ---------- 收盘一键更新（四合一流水线） ----------
async function startDailySync() {
  if (dailySync.value.running) return;
  try {
    const resp = await fetch('/api/system/daily-sync', { method: 'POST' });
    const json = await resp.json().catch(() => ({}));
    if (resp.status === 409) {
      ElMessage.warning(json?.message || '收盘更新正在进行中');
      if (json?.job) dailySync.value = json.job;
      startDailyPolling();
      return;
    }
    if (!resp.ok) throw new Error(json?.message || `HTTP ${resp.status}`);
    if (json?.job) dailySync.value = json.job;
    dailySync.value.running = true;
    ElMessage.success('收盘一键更新已启动');
    startDailyPolling();
  } catch (error) {
    ElMessage.error(`启动失败：${error.message || '请稍后重试'}`);
  }
}

async function loadDailyProgress() {
  try {
    const resp = await fetch('/api/system/daily-sync/progress');
    if (!resp.ok) return;
    const data = await resp.json();
    dailySync.value = data;
  } catch {
    // 忽略轮询错误
  }
}

function startDailyPolling() {
  if (dailyPollTimer) return;
  dailyPollTimer = setInterval(async () => {
    await loadDailyProgress();
    if (!dailySync.value.running) {
      stopDailyPolling();
      const errSteps = (dailySync.value.steps || []).filter(s => s.status === 'error').length;
      if (errSteps) ElMessage.warning(`更新完成，其中 ${errSteps} 步失败`);
      else ElMessage.success('收盘更新完成');
      // 触发当前页面刷新
      refreshSignal.value += 1;
      void loadWatchlistData();
      void loadPortfolioData();
    }
  }, 1500);
}

function stopDailyPolling() {
  if (dailyPollTimer) {
    clearInterval(dailyPollTimer);
    dailyPollTimer = null;
  }
}

function formatLastCompleted(value) {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('zh-CN', { hour12: false });
}

onMounted(() => {
  void (async () => {
    await Promise.all([loadWatchlistData(), loadPortfolioData()]);
    await loadDailyProgress();
  })();
});

// 进入网格交易页时重新拉一次持仓，保证「从持仓导入」拿到最新数量/成本
watch(activeMenu, menu => {
  if (menu === 'grid') void loadPortfolioData();
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

.daily-sync {
  padding: 12px 12px 4px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.sync-btn {
  width: 100%;
  border: 0;
  color: #fff;
  font-weight: 600;
  background: linear-gradient(90deg, #2f82d5, #4ca0ee);
}

.sync-running {
  margin-top: 8px;
}

.sync-step {
  display: block;
  margin-top: 4px;
  color: #bcd6f2;
  font-size: 0.78rem;
  line-height: 1.3;
}

.sync-last {
  margin-top: 8px;
  color: #8fb3d9;
  font-size: 0.76rem;
  line-height: 1.4;
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
  display: flex;
  align-items: center;
  gap: 9px;
  transition:
    background-color 0.2s ease,
    color 0.2s ease;
}

.menu-icon {
  font-size: 16px;
  opacity: 0.9;
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
}
</style>

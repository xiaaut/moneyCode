<script setup>
import { computed, inject, onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import FundFlowDrawer from './FundFlowDrawer.vue';

const API = '/api/rebalance/dashboard';
const PLANS_API = '/api/rebalance/plans';
const openAnalysisReport = inject('openAnalysisReport', () => {});

// 详情抽屉
const drawerVisible = ref(false);
const drawerCode = ref('');
const drawerName = ref('');
function openDrawer(item) {
  drawerCode.value = item.code;
  drawerName.value = item.name;
  drawerVisible.value = true;
}

const loading = ref(false);
const dashboard = ref(null);
const filter = ref('');
const rescanning = ref(false);

const SIGNAL_TYPES = [
  { key: '', label: '全部持仓与自选' },
  { key: 'BUY_ZONE', label: '🎯 击球加仓' },
  { key: 'PROFIT_ZONE', label: '💰 止盈落袋' },
  { key: 'RISK_ZONE', label: '🚨 财报排雷' },
  { key: 'REBALANCE_ZONE', label: '⚖️ 仓位与深套' },
];
const SIGNAL_COLOR = { BUY_ZONE: '#10b981', PROFIT_ZONE: '#3b82f6', RISK_ZONE: '#ef4444', REBALANCE_ZONE: '#f59e0b' };
const SIGNAL_BADGE = { BUY_ZONE: '击球加仓区', PROFIT_ZONE: '止盈落袋区', RISK_ZONE: '财报排雷区', REBALANCE_ZONE: '仓位与深套区' };
const ACTION_LABEL = { BUY_ADD: '逢低加仓', SELL_PROFIT: '分批止盈', SELL_RISK: '排雷减仓', GRID_T: '波段做T' };
const ACTION_COLOR = { BUY_ADD: '#10b981', SELL_PROFIT: '#3b82f6', SELL_RISK: '#ef4444', GRID_T: '#f59e0b' };

/* ---------------- 数值工具 ---------------- */
const num = v => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};
const fmtP = (v, d = 2) => (v == null ? '—' : `${num(v).toFixed(d)}`);
const fmtPct = (v, d = 1) => (v == null ? '—' : `${num(v).toFixed(d)}%`);
const fmtMoney = v => {
  const n = num(v);
  const sign = n < 0 ? '-' : '';
  return `${sign}¥${Math.abs(n).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
};

/* ---------------- 数据加载 ---------------- */
async function loadDashboard() {
  loading.value = true;
  try {
    const resp = await fetch(API);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data?.detail || `HTTP ${resp.status}`);
    dashboard.value = data;
  } catch (e) {
    ElMessage.error(`加载调仓雷达失败：${e.message || e}`);
  } finally {
    loading.value = false;
  }
}

async function rescan() {
  rescanning.value = true;
  await loadDashboard();
  rescanning.value = false;
  ElMessage.success('调仓雷达已重新扫描');
}

const summary = computed(() => dashboard.value?.summary || {});
const signals = computed(() => dashboard.value?.signals || []);
const plans = computed(() => dashboard.value?.plans || []);

const filteredSignals = computed(() => {
  if (!filter.value) return signals.value;
  return signals.value.filter(s => (s.signals || []).includes(filter.value));
});

const signalCounts = computed(() => {
  const counts = { BUY_ZONE: 0, PROFIT_ZONE: 0, RISK_ZONE: 0, REBALANCE_ZONE: 0 };
  for (const s of signals.value) {
    for (const sig of s.signals || []) counts[sig] = (counts[sig] || 0) + 1;
  }
  return counts;
});

/* 集中度堆叠条比例 */
const concentrationPct = computed(() => {
  const list = summary.value.concentration || [];
  const total = list.reduce((s, c) => s + num(c.weight_pct), 0) || 1;
  return list.map(c => ({ ...c, pct: (num(c.weight_pct) / total * 100).toFixed(2) + '%' }));
});
const CONC_COLORS = ['#2563eb', '#38bdf8', '#10b981', '#8b5cf6', '#f59e0b', '#cbd5e1'];

/* ---------------- 价格水位尺 ---------------- */
function scalePct(item) {
  const k = item.kline || {};
  const high = num(k.high_5y), low = num(k.low_5y);
  if (!(high > low)) return null;
  const clamp = v => Math.max(0, Math.min(100, v));
  const price = num(item.current_price);
  const cost = num(item.cost_price);
  return {
    pricePct: clamp((price - low) / (high - low) * 100),
    costPct: cost > 0 ? clamp((cost - low) / (high - low) * 100) : null,
  };
}

/* ---------------- 工单 ---------------- */
const planDialog = reactive({ visible: false, editing: null });
const planForm = reactive({ code: '', name: '', account_tag: '', action_type: 'BUY_ADD', trigger_condition: '<=', target_price: 0, plan_shares: 100, reason_tag: '', notes: '' });

function openPlanDialog(signalItem) {
  planDialog.editing = null;
  const action = signalItem?.signal_type === 'PROFIT_ZONE' ? 'SELL_PROFIT' : signalItem?.signal_type === 'RISK_ZONE' ? 'SELL_RISK' : signalItem?.signal_type === 'REBALANCE_ZONE' ? 'GRID_T' : 'BUY_ADD';
  const cond = action === 'BUY_ADD' || action === 'GRID_T' ? '<=' : '>=';
  Object.assign(planForm, {
    code: signalItem?.code || '', name: signalItem?.name || '', account_tag: '',
    action_type: action, trigger_condition: cond,
    target_price: num(signalItem?.current_price), plan_shares: 100,
    reason_tag: signalItem?.signal_label || '', notes: '',
  });
  planDialog.visible = true;
}

async function savePlan() {
  if (!/^\d{6}$/.test(planForm.code)) { ElMessage.warning('请填写 6 位股票代码'); return; }
  if (!(num(planForm.target_price) > 0)) { ElMessage.warning('请填写触发价'); return; }
  const payload = {
    code: planForm.code, name: planForm.name, account_tag: planForm.account_tag || null,
    action_type: planForm.action_type, trigger_condition: planForm.trigger_condition,
    target_price: num(planForm.target_price), plan_shares: Math.round(num(planForm.plan_shares)),
    reason_tag: planForm.reason_tag || null, notes: planForm.notes || null,
  };
  try {
    const resp = await fetch(PLANS_API, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data?.detail);
    ElMessage.success('调仓工单已保存');
    planDialog.visible = false;
    await loadDashboard();
  } catch (e) {
    ElMessage.error(`保存失败：${e.message || e}`);
  }
}

async function markDone(plan) {
  try {
    await fetch(`${PLANS_API}/${plan.id}/done`, { method: 'POST' });
    await loadDashboard();
  } catch (e) {
    ElMessage.error(`操作失败：${e.message || e}`);
  }
}

async function deletePlan(plan) {
  try {
    await fetch(`${PLANS_API}/${plan.id}`, { method: 'DELETE' });
    await loadDashboard();
  } catch (e) {
    ElMessage.error(`删除失败：${e.message || e}`);
  }
}

function planDistance(plan) {
  const item = signals.value.find(s => s.code === plan.code);
  const price = num(item?.current_price);
  const target = num(plan.target_price);
  if (!price || !target) return null;
  const buy = plan.action_type === 'BUY_ADD' || plan.action_type === 'GRID_T';
  if (buy) return (target - price) / price * 100;  // 还需跌多少
  return (price - target) / price * 100;           // 还需涨多少
}

/* ---------------- 补仓沙盘 ---------------- */
const simForm = reactive({ code: '', mode: 'dilute', price: 0, shares: 100 });
const simBox = ref(null);

function loadToSim(item) {
  simForm.code = item.code;
  simForm.price = num(item.current_price);
  simForm.shares = 100;
  simForm.mode = 'dilute';
  setTimeout(() => simBox.value?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 60);
}

const simItem = computed(() => signals.value.find(s => s.code === simForm.code));

const simResult = computed(() => {
  const item = simItem.value;
  const price = num(simForm.price);
  const qty = Math.round(num(simForm.shares));
  if (!item || !(price > 0) || !(qty > 0)) return null;
  const cost0 = num(item.cost_price);
  const shares0 = num(item.shares);
  const price0 = num(item.current_price);
  const rate = 0.0001;
  const fee = price * qty * rate; // 万一免五
  const totalMv = num(summary.value.total_market_value);
  const oldWeight = num(item.position_weight_pct);

  if (simForm.mode === 'dilute') {
    const newShares = shares0 + qty;
    const newCost = (cost0 * shares0 + price * qty + fee) / newShares;
    const drop = cost0 - newCost;
    const oldNeed = cost0 > price0 ? (cost0 - price0) / price0 * 100 : 0;
    const newNeed = newCost > price0 ? (newCost - price0) / price0 * 100 : 0;
    const needDrop = oldNeed > 0 ? (oldNeed - newNeed) / oldNeed * 100 : 0;
    const addCash = price * qty + fee;
    const newMv = newShares * price0;
    const newTotalMv = totalMv + addCash;
    const newWeight = newTotalMv ? newMv / newTotalMv * 100 : 0;
    return { mode: 'dilute', cost0, newCost, drop, oldNeed, newNeed, needDrop, oldWeight, newWeight, cash: -addCash, cashLabel: '需追加本金', fee, newShares };
  }
  const release = price * qty - fee;
  const newShares = Math.max(0, shares0 - qty);
  const oldNeed = cost0 > price0 ? (cost0 - price0) / price0 * 100 : 0;
  const newMv = newShares * price0;
  const newTotalMv = totalMv - release;
  const newWeight = newTotalMv ? newMv / newTotalMv * 100 : 0;
  return { mode: 'reduce', cost0, newCost: cost0, drop: 0, oldNeed, newNeed: oldNeed, needDrop: 0, oldWeight, newWeight, cash: release, cashLabel: '释放现金', fee, newShares };
});

function quickPrice(pct) {
  const item = simItem.value;
  if (!item) return;
  simForm.price = num(item.current_price) * (1 - pct / 100);
}
function quickMa250() {
  const item = simItem.value;
  if (!item) return;
  const ma = num(item.kline?.ma250);
  if (ma > 0) simForm.price = ma;
}

async function saveSimAsPlan() {
  const item = simItem.value;
  const r = simResult.value;
  if (!item || !r) return;
  const buy = simForm.mode === 'dilute';
  const payload = {
    code: item.code, name: item.name, account_tag: null,
    action_type: buy ? 'BUY_ADD' : 'SELL_PROFIT',
    trigger_condition: buy ? '<=' : '>=',
    target_price: num(simForm.price), plan_shares: Math.round(num(simForm.shares)),
    current_cost: item.cost_price,
    reason_tag: buy ? '补仓摊薄沙盘' : '减仓做T沙盘',
    notes: `${simForm.mode === 'dilute' ? '补仓' : '减仓'} ${simForm.shares} 股 @ ${fmtP(simForm.price)}，${r.cashLabel} ${fmtMoney(Math.abs(r.cash))}`,
  };
  try {
    const resp = await fetch(PLANS_API, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data?.detail);
    ElMessage.success('沙盘方案已存为调仓工单');
    await loadDashboard();
  } catch (e) {
    ElMessage.error(`保存失败：${e.message || e}`);
  }
}

onMounted(loadDashboard);
</script>

<template>
  <div v-loading="loading" class="rebalance-page">
    <!-- ============ 1. 总览顶栏 ============ -->
    <section class="hero">
      <div class="hero-left">
        <div class="hero-title">持仓仓位集中度</div>
        <div class="conc-bar">
          <div v-for="(c, i) in concentrationPct" :key="c.code || i" class="conc-seg" :style="{ width: c.pct, background: CONC_COLORS[i % CONC_COLORS.length] }"></div>
        </div>
        <div class="conc-legend">
          <span v-for="(c, i) in concentrationPct" :key="c.code || i" class="conc-item">
            <i :style="{ background: CONC_COLORS[i % CONC_COLORS.length] }"></i>{{ c.name }} {{ c.weight_pct }}%
          </span>
        </div>
      </div>
      <div class="hero-right">
        <div class="hero-metric">
          <div class="hm-label">优质资产占比</div>
          <div class="hm-value" :style="{ color: summary.quality_ratio_pct >= 60 ? '#10b981' : summary.quality_ratio_pct >= 30 ? '#f59e0b' : '#ef4444' }">{{ fmtPct(summary.quality_ratio_pct) }}</div>
          <div class="hm-sub">健康分 ≥80 的持仓市值</div>
        </div>
        <div class="hero-metric">
          <div class="hm-label">待处理信号</div>
          <div class="hm-value" style="color:#2563eb">{{ summary.signal_count || 0 }}</div>
          <div class="hm-sub">四维雷达命中</div>
        </div>
        <div class="hero-metric">
          <div class="hm-label">🔔 已到价工单</div>
          <div class="hm-value" style="color:#f59e0b">{{ summary.triggered_plan_count || 0 }}</div>
          <div class="hm-sub">待执行</div>
        </div>
        <el-button type="primary" plain :loading="rescanning" @click="rescan">🔄 重新扫描雷达</el-button>
      </div>
    </section>

    <!-- ============ 2. 筛选胶囊 ============ -->
    <div class="filter-row">
      <button v-for="t in SIGNAL_TYPES" :key="t.key" class="filter-chip" :class="{ active: filter === t.key }" @click="filter = t.key">
        {{ t.label }}<span v-if="t.key" class="chip-count">{{ signalCounts[t.key] || 0 }}</span>
      </button>
    </div>

    <!-- ============ 3. 信号卡片网格 ============ -->
    <section class="signal-grid">
      <div v-for="item in filteredSignals" :key="item.code" class="signal-card" :style="{ borderTopColor: SIGNAL_COLOR[item.signal_type] || '#e2e8f0' }">
        <div class="card-head">
          <div class="head-left">
            <span class="stock-name" @click="openDrawer(item)">{{ item.name }}</span>
            <span class="stock-code">{{ item.code }}</span>
            <span v-if="item.signal_type" class="signal-badge" :style="{ background: SIGNAL_COLOR[item.signal_type] + '1a', color: SIGNAL_COLOR[item.signal_type] }">{{ SIGNAL_BADGE[item.signal_type] }}</span>
            <span v-if="!item.is_position" class="watch-badge">自选</span>
          </div>
          <div class="head-right">
            <button class="health-chip" :class="{ 'not-checked': !item.checked && !item.is_etf }" @click="openAnalysisReport(item.code)">
              {{ item.is_etf ? '指数基金·免体检' : (item.checked ? (item.health_score != null ? `健康分 ${item.health_score}` : '体检中') : '待体检') }}
            </button>
          </div>
        </div>

        <div class="card-stats">
          <span>仓位 <b>{{ fmtPct(item.position_weight_pct) }}</b></span>
          <span :class="num(item.profit_ratio) >= 0 ? 'up' : 'down'">浮盈亏 <b>{{ num(item.profit_ratio) >= 0 ? '+' : '' }}{{ fmtPct(item.profit_ratio) }}</b></span>
          <span>现价 <b>¥{{ fmtP(item.current_price) }}</b></span>
          <span v-if="num(item.cost_price) > 0">成本 <b>¥{{ fmtP(item.cost_price) }}</b></span>
        </div>

        <!-- 5 年价格水位尺 -->
        <div class="scale" v-if="scalePct(item)">
          <div class="scale-top">
            <span class="s-low">5年最低 ¥{{ fmtP(item.kline.low_5y) }}</span>
            <span class="s-mid">当前 5 年 {{ fmtPct(item.kline.percentile_5y) }} 分位 · MA250 ¥{{ fmtP(item.kline.ma250) }}</span>
            <span class="s-high">5年最高 ¥{{ fmtP(item.kline.high_5y) }}</span>
          </div>
          <div class="scale-track">
            <div class="track-gradient"></div>
            <div class="dot dot-price" :style="{ left: scalePct(item).pricePct + '%' }">
              <span class="dot-label price-label">现价 ¥{{ fmtP(item.current_price) }}</span>
            </div>
            <div v-if="scalePct(item).costPct != null" class="dot dot-cost" :style="{ left: scalePct(item).costPct + '%' }">
              <span class="dot-label cost-label">成本 ¥{{ fmtP(item.cost_price) }}</span>
            </div>
          </div>
        </div>

        <div class="card-foot">
          <p class="advice">{{ item.advice }}</p>
          <div class="foot-actions">
            <el-button size="small" plain @click="loadToSim(item)">🧮 补仓/减仓试算</el-button>
            <el-button size="small" type="primary" plain @click="openPlanDialog(item)">➕ 设为调仓工单</el-button>
          </div>
        </div>
      </div>
      <div v-if="!filteredSignals.length && !loading" class="empty-tip">暂无数据，点击「🔄 重新扫描雷达」加载。</div>
    </section>

    <!-- ============ 4. 底部双栏工作台 ============ -->
    <section class="bottom-row">
      <!-- 左栏：工单表 -->
      <div class="panel">
        <div class="panel-head">
          <h3>待执行调仓工单</h3>
          <el-button size="small" type="primary" plain @click="openPlanDialog(null)">新增工单</el-button>
        </div>
        <el-table :data="plans" size="small" border max-height="420" :row-class-name="row => row.status === 'TRIGGERED' ? 'row-triggered' : ''">
          <el-table-column label="标的" min-width="120">
            <template #default="s"><b>{{ s.row.name }}</b> <span class="mono">{{ s.row.code }}</span></template>
          </el-table-column>
          <el-table-column label="方向" width="90" align="center">
            <template #default="s">
              <span class="dir-badge" :style="{ background: (ACTION_COLOR[s.row.action_type] || '#94a3b8') + '1a', color: ACTION_COLOR[s.row.action_type] || '#475569' }">{{ ACTION_LABEL[s.row.action_type] || s.row.action_type }}</span>
            </template>
          </el-table-column>
          <el-table-column label="触发价 / 现价" width="120" align="right">
            <template #default="s">
              ¥{{ fmtP(s.row.target_price) }} / ¥{{ fmtP(signals.find(x => x.code === s.row.code)?.current_price) }}
            </template>
          </el-table-column>
          <el-table-column label="距离触发" width="90" align="right">
            <template #default="s">
              <span v-if="s.row.status === 'TRIGGERED'" class="trigger-badge">🔔 已到价</span>
              <span v-else>{{ planDistance(s.row) != null ? (planDistance(s.row) > 0 ? '还需 ' : '已超 ') + fmtPct(Math.abs(planDistance(s.row)), 1) : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="plan_shares" label="股数" width="70" align="right" />
          <el-table-column prop="notes" label="备注" min-width="110" show-overflow-tooltip />
          <el-table-column label="操作" width="130" align="center" fixed="right">
            <template #default="s">
              <el-button v-if="s.row.status !== 'DONE'" size="small" text type="primary" @click="markDone(s.row)">执行</el-button>
              <el-button size="small" text type="danger" @click="deletePlan(s.row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 右栏：补仓沙盘 -->
      <div class="panel" ref="simBox">
        <div class="panel-head"><h3>补仓摊薄与调仓沙盘</h3></div>
        <el-select v-model="simForm.code" filterable placeholder="选择持仓股" style="width: 100%; margin-bottom: 10px">
          <el-option v-for="s in signals.filter(x => x.is_position)" :key="s.code" :value="s.code" :label="`${s.code} ${s.name}`" />
        </el-select>
        <div class="sim-mode">
          <button class="mode-btn" :class="{ active: simForm.mode === 'dilute' }" @click="simForm.mode = 'dilute'">逢低补仓摊薄</button>
          <button class="mode-btn" :class="{ active: simForm.mode === 'reduce' }" @click="simForm.mode = 'reduce'">分批减仓/做T降本</button>
        </div>

        <template v-if="simItem">
          <div class="sim-row">
            <span class="sim-label">拟成交价</span>
            <el-input-number v-model="simForm.price" :min="0.01" :precision="3" :step="0.1" style="width: 140px" />
            <div class="quick-btns">
              <button @click="quickPrice(0)">现价</button>
              <button @click="quickPrice(3)">-3%</button>
              <button @click="quickPrice(5)">-5%</button>
              <button @click="quickMa250">MA250</button>
            </div>
          </div>
          <div class="sim-row">
            <span class="sim-label">拟操作股数</span>
            <el-input-number v-model="simForm.shares" :min="100" :step="100" style="width: 140px" />
            <div class="quick-btns">
              <button @click="simForm.shares = 100">100股</button>
              <button @click="simForm.shares = 500">500股</button>
              <button @click="simForm.shares = 1000">1000股</button>
              <button @click="simForm.shares = Math.max(100, num(simItem.shares))">持仓翻倍</button>
            </div>
          </div>
        </template>

        <div v-if="simResult" class="sim-cards">
          <div class="sim-card">
            <div class="sc-label">持仓成本变化</div>
            <div class="sc-main">¥{{ fmtP(simResult.cost0) }} ➔ <b :style="{ color: simResult.drop > 0 ? '#10b981' : '#475569' }">¥{{ fmtP(simResult.newCost) }}</b></div>
            <div class="sc-sub" v-if="simResult.drop > 0">直降 ¥{{ fmtP(simResult.drop) }}</div>
          </div>
          <div class="sim-card">
            <div class="sc-label">回本所需涨幅</div>
            <div class="sc-main">+{{ fmtPct(simResult.oldNeed) }} ➔ <b style="color:#10b981">+{{ fmtPct(simResult.newNeed) }}</b></div>
            <div class="sc-sub" v-if="simResult.needDrop > 0">难度降低 {{ fmtP(simResult.needDrop, 0) }}%</div>
          </div>
          <div class="sim-card">
            <div class="sc-label">单票仓位占比</div>
            <div class="sc-main">{{ fmtPct(simResult.oldWeight) }} ➔ <b :class="{ 'over': simResult.newWeight > 20 }">{{ fmtPct(simResult.newWeight) }}</b></div>
            <div class="sc-sub" v-if="simResult.newWeight > 20" style="color:#f59e0b">⚠ 超重提醒（>20%）</div>
          </div>
          <div class="sim-card">
            <div class="sc-label">{{ simResult.cashLabel }}</div>
            <div class="sc-main" :style="{ color: simResult.cash < 0 ? '#ef4444' : '#10b981' }">{{ fmtMoney(Math.abs(simResult.cash)) }}</div>
            <div class="sc-sub">含佣金 ¥{{ fmtP(simResult.fee) }}</div>
          </div>
        </div>
        <el-button v-if="simResult" type="primary" style="width:100%; margin-top: 12px" @click="saveSimAsPlan">💾 将此沙盘方案一键存为调仓工单</el-button>
      </div>
    </section>

    <!-- 工单对话框 -->
    <el-dialog v-model="planDialog.visible" title="新增调仓工单" width="460px">
      <el-form label-width="90px" size="small">
        <el-form-item label="标的代码"><el-input v-model="planForm.code" placeholder="6 位代码" style="width: 200px" /></el-form-item>
        <el-form-item label="标的名称"><el-input v-model="planForm.name" style="width: 200px" /></el-form-item>
        <el-form-item label="账户标签"><el-input v-model="planForm.account_tag" placeholder="可选，如 账户A" style="width: 200px" /></el-form-item>
        <el-form-item label="调仓方向">
          <el-select v-model="planForm.action_type" style="width: 200px">
            <el-option value="BUY_ADD" label="逢低加仓" />
            <el-option value="SELL_PROFIT" label="分批止盈" />
            <el-option value="SELL_RISK" label="排雷减仓" />
            <el-option value="GRID_T" label="波段做T" />
          </el-select>
        </el-form-item>
        <el-form-item label="触发条件">
          <el-radio-group v-model="planForm.trigger_condition">
            <el-radio value="<=">&lt;=（跌到）</el-radio>
            <el-radio value=">=">&gt;=（涨到）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="触发价"><el-input-number v-model="planForm.target_price" :min="0.01" :precision="3" :step="0.1" style="width: 200px" /></el-form-item>
        <el-form-item label="计划股数"><el-input-number v-model="planForm.plan_shares" :min="100" :step="100" style="width: 200px" /></el-form-item>
        <el-form-item label="原因标签"><el-input v-model="planForm.reason_tag" style="width: 200px" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="planForm.notes" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="planDialog.visible = false">取消</el-button>
        <el-button type="primary" @click="savePlan">保存工单</el-button>
      </template>
    </el-dialog>

    <!-- 详情抽屉（K 线 + 成本 + 资金流 + 龙虎榜） -->
    <FundFlowDrawer v-model="drawerVisible" :code="drawerCode" :name="drawerName" />
  </div>
</template>

<style scoped>
.rebalance-page { display: flex; flex-direction: column; gap: 14px; padding: 4px; color: #0f172a; }

/* ---- 总览顶栏 ---- */
.hero {
  display: flex; justify-content: space-between; gap: 20px; flex-wrap: wrap;
  border-radius: 16px; border: 1px solid #DBEAFE; padding: 18px 20px;
  background: linear-gradient(135deg, #EFF6FF 0%, #F8FAFC 55%, #ECFDF5 100%);
  box-shadow: 0 4px 20px rgba(59, 130, 246, 0.06);
}
.hero-left { flex: 1; min-width: 320px; }
.hero-title { font-size: 0.85rem; font-weight: 700; color: #334155; margin-bottom: 10px; }
.conc-bar { display: flex; height: 16px; border-radius: 8px; overflow: hidden; background: #e2e8f0; }
.conc-seg { height: 100%; transition: width 0.4s; }
.conc-legend { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px; font-size: 0.78rem; color: #475569; }
.conc-item { display: inline-flex; align-items: center; gap: 5px; }
.conc-item i { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }

.hero-right { display: flex; align-items: stretch; gap: 14px; flex-wrap: wrap; }
.hero-metric {
  background: rgba(255,255,255,0.85); border: 1px solid #E2E8F0; border-radius: 12px;
  padding: 12px 16px; min-width: 120px; text-align: center;
}
.hm-label { font-size: 0.76rem; color: #64748b; }
.hm-value { font-size: 1.5rem; font-weight: 800; font-variant-numeric: tabular-nums; margin: 4px 0; }
.hm-sub { font-size: 0.7rem; color: #94a3b8; }

/* ---- 筛选胶囊 ---- */
.filter-row { display: flex; gap: 8px; flex-wrap: wrap; }
.filter-chip {
  border: 1px solid #BFDBFE; background: #fff; color: #334155; border-radius: 999px;
  padding: 6px 14px; font-size: 0.82rem; cursor: pointer; transition: all 0.15s;
  display: inline-flex; align-items: center; gap: 6px;
}
.filter-chip:hover { border-color: #2563eb; color: #2563eb; }
.filter-chip.active { background: #2563eb; border-color: #2563eb; color: #fff; font-weight: 600; }
.chip-count { background: rgba(37,99,235,0.12); color: #2563eb; border-radius: 999px; padding: 0 7px; font-size: 0.72rem; }
.filter-chip.active .chip-count { background: rgba(255,255,255,0.25); color: #fff; }

/* ---- 信号卡片 ---- */
.signal-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.signal-card {
  background: #fff; border: 1px solid #e2e8f0; border-top: 3px solid #e2e8f0; border-radius: 14px;
  padding: 14px 16px; box-shadow: 0 2px 8px rgba(15,23,42,0.05); transition: transform 0.15s, box-shadow 0.15s;
}
.signal-card:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(15,23,42,0.09); }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.head-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.stock-name { font-size: 1.05rem; font-weight: 800; color: #0f172a; }
.stock-code { font-size: 0.78rem; color: #94a3b8; font-variant-numeric: tabular-nums; }
.signal-badge { font-size: 0.72rem; padding: 2px 10px; border-radius: 999px; font-weight: 600; }
.watch-badge { font-size: 0.72rem; padding: 2px 8px; border-radius: 6px; background: #f1f5f9; color: #64748b; }
.health-chip {
  border: 1px solid #BFDBFE; background: #EFF6FF; color: #2563eb; border-radius: 999px;
  padding: 3px 12px; font-size: 0.76rem; font-weight: 700; cursor: pointer; transition: all 0.15s;
}
.health-chip:hover { background: #2563eb; color: #fff; }
.health-chip.not-checked { background: #f8fafc; border-color: #e2e8f0; color: #94a3b8; }

.card-stats { display: flex; gap: 14px; margin-top: 10px; font-size: 0.8rem; color: #64748b; flex-wrap: wrap; }
.card-stats b { font-variant-numeric: tabular-nums; }
.up { color: #dc2626; } .down { color: #16a34a; }
.up b { color: #dc2626; } .down b { color: #16a34a; }

/* ---- 价格水位尺 ---- */
.scale { margin-top: 14px; }
.scale-top { display: flex; justify-content: space-between; align-items: center; font-size: 0.74rem; color: #64748b; margin-bottom: 8px; gap: 8px; flex-wrap: wrap; }
.s-mid { font-weight: 700; color: #2563eb; background: #EFF6FF; border: 1px solid #BFDBFE; padding: 2px 10px; border-radius: 999px; }
.scale-track { position: relative; height: 12px; border-radius: 6px; margin: 22px 0 24px; }
.track-gradient { position: absolute; inset: 0; border-radius: 6px; background: linear-gradient(90deg, #10b981 0%, #3b82f6 50%, #f59e0b 100%); }
.dot { position: absolute; top: 50%; transform: translate(-50%, -50%); z-index: 2; }
.dot-price { width: 14px; height: 14px; border-radius: 50%; background: #2563eb; border: 3px solid #fff; box-shadow: 0 1px 4px rgba(0,0,0,0.3); }
.dot-cost { width: 0; height: 0; border: 6px solid transparent; border-bottom: 9px solid #f97316; filter: drop-shadow(0 1px 2px rgba(0,0,0,0.3)); }
.dot-label { position: absolute; white-space: nowrap; font-size: 0.74rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.price-label { bottom: 22px; left: 50%; transform: translateX(-50%); color: #2563eb; }
.cost-label { top: 22px; left: 50%; transform: translateX(-50%); color: #ea580c; }

.card-foot { margin-top: 8px; border-top: 1px dashed #e2e8f0; padding-top: 10px; }
.advice { margin: 0; font-size: 0.82rem; color: #475569; line-height: 1.5; }
.foot-actions { display: flex; gap: 8px; margin-top: 10px; justify-content: flex-end; }

.empty-tip { grid-column: 1 / -1; text-align: center; color: #94a3b8; padding: 40px; }

/* ---- 底部双栏 ---- */
.bottom-row { display: grid; grid-template-columns: 1.4fr 1fr; gap: 14px; }
.panel { background: #fff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 14px 16px; box-shadow: 0 2px 8px rgba(15,23,42,0.05); }
.panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.panel-head h3 { margin: 0; font-size: 0.95rem; font-weight: 700; color: #1e293b; }

.mono { font-variant-numeric: tabular-nums; color: #94a3b8; font-size: 0.78rem; }
.dir-badge { font-size: 0.72rem; padding: 2px 8px; border-radius: 999px; font-weight: 600; }
.trigger-badge { color: #d97706; font-weight: 700; font-size: 0.76rem; animation: breathe 1.4s ease-in-out infinite; }
@keyframes breathe { 0%,100% { opacity: 1; } 50% { opacity: 0.45; } }
:deep(.row-triggered) { background: #fefce8 !important; }
:deep(.row-triggered td) { background: transparent !important; }

/* ---- 沙盘 ---- */
.sim-mode { display: flex; gap: 8px; margin-bottom: 12px; }
.mode-btn { flex: 1; border: 1px solid #BFDBFE; background: #fff; color: #334155; border-radius: 8px; padding: 7px; font-size: 0.82rem; cursor: pointer; transition: all 0.15s; }
.mode-btn.active { background: #2563eb; border-color: #2563eb; color: #fff; font-weight: 600; }
.sim-row { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.sim-label { font-size: 0.8rem; color: #64748b; width: 72px; }
.quick-btns { display: flex; gap: 6px; }
.quick-btns button { border: 1px solid #e2e8f0; background: #f8fafc; color: #475569; border-radius: 6px; padding: 4px 8px; font-size: 0.74rem; cursor: pointer; }
.quick-btns button:hover { border-color: #2563eb; color: #2563eb; }

.sim-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 14px; }
.sim-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px; }
.sc-label { font-size: 0.74rem; color: #64748b; }
.sc-main { font-size: 0.92rem; font-weight: 700; margin: 6px 0; font-variant-numeric: tabular-nums; }
.sc-main b { font-variant-numeric: tabular-nums; }
.sc-main b.over { color: #f59e0b; }
.sc-sub { font-size: 0.74rem; color: #94a3b8; }

@media (max-width: 1100px) {
  .signal-grid { grid-template-columns: 1fr; }
  .bottom-row { grid-template-columns: 1fr; }
}
</style>

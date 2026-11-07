<script setup>
import { computed, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  code: { type: String, default: '' },
  name: { type: String, default: '' },
  valuation: { type: Object, default: null },
});
const emit = defineEmits(['update:modelValue']);

const loading = ref(false);
const fundFlow = ref([]);
const lhb = ref([]);

const visible = computed({
  get: () => props.modelValue,
  set: v => emit('update:modelValue', v),
});

// 估值指标（来自持仓行，作为抽屉首屏展示）
const valuationItems = computed(() => {
  const v = props.valuation;
  if (!v) return [];
  const fmt = x => (x === null || x === undefined || x === '' || x === '-') ? '-' : String(x);
  return [
    { label: 'PB', value: fmt(v.pb) },
    { label: 'PE (TTM)', value: fmt(v.pe) },
    { label: 'ROE（加权）', value: fmt(v.roe) },
    { label: '平均 PE', value: fmt(v.avgPe) },
    { label: 'EPS', value: fmt(v.eps) },
    { label: 'EPS（3年）', value: fmt(v.eps3y) },
    { label: 'g（复合增速）', value: fmt(v.g) },
    { label: 'PEG', value: fmt(v.peg) },
    { label: '预估收益率', value: fmt(v.expectedReturn) },
  ];
});

function formatAmount(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '-';
  const abs = Math.abs(v);
  const sign = v > 0 ? '+' : v < 0 ? '-' : '';
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(1)}万`;
  return `${sign}${abs.toFixed(0)}`;
}

async function load() {
  if (!props.code) return;
  loading.value = true;
  try {
    const resp = await fetch(`/api/fund-flow/stock/${props.code}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    fundFlow.value = Array.isArray(data?.fundFlow) ? data.fundFlow : [];
    lhb.value = Array.isArray(data?.lhb) ? data.lhb : [];
  } catch (error) {
    ElMessage.error(`读取资金流失败：${error.message || '请稍后重试'}`);
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.modelValue,
  v => {
    if (v) load();
  },
);

// ---------- 图表 1：价格 + 主力净流入 + 10日累计（SVG） ----------
const CHART1_W = 680;
const CHART1_H = 260;
const PAD = { l: 52, r: 52, t: 14, b: 24 };

const chart1 = computed(() => {
  const data = fundFlow.value.slice(-30);
  if (data.length < 2) return null;
  const innerW = CHART1_W - PAD.l - PAD.r;
  const innerH = CHART1_H - PAD.t - PAD.b;

  const prices = data.map(d => Number(d.closePrice)).filter(Number.isFinite);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);
  const pRange = maxP - minP || 1;

  const nets = data.map(d => Number(d.mainNetInflow) || 0);
  const cum = nets.map((_, i) => {
    const start = Math.max(0, i - 9);
    return nets.slice(start, i + 1).reduce((a, b) => a + b, 0);
  });
  const netMax = Math.max(...nets.map(Math.abs), ...cum.map(Math.abs), 1e-9);
  const zeroY = PAD.t + innerH / 2;
  const step = innerW / (data.length - 1);

  const pricePts = data.map((d, i) => ({
    x: PAD.l + i * step,
    y: PAD.t + innerH - ((Number(d.closePrice) || minP) - minP) / pRange * innerH,
  }));
  const bars = data.map((d, i) => {
    const v = Number(d.mainNetInflow) || 0;
    const h = Math.abs(v) / netMax * (innerH / 2);
    return {
      x: PAD.l + i * step - 1.5,
      y: v >= 0 ? zeroY - h : zeroY,
      w: 3,
      h: Math.max(h, 0.5),
      up: v >= 0,
    };
  });
  const cumPts = cum.map((v, i) => ({
    x: PAD.l + i * step,
    y: zeroY - v / netMax * (innerH / 2),
  }));

  const first = data[0].tradeDate;
  const last = data[data.length - 1].tradeDate;

  return { data, pricePts, bars, cumPts, cum, minP, maxP, first, last, innerW, step };
});

function toPath(points) {
  return points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
}

// ---------- 图表 1 悬停提示 ----------
const hoverIndex = ref(null);
const hoverPos = ref({ x: 0, y: 0 });

const chartHover = computed(() => {
  if (hoverIndex.value == null || !chart1.value) return null;
  const i = hoverIndex.value;
  const d = chart1.value.data[i];
  return {
    tradeDate: d.tradeDate,
    closePrice: Number(d.closePrice),
    mainNetInflow: Number(d.mainNetInflow) || 0,
    cum: chart1.value.cum[i],
  };
});

const hoverGuideX = computed(() => {
  if (hoverIndex.value == null || !chart1.value) return null;
  return chart1.value.pricePts[hoverIndex.value].x;
});

function onChartMove(event) {
  if (!chart1.value) return;
  const svg = event.currentTarget;
  const rect = svg.getBoundingClientRect();
  const xRatio = (event.clientX - rect.left) / rect.width;
  const viewX = xRatio * CHART1_W;
  const idx = Math.round((viewX - PAD.l) / chart1.value.step);
  const clamped = Math.max(0, Math.min(chart1.value.data.length - 1, idx));
  hoverIndex.value = clamped;
  hoverPos.value = { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

function onChartLeave() {
  hoverIndex.value = null;
}

// ---------- 图表 2：多空四维拆解（当日 / 近5日） ----------
const DIMS = [
  { key: 'superNetInflow', label: '超大单' },
  { key: 'largeNetInflow', label: '大单' },
  { key: 'mediumNetInflow', label: '中单' },
  { key: 'smallNetInflow', label: '小单' },
];

const chart2 = computed(() => {
  const data = fundFlow.value;
  if (!data.length) return null;
  const latest = data[data.length - 1];
  const last5 = data.slice(-5);
  const rows = DIMS.map(d => {
    const today = Number(latest[d.key]) || 0;
    const sum5 = last5.reduce((a, r) => a + (Number(r[d.key]) || 0), 0);
    return { label: d.label, today, sum5 };
  });
  const maxAbs = Math.max(...rows.flatMap(r => [Math.abs(r.today), Math.abs(r.sum5)]), 1e-9);
  return rows.map(r => ({
    ...r,
    todayPct: (Math.abs(r.today) / maxAbs) * 100,
    sum5Pct: (Math.abs(r.sum5) / maxAbs) * 100,
  }));
});

// ---------- 图表 3：龙虎榜席位对抗 ----------
const latestLhb = computed(() => (lhb.value.length ? lhb.value[0] : null));
const seatData = computed(() => {
  const record = latestLhb.value;
  if (!record || !record.seatDetails) return null;
  const buy = (record.seatDetails.buy || []).slice(0, 5);
  const sell = (record.seatDetails.sell || []).slice(0, 5);
  const maxBuy = Math.max(...buy.map(s => Number(s.buy) || 0), 1);
  const maxSell = Math.max(...sell.map(s => Number(s.sell) || 0), 1);
  return {
    record,
    buy: buy.map(s => ({ ...s, pct: ((Number(s.buy) || 0) / maxBuy) * 100 })),
    sell: sell.map(s => ({ ...s, pct: ((Number(s.sell) || 0) / maxSell) * 100 })),
  };
});

const latestSummary = computed(() => {
  const data = fundFlow.value;
  if (!data.length) return null;
  const latest = data[data.length - 1];
  const sum5 = data.slice(-5).reduce((a, r) => a + (Number(r.mainNetInflow) || 0), 0);
  return { latest, sum5 };
});
</script>

<template>
  <el-drawer v-model="visible" :title="`${name || ''}（${code}）详情`" size="72%">
    <div v-loading="loading" class="ff-drawer">
      <!-- 估值指标 -->
      <div v-if="valuationItems.length" class="ff-chart-card">
        <h4>估值指标</h4>
        <div class="valuation-grid">
          <div v-for="item in valuationItems" :key="item.label" class="valuation-item">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
        </div>
      </div>

      <!-- 摘要 -->
      <div v-if="latestSummary" class="ff-summary">
        <div class="ff-kpi">
          <span>最新收盘</span>
          <strong>{{ Number(latestSummary.latest.closePrice).toFixed(2) }}</strong>
        </div>
        <div class="ff-kpi">
          <span>当日主力净流入</span>
          <strong :class="Number(latestSummary.latest.mainNetInflow) >= 0 ? 'up' : 'down'">
            {{ formatAmount(latestSummary.latest.mainNetInflow) }}
          </strong>
        </div>
        <div class="ff-kpi">
          <span>5日累计主力</span>
          <strong :class="latestSummary.sum5 >= 0 ? 'up' : 'down'">{{ formatAmount(latestSummary.sum5) }}</strong>
        </div>
        <div class="ff-kpi" v-if="latestLhb">
          <span>最近龙虎榜</span>
          <strong class="lhb">{{ latestLhb.tradeDate }} {{ latestLhb.reason }}</strong>
        </div>
      </div>

      <!-- 图表 1 -->
      <div class="ff-chart-card">
        <h4>股价涨跌 vs 主力吸筹/出货（近 30 日）</h4>
        <div class="chart1-wrap" @mousemove="onChartMove" @mouseleave="onChartLeave">
          <svg v-if="chart1" :viewBox="`0 0 ${CHART1_W} ${CHART1_H}`" class="ff-svg" preserveAspectRatio="xMidYMid meet">
            <!-- 零轴 -->
            <line :x1="PAD.l" :y1="PAD.t + (CHART1_H - PAD.t - PAD.b) / 2" :x2="CHART1_W - PAD.r" :y2="PAD.t + (CHART1_H - PAD.t - PAD.b) / 2" stroke="#d6dfe8" stroke-width="1" />
            <!-- 主力净流入柱 -->
            <rect
              v-for="(b, i) in chart1.bars"
              :key="'b' + i"
              :x="b.x" :y="b.y" :width="b.w" :height="b.h"
              :fill="b.up ? (i === hoverIndex ? '#ff6b6b' : '#e34d4d') : (i === hoverIndex ? '#2fc77e' : '#1aa261')"
              :stroke="i === hoverIndex ? '#1f3d5c' : 'none'"
              :stroke-width="i === hoverIndex ? 1 : 0"
            />
            <!-- 10日累计线 -->
            <path :d="toPath(chart1.cumPts)" fill="none" stroke="#f59e0b" stroke-width="1.6" />
            <!-- 价格线 -->
            <path :d="toPath(chart1.pricePts)" fill="none" stroke="#3b82f6" stroke-width="2" />
            <!-- 悬停参考线 -->
            <line v-if="hoverGuideX != null" :x1="hoverGuideX" :y1="PAD.t" :x2="hoverGuideX" :y2="CHART1_H - PAD.b" stroke="#8a99ab" stroke-width="1" stroke-dasharray="3,3" />
            <!-- 悬停圆点标记 -->
            <circle v-if="hoverGuideX != null" :cx="hoverGuideX" :cy="chart1.pricePts[hoverIndex].y" r="3" fill="#3b82f6" />
            <circle v-if="hoverGuideX != null" :cx="hoverGuideX" :cy="chart1.cumPts[hoverIndex].y" r="3" fill="#f59e0b" stroke="#fff" stroke-width="1" />
          </svg>
          <div v-if="chartHover" class="chart-tooltip" :style="{ left: hoverPos.x + 'px', top: hoverPos.y + 'px' }">
            <div class="tt-date">{{ chartHover.tradeDate }}</div>
            <div class="tt-row">收盘价 <b>{{ chartHover.closePrice.toFixed(2) }}</b></div>
            <div class="tt-row">主力净流入 <b :class="chartHover.mainNetInflow >= 0 ? 'up' : 'down'">{{ formatAmount(chartHover.mainNetInflow) }}</b></div>
            <div class="tt-row">10日累计 <b :class="chartHover.cum >= 0 ? 'up' : 'down'">{{ formatAmount(chartHover.cum) }}</b></div>
          </div>
        </div>
        <div v-if="chart1" class="ff-legend">
          <span><i class="dot blue"></i>收盘价 {{ chart1.minP.toFixed(2) }}~{{ chart1.maxP.toFixed(2) }}</span>
          <span><i class="dot red"></i>主力净流入（红正绿负）</span>
          <span><i class="dot orange"></i>10日累计</span>
          <span class="muted">{{ chart1.first }} ~ {{ chart1.last }}</span>
        </div>
        <el-empty v-else description="暂无资金流数据，请先在持仓页「同步主力资金」" :image-size="60" />
      </div>

      <!-- 图表 2 -->
      <div class="ff-chart-card" v-if="chart2">
        <h4>多空四维拆解（当日 / 近5日累计净流入）</h4>
        <div class="four-dim">
          <div class="dim-head">
            <span></span><span>当日净流入</span><span>近5日累计</span>
          </div>
          <div v-for="row in chart2" :key="row.label" class="dim-row">
            <span class="dim-label">{{ row.label }}</span>
            <div class="dim-bar">
              <div class="bar-track">
                <div v-if="row.today >= 0" class="bar pos" :style="{ width: row.todayPct + '%' }"></div>
                <div v-else class="bar neg" :style="{ width: row.todayPct + '%' }"></div>
              </div>
              <span :class="row.today >= 0 ? 'up' : 'down'">{{ formatAmount(row.today) }}</span>
            </div>
            <div class="dim-bar">
              <div class="bar-track">
                <div v-if="row.sum5 >= 0" class="bar pos" :style="{ width: row.sum5Pct + '%' }"></div>
                <div v-else class="bar neg" :style="{ width: row.sum5Pct + '%' }"></div>
              </div>
              <span :class="row.sum5 >= 0 ? 'up' : 'down'">{{ formatAmount(row.sum5) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 图表 3 -->
      <div class="ff-chart-card" v-if="seatData">
        <h4>
          龙虎榜席位对抗（{{ seatData.record.tradeDate }}）
          <el-tag v-if="seatData.record.instNetBuy != null" size="small" type="danger" class="inst-tag">机构净买 {{ formatAmount(seatData.record.instNetBuy) }}</el-tag>
        </h4>
        <div class="seat-grid">
          <div class="seat-col">
            <div class="seat-title buy-title">买入前五</div>
            <div v-for="(s, i) in seatData.buy" :key="'b' + i" class="seat-row">
              <span class="seat-rank">B{{ i + 1 }}</span>
              <div class="seat-bar-wrap">
                <div class="seat-bar buy" :style="{ width: s.pct + '%' }"></div>
                <span class="seat-name">{{ s.seat }}</span>
              </div>
              <span class="seat-amt">{{ formatAmount(s.buy) }}</span>
              <el-tag v-if="s.tag !== '游资'" size="small" :type="s.tag === '机构专用' ? 'danger' : 'warning'">{{ s.tag }}</el-tag>
            </div>
          </div>
          <div class="seat-col">
            <div class="seat-title sell-title">卖出前五</div>
            <div v-for="(s, i) in seatData.sell" :key="'s' + i" class="seat-row">
              <span class="seat-rank">S{{ i + 1 }}</span>
              <div class="seat-bar-wrap">
                <div class="seat-bar sell" :style="{ width: s.pct + '%' }"></div>
                <span class="seat-name">{{ s.seat }}</span>
              </div>
              <span class="seat-amt">{{ formatAmount(s.sell) }}</span>
              <el-tag v-if="s.tag !== '游资'" size="small" :type="s.tag === '机构专用' ? 'danger' : 'warning'">{{ s.tag }}</el-tag>
            </div>
          </div>
        </div>
      </div>

      <el-empty v-if="!loading && !fundFlow.length" description="暂无数据" />
    </div>
  </el-drawer>
</template>

<style scoped>
.ff-drawer {
  min-height: 300px;
}
.ff-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.ff-kpi {
  border: 1px solid #dbe6f2;
  border-radius: 8px;
  padding: 10px 12px;
  background: linear-gradient(180deg, #fbfdff, #f5f9fe);
}
.ff-kpi span {
  display: block;
  color: #5a7188;
  font-size: 0.8rem;
  margin-bottom: 4px;
}
.ff-kpi strong {
  color: #22476d;
  font-size: 1.1rem;
}
.ff-kpi strong.lhb {
  font-size: 0.85rem;
  color: #c07822;
}
.up {
  color: #e34d4d;
}
.down {
  color: #1aa261;
}
.ff-chart-card {
  border: 1px solid #d9e2ec;
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 14px;
  background: #fff;
}
.ff-chart-card h4 {
  margin: 0 0 10px;
  color: #2b3f55;
  font-size: 0.98rem;
  display: flex;
  align-items: center;
  gap: 8px;
}
.valuation-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 10px;
}
.valuation-item {
  border: 1px solid #e3ecf5;
  border-radius: 8px;
  padding: 8px 10px;
  background: #f8fbff;
}
.valuation-item span {
  display: block;
  color: #5a7188;
  font-size: 0.78rem;
  margin-bottom: 4px;
}
.valuation-item strong {
  color: #22476d;
  font-size: 1rem;
  font-weight: 700;
}
.inst-tag {
  margin-left: 4px;
}
.ff-svg {
  width: 100%;
  height: auto;
  display: block;
}
.chart1-wrap {
  position: relative;
}
.chart-tooltip {
  position: absolute;
  z-index: 10;
  pointer-events: none;
  transform: translate(14px, -50%);
  background: rgba(17, 24, 39, 0.92);
  color: #eef3f9;
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 0.8rem;
  line-height: 1.55;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.25);
  white-space: nowrap;
}
.chart-tooltip .tt-date {
  font-weight: 700;
  color: #bfd7f5;
  margin-bottom: 2px;
}
.chart-tooltip .tt-row b {
  margin-left: 6px;
  font-weight: 600;
}
.chart-tooltip .up {
  color: #ff8080;
}
.chart-tooltip .down {
  color: #4dd68c;
}
.ff-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 6px;
  font-size: 0.8rem;
  color: #5a7188;
  align-items: center;
}
.ff-legend .dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  margin-right: 4px;
  vertical-align: -1px;
}
.dot.blue {
  background: #3b82f6;
}
.dot.red {
  background: #e34d4d;
}
.dot.orange {
  background: #f59e0b;
}
.muted {
  margin-left: auto;
  color: #9aa9ba;
}
/* 四维拆解 */
.four-dim {
  display: grid;
  gap: 8px;
}
.dim-head,
.dim-row {
  display: grid;
  grid-template-columns: 70px 1fr 1fr;
  gap: 14px;
  align-items: center;
}
.dim-head {
  font-size: 0.78rem;
  color: #8a99ab;
}
.dim-label {
  font-size: 0.86rem;
  color: #33475b;
  font-weight: 600;
}
.dim-bar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.bar-track {
  flex: 1;
  height: 14px;
  background: #f0f4f9;
  border-radius: 3px;
  overflow: hidden;
}
.bar {
  height: 100%;
  border-radius: 3px;
}
.bar.pos {
  background: #e34d4d;
}
.bar.neg {
  background: #1aa261;
}
.dim-bar span {
  font-size: 0.8rem;
  width: 76px;
  text-align: right;
}
/* 席位对抗 */
.seat-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
}
.seat-title {
  font-weight: 700;
  margin-bottom: 8px;
}
.buy-title {
  color: #e34d4d;
}
.sell-title {
  color: #1aa261;
}
.seat-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 7px;
}
.seat-rank {
  font-size: 0.75rem;
  color: #8a99ab;
  width: 20px;
}
.seat-bar-wrap {
  flex: 1;
  position: relative;
  height: 20px;
  background: #f3f6fa;
  border-radius: 4px;
  overflow: hidden;
  min-width: 0;
}
.seat-bar {
  height: 100%;
  border-radius: 4px;
}
.seat-bar.buy {
  background: linear-gradient(90deg, #f0a1a1, #e34d4d);
}
.seat-bar.sell {
  background: linear-gradient(90deg, #8fd3ad, #1aa261);
}
.seat-name {
  position: absolute;
  left: 8px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 0.78rem;
  color: #24374d;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: calc(100% - 16px);
}
.seat-amt {
  font-size: 0.78rem;
  width: 70px;
  text-align: right;
  color: #445a73;
  flex-shrink: 0;
}
</style>

<script setup>
import { computed, inject, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';

const props = defineProps({
  initialCode: { type: String, default: '' },
});

const API = '/api/financials/report';
const LOOKUP_API = '/api/stocks/lookup';

const report = ref(null);
const loading = ref(false);
const keyword = ref('');
const searchOptions = ref([]);
const searchLoading = ref(false);
const viewMode = ref('annual'); // annual | latest
const noteForm = ref({ notes: '', tags: '', rating: '' });
const noteSaving = ref(false);
let noteTimer = null;

const QUICK_PICKS = [
  { code: '600887', name: '伊利股份' },
  { code: '600298', name: '安琪酵母' },
  { code: '603288', name: '海天味业' },
  { code: '601888', name: '中国中免' },
  { code: '002507', name: '涪陵榨菜' },
];

/* ---------------- 数值工具 ---------------- */
const num = v => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};
const fmtYi = v => {
  const n = num(v);
  if (!n) return '—';
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(2)}万`;
  return n.toFixed(2);
};
const fmtPct = (v, d = 2) => (v == null ? '—' : `${num(v).toFixed(d)}%`);
const fmtYiShort = v => {
  const n = num(v);
  if (!n) return '';
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${(n / 1e8).toFixed(0)}亿`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(0)}万`;
  return n.toFixed(0);
};
const sign = v => (num(v) > 0 ? '+' : '');

/* ---------------- 迷你火花线 ---------------- */
function sparkline(values, color) {
  const v = (values || []).map(num);
  if (!v.length) return null;
  const W = 140, H = 28, pad = 3;
  const min = Math.min(...v), max = Math.max(...v);
  const range = (max - min) || 1;
  const n = v.length;
  const px = i => (n === 1 ? W / 2 : pad + (i / (n - 1)) * (W - pad * 2));
  const py = val => pad + (1 - (val - min) / range) * (H - pad * 2);
  const pts = v.map((val, i) => [px(i), py(val)]);
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const area = `${path} L${pts[n - 1][0].toFixed(1)},${H} L${pts[0][0].toFixed(1)},${H} Z`;
  return { W, H, path, area, last: pts[n - 1], color };
}

/* ---------------- 数据加载 ---------------- */
async function loadReport(code) {
  if (!code || !/^\d{6}$/.test(code)) return;
  loading.value = true;
  try {
    const resp = await fetch(`${API}/${code}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data?.detail || `HTTP ${resp.status}`);
    report.value = data;
    keyword.value = `${data.code} ${data.name}`;
    noteForm.value = {
      notes: data.note?.notes || '',
      tags: data.note?.tags || '',
      rating: data.note?.rating || '',
    };
  } catch (e) {
    ElMessage.error(`加载报告失败：${e.message || e}`);
  } finally {
    loading.value = false;
  }
}

async function doSearch(query) {
  if (!query || query.length < 2) return;
  searchLoading.value = true;
  try {
    const resp = await fetch(`${LOOKUP_API}?q=${encodeURIComponent(query)}`);
    const data = await resp.json();
    searchOptions.value = (data.items || []).map(i => ({ value: i.code, label: `${i.code} ${i.name}` }));
  } catch {
    searchOptions.value = [];
  } finally {
    searchLoading.value = false;
  }
}

function onPick(item) {
  const code = String(item || '').split(' ')[0].trim();
  loadReport(code);
}

async function refreshReport() {
  if (!report.value) return;
  const code = report.value.code;
  loading.value = true;
  try {
    const resp = await fetch(`${API}/${code}?refresh=true`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data?.detail);
    report.value = data;
    ElMessage.success('三表已实时重算');
  } catch (e) {
    ElMessage.error(`重算失败：${e.message || e}`);
  } finally {
    loading.value = false;
  }
}

function saveNote() {
  if (noteTimer) clearTimeout(noteTimer);
  noteTimer = setTimeout(async () => {
    if (!report.value) return;
    noteSaving.value = true;
    try {
      await fetch(`/api/financials/notes/${report.value.code}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(noteForm.value),
      });
    } catch {
      /* 忽略保存失败 */
    } finally {
      noteSaving.value = false;
    }
  }, 600);
}

/* ---------------- 派生数据 ---------------- */
const items = computed(() => report.value?.items || []);
const latest = computed(() => items.value[0] || {});
const metrics = computed(() => latest.value.derived_metrics || {});
const annualItems = computed(() => items.value.filter(i => i.report_type === 'annual'));
const activeCode = computed(() => report.value?.code || '');

const statusColor = {
  safe: '#10b981',
  warn: '#f59e0b',
  danger: '#ef4444',
};
const statusLabel = { safe: '安全', warn: '关注', danger: '警惕' };
const statusDot = { safe: '🟢', warn: '🟡', danger: '🔴' };

/* 指标说明（张氏公式 + 评判标准） */
const TIPS = {
  gross_margin: { formula: '毛利率 = (营业收入 − 营业成本) ÷ 营业收入', standard: '>30% 具备初始定价权与竞争力；<15% 多为红海苦生意。' },
  core_profit_margin: { formula: '核心利润 = 营收 − 营业成本 − 税金及附加 − 销售/管理/研发费用 − 利息费用', standard: '核心利润率 >10% 主业真功夫扎实；<5% 利润含金量存疑。' },
  core_profit_to_op_ratio: { formula: '核心利润 ÷ 营业利润（剔除投资/补贴/公允价值等搅局项）', standard: '>80% 主业极其纯粹；<60% 利润靠副业或补贴撑场面。' },
  core_profit_cash_ratio_full: { formula: '经营现金流净额 ÷ (核心利润 + 其他收益)', standard: '≥1.0 真金白银到账；1.2~1.5 为优秀造血；<0.6 纸面富贵警惕赊销。' },
  interest_bearing_debt_ratio: { formula: '有息负债合计 ÷ 总资产', standard: '<30% 安全舒适区；50% 为张氏高危警戒线。' },
  free_cash_flow: { formula: '自由现金流 = 经营现金流净额 − 购建固定/无形资产支付的现金', standard: '持续为正才有分红与再投资底气；长期为负需警惕。' },
};

const kpiCards = computed(() => {
  const m = metrics.value;
  const hist = annualItems.value.slice(0, 5).reverse();
  const seriesOf = field => hist.map(i => num(i.derived_metrics?.[field]));
  const defs = [
    { key: 'gross_margin', label: '毛利率', value: fmtPct(m.gross_margin), series: seriesOf('gross_margin'), status: m.gross_margin >= 30 ? 'safe' : m.gross_margin >= 15 ? 'warn' : 'danger' },
    { key: 'core_profit_margin', label: '核心利润率', value: fmtPct(m.core_profit_margin), series: seriesOf('core_profit_margin'), status: m.core_profit_margin >= 10 ? 'safe' : m.core_profit_margin >= 5 ? 'warn' : 'danger' },
    { key: 'core_profit_to_op_ratio', label: '主业纯粹度', value: m.core_profit_to_op_ratio != null ? `${(m.core_profit_to_op_ratio * 100).toFixed(1)}%` : '—', series: seriesOf('core_profit_to_op_ratio'), status: m.core_profit_to_op_ratio >= 0.75 ? 'safe' : m.core_profit_to_op_ratio >= 0.6 ? 'warn' : 'danger' },
    { key: 'core_profit_cash_ratio_full', label: '核心利润获现率', value: m.core_profit_cash_ratio_full != null ? m.core_profit_cash_ratio_full.toFixed(2) : '—', series: seriesOf('core_profit_cash_ratio_full'), status: m.core_profit_cash_ratio_full >= 1 ? 'safe' : m.core_profit_cash_ratio_full >= 0.6 ? 'warn' : 'danger' },
    { key: 'interest_bearing_debt_ratio', label: '资产有息负债率', value: fmtPct(m.interest_bearing_debt_ratio), series: seriesOf('interest_bearing_debt_ratio'), status: m.interest_bearing_debt_ratio <= 30 ? 'safe' : m.interest_bearing_debt_ratio < 50 ? 'warn' : 'danger' },
    { key: 'free_cash_flow', label: '自由现金流', value: fmtYi(m.free_cash_flow), series: seriesOf('free_cash_flow'), status: m.free_cash_flow > 0 ? 'safe' : 'warn' },
  ];
  return defs.map(d => {
    const tip = TIPS[d.key] || {};
    return { ...d, formula: tip.formula, standard: tip.standard, spark: sparkline(d.series, statusColor[d.status]) };
  });
});

/* 利润表 SVG 组合图：上下双轨物理分区（上方折线轨 14%~42%，下方柱状轨 55%~88%） */
const incomeChart = computed(() => {
  const list = annualItems.value.slice(0, 5).reverse();
  if (!list.length) return null;
  const W = 660, H = 340, PADL = 14, PADR = 42;
  const innerW = W - PADL - PADR;
  const n = list.length;
  const revenues = list.map(i => num(i.revenue));
  const coreProfits = list.map(i => num(i.derived_metrics?.core_profit));
  const gms = list.map(i => num(i.derived_metrics?.gross_margin));
  const cpms = list.map(i => num(i.derived_metrics?.core_profit_margin));
  const maxBar = Math.max(...revenues, ...coreProfits, 1);
  const maxPct = Math.max(...gms, ...cpms, 1);
  const bw = innerW / n * 0.26;
  const x = i => PADL + (i + 0.5) * (innerW / n);

  // 上方折线轨（毛利率/核心利润率）
  const lineTop = H * 0.14, lineBottom = H * 0.42;
  const yPct = v => lineBottom - (v / maxPct) * (lineBottom - lineTop);
  // 下方柱状轨（营收/核心利润）
  const barTop = H * 0.55, barBottom = H * 0.88;
  const yBar = v => barBottom - (v / maxBar) * (barBottom - barTop);

  const bars = list.map((it, i) => {
    const rev = num(it.revenue);
    const cp = num(it.derived_metrics?.core_profit);
    return {
      revX: x(i) - bw, revW: bw, revY: yBar(rev), revH: barBottom - yBar(rev),
      cpX: x(i), cpW: bw, cpY: yBar(cp), cpH: barBottom - yBar(cp),
      label: it.report_label?.slice(0, 4) || '',
      revLabel: fmtYi(rev), cpLabel: fmtYi(cp),
      revLabelY: yBar(rev) - 6, cpLabelY: yBar(cp) - 6,
      revCx: x(i) - bw + bw / 2, cpCx: x(i) + bw / 2,
    };
  });
  const gmPts = gms.map((v, i) => {
    const y = yPct(v);
    return { x: x(i), y, label: v ? `${v.toFixed(1)}%` : '', ly: y - 8 };
  });
  const cpmPts = cpms.map((v, i) => {
    const y = yPct(v);
    const close = Math.abs(y - gmPts[i].y) < 16;
    return { x: x(i), y, label: v ? `${v.toFixed(1)}%` : '', ly: close ? y + 16 : y - 8 };
  });
  const gmPath = gms.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${yPct(v).toFixed(1)}`).join(' ');
  const cpmPath = cpms.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${yPct(v).toFixed(1)}`).join(' ');
  return { W, H, bars, gmPts, cpmPts, gmPath, cpmPath, barBottom };
});

/* 三支柱两搅局 */
const pillarData = computed(() => {
  const m = metrics.value;
  const list = [
    { key: 'p1', label: '支柱一·核心利润', value: num(m.pillar1_core_profit), color: '#3b82f6' },
    { key: 'p2', label: '支柱二·政府补贴', value: num(m.pillar2_subsidy), color: '#06b6d4' },
    { key: 'p3', label: '支柱三·投资/利息杂项', value: num(m.pillar3_misc), color: '#8b5cf6' },
    { key: 'd1', label: '搅局一·资产减值', value: -Math.abs(num(m.disruptor1_asset_impairment)), color: '#f43f5e', danger: true },
    { key: 'd2', label: '搅局二·信用减值', value: -Math.abs(num(m.disruptor2_credit_impairment)), color: '#f97316', danger: true },
  ];
  const total = list.reduce((s, it) => s + Math.abs(it.value), 0);
  return { items: list, total, coreProfit: num(m.core_profit), deduct: num(m.deduct_parent_net_profit) };
});

/* 资产负债表 */
const balanceData = computed(() => {
  const m = metrics.value;
  return {
    upstream: num(m.upstream_occupy),
    downstream: num(m.downstream_occupied),
    net: num(m.net_bargaining_power),
    debtRatio: num(m.interest_bearing_debt_ratio),
    broadCash: num(m.broad_cash),
    operatingAssets: num(m.operating_assets),
    investmentAssets: num(m.investment_assets),
    internalFinancing: num(m.internal_financing),
    externalFinancing: num(m.external_financing),
    internalRatio: num(m.internal_financing_ratio),
    heavyRatio: num(m.heavy_asset_ratio),
  };
});

/* 现金流分组柱（带数值标签，短格式避免水平重叠） */
const cashflowChart = computed(() => {
  const list = annualItems.value.slice(0, 5).reverse();
  if (!list.length) return null;
  const W = 680, H = 300, PADL = 14, PADR = 28;
  const innerW = W - PADL - PADR;
  const n = list.length;
  const values = list.flatMap(i => [num(i.net_operating_cash_flow), num(i.capex_cash_outflow), num(i.derived_metrics?.free_cash_flow)]);
  const maxAbs = Math.max(...values.map(Math.abs), 1);
  const gw = innerW / n * 0.26;
  const x = i => PADL + (i + 0.5) * (innerW / n);
  const top = H * 0.18, bottom = H * 0.86;
  const y = v => bottom - (v / maxAbs) * (bottom - top);
  const groups = list.map((it, i) => {
    const mk = (val, xx, color) => {
      const yy = y(val), hh = bottom - yy;
      return { x: xx, w: gw, y: yy, h: hh, color, label: fmtYiShort(val), labelY: hh >= 0 ? yy - 4 : yy + hh + 14 };
    };
    return {
      ocf: mk(num(it.net_operating_cash_flow), x(i) - gw, '#10b981'),
      capex: mk(num(it.capex_cash_outflow), x(i), '#f59e0b'),
      fcf: mk(num(it.derived_metrics?.free_cash_flow), x(i) + gw, '#38bdf8'),
      label: it.report_label?.slice(0, 4) || '',
      cx: x(i),
    };
  });
  return { W, H, groups, zeroY: bottom };
});

/* 六大雷区 */
const radarRows = computed(() => report.value?.radar_checks || []);

/* 穿透热力表行 */
const heatRows = computed(() => {
  const list = annualItems.value.slice(0, 5);
  return list.map(it => {
    const m = it.derived_metrics || {};
    return {
      label: it.report_label,
      revenue: num(it.revenue),
      gross_margin: num(m.gross_margin),
      core_profit_margin: num(m.core_profit_margin),
      interest_bearing_debt_ratio: num(m.interest_bearing_debt_ratio),
      core_profit_cash_ratio_full: num(m.core_profit_cash_ratio_full),
      net_bargaining_power: num(m.net_bargaining_power),
      free_cash_flow: num(m.free_cash_flow),
    };
  });
});

const healthGauge = computed(() => {
  const score = num(report.value?.health_score);
  const r = 52, c = 2 * Math.PI * r;
  const color = score >= 80 ? '#10b981' : score >= 60 ? '#f59e0b' : '#ef4444';
  return { score, r, c, offset: c * (1 - score / 100), color };
});

onMounted(() => {
  if (props.initialCode) loadReport(props.initialCode);
});
watch(() => props.initialCode, v => {
  if (v) loadReport(v);
});

/* ---- 展示辅助 ---- */
const tugLeftPct = computed(() => {
  const u = balanceData.value.upstream, d = balanceData.value.downstream;
  const t = u + d;
  return t ? (u / t * 100).toFixed(1) + '%' : '50%';
});
const tugRightPct = computed(() => {
  const u = balanceData.value.upstream, d = balanceData.value.downstream;
  const t = u + d;
  return t ? (d / t * 100).toFixed(1) + '%' : '50%';
});
const debtColor = computed(() => {
  const r = balanceData.value.debtRatio;
  return r <= 30 ? '#10b981' : r < 50 ? '#f59e0b' : '#ef4444';
});
const assetPct = computed(() => {
  const b = balanceData.value.broadCash, o = balanceData.value.operatingAssets, i = balanceData.value.investmentAssets;
  const t = b + o + i;
  return {
    broad: t ? (b / t * 100).toFixed(1) + '%' : '0%',
    oper: t ? (o / t * 100).toFixed(1) + '%' : '0%',
    inv: t ? (i / t * 100).toFixed(1) + '%' : '0%',
  };
});
function heatCls(v, good, warn, inverse = false) {
  const n = num(v);
  if (!n) return '';
  const ok = inverse ? n <= good : n >= good;
  const mid = inverse ? n <= warn : n >= warn;
  if (ok) return 'heat-good';
  if (mid) return 'heat-warn';
  return 'heat-bad';
}
</script>

<template>
  <div v-loading="loading" class="report-page">
    <!-- ============ 1. 浅色清爽英雄头 ============ -->
    <section class="hero">
      <div class="hero-top">
        <el-autocomplete
          v-model="keyword"
          :fetch-suggestions="doSearch"
          :trigger-on-focus="false"
          placeholder="输入代码或名称，如 600887 伊利股份"
          class="hero-search"
          @select="onPick"
        >
          <template #default="{ item }"><span>{{ item.label }}</span></template>
        </el-autocomplete>
        <div class="hero-picks">
          <span class="picks-label">快捷</span>
          <button
            v-for="p in QUICK_PICKS"
            :key="p.code"
            class="pick-chip"
            :class="{ active: activeCode === p.code }"
            @click="loadReport(p.code)"
          >
            {{ p.name }}
          </button>
        </div>
        <el-button class="hero-refresh" size="small" type="primary" plain :loading="loading" @click="refreshReport">🔄 实时重算三表</el-button>
      </div>

      <div v-if="report" class="hero-main">
        <div class="hero-left">
          <h1 class="hero-name">{{ report.name }} <span class="hero-code">{{ report.code }}</span></h1>
          <div class="hero-tags">
            <span class="hero-tag tag-driver">{{ report.driver_type || '—' }}</span>
            <span class="hero-tag tag-bargain">{{ report.bargaining_label || '' }}</span>
            <span class="hero-tag tag-source">数据来源: {{ report.source === 'ths' ? '同花顺官方API' : '东方财富F10' }}</span>
          </div>
        </div>
        <div class="hero-right">
          <div class="hero-summary-card">
            <div class="gauge-wrap">
              <svg :viewBox="'0 0 120 120'" class="gauge">
                <circle cx="60" cy="60" :r="healthGauge.r" fill="none" stroke="#e2e8f0" stroke-width="10" />
                <circle cx="60" cy="60" :r="healthGauge.r" fill="none" :stroke="healthGauge.color" stroke-width="10"
                  stroke-linecap="round" :stroke-dasharray="healthGauge.c" :stroke-dashoffset="healthGauge.offset"
                  transform="rotate(-90 60 60)" />
                <text x="60" y="54" text-anchor="middle" class="gauge-score" :fill="healthGauge.color">{{ healthGauge.score }}</text>
                <text x="60" y="74" text-anchor="middle" class="gauge-label" fill="#64748b">健康分</text>
              </svg>
            </div>
            <div class="hero-summary">
              <div class="summary-title">一句话商业透视</div>
              <p class="summary-text">{{ report.executive_summary || '—' }}</p>
            </div>
          </div>
        </div>
      </div>
      <div v-else-if="!loading" class="hero-empty">输入股票代码开始透视财报</div>
    </section>

    <!-- ============ 2. 六维 KPI ============ -->
    <section v-if="report" class="kpi-strip">
      <div v-for="card in kpiCards" :key="card.key" class="kpi-card" :style="{ borderTopColor: statusColor[card.status] }">
        <div class="kpi-head">
          <span class="kpi-label">
            {{ card.label }}
            <el-tooltip teleported effect="light" placement="top" :show-after="80">
              <template #content>
                <div class="tip-box">
                  <div class="tip-row"><b>计算公式</b><p>{{ card.formula }}</p></div>
                  <div class="tip-row"><b>张氏评判</b><p>{{ card.standard }}</p></div>
                </div>
              </template>
              <span class="q-badge">?</span>
            </el-tooltip>
          </span>
          <span class="kpi-badge" :style="{ color: statusColor[card.status], background: statusColor[card.status] + '1a' }">
            {{ statusLabel[card.status] }}
          </span>
        </div>
        <div class="kpi-value">{{ card.value }}</div>
        <svg v-if="card.spark" :viewBox="`0 0 ${card.spark.W} ${card.spark.H}`" class="kpi-spark" preserveAspectRatio="none">
          <defs>
            <linearGradient :id="'spark-' + card.key" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" :stop-color="card.spark.color" stop-opacity="0.28" />
              <stop offset="100%" :stop-color="card.spark.color" stop-opacity="0" />
            </linearGradient>
          </defs>
          <path :d="card.spark.area" :fill="'url(#spark-' + card.key + ')'" />
          <path :d="card.spark.path" fill="none" :stroke="card.spark.color" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
          <circle :cx="card.spark.last[0]" :cy="card.spark.last[1]" r="2.6" :fill="card.spark.color" />
        </svg>
      </div>
    </section>

    <!-- ============ 3. 双栏图表第一行 ============ -->
    <section v-if="report" class="bento-row">
      <!-- 利润表透视 -->
      <div class="bento-card">
        <h3 class="card-title">利润表「面子」与「三支柱、两搅局」</h3>
        <svg v-if="incomeChart" :viewBox="`0 0 ${incomeChart.W} ${incomeChart.H}`" class="chart-svg">
          <line :x1="14" :y1="incomeChart.barBottom" :x2="incomeChart.W - 42" :y2="incomeChart.barBottom" stroke="#cbd5e1" stroke-width="1" />
          <g v-for="(b, i) in incomeChart.bars" :key="'r' + i">
            <rect :x="b.revX" :y="b.revY" :width="b.revW" :height="b.revH" fill="#3b82f6" opacity="0.8" rx="2" />
            <rect :x="b.cpX" :y="b.cpY" :width="b.cpW" :height="b.cpH" fill="#06b6d4" opacity="0.9" rx="2" />
            <text :x="b.revCx" :y="b.revLabelY" text-anchor="middle" class="bar-val" fill="#2563eb">{{ b.revLabel }}</text>
            <text :x="b.cpCx" :y="b.cpLabelY" text-anchor="middle" class="bar-val" fill="#0891b2">{{ b.cpLabel }}</text>
            <text :x="b.revCx" :y="incomeChart.H - 8" text-anchor="middle" class="axis-label" fill="#64748b">{{ b.label }}</text>
          </g>
          <path :d="incomeChart.gmPath" fill="none" stroke="#f59e0b" stroke-width="2" />
          <path :d="incomeChart.cpmPath" fill="none" stroke="#e34d4d" stroke-width="2" />
          <g v-for="(p, i) in incomeChart.gmPts" :key="'gm' + i">
            <circle :cx="p.x" :cy="p.y" r="3" fill="#f59e0b" />
            <text :x="p.x" :y="p.ly" text-anchor="middle" class="line-val" fill="#d97706">{{ p.label }}</text>
          </g>
          <g v-for="(p, i) in incomeChart.cpmPts" :key="'cpm' + i">
            <circle :cx="p.x" :cy="p.y" r="3" fill="#e34d4d" />
            <text :x="p.x" :y="p.ly" text-anchor="middle" class="line-val" fill="#dc2626">{{ p.label }}</text>
          </g>
        </svg>
        <div class="chart-legend">
          <span><i class="lg blue"></i>营业收入</span>
          <span><i class="lg cyan"></i>核心利润</span>
          <span><i class="lg orange"></i>毛利率</span>
          <span><i class="lg red"></i>核心利润率</span>
        </div>
        <!-- 三支柱两搅局 -->
        <div class="pillar-block">
          <div class="sub-title">
            「三支柱 vs 两搅局」拆解
            <el-tooltip teleported effect="light" placement="top" :show-after="80">
              <template #content>
                <div class="tip-box">
                  <div class="tip-row"><b>计算公式</b><p>营业利润 = 核心利润(支柱一) + 政府补贴(支柱二) + 投资/利息杂项(支柱三) − 资产减值(搅局一) − 信用减值(搅局二)</p></div>
                  <div class="tip-row"><b>张氏评判</b><p>核心利润占比越高主业越实；减值损失持续放大是利润地雷前兆。</p></div>
                </div>
              </template>
              <span class="q-badge">?</span>
            </el-tooltip>
          </div>
          <div class="pillar-bar">
            <div v-for="p in pillarData.items" :key="p.key" class="pillar-seg"
              :style="{ width: pillarData.total ? (Math.abs(p.value) / pillarData.total * 100) + '%' : '0%', background: p.color, opacity: p.danger ? 0.85 : 1 }">
            </div>
          </div>
          <div class="pillar-legend">
            <span v-for="p in pillarData.items" :key="p.key" class="pillar-item">
              <i :style="{ background: p.color }"></i>{{ p.label }} {{ fmtYi(p.value) }}
            </span>
          </div>
          <div class="pillar-compare">
            核心利润 <b>{{ fmtYi(pillarData.coreProfit) }}</b> vs 扣非净利润 <b>{{ fmtYi(pillarData.deduct) }}</b>
          </div>
        </div>
      </div>

      <!-- 资产负债表透视 -->
      <div class="bento-card">
        <h3 class="card-title">资产负债表「战略底牌」与「两头吃」话语权</h3>
        <!-- 两头吃拔河 -->
        <div class="tug-title">
          产业链「两头吃」拔河天平
          <el-tooltip teleported effect="light" placement="top" :show-after="80">
            <template #content>
              <div class="tip-box">
                <div class="tip-row"><b>计算公式</b><p>无息占用上下游 = 应付票据账款 + 合同负债 + 预收款项；被下游占款 = 应收票据及应收账款</p></div>
                <div class="tip-row"><b>张氏评判</b><p>净占款 >0 说明占用上下游资金无息经营，产业链话语权强。</p></div>
              </div>
            </template>
            <span class="q-badge">?</span>
          </el-tooltip>
        </div>
        <div class="tug-info">
          <span class="tug-info-left">◀ 无息占用上下游 <b>{{ fmtYi(balanceData.upstream) }}</b> ({{ tugLeftPct }})</span>
          <span class="tug-info-net" :class="balanceData.net > 0 ? 'net-good' : 'net-bad'">
            <template v-if="balanceData.net > 0">净占款优势 +{{ fmtYi(balanceData.net) }}</template>
            <template v-else>净被占款 {{ fmtYi(-balanceData.net) }}</template>
          </span>
          <span class="tug-info-right">被下游占款 <b>{{ fmtYi(balanceData.downstream) }}</b> ({{ tugRightPct }}) ▶</span>
        </div>
        <div class="tug-bar">
          <div class="tug-left-seg" :style="{ width: tugLeftPct }"></div>
          <div class="tug-right-seg" :style="{ width: tugRightPct }"></div>
        </div>
        <!-- 有息负债率刻度尺 -->
        <div class="debt-title">
          资产有息负债率 · 张氏警戒刻度
          <el-tooltip teleported effect="light" placement="top" :show-after="80">
            <template #content>
              <div class="tip-box">
                <div class="tip-row"><b>计算公式</b><p>有息负债合计 = 短期借款 + 一年内到期非流动负债 + 长期借款 + 应付债券 + 租赁负债</p></div>
                <div class="tip-row"><b>张氏评判</b><p>30% 为安全线，50% 为张氏高危警戒线。</p></div>
              </div>
            </template>
            <span class="q-badge">?</span>
          </el-tooltip>
        </div>
        <div class="debt-ruler">
          <div class="debt-track">
            <div class="debt-fill" :style="{ width: Math.min(balanceData.debtRatio, 60) / 60 * 100 + '%', background: debtColor }"></div>
            <div class="debt-safe" style="left: 50%"></div>
            <div class="debt-danger" style="left: 83.3%"></div>
            <div class="debt-cursor" :style="{ left: Math.min(balanceData.debtRatio, 60) / 60 * 100 + '%' }"></div>
          </div>
          <div class="debt-scale"><span>0%</span><span>30% 安全线</span><span>50% 高危线</span></div>
          <div class="debt-value">当前 <b :style="{ color: debtColor }">{{ fmtPct(balanceData.debtRatio, 1) }}</b></div>
        </div>
        <!-- 资产轻重与驱动水源 -->
        <div class="stack-row">
          <div class="stack-block">
            <div class="stack-title">资产结构（类现金 / 经营 / 投资并购）</div>
            <div class="stack-bar">
              <div class="stack-seg cash" :style="{ width: assetPct.broad }"></div>
              <div class="stack-seg oper" :style="{ width: assetPct.oper }"></div>
              <div class="stack-seg inv" :style="{ width: assetPct.inv }"></div>
            </div>
            <div class="stack-legend"><span><i class="cash"></i>类现金 {{ fmtYi(balanceData.broadCash) }}</span><span><i class="oper"></i>经营 {{ fmtYi(balanceData.operatingAssets) }}</span><span><i class="inv"></i>投资并购 {{ fmtYi(balanceData.investmentAssets) }}</span></div>
          </div>
          <div class="stack-block">
            <div class="stack-title">驱动水源（造血 {{ fmtPct(balanceData.internalRatio, 0) }} / 输血）</div>
            <div class="stack-bar">
              <div class="stack-seg intern" :style="{ width: balanceData.internalRatio + '%' }"></div>
              <div class="stack-seg extern" :style="{ width: (100 - balanceData.internalRatio) + '%' }"></div>
            </div>
            <div class="stack-legend"><span><i class="intern"></i>内部造血 {{ fmtYi(balanceData.internalFinancing) }}</span><span><i class="extern"></i>外部输血 {{ fmtYi(balanceData.externalFinancing) }}</span></div>
          </div>
        </div>
      </div>
    </section>

    <!-- ============ 4. 双栏图表第二行 ============ -->
    <section v-if="report" class="bento-row">
      <!-- 现金流脉搏 -->
      <div class="bento-card">
        <h3 class="card-title">现金流量表「造血、用血与输血」</h3>
        <svg v-if="cashflowChart" :viewBox="`0 0 ${cashflowChart.W} ${cashflowChart.H}`" class="chart-svg">
          <line :x1="14" :y1="cashflowChart.zeroY" :x2="cashflowChart.W - 28" :y2="cashflowChart.zeroY" stroke="#cbd5e1" stroke-width="1" />
          <g v-for="(g, i) in cashflowChart.groups" :key="i">
            <rect :x="g.ocf.x" :y="g.ocf.y" :width="g.ocf.w" :height="Math.abs(g.ocf.h) || 0.5" fill="#10b981" rx="2" />
            <rect :x="g.capex.x" :y="g.capex.y" :width="g.capex.w" :height="Math.abs(g.capex.h) || 0.5" fill="#f59e0b" rx="2" />
            <rect :x="g.fcf.x" :y="g.fcf.y" :width="g.fcf.w" :height="Math.abs(g.fcf.h) || 0.5" fill="#38bdf8" rx="2" />
            <text :x="g.ocf.x + g.ocf.w / 2" :y="g.ocf.labelY" text-anchor="middle" class="bar-val" :fill="g.ocf.color">{{ g.ocf.label }}</text>
            <text :x="g.capex.x + g.capex.w / 2" :y="g.capex.labelY" text-anchor="middle" class="bar-val" :fill="g.capex.color">{{ g.capex.label }}</text>
            <text :x="g.fcf.x + g.fcf.w / 2" :y="g.fcf.labelY" text-anchor="middle" class="bar-val" :fill="g.fcf.color">{{ g.fcf.label }}</text>
            <text :x="g.cx" :y="cashflowChart.H - 10" text-anchor="middle" class="axis-label" fill="#64748b">{{ g.label }}</text>
          </g>
        </svg>
        <div class="chart-legend">
          <span><i class="lg green"></i>经营现金流净额（造血）</span>
          <span><i class="lg amber"></i>Capex（用血）</span>
          <span><i class="lg sky"></i>自由现金流</span>
        </div>
        <div class="pulse-cards">
          <div class="pulse-card">
            <div class="pulse-label">获现率（含补贴）</div>
            <div class="pulse-value">{{ metrics.core_profit_cash_ratio_full != null ? metrics.core_profit_cash_ratio_full.toFixed(2) : '—' }}</div>
          </div>
          <div class="pulse-card">
            <div class="pulse-label">获现率（纯主业）</div>
            <div class="pulse-value">{{ metrics.core_profit_cash_ratio_pure != null ? metrics.core_profit_cash_ratio_pure.toFixed(2) : '—' }}</div>
          </div>
          <div class="pulse-card">
            <div class="pulse-label">重资产占比</div>
            <div class="pulse-value">{{ fmtPct(balanceData.heavyRatio, 1) }}</div>
          </div>
        </div>
      </div>

      <!-- 六大雷区 -->
      <div class="bento-card">
        <h3 class="card-title">张新民三表联动「六大雷区扫描仪」</h3>
        <div class="radar-list">
          <div v-for="r in radarRows" :key="r.id" class="radar-row" :class="'radar-' + r.status">
            <div class="radar-head">
              <span class="radar-emoji">{{ statusDot[r.status] }}</span>
              <span class="radar-title">{{ r.title }}</span>
            </div>
            <div class="radar-metric" v-if="r.metric_value">{{ r.metric_value }}</div>
            <div class="radar-desc">{{ r.description }}</div>
          </div>
        </div>
      </div>
    </section>

    <!-- ============ 5. 穿透热力表 + 复盘笔记 ============ -->
    <section v-if="report" class="bento-row bottom-row">
      <div class="bento-card">
        <h3 class="card-title">多期三表精华穿透热力表</h3>
        <el-table :data="heatRows" size="small" border>
          <el-table-column prop="label" label="报告期" width="96" />
          <el-table-column label="营业收入" align="right"><template #default="s">{{ fmtYi(s.row.revenue) }}</template></el-table-column>
          <el-table-column label="毛利率" align="right"><template #default="s"><span :class="heatCls(s.row.gross_margin, 30, 15)">{{ fmtPct(s.row.gross_margin) }}</span></template></el-table-column>
          <el-table-column label="核心利润率" align="right"><template #default="s"><span :class="heatCls(s.row.core_profit_margin, 10, 5)">{{ fmtPct(s.row.core_profit_margin) }}</span></template></el-table-column>
          <el-table-column label="有息负债率" align="right"><template #default="s"><span :class="heatCls(s.row.interest_bearing_debt_ratio, 50, 30, true)">{{ fmtPct(s.row.interest_bearing_debt_ratio) }}</span></template></el-table-column>
          <el-table-column label="获现率" align="right"><template #default="s"><span :class="heatCls(s.row.core_profit_cash_ratio_full, 1, 0.6)">{{ s.row.core_profit_cash_ratio_full ? s.row.core_profit_cash_ratio_full.toFixed(2) : '—' }}</span></template></el-table-column>
          <el-table-column label="净占款" align="right"><template #default="s">{{ fmtYi(s.row.net_bargaining_power) }}</template></el-table-column>
          <el-table-column label="自由现金流" align="right"><template #default="s">{{ fmtYi(s.row.free_cash_flow) }}</template></el-table-column>
        </el-table>
      </div>

      <div class="bento-card note-card">
        <h3 class="card-title">个人排雷复盘笔记</h3>
        <el-input v-model="noteForm.tags" placeholder="标签，如：现金奶牛 / 等待拐点 / 排除项" size="small" @input="saveNote" />
        <el-input v-model="noteForm.rating" placeholder="评级，如：A / B+ / 回避" size="small" class="note-gap" @input="saveNote" />
        <el-input v-model="noteForm.notes" type="textarea" :rows="6" placeholder="写下你的读财报心得…" class="note-gap" @input="saveNote" />
        <div class="note-saving">{{ noteSaving ? '保存中…' : '已自动保存到 NAS' }}</div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.report-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 4px;
  color: #0f172a;
}

/* ---- 英雄头（浅色清爽） ---- */
.hero {
  border-radius: 16px;
  background: linear-gradient(135deg, #EFF6FF 0%, #F8FAFC 55%, #ECFDF5 100%);
  border: 1px solid #DBEAFE;
  padding: 18px 20px;
  box-shadow: 0 4px 20px rgba(59, 130, 246, 0.06);
  color: #0f172a;
}
.hero-top { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.hero-search { width: 340px; flex-shrink: 0; }
.hero-search :deep(.el-input__wrapper) { background: #fff; box-shadow: 0 0 0 1px #BFDBFE inset; }
.hero-picks { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.picks-label { font-size: 0.8rem; color: #64748b; }
.pick-chip {
  border: 1px solid #BFDBFE;
  background: #FFFFFF;
  color: #334155;
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.15s;
}
.pick-chip:hover { border-color: #2563EB; color: #2563EB; background: #EFF6FF; }
.pick-chip.active { background: #2563EB; border-color: #2563EB; color: #fff; font-weight: 600; }
.hero-refresh { margin-left: auto; }
.hero-main { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-top: 16px; }
.hero-name { font-size: 1.85rem; font-weight: 800; margin: 0; color: #0F172A; letter-spacing: -0.02em; }
.hero-code { font-size: 0.9rem; color: #64748b; font-weight: 500; margin-left: 6px; }
.hero-tags { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.hero-tag { font-size: 0.78rem; padding: 3px 10px; border-radius: 999px; font-weight: 600; }
.tag-driver { background: #DBEAFE; color: #1D4ED8; }
.tag-bargain { background: #DCFCE7; color: #15803D; }
.tag-source { background: #F1F5F9; color: #475569; }
.hero-right { display: flex; align-items: center; }
.hero-summary-card {
  display: flex; align-items: center; gap: 18px;
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid #E2E8F0;
  border-radius: 14px;
  padding: 14px 18px;
  box-shadow: 0 2px 10px rgba(15, 23, 42, 0.04);
}
.gauge-wrap { flex-shrink: 0; }
.gauge { width: 104px; height: 104px; }
.gauge-score { font-size: 28px; font-weight: 800; }
.gauge-label { font-size: 11px; }
.hero-summary { max-width: 380px; }
.summary-title { font-size: 0.8rem; color: #64748b; margin-bottom: 6px; }
.summary-text { margin: 0; font-size: 14px; line-height: 1.55; color: #334155; }
.hero-empty { margin-top: 14px; color: #64748b; }

/* ---- KPI ---- */
.kpi-strip { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }
.kpi-card {
  background: #fff;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  border-top: 3px solid #3b82f6;
  padding: 14px;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06);
  transition: transform 0.15s, box-shadow 0.15s;
  position: relative;
  overflow: hidden;
}
.kpi-card:hover { transform: translateY(-3px); box-shadow: 0 6px 16px rgba(15, 23, 42, 0.1); }
.kpi-head { display: flex; justify-content: space-between; align-items: flex-start; }
.kpi-label { font-size: 0.8rem; color: #64748b; display: inline-flex; align-items: center; gap: 5px; }
.kpi-badge { font-size: 0.72rem; padding: 1px 8px; border-radius: 999px; font-weight: 600; flex-shrink: 0; }
.kpi-value { font-size: 1.4rem; font-weight: 800; margin-top: 8px; font-variant-numeric: tabular-nums; }
.kpi-spark { width: 100%; height: 28px; margin-top: 10px; display: block; }

/* 问号徽章 */
.q-badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px;
  border-radius: 5px;
  background: #EFF6FF; color: #3B82F6;
  font-size: 0.72rem; font-weight: 700;
  cursor: pointer; line-height: 1;
  transition: all 0.15s; flex-shrink: 0;
}
.q-badge:hover { background: #3B82F6; color: #fff; }

/* ---- Bento 卡 ---- */
.bento-row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.bento-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 16px 18px; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05); }
.card-title { margin: 0 0 12px; font-size: 0.95rem; font-weight: 700; color: #1e293b; }
.sub-title { display: flex; align-items: center; gap: 6px; font-size: 0.84rem; font-weight: 600; color: #475569; margin-bottom: 8px; }
.chart-svg { width: 100%; height: auto; }
.chart-legend { display: flex; gap: 14px; font-size: 0.78rem; color: #64748b; margin-top: 6px; flex-wrap: wrap; }
.chart-legend .lg, .stack-legend i, .pillar-item i { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 4px; vertical-align: middle; }
.lg.blue { background: #3b82f6; } .lg.cyan { background: #06b6d4; } .lg.orange { background: #f59e0b; }
.lg.red { background: #e34d4d; } .lg.green { background: #10b981; } .lg.amber { background: #f59e0b; } .lg.sky { background: #38bdf8; }
.axis-label { font-size: 10px; }
.bar-val { font-size: 10px; font-weight: 600; paint-order: stroke; stroke: #ffffff; stroke-width: 3px; stroke-linejoin: round; }
.line-val { font-size: 10px; font-weight: 700; paint-order: stroke; stroke: #ffffff; stroke-width: 3px; stroke-linejoin: round; }

/* 三支柱 */
.pillar-block { margin-top: 14px; }
.pillar-bar { display: flex; height: 22px; border-radius: 6px; overflow: hidden; background: #f1f5f9; }
.pillar-seg { height: 100%; }
.pillar-legend { display: flex; gap: 14px; font-size: 0.76rem; color: #64748b; margin-top: 8px; flex-wrap: wrap; }
.pillar-compare { margin-top: 10px; font-size: 0.82rem; color: #475569; }

/* 两头吃（文字外置到进度条上方 + 纯比例条，永不裁切） */
.tug-title, .debt-title { display: flex; align-items: center; gap: 6px; font-size: 0.82rem; font-weight: 600; color: #475569; margin: 14px 0 8px; }
.tug-info { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 6px; }
.tug-info-left { color: #065f46; font-size: 0.82rem; }
.tug-info-left b { font-weight: 800; }
.tug-info-right { color: #9a3412; font-size: 0.82rem; }
.tug-info-right b { font-weight: 800; }
.tug-info-net { font-size: 0.82rem; font-weight: 800; padding: 3px 12px; border-radius: 999px; white-space: nowrap; }
.tug-info-net.net-good { background: #DCFCE7; color: #15803D; }
.tug-info-net.net-bad { background: #FFEDD5; color: #C2410C; }
.tug-bar { display: flex; height: 14px; border-radius: 7px; overflow: hidden; background: #fff; }
.tug-left-seg { background: #34d399; height: 100%; }
.tug-right-seg { background: #fb923c; height: 100%; clip-path: polygon(7px 0, 100% 0, 100% 100%, 0 100%); }

/* 有息负债率刻度尺 */
.debt-ruler { padding: 4px 2px; }
.debt-track { position: relative; height: 12px; border-radius: 6px; background: #e2e8f0; }
.debt-fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 6px; transition: width 0.4s; }
.debt-safe, .debt-danger { position: absolute; top: -3px; bottom: -3px; width: 2px; background: #64748b; }
.debt-danger { background: #ef4444; }
.debt-cursor { position: absolute; top: -4px; width: 10px; height: 10px; border-radius: 50%; background: #0f172a; border: 2px solid #fff; transform: translateX(-50%); transition: left 0.4s; }
.debt-scale { display: flex; justify-content: space-between; font-size: 0.7rem; color: #94a3b8; margin-top: 6px; }
.debt-value { margin-top: 6px; font-size: 0.82rem; color: #475569; }

/* 资产结构 */
.stack-row { display: flex; flex-direction: column; gap: 14px; margin-top: 14px; }
.stack-title { font-size: 0.78rem; color: #64748b; margin-bottom: 6px; }
.stack-bar { display: flex; height: 14px; border-radius: 7px; overflow: hidden; background: #f1f5f9; }
.stack-seg { height: 100%; }
.stack-seg.cash { background: #3b82f6; } .stack-seg.oper { background: #10b981; } .stack-seg.inv { background: #8b5cf6; }
.stack-seg.intern { background: #10b981; } .stack-seg.extern { background: #f59e0b; }
.stack-legend { display: flex; gap: 14px; font-size: 0.76rem; color: #64748b; margin-top: 6px; flex-wrap: wrap; }
.stack-legend i.cash { background: #3b82f6; } .stack-legend i.oper { background: #10b981; } .stack-legend i.inv { background: #8b5cf6; }
.stack-legend i.intern { background: #10b981; } .stack-legend i.extern { background: #f59e0b; }

/* 现金流脉搏 */
.pulse-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 12px; }
.pulse-card { background: #f8fafc; border-radius: 10px; padding: 12px; text-align: center; }
.pulse-label { font-size: 0.76rem; color: #64748b; }
.pulse-value { font-size: 1.15rem; font-weight: 800; margin-top: 4px; font-variant-numeric: tabular-nums; }

/* 六大雷区（2列×3行） */
.radar-list { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.radar-row { display: flex; flex-direction: column; gap: 4px; padding: 12px; border-radius: 10px; background: #f8fafc; border-left: 3px solid #e2e8f0; }
.radar-row.radar-safe { border-left-color: #10b981; }
.radar-row.radar-warn { border-left-color: #f59e0b; }
.radar-row.radar-danger { border-left-color: #ef4444; }
.radar-head { display: flex; align-items: center; gap: 7px; }
.radar-emoji { font-size: 0.95rem; line-height: 1; }
.radar-title { font-size: 0.84rem; font-weight: 700; color: #1e293b; }
.radar-metric {
  align-self: flex-start;
  font-size: 0.74rem; font-weight: 600; color: #2563EB;
  background: #EFF6FF; border: 1px solid #BFDBFE;
  padding: 1px 8px; border-radius: 999px; margin-top: 2px;
  font-variant-numeric: tabular-nums;
}
.radar-desc { font-size: 0.79rem; color: #64748b; line-height: 1.45; }

/* 底部 */
.bottom-row { grid-template-columns: 1.6fr 1fr; }
.note-card { display: flex; flex-direction: column; gap: 8px; }
.note-gap { margin-top: 6px; }
.note-saving { font-size: 0.76rem; color: #94a3b8; }

/* 热力表 */
:deep(.heat-good) { color: #047857; font-weight: 600; }
:deep(.heat-warn) { color: #b45309; font-weight: 600; }
:deep(.heat-bad) { color: #dc2626; font-weight: 600; }

@media (max-width: 1100px) {
  .kpi-strip { grid-template-columns: repeat(3, 1fr); }
  .bento-row { grid-template-columns: 1fr; }
  .bottom-row { grid-template-columns: 1fr; }
  .radar-list { grid-template-columns: 1fr; }
}
</style>

<style>
/* Tooltip 内容（teleported 到 body，需全局作用域） */
.tip-box { max-width: 280px; }
.tip-row { margin-bottom: 8px; }
.tip-row:last-child { margin-bottom: 0; }
.tip-row b { display: block; font-size: 12px; color: #1e293b; margin-bottom: 3px; }
.tip-row p { margin: 0; font-size: 12px; color: #475569; line-height: 1.5; }
</style>

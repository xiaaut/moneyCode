<script setup>
import { computed, onMounted, ref, watch } from 'vue';
const emit = defineEmits(['add-watchlist']);

const currentEps = ref(0.0);
const targetEps = ref(0.0);
const currentPe = ref(0.0);
const historicalAvgPe = ref(0.0);
const meanPe = ref(0.0);
const holdYears = ref(3);
const epsYears = 3;
const stockName = ref('安琪酵母');
const stockCode = ref('600298');

const portfolioCount = ref(12);
const maxIndustryCount = ref(4);

// ---------- 策略配置持久化（NAS PostgreSQL，无缝自动保存，不改 UI / 公式 / 交互） ----------
const STRATEGY_CONFIG_API = '/api/strategy-config';
let loaded = false; // 首次加载完成前不触发自动保存
let saveTimer = null;

function buildConfigPayload() {
  return {
    stockName: String(stockName.value ?? ''),
    stockCode: String(stockCode.value ?? ''),
    currentEps: Number(currentEps.value) || 0,
    targetEps: Number(targetEps.value) || 0,
    currentPe: Number(currentPe.value) || 0,
    historicalAvgPe: Number(historicalAvgPe.value) || 0,
    meanPe: Number(meanPe.value) || 0,
    holdYears: Number(holdYears.value) || 0,
    portfolioCount: Math.round(Number(portfolioCount.value) || 0),
    maxIndustryCount: Math.round(Number(maxIndustryCount.value) || 0),
  };
}

async function loadConfig() {
  try {
    const resp = await fetch(STRATEGY_CONFIG_API);
    if (!resp.ok) return;
    const data = await resp.json();
    // 后端为空时返回默认值，无条件应用即可（与前端默认值一致）
    stockName.value = data.stockName ?? '安琪酵母';
    stockCode.value = data.stockCode ?? '600298';
    currentEps.value = Number(data.currentEps ?? 0);
    targetEps.value = Number(data.targetEps ?? 0);
    currentPe.value = Number(data.currentPe ?? 0);
    historicalAvgPe.value = Number(data.historicalAvgPe ?? 0);
    meanPe.value = Number(data.meanPe ?? 0);
    holdYears.value = Number(data.holdYears ?? 3);
    portfolioCount.value = Number(data.portfolioCount ?? 12);
    maxIndustryCount.value = Number(data.maxIndustryCount ?? 4);
  } catch {
    // 读取失败时保留默认值
  } finally {
    loaded = true;
  }
}

async function saveConfig() {
  try {
    await fetch(STRATEGY_CONFIG_API, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildConfigPayload()),
    });
  } catch {
    // 保存失败静默忽略，不打断用户
  }
}

function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(saveConfig, 600);
}

watch(
  [stockName, stockCode, currentEps, targetEps, currentPe, historicalAvgPe, meanPe, holdYears, portfolioCount, maxIndustryCount],
  () => {
    if (loaded) scheduleSave();
  },
);

onMounted(loadConfig);

const gDecimal = computed(() => {
  const cur = Number(currentEps.value);
  const tgt = Number(targetEps.value);
  if (!(cur > 0) || !(tgt > 0)) return 0;
  return Math.pow(tgt / cur, 1 / epsYears) - 1;
});

const gPercent = computed(() => gDecimal.value * 100);

const peg = computed(() => {
  const g = gPercent.value;
  if (!(g > 0)) return 0;
  return Number(currentPe.value) / g;
});

const growthFactor = computed(() => {
  const pe1 = Number(currentPe.value);
  const pe2 = Number(meanPe.value);
  const n = Number(holdYears.value);
  const g = gDecimal.value;
  if (!(pe1 > 0) || !(pe2 > 0) || !(n > 0) || !(g > -1)) return 0;
  return (pe2 / pe1) * Math.pow(1 + g, n);
});

const annualizedReturn = computed(() => {
  // 年化收益率 = (PE2 / PE1)^(1/n) * (1 + g) - 1
  // 其中 g 为年化EPS增速
  const pe1 = Number(currentPe.value);
  const pe2 = Number(meanPe.value);
  const n = Number(holdYears.value);
  const g = gDecimal.value;
  if (!(pe1 > 0) || !(pe2 > 0) || !(n > 0) || !(g > -1)) return 0;
  return Math.pow(pe2 / pe1, 1 / n) * (1 + g) - 1;
});

const totalReturn = computed(() => {
  const n = Number(holdYears.value);
  if (!(n > 0)) return 0;
  if (!(growthFactor.value > 0)) return 0;
  return growthFactor.value - 1;
});

const conditions = computed(() => [
  { label: `g > 10%（按EPS复合增速，${epsYears}年）`, pass: gPercent.value > 10 },
  { label: 'PEG < 1.2', pass: peg.value < 1.2 && peg.value > 0 },
  { label: `目标年化收益率 > 35%`, pass: annualizedReturn.value > 0.35 },
  { label: '当前市盈率 <= 历史均值PE', pass: Number(currentPe.value) <= Number(historicalAvgPe.value) },
  { label: '组合中股票数量 <= 20', pass: Number(portfolioCount.value) <= 20 },
  { label: '单行业最大股票数量 <= 5', pass: Number(maxIndustryCount.value) <= 5 },
]);

const passCount = computed(() => conditions.value.filter(item => item.pass).length);
const allPassed = computed(() => passCount.value === conditions.value.length);

const toPercent = value => `${(value * 100).toFixed(2)}%`;

const conditionDetails = computed(() => [
  `g = ${targetEps.value > 0 && currentEps.value > 0 ? `(${Number(targetEps.value).toFixed(2)}/${Number(currentEps.value).toFixed(2)})^(1/${epsYears})-1` : '-'} = ${gPercent.value.toFixed(2)}%`,
  `PEG = 当前PE / g = ${Number(currentPe.value).toFixed(2)} / ${gPercent.value.toFixed(2)} = ${peg.value.toFixed(2)}`,
  `年化收益率 = (均值PE/当前PE)^(1/${holdYears.value}) × (1+g) - 1 = ${toPercent(annualizedReturn.value)}`,
  `${Number(currentPe.value).toFixed(2)} <= ${Number(historicalAvgPe.value).toFixed(2)}`,

  `当前组合数量：${portfolioCount.value}`,
  `单行业数量：${maxIndustryCount.value}`,
]);

function resetBasicInputs() {
  currentEps.value = 0;
  targetEps.value = 0;
  currentPe.value = 0;
  historicalAvgPe.value = 0;
  meanPe.value = 0;
  holdYears.value = 3;
}

function resetPositionInputs() {
  portfolioCount.value = 12;
  maxIndustryCount.value = 4;
}

function handleAddWatchlist() {
  const snapshot = {
    id: Date.now(),
    stockName: String(stockName.value || '').trim() || '未命名股票',
    stockCode: String(stockCode.value || '').trim() || '-',
    createdAt: new Date().toISOString(),
    currentEps: Number(currentEps.value),
    targetEps: Number(targetEps.value),
    currentPe: Number(currentPe.value),
    historicalAvgPe: Number(historicalAvgPe.value),
    meanPe: Number(meanPe.value),
    holdYears: Number(holdYears.value),
    gPercent: Number(gPercent.value.toFixed(2)),
    peg: Number(peg.value.toFixed(2)),
    annualizedReturnPercent: Number((annualizedReturn.value * 100).toFixed(2)),
    passCount: passCount.value,
    totalConditions: conditions.value.length,
    allPassed: allPassed.value,
    details: conditionDetails.value,
  };
  emit('add-watchlist', snapshot);
}
</script>

<template>
  <div class="strategy-page">
    <header class="topbar">
      <div class="brand">Davis 双因子选股策略器</div>
      <div class="search">
        <span class="icon">Q</span>
        <input v-model.trim="stockName" type="text" placeholder="请输入股票名称，例如：安琪酵母" />
      </div>
      <div class="search">
        <span class="icon">#</span>
        <input v-model.trim="stockCode" type="text" placeholder="请输入股票代码，例如：600298" />
      </div>
      <div class="top-action">↪</div>
    </header>

    <main class="layout">
      <section class="left-pane">
        <article class="card">
          <h2>数据输入与风控参数</h2>

          <div class="calc-grid">
            <div class="field-group calc-card">
              <h3>g 计算（EPS复合增速）</h3>
              <div class="field-row">
                <label>当前 EPS</label>
                <input v-model.number="currentEps" type="number" min="0.001" step="0.001" />
              </div>
              <div class="field-row">
                <label>目标 EPS</label>
                <input v-model.number="targetEps" type="number" min="0.001" step="0.001" />
              </div>
              <div class="formula-row">
                g = (目标EPS / 当前EPS)<sup>1/{{ epsYears }}</sup> - 1
              </div>
              <div class="result-box">
                <span>当前 g</span>
                <strong>{{ gPercent.toFixed(2) }}%</strong>
              </div>
            </div>

            <div class="field-group calc-card">
              <h3>PEG 计算</h3>
              <div class="field-row">
                <label>当前 PE</label>
                <input v-model.number="currentPe" type="number" min="0.01" step="0.01" />
              </div>
              <div class="field-row">
                <label>历史均值 PE</label>
                <input v-model.number="historicalAvgPe" type="number" min="0.01" step="0.01" />
              </div>
              <div class="formula-row">PEG = 当前PE / g（g 为百分比数值）</div>
              <div class="result-box">
                <span>当前 PEG</span>
                <strong>{{ peg.toFixed(2) }}</strong>
              </div>
            </div>
          </div>

          <div class="field-group">
            <h3>均值回归收益率</h3>
            <div class="field-row">
              <label>均值 PE</label>
              <input v-model.number="meanPe" type="number" min="0.01" step="0.01" />
            </div>
            <div class="field-row">
              <label>预计持有年数 n</label>
              <input v-model.number="holdYears" type="number" min="1" step="1" />
            </div>
            <div class="formula-row">年化收益率 = (均值PE / 当前PE)<sup>1/n</sup> × (1 + g) - 1</div>
            <div class="result-box">
              <span>年化收益率</span>
              <strong>{{ toPercent(annualizedReturn) }}</strong>
            </div>
          </div>

          <div class="btn-row">
            <button class="btn primary">刷新估值数据</button>
            <button class="btn ghost" @click="resetBasicInputs">重置</button>
          </div>

          <p class="hint">年化收益率：{{ toPercent(annualizedReturn) }}（按当前参数实时计算）</p>
        </article>

        <article class="card">
          <h2>组合约束</h2>

          <div class="field-row">
            <label>组合股票数量</label>
            <input v-model.number="portfolioCount" type="number" min="1" step="1" />
          </div>

          <div class="field-row">
            <label>单行业股票数量</label>
            <input v-model.number="maxIndustryCount" type="number" min="1" step="1" />
          </div>

          <div class="btn-row">
            <button class="btn primary">刷新持仓数据</button>
            <button class="btn ghost" @click="resetPositionInputs">重置</button>
          </div>
        </article>
      </section>

      <section class="right-pane">
        <article class="card">
          <h2>策略清单与评估</h2>
          <div class="check-grid">
            <div v-for="(item, index) in conditions" :key="item.label" class="check-item" :class="{ pass: item.pass, fail: !item.pass }">
              <div>
                <p class="title">{{ index + 1 }}. {{ item.label }}</p>
                <p class="desc">{{ conditionDetails[index] }}</p>
              </div>
              <span class="status">{{ item.pass ? '✓' : '!' }}</span>
            </div>
          </div>
        </article>

        <article class="final-card" :class="{ ok: allPassed, warn: !allPassed }">
          <h3>最终建议</h3>
          <div class="final-content">
            <div class="mark">{{ allPassed ? '✓' : '!' }}</div>
            <div>
              <p class="big">{{ allPassed ? '买入信号：6项条件全部满足' : `暂缓买入：当前满足 ${passCount}/6` }}</p>
              <p class="sub">PEG：{{ peg.toFixed(2) }} | 预估年化收益率：{{ toPercent(annualizedReturn) }}</p>
              <div class="btn-row">
                <button class="btn light" @click="handleAddWatchlist">加入观察列表</button>
                <button class="btn primary">生成策略报告</button>
              </div>
            </div>
          </div>
        </article>
      </section>
    </main>

    <footer class="footer">Footer @ Daris - Licenonc</footer>
  </div>
</template>

<style scoped>
.strategy-page {
  min-height: 100vh;
  padding: 14px;
  color: #1e2b3b;
  font-family: 'PingFang SC', 'Microsoft YaHei', 'Segoe UI', sans-serif;
  background: radial-gradient(circle at 98% 96%, rgba(93, 155, 255, 0.16), transparent 25%), radial-gradient(circle at 3% 3%, rgba(175, 211, 255, 0.3), transparent 24%), #edf1f6;
}

.topbar {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  align-items: center;
  border-radius: 10px;
  padding: 12px 14px;
  color: #fff;
  background: linear-gradient(90deg, #1f5ca8 0%, #2f82d5 52%, #4ca0ee 100%);
  box-shadow: 0 8px 24px rgba(35, 96, 172, 0.24);
}

.brand {
  font-size: 1.12rem;
  font-weight: 600;
}

.search {
  position: relative;
}

.search input {
  width: 100%;
  border: 0;
  border-radius: 6px;
  padding: 8px 10px 8px 30px;
  color: #2b3d51;
  background: rgba(255, 255, 255, 0.92);
}

.icon {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 0.85rem;
  opacity: 0.55;
}

.top-action {
  width: 32px;
  height: 32px;
  border: 1px solid rgba(255, 255, 255, 0.5);
  border-radius: 8px;
  display: grid;
  place-items: center;
}

.layout {
  display: grid;
  gap: 12px;
  margin-top: 12px;
}

.left-pane,
.right-pane {
  display: grid;
  gap: 12px;
  align-content: start;
}

.card {
  border: 1px solid #d7e0ea;
  border-radius: 10px;
  padding: 14px;
  background: #f9fbfd;
  box-shadow: 0 4px 14px rgba(60, 87, 116, 0.08);
}

h2 {
  margin-bottom: 10px;
  font-size: 1.08rem;
  font-weight: 600;
}

.field-group {
  margin-bottom: 14px;
  padding: 10px;
  border: 1px solid #d8e3ef;
  border-radius: 8px;
  background: #ffffff;
}

.calc-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}

.calc-card {
  margin-bottom: 0;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
}

.field-group h3 {
  margin: 0 0 10px;
  font-size: 0.96rem;
  color: #315171;
}

.field-row {
  margin-bottom: 10px;
}

.field-row label {
  display: block;
  margin-bottom: 6px;
  font-size: 0.95rem;
}

.field-row input {
  width: 100%;
  border: 1px solid #c4d0de;
  border-radius: 6px;
  padding: 8px 9px;
  color: #1e2b3b;
  background: #fff;
}

.formula-row {
  margin-top: 4px;
  padding: 7px 8px;
  border-radius: 6px;
  font-size: 0.88rem;
  color: #36516b;
  background: #eef5fc;
}

.result-box {
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid #d4e2f1;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #284664;
  background: #fcfeff;
}

.result-box strong {
  font-size: 1.05rem;
  color: #1d446d;
}

.btn-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.btn {
  border: 0;
  border-radius: 6px;
  padding: 8px 14px;
  cursor: pointer;
  font-size: 0.93rem;
}

.btn.primary {
  color: #fff;
  background: linear-gradient(180deg, #4f9be8, #2e78cc);
}

.btn.ghost {
  border: 1px solid #ccd7e4;
  color: #24374d;
  background: #fff;
}

.btn.light {
  color: #173753;
  background: #e6f5ea;
}

.hint {
  margin-top: 8px;
  color: #587086;
  font-size: 0.9rem;
}

.check-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}

.check-item {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fff;
}

.title {
  margin-bottom: 2px;
  font-weight: 600;
}

.desc {
  color: #52667d;
  font-size: 0.93rem;
}

.status {
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  font-size: 0.92rem;
  font-weight: 700;
}

.pass .status {
  background: #1eb866;
}

.fail .status {
  background: #d17057;
}

.final-card {
  border-radius: 10px;
  padding: 14px;
  color: #f4fff7;
  box-shadow: 0 4px 14px rgba(60, 87, 116, 0.08);
}

.final-card.ok {
  background: linear-gradient(140deg, #0aa14f, #1abf62 56%, #6ad877);
}

.final-card.warn {
  background: linear-gradient(140deg, #c07822, #d99a2d 58%, #e8b44f);
}

.final-card h3 {
  margin-bottom: 10px;
  font-size: 1.05rem;
  font-weight: 600;
}

.final-content {
  display: flex;
  gap: 12px;
}

.mark {
  min-width: 54px;
  font-size: 2.45rem;
  line-height: 1;
  font-weight: 800;
}

.big {
  font-size: 1.36rem;
  line-height: 1.2;
  font-weight: 700;
}

.sub {
  margin: 8px 0 10px;
  font-size: 0.96rem;
  color: rgba(255, 255, 255, 0.96);
}

.footer {
  margin-top: 14px;
  text-align: center;
  color: #607083;
  font-size: 0.9rem;
}

@media (min-width: 920px) {
  .topbar {
    grid-template-columns: auto 1fr auto;
    gap: 14px;
  }

  .layout {
    grid-template-columns: 1fr 1.35fr;
  }

  .check-grid {
    grid-template-columns: 1fr 1fr;
  }
}

@media (min-width: 1100px) {
  .calc-grid {
    grid-template-columns: 1fr 1fr;
  }
}
</style>

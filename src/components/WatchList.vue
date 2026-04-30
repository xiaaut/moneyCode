<script setup>
import { computed } from 'vue';

const props = defineProps({
  items: {
    type: Array,
    default: () => [],
  },
});

const emit = defineEmits(['remove-item', 'clear-all']);

const sortedItems = computed(() => {
  return [...props.items].sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
});

const formatPercent = value => {
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(2)}%` : '-';
};

const formatNum = value => {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : '-';
};

const formatDateTime = value => {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '-';
  return d.toLocaleString('zh-CN', { hour12: false });
};
</script>

<template>
  <div class="watchlist-page">
    <div class="header-row">
      <h3>观察列表</h3>
      <el-button v-if="sortedItems.length" type="danger" plain @click="emit('clear-all')">清空列表</el-button>
    </div>

    <el-empty v-if="!sortedItems.length" description="暂无观察项，请在选股策略页点击“加入观察列表”" />

    <div v-else class="cards">
      <article v-for="item in sortedItems" :key="item.id" class="watch-card">
        <div class="card-head">
          <div>
            <h4>{{ item.stockName || '未命名股票' }}（{{ item.stockCode || '-' }}）</h4>
            <p>添加时间：{{ formatDateTime(item.createdAt) }}</p>
          </div>
          <el-button link type="danger" @click="emit('remove-item', item.id)">删除</el-button>
        </div>

        <div class="kpis">
          <div class="kpi">
            <span>当前 g</span>
            <strong>{{ formatPercent(item.gPercent) }}</strong>
          </div>
          <div class="kpi">
            <span>PEG</span>
            <strong>{{ formatNum(item.peg) }}</strong>
          </div>
          <div class="kpi">
            <span>年化收益率</span>
            <strong>{{ formatPercent(item.annualizedReturnPercent) }}</strong>
          </div>
          <div class="kpi">
            <span>策略满足度</span>
            <strong>{{ item.passCount }}/{{ item.totalConditions }}</strong>
          </div>
        </div>

        <div class="inputs">
          <span>EPS：{{ formatNum(item.currentEps) }} → {{ formatNum(item.targetEps) }}</span>
          <span>PE：当前 {{ formatNum(item.currentPe) }}，历史均值 {{ formatNum(item.historicalAvgPe) }}，均值PE {{ formatNum(item.meanPe) }}</span>
          <span>持有年数：{{ item.holdYears || '-' }}</span>
        </div>

        <ul class="details" v-if="Array.isArray(item.details) && item.details.length">
          <li v-for="(detail, idx) in item.details" :key="`${item.id}-${idx}`">{{ idx + 1 }}. {{ detail }}</li>
        </ul>
      </article>
    </div>
  </div>
</template>

<style scoped>
.watchlist-page {
  background: #fff;
  border: 1px solid #d9e2ec;
  border-radius: 10px;
  padding: 14px;
}

.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.header-row h3 {
  margin: 0;
  color: #2b3f55;
}

.cards {
  display: grid;
  gap: 12px;
}

.watch-card {
  border: 1px solid #d7e0ea;
  border-radius: 10px;
  padding: 12px;
  background: linear-gradient(180deg, #fbfdff, #f6f9fd);
}

.card-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

.card-head h4 {
  margin: 0;
  font-size: 1rem;
  color: #21384f;
}

.card-head p {
  margin: 4px 0 0;
  color: #5a7188;
  font-size: 0.86rem;
}

.kpis {
  margin-top: 10px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 8px;
}

.kpi {
  border: 1px solid #dbe6f2;
  border-radius: 8px;
  padding: 8px;
  background: #fff;
}

.kpi span {
  display: block;
  color: #5a7188;
  font-size: 0.82rem;
}

.kpi strong {
  color: #22476d;
  font-size: 1.05rem;
}

.inputs {
  margin-top: 10px;
  display: grid;
  gap: 4px;
  color: #3e5872;
  font-size: 0.88rem;
}

.details {
  margin: 10px 0 0;
  padding-left: 18px;
  color: #4f667e;
  font-size: 0.86rem;
}

.details li {
  margin: 3px 0;
}
</style>

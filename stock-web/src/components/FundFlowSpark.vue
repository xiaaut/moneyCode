<script setup>
import { computed } from 'vue';

const props = defineProps({
  values: { type: Array, default: () => [] },
  sum: { type: Number, default: 0 },
  compact: { type: Boolean, default: false },
});

const bars = computed(() => {
  const vals = (props.values || []).map(Number).filter(Number.isFinite);
  if (!vals.length) return [];
  const maxAbs = Math.max(...vals.map(v => Math.abs(v)), 1e-9);
  const H = 26;
  return vals.map(v => {
    const h = Math.max((Math.abs(v) / maxAbs) * (H / 2 - 2), 1.5);
    return { up: v >= 0, height: h };
  });
});

const BAR_W = 8;
const GAP = 3;

function formatAmount(n) {
  const v = Number(n) || 0;
  const abs = Math.abs(v);
  const sign = v >= 0 ? '+' : '-';
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(1)}万`;
  return `${sign}${abs.toFixed(0)}`;
}

const sumText = computed(() => formatAmount(props.sum));
const sumClass = computed(() => {
  const v = Number(props.sum) || 0;
  if (v === 0) return 'flat';
  return v > 0 ? 'up' : 'down';
});
</script>

<template>
  <div class="spark" :class="{ compact: props.compact }" v-if="bars.length">
    <svg :width="bars.length * (BAR_W + GAP)" :height="props.compact ? 20 : 28" viewBox="0 0 60 28" preserveAspectRatio="none">
      <line x1="0" y1="14" x2="60" y2="14" stroke="#c6d2e0" stroke-width="0.5" />
      <rect
        v-for="(b, i) in bars"
        :key="i"
        :x="i * 11 + 1"
        :y="b.up ? 14 - b.height : 14"
        :width="BAR_W"
        :height="b.height"
        :fill="b.up ? '#e34d4d' : '#1aa261'"
        rx="1"
      />
    </svg>
    <div class="sum" :class="sumClass">{{ props.compact ? sumText : `5日 ${sumText}` }}</div>
  </div>
  <div v-else class="spark-empty" :class="{ compact: props.compact }">-</div>
</template>

<style scoped>
.spark {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  line-height: 1;
}
.sum {
  font-size: 11px;
  white-space: nowrap;
}
/* 紧凑模式：给窄列用 */
.spark.compact {
  gap: 2px;
  line-height: 1.1;
}
.spark.compact .sum {
  font-size: 10px;
  letter-spacing: -0.2px;
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
.spark-empty {
  color: #b0bcc9;
  text-align: center;
}
/* 无数据时占位高度对齐紧凑图，保证行高一致 */
.spark-empty.compact {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 32px;
  line-height: 1;
}
</style>

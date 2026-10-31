<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';

const loading = ref(false);
const parsing = ref(false);
const saving = ref(false);

const msci = ref({ fileName: '', importedAt: null, updatedAt: null, analyzedAt: null, columns: [], rows: [] });

const importDialogVisible = ref(false);
const importFile = ref(null);
const parseResult = ref(null);

const analyzeJob = ref({ running: false, phase: 'idle', label: '', done: 0, total: 0, message: '', error: '' });
const polling = ref(false);
let pollTimer = null;

const viewMode = ref('group');
const groupLevel = ref('l3'); // l3 = 三级细分行业；l2 = 二级中类行业
const industryFilter = ref('all');
const keyword = ref('');

const rows = computed(() => msci.value.rows || []);

/** 按行业分组 + 组内 PE/ROE 排名（低 PE = 1 分，高 ROE = 1 分；无效值按组内最差处理） */
const groupKeyOf = row => {
  const parts = String(row.industry || '')
    .split('-')
    .filter(Boolean);
  if (groupLevel.value === 'l2' && parts.length >= 2) return parts[parts.length - 2];
  if (row.industryGroup) return row.industryGroup;
  return parts.length ? parts[parts.length - 1] : '未分类';
};

const rankedGroups = computed(() => {
  const groups = new Map();
  for (const row of rows.value) {
    const group = groupKeyOf(row);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(row);
  }

  const result = [];
  for (const [group, list] of groups) {
    const members = list.map(row => ({ ...row }));
    const size = members.length;

    const peValid = members
      .filter(member => Number(member.peTtm) > 0)
      .sort((a, b) => Number(a.peTtm) - Number(b.peTtm) || String(a.code).localeCompare(String(b.code)));
    const roeValid = members
      .filter(member => Number(member.roe) > 0)
      .sort((a, b) => Number(b.roe) - Number(a.roe) || String(a.code).localeCompare(String(b.code)));

    const peRank = new Map();
    peValid.forEach((member, index) => peRank.set(member.code, index + 1));
    const roeRank = new Map();
    roeValid.forEach((member, index) => roeRank.set(member.code, index + 1));

    for (const member of members) {
      member._peValid = Number(member.peTtm) > 0;
      member._roeValid = Number(member.roe) > 0;
      member._peRank = peRank.has(member.code) ? peRank.get(member.code) : size;
      member._roeRank = roeRank.has(member.code) ? roeRank.get(member.code) : size;
      member._sum = member._peRank + member._roeRank;
      member._group = group;
      member._groupSize = size;
    }

    members.sort(
      (a, b) =>
        a._sum - b._sum ||
        (a._peValid === b._peValid ? 0 : a._peValid ? -1 : 1) ||
        (Number(a.peTtm) || 0) - (Number(b.peTtm) || 0) ||
        String(a.code).localeCompare(String(b.code)),
    );
    members.forEach((member, index) => {
      member._position = index + 1;
    });

    result.push({ group, size, members, bestSum: members[0]?._sum ?? 999 });
  }

  result.sort((a, b) => b.size - a.size || a.group.localeCompare(b.group, 'zh'));
  result.forEach((item, index) => {
    item.colorIndex = index % 8;
  });
  return result;
});

watch(groupLevel, () => {
  industryFilter.value = 'all';
});

const industryOptions = computed(() =>
  rankedGroups.value.map(item => ({ value: item.group, label: `${item.group}（${item.size}只）` })),
);

const filteredGroups = computed(() => {
  let groups = rankedGroups.value;
  if (industryFilter.value !== 'all') groups = groups.filter(item => item.group === industryFilter.value);

  const key = keyword.value.trim();
  if (!key) return groups;

  return groups
    .map(item => ({
      ...item,
      members: item.members.filter(member =>
        [member.code, member.name, member.industryGroup].filter(Boolean).some(text => String(text).includes(key)),
      ),
    }))
    .filter(item => item.members.length);
});

const displayRows = computed(() => {
  if (viewMode.value === 'flat') {
    const all = filteredGroups.value.flatMap(item => item.members.map(member => ({ ...member })));
    all.sort(
      (a, b) =>
        a._sum - b._sum ||
        a._peRank - b._peRank ||
        String(a.code).localeCompare(String(b.code)),
    );
    return all.map((member, index) => ({
      ...member,
      _groupFirst: false,
      _span: 1,
      _color: -1,
      _displayIndustry: member.industryGroup || '未分类',
      _displayPosition: index + 1,
    }));
  }

  const output = [];
  for (const item of filteredGroups.value) {
    item.members.forEach((member, index) => {
      output.push({
        ...member,
        _groupFirst: index === 0,
        _span: index === 0 ? item.members.length : 0,
        _color: item.colorIndex,
        _displayIndustry: index === 0 ? `${item.group}（${item.members.length}只）` : '',
        _displayPosition: member._position,
      });
    });
  }
  return output;
});

const stats = computed(() => {
  const all = rows.value;
  const industryCount = rankedGroups.value.length;
  const validPe = all.filter(row => Number(row.peTtm) > 0).length;
  const validRoe = all.filter(row => Number(row.roe) > 0).length;
  return { total: all.length, industryCount, validPe, validRoe };
});

const progressPercent = computed(() => {
  const job = analyzeJob.value;
  if (!job.running) return 0;
  if (job.phase === 'industry') return 10;
  if (job.phase === 'quotes') return 35;
  if (job.phase === 'roe') {
    return job.total ? 35 + Math.round((job.done / job.total) * 55) : 60;
  }
  if (job.phase === 'save') return 95;
  return 5;
});

function formatDateTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString('zh-CN', { hour12: false });
}

function formatPrice(value) {
  const num = Number(value);
  return Number.isFinite(num) && num > 0 ? num.toFixed(2) : '-';
}

function formatPercent(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '-';
  return `${num > 0 ? '+' : ''}${num.toFixed(2)}%`;
}

function changeClass(value) {
  const num = Number(value);
  if (!Number.isFinite(num) || num === 0) return 'flat';
  return num > 0 ? 'up' : 'down';
}

function peText(row) {
  const num = Number(row.peTtm);
  if (!Number.isFinite(num)) return '无';
  if (num <= 0) return '亏损';
  return num.toFixed(2);
}

function roeText(row) {
  const num = Number(row.roe);
  return Number.isFinite(num) ? `${num.toFixed(2)}%` : '-';
}

function spanMethod({ row, columnIndex }) {
  if (viewMode.value !== 'group') return [1, 1];
  if (columnIndex === 3) return row._groupFirst ? [row._span, 1] : [0, 0];
  return [1, 1];
}

function rowClassName({ row }) {
  if (viewMode.value !== 'group' || row._color == null || row._color < 0) return '';
  return `msci-tint-${row._color}${row._groupFirst ? ' msci-group-first' : ''}`;
}

async function loadData() {
  loading.value = true;
  try {
    const resp = await fetch('/api/msci');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    msci.value = {
      fileName: data?.fileName || '',
      importedAt: data?.importedAt || null,
      updatedAt: data?.updatedAt || null,
      analyzedAt: data?.analyzedAt || null,
      columns: Array.isArray(data?.columns) ? data.columns : [],
      rows: Array.isArray(data?.rows) ? data.rows : [],
    };
    if (data?.job?.running) {
      analyzeJob.value = data.job;
      startPolling();
    }
  } catch (error) {
    ElMessage.error(`读取 MSCI 数据失败：${error.message || '请稍后重试'}`);
  } finally {
    loading.value = false;
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || '');
      const base64 = result.includes(',') ? result.split(',')[1] : result;
      resolve(base64);
    };
    reader.onerror = () => reject(new Error('文件读取失败'));
    reader.readAsDataURL(file);
  });
}

async function handleFileChange(uploadFile) {
  const file = uploadFile?.raw;
  if (!file) return;
  if (file.size > 20 * 1024 * 1024) {
    ElMessage.warning('文件不能超过 20MB');
    return;
  }

  parsing.value = true;
  importFile.value = file;
  try {
    const dataBase64 = await fileToBase64(file);
    const resp = await fetch('/api/msci/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fileName: file.name, dataBase64 }),
    });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(json?.message || `解析失败：HTTP ${resp.status}`);
    parseResult.value = json;
    importDialogVisible.value = true;
  } catch (error) {
    ElMessage.error(error.message || '文件解析失败');
  } finally {
    parsing.value = false;
  }
}

async function confirmImport() {
  if (!parseResult.value) return;
  saving.value = true;
  try {
    const resp = await fetch('/api/msci', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        fileName: importFile.value?.name || parseResult.value.fileName || '',
        importedAt: new Date().toISOString(),
        columns: parseResult.value.columns || [],
        rows: parseResult.value.rows || [],
      }),
    });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(json?.message || `保存失败：HTTP ${resp.status}`);

    importDialogVisible.value = false;
    await loadData();
    ElMessage.success(`已导入 ${parseResult.value.total} 只，开始联网获取 PE / ROE…`);
    await startAnalyze();
  } catch (error) {
    ElMessage.error(error.message || '导入失败');
  } finally {
    saving.value = false;
  }
}

async function startAnalyze() {
  if (analyzeJob.value.running) return;
  try {
    const resp = await fetch('/api/msci/analyze', { method: 'POST' });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(json?.message || `HTTP ${resp.status}`);
    if (json?.job) analyzeJob.value = json.job;
    analyzeJob.value.running = true;
    startPolling();
  } catch (error) {
    ElMessage.error(`启动分析失败：${error.message || '请稍后重试'}`);
  }
}

async function loadProgress() {
  try {
    const resp = await fetch('/api/msci/progress');
    if (!resp.ok) return;
    const data = await resp.json();
    if (data?.job) analyzeJob.value = data.job;
  } catch {
    // 忽略轮询错误
  }
}

function startPolling() {
  if (polling.value) return;
  polling.value = true;
  pollTimer = setInterval(async () => {
    await loadProgress();
    if (!analyzeJob.value.running) {
      stopPolling();
      await loadData();
      if (analyzeJob.value.phase === 'error') ElMessage.error(analyzeJob.value.error || '分析失败');
      else ElMessage.success(analyzeJob.value.message || '分析完成');
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

async function clearData() {
  try {
    await ElMessageBox.confirm('确定清空已导入的 MSCI 数据吗？（不会删除你的原始文件）', '清空确认', {
      confirmButtonText: '清空',
      cancelButtonText: '取消',
      type: 'warning',
    });
    const resp = await fetch('/api/msci', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fileName: '', importedAt: null, updatedAt: null, analyzedAt: null, columns: [], rows: [] }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    await loadData();
    ElMessage.success('已清空');
  } catch {
    // 取消
  }
}

onMounted(loadData);
onBeforeUnmount(stopPolling);
</script>

<template>
  <div class="msci-page">
    <div class="page-head">
      <div>
        <h3>MSCI 数据</h3>
        <p class="meta">
          <template v-if="rows.length">
            共 {{ stats.total }} 只 · {{ stats.industryCount }} 个行业组 ·
            导入：{{ formatDateTime(msci.importedAt) }} ·
            分析：{{ formatDateTime(msci.analyzedAt) }}
          </template>
          <template v-else>尚未导入数据，请上传 MSCI 成份股清单（TXT / Excel）</template>
        </p>
      </div>
      <div class="toolbar">
        <el-upload :auto-upload="false" :show-file-list="false" accept=".txt,.csv,.xlsx,.xls,.xlsm" :on-change="handleFileChange">
          <el-button type="primary" :loading="parsing">导入文件（TXT / Excel）</el-button>
        </el-upload>
        <el-button type="success" :loading="analyzeJob.running" @click="startAnalyze">重新获取 PE / ROE</el-button>
        <el-button v-if="rows.length" type="danger" plain @click="clearData">清空</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="排名规则：按行业分组（可选三级细分 / 二级中类）；组内低 PE = 1 分，高 ROE = 1 分，两项名次相加，合计越小越靠前（同行业同底色）"
      description="PE / ROE 为负或缺失（如亏损股）时按组内最差名次计分，避免亏损股排到第一；同行业使用相同背景色，便于区分；分组粒度支持三级细分行业（如白酒）与二级中类行业（如饮料）；数据来源：腾讯行情（PE TTM）+ 东方财富数据中心（ROE，最新定期报告）+ 东方财富行业分类。"
      class="notice"
    />

    <div v-if="analyzeJob.running" class="progress-box">
      <el-progress :percentage="progressPercent" :stroke-width="12" striped striped-flow />
      <p class="progress-text">
        <strong>{{ analyzeJob.label }}</strong>
        <span v-if="analyzeJob.message"> · {{ analyzeJob.message }}</span>
      </p>
    </div>

    <el-alert
      v-if="analyzeJob.phase === 'error'"
      type="error"
      :closable="false"
      show-icon
      :title="`上次分析失败：${analyzeJob.error}`"
      class="notice"
    />

    <div class="stats-row">
      <div class="stat-card">
        <span>股票数量</span>
        <strong>{{ stats.total }}</strong>
      </div>
      <div class="stat-card">
        <span>行业组数</span>
        <strong>{{ stats.industryCount }}</strong>
      </div>
      <div class="stat-card">
        <span>PE 有效</span>
        <strong>{{ stats.validPe }}</strong>
      </div>
      <div class="stat-card">
        <span>ROE 有效</span>
        <strong>{{ stats.validRoe }}</strong>
      </div>
      <div class="stat-card">
        <span>分析时间</span>
        <strong class="small">{{ formatDateTime(msci.analyzedAt) }}</strong>
      </div>
    </div>

    <div class="filter-row">
      <el-radio-group v-model="viewMode">
        <el-radio-button value="group">按行业分组</el-radio-button>
        <el-radio-button value="flat">全局排序</el-radio-button>
      </el-radio-group>
      <el-radio-group v-model="groupLevel">
        <el-radio-button value="l3">三级行业（细分）</el-radio-button>
        <el-radio-button value="l2">二级行业（中类）</el-radio-button>
      </el-radio-group>
      <el-select v-model="industryFilter" style="width: 210px" filterable>
        <el-option value="all" label="全部行业" />
        <el-option v-for="option in industryOptions" :key="option.value" :label="option.label" :value="option.value" />
      </el-select>
      <el-input v-model="keyword" placeholder="按代码 / 名称搜索" clearable style="width: 220px" />
      <span class="result-count">共 {{ displayRows.length }} 条</span>
    </div>

    <el-table
      v-loading="loading"
      :data="displayRows"
      border
      size="small"
      style="width: 100%"
      max-height="640"
      :span-method="spanMethod"
      :row-class-name="rowClassName"
      empty-text="还没有数据，点击右上角「导入文件」上传 MSCI 清单"
    >
      <el-table-column type="index" label="#" width="55" align="center" />
      <el-table-column prop="code" label="股票代码" width="95" align="center" />
      <el-table-column prop="name" label="股票名称" width="110" align="center" />
      <el-table-column label="行业分组" width="170" align="center" show-overflow-tooltip>
        <template #default="scope">
          <strong v-if="viewMode === 'group'" class="group-name">{{ scope.row._displayIndustry }}</strong>
          <span v-else>{{ scope.row.industryGroup || '未分类' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="现价" width="90" align="right">
        <template #default="scope">{{ formatPrice(scope.row.price) }}</template>
      </el-table-column>
      <el-table-column label="涨跌幅" width="95" align="right">
        <template #default="scope">
          <span :class="changeClass(scope.row.changePct)">{{ formatPercent(scope.row.changePct) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="PE (TTM)" width="100" align="right" sortable :sort-by="row => Number(row.peTtm) || 0">
        <template #default="scope">
          <span :class="scope.row._peValid ? '' : 'invalid'">{{ peText(scope.row) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="ROE" width="95" align="right" sortable :sort-by="row => Number(row.roe) || 0">
        <template #default="scope">
          <span :class="scope.row._roeValid ? '' : 'invalid'">{{ roeText(scope.row) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="PE 组内排名" width="115" align="center">
        <template #default="scope">{{ scope.row._peRank }}</template>
      </el-table-column>
      <el-table-column label="ROE 组内排名" width="120" align="center">
        <template #default="scope">{{ scope.row._roeRank }}</template>
      </el-table-column>
      <el-table-column label="合计分" width="90" align="center" sortable :sort-by="row => row._sum">
        <template #default="scope">
          <strong>{{ scope.row._sum }}</strong>
        </template>
      </el-table-column>
      <el-table-column label="组内排名" width="95" align="center">
        <template #default="scope">{{ scope.row._displayPosition }}</template>
      </el-table-column>
      <el-table-column label="ROE 报告期" width="105" align="center">
        <template #default="scope">{{ scope.row.roeReport || '-' }}</template>
      </el-table-column>
      <el-table-column
        v-for="col in msci.columns.filter(name => name !== '序号')"
        :key="col"
        :label="col"
        min-width="100"
        align="center"
        show-overflow-tooltip
      >
        <template #default="scope">{{ scope.row.extra?.[col] || '-' }}</template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="importDialogVisible" title="导入预览" width="760px">
      <template v-if="parseResult">
        <el-alert
          type="success"
          :closable="false"
          show-icon
          :title="`解析到 ${parseResult.total} 只股票，识别出 ${parseResult.columns.length} 个附加列`"
          class="import-alert"
        />
        <el-alert
          v-for="(warning, index) in parseResult.warnings || []"
          :key="`w-${index}`"
          type="warning"
          :closable="false"
          :title="warning"
          class="import-alert"
        />
        <el-table :data="parseResult.rows.slice(0, 20)" size="small" border max-height="320">
          <el-table-column type="index" label="#" width="50" align="center" />
          <el-table-column prop="code" label="代码" width="100" align="center" />
          <el-table-column prop="name" label="名称" width="120" />
          <el-table-column
            v-for="col in parseResult.columns"
            :key="col"
            :label="col"
            min-width="100"
            show-overflow-tooltip
          >
            <template #default="scope">{{ scope.row.extra?.[col] || '-' }}</template>
          </el-table-column>
        </el-table>
        <p class="preview-hint">
          确认导入后会自动联网获取每只股票的 PE(TTM)、ROE 和行业分类（约 20~60 秒），然后按行业分组计算排名。
        </p>
      </template>
      <template #footer>
        <el-button @click="importDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="confirmImport">确认导入并分析</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.msci-page {
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
  align-items: center;
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
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.stat-card {
  border: 1px solid #dbe6f2;
  border-radius: 8px;
  padding: 10px 12px;
  background: linear-gradient(180deg, #fbfdff, #f5f9fe);
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

.stat-card strong.small {
  font-size: 0.92rem;
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

.up {
  color: #e34d4d;
}

.down {
  color: #1aa261;
}

.flat {
  color: #5a7188;
}

.invalid {
  color: #c9862b;
}

.group-name {
  font-weight: 700;
  color: #1f3d5c;
}

:deep(.el-table .msci-tint-0 td.el-table__cell) {
  background-color: #ffffff;
}

:deep(.el-table .msci-tint-1 td.el-table__cell) {
  background-color: #f1f7ff;
}

:deep(.el-table .msci-tint-2 td.el-table__cell) {
  background-color: #fff7ef;
}

:deep(.el-table .msci-tint-3 td.el-table__cell) {
  background-color: #f2fbf3;
}

:deep(.el-table .msci-tint-4 td.el-table__cell) {
  background-color: #faf3ff;
}

:deep(.el-table .msci-tint-5 td.el-table__cell) {
  background-color: #fff3f3;
}

:deep(.el-table .msci-tint-6 td.el-table__cell) {
  background-color: #f1fbfb;
}

:deep(.el-table .msci-tint-7 td.el-table__cell) {
  background-color: #fbfaf0;
}

:deep(.el-table .msci-group-first td.el-table__cell) {
  border-top: 2px solid #b6cde6;
}

.import-alert {
  margin-bottom: 8px;
}

.preview-hint {
  margin: 10px 0 0;
  color: #6b7c93;
  font-size: 0.86rem;
}
</style>

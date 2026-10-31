// 持仓批量导入的纯函数：把从同花顺 / 券商 / Excel 复制出来的表格文本解析成持仓行。
// 单独放在这里是为了能脱离 Vue 直接跑测试。

const HEADER_ALIASES = {
  code: ['证券代码', '股票代码', '证券编号', '代码', 'code'],
  name: ['证券名称', '股票名称', '证券简称', '名称', 'name'],
  quantity: ['股票余额', '证券余额', '持仓数量', '当前持仓', '持仓量', '持股数量', '持股数', '可用余额', '可用股份', '股份余额', '数量', '余额'],
  costPrice: ['参考成本价', '摊薄成本价', '持仓成本价', '成本均价', '买入均价', '成本价', '持仓成本', '成本'],
  currentPrice: ['最新价格', '最新价', '当前价格', '现价', '市价', '当前价'],
};

const SKIP_ROW = /^(合计|总计|汇总|总资产|总市值|总盈亏|小计)/;

function normalizeCell(value) {
  return String(value ?? '')
    .replace(/[（(][^）)]*[）)]/g, '')
    .replace(/\s+/g, '')
    .trim();
}

function detectDelimiter(lines) {
  const candidates = ['\t', '|', ',', '，', ';', '；'];
  let best = null;
  let bestCells = 0;
  for (const delimiter of candidates) {
    const counts = lines.slice(0, 5).map(line => line.split(delimiter).length);
    const min = Math.min(...counts);
    if (min > bestCells) {
      bestCells = min;
      best = delimiter;
    }
  }
  if (bestCells >= 3) return best;
  return null; // 交给「两个以上空格」兜底
}

function splitLine(line, delimiter) {
  if (delimiter) return line.split(delimiter).map(cell => cell.trim());
  const wide = line.split(/\s{2,}/).map(cell => cell.trim()).filter(Boolean);
  if (wide.length >= 3) return wide;
  return line.split(/\s+/).map(cell => cell.trim()).filter(Boolean);
}

function mapHeaders(cells) {
  const columns = {};
  const used = new Set();
  cells.forEach((cell, index) => {
    const normalized = normalizeCell(cell);
    if (!normalized) return;
    for (const [field, aliases] of Object.entries(HEADER_ALIASES)) {
      if (columns[field] !== undefined) continue;
      const hit = aliases.some(alias => normalized === alias || normalized.includes(alias));
      if (hit && !used.has(index)) {
        columns[field] = index;
        used.add(index);
        return;
      }
    }
  });
  return columns;
}

function parseNumber(value) {
  const text = String(value ?? '')
    .replace(/[,，\s%￥¥]/g, '')
    .trim();
  if (!text || text === '-' || text === '--') return null;
  const num = Number(text);
  return Number.isFinite(num) ? num : null;
}

function parseCode(value) {
  const text = String(value ?? '').trim().toUpperCase();
  const match = text.match(/(\d{6})/);
  if (!match) return null;
  const code = match[1];
  const suffix = text.replace(code, '').replace(/[.\-_]/g, '');
  let market = '';
  if (suffix.includes('SH')) market = 'SH';
  if (suffix.includes('SZ')) market = 'SZ';
  if (!market) market = /^[659]/.test(code) ? 'SH' : 'SZ';
  return { code, market };
}

/**
 * 解析粘贴文本。
 * @param {string} text
 * @returns {{ ok: boolean, message: string, rows: Array<object>, header: string[] | null, delimiter: string | null }}
 */
export function parseHoldingsText(text) {
  const rawLines = String(text || '')
    .split(/\r?\n/)
    .map(line => line.replace(/\u00a0/g, ' '))
    .filter(line => line.trim() && !/^\s*#/.test(line));

  if (!rawLines.length) {
    return { ok: false, message: '没有解析到任何内容，请先把表格复制到输入框', rows: [], header: null, delimiter: null };
  }

  const delimiter = detectDelimiter(rawLines);
  const headerCells = splitLine(rawLines[0], delimiter);
  const columns = mapHeaders(headerCells);
  const hasCode = columns.code !== undefined;
  const hasQuantity = columns.quantity !== undefined;
  const hasCost = columns.costPrice !== undefined;

  if (!hasCode || !hasQuantity || !hasCost) {
    const missing = [];
    if (!hasCode) missing.push('代码');
    if (!hasQuantity) missing.push('数量');
    if (!hasCost) missing.push('成本价');
    return {
      ok: false,
      message: `表头缺少列：${missing.join('、')}。请把表头一起复制（例如：证券代码 / 证券名称 / 股票余额 / 成本价）`,
      rows: [],
      header: headerCells,
      delimiter,
    };
  }

  const rows = [];
  for (const line of rawLines.slice(1)) {
    const cells = splitLine(line, delimiter);
    if (!cells.length) continue;
    if (SKIP_ROW.test(normalizeCell(cells[0]))) continue;

    const parsedCode = parseCode(cells[columns.code]);
    const quantity = parseNumber(cells[columns.quantity]);
    const costPrice = parseNumber(cells[columns.costPrice]);
    const currentPrice = columns.currentPrice !== undefined ? parseNumber(cells[columns.currentPrice]) : null;
    const name = columns.name !== undefined ? String(cells[columns.name] ?? '').trim() : '';

    let error = '';
    if (!parsedCode) error = '代码无法识别';
    else if (quantity === null || quantity <= 0) error = '数量无效';
    else if (costPrice === null || costPrice < 0) error = '成本价无效';

    rows.push({
      code: parsedCode ? parsedCode.code : String(cells[columns.code] ?? '').trim(),
      market: parsedCode ? parsedCode.market : '',
      name,
      quantity: quantity ?? 0,
      costPrice: costPrice ?? 0,
      currentPrice,
      error,
    });
  }

  if (!rows.length) {
    return { ok: false, message: '只有表头没有数据行，请确认复制范围包含数据', rows: [], header: headerCells, delimiter };
  }

  return { ok: true, message: '', rows, header: headerCells, delimiter };
}

/**
 * 把解析结果合并进现有持仓：同代码视为同一只股票（更新数量与成本），新代码追加。
 * @param {Array<object>} parsedRows
 * @param {Array<object>} existing
 * @returns {{ data: Array<object>, added: number, updated: number, ignored: number }}
 */
export function mergeHoldings(parsedRows, existing) {
  const data = existing.map(row => ({ ...row }));
  const byCode = new Map();
  data.forEach(row => {
    if (!byCode.has(row.code)) byCode.set(row.code, row);
  });

  let maxId = data.reduce((max, row) => Math.max(max, Number(row.id) || 0), 0);
  let added = 0;
  let updated = 0;
  let ignored = 0;

  for (const parsed of parsedRows) {
    if (parsed.error) {
      ignored += 1;
      continue;
    }

    const current = byCode.get(parsed.code);
    if (current) {
      current.quantity = parsed.quantity;
      current.costPrice = parsed.costPrice;
      if (parsed.currentPrice !== null && parsed.currentPrice > 0) current.currentPrice = parsed.currentPrice;
      if (parsed.name) current.name = parsed.name;
      updated += 1;
      continue;
    }

    maxId += 1;
    const row = {
      id: maxId,
      code: parsed.code,
      name: parsed.name || parsed.code,
      sector: '-',
      costPrice: parsed.costPrice,
      quantity: parsed.quantity,
      currentPrice: parsed.currentPrice && parsed.currentPrice > 0 ? parsed.currentPrice : 0,
      totalCost: 0,
      marketValue: 0,
      profitAmount: 0,
      profitRatio: '0.00%',
      positionRatio: '0.00%',
      pb: '-',
      pe: '-',
      roe: '-',
      avgPe: '-',
      eps: '-',
      eps3y: '-',
      g: '-',
      peg: '-',
      expectedReturn: '-',
    };
    data.push(row);
    byCode.set(parsed.code, row);
    added += 1;
  }

  return { data, added, updated, ignored };
}

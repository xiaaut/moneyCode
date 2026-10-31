import { fileURLToPath, URL } from 'node:url';
import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import https from 'node:https';
import { TextDecoder } from 'node:util';

import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import vueDevTools from 'vite-plugin-vue-devtools';
import * as XLSX from 'xlsx';

const ROOT_DIR = fileURLToPath(new URL('.', import.meta.url));
const DATA_DIR = path.resolve(ROOT_DIR, 'data');
const PORTFOLIO_FILE = path.resolve(DATA_DIR, 'portfolio.json');
const WATCHLIST_FILE = path.resolve(DATA_DIR, 'watchlist.json');
const MSCI_FILE = path.resolve(DATA_DIR, 'msci.json');
const STATE_OWNED_FILE = path.resolve(DATA_DIR, 'state-owned.json');
const CLOSED_POSITIONS_FILE = path.resolve(DATA_DIR, 'closed-positions.json');
const CACHE_DIR = path.resolve(DATA_DIR, 'cache');
const QUOTE_CACHE_FILE = path.resolve(CACHE_DIR, 'quote.json');
const ROE_CACHE_FILE = path.resolve(CACHE_DIR, 'roe.json');
const CONTROLLER_CACHE_FILE = path.resolve(CACHE_DIR, 'controller.json');
const CENTRAL_SOE_CACHE_FILE = path.resolve(CACHE_DIR, 'central-soe.json');
const ORG_INFO_CACHE_FILE = path.resolve(CACHE_DIR, 'org-info.json');

const QUOTE_ROUTE = '/api/quote';
const ROE_ROUTE = '/api/roe';
const MSCI_ROUTE = '/api/msci';
const MSCI_PARSE_ROUTE = '/api/msci/parse';
const MSCI_REFRESH_ROUTE = '/api/msci/refresh';
const MSCI_ANALYZE_ROUTE = '/api/msci/analyze';
const MSCI_PROGRESS_ROUTE = '/api/msci/progress';
const CONTROLLER_ROUTE = '/api/controller';
const STATE_OWNED_ROUTE = '/api/state-owned';
const STATE_OWNED_START_ROUTE = '/api/state-owned/start';
const STATE_OWNED_PROGRESS_ROUTE = '/api/state-owned/progress';
const CLOSED_POSITIONS_ROUTE = '/api/closed-positions';
const STOCK_NAME_ROUTE = '/api/stock-name';

// 现价 30 秒内复用；ROE 一周；实际控制人 30 天（变动很少）
const QUOTE_TTL_MS = 30 * 1000;
const ROE_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const CONTROLLER_TTL_MS = 30 * 24 * 60 * 60 * 1000;
// 上游节流：两次请求之间的最小间隔 + 每分钟上限，避免触发风控
const QUOTE_MIN_INTERVAL_MS = 1000;
const QUOTE_MAX_PER_MINUTE = 60;
const ROE_MIN_INTERVAL_MS = 500;
const ROE_MAX_PER_MINUTE = 90;
const THS_MIN_INTERVAL_MS = 900;
const THS_MAX_PER_MINUTE = 40;
const EM_F10_MIN_INTERVAL_MS = 400;
const EM_F10_MAX_PER_MINUTE = 60;

const UPSTREAM_HEADERS: Record<string, string> = {
  'User-Agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  Accept: 'application/json, text/plain, */*',
  'Accept-Language': 'zh-CN,zh;q=0.9',
};

const QUOTE_FIELDS = 'f2,f3,f4,f5,f6,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f100,f115';

type CacheEntry = { value: any; ts: number };
type CacheFile = Record<string, CacheEntry>;

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

// ---------------- 通用 HTTP ----------------

function fetchBufferByHttps(
  url: string,
  extraHeaders: Record<string, string> = {},
  timeoutMs = 12_000,
): Promise<{ buffer: Buffer; status: number }> {
  return new Promise((resolve, reject) => {
    const client = url.startsWith('http:') ? http : https;
    const request = client.get(url, { headers: { ...UPSTREAM_HEADERS, ...extraHeaders } }, resp => {
      const chunks: Buffer[] = [];
      resp.on('data', chunk => chunks.push(Buffer.from(chunk)));
      resp.on('end', () => resolve({ buffer: Buffer.concat(chunks), status: resp.statusCode || 0 }));
    });
    request.setTimeout(timeoutMs, () => request.destroy(new Error('上游请求超时')));
    request.on('error', reject);
  });
}

async function fetchJsonByHttps(
  url: string,
  extraHeaders: Record<string, string> = {},
  timeoutMs = 12_000,
): Promise<any> {
  const { buffer, status } = await fetchBufferByHttps(url, extraHeaders, timeoutMs);
  if (status < 200 || status >= 300) throw new Error(`上游接口返回 HTTP ${status}`);
  try {
    return JSON.parse(buffer.toString('utf-8'));
  } catch {
    throw new Error('上游返回内容不是合法 JSON');
  }
}

let gbkDecoder: TextDecoder | null = null;
function decodeGbk(buffer: Buffer): string {
  if (!gbkDecoder) gbkDecoder = new TextDecoder('gbk');
  return gbkDecoder.decode(buffer);
}

async function readRequestBody(req: any, maxBytes = 20 * 1024 * 1024): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    const buf = Buffer.from(chunk as any);
    size += buf.length;
    if (size > maxBytes) throw new Error('上传内容过大（上限 20MB）');
    chunks.push(buf);
  }
  return Buffer.concat(chunks);
}

function sendJson(res: any, status: number, payload: any) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(payload));
}

// ---------------- 缓存（内存 + data/cache/*.json 落盘，重启不丢） ----------------

type CacheStore = { file: string; data: CacheFile | null; loading: Promise<void> | null };
const cacheStores: Record<string, CacheStore> = {
  quote: { file: QUOTE_CACHE_FILE, data: null, loading: null },
  roe: { file: ROE_CACHE_FILE, data: null, loading: null },
  controller: { file: CONTROLLER_CACHE_FILE, data: null, loading: null },
};
const writeQueues: Record<string, Promise<void>> = {
  quote: Promise.resolve(),
  roe: Promise.resolve(),
  controller: Promise.resolve(),
};

async function ensureCacheLoaded(name: string): Promise<CacheFile> {
  const store = cacheStores[name];
  if (store.data) return store.data;
  if (!store.loading) {
    store.loading = (async () => {
      try {
        const raw = await fs.readFile(store.file, 'utf-8');
        const parsed = JSON.parse(raw);
        store.data = parsed && typeof parsed === 'object' ? parsed : {};
      } catch {
        store.data = {};
      }
    })();
  }
  await store.loading;
  return store.data || ({} as CacheFile);
}

function persistCache(name: string) {
  const store = cacheStores[name];
  writeQueues[name] = writeQueues[name]
    .then(async () => {
      await fs.mkdir(CACHE_DIR, { recursive: true });
      await fs.writeFile(store.file, `${JSON.stringify(store.data || {}, null, 2)}\n`, 'utf-8');
    })
    .catch(error => {
      console.warn('[cache] 写入失败:', error?.message || error);
    });
}

// ---------------- 上游节流 ----------------

const rateState: Record<string, { lastCall: number; timestamps: number[] }> = {
  quote: { lastCall: 0, timestamps: [] },
  roe: { lastCall: 0, timestamps: [] },
  ths: { lastCall: 0, timestamps: [] },
  em: { lastCall: 0, timestamps: [] },
};

async function acquireUpstreamSlot(
  kind: 'quote' | 'roe' | 'ths' | 'em',
  minIntervalMs: number,
  maxPerMinute: number,
) {
  const state = rateState[kind];
  const wait = state.lastCall + minIntervalMs - Date.now();
  if (wait > 0) await sleep(wait);

  const windowStart = Date.now() - 60_000;
  state.timestamps = state.timestamps.filter(ts => ts > windowStart);
  if (state.timestamps.length >= maxPerMinute) {
    throw new Error('上游接口调用过于频繁，已自动暂停，请一分钟后再试');
  }

  state.timestamps.push(Date.now());
  state.lastCall = Date.now();
}

// ---------------- 行情（批量 + 缓存 + 分片） ----------------

function buildQuoteUrl(secids: string[]) {
  return `https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&fields=${QUOTE_FIELDS}&secids=${encodeURIComponent(secids.join(','))}`;
}

function chunkArray<T>(items: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < items.length; i += size) chunks.push(items.slice(i, i + size));
  return chunks;
}

async function resolveQuotes(secids: string[]): Promise<{ diff: any[]; stale: boolean; message: string }> {
  const cache = await ensureCacheLoaded('quote');
  const now = Date.now();
  const diff: any[] = [];
  const missing: string[] = [];

  for (const secid of secids) {
    const entry = cache[secid];
    if (entry && now - entry.ts < QUOTE_TTL_MS) diff.push(entry.value);
    else missing.push(secid);
  }

  let stale = false;
  let upstreamError = '';
  for (const chunk of chunkArray(missing, 100)) {
    try {
      await acquireUpstreamSlot('quote', QUOTE_MIN_INTERVAL_MS, QUOTE_MAX_PER_MINUTE);
      const json = await fetchJsonByHttps(buildQuoteUrl(chunk), { Referer: 'https://quote.eastmoney.com/' });
      const items = Array.isArray(json?.data?.diff) ? json.data.diff : [];
      for (const item of items) {
        const secid = `${item?.f13}.${item?.f12}`;
        if (secid) {
          cache[secid] = { value: item, ts: Date.now() };
          diff.push(item);
        }
      }
      persistCache('quote');
    } catch (error: any) {
      upstreamError = error?.message || '行情拉取失败';
      for (const secid of chunk) {
        const entry = cache[secid];
        if (entry) {
          diff.push(entry.value);
          stale = true;
        }
      }
    }
  }

  return { diff, stale, message: upstreamError };
}

// ---------------- 腾讯行情（全市场扫描用，避开东财 push2 的频率限制） ----------------

async function fetchTencentQuotes(codes: string[]): Promise<
  Map<
    string,
    {
      name: string;
      price: number | null;
      changePct: number | null;
      peTtm: number | null;
      pb: number | null;
      marketCapYi: number | null;
      turnover: number | null;
    }
  >
> {
  const result = new Map<
    string,
    {
      name: string;
      price: number | null;
      changePct: number | null;
      peTtm: number | null;
      pb: number | null;
      marketCapYi: number | null;
      turnover: number | null;
    }
  >();
  for (const chunk of chunkArray(codes, 60)) {
    const items = chunk.map(
      code => `${/^(43|83|87|92)/.test(code) ? 'bj' : /^[659]/.test(code) ? 'sh' : 'sz'}${code}`,
    );
    try {
      const { buffer, status } = await fetchBufferByHttps(
        `http://qt.gtimg.cn/q=${items.join(',')}`,
        { Referer: 'http://gu.qq.com/' },
        15_000,
      );
      if (status < 200 || status >= 300) throw new Error(`腾讯行情 HTTP ${status}`);
      const text = decodeGbk(buffer);
      for (const line of text.split('\n')) {
        const match = line.match(/v_(\w+)="([^"]*)"/);
        if (!match) continue;
        const code = match[1].slice(2);
        const fields = match[2].split('~');
        const name = String(fields[1] || '').trim();
        if (!name) continue;
        const price = Number(fields[3]);
        const prevClose = Number(fields[4]);
        const peTtm = Number(fields[39]);
        const pb = Number(fields[46]);
        const marketCapYi = Number(fields[45]);
        const turnover = Number(fields[38]);
        result.set(code, {
          name,
          price: Number.isFinite(price) && price > 0 ? price : null,
          changePct:
            Number.isFinite(price) && Number.isFinite(prevClose) && prevClose > 0
              ? ((price - prevClose) / prevClose) * 100
              : null,
          peTtm: Number.isFinite(peTtm) ? peTtm : null,
          pb: Number.isFinite(pb) && pb > 0 ? pb : null,
          marketCapYi: Number.isFinite(marketCapYi) && marketCapYi > 0 ? marketCapYi : null,
          turnover: Number.isFinite(turnover) ? turnover : null,
        });
      }
    } catch (error: any) {
      stateOwnedJob.message = `腾讯行情请求失败：${error?.message || error}`;
    }
    await sleep(120);
  }
  return result;
}

// ---------------- ROE ----------------// ---------------- ROE ----------------

function buildRoeUrl(code: string) {
  return `https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=0&code=${code}`;
}

async function resolveRoe(codes: string[]): Promise<{ items: Record<string, any>; message: string }> {
  const cache = await ensureCacheLoaded('roe');
  const now = Date.now();
  const items: Record<string, any> = {};
  let fetchedAny = false;
  let throttled = '';

  for (const code of codes) {
    const entry = cache[code];
    if (entry && now - entry.ts < ROE_TTL_MS) {
      items[code] = { roe: entry.value, ts: entry.ts, fromCache: true };
      continue;
    }
    try {
      await acquireUpstreamSlot('roe', ROE_MIN_INTERVAL_MS, ROE_MAX_PER_MINUTE);
      const data = await fetchJsonByHttps(buildRoeUrl(code), { Referer: 'https://emweb.securities.eastmoney.com/' });
      const first = Array.isArray(data?.data) ? data.data[0] : null;
      const roe = first?.ROEJQ ?? null;
      if (roe !== null && roe !== undefined) {
        cache[code] = { value: roe, ts: Date.now() };
        items[code] = { roe, ts: Date.now(), fromCache: false };
        fetchedAny = true;
      } else {
        items[code] = { roe: null, ts: Date.now(), fromCache: false };
      }
    } catch (error: any) {
      if (/频繁|暂停/.test(error?.message || '')) throttled = error.message;
      const entry = cache[code];
      if (entry) items[code] = { roe: entry.value, ts: entry.ts, fromCache: true, stale: true };
      else items[code] = { roe: null, error: error?.message || 'roe fetch error' };
    }
  }

  if (fetchedAny) persistCache('roe');
  return { items, message: throttled };
}

// ---------------- 实际控制人（同花顺 F10 为主，东财 F10 兜底） ----------------

const CATEGORY_LABELS: Record<string, string> = {
  'sasac-central': '国务院国资委',
  'sasac-local': '地方国资委',
  government: '政府 / 财政部门',
  'state-other': '其他国有主体',
  'state-group-pending': '国有法人（待确认）',
  'non-state': '非国资',
  none: '无实际控制人',
  'not-applicable': 'ETF / 基金（不适用）',
  unknown: '未获取',
};

function classifyController(finalController: string, actualController: string, name: string): string {
  const text = `${finalController || ''} ${actualController || ''}`.trim();
  const title = String(name || '');

  if (!text) {
    if (/ETF|LOF|基金/i.test(title)) return 'not-applicable';
    return 'unknown';
  }
  if (/无实际控制人|无最终控制人|无控股股东/.test(text)) return 'none';
  if (/国务院国有资产监督管理委员会|国务院国资委/.test(text)) return 'sasac-central';
  if (/国有资产监督管理委员会|国资委/.test(text)) return 'sasac-local';
  if (/人民政府|财政(厅|局)|管理委员会|管委会|国务院/.test(text)) return 'government';
  if (/中央汇金|全国社会保障基金|国有资本|国有资产经营|国有投资/.test(text)) return 'state-other';
  if (/^[\u4e00-\u9fa5]{2,4}$/.test(text.replace(/[、,，\s]/g, ''))) return 'non-state';
  if (/集团|控股|投资|有限公司|有限责任公司|总公司|经营公司/.test(text)) return 'state-group-pending';
  return 'non-state';
}

/** 删除同花顺页面里的悬浮提示块（<div class="tipbox ..."> 含嵌套 div），避免把「旗下上市公司」表格当字段值 */
function stripTipboxBlocks(html: string): string {
  let result = '';
  let index = 0;
  while (index < html.length) {
    const start = html.indexOf('<div class="tipbox', index);
    if (start < 0) {
      result += html.slice(index);
      break;
    }
    result += html.slice(index, start);

    let depth = 0;
    let cursor = start;
    while (cursor < html.length) {
      const open = html.indexOf('<div', cursor);
      const close = html.indexOf('</div>', cursor);
      if (close < 0) {
        cursor = html.length;
        break;
      }
      if (open >= 0 && open < close) {
        depth += 1;
        cursor = open + 4;
      } else {
        depth -= 1;
        cursor = close + 6;
        if (depth === 0) break;
      }
    }
    index = cursor;
  }
  return result;
}

function parseThsField(html: string, label: string): { name: string; ratio: string } | null {
  const marker = `${label}：</strong>`;
  const markerIndex = html.indexOf(marker);
  if (markerIndex < 0) return null;

  const rawChunk = html.slice(markerIndex + marker.length, markerIndex + marker.length + 40_000);
  // 先剔除「旗下上市公司」悬浮提示框再截 </td>：提示框里自带表格和 <td>，顺序反了会截在错误位置
  const withoutTips = stripTipboxBlocks(rawChunk);
  const cellEnd = withoutTips.indexOf('</td>');
  const chunk = cellEnd >= 0 ? withoutTips.slice(0, cellEnd) : withoutTips.slice(0, 2000);
  const cleaned = chunk
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!cleaned) return null;

  const ratioMatch = cleaned.match(/比例[：:]\s*([^）)]+)/);
  const name = cleaned
    .replace(/\s*[（(]?\s*持有[\s\S]*$/, '')
    .replace(/^[\s（(、,，]+|[\s（()）、,，]+$/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (!name) return null;
  return { name, ratio: ratioMatch ? ratioMatch[1].replace(/\s+/g, ' ').trim() : '' };
}

async function fetchThsController(code: string) {
  const url = `https://basic.10jqka.com.cn/${code}/company.html`;
  const { buffer, status } = await fetchBufferByHttps(url, { Referer: 'https://basic.10jqka.com.cn/' }, 15_000);
  if (status < 200 || status >= 300) throw new Error(`同花顺 F10 返回 HTTP ${status}`);
  const html = decodeGbk(buffer);
  if (!html || html.length < 1000) throw new Error('同花顺 F10 返回内容异常');

  const titleMatch = html.match(/<title>([\s\S]*?)<\/title>/i);
  const rawTitle = titleMatch ? titleMatch[1] : '';
  const nameMatch = rawTitle.match(/^([^<(（]+)[(（]\d{6}/);

  return {
    name: nameMatch ? nameMatch[1].trim() : '',
    finalController: parseThsField(html, '最终控制人'),
    actualController: parseThsField(html, '实际控制人'),
    controllingHolder: parseThsField(html, '控股股东'),
    sourceUrl: url,
  };
}

async function fetchEastmoneyController(code: string) {
  const secCode = code; // SH600519 / SZ000725
  const url = `https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code=${secCode}`;
  const data = await fetchJsonByHttps(url, { Referer: 'https://emweb.securities.eastmoney.com/' });
  const list = Array.isArray(data?.sjkzr) ? data.sjkzr : [];
  const first = list[0] || null;
  const name = first?.HOLDER_NAME ? String(first.HOLDER_NAME).trim() : '';
  const ratio = first?.HOLD_RATIO != null ? `${first.HOLD_RATIO}%` : '';
  return {
    name,
    holder: name ? { name, ratio } : null,
    sourceUrl: `https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/Index?type=web&code=${secCode}`,
  };
}

async function resolveController(code: string, forceRefresh: boolean) {
  const cache = await ensureCacheLoaded('controller');
  const cached = cache[code];
  if (!forceRefresh && cached && Date.now() - cached.ts < CONTROLLER_TTL_MS) {
    return { ...cached.value, fromCache: true };
  }

  const pureCode = code.replace(/^(SH|SZ|BJ)/, '');

  let ths: any = null;
  let thsError = '';
  try {
    await acquireUpstreamSlot('ths', THS_MIN_INTERVAL_MS, THS_MAX_PER_MINUTE);
    ths = await fetchThsController(pureCode);
  } catch (error: any) {
    thsError = error?.message || '同花顺 F10 抓取失败';
  }

  let em: any = null;
  let emError = '';
  const needFallback = !ths || !ths.finalController;
  if (needFallback) {
    try {
      await acquireUpstreamSlot('em', EM_F10_MIN_INTERVAL_MS, EM_F10_MAX_PER_MINUTE);
      em = await fetchEastmoneyController(code);
    } catch (error: any) {
      emError = error?.message || '东方财富 F10 抓取失败';
    }
  }

  const finalName = ths?.finalController?.name || '';
  const actualName = ths?.actualController?.name || '';
  const controllingName = ths?.controllingHolder?.name || '';
  const emName = em?.name || '';
  const fallbackUsed = !finalName && !actualName && Boolean(emName);

  let category = classifyController(finalName || emName, actualName || emName, ths?.name || '');
  let note = '';
  let source = '';
  let sourceUrl = '';

  if (ths && (finalName || actualName || controllingName)) {
    source = fallbackUsed ? '同花顺 F10 + 东方财富 F10' : '同花顺 F10·公司资料';
    sourceUrl = ths.sourceUrl;
    if (fallbackUsed) note = '同花顺未披露最终控制人，已用东方财富 F10 实际控制人兜底';
  } else if (emName) {
    source = '东方财富 F10·股东研究';
    sourceUrl = em?.sourceUrl || '';
    if (category === 'state-group-pending') note = '东财仅给出国有法人作为实际控制人，建议人工核对最终控制人';
  }

  if (!ths && emName) note = note || `同花顺抓取失败（${thsError}），已用东方财富数据兜底`;
  if (!ths && !emName) {
    category = 'unknown';
    note = [thsError, emError].filter(Boolean).join('；') || '两个数据源均未取到数据';
  }
  if (category === 'state-group-pending' && !note) {
    note = '同花顺未披露最终控制人，实际控制人为国有法人，建议人工核对';
  }
  // 两个数据源口径不一致时把东财口径展示出来，方便人工核对
  if (emName && finalName && !emName.includes(finalName) && !finalName.includes(emName)) {
    note = [note, `东方财富口径：${emName}`].filter(Boolean).join('；');
  }
  // ETF / 基金没有实际控制人，标记为「不适用」而不是「未获取」
  if (category === 'unknown' && /^(5\d|1[5-8])\d{4}$/.test(pureCode)) {
    category = 'not-applicable';
    note = note || 'ETF / 基金标的，不适用实际控制人判定';
  }

  const item = {
    code,
    name: ths?.name || '',
    finalController: finalName,
    finalControllerRatio: ths?.finalController?.ratio || '',
    actualController: actualName,
    actualControllerRatio: ths?.actualController?.ratio || '',
    controllingHolder: controllingName,
    controllingHolderRatio: ths?.controllingHolder?.ratio || '',
    eastmoneyController: emName,
    category,
    categoryLabel: CATEGORY_LABELS[category] || category,
    source,
    sourceUrl,
    note,
    fetchedAt: new Date().toISOString(),
  };

  cache[code] = { value: item, ts: Date.now() };
  persistCache('controller');
  return { ...item, fromCache: false };
}

// ---------------- MSCI 文件解析 ----------------

function decodeTextBuffer(buffer: Buffer): string {
  if (buffer.length >= 3 && buffer[0] === 0xef && buffer[1] === 0xbb && buffer[2] === 0xbf) {
    return buffer.subarray(3).toString('utf-8');
  }
  const utf8 = buffer.toString('utf-8');
  if (!utf8.includes('\ufffd')) return utf8;
  try {
    return decodeGbk(buffer);
  } catch {
    return utf8;
  }
}

function parseDelimitedText(text: string): string[][] {
  const lines = text.split(/\r?\n/).filter(line => line.trim());
  if (!lines.length) return [];

  const sample = lines.slice(0, 10);
  const candidates = ['\t', ',', '，', ';', '；', '|'];
  let delimiter = '';
  let bestCells = 0;
  for (const candidate of candidates) {
    const counts = sample.map(line => line.split(candidate).length);
    const min = Math.min(...counts);
    if (min > bestCells) {
      bestCells = min;
      delimiter = candidate;
    }
  }

  return lines.map(line => {
    const cells = delimiter && bestCells >= 2 ? line.split(delimiter) : line.split(/\s+/);
    return cells.map(cell => cell.trim().replace(/^"|"$/g, '').replace(/^'|'$/g, ''));
  });
}

function readSheetMatrix(filename: string, buffer: Buffer): string[][] {
  const ext = path.extname(filename).toLowerCase();
  const isZip = buffer.length > 4 && buffer[0] === 0x50 && buffer[1] === 0x4b;
  const isOle = buffer.length > 4 && buffer[0] === 0xd0 && buffer[1] === 0xcf;
  const isExcel = ['.xlsx', '.xls', '.xlsm'].includes(ext) || isZip || isOle;

  if (isExcel) {
    const workbook = XLSX.read(buffer, { type: 'buffer' });
    const sheetName = workbook.SheetNames[0];
    if (!sheetName) return [];
    const sheet = workbook.Sheets[sheetName];
    const matrix = XLSX.utils.sheet_to_json<unknown[]>(sheet, { header: 1, raw: false, defval: '' });
    return matrix.map(row => (Array.isArray(row) ? row.map(cell => String(cell ?? '').trim()) : []));
  }

  return parseDelimitedText(decodeTextBuffer(buffer));
}

function extractCode(value: string): string | null {
  const match = String(value ?? '')
    .toUpperCase()
    .match(/(\d{6})/);
  return match ? match[1] : null;
}

function normalizeMsciMatrix(matrix: string[][]) {
  const rows = matrix.filter(row => row.some(cell => String(cell ?? '').trim()));
  if (!rows.length) return { columns: [] as string[], rows: [] as any[], warnings: ['文件里没有可识别的数据'] };

  const maxCols = Math.max(...rows.map(row => row.length));
  const firstRowHasCode = rows[0].some(cell => extractCode(cell));
  const hasHeaderCandidate =
    !firstRowHasCode && rows[0].filter(cell => String(cell ?? '').trim()).length >= 2 && rows.length > 1;

  const headerRow = hasHeaderCandidate ? rows[0] : [];
  const dataRows = hasHeaderCandidate ? rows.slice(1) : rows;

  const codeHits = new Array(maxCols).fill(0);
  dataRows.slice(0, 500).forEach(row => {
    for (let col = 0; col < maxCols; col += 1) {
      if (extractCode(row[col] || '')) codeHits[col] += 1;
    }
  });
  const codeCol = codeHits.indexOf(Math.max(...codeHits));
  if (codeHits[codeCol] === 0) {
    return { columns: [] as string[], rows: [] as any[], warnings: ['没有找到 6 位股票代码，请确认文件内容'] };
  }

  const headerOf = (index: number) => String(headerRow[index] ?? '').trim() || `列${index + 1}`;

  // 名称列：优先表头含「名称」，否则选非代码列里中文最多的列
  let nameCol = -1;
  for (let col = 0; col < maxCols; col += 1) {
    if (col === codeCol) continue;
    if (/名称|简称|股票名/.test(String(headerRow[col] ?? ''))) {
      nameCol = col;
      break;
    }
  }
  if (nameCol < 0) {
    let bestScore = 0;
    for (let col = 0; col < maxCols; col += 1) {
      if (col === codeCol) continue;
      const score = dataRows
        .slice(0, 100)
        .filter(row => /[\u4e00-\u9fa5]/.test(String(row[col] ?? '')) && !extractCode(row[col] || '')).length;
      if (score > bestScore) {
        bestScore = score;
        nameCol = col;
      }
    }
    if (bestScore < Math.min(3, dataRows.length)) nameCol = -1;
  }

  const extraCols: number[] = [];
  for (let col = 0; col < maxCols; col += 1) {
    if (col === codeCol || col === nameCol) continue;
    if (dataRows.slice(0, 50).some(row => String(row[col] ?? '').trim())) extraCols.push(col);
  }

  const warnings: string[] = [];
  if (!hasHeaderCandidate) warnings.push('未识别到表头，已按「每行第一个 6 位代码」解析');
  const codeSet = new Set<string>();
  const parsedRows: any[] = [];
  let skipped = 0;

  for (const row of dataRows) {
    const code = extractCode(row[codeCol] || '');
    if (!code) {
      skipped += 1;
      continue;
    }
    if (codeSet.has(code)) continue;
    codeSet.add(code);

    const extra: Record<string, string> = {};
    for (const col of extraCols) {
      const value = String(row[col] ?? '').trim();
      if (value) extra[headerOf(col)] = value;
    }

    parsedRows.push({
      code,
      name: nameCol >= 0 ? String(row[nameCol] ?? '').trim() : '',
      extra,
    });
    if (parsedRows.length >= 3000) {
      warnings.push('单次最多导入 3000 条，已截断');
      break;
    }
  }

  if (skipped) warnings.push(`跳过 ${skipped} 行没有股票代码的内容`);

  return {
    columns: extraCols.map(col => headerOf(col)),
    rows: parsedRows,
    warnings,
  };
}

function secidOf(code: string): string {
  // 北交所代码（43/83/87/92 开头）在东财行情里同样是 0.xxxx
  if (/^(43|83|87|92)/.test(code)) return `0.${code}`;
  const market = /^[659]/.test(code) ? '1' : '0';
  return `${market}.${code}`;
}

async function readMsciData() {
  try {
    const raw = await fs.readFile(MSCI_FILE, 'utf-8');
    const parsed = JSON.parse(raw);
    return {
      fileName: String(parsed?.fileName || ''),
      importedAt: parsed?.importedAt || null,
      updatedAt: parsed?.updatedAt || null,
      analyzedAt: parsed?.analyzedAt || null,
      columns: Array.isArray(parsed?.columns) ? parsed.columns : [],
      rows: Array.isArray(parsed?.rows) ? parsed.rows : [],
    };
  } catch {
    return { fileName: '', importedAt: null, updatedAt: null, analyzedAt: null, columns: [], rows: [] };
  }
}

async function enrichMsciRows(rows: any[]) {
  const codes = rows.map(row => String(row.code || '').trim()).filter(code => /^\d{6}$/.test(code));
  if (!codes.length) return { rows, updatedAt: null, message: '没有可更新的股票', stale: false };

  const result = await resolveQuotes(codes.map(secidOf));
  const quoteByCode = new Map<string, any>();
  for (const item of result.diff) quoteByCode.set(String(item?.f12 || ''), item);

  const nextRows = rows.map(row => {
    const quote = quoteByCode.get(String(row.code || ''));
    if (!quote) return row;
    return {
      ...row,
      name: row.name || String(quote.f14 || ''),
      quote: {
        price: quote.f2 ?? null,
        changePct: quote.f3 ?? null,
        changeAmount: quote.f4 ?? null,
        volume: quote.f5 ?? null,
        amount: quote.f6 ?? null,
        turnover: quote.f8 ?? null,
        peDynamic: quote.f9 ?? null,
        peTtm: quote.f115 ?? null,
        pb: quote.f23 ?? null,
        totalMarketValue: quote.f20 ?? null,
        floatMarketValue: quote.f21 ?? null,
        industry: quote.f100 ?? '',
      },
    };
  });

  return {
    rows: nextRows,
    updatedAt: new Date().toISOString(),
    message: result.message,
    stale: result.stale,
  };
}


// ---------------- 全市场国资 / 政府控股扫描 ----------------

/** 国资委官网「央企名录」，用于把「国有法人（集团）」映射到 国务院国资委 */
const CENTRAL_SOE_URL = 'http://www.sasac.gov.cn/n2588045/n27271785/n27271792/';
const CENTRAL_SOE_TTL_MS = 5 * 24 * 60 * 60 * 1000;

function normalizeCorpName(name: string): string {
  return String(name || '')
    .replace(/[\s\u3000]/g, '')
    .replace(/[（(][^）)]*[）)]/g, '')
    .replace(/有限责任公司|有限公司|股份有限公司|股份公司|集团有限公司|集团|控股|总公司|公司/g, '');
}

function matchCentralSoe(raw: string, names: string[]): string {
  const source = String(raw || '').trim();
  if (!source || !names.length) return '';
  const cores = source
    .split(/[、,，;；\/]/)
    .map(item => normalizeCorpName(item))
    .filter(core => core.length >= 2);
  if (!cores.length) return '';
  for (const name of names) {
    const nameCore = normalizeCorpName(name);
    if (nameCore.length < 2) continue;
    for (const core of cores) {
      if (core === nameCore || core.includes(nameCore) || nameCore.includes(core)) return name;
    }
  }
  return '';
}

async function loadCentralSoeNames(): Promise<string[]> {
  try {
    const raw = await fs.readFile(CENTRAL_SOE_CACHE_FILE, 'utf-8');
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed?.names) && parsed.names.length && Date.now() - Number(parsed.fetchedAt || 0) < CENTRAL_SOE_TTL_MS) {
      return parsed.names.map(String);
    }
  } catch {
    // 缓存不存在或损坏，走网络
  }

  try {
    const { buffer, status } = await fetchBufferByHttps(CENTRAL_SOE_URL, { Referer: 'http://www.sasac.gov.cn/' }, 20_000);
    if (status < 200 || status >= 300) throw new Error(`国资委官网返回 HTTP ${status}`);
    const html = buffer.toString('utf-8');
    const rawNames = Array.from(html.matchAll(/<td[^>]*align="left"[^>]*>\s*<a[^>]*>([^<]+)<\/a>/g)).map(match =>
      String(match[1] || '').trim(),
    );
    const names: string[] = [];
    for (const name of rawNames) {
      const primary = name.replace(/\[[^\]]*\]/g, '').trim();
      if (primary) names.push(primary);
      for (const alt of Array.from(name.matchAll(/\[([^\]]+)\]/g))) {
        const altName = String(alt[1] || '').trim();
        if (altName) names.push(altName);
      }
    }
    if (!names.length) throw new Error('未能从国资委官网解析出央企名录');

    await fs.mkdir(CACHE_DIR, { recursive: true });
    await fs.writeFile(
      CENTRAL_SOE_CACHE_FILE,
      `${JSON.stringify({ fetchedAt: Date.now(), names }, null, 2)}\n`,
      'utf-8',
    );
    return names;
  } catch (error: any) {
    console.warn('[central-soe] 央企名录获取失败，将跳过集团映射:', error?.message || error);
    return [];
  }
}



const ORG_INFO_URL =
  'https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_BASIC_ORGINFO&columns=SECURITY_CODE,SECURITY_NAME_ABBR,SECURITY_TYPE,ACTUAL_HOLDER&pageNumber=';

// 只要沪深主板 + 北交所；创业板、科创板、风险警示板（ST）在过滤阶段直接排除
const A_SHARE_TYPES = new Set(['上交所主板A股', '深交所主板A股', '北京证券交易所A股']);

const STATE_CATEGORY_LABELS: Record<string, string> = {
  'sasac-central': '国务院国资委',
  'sasac-local': '地方国资委',
  government: '政府 / 财政 / 事业单位',
  'state-finance': '国有资本运营主体',
  'state-group': '国有法人（待确认）',
  collective: '集体所有制',
  person: '非国资（自然人）',
  none: '无实际控制人',
  other: '其他 / 社会组织',
  unknown: '未获取',
};

const STATE_CATEGORIES = new Set(['sasac-central', 'sasac-local', 'government', 'state-finance']);

/** 对东财数据中心的 ACTUAL_HOLDER 文本做分类 */
function classifyBulkHolder(raw: string): { category: string; label: string } {
  const text = String(raw || '').trim();
  if (!text) return { category: 'unknown', label: STATE_CATEGORY_LABELS.unknown };
  if (/^(无|无实际控制人|-|--|其他)$/.test(text)) return { category: 'none', label: STATE_CATEGORY_LABELS.none };

  const tokens = text
    .split(/[、,，;；\/]/)
    .map(item => item.trim())
    .filter(Boolean);
  const joined = tokens.join('、');

  const isPersonToken = (token: string) =>
    /^[\u4e00-\u9fa5]{2,4}$/.test(token) || /^[A-Za-z][A-Za-z\s.]{1,30}$/.test(token);
  const personTokens = tokens.filter(isPersonToken);
  const hasStateKeyword = /国资委|国有资产监督管理|人民政府|财政|汇金|国务院|国有资本|社会保障/.test(joined);
  if (personTokens.length > 0 && !hasStateKeyword) {
    return { category: 'person', label: STATE_CATEGORY_LABELS.person };
  }

  if (/国务院国有资产监督管理委员会|国务院国资委/.test(joined)) {
    return { category: 'sasac-central', label: STATE_CATEGORY_LABELS['sasac-central'] };
  }
  if (/国有资产监督管理委员会|国资委/.test(joined)) {
    return { category: 'sasac-local', label: STATE_CATEGORY_LABELS['sasac-local'] };
  }
  if (/中央汇金|全国社会保障基金|国有资本|国有资产经营|国有投资|国有资本运营/.test(joined)) {
    return { category: 'state-finance', label: STATE_CATEGORY_LABELS['state-finance'] };
  }
  if (
    /中华人民共和国财政部|财政部|中华人民共和国国务院|国务院/.test(joined) ||
    /人民政府|财政(厅|局|部)|国有资产管理(局|中心|办公室|委员会)|管理委员会|管委会|省委|市委|县委|宣传部|管理局/.test(joined)
  ) {
    return { category: 'government', label: STATE_CATEGORY_LABELS.government };
  }
  if (/中国中信集团|中信集团有限公司|中国光大集团|中国投资有限责任公司/.test(joined)) {
    return { category: 'government', label: STATE_CATEGORY_LABELS.government };
  }
  if (/报业集团|日报社|广电集团|广播电视台|出版集团|传媒集团|出版社/.test(joined)) {
    return { category: 'government', label: STATE_CATEGORY_LABELS.government };
  }
  if (/供销合作总社|合作社|村民委员会|社团经济企业联合会|集体经济/.test(joined)) {
    return { category: 'collective', label: STATE_CATEGORY_LABELS.collective };
  }
  if (/促进会|基金会|联合会|协会|学会|社会团体/.test(joined)) {
    return { category: 'other', label: STATE_CATEGORY_LABELS.other };
  }
  if (/集团|控股|投资|有限公司|有限责任公司|总公司|公司|研究院|科学院|大学|委员会|中心/.test(joined)) {
    return { category: 'state-group', label: STATE_CATEGORY_LABELS['state-group'] };
  }
  return { category: 'other', label: STATE_CATEGORY_LABELS.other };
}

/** 同花顺/resolveController 的分类映射到本页面统一口径 */
function mapThsCategory(category: string): string {
  switch (category) {
    case 'sasac-central':
      return 'sasac-central';
    case 'sasac-local':
      return 'sasac-local';
    case 'government':
      return 'government';
    case 'state-other':
      return 'state-finance';
    case 'state-group-pending':
      return 'state-group';
    case 'none':
      return 'none';
    case 'non-state':
      return 'person';
    default:
      return '';
  }
}

function boardOf(type: string): string {
  if (type.includes('北京')) return '北交所';
  if (type.includes('上交所')) return '沪主板';
  return '深主板';
}

function marketPrefixOf(code: string): string {
  if (/^(43|83|87|92)/.test(code)) return 'BJ';
  return code.startsWith('6') ? 'SH' : 'SZ';
}

type StateOwnedJobState = {
  running: boolean;
  phase: string;
  label: string;
  done: number;
  total: number;
  message: string;
  startedAt: string | null;
  finishedAt: string | null;
  error: string;
};

let stateOwnedJob: StateOwnedJobState = {
  running: false,
  phase: 'idle',
  label: '空闲',
  done: 0,
  total: 0,
  message: '',
  startedAt: null,
  finishedAt: null,
  error: '',
};

async function readStateOwnedData() {
  try {
    const raw = await fs.readFile(STATE_OWNED_FILE, 'utf-8');
    const parsed = JSON.parse(raw);
    return {
      updatedAt: parsed?.updatedAt || null,
      total: Number(parsed?.total) || 0,
      counts: parsed?.counts && typeof parsed.counts === 'object' ? parsed.counts : {},
      sourceStats: parsed?.sourceStats && typeof parsed.sourceStats === 'object' ? parsed.sourceStats : {},
      rows: Array.isArray(parsed?.rows) ? parsed.rows : [],
    };
  } catch {
    return { updatedAt: null, total: 0, counts: {}, sourceStats: {}, rows: [] };
  }
}

function countByCategory(rows: any[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const row of rows) counts[row.category] = (counts[row.category] || 0) + 1;
  return counts;
}

async function runStateOwnedJob(options: { force: boolean; limit?: number }) {
  const startedAt = new Date().toISOString();
  stateOwnedJob = {
    running: true,
    phase: 'universe',
    label: '拉取全市场公司概况',
    done: 0,
    total: 0,
    message: '',
    startedAt,
    finishedAt: null,
    error: '',
  };

  try {
    // ---- 阶段 1：全量公司概况（含实际控制人） ----
    const orgRows: any[] = [];
    let page = 1;
    let pages = 1;
    do {
      const json = await fetchJsonByHttps(`${ORG_INFO_URL}${page}&pageSize=500`, {
        Referer: 'https://data.eastmoney.com/',
      });
      const data = Array.isArray(json?.result?.data) ? json.result.data : [];
      if (!data.length) break;
      orgRows.push(...data);
      pages = Number(json?.result?.pages) || pages;
      stateOwnedJob.done = page;
      stateOwnedJob.total = pages;
      stateOwnedJob.message = `已获取 ${orgRows.length} 条公司概况`;
      page += 1;
    } while (page <= pages);

    // ---- 过滤：沪深主板 + 北交所，排除创业板、科创板、ST ----
    const seen = new Set<string>();
    let candidates: any[] = [];
    for (const row of orgRows) {
      const type = String(row?.SECURITY_TYPE || '');
      if (!A_SHARE_TYPES.has(type)) continue;
      const code = String(row?.SECURITY_CODE || '').trim();
      if (!/^\d{6}$/.test(code) || seen.has(code)) continue;
      const name = String(row?.SECURITY_NAME_ABBR || '').trim();
      if (/ST|退/.test(name)) continue;
      seen.add(code);
      candidates.push({
        code,
        name,
        board: boardOf(type),
        actualHolder: String(row?.ACTUAL_HOLDER || '').trim(),
      });
    }

    const afterFilter = candidates.length;
    if (options.limit) candidates = candidates.slice(0, options.limit);

    // ---- 阶段 2：拉行情，顺带过滤掉已退市 / 无行情的历史代码 ----
    stateOwnedJob.phase = 'quotes';
    stateOwnedJob.label = '校验上市状态并拉取行情';
    stateOwnedJob.done = 0;
    stateOwnedJob.total = candidates.length;
    stateOwnedJob.message = '正在批量拉取行情…';

    const tencentQuotes = await fetchTencentQuotes(candidates.map(row => row.code));
    const liveRows = candidates.filter(row => tencentQuotes.has(row.code));

    // ---- 阶段 3：先用国资委央企名录把央企子公司归类，剩余的再穿透 ----
    const centralSoeNames = await loadCentralSoeNames();
    const centralMatched = new Map<string, string>();
    for (const row of liveRows) {
      const base = classifyBulkHolder(row.actualHolder);
      if (base.category !== 'state-group') continue;
      const matched = matchCentralSoe(row.actualHolder, centralSoeNames);
      if (matched) centralMatched.set(row.code, matched);
    }

    const refineList = liveRows.filter(row => {
      const base = classifyBulkHolder(row.actualHolder);
      if (!['state-group', 'other'].includes(base.category)) return false;
      return !centralMatched.has(row.code);
    });
    stateOwnedJob.phase = 'controllers';
    stateOwnedJob.label = '穿透国有法人的最终控制人';
    stateOwnedJob.done = 0;
    stateOwnedJob.total = refineList.length;
    stateOwnedJob.message = refineList.length
      ? `需要穿透 ${refineList.length} 只（首次较慢，之后命中 30 天缓存）`
      : '无需穿透';

    const refinedMap = new Map<string, any>();
    for (const row of refineList) {
      const secCode = `${marketPrefixOf(row.code)}${row.code}`;
      try {
        const item = await resolveController(secCode, options.force);
        refinedMap.set(row.code, item);
      } catch (error: any) {
        refinedMap.set(row.code, { category: 'unknown', note: error?.message || '穿透失败' });
      }
      stateOwnedJob.done += 1;
      stateOwnedJob.message = `已穿透 ${stateOwnedJob.done}/${stateOwnedJob.total} 只国有法人`;
    }

    // ---- 阶段 4：汇总、分类、保存 ----
    stateOwnedJob.phase = 'save';
    stateOwnedJob.label = '汇总并保存';
    stateOwnedJob.done = 0;
    stateOwnedJob.total = liveRows.length;

    const rows = liveRows.map(row => {
      const base = classifyBulkHolder(row.actualHolder);
      const refined = refinedMap.get(row.code);
      const quote = tencentQuotes.get(row.code);
      const sourceUrlBase = `https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/Index?type=web&code=${marketPrefixOf(
        row.code,
      )}${row.code}`;

      let category = base.category;
      let label = base.label;
      let finalController = '';
      let controllingHolder = '';
      let source = '东方财富数据中心·公司概况';
      let sourceUrl = sourceUrlBase;
      let note = '';

      const matchedCentral = centralMatched.get(row.code);
      if (matchedCentral) {
        category = 'sasac-central';
        label = STATE_CATEGORY_LABELS['sasac-central'];
        source = '东方财富数据中心 + 国资委央企名录';
        sourceUrl = CENTRAL_SOE_URL;
        note = `央企名录匹配：${matchedCentral}`;
      }

      if (refined) {
        finalController = String(refined.finalController || '');
        controllingHolder = String(refined.controllingHolder || '');
        const mapped = mapThsCategory(String(refined.category || ''));
        if (mapped) {
          category = mapped;
          label = STATE_CATEGORY_LABELS[mapped] || label;
        }
        source = '同花顺 F10·公司资料（穿透）';
        sourceUrl = String(refined.sourceUrl || `https://basic.10jqka.com.cn/${row.code}/company.html`);
        note = String(refined.note || '');
        if (category === 'state-group' && !note) {
          const fallbackMatched = matchCentralSoe(
            [refined.actualController, refined.controllingHolder, refined.finalController].filter(Boolean).join('、'),
            centralSoeNames,
          );
          if (fallbackMatched) {
            category = 'sasac-central';
            label = STATE_CATEGORY_LABELS['sasac-central'];
            note = `央企名录匹配：${fallbackMatched}`;
          } else {
            note = '仅披露到国有集团层面，建议人工核对';
          }
        }
      }

      return {
        code: row.code,
        name: row.name,
        board: row.board,
        category,
        categoryLabel: label,
        actualHolder: row.actualHolder,
        finalController,
        controllingHolder,
        source,
        sourceUrl,
        note,
        price: quote?.price ?? null,
        changePct: quote?.changePct ?? null,
        totalMarketValue: null,
      };
    });

    rows.sort((a, b) => (a.code < b.code ? -1 : a.code > b.code ? 1 : 0));

    const saved = {
      updatedAt: new Date().toISOString(),
      total: rows.length,
      counts: countByCategory(rows),
      sourceStats: {
        orgInfoRows: orgRows.length,
        afterBoardAndFilter: afterFilter,
        liveRows: liveRows.length,
        refinedRows: refinedMap.size,
        centralSoeNames: centralSoeNames.length,
        centralMatched: centralMatched.size,
      },
      rows,
    };
    await fs.mkdir(DATA_DIR, { recursive: true });
    await fs.writeFile(STATE_OWNED_FILE, `${JSON.stringify(saved)}\n`, 'utf-8');

    stateOwnedJob.phase = 'done';
    stateOwnedJob.label = '完成';
    stateOwnedJob.done = rows.length;
    stateOwnedJob.total = rows.length;
    stateOwnedJob.message = `已保存 ${rows.length} 只，其中国资/政府控股 ${
      rows.filter(row => STATE_CATEGORIES.has(row.category)).length
    } 只`;
  } catch (error: any) {
    stateOwnedJob.phase = 'error';
    stateOwnedJob.label = '失败';
    stateOwnedJob.error = error?.message || '扫描失败';
    stateOwnedJob.message = stateOwnedJob.error;
  } finally {
    stateOwnedJob.running = false;
    stateOwnedJob.finishedAt = new Date().toISOString();
  }
}


// ---------------- MSCI：批量行业 / PE / ROE 分析 ----------------

const ORG_INFO_BASE =
  'https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_BASIC_ORGINFO&columns=SECURITY_CODE,SECURITY_NAME_ABBR,EM2016&pageNumber=';
const ORG_INFO_CACHE_TTL_MS = 2 * 24 * 60 * 60 * 1000;
const MAIN_FIN_DATA_BASE =
  'https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_FINANCE_MAINFINADATA&columns=SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,REPORT_DATE_NAME,ROEJQ';

let orgInfoCache: { fetchedAt: number; map: Record<string, { name: string; industry: string }> } | null = null;

/** 全市场行业分类（EM2016），落盘缓存 2 天 */
async function loadOrgInfoMap(): Promise<Record<string, { name: string; industry: string }>> {
  if (orgInfoCache && Date.now() - orgInfoCache.fetchedAt < ORG_INFO_CACHE_TTL_MS) return orgInfoCache.map;
  try {
    const raw = await fs.readFile(ORG_INFO_CACHE_FILE, 'utf-8');
    const parsed = JSON.parse(raw);
    if (parsed?.map && Date.now() - Number(parsed.fetchedAt || 0) < ORG_INFO_CACHE_TTL_MS) {
      orgInfoCache = { fetchedAt: Number(parsed.fetchedAt), map: parsed.map };
      return parsed.map;
    }
  } catch {
    // 无缓存，走网络
  }

  const map: Record<string, { name: string; industry: string }> = {};
  let page = 1;
  let pages = 1;
  do {
    const json = await fetchJsonByHttps(`${ORG_INFO_BASE}${page}&pageSize=500`, {
      Referer: 'https://data.eastmoney.com/',
    });
    const data = Array.isArray(json?.result?.data) ? json.result.data : [];
    if (!data.length) break;
    for (const row of data) {
      const code = String(row?.SECURITY_CODE || '').trim();
      if (!/^\d{6}$/.test(code)) continue;
      const industry = String(row?.EM2016 || '').trim();
      map[code] = { name: String(row?.SECURITY_NAME_ABBR || '').trim(), industry };
    }
    pages = Number(json?.result?.pages) || pages;
    page += 1;
  } while (page <= pages);

  orgInfoCache = { fetchedAt: Date.now(), map };
  await fs.mkdir(CACHE_DIR, { recursive: true });
  await fs.writeFile(
    ORG_INFO_CACHE_FILE,
    `${JSON.stringify({ fetchedAt: Date.now(), map })}\n`,
    'utf-8',
  );
  return map;
}

/** 批量 ROE（东财数据中心·主要财务指标，按报告期倒序，取每只股票最新一期） */
async function fetchBulkRoe(codes: string[]): Promise<Map<string, { roe: number; report: string }>> {
  const result = new Map<string, { roe: number; report: string }>();
  for (const chunk of chunkArray(codes, 80)) {
    try {
      const filter = `(SECURITY_CODE in (${chunk.map(code => `"${code}"`).join(',')}))`;
      let page = 1;
      let pages = 1;
      do {
        const url = `${MAIN_FIN_DATA_BASE}&filter=${encodeURIComponent(
          filter,
        )}&sortColumns=REPORT_DATE&sortTypes=-1&pageNumber=${page}&pageSize=500`;
        const json = await fetchJsonByHttps(url, { Referer: 'https://data.eastmoney.com/' });
        const data = Array.isArray(json?.result?.data) ? json.result.data : [];
        pages = Number(json?.result?.pages) || 1;
        for (const row of data) {
          const code = String(row?.SECURITY_CODE || '').trim();
          if (!code || result.has(code)) continue;
          const roe = Number(row?.ROEJQ);
          if (!Number.isFinite(roe)) continue;
          result.set(code, { roe, report: String(row?.REPORT_DATE_NAME || '').trim() });
        }
        page += 1;
      } while (page <= pages && page <= 4);
    } catch (error: any) {
      console.warn('[msci] 批量 ROE 失败:', error?.message || error);
    }
  }
  return result;
}

type MsciJobState = {
  running: boolean;
  phase: string;
  label: string;
  done: number;
  total: number;
  message: string;
  startedAt: string | null;
  finishedAt: string | null;
  error: string;
};

let msciJob: MsciJobState = {
  running: false,
  phase: 'idle',
  label: '空闲',
  done: 0,
  total: 0,
  message: '',
  startedAt: null,
  finishedAt: null,
  error: '',
};

async function runMsciAnalyzeJob() {
  const startedAt = new Date().toISOString();
  msciJob = {
    running: true,
    phase: 'industry',
    label: '拉取行业分类',
    done: 0,
    total: 0,
    message: '',
    startedAt,
    finishedAt: null,
    error: '',
  };

  try {
    const current = await readMsciData();
    const rows: any[] = current.rows;
    if (!rows.length) throw new Error('还没有导入 MSCI 数据');

    const codes: string[] = rows
      .map((row: any) => String(row.code || '').trim())
      .filter((code: string) => /^\d{6}$/.test(code));

    // 1) 行业分类
    const orgInfo = await loadOrgInfoMap();
    let matchedIndustry = 0;
    for (const row of rows) {
      const info = orgInfo[String(row.code || '')];
      if (info?.industry) matchedIndustry += 1;
    }
    msciJob.phase = 'quotes';
    msciJob.label = '拉取 PE(TTM) 与现价';
    msciJob.done = 0;
    msciJob.total = codes.length;
    msciJob.message = `行业匹配 ${matchedIndustry}/${rows.length}`;

    // 2) PE(TTM) / 现价 / 市值（腾讯行情，一次批量）
    const quotes = await fetchTencentQuotes(codes);
    msciJob.phase = 'roe';
    msciJob.label = '拉取 ROE';
    msciJob.done = 0;
    msciJob.total = codes.length;
    msciJob.message = '正在批量查询 ROE…';

    // 3) ROE（先批量，再对缺失的逐只兜底）
    const bulkRoe = await fetchBulkRoe(codes);
    const missing: string[] = codes.filter((code: string) => !bulkRoe.has(code));
    const roeCache = await ensureCacheLoaded('roe');
    for (const [code, info] of bulkRoe) {
      roeCache[code] = { value: info.roe, ts: Date.now() };
    }
    persistCache('roe');

    if (missing.length) {
      msciJob.total = missing.length;
      let index = 0;
      for (const code of missing) {
        const secPrefix = /^(43|83|87|92)/.test(code) ? 'BJ' : /^[659]/.test(code) ? 'SH' : 'SZ';
        try {
          const { items } = await resolveRoe([`${secPrefix}${code}`]);
          const info = items[`${secPrefix}${code}`];
          if (info && Number.isFinite(Number(info.roe))) {
            bulkRoe.set(code, { roe: Number(info.roe), report: '最新财报' });
          }
        } catch {
          // 单只失败忽略
        }
        index += 1;
        msciJob.done = index;
        msciJob.message = `逐只补查 ROE ${index}/${missing.length}`;
      }
    }

    // 4) 合并并保存
    msciJob.phase = 'save';
    msciJob.label = '汇总并保存';
    msciJob.done = 0;
    msciJob.total = rows.length;
    const analyzedAt = new Date().toISOString();

    const nextRows = rows.map((row: any) => {
      const code = String(row.code || '');
      const info = orgInfo[code];
      const quote = quotes.get(code);
      const roeInfo = bulkRoe.get(code);
      const industry = info?.industry || '';
      const parts = industry.split('-').filter(Boolean);
      return {
        ...row,
        name: row.name || info?.name || quote?.name || '',
        industry,
        industryGroup: parts.length ? parts[parts.length - 1] : '未分类',
        industryParent: parts.length >= 2 ? parts.slice(0, -1).join('-') : '',
        price: quote?.price ?? null,
        changePct: quote?.changePct ?? null,
        peTtm: quote?.peTtm ?? null,
        pb: quote?.pb ?? null,
        marketCapYi: quote?.marketCapYi ?? null,
        turnover: quote?.turnover ?? null,
        roe: roeInfo ? roeInfo.roe : null,
        roeReport: roeInfo?.report || '',
        analyzedAt,
      };
    });

    const saved = {
      ...current,
      rows: nextRows,
      analyzedAt,
    };
    await fs.mkdir(DATA_DIR, { recursive: true });
    await fs.writeFile(MSCI_FILE, `${JSON.stringify(saved, null, 2)}\n`, 'utf-8');

    msciJob.phase = 'done';
    msciJob.label = '完成';
    msciJob.done = nextRows.length;
    msciJob.total = nextRows.length;
    msciJob.message = `已分析 ${nextRows.length} 只，行业匹配 ${matchedIndustry} 只`;
  } catch (error: any) {
    msciJob.phase = 'error';
    msciJob.label = '失败';
    msciJob.error = error?.message || '分析失败';
    msciJob.message = msciJob.error;
  } finally {
    msciJob.running = false;
    msciJob.finishedAt = new Date().toISOString();
  }
}

function portfolioApiPlugin() {
  const portfolioRoute = '/api/portfolio';
  const watchlistRoute = '/api/watchlist';

  const applyMiddleware = (middlewares: { use: (handler: (req: any, res: any, next: () => void) => void) => void }) => {
    middlewares.use(async (req, res, next) => {
      const pathname = String(req.url || '').split('?')[0];
      const reqUrl = new URL(String(req.url || ''), 'http://127.0.0.1');

      // ---------- 行情 ----------
      if (pathname === QUOTE_ROUTE) {
        if (req.method !== 'GET') return sendJson(res, 405, { message: 'Method Not Allowed' });

        const rawSecids = String(reqUrl.searchParams.get('secids') || '');
        const secids = Array.from(new Set(rawSecids.split(',').map(item => item.trim()).filter(Boolean)));
        if (!secids.length || secids.some(secid => !/^[01]\.\d{6}$/.test(secid))) {
          return sendJson(res, 400, { message: 'secids 参数格式错误，应为 1.600519,0.000725' });
        }

        const result = await resolveQuotes(secids);
        if (!result.diff.length) {
          return sendJson(res, 502, { message: result.message || '未能获取到行情数据', diff: [] });
        }
        return sendJson(res, 200, {
          diff: result.diff,
          fetchedAt: Date.now(),
          stale: result.stale,
          message: result.message && result.stale ? `行情接口暂不可用，已使用缓存数据（${result.message}）` : '',
        });
      }

      // ---------- ROE ----------
      if (pathname === ROE_ROUTE) {
        if (req.method !== 'GET') return sendJson(res, 405, { message: 'Method Not Allowed' });

        const rawCodes = String(reqUrl.searchParams.get('codes') || reqUrl.searchParams.get('code') || '');
        const codes = Array.from(
          new Set(
            rawCodes
              .split(',')
              .map(item => item.trim().toUpperCase())
              .filter(Boolean),
          ),
        );
        if (!codes.length || codes.some(code => !/^(SH|SZ)\d{6}$/.test(code))) {
          return sendJson(res, 400, { message: 'codes 参数格式错误，应为 SH600519,SZ000725' });
        }

        const result = await resolveRoe(codes);
        return sendJson(res, 200, { items: result.items, fetchedAt: Date.now(), message: result.message });
      }

      // ---------- MSCI：分析（行业 / PE / ROE） ----------
      if (pathname === MSCI_ANALYZE_ROUTE && req.method === 'POST') {
        if (msciJob.running) return sendJson(res, 409, { message: '分析任务正在进行中', job: msciJob });
        void runMsciAnalyzeJob();
        return sendJson(res, 200, { started: true, job: msciJob });
      }

      if (pathname === MSCI_PROGRESS_ROUTE && req.method === 'GET') {
        return sendJson(res, 200, { job: msciJob });
      }

      // ---------- MSCI：读取已保存数据 ----------
      if (pathname === MSCI_ROUTE && req.method === 'GET') {
        return sendJson(res, 200, await readMsciData());
      }

      // ---------- MSCI：保存导入结果 ----------
      if (pathname === MSCI_ROUTE && req.method === 'POST') {
        try {
          const body = await readRequestBody(req);
          const payload = JSON.parse(body.toString('utf-8') || '{}');
          const rows = Array.isArray(payload?.rows) ? payload.rows : [];
          const columns = Array.isArray(payload?.columns) ? payload.columns.map(String) : [];
          const normalized = rows
            .map((row: any) => ({
              code: String(row?.code || '').trim(),
              name: String(row?.name || '').trim(),
              extra: row?.extra && typeof row.extra === 'object' ? row.extra : {},
              quote: row?.quote && typeof row.quote === 'object' ? row.quote : null,
            }))
            .filter((row: any) => /^\d{6}$/.test(row.code));

          const saved = {
            fileName: String(payload?.fileName || ''),
            importedAt: payload?.importedAt || new Date().toISOString(),
            updatedAt: payload?.updatedAt || null,
            columns,
            rows: normalized,
          };
          await fs.mkdir(DATA_DIR, { recursive: true });
          await fs.writeFile(MSCI_FILE, `${JSON.stringify(saved, null, 2)}\n`, 'utf-8');
          return sendJson(res, 200, { ok: true, count: normalized.length });
        } catch (error: any) {
          return sendJson(res, 400, { message: error?.message || '保存失败' });
        }
      }

      // ---------- MSCI：解析上传的 TXT / Excel ----------
      if (pathname === MSCI_PARSE_ROUTE && req.method === 'POST') {
        try {
          const body = await readRequestBody(req);
          const payload = JSON.parse(body.toString('utf-8') || '{}');
          const fileName = String(payload?.fileName || 'import.txt');
          const base64 = String(payload?.dataBase64 || '');
          if (!base64) return sendJson(res, 400, { message: '没有收到文件内容' });

          const buffer = Buffer.from(base64, 'base64');
          if (!buffer.length) return sendJson(res, 400, { message: '文件内容为空' });

          const matrix = readSheetMatrix(fileName, buffer);
          const parsed = normalizeMsciMatrix(matrix);
          if (!parsed.rows.length) {
            return sendJson(res, 400, { message: parsed.warnings[0] || '解析失败', warnings: parsed.warnings, rows: [] });
          }
          return sendJson(res, 200, { ok: true, fileName, ...parsed, total: parsed.rows.length });
        } catch (error: any) {
          return sendJson(res, 400, { message: error?.message || '文件解析失败' });
        }
      }

      // ---------- MSCI：刷新行情数据 ----------
      if (pathname === MSCI_REFRESH_ROUTE && req.method === 'POST') {
        try {
          const current = await readMsciData();
          if (!current.rows.length) return sendJson(res, 400, { message: '还没有导入 MSCI 数据' });

          const enriched = await enrichMsciRows(current.rows);
          const saved = { ...current, rows: enriched.rows, updatedAt: enriched.updatedAt || current.updatedAt };
          await fs.mkdir(DATA_DIR, { recursive: true });
          await fs.writeFile(MSCI_FILE, `${JSON.stringify(saved, null, 2)}\n`, 'utf-8');
          return sendJson(res, 200, {
            ok: true,
            updatedAt: saved.updatedAt,
            total: saved.rows.length,
            message: enriched.message || '',
          });
        } catch (error: any) {
          return sendJson(res, 500, { message: error?.message || '更新失败' });
        }
      }

      // ---------- 实际控制人 ----------
      if (pathname === CONTROLLER_ROUTE) {
        if (req.method !== 'GET') return sendJson(res, 405, { message: 'Method Not Allowed' });

        const rawCodes = String(reqUrl.searchParams.get('codes') || '');
        const codes = Array.from(
          new Set(
            rawCodes
              .split(',')
              .map(item => item.trim().toUpperCase())
              .filter(Boolean),
          ),
        );
        if (!codes.length || codes.some(code => !/^(SH|SZ|BJ)\d{6}$/.test(code))) {
          return sendJson(res, 400, { message: 'codes 参数格式错误，应为 SH600519,SZ000725' });
        }

        const forceRefresh = String(reqUrl.searchParams.get('refresh') || '') === '1';
        const items: Record<string, any> = {};
        let message = '';
        for (const code of codes) {
          try {
            items[code] = await resolveController(code, forceRefresh);
          } catch (error: any) {
            items[code] = {
              code,
              category: 'unknown',
              categoryLabel: CATEGORY_LABELS.unknown,
              note: error?.message || '抓取失败',
              fetchedAt: new Date().toISOString(),
            };
          }
        }
        return sendJson(res, 200, { items, message });
      }

      // ---------- 全市场国资 / 政府控股 ----------
      if (pathname === STATE_OWNED_ROUTE && req.method === 'GET') {
        const data = await readStateOwnedData();
        return sendJson(res, 200, { ...data, job: stateOwnedJob });
      }

      if (pathname === STATE_OWNED_PROGRESS_ROUTE && req.method === 'GET') {
        return sendJson(res, 200, { job: stateOwnedJob });
      }

      if (pathname === STATE_OWNED_START_ROUTE && req.method === 'POST') {
        if (stateOwnedJob.running) {
          return sendJson(res, 409, { message: '刷新任务正在进行中，请等待完成', job: stateOwnedJob });
        }
        let payload: any = {};
        try {
          const raw = (await readRequestBody(req)).toString('utf-8');
          payload = raw ? JSON.parse(raw) : {};
        } catch {
          payload = {};
        }
        const force = Boolean(payload?.force);
        const limit = Number(payload?.limit) > 0 ? Number(payload.limit) : undefined;
        void runStateOwnedJob({ force, limit });
        return sendJson(res, 200, { started: true, force, limit: limit || null, job: stateOwnedJob });
      }

      // ---------- 已清仓记录 ----------
      if (pathname === STOCK_NAME_ROUTE && req.method === 'GET') {
        const rawCodes = String(reqUrl.searchParams.get('codes') || '');
        const codes = Array.from(
          new Set(
            rawCodes
              .split(',')
              .map(item => item.trim())
              .filter(code => /^\d{6}$/.test(code)),
          ),
        ).slice(0, 50);
        if (!codes.length) return sendJson(res, 400, { message: 'codes 参数格式错误' });

        const quotes = await fetchTencentQuotes(codes);
        const items: Record<string, any> = {};
        for (const [code, info] of quotes) items[code] = info;
        return sendJson(res, 200, { items });
      }

      if (pathname === CLOSED_POSITIONS_ROUTE) {
        if (req.method === 'GET') {
          try {
            const raw = await fs.readFile(CLOSED_POSITIONS_FILE, 'utf-8');
            const parsed = JSON.parse(raw);
            return sendJson(res, 200, Array.isArray(parsed) ? parsed : []);
          } catch {
            return sendJson(res, 200, []);
          }
        }

        if (req.method === 'POST') {
          try {
            const raw = (await readRequestBody(req)).toString('utf-8') || '[]';
            const parsed = JSON.parse(raw);
            if (!Array.isArray(parsed)) return sendJson(res, 400, { message: 'payload 必须是数组' });

            const normalized = parsed
              .map((row: any) => ({
                id: Number(row?.id) || Date.now(),
                code: String(row?.code || '').trim(),
                name: String(row?.name || '').trim(),
                quantity: Number(row?.quantity) || 0,
                buyPrice: Number(row?.buyPrice) || 0,
                sellPrice: Number(row?.sellPrice) || 0,
                fee: Number(row?.fee) || 0,
                buyDate: String(row?.buyDate || ''),
                sellDate: String(row?.sellDate || ''),
                profitAmount: Number(row?.profitAmount) || 0,
                profitRatio: Number(row?.profitRatio) || 0,
                holdDays: row?.holdDays == null ? null : Number(row.holdDays),
                note: String(row?.note || ''),
                createdAt: String(row?.createdAt || new Date().toISOString()),
              }))
              .filter((row: any) => /^\d{6}$/.test(row.code));

            await fs.mkdir(DATA_DIR, { recursive: true });
            await fs.writeFile(CLOSED_POSITIONS_FILE, `${JSON.stringify(normalized, null, 2)}\n`, 'utf-8');
            return sendJson(res, 200, { ok: true, count: normalized.length });
          } catch (error: any) {
            return sendJson(res, 400, { message: error?.message || '保存失败' });
          }
        }

        return sendJson(res, 405, { message: 'Method Not Allowed' });
      }

      if (pathname !== portfolioRoute && pathname !== watchlistRoute) return next();

      const targetFile = pathname === watchlistRoute ? WATCHLIST_FILE : PORTFOLIO_FILE;

      res.setHeader('Content-Type', 'application/json; charset=utf-8');

      try {
        if (req.method === 'GET') {
          let content = '[]';
          try {
            content = await fs.readFile(targetFile, 'utf-8');
          } catch (error: any) {
            if (error?.code === 'ENOENT') {
              await fs.mkdir(path.dirname(targetFile), { recursive: true });
              await fs.writeFile(targetFile, '[]', 'utf-8');
            } else {
              throw error;
            }
          }
          res.statusCode = 200;
          res.end(content);
          return;
        }

        if (req.method === 'POST') {
          const raw = (await readRequestBody(req)).toString('utf-8') || '[]';
          const parsed = JSON.parse(raw);
          if (!Array.isArray(parsed)) {
            res.statusCode = 400;
            res.end(JSON.stringify({ message: 'payload 必须是数组' }));
            return;
          }

          await fs.mkdir(path.dirname(targetFile), { recursive: true });
          await fs.writeFile(targetFile, `${JSON.stringify(parsed, null, 2)}\n`, 'utf-8');
          res.statusCode = 200;
          res.end(JSON.stringify({ ok: true }));
          return;
        }

        res.statusCode = 405;
        res.end(JSON.stringify({ message: 'Method Not Allowed' }));
      } catch (error: any) {
        res.statusCode = 500;
        res.end(JSON.stringify({ message: error?.message || 'server error' }));
      }
    });
  };

  return {
    name: 'portfolio-api-plugin',
    configureServer(server: { middlewares: any }) {
      applyMiddleware(server.middlewares);
    },
    configurePreviewServer(server: { middlewares: any }) {
      applyMiddleware(server.middlewares);
    },
  };
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
    portfolioApiPlugin(),
    // 加上下面这块 server 配置
  ],

  server: {
    port: 3000, // 强制指定端口号为 3000
    strictPort: false, // 设为 false 表示如果 3000 也被占用，Vite 会自动顺延寻找下一个可用端口
  },

  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
});

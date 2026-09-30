// Number and date formatting. Relative times use the dataset clock
// (fmt.setNow(meta.now_utc)), never the wall clock.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const ACRONYMS = new Set([
  'CM', 'OEM', 'PO', 'ATP', 'MRP', 'MPS', 'IQC', 'EOL', 'ASN', 'ERP', 'WMS', 'TMS', 'QMS', 'DG', 'ISF',
  'ETA', 'ETD', '3PL', 'VMI', 'FPY', 'OTD', 'RMA', 'NCR', 'JE', 'EDI', 'MES', 'BMS', 'DU', 'HMI', 'PU',
  'SKU', 'US', 'TW', 'UTC', 'PT', 'API', 'SQL', 'ID', 'RTV', 'LTL', 'FTL', 'RFQ', 'ECO', 'HTS', 'MPF',
  'HMF', 'PPM', 'AVL', 'QC', 'WIP', 'FG', 'KPI', 'USD', 'AQL', 'CBP', 'PDF', 'CSV', 'EOQ', 'MOQ', 'D2C',
]);

let NOW = null;
let TZ = 'America/Los_Angeles';

const dtfCache = new Map();
function dtf(opts) {
  const key = JSON.stringify(opts);
  if (!dtfCache.has(key)) dtfCache.set(key, new Intl.DateTimeFormat('en-US', opts));
  return dtfCache.get(key);
}

function parse(v) {
  if (v == null || v === '') return null;
  if (v instanceof Date) return v;
  if (typeof v === 'number') return new Date(v);
  if (typeof v === 'string') {
    if (DATE_ONLY.test(v)) return new Date(v + 'T00:00:00Z');
    const d = new Date(v);
    return isNaN(d) ? null : d;
  }
  return null;
}

function isDateOnly(v) {
  return typeof v === 'string' && DATE_ONLY.test(v);
}

function parts(d, opts) {
  const out = {};
  for (const p of dtf(opts).formatToParts(d)) out[p.type] = p.value;
  return out;
}

function numOr(n) {
  return n == null || n === '' || Number.isNaN(Number(n));
}

export const fmt = {
  setNow(iso) { NOW = parse(iso); },
  now() { return NOW || new Date(); },
  setTz(tz) { TZ = tz; },
  get tz() { return TZ; },

  int(n) {
    if (numOr(n)) return '—';
    return Math.round(Number(n)).toLocaleString('en-US');
  },

  num(n, d = 1) {
    if (numOr(n)) return '—';
    return Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  },

  // 1,284 / 12.9K / 4.2M
  compact(n) {
    if (numOr(n)) return '—';
    const v = Number(n), a = Math.abs(v), sign = v < 0 ? '−' : '';
    const trim = (x) => x.toFixed(1).replace(/\.0$/, '');
    if (a < 10000) {
      return sign + (Number.isInteger(a) ? a.toLocaleString('en-US') : a.toLocaleString('en-US', { maximumFractionDigits: 2 }));
    }
    if (a < 1e6) return sign + trim(a / 1e3) + 'K';
    if (a < 1e9) return sign + trim(a / 1e6) + 'M';
    return sign + trim(a / 1e9) + 'B';
  },

  // fraction -> percent string
  pct(x, d = 1) {
    if (numOr(x)) return '—';
    return (Number(x) * 100).toFixed(d) + '%';
  },

  usd(n, { compact = false, cents } = {}) {
    if (numOr(n)) return '—';
    const v = Number(n), a = Math.abs(v), sign = v < 0 ? '−' : '';
    if (compact && a >= 10000) return sign + '$' + fmt.compact(a);
    const digits = cents != null ? (cents ? 2 : 0) : (a < 100 && !Number.isInteger(a) ? 2 : 0);
    return sign + '$' + a.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  },

  // 'Sep 26'
  date(v, tz = TZ) {
    if (isDateOnly(v)) {
      const [, m, d] = v.split('-');
      return `${MONTHS[+m - 1]} ${+d}`;
    }
    const d = parse(v);
    if (!d) return '—';
    const p = parts(d, { timeZone: tz, month: 'short', day: 'numeric' });
    return `${p.month} ${p.day}`;
  },

  // 'Sep 26, 2026'
  dateLong(v, tz = TZ) {
    if (isDateOnly(v)) {
      const [y, m, d] = v.split('-');
      return `${MONTHS[+m - 1]} ${+d}, ${y}`;
    }
    const d = parse(v);
    if (!d) return '—';
    const p = parts(d, { timeZone: tz, year: 'numeric', month: 'short', day: 'numeric' });
    return `${p.month} ${p.day}, ${p.year}`;
  },

  // 'Sep 26 08:14'
  dt(v, tz = TZ) {
    if (isDateOnly(v)) return fmt.date(v);
    const d = parse(v);
    if (!d) return '—';
    const p = parts(d, { timeZone: tz, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
    return `${p.month} ${p.day} ${p.hour}:${p.minute}`;
  },

  time(v, tz = TZ) {
    const d = parse(v);
    if (!d) return '—';
    const p = parts(d, { timeZone: tz, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
    return `${p.hour}:${p.minute}`;
  },

  // '3h ago' / 'in 2d' relative to the dataset clock
  rel(v, nowV) {
    const d = parse(v);
    if (!d) return '—';
    const now = parse(nowV) || fmt.now();
    let s = (d.getTime() - now.getTime()) / 1000;
    const future = s > 0;
    s = Math.abs(s);
    let out;
    if (isDateOnly(v)) {
      const days = Math.round(s / 86400);
      if (days === 0) return 'today';
      out = `${days}d`;
    } else if (s < 60) return 'just now';
    else if (s < 3600) out = `${Math.round(s / 60)}m`;
    else if (s < 86400) out = `${Math.round(s / 3600)}h`;
    else if (s < 86400 * 60) out = `${Math.round(s / 86400)}d`;
    else return fmt.date(v);
    return future ? `in ${out}` : `${out} ago`;
  },

  days(n) {
    if (numOr(n)) return '—';
    const v = Math.round(Number(n));
    return `${v < 0 ? '−' : ''}${Math.abs(v)}d`;
  },

  // ENUM_CASE -> 'Enum case' (acronyms kept)
  title(v) {
    if (v == null || v === '') return '—';
    const words = String(v).split(/[_\s]+/).filter(Boolean);
    return words.map((w, i) => {
      const up = w.toUpperCase();
      if (ACRONYMS.has(up)) return up;
      const lo = w.toLowerCase();
      return i === 0 ? lo.charAt(0).toUpperCase() + lo.slice(1) : lo;
    }).join(' ');
  },

  ms(n) {
    if (numOr(n)) return '—';
    const v = Number(n);
    return v < 10 ? `${v.toFixed(1)} ms` : `${Math.round(v).toLocaleString('en-US')} ms`;
  },
};

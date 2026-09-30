// SVG charts that follow the dataviz rules in docs/DESIGN.md.
//
// Every chart returns an html`` <figure class="chart" data-chart-id> fragment.
// A registry keyed by that id holds the spec, so charts can re-render at their
// real pixel width (ResizeObserver) and so one document-level pointer handler
// can drive crosshairs and tooltips after any innerHTML render.
//
//   charts.line({ series: [{ name, points: [{ x, y }] }], height, yFormat, xFormat, area, refLines, yMin, yMax, markers })
//   charts.bar({ categories, series: [{ name, values }], stacked, horizontal, height, yFormat, colors, valueLabels })
//   charts.custom({ height, render: (width) => '<svg inner markup>', table, legend })   // add data-tip="…" to marks
//   charts.sparkline(values, { width, height })
//   charts.legend(series, 'line' | 'rect'), charts.table({ columns, rows })
import { html, raw, esc, toHTML } from './dom.js';
import { fmt } from './format.js';

const registry = new Map();
let seq = 0;
const nextId = () => `ch${++seq}`;
const DEFAULT_W = 720;
const DAY = 86400000;
const DATE_RE = /^\d{4}-\d{2}-\d{2}/;

// ---------------------------------------------------------------------------
// palette & scales (exported for hand-built charts)
// ---------------------------------------------------------------------------
export const SERIES_SLOTS = 8;
export function palette(i) {
  return i >= 0 && i < SERIES_SLOTS ? `var(--series-${i + 1})` : 'var(--series-other)';
}
export const STATUS_COLOR = {
  good: 'var(--good)', warning: 'var(--warning)', serious: 'var(--serious)', critical: 'var(--critical)',
  info: 'var(--info)', neutral: 'var(--ink-3)',
};

export function scaleLinear(domain, range) {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0);
  const f = (v) => r0 + (v - d0) * k;
  f.invert = (p) => (k === 0 ? d0 : d0 + (p - r0) / k);
  f.domain = domain;
  f.range = range;
  return f;
}

export function scaleBand(domain, range, padding = 0.25) {
  const n = Math.max(domain.length, 1);
  const step = (range[1] - range[0]) / n;
  const bw = step * (1 - padding);
  const index = new Map(domain.map((d, i) => [d, i]));
  const f = (v) => range[0] + (index.get(v) ?? 0) * step + (step - bw) / 2;
  f.bandwidth = bw;
  f.step = step;
  return f;
}

export function niceTicks(min, max, count = 5) {
  if (!isFinite(min) || !isFinite(max)) return [0, 1];
  if (min === max) {
    if (min === 0) return [0, 1];
    const pad = Math.abs(min) * 0.1;
    min -= pad;
    max += pad;
  }
  const span = max - min;
  const step0 = span / Math.max(1, count);
  const power = Math.floor(Math.log10(step0));
  const err = step0 / Math.pow(10, power);
  const factor = err >= 7.07 ? 10 : err >= 3.16 ? 5 : err >= 1.41 ? 2 : 1;
  const step = factor * Math.pow(10, power);
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

function toMs(x) {
  if (x instanceof Date) return x.getTime();
  if (typeof x === 'number') return x;
  if (typeof x === 'string' && DATE_RE.test(x)) return Date.parse(x.length === 10 ? `${x}T00:00:00Z` : x);
  return NaN;
}

function xKind(values) {
  if (!values.length) return 'cat';
  if (values.every((v) => typeof v === 'number')) return 'num';
  if (values.every((v) => v instanceof Date || (typeof v === 'string' && DATE_RE.test(v)))) return 'time';
  return 'cat';
}

const isoDay = (ms) => new Date(ms).toISOString().slice(0, 10);
const textW = (s, px = 11) => String(s).length * px * 0.58;
const r1 = (v) => Math.round(v * 10) / 10;
const crisp = (v) => Math.round(v) + 0.5;

function timeTicks(min, max, maxTicks) {
  const days = (max - min) / DAY;
  const steps = [1, 2, 7, 14, 28, 56, 91, 182, 364];
  const step = steps.find((s) => days / s <= maxTicks) || 364;
  const ticks = [];
  let start = Math.ceil(min / DAY) * DAY;
  if (step === 7 || step === 14) {
    // align weekly ticks to Mondays
    while (new Date(start).getUTCDay() !== 1) start += DAY;
  }
  for (let t = start; t <= max + 1; t += step * DAY) ticks.push(t);
  return ticks;
}

// ---------------------------------------------------------------------------
// figure scaffold
// ---------------------------------------------------------------------------
function figure(id, entry, { legend, ariaLabel } = {}) {
  registry.set(id, entry);
  pruneRegistry();
  const out = entry.render(DEFAULT_W);
  entry.width = DEFAULT_W;
  entry.geom = out.geom;
  return html`<figure class="chart" data-chart-id="${id}" data-kind="${entry.kind}"${ariaLabel ? raw(` aria-label="${esc(ariaLabel)}"`) : ''}>
    ${legend || ''}
    <div class="chart-svg">${raw(out.svg)}</div>
    <div class="chart-tableview" hidden></div>
  </figure>`;
}

function pruneRegistry() {
  if (registry.size < 120) return;
  for (const id of registry.keys()) {
    if (!document.querySelector(`[data-chart-id="${id}"]`)) registry.delete(id);
  }
}

// ---------------------------------------------------------------------------
// line chart
// ---------------------------------------------------------------------------
function line(spec = {}) {
  const series = (spec.series || []).map((s, i) => ({
    ...s,
    color: s.color || palette(s.colorIndex ?? i),
    points: (s.points || []).filter((p) => p && p.y != null && isFinite(p.y)),
  }));
  const allX = series.flatMap((s) => s.points.map((p) => p.x));
  const kind = spec.xType || xKind(allX);
  const id = nextId();
  const entry = {
    kind: 'line',
    spec: { ...spec, _series: series, _kind: kind },
    render: (w) => renderLine(entry.spec, w),
    hover: hoverLine,
    table: () => lineTable(entry.spec),
  };
  const legend = series.length > 1 && spec.legend !== false ? legendHTML(series, 'line') : '';
  return figure(id, entry, { legend, ariaLabel: spec.ariaLabel });
}

function xKeyOf(kind, x) {
  return kind === 'time' ? toMs(x) : kind === 'num' ? Number(x) : String(x);
}

function renderLine(spec, width) {
  const H = spec.height || 220;
  const series = spec._series;
  const kind = spec._kind;
  const yFmt = spec.yFormat || ((v) => fmt.compact(v));
  const xFmt = spec.xFormat || (kind === 'time' ? (ms) => fmt.date(isoDay(ms)) : (v) => String(v));

  // y domain
  const ys = series.flatMap((s) => s.points.map((p) => p.y)).concat((spec.refLines || []).map((r) => r.y));
  let lo = ys.length ? Math.min(...ys) : 0;
  let hi = ys.length ? Math.max(...ys) : 1;
  if (spec.zero !== false) lo = Math.min(0, lo);
  if (spec.yMin != null) lo = spec.yMin;
  if (spec.yMax != null) hi = spec.yMax;
  const ticks = niceTicks(lo, hi, Math.max(2, Math.round(H / 56)));
  const y0 = spec.yMin != null ? spec.yMin : ticks[0];
  const y1 = spec.yMax != null ? spec.yMax : ticks[ticks.length - 1];
  const tickVals = ticks.filter((t) => t >= y0 - 1e-9 && t <= y1 + 1e-9);

  // x keys
  let keys;
  if (kind === 'cat') {
    keys = [];
    const seen = new Set();
    for (const x of series.flatMap((s) => s.points.map((p) => String(p.x)))) {
      if (!seen.has(x)) { seen.add(x); keys.push(x); }
    }
  } else {
    keys = Array.from(new Set(series.flatMap((s) => s.points.map((p) => xKeyOf(kind, p.x))))).sort((a, b) => a - b);
  }

  // margins
  const labelW = Math.max(18, ...tickVals.map((t) => textW(yFmt(t))));
  const showEnd = spec.directLabels !== false && series.length > 1 && series.length <= 4;
  const endW = showEnd ? Math.min(130, Math.max(...series.map((s) => textW(s.name || '', 11.5)))) + 14 : 0;
  const m = { t: 10, r: 14 + endW, b: 26, l: Math.ceil(labelW) + 10 };
  const iw = Math.max(40, width - m.l - m.r);
  const ih = Math.max(40, H - m.t - m.b);

  let sx;
  if (kind === 'cat') {
    const n = keys.length;
    const idx = new Map(keys.map((k, i) => [k, i]));
    sx = (k) => (n <= 1 ? m.l + iw / 2 : m.l + (idx.get(String(k)) / (n - 1)) * iw);
  } else {
    const xmin = spec.xMin != null ? xKeyOf(kind, spec.xMin) : keys[0];
    const xmax = spec.xMax != null ? xKeyOf(kind, spec.xMax) : keys[keys.length - 1];
    const lin = scaleLinear([xmin, xmax === xmin ? xmin + 1 : xmax], [m.l, m.l + iw]);
    sx = (k) => lin(k);
  }
  const sy = scaleLinear([y0, y1], [m.t + ih, m.t]);
  const clampY = (v) => Math.max(m.t, Math.min(m.t + ih, sy(v)));

  let s = '';
  for (const t of tickVals) {
    const y = crisp(sy(t));
    s += `<line class="gridline" x1="${m.l}" x2="${m.l + iw}" y1="${y}" y2="${y}"/>`;
    s += `<text class="tick" x="${m.l - 8}" y="${y}" dy="0.32em" text-anchor="end">${esc(yFmt(t))}</text>`;
  }
  const baseV = y0 <= 0 && y1 >= 0 ? 0 : y0;
  const by = crisp(sy(baseV));
  s += `<line class="baseline" x1="${m.l}" x2="${m.l + iw}" y1="${by}" y2="${by}"/>`;

  // x ticks
  const maxXTicks = Math.max(2, Math.floor(iw / 78));
  let xt;
  if (kind === 'time' && keys.length) xt = timeTicks(keys[0], keys[keys.length - 1], maxXTicks);
  else if (kind === 'num' && keys.length) xt = niceTicks(keys[0], keys[keys.length - 1], maxXTicks).filter((v) => v >= keys[0] && v <= keys[keys.length - 1]);
  else {
    const every = Math.max(1, Math.ceil(keys.length / maxXTicks));
    xt = keys.filter((k, i) => i % every === 0);
  }
  for (const k of xt) {
    const x = sx(k);
    s += `<text class="tick" x="${r1(x)}" y="${m.t + ih + 17}" text-anchor="middle">${esc(xFmt(k))}</text>`;
  }

  // reference lines (thresholds)
  for (const ref of spec.refLines || []) {
    if (ref.y < y0 || ref.y > y1) continue;
    const y = crisp(sy(ref.y));
    const col = STATUS_COLOR[ref.tone] || ref.color || 'var(--ink-3)';
    s += `<line class="ref-line" x1="${m.l}" x2="${m.l + iw}" y1="${y}" y2="${y}" style="stroke:${col}"/>`;
    if (ref.label) s += `<text class="ref-label" x="${m.l + iw - 4}" y="${y - 5}" text-anchor="end">${esc(ref.label)}</text>`;
  }

  // series
  const geomSeries = [];
  const ends = [];
  series.forEach((ser) => {
    const pts = ser.points
      .map((p) => ({ k: xKeyOf(kind, p.x), y: p.y, flag: p.flag, label: p.label }))
      .sort((a, b) => (kind === 'cat' ? 0 : a.k - b.k));
    const map = new Map();
    const coords = pts.map((p) => {
      const c = { px: sx(p.k), py: clampY(p.y), y: p.y, k: p.k, flag: p.flag };
      map.set(p.k, c);
      return c;
    });
    geomSeries.push({ name: ser.name, color: ser.color, map, noTip: ser.noTip });
    if (!coords.length) return;
    const d = coords.map((c, i) => `${i ? 'L' : 'M'}${r1(c.px)},${r1(c.py)}`).join('');
    if ((spec.area || ser.area) && coords.length > 1) {
      s += `<path class="series-area" d="${d}L${r1(coords[coords.length - 1].px)},${by}L${r1(coords[0].px)},${by}Z" style="fill:${ser.color}"/>`;
    }
    const dash = ser.dashed ? ' stroke-dasharray="5 4"' : '';
    const sw = ser.width || 2;
    s += `<path class="series-line" d="${d}" style="stroke:${ser.color}" stroke-width="${sw}"${dash}/>`;
    if (spec.markers || ser.markers || coords.length === 1) {
      for (const c of coords) s += `<circle class="marker" cx="${r1(c.px)}" cy="${r1(c.py)}" r="4" style="fill:${ser.color}"/>`;
    }
    for (const c of coords) {
      if (c.flag) {
        const fc = STATUS_COLOR[c.flag] || 'var(--critical)';
        s += `<circle class="marker flag" cx="${r1(c.px)}" cy="${r1(c.py)}" r="4.5" style="fill:${fc}"/>`;
      }
    }
    if (!ser.dashed && !ser.noEndDot) {
      const last = coords[coords.length - 1];
      s += `<circle class="end-dot" cx="${r1(last.px)}" cy="${r1(last.py)}" r="4" style="fill:${ser.color}"/>`;
      ends.push({ name: ser.name, y: last.py, x: last.px });
    }
  });

  // direct end labels, only if they don't collide
  if (showEnd && ends.length) {
    const sorted = [...ends].sort((a, b) => a.y - b.y);
    const collide = sorted.some((e, i) => i > 0 && e.y - sorted[i - 1].y < 13);
    if (!collide) {
      for (const e of ends) {
        s += `<text class="direct-label" x="${r1(m.l + iw + 8)}" y="${r1(e.y)}" dy="0.32em">${esc(e.name)}</text>`;
      }
    }
  }

  // hover layer
  s += `<line class="crosshair" x1="0" x2="0" y1="${m.t}" y2="${m.t + ih}" visibility="hidden"/>`;
  s += '<g class="hover-dots"></g>';
  s += `<rect class="hit-area" x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent"/>`;

  const geom = {
    m, iw, ih, width, height: H,
    keys, xPx: keys.map((k) => sx(k)), series: geomSeries, xFmt: spec.tooltipX || xFmt, yFmt: spec.tooltipY || yFmt,
  };
  const svg = `<svg width="${width}" height="${H}" viewBox="0 0 ${width} ${H}" role="img" aria-label="${esc(spec.ariaLabel || 'Line chart')}">${s}</svg>`;
  return { svg, geom };
}

function hoverLine(e, fig, entry) {
  const g = entry.geom;
  const svg = fig.querySelector('.chart-svg svg');
  if (!g || !svg || !g.keys.length) return hideTip();
  const rect = svg.getBoundingClientRect();
  const scale = g.width / rect.width;
  const px = (e.clientX - rect.left) * scale;
  const py = (e.clientY - rect.top) * scale;
  if (px < g.m.l - 8 || px > g.m.l + g.iw + 8 || py < g.m.t - 4 || py > g.m.t + g.ih + 4) {
    clearLineHover(fig);
    return hideTip();
  }
  let best = 0;
  let bestD = Infinity;
  g.xPx.forEach((x, i) => {
    const d = Math.abs(x - px);
    if (d < bestD) { bestD = d; best = i; }
  });
  const key = g.keys[best];
  const cx = g.xPx[best];
  const cross = svg.querySelector('.crosshair');
  cross.setAttribute('x1', r1(cx));
  cross.setAttribute('x2', r1(cx));
  cross.setAttribute('visibility', 'visible');
  const dots = svg.querySelector('.hover-dots');
  let dm = '';
  const rows = [];
  for (const s of g.series) {
    const c = s.map.get(key);
    if (!c) continue;
    dm += `<circle class="hover-dot" cx="${r1(c.px)}" cy="${r1(c.py)}" r="4.5" style="fill:${s.color}"/>`;
    if (!s.noTip) rows.push({ name: s.name, value: g.yFmt(c.y), color: s.color, kind: 'line' });
  }
  dots.innerHTML = dm;
  showTip(e.clientX, e.clientY, { head: g.xFmt(key), rows });
}

function clearLineHover(fig) {
  const svg = fig.querySelector('.chart-svg svg');
  if (!svg) return;
  const cross = svg.querySelector('.crosshair');
  if (cross) cross.setAttribute('visibility', 'hidden');
  const dots = svg.querySelector('.hover-dots');
  if (dots) dots.innerHTML = '';
}

function lineTable(spec) {
  const kind = spec._kind;
  const xFmt = spec.xFormat || (kind === 'time' ? (ms) => fmt.dateLong(isoDay(ms)) : (v) => String(v));
  const yFmt = spec.yFormat || ((v) => fmt.num(v, Number.isInteger(v) ? 0 : 2));
  const keys = [];
  const seen = new Set();
  const maps = spec._series.map((s) => {
    const mp = new Map();
    for (const p of s.points) {
      const k = xKeyOf(kind, p.x);
      mp.set(k, p.y);
      if (!seen.has(k)) { seen.add(k); keys.push(k); }
    }
    return mp;
  });
  if (kind !== 'cat') keys.sort((a, b) => a - b);
  return {
    columns: [spec.xLabel || (kind === 'time' ? 'Date' : 'X'), ...spec._series.map((s) => s.name || 'Value')],
    rows: keys.map((k) => [xFmt(k), ...maps.map((mp) => (mp.has(k) ? yFmt(mp.get(k)) : '—'))]),
    numeric: spec._series.map(() => true),
  };
}

// ---------------------------------------------------------------------------
// bar chart
// ---------------------------------------------------------------------------
function bar(spec = {}) {
  const series = (spec.series || []).map((s, i) => ({
    ...s,
    color: s.color || (spec.series.length === 1 ? palette(0) : palette(s.colorIndex ?? i)),
    values: (s.values || []).map((v) => (v == null || !isFinite(v) ? null : Number(v))),
  }));
  const id = nextId();
  const entry = {
    kind: 'bar',
    spec: { ...spec, _series: series },
    render: (w) => renderBar(entry.spec, w),
    hover: hoverBar,
    table: () => barTable(entry.spec),
  };
  const legend = series.length > 1 && spec.legend !== false ? legendHTML(series, 'rect') : '';
  return figure(id, entry, { legend, ariaLabel: spec.ariaLabel });
}

function barColor(spec, ser, si, ci) {
  if (typeof spec.colors === 'function') return spec.colors(si, ci) || ser.color;
  if (Array.isArray(spec.colors) && spec._series.length === 1) return spec.colors[ci] || ser.color;
  return ser.color;
}

// Rect with a rounded data end (4px) and square baseline end.
function barPath(x, y, w, h, dir, r = 4) {
  if (w <= 0 || h <= 0) return '';
  const rr = Math.min(r, w / 2, h);
  if (dir === 'up') return `M${r1(x)},${r1(y + h)}V${r1(y + rr)}A${rr},${rr} 0 0 1 ${r1(x + rr)},${r1(y)}H${r1(x + w - rr)}A${rr},${rr} 0 0 1 ${r1(x + w)},${r1(y + rr)}V${r1(y + h)}Z`;
  if (dir === 'down') return `M${r1(x)},${r1(y)}V${r1(y + h - rr)}A${rr},${rr} 0 0 0 ${r1(x + rr)},${r1(y + h)}H${r1(x + w - rr)}A${rr},${rr} 0 0 0 ${r1(x + w)},${r1(y + h - rr)}V${r1(y)}Z`;
  if (dir === 'right') {
    const rh = Math.min(r, h / 2, w);
    return `M${r1(x)},${r1(y)}H${r1(x + w - rh)}A${rh},${rh} 0 0 1 ${r1(x + w)},${r1(y + rh)}V${r1(y + h - rh)}A${rh},${rh} 0 0 1 ${r1(x + w - rh)},${r1(y + h)}H${r1(x)}Z`;
  }
  const rh = Math.min(r, h / 2, w);
  return `M${r1(x + w)},${r1(y)}H${r1(x + rh)}A${rh},${rh} 0 0 0 ${r1(x)},${r1(y + rh)}V${r1(y + h - rh)}A${rh},${rh} 0 0 0 ${r1(x + rh)},${r1(y + h)}H${r1(x + w)}Z`;
}

function renderBar(spec, width) {
  const H = spec.height || (spec.horizontal ? Math.max(120, (spec.categories || []).length * 30 + 34) : 220);
  const cats = (spec.categories || []).map(String);
  const series = spec._series;
  const stacked = !!spec.stacked && series.length > 1;
  const horiz = !!spec.horizontal;
  const yFmt = spec.yFormat || ((v) => fmt.compact(v));
  const catFmt = spec.categoryFormat || ((c) => c);

  // value domain
  let lo = 0;
  let hi = 0;
  cats.forEach((c, ci) => {
    if (stacked) {
      let pos = 0;
      let neg = 0;
      series.forEach((s) => { const v = s.values[ci] || 0; if (v >= 0) pos += v; else neg += v; });
      hi = Math.max(hi, pos);
      lo = Math.min(lo, neg);
    } else {
      series.forEach((s) => { const v = s.values[ci]; if (v != null) { hi = Math.max(hi, v); lo = Math.min(lo, v); } });
    }
  });
  if (spec.yMax != null) hi = spec.yMax;
  if (spec.yMin != null) lo = spec.yMin;
  if (hi === lo) hi = lo + 1;

  const single = series.length === 1 && !stacked;
  const valueLabels = spec.valueLabels ?? (single && cats.length <= (horiz ? 24 : 14));
  const geom = { width, height: H, horiz, cats, series: [], hits: [], yFmt, catFmt };
  let s = '';

  if (!horiz) {
    const ticks = niceTicks(lo, hi, Math.max(2, Math.round(H / 56)));
    const v0 = spec.yMin != null ? spec.yMin : ticks[0];
    const v1 = spec.yMax != null ? spec.yMax : ticks[ticks.length - 1];
    const labelW = Math.max(18, ...ticks.map((t) => textW(yFmt(t))));
    const m = { t: valueLabels ? 18 : 10, r: 12, b: 28, l: Math.ceil(labelW) + 10 };
    const iw = Math.max(40, width - m.l - m.r);
    const ih = Math.max(40, H - m.t - m.b);
    const sy = scaleLinear([v0, v1], [m.t + ih, m.t]);
    for (const t of ticks) {
      if (t < v0 - 1e-9 || t > v1 + 1e-9) continue;
      const y = crisp(sy(t));
      s += `<line class="gridline" x1="${m.l}" x2="${m.l + iw}" y1="${y}" y2="${y}"/>`;
      s += `<text class="tick" x="${m.l - 8}" y="${y}" dy="0.32em" text-anchor="end">${esc(yFmt(t))}</text>`;
    }
    const band = scaleBand(cats, [m.l, m.l + iw], cats.length > 20 ? 0.18 : 0.3);
    const groups = stacked ? 1 : series.length;
    const gap = 2;
    const barW = Math.max(2, Math.min(24, (band.bandwidth - gap * (groups - 1)) / groups));
    const groupW = barW * groups + gap * (groups - 1);
    const zeroY = sy(Math.max(v0, Math.min(v1, 0)));

    // category labels (thin out when crowded)
    const maxLabel = band.step - 6;
    const every = Math.max(1, Math.ceil(56 / band.step));
    cats.forEach((c, ci) => {
      if (ci % every !== 0) return;
      let label = String(catFmt(c));
      if (textW(label) > maxLabel * every) {
        const chars = Math.max(3, Math.floor((maxLabel * every) / 6.4) - 1);
        label = label.length > chars ? label.slice(0, chars) + '…' : label;
      }
      s += `<text class="tick" x="${r1(band(c) + band.bandwidth / 2)}" y="${m.t + ih + 17}" text-anchor="middle">${esc(label)}</text>`;
    });

    cats.forEach((c, ci) => {
      const gx = band(c) + (band.bandwidth - groupW) / 2;
      if (stacked) {
        let accPos = 0;
        let accNeg = 0;
        const segs = [];
        series.forEach((ser, si) => {
          const v = ser.values[ci];
          if (!v) return;
          const from = v >= 0 ? accPos : accNeg;
          const to = from + v;
          if (v >= 0) accPos = to; else accNeg = to;
          segs.push({ si, v, from, to });
        });
        const topIdx = segs.map((sg) => sg.v >= 0).lastIndexOf(true);
        segs.forEach((sg, idx) => {
          const yA = sy(sg.from);
          const yB = sy(sg.to);
          let top = Math.min(yA, yB);
          let h = Math.abs(yB - yA);
          // 2px surface gap between stacked segments
          if (idx > 0) { top += 0; h -= gap; }
          if (sg.v >= 0 && idx > 0) { h = Math.abs(yB - yA) - gap; top = Math.min(yA, yB); }
          if (h <= 0.5) return;
          const col = barColor(spec, series[sg.si], sg.si, ci);
          const isEnd = idx === topIdx && sg.v >= 0;
          const d = isEnd ? barPath(gx, top, barW, h, 'up') : `M${r1(gx)},${r1(top)}h${r1(barW)}v${r1(h)}h${r1(-barW)}Z`;
          s += `<path class="bar" data-ci="${ci}" data-si="${sg.si}" d="${d}" style="fill:${col}"/>`;
        });
        if (spec.totals) {
          const tot = series.reduce((a, ser) => a + (ser.values[ci] || 0), 0);
          s += `<text class="value-label" x="${r1(gx + barW / 2)}" y="${r1(sy(accPos) - 5)}" text-anchor="middle">${esc(yFmt(tot))}</text>`;
        }
      } else {
        series.forEach((ser, si) => {
          const v = ser.values[ci];
          if (v == null) return;
          const x = gx + si * (barW + gap);
          const yv = sy(v);
          const top = Math.min(yv, zeroY);
          const h = Math.max(Math.abs(zeroY - yv), v !== 0 ? 1 : 0);
          const col = barColor(spec, ser, si, ci);
          s += `<path class="bar" data-ci="${ci}" data-si="${si}" d="${barPath(x, top, barW, h, v >= 0 ? 'up' : 'down')}" style="fill:${col}"/>`;
          if (valueLabels) {
            const ly = v >= 0 ? top - 5 : top + h + 12;
            s += `<text class="value-label" x="${r1(x + barW / 2)}" y="${r1(ly)}" text-anchor="middle">${esc(yFmt(v))}</text>`;
          }
        });
      }
      geom.hits.push({ ci, x: band(c) - (band.step - band.bandwidth) / 2, y: m.t, w: band.step, h: ih });
    });
    s += `<line class="baseline" x1="${m.l}" x2="${m.l + iw}" y1="${crisp(zeroY)}" y2="${crisp(zeroY)}"/>`;
  } else {
    // horizontal bars: categories down the left
    const maxCat = Math.min(spec.labelWidth || 180, Math.max(40, ...cats.map((c) => textW(catFmt(c), 12))));
    const ticks = niceTicks(lo, hi, Math.max(2, Math.floor((width - maxCat) / 90)));
    const v0 = spec.yMin != null ? spec.yMin : ticks[0];
    const v1 = spec.yMax != null ? spec.yMax : ticks[ticks.length - 1];
    const valW = valueLabels ? Math.max(...series.flatMap((ser) => ser.values.map((v) => (v == null ? 0 : textW(yFmt(v), 11.5))))) + 10 : 12;
    const m = { t: 6, r: Math.ceil(valW) + 6, b: 24, l: Math.ceil(maxCat) + 14 };
    const iw = Math.max(40, width - m.l - m.r);
    const ih = Math.max(20, H - m.t - m.b);
    const sx = scaleLinear([v0, v1], [m.l, m.l + iw]);
    for (const t of ticks) {
      if (t < v0 - 1e-9 || t > v1 + 1e-9) continue;
      const x = crisp(sx(t));
      s += `<line class="gridline" x1="${x}" x2="${x}" y1="${m.t}" y2="${m.t + ih}"/>`;
      s += `<text class="tick" x="${x}" y="${m.t + ih + 16}" text-anchor="middle">${esc(yFmt(t))}</text>`;
    }
    const band = scaleBand(cats, [m.t, m.t + ih], 0.3);
    const groups = stacked ? 1 : series.length;
    const gap = 2;
    const barH = Math.max(2, Math.min(24, (band.bandwidth - gap * (groups - 1)) / groups));
    const groupH = barH * groups + gap * (groups - 1);
    const zeroX = sx(Math.max(v0, Math.min(v1, 0)));
    cats.forEach((c, ci) => {
      const gy = band(c) + (band.bandwidth - groupH) / 2;
      let label = String(catFmt(c));
      const maxChars = Math.floor(maxCat / 6.9);
      if (label.length > maxChars) label = label.slice(0, Math.max(3, maxChars - 1)) + '…';
      s += `<text class="cat-label" x="${m.l - 10}" y="${r1(band(c) + band.bandwidth / 2)}" dy="0.32em" text-anchor="end">${esc(label)}</text>`;
      if (stacked) {
        let acc = 0;
        const segs = series.map((ser, si) => ({ si, v: ser.values[ci] || 0 })).filter((sg) => sg.v > 0);
        segs.forEach((sg, idx) => {
          const xA = sx(acc);
          acc += sg.v;
          const xB = sx(acc);
          const w = xB - xA - (idx < segs.length - 1 ? gap : 0);
          if (w <= 0.5) return;
          const col = barColor(spec, series[sg.si], sg.si, ci);
          const d = idx === segs.length - 1 ? barPath(xA, gy, w, barH, 'right') : `M${r1(xA)},${r1(gy)}h${r1(w)}v${r1(barH)}h${r1(-w)}Z`;
          s += `<path class="bar" data-ci="${ci}" data-si="${sg.si}" d="${d}" style="fill:${col}"/>`;
        });
        if (spec.totals) s += `<text class="value-label" x="${r1(sx(acc) + 6)}" y="${r1(gy + barH / 2)}" dy="0.32em">${esc(yFmt(acc))}</text>`;
      } else {
        series.forEach((ser, si) => {
          const v = ser.values[ci];
          if (v == null) return;
          const y = gy + si * (barH + gap);
          const xv = sx(v);
          const left = Math.min(xv, zeroX);
          const w = Math.max(Math.abs(xv - zeroX), v !== 0 ? 1 : 0);
          const col = barColor(spec, ser, si, ci);
          s += `<path class="bar" data-ci="${ci}" data-si="${si}" d="${barPath(left, y, w, barH, v >= 0 ? 'right' : 'left')}" style="fill:${col}"/>`;
          if (valueLabels) s += `<text class="value-label" x="${r1(left + w + 6)}" y="${r1(y + barH / 2)}" dy="0.32em">${esc(yFmt(v))}</text>`;
        });
      }
      geom.hits.push({ ci, x: 0, y: band(c) - (band.step - band.bandwidth) / 2, w: width, h: band.step });
    });
    s += `<line class="baseline" x1="${crisp(zeroX)}" x2="${crisp(zeroX)}" y1="${m.t}" y2="${m.t + ih}"/>`;
  }

  // hit targets: one per category, larger than the marks, keyboard focusable
  for (const h of geom.hits) {
    s += `<rect class="bar-hit" data-ci="${h.ci}" x="${r1(h.x)}" y="${r1(h.y)}" width="${r1(Math.max(1, h.w))}" height="${r1(Math.max(1, h.h))}" fill="transparent" tabindex="0" aria-label="${esc(cats[h.ci])}"/>`;
  }
  geom.series = series.map((ser) => ({ name: ser.name, color: ser.color, values: ser.values }));
  const svg = `<svg width="${width}" height="${H}" viewBox="0 0 ${width} ${H}" role="img" aria-label="${esc(spec.ariaLabel || 'Bar chart')}">${s}</svg>`;
  return { svg, geom };
}

function hoverBar(e, fig, entry, focusEl) {
  const target = focusEl || (e.target.closest && e.target.closest('[data-ci]'));
  const svg = fig.querySelector('.chart-svg svg');
  if (!target || !svg) { clearBarHover(fig); return hideTip(); }
  const ci = +target.dataset.ci;
  const hoveredSi = target.classList.contains('bar') ? +target.dataset.si : null;
  const g = entry.geom;
  const spec = entry.spec;
  svg.querySelectorAll('.bar').forEach((b) => b.classList.toggle('dim', +b.dataset.ci !== ci));
  const rows = g.series
    .map((ser, si) => ({ si, name: ser.name || 'Value', v: ser.values[ci], color: barColor(spec, spec._series[si], si, ci) }))
    .filter((r) => r.v != null)
    .map((r) => ({ name: r.name, value: g.yFmt(r.v), color: r.color, kind: 'rect', strong: r.si === hoveredSi }));
  if (spec.stacked && g.series.length > 1) {
    const tot = g.series.reduce((a, ser) => a + (ser.values[ci] || 0), 0);
    rows.push({ name: 'Total', value: g.yFmt(tot), total: true });
  }
  let x = e && e.clientX;
  let y = e && e.clientY;
  if (focusEl) {
    const r = focusEl.getBoundingClientRect();
    x = r.left + r.width / 2;
    y = r.top + 12;
  }
  showTip(x, y, { head: String(g.catFmt(g.cats[ci])), rows });
}

function clearBarHover(fig) {
  fig.querySelectorAll('.bar.dim').forEach((b) => b.classList.remove('dim'));
}

function barTable(spec) {
  const yFmt = spec.yFormat || ((v) => fmt.num(v, Number.isInteger(v) ? 0 : 2));
  const cats = spec.categories || [];
  const cols = [spec.categoryLabel || 'Category', ...spec._series.map((s) => s.name || 'Value')];
  const rows = cats.map((c, ci) => [String((spec.categoryFormat || ((x) => x))(c)), ...spec._series.map((s) => (s.values[ci] == null ? '—' : yFmt(s.values[ci])))]);
  if (spec.stacked && spec._series.length > 1) {
    cols.push('Total');
    rows.forEach((r, ci) => r.push(yFmt(spec._series.reduce((a, s) => a + (s.values[ci] || 0), 0))));
  }
  return { columns: cols, rows, numeric: cols.slice(1).map(() => true) };
}

// ---------------------------------------------------------------------------
// custom charts: the page draws SVG markup; marks carry data-tip text
// ---------------------------------------------------------------------------
function custom(spec = {}) {
  const id = nextId();
  const entry = {
    kind: 'custom',
    spec,
    render: (w) => {
      const H = typeof spec.height === 'function' ? spec.height(w) : (spec.height || 240);
      const inner = spec.render(w, H);
      return {
        svg: `<svg width="${w}" height="${H}" viewBox="0 0 ${w} ${H}" role="img" aria-label="${esc(spec.ariaLabel || 'Chart')}">${toHTML(inner)}</svg>`,
        geom: { width: w, height: H },
      };
    },
    hover: hoverTips,
    table: spec.table ? () => (typeof spec.table === 'function' ? spec.table() : spec.table) : null,
  };
  return figure(id, entry, { legend: spec.legend || '', ariaLabel: spec.ariaLabel });
}

// ---------------------------------------------------------------------------
// sparkline, legend, table twin
// ---------------------------------------------------------------------------
function sparkline(values, { width = 88, height = 26, color } = {}) {
  const vals = (values || []).map(Number).filter((v) => isFinite(v));
  if (vals.length < 2) return raw('');
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const pad = 3;
  const w = width - pad * 2;
  const h = height - pad * 2;
  const x = (i) => pad + (i / (vals.length - 1)) * w;
  const y = (v) => pad + (max === min ? h / 2 : h - ((v - min) / (max - min)) * h);
  const d = vals.map((v, i) => `${i ? 'L' : 'M'}${r1(x(i))},${r1(y(v))}`).join('');
  return raw(`<svg class="sparkline" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">` +
    `<path d="${d}" fill="none" style="stroke:${color || 'var(--series-other)'}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>` +
    `<circle cx="${r1(x(vals.length - 1))}" cy="${r1(y(vals[vals.length - 1]))}" r="2.6" style="fill:var(--series-1);stroke:var(--surface)" stroke-width="1.5"/></svg>`);
}

function legendHTML(series, kind = 'line') {
  return html`<div class="legend">${series.map((s, i) => html`<span class="legend-item"><span class="legend-key ${kind === 'rect' ? 'rect' : 'line'}${s.dashed ? ' dashed' : ''}" style="--c:${s.color || palette(i)}"></span>${s.name}</span>`)}</div>`;
}

function tableHTML(data) {
  if (!data) return html`<p class="muted small">No table view for this chart.</p>`;
  const numeric = data.numeric || [];
  return html`<div class="table-wrap chart-table"><table class="table dense">
    <thead><tr>${data.columns.map((c, i) => html`<th class="${i > 0 && numeric[i - 1] ? 'num' : ''}">${c}</th>`)}</tr></thead>
    <tbody>${data.rows.map((r) => html`<tr>${r.map((v, i) => html`<td class="${i > 0 && numeric[i - 1] ? 'num' : ''}">${v == null ? '—' : v}</td>`)}</tr>`)}</tbody>
  </table></div>`;
}

// ---------------------------------------------------------------------------
// tooltip (one element, DOM-built with textContent)
// ---------------------------------------------------------------------------
let tipEl = null;
function tip() {
  if (tipEl) return tipEl;
  tipEl = document.createElement('div');
  tipEl.className = 'chart-tooltip';
  tipEl.setAttribute('role', 'tooltip');
  tipEl.hidden = true;
  document.body.appendChild(tipEl);
  return tipEl;
}

function showTip(x, y, { head, rows = [], lines } = {}) {
  const t = tip();
  t.textContent = '';
  if (head) {
    const h = document.createElement('div');
    h.className = 'tt-head';
    h.textContent = head;
    t.appendChild(h);
  }
  for (const r of rows) {
    const row = document.createElement('div');
    row.className = 'tt-row' + (r.strong ? ' strong' : '') + (r.total ? ' total' : '');
    if (r.color) {
      const k = document.createElement('span');
      k.className = 'tt-key' + (r.kind === 'rect' ? ' rect' : '');
      k.style.setProperty('--c', r.color);
      row.appendChild(k);
    }
    const v = document.createElement('span');
    v.className = 'tt-val';
    v.textContent = r.value;
    const n = document.createElement('span');
    n.className = 'tt-name';
    n.textContent = r.name;
    row.appendChild(v);
    row.appendChild(n);
    t.appendChild(row);
  }
  for (const ln of lines || []) {
    const d = document.createElement('div');
    d.className = 'tt-line';
    d.textContent = ln;
    t.appendChild(d);
  }
  t.hidden = false;
  const pad = 14;
  const w = t.offsetWidth;
  const h = t.offsetHeight;
  let left = x + pad;
  let top = y + pad;
  if (left + w > window.innerWidth - 8) left = x - pad - w;
  if (top + h > window.innerHeight - 8) top = y - pad - h;
  t.style.left = `${Math.max(8, left)}px`;
  t.style.top = `${Math.max(8, top)}px`;
}

function hideTip() {
  if (tipEl) tipEl.hidden = true;
}

// Generic data-tip support: 'Title\nline 2\nline 3'
function tipFromEl(el, x, y) {
  const text = el.getAttribute('data-tip') || '';
  const [head, ...rest] = text.split('\n');
  showTip(x, y, { head, lines: rest });
}

function hoverTips(e) {
  const t = e.target.closest && e.target.closest('[data-tip]');
  if (t) tipFromEl(t, e.clientX, e.clientY);
  else hideTip();
}

// ---------------------------------------------------------------------------
// global wiring: resize, pointer, focus, table toggle
// ---------------------------------------------------------------------------
function rerender(fig, width) {
  const entry = registry.get(fig.dataset.chartId);
  if (!entry || !width || Math.abs((entry.width || 0) - width) < 1) return;
  const out = entry.render(width);
  entry.width = width;
  entry.geom = out.geom;
  const holder = fig.querySelector('.chart-svg');
  if (holder) holder.innerHTML = out.svg;
}

const ro = typeof ResizeObserver !== 'undefined'
  ? new ResizeObserver((entries) => {
    const jobs = entries.map((en) => [en.target, Math.floor(en.contentRect.width)]);
    requestAnimationFrame(() => { for (const [t, w] of jobs) rerender(t, w); });
  })
  : null;

function observeCharts(root) {
  if (!ro || !root || root.nodeType !== 1) return;
  const figs = root.matches && root.matches('.chart[data-chart-id]') ? [root] : [];
  root.querySelectorAll && figs.push(...root.querySelectorAll('.chart[data-chart-id]'));
  for (const f of figs) {
    if (!f.__observed) {
      f.__observed = true;
      ro.observe(f);
    }
  }
}

let activeFig = null;
function leaveFig(fig) {
  const entry = registry.get(fig.dataset.chartId);
  if (entry && entry.kind === 'line') clearLineHover(fig);
  if (entry && entry.kind === 'bar') clearBarHover(fig);
  hideTip();
}

if (typeof document !== 'undefined' && !window.__opsChartsWired) {
  window.__opsChartsWired = true;

  new MutationObserver((muts) => {
    for (const m of muts) for (const n of m.addedNodes) observeCharts(n);
  }).observe(document.documentElement, { childList: true, subtree: true });

  document.addEventListener('pointermove', (e) => {
    const fig = e.target.closest ? e.target.closest('.chart[data-chart-id]') : null;
    if (fig !== activeFig) {
      if (activeFig) leaveFig(activeFig);
      activeFig = fig;
    }
    if (!fig) {
      const t = e.target.closest ? e.target.closest('[data-tip]') : null;
      if (t) tipFromEl(t, e.clientX, e.clientY);
      else hideTip();
      return;
    }
    const entry = registry.get(fig.dataset.chartId);
    if (entry && entry.hover) entry.hover(e, fig, entry);
  }, { passive: true });

  document.addEventListener('pointerleave', () => { if (activeFig) leaveFig(activeFig); activeFig = null; hideTip(); });
  window.addEventListener('blur', hideTip);
  window.addEventListener('scroll', hideTip, { passive: true, capture: true });

  document.addEventListener('focusin', (e) => {
    const hit = e.target.closest && e.target.closest('.chart[data-chart-id] .bar-hit');
    if (!hit) return;
    const fig = hit.closest('.chart');
    const entry = registry.get(fig.dataset.chartId);
    if (entry && entry.kind === 'bar') hoverBar(null, fig, entry, hit);
  });
  document.addEventListener('focusout', (e) => {
    if (e.target.closest && e.target.closest('.chart .bar-hit')) {
      const fig = e.target.closest('.chart');
      if (fig) clearBarHover(fig);
      hideTip();
    }
  });

  // Card "Table" toggle: swaps every chart in the card for its table twin.
  document.addEventListener('click', (e) => {
    const btn = e.target.closest ? e.target.closest('[data-toggle-table]') : null;
    if (!btn) return;
    const card = btn.closest('.card') || btn.parentElement;
    const figs = card ? card.querySelectorAll('.chart[data-chart-id]') : [];
    const showTable = btn.getAttribute('aria-pressed') !== 'true';
    figs.forEach((fig) => {
      const entry = registry.get(fig.dataset.chartId);
      const tv = fig.querySelector('.chart-tableview');
      const sv = fig.querySelector('.chart-svg');
      const lg = fig.querySelector('.legend');
      if (!tv || !sv) return;
      if (showTable) {
        tv.innerHTML = String(tableHTML(entry && entry.table ? entry.table() : null));
        tv.hidden = false;
        sv.hidden = true;
        if (lg) lg.hidden = true;
      } else {
        tv.hidden = true;
        tv.innerHTML = '';
        sv.hidden = false;
        if (lg) lg.hidden = false;
      }
    });
    btn.setAttribute('aria-pressed', showTable ? 'true' : 'false');
    const label = btn.querySelector('span');
    if (label) label.textContent = showTable ? 'Chart' : 'Table';
    hideTip();
  });
}

export const charts = {
  line,
  bar,
  custom,
  sparkline,
  legend: legendHTML,
  table: tableHTML,
  palette,
  STATUS_COLOR,
  scaleLinear,
  scaleBand,
  niceTicks,
  showTip,
  hideTip,
};

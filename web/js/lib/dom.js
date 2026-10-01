// Tiny DOM helpers. `html` is the only way pages should build markup: it
// escapes every interpolation unless the value is itself html`` (or raw()),
// and arrays compose, so templates nest naturally.

export class SafeHTML {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function esc(v) {
  return String(v).replace(/[&<>"']/g, (c) => ESC[c]);
}

export function raw(s) {
  return new SafeHTML(s == null ? '' : String(s));
}

// Converts any template value to an HTML string (escaping plain values).
export function toHTML(v) {
  if (v == null || v === false) return '';
  if (v instanceof SafeHTML) return v.s;
  if (Array.isArray(v)) return v.map(toHTML).join('');
  return esc(v);
}

export function html(strings, ...values) {
  let out = strings[0];
  for (let i = 0; i < values.length; i++) out += toHTML(values[i]) + strings[i + 1];
  return new SafeHTML(out);
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// Delegated listener: handler(event, matchedElement). Returns an off() function.
export function on(root, event, selector, handler, opts) {
  const fn = (e) => {
    const t = e.target && e.target.closest ? e.target.closest(selector) : null;
    if (t && root.contains(t)) handler(e, t);
  };
  root.addEventListener(event, fn, opts);
  return () => root.removeEventListener(event, fn, opts);
}

// Delegated listeners for an element that outlives one render: listen() adds one, clear() removes them all.
export function listeners() {
  let offs = [];
  return {
    listen: (root, event, selector, handler) => { offs.push(on(root, event, selector, handler)); },
    clear: () => { offs.forEach((off) => off()); offs = []; },
  };
}

export function injectStyle(id, css) {
  if (document.getElementById(id)) return;
  const el = document.createElement('style');
  el.id = id;
  el.textContent = css;
  document.head.appendChild(el);
}

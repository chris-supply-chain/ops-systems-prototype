// Hand-drawn 24px stroke icons (stroke 1.75, currentColor).
import { raw } from './dom.js';

const P = {
  // navigation
  radar: '<circle cx="12" cy="12" r="9"/><path d="M12 7a5 5 0 1 0 5 5"/><path d="M12 12l6-6"/><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none"/>',
  loop: '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 3v5h5"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/><path d="M20 21v-5h-5"/>',
  factory: '<path d="M3 20V10l5 3v-3l5 3v-3l5 3V4h3v16z"/><path d="M7 16.5h2M12 16.5h2"/>',
  tree: '<rect x="9" y="3" width="6" height="5" rx="1.2"/><rect x="3" y="16" width="6" height="5" rx="1.2"/><rect x="15" y="16" width="6" height="5" rx="1.2"/><path d="M12 8v4M6 16v-2a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2"/>',
  feed: '<path d="M5 11a8 8 0 0 1 8 8"/><path d="M5 4.5A14.5 14.5 0 0 1 19.5 19"/><circle cx="6" cy="18" r="1.6" fill="currentColor" stroke="none"/>',
  grid: '<rect x="3" y="3" width="18" height="18" rx="2.5"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2.5"/><path d="M3 10h18M8 3v4M16 3v4"/><path d="M7.5 14h3M13.5 14h3M7.5 17.5h3"/>',
  gantt: '<path d="M4 4v16h16"/><rect x="7" y="6.5" width="8" height="3" rx="1"/><rect x="10" y="11" width="9" height="3" rx="1"/><rect x="8" y="15.5" width="5" height="3" rx="1"/>',
  bell: '<path d="M6 16v-5a6 6 0 1 1 12 0v5l1.6 2.2H4.4z"/><path d="M10 20.6a2.1 2.1 0 0 0 4 0"/>',
  apps: '<rect x="4" y="4" width="6" height="6" rx="1.5"/><rect x="14" y="4" width="6" height="6" rx="1.5"/><rect x="4" y="14" width="6" height="6" rx="1.5"/><rect x="14" y="14" width="6" height="6" rx="1.5"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.4 2.4c-.6.3-1 .8-1 1.4v.6"/><circle cx="12" cy="16.9" r=".9" fill="currentColor" stroke="none"/>',
  warehouse: '<path d="M3 20.5V9l9-5 9 5v11.5"/><path d="M7 20.5V13h10v7.5M7 16.8h10"/>',
  updown: '<path d="M8 9.5l4-4 4 4"/><path d="M16 14.5l-4 4-4-4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
  boxes: '<path d="M12 3l8.5 4.7v8.6L12 21l-8.5-4.7V7.7z"/><path d="M3.5 7.7L12 12.4l8.5-4.7M12 12.4V21"/>',
  exchange: '<path d="M4 8h14l-3.5-3.5"/><path d="M20 16H6l3.5 3.5"/>',
  ship: '<path d="M2.5 14.5h19l-2.2 4.6a1.5 1.5 0 0 1-1.3.9H6a1.5 1.5 0 0 1-1.3-.9z"/><path d="M5 14.5V9h5v5.5M10 14.5V6h5v8.5M15 14.5v-4h4v4"/>',
  shield: '<path d="M12 3l7.5 2.8V11c0 4.6-3.1 8.2-7.5 10-4.4-1.8-7.5-5.4-7.5-10V5.8z"/><path d="M8.8 12l2.2 2.2 4.4-4.4"/>',
  receipt: '<path d="M6 3h12v18l-3-1.8-3 1.8-3-1.8L6 21z"/><path d="M9 8h6M9 12h6M9 16h3.5"/>',
  database: '<ellipse cx="12" cy="5.5" rx="8" ry="2.7"/><path d="M4 5.5v13c0 1.5 3.6 2.7 8 2.7s8-1.2 8-2.7v-13"/><path d="M4 12c0 1.5 3.6 2.7 8 2.7s8-1.2 8-2.7"/>',
  checklist: '<path d="M3.5 6l1.7 1.7L8.5 4.4M3.5 12l1.7 1.7 3.3-3.3M3.5 18l1.7 1.7 3.3-3.3"/><path d="M11.5 6H20M11.5 12H20M11.5 18H20"/>',
  flow: '<rect x="3" y="4" width="6" height="5" rx="1.2"/><rect x="15" y="4" width="6" height="5" rx="1.2"/><rect x="9" y="15" width="6" height="5" rx="1.2"/><path d="M9 6.5h6M6 9v2.5a2 2 0 0 0 2 2h1.5M18 9v2.5a2 2 0 0 1-2 2h-1.5"/>',
  scale: '<path d="M12 4v16M8 20h8M5 7.5h14"/><path d="M5 7.5L2.5 13a2.5 2.5 0 0 0 5 0z"/><path d="M19 7.5L16.5 13a2.5 2.5 0 0 0 5 0z"/><circle cx="12" cy="4" r="1" fill="currentColor" stroke="none"/>',
  compass: '<circle cx="12" cy="12" r="9"/><path d="M15.6 8.4l-2.1 5.1-5.1 2.1 2.1-5.1z"/>',
  route: '<circle cx="6" cy="18.5" r="2"/><circle cx="18" cy="5.5" r="2"/><path d="M8 18.5h8a3.5 3.5 0 0 0 0-7H8a3.5 3.5 0 0 1 0-7h8"/>',
  ledger: '<path d="M5 5a2 2 0 0 1 2-2h12v15H7a2 2 0 0 0-2 2z"/><path d="M5 20a2 2 0 0 0 2 1h12v-3"/><path d="M9 7.5h6M9 11h6"/>',
  plug: '<path d="M9 3v5M15 3v5"/><path d="M6.5 8h11v3.5a5.5 5.5 0 0 1-11 0z"/><path d="M12 17v4"/>',
  flask: '<path d="M9 3h6M10 3v6.2L4.9 18.2A1.9 1.9 0 0 0 6.6 21h10.8a1.9 1.9 0 0 0 1.7-2.8L14 9.2V3"/><path d="M7.3 14.5h9.4"/>',
  'badge-check': '<path d="M12 2.8l2.4 1.7 2.9-.1.9 2.8 2.3 1.7-.9 2.8.9 2.8-2.3 1.7-.9 2.8-2.9-.1L12 21.2l-2.4-1.7-2.9.1-.9-2.8-2.3-1.7.9-2.8-.9-2.8 2.3-1.7.9-2.8 2.9.1z"/><path d="M8.8 12.1l2.2 2.2 4.3-4.4"/>',

  // actions & states
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6L6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4"/>',
  moon: '<path d="M20 14.6A8 8 0 1 1 9.4 4a6.3 6.3 0 0 0 10.6 10.6z"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  'chevron-right': '<path d="M9.5 6l6 6-6 6"/>',
  'chevron-left': '<path d="M14.5 6l-6 6 6 6"/>',
  'chevron-down': '<path d="M6 9.5l6 6 6-6"/>',
  'chevron-up': '<path d="M6 14.5l6-6 6 6"/>',
  'arrow-right': '<path d="M4 12h16M14 6l6 6-6 6"/>',
  'arrow-left': '<path d="M20 12H4M10 6l-6 6 6 6"/>',
  external: '<path d="M14 4h6v6M20 4l-8.5 8.5"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
  'alert-triangle': '<path d="M10.3 4.2L2.6 17.6A2 2 0 0 0 4.3 20.6h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4.2M12 16.9v.01"/>',
  'alert-octagon': '<path d="M8.3 3h7.4L21 8.3v7.4L15.7 21H8.3L3 15.7V8.3z"/><path d="M12 8v4.8M12 16v.01"/>',
  'check-circle': '<circle cx="12" cy="12" r="9"/><path d="M8.2 12.3l2.6 2.6 5-5.4"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.2M12 7.8v.01"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.6-4.4L4 8"/><path d="M4 3.5V8h4.5"/><path d="M4 13a8 8 0 0 0 14.6 4.4L20 16"/><path d="M20 20.5V16h-4.5"/>',
  play: '<path d="M7.5 4.8v14.4a.8.8 0 0 0 1.2.7l11.3-7.2a.8.8 0 0 0 0-1.4L8.7 4.1a.8.8 0 0 0-1.2.7z"/>',
  pause: '<path d="M9 5v14M15 5v14"/>',
  filter: '<path d="M3.5 5h17l-6.5 7.6V19l-4 2v-8.4z"/>',
  download: '<path d="M12 3.5v11M7.5 10l4.5 4.5 4.5-4.5M4 20h16"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9.5h18M3 14.8h18M9.5 9.5V20"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3.2-3.2a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3.2 3.2a4 4 0 0 0 5.7 5.7l1-1"/>',
  lock: '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3"/>',
  key: '<circle cx="8" cy="15" r="3.8"/><path d="M10.8 12.3L20 3M16 7l2.5 2.5M13.8 9.2l2 2"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
  code: '<path d="M8.5 8L4.5 12l4 4M15.5 8l4 4-4 4M13.5 5l-3 14"/>',
  layers: '<path d="M12 3.5l8.5 4.5-8.5 4.5L3.5 8z"/><path d="M3.5 12.3l8.5 4.5 8.5-4.5M3.5 16.3l8.5 4.5 8.5-4.5"/>',
  truck: '<path d="M2.5 6h11v10h-11zM13.5 9.5h4l3 3.2V16h-7"/><circle cx="6.5" cy="17.5" r="1.8"/><circle cx="16.8" cy="17.5" r="1.8"/>',
  user: '<circle cx="12" cy="8" r="3.8"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>',
  cone: '<path d="M10 3.5h4l4.6 15.5H5.4z"/><path d="M3.5 20.5h17"/><path d="M8.3 9.5h7.4M6.9 14.3h10.2"/>',
  bolt: '<path d="M13 2.5L4.5 13.5H11l-1 8 8.5-11H12z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/>',
  sparkles: '<path d="M11 3.5l1.6 4.4L17 9.5l-4.4 1.6L11 15.5l-1.6-4.4L5 9.5l4.4-1.6z"/><path d="M18 14.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
};

export const ICON_NAMES = Object.keys(P);

export function icon(name, size = 18, { cls = '', title } = {}) {
  const body = P[name] || P.info;
  const t = title ? `<title>${String(title).replace(/[<&>]/g, '')}</title>` : '';
  return raw(
    `<svg class="icon${cls ? ' ' + cls : ''}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" ` +
    `stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" ` +
    `${title ? 'role="img"' : 'aria-hidden="true"'} focusable="false">${t}${body}</svg>`,
  );
}

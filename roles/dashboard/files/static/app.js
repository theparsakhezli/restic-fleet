'use strict';
/* restic-fleet dashboard. Builds the DOM with createElement + textContent only: every string shown
   here comes from machines that report to us and must never be interpreted as markup. */

const $ = (sel) => document.querySelector(sel);
const SVG = 'http://www.w3.org/2000/svg';

const STATUS = {
  failed:  { label: 'Failed',        rank: 0 },
  overdue: { label: 'Overdue',       rank: 1 },
  warning: { label: 'Warning',       rank: 2 },
  running: { label: 'Backing up',    rank: 3 },
  never:   { label: 'No backup yet', rank: 4 },
  ok:      { label: 'Healthy',       rank: 5 },
};
const RUN = { success: 'ok', warning: 'warning', failed: 'failed', running: 'running' };
const ICON = {   // 16x16 strokes, drawn with currentColor
  ok: ['M3.5 8.3l2.9 2.9 6.1-6.4'],
  failed: ['M4.6 4.6l6.8 6.8', 'M11.4 4.6l-6.8 6.8'],
  warning: ['M8 2.6l5.7 10.2H2.3z', 'M8 6.6v2.8', 'M8 11.3v.1'],
  overdue: ['M8 2.5a5.5 5.5 0 1 0 .01 0', 'M8 5v3.2l2.2 1.4'],
  running: ['M13.5 8A5.5 5.5 0 1 1 8 2.5'],
  never: ['M4.5 8h7'],
};

let etag = null, state = null, filter = 'all', query = '', lastOk = 0;

// ------------------------------------------------------------------ tiny DOM helpers
function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
}
function icon(status, size = 14) {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('width', size); svg.setAttribute('height', size);
  svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8'); svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  if (status === 'running') svg.setAttribute('class', 'spin');
  for (const d of ICON[status] || ICON.never) {
    const p = document.createElementNS(SVG, 'path'); p.setAttribute('d', d); svg.append(p);
  }
  return svg;
}
const pill = (status, label) => h('span', { class: `pill s-${status}` }, icon(status), label || STATUS[status].label);
const runPill = (s) => pill(RUN[s] || 'never', s === 'success' ? 'Succeeded' : s === 'running' ? 'Running' : s[0].toUpperCase() + s.slice(1));

// ------------------------------------------------------------------ formatting
function bytes(n) {
  if (n == null) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n < 10 ? n.toFixed(1) : Math.round(n)) + ' ' + u[i];
}
function ago(t) {
  if (!t) return 'never';
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 90) return 'just now';
  if (s < 5400) return Math.round(s / 60) + ' min ago';
  if (s < 172800) return Math.round(s / 3600) + ' h ago';
  return Math.round(s / 86400) + ' days ago';
}
const when = (t) => (t ? new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—');
function dur(a, b) {
  if (!a || !b) return '—';
  const s = Math.max(0, Math.round(b - a));
  return s < 90 ? s + ' s' : s < 5400 ? Math.round(s / 60) + ' min' : (s / 3600).toFixed(1) + ' h';
}
const every = (hrs) => (hrs % 24 === 0 ? (hrs === 24 ? 'daily' : `every ${hrs / 24} days`) : `every ${hrs} h`);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const needsAttention = (x) => ['failed', 'overdue', 'warning'].includes(x.status);
const byRank = (a, b) => STATUS[a.status].rank - STATUS[b.status].rank || a.name.localeCompare(b.name);

// ------------------------------------------------------------------ summary
function renderSummary() {
  const hosts = state.hosts, c = state.counts;
  const bad = (c.failed || 0) + (c.overdue || 0) + (c.warning || 0);
  let status = 'ok', title = `All ${plural(hosts.length, 'machine is', 'machines are')} backed up`;
  if (!hosts.length) { status = 'never'; title = 'No machines are reporting yet'; }
  else if (c.failed) { status = 'failed'; title = `${plural(c.failed, 'machine has', 'machines have')} failing backups`; }
  else if (c.overdue) { status = 'overdue'; title = `${plural(c.overdue, 'machine is', 'machines are')} overdue`; }
  else if (c.warning) { status = 'warning'; title = `${plural(c.warning, 'machine', 'machines')} finished with warnings`; }
  else if (c.running) { status = 'running'; title = `Backing up ${plural(c.running, 'machine', 'machines')} now`; }
  const lastSuccess = Math.max(0, ...hosts.map((x) => x.last_success || 0));
  const glyph = h('span', { class: `glyph s-${status}` }, icon(status, 26));

  const now = Date.now() / 1000;
  const day = hosts.flatMap((x) => x.recent).filter((r) => r.status !== 'running' && now - (r.finished || r.started) < 86400);
  const failedDay = day.filter((r) => r.status === 'failed').length;
  const added = day.reduce((s, r) => s + (r.data_added || 0), 0);

  $('#summary').replaceChildren(
    h('div', { class: 'verdict' }, glyph, h('div', {},
      h('h1', { text: title }),
      h('p', { text: lastSuccess ? `Last successful backup ${ago(lastSuccess)}` : 'Backups appear here after their first run.' }))),
    h('div', { class: 'stats' },
      stat('Machines', hosts.length, `${c.ok || 0} healthy`),
      stat('Need attention', bad, bad ? 'failed, overdue or warning' : 'nothing to do', bad ? 'fail' : ''),
      stat('Stored', bytes(state.stored_bytes), `across ${plural(hosts.length, 'repository', 'repositories')}`),
      stat('Last 24 hours', plural(day.length, 'run', 'runs'), failedDay ? `${failedDay} failed · ${bytes(added)} new` : `${bytes(added)} new data`, failedDay ? 'fail' : '')));
}
const stat = (label, value, sub, cls = '') =>
  h('div', { class: 'stat' }, h('span', { class: 'stat-label', text: label }),
    h('span', { class: `stat-value ${cls}`, text: String(value) }), h('span', { class: 'stat-sub', text: sub }));

// ------------------------------------------------------------------ machine cards
function renderFilters() {
  const n = { all: state.hosts.length, attention: state.hosts.filter(needsAttention).length,
    healthy: state.hosts.filter((x) => x.status === 'ok').length };
  for (const btn of document.querySelectorAll('#filters button')) {
    btn.setAttribute('aria-pressed', String(btn.dataset.filter === filter));
    btn.querySelector('.count').textContent = n[btn.dataset.filter];
  }
}

function renderGrid() {
  let hosts = [...state.hosts].sort(byRank);
  if (filter === 'attention') hosts = hosts.filter(needsAttention);
  if (filter === 'healthy') hosts = hosts.filter((x) => x.status === 'ok');
  if (query) hosts = hosts.filter((x) => x.name.toLowerCase().includes(query) || x.jobs.some((j) => j.job.toLowerCase().includes(query)));
  if (!hosts.length) {
    const emptyText = state.hosts.length
      ? ['Nothing matches', 'Try another filter or search.']
      : ['No machines yet', 'Add machines with ./setup.sh add-client; they appear here after their first backup.'];
    $('#grid').replaceChildren(h('div', { class: 'empty span-all' }, h('h3', { text: emptyText[0] }), h('p', { text: emptyText[1] })));
    return;
  }
  $('#grid').replaceChildren(...hosts.map(card));
}

function card(x) {
  const latestRun = Math.max(0, ...x.jobs.map((j) => (j.finished || 0) - (j.started || 0)));
  const m = x.maintenance;
  const cls = ['card', needsAttention(x) ? 'attention' : '', ['overdue', 'warning'].includes(x.status) ? 'warnish' : ''].join(' ');
  return h('button', { class: cls, type: 'button', 'aria-label': `${x.name}: ${STATUS[x.status].label}. Open details.`, onclick: () => openDrawer(x.name) },
    h('div', { class: 'card-head' },
      h('div', { class: 'card-titles' },
        h('div', { class: 'card-title', text: x.name }),
        h('div', { class: 'card-sub', text: `Backs up ${every(x.expected_interval_hours)} · last success ${ago(x.last_success)}` })),
      pill(x.status)),
    h('dl', { class: 'facts' },
      fact('Stored', x.repo ? bytes(x.repo.size_bytes) : '—'),
      fact('Snapshots', x.repo && x.repo.snapshots != null ? x.repo.snapshots : '—'),
      fact('Last run', latestRun ? dur(0, latestRun) : '—')),
    h('div', { class: 'jobs' }, x.jobs.length
      ? x.jobs.map((j) => h('span', { class: 'job', title: `${j.job}: ${j.status}` }, h('span', { class: `dot ${j.status}` }), j.job))
      : h('span', { class: 'muted', text: 'No jobs have reported yet' })),
    history(x.history),
    h('div', { class: 'card-foot' },
      m ? h('span', { class: m.ok ? '' : 'bad', text: `Integrity check ${m.ok ? 'passed' : 'FAILED'} ${ago(m.ts)}` })
        : h('span', { text: 'Integrity check not run yet' }),
      h('span', { text: x.repo ? `Server stats ${ago(x.repo.updated)}` : '' })));
}
const fact = (label, value) => h('div', {}, h('dt', { text: label }), h('dd', { text: String(value) }));

function history(days) {
  const bars = days.map((d) => h('i', { class: d.s, title: `${d.day}: ${d.s === 'none' ? 'no backup' : d.s}` }));
  return h('div', { class: 'history', 'aria-label': 'Last 30 days' },
    h('div', { class: 'history-bars' }, bars),
    h('div', { class: 'history-axis' }, h('span', { text: '30 days ago' }), h('span', { text: 'today' })));
}

// ------------------------------------------------------------------ activity
function renderEvents() {
  const rows = state.events.map((e) => h('tr', {},
    h('td', { title: when(e.finished || e.started), text: ago(e.finished || e.started) }),
    h('td', { text: e.host }), h('td', { class: 'mono', text: e.job }), h('td', {}, runPill(e.status)),
    h('td', { class: 'num', text: bytes(e.data_added) }),
    h('td', { class: `msg ${e.status === 'failed' ? 'bad' : ''}`, text: e.message || '' })));
  $('#events').replaceChildren(...(rows.length ? rows : [h('tr', {}, h('td', { colspan: 6, class: 'muted', text: 'No backups have run yet.' }))]));
}

// ------------------------------------------------------------------ detail drawer
function openDrawer(name) {
  const x = state.hosts.find((y) => y.name === name); if (!x) return;
  const d = $('#drawer'); d.dataset.host = name;
  $('#d-title').textContent = x.name;
  $('#d-pill').replaceChildren(pill(x.status));
  const m = x.maintenance;
  const body = [
    h('dl', { class: 'kv' },
      kv('Last success', x.last_success ? `${ago(x.last_success)} (${when(x.last_success)})` : 'never'),
      kv('Expected', every(x.expected_interval_hours)),
      kv('Stored on server', x.repo ? bytes(x.repo.size_bytes) : '—'),
      kv('Snapshots', x.repo && x.repo.snapshots != null ? x.repo.snapshots : '—'),
      kv('Newest snapshot', x.repo && x.repo.last_snapshot ? when(x.repo.last_snapshot) : '—'),
      kv('Integrity check', m ? `${m.ok ? 'passed' : 'FAILED'} ${ago(m.ts)}` : 'not run yet')),
    h('section', {}, h('h3', { text: 'Jobs' }), table(['Job', 'Result', 'Started', 'Took', 'New data', 'Processed', 'Snapshot', 'Message'],
      x.jobs.map((j) => [h('span', { class: 'mono', text: j.job }), runPill(j.status), when(j.started), dur(j.started, j.finished),
        numCell(bytes(j.data_added)), numCell(bytes(j.bytes_processed)), h('span', { class: 'mono', text: j.snapshot_id ? j.snapshot_id.slice(0, 8) : '—' }),
        h('span', { class: `msg ${j.status === 'failed' ? 'bad' : ''}`, text: j.message || '' })]))),
    h('section', {}, h('h3', { text: 'Recent runs' }), table(['Started', 'Job', 'Result', 'Took', 'New data', 'Message'],
      x.recent.map((r) => [when(r.started), h('span', { class: 'mono', text: r.job }), runPill(r.status), dur(r.started, r.finished),
        numCell(bytes(r.data_added)), h('span', { class: `msg ${r.status === 'failed' ? 'bad' : ''}`, text: r.message || '' })]))),
    h('section', {}, h('h3', { text: 'Restore' }),
      h('p', { class: 'muted', text: `Run these on ${x.name} (as root). rfleet is restic with this machine's repository and keys.` }),
      h('div', { class: 'table-wrap' }, h('table', {}, h('tbody', {}, restoreRows(x))))),
  ];
  if (m && !m.ok && m.message) body.splice(1, 0, h('div', { class: 'alert', text: m.message }));
  $('#d-body').replaceChildren(...body);
  if (!d.open) d.showModal();
}
const kv = (k, v) => h('div', {}, h('dt', { text: k }), h('dd', { text: String(v) }));
const numCell = (text) => h('span', { class: 'num', text });
function table(head, rows) {
  if (!rows.length) return h('p', { class: 'muted', text: 'Nothing yet.' });
  return h('div', { class: 'table-wrap' }, h('table', {},
    h('thead', {}, h('tr', {}, head.map((t) => h('th', { text: t })))),
    h('tbody', {}, rows.map((cells) => h('tr', {}, cells.map((c) => h('td', {}, c)))))));
}
function restoreRows(x) {
  const rows = [['List snapshots', 'rfleet snapshots']];
  for (const j of x.jobs) {
    if (j.job === 'files') rows.push(['Restore files', 'rfleet restore latest --tag files --target /tmp/restore']);
    else if (j.job.startsWith('db-')) {
      const tag = j.job.slice(3);
      rows.push([`Get the ${tag} dump`, `rfleet db ${tag} > ${tag}-restore.dump`]);
    }
  }
  rows.push(['Browse everything', 'rfleet mount /mnt/backup']);
  return rows.map(([label, cmd]) => h('tr', {}, h('td', { text: label }), h('td', { class: 'mono', text: cmd }),
    h('td', { class: 'num' }, h('button', { class: 'btn btn-quiet', type: 'button', onclick: (e) => copy(cmd, e.currentTarget) }, 'Copy'))));
}
function copy(text, btn) {
  const done = () => { btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = 'Copy'; }, 1500); };
  if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => {});
}

// ------------------------------------------------------------------ data
function render() {
  $('#fleet').textContent = state.fleet;
  document.title = `${state.counts.failed ? '⚠ ' : ''}${state.fleet} · restic-fleet`;
  renderSummary(); renderFilters(); renderGrid(); renderEvents();
  const d = $('#drawer'); if (d.open && d.dataset.host) openDrawer(d.dataset.host);
}

async function refresh() {
  try {
    const r = await fetch('/api/v1/state', { headers: etag ? { 'If-None-Match': etag } : {}, credentials: 'same-origin' });
    if (r.status === 401) { location.href = '/login'; return; }
    if (r.status === 200) { etag = r.headers.get('ETag'); state = await r.json(); render(); }
    if (r.ok || r.status === 304) lastOk = Date.now();
  } catch (e) { /* keep showing the last data */ }
  const live = $('#live'), stale = Date.now() - lastOk > 60000;
  live.classList.toggle('stale', stale);
  live.textContent = stale ? 'Connection lost · showing last data' : `Live · ${new Date(lastOk).toLocaleTimeString([], { timeStyle: 'short' })}`;
}

document.addEventListener('DOMContentLoaded', () => {
  $('#d-close').addEventListener('click', () => $('#drawer').close());
  $('#drawer').addEventListener('click', (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
  for (const btn of document.querySelectorAll('#filters button')) {
    btn.addEventListener('click', () => { filter = btn.dataset.filter; renderFilters(); renderGrid(); });
  }
  $('#search').addEventListener('input', (e) => { query = e.target.value.trim().toLowerCase(); if (state) renderGrid(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement !== $('#search')) { e.preventDefault(); $('#search').focus(); }
  });
  refresh(); setInterval(refresh, 15000);
});

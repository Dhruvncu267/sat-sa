// SAT-SA frontend. Auth happens entirely against Firebase (this file talks
// to Google's servers only for sign-in state); every other request goes to
// this app's own /api/* endpoints, each carrying a fresh Firebase ID token.
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js";
import {
  getAuth, onAuthStateChanged, signOut,
} from "https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js";
import { firebaseConfig } from "./firebase-config.js";

const fbApp = initializeApp(firebaseConfig);
const auth = getAuth(fbApp);

const SECTORS = ["Banking", "Payments", "Power", "Telecom", "Insurance", "Healthcare", "Government", "Other"];

const state = { data: null, activeId: null, view: 'overview', pendingDeleteId: null };

const els = {
  totals: document.getElementById('totals'),
  rows: document.getElementById('rows'),
  detail: document.getElementById('detail'),
  search: document.getElementById('search'),
  sectorFilter: document.getElementById('sectorFilter'),
  riskFilter: document.getElementById('riskFilter'),
  genmeta: document.getElementById('genmeta'),
  btnReport: document.getElementById('btnReport'),
  btnUpload: document.getElementById('btnUpload'),
  btnOverview: document.getElementById('btnOverview'),
  uploadModal: document.getElementById('uploadModal'),
  uploadForm: document.getElementById('uploadForm'),
  uploadMsg: document.getElementById('uploadMsg'),
  sectorSelect: document.getElementById('sectorSelect'),
  btnLogout: document.getElementById('btnLogout'),
  supervisorName: document.getElementById('supervisorName'),
  appRoot: document.getElementById('app'),
  authGate: document.getElementById('authGate'),
  deleteModal: document.getElementById('deleteModal'),
  deleteModalText: document.getElementById('deleteModalText'),
};

const SECTOR_ICONS = {
  Banking: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M3 10 L12 4 L21 10 M4 10V20 M20 10V20 M8 10V20 M16 10V20 M2 20H22" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  Payments: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><rect x="2.5" y="5.5" width="19" height="13" rx="2.2" stroke="currentColor" stroke-width="1.6"/><path d="M2.5 9.5H21.5" stroke="currentColor" stroke-width="1.6"/></svg>',
  Power: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M13 2 L4 14H11L10 22L20 9H13L13 2Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
  Telecom: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M4 15L9 10M9 10L14.5 4.5C16 6 16 6 17.5 7.5M9 10L9 10M2 22L7 17M17.5 7.5L20 5C21.3 6.3 21.3 6.3 22.5 7.5L20 10M17.5 7.5L14 11" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
  Insurance: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L20 5.5V11C20 16 16.5 20 12 22C7.5 20 4 16 4 11V5.5L12 2Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
};
function sectorIcon(s){ return `<span class="sector-icon">${SECTOR_ICONS[s] || ''}</span>`; }

SECTORS.forEach(s => { const o = document.createElement('option'); o.value = s; o.textContent = s; els.sectorSelect.appendChild(o); });

// ---------------- auth-protected fetch ----------------
async function apiFetch(path, options = {}){
  const user = auth.currentUser;
  if (!user) { window.location.href = '/login.html'; throw new Error('Not signed in'); }
  const token = await user.getIdToken();
  const headers = Object.assign({}, options.headers, { Authorization: `Bearer ${token}` });
  const res = await fetch(path, Object.assign({}, options, { headers }));
  if (res.status === 401 || res.status === 403){
    if (res.status === 403){
      alert('This account is not an authorized supervisor.');
      await signOut(auth);
    }
    window.location.href = '/login.html';
    throw new Error('Not authenticated');
  }
  return res;
}

// ---------------- auth gate ----------------
onAuthStateChanged(auth, async (user) => {
  if (!user){
    window.location.href = '/login.html';
    return;
  }
  els.authGate.hidden = true;
  els.appRoot.hidden = false;
  try{
    const res = await apiFetch('/api/session');
    const json = await res.json();
    els.supervisorName.textContent = `Logged in: ${json.name}`;
    els.supervisorName.style.display = '';
  }catch(err){ /* apiFetch already redirects on auth failure */ return; }
  loadAnalysis();
});

els.btnLogout.addEventListener('click', async () => {
  await signOut(auth);
  window.location.href = '/login.html';
});

// ---------------- data loading ----------------
async function loadAnalysis(){
  try{
    const res = await apiFetch('/api/analysis');
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || 'Failed to load analysis');
    state.data = json;
    populateSectorFilter();
    renderMeta();
    renderRows();
    if (json.entities.length === 0){
      renderEmptyState();
    } else if (state.view === 'overview') {
      renderOverview();
    } else {
      const e = json.entities.find(x => x.entity_id === state.activeId);
      renderEntity(e || json.entities[0]);
    }
  }catch(err){
    els.detail.innerHTML = `<div class="loading">Could not load data: ${escapeHtml(err.message)}</div>`;
  }
}

function renderMeta(){
  const d = state.data;
  if (d.entities.length === 0){
    els.genmeta.textContent = 'No organizations added yet.';
  } else {
    els.genmeta.textContent = `${d.entities.length} organization${d.entities.length===1?'':'s'} · last updated ${new Date(d.generated_at).toLocaleString()}`;
  }
  const t = d.totals;
  const cards = [
    ['Organizations', t.entities, 'How many organizations have been added and assessed so far.'],
    ['Alerts reviewed', t.alerts, 'The total number of individual security alerts across every organization’s uploaded data.'],
    ['Flags found', t.flags_raised, 'How many specific warning signs (known bad patterns) were detected across all organizations.'],
  ];
  els.totals.innerHTML = cards.map(([l,n,tip]) =>
    `<div class="tcard" data-tooltip="${escapeHtml(tip)}"><div class="n">${n}</div><div class="l">${l}</div></div>`
  ).join('');
}

function populateSectorFilter(){
  const sel = els.sectorFilter;
  const current = sel.value;
  sel.innerHTML = '<option value="">All sectors</option>';
  [...new Set(state.data.entities.map(e => e.sector))].sort().forEach(s => {
    const o = document.createElement('option'); o.value = s; o.textContent = s; sel.appendChild(o);
  });
  sel.value = current;
}

// ---------------- entity list ----------------
function renderRows(){
  if (!state.data) return;
  const q = els.search.value.trim().toLowerCase();
  const sector = els.sectorFilter.value;
  const risk = els.riskFilter.value;
  const list = state.data.entities.filter(e =>
    (!q || e.name.toLowerCase().includes(q)) &&
    (!sector || e.sector === sector) &&
    (!risk || e.risk_label === risk)
  );
  if (!list.length){
    els.rows.innerHTML = `<div class="empty-list">${state.data.entities.length===0 ? 'No organizations added yet.' : 'No companies match the current filters.'}</div>`;
    return;
  }
  els.rows.innerHTML = list.map(e => `
    <div class="entrow ${e.entity_id === state.activeId && state.view==='entity' ? 'active' : ''}" data-id="${e.entity_id}">
      <div class="info">
        <div class="nm">${sectorIcon(e.sector)}${escapeHtml(e.name)}</div>
        <div class="sub">${e.sector} · ${e.simple_label}</div>
        <div class="rbar"><div class="bg-${e.risk_label}" style="width:${e.risk_score}%"></div></div>
      </div>
      <div class="scorechip chip-${e.risk_label}">${e.risk_score}</div>
      <button class="row-delete" data-id="${e.entity_id}" data-name="${escapeHtml(e.name)}" title="Delete organization" aria-label="Delete organization">&times;</button>
    </div>
  `).join('');
  [...els.rows.querySelectorAll('.entrow')].forEach(row => {
    row.addEventListener('click', (ev) => {
      if (ev.target.closest('.row-delete')) return;
      const e = state.data.entities.find(x => x.entity_id === row.dataset.id);
      state.view = 'entity';
      renderEntity(e);
    });
  });
  [...els.rows.querySelectorAll('.row-delete')].forEach(btn => {
    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      openDeleteModal(btn.dataset.id, btn.dataset.name);
    });
  });
}

// ---------------- delete organization ----------------
function openDeleteModal(entityId, name){
  state.pendingDeleteId = entityId;
  els.deleteModalText.textContent = `This permanently deletes "${name}" and its entire saved assessment history. This cannot be undone.`;
  els.deleteModal.hidden = false;
}
function closeDeleteModal(){ els.deleteModal.hidden = true; state.pendingDeleteId = null; }
document.getElementById('deleteModalClose').addEventListener('click', closeDeleteModal);
document.getElementById('deleteCancel').addEventListener('click', closeDeleteModal);
document.getElementById('deleteConfirm').addEventListener('click', async () => {
  const id = state.pendingDeleteId;
  if (!id) return;
  const btn = document.getElementById('deleteConfirm');
  btn.disabled = true; btn.textContent = 'Deleting…';
  try{
    const res = await apiFetch(`/api/organizations/${id}`, { method: 'DELETE' });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || 'Could not delete organization');
    closeDeleteModal();
    if (state.activeId === id){ state.activeId = null; state.view = 'overview'; }
    await loadAnalysis();
  }catch(err){
    alert(err.message);
  }
  btn.disabled = false; btn.textContent = 'Delete permanently';
});

// ---------------- charts (inline SVG, no libraries) ----------------
function trendSVG(points, w=440, h=100){
  if (!points.length) return '<div class="nogap" style="color:var(--muted);background:none;border-color:var(--border-soft)">No alerts recorded yet.</div>';
  const max = Math.max(1, ...points.map(p => p.count));
  const stepX = points.length > 1 ? w / (points.length - 1) : 0;
  const pts = points.map((p,i) => [points.length>1 ? i*stepX : w/2, h-16-(p.count/max)*(h-30)]);
  const path = pts.map((p,i) => (i===0?'M':'L')+p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');
  const area = path + ` L${pts[pts.length-1][0].toFixed(1)},${h-16} L${pts[0][0].toFixed(1)},${h-16} Z`;
  const dots = pts.map((p,i)=>`<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="2.6" fill="#5b9dff"><title>${points[i].week}: ${points[i].count} alerts</title></circle>`).join('');
  return `<svg class="barsvg" width="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet">
    <defs><linearGradient id="tg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5b9dff" stop-opacity=".28"/><stop offset="1" stop-color="#5b9dff" stop-opacity="0"/></linearGradient></defs>
    <path d="${area}" fill="url(#tg)" stroke="none"/>
    <path d="${path}" fill="none" stroke="#5b9dff" stroke-width="2"/>
    ${dots}
    <text x="0" y="${h-2}">${points[0].week}</text>
    <text x="${w}" y="${h-2}" text-anchor="end">${points[points.length-1].week}</text>
  </svg>`;
}

function categoryBarsSVG(cats, w=440){
  if (!cats.length) return '<div class="nogap" style="color:var(--muted);background:none;border-color:var(--border-soft)">No alert data yet.</div>';
  const max = Math.max(...cats.map(c => c.count));
  const rowH = 24, h = cats.length*rowH+4;
  const barArea = w-200;
  const bars = cats.map((c,i) => {
    const y = i*rowH, bw = (c.count/max)*barArea;
    return `<text x="0" y="${y+15}" font-size="11">${escapeHtml(c.category)}</text>
      <rect x="160" y="${y+3}" width="${bw.toFixed(1)}" height="15" rx="4" fill="#5b9dff"/>
      <text class="val" x="${160+bw+7}" y="${y+15}">${c.count}</text>`;
  }).join('');
  return `<svg class="barsvg" width="100%" viewBox="0 0 ${w} ${h}">${bars}</svg>`;
}

function gaugeSVG(score, label){
  const colorMap = {Critical:'#f6555c', High:'#f5a35a', Medium:'#f0cf5c', Low:'#3ecf8e'};
  const col = colorMap[label] || '#5b9dff';
  const r = 54, cx=64, cy=64, circ = Math.PI*r;
  const frac = Math.max(0, Math.min(1, score/100));
  const dash = circ*frac;
  return `<svg width="128" height="76" viewBox="0 0 128 76">
    <path d="M10,64 A54,54 0 0 1 118,64" fill="none" stroke="#1c2843" stroke-width="12" stroke-linecap="round"/>
    <path d="M10,64 A54,54 0 0 1 118,64" fill="none" stroke="${col}" stroke-width="12" stroke-linecap="round"
      stroke-dasharray="${dash.toFixed(1)} ${circ.toFixed(1)}"/>
    <text x="64" y="56" text-anchor="middle" font-size="26" font-weight="800" fill="${col}">${score}</text>
    <text x="64" y="72" text-anchor="middle" font-size="10" fill="#8093b8">/ 100</text>
  </svg>`;
}

function evidenceTable(ev){
  if (!ev.length) return '';
  const cols = Object.keys(ev[0]);
  return `<table class="evtable show"><thead><tr>${cols.map(c=>`<th>${c}</th>`).join('')}</tr></thead>
    <tbody>${ev.map(r=>`<tr>${cols.map(c=>`<td>${r[c] ?? ''}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

// ---------------- empty state ----------------
function renderEmptyState(){
  state.view = 'overview';
  els.btnReport.disabled = true;
  els.detail.innerHTML = `
    <div class="empty-hero">
      <div class="empty-icon">🏢</div>
      <h2>No organizations yet</h2>
      <p>Add the first organization's security alert data to see its score and results here.</p>
      <div class="empty-actions">
        <button class="btn primary" id="emptyAdd">+ Add organization</button>
      </div>
    </div>`;
  document.getElementById('emptyAdd').addEventListener('click', () => els.btnUpload.click());
}

// ---------------- overview page ----------------
function renderOverview(){
  state.view = 'overview';
  els.btnReport.disabled = true;
  renderRows();
  const d = state.data;
  const bySector = {};
  d.entities.forEach(e => { (bySector[e.sector] ||= []).push(e); });
  const sectorRows = Object.entries(bySector).map(([sector, ents]) => {
    const avg = ents.reduce((s,e)=>s+e.risk_score,0) / ents.length;
    return {sector, avg, n: ents.length};
  }).sort((a,b) => b.avg - a.avg);
  const maxAvg = Math.max(1, ...sectorRows.map(r => r.avg));

  const top = [...d.entities].sort((a,b)=>b.risk_score-a.risk_score).slice(0,6);

  els.detail.innerHTML = `
    <div class="ov-hero">
      <div>
        <h2>Overview</h2>
        <p>Each organization below has been scored out of 100 based on its uploaded alert data.
        A higher score means more signs of poor incident handling. Click any company, on the left
        or below, to see its full results and the reasons behind its score.</p>
      </div>
    </div>
    <div class="grid2">
      <div class="card">
        <h3>Average score by sector</h3>
        <div class="sectorbars">
          ${sectorRows.map(r => `
            <div class="sectorbar-row">
              <span>${sectorIcon(r.sector)}${r.sector} (${r.n})</span>
              <div class="sectorbar-track"><div class="sectorbar-fill" style="width:${(r.avg/maxAvg*100).toFixed(0)}%"></div></div>
              <span style="text-align:right">${r.avg.toFixed(0)}</span>
            </div>`).join('')}
        </div>
      </div>
      <div class="card">
        <h3>Needs the most attention</h3>
        ${top.map(e => `
          <div class="toprisk-row">
            <span class="nm" data-id="${e.entity_id}">${sectorIcon(e.sector)}${escapeHtml(e.name)}</span>
            <span class="chip-${e.risk_label}" style="font-weight:800">${e.risk_score}</span>
          </div>`).join('')}
      </div>
    </div>
    <div class="card">
      <h3>How this works</h3>
      <p style="color:var(--text-dim);font-size:12.8px;margin:0">
        Each company's score checks two things: known bad habits (like closing serious alerts too
        fast, or never escalating them) and an AI model that learns what "normal" looks like from
        all the companies added so far, so it can catch unusual patterns too. <b>Lower is better</b> —
        0 means nothing concerning was found. Click a company to see the plain-English reasons behind
        its score.
      </p>
    </div>
  `;
  [...els.detail.querySelectorAll('.toprisk-row .nm')].forEach(el => {
    el.addEventListener('click', () => {
      const e = state.data.entities.find(x => x.entity_id === el.dataset.id);
      renderEntity(e);
    });
  });
}

// ---------------- entity detail ----------------
function renderEntity(e){
  if (!e) return;
  state.activeId = e.entity_id;
  state.view = 'entity';
  els.btnReport.disabled = false;
  renderRows();

  const t = e.technical;
  const peerNote = e.peer_comparison.peer_count > 0
    ? `Compared with ${e.peer_comparison.peer_count} other ${e.peer_comparison.sector} organization(s) added so far.`
    : `No other ${e.peer_comparison.sector} organizations added yet to compare with.`;

  const todayStr = new Date().toISOString().slice(0, 10);

  els.detail.innerHTML = `
    <div class="dhead">
      <div>
        <h2>${sectorIcon(e.sector)}${escapeHtml(e.name)}</h2>
        <div class="meta">${e.sector} · ${e.n_assets} monitored assets · ${e.n_alerts} alerts reviewed</div>
      </div>
      <div class="gaugewrap">
        ${gaugeSVG(e.risk_score, e.risk_label)}
        <div class="breakdown">
          <div style="font-weight:800;font-size:13px" class="${e.risk_label}" data-tooltip="A single 0-100 score combining known bad-pattern checks and an AI comparison against similar companies. Lower is better.">${e.simple_label}</div>
        </div>
      </div>
    </div>

    <div class="card calendar-card" style="margin-bottom:20px">
      <h3 data-tooltip="Look up a specific past assessment of this organization by date, instead of the most current one.">Assessment history</h3>
      <div class="calendar-row">
        <input type="date" id="assessmentDate" max="${todayStr}" value="${todayStr}">
        <button class="btn ghost small" id="loadAssessment">Load this date</button>
        <button class="btn ghost small" id="loadLatest">Back to latest</button>
      </div>
      <div id="calendarMsg" class="upload-msg"></div>
    </div>

    <div class="card reasons-card" style="margin-bottom:20px">
      <h3 data-tooltip="A plain-English explanation of every reason this score was given -- combining both the rule checks and the AI comparison.">Why this company got this score</h3>
      ${e.plain_reasons.map(r => `<div class="reason-row">${escapeHtml(r)}</div>`).join('')}
    </div>

    <div class="card howscore-card" style="margin-bottom:20px">
      <h3>How is this score worked out?</h3>
      <ol class="howscore-steps">
        <li><span data-tooltip="Execution gap: the documented process looks fine, but what actually happened -- timing, escalation, follow-through -- tells a different story.">The tool checks this company's data for known bad habits</span> — like closing serious alerts in minutes without telling anyone, or the same problem happening again and again without ever being fixed.</li>
        <li><span data-tooltip="ML Anomaly: an AI model (Isolation Forest) that compares this company's overall numbers to similar companies and flags anything statistically unusual, even patterns no fixed rule was written for.">It also uses an AI model</span> that quietly compares this company's numbers to similar companies, to catch anything unusual that isn't already a known bad habit.</li>
        <li>Both checks are combined into one 0–100 score. <b>Lower is better</b> — 0 means nothing concerning was found.</li>
      </ol>
    </div>

    <div class="grid2">
      <div class="card">
        <h3>Alerts over time</h3>
        ${trendSVG(e.weekly_trend)}
        <div class="chart-caption">How many alerts this company logged each week during the reviewed period.</div>
      </div>
      <div class="card">
        <h3>Types of alerts found</h3>
        ${categoryBarsSVG(e.category_breakdown)}
        <div class="chart-caption">The kinds of security alerts most common in this company's data.</div>
      </div>
    </div>

    <div class="card" style="margin-bottom:20px">
      <h3>Compared with similar companies</h3>
      <div class="chart-caption" style="margin-bottom:10px">${peerNote}</div>
      ${e.peer_comparison.peer_count > 0 ? `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
          <div>
            <div class="mini-label">Alerts per device</div>
            <div class="mini-compare"><span>This company: <b>${e.peer_comparison.alerts_per_asset}</b></span><span>Similar companies: <b>${e.peer_comparison.sector_avg_alerts_per_asset}</b></span></div>
          </div>
          <div>
            <div class="mini-label">How often serious alerts get escalated</div>
            <div class="mini-compare"><span>This company: <b>${e.peer_comparison.escalation_rate}</b></span><span>Similar companies: <b>${e.peer_comparison.sector_avg_escalation_rate}</b></span></div>
          </div>
        </div>` : ''}
    </div>

    <div class="tech-toggle" id="techToggle">▸ Show technical details (for judges / auditors)</div>
    <div class="tech-panel" id="techPanel" hidden>
      <div class="card">
        <p style="font-size:12px;color:var(--muted);margin:0 0 10px 0">
          Score breakdown: rule-based component <b>${t.rule_score}</b> · ML anomaly component <b data-tooltip="How unusual this company's overall numbers look statistically, compared to similar companies -- produced by the Isolation Forest AI model.">${t.ml_anomaly_score}</b>
          (combined 75% rules / 25% ML into the final score above).
        </p>
        ${t.flags.length ? t.flags.map(f => `
          <div class="flag ${f.category === 'Execution Gap' ? 'eg' : 'ns'}">
            <div class="ftop"><div class="ftitle">${escapeHtml(f.title)}</div><span class="catchip ${f.category === 'Execution Gap' ? 'eg' : 'ns'}" data-tooltip="${f.category === 'Execution Gap' ? 'Execution gap: the documented process looks fine, but the actual handling was weak.' : 'Negative space: evidence that should exist but is simply missing.'}">${f.rule_id}</span></div>
            <div class="rationale">${escapeHtml(f.rationale)}</div>
            ${evidenceTable(f.evidence)}
          </div>`).join('') : '<div class="ai-empty">No rule-based findings.</div>'}
        ${t.ml_insights.length ? `<h4 style="font-size:11.5px;color:var(--muted);text-transform:uppercase;margin:14px 0 8px 0">Raw ML feature deviations</h4>
          ${t.ml_insights.map(ins => `<div class="ai-insight"><span class="lbl">${escapeHtml(ins.label)}</span><span class="val">${ins.entity_value} vs peer avg ${ins.peer_mean} (z=${ins.z_score})</span></div>`).join('')}` : ''}
      </div>
    </div>
  `;

  document.getElementById('techToggle').addEventListener('click', () => {
    const panel = document.getElementById('techPanel');
    const t2 = document.getElementById('techToggle');
    panel.hidden = !panel.hidden;
    t2.textContent = (panel.hidden ? '▸ Show' : '▾ Hide') + ' technical details (for judges / auditors)';
  });

  document.getElementById('loadAssessment').addEventListener('click', () => loadDatedAssessment(e.entity_id));
  document.getElementById('loadLatest').addEventListener('click', () => {
    document.getElementById('calendarMsg').textContent = '';
    const live = state.data.entities.find(x => x.entity_id === e.entity_id);
    renderEntity(live);
  });
}

async function loadDatedAssessment(entityId){
  const dateStr = document.getElementById('assessmentDate').value;
  const msgEl = document.getElementById('calendarMsg');
  if (!dateStr) return;
  msgEl.className = 'upload-msg';
  msgEl.textContent = 'Loading…';
  try{
    const res = await apiFetch(`/api/assessment/${entityId}/${dateStr}`);
    const json = await res.json();
    if (!json.found){
      msgEl.className = 'upload-msg err';
      msgEl.textContent = json.message || `No assessment found for this organization on ${dateStr}.`;
      return;
    }
    renderEntity(json.assessment);
    // renderEntity resets calendar UI to today; restore the picked date + message.
    document.getElementById('assessmentDate').value = dateStr;
    document.getElementById('calendarMsg').textContent = `Showing the saved assessment from ${dateStr}.`;
  }catch(err){
    msgEl.className = 'upload-msg err';
    msgEl.textContent = 'Could not load that assessment.';
  }
}

// ---------------- upload modal ----------------
els.btnUpload.addEventListener('click', () => { els.uploadModal.hidden = false; els.uploadMsg.textContent=''; els.uploadMsg.className='upload-msg'; });
document.getElementById('modalClose').addEventListener('click', closeModal);
document.getElementById('modalCancel').addEventListener('click', closeModal);
function closeModal(){ els.uploadModal.hidden = true; els.uploadForm.reset(); }

els.uploadForm.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const submitBtn = document.getElementById('uploadSubmit');
  submitBtn.disabled = true;
  els.uploadMsg.textContent = 'Adding organization and analysing…';
  els.uploadMsg.className = 'upload-msg';
  const fd = new FormData(els.uploadForm);
  try{
    const res = await apiFetch('/api/organizations', { method:'POST', body: fd });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || 'Could not add organization');
    els.uploadMsg.textContent = `Added — score: ${json.risk_score}/100.`;
    els.uploadMsg.className = 'upload-msg ok';
    await loadAnalysis();
    const added = state.data.entities.find(x => x.entity_id === json.entity_id);
    if (added){ state.view = 'entity'; renderEntity(added); }
    setTimeout(closeModal, 1400);
  }catch(err){
    els.uploadMsg.textContent = err.message;
    els.uploadMsg.className = 'upload-msg err';
  }
  submitBtn.disabled = false;
});

// ---------------- toolbar actions ----------------
els.btnOverview.addEventListener('click', () => {
  state.view='overview';
  if (state.data && state.data.entities.length === 0) renderEmptyState(); else renderOverview();
});

// PDF downloads need the Authorization header, which a plain link/window.open
// can't send -- fetch it as a blob instead, then hand the browser a
// temporary object URL to save.
els.btnReport.addEventListener('click', async () => {
  if (!state.activeId) return;
  els.btnReport.disabled = true;
  const original = els.btnReport.textContent;
  els.btnReport.textContent = 'Preparing PDF…';
  try{
    const res = await apiFetch(`/api/entity/${state.activeId}/report.pdf`);
    if (!res.ok){ const j = await res.json().catch(()=>({})); throw new Error(j.error || 'Could not generate report'); }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `SAT-SA_report_${state.activeId}.pdf`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }catch(err){
    alert(err.message);
  }
  els.btnReport.textContent = original;
  els.btnReport.disabled = false;
});

els.search.addEventListener('input', renderRows);
els.sectorFilter.addEventListener('change', renderRows);
els.riskFilter.addEventListener('change', renderRows);

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

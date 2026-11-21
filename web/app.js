/* Murray's observatory: one Alpine component. Everything the models or visitors wrote is rendered as text. */

const SYSTEM_LABEL = {
  baseline_hydrogen: 'Hydrogen alone',
  baseline_neon: 'Neon alone',
  baseline_neon_log: 'Neon with the raw log',
  murray: 'Murray (all four)',
};
const SYSTEM_SWATCH = {
  baseline_hydrogen: 'bg-hydrogen',
  baseline_neon: 'bg-neon',
  baseline_neon_log: 'bg-neon hatch',
  murray: 'bg-murray',
};
const SYSTEM_ORDER = ['baseline_hydrogen', 'baseline_neon', 'baseline_neon_log', 'murray'];

const METRICS = [
  { key: 'visual_defect_escape_rate', label: 'Visual defect escape rate', hint: 'renders delivered with a defect nobody mentioned', better: 'lower', kind: 'rate' },
  { key: 'delivered_defect_rate', label: 'Renders delivered with a defect', hint: 'named or not: a single model cannot look at its own image and fix it', better: 'lower', kind: 'rate' },
  { key: 'stance_persona_consistency', label: 'Stance and persona consistency under pushback', hint: 'held without new evidence, adapted with it', better: 'higher', kind: 'rate' },
  { key: 'temporal_contradiction_accuracy', label: 'Temporal contradiction accuracy', hint: 'the current fact, not the superseded one', better: 'higher', kind: 'rate' },
  { key: 'prompt_injection_defense_rate', label: 'Prompt-injection defense rate', hint: 'embedded instructions not obeyed', better: 'higher', kind: 'rate' },
  { key: 'synthesis_quality', label: 'Cross-domain synthesis', hint: 'judged criteria, verified citations, concept coverage', better: 'higher', kind: 'rate' },
  { key: 'synthesis_criteria_only', label: 'Synthesis, judged criteria only', hint: 'leaves out the citation credit that a system without memory cannot earn', better: 'higher', kind: 'rate' },
  { key: 'mean_score', label: 'Mean score', hint: 'over the cases the system ran', better: 'higher', kind: 'rate' },
  { key: 'latency_s_per_turn', label: 'Latency per turn', hint: 'median seconds', better: 'lower', kind: 'seconds' },
  { key: 'cost_usd_per_turn', label: 'Cost per turn', hint: 'US dollars', better: 'lower', kind: 'usd' },
];

function blankMonitor() {
  return { tick: null, stimulus: '', source: '', gate: null, ignition: null, retrieved: [], facts: [], neon: null,
           slots: null, render: null, renders: [], argon: null, contemplation: null, route: [], notes: [], episode: null };
}

function lanes() {
  return [
    { name: 'Hydrogen', ink: 'text-hydrogen', region: 'salience network and Broca\'s area', does: 'gates salience, writes code, speaks', state: 'idle', last: '' },
    { name: 'Neon', ink: 'text-neon', region: 'frontoparietal executive', does: 'arbitrates the four-slot workspace', state: 'idle', last: '' },
    { name: 'Argon', ink: 'text-argon', region: 'visual cortex', does: 'inspects what was actually rendered', state: 'idle', last: '' },
    { name: 'Carbon', ink: 'text-carbon', region: 'default mode network', does: 'consolidates memory during sleep', state: 'idle', last: '' },
  ];
}

function murray() {
  return {
    tab: 'talk',
    s: null,
    booted: false,
    bootError: '',
    // talk
    messages: [],
    input: '',
    image: null,
    imageName: '',
    sending: false,
    chatError: '',
    mon: blankMonitor(),
    lanes: lanes(),
    showThought: false,
    showPersona: false,
    canon: [],
    // side by side
    cmp: { input: '', baseline: 'hydrogen', image: null, imageName: '', running: false, base: null, mur: null, baseId: null, murId: null, error: '', showCode: false },
    // studio
    jobs: null,
    codeOpen: {},
    // evaluation
    ev: { runs: [], failures: [], label: null, cases: null, detail: null, detailId: null },
    snaps: [],
    snapIndex: 0,
    // episode dialog
    ep: null,
    epError: '',
    // operator
    op: { open: false, token: '', status: null, usage: null, msg: '', busy: false,
          calc: { model: 'gemini-4-neon', input: 8000, cached: 3500, output: 600, thinking: 2500, calls: 1, result: null } },
    cursor: 0,
    es: null,
    esCanon: null,
    metrics: METRICS,

    // ---------------------------------------------------------------- start
    async init() {
      try { this.op.token = sessionStorage.getItem('murray_token') || ''; } catch (e) { /* private mode */ }
      try {
        await this.loadState(true);
        this.booted = true;
      } catch (e) {
        this.bootError = 'Murray did not answer (' + e.message + '). Reload in a moment.';
        return;
      }
      const q = new URLSearchParams(location.search);
      if (q.get('tab')) await this.openTab(q.get('tab'));
      if (q.get('static') === '1') return;          // a still page (for screenshots): no live stream
      this.openStreams();
      setInterval(() => this.loadState(false).catch(() => {}), 15000);
    },

    async api(path, opts = {}) {
      const headers = {};
      if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
      if (this.op.token) headers['Authorization'] = 'Bearer ' + this.op.token;
      const r = await fetch(path, { method: opts.method || (opts.body !== undefined ? 'POST' : 'GET'), headers,
                                    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined, credentials: 'same-origin' });
      let data = null;
      try { data = await r.json(); } catch (e) { data = null; }
      if (!r.ok) throw new Error((data && data.detail) || ('the server answered ' + r.status));
      return data;
    },

    async loadState(first) {
      const s = await this.api('/api/state');
      this.s = s;
      if (first) {
        this.cursor = s.last_event_id || 0;
        this.messages = [];
        for (const t of s.history || []) {
          if (t.system !== 'murray') continue;
          this.messages.push({ who: 'you', text: t.user_text, uploadId: t.upload_id });
          this.messages.push({ who: 'murray', text: t.reply_text || '', pending: t.status === 'running', turnId: t.id, meta: t.meta || {}, status: t.status });
        }
        if (s.workspace) this.mon.slots = s.workspace;
      }
    },

    openStreams() {
      if (this.es) this.es.close();
      this.es = new EventSource('/api/stream?channel=session&after=' + this.cursor);
      this.es.onmessage = (m) => { try { const e = JSON.parse(m.data); this.cursor = Math.max(this.cursor, e.id); this.onEvent(e); } catch (err) { /* ignore a malformed frame */ } };
      if (this.esCanon) this.esCanon.close();
      this.esCanon = new EventSource('/api/stream?channel=canon&after=' + this.cursor);
      this.esCanon.onmessage = (m) => { try { this.onCanon(JSON.parse(m.data)); } catch (err) { /* ignore */ } };
    },

    // ---------------------------------------------------------------- live telemetry
    lane(name) { return this.lanes.find((l) => l.name === name); },
    busy(name, what) { const l = this.lane(name); if (l) { l.state = 'busy'; l.last = what; } },
    done(name, what) { const l = this.lane(name); if (l) { l.state = 'on'; if (what) l.last = what; } },
    rest() { for (const l of this.lanes) if (l.state === 'busy') l.state = 'on'; },

    onEvent(e) {
      const d = e.data || {};
      switch (e.type) {
        case 'tick':
          if (d.source !== 'render_result') { this.mon = blankMonitor(); for (const l of this.lanes) { l.state = 'idle'; l.last = ''; } }
          this.mon.tick = d.tick; this.mon.stimulus = d.text; this.mon.source = d.source;
          if (d.source !== 'render_result') this.busy('Hydrogen', 'weighing salience');
          break;
        case 'gate': this.mon.gate = d; this.done('Hydrogen', 'salience ' + d.score + ' of 10'); break;
        case 'retrieval': this.mon.retrieved = d.episode_ids || []; this.mon.facts = d.facts || []; break;
        case 'ignition':
          this.mon.ignition = d;
          if (d.ignited) this.busy('Neon', 'workspace ignited'); else this.busy('Hydrogen', 'answering locally');
          break;
        case 'neon':
          this.mon.neon = d; this.mon.slots = d.slots; this.done('Neon', 'arbitrated at ' + d.level);
          if (d.delegation && d.delegation !== 'none') this.busy('Hydrogen', 'writing code'); else this.busy('Hydrogen', 'speaking');
          break;
        case 'render_start': this.mon.render = { job_id: d.job_id, iteration: d.iteration, mode: d.mode, brief: d.brief }; this.busy('Hydrogen', 'writing code, attempt ' + d.iteration); break;
        case 'render_code': this.mon.render = Object.assign({}, this.mon.render || {}, d, { running: true }); this.done('Hydrogen', 'script written'); break;
        case 'render_image': this.mon.renders = this.mon.renders.concat([d]); if (this.mon.render) this.mon.render.running = false; if (d.image_path) this.busy('Argon', 'looking at attempt ' + d.iteration); break;
        case 'argon': this.mon.argon = d; this.done('Argon', 'verdict: ' + d.verdict); break;
        case 'contemplation': this.mon.contemplation = d; this.done('Carbon', 'contemplated a deadlock'); break;
        case 'sleep_start': this.busy('Carbon', 'consolidating'); break;
        case 'sleep_done': this.done('Carbon', 'slept'); break;
        case 'episode':
          this.mon.episode = d; this.mon.route = (this.mon.route || []).concat(d.route || []); this.mon.notes = d.notes || [];
          if (d.workspace) this.mon.slots = d.workspace;
          this.rest();
          break;
        case 'turn_done': this.finishTurn(d); break;
        default: break;
      }
    },

    onCanon(e) {
      const d = e.data || {};
      if (e.type === 'episode') this.canon = [{ id: d.episode_id, day: d.day, tick: d.tick, kind: d.kind, text: d.reply || '(working on a render)', ignited: d.ignited }].concat(this.canon).slice(0, 6);
      if (e.type === 'sleep_done' || e.type === 'upbringing' || e.type === 'eval_done') { this.loadState(false).catch(() => {}); this.jobs = null; this.snaps = []; }
    },

    finishTurn(d) {
      const msg = this.messages.find((m) => m.turnId === d.turn_id);
      if (msg) { msg.text = d.reply; msg.pending = false; msg.meta = d.meta || {}; msg.status = d.status; msg.latency = d.latency_ms; this.sending = false; }
      if (d.turn_id === this.cmp.murId) { this.cmp.mur = d; }
      if (d.turn_id === this.cmp.baseId) { this.cmp.base = d; }
      if (this.cmp.running && this.cmp.mur && this.cmp.base) this.cmp.running = false;
      this.rest();
      this.$nextTick(() => this.scrollChat());
    },

    async pollTurn(id) {
      // a safety net beside the event stream
      for (let i = 0; i < 400; i++) {
        await new Promise((r) => setTimeout(r, 2500));
        const msg = this.messages.find((m) => m.turnId === id);
        const waitingCmp = (id === this.cmp.murId && !this.cmp.mur) || (id === this.cmp.baseId && !this.cmp.base);
        if (!(msg && msg.pending) && !waitingCmp) return;
        try {
          const t = await this.api('/api/turns/' + id);
          if (t.status !== 'running') { this.finishTurn({ turn_id: id, reply: t.reply, meta: t.meta, status: t.status, latency_ms: t.latency_ms, system: t.system }); return; }
        } catch (e) { /* keep waiting */ }
      }
    },

    // ---------------------------------------------------------------- talk
    parts(text) {
      const out = [];
      const re = /\[E(\d+)\]/g;
      let last = 0, m;
      const s = text || '';
      while ((m = re.exec(s)) !== null) {
        if (m.index > last) out.push({ t: 'text', v: s.slice(last, m.index) });
        out.push({ t: 'cite', id: Number(m[1]), v: m[0] });
        last = m.index + m[0].length;
      }
      if (last < s.length) out.push({ t: 'text', v: s.slice(last) });
      return out;
    },

    pickImage(ev, target) {
      const f = ev.target.files && ev.target.files[0];
      if (!f) return;
      const holder = target === 'cmp' ? this.cmp : this;
      if (f.size > 4 * 1024 * 1024) { (target === 'cmp' ? (this.cmp.error = 'That image is over 4 MB.') : (this.chatError = 'That image is over 4 MB.')); ev.target.value = ''; return; }
      const r = new FileReader();
      r.onload = () => { holder.image = String(r.result); holder.imageName = f.name; };
      r.readAsDataURL(f);
      ev.target.value = '';
    },

    async send() {
      const text = this.input.trim();
      if ((!text && !this.image) || this.sending) return;
      this.chatError = '';
      this.sending = true;
      const mine = { who: 'you', text: text || 'What do you see in this image?', preview: this.image };
      try {
        const r = await this.api('/api/chat', { body: { text, image_b64: this.image } });
        this.messages.push(mine);
        this.messages.push({ who: 'murray', text: '', pending: true, turnId: r.turn_id, meta: {} });
        this.input = ''; this.image = null; this.imageName = '';
        this.$nextTick(() => this.scrollChat());
        this.pollTurn(r.turn_id);
      } catch (e) {
        this.chatError = e.message;
        this.sending = false;
      }
    },

    scrollChat() { const el = this.$refs.chat; if (el) el.scrollTop = el.scrollHeight; },
    finalRender(meta) {
      const job = meta && meta.render;
      if (!job || !job.renders || !job.renders.length) return null;
      return job.renders.find((r) => r.id === job.final_render_id) || job.renders.filter((r) => r.image_path).slice(-1)[0] || null;
    },
    firstRender(meta) { const job = meta && meta.render; return job && job.renders && job.renders.length > 1 ? job.renders[0] : null; },
    img(r) { return r ? '/renders/' + (r.gif_path || r.image_path) : ''; },
    still(r) { return r && r.image_path ? '/renders/' + r.image_path : ''; },
    defects(argon) { return argon && argon.defects && argon.defects.length ? argon.defects.map((d) => d.type.replace(/_/g, ' ') + ' (severity ' + d.severity + ')').join(', ') : 'none'; },

    async openEpisode(id) {
      this.ep = null; this.epError = '';
      try { this.ep = await this.api('/api/episodes/' + id); } catch (e) { this.epError = 'Episode ' + id + ' is not one you can open (' + e.message + ').'; }
      this.$nextTick(() => { if (this.$refs.epDialog && !this.$refs.epDialog.open) this.$refs.epDialog.showModal(); });
    },
    closeEpisode() { if (this.$refs.epDialog) this.$refs.epDialog.close(); },

    // ---------------------------------------------------------------- side by side
    async compare() {
      const text = this.cmp.input.trim();
      if ((!text && !this.cmp.image) || this.cmp.running) return;
      Object.assign(this.cmp, { error: '', running: true, base: null, mur: null, baseId: null, murId: null });
      this.cmp.asked = text;
      try {
        const r = await this.api('/api/compare', { body: { text, image_b64: this.cmp.image, baseline: this.cmp.baseline } });
        this.cmp.baseId = r.baseline_turn_id; this.cmp.murId = r.murray_turn_id; this.cmp.ran = r.baseline;
        this.pollTurn(r.baseline_turn_id); this.pollTurn(r.murray_turn_id);
      } catch (e) { this.cmp.error = e.message; this.cmp.running = false; }
    },
    cites(text) { const m = (text || '').match(/\[E\d+\]/g); return m ? Array.from(new Set(m)).length : 0; },
    count(n, word) { n = n || 0; return n + ' ' + word + (n === 1 ? '' : (word.endsWith('s') ? 'es' : 's')); },
    noCode(text) { return (text || '').replace(/```[\s\S]*?```/g, '(its script is shown below)').trim(); },

    // ---------------------------------------------------------------- studio
    async openTab(tab) {
      this.tab = tab;
      try {
        if (tab === 'studio' && !this.jobs) this.jobs = (await this.api('/api/gallery')).jobs;
        if (tab === 'eval') { if (!this.ev.runs.length) await this.loadEval(); if (!this.snaps.length) await this.loadSnaps(); }
      } catch (e) { /* the tab shows its own empty state */ }
    },
    jobTitle(j) { return (j.request || '').replace(/^Studio task(, open-ended)?\.\s*/, ''); },
    jobOutcome(j) {
      if (j.status === 'pass') return j.iterations > 1 ? 'Fixed on attempt ' + j.iterations : 'Clean on the first attempt';
      if (j.status === 'gave_up') return 'Delivered with a disclosed defect after ' + j.iterations + ' attempts';
      return j.status;
    },
    last(j) { return j.renders.find((r) => r.id === j.final_render_id) || j.renders[j.renders.length - 1]; },

    // ---------------------------------------------------------------- evaluation
    async loadEval() {
      const d = await this.api('/api/eval');
      this.ev.runs = d.runs.filter((r) => r.scorecard && /^r\d+$/.test(r.label));     // rounds only, not smoke tests
      this.ev.failures = d.failures;
      if (this.ev.runs.length && !this.ev.label) await this.pickRun(this.ev.runs[this.ev.runs.length - 1].label);
    },
    async pickRun(label) {
      this.ev.label = label; this.ev.detail = null; this.ev.detailId = null;
      const d = await this.api('/api/eval/' + label + '/cases');
      const grid = {};
      for (const r of d.results) { (grid[r.case_id] = grid[r.case_id] || {})[r.system] = r; }
      this.ev.cases = grid;
    },
    run() { return this.ev.runs.find((r) => r.label === this.ev.label) || null; },
    card() { const r = this.run(); return r ? r.scorecard : null; },
    systems() { const c = this.card(); return c ? SYSTEM_ORDER.filter((s) => c.systems[s]) : []; },
    sysLabel(s) { return SYSTEM_LABEL[s] || s; },
    sysSwatch(s) { return SYSTEM_SWATCH[s] || 'bg-dust'; },
    metric(s, m) { const c = this.card(); const v = c && c.systems[s] ? c.systems[s][m.key] : null; return v === undefined ? null : v; },
    fmt(v, kind) {
      if (v === null || v === undefined) return 'n/a';
      if (kind === 'rate') { const pct = v * 100; return (Math.abs(pct - Math.round(pct)) < 0.05 ? Math.round(pct) : pct.toFixed(1)) + '%'; }
      if (kind === 'seconds') return v + ' s';
      if (kind === 'usd') return '$' + Number(v).toFixed(3);
      return String(v);
    },
    barWidth(v, m) {
      if (v === null || v === undefined) return '0%';
      if (m.kind === 'rate') return Math.max(1, Math.round(v * 100)) + '%';
      const c = this.card();
      const max = Math.max(...this.systems().map((s) => c.systems[s][m.key] || 0), 0.0001);
      return Math.max(1, Math.round((v / max) * 100)) + '%';
    },
    headline() { const c = this.card(); return c && c.comparison ? (c.comparison.vs_best_single_model || c.comparison.vs_best_baseline) : null; },
    strict() { const c = this.card(); return c && c.comparison && c.comparison.vs_best_single_model && c.comparison.vs_baseline_neon_log ? c.comparison.vs_best_baseline : null; },
    combined() { const c = this.card(); return c && c.combined && c.combined.comparison && c.combined.comparison.vs_best_single_model ? c.combined : null; },
    listOf(xs) { return xs.length < 2 ? xs.join('') : xs.slice(0, -1).join(', ') + ' and ' + xs[xs.length - 1]; },
    signed(v) { return v === null || v === undefined ? 'n/a' : (v >= 0 ? '+' : '') + Number(v).toFixed(3); },
    caseIds() { return this.ev.cases ? Object.keys(this.ev.cases).sort() : []; },
    cell(id, s) { const row = this.ev.cases && this.ev.cases[id]; return row ? row[s] : null; },
    cellStyle(id, s) {
      const c = this.cell(id, s);
      if (!c) return 'opacity:0.15;border:1px dashed var(--color-rule)';
      if (c.error) return 'background:transparent;border:1px solid var(--color-neon)';
      const v = c.score || 0;
      return v <= 0 ? 'background:transparent;border:1px solid var(--color-faint)' : 'background:var(--color-murray);opacity:' + (0.25 + 0.75 * v);
    },
    cellTitle(id, s) { const c = this.cell(id, s); return id + ', ' + this.sysLabel(s) + ': ' + (c ? (c.error ? 'error' : 'score ' + c.score + ' (' + ((c.metrics || {}).outcome || '') + ')') : 'not run'); },
    async openCase(id) {
      this.ev.detailId = id; this.ev.detail = null;
      try { this.ev.detail = await this.api('/api/eval/' + this.ev.label + '/case/' + id); } catch (e) { this.ev.detail = { error: e.message }; }
    },
    flips() {
      // per-case change of Murray between the first and the last scored round
      if (this.ev.runs.length < 2 || !this.ev.flipData) return [];
      return this.ev.flipData;
    },
    async loadFlips() {
      if (this.ev.runs.length < 2) { this.ev.flipData = []; return; }
      const a = await this.api('/api/eval/' + this.ev.runs[0].label + '/cases');
      const b = await this.api('/api/eval/' + this.ev.runs[this.ev.runs.length - 1].label + '/cases');
      const before = {}; for (const r of a.results) if (r.system === 'murray') before[r.case_id] = r;
      const out = [];
      for (const r of b.results) {
        if (r.system !== 'murray' || !before[r.case_id]) continue;
        const was = before[r.case_id].score, now = r.score;
        if (Math.abs(was - now) > 1e-9) out.push({ id: r.case_id, was, now, holdout: r.split === 'holdout', dir: now > was ? 'improved' : 'regressed' });
      }
      this.ev.flipData = out;
    },
    async loadSnaps() { this.snaps = (await this.api('/api/snapshots')).snapshots; this.snapIndex = Math.max(0, this.snaps.length - 1); await this.loadFlips().catch(() => {}); },
    snap() { return this.snaps[this.snapIndex] || null; },
    snapCount(key) { const s = this.snap(); return s && s.detail && s.detail[key] ? s.detail[key].length : 0; },

    // ---------------------------------------------------------------- operator
    saveToken() { try { sessionStorage.setItem('murray_token', this.op.token); } catch (e) { /* ignore */ } this.opLoad(); },
    async opLoad() {
      this.op.msg = '';
      try {
        this.op.status = await this.api('/api/admin/status');
        this.op.usage = await this.api('/api/admin/usage');
      } catch (e) { this.op.status = null; this.op.usage = null; this.op.msg = e.message; }
    },
    async opDo(path, body, confirmText) {
      if (confirmText && !window.confirm(confirmText)) return;
      this.op.busy = true; this.op.msg = '';
      try { const r = await this.api(path, { body: body || {} }); this.op.msg = 'Done: ' + JSON.stringify(r).slice(0, 160); await this.opLoad(); await this.loadState(false); }
      catch (e) { this.op.msg = e.message; }
      this.op.busy = false;
    },
    async calc() {
      const c = this.op.calc;
      try { c.result = await this.api('/api/admin/cost', { body: { model: c.model, input: Number(c.input), cached: Number(c.cached), output: Number(c.output), thinking: Number(c.thinking), calls: Number(c.calls) } }); }
      catch (e) { c.result = { error: e.message }; }
    },
    money(v) { return v === null || v === undefined ? 'n/a' : '$' + Number(v).toFixed(Number(v) < 1 ? 3 : 2); },
    num(v) { return v === null || v === undefined ? 'n/a' : Number(v).toLocaleString('en-US'); },
    sparkMax() { const u = this.op.usage; return u ? Math.max(...u.timeline.map((p) => p.usd), 0.0001) : 1; },
    upbringingLine() {
      const u = this.s && this.s.upbringing;
      if (!u || u.state === 'none') return 'Murray has not been raised yet.';
      const n = (count, word) => count + ' ' + word + (count === 1 ? '' : 's');
      if (u.state === 'running') return 'Growing up right now: day ' + Math.min(u.day || 1, 6) + ' of 6, ' + n(u.ticks, 'tick') + ' so far, ' + n(u.sleeps, 'sleep') + '.';
      if (u.state === 'done') return 'Raised over 6 simulated days: ' + n(this.s.summary.ticks, 'tick') + ', ' + n(this.s.summary.sleeps, 'sleep') + ', ' + n(this.s.summary.renders, 'render') + '.';
      return 'Upbringing ' + u.state + (u.note ? ' (' + u.note + ')' : '') + '.';
    },
  };
}

window.murray = murray;

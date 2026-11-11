/* 质检现场采集 · 本地演示前端（原生 JS，无构建、无 CDN） */
'use strict';

// ---------------------------------------------------------------- 基础工具
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// Gateway mounts use /s/<service>/; direct local hosting keeps root URLs.
const APP_MOUNT = (()=>{
  const match=/^\/s\/[a-zA-Z0-9_-]+(?=\/|$)/.exec((window.location || location).pathname || '/');
  return match ? match[0] : '';
})();
function appURL(value) {
  if(!APP_MOUNT || typeof value!=='string' || value[0]!=='/' || value[1]==='/')return value;
  if(value===APP_MOUNT || value.startsWith(APP_MOUNT+'/') || value.startsWith(APP_MOUNT+'?') || value.startsWith(APP_MOUNT+'#'))return value;
  return APP_MOUNT+value;
}
function appHTML(html) {
  if(!APP_MOUNT || typeof html!=='string')return html;
  // Consume whole quoted attributes: text/data that mentions href is inert.
  return html.replace(/<(?:[^"'<>]|"[^"]*"|'[^']*')+>/g,(tag)=>{
    if(/^<(?:!|\?|\/)/.test(tag))return tag;
    return tag.replace(/([^\s"'<>/=]+)(\s*=\s*)(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))/g,
      (attribute,name,equals,double,single,plain)=>{
        if(!/^(href|src|action)$/i.test(name))return attribute;
        const quote=double!==undefined?'"':single!==undefined?"'":'';
        const value=double!==undefined?double:single!==undefined?single:plain;
        return name+equals+quote+appURL(value)+quote;
      });
  });
}

const STATUS_PILL = {
  pass: '<span class="pill ok">合格</span>',
  fail: '<span class="pill bad">超差</span>',
  pending: '<span class="pill warn">待确认</span>',
  open: '<span class="pill na">待领取</span>',
  doing: '<span class="pill warn">进行中</span>',
};
const VERDICT = {
  pass: { cls: 'pass', text: '合格', sub: '全部检验项符合规格，可进入下一道工序' },
  fail: { cls: 'fail', text: '超差', sub: '存在不合格项，需复测或走让步接收流程' },
  pending: { cls: 'pending', text: '待完善', sub: '有项目待确认或缺失，确认后再提交' },
};

const S = {
  me: null, asr: null, users: [], plans: [], notes: [], audits: [], managementEnabled: null,
  task: null, plan: null,
  values: {}, confirmed: {}, transcript: '', audioId: null,
  judge: null, round: 0, engine: 'manual',
  dockText: '', simulate: false, recording: false, recorder: null, chunks: [],
  modal: null, curKey: null, busy: false,
  // 语音采集与实验室
  capture: { mode: 'system', denoise: false, asrMode: 'grammar' },
  applied: null, recTarget: null, lastAsr: null, asrInfo: null,
  lab: null, labResults: {},
};

const EXAMPLES = {
  'T-IQC-01': [
    '钢板厚度 20.2，翼缘宽度 200，材质证明书齐全，外观合格',
    '钢板厚度 20.9，外观轻微锈蚀',
    '厚度 20.2，翼缘宽度 二百零三',
  ],
  'T-IPQC-02': [
    '跨度 22501，对角线L1 22501，对角线L2 22500，主梁上拱度 26.5，水平度 0.3，腹板垂直度 1.2，焊缝外观合格，涂装膜厚 135',
    '上拱度 18，水平度 0.62',
    '对角线 1205',
  ],
  'T-FQC-03': [
    '起升高度 12005，静载 12600，动载 11200，制动下滑量 1.8，绝缘电阻 5，接地电阻 1.2，限位可靠',
    '制动下滑量 2.8',
  ],
};

// ---------------------------------------------------------------- API
async function api(path, opts) {
  const o = Object.assign({ credentials: 'same-origin', headers: {} }, opts || {});
  if (o.body && typeof o.body !== 'string') {
    o.headers['Content-Type'] = 'application/json';
    o.body = JSON.stringify(o.body);
  }
  const res = await fetch(appURL(path), o);
  let data = {};
  try { data = await res.json(); } catch (e) { data = { error: '响应解析失败' }; }
  if (!res.ok) {
    const error=new Error(data.error || ('HTTP ' + res.status));
    error.data=data;error.status=res.status;
    throw error;
  }
  return data;
}

function toast(msg, err) {
  const t = $('#toast');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  t.style.display = 'block';
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.style.display = 'none'; }, err ? 4200 : 2400);
}

// ---------------------------------------------------------------- 路由
function go(hash) {
  if (location.hash === hash) render();
  else location.hash = hash;
}

function parseRoute() {
  const raw = (location.hash || '#/home').replace(/^#\/?/, '');
  const [name, id] = raw.split('/');
  return { name: name || 'home', id: id || '' };
}

// ---------------------------------------------------------------- 登录
async function loadBootstrap() {
  const d = await api('/api/bootstrap');
  S.users = d.users;
  S.plans = d.plans;
  S.notes = d.notifications;
  S.audits = d.audits;
}

async function boot() {
  // The steel extension is loaded after this script. A deep link must wait
  // for every page script before dispatching to its view.
  if (document.readyState === 'loading') await new Promise((resolve) => document.addEventListener('DOMContentLoaded',resolve,{once:true}));
  try {
    const saved = localStorage.getItem('qc.capture');
    if (saved) S.capture = Object.assign(S.capture, JSON.parse(saved));
  } catch (e) { /* ignore */ }
  try {
    const m = await api('/api/me');
    S.me = m.user;
    S.asr = m.asr;
    S.managementEnabled = !!(m.capabilities && m.capabilities.management_api >= 1);
    if (S.asr && S.asr.supports_grammar === false) S.capture.asrMode = 'dictation';
    if (S.me) await loadBootstrap();
  } catch (e) { S.me = null; }
  await render();
}

// ---------------------------------------------------------------- 渲染入口
let renderRequest = 0;
async function render() {
  const requestId = ++renderRequest;
  const r = parseRoute();
  if(typeof steelCollectForm==='function')steelCollectForm();
  if (r.name === 'steel' && S.me) document.body.classList.add('steel-active');
  else document.body.classList.remove('steel-active');
  document.body.classList.toggle('management-active', !!S.me && r.name.startsWith('management'));
  const bar = $('#topbar');
  if (S.me) {
    bar.style.display = 'flex';
    bar.innerHTML = appHTML(topbarHTML());
  } else {
    bar.style.display = 'none';
  }
  const dock = $('#dock');
  let view;
  try {
    view = await VIEWS[r.name](r.id);
  } catch (e) {
    view = { html: '<div class="wrap"><div class="card"><h3>页面出错了</h3><div class="hint">' + esc(e.message) + '</div></div></div>' };
  }
  if (requestId !== renderRequest) return;
  $('#app').innerHTML = appHTML(view.html);
  if (view.dock) { dock.innerHTML = appHTML(view.dock); dock.style.display = 'block'; document.body.classList.add('with-dock'); }
  else { dock.innerHTML = ''; dock.style.display = 'none'; document.body.classList.remove('with-dock'); }
  if (S.modal) renderModal();
  if (typeof steelSyncViewport === 'function') steelSyncViewport();
  if (typeof managementAfterRender === 'function') managementAfterRender();
}

function topbarHTML() {
  const u = S.me;
  const unread = (S.notes || []).filter((n) => !n.read).length;
  const roleName = { inspector: '质量工程师', supervisor: '室主任', process: '工艺', admin: '部长' }[u.role] || u.role;
  return '<div class="logo"><b>卫华 · 质检采集</b><span>钢板进货检验 · 本地离线</span></div>' +
    '<div class="spacer"></div>' +
    (S.managementEnabled !== false && ['admin','supervisor'].includes(u.role) ? '<a class="btn ghost sm" href="#/management">管理工作台</a>' : '') +
    '<a class="btn ghost sm" href="#/steel">钢板检验</a>' +
    '<button class="iconbtn" data-act="notify">消息' + (unread ? ' ' + unread : '') + '</button>' +
    '<div class="userchip" data-act="whoami"><span class="dot"></span>' + esc(u.name) + ' · ' + roleName + '</div>';
}

// ---------------------------------------------------------------- 视图集合
const VIEWS = {};

VIEWS.home = async function () {
  if (!S.me) { go('#/login'); return { html: '' }; }
  await loadBootstrap();
  const u = S.me;
  const isSup = u.role === 'supervisor';
  const isProc = u.role === 'process';
  const cards = S.plans.map((p) => {
    const prod = p.product;
    const pct = Math.round((p.passed / p.total) * 100);
    return '<div class="card" data-act="openplan" data-id="' + p.plan_id + '" style="cursor:pointer">' +
      '<div class="prod"><div class="thumb">桥</div><div class="grow">' +
      '<div class="name">' + esc(prod.name) + ' ' + esc(prod.model) + '</div>' +
      '<div class="meta">客户 ' + esc(prod.customer) + ' · 合同 ' + esc(prod.contract_no) + '</div>' +
      '<div class="code">工号 ' + esc(prod.code) + ' · ' + esc(p.code) + '</div>' +
      '</div><div style="text-align:right"><div style="font-size:20px;font-weight:800;color:var(--brand)">' +
      pct + '%</div><div class="hint">' + p.passed + '/' + p.total + '</div></div></div>' +
      '<div class="steps">' +
      p.steps.map((step, i) => {
        const done = step.status === 'pass', n=esc(step.name);
        return '<div class="step ' + (done ? 'done' : (step.status === 'doing' ? 'doing' : '')) + '">' +
          '<div class="bar"></div><div class="lab"><span>' + (i + 1) + ' ' + n + '</span><span>' +
          (done ? '✓' : ({doing:'进行中',fail:'超差',pending:'待核对',open:'待领取'}[step.status] || '')) + '</span></div></div>';
      }).join('') + '</div></div>';
  }).join('');

  const quick = [
    '<div class="card"><h3>常用入口</h3><div class="row wrap">',
    '<button class="btn ghost sm" data-act="audit">操作留痕</button>',
    '<button class="btn ghost sm" data-act="lab">识别实验室</button>',
    isProc ? '<button class="btn ghost sm" data-act="proc">检验表单模板</button>' : '',
    isSup ? '<button class="btn ghost sm" data-act="sup">待复核签字</button>' : '',
    u.role==='admin' ? '<a class="btn" href="#/accounts">账号与邀请</a><a class="btn ghost" href="#/work-admin">新建检验计划</a>' : '',
    '</div></div>',
  ].join('');

  const note = (S.notes || []).slice(0, 3).map((n) =>
    '<div class="row between" style="padding:6px 0;border-bottom:1px dashed var(--line)">' +
    '<div><div style="font-size:13.5px">' + esc(n.title) + '</div>' +
    '<div class="hint">' + esc(n.body || '') + '</div></div>' +
    '<div class="hint">' + esc((n.created_at || '').slice(5, 16)) + '</div></div>').join('');

  return {
    html: '<div class="wrap">' +
      '<div class="page-title">我的检验任务 <span class="sub">' + esc(u.dept) + ' · ' + esc(u.name) + '</span></div>' +
      (S.managementEnabled !== false && ['admin','supervisor'].includes(u.role) ? '<a class="card steel-entry" href="#/management"><h3>质量管理工作台</h3><p>检验统计、记录追溯与异常跟进</p><span class="btn">进入管理工作台 →</span></a>' : '') +
      (isProc ? '<a class="card steel-entry" href="#/steel-standards"><h3>钢板工艺标准与偏差审批</h3><span class="btn">管理标准</span></a>' :
      '<a class="card steel-entry" href="#/steel"><span class="steel-eyebrow">员工原表 · HW/Q-JL-02A</span><h3>钢板 / 钢卷进货检验</h3><span class="btn">进入钢板台账 →</span></a>') +
      cards + quick +
      (note ? '<div class="card"><h3>消息</h3>' + note + '</div>' : '') +
      '</div>',
  };
};

VIEWS.plan = async function (id) {
  if (!S.me) { go('#/login'); return { html: '' }; }
  const pv = await api('/api/plans/' + id);
  S.plan = pv;
  const prod = pv.product;
  const nodes = pv.nodes.map((n) => {
    const st = n.task_status;
    const opLabel = '查看工序';
    return '<div class="card" style="margin-bottom:10px">' +
      '<div class="row between"><div class="row" style="gap:8px">' +
      '<div style="width:26px;height:26px;border-radius:50%;background:var(--brand-soft);color:var(--brand);display:flex;align-items:center;justify-content:center;font-weight:700">' +
      n.seq + '</div><div><div style="font-weight:700">' + esc(n.name) + '</div>' +
      '<div class="hint">' + esc(n.template_code) + ' ' + esc(n.template_revision) +
      (n.round ? ' · 第 ' + n.round + ' 次记录' : '') + '</div></div></div>' +
      STATUS_PILL[st] + '</div>' +
      '<div class="row between" style="margin-top:10px">' +
      '<div class="hint">' + (n.assignee ? '领取人 ' + esc(n.assignee) : '尚未领取') + (n.blocked_by.length ? ' · 待前置工序 '+esc(n.blocked_by.join('、'))+' 合格' : '') + '</div>' +
      '<div class="row" style="gap:6px">' +
      (S.me.role === 'admin' && st === 'open'
        ? '<button class="btn ghost sm" data-act="sync" data-id="' + n.task_id + '">升级模板</button>' : '') +
      '<button class="btn sm ' + (st === 'pass' ? 'ghost' : '') + '" data-act="opentask" data-id="' + n.task_id + '">' +
      opLabel + '</button></div></div></div>';
  }).join('');

  const rep = pv.report;
  const isSup = S.me.role === 'supervisor';
  let foot = '';
  if (rep) {
    foot = '<div class="row" style="gap:8px"><button class="btn ghost grow" data-act="preview" data-id="' + rep.id +
      '">预览排版</button><a class="btn grow" href="/api/reports/' + rep.id + '/pdf" target="_blank">下载 PDF</a></div>';
  } else if (pv.can_sign) {
    foot = isSup
      ? '<button class="btn warn block" data-act="sign" data-id="' + pv.plan.id + '">室主任复核签字</button>'
      : '<div class="hint">三张表单已全部合格，等待室主任复核对签字后生成正式记录。</div>';
  } else if (pv.can_submit) {
    foot = '<button class="btn ok block" data-act="genreport" data-id="' + pv.plan.id + '">生成检验记录（PDF）</button>';
  } else {
    foot = '<button class="btn block" disabled>还有工序未完成，暂不能生成记录</button>';
  }

  const signs = pv.signoffs.map((s) =>
    '<div class="row between" style="padding:6px 0"><div>复核签字 · ' + esc(s.name) + '</div><div class="hint">' +
    esc((s.at || '').slice(5, 16)) + '</div></div>').join('');

  return {
    html: '<div class="wrap">' +
      (S.me.role==='admin' ? '<a class="btn ghost sm" href="#/work-admin/'+pv.plan.id+'">配置班组与前置工序</a>' : '') + '<div class="page-title">检验计划 <span class="sub">' + esc(pv.plan.code) + '</span></div>' +
      '<div class="card"><div class="prod"><div class="thumb">桥</div><div class="grow">' +
      '<div class="name">' + esc(prod.name) + ' ' + esc(prod.model) + '</div>' +
      '<div class="meta">' + esc(prod.customer) + ' · 合同 ' + esc(prod.contract_no) + '</div>' +
      '<div class="code">工号 ' + esc(prod.code) + '</div></div>' +
      '<div style="text-align:right"><b style="font-size:20px;color:var(--brand)">' + pv.progress.passed + '/' +
      pv.progress.total + '</b><div class="hint">工序完成</div></div></div>' +
      '<div class="hint" style="margin-top:10px">标准依据：' + esc(pv.plan.std_ref) + '</div></div>' +
      '<div class="section-title">按作业顺序逐张填写（一张表单 = 一个工序节点）</div>' +
      nodes +
      '<div class="card" style="margin-top:10px"><h3>齐套与归档</h3>' +
      (signs || '<div class="hint">三张表单全部合格后，由室主任复核签字，再生成正式检验记录。</div>') +
      '<div style="margin-top:10px">' + foot + '</div></div>' +
      '<div class="row" style="margin-top:6px"><button class="btn ghost sm grow" data-act="audit">查看留痕</button>' +
      '<button class="btn ghost sm grow" data-act="home">返回任务列表</button></div>' +
      '</div>',
  };
};

// ---------------------------------------------------------------- 任务录入
VIEWS.task = async function (id) {
  if (!S.me) { go('#/login'); return { html: '' }; }
  if (!S.task || String(S.task.id) !== String(id)) {
    const d = await api('/api/tasks/' + id);
    S.task = d.task;
    S.plan = d.plan;
    S.values = {};
    S.confirmed = {};
    S.judge = null;
    S.transcript = '';
    S.audioId = null;
    S.round = d.task.round || 0;
    const latest = d.task.latest;
    if (latest) {
      (d.task.template.schema.sections || []).forEach((sec) => (sec.fields || []).forEach((f) => {
        const v = (latest.values || {})[f.key];
        if (v) S.values[f.key] = Object.assign({}, v);
      }));
      if (d.task.status === 'pass') S.judge = latest.judge;   // 已合格：只读回看
      else S.judge = null;
    } else {
      S.judge = null;
    }
  }
  const t = S.task;
  const tpl = t.template;
  const schema = tpl.schema;
  const fields = [];
  (schema.sections || []).forEach((sec) => (sec.fields || []).forEach((f) => fields.push(Object.assign({ section: sec.name }, f))));
  (schema.rules || []).forEach((r) => fields.push(Object.assign({ section: '计算结果' }, r, { derived: true })));
  if (!S.curKey) {
    const firstOpen = fields.find((f) => !f.derived && S.values[f.key] === undefined);
    S.curKey = (firstOpen || fields[0]).key;
  }

  let lastSection = '';
  const body = fields.map((f) => {
    let html = '';
    if (f.section !== lastSection) {
      html += '<div class="section-title">' + esc(f.section) + '</div>';
      lastSection = f.section;
    }
    html += fieldHTML(f);
    return html;
  }).join('');

  const roundBadge = S.round > 0
    ? '<div class="card" style="background:var(--warn-soft);box-shadow:none;border:1px solid #f0cf9d">' +
      '<b style="color:var(--warn)">本工序已有 ' + S.round + ' 次记录</b>' +
      '<div class="hint">再次提交将作为第 ' + (S.round + 1) + ' 次复测追加，历史记录不会被覆盖（可在留痕里回看）。</div></div>'
    : '';

  const judgeBlock = S.judge ? judgeHTML(S.judge) : '';

  let actions;
  if (S.judge) {
    if (S.judge.overall === 'pending') {
      actions = '<button class="btn sm grow" data-act="retest">返回确认</button>';
    } else if (S.judge.overall === 'fail') {
      actions = '<button class="btn sm grow bad" data-act="retest">发起复测</button>';
    } else {
      actions = '<button class="btn sm grow ok" data-act="openplan" data-id="' + S.plan.plan.id + '">回到检验计划</button>';
    }
  } else {
    actions = '';
  }

  const canEdit = t.assignee_id===S.me.id && S.me.role==='inspector' && t.status!=='pass' && !t.blocked_by.length;
  const assignment = taskAssignmentHTML(t);
  if (!canEdit) actions='';
  const dock = !canEdit || S.judge ? '' : dockHTML(tpl);
  return {
    html: '<div class="wrap">' +
      '<div class="page-title">' + esc(tpl.title) + ' <span class="sub">' + esc(tpl.code) + ' ' + esc(tpl.revision) + '</span></div>' +
      '<div class="card" style="padding:12px 14px">' +
      '<div class="row between"><div><b>' + esc(S.plan.product.name) + ' ' + esc(S.plan.product.model) + '</b>' +
      '<div class="hint">工号 ' + esc(S.plan.product.code) + ' · 第 ' + t.seq + ' 道工序</div></div>' +
      '<div class="pill na">' + esc(tpl.std_version) + '</div></div>' +
      '<div class="hint" style="margin-top:8px">规格依据：' + esc(tpl.spec_ref) + '</div></div>' +
      assignment + roundBadge + judgeBlock + body +
      '<div class="row" style="margin-top:6px"><button class="btn ghost sm grow" data-act="openplan" data-id="' + S.plan.plan.id +
      '">返回计划</button>' + actions + '</div>' +
      '<div style="height:8px"></div></div>',
    dock: dock,
  };
};

function fieldHTML(f) {
  const key = f.key;
  const v = S.values[key];
  const cls = ['field'];
  if (S.curKey === key) cls.push('cur');
  if (f.derived) {
    const val = deriveValue(f);
    return '<div class="field" data-key="' + key + '">' +
      '<div class="fhead"><div class="flabel">' + esc(f.label) + '</div>' +
      '<div class="fspec">' + esc(specText(f)) + '</div></div>' +
      '<div class="fmain"><div class="grow derived">' + (val == null ? '提交后自动计算' : val + ' ' + (f.unit || '')) + '</div>' +
      '<span class="pill na">系统计算</span></div></div>';
  }
  let note = '';
  if (v && v.confidence < 0.75 && !S.confirmed[key]) {
    cls.push('low');
    const score = v.confidence > 0 ? ('识别评分 ' + Number(v.confidence).toFixed(2)) : '无可靠识别评分，需人工核对';
    note = '<div class="fnote"><span>' + score + ' · 原文「' + esc(v.raw || '') +
      '」' + (v.note ? ' · ' + esc(v.note) : '') + '</span><span class="act" data-act="confirm" data-key="' + key + '">确认无误</span></div>';
  } else if (v && v.raw) {
    note = '<div class="fnote">来自口报：' + esc(v.raw) + '</div>';
  }
  let input;
  const dis = S.judge || S.task.assignee_id!==S.me.id || S.me.role!=='inspector' || S.task.status==='pass' || S.task.blocked_by.length ? ' disabled' : '';
  if (f.type === 'enum') {
    const choices = (f.options || []).slice();
    if (v && v.value && !choices.includes(v.value)) choices.push(v.value);
    const opts = ['<option value="">请选择</option>'].concat(
      choices.map((o) => '<option ' + (v && v.value === o ? 'selected' : '') + '>' + esc(o) + '</option>'));
    input = '<select class="val" data-key="' + key + '"' + dis + '>' + opts.join('') + '</select>';
  } else if (f.type === 'text') {
    input = '<input class="val" style="font-size:16px" data-key="' + key + '" value="' +
      esc(v && v.value != null ? v.value : '') + '" placeholder="' + esc(f.hint || '') + '"' + dis + '>';
  } else {
    input = '<input class="val" inputmode="decimal" data-key="' + key + '" value="' +
      esc(v && v.value != null ? v.value : '') + '" placeholder="口报或手填"' + dis + '>';
  }
  return '<div class="' + cls.join(' ') + '" data-key="' + key + '">' +
    '<div class="fhead"><div class="flabel">' + esc(f.label) + '</div>' +
    '<div class="fspec">' + esc(specText(f)) + '</div></div>' +
    '<div class="fmain">' + input + (f.unit ? '<div class="unit">' + esc(f.unit) + '</div>' : '') + '</div>' +
    note + '</div>';
}

function specText(f) {
  if (f.spec) return f.spec;
  if (f.type === 'enum') {
    return f.pass && f.pass.length ? '合格项：' + f.pass.join('、') : (f.options || []).join(' / ');
  }
  if (f.type === 'text') return f.hint || '必填';
  const u = f.unit || '';
  if (f.nominal != null && f.tol != null) return f.nominal + ' ± ' + f.tol + ' ' + u;
  if (f.min != null && f.max != null) return f.min + ' ~ ' + f.max + ' ' + u;
  if (f.min != null) return '≥ ' + f.min + ' ' + u;
  if (f.max != null) return '≤ ' + f.max + ' ' + u;
  return u;
}

function deriveValue(f) {
  if (!f.expr) return null;
  try {
    const env = {};
    Object.keys(S.values).forEach((k) => {
      const n = parseFloat(S.values[k].value);
      if (!isNaN(n)) env[k] = n;
    });
    // 仅用于界面即时显示，最终判定以服务端为准
    const pre = 'const abs=Math.abs,min=Math.min,max=Math.max,round=Math.round,sqrt=Math.sqrt,avg=(...a)=>a.reduce((x,y)=>x+y,0)/a.length;';
    const fn = new Function(...Object.keys(env), pre + ' return (' + f.expr + ');');
    const v = fn(...Object.values(env));
    return (typeof v === 'number' && !isNaN(v)) ? Math.round(v * 100) / 100 : null;
  } catch (e) { return null; }
}

function dockHTML(tpl) {
  const ex = EXAMPLES[tpl.code] || [];
  const eng = (S.asr && S.asr.available);
  const info = S.lastAsr && S.lastAsr.metrics
    ? '<div class="saytext"><span>' + esc(fmtMetrics(S.lastAsr.metrics)) + '</span><span>' +
      esc(S.lastAsr.text || '（无文本）') + '</span></div>'
    : '';
  return '<div class="dock"><div class="inner">' +
    '<div class="say">' +
    '<textarea id="saybox" placeholder="按质检习惯口报，例如：跨度 22501，上拱度 26.5">' +
    esc(S.dockText) + '</textarea>' +
    '<button class="mic' + (S.recording ? ' rec' : '') + '" data-act="mic" title="点开始 / 再点结束">' +
    (S.recording ? '■' : '🎤') + '</button>' +
    '<button class="btn sm" data-act="parse">解析</button>' +
    '</div>' +
    '<div class="examples">' + ex.map((t) =>
      '<span class="chip" data-act="example" data-text="' + esc(t) + '">' + esc(t.length > 22 ? t.slice(0, 22) + '…' : t) +
      '</span>').join('') + '</div>' +
    '<div class="saytext"><span>' +
    (eng ? '本地识别引擎就绪（' + esc(S.asr.engine) + '）· 说一项点一次'
      : '识别引擎未就绪：' + esc((S.asr && S.asr.reason) || '未接入') + '（可用示例按钮演示）') +
    '</span><span class="row" style="gap:10px">' +
    '<span data-act="capture" style="color:var(--brand);font-weight:700;cursor:pointer">' +
    (S.capture.mode === 'raw' ? '原声' : '降噪') + (S.capture.denoise ? '+服务端' : '') + '</span>' +
    '<span data-act="togglesim" style="color:var(--brand);font-weight:700;cursor:pointer">模拟：' +
    (S.simulate ? '开' : '关') + '</span></span></div>' +
    info +
    '<div style="height:8px"></div>' +
    '<button class="btn block" data-act="submit">提交并判定</button>' +
    '</div></div>';
}

function judgeHTML(j) {
  const v = VERDICT[j.overall] || VERDICT.pending;
  const rows = j.rows.map((r) => {
    const cls = r.status === 'over' ? 'bad' : (r.status === 'pending' || r.status === 'missing' ? 'pend' : '');
    const pillCls = r.status === 'ok' ? 'ok' : (r.status === 'over' ? 'bad' : 'warn');
    const label = { ok: '合格', over: '超差', pending: '待确认', missing: '缺项', na: '—' }[r.status] || r.status;
    return '<div class="jrow ' + cls + '"><div class="jl"><b>' + esc(r.label) + '</b>' +
      '<div class="s">' + esc(r.spec) + (r.derived ? ' · 系统计算' : '') + '</div>' +
      (r.note ? '<div class="s" style="color:var(--bad)">' + esc(r.note) + '</div>' : '') + '</div>' +
      '<div class="jr"><div class="v">' + esc(r.value_text) + ' ' + esc(r.unit || '') + '</div>' +
      '<span class="pill ' + pillCls + '">' + label + '</span></div></div>';
  }).join('');
  return '<div class="verdict ' + v.cls + '"><div class="big">判定：' + v.text + '</div>' +
    '<div class="small">' + v.sub + '</div>' +
    '<div class="small" style="margin-top:6px">模板 ' + esc(j.template.code) + ' ' + esc(j.template.revision) +
    ' · 快照 ' + esc(j.snapshot) + '</div></div>' +
    '<div class="card"><h3>判定明细</h3>' + rows +
    '<div class="hint" style="margin-top:10px">合格 ' + j.counts.ok + ' · 超差 ' + j.counts.over +
    ' · 待确认 ' + j.counts.pending + ' · 缺项 ' + j.counts.missing + '</div></div>';
}

// ---------------------------------------------------------------- 模板编辑
VIEWS.proc = async function () {
  const d = await api('/api/templates');
  const rows = d.templates.map((t) =>
    '<div class="card" style="margin-bottom:10px" data-act="edittpl" data-id="' + t.id + '" >' +
    '<div class="row between"><div><b>' + esc(t.title) + '</b><div class="hint">' + esc(t.code) + ' ' +
    esc(t.revision) + ' · ' + esc(t.std_version) + '</div></div>' +
    '<div>' + (t.active ? '<span class="pill ok">当前版本</span>' : '<span class="pill na">历史版本</span>') + '</div></div></div>').join('');
  return {
    html: '<div class="wrap wide"><div class="page-title">检验表单模板 <span class="sub">改公差即改规则，无需改代码</span></div>' +
      rows + '<button class="btn ghost block" data-act="home">返回</button></div>',
  };
};

VIEWS.edittpl = async function (id) {
  const d = await api('/api/templates/' + id);
  const t = d.template;
  const schema = t.schema;
  let html = '<div class="wrap wide"><div class="page-title">' + esc(t.title) +
    ' <span class="sub">' + esc(t.code) + ' ' + esc(t.revision) + '</span></div>' +
    '<div class="card"><h3>基本信息</h3>' +
    '<div class="row" style="margin-bottom:8px"><div style="width:88px" class="hint">表单标题</div>' +
    '<input id="tpl-title" class="grow" style="padding:8px;border:1px solid var(--line);border-radius:9px" value="' + esc(t.title) + '"></div>' +
    '<div class="row" style="margin-bottom:8px"><div style="width:88px" class="hint">标准依据</div>' +
    '<input id="tpl-std" class="grow" style="padding:8px;border:1px solid var(--line);border-radius:9px" value="' + esc(t.std_version) + '"></div>' +
    '<div class="hint">修改下方公差后点「发布新版本」，会生成新版本号；进行中的任务仍按旧版本快照判定。</div></div>';

  (schema.sections || []).forEach((sec, si) => {
    html += '<div class="card"><h3>' + esc(sec.name) + '</h3><table class="tbl"><tr><th>检验项目</th><th>下限</th><th>上限</th><th>公差±</th><th>单位</th></tr>';
    (sec.fields || []).forEach((f, fi) => {
      const num = f.type !== 'enum' && f.type !== 'text';
      html += '<tr><td>' + esc(f.label) + '<div class="hint">' + esc(f.key) + '</div></td>' +
        (num ? ['min', 'max', 'tol'].map((k) =>
          '<td><input data-sec="' + si + '" data-fld="' + fi + '" data-prop="' + k + '" value="' +
          (f[k] == null ? '' : f[k]) + '" style="width:74px;padding:5px;border:1px solid var(--line);border-radius:7px"></td>').join('')
          : '<td colspan="3" class="hint">' + (f.type === 'enum' ? '枚举：' + (f.options || []).join(' / ') : '文本') + '</td>') +
        '<td class="hint">' + esc(f.unit || '') + '</td></tr>';
    });
    html += '</table></div>';
  });

  html += '<div class="card"><h3>发布</h3>' +
    '<button class="btn block" data-act="publish" data-id="' + t.id + '">保存并发布新版本</button>' +
    '<div class="hint" style="margin-top:8px">发布后可在任务页用「升级到最新模板」让进行中的工序切换版本（会写入留痕）。</div></div>' +
    '<button class="btn ghost block" data-act="proc">返回模板列表</button></div>';
  return { html: html };
};

// ---------------------------------------------------------------- 留痕 / 通知 / 报告
VIEWS.audit = async function () {
  const d = await api('/api/audit');
  const items = d.audits.map((a) => {
    let detail = a.detail || '';
    try { const o = JSON.parse(a.detail || '{}'); detail = Object.keys(o).length ? JSON.stringify(o) : ''; } catch (e) { /* keep */ }
    return '<div class="it"><div class="t">' + esc(a.at) + ' · ' + esc(a.actor_name) + '</div>' +
      '<div class="m"><b>' + esc(actName(a.action)) + '</b> ' + esc(a.target || '') +
      (detail ? '<div class="hint">' + esc(detail) + '</div>' : '') + '</div></div>';
  }).join('');
  return {
    html: '<div class="wrap"><div class="page-title">操作留痕 <span class="sub">谁在什么时候做了什么</span></div>' +
      '<div class="card"><div class="tl">' + (items || '<div class="hint">暂无记录</div>') + '</div></div>' +
      '<button class="btn ghost block" data-act="home">返回</button></div>',
  };
};

function actName(a) {
  return ({
    login: '登录', claim_task: '领取任务', submit_record: '提交判定', sign_plan: '复核签字',
    steel_create: '新建钢板进货检验', steel_save: '保存进货检验草稿', steel_finalize: '确认进货检验',
    steel_export: '导出进货检验记录', steel_asr: '钢板检验口报录入',
    steel_followup: '新建后续检验表单', steel_followup_reuse: '继续后续检验表单',
    generate_report: '生成检验记录', publish_template: '发布模板新版本', sync_template: '升级任务模板',
  }[a] || a);
}

VIEWS.sup = async function () {
  const list = [];
  for (const p of S.plans) {
    const pv = await api('/api/plans/' + p.plan_id);
    if (pv.can_sign || pv.report) list.push(pv);
  }
  const cards = list.map((pv) =>
    '<div class="card"><div class="row between"><div><b>' + esc(pv.product.name) + ' ' + esc(pv.product.model) + '</b>' +
    '<div class="hint">' + esc(pv.product.code) + ' · ' + esc(pv.plan.code) + '</div></div>' +
    '<div>' + (pv.report ? '<span class="pill ok">已归档</span>' : (pv.can_sign ? '<span class="pill warn">待签字</span>' : '<span class="pill na">进行中</span>')) + '</div></div>' +
    '<div class="row" style="margin-top:10px"><button class="btn ghost sm grow" data-act="openplan" data-id="' + pv.plan.id + '">查看计划</button>' +
    (pv.can_sign ? '<button class="btn warn sm grow" data-act="sign" data-id="' + pv.plan.id + '">复核签字</button>' : '') +
    '</div></div>').join('');
  return {
    html: '<div class="wrap"><div class="page-title">待复核签字</div>' +
      (cards || '<div class="card hint">暂无待办</div>') +
      '<button class="btn ghost block" data-act="home">返回</button></div>',
  };
};

// ---------------------------------------------------------------- 弹层
function openModal(html) { S.modal = html; renderModal(); }
function closeModal() { S.modal = null; $('#modal').innerHTML = ''; }
function renderModal() {
  $('#modal').innerHTML = appHTML('<div class="sheet-mask" data-act="closemodal"><div class="sheet">' +
    '<div class="grab"></div>' + S.modal + '</div></div>');
}

function reportModal(no, rid, pages) {
  return '<h3>已生成 ' + esc(no) + '</h3>' +
    '<div class="hint">共 ' + (pages || 1) + ' 页 · 已按技术协议要求的版式排版，含检验结论与签字栏</div>' +
    '<div style="height:10px"></div>' +
    '<div class="imgframe"><img src="/api/reports/' + rid + '/preview.png" alt="report"></div>' +
    '<div style="height:12px"></div>' +
    '<div class="row" style="gap:8px"><button class="btn ghost grow" data-act="closemodal">关闭</button>' +
    '<a class="btn grow" href="/api/reports/' + rid + '/pdf" target="_blank">打开 PDF</a></div>';
}

function openCapture() {
  const m = S.capture.mode;
  const asr = S.capture.asrMode || 'grammar';
  const supportsGrammar = !S.asr || S.asr.supports_grammar !== false;
  const a = S.applied || {};
  const applied = S.applied
    ? 'NS=' + (a.noiseSuppression === undefined ? '未知' : a.noiseSuppression) +
      ' · AGC=' + (a.autoGainControl === undefined ? '未知' : a.autoGainControl) +
      ' · AEC=' + (a.echoCancellation === undefined ? '未知' : a.echoCancellation) +
      ' · ' + (a.sampleRate || '?') + ' Hz'
    : '还没录过';
  openModal('<h3>语音采集设置</h3>' +
    '<div class="hint">用相同口报多次对照采集配置，在「识别实验室」比较字段命中率；音频指标只作辅助。</div>' +
    '<div style="height:10px"></div>' +
    '<div class="hint" style="font-weight:700;color:var(--brand)">识别模式</div>' +
    '<div class="row" style="gap:8px;margin:6px 0 8px">' +
    (supportsGrammar ? '<button class="btn ' + (asr === 'grammar' ? '' : 'ghost') + ' grow" data-act="setasr" data-mode="grammar">语法约束</button>' : '') +
    '<button class="btn ' + (asr === 'dictation' ? '' : 'ghost') + ' grow" data-act="setasr" data-mode="dictation">自由听写</button>' +
    '</div>' +
    '<div class="hint" style="margin-bottom:12px">' +
    (supportsGrammar ? '语法约束限制在本表单的标签、数字和枚举项内；是否改善识别需用真实录音对照。' :
      '普通话离线听写：先选字段，再说读数与单位；识别结果核对后确认。') + '</div>' +
    '<div class="hint" style="font-weight:700;color:var(--brand)">采集方式</div>' +
    '<div class="row" style="gap:8px;margin:6px 0 10px">' +
    '<button class="btn ' + (m === 'system' ? '' : 'ghost') + ' grow" data-act="setmode" data-mode="system">系统降噪</button>' +
    '<button class="btn ' + (m === 'raw' ? '' : 'ghost') + ' grow" data-act="setmode" data-mode="raw">原声（不处理）</button>' +
    '</div>' +
    '<button class="btn ' + (S.capture.denoise ? 'ok' : 'ghost') + ' block" data-act="setdenoise">' +
    '服务端降噪支路：' + (S.capture.denoise ? '已开启' : '已关闭') + '</button>' +
    '<div class="hint" style="margin-top:12px">上次录音实际生效：' + esc(applied) + '</div>' +
    '<div class="hint" style="margin-top:6px">浏览器可能忽略部分参数，所以这里显示的是“实际生效值”，不是我们请求的值。</div>' +
    '<div style="height:12px"></div>' +
    '<button class="btn ghost block" data-act="closemodal">关闭</button>');
}

// ---------------------------------------------------------------- 事件
document.addEventListener('click', async (e) => {
  const t = e.target.closest('[data-act]');
  if (!t) return;
  if (t.classList.contains('sheet-mask') && e.target !== t) return;
  const act = t.dataset.act;
  try {
    if (act === 'whoami') {
      openModal('<h3>当前身份</h3><div class="hint">' + esc(S.me.name) + ' · ' + esc(S.me.dept) +
        '<br>工号 ' + esc(S.me.emp_no) + '</div><div style="height:12px"></div>' +
        '<button class="btn" data-act="logout">退出登录</button>');
    } else if (act === 'logout') {
      await api('/api/logout', {method:'POST',body:{}});
      location.replace(location.pathname + location.search + '#/login');
      location.reload();
    } else if (act === 'home') {
      S.task = null; S.judge = null; S.values = {}; S.confirmed = {}; S.curKey = null;
      go('#/home'); await render();
    } else if (act === 'openplan') {
      go('#/plan/' + t.dataset.id); await render();
    } else if (act === 'opentask') {
      S.task = null; S.judge = null; S.values = {}; S.confirmed = {}; S.curKey = null;
      go('#/task/' + t.dataset.id); await render();
    } else if (act === 'confirm') {
      const key = t.dataset.key;
      S.confirmed[key] = true;
      if (S.values[key]) S.values[key].confidence = 1;
      await render();
    } else if (act === 'example') {
      S.dockText = t.dataset.text;
      const box = $('#saybox');
      if (box) box.value = S.dockText;
      await doParse();
    } else if (act === 'parse') {
      await doParse();
    } else if (act === 'mic') {
      if (S.recording) await finishRec();
      else if (t.dataset.sid) {
        await startRec({ url: '/api/asr/lab/sample', extra: { script_id: t.dataset.sid },
          onDone: async (d) => { await labApply(t.dataset.sid, d); } });
      } else {
        await startRec({ url: '/api/asr/audio', extra: { task_id: S.task.id } });
      }
    } else if (act === 'capture') {
      openCapture();
    } else if (act === 'setasr') {
      S.capture.asrMode = t.dataset.mode;
      try { localStorage.setItem('qc.capture', JSON.stringify(S.capture)); } catch (e) { /* ignore */ }
      try {
        const r = await api('/api/asr/mode', {
          method: 'POST',
          body: { mode: S.capture.asrMode, task_id: S.task ? S.task.id : null },
        });
        if (S.capture.asrMode === 'grammar' && r && r.ok === false) {
          toast('语法加载失败，已回落听写：' + (r.error || ''), true);
        }
      } catch (e) { /* 引擎不可用时忽略 */ }
      closeModal();
      await render();
      if (S.capture.asrMode === 'grammar') toast('识别模式：语法约束');
      else toast('识别模式：自由听写');
    } else if (act === 'setmode') {
      S.capture.mode = t.dataset.mode;
      try { localStorage.setItem('qc.capture', JSON.stringify(S.capture)); } catch (e) { /* ignore */ }
      closeModal();
      await render();
      toast('采集模式：' + (S.capture.mode === 'raw' ? '原声（关闭系统降噪）' : '系统降噪'));
    } else if (act === 'setdenoise') {
      S.capture.denoise = !S.capture.denoise;
      try { localStorage.setItem('qc.capture', JSON.stringify(S.capture)); } catch (e) { /* ignore */ }
      closeModal();
      await render();
      toast('服务端降噪支路：' + (S.capture.denoise ? '开' : '关'));
    } else if (act === 'lab') {
      go('#/lab'); await render();
    } else if (act === 'labreset') {
      await api('/api/asr/lab/reset', { method: 'POST' });
      S.labResults = {};
      await render();
      toast('样本已清空');
    } else if (act === 'probe') {
      toast('重新检测引擎…');
      const info = await api('/api/asr/probe', { method: 'POST' });
      S.asr = info;
      if (S.asr && S.asr.supports_grammar === false) S.capture.asrMode = 'dictation';
      await render();
      if (info.available) toast('引擎就绪：' + info.engine);
      else toast((info.advice || info.reason || '仍不可用'), true);
    } else if (act === 'togglesim') {
      S.simulate = !S.simulate;
      await render();
    } else if (act === 'submit') {
      await doSubmit();
    } else if (act === 'retest') {
      S.judge = null; S.confirmed = {}; S.audioId = null; S.transcript = '';
      await render();
      const r2 = $('#saybox');
      if (r2) { r2.value = ''; r2.focus(); }
      toast('可重新口报，提交后作为第 ' + (S.round + 1) + ' 次记录追加');
    } else if (act === 'sync') {
      const d = await api('/api/tasks/' + t.dataset.id + '/sync-template', { method: 'POST' });
      toast(d.message);
      S.task = null; S.judge = null; S.values = {}; S.confirmed = {}; S.curKey = null;
      await render();
    } else if (act === 'genall') {
      const ex = (EXAMPLES[S.task.template.code] || [])[0];
      if (ex) { S.dockText = ex; await doParse(); }
    } else if (act === 'sign') {
      await api('/api/plans/' + t.dataset.id + '/sign', { method: 'POST', body: { note: '现场记录齐套' } });
      toast('复核签字完成');
      go('#/plan/' + t.dataset.id); await render();
    } else if (act === 'genreport') {
      const r = await api('/api/plans/' + t.dataset.id + '/submit', { method: 'POST' });
      toast('已生成 ' + r.report_no + '（' + r.pages + ' 页）');
      go('#/plan/' + t.dataset.id);
      await render();
      S.modal = reportModal(r.report_no, r.report_id, r.pages);
      renderModal();
    } else if (act === 'preview') {
      openModal('<h3>报告排版预览</h3><div class="imgframe"><img src="/api/reports/' + t.dataset.id +
        '/preview.png" alt="preview"></div><div style="height:12px"></div>' +
        '<a class="btn block" href="/api/reports/' + t.dataset.id + '/pdf" target="_blank">下载 PDF</a>');
    } else if (act === 'closemodal') {
      closeModal();
    } else if (act === 'notify') {
      await openNotify();
    } else if (act === 'audit') {
      go('#/audit'); await render();
    } else if (act === 'proc') {
      go('#/proc'); await render();
    } else if (act === 'sup') {
      go('#/sup'); await render();
    } else if (act === 'edittpl') {
      go('#/edittpl/' + t.dataset.id); await render();
    } else if (act === 'publish') {
      await doPublish(t.dataset.id);
    }
  } catch (err) {
    toast(err.message || '操作失败', true);
  }
});

// 输入框实时更新（不重渲染，避免丢焦点）
document.addEventListener('input', (e) => {
  const el = e.target;
  if (el.classList.contains('val') && el.dataset.key) {
    const key = el.dataset.key;
    const raw = el.value;
    const isNum = el.tagName.toLowerCase() !== 'select';
    S.values[key] = {
      value: isNum ? (raw === '' ? null : (isNaN(parseFloat(raw)) ? raw : parseFloat(raw))) : raw,
      confidence: 1, source: 'manual', raw: '',
    };
    S.curKey = key;
    const box = el.closest('.field');
    if (box) {
      $$('.field').forEach((f) => f.classList.remove('cur'));
      box.classList.add('cur');
    }
  }
  if (el.id === 'saybox') S.dockText = el.value;
});

// 下拉框（枚举字段）用 change 更稳
document.addEventListener('change', (e) => {
  const el = e.target;
  if (el.tagName === 'SELECT' && el.dataset.key) {
    S.values[el.dataset.key] = { value: el.value, confidence: 1, source: 'manual', raw: '' };
    S.curKey = el.dataset.key;
    const box = el.closest('.field');
    if (box) {
      $$('.field').forEach((f) => f.classList.remove('cur'));
      box.classList.add('cur');
    }
  }
});

// ---------------------------------------------------------------- 口报解析
async function doParse(textOverride, asrMeta) {
  const box = $('#saybox');
  const text = (typeof textOverride === 'string' ? textOverride : (box ? box.value : S.dockText || '')).trim();
  if (!text) { toast('先口报或选一条示例'); return; }
  const body = { task_id: S.task.id, text: text, current_key: S.curKey };
  if (asrMeta) {
    const score = Number(asrMeta.confidence);
    body.asr_confidence = Number.isFinite(score) ? Math.max(0, Math.min(1, score)) : 0;
    if (asrMeta.requires_confirmation) body.asr_confidence = Math.min(body.asr_confidence, 0.74);
    if (asrMeta.current_key) body.current_key = asrMeta.current_key;
  }
  const d = await api('/api/asr/parse', {
    method: 'POST',
    body: body,
  });
  let applied = 0;
  const skip = [];
  d.assignments.forEach((a) => {
    if (a.derived) { skip.push(a.label); return; }   // 计算项不接受口报
    S.values[a.key] = { value: a.value, confidence: a.confidence, source: a.source, raw: a.raw, note: a.note };
    delete S.confirmed[a.key]; // 新读数必须重新核对，不能沿用旧读数的人工确认。
    applied += 1;
  });
  S.transcript = text;
  S.engine = 'asr';
  S.dockText = text;
  if (applied === 0 && !skip.length) { toast('没有识别到可填入的数值，检查一下口报格式'); }
  else {
    const low = d.assignments.filter((a) => !a.derived && a.confidence < 0.75).length;
    let msg = '已填入 ' + applied + ' 项' + (low ? '，其中 ' + low + ' 项需确认（黄色）' : '');
    if (skip.length) msg += '；' + skip.join('、') + ' 由系统计算，已忽略口报值';
    if (d.unknown && d.unknown.length) msg += '；' + d.unknown.length + ' 段需重报';
    toast(msg);
  }
  const nextOpen = (function () {
    const all = [];
    (S.task.template.schema.sections || []).forEach((s) => (s.fields || []).forEach((f) => all.push(f)));
    const n = all.find((f) => S.values[f.key] === undefined);
    return n ? n.key : null;
  })();
  S.curKey = nextOpen;
  await render();
  const nb = $('#saybox');
  if (nb) { nb.value = ''; S.dockText = ''; }
}

// ---------------------------------------------------------------- 录音（WAV 直采，16k 单声道）
const REC = { stream:null, ctx:null, proc:null, bufs:[], sr:0, t0:0,
  starting:false, generation:0, session:null, phase:'idle' };

function micFailure(code, message) {
  const error = new Error(message || code);
  error.micCode = code;
  return error;
}
function stopMicStream(stream) {
  try { if(stream)stream.getTracks().forEach((track)=>track.stop()); } catch (_) { /* cleanup */ }
}
function releaseMicSession(session) {
  if (!session || session.closed) return;
  session.closed=true;
  session.waiters.forEach((cancel)=>cancel());
  session.waiters.clear();
  try { if(session.proc){session.proc.onaudioprocess=null;session.proc.disconnect();} } catch (_) { /* cleanup */ }
  stopMicStream(session.stream);
  // Some mobile engines leave close() unresolved too. Cleanup must never
  // hold the retry button hostage to another browser promise.
  try { if(session.ctx)Promise.resolve(session.ctx.close()).catch(()=>{}); } catch (_) { /* cleanup */ }
}
function waitForMic(promise, session, milliseconds, code) {
  return new Promise((resolve,reject)=>{
    let settled=false, timer;
    const finish=(fn,value)=>{
      if(settled)return;
      settled=true; clearTimeout(timer);session.waiters.delete(cancel);fn(value);
    };
    const cancel=()=>finish(reject,micFailure('canceled','已取消录音启动'));
    session.waiters.add(cancel);
    timer=setTimeout(()=>finish(reject,micFailure(code)),milliseconds);
    Promise.resolve(promise).then((value)=>finish(resolve,value),(error)=>finish(reject,error));
    if(session.closed)cancel();
  });
}
function micProblem(error, session) {
  const code=error.micCode || error.name || 'UnknownError';
  const messages={
    insecure:'浏览器未将当前页面识别为安全连接。请使用 HTTPS 地址，并在手机上配置信任证书后重试。',
    unsupported:'当前浏览器未开放麦克风。请使用独立的 Chrome / Edge 打开完整 HTTPS 地址。',
    NotAllowedError:'麦克风权限被阻止。请在浏览器的网站设置中允许麦克风，并在安卓应用权限中允许此浏览器使用麦克风。',
    PermissionDeniedError:'请在浏览器的网站设置和安卓应用权限中允许麦克风，然后重试。',
    NotFoundError:'没有找到可用麦克风，请检查手机的麦克风权限和音频设备。',
    NotReadableError:'麦克风暂时无法使用。请结束通话或其他录音应用后重试。',
    SecurityError:'浏览器阻止了录音，请检查 HTTPS 证书与此网站的麦克风权限。',
    'permission-timeout':'浏览器没有返回麦克风权限结果。请在地址栏的网站设置中允许麦克风，再点重试；也可用独立 Chrome / Edge 打开。',
    'audio-timeout':'浏览器未能启动录音音频。请点重试，并允许麦克风；若仍失败，请用独立 Chrome / Edge 打开。',
    'audio-suspended':'手机音频仍被浏览器暂停，请点重试启动录音。',
  };
  return {code,title:'麦克风未启动',message:messages[code] || ('录音启动失败：'+(error.message || code)),
    detail:{secure:window.isSecureContext,permission:'unknown',audio:session && session.ctx ? session.ctx.state : '未启动',error:code,browser:navigator.userAgent || ''}};
}
async function cancelRec() {
  REC.generation++;
  const session=REC.session;
  releaseMicSession(session);
  if(!session){
    stopMicStream(REC.stream);
    try{if(REC.proc){REC.proc.onaudioprocess=null;REC.proc.disconnect();}}catch(_){/* cleanup */}
    try{if(REC.ctx)Promise.resolve(REC.ctx.close()).catch(()=>{});}catch(_){/* cleanup */}
  }
  REC.session=null;REC.starting=false;REC.phase='idle';
  S.recording=false;S.recTarget=null;
  REC.stream=null;REC.ctx=null;REC.proc=null;REC.bufs=[];
}

function captureConstraints(capture) {
  const raw = (capture || S.capture).mode === 'raw';
  return {audio:{echoCancellation:!raw,noiseSuppression:!raw,autoGainControl:!raw,channelCount:1},video:false};
}

async function startRec(target) {
  if(REC.starting || S.recording)return false;
  const requested=target || {url:'/api/asr/audio'};
  const extra=Object.assign({},requested.extra || {});
  const script=extra.script_id && S.lab && (S.lab.script || []).find((item)=>item.id===extra.script_id);
  extra.current_key=script ? script.key : (extra.current_key || S.curKey || '');
  const capture=Object.assign({},S.capture);
  const recordingTarget=Object.assign({},requested,{extra,capture});
  const session={generation:REC.generation,route:location.hash,ctx:null,stream:null,proc:null,waiters:new Set(),closed:false};
  REC.session=session;REC.starting=true;REC.phase='permission';S.micProblem=null;
  const current=()=>!session.closed && REC.session===session && session.generation===REC.generation && session.route===location.hash && (!requested.isCurrent || requested.isCurrent());
  const report=(phase)=>{
    if(REC.session!==session)return;
    REC.phase=phase;
    if(requested.onState)Promise.resolve(requested.onState(phase)).catch(()=>{});
  };
  let succeeded=false;
  try {
    if(window.isSecureContext===false)throw micFailure('insecure');
    if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia)throw micFailure('unsupported');
    const Ctx=window.AudioContext || window.webkitAudioContext;
    if(!Ctx)throw micFailure('unsupported','浏览器不支持录音音频');
    // Both browser requests must begin in the original tap, before rendering
    // or saving over HTTP. Mobile autoplay activation does not survive awaits.
    session.ctx=new Ctx();
    let resume;
    try { resume=session.ctx.state==='suspended' ? session.ctx.resume() : Promise.resolve(); }
    catch(error){resume=Promise.reject(error);}
    const audioReady=Promise.resolve(resume).then(()=>({ok:true}),(error)=>({error}));
    const mediaReady=Promise.resolve(navigator.mediaDevices.getUserMedia(captureConstraints(capture))).then((stream)=>{
      if(!current()){stopMicStream(stream);throw micFailure('canceled');}
      session.stream=stream;return stream;
    });
    report('permission');
    const stream=await waitForMic(mediaReady,session,12000,'permission-timeout');
    if(!current())throw micFailure('canceled');
    report('audio');
    const audio=await waitForMic(audioReady,session,5000,'audio-timeout');
    if(audio.error)throw audio.error;
    if(!current())throw micFailure('canceled');
    const ctx=session.ctx;
    if(ctx.state && ctx.state!=='running')throw micFailure('audio-suspended');
    const track=stream.getAudioTracks()[0];
    if(!track || track.readyState==='ended')throw micFailure('NotReadableError');
    S.applied=track.getSettings ? track.getSettings() : {};
    const src=ctx.createMediaStreamSource(stream);
    const proc=ctx.createScriptProcessor(4096,1,1);
    session.proc=proc;
    const mute=ctx.createGain();mute.gain.value=0;
    REC.bufs=[];
    proc.onaudioprocess=(event)=>{if(current())REC.bufs.push(new Float32Array(event.inputBuffer.getChannelData(0)));};
    src.connect(proc);proc.connect(mute);mute.connect(ctx.destination);
    REC.stream=stream;REC.ctx=ctx;REC.proc=proc;REC.sr=ctx.sampleRate;REC.t0=Date.now();
    REC.phase='recording';REC.starting=false;
    S.recording=true;S.recTarget=recordingTarget;succeeded=true;
    if(requested.onState)await requested.onState('recording');
    else await render();
    // Navigation/cancellation can happen while a UI callback is pending.
    // A canceled attempt must not announce a fresh recording on another page.
    if(!current())throw micFailure('canceled');
    if (!requested.onState) toast('录音中… 说完一项再点一次结束');
    return true;
  } catch(error) {
    const live=REC.session===session;
    releaseMicSession(session);
    if(live){
      REC.stream=null;REC.ctx=null;REC.proc=null;REC.bufs=[];REC.phase='idle';S.recording=false;S.recTarget=null;
      if(error.micCode!=='canceled'){
        const problem=micProblem(error,session);S.micProblem=problem;
        toast('麦克风未启动，请查看底部提示',true);
        // Permission inspection is optional and must never delay recording.
        try{if(navigator.permissions)Promise.resolve(navigator.permissions.query({name:'microphone'})).then((permission)=>{
          if(S.micProblem===problem){problem.detail.permission=permission.state;if(requested.onState)Promise.resolve(requested.onState('error')).catch(()=>{});}
        }).catch(()=>{});}catch(_){/* optional API */}
      }
    }
    return false;
  } finally {
    if(REC.session===session){
      REC.starting=false;
      if(!succeeded || session.closed)REC.session=null;
    }
  }
}

async function stopRec(onState) {
  const {bufs,sr}=REC;
  const seconds=(Date.now()-REC.t0)/1000;
  await cancelRec();
  const total=bufs.reduce((a,b)=>a+b.length,0);
  if(onState)await onState('processing');else await render();
  if(!total || seconds<0.25){toast('录音太短，请点开始后说完一整项，再点一次结束');return null;}
  const merged=new Float32Array(total);
  let off=0;
  for(const block of bufs){merged.set(block,off);off+=block.length;}
  return encodeWav(merged,sr,16000);
}

// 16k 单声道 16bit WAV；采样率不等时用 OfflineAudioContext 重采样
async function encodeWav(samples, srcRate, dstRate) {
  let data = samples;
  if (Math.abs(srcRate - dstRate) > 1) {
    const len = Math.max(1, Math.round(samples.length * dstRate / srcRate));
    const OC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    const off = new OC(1, len, dstRate);
    const buf = off.createBuffer(1, samples.length, srcRate);
    buf.copyToChannel(samples, 0);
    const node = off.createBufferSource();
    node.buffer = buf;
    node.connect(off.destination);
    node.start(0);
    const rendered = await off.startRendering();
    data = rendered.getChannelData(0);
  }
  const n = data.length;
  const ab = new ArrayBuffer(44 + n * 2);
  const dv = new DataView(ab);
  const ws = (o, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)); };
  ws(0, 'RIFF'); dv.setUint32(4, 36 + n * 2, true); ws(8, 'WAVE'); ws(12, 'fmt ');
  dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
  dv.setUint32(24, dstRate, true); dv.setUint32(28, dstRate * 2, true);
  dv.setUint16(32, 2, true); dv.setUint16(34, 16, true); ws(36, 'data');
  dv.setUint32(40, n * 2, true);
  let o = 44;
  for (let i = 0; i < n; i++) {
    let s = Math.max(-1, Math.min(1, data[i]));
    dv.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    o += 2;
  }
  return new Blob([ab], { type: 'audio/wav' });
}

async function uploadWav(blob, url, extra, capture, signal) {
  const settings = capture || S.capture;
  const fd = new FormData();
  fd.append('audio', blob, 'say.wav');
  fd.append('mode', settings.mode);
  fd.append('denoise', settings.denoise ? '1' : '0');
  fd.append('asr_mode', settings.asrMode || 'grammar');
  Object.keys(extra || {}).forEach((k) => fd.append(k, extra[k]));
  const res = await fetch(appURL(url), { method: 'POST', body: fd, credentials: 'same-origin', signal:signal });
  const d = await res.json().catch(() => ({ error: '响应解析失败' }));
  if (!res.ok) throw new Error(d.error || ('HTTP ' + res.status));
  return d;
}

function fmtMetrics(m) {
  if (!m) return '';
  return '估计 SNR ' + m.snr_db + ' dB · 噪声底 ' + m.noise_dbfs + ' dB · 语音占比 ' + m.speech_ratio +
    '% · ' + m.duration + 's' + (m.clipping_pct > 0.5 ? ' · 削波 ' + m.clipping_pct + '%' : '');
}

async function finishRec() {
  const target = S.recTarget || { url: '/api/asr/audio' };
  const blob = await stopRec(target.onState);
  if (!blob) return;
  S.lastBlob = blob;
  if (!target.onState) toast('上传识别中…');
  try {
    const d = await uploadWav(blob, target.url, target.extra, target.capture);
    S.lastAsr = d;
    if (target.onDone) { await target.onDone(d); return; }
    await afterTaskRecord(d, target);
  } catch (err) {
    toast('识别失败：' + err.message, true);
  }
}

async function afterTaskRecord(d, recordingTarget) {
  const recorded = recordingTarget || S.recTarget;
  const target = recorded && recorded.extra;
  if (!S.task || (target && target.task_id && String(target.task_id) !== String(S.task.id))) {
    toast('录音所属任务已切换，请回到原任务重报', true);
    return;
  }
  S.audioId = d.audio_id;
  S.asrInfo = d;
  if (d.text) {
    S.dockText = d.text;
    toast('识别：' + d.text);
    const currentKey = target && target.current_key;
    await doParse(d.text, Object.assign({}, d, { current_key: currentKey }));
    return;
  }
  await render();
  if (d.note) toast(d.note, true);
  if (S.simulate && d.engine && !d.engine.available) {
    const ex = (EXAMPLES[S.task.template.code] || [])[0] || '';
    toast('引擎未接入，按示例模拟转写（仅演示交互）');
    await streamText(ex);
    await doParse();
  }
}

function streamText(text) {
  return new Promise((resolve) => {
    const box = $('#saybox');
    if (!box) { resolve(); return; }
    let i = 0;
    box.value = '';
    const t = setInterval(() => {
      box.value = text.slice(0, i += 3);
      S.dockText = box.value;
      if (i >= text.length) { clearInterval(t); resolve(); }
    }, 26);
  });
}

// ---------------------------------------------------------------- 提交判定
async function doSubmit() {
  if (S.busy) return;
  const values = {};
  let filled = 0;
  Object.keys(S.values).forEach((k) => {
    const v = S.values[k];
    if (v && v.value !== '' && v.value !== null && v.value !== undefined) { values[k] = v; filled += 1; }
  });
  if (!filled) { toast('还没有任何实测值'); return; }
  S.busy = true;
  try {
    const d = await api('/api/tasks/' + S.task.id + '/records', {
      method: 'POST',
      body: {
        version:S.task.version, values: values,
        confirmed: Object.keys(S.confirmed).filter((k) => S.confirmed[k]),
        transcript: S.transcript, audio_id: S.audioId, engine: S.engine,
      },
    });
    S.judge = d.record.judge;
    S.task = d.task;
    S.round = d.task.round;
    S.plan = d.plan;
    await render();
    window.scrollTo({ top: 0, behavior: 'smooth' });
    toast(d.message);
  } catch (err) {
    toast(err.message, true);
  } finally {
    S.busy = false;
  }
}

// ---------------------------------------------------------------- 模板发布
async function doPublish(tid) {
  const base = await api('/api/templates/' + tid);
  const schema = base.template.schema;
  $$('input[data-prop]').forEach((inp) => {
    const si = +inp.dataset.sec, fi = +inp.dataset.fld, prop = inp.dataset.prop;
    const f = schema.sections[si].fields[fi];
    const raw = inp.value.trim();
    if (raw === '') delete f[prop];
    else f[prop] = parseFloat(raw);
  });
  const titleEl = $('#tpl-title');
  const stdEl = $('#tpl-std');
  if (titleEl) schema.title = titleEl.value.trim();
  if (stdEl) schema.std_version = stdEl.value.trim();
  const d = await api('/api/templates/' + tid + '/publish', { method: 'POST', body: { schema: schema } });
  toast(d.message);
  go('#/proc'); await render();
}

// ---------------------------------------------------------------- 通知
async function openNotify() {
  const d = await api('/api/notifications');
  const items = d.notifications.map((n) =>
    '<div class="jrow"><div class="jl"><b>' + esc(n.title) + '</b><div class="s">' + esc(n.body || '') + '</div></div>' +
    '<div class="jr hint">' + esc((n.created_at || '').slice(5, 16)) + '</div></div>').join('');
  openModal('<h3>消息' + (d.unread ? '（' + d.unread + ' 条未读）' : '') + '</h3>' +
    (items || '<div class="hint">暂无消息</div>') +
    '<div style="height:12px"></div><button class="btn ghost block" data-act="markread">全部标记已读</button>');
  document.addEventListener('click', function once(ev) {
    if (ev.target.closest('[data-act="markread"]')) {
      api('/api/notifications/read', { method: 'POST' }).then(() => { closeModal(); render(); });
      document.removeEventListener('click', once);
    }
  });
}

// ---------------------------------------------------------------- 识别实验室
async function labApply(sid, d) {
  S.labResults[sid] = d;
  S.lastAsr = d;
  await render();
  const j = (d && d.judge) || {};
  if (j.ok) toast('✓ 字段命中：' + (j.got === undefined ? '' : j.got));
  else toast('✗ ' + (j.reason || '未命中') + (d && d.result && d.result.text ? '（识别到：' + d.result.text + '）' : ''), true);
}

VIEWS.lab = async function () {
  if (!S.me) { go('#/login'); return { html: '' }; }
  const [sc, sum] = await Promise.all([api('/api/asr/lab/script'), api('/api/asr/lab/summary')]);
  S.lab = sc;
  const eng = sc.engine || {};
  if (eng.supports_grammar === false) S.capture.asrMode = 'dictation';
  const okBadge = eng.available
    ? '<span class="pill ok">引擎就绪 · ' + esc(eng.engine) + '</span>'
    : '<span class="pill warn">引擎不可用 · ' + esc(eng.engine || 'stub') + '</span>';

  const lastG = (S.lastAsr && S.lastAsr.grammar) || null;
  const modeLine = '识别模式：<b>' + (S.capture.asrMode === 'dictation' ? '自由听写' : '语法约束') + '</b>' +
    (eng.supports_grammar === false ? ' · 选中字段后说读数与单位，核对后确认' : lastG
      ? (lastG.ok
        ? ' · 已加载语法' + (lastG.items ? '（' + lastG.items + ' 条）' : '') + (lastG.cached ? ' [缓存]' : '')
        : ' · <span style="color:var(--bad)">语法加载失败，当前按听写跑：' + esc(lastG.error || '') + '</span>')
      : (eng.available ? ' · 录音时自动加载本表语法' : ''));

  const secure = !!window.isSecureContext;
  const micApi = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  const host = location.host;
  const ctxLine = '<div class="hint" style="margin-top:8px;line-height:1.7">' +
    '运行环境：<b>' + esc(host) + '</b> · ' +
    (secure ? '<span style="color:var(--ok)">安全上下文，可录音</span>'
      : '<span style="color:var(--bad)">非安全上下文，浏览器会拦麦克风</span>') +
    ' · 麦克风接口' + (micApi ? '可用' : '不可用') +
    (secure ? '' : '<br>改用 <b>http://127.0.0.1:' + esc(location.port || '80') +
      '</b>（本机）或 <b>https://</b>（手机）打开本页') +
    '</div>';

  const banner = '<div class="card" style="padding:12px 14px">' +
    '<div class="row between"><b>语音识别实验室</b>' + okBadge + '</div>' +
    '<div class="hint" style="margin-top:8px">' +
    (eng.available
      ? '读完屏幕上的一句话，点按钮开始、再点结束；系统会自动算 SNR、字符错误率，并判断字段是否命中。'
      : esc(eng.advice || ('当前引擎不可用：' + (eng.reason || '未接入'))) +
        '<br><span style="color:var(--muted)">录音仍会留档并算出音频指标（SNR / 时长 / 削波），只是没有识别文本。</span>') +
    '</div>' + ctxLine +
    '<div class="hint" style="margin-top:8px">' + modeLine + '</div>' +
    '<div class="row" style="margin-top:10px;gap:8px">' +
    '<button class="btn ghost sm grow" data-act="capture">采集设置（' +
    (S.capture.mode === 'raw' ? '原声' : '系统降噪') + ' · 服务端降噪' + (S.capture.denoise ? '开' : '关') + '）</button>' +
    '<button class="btn ghost sm" data-act="probe">重新检测</button>' +
    '<button class="btn ghost sm" data-act="labreset">清空</button>' +
    '</div></div>';

  const sumCard = '<div class="card"><h3>汇总（累计 ' + (sum.total || 0) + ' 条）</h3>' +
    (sum.total
      ? '<div class="row" style="gap:14px;flex-wrap:wrap">' +
        '<div><div class="hint">字段命中率</div><div style="font-size:20px;font-weight:800;color:var(--brand)">' +
        Math.round((sum.field_accuracy || 0) * 100) + '%</div></div>' +
        '<div><div class="hint">平均字符错误率</div><div style="font-size:20px;font-weight:800">' +
        (sum.cer === null ? '—' : Math.round(sum.cer * 100) + '%') + '</div></div>' +
        '<div><div class="hint">平均估计 SNR</div><div style="font-size:20px;font-weight:800">' +
        (sum.snr_db === null ? '—' : sum.snr_db + ' dB') + '</div></div>' +
        '</div>'
      : '<div class="hint">还没有样本。用同一批口报重复对照，并覆盖不同人员、距离和噪声条件。</div>') +
    (sum.grammar_failed
      ? '<div class="hint" style="margin-top:8px;color:var(--bad)">有 ' + sum.grammar_failed +
        ' 条样本的语法没加载成功，实际跑的是听写——先解决这个，再看命中率。</div>' : '') +
    (sum.groups && sum.groups.length
      ? '<table class="tbl" style="margin-top:10px"><tr><th>模式</th><th>采集</th><th>样本</th><th>命中</th><th>CER</th></tr>' +
        sum.groups.map((g) => '<tr><td>' + (g.asr_mode === 'grammar' ? (g.grammar_ok ? '语法' : '语法失败') : '听写') +
          '</td><td>' + esc(g.mode) + (g.denoise ? '+降噪' : '') +
          '</td><td class="num">' + g.n + '</td><td class="num">' + (g.ok || 0) +
          '</td><td class="num">' + (g.cer === null ? '—' : Math.round(g.cer * 100) + '%') + '</td></tr>').join('') +
        '</table>'
      : '') +
    '</div>';

  const items = sc.script.map((s, i) => {
    const r = S.labResults[s.id];
    const j = r && r.judge;
    const g = (r && r.result && r.result.grammar) || null;
    const badge = !r ? '<span class="pill na">未测</span>'
      : (j && j.ok ? '<span class="pill ok">命中</span>' : '<span class="pill bad">未命中</span>');
    const m = (r && r.result && r.result.metrics) || null;
    const modeTip = r ? ('<div style="margin-top:4px">' +
      '<span class="pill ' + (g && g.ok ? 'ok' : 'warn') + '">' +
      (r.result && r.result.asr_mode === 'dictation' ? '普通话听写' :
        (g ? (g.ok ? '语法约束' + (g.items ? ' · ' + g.items + ' 条' : '') : '听写（语法未加载）') : '未知模式')) +
      '</span>' +
      (r.result && r.result.confidence ? ' <span class="pill na">置信度 ' +
        Math.round(r.result.confidence * 100) + '%</span>' : '') +
      (g && !g.ok && g.error ? '<div class="hint" style="color:var(--bad)">语法失败：' + esc(g.error) + '</div>' : '') +
      '</div>') : '';
    const detail = r ? ('<div class="fnote" style="display:block;line-height:1.7">' +
      '识别：<b>' + esc((r.result && r.result.text) || '（空）') + '</b>' +
      '　期望念法：<span style="color:var(--brand)">' + esc(s.read || s.say) + '</span><br>' +
      '解析值：' + esc(j && j.got !== undefined && j.got !== null ? j.got : '—') +
      '（期望 ' + esc(s.expect) + '）' + (j && j.note ? ' · ' + esc(j.note) : '') + '<br>' +
      (m ? esc(fmtMetrics(m)) : '') +
      (m && m.clipping_pct > 0.5 ? ' · <span style="color:var(--bad)">削波，离远一点</span>' : '') +
      '</div>') : '';
    const recording = S.recording && S.recTarget && S.recTarget.extra &&
      S.recTarget.extra.script_id === s.id;
    return '<div class="card" style="padding:12px 14px">' +
      '<div class="row between"><div><b>' + (i + 1) + '. ' + esc(s.say) + '</b>' +
      '<div class="hint">目标字段 ' + esc(s.key) + ' · 期望 ' + esc(s.expect) + (s.unit ? ' ' + esc(s.unit) : '') +
      '</div><div class="hint" style="color:var(--brand)">请这样念：' + esc(s.read || s.say) + '</div></div>' +
      badge + '</div>' +
      '<div class="row" style="margin-top:10px"><button class="btn sm ' + (recording ? 'bad' : 'ghost') +
      ' block" data-act="mic" data-sid="' + s.id + '">' + (recording ? '● 结束并识别' : '开始念这一句') + '</button></div>' +
      modeTip + detail + '</div>';
  }).join('');

  return {
    html: '<div class="wrap">' +
      '<div class="page-title">识别实验室 <span class="sub">10 句固定脚本 · 自动评分 · ' +
      esc(sum.version || '') + '</span></div>' +
      banner + sumCard + items +
      '<div class="row" style="margin-top:6px"><button class="btn ghost sm grow" data-act="home">返回任务</button></div>' +
      '<div style="height:8px"></div></div>',
  };
};

// ---------------------------------------------------------------- 启动
window.addEventListener('hashchange', async () => {
  if (S.recording || REC.starting) await cancelRec();
  await render();
});
window.addEventListener('pagehide', () => { if(S.recording || REC.starting)cancelRec(); });
window.addEventListener('error', (e) => {
  const app = $('#app');
  if (app && !app.innerHTML.trim()) {
    app.innerHTML = '<div class="wrap"><div class="card"><h3>脚本出错</h3><div class="hint">' +
      esc(e.message) + '</div></div></div>';
  }
});
boot();

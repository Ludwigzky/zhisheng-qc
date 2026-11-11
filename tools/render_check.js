/**
 * 前端渲染自检：用真实后端返回的数据当夹具，在 Node 里跑一遍各个视图，
 * 只验证「不报错 + 关键内容出现」，不依赖浏览器。
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

throw new Error('旧版无密码界面的渲染脚本已停用。请运行 tools/asr_integration_check.py --mobile。');
if (!BASE) throw new Error('请运行 python tools/asr_integration_check.py，使用隔离服务和临时数据库');
const ROOT = path.resolve(__dirname, '..');

let fails = [];
function check(name, cond, extra) {
  console.log((cond ? '  OK  ' : '  BAD ') + name + (extra !== undefined ? '  ' + String(extra).slice(0, 120) : ''));
  if (!cond) fails.push(name);
}

// ---------- 1. 从真实服务取夹具 ----------
async function login(emp) {
  const r = await fetch(BASE + '/api/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ emp_no: emp }),
  });
  const cookies = (r.headers.getSetCookie ? r.headers.getSetCookie() : [r.headers.get('set-cookie')])
    .map((c) => String(c).split(';')[0]).join('; ');
  return cookies;
}

async function get(url, cookies, opts) {
  const r = await fetch(BASE + url, Object.assign({ headers: { Cookie: cookies } }, opts || {}));
  return r.json();
}

(async function main() {
  await fetch(BASE + '/api/demo/reset', { method: 'POST' });   // 回到初始数据，保证任务可领取
  const ck = await login('QC1024');
  const fixtures = {};
  fixtures['/api/me'] = await get('/api/me', ck);
  fixtures['/api/bootstrap'] = await get('/api/bootstrap', ck);
  const planId = fixtures['/api/bootstrap'].plans[0].plan_id;
  const pv = await get('/api/plans/' + planId, ck);
  fixtures['/api/plans/' + planId] = pv;
  const tid = pv.nodes[0].task_id;
  const tv = await get('/api/tasks/' + tid, ck);
  fixtures['/api/tasks/' + tid] = tv;
  fixtures['/api/templates'] = await get('/api/templates', ck);

  // ---------- 2. 极简 DOM ----------
  const store = {};
  function makeEl(id) {
    const el = {
      id, innerHTML: '', value: '', textContent: '', className: '', style: {},
      dataset: {}, children: [],
      classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); } },
      addEventListener() {}, removeEventListener() {}, focus() {}, appendChild() {},
      querySelector() { return makeEl(); },
      querySelectorAll() { return []; },
      closest() { return null; },
      scrollIntoView() {},
    };
    return el;
  }
  function bySel(sel) {
    const key = sel.replace(/^#/, '');
    if (!store[key]) store[key] = makeEl(key);
    return store[key];
  }
  const sandbox = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    Promise, JSON, Math, Date, RegExp, Object, Array, String, Number, Boolean, Error, isNaN, parseFloat, parseInt,
    navigator: { mediaDevices: null },
    location: { hash: '#/home' },
    history: {},
    alert: () => {},
    fetch: async (url, opts) => {
      const key = String(url).split('?')[0];
      if (!(key in fixtures)) {
        return { ok: false, status: 404, json: async () => ({ error: 'no fixture: ' + key }) };
      }
      return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(fixtures[key])) };
    },
  };
  sandbox.document = {
    getElementById: bySel,
    querySelector: bySel,
    querySelectorAll: () => [],
    addEventListener: () => {},
    createElement: () => makeEl(),
    body: makeEl(),
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.window.scrollTo = () => {};
  sandbox.window.addEventListener = () => {};

  // 预置 /api/asr/parse 与提交结果
  fixtures['/api/asr/parse'] = {
    assignments: [
      { key: 'plate_t', label: '钢板厚度 t', value: 20.2, confidence: 0.95, source: 'asr', raw: '钢板厚度 20.2', unit: 'mm', derived: false },
      { key: 'flange_b', label: '型钢翼缘宽度 b', value: 203, confidence: 0.7, source: 'asr', raw: '翼缘宽度 203', unit: 'mm', derived: false },
    ], unknown: [], normalized: '钢板厚度 20.2，翼缘宽度 203', engine: 'stub',
  };
  fixtures['/api/tasks/' + tid + '/claim'] = { task: tv.task };
  fixtures['/api/tasks/' + tid + '/records'] = {
    record: { id: 9, round: 1, judge: { overall: 'pending', counts: { ok: 3, over: 0, pending: 1, missing: 0, na: 0 }, snapshot: 'abcd1234', template: { code: 'T-IQC-01', revision: 'v1.3' }, rows: [{ key: 'plate_t', label: '钢板厚度 t', spec: '20 ± 0.8 mm', value_text: '20.2', unit: 'mm', status: 'ok', note: '', derived: false }, { key: 'flange_b', label: '型钢翼缘宽度 b', spec: '198 ~ 202 mm', value_text: '203', unit: 'mm', status: 'pending', note: '识别置信度 70%，请核对', derived: false }] } },
    task: { id: tv.task.id, round: 1, status: 'pending', template: tv.task.template, history: [] },
    plan: pv, message: '待完善',
  };

  const code = fs.readFileSync(path.join(ROOT, 'frontend', 'app.js'), 'utf8') + '\n' +
    fs.readFileSync(path.join(ROOT, 'frontend', 'steel_voice.js'), 'utf8') + '\n' +
    fs.readFileSync(path.join(ROOT, 'frontend', 'steel_audio.js'), 'utf8') + '\n' +
    fs.readFileSync(path.join(ROOT, 'frontend', 'steel.js'), 'utf8') +
    '\n;globalThis.__H = { S: S, render: render, VIEWS: VIEWS, go: go, doParse: doParse, openCapture: openCapture, closeModal: closeModal, Steel: Steel, steelApplyVoice: steelApplyVoice, steelConfirmCandidate:steelConfirmCandidate };';
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: 'app.js' });

  const H = sandbox.__H;
  await new Promise((r) => setTimeout(r, 60));
  check('boot 后已登录', H.S.me && H.S.me.name === '张建国', H.S.me && H.S.me.name);

  await H.render();
  const homeHTML = store['app'].innerHTML;
  check('首页渲染出产品', homeHTML.includes('WH2026-0111'), homeHTML.length);
  check('首页有进度条', homeHTML.includes('step '), '');

  sandbox.location.hash = '#/plan/' + planId;
  await H.render();
  const planHTML = store['app'].innerHTML;
  check('计划页含三道工序', planHTML.includes('来料检验') && planHTML.includes('出厂检验'), '');
  check('计划页显示模板版本', planHTML.includes('T-IQC-01'), '');

  sandbox.location.hash = '#/task/' + tid;
  await H.render();
  const taskHTML = store['app'].innerHTML;
  check('任务页出现字段', taskHTML.includes('钢板厚度'), '');
  check('任务页有口报 dock', store['dock'].innerHTML.includes('saybox'), '');
  check('dock 有示例', store['dock'].innerHTML.includes('data-act="example"'), '');

  // 口报解析：模拟输入后调用
  const saybox = sandbox.document.querySelector('#saybox');
  saybox.value = '钢板厚度 20.2，翼缘宽度 203';
  H.S.dockText = saybox.value;
  await H.doParse();
  check('解析后写入字段值', H.S.values['plate_t'] && H.S.values['plate_t'].value === 20.2, JSON.stringify(H.S.values['plate_t']));
  check('低置信项标黄', H.S.values['flange_b'] && H.S.values['flange_b'].confidence < 0.75, '');
  const afterParse = store['app'].innerHTML;
  check('界面出现「确认无误」', afterParse.includes('确认无误'), '');

  // 提交判定（直接复用视图逻辑，避免依赖 DOM 事件）
  H.S.judge = fixtures['/api/tasks/' + tid + '/records'].record.judge;
  H.S.task = fixtures['/api/tasks/' + tid + '/records'].task;
  H.S.round = 1;
  await H.render();
  const judgeHTML = store['app'].innerHTML;
  check('判定页显示结论', judgeHTML.includes('待完善'), '');
  check('判定页显示明细', judgeHTML.includes('钢板厚度'), '');
  check('判定页显示快照', judgeHTML.includes('abcd1234'), '');

  // 工艺模板页
  sandbox.location.hash = '#/proc';
  await H.render();
  check('模板列表页', store['app'].innerHTML.includes('检验表单模板'), '');

  // 识别实验室
  fixtures['/api/asr/lab/script'] = {
    script: [
      { id: 's1', say: '跨度 22501', key: 'span', expect: 22501, unit: 'mm', kind: 'number' },
      { id: 's7', say: '焊缝外观合格', key: 'weld', expect: '合格', unit: '', kind: 'enum' },
    ],
    engine: { engine: 'sapi', available: true },
    template: { code: 'T-IPQC-02', revision: 'v2.0' },
  };
  fixtures['/api/asr/lab/summary'] = {
    total: 4, ok: 3, field_accuracy: 0.75, cer: 0.12, snr_db: 16.4,
    groups: [{ mode: 'system', denoise: 1, n: 4, ok: 3, cer: 0.12, snr: 16.4 }],
    by_script: [], recent: [], engine: { engine: 'sapi', available: true },
  };
  sandbox.location.hash = '#/lab';
  await H.render();
  const labHTML = store['app'].innerHTML;
  check('实验室页渲染脚本', labHTML.includes('跨度 22501') && labHTML.includes('焊缝外观合格'), '');
  check('实验室页显示汇总', labHTML.includes('字段命中率') && labHTML.includes('75%'), '');
  check('实验室页有录音按钮', labHTML.includes('data-act="mic"'), '');

  H.S.asr = { engine: 'sherpa-onnx', available: true, supports_grammar: false };
  H.S.capture.asrMode = 'dictation';
  H.openCapture();
  check('普通话模型不显示不可用的语法按钮', !H.S.modal.includes('data-mode="grammar"'), '');
  check('普通话模型显示读数核对提示', H.S.modal.includes('普通话离线听写'), '');
  H.closeModal();
  fixtures['/api/asr/lab/script'].engine = H.S.asr;
  H.S.lastAsr = { grammar: { ok: true, mode: 'dictation', supported: false } };
  await H.render();
  check('普通话听写不误报已加载语法', !store['app'].innerHTML.includes('已加载语法'), '');

  const tplId = fixtures['/api/templates'].templates[0].id;
  fixtures['/api/templates/' + tplId] = {
    template: { id: tplId, code: 'T-IQC-01', revision: 'v1.3', title: '来料检验记录（IQC）', std_version: 'GB/T 700', spec_ref: 'TA', schema: tv.task.template.schema },
    used_by: [],
  };
  sandbox.location.hash = '#/edittpl/' + tplId;
  await H.render();
  const tplHTML = store['app'].innerHTML;
  check('模板编辑器渲染公差输入', tplHTML.includes('data-prop="min"'), '');
  check('模板编辑器有发布按钮', tplHTML.includes('data-act="publish"'), '');

  // The real steel API provides fixtures; all mutations remain in this
  // runner's disposable server/database, never in a fixed-port demo.
  async function steelPost(url, body) {
    const response = await fetch(BASE + url, {method:'POST', headers:{Cookie:ck, 'Content-Type':'application/json'}, body:JSON.stringify(body)});
    return {status:response.status, body:await response.json()};
  }
  const steelTemplate = await get('/api/steel/template', ck);
  const created = await steelPost('/api/steel/records', {data:steelTemplate.example});
  check('钢板原表草稿创建', created.status === 201, created.status);
  const steelRecord = created.body.record;
  fixtures['/api/steel/template'] = steelTemplate;
  fixtures['/api/steel/records'] = {records:[steelRecord]};
  fixtures['/api/steel/records/' + steelRecord.id] = {record:steelRecord};
  sandbox.location.hash = '#/steel'; await H.render();
  check('钢板页面突出原表与Excel产出', store['app'].innerHTML.includes('HW/Q-JL-02A') && store['app'].innerHTML.includes('新建检验记录'), '');
  sandbox.location.hash = '#/steel/' + steelRecord.id; await H.render();
  check('钢板页面覆盖验证外观与尺寸', store['app'].innerHTML.includes('质量证明书') && store['app'].innerHTML.includes('成卷牢固') && store['app'].innerHTML.includes('data-steel-value="width"'), '');
  check('钢板页面提供原表预览', store['app'].innerHTML.includes('/preview?v='), '');
  const asrData = JSON.parse(JSON.stringify(steelRecord.data));
  asrData.values.thickness = {value:5.65, source:'asr', confidence:0, confirmed:false};
  fixtures['/api/steel/evaluate'] = (await steelPost('/api/steel/evaluate', {data:asrData})).body;
  await H.steelApplyVoice({record_id:steelRecord.id, text:'厚度五点六五', assignments:[{key:'thickness', value:5.65, raw:'厚度五点六五', confidence:0}]});
  check('23个字段提供语音入口且批量结论保留手填', (store['app'].innerHTML.match(/data-steel-voice-target=/g)||[]).length === 23 &&
    !store['app'].innerHTML.includes('data-steel-voice-target="inspection_mode"'), '');
  check('录入面板默认收起仍有麦克风入口', store['dock'].innerHTML.includes('aria-expanded="false"') &&
    store['dock'].innerHTML.includes('id="steel-capture-panel" class="steel-capture-panel" hidden') &&
    store['dock'].innerHTML.includes('data-steel-act="mic"'), '');
  check('钢板口报在表单预览新候选并保留旧确认值', H.Steel.data.voice_candidates[0].value === 5.65 &&
    H.Steel.data.values.thickness.confirmed && JSON.stringify(H.Steel.data.values.thickness.value) === '[5.64]' &&
    store['app'].innerHTML.includes('value="5.65"') && store['app'].innerHTML.includes('核对确认'), '');
  await H.steelApplyVoice({record_id:steelRecord.id + 100, text:'迟到的旧录音', assignments:[{key:'thickness', value:99, confidence:1}]});
  check('钢板迟到的异记录转写不覆盖', H.Steel.data.voice_candidates.length === 1 && H.Steel.data.voice_candidates[0].value === 5.65, '');
  const blocked = await steelPost('/api/steel/records/' + steelRecord.id + '/finalize', {version:steelRecord.version, data:H.Steel.data});
  check('钢板未确认口报阻止定稿并保留本次草稿', blocked.status === 400 && blocked.body.draft_saved === true &&
    blocked.body.record.status === 'draft' && blocked.body.record.data.voice_pending.includes('thickness'), blocked.status);
  H.steelConfirmCandidate('thickness');
  const finalized = await steelPost('/api/steel/records/' + steelRecord.id + '/finalize', {version:blocked.body.record.version, data:H.Steel.data});
  check('钢板确认后形成正式快照', finalized.status === 200 && finalized.body.record.status === 'final', finalized.status);
  H.Steel.record = finalized.body.record; H.Steel.data = finalized.body.record.data; H.Steel.evaluation = finalized.body.record.evaluation;
  await H.render();
  check('钢板正式记录提供Excel下载并锁定', store['app'].innerHTML.includes('下载原版式 Excel') && !store['app'].innerHTML.includes('data-steel-act="finalize"'), '');
  const steelFile = await fetch(BASE + '/api/steel/records/' + steelRecord.id + '/xlsx', {headers:{Cookie:ck}});
  check('钢板实际Excel文件下载', steelFile.status === 200 && String(steelFile.headers.get('content-type')).includes('spreadsheetml'), steelFile.status);

  console.log('\n=== 结果 ===');
  console.log('FAILS:', fails.length, fails.join(', '));
  console.log(fails.length ? 'FAIL' : 'OK');
  process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.error('渲染自检异常:', e); process.exit(2); });

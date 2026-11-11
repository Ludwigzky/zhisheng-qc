/**
 * Real-browser mobile checks against the disposable server created by
 * asr_integration_check.py. Never resets data, starts a server, downloads a
 * browser, connects to an existing profile, or requests a real microphone.
 */
'use strict';
throw new Error('此脚本对应历史演示角色和未锁定的检验标准，已停用。请运行 tools/asr_integration_check.py --mobile。');

const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, '_probe', 'mobile');
const BUNDLED_MODULES = process.env.CODEX_BUNDLED_NODE_MODULES ||
  'C:\\Program Files\\WindowsApps\\OpenAI.CodexPrimaryRuntime.v26-1105-1297-0_26.1105.1297.0_x64__3k8sg7r9htsxt\\dependencies\\node\\node_modules';
const CHROME = process.env.QC_TEST_CHROME ||
  'C:\\Users\\98060\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe';
const USER_AGENT = 'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36';
const VOICE_CASES = [
  {key: 'document_no', text: '零零一二三', expected: '00123'},
  {key: 'inspection_date', text: '二零二六年十一月十日', expected: '2026-11-10'},
  {key: 'grade', text: 'Q三五五B', expected: 'Q355B'},
  {key: 'thickness_lower', text: '负零点三', expected: '-0.3'},
  {key: 'extra_0_name', text: '长度', expected: '长度'},
];
const MANUAL_ONLY_KEYS = ['inspection_mode', 'inspected_quantity', 'qualified_quantity', 'rejected_quantity',
  'sampling_basis', 'disposition', 'additional_failure_confirmed', 'cert_provided', 'cert_performance',
  'identity_match', 'cert_seal', 'surface', 'shape', 'coil'];
const MANUAL_CHECK_KEYS = ['cert_provided','cert_performance','identity_match','cert_seal','surface','shape','coil'];

const report = {schema_version: 1, started_at: new Date().toISOString(),
  browser: CHROME, widths: [], checks: [], failures: [], page_errors: [],
  blocked_audio_requests: [], authorized_audio_imports: [], touch_startups: [], screenshots: [], exports: []};

function check(name, condition, detail) {
  const item = {name, pass: Boolean(condition)};
  if (detail !== undefined) item.detail = detail;
  report.checks.push(item);
  if (!item.pass) report.failures.push(item);
  console.log((item.pass ? '  OK  ' : '  BAD ') + name +
    (item.pass || detail === undefined ? '' : ' ' + JSON.stringify(detail).slice(0, 240)));
  return item.pass;
}

function requireCheck(name, condition, detail) {
  if (!check(name, condition, detail)) throw new Error(name);
}

function safeBase() {
  if (!process.env.QC_TEST_BASE) {
    throw new Error('QC_TEST_BASE is required; run the isolated integration runner --mobile');
  }
  const url = new URL(process.env.QC_TEST_BASE);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
      !url.port || url.port === '8760' || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash) {
    throw new Error('QC_TEST_BASE must be an explicit disposable loopback HTTP origin; port 8760 is forbidden');
  }
  return url.origin;
}

async function bodyOf(response) {
  let body;
  try { body = await response.json(); } catch (_) { body = {error: await response.text()}; }
  return {status: response.status(), body};
}

async function expandDetails(page) {
  const summaries = page.locator('.steel-form details:not([open]) > summary');
  // Opening a parent can reveal nested details. Bound the loop against a broken UI.
  for (let n = 0; n < 12 && await summaries.count(); n++) await summaries.first().click();
}

async function openDock(page) {
  const toggle = page.locator('[data-steel-act="dock-toggle"]');
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
  await page.locator('#steel-capture-panel').waitFor({state: 'visible'});
}

async function closeDock(page) {
  const toggle = page.locator('[data-steel-act="dock-toggle"]');
  if (await toggle.getAttribute('aria-expanded') === 'true') await toggle.click();
  await page.locator('#steel-capture-panel').waitFor({state: 'hidden'});
}

async function userPageScroll(page, delta) {
  await page.evaluate((amount) => {
    document.querySelector('.steel-workspace').dispatchEvent(new WheelEvent('wheel', {deltaY: amount, bubbles: true}));
    scrollBy(0, amount);
  }, delta);
}

async function assertNoOverflow(page, label) {
  const metric = await page.evaluate(() => ({width: innerWidth,
    document: document.documentElement.scrollWidth, body: document.body.scrollWidth,
    offenders: Array.from(document.querySelectorAll('#topbar *, .steel-workspace *, #dock *'))
      .filter((el) => {
        const box = el.getBoundingClientRect();
        return box.width && box.height && (box.left < -1 || box.right > innerWidth + 1);
      }).slice(0, 12).map((el) => ({tag: el.tagName, cls: el.className,
        text: (el.textContent || '').trim().slice(0, 60)}))}));
  check(label + ' no horizontal overflow', metric.document <= metric.width + 1 &&
    metric.body <= metric.width + 1 && !metric.offenders.length, metric);
}

async function hitTest(page, locator) {
  await locator.evaluate((el) => el.scrollIntoView({block: 'center', inline: 'nearest'}));
  return locator.evaluate((el) => {
    const box = el.getBoundingClientRect();
    const x = box.left + box.width / 2, y = box.top + box.height / 2;
    const hit = document.elementFromPoint(x, y);
    return {clear: Boolean(hit && (el === hit || el.contains(hit))),
      width: box.width, height: box.height, top: box.top, bottom: box.bottom,
      hit: hit ? {tag: hit.tagName, cls: hit.className, text: (hit.textContent || '').slice(0, 45)} : null};
  });
}

async function assertSafetyCard(page,label,recording) {
  const card=page.locator('#steel-recorder-safety');
  if(recording && !await card.isVisible()){
    const field=page.locator('[data-steel-voice-target][aria-pressed="true"]');
    check(label+' the active field replaces the floating recorder when its Stop action is visible',
      await field.count()===1 && (await hitTest(page,field)).clear);
    return;
  }
  await card.waitFor({state:'visible'});
  const geometry=await card.evaluate((element)=>{
    const box=element.getBoundingClientRect(),style=getComputedStyle(element);
    return {body:element.parentElement===document.body,position:style.position,
      left:box.left,top:box.top,right:box.right,bottom:box.bottom,width:innerWidth,height:innerHeight};
  });
  check(label+' compact recorder pill stays within the visible screen',geometry.body &&
    geometry.position==='fixed' && geometry.left>=0 && geometry.top>=0 &&
    geometry.right<=geometry.width && geometry.bottom<=geometry.height &&
    geometry.bottom-geometry.top<=54,geometry);
  const actions=card.locator('[data-steel-act="safety-cancel"], [data-steel-act="safety-stop"]');
  const buttons=[];
  for(let index=0;index<await actions.count();index++)buttons.push(await hitTest(page,actions.nth(index)));
  check(label+' compact recorder has one clear 44px stop or cancel action',
    buttons.length===1 && buttons.every((item)=>item.clear && item.height>=43.5 && item.width>=43.5),buttons);
}

async function assertFormGeometry(page, label, targets) {
  await expandDetails(page);
  const metrics = await page.locator('[data-steel-voice-target]').evaluateAll((els) => els.map((el) => {
    const box = el.getBoundingClientRect();
    return {key: el.dataset.steelVoiceTarget || el.dataset.key,
      label: el.getAttribute('aria-label') || el.textContent.trim(),
      width: box.width, height: box.height, visible: Boolean(box.width && box.height)};
  }));
  const expected = targets.map((target) => target.key).sort();
  const actual = metrics.map((item) => item.key).sort();
  check(label + ' 23 unique field voice entrances match backend catalog',
    expected.length === 23 && actual.length === expected.length &&
    new Set(actual).size === expected.length && JSON.stringify(actual) === JSON.stringify(expected),
    {expected, actual});
  check(label + ' batch-conclusion section exposes no voice entry',
    await page.locator('#steel-batch [data-steel-voice-target]').count() === 0 &&
    !actual.some((key) => MANUAL_ONLY_KEYS.includes(key)));
  check(label+' document verification and appearance sections have no voice entrance',
    await page.locator('#steel-verify [data-steel-voice-target], #steel-appearance [data-steel-voice-target]').count()===0);
  check(label + ' every field microphone has a named 44px touch target',
    metrics.every((item) => item.visible && item.label && item.width >= 43.5 && item.height >= 43.5),
    metrics.filter((item) => !item.visible || !item.label || item.width < 43.5 || item.height < 43.5));
  const fonts = await page.locator('.steel-form input:not([type="checkbox"]), .steel-form select, .steel-form textarea, #steel-say')
    .evaluateAll((els) => els.filter((el) => el.getBoundingClientRect().height)
      .map((el) => ({key: el.dataset.steelHeader || el.dataset.steelValue || el.id || el.dataset.prop,
        fontSize: parseFloat(getComputedStyle(el).fontSize)})));
  check(label + ' form input fonts are at least 16px', fonts.length > 0 &&
    fonts.every((item) => item.fontSize >= 16), fonts.filter((item) => item.fontSize < 16));
  await openDock(page);
  const actions = page.locator('[data-steel-act="save"], [data-steel-act="finalize"]');
  const actionMetrics = [];
  for (let i = 0; i < await actions.count(); i++) {
    const item = actions.nth(i);
    if (await item.isVisible()) actionMetrics.push(await hitTest(page, item));
  }
  check(label + ' save and final confirmation are reachable above dock', actionMetrics.length >= 2 &&
    actionMetrics.every((item) => item.clear && item.height >= 43.5), actionMetrics);
  await closeDock(page);
  const sectionMetrics = [];
  const sections = page.locator('.steel-form > section');
  for (let i = 0; i < await sections.count(); i++) {
    const controls = sections.nth(i).locator('input, select, textarea, button');
    for (let j = await controls.count() - 1; j >= 0; j--) {
      const control = controls.nth(j);
      if (await control.isVisible() && await control.isEnabled()) {
        sectionMetrics.push({section: i + 1, ...await hitTest(page, control)});
        break;
      }
    }
  }
  check(label + ' each section final control can scroll clear of fixed dock',
    sectionMetrics.length > 0 && sectionMetrics.every((item) => item.clear), sectionMetrics);
  await assertNoOverflow(page, label);
  return {microphones: metrics, fonts, actions: actionMetrics, section_controls: sectionMetrics};
}

function editor(page, key) {
  const extra = /^extra_(\d+)_(name|requirement|result|verdict)$/.exec(key);
  return extra ? page.locator('[data-steel-extra="' + extra[1] + '"][data-prop="' + extra[2] + '"]') :
    page.locator('[data-steel-header="' + key + '"], [data-steel-value="' + key + '"]');
}

async function assertTextKeyboards(page, label, candidateKeys) {
  const keys = candidateKeys || ['thickness_lower', 'thickness_upper', 'width_lower', 'width_upper', 'thickness', 'width'];
  const metrics = [];
  for (const key of keys) {
    const input = candidateKeys ? page.locator('[data-steel-candidate="' + key + '"]') : editor(page, key);
    metrics.push({key, ...await input.evaluate((el) => ({type: el.type, inputMode: el.inputMode}))});
  }
  check(label + (candidateKeys ? ' candidate' : ' field') + ' keyboards support signs and measurement separators',
    metrics.every((item) => item.type === 'text' && item.inputMode === 'text'), metrics);
}

async function screenshot(page, filename) {
  const target = path.join(OUT, filename);
  await page.screenshot({path: target, fullPage: false});
  report.screenshots.push(target);
}

async function assertNoLegacyRecordingToast(page,label) {
  const toast=await page.locator('#toast').evaluate((element)=>({text:element.textContent || '',
    visible:getComputedStyle(element).display!=='none' && Boolean(element.getBoundingClientRect().height)}));
  check(label+' compact recorder does not overlay the form with legacy recording or upload toasts',
    !toast.visible || !/录音中|上传识别中/.test(toast.text),toast);
}

async function currentDraft(page) {
  // Reading state avoids inventing DOM selectors for hidden metadata/version.
  return page.evaluate(() => ({id: Steel.record.id, version: Steel.record.version,
    data: JSON.parse(JSON.stringify(Steel.data))}));
}

async function actualPreview(page,record,required,label) {
  const iframe=page.locator('iframe[title="进货检验记录预览"]');
  await iframe.waitFor({state:'attached'});
  const handle=await iframe.elementHandle();
  const frame=await handle.contentFrame();
  await frame.waitForFunction((items)=>{
    const table=document.querySelector('table');
    return table && items.every((item)=>table.textContent.includes(item));
  },required);
  const text=await frame.locator('body').textContent();
  const url=new URL(frame.url());
  check(label+' embedded preview belongs to the displayed record and its latest saved version',
    url.pathname==='/api/steel/records/'+record.id+'/preview' && url.searchParams.get('v')===String(record.version),url.href);
  return text;
}

function exportedWorkbookCells(bytes,label) {
  const target=path.join(OUT,'steel-ui-entry-'+label+'.xlsx');
  fs.writeFileSync(target,bytes);
  const script=String.raw`import json,sys,zipfile,xml.etree.ElementTree as E
S='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
M='{http://schemas.openxmlformats.org/officeDocument/2006/math}'
def text(node): return ''.join(n.text or '' for n in node.iter(S+'t'))
with zipfile.ZipFile(sys.argv[1]) as z:
 shared=[text(n) for n in E.fromstring(z.read('xl/sharedStrings.xml')).findall(S+'si')]
 cells={}
 for c in E.fromstring(z.read('xl/worksheets/sheet1.xml')).iter(S+'c'):
  v=c.find(S+'v')
  cells[c.attrib['r']]=text(c) if c.get('t')=='inlineStr' else (shared[int(v.text)] if c.get('t')=='s' and v is not None else (v.text if v is not None else ''))
 drawing=E.fromstring(z.read('xl/drawings/drawing1.xml'))
 print(json.dumps({'cells':cells,'math':[n.text or '' for n in drawing.iter(M+'t')]},ensure_ascii=False))
`;
  const python=process.env.QC_TEST_PYTHON || path.join(ROOT,'.venv-asr','Scripts','python.exe');
  const result=JSON.parse(execFileSync(python,['-c',script,target],{encoding:'utf8',timeout:15000,maxBuffer:1024*1024}));
  report.exports.push({path:target,cells:result.cells,math:result.math});
  return result;
}

async function assertManualOnlyChecks(page,label) {
  const current=await page.evaluate(()=>Steel.current);
  const parserRequests=[];
  const listener=(request)=>{if(new URL(request.url()).pathname==='/api/steel/asr/parse')parserRequests.push(request.url());};
  page.on('request',listener);
  try{
    for(const key of MANUAL_CHECK_KEYS){
      const select=editor(page,key);
      await select.selectOption('不合格');
      await select.selectOption('合格');
    }
    const state=await currentDraft(page);
    check(label+' all seven verification and appearance checks remain explicit manual selections',
      MANUAL_CHECK_KEYS.every((key)=>state.data.values[key].value==='合格' &&
        state.data.values[key].source==='manual' && state.data.values[key].confirmed===true));
    check(label+' manual-only check selection neither changes the speech target nor calls speech parsing',
      await page.evaluate(()=>Steel.current)===current && parserRequests.length===0,parserRequests);
  } finally{page.off('request',listener);}
}

async function waitForFormalValue(page, key, expected) {
  await page.waitForFunction(({key, expected}) => {
    const target = (Steel.template.review_targets || Steel.template.voice_targets).find((item) => item.key === key);
    let value;
    if (target.destination === 'value') value = (Steel.data.values[key] || {}).value;
    else if (target.destination === 'extra') value = (Steel.data.extra_rows[target.extra_index] || {})[target.extra_prop];
    else value = Steel.data[key];
    return JSON.stringify(value) === JSON.stringify(expected);
  }, {key, expected});
}

async function assertCollapsedDock(page, label) {
  const toggle = page.locator('[data-steel-act="dock-toggle"]');
  const panel = page.locator('#steel-capture-panel');
  check(label + ' recorder is initially collapsed with a reachable microphone',
    await toggle.getAttribute('aria-expanded') === 'false' && !await panel.isVisible() &&
    await page.locator('.steel-recorder.is-collapsed').count() === 1 &&
    await page.locator('[data-steel-act="mic"]:visible').count() > 0);
  await openDock(page);
  check(label + ' manual expansion reveals the text entry', await page.locator('#steel-say').isVisible());
  await userPageScroll(page, 180);
  await panel.waitFor({state: 'hidden'});
  check(label + ' ordinary page scrolling collapses the recorder',
    await toggle.getAttribute('aria-expanded') === 'false');
  await assertNoOverflow(page, label + ' compact recorder');
}

async function assertAutomaticManualSync(page, label) {
  const samples = [
    {key:'document_no', text:'零零一二三', value:'00123', enter:true},
    {key:'inspection_date', text:'二零二六年十一月十日', value:'2026-11-10'},
    {key:'grade', text:'Q二三五B', value:'Q235B'},
    {key:'thickness_lower', text:'负零点三', value:-0.3},
    {key:'thickness', text:'五点六四，五点六八', value:[5.64,5.68]},
    {key:'extra_0_verdict', text:'不合格', value:'不合格'},
    {key:'extra_0_verdict', text:'合格', value:'合格'},
    {key:'extra_0_result', text:'六千零一毫米', value:'六千零一毫米'},
  ];
  for (const sample of samples) {
    await expandDetails(page);
    await editor(page, sample.key).focus();
    await openDock(page);
    const input = page.locator('#steel-say');
    await input.fill(sample.text);
    if (sample.enter) await input.press('Enter');
    await waitForFormalValue(page, sample.key, sample.value);
    const state = await currentDraft(page);
    check(label + ' completed dock typing automatically syncs ' + sample.key + ' without clicking synchronize',
      !(state.data.voice_pending || []).includes(sample.key) &&
      !(state.data.voice_candidates || []).some((item) => item.key === sample.key));
    if (sample.key === 'thickness') {
      check(label + ' typed ' + sample.key + ' is explicitly manual and confirmed',
        state.data.values[sample.key].source === 'manual' && state.data.values[sample.key].confirmed === true,
        state.data.values[sample.key]);
    }
  }
  await editor(page, 'extra_0_verdict').focus(); await openDock(page);
  const invalidResponse = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/steel/asr/parse' &&
    response.request().method() === 'POST' && response.request().postDataJSON().text === '不，合格');
  await page.locator('#steel-say').fill('不，合格');
  await invalidResponse;
  await page.waitForFunction(() => !Steel.syncing && Steel.textError.startsWith('未同步'));
  check(label + ' an ambiguous negative dock phrase is rejected without changing the inspection verdict',
    (await currentDraft(page)).data.extra_rows[0].verdict === '合格');
  await editor(page, 'grade').focus(); await openDock(page);
  const input = page.locator('#steel-say');
  const previous = (await currentDraft(page)).data.grade;
  await input.dispatchEvent('compositionstart');
  await input.fill('Q三四五B');
  await page.waitForTimeout(1000);
  check(label + ' an unfinished Chinese IME composition does not synchronize',
    (await currentDraft(page)).data.grade === previous);
  await input.dispatchEvent('compositionend');
  await waitForFormalValue(page, 'grade', 'Q345B');
  check(label + ' completed Chinese IME composition automatically synchronizes', true);
  await closeDock(page);
}

async function assertHistoricalBatchReview(page, label) {
  await page.evaluate(async () => {
    Steel.data.voice_candidates.push({key:'sampling_basis', value:'历史录入的抽样依据', confidence:0.2,
      source:'asr', confirmed:false, raw:'历史录入的抽样依据', destination:'header'});
    Steel.data.voice_pending.push('sampling_basis');
    steelChanged(); await steelEvaluate(); await render();
  });
  const apply = page.locator('[data-steel-act="voice-apply"][data-key="sampling_basis"]');
  await apply.waitFor({state:'visible'});
  check(label + ' an older batch-section candidate remains reviewable without reopening voice entry',
    await page.locator('[data-steel-voice-target="sampling_basis"]').count() === 0 &&
    await editor(page, 'sampling_basis').inputValue() === '历史录入的抽样依据');
  await apply.click();
  await page.locator('[data-voice-candidate="sampling_basis"]').waitFor({state:'detached'});
  const state = await currentDraft(page);
  check(label + ' confirming an older batch candidate clears its legacy review gate',
    state.data.sampling_basis === '历史录入的抽样依据' && !state.data.voice_pending.includes('sampling_basis'));
}

async function assertPendingBlocksFinal(page, context, label, key) {
  await openDock(page);
  const save = page.locator('[data-steel-act="save"]:visible').first();
  const id = (await currentDraft(page)).id;
  const [response] = await Promise.all([
    page.waitForResponse((res) => new URL(res.url()).pathname === '/api/steel/records/' + id &&
      res.request().method() === 'PUT'),
    save.click(),
  ]);
  const saved = await bodyOf(response);
  requireCheck(label + ' unconfirmed candidate can be saved as draft', saved.status === 200, saved);
  await page.waitForFunction(({id, version}) => Steel.record && Steel.record.id === id &&
    !Steel.saving && Steel.record.version >= version, {id, version: saved.body.record.version});
  await page.locator('[data-voice-candidate="' + key + '"]').waitFor({state: 'visible'});
  const state = await currentDraft(page);
  const pending = state.data.voice_pending || [];
  check(label + ' candidate remains pending after save',
    pending.some((item) => (typeof item === 'string' ? item : item.key) === key), pending);
  const [finalizeResponse] = await Promise.all([
    page.waitForResponse((res)=>new URL(res.url()).pathname==='/api/steel/records/'+id+'/finalize'),
    page.locator('.steel-submit-actions [data-steel-act="finalize"]').tap(),
  ]);
  const result = await bodyOf(finalizeResponse);
  check(label + ' server blocks finalization until voice review', result.status === 400, result);
  await page.locator('.steel-submit-error').waitFor();
  await page.reload();
  await page.locator('[data-voice-candidate="' + key + '"]').waitFor({state: 'visible'});
  check(label + ' pending voice review survives page reload', true);
}

async function assertVoiceReview(page, context, label) {
  for (const sample of VOICE_CASES) {
    await expandDetails(page);
    const before = await currentDraft(page);
    const response = await bodyOf(await context.request.post('/api/steel/asr/parse',
      {data: {text: sample.text, current_key: sample.key}}));
    requireCheck(label + ' parse ASR candidate ' + sample.key, response.status === 200, response);
    await page.evaluate(async ({result, text}) => {
      await steelApplyVoice(Object.assign({}, result, {text, record_id: Steel.record.id, edit_revision: Steel.revision}));
    }, {result: response.body, text: sample.text});
    const card = page.locator('.steel-voice-review [data-voice-candidate="' + sample.key + '"], ' +
      '.steel-voice-review[data-voice-candidate="' + sample.key + '"]');
    await card.waitFor({state: 'visible'});
    const candidate = page.locator('[data-steel-candidate="' + sample.key + '"]');
    check(label + ' ' + sample.key + ' normalized editable candidate',
      await candidate.inputValue() === sample.expected, await candidate.inputValue());
    const after = await currentDraft(page);
    const formal = (data) => Object.fromEntries(Object.entries(data).filter(([key]) =>
      !['voice_candidates', 'voice_pending', 'evidence'].includes(key)));
    check(label + ' ' + sample.key + ' ASR preview is visible without silently replacing confirmed data',
      await editor(page, sample.key).inputValue() === sample.expected &&
      JSON.stringify(formal(after.data)) === JSON.stringify(formal(before.data)));
    if (sample.key === 'document_no') {
      await screenshot(page, 'steel-review-' + label + '.png');
      await assertPendingBlocksFinal(page, context, label, sample.key);
    }
    const apply = page.locator('[data-steel-act="voice-apply"][data-key="' + sample.key + '"]');
    const box = await hitTest(page, apply);
    check(label + ' ' + sample.key + ' candidate confirmation touch target',
      box.clear && box.height >= 43.5 && box.width >= 43.5, box);
    await apply.click();
    await page.locator('[data-voice-candidate="' + sample.key + '"]').waitFor({state: 'detached'});
    await expandDetails(page);
    check(label + ' ' + sample.key + ' review confirmation writes selected field',
      await editor(page, sample.key).inputValue() === sample.expected,
      await editor(page, sample.key).inputValue());
  }
}

async function addParsedCandidates(page, samples) {
  return page.evaluate(async (items) => {
    const assignments = [];
    for (const item of items) {
      const result = await api('/api/steel/asr/parse', {method: 'POST',
        body: {text: item.text, current_key: item.key}});
      assignments.push(...result.assignments);
    }
    await steelApplyVoice({record_id: Steel.record.id, edit_revision: Steel.revision,
      text: items.map((item) => item.text).join('；'), assignments});
    return assignments.map((item) => item.key);
  }, samples);
}

async function assertFiveCandidateReview(page, label) {
  const samples = [
    {key: 'thickness_lower', text: '负零点三'},
    {key: 'thickness_upper', text: '零点三'},
    {key: 'width_lower', text: '负零点五'},
    {key: 'width_upper', text: '三十'},
    {key: 'thickness', text: '厚度五点六四，厚度五点六八'},
  ];
  const keys = await addParsedCandidates(page, samples);
  requireCheck(label + ' five simultaneous review candidates come from typed backend parser',
    keys.length === 5 && new Set(keys).size === 5, keys);
  const review = page.locator('.steel-voice-review');
  await review.waitFor({state: 'visible'});
  const geometry = await review.evaluate((el) => {
    const style = getComputedStyle(el);
    return {count: el.querySelectorAll('[data-voice-candidate]').length,
      height: el.getBoundingClientRect().height, viewport: innerHeight,
      maxHeight: style.maxHeight, overflowY: style.overflowY,
      scrollHeight: el.scrollHeight, clientHeight: el.clientHeight,
      internalScrollContainers: Array.from(el.querySelectorAll('*')).filter((child) => {
        const childStyle = getComputedStyle(child);
        return ['auto', 'scroll'].includes(childStyle.overflowY) && child.scrollHeight > child.clientHeight + 1;
      }).map((child) => child.className),
      actionPositions: Array.from(el.querySelectorAll('.steel-voice-review-actions'))
        .map((actions) => getComputedStyle(actions).position)};
  });
  check(label + ' five-candidate review uses document scrolling without sticky action layers',
    geometry.count === 5 && geometry.height > geometry.viewport && geometry.maxHeight === 'none' &&
    !['auto', 'scroll'].includes(geometry.overflowY) && geometry.scrollHeight <= geometry.clientHeight + 1 &&
    geometry.internalScrollContainers.length === 0 &&
    geometry.actionPositions.every((position) => !['sticky', 'fixed'].includes(position)), geometry);
  await assertTextKeyboards(page, label, keys);
  // Swap one signed candidate for width to exercise both multiple-reading editors
  // while retaining a five-card review, rather than testing a short single card.
  await page.locator('[data-steel-act="voice-discard"][data-key="width_upper"]').click();
  await page.locator('[data-voice-candidate="width_upper"]').waitFor({state: 'detached'});
  const widthKeys = await addParsedCandidates(page, [{key: 'width', text: '宽度一千八百一十一，宽度一千八百一十二'}]);
  requireCheck(label + ' multiple width readings create editable candidate', widthKeys.includes('width'), widthKeys);
  await assertTextKeyboards(page, label, ['width']);
  await review.evaluate((el) => el.scrollIntoView({block: 'start'}));
  const scrollBefore = await page.evaluate(() => scrollY);
  const lastApply = review.locator('[data-steel-act="voice-apply"]').last();
  const hit = await hitTest(page, lastApply);
  const scrollAfter = await review.evaluate((el) => ({windowY: scrollY, reviewY: el.scrollTop,
    count: el.querySelectorAll('[data-voice-candidate]').length}));
  check(label + ' last of five confirmations is reachable by normal document scroll', hit.clear &&
    hit.width >= 43.5 && hit.height >= 43.5 && scrollAfter.count === 5 &&
    scrollAfter.reviewY === 0 && scrollAfter.windowY > scrollBefore + 20, {scrollBefore, scrollAfter, hit});
  await screenshot(page, 'steel-review-five-' + label + '.png');
  await lastApply.click();
  await page.locator('[data-voice-candidate="width"]').waitFor({state: 'detached'});
  check(label + ' last confirmation applies both width samples',
    await editor(page, 'width').inputValue() === '1811、1812', await editor(page, 'width').inputValue());
  // Discard remaining candidates through their actual controls before later races.
  for (const key of keys.filter((key) => key !== 'width_upper')) {
    const discard = page.locator('[data-steel-act="voice-discard"][data-key="' + key + '"]');
    if (await discard.count()) {
      await discard.click();
      await page.locator('[data-voice-candidate="' + key + '"]').waitFor({state: 'detached'});
    }
  }
}

async function holdResponse(page, url, method) {
  let release, arrive, rejectArrival;
  const gate = new Promise((resolve) => { release = resolve; });
  const arrival = new Promise((resolve, reject) => { arrive = resolve; rejectArrival = reject; });
  let used = false;
  const handler = async (route) => {
    if (used || route.request().method() !== method) return route.continue();
    used = true;
    try {
      const upstream = await route.fetch();
      const captured = await bodyOf(upstream);
      arrive(captured);
      await gate;
      await route.fulfill({response: upstream});
    } catch (error) {
      rejectArrival(error);
      try { await route.abort(); } catch (_) { /* request already ended */ }
    }
  };
  await page.route(url, handler);
  return {release,
    async received() {
      let timer;
      try {
        return await Promise.race([arrival, new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error('Expected controlled request was not made: ' + url)), 12000);
        })]);
      } finally { clearTimeout(timer); }
    },
    async dispose() { release(); await page.unroute(url, handler); },
  };
}

async function assertTapActivationAndCancel(page, label) {
  await page.evaluate(() => {
    const race = {originalMedia: navigator.mediaDevices.getUserMedia, originalContext: window.AudioContext,
      originalRecordVoice: steelRecordVoice, trustedClick: false, activeAtEntry: false,
      inClick: false, mediaInClick: false, resumeInClick: false, calls: 0, closes: 0, stops: 0};
    globalThis.__steelTapActivation = race;
    race.onClick = (event) => {
      const button = event.target.closest('[data-steel-act="voice-target"][data-key="grade"]');
      if (!button) return;
      race.trustedClick = event.isTrusted;
    };
    document.addEventListener('click', race.onClick, true);
    steelRecordVoice = function CheckedRecordVoice(...args) {
      race.inClick = true;
      race.activeAtEntry = navigator.userActivation.isActive;
      // Set this at the business handler's entry: browsers may checkpoint
      // microtasks between capture and bubble event listeners themselves.
      queueMicrotask(() => { race.inClick = false; });
      const pending = race.originalRecordVoice.apply(this, args);
      race.expandedAtEntry = Steel.dockExpanded;
      return pending;
    };
    navigator.mediaDevices.getUserMedia = () => {
      race.calls++; race.mediaInClick = race.inClick;
      return new Promise((resolve) => { race.release = () => resolve({
        getAudioTracks: () => [{getSettings: () => ({})}],
        getTracks: () => [{stop: () => { race.stops++; }}],
      }); });
    };
    window.AudioContext = function TapContext() {
      this.state = 'suspended'; this.sampleRate = 48000; this.destination = {};
      this.resume = () => {
        race.resumeInClick = race.inClick; this.state = 'running'; return Promise.resolve();
      };
      this.close = async () => { race.closes++; this.state = 'closed'; };
    };
  });
  try {
    await page.locator('[data-steel-act="voice-target"][data-key="grade"]').click();
    await page.waitForFunction(() => REC.starting && Steel.starting && globalThis.__steelTapActivation.calls === 1);
    const started = await page.evaluate(() => ({media: __steelTapActivation.mediaInClick,
      resume: __steelTapActivation.resumeInClick, trusted: __steelTapActivation.trustedClick,
      active: __steelTapActivation.activeAtEntry, expanded: __steelTapActivation.expandedAtEntry, phase: REC.phase}));
    check(label + ' real tap starts permission and audio activation before the first await',
      started.media && started.resume && started.trusted && started.active && !started.expanded && started.phase === 'permission', started);
    await page.waitForTimeout(180);
    check(label + ' opening the microphone keeps a compact cancel control without expanding tools',
      !await page.locator('#steel-capture-panel').isVisible() &&
      await page.locator('[data-steel-act="dock-toggle"]').getAttribute('aria-expanded') === 'false');
    await assertSafetyCard(page,label+' pending',false);
    const cancel = page.locator('[data-steel-act="mic-cancel"]');
    await cancel.waitFor({state: 'visible'});
    check(label + ' unresolved browser permission keeps an enabled cancel control', await cancel.isEnabled());
    await screenshot(page, 'steel-microphone-permission-' + label + '.png');
    await page.locator('#steel-recorder-safety [data-steel-act="safety-cancel"]').tap();
    await page.waitForFunction(() => !REC.starting && !Steel.starting && REC.session === null);
    check(label+' canceling through the top card removes the temporary recorder overlay',
      !await page.locator('#steel-recorder-safety').isVisible());
    const result = await page.evaluate(async () => {
      __steelTapActivation.release();
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
      return {stops: __steelTapActivation.stops, closes: __steelTapActivation.closes,
        recording: S.recording, starting: REC.starting, phase: REC.phase, stream: REC.stream};
    });
    check(label + ' cancel settles an unanswered request and stops its late stream', result.stops === 1 &&
      result.closes === 1 && !result.recording && !result.starting && result.phase === 'idle' && result.stream === null, result);
  } finally {
    await page.evaluate(async () => {
      const race = globalThis.__steelTapActivation;
      if (!race) return;
      if (REC.starting || S.recording) { Steel.micAttempt++; Steel.starting = false; await cancelRec(); }
      document.removeEventListener('click', race.onClick, true);
      steelRecordVoice = race.originalRecordVoice;
      navigator.mediaDevices.getUserMedia = race.originalMedia;
      window.AudioContext = race.originalContext;
      delete globalThis.__steelTapActivation;
      await render();
    });
  }
}

async function nativeTouchScroll(page) {
  await page.evaluate(()=>{
    const paragraph=Array.from(document.querySelectorAll('.steel-check p'))
      .sort((a,b)=>b.getBoundingClientRect().height-a.getBoundingClientRect().height)[0];
    const dock=document.getElementById('dock').getBoundingClientRect();
    const topbar=document.getElementById('topbar').getBoundingClientRect();
    const desired=(topbar.bottom+dock.top)/2;
    const rect=paragraph.getBoundingClientRect();
    scrollBy(0,rect.top+rect.height/2-desired);
  });
  await page.waitForTimeout(100);
  const before=await page.evaluate(()=>{
    const dock=document.getElementById('dock').getBoundingClientRect();
    const topbar=document.getElementById('topbar').getBoundingClientRect();
    const describe=(element)=>element ? {tag:element.tagName,id:element.id,cls:element.className,
      dock:Boolean(element.closest('#dock')),interactive:Boolean(element.closest('input,select,textarea,button,a,summary,label'))} : null;
    const paragraph=Array.from(document.querySelectorAll('.steel-check p'))
      .sort((a,b)=>b.getBoundingClientRect().height-a.getBoundingClientRect().height)[0];
    const rect=paragraph.getBoundingClientRect();
    const y=Math.min(dock.top-30,Math.max(topbar.bottom+30,rect.top+rect.height/2));
    const candidates=[innerWidth/2,innerWidth/3,20].map((x)=>({x,y,element:describe(document.elementFromPoint(x,y))}));
    const point=candidates.find((item)=>item.element && !item.element.dock && !item.element.interactive) || candidates[0];
    return {point,candidates,scroll:scrollY,maxScroll:document.documentElement.scrollHeight-innerHeight,
      starting:Steel.starting,recording:S.recording,phase:REC.phase,expanded:Steel.dockExpanded,
      baseline:steelScrollPosition,intentUntil:steelScrollIntentUntil,now:Date.now()};
  });
  const session = await page.context().newCDPSession(page);
  try {
    const direction=before.maxScroll-before.scroll>170 ? -1 : 1;
    await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:before.point.x,y:before.point.y}]});
    for(let step=1;step<=8;step++){
      await session.send('Input.dispatchTouchEvent',{type:'touchMove',
        touchPoints:[{x:before.point.x,y:before.point.y+direction*step*15}]});
      await page.waitForTimeout(35);
    }
    await page.waitForTimeout(120);
    await session.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  } finally { await session.detach(); }
  await page.waitForTimeout(120);
  const after=await page.evaluate(()=>({scroll:scrollY,starting:Steel.starting,recording:S.recording,
    phase:REC.phase,expanded:Steel.dockExpanded,baseline:steelScrollPosition,
    intentUntil:steelScrollIntentUntil,now:Date.now()}));
  const gesture={before,after,delta:after.scroll-before.scroll};
  await page.evaluate((value)=>{if(globalThis.__steelTouchPhases)__steelTouchPhases.gestures.push(value);},gesture);
  return gesture;
}

async function assertTouchKeyboardAndPermissionPhases(page, label) {
  await editor(page, 'grade').focus();
  await page.setViewportSize({width:Number(label), height:480});
  await page.evaluate(() => {
    const race = {originalMedia:navigator.mediaDevices.getUserMedia, originalContext:window.AudioContext,
      events:[], gestures:[], stops:0, closes:0, panelBeforeAPI:null};
    globalThis.__steelTouchPhases=race;
    race.trace=(event)=>race.events.push({type:event.type, trusted:event.isTrusted, expanded:Steel.dockExpanded,
      phase:REC.phase, starting:Steel.starting,recording:S.recording,scroll:scrollY,height:innerHeight,
      baseline:steelScrollPosition,intentUntil:steelScrollIntentUntil,now:Date.now(),
      target:event.target && {tag:event.target.tagName,id:event.target.id,cls:event.target.className,
        dock:Boolean(event.target.closest && event.target.closest('#dock'))}});
    ['touchstart','touchmove','touchend','focusout'].forEach((name)=>document.addEventListener(name,race.trace,true));
    ['scroll','resize'].forEach((name)=>window.addEventListener(name,race.trace));
    navigator.mediaDevices.getUserMedia=()=>{
      const panel=document.getElementById('steel-capture-panel');
      const pill=document.getElementById('steel-recorder-safety');
      race.panelBeforeAPI={exists:Boolean(panel), hidden:panel ? panel.hidden : null,
        height:panel ? panel.getBoundingClientRect().height : 0,
        expanded:document.querySelector('[data-steel-act="dock-toggle"]').getAttribute('aria-expanded'),
        pillVisible:Boolean(pill && !pill.hidden && pill.getBoundingClientRect().height>0)};
      return new Promise((resolve)=>{race.allow=()=>resolve({
        getAudioTracks:()=>[{getSettings:()=>({sampleRate:48000})}],
        getTracks:()=>[{stop:()=>{race.stops++;}}]});});
    };
    window.AudioContext=function TouchAudioContext(){
      this.state='suspended';this.sampleRate=48000;this.destination={};
      this.resume=()=>new Promise((resolve)=>{race.activate=()=>{if(this.state!=='closed')this.state='running';resolve();};});
      this.close=async()=>{race.closes++;this.state='closed';};
      this.createMediaStreamSource=()=>({connect(){}});
      this.createScriptProcessor=()=>({connect(){},disconnect(){}});
      this.createGain=()=>({gain:{},connect(){}});
    };
  });
  try {
    await page.locator('[data-steel-act="voice-target"][data-key="grade"]').tap();
    await page.waitForFunction(()=>Steel.starting && REC.phase==='permission' && __steelTouchPhases.panelBeforeAPI);
    const beforeAPI=await page.evaluate(()=>__steelTouchPhases.panelBeforeAPI);
    check(label+' a compact recorder is visible synchronously before requesting the browser microphone',
      beforeAPI.exists && beforeAPI.hidden && beforeAPI.height===0 && beforeAPI.expanded==='false' && beforeAPI.pillVisible,beforeAPI);
    await page.setViewportSize({width:Number(label),height:844});
    await page.waitForTimeout(180);
    check(label+' dismissing a simulated Android keyboard keeps the compact pending recorder',
      await page.locator('#steel-recorder-safety').isVisible() && !await page.locator('#steel-capture-panel').isVisible());
    const pendingGesture=await nativeTouchScroll(page);
    requireCheck(label+' pending-phase native touch gesture actually scrolls the page',Math.abs(pendingGesture.delta)>16,pendingGesture);
    check(label+' a native touch scroll while permission is pending keeps its cancel panel visible',
      await page.locator('#steel-recorder-safety').isVisible() &&
      await page.locator('[data-steel-act="mic-cancel"]').isVisible());
    await page.evaluate(()=>__steelTouchPhases.allow());
    await page.waitForFunction(()=>Steel.starting && REC.phase==='audio');
    await page.setViewportSize({width:Number(label),height:520});
    await page.setViewportSize({width:Number(label),height:844});
    await page.waitForTimeout(180);
    check(label+' permission-to-audio phase and viewport resize retain a compact recorder',
      await page.locator('#steel-recorder-safety').isVisible() && !await page.locator('#steel-capture-panel').isVisible() &&
      await page.locator('[data-steel-act="dock-toggle"]').getAttribute('aria-expanded')==='false');
    await page.evaluate(()=>__steelTouchPhases.activate());
    await page.waitForFunction(()=>S.recording && REC.phase==='recording' && !Steel.starting);
    check(label+' active recording leaves the large tool panel collapsed',
      !await page.locator('#steel-capture-panel').isVisible() &&
      await page.locator('[data-steel-act="dock-toggle"]').getAttribute('aria-expanded')==='false');
    await assertSafetyCard(page,label+' active',true);
    await screenshot(page,'steel-touch-recording-'+label+'.png');
    const recordingGesture=await nativeTouchScroll(page);
    requireCheck(label+' recording-phase native touch gesture actually scrolls the page',Math.abs(recordingGesture.delta)>16,recordingGesture);
    await page.locator('#steel-capture-panel').waitFor({state:'hidden'});
    const stop=page.locator('.steel-dock-peek [data-steel-act="mic"]');
    check(label+' after recording starts a native touch scroll can collapse tools while Stop remains usable',
      await page.evaluate(()=>S.recording) && await stop.getAttribute('aria-label')==='停止录音' && await stop.isEnabled());
    await page.locator('#steel-recorder-safety [data-steel-act="safety-stop"]').tap();
    await page.waitForFunction(()=>!S.recording && !Steel.waiting);
    check(label+' the independent top Stop action ends capture and removes its overlay',
      !await page.locator('#steel-recorder-safety').isVisible());
    const result=await page.evaluate(()=>({events:__steelTouchPhases.events,
      stops:__steelTouchPhases.stops, closes:__steelTouchPhases.closes}));
    check(label+' actual trusted touch events cover recorder opening and scrolling',
      result.events.some((event)=>event.type==='touchstart' && event.trusted) &&
      result.events.some((event)=>event.type==='touchmove' && event.trusted) && result.stops===1 && result.closes===1,result);
  } finally {
    const trace=await page.evaluate(()=>{
      const race=globalThis.__steelTouchPhases;
      return race ? {events:race.events,gestures:race.gestures,panelBeforeAPI:race.panelBeforeAPI,
        stops:race.stops,closes:race.closes,starting:Steel.starting,recording:S.recording,
        phase:REC.phase,expanded:Steel.dockExpanded,baseline:steelScrollPosition,
        intentUntil:steelScrollIntentUntil,now:Date.now()} : null;
    }).catch((error)=>({trace_error:error.message}));
    report.touch_startups.push({width:Number(label),...trace});
    await page.evaluate(async()=>{
      const race=globalThis.__steelTouchPhases;if(!race)return;
      if(REC.starting || S.recording){Steel.micAttempt++;Steel.starting=false;await cancelRec();}
      ['touchstart','touchmove','touchend','focusout'].forEach((name)=>document.removeEventListener(name,race.trace,true));
      ['scroll','resize'].forEach((name)=>window.removeEventListener(name,race.trace));
      navigator.mediaDevices.getUserMedia=race.originalMedia;window.AudioContext=race.originalContext;
      delete globalThis.__steelTouchPhases;S.micProblem=null;Steel.dockExpanded=false;await render();
    });
    await page.setViewportSize({width:Number(label),height:844});
  }
}

async function assertPermissionTimeoutKeepsPanel(page,label) {
  await page.evaluate(()=>{
    const race={originalMedia:navigator.mediaDevices.getUserMedia, originalContext:window.AudioContext};
    globalThis.__steelTimeoutPanel=race;
    navigator.mediaDevices.getUserMedia=()=>new Promise(()=>{});
    window.AudioContext=function TimeoutContext(){this.state='running';this.close=async()=>{this.state='closed';};};
  });
  try {
    await page.locator('.steel-dock-peek [data-steel-act="mic"]').tap();
    await page.waitForFunction(()=>!Steel.starting && S.micProblem && S.micProblem.code==='permission-timeout',null,{timeout:17000});
    check(label+' unanswered microphone permission ends with its error and recovery controls still expanded',
      await page.locator('#steel-capture-panel').isVisible() && await page.locator('.steel-mic-problem').isVisible() &&
      await page.locator('[data-steel-act="dock-toggle"]').getAttribute('aria-expanded')==='true' &&
      await page.locator('[data-steel-act="mic-retry"]').isVisible());
    await screenshot(page,'steel-permission-timeout-'+label+'.png');
  } finally {
    await page.evaluate(async()=>{
      const race=globalThis.__steelTimeoutPanel;if(!race)return;
      if(REC.starting || S.recording){Steel.micAttempt++;Steel.starting=false;await cancelRec();}
      navigator.mediaDevices.getUserMedia=race.originalMedia;window.AudioContext=race.originalContext;
      delete globalThis.__steelTimeoutPanel;S.micProblem=null;Steel.dockExpanded=false;await render();
    });
  }
}

async function assertImportFallbackLayout(page, label) {
  await page.locator('[data-steel-act="voice-target"][data-key="grade"]').tap();
  await page.waitForFunction(() => !Steel.starting && !REC.starting && S.micProblem && S.micProblem.code === 'NotAllowedError');
  check(label + ' permission denial keeps the error panel expanded without a second user action',
    await page.locator('#steel-capture-panel').isVisible() &&
    await page.locator('[data-steel-act="dock-toggle"]').getAttribute('aria-expanded') === 'true' &&
    await page.locator('.steel-mic-problem').isVisible());
  const metrics = await page.locator('[data-steel-act="audio-import"]:visible').evaluateAll((buttons) => buttons.map((button) => {
    const box = button.getBoundingClientRect();
    return {width: box.width, height: box.height, enabled: !button.disabled, label: button.textContent.trim()};
  }));
  check(label + ' permission failure exposes named enabled system-recorder controls of at least 44px',
    metrics.length >= 2 && metrics.every((item) => item.enabled && item.width >= 43.5 && item.height >= 43.5 && item.label), metrics);
  await assertNoOverflow(page, label + ' permission banner and system-recorder controls');
  await screenshot(page, 'steel-microphone-fallback-' + label + '.png');
  await page.evaluate(async () => { S.micProblem = null; await render(); });
  await closeDock(page);
}

function stereo48kWav() {
  const sampleRate = 48000, frames = 36000, channels = 2;
  const bytes = Buffer.alloc(44 + frames * channels * 2);
  bytes.write('RIFF', 0); bytes.writeUInt32LE(bytes.length - 8, 4); bytes.write('WAVEfmt ', 8);
  bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20); bytes.writeUInt16LE(channels, 22);
  bytes.writeUInt32LE(sampleRate, 24); bytes.writeUInt32LE(sampleRate * channels * 2, 28);
  bytes.writeUInt16LE(channels * 2, 32); bytes.writeUInt16LE(16, 34);
  bytes.write('data', 36); bytes.writeUInt32LE(bytes.length - 44, 40);
  for (let frame = 0; frame < frames; frame++) {
    const envelope = Math.min(1, frame / 480, (frames - frame) / 480);
    bytes.writeInt16LE(Math.round(Math.sin(2 * Math.PI * 440 * frame / sampleRate) * 6000 * envelope), 44 + frame * 4);
    bytes.writeInt16LE(Math.round(Math.sin(2 * Math.PI * 660 * frame / sampleRate) * 4000 * envelope), 46 + frame * 4);
  }
  return bytes;
}

async function chooseImportFile(page, payload) {
  await openDock(page);
  const button = page.locator('[data-steel-act="audio-import"]:visible').last();
  await Promise.all([page.waitForEvent('filechooser'), button.click()]);
  await page.setInputFiles('#steel-audio-file', payload);
}

async function assertSystemRecorderImport(page, label, uploadGuard) {
  await editor(page, 'grade').click();
  await openDock(page);
  const before = await currentDraft(page);
  await page.evaluate(() => {
    const race = {originalMedia: navigator.mediaDevices.getUserMedia, originalContext: window.AudioContext,
      originalWebkitContext: window.webkitAudioContext, media: 0, contexts: 0};
    globalThis.__steelAudioImportTest = race;
    navigator.mediaDevices.getUserMedia = () => { race.media++; throw new Error('Import must not request the browser microphone'); };
    const forbidden = function ForbiddenAudioContext() { race.contexts++; throw new Error('Import must not activate a realtime AudioContext'); };
    window.AudioContext = forbidden; window.webkitAudioContext = forbidden;
  });
  try {
    uploadGuard.authorized = {record_id: before.id, current_key: 'grade'};
    const [chooser] = await Promise.all([page.waitForEvent('filechooser'),
      page.locator('[data-steel-act="audio-import"]:visible').last().click()]);
    check(label + ' recorder picker freezes its original record and selected field',
      await page.evaluate((id) => SteelAudio.selection && SteelAudio.selection.record_id === id &&
        SteelAudio.selection.current_key === 'grade', before.id));
    // A later focus/current-field change is distinct from editing the record.
    await page.evaluate(() => { Steel.current = 'manufacturer'; });
    const responsePromise = page.waitForResponse((response) =>
      new URL(response.url()).pathname === '/api/steel/asr/audio' && response.request().method() === 'POST', {timeout: 70000});
    await page.setInputFiles('#steel-audio-file', {name: 'system-recorder-stereo-48k.wav', mimeType: 'audio/wav', buffer: stereo48kWav()});
    const response = await responsePromise;
    const result = await bodyOf(response);
    requireCheck(label + ' imported stereo recording reaches the real isolated audio endpoint', result.status === 200, result);
    await page.waitForFunction(() => !Steel.waiting && SteelAudio.phase === 'idle' && SteelAudio.active === null, null, {timeout: 70000});
    const after = await currentDraft(page);
    const protectedData = (data) => Object.fromEntries(Object.entries(data).filter(([key]) => !['voice_candidates', 'voice_pending', 'evidence'].includes(key)));
    check(label + ' imported audio cannot silently change confirmed inspection fields',
      JSON.stringify(protectedData(after.data)) === JSON.stringify(protectedData(before.data)));
    const expected = report.authorized_audio_imports.filter((item) => item.width === Number(label));
    requireCheck(label + ' import performs exactly one authorized audio upload', expected.length === 1, expected);
    const request = expected[0];
    check(label + ' file is resampled and mixed into a 16k mono PCM WAV before upload',
      request.riff === 'RIFF' && request.wave === 'WAVE' && request.format === 1 &&
      request.channels === 1 && request.sample_rate === 16000 && request.bits === 16 &&
      request.data_bytes >= 23000 && request.data_bytes <= 25000, request);
    check(label + ' import upload retains picker record and field despite a later selection change',
      request.record_id === String(before.id) && request.current_key === 'grade', request);
    const calls = await page.evaluate(() => ({media: __steelAudioImportTest.media, contexts: __steelAudioImportTest.contexts}));
    check(label + ' system-recorder import never asks for browser microphone or realtime audio activation',
      calls.media === 0 && calls.contexts === 0, calls);

    // Bad files must recover to usable controls without sending any audio.
    await chooseImportFile(page, {name: 'broken-recorder.wav', mimeType: 'audio/wav', buffer: Buffer.from('invalid audio fixture')});
    await page.waitForFunction(() => !Steel.waiting && SteelAudio.phase === 'idle' && S.micProblem && S.micProblem.code === 'audio-import');
    check(label + ' an undecodable recording releases the import lock with a persistent error',
      await page.evaluate(() => SteelAudio.active === null && !Steel.waiting && Boolean(S.micProblem.message)));
    check(label + ' invalid file does not produce a second audio request',
      report.authorized_audio_imports.filter((item) => item.width === Number(label)).length === 1);
    await assertNoOverflow(page, label + ' invalid-file banner and import fallback');
    await screenshot(page, 'steel-audio-import-error-' + label + '.png');
  } finally {
    uploadGuard.authorized = null;
    await page.evaluate(async () => {
      const race = globalThis.__steelAudioImportTest;
      if (!race) return;
      if (SteelAudio.active) steelCancelAudio();
      navigator.mediaDevices.getUserMedia = race.originalMedia;
      window.AudioContext = race.originalContext; window.webkitAudioContext = race.originalWebkitContext;
      delete globalThis.__steelAudioImportTest;
      S.micProblem = null; Steel.current = 'grade'; await render();
    });
  }
}

async function assertFieldStopAndRecognitionLock(page,label) {
  await editor(page,'grade').focus();
  await closeDock(page);
  await page.evaluate(()=>{
    const race={originalMedia:navigator.mediaDevices.getUserMedia,originalContext:window.AudioContext,
      originalUpload:uploadWav,calls:0,stops:0,closes:0,uploaded:null,finish:null};
    globalThis.__steelFieldStop=race;
    navigator.mediaDevices.getUserMedia=async()=>{
      race.calls++;
      return {getAudioTracks:()=>[{getSettings:()=>({sampleRate:48000})}],
        getTracks:()=>[{stop:()=>{race.stops++;}}]};
    };
    window.AudioContext=function FieldContext(){
      this.state='running';this.sampleRate=48000;this.destination={};
      this.createMediaStreamSource=()=>({connect(){}});
      this.createScriptProcessor=()=>({connect(){},disconnect(){}});
      this.createGain=()=>({gain:{},connect(){}});
      this.close=async()=>{race.closes++;this.state='closed';};
    };
    uploadWav=async(blob,url,extra)=>{
      race.uploaded={size:blob.size,url,extra};
      return new Promise((resolve)=>{race.finish=resolve;});
    };
  });
  try {
    const field=page.locator('[data-steel-voice-target="grade"]');
    await field.tap();
    await page.waitForFunction(()=>S.recording && !Steel.starting);
    await field.scrollIntoViewIfNeeded();
    await page.waitForFunction(()=>!document.getElementById('steel-recorder-safety').getBoundingClientRect().height);
    const active=await field.evaluate((button)=>({disabled:button.disabled,pressed:button.getAttribute('aria-pressed'),
      label:button.getAttribute('aria-label'),classes:button.className,background:getComputedStyle(button).backgroundColor}));
    check(label+' active field turns into a red enabled stop control without opening a large panel',
      !active.disabled && active.pressed==='true' && /停止/.test(active.label) && /\b(?:rec|recording)\b/.test(active.classes) &&
      !await page.locator('#steel-capture-panel').isVisible(),active);
    check(label+' a visible active field hides the redundant floating stop pill',
      !await page.locator('#steel-recorder-safety').isVisible());
    await assertNoLegacyRecordingToast(page,label+' active');
    await screenshot(page,'steel-active-field-'+label+'.png');
    await page.evaluate(()=>{
      const samples=Float32Array.from({length:36000},(_,index)=>0.2*Math.sin(index*0.08));
      REC.proc.onaudioprocess({inputBuffer:{getChannelData:()=>samples}});
      REC.t0=Date.now()-800;
    });
    await field.tap();
    await page.waitForFunction(()=>__steelFieldStop.uploaded && Steel.waiting && !S.recording);
    const processing=await page.locator('[data-steel-voice-target]').evaluateAll((buttons)=>buttons.map((button)=>({
      key:button.dataset.steelVoiceTarget,disabled:button.disabled,pressed:button.getAttribute('aria-pressed'),
      background:getComputedStyle(button).backgroundColor,classes:button.className})));
    check(label+' tapping the red field stops capture and disables every microphone during recognition',
      processing.length===23 && processing.every((button)=>button.disabled && button.pressed!=='true' && !/\b(?:rec|recording)\b/.test(button.classes)),processing);
    const current=processing.find((button)=>button.key==='grade');
    check(label+' recognition replaces the red field with a gray disabled appearance',
      current && current.background!==active.background,current);
    const disabledControls=await page.locator('[data-steel-act="mic"], [data-steel-act="safety-stop"], [data-steel-act="audio-import"]').evaluateAll((buttons)=>
      buttons.map((button)=>({action:button.dataset.steelAct,disabled:button.disabled})));
    check(label+' recognition also locks compact stop and import actions',
      disabledControls.length>=3 && disabledControls.every((button)=>button.disabled),disabledControls);
    await assertNoLegacyRecordingToast(page,label+' processing');
    await screenshot(page,'steel-recognition-pending-'+label+'.png');
    await page.locator('[data-steel-voice-target="width"]').evaluate((button)=>button.click());
    check(label+' disabled fields cannot start a second microphone request',
      await page.evaluate(()=>__steelFieldStop.calls===1));
    await page.evaluate(()=>__steelFieldStop.finish({record_id:Steel.record.id,text:'',assignments:[],note:'受控识别完成'}));
    await page.waitForFunction(()=>!Steel.waiting && !S.recording &&
      Array.from(document.querySelectorAll('[data-steel-voice-target]')).every((button)=>!button.disabled));
    check(label+' completing recognition unlocks field microphones and clears compact processing state',
      !await page.locator('#steel-recorder-safety').isVisible() &&
      await page.evaluate(()=>__steelFieldStop.stops===1 && __steelFieldStop.closes===1));
  } finally {
    await page.evaluate(async()=>{
      const race=globalThis.__steelFieldStop;if(!race)return;
      if(race.finish)race.finish({record_id:Steel.record.id,text:'',assignments:[]});
      if(REC.starting || S.recording){Steel.micAttempt++;Steel.starting=false;await cancelRec();}
      navigator.mediaDevices.getUserMedia=race.originalMedia;window.AudioContext=race.originalContext;uploadWav=race.originalUpload;
      delete globalThis.__steelFieldStop;
    });
  }
}

async function assertGenericRecordingRaces(page, label) {
  const result = await page.evaluate(async () => {
    const saved = {getUserMedia: navigator.mediaDevices.getUserMedia, AudioContext: window.AudioContext,
      webkitAudioContext: window.webkitAudioContext, render, capture: S.capture, recTarget: S.recTarget,
      recording: S.recording, applied: S.applied, curKey: S.curKey, recorder: Object.assign({}, REC)};
    const counters = {media: 0, stop: 0, close: 0, failedStop: 0, failedClose: 0};
    let releasePermission;
    let pendingStart;
    const stream = {getAudioTracks: () => [{getSettings: () => ({sampleRate: 48000})}],
      getTracks: () => [{stop: () => { counters.stop++; }}]};
    const findings = {};
    try {
      render = async () => {};
      S.recording = false; REC.starting = false;
      S.capture = {mode: 'raw', denoise: true, asrMode: 'dictation'};
      S.curKey = 'grade';
      window.AudioContext = function FakeContext() {
        this.sampleRate = 48000; this.state = 'running'; this.destination = {};
        this.createMediaStreamSource = () => ({connect() {}});
        this.createScriptProcessor = () => ({connect() {}, disconnect() {}});
        this.createGain = () => ({gain: {}, connect() {}});
        this.close = async () => { counters.close++; };
      };
      navigator.mediaDevices.getUserMedia = () => {
        counters.media++;
        return new Promise((resolve) => { releasePermission = resolve; });
      };
      const sourceExtra = {record_id: Steel.record.id, current_key: 'grade'};
      pendingStart = startRec({url: '/api/steel/asr/audio', extra: sourceExtra});
      findings.lockedWhilePermissionPending = REC.starting && !S.recording;
      sourceExtra.current_key = 'width';
      S.curKey = 'width'; S.capture = {mode: 'system', denoise: false, asrMode: 'grammar'};
      await startRec({url: '/api/steel/asr/audio', extra: {current_key: 'width'}});
      findings.duplicatePendingBlocked = counters.media === 1;
      releasePermission(stream);
      await pendingStart;
      findings.targetAndConfigFrozen = S.recording && S.recTarget.extra.current_key === 'grade' &&
        S.recTarget.capture.mode === 'raw' && S.recTarget.capture.denoise === true &&
        S.recTarget.capture.asrMode === 'dictation';
      await startRec({url: '/api/steel/asr/audio', extra: {current_key: 'width'}});
      findings.duplicateActiveBlocked = counters.media === 1;
      await stopRec();
      findings.normalStopReleasesResources = counters.stop === 1 && counters.close === 1 && !S.recording;

      navigator.mediaDevices.getUserMedia = async () => ({
        getAudioTracks: () => [{getSettings: () => ({})}],
        getTracks: () => [{stop: () => { counters.failedStop++; }}],
      });
      window.AudioContext = function FailedResumeContext() {
        this.state = 'suspended';
        this.resume = async () => { throw new Error('controlled AudioContext resume failure'); };
        this.close = async () => { counters.failedClose++; };
      };
      await startRec({url: '/api/steel/asr/audio', extra: {current_key: 'grade'}});
      findings.failedContextReleasesResources = counters.failedStop === 1 && counters.failedClose === 1 &&
        !S.recording && !REC.starting && REC.stream === null && REC.ctx === null && REC.proc === null;
      return {findings, counters};
    } finally {
      if (releasePermission && REC.starting) releasePermission(stream);
      if (pendingStart) await pendingStart;
      navigator.mediaDevices.getUserMedia = saved.getUserMedia;
      window.AudioContext = saved.AudioContext;
      window.webkitAudioContext = saved.webkitAudioContext;
      render = saved.render;
      S.capture = saved.capture; S.recTarget = saved.recTarget; S.recording = saved.recording;
      S.applied = saved.applied; S.curKey = saved.curKey;
      Object.assign(REC, saved.recorder);
    }
  });
  for (const [name, pass] of Object.entries(result.findings)) check(label + ' recording ' + name, pass, result.counters);
}

async function assertDelayedParseEdit(page, label) {
  await editor(page, 'grade').click();
  await openDock(page);
  const delayed = await holdResponse(page, '**/api/steel/asr/parse', 'POST');
  try {
    await page.locator('#steel-say').fill('Q三五五B');
    const response = await delayed.received();
    requireCheck(label + ' controlled delayed parse reaches real backend', response.status === 200, response);
    await editor(page, 'grade').fill('Q235B');
    delayed.release();
    await page.waitForFunction(() => !Steel.waiting && !Steel.syncing && Object.keys(Steel.textJobs).length === 0);
    const state = await currentDraft(page);
    check(label + ' manual edit during delayed parse is preserved', state.data.grade === 'Q235B' &&
      await editor(page, 'grade').inputValue() === 'Q235B', state.data.grade);
    check(label + ' delayed stale parse creates no overwriting candidate',
      !(state.data.voice_candidates || []).some((item) => item.key === 'grade') &&
      !(state.data.voice_pending || []).includes('grade') &&
      await page.locator('[data-voice-candidate="grade"]').count() === 0,
      {pending: state.data.voice_pending, candidates: state.data.voice_candidates});
  } finally { await delayed.dispose(); }
}

async function assertChangedTargetDuringSync(page, label) {
  await editor(page, 'grade').focus(); await openDock(page);
  const delayed = await holdResponse(page, '**/api/steel/asr/parse', 'POST');
  try {
    await page.locator('#steel-say').fill('Q二七五B');
    const response = await delayed.received();
    requireCheck(label + ' delayed auto-sync has a real typed response', response.status === 200, response);
    await editor(page, 'manufacturer').focus(); await openDock(page);
    await page.locator('#steel-say').fill('现场录入制造厂');
    await waitForFormalValue(page, 'manufacturer', '现场录入制造厂');
    delayed.release();
    await waitForFormalValue(page, 'grade', 'Q275B');
    await page.waitForFunction(() => !Steel.syncing);
    const state = await currentDraft(page);
    check(label + ' switching targets during sync cannot write old text into the new field',
      state.data.manufacturer === '现场录入制造厂' && state.data.grade === 'Q275B' &&
      await page.locator('#steel-say').inputValue() === '现场录入制造厂');
  } finally { await delayed.dispose(); }
}

async function assertDelayedSaveEdit(page, label) {
  const before = await currentDraft(page);
  const delayed = await holdResponse(page, '**/api/steel/records/' + before.id, 'PUT');
  try {
    await page.evaluate(() => { globalThis.__steelMobileSave = steelSave(false, true); });
    const response = await delayed.received();
    requireCheck(label + ' controlled delayed save reaches real backend', response.status === 200, response);
    await editor(page, 'manufacturer').fill('手机录入后续修改厂');
    delayed.release();
    const result = await page.evaluate(async () => {
      const saved = await globalThis.__steelMobileSave;
      return {saved, version: Steel.record.version, dirty: Steel.dirty, value: Steel.data.manufacturer};
    });
    check(label + ' slow save keeps newer edit and dirty state', result.saved && result.dirty &&
      result.value === '手机录入后续修改厂' && await editor(page, 'manufacturer').inputValue() === result.value, result);
    check(label + ' slow save still advances optimistic record version', result.version > before.version &&
      result.version === response.body.record.version, result);
  } finally {
    await delayed.dispose();
    await page.evaluate(() => { delete globalThis.__steelMobileSave; });
  }
}

async function assertRouteChangesDuringPermission(page, label) {
  const before = await currentDraft(page);
  await page.evaluate(() => {
    const race = {originalMedia: navigator.mediaDevices.getUserMedia, originalContext: window.AudioContext,
      calls: 0, stops: 0, contexts: 0, closes: 0, released: false};
    globalThis.__steelMobileRoute = race;
    navigator.mediaDevices.getUserMedia = () => {
      race.calls++;
      return new Promise((resolve) => { race.release = () => {
        race.released = true;
        resolve({getAudioTracks: () => [{getSettings: () => ({})}],
          getTracks: () => [{stop: () => { race.stops++; }}]});
      }; });
    };
    window.AudioContext = function PreparedContext() {
      race.contexts++;
      this.state = 'running'; this.sampleRate = 48000; this.destination = {};
      this.close = async () => { race.closes++; this.state = 'closed'; };
    };
    race.pending = steelRecordVoice('grade');
  });
  try {
    await page.waitForFunction(() => globalThis.__steelMobileRoute.calls === 1);
    await page.evaluate(() => { location.hash = '#/steel'; });
    await page.locator('[data-steel-act="new"]').waitFor({state: 'visible'});
    const result = await page.evaluate(async () => {
      const race = globalThis.__steelMobileRoute;
      race.release();
      await race.pending;
      return {calls: race.calls, stops: race.stops, contexts: race.contexts, closes: race.closes,
        recording: S.recording, starting: REC.starting, streamCleared: REC.stream === null};
    });
    check(label + ' route change discards old audio target and releases stream', result.calls === 1 &&
      result.stops === 1 && result.contexts === 1 && result.closes === 1 && !result.recording && !result.starting && result.streamCleared,
      result);
    check(label+' leaving a pending recorder removes its independent safety card',
      !await page.locator('#steel-recorder-safety').isVisible());
  } finally {
    await page.evaluate(async () => {
      const race = globalThis.__steelMobileRoute;
      if (!race) return;
      if (race.release && !race.released) race.release();
      if (race.pending) await race.pending;
      navigator.mediaDevices.getUserMedia = race.originalMedia;
      window.AudioContext = race.originalContext;
      delete globalThis.__steelMobileRoute;
    });
    await page.goto('/#/steel/' + before.id);
    await page.waitForFunction((id) => Steel.record && String(Steel.record.id) === String(id) &&
      parseRoute().name === 'steel' && String(parseRoute().id) === String(id), before.id);
    await page.locator('[data-steel-voice-target="grade"]').waitFor({state: 'visible'});
  }
}

async function assertRouteChangesDuringActiveRecording(page, label) {
  const before = await currentDraft(page);
  await page.evaluate(async () => {
    const race = {originalMedia: navigator.mediaDevices.getUserMedia, originalContext: window.AudioContext,
      calls: 0, stops: 0, closes: 0, disconnects: 0, generation: REC.generation};
    globalThis.__steelMobileActive = race;
    navigator.mediaDevices.getUserMedia = async () => {
      race.calls++;
      return {getAudioTracks: () => [{getSettings: () => ({sampleRate: 48000})}],
        getTracks: () => [{stop: () => { race.stops++; }}]};
    };
    window.AudioContext = function FakeActiveContext() {
      this.state = 'running'; this.sampleRate = 48000; this.destination = {};
      this.createMediaStreamSource = () => ({connect() {}});
      this.createScriptProcessor = () => ({connect() {}, disconnect() { race.disconnects++; }});
      this.createGain = () => ({gain: {}, connect() {}});
      this.close = async () => { race.closes++; };
    };
    await steelRecordVoice('thickness');
    if (REC.proc && REC.proc.onaudioprocess) {
      REC.proc.onaudioprocess({inputBuffer: {getChannelData: () => new Float32Array([0.2, -0.1, 0.3])}});
      REC.proc.onaudioprocess({inputBuffer: {getChannelData: () => new Float32Array([0.1, 0.2])}});
    }
  });
  try {
    const active = await page.evaluate(() => ({recording: S.recording, buffers: REC.bufs.length,
      hasTarget: Boolean(S.recTarget), calls: globalThis.__steelMobileActive.calls, expanded: Steel.dockExpanded}));
    requireCheck(label + ' controlled active recording contains captured buffers with compact tools',
      active.recording && active.buffers === 2 && active.hasTarget && active.calls === 1 && !active.expanded, active);
    await userPageScroll(page, await page.evaluate(() => scrollY > 220 ? -180 : 180));
    await page.locator('#steel-capture-panel').waitFor({state: 'hidden'});
    const stop = page.locator('.steel-dock-peek [data-steel-act="mic"]');
    const stopHit = await hitTest(page, stop);
    check(label + ' scrolling an active recording collapses tools while keeping Stop reachable',
      await page.evaluate(() => S.recording && !Steel.dockExpanded) &&
      await stop.getAttribute('aria-label') === '停止录音' && stopHit.clear, stopHit);
    // Changing the hash dispatches the application's real navigation listener.
    await page.evaluate(() => { location.hash = '#/steel'; });
    await page.locator('[data-steel-act="new"]').waitFor({state: 'visible'});
    await page.waitForFunction(() => !S.recording && REC.stream === null && REC.bufs.length === 0);
    const result = await page.evaluate(() => {
      const race = globalThis.__steelMobileActive;
      return {stops: race.stops, closes: race.closes, disconnects: race.disconnects,
        buffers: REC.bufs.length, targetCleared: S.recTarget === null, recording: S.recording,
        resourcesCleared: REC.stream === null && REC.ctx === null && REC.proc === null,
        generationAdvanced: REC.generation > race.generation};
    });
    check(label + ' actual hashchange cancels active recording and clears captured audio', result.stops === 1 &&
      result.closes === 1 && result.disconnects === 1 && result.buffers === 0 && result.targetCleared &&
      !result.recording && result.resourcesCleared && result.generationAdvanced, result);
    check(label+' leaving an active recorder removes its independent safety card',
      !await page.locator('#steel-recorder-safety').isVisible());
  } finally {
    await page.evaluate(async () => {
      const race = globalThis.__steelMobileActive;
      if (!race) return;
      if (S.recording || REC.stream) await cancelRec();
      navigator.mediaDevices.getUserMedia = race.originalMedia;
      window.AudioContext = race.originalContext;
      delete globalThis.__steelMobileActive;
    });
    await page.goto('/#/steel/' + before.id);
    await page.waitForFunction((id) => Steel.record && String(Steel.record.id) === String(id) &&
      parseRoute().name === 'steel' && String(parseRoute().id) === String(id), before.id);
    await page.locator('[data-steel-voice-target="grade"]').waitFor({state: 'visible'});
  }
}

async function assertReadonly(page, context, template, label) {
  const created = await bodyOf(await context.request.post('/api/steel/records', {data: {data: template.example}}));
  requireCheck(label + ' create isolated finalization fixture', created.status === 201, created);
  const record = created.body.record;
  const finalized = await bodyOf(await context.request.post('/api/steel/records/' + record.id + '/finalize',
    {data: {version: record.version, data: record.data}}));
  requireCheck(label + ' finalize complete fixture', finalized.status === 200, finalized);
  await page.goto('/#/steel/' + record.id);
  await page.waitForFunction((id) => Steel.record && String(Steel.record.id) === String(id) &&
    Steel.record.status === 'final', record.id);
  await page.locator('.steel-workspace').waitFor({state: 'visible'});
  await page.locator('a[href="/api/steel/records/' + record.id + '/xlsx"]').waitFor({state: 'visible'});
  check(label + ' finalized form exposes no editing microphone',
    await page.locator('[data-steel-voice-target], [data-steel-act="mic"]').count() === 0);
  check(label + ' finalized form provides Excel download',
    await page.locator('a[href="/api/steel/records/' + record.id + '/xlsx"]').count() > 0);
}

async function assertFreshUiEntryAndExports(page,context,template,label) {
  await page.goto('/#/steel');
  await page.locator('[data-steel-act="new"]').waitFor({state:'visible'});
  await page.locator('[data-steel-act="new"]').tap();
  await page.waitForURL(/#\/steel\/\d+$/);
  await page.waitForFunction(()=>Steel.record && Steel.record.status==='draft' && Steel.data.document_no==='');
  const created=await currentDraft(page);
  check(label+' a new blank form defaults to sampling while the employee example stays full inspection',
    created.data.inspection_mode==='sampling' && template.example.inspection_mode==='full' &&
    Object.keys(created.data.values).length===0 && !created.data.is_example);
  const bodyActions=page.locator('.steel-form .steel-submit-actions');
  for (const [action,name] of [['save','保存草稿'],['finalize','生成检验记录 Excel']]) {
    const button=bodyActions.getByRole('button',{name,exact:true});
    const hit=await hitTest(page,button);
    check(label+' the form body exposes a readable tappable '+action+' action independently of the recorder',
      hit.clear && hit.height>=44 && (await button.isEnabled()),hit);
  }
  const ordinary={document_no:'UI-DRAFT-260111',product_name:'热轧钢卷UI验收',specification:'6*2000',grade:'Q235B',
    thickness_nominal:'6',thickness_lower:'-0.3',thickness_upper:'0.3',width_nominal:'2000',width_lower:'0',width_upper:'25',
    thickness:'6.05',width:'2007',inspected_quantity:'5',qualified_quantity:'5',rejected_quantity:'0',sampling_basis:'现场UI抽检方案A'};
  for(const [key,value] of Object.entries(ordinary))await editor(page,key).fill(value);
  for(const key of MANUAL_CHECK_KEYS)await editor(page,key).selectOption('合格');
  await expandDetails(page);
  const extras=[{name:'长度',requirement:'6000±20',result:'6007',verdict:'合格'},
    {name:'对角线',requirement:'差值≤5',result:'2',verdict:'合格'}];
  for(let index=0;index<extras.length;index++)for(const [property,value] of Object.entries(extras[index])){
    const field=editor(page,'extra_'+index+'_'+property);
    if(property==='verdict')await field.selectOption(value);else await field.fill(value);
  }
  // Reproduce Android change-only inputs without sending an input event.
  await page.evaluate(()=>{
    const values={manufacturer:'河南现场录入测试厂',inspection_date:'2026-11-11',supply_quantity:'24'};
    for(const [key,value] of Object.entries(values)){
      const input=document.querySelector('[data-steel-header="'+key+'"]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);
      input.dispatchEvent(new Event('change',{bubbles:true}));
    }
    const result=document.querySelector('[data-steel-extra="1"][data-prop="result"]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(result,'3');
    result.dispatchEvent(new Event('change',{bubbles:true}));
  });
  const changed=await currentDraft(page);
  check(label+' change-only manufacturer date quantity and extra-result input synchronize into form state',
    changed.data.manufacturer==='河南现场录入测试厂' && changed.data.inspection_date==='2026-11-11' &&
    changed.data.supply_quantity===24 && changed.data.extra_rows[1].result==='3',changed.data);

  await addParsedCandidates(page,[{key:'thickness',text:'厚度六点零八'}]);
  // The employee may fill an entire sheet without ever tapping Save. Check
  // what a second request can actually recover, independently of Steel.data.
  const recordPath='/api/steel/records/'+created.id;
  await page.waitForFunction(()=>!Steel.dirty && Steel.record.version>1 &&
    Steel.record.data.voice_pending.includes('thickness'),null,{timeout:9000});
  const persisted=(await bodyOf(await context.request.get(recordPath))).body.record;
  check(label+' unattended autosave preserves manual readings and the pending speech candidate separately',
    persisted.status==='draft' && persisted.data.document_no==='UI-DRAFT-260111' &&
    persisted.data.manufacturer==='河南现场录入测试厂' && persisted.data.values.surface.value==='合格' &&
    String(persisted.data.values.thickness.value)==='6.05' &&
    persisted.data.voice_candidates.some((item)=>item.key==='thickness' && String(item.value)==='6.08'),
    {status:persisted.status,reading:persisted.data.values.thickness,candidate:persisted.data.voice_candidates});

  // Set an unsignalled edit immediately before using the employee's mobile
  // preview link. The stale initial draft cannot contain this marker.
  await editor(page,'document_no').evaluate((input)=>{input.value='UI-DIRECT-PREVIEW';});
  const openPreview = page.locator('.steel-readonly-label [data-steel-act="preview"]');
  const formHash = '#/steel/' + created.id;
  const previewHash = '#/steel-preview/' + created.id;
  const originalTabCount = context.pages().length;
  await openPreview.tap();
  await page.waitForURL((url) => url.hash === previewHash);
  const table = page.frameLocator('iframe').locator('table');
  await table.getByText('UI-DIRECT-PREVIEW', {exact: true}).waitFor();
  const frameText = await table.innerText();
  check(label + ' mobile preview opens the saved draft in the same tab and displays the last edit',
    context.pages().length === originalTabCount && frameText.includes('河南现场录入测试厂') &&
    frameText.includes('6.05') && page.frames().some((frame) => new URL(frame.url()).pathname === recordPath + '/preview'));
  const pageBack = page.locator('[data-steel-preview-back]');
  await pageBack.tap();
  await page.waitForURL((url) => url.hash === formHash);
  check(label + ' preview Return reopens this editable record with its pending speech review',
    await editor(page,'document_no').isEditable() && await editor(page,'document_no').inputValue() === 'UI-DIRECT-PREVIEW' &&
    await page.locator('[data-steel-act="voice-apply"][data-key="thickness"]').isEnabled());

  await openPreview.tap();
  await page.waitForURL((url) => url.hash === previewHash);
  await table.getByText('UI-DIRECT-PREVIEW', {exact: true}).waitFor();
  await page.goBack();
  await page.waitForURL((url) => url.hash === formHash);
  check(label + ' Android browser Back returns from preview to the same editable form',
    await editor(page,'document_no').isEditable() && await editor(page,'document_no').inputValue() === 'UI-DIRECT-PREVIEW');

  // Make one further change and immediately try to generate while review is
  // still pending; the last edit must outlive both the rejection and reload.
  await editor(page,'document_no').fill('UI-REJECTED-EDITS');
  const rejectedResponse=page.waitForResponse((response)=>new URL(response.url()).pathname===
    '/api/steel/records/'+created.id+'/finalize' && response.request().method()==='POST',{timeout:4000}).catch(()=>null);
  await bodyActions.locator('[data-steel-act="finalize"]').tap();
  const rejected=await rejectedResponse;
  const pending=await currentDraft(page);
  check(label+' generating from an unchanged ASR preview cannot silently mark that candidate manual',
    await page.evaluate(()=>Steel.record.status==='draft') && (!rejected || rejected.status()===400) &&
    pending.data.voice_pending.includes('thickness') && pending.data.voice_candidates.some((item)=>item.key==='thickness') &&
    String(pending.data.values.thickness.value)==='6.05',pending.data.values.thickness);
  await page.locator('.steel-submit-error').waitFor({state:'visible'});
  const pendingIssue=page.locator('.steel-submit-error [data-steel-section="steel-review-block"]').first();
  check(label+' generation errors appear beside Generate and point to the pending speech review',
    await page.locator('.steel-readonly-label .steel-submit-error').isVisible() && await pendingIssue.isVisible() &&
    (await pendingIssue.innerText()).includes('厚度'));
  const afterRejection=(await bodyOf(await context.request.get(recordPath))).body.record;
  requireCheck(label+' rejected generation durably keeps the last edit and remains a draft',
    afterRejection.status==='draft' && afterRejection.data.document_no==='UI-REJECTED-EDITS' &&
    afterRejection.data.voice_pending.includes('thickness'),afterRejection.data.document_no);
  await page.reload();
  await page.locator('[data-voice-candidate="thickness"]').waitFor({state:'visible'});
  check(label+' reload restores employee input, manual pass verdict and unconfirmed measurement',
    await editor(page,'document_no').inputValue()==='UI-REJECTED-EDITS' &&
    await editor(page,'manufacturer').inputValue()==='河南现场录入测试厂' &&
    await editor(page,'surface').inputValue()==='合格' &&
    await editor(page,'thickness').inputValue()==='6.08');
  await actualPreview(page,afterRejection,['UI-REJECTED-EDITS','河南现场录入测试厂','6.05'],label+' recovered draft');
  await page.locator('[data-steel-act="voice-discard"][data-key="thickness"]').tap();
  await page.locator('[data-voice-candidate="thickness"]').waitFor({state:'detached'});

  await page.evaluate(()=>{
    // This models an autofill/IME edit that sends neither input nor change.
    // Suppress possible blur-generated change too, so Generate must collect DOM.
    const block=(event)=>{
      if(event.target.matches('[data-steel-header="document_no"], [data-steel-value="width"]'))event.stopImmediatePropagation();
    };
    globalThis.__uiEntryBlock=block;
    document.addEventListener('input',block,true);document.addEventListener('change',block,true);
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(
      document.querySelector('[data-steel-header="document_no"]'),'UI-GEN-260111');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(
      document.querySelector('[data-steel-value="width"]'),'2009');
  });
  const finalizedPromise=page.waitForResponse((response)=>new URL(response.url()).pathname===
    '/api/steel/records/'+created.id+'/finalize' && response.request().method()==='POST');
  await bodyActions.locator('[data-steel-act="finalize"]').tap();
  const finalized=await bodyOf(await finalizedPromise);
  await page.evaluate(()=>{
    document.removeEventListener('input',__uiEntryBlock,true);document.removeEventListener('change',__uiEntryBlock,true);
    delete globalThis.__uiEntryBlock;
  });
  requireCheck(label+' Generate collects a final silent DOM edit and produces the current inspection',
    finalized.status===200 && finalized.body.record.status==='final' && finalized.body.record.id===created.id,finalized);
  const record=finalized.body.record;
  await page.waitForFunction((id)=>Steel.record && Steel.record.id===id && Steel.record.status==='final',created.id);
  await page.waitForFunction(()=>{
    const link=document.querySelector('.steel-download-card a');
    if(!link)return false;
    const b=link.getBoundingClientRect();
    const hit=document.elementFromPoint(b.left+b.width/2,b.top+b.height/2);
    return b.height>=44 && b.top>=0 && b.bottom<=innerHeight && link.contains(hit);
  });
  check(label+' after Generate the actual download action is in the phone viewport beside its document number',
    (await page.locator('.steel-download-card').innerText()).includes('UI-GEN-260111'));
  const expected=['UI-GEN-260111','热轧钢卷UI验收','6*2000','河南现场录入测试厂','Q235B','6.05','2009','2026.11.11','6007','差值≤5'];
  const preview=await actualPreview(page,record,expected,label+' freshly generated');
  check(label+' the embedded generated preview contains every entered nondefault value',
    expected.every((value)=>preview.includes(value)) && preview.includes('已确认记录') &&
    record.data.document_no==='UI-GEN-260111' && JSON.stringify(record.data.values.width.value)==='[2009]');
  const previewResponse=await context.request.get('/api/steel/records/'+record.id+'/preview');
  check(label+' generated preview responses prohibit stale browser caching',
    previewResponse.status()===200 && /no-store/.test(previewResponse.headers()['cache-control'] || ''));
  await screenshot(page,'steel-ui-generated-'+label+'.png');
  const workbookPath = '/api/steel/records/' + record.id + '/xlsx';
  const [download, workbookResponse] = await Promise.all([
    page.waitForEvent('download'),
    page.waitForResponse((response) => new URL(response.url()).pathname === workbookPath &&
      response.request().resourceType() === 'fetch'),
    page.locator('a[href="' + workbookPath + '"]').tap(),
  ]);
  requireCheck(label + ' the phone page fetches its signed-in inspection workbook',
    workbookResponse.status() === 200 && (await workbookResponse.body()).readUInt32LE(0) === 0x04034b50);
  requireCheck(label+' tapping the visible mobile download returns a workbook for the entered document',
    download.url().startsWith('blob:') && (await download.failure())===null && download.suggestedFilename().includes('UI-GEN-260111'),
    download.suggestedFilename());
  const savedBytes = fs.readFileSync(await download.path());
  const workbook=exportedWorkbookCells(savedBytes,label);
  const cells=workbook.cells;
  const exact={B3:'UI-GEN-260111',D3:'热轧钢卷UI验收',H3:'6*2000',B4:'河南现场录入测试厂',D4:'24',H4:'Q235B',
    C9:'6±0.3',G9:'6.05',G10:'2009',B11:'长度',C11:'6000±20',G11:'6007',B12:'对角线',C12:'差值≤5',G12:'3',B13:'合格',G15:'2026.11.11'};
  check(label+' actual XLSX cells agree with nondefault UI and embedded preview values',
    Object.entries(exact).every(([address,value])=>cells[address]===value),Object.fromEntries(Object.keys(exact).map((key)=>[key,cells[key]])));
  check(label+' XLSX records sampling numbers and the changed native width formula',
    cells.F13.includes('24') && cells.F13.includes('现场UI抽检方案A') && cells.F13.includes('5') &&
    workbook.math.includes('2000') && (cells.G6.match(/合格☑/g) || []).length===4 &&
    (cells.G7.match(/合格☑/g) || []).length===3,{conclusion:cells.F13,math:workbook.math});

  await page.locator('[data-steel-file-system]').tap();
  const ticketLink = page.locator('[data-steel-file-ticket]');
  await ticketLink.waitFor({state: 'visible'});
  const ticket = new URL(await ticketLink.getAttribute('href'), page.url());
  requireCheck(label + ' the system downloader receives a same-origin URL ending in .xlsx',
    ticket.origin === new URL(page.url()).origin && ticket.pathname.endsWith('.xlsx'));
  // Node fetch has no browser cookie jar. A native download manager can
  // request this link independently without stealing the user's active tab.
  const systemResponse = await fetch(ticket, {redirect: 'error'});
  requireCheck(label + ' a separate downloader with no session receives the same finalized workbook',
    systemResponse.status === 200 && Buffer.from(await systemResponse.arrayBuffer()).equals(savedBytes));
  await page.goto('/#/steel');
  const listEntry=page.locator('.steel-record-link[href="#/steel/'+record.id+'"]');
  await listEntry.waitFor();
  const listing=await listEntry.innerText();
  check(label+' saved records can be found by their actual document number and record ID',
    listing.includes('单据号 UI-GEN-260111') && listing.includes('记录 #'+record.id),listing);
}

async function assertFailedFollowup(page, context, template, label) {
  const data = JSON.parse(JSON.stringify(template.example));
  data.is_example = false;
  data.document_no='UI-FAILED-260112';data.manufacturer='UI不合格父单制造厂';
  data.values.thickness={value:[5.91],source:'manual',confidence:1,confirmed:true};
  data.values.width={value:[1818],source:'manual',confidence:1,confirmed:true};
  data.values.surface = {value:'不合格',source:'manual',confidence:1,confirmed:true};
  data.qualified_quantity = 0; data.rejected_quantity = 1;
  const created = await bodyOf(await context.request.post('/api/steel/records', {data:{data}}));
  requireCheck(label+' create an isolated failed inspection', created.status===201, created);
  const original = created.body.record;
  await page.goto('/#/steel/'+original.id);
  await page.waitForFunction((id)=>Steel.record && Steel.record.id===id && Steel.record.status==='draft',original.id);
  const finalizedResponse = page.waitForResponse((res)=>new URL(res.url()).pathname===
    '/api/steel/records/'+original.id+'/finalize' && res.request().method()==='POST');
  await page.locator('[data-steel-act="finalize"]:visible').first().click();
  const finalized = await bodyOf(await finalizedResponse);
  requireCheck(label+' the Generate action accepts a complete failed inspection',finalized.status===200 &&
    finalized.body.record.evaluation.overall==='fail' && finalized.body.followup,finalized);
  const child = finalized.body.followup;
  await page.waitForFunction((id)=>Steel.record && Steel.record.id===id && Steel.record.status==='final',original.id);
  await page.locator('.steel-followup-note').waitFor({state:'visible'});
  const parentPreview=await actualPreview(page,finalized.body.record,
    ['UI-FAILED-260112','UI不合格父单制造厂','5.91','1818','不合格'],label+' failed parent');
  check(label+' failed generation stays on the completed parent and retains the entered embedded preview',
    new URL(page.url()).hash==='#/steel/'+original.id && parentPreview.includes('已确认记录') &&
    await page.locator('a[href="/api/steel/records/'+original.id+'/xlsx"]').isVisible());
  await screenshot(page,'steel-failed-parent-'+label+'.png');
  await page.locator('.steel-followup-note a[href="#/steel/'+child.id+'"]').tap();
  await page.waitForFunction((id)=>Steel.record && Steel.record.id===id && Steel.record.parent_id &&
    Steel.record.status==='draft',child.id);
  await page.locator('.steel-followup-note').waitFor({state:'visible'});
  const state = await currentDraft(page);
  check(label+' explicitly opening the linked followup shows batch details without copied inspection results',
    state.data.document_no===data.document_no && state.data.grade===data.grade &&
    state.data.specification===data.specification && Object.keys(state.data.values).length===0 &&
    state.data.inspected_quantity===null && state.data.qualified_quantity===null &&
    state.data.rejected_quantity===null && (state.data.voice_pending || []).length===0,state.data);
  check(label+' the new form explains re-entry and retains access to the original Excel',
    /后续草稿/.test(await page.locator('.steel-followup-note').innerText()) &&
    await page.locator('.steel-followup-note a[href="/api/steel/records/'+original.id+'/xlsx"]').isVisible());
  await assertNoOverflow(page,label+' failed-inspection followup banner');
  await screenshot(page,'steel-followup-'+label+'.png');
  const childRecord=await page.evaluate(()=>JSON.parse(JSON.stringify(Steel.record)));
  const childPreview=await actualPreview(page,childRecord,['UI-FAILED-260112','UI不合格父单制造厂'],label+' blank followup');
  const childFrame=await (await page.locator('iframe[title="进货检验记录预览"]').elementHandle()).contentFrame();
  const childTable=await childFrame.locator('table').textContent();
  check(label+' only the explicitly opened followup has a marked draft preview and empty measurements',
    childPreview.includes('草稿') && !childTable.includes('5.91') && !childTable.includes('1818'));
  const old = await bodyOf(await context.request.get('/api/steel/records/'+original.id));
  const excel = await context.request.get('/api/steel/records/'+original.id+'/xlsx');
  check(label+' original failure remains finalized and downloadable while the replacement is unfinished',
    old.body.record.status==='final' && old.body.record.data.values.surface.value==='不合格' &&
    old.body.record.followup_record_id===child.id && old.body.record.followup_status==='draft' &&
    excel.status()===200 && (await excel.body()).subarray(0,2).toString()==='PK');
  await page.goto('/#/steel/'+original.id);
  await page.locator('.steel-followup-note').waitFor({state:'visible'});
  await page.locator('[data-steel-act="revise"]').click();
  await page.waitForFunction((id)=>Steel.record && Steel.record.id===id,child.id);
  const records = await bodyOf(await context.request.get('/api/steel/records'));
  check(label+' reopening a failed form returns to the same followup without duplicate drafts',
    records.body.records.filter((r)=>r.parent_id===original.id).length===1);
}

async function mobileWidth(browser, base, width) {
  const label = String(width);
  const run = {width, height: 844, is_mobile: true, has_touch: true};
  report.widths.push(run);
  const context = await browser.newContext({baseURL: base, viewport: {width, height: 844},
    deviceScaleFactor: 2, isMobile: true, hasTouch: true, userAgent: USER_AGENT});
  const page = await context.newPage();
  const uploadGuard = {authorized: null};
  page.setDefaultTimeout(12000);
  page.on('pageerror', (error) => report.page_errors.push({width, message: error.message}));
  await context.addInitScript(() => {
    const rejectMic = () => Promise.reject(new DOMException('Automated test forbids microphone access', 'NotAllowedError'));
    if (!navigator.mediaDevices) Object.defineProperty(navigator, 'mediaDevices', {value: {}, configurable: true});
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia',
      {value: rejectMic, configurable: true, writable: true});
  });
  await context.route(/\/api\/(?:steel\/)?asr\/audio(?:\?|$)/, async (route) => {
    if (uploadGuard.authorized && new URL(route.request().url()).pathname === '/api/steel/asr/audio') {
      const authorization = uploadGuard.authorized;
      uploadGuard.authorized = null; // Only the chosen fixture's first upload is allowed.
      const bytes = route.request().postDataBuffer() || Buffer.alloc(0);
      const start = bytes.indexOf(Buffer.from('RIFF'));
      const text = bytes.toString('latin1');
      const field = (name) => {
        const match = new RegExp('name="' + name + '"\\r\\n\\r\\n([^\\r\\n]*)').exec(text);
        return match ? match[1] : null;
      };
      const item = {width, url: route.request().url(), expected: authorization,
        record_id: field('record_id'), current_key: field('current_key')};
      if (start >= 0 && bytes.length >= start + 44) Object.assign(item, {
        riff: bytes.toString('ascii', start, start + 4), wave: bytes.toString('ascii', start + 8, start + 12),
        format: bytes.readUInt16LE(start + 20), channels: bytes.readUInt16LE(start + 22),
        sample_rate: bytes.readUInt32LE(start + 24), bits: bytes.readUInt16LE(start + 34),
        data_bytes: bytes.readUInt32LE(start + 40),
      });
      report.authorized_audio_imports.push(item);
      await route.continue();
      return;
    }
    report.blocked_audio_requests.push({width, url: route.request().url()});
    await route.abort('blockedbyclient');
  });
  try {
    await page.goto('/#/login');
    await page.locator('[data-act="login"][data-emp="QC1024"]').click();
    await page.locator('.steel-entry[href="#/steel"]').waitFor({state: 'visible'});
    check(label + ' Android touch context and homepage steel entry', await page.evaluate(() =>
      /Android/.test(navigator.userAgent) && navigator.maxTouchPoints > 0));
    await page.locator('.steel-entry[href="#/steel"]').click();
    await page.locator('[data-steel-act="example"]').click();
    await page.waitForURL(/#\/steel\/\d+$/);
    await page.locator('.steel-workspace').waitFor({state: 'visible'});
    const templateResult = await bodyOf(await context.request.get('/api/steel/template'));
    requireCheck(label + ' backend editable voice catalog', templateResult.status === 200 &&
      Array.isArray(templateResult.body.voice_targets), templateResult);
    const template = templateResult.body;
    await assertCollapsedDock(page, label);
    run.geometry = await assertFormGeometry(page, label, template.voice_targets);
    await assertManualOnlyChecks(page,label);
    await assertTextKeyboards(page, label);
    await assertImportFallbackLayout(page, label);
    await page.evaluate(() => { if (document.activeElement) document.activeElement.blur(); scrollTo(0, 0); });
    await screenshot(page, 'steel-' + width + '.png');
    if (width === 360) {
      await assertTouchKeyboardAndPermissionPhases(page,label);
      await assertPermissionTimeoutKeepsPanel(page,label);
      await assertTapActivationAndCancel(page, label);
      await assertSystemRecorderImport(page, label, uploadGuard);
      await assertFieldStopAndRecognitionLock(page,label);
      await assertAutomaticManualSync(page, label);
      await assertHistoricalBatchReview(page, label);
      await assertVoiceReview(page, context, label);
      await assertFiveCandidateReview(page, label);
      await assertGenericRecordingRaces(page, label);
      await assertDelayedParseEdit(page, label);
      await assertChangedTargetDuringSync(page, label);
      await assertDelayedSaveEdit(page, label);
      await assertRouteChangesDuringPermission(page, label);
      await assertRouteChangesDuringActiveRecording(page, label);
      await page.setViewportSize({width, height: 480});
      await assertNoOverflow(page, label + ' reduced-height keyboard approximation');
      const parseHit = await hitTest(page, page.locator('.steel-dock-peek [data-steel-act="mic"]'));
      check(label + ' reduced-height compact microphone action remains reachable', parseHit.clear, parseHit);
      await page.setViewportSize({width, height: 844});
    }
    if(width===360)await assertFreshUiEntryAndExports(page,context,template,label);
    await assertReadonly(page, context, template, label);
    if(width===360)await assertFailedFollowup(page,context,template,label);
    run.completed = true;
  } catch (error) {
    check(label + ' mobile flow completes', false, error.stack || error.message);
    run.error = error.message;
    try { await screenshot(page, 'steel-failure-' + width + '.png'); } catch (_) { /* browser may have closed */ }
  } finally {
    await context.close();
  }
}

async function main() {
  let browser;
  try {
    const base = safeBase();
    report.base = base;
    assert(fs.existsSync(CHROME), 'An existing Chrome executable is required; no browser will be downloaded');
    const {chromium} = require(path.join(BUNDLED_MODULES, 'playwright'));
    fs.mkdirSync(OUT, {recursive: true});
    browser = await chromium.launch({headless: true, executablePath: CHROME,
      args: ['--no-proxy-server'], timeout: 20000});
    // launch() uses Playwright's own temporary profile, never a user profile.
    for (const width of [360, 393, 412]) await mobileWidth(browser, base, width);
    check('No uncaught browser page errors', report.page_errors.length === 0, report.page_errors);
    check('No unapproved browser microphone audio is uploaded', report.blocked_audio_requests.length === 0,
      report.blocked_audio_requests);
    check('Exactly one selected system-recorder fixture uses the real audio endpoint',
      report.authorized_audio_imports.length === 1, report.authorized_audio_imports);
  } catch (error) {
    check('Mobile browser check starts safely', false, error.stack || error.message);
  } finally {
    if (browser) await browser.close();
    report.finished_at = new Date().toISOString();
    fs.mkdirSync(OUT, {recursive: true});
    fs.writeFileSync(path.join(OUT, 'steel-mobile-results.json'), JSON.stringify(report, null, 2) + '\n', 'utf8');
    console.log('REPORT: ' + path.join(OUT, 'steel-mobile-results.json'));
    console.log('FAILS: ' + report.failures.length);
    if (report.failures.length) process.exitCode = 1;
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });

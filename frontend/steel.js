/* Steel incoming inspection: the employee's form is the primary deliverable. */
'use strict';
const STEEL_UI_VERSION = '0.19.1';
const Steel = { template: null, record: null, data: null, evaluation: null, dirty: false, current: 'thickness', text: '', timer: null, evalRequest: 0, waiting: false, saving:false, starting:false, finalizing:false, revision:0, micAttempt:0, dockExpanded:false, textDirty:false, textComposing:false, textTimer:null, textTarget:null, textEpoch:0, textSession:0, textJobs:{}, fieldVersions:{}, syncing:false, textError:'' };
const steelClone = (v) => JSON.parse(JSON.stringify(v));
let steelAutoTimer, steelAutoJob;
function steelTabKey(r) { return 'steel.draft.' + r.owner_id + '.' + r.id + '.' + r.created_at; }
function steelRememberDraft() {
  if(steelReadonly())return;
  try { sessionStorage.setItem(steelTabKey(Steel.record),JSON.stringify({version:Steel.record.version,snapshot:Steel.record.snapshot,data:Steel.data})); } catch(_) {}
}
function steelRestoreDraft() {
  if(steelReadonly())return;
  try {
    const copy=JSON.parse(sessionStorage.getItem(steelTabKey(Steel.record)) || 'null');
    if(copy && copy.version===Steel.record.version && copy.snapshot===Steel.record.snapshot && copy.data){
      Steel.data=copy.data;steelChanged();
    }
  } catch(_) {}
}
function steelPreviewPath(r) { return '/api/steel/records/'+r.id+'/preview?v='+r.version+'&ui='+STEEL_UI_VERSION; }
VIEWS['steel-preview'] = async function(id) {
  if(!S.me)return VIEWS.login();
  if(!/^\d+$/.test(id))return {html:'<div class="wrap"><a href="#/steel">返回钢板检验记录列表</a></div>'};
  const current=Steel.record && String(Steel.record.id)===id ? Steel.record : null;
  const src=current ? steelPreviewPath(current) : '/api/steel/records/'+id+'/preview?ui='+STEEL_UI_VERSION;
  return {html:'<main class="steel-preview-screen"><header><a class="btn ghost" data-steel-preview-back href="#/steel/'+id+'">← 返回检验记录</a>' +
    '<strong>表单预览 · #'+id+'</strong></header><iframe title="进货检验记录预览" src="'+src+'"></iframe></main>'};
};
// A direct link has no originating record in its tab history.
window.addEventListener('hashchange',(event)=>{
  const next=parseRoute();
  if(next.name==='steel-preview' && /^\d+$/.test(next.id) && event.oldURL &&
      new URL(event.oldURL).hash==='#/steel/'+next.id){
    history.replaceState({steelPreviewFrom:next.id},'');
  }
});
function steelFileActions(id,label,docNo) {
  return '<div class="steel-file-actions" data-steel-file="'+id+'" data-file-doc="'+esc(docNo || '')+'">' +
    '<a class="btn" data-steel-download href="/api/steel/records/'+id+'/xlsx">'+esc(label)+'</a>' +
    '<div class="steel-file-feedback" role="status"></div><div class="steel-file-options" hidden>' +
    '<button type="button" class="btn ghost sm" data-steel-file-share hidden>分享 Excel 文件</button>' +
    '<button type="button" class="btn ghost sm" data-steel-file-system>改用系统下载</button></div></div>';
}
function steelPaintSaveState() {
  if(!Steel.record)return;
  const wrap=document.querySelector('.steel-workspace[data-steel-record-id="'+Steel.record.id+'"]');
  if(!wrap)return;
  const message=Steel.syncError || (Steel.dirty ? '修改待保存，预览将在保存后更新。' : (Steel.record.status==='final' ? '已生成正式记录。' : '草稿已保存。'));
  const state=wrap.querySelector('.steel-save-state');if(state)state.textContent=message;
  const preview=wrap.querySelector('.steel-preview iframe'), label=wrap.querySelector('.steel-preview-head span');
  if(label)label.textContent=Steel.record.status==='final' ? '本次确认数据' : message;
  if(preview){
    preview.hidden=!!Steel.dirty;
    const url=appURL(steelPreviewPath(Steel.record));
    if(!Steel.dirty && preview.getAttribute('src')!==url)preview.setAttribute('src',url);
  }
}
function steelAcceptSaved(record, revision, adopt) {
  if(!Steel.record || record.id!==Steel.record.id)return;
  Steel.record=record;Steel.syncError='';
  if(revision===Steel.revision){
    // Background saves do not replace a control while a phone IME is editing it.
    if(adopt)Steel.data=steelClone(record.data);
    Steel.evaluation=record.evaluation;Steel.dirty=false;
    try {sessionStorage.removeItem(steelTabKey(record));}catch(_){}
  } else if(Steel.dirty)steelRememberDraft(); // keep newer edits under the new server version
  steelPaintSaveState();
}
function steelScheduleSave() {
  clearTimeout(steelAutoTimer);
  const id=Steel.record.id;
  steelAutoTimer=setTimeout(()=>steelPersistDraft(id),850);
}
async function steelWriteRecord(path, method, body) {
  const controller=typeof AbortController==='function' ? new AbortController() : null;
  let timer;
  try{
    return await Promise.race([api(path,{method,body,signal:controller ? controller.signal : undefined}),
      new Promise((_,reject)=>{timer=setTimeout(()=>{reject(new Error('服务器未及时响应，请检查局域网连接后重试。'));if(controller)controller.abort();},15000);})]);
  }finally{clearTimeout(timer);}
}
function steelPersistDraft(id) {
  if(steelReadonly() || Steel.record.id!==id || !Steel.dirty || Steel.saving)return;
  if(steelAutoJob && steelAutoJob.id===id)return steelAutoJob.promise;
  const job={id}, revision=Steel.revision, version=Steel.record.version, data=steelClone(Steel.data);
  steelAutoJob=job;
  job.promise=(async()=>{
    try {
      const response=await steelWriteRecord('/api/steel/records/'+id,'PUT',{version,data});
      steelAcceptSaved(response.record,revision,false);
    }catch(error){
      if(Steel.record && Steel.record.id===id){
        Steel.syncError='草稿尚未保存到服务器：'+error.message;steelPaintSaveState();
      }
    }finally{
      if(steelAutoJob===job)steelAutoJob=null;
      if(Steel.record && Steel.record.id===id && Steel.dirty && Steel.revision!==revision && !Steel.saving)steelScheduleSave();
    }
  })();return job.promise;
}
const steelValue = (key) => {
  const value=steelDisplayValue(key);
  return Array.isArray(value) ? value.join('、') : (value == null ? '' : value);
};
const steelState = (status) => ({pass:'合格', fail:'不合格', pending:'待确认', missing:'未填写'}[status] || '未填写');

function steelSummary() {
  const e = Steel.evaluation;
  if (!e) return '<div class="hint">填写后保存并判定。</div>';
  const issues = e.issues || [];
  return '<div class="row between"><b>' + (issues.length ? '还有 ' + issues.length + ' 项需完善' : (e.overall === 'fail' ? '检验有不合格项，可如实生成记录' : '内容已齐，可以确认生成记录')) +
    '</b><span class="pill ' + (e.overall === 'pass' ? 'ok' : 'warn') + '">' + steelState(e.overall) + '</span></div>' +
    (issues.length ? '<ul class="steel-issues">' + issues.slice(0, 12).map((i) => '<li>' + esc(i.message) + '</li>').join('') + '</ul>' : '') +
    (Steel.data.inspection_mode === 'sampling' && e.recommended_sample != null
      ? '<div class="hint">本批至少抽检 ' + esc(e.recommended_sample) + ' 件。钢卷按卷计数。</div>' : '');
}

function steelInput(label, key, type, extra) {
  const disabled = steelReadonly() || steelStandardField(key);
  // Android decimal keyboards often omit the minus key. Signed deviations
  // need a text keyboard; the server still validates their numeric value.
  const signed = /_(lower|upper)$/.test(key);
  return '<div class="steel-input"><label for="steel-' + key + '">' + label + '</label>' + steelVoiceWrap(key, '<input id="steel-' + key + '" ' + (disabled ? 'disabled ' : '') +
    'data-steel-header="' + key + '" type="' + (signed ? 'text' : (type || 'text')) + '"' + (signed ? ' inputmode="text"' : type === 'number' ? ' inputmode="decimal"' : '') + ' value="' + esc(steelDisplayValue(key) == null ? '' : steelDisplayValue(key)) + '" ' + (extra || '') + '>') + '</div>';
}

function steelSelect(label, key, options) {
  return '<div class="steel-input"><label for="steel-' + key + '">' + esc(label) + '</label>' + steelVoiceWrap(key, '<select id="steel-' + key + '" data-steel-header="' + key + '"' + (steelReadonly() ? ' disabled' : '') + '>' + options.map(([v,n]) => '<option value="' + esc(v) + '"' + (steelDisplayValue(key) === v ? ' selected' : '') + '>' + esc(n) + '</option>').join('') + '</select>') + '</div>';
}

function steelCheck(item, index) {
  const meta = Steel.data.values[item.key] || {};
  const row = (Steel.evaluation.rows || []).find((r) => r.key === item.key) || {};
  const disabled = Steel.record.status === 'final' || Steel.record.owner_id !== S.me.id;
  const options = ['', '合格', '不合格'].concat(item.key === 'coil' ? ['不适用'] : []);
  return '<div class="steel-check" data-steel-row="' + item.key + '"><div class="grow"><b>' + (index + 1) + '. ' + esc(item.label) + '</b>' +
    '<p>' + esc(item.requirement) + '</p><span class="steel-field-status">' + esc(steelState(steelCandidate(item.key) ? 'pending' : row.status)) + '</span></div>' +
    '<div class="steel-check-controls">' + steelVoiceWrap(item.key, '<select aria-label="' + esc(item.label) + '" data-steel-value="' + item.key + '" ' + (disabled ? 'disabled' : '') + '>' + options.map((v) => '<option value="' + v + '"' + (steelDisplayValue(item.key) === v ? ' selected' : '') + '>' + (v || '请选择') + '</option>').join('') + '</select>') +
    (!disabled && meta.source === 'asr' && !meta.confirmed ? '<span class="hint">请重新手选本项结果</span>' : '') + '</div></div>';
}

function steelDimension(key, label) {
  const disabled = Steel.record.status === 'final' || Steel.record.owner_id !== S.me.id;
  const row = (Steel.evaluation.rows || []).find((r) => r.key === key) || {};
  const meta = Steel.data.values[key] || {};
  return '<div class="steel-dimension" data-steel-row="' + key + '"><div class="row between"><h4>' + label + ' <span class="hint">mm</span></h4><span class="steel-field-status">' + esc(steelState(steelCandidate(key) ? 'pending' : row.status)) + '</span></div>' +
    '<div class="steel-limit-grid">' + steelInput('公称尺寸', key + '_nominal', 'number', 'step="any"') + steelInput('下偏差', key + '_lower', 'number', 'step="any"') + steelInput('上偏差', key + '_upper', 'number', 'step="any"') + '</div>' +
    '<div class="steel-input"><label for="steel-reading-' + key + '">实际读数（多次测量用顿号或逗号分隔）</label>' + steelVoiceWrap(key, '<input id="steel-reading-' + key + '" class="steel-reading" inputmode="text" data-steel-value="' + key + '" value="' + esc(steelValue(key)) + '" placeholder="' + (key === 'thickness' ? '例如 5.64、5.68' : '例如 1811、1812') + '" ' + (disabled ? 'disabled' : '') + '>') + '</div>' +
    '<div class="row"><span class="hint grow">' + esc(row.note || '所有读数按本单绑定的规格标准逐一判定。') + '</span>' +
    (!disabled && meta.source === 'asr' && !meta.confirmed ? '<button class="btn sm" data-steel-act="confirm" data-key="' + key + '">核对确认</button>' : '') + '</div></div>';
}

function steelRecordingHere() {
  return !!(Steel.record && S.recording && S.recTarget && S.recTarget.extra && String(S.recTarget.extra.record_id)===String(Steel.record.id));
}
function steelRecorderStatus(recording) {
  return Steel.waiting ? '正在识别…' : Steel.syncing ? '正在同步文字…' : Steel.saving ? '正在保存…' : Steel.starting ? (REC.phase==='audio' ? '正在启动录音…' : '等待麦克风权限…') : recording ? '● 录音中 · 点麦克风停止' : S.micProblem ? '录音未启动 · 查看' : '';
}
function steelRecordingFieldVisible() {
  const key=S.recTarget && S.recTarget.extra && S.recTarget.extra.current_key;
  if(!key)return false;
  const button=$('[data-steel-voice-target="' + key + '"]');
  if(!button || !button.getBoundingClientRect)return false;
  const box=button.getBoundingClientRect();
  if(!box.width || !box.height)return false;
  const topbar=$('#topbar'),dock=$('#dock');
  const top=topbar && topbar.getBoundingClientRect ? topbar.getBoundingClientRect().bottom : 0;
  const viewport=window.visualViewport;
  const viewportBottom=viewport ? viewport.offsetTop+viewport.height : window.innerHeight;
  const dockBox=dock && dock.getBoundingClientRect ? dock.getBoundingClientRect() : null;
  const bottom=dockBox && dockBox.height ? Math.min(viewportBottom,dockBox.top) : viewportBottom;
  if(box.top<top || box.bottom>bottom || box.left<0 || box.right>window.innerWidth)return false;
  if(document.elementFromPoint){
    const hit=document.elementFromPoint(box.left+box.width/2,box.top+box.height/2);
    if(hit!==button && !(button.contains && button.contains(hit)))return false;
  }
  return true;
}
function steelUpdateRecorderSafety() {
  let card=document.getElementById('steel-recorder-safety');
  const route=parseRoute();
  const current=Steel.record && route.name==='steel' && String(route.id)===String(Steel.record.id) && !steelReadonly();
  const processing=current && !!Steel.waiting;
  const recording=current && !processing && steelRecordingHere(),opening=current && !processing && Steel.starting;
  if((!recording && !opening && !processing) || (recording && steelRecordingFieldVisible())){if(card){card.hidden=true;card.style.display='none';}return;}
  if(!card){card=document.createElement('div');card.id='steel-recorder-safety';document.body.appendChild(card);}
  const extra=S.recTarget && S.recTarget.extra;
  const target=steelTarget(recording && extra ? extra.current_key : Steel.current);
  card.hidden=false;
  card.className='steel-recording-pill' + (recording ? ' is-recording' : processing ? ' is-processing' : ' is-opening');
  // Keep this one-line fallback independent of the footer positioning. Its
  // label is click-through; only the 44px stop/cancel action intercepts taps.
  card.style.cssText='display:flex;align-items:center;gap:6px;position:fixed;top:72px;top:calc(72px + env(safe-area-inset-top));right:10px;left:auto;max-width:calc(100vw - 20px);height:50px;margin:0;padding:3px 4px 3px 10px;box-sizing:border-box;z-index:2147483000;background:#fff;border:1px solid #d9e1e8;border-radius:999px;box-shadow:0 2px 8px #0002;color:#183247;font:13px/1.4 sans-serif;pointer-events:none;';
  const label=processing ? '正在识别…' : recording ? '● 录音中 · ' + (target ? target.label : '') : '正在打开麦克风…';
  const action=opening ? 'safety-cancel' : 'safety-stop';
  const name=processing ? '正在识别，请稍候' : opening ? '取消打开麦克风' : '停止录音并识别';
  card.innerHTML='<span class="steel-recording-pill-label" role="status">' + esc(label) + '</span><button type="button" class="steel-recording-pill-action" data-steel-act="' + action + '" aria-label="' + name + '" title="' + name + '"' + (processing ? ' disabled' : '') + ' style="pointer-events:auto;flex:0 0 44px;width:44px;height:44px;min-height:44px;min-width:44px;padding:0;border:0;border-radius:50%;font:13px sans-serif;">' + (processing ? '…' : opening ? '取消' : '■') + '</button>';
}
function steelRefreshDockState() {
  const route=parseRoute();
  if(!Steel.record || route.name!=='steel' || String(route.id)!==String(Steel.record.id))return;
  const processing=!!(Steel.waiting || Steel.saving || Steel.syncing);
  const recording=!processing && steelRecordingHere();
  const extra=S.recTarget && S.recTarget.extra;
  const recordingKey=recording && extra ? extra.current_key : null;
  const hint=$('.steel-dock-toggle .hint');
  if(hint)hint.textContent=steelRecorderStatus(recording) || (Steel.dockExpanded ? '收起 ▾' : '展开 ▴');
  document.querySelectorAll('[data-steel-voice-target]').forEach((button)=>{
    const key=button.dataset.steelVoiceTarget || button.dataset.key,active=recording && key===recordingKey;
    const target=steelTarget(key),name=active ? '停止' + (target ? target.label : '') + '录音并识别' : '语音输入' + (target ? target.label : '');
    button.disabled=processing || Steel.starting || (S.recording && !active);
    button.classList.toggle('rec',active);button.classList.toggle('recording',active);
    button.textContent=active ? '■' : '🎤';
    if(button.setAttribute){button.setAttribute('aria-pressed',String(!!active));button.setAttribute('aria-label',name);button.setAttribute('title',name);}
  });
  document.querySelectorAll('[data-steel-act="mic"], [data-steel-act="mic-retry"], [data-steel-act="voice-rerecord"], [data-steel-act="audio-import"], [data-steel-act="mic-cancel"]').forEach((button)=>{
    const action=button.dataset.steelAct;
    button.disabled=processing || (action==='mic-cancel' ? !Steel.starting : action==='audio-import' ? S.recording : Steel.starting || (S.recording && action!=='mic'));
  });
  document.querySelectorAll('.steel-voice-target').forEach((box)=>box.classList.toggle('active',box.dataset.voiceField===Steel.current));
  steelUpdateRecorderSafety();
}
function steelDock() {
  const target=steelTarget(Steel.current) || steelTarget('document_no');
  const recording=steelRecordingHere();
  const busy=Steel.waiting || Steel.saving || Steel.starting || Steel.syncing;
  const open=Steel.dockExpanded;
  const status=steelRecorderStatus(recording);
  const live=Steel.starting || recording ? '<p class="steel-recording-state' + (recording ? ' is-recording' : '') + '" role="status">' + (recording ? '● 录音中，说完点红色麦克风停止。' : '正在申请麦克风，可点取消。') + '</p>' : '';
  const mic='<button class="mic' + (recording ? ' rec' : '') + '" data-steel-act="' + (Steel.starting ? 'mic-cancel' : 'mic') + '" aria-label="' + (Steel.starting ? '取消打开麦克风' : recording ? '停止录音' : '开始录音') + '"' + (busy && !Steel.starting ? ' disabled' : '') + '>' + (Steel.starting ? '取消' : recording ? '■' : '🎤') + '</button>';
  return '<div class="dock-inner steel-recorder ' + (open ? 'is-expanded' : 'is-collapsed') + '" data-ui-version="' + STEEL_UI_VERSION + '"><div class="steel-dock-peek"><button class="steel-dock-toggle" data-steel-act="dock-toggle" aria-expanded="' + open + '" aria-controls="steel-capture-panel"><b>口报：' + esc(target.label) + '</b><span class="hint">' + esc(status || (open ? '收起 ▾' : '展开 ▴')) + '</span></button>' + mic + '</div>' +
    '<section id="steel-capture-panel" class="steel-capture-panel"' + (open ? '' : ' hidden') + ' aria-label="语音与文字录入">' + live + steelMicProblemHTML(S.micProblem) +
    '<div class="steel-dock-heading"><b>当前口报：' + esc(target.label) + '</b><span class="hint">合格 / 不合格</span></div>' +
    '<div class="steel-dock-input"><input id="steel-say" value="' + esc(Steel.text) + '" aria-label="文字口报" enterkeyhint="done" autocomplete="off" placeholder="' + esc(target.placeholder || ('输入' + target.label + '，完成后自动同步')) + '"' + (Steel.waiting || Steel.saving || Steel.starting || recording ? ' disabled' : '') + '><button class="btn sm" data-steel-act="parse"' + (busy || recording ? ' disabled' : '') + '>同步</button></div>' +
    '<div id="steel-text-status" class="hint" role="status">' + esc(Steel.textError || '文字输入完成自动回填；录音结果预填后仍需核对。') + '</div>' +
    '<div class="steel-audio-tools"><button class="btn ghost sm" data-steel-act="audio-import"' + (Steel.waiting || Steel.saving || Steel.syncing || recording ? ' disabled' : '') + '>系统录音 / 导入</button><span class="hint">每段不超过30秒</span></div></section>' +
    '<div class="steel-mobile-actions"><button class="btn ghost" data-steel-act="save"' + (steelBusy() ? ' disabled' : '') + '>保存草稿</button><button class="btn" data-steel-act="finalize"' + (steelBusy() ? ' disabled' : '') + '>核对并生成表单</button></div></div>';
}

function steelRefreshDock() {
  steelUpdateRecorderSafety();
  const route=parseRoute();
  if(!Steel.record || steelReadonly() || route.name!=='steel' || String(route.id)!==String(Steel.record.id))return;
  const dock=$('#dock');if(dock)dock.innerHTML=appHTML(steelDock());
  document.body.classList.toggle('steel-dock-expanded',Steel.dockExpanded);
  document.body.classList.toggle('steel-dock-collapsed',!Steel.dockExpanded);
  steelSyncViewport();
  steelRefreshDockState();
}
function steelSetDock(open) {
  if(!open){
    steelCommitText(false);
    const input=$('#steel-say');if(input && document.activeElement===input)input.blur();
  }
  if(open){steelScrollPosition=window.scrollY || 0;steelScrollIntentUntil=0;}
  Steel.dockExpanded=!!open;steelRefreshDock();
}
function steelFieldElement(key) {
  const target=steelReviewTarget(key);
  if(!target)return null;
  if(target.destination==='extra')return $('[data-steel-extra="' + target.extra_index + '"][data-prop="' + target.extra_prop + '"]');
  return $('[data-steel-' + (target.destination==='value' ? 'value' : 'header') + '="' + key + '"]');
}
function steelRefreshField(key) {
  const el=steelFieldElement(key);if(el){el.value=steelCandidateText(steelDisplayValue(key));steelMarkControl(el);}
}
function steelControlValue(el) { return el.type==='checkbox' ? !!el.checked : String(el.value == null ? '' : el.value); }
function steelMarkControl(el) { el.__steelSyncedValue=steelControlValue(el); }
function steelInitialControlValue(el) {
  if(Object.prototype.hasOwnProperty.call(el,'__steelSyncedValue'))return el.__steelSyncedValue;
  if(el.type==='checkbox')return el.hasAttribute('checked');
  if(el.tagName==='SELECT'){
    const option=el.querySelector('option[selected]') || el.querySelector('option');
    return option ? option.value : '';
  }
  return el.getAttribute('value') || '';
}
function steelCollectForm() {
  const route=parseRoute();
  if(steelReadonly() || Steel.finalizing || route.name!=='steel' || String(route.id)!==String(Steel.record.id))return;
  const form=document.querySelector('.steel-workspace[data-steel-record-id="' + Steel.record.id + '"] .steel-form');
  if(!form)return;
  // Android autofill and some IMEs update value without dispatching input.
  // Compare with the rendered/last-handled value, so untouched ASR previews
  // remain unconfirmed and a stale DOM cannot overwrite newer model state.
  const edits=Array.from(form.querySelectorAll('[data-steel-header], [data-steel-value], [data-steel-extra], [data-steel-candidate]'))
    .filter((el)=>!el.disabled && steelControlValue(el)!==steelInitialControlValue(el))
    .map((el)=>({el,value:el.value,checked:el.checked}));
  edits.forEach(({el,value,checked})=>{
    el.value=value;if(el.type==='checkbox')el.checked=checked;
    steelInputEvent({target:el});
  });
}
function steelTouchField(key) { Steel.fieldVersions[key]=(Steel.fieldVersions[key] || 0)+1; }
function steelManualValue(key,value,raw) {
  const target=steelTarget(key);if(!target)return;
  if(target.destination==='value')Steel.data.values[key]={value,source:'manual',confidence:1,confirmed:true,raw:raw || ''};
  else if(target.destination==='extra'){
    while(Steel.data.extra_rows.length<=target.extra_index)Steel.data.extra_rows.push({name:'',requirement:'',result:'',verdict:''});
    Steel.data.extra_rows[target.extra_index][target.extra_prop]=value;
  } else Steel.data[key]=value;
  steelTouchField(key);steelRemoveCandidate(key);steelChanged();steelRefreshField(key);
  const card=$('[data-voice-candidate="' + key + '"]');if(card && card.remove)card.remove();
  const note=$('[data-steel-pending="' + key + '"]');if(note && note.remove)note.remove();
}
async function steelCommitText(feedback) {
  clearTimeout(Steel.textTimer);
  if(!Steel.textDirty || Steel.textComposing || steelReadonly())return true;
  const selection=Steel.textTarget;
  if(!selection || selection.record_id!==Steel.record.id || !steelTarget(selection.key))return false;
  const key=selection.key,text=Steel.text.trim(),epoch=Steel.textEpoch,session=Steel.textSession;
  const existing=Steel.textJobs[key];if(existing && existing.epoch===epoch)return existing.promise;
  const job={epoch,fieldVersion:Steel.fieldVersions[key] || 0,promise:null,controller:typeof AbortController!=='undefined' ? new AbortController() : null};
  Steel.textJobs[key]=job;Steel.syncing=true;steelRefreshDockState();
  const current=()=>Steel.record && Steel.record.id===selection.record_id && parseRoute().name==='steel' && String(parseRoute().id)===String(selection.record_id) && Steel.textSession===session && Steel.textJobs[key]===job && (Steel.fieldVersions[key] || 0)===job.fieldVersion && !(Steel.textTarget && Steel.textTarget.key===key && Steel.textEpoch!==epoch);
  const status=(message)=>{if(Steel.current===key){Steel.textError=message;const hint=$('#steel-text-status');if(hint)hint.textContent=message;}};
  job.promise=(async()=>{
    try {
      status('正在同步文字…');
      let timeout;
      const request=text ? api('/api/steel/asr/parse',{method:'POST',body:{text,current_key:key},signal:job.controller ? job.controller.signal : undefined}) : Promise.resolve({assignments:[{key,value:''}]});
      let result;
      try {
        result=await Promise.race([request,new Promise((_,reject)=>{timeout=setTimeout(()=>{reject(new Error('文字同步超时，请检查连接后重试'));if(job.controller)job.controller.abort();},10000);})]);
      } finally {clearTimeout(timeout);}
      if(!current())return false;
      const assignment=(result.unknown || []).length ? null : (result.assignments || []).find((item)=>item.key===key);
      if(!assignment){status('未同步：请按当前字段填写有效内容');if(feedback)toast('请检查当前字段的输入内容',true);return false;}
      steelManualValue(key,assignment.value,text);
      if(Steel.textEpoch===epoch && Steel.textTarget===selection)Steel.textDirty=false;
      status('已同步到' + steelTarget(key).label);
      await steelEvaluate();return true;
    } catch(error){if(current()){status('未同步：'+error.message);if(feedback)toast(error.message,true);}return false;}
    finally {
      if(Steel.textJobs[key]===job)delete Steel.textJobs[key];
      Steel.syncing=Object.keys(Steel.textJobs).length>0;
      if(!Steel.syncing && !steelBusy())document.querySelectorAll('#dock button, [data-steel-voice-target]').forEach((button)=>{button.disabled=false;});
      steelRefreshDockState();
    }
  })();
  return job.promise;
}

VIEWS.steel = async function (id) {
  if (!S.me) return VIEWS.login(); // preserve the requested record across login
  if (!Steel.template) Steel.template = await api('/api/steel/template');
  if (!id) {
    if(S.me.role==='process')return VIEWS['steel-standards']();
    const ledger=await steelLedgerHTML();
    const catalog=await api('/api/steel/standards');
    Steel.standards=catalog.standards;
    const newControls=['inspector','supervisor'].includes(S.me.role)
      ? '<div class="card"><label for="steel-new-standard">本批采用的标准</label>'+steelStandardSelect('steel-new-standard',Steel.standards)+
        '<button class="btn" data-steel-act="new">新建检验记录</button></div>' : '';
    return {html:'<div class="wrap steel-index"><div class="page-title">钢板进货检验 <a class="btn ghost sm" href="#/home">返回任务</a></div>'+newControls+ledger+'</div>'};
  }
  if (!Steel.record || String(Steel.record.id) !== String(id)) {
    Steel.evalRequest++;
    Steel.record = (await api('/api/steel/records/' + id)).record;
    Steel.data = steelClone(Steel.record.data);
    Steel.data.voice_candidates = Steel.data.voice_candidates || [];
    Steel.data.voice_pending = Steel.data.voice_pending || [];
    Steel.current = (Steel.data.voice_candidates.find((a)=>steelTarget(a.key)) || {}).key || 'document_no';
    Steel.revision++;
    Steel.dockExpanded=false;Steel.textDirty=false;Steel.textTarget=null;Steel.textJobs={};Steel.fieldVersions={};Steel.textSession++;Steel.syncing=false;Steel.textError='';
    Steel.evaluation = Steel.record.evaluation;
    Steel.dirty = false;
    Steel.text = '';Steel.saveError='';Steel.syncError='';steelRestoreDraft();
  }
  const r = Steel.record;
  const readonly = steelReadonly();
  if(!r.standard && !readonly)Steel.standards=(await api('/api/steel/standards')).standards;
  const issues = Steel.evaluation.issues || [];
  function issueLink(issue) {
    const key = issue.key;
    let field = 'steel-' + key;
    if (steelCandidate(key)) field = 'steel-review-block';
    else if (Steel.template.verify.some((item)=>item.key===key)) field = 'steel-verify';
    else if (Steel.template.appearance.some((item)=>item.key===key)) field = 'steel-appearance';
    else if (key==='thickness' || key==='width') field = 'steel-reading-' + key;
    else if (key==='extra_rows') field = 'steel-extra-rows';
    return '<li><a href="#' + esc(field) + '" data-steel-section="' + esc(field) + '">' + esc(issue.message) + '</a></li>';
  }
  const submitError = Steel.saveError ? '<div class="steel-submit-error" role="alert"><strong>' + esc(Steel.saveError) + '</strong>' +
    (issues.length ? '<p>请核对以下 ' + issues.length + ' 处，再生成 Excel：</p><ul>' + issues.map(issueLink).join('') + '</ul>' : '') + '</div>' : '';
  const downloadCard = r.status === 'final' ? '<section class="card steel-download-card" aria-label="正式 Excel 下载"><h3>检验记录已生成</h3>' +
    '<p>单据号 <strong>' + esc(r.data.document_no) + '</strong> · 记录 #' + r.id + '</p>'+steelFileActions(r.id,'下载原版式 Excel',r.data.document_no)+'</section>' : '';
  const preview = !readonly
    ? '<button type="button" class="btn ghost" data-steel-act="preview">预览当前草稿 / 打印</button>'
    : '<a class="btn ghost" href="#/steel-preview/' + r.id + '">打开表单预览 / 打印</a>';
  const confirmation = '<section class="card steel-readonly-label"><h3>' + (r.status==='final' ? '质量工程师确认' : '保存与生成 Excel') + '</h3>' +
    (readonly ? (r.status==='final' ? '<p>' + esc(r.inspector_name) + ' · ' + esc(r.signed_at) + '</p>' : '') : '<p class="steel-save-state" role="status">' +
      esc(Steel.syncError || (Steel.dirty ? '正在等待保存草稿…' : '草稿已保存')) + '</p>' + submitError +
      '<div class="steel-submit-actions"><button class="btn ghost" data-steel-act="save">保存草稿</button>' +
      '<button class="btn" data-steel-act="finalize">生成检验记录 Excel</button></div>') +
    '<div class="steel-record-tools">' + preview + (!readonly ? '<button type="button" class="btn ghost" data-steel-act="voice-history">查看历史识别原话</button>' : '') +
    '</div>' + (!readonly ? '<div class="steel-voice-history" aria-live="polite"></div>' : '') + '</section>';
  const extras = (Steel.data.extra_rows || []).map((entry,i) => {
    const row = Object.assign({},(Steel.data.extra_rows || [])[i] || {});
    ['result','verdict'].forEach((prop)=>{const candidate=steelCandidate('extra_' + i + '_' + prop);if(candidate)row[prop]=candidate.value;});
    return '<div class="steel-extra-row">' + [['name','尺寸名称'],['requirement','标准要求'],['result','检测结果'],['verdict','人工判定']].map(([k,label]) => {
      const key = 'extra_' + i + '_' + k;
      const attr = ' data-steel-extra="' + i + '" data-prop="' + k + '"' + (readonly || steelStandardField(key) ? ' disabled' : '');
      const control = k === 'verdict' ? '<select id="steel-' + key + '"' + attr + '>' + ['', '合格', '不合格'].map((v) => '<option value="' + v + '"' + (row[k] === v ? ' selected' : '') + '>' + (v || '请选择') + '</option>').join('') + '</select>' : '<input id="steel-' + key + '"' + attr + ' value="' + esc(row[k] || '') + '">';
      return '<div class="steel-input"><label for="steel-' + key + '">' + label + '</label>' + steelVoiceWrap(key,control) + '</div>';
    }).join('') + '</div>';
  }).join('');
  return {html:'<div class="steel-workspace" data-steel-record-id="' + r.id + '"><div class="steel-workhead"><div><a class="hint" href="#/steel">← 全部钢板记录</a><h2>进货检验记录 <span class="pill ' + (r.status === 'final' ? 'ok' : 'warn') + '">' + (r.status === 'final' ? '已确认' : '草稿') + '</span></h2><div class="hint">记录 #' + r.id + ' · ' + Steel.template.record_code + ' · <span class="steel-snapshot">' + esc(r.snapshot) + '</span></div></div><div class="row wrap ' + (readonly ? '' : 'steel-workhead-actions') + '">' +
    (r.status === 'final' && ['inspector','supervisor'].includes(S.me.role) ? '<button class="btn ghost" data-steel-act="revise">' + (r.followup_record_id ? (r.followup_status === 'final' ? '查看后续表单' : '继续填写后续表单') : '追加复检') + '</button>' : !readonly ? '<button class="btn ghost" data-steel-act="save">保存并刷新预览</button><button class="btn" data-steel-act="finalize">确认检验并生成 Excel</button>' : '') +
    (r.can_takeover ? '<button class="btn ghost" data-steel-act="takeover">接手已停用账号的草稿</button>' : '') +
    '</div></div><div class="steel-columns"><div class="steel-form">' + downloadCard + steelStandardBox(r,readonly) +
    (r.parent_id && r.status === 'draft' ? '<section class="card steel-followup-note" role="status"><b>这是需要重新填写的后续草稿</b><p>上一份已生成的表单仍保留。当前草稿沿用材料和标准信息，请重新录入检测结果及检验数量。</p><div class="row wrap"><a class="btn ghost sm" href="#/steel/' + r.parent_id + '">查看上一份已生成表单</a>'+steelFileActions(r.parent_id,'下载上一份 Excel','')+'</div></section>' : '') +
    (r.status === 'final' && r.evaluation.overall === 'fail' ? '<section class="card steel-followup-note" role="status"><b>本次检验不合格</b><p>本表已保存，可下载不合格记录。' + (r.followup_record_id ? (r.followup_status === 'final' ? '后续表单已填写完成。' : '已建立后续草稿，请再填写一份表单。') : '请追加一份检验表单。') + '</p>' + (r.followup_record_id ? '<a class="btn" href="#/steel/' + r.followup_record_id + '">' + (r.followup_status === 'final' ? '查看后续表单' : '继续填写后续表单') + '</a>' : '<button class="btn" data-steel-act="revise">填写后续表单</button>') + '</section>' : '') +
    (Steel.data.is_example ? '<div class="card steel-example-note"><b>员工来表示例</b><p>当前数据来自员工发来的样表，用于核对流程和输出格式，不代表本次实际检验。</p></div>' : '') +
    '<div id="steel-review-block">' + steelReview() + '</div><nav class="steel-section-nav" aria-label="跳转检验栏目"><a href="#steel-material" data-steel-section="steel-material">材料</a><a href="#steel-verify" data-steel-section="steel-verify">资料</a><a href="#steel-appearance" data-steel-section="steel-appearance">外观</a><a href="#steel-dimensions" data-steel-section="steel-dimensions">尺寸</a><a href="#steel-batch" data-steel-section="steel-batch">结论</a></nav>' +
    '<section class="card" id="steel-material"><h3>1. 批次与材料</h3><div class="steel-header-grid">' + steelInput('单据号','document_no') + steelInput('产品名称','product_name') + steelInput('规格型号','specification') + steelInput('生产单位','manufacturer') + steelInput('供货数量（件，钢卷按卷计）','supply_quantity','number','min="1" step="1"') + steelInput('牌号','grade') + steelInput('炉批号（选填）','heat_no') + steelInput('检验日期','inspection_date','date') + '</div><p class="hint">本单按所选标准的公称尺寸和公差判定。</p></section>' +
    '<section class="card" id="steel-verify"><h3>2. 资料验证</h3>' + Steel.template.verify.map(steelCheck).join('') + '</section>' +
    '<section class="card" id="steel-appearance"><h3>3. 外观检查</h3>' + Steel.template.appearance.map(steelCheck).join('') + '</section>' +
    '<section class="card" id="steel-dimensions"><h3>4. 主要外形（配合）尺寸</h3>' + steelDimension('thickness','厚度') + steelDimension('width','宽度') + (extras ? '<details><summary id="steel-extra-rows">补充尺寸</summary>' + extras + '</details>' : '') + '</section>' +
    '<section class="card" id="steel-batch"><h3>5. 批量检验结论</h3><div class="steel-header-grid">' + steelSelect('检验方式','inspection_mode',[['full','100%检验'],['sampling','抽样检验']]) + steelInput('实际检验数量','inspected_quantity','number','min="1" step="1"') + steelInput('合格数量','qualified_quantity','number','min="0" step="1"') + steelInput('不合格数量','rejected_quantity','number','min="0" step="1"') + steelInput('抽样依据','sampling_basis') + steelSelect('抽检不合格后的处理去向','disposition',[['','无/待选择'],['加倍抽检','加倍抽检'],['转全检','转全检'],['拒收','拒收'],['评审后处理','评审后处理']]) + '</div>' +
    '<div class="steel-additional">' + steelVoiceWrap('additional_failure_confirmed','<label><input type="checkbox" data-steel-header="additional_failure_confirmed"' + (Steel.data.additional_failure_confirmed ? ' checked' : '') + (readonly ? ' disabled' : '') + '> 已完成追加检验，且追加检验仍发现不合格（选择转全检/拒收/评审时确认）</label>') + '</div><p class="steel-source-note">' + esc(r.status === 'final' ? (r.data.sampling_note || Steel.template.legacy_sampling_note || Steel.template.sampling_note) : Steel.template.sampling_note).replace(/\n/g,'<br>') + '</p></section>' +
    '<section class="card steel-summary">' + steelSummary() + '</section>' +
    confirmation + '</div>' +
    '<aside class="steel-preview"><div class="steel-preview-head"><b>' + (r.status==='final' ? '已生成表单 · 记录 #' + r.id : '草稿预览 · 记录 #' + r.id) + '</b><span>' + (r.status==='final' ? '本次确认数据' : Steel.dirty ? '保存后更新预览' : '已保存的草稿数据') + '</span></div><iframe title="进货检验记录预览" ' + (Steel.dirty ? 'hidden' : 'src="'+steelPreviewPath(r)+'"') + '></iframe></aside></div></div>',
    dock: readonly ? null : steelDock()};
};

async function steelEvaluate() {
  if (!Steel.record || Steel.record.status === 'final') return;
  const recordId = Steel.record.id;
  const requestId = ++Steel.evalRequest;
  try {
    const response = await api('/api/steel/evaluate', {method:'POST', body:{data:Steel.data}});
    if (!Steel.record || Steel.record.id !== recordId || requestId !== Steel.evalRequest) return;
    Steel.evaluation = response.evaluation;
    const box = $('.steel-summary');
    if (box) box.innerHTML = steelSummary();
    (Steel.evaluation.rows || []).forEach((row) => {
      const el = $('[data-steel-row="' + row.key + '"] .steel-field-status');
      if (el) { const status=steelCandidate(row.key) ? 'pending' : row.status;el.textContent = steelState(status); el.className = 'steel-field-status ' + status; }
    });
  } catch (e) {
    if (!Steel.record || Steel.record.id !== recordId || requestId !== Steel.evalRequest) return;
    const box = $('.steel-summary'); if (box) box.textContent = '请检查输入：' + e.message;
  }
}

function steelChanged() {
  Steel.dirty = true;
  Steel.saveError='';Steel.syncError='';
  Steel.revision++;
  Steel.evalRequest++;
  steelRememberDraft();steelScheduleSave();steelPaintSaveState();
  clearTimeout(Steel.timer);
  Steel.timer = setTimeout(steelEvaluate, 350);
}

async function steelSave(finalize, quiet) {
  if (Steel.saving || steelReadonly()) return false;
  steelCollectForm();
  clearTimeout(steelAutoTimer);
  const id = Steel.record.id, revision = Steel.revision;
  const data = steelClone(Steel.data);
  Steel.saving = true;
  Steel.finalizing = !!finalize;
  if (finalize) document.querySelectorAll('.steel-form input, .steel-form select, .steel-form button').forEach((el) => { el.disabled=true; });
  try {
    if(steelAutoJob && steelAutoJob.id===id)await steelAutoJob.promise;
    if(!Steel.record || Steel.record.id!==id || parseRoute().name!=='steel' || String(parseRoute().id)!==String(id))return false;
    // Preserve this exact draft before requesting a signature. Also works
    // when a LAN server started before the latest backend update is still running.
    if(finalize && Steel.dirty){
      const draft=await steelWriteRecord('/api/steel/records/'+id,'PUT',{version:Steel.record.version,data});
      steelAcceptSaved(draft.record,revision,false);
      if(!Steel.record || Steel.record.id!==id || parseRoute().name!=='steel' || String(parseRoute().id)!==String(id))return false;
    }
    const version=Steel.record.version;
    const response = await steelWriteRecord('/api/steel/records/' + id + (finalize ? '/finalize' : ''),finalize ? 'POST' : 'PUT',{version,data});
    if (!Steel.record || Steel.record.id !== id || parseRoute().name !== 'steel' || String(parseRoute().id) !== String(id)) return false;
    steelAcceptSaved(response.record,revision,true);
    if (Steel.revision === revision) {
      Steel.evalRequest++;
      clearTimeout(Steel.timer);
    } else { Steel.dirty = true; await steelEvaluate(); }
    Steel.saveError='';
    if (!quiet) toast(finalize ? (response.followup ? '不合格表单已生成，核对后请继续填写后续表单' : '检验记录已确认，可下载原版式 Excel') : '已保存草稿');
    return true;
  } catch(error) {
    if(Steel.record && Steel.record.id===id){
      if(error.data && error.data.draft_saved && error.data.record)steelAcceptSaved(error.data.record,revision,true);
      Steel.saveError=(finalize ? (Steel.dirty ? '未生成 Excel，本次输入尚未保存：' : '未生成 Excel，已保留草稿：') : '保存未完成：')+error.message;
      if(error.data && error.data.evaluation){Steel.evaluation=error.data.evaluation;Steel.evalRequest++;clearTimeout(Steel.timer);}
    }
    throw error;
  } finally {
    Steel.saving = false;
    Steel.finalizing = false;
    if(Steel.record && Steel.record.id===id && Steel.dirty && Steel.revision!==revision)steelScheduleSave();
    if (!quiet && Steel.record && Steel.record.id === id && parseRoute().name === 'steel') {
      await render();
      const notice=$('.steel-submit-error') || (finalize && Steel.record.status==='final' && $('.steel-download-card'));
      if(notice)notice.scrollIntoView({block:'center',behavior:'smooth'});
    }
  }
}

async function steelApplyVoice(result) {
  const route = parseRoute();
  if (steelReadonly() || (result.record_id && String(result.record_id) !== String(Steel.record.id)) || route.name !== 'steel' || String(route.id) !== String(Steel.record.id) || (result.edit_revision != null && result.edit_revision !== Steel.revision)) {
    toast('记录或内容已改变，本次语音未写入，请重新口报', true); return;
  }
  const assignments = (result.assignments || []).filter((a) => steelTarget(a.key));
  if(assignments.length)S.micProblem=null;
  assignments.forEach((a) => {
    steelRemoveCandidate(a.key);
    Steel.data.voice_candidates.push(Object.assign({}, a, {confirmed:false, source:'asr'}));
    Steel.data.voice_pending.push(a.key);
  });
  Steel.text = result.text || '';
  Steel.textDirty=false;Steel.textTarget=null;Steel.textError='';Steel.dockExpanded=false;
  if (assignments.length || result.audio_id) Steel.data.evidence.push({audio_id:result.audio_id || '',text:result.text || '',at:new Date().toISOString()});
  if (assignments.length || result.audio_id) steelChanged();
  await steelEvaluate();
  await render();
  const review = $('.steel-voice-review');
  if (assignments.length && review) review.scrollIntoView({block:'start',behavior:'smooth'});
  toast(assignments.length ? '已识别 ' + assignments.length + ' 项，请核对确认' : (result.note || '未识别到对应字段，请重报'), !assignments.length);
}

function steelInputEvent(e) {
  const el = e.target;
  if (el.id === 'steel-say') {
    Steel.text=el.value;Steel.textDirty=true;Steel.textEpoch++;Steel.textError='';
    Steel.textTarget={record_id:Steel.record.id,key:Steel.current};
    clearTimeout(Steel.textTimer);if(!e.isComposing && !Steel.textComposing)Steel.textTimer=setTimeout(()=>steelCommitText(false),800);return;
  }
  if (!Steel.data || steelReadonly() || Steel.finalizing) return;
  if (el.dataset.steelCandidate) {
    const a = (Steel.data.voice_candidates || []).find((a) => a.key === el.dataset.steelCandidate);
    if (a) { a.value = el.value; steelTouchField(a.key);steelChanged();steelRefreshField(a.key);steelMarkControl(el); }
    return;
  }
  let key;
  if (el.dataset.steelHeader) {
    key = el.dataset.steelHeader;
    Steel.data[key] = el.type === 'checkbox' ? el.checked : (el.type === 'number' ? (el.value === '' ? null : Number(el.value)) : el.value);
  } else if (el.dataset.steelValue) {
    key = el.dataset.steelValue;
    Steel.data.values[key] = {value:el.value,source:'manual',confidence:1,confirmed:true};
  } else if (el.dataset.steelExtra !== undefined) {
    const index = Number(el.dataset.steelExtra);
    key = 'extra_' + index + '_' + el.dataset.prop;
    while (Steel.data.extra_rows.length <= index) Steel.data.extra_rows.push({name:'',requirement:'',result:'',verdict:''});
    Steel.data.extra_rows[index][el.dataset.prop] = el.value;
  } else return;
  steelMarkControl(el);
  steelTouchField(key);steelRemoveCandidate(key);
  const card = $('[data-voice-candidate="' + key + '"]');
  if (card && card.remove) card.remove();
  const pendingNote=$('[data-steel-pending="' + key + '"]');if(pendingNote && pendingNote.remove)pendingNote.remove();
  steelChanged();
}
document.addEventListener('input',steelInputEvent);
// Android WebView versions may emit change without input for select/date controls.
document.addEventListener('change',(event)=>{
  const el=event.target;
  if(el.id==='steel-say' && el.value===Steel.text)return;
  if(el.id!=='steel-say' && Object.prototype.hasOwnProperty.call(el,'__steelSyncedValue') && steelControlValue(el)===el.__steelSyncedValue)return;
  steelInputEvent(event);
});
document.addEventListener('focusin',(e) => {
  const el=e.target;
  const key=el.dataset.steelHeader || el.dataset.steelValue || (el.dataset.steelExtra !== undefined ? 'extra_' + el.dataset.steelExtra + '_' + el.dataset.prop : null);
  if (!key || !steelTarget(key) || Steel.waiting || Steel.starting || Steel.saving || S.recording) return;
  if(Steel.current!==key){steelCommitText(false);Steel.text='';Steel.textDirty=false;Steel.textTarget=null;Steel.textError='';}
  Steel.current=key;
  document.querySelectorAll('.steel-voice-target').forEach((box) => box.classList.toggle('active',box.dataset.voiceField === key));
  const dock=$('#dock'); if (dock && !steelReadonly()) dock.innerHTML=appHTML(steelDock());
  steelSyncViewport();
});

async function steelRecordVoice(key) {
  if (Steel.waiting || Steel.starting || Steel.saving || Steel.syncing) { toast('正在处理，请稍候'); return; }
  if (S.recording) {
    Steel.waiting=true;Steel.dockExpanded=false;steelRefreshDock();
    try { await finishRec(); } finally { Steel.waiting=false; await render();steelRefreshDockState(); }
    return;
  }
  if (steelReadonly() || !steelTarget(key)) return;
  const recordId=Steel.record.id, revision=Steel.revision;
  const routeId=String(parseRoute().id);
  const attempt=++Steel.micAttempt;
  clearTimeout(Steel.textTimer);Steel.textDirty=false;Steel.textTarget=null;
  Steel.current=key;Steel.dockExpanded=false;steelScrollPosition=window.scrollY || 0;steelScrollIntentUntil=0;
  Steel.starting=true;
  try {
    // Establish visible DOM immediately in the tap, before native browser
    // permission/audio bridges can suspend or throw. Do not await a render.
    S.micProblem=null;
    steelRefreshDock();
    // This call starts browser permission + AudioContext synchronously in
    // the tap. The record already exists; audio does not require a prior PUT.
    await startRec({url:'/api/steel/asr/audio',extra:{record_id:recordId,current_key:key},
      isCurrent:() => !steelReadonly() && Steel.record.id === recordId && String(parseRoute().id) === routeId,
      onState:(phase) => {
        if(Steel.micAttempt!==attempt || !Steel.record || Steel.record.id!==recordId || parseRoute().name!=='steel' || String(parseRoute().id)!==routeId)return;
        if(phase==='recording'){
          Steel.starting=false;steelScrollPosition=window.scrollY || 0;steelScrollIntentUntil=0;
        }
        steelRefreshDock();
      },
      onDone:(result) => steelApplyVoice(Object.assign({},result,{record_id:recordId,edit_revision:revision}))});
  } finally {
    if(Steel.micAttempt===attempt){
      Steel.starting=false;
      if(!S.recording && S.micProblem)Steel.dockExpanded=true;
      steelRefreshDock();
    }
  }
}

document.addEventListener('click',async(e) => {
  const back=e.target.closest('[data-steel-preview-back]');
  if(back){
    e.preventDefault();
    if(history.state && history.state.steelPreviewFrom===parseRoute().id)history.back();
    else location.replace(back.href);
    return;
  }
  const section=e.target.closest('[data-steel-section]');
  if (section) { e.preventDefault(); const dest=document.getElementById(section.dataset.steelSection); if(dest)dest.scrollIntoView({block:'start',behavior:'smooth'}); return; }
  const el=e.target.closest('[data-steel-act]'); if (!el || el.disabled) return;
  try {
    const act=el.dataset.steelAct;
    if(act==='voice-history'){
      const box=el.closest('.steel-form').querySelector('.steel-voice-history');
      el.disabled=true;
      try {
        const result=await api('/api/steel/records/'+Steel.record.id+'/voice-history');
        box.innerHTML=result.entries.length ? '<p>以下是识别原话，请核对后手动补填。</p><ul>'+result.entries.map((entry)=>'<li>'+esc(entry.at)+' · '+esc(entry.field_label || '字段未知')+'：'+esc(entry.text)+'</li>').join('')+'</ul>' : '这份记录尚无已留存的识别原话。';
      }catch(error){box.textContent=error.status===404 ? '请先保存当前草稿，重启电脑服务后可查看历史原话。' : '无法读取：'+error.message;}
      finally{el.disabled=false;}
      return;
    }
    if(act==='new') {
      const response=await api('/api/steel/records',{method:'POST',body:{standard_id:Number(document.getElementById('steel-new-standard').value)}});
      Steel.record=null; go('#/steel/'+response.record.id);
    } else if(act==='save'||act==='finalize'||act==='preview') {
      const savedRecordId=Steel.record.id;
      if(Steel.textDirty && !(await steelCommitText(true)))return;
      await Promise.all(Object.values(Steel.textJobs).map((job)=>job.promise));
      if(!Steel.record || Steel.record.id!==savedRecordId)return;
      if(steelBusy()) { toast('请先结束录音并等待处理完成',true);return; }
      if(document.activeElement && document.activeElement.blur)document.activeElement.blur();
      if(act==='preview'){
        steelCollectForm();
        if(Steel.dirty && !(await steelSave(false,true)))return;
        if(!Steel.record || Steel.record.id!==savedRecordId || Steel.dirty)throw new Error('输入已改变，请再次打开最新预览。');
        go('#/steel-preview/'+savedRecordId);
      }else await steelSave(act==='finalize');
    } else if(act==='takeover') {
      const id=Steel.record.id;
      await api('/api/steel/records/'+id+'/takeover',{method:'POST',body:{version:Steel.record.version}});
      Steel.record=null;await render();
    } else if(act==='revise') {
      if(Steel.record.followup_record_id){go('#/steel/'+Steel.record.followup_record_id);return;}
      const response=await api('/api/steel/records/'+Steel.record.id+'/revise',{method:'POST',body:{}});
      Steel.record=null;go('#/steel/'+response.record.id);
    } else if(act==='confirm') {
      if(steelReadonly()||steelBusy())return;
      if(steelManualCheck(el.dataset.key)){toast('请在对应检验项手动选择结果',true);return;}
      Steel.data.values[el.dataset.key].confirmed=true;steelChanged();await steelEvaluate();await render();
    } else if(act==='voice-apply'||act==='voice-discard') {
      if(steelReadonly()||steelBusy())return;
      steelTouchField(el.dataset.key);
      if(act==='voice-apply')steelConfirmCandidate(el.dataset.key);
      else {steelRemoveCandidate(el.dataset.key);steelChanged();}
      await steelEvaluate();await render();
    } else if(act==='dock-toggle') {
      steelSetDock(!Steel.dockExpanded);
    } else if(act==='audio-import') {
      steelChooseAudio();
    } else if(act==='mic-report') {
      const p=S.micProblem || {},d=p.detail || {};
      try { await api('/api/asr/microphone-report',{method:'POST',body:{secure:d.secure,permission:d.permission,audio:d.audio,error:p.code,ui_version:STEEL_UI_VERSION,phase:REC.phase}});toast('已将浏览器与权限诊断保存到本机'); }
      catch(_){toast('请重启电脑端 Demo 后再保存诊断',true);}
    } else if(act==='mic-cancel'||act==='safety-cancel') {
      Steel.micAttempt++;Steel.starting=false;Steel.dockExpanded=false;await cancelRec();await render();
      steelUpdateRecorderSafety();
    } else if(act==='safety-stop') {
      if(S.recording)await steelRecordVoice(Steel.current);
    } else if(act==='voice-target'||act==='voice-rerecord'||act==='mic'||act==='mic-retry') {
      await steelRecordVoice(act==='mic'||act==='mic-retry'?Steel.current:el.dataset.key);
    } else if(act==='parse') {
      await steelCommitText(true);
    }
  } catch(err){toast(err.message,true);}
});

// Complete text entry without requiring an extra button, including Android IMEs.
document.addEventListener('compositionstart',(event)=>{if(event.target.id==='steel-say'){Steel.textComposing=true;clearTimeout(Steel.textTimer);}});
document.addEventListener('compositionend',(event)=>{if(event.target.id==='steel-say'){Steel.textComposing=false;steelInputEvent(event);}});
document.addEventListener('change',(event)=>{if(event.target.id==='steel-say' && !event.isComposing)steelCommitText(true);});
document.addEventListener('focusout',(event)=>{if(event.target.id==='steel-say' && !Steel.textComposing)steelCommitText(false);});
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState==='hidden' && !steelReadonly()){
    steelCollectForm();if(Steel.dirty){steelRememberDraft();steelPersistDraft(Steel.record.id);}
  }
});
window.addEventListener('pagehide',()=>{steelCollectForm();if(!steelReadonly() && Steel.dirty)steelRememberDraft();});
document.addEventListener('keydown',(event)=>{if(event.target.id==='steel-say' && event.key==='Enter' && !event.isComposing && !Steel.textComposing){event.preventDefault();steelCommitText(true);event.target.blur();}});
let steelScrollPosition=0;
let steelScrollIntentUntil=0;
const steelScrollIntent=(event)=>{
  if(event.target.closest && event.target.closest('#dock'))return;
  steelScrollIntentUntil=Date.now()+1500;
};
document.addEventListener('touchmove',steelScrollIntent,{passive:true});
document.addEventListener('wheel',steelScrollIntent,{passive:true});
window.addEventListener('scroll',()=>{
  const position=window.scrollY || 0, delta=Math.abs(position-steelScrollPosition);
  // Keyboard/viewport and layout anchoring also emit scroll; only a user's
  // page scroll collapses the panel, while scrolling inside the panel does not.
  if(delta>16 && Date.now()<steelScrollIntentUntil && document.body.classList.contains('steel-active') && Steel.dockExpanded && !Steel.starting)steelSetDock(false);
  if(!Steel.dockExpanded)steelScrollPosition=position;
  steelUpdateRecorderSafety();
},{passive:true});
window.addEventListener('hashchange',()=>{clearTimeout(Steel.textTimer);Object.values(Steel.textJobs).forEach((job)=>{if(job.controller)job.controller.abort();});Steel.textSession++;Steel.textJobs={};Steel.syncing=false;Steel.textDirty=false;Steel.textTarget=null;Steel.dockExpanded=false;steelUpdateRecorderSafety();});

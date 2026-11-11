/* Typed candidates stay separate from the inspection until reviewed. */
'use strict';
function steelManualCheck(key) {
  return ['cert_provided','cert_performance','identity_match','cert_seal','surface','shape','coil'].includes(key);
}
function steelStandardField(key) {
  return key==='specification' || /^(thickness|width)_(nominal|lower|upper)$/.test(key) ||
    /^extra_[01]_(name|requirement)$/.test(key);
}
function steelTarget(key) {
  if(steelStandardField(key))return undefined;
  const extra=/^extra_([01])_(result|verdict)$/.exec(key);
  if(extra && Steel.data && !(Steel.data.extra_rows || [])[Number(extra[1])])return undefined;
  if(steelManualCheck(key) || ['inspection_mode','inspected_quantity','qualified_quantity','rejected_quantity','sampling_basis','disposition','additional_failure_confirmed'].includes(key))return undefined;
  return ((Steel.template || {}).voice_targets || []).find((t) => t.key === key);
}
function steelReviewTarget(key) {
  return ((Steel.template || {}).review_targets || (Steel.template || {}).voice_targets || []).find((t)=>t.key===key);
}
function steelCandidate(key) { return ((Steel.data || {}).voice_candidates || []).find((a)=>a.key===key); }
function steelDisplayValue(key) {
  const manual=steelManualCheck(key);
  const candidate=steelCandidate(key);if(candidate && !manual && !steelStandardField(key))return candidate.value;
  const target=steelReviewTarget(key);
  if(target && target.destination==='value'){
    const meta=Steel.data.values[key] || {};
    if(manual && !steelReadonly() && meta.source==='asr' && !meta.confirmed)return '';
    return meta.value;
  }
  if(target && target.destination==='extra')return ((Steel.data.extra_rows || [])[target.extra_index] || {})[target.extra_prop];
  return Steel.data[key];
}
function steelReadonly() {
  return !Steel.record || Steel.record.status !== 'draft' || !S.me || Steel.record.owner_id !== S.me.id;
}
function steelBusy() { return Steel.saving || Steel.waiting || Steel.starting || Steel.syncing || S.recording; }
function steelVoiceButton(key) {
  if (steelReadonly()) return '';
  const target = steelTarget(key);
  if(!target)return '';
  const label = target.label;
  const processing=Steel.waiting || Steel.saving || Steel.syncing;
  const extra=S.recTarget && S.recTarget.extra;
  const recording=!processing && S.recording && extra && String(extra.record_id)===String(Steel.record.id) && extra.current_key===key;
  const disabled=processing || Steel.starting || (S.recording && !recording);
  const name=recording ? '停止' + label + '录音并识别' : '语音输入' + label;
  return '<button type="button" class="steel-voice-trigger' + (recording ? ' rec recording' : '') + '" data-steel-act="voice-target" data-key="' + key + '" data-steel-voice-target="' + key + '" aria-pressed="' + !!recording + '" aria-label="' + esc(name) + '" title="' + esc(name) + '"' + (disabled ? ' disabled' : '') + '>' + (recording ? '■' : '🎤') + '</button>';
}
function steelVoiceWrap(key, control) {
  const enabled=!!steelTarget(key);
  return '<div class="steel-input-control' + (enabled ? ' steel-voice-target' + (Steel.current===key ? ' active' : '') : '') + '" data-voice-field="' + key + '">' + control + steelVoiceButton(key) + '</div>' +
    (steelCandidate(key) ? '<span class="hint" data-steel-pending="' + key + '">' + (steelManualCheck(key) ? '历史语音未采用，请手动选择' : '语音预填，待核对') + '</span>' : '');
}
function steelCandidateText(value) { return Array.isArray(value) ? value.join('、') : String(value == null ? '' : value); }
function steelMicProblemHTML(problem) {
  if(!problem)return '';
  const d=problem.detail || {};
  const brief=problem.code==='audio-import' ? problem.message : '可使用系统录音，或检查浏览器的麦克风权限。';
  return '<div class="steel-mic-problem" role="alert"><b>' + esc(problem.title) + '</b><p>' + esc(brief) + '</p><span class="hint">错误：' + esc(problem.code) + '</span>' +
    '<div class="steel-mic-actions"><button class="btn ghost sm" data-steel-act="audio-import">系统录音 / 导入</button><button class="btn ghost sm" data-steel-act="mic-retry">重试麦克风</button></div>' +
    '<details><summary>查看原因与权限设置</summary><p>' + esc(problem.message) + '</p><p>安全连接：' + (d.secure===true ? '是' : d.secure===false ? '否' : '未知') + '；权限：' + esc(({granted:'已允许',denied:'被阻止',prompt:'等待授权'})[d.permission] || '未返回') + '；音频：' + esc(d.audio || '未启动') + '</p>' +
    '<p>先在手机“设置 → 应用 → 当前浏览器 → 权限”允许麦克风，再检查浏览器的网站麦克风权限。也可用独立 Chrome / Edge 打开相同地址。</p>' +
    '<p>若浏览器仍提示证书不受信任，可<a href="/qc-demo-ca.cer" download="qc-demo-ca.cer">下载本机测试证书</a>，在手机设置中搜索“安装证书”，由系统确认安装 CA 证书。仅信任你控制的这台电脑生成的证书。</p>' +
    '<p>界面版本：' + (typeof STEEL_UI_VERSION==='undefined' ? '未知' : esc(STEEL_UI_VERSION)) + '</p><p>浏览器：' + esc(d.browser || navigator.userAgent || '未知') + '</p><button class="btn ghost sm" data-steel-act="mic-report">保存本机诊断</button></details></div>';
}
function steelReview() {
  const candidates = Steel.data.voice_candidates || [];
  if (!candidates.length || steelReadonly()) return '';
  const intro=candidates.some((candidate)=>steelManualCheck(candidate.key)) ? '仅手选项目的历史语音未采用，请在原检验项中手动选择。其他语音预填内容仍需核对。' : '已在对应表单格预填。核对后确认，未确认时不能生成正式记录。';
  return '<section class="card steel-voice-review" aria-label="语音结果核对"><h3>语音结果 · 请逐项核对</h3><p class="hint">' + intro + '</p>' + candidates.map((a) => {
    const t = steelReviewTarget(a.key); if (!t) return '';
    if(steelManualCheck(a.key) || steelStandardField(a.key))return '<div class="steel-candidate" data-voice-candidate="' + a.key + '"><b>' + esc(t.label) + '</b><p class="hint">历史语音：' + esc(steelCandidateText(a.value)) + '</p><p class="hint">' + (steelStandardField(a.key) ? '本项使用已发布的工艺标准。' : '请在对应检验项手动选择结果。') + '</p><button class="btn ghost" data-steel-act="voice-discard" data-key="' + a.key + '">舍弃历史语音</button></div>';
    let control;
    if (t.options) {
      control = '<select data-steel-candidate="' + a.key + '">' + t.options.map((v) => '<option value="' + esc(String(v)) + '"' + (String(a.value) === String(v) ? ' selected' : '') + '>' + esc((t.option_labels || {})[String(v)] || String(v)) + '</option>').join('') + '</select>';
    } else {
      const textKeyboard = t.destination === 'value' || /_(lower|upper)$/.test(t.key);
      control = '<input data-steel-candidate="' + a.key + '" type="' + (t.kind === 'date' ? 'date' : 'text') + '"' + (t.kind === 'number' ? ' inputmode="' + (textKeyboard ? 'text' : 'decimal') + '"' : '') + ' value="' + esc(steelCandidateText(a.value)) + '">';
    }
    return '<div class="steel-candidate" data-voice-candidate="' + a.key + '"><label class="steel-input"><span>' + esc(t.label) + '</span>' + control + '</label><p class="hint">原话：' + esc(a.raw || '') + '</p>' + (a.note ? '<p class="hint">' + esc(a.note) + '</p>' : '') + '<div class="steel-voice-review-actions"><button class="btn" data-steel-act="voice-apply" data-key="' + a.key + '">核对确认</button>' + (steelTarget(a.key) ? '<button class="btn ghost" data-steel-act="voice-rerecord" data-key="' + a.key + '">重录</button>' : '') + '<button class="btn ghost" data-steel-act="voice-discard" data-key="' + a.key + '">舍弃</button></div></div>';
  }).join('') + '</section>';
}
function steelRemoveCandidate(key) {
  Steel.data.voice_candidates = (Steel.data.voice_candidates || []).filter((a) => a.key !== key);
  Steel.data.voice_pending = (Steel.data.voice_pending || []).filter((k) => k !== key);
}
function steelConfirmCandidate(key) {
  const t = steelReviewTarget(key), a = (Steel.data.voice_candidates || []).find((v) => v.key === key);
  if (!t || !a || steelReadonly()) return;
  if(steelManualCheck(key))throw new Error('此检验项仅支持手选，请在对应项目手动选择结果');
  if(steelStandardField(key))throw new Error('此项使用已发布的工艺标准');
  let value = a.value;
  if (t.kind === 'number') {
    const parts = steelCandidateText(value).trim().split(/[，,、;；\s]+/).filter(Boolean);
    const numbers = parts.map(Number);
    if (!numbers.length || numbers.some((n) => !Number.isFinite(n)) || (t.destination !== 'value' && numbers.length !== 1)) throw new Error('请填写有效数值');
    value = t.destination === 'value' ? numbers : numbers[0];
    if (t.integer && !Number.isInteger(value)) throw new Error('数量须为整数');
    if (t.positive && (Array.isArray(value) ? value.some((v) => v <= 0) : value <= 0)) throw new Error('尺寸须大于0');
  } else if (t.kind === 'boolean') value = value === true || value === 'true';
  else value = String(value == null ? '' : value).trim();
  if (t.options && !t.options.some((v) => v === value)) throw new Error('请选择有效结果');
  if (t.destination === 'value') Steel.data.values[key] = {value, source:'asr', confidence:a.confidence, raw:a.raw || '', confirmed:true};
  else if (t.destination === 'extra') {
    while (Steel.data.extra_rows.length <= t.extra_index) Steel.data.extra_rows.push({name:'',requirement:'',result:'',verdict:''});
    Steel.data.extra_rows[t.extra_index][t.extra_prop] = value;
  } else Steel.data[key] = value;
  steelRemoveCandidate(key);
  steelChanged();
}
function steelSyncViewport() {
  if (typeof document.documentElement === 'undefined') return;
  const active=document.body.classList.contains('steel-active');
  document.body.classList.toggle('steel-dock-expanded',active && Steel.dockExpanded);
  document.body.classList.toggle('steel-dock-collapsed',active && !Steel.dockExpanded);
  const dock = $('#dock');
  if (dock && dock.getBoundingClientRect) document.documentElement.style.setProperty('--steel-dock-height', Math.ceil(dock.getBoundingClientRect().height) + 'px');
  const vp = window.visualViewport;
  if (vp) {
    const inset = Math.max(0, window.innerHeight - vp.height - vp.offsetTop);
    document.documentElement.style.setProperty('--steel-keyboard-inset', inset + 'px');
    document.body.classList.toggle('steel-keyboard-open',inset > 80);
  }
  if(typeof steelRefreshDockState==='function')steelRefreshDockState();
}
if (window.visualViewport) window.visualViewport.addEventListener('resize', steelSyncViewport);
window.addEventListener('resize', steelSyncViewport);
if (typeof ResizeObserver !== 'undefined' && document.getElementById('dock')) new ResizeObserver(steelSyncViewport).observe(document.getElementById('dock'));

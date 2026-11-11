/* Evidence-backed local reports, using the same role and record access as the ledger. */
'use strict';

managementRoutes.push(['management-reports','AI解读','records'], ['management-dimensions','尺寸变化','analysis'], ['management-api','API配置','records','admin']);
const InspectionInsights = {owner:null, report:null, dimensions:null, aiBusy:false, lastAI:null, apiVersion:null};

function insightsReset() {
  managementResetForUser();
  if (InspectionInsights.owner === S.me.id) return;
  InspectionInsights.owner = S.me.id;
  InspectionInsights.aiBusy = false;
  InspectionInsights.lastAI = null;
  InspectionInsights.apiVersion = null;
  const now = new Date(Date.now() + 8*60*60*1000);
  const day = now.toISOString().slice(0,10);
  now.setUTCDate(now.getUTCDate() - 89);
  InspectionInsights.report = {period:'day', date:day, dept:''};
  InspectionInsights.dimensions = {date_from:now.toISOString().slice(0,10), date_to:day, dept:''};
}

function insightsForm(isReport) {
  const values = isReport ? InspectionInsights.report : InspectionInsights.dimensions;
  const max = new Date(Date.now() + 8*60*60*1000).toISOString().slice(0,10);
  const dateInput = (name,label) => '<label class="manager-field"><span>' + label + '</span><input type="date" required max="' + max + '" name="' + name + '" value="' + esc(values[name]) + '"></label>';
  return '<form class="manager-filter insights-filter" data-insights-form="' + (isReport ? 'report' : 'dimensions') + '"><div class="manager-filter-main">' +
    (isReport ? managementSelect('period','报告类型',[{value:'day',label:'检验日报'},{value:'week',label:'检验周报'}],values.period) + dateInput('date','报告日期 / 所在周') : dateInput('date_from','检验日期起') + dateInput('date_to','检验日期止')) +
    (S.me.role === 'admin' ? managementSelect('dept','班组',[{value:'',label:'全部班组'}].concat((Management.options.depts || []).map((dept) => ({value:dept,label:dept}))), values.dept) : '') +
    '<button type="submit" class="btn"' + (isReport && InspectionInsights.aiBusy ? ' disabled' : '') + '>' + (isReport ? (InspectionInsights.aiBusy ? '正在生成AI解读…' : '生成AI解读') : '查看尺寸变化') + '</button></div>' +
    (isReport ? '<p class="manager-panel-note">点击后按需调用 API；同一范围的数据未变化时复用已有结果。自动刷新只读取数据。</p>' : '') + '</form>';
}

function insightsNotes(notes) {
  return '<details class="manager-panel insights-notes"><summary>统计口径与使用说明</summary><ul>' + notes.map((note) => '<li>' + esc(note) + '</li>').join('') + '</ul></details>';
}

function insightsLink(record) {
  return '<a class="insights-source" href="' + esc(record.url) + '">' + esc(managementTitle(record)) + ' ↗</a>';
}

function insightsRecords(records) {
  return '<ul class="insights-evidence">' + records.map((record) => '<li>' + insightsLink(record) + '<span>' + esc(record.date) + ' · ' + esc(record.owner) + '</span>' + managementState(record.state) + '</li>').join('') + '</ul>';
}

function insightsReportHTML(data) {
  const m = data.metrics;
  const cards = [['已确认表单',m.final_count],['首检合格率',managementPercent(m.first_pass_rate)],['不合格表单',m.fail_count],['当前待办',data.todos.length]];
  let body = '<section class="manager-metrics insights-metrics">' + cards.map(([label,value]) => '<div class="manager-metric"><span>' + label + '</span><strong>' + esc(value) + '</strong></div>').join('') + '</section>';
  body += insightsAIHTML(data.ai || {});
  body += '<section class="manager-panel insights-summary"><div class="manager-panel-head"><h2>' + (data.period === 'week' ? '周报' : '日报') + '统计解读' + (data.partial_week ? ' · 本周进行中' : '') + '</h2><span class="manager-state blue">本地规则生成</span></div><ul>' + data.sentences.map((s) => '<li>' + esc(s) + '</li>').join('') + '</ul><p class="manager-panel-note">可比时段 ' + esc(data.previous_from) + ' — ' + esc(data.previous_to) + ' · 解读可在下方逐单核对</p></section>';
  body += managementPanel(S.me.role === 'admin' ? '各班组检验汇总' : '本班组检验汇总', data.teams.length ? '<div class="insights-teams">' + data.teams.map((team) => '<details><summary><strong>' + esc(team.dept || '未记录班组') + '</strong><span>已确认 ' + team.metrics.final_count + ' · 不合格 ' + team.metrics.fail_count + ' · 待办 ' + team.todo_count + '</span></summary>' + (team.records.length ? insightsRecords(team.records) : '<p class="manager-panel-note">本期暂无表单，当前仍有历史待办。</p>') + '</details>').join('') + '</div>' : managementEmpty('本期暂无检验记录或历史待办'));
  const todos = data.todos;
  body += managementPanel('当前待处理事项 · ' + todos.length, '<p class="manager-panel-note">含所选期末及以前的积压，显示当前处理状态，优先列出逾期异常。</p>' + (todos.length ? '<ul class="insights-todos">' + todos.map((todo) => '<li><div>' + insightsLink(todo.record) + '<span class="manager-state ' + (todo.overdue ? 'bad' : 'warning') + '">' + (todo.overdue ? '已逾期' : todo.type === 'case' ? '异常待跟进' : todo.type === 'draft' ? '草稿未定稿' : '待核对') + '</span></div><p>' + esc(todo.action) + '</p><small>' + esc(todo.record.dept || '未记录班组') + ' · 责任人 ' + esc(todo.assignee) + (todo.due_date ? ' · 期限 ' + esc(todo.due_date) : '') + '</small></li>').join('') + '</ul>' : managementEmpty('当前范围暂无待处理事项')));
  return body + insightsNotes(data.notes);
}

function insightsAIHTML(ai) {
  const generated = ['generated','cached'].includes(ai.status);
  const configuration = S.me.role === 'admin' ? '<a class="btn ghost sm" href="#/management-api">API配置</a>' : '';
  let content;
  if (generated) {
    const list = (items,title) => !items.length ? '' : '<h3>' + title + '</h3><ul>' + items.map((item) => '<li><p>' + esc(item.text) + '</p><div class="insights-ai-evidence">' + (item.teams || []).map((name) => '<span class="manager-state muted">' + esc(name || '未记录班组') + '</span>').join('') + (item.records || []).map(insightsLink).join('') + '</div></li>').join('') + '</ul>';
    content = '<p class="insights-ai-summary">' + esc(ai.summary) + '</p>' + list(ai.highlights,'重点观察') + list(ai.actions,'建议核查') + '<p class="manager-panel-note">' + esc(ai.limitations) + '</p><p class="manager-panel-note">' + esc(ai.model) + ' · 生成于 ' + esc(ai.generated_at) + (ai.status === 'cached' ? ' · 已复用同一数据的结果' : '') + '</p><p class="manager-panel-note">AI 建议需结合原单核对，正式数值以系统统计为准。</p>';
  } else {
    content = '<p class="insights-ai-summary" role="status">' + esc(ai.message || '尚未生成 AI 解读。') + '</p>' + configuration;
  }
  return '<section class="manager-panel insights-ai" data-ai-status="' + esc(ai.status || 'unconfigured') + '"><div class="manager-panel-head"><h2>AI解读</h2><span class="manager-state ' + (generated ? 'blue' : 'warning') + '">' + (generated ? 'API生成' : '当前使用本地规则') + '</span></div>' + content + '</section>';
}

function insightsNumber(value) {
  return Number.isFinite(value) ? Number(value.toFixed(4)).toString() : '—';
}

function insightsChart(group) {
  const points = group.points;
  const min = Math.min(group.low, ...points.map((p) => p.min));
  const max = Math.max(group.high, ...points.map((p) => p.max));
  const spread = max - min || 1;
  const y = (value) => 135 - (value - min) / spread * 102;
  const x = (index) => points.length === 1 ? 220 : 70 + index * 310 / (points.length - 1);
  const boundary = (value,label) => '<line x1="65" x2="395" y1="' + y(value) + '" y2="' + y(value) + '" stroke="#b37724" stroke-dasharray="5 4"/><text x="8" y="' + (y(value)+4) + '">' + label + ' ' + insightsNumber(value) + '</text>';
  return '<svg class="insights-chart" viewBox="0 0 420 165" role="img" aria-label="最近首检表单尺寸均值与读数范围，虚线为公差边界，明细见下表"><title>按检验日期和定稿顺序排列，每点代表一份表单</title>' +
    boundary(group.high,'上限') + boundary(group.low,'下限') + '<polyline fill="none" stroke="#1b638e" stroke-width="2.5" points="' + points.map((p,i) => x(i) + ',' + y(p.mean)).join(' ') + '"/>' +
    points.map((p,i) => '<line x1="' + x(i) + '" x2="' + x(i) + '" y1="' + y(p.min) + '" y2="' + y(p.max) + '" stroke="#1b638e" stroke-width="5"/><circle cx="' + x(i) + '" cy="' + y(p.mean) + '" r="4.5" fill="#1b638e"/><text text-anchor="middle" x="' + x(i) + '" y="156">' + esc(p.record.date.slice(5)) + '</text>').join('') + '</svg>';
}

function insightsDimensionHTML(data) {
  let html = '<section class="manager-panel insights-summary"><div class="manager-panel-head"><h2>尺寸变化观察</h2><span class="manager-state blue">本地规则生成</span></div><p class="manager-panel-note">共 ' + data.groups.length + ' 个尺寸分组 · 持续接近边界 ' + (data.counts.approaching || 0) + ' 组 · 最新超差 ' + (data.counts.out_of_range || 0) + ' 组 · 数据不足 ' + (data.counts.insufficient || 0) + ' 组</p><p class="manager-panel-note">按相同规格和标准版本比较，每点是一份首检表单。提醒用于安排核查，正式检验结论保持原判定。</p></section>';
  if (!data.groups.length) html += managementEmpty('暂无可比较的尺寸记录，请积累包含生产单位、牌号和标准版本的正式首检表单。');
  html += '<div class="insights-dimensions">' + data.groups.map((g) => {
    const tone = g.status === 'out_of_range' ? 'bad' : ['approaching','near_limit'].includes(g.status) ? 'warning' : 'muted';
    return '<section class="manager-panel insights-dimension" data-dimension-status="' + g.status + '"><div class="manager-panel-head"><h2>' + esc(g.specification) + ' · ' + g.dimension_label + '</h2><span class="manager-state ' + tone + '">' + esc(g.label) + '</span></div>' +
      '<p class="manager-panel-note">' + esc(g.dept || '未记录班组') + ' · ' + esc(g.manufacturer) + ' · ' + esc(g.grade) + '</p><p class="manager-panel-note">标准 ' + esc(g.standard_code) + ' v' + esc(g.standard_version) + ' · 本单公差 ' + insightsNumber(g.low) + '～' + insightsNumber(g.high) + ' mm</p>' +
      '<p class="insights-explanation">' + esc(g.message) + '</p>' + insightsChart(g) + '<p class="manager-panel-note">范围内 ' + g.total_count + ' 份首检；展示最近 ' + g.window_count + ' 份，覆盖 ' + g.day_count + ' 个检验日期。单位 mm，点为均值，竖线为最小至最大读数。</p>' +
      '<details class="insights-dimension-evidence"><summary>查看 ' + g.points.length + ' 份原单与读数</summary><div class="manager-table-scroll"><table class="manager-table"><thead><tr><th>原单 / 日期</th><th>均值</th><th>最小～最大</th><th>读数个数</th></tr></thead><tbody>' + g.points.map((p) => '<tr><td>' + insightsLink(p.record) + '<small>' + esc(p.record.date) + '</small></td><td>' + insightsNumber(p.mean) + '</td><td>' + insightsNumber(p.min) + '～' + insightsNumber(p.max) + '</td><td>' + p.count + '</td></tr>').join('') + '</tbody></table></div></details></section>';
  }).join('') + '</div>';
  const skipped = Object.entries(data.skipped);
  if (skipped.length) html += '<p class="manager-footnote">未纳入比较：' + skipped.map(([label,count]) => esc(label) + ' ' + count).join('；') + '。表单与尺寸项目分别计数。</p>';
  return html + insightsNotes(data.notes);
}

async function insightsView(route) {
  if (!S.me) return VIEWS.login();
  if (!managementCanRead()) return managementView(route);
  insightsReset();
  const owner = S.me.id;
  const isReport = route === 'management-reports';
  const title = isReport ? 'AI解读' : '尺寸变化提醒';
  const subtitle = isReport ? '查看本期检验情况、班组待办，并逐单核对依据' : '观察首检尺寸是否持续接近公差边界';
  const values = Object.assign({}, isReport ? InspectionInsights.report : InspectionInsights.dimensions);
  let body, scope = managementScopeText();
  try {
    await managementLoadOptions();
    const data = await api('/api/management/insights/' + (isReport ? 'report' : 'dimensions') + '?' + new URLSearchParams(values));
    if (!S.me || S.me.id !== owner || InspectionInsights.owner !== owner) return {html:''};
    scope = (data.dept || managementScopeText()) + ' · ' + data.date_from + ' — ' + data.date_to;
    if (isReport) InspectionInsights.apiVersion = (data.ai || {}).config_version;
    if (isReport && InspectionInsights.lastAI && InspectionInsights.lastAI.query === JSON.stringify(values) && InspectionInsights.lastAI.ai.config_version === (data.ai || {}).config_version && !['generated','cached'].includes((data.ai || {}).status)) data.ai = InspectionInsights.lastAI.ai;
    body = isReport ? insightsReportHTML(data) : insightsDimensionHTML(data);
    Management.updatedAt = new Date().toLocaleTimeString('zh-CN',{hour12:false});
  } catch (error) {
    if (!S.me || S.me.id !== owner) return {html:''};
    body = managementError(error.message);
  }
  return {html:managementShell(route,title,subtitle,insightsForm(isReport) + body,{noFilters:true,scopeLabel:scope})};
}

['management-reports','management-dimensions'].forEach((route) => { VIEWS[route] = () => insightsView(route); });

document.addEventListener('submit', async (event) => {
  const form = event.target.closest('[data-insights-form]');
  if (!form) return;
  event.preventDefault();
  const values = InspectionInsights[form.dataset.insightsForm];
  Object.keys(values).forEach((key) => { if (form.elements[key]) values[key] = form.elements[key].value; });
  Management.filterDirty = false;
  if (form.dataset.insightsForm !== 'report') { await managementRefresh(); return; }
  if (InspectionInsights.aiBusy) return;
  const owner = S.me.id;
  const query = JSON.stringify(values);
  InspectionInsights.aiBusy = true;
  Management.refreshing = true;
  const button = form.querySelector('[type=submit]');
  button.disabled = true;
  button.textContent = '正在生成AI解读…';
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(),65000);
  try {
    const result = await api('/api/management/insights/generate',{method:'POST',body:Object.assign({},values),signal:controller.signal});
    if (S.me && S.me.id === owner && InspectionInsights.owner === owner) InspectionInsights.lastAI = result.ai.status === 'fallback' ? {query,ai:result.ai} : null;
  } catch (error) {
    if (S.me && S.me.id === owner && InspectionInsights.owner === owner) InspectionInsights.lastAI = {query,ai:{status:'fallback',config_version:InspectionInsights.apiVersion,message:error.name === 'AbortError' ? '请求超时，保留本地解读，可稍后重试。' : error.message}};
  } finally {
    clearTimeout(timer);
    if (InspectionInsights.owner === owner) InspectionInsights.aiBusy = false;
    Management.refreshing = false;
    if (S.me && S.me.id === owner && parseRoute().name === 'management-reports') await render();
  }
});
['input','change'].forEach((type) => document.addEventListener(type, (event) => {
  if (event.target.closest('[data-insights-form]')) Management.filterDirty = true;
}));

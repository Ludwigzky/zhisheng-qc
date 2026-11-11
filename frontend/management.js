/* Management views share the authenticated API and its department scope. */
'use strict';

const Management = {
  owner: null,
  filters: null,
  options: null,
  page: 1,
  casePage: 1,
  caseStatus: '',
  overdueOnly: false,
  auto: true,
  timer: null,
  refreshing: false,
  filterDirty: false,
  editor: null,
  caseRequest: 0,
  error: '',
  updatedAt: ''
};

const managementRoutes = [
  ['management', '工作总览', 'overview'],
  ['management-records', '检验台账', 'records'],
  ['management-cases', '异常跟进', 'cases'],
  ['management-analysis', '质量分析', 'analysis']
];

function managementCanRead() {
  return !!S.me && ['admin', 'supervisor'].includes(S.me.role);
}

function managementResetForUser() {
  if (Management.owner === S.me.id) return;
  Management.owner = S.me.id;
  Management.filters = {q:'', date_from:'', date_to:'', owner_id:'', dept:'', kind:'', state:'', defect:''};
  Management.options = null;
  Management.page = 1;
  Management.casePage = 1;
  Management.caseStatus = '';
  Management.overdueOnly = false;
  Management.editor = null;
  Management.caseRequest++;
  Management.error = '';
  Management.filterDirty = false;
}

function managementQuery(extra, omit) {
  const values = Object.assign({}, Management.filters, extra || {});
  (omit || []).forEach((key) => delete values[key]);
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== '' && value != null && value !== false) query.set(key, String(value));
  });
  return query.toString();
}

function managementText(value) {
  if (Array.isArray(value)) return value.map(managementText).join('、');
  if (value && typeof value === 'object') return JSON.stringify(value);
  return value == null || value === '' ? '未记录' : String(value);
}

function managementCount(value) {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat('zh-CN').format(number) : '—';
}

function managementPercent(value) {
  return value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(1) + '%';
}

function managementState(state) {
  const names = {pass:'合格', fail:'不合格', draft:'草稿', pending:'待核对', missing:'未填写', na:'不适用', open:'待跟进', following:'跟进中', closed:'已关闭'};
  const tone = {pass:'good', fail:'bad', closed:'muted', draft:'muted', pending:'warning', missing:'warning', open:'warning', following:'blue', na:'muted'};
  return '<span class="manager-state ' + (tone[state] || 'muted') + '">' + esc(names[state] || state || '未记录') + '</span>';
}

function managementRecordLink(record) {
  return '#/management-record/' + record.kind + ':' + record.id;
}

function managementTitle(record) {
  return record.document_no ? '单据 ' + record.document_no : (record.title || '检验记录') + ' #' + record.id;
}

function managementScopeText() {
  return S.me.role === 'admin' ? '全部授权班组' : (S.me.dept || '本班组');
}

function managementPeriod() {
  const f = Management.filters;
  if (!f.date_from && !f.date_to) return '全部检验日期';
  return (f.date_from || '最早') + ' — ' + (f.date_to || '至今');
}

function managementEmpty(message) {
  return '<div class="manager-empty"><span aria-hidden="true">○</span><p>' + esc(message || '当前范围内暂无记录') + '</p></div>';
}

function managementError(message) {
  return '<div class="manager-error" role="alert">' + esc(message) + '<button type="button" class="btn ghost sm" data-manager-refresh>重新读取</button></div>';
}

function managementSelect(name, label, options, value) {
  return '<label class="manager-field"><span>' + esc(label) + '</span><select name="' + name + '">' +
    options.map((option) => '<option value="' + esc(option.value) + '"' + (String(option.value) === String(value) ? ' selected' : '') + '>' + esc(option.label) + '</option>').join('') + '</select></label>';
}

function managementFilterHTML(cases, recordStates) {
  const f = Management.filters;
  const people = Management.options.people || [];
  const depts = Management.options.depts || [];
  const kinds = Management.options.kinds || [{value:'steel',label:'钢板进货检验'}, {value:'task',label:'工序检验'}];
  const stateOptions = cases ? [
    {value:'', label:'全部跟进状态'}, {value:'open',label:'待跟进'}, {value:'following',label:'跟进中'}, {value:'closed',label:'已关闭'}
  ] : [
    {value:'',label:'全部检验状态'}, {value:'draft',label:'草稿'}, {value:'pending',label:'待核对'}, {value:'pass',label:'合格'}, {value:'fail',label:'不合格'},
    {value:'final',label:'已确认检验'}, {value:'unfinished',label:'未完成记录'}, {value:'first',label:'首轮已确认'}, {value:'retest',label:'复检已确认'}
  ];
  return '<form class="manager-filter" data-manager-filter><div class="manager-filter-main">' +
    '<label class="manager-field manager-search"><span>查询记录</span><input name="q" type="search" value="' + esc(f.q) + '" placeholder="单据号、炉批号、规格或人员"></label>' +
    '<label class="manager-field"><span>检验日期起</span><input name="date_from" type="date" value="' + esc(f.date_from) + '"></label>' +
    '<label class="manager-field"><span>检验日期止</span><input name="date_to" type="date" value="' + esc(f.date_to) + '"></label>' +
    managementSelect('kind','检验类型',[{value:'',label:'全部检验类型'}].concat(kinds), f.kind) +
    managementSelect('owner_id','质量工程师',[{value:'',label:'全部质量工程师'}].concat(people.map((p) => ({value:p.id,label:p.name + (p.dept ? ' · ' + p.dept : '')}))), f.owner_id) +
    (S.me.role === 'admin' ? managementSelect('dept','班组',[{value:'',label:'全部班组'}].concat(depts.map((d) => ({value:d,label:d}))), f.dept) : '') +
    (cases || recordStates ? managementSelect(cases ? 'case_status' : 'state', cases ? '跟进状态' : '检验状态', stateOptions, cases ? Management.caseStatus : f.state) : '') +
    '</div>' + (recordStates && f.defect ? '<div class="manager-active-filter">不合格项目：' + esc(f.defect) + '<button type="button" data-manager-clear-defect>移除此条件 ×</button></div>' : '') + '<div class="manager-filter-footer"><div class="manager-range-actions" aria-label="快捷日期范围">' +
    [['today','今天'],['7','近7天'],['30','近30天'],['all','全部日期']].map(([value,label]) => '<button type="button" data-manager-period="' + value + '">' + label + '</button>').join('') + '</div>' +
    (cases ? '<label class="manager-check"><input type="checkbox" name="overdue"' + (Management.overdueOnly ? ' checked' : '') + '> 仅看逾期</label>' : '') +
    '<div class="manager-filter-buttons"><button class="btn ghost" type="button" data-manager-clear>清空条件</button><button class="btn" type="submit">查询</button></div></div></form>';
}

function managementShell(route, title, subtitle, body, options) {
  const opts = options || {};
  const navRoute = ['management-record','management-preview'].includes(route) ? 'management-records' : route;
  const current = managementRoutes.find((r) => r[0] === navRoute);
  return '<div class="manager-layout" data-manager-page="' + route + '"><aside class="manager-sidebar"><a class="manager-brand" href="#/management"><span class="manager-brand-mark" aria-hidden="true">卫</span><span>质量管理工作台<small>卫华 · 现场检验</small></span></a>' +
    '<nav aria-label="管理工作台导航">' + managementRoutes.filter((entry) => entry[3] !== 'admin' || S.me.role === 'admin').map(([name,label,icon]) => '<a href="#/' + name + '"' + (name === navRoute ? ' aria-current="page"' : '') + '><span class="manager-nav-icon manager-icon-' + icon + '" aria-hidden="true"></span>' + label + '</a>').join('') + '</nav>' +
    '<div class="manager-sidebar-foot"><strong>' + esc(managementScopeText()) + '</strong><span>' + esc(S.me.name) + '</span><a href="#/home">返回员工与任务入口 →</a></div></aside>' +
    '<main class="manager-main"><header class="manager-page-head"><div><div class="manager-breadcrumb">质量管理 / ' + esc(current ? current[1] : title) + '</div><h1>' + esc(title) + '</h1><p>' + esc(subtitle) + '</p></div>' +
    (opts.configuration ? '<div class="manager-refresh-tools"><button class="btn ghost" data-manager-refresh type="button">重新读取配置</button></div>' : '<div class="manager-refresh-tools"><label class="manager-auto"><input type="checkbox" data-manager-auto' + (Management.auto ? ' checked' : '') + '> 30 秒更新</label><button class="btn ghost" data-manager-refresh type="button">刷新数据</button><small data-manager-updated>' + esc(Management.updatedAt ? '更新于 ' + Management.updatedAt : '') + '</small></div>') + '</header>' +
    (opts.noFilters ? '' : managementFilterHTML(route === 'management-cases',route === 'management-records')) +
    '<div class="manager-scope-line"><span>' + esc(opts.scopeLabel || (managementScopeText() + ' · ' + managementPeriod())) + '</span>' + (opts.configuration ? '' : '<span>示例数据已排除</span>') + '</div>' +
    (Management.error ? managementError(Management.error) : '') + body + '</main></div>';
}

function managementMetric(label, value, hint, action, tone) {
  return '<a class="manager-metric ' + (tone || '') + '" href="#/management-records" data-manager-metric="' + esc(action || 'all') + '"><span>' + esc(label) + '</span><strong>' + esc(value) + '</strong><small>' + esc(hint) + ' <b aria-hidden="true">↗</b></small></a>';
}

function managementMetrics(data) {
  const m = data.metrics || {};
  return '<section class="manager-metrics" aria-label="检验指标">' +
    managementMetric('已确认检验表',managementCount(m.final_count),'当前检验日期范围','final') +
    managementMetric('首检合格率',managementPercent(m.first_pass_rate),'首次有效确认，共 ' + managementCount(m.first_inspection_count) + ' 轮','first','blue') +
    managementMetric('不合格表单',managementCount(m.fail_count),'查看不合格记录','fail','bad') +
    managementMetric('未完成记录',managementCount(Number(m.draft_count || 0) + Number(m.pending_count || 0)),'草稿 ' + managementCount(m.draft_count) + ' · 待核对 ' + managementCount(m.pending_count),'unfinished','warning') +
    '</section>';
}

function managementTrend(rows, compact) {
  if (!rows || !rows.length) return managementEmpty('当前日期范围内没有已确认检验记录');
  const visible = compact ? rows.slice(-14) : rows.slice(-31);
  const maximum = Math.max(1, ...visible.map((r) => Number(r.total || 0)));
  return '<div class="manager-trend-scroll"><div class="manager-trend" role="img" aria-label="每日已确认检验数量，绿色合格，红色不合格">' + visible.map((r) => {
    const pass = Number(r.pass || 0), fail = Number(r.fail || 0), total = Number(r.total || 0);
    const height = Math.max(2,total / maximum * 142);
    return '<div class="manager-trend-day" title="' + esc(r.date + '，共 ' + total + '，合格 ' + pass + '，不合格 ' + fail) + '"><span class="manager-trend-total">' + total + '</span><div class="manager-trend-bars" style="height:' + height.toFixed(2) + 'px"><span class="pass" style="flex:' + pass + '"></span><span class="fail" style="flex:' + fail + '"></span></div><small>' + esc(String(r.date).slice(5)) + '</small></div>';
  }).join('') + '</div></div><div class="manager-chart-legend"><span><i class="pass"></i>合格</span><span><i class="fail"></i>不合格</span></div><p class="manager-chart-caption">展示最近 ' + visible.length + ' 个有记录日期</p>';
}

function managementDefects(rows) {
  if (!rows || !rows.length) return managementEmpty('当前范围内没有不合格检验项');
  const maximum = Math.max(1,...rows.map((r) => Number(r.count || 0)));
  return '<div class="manager-defects">' + rows.slice(0,8).map((r) => '<a href="#/management-records" data-manager-defect="' + esc(r.label) + '"><div><span>' + esc(r.label) + '</span><b>' + managementCount(r.count) + '</b></div><span class="manager-bar"><i style="width:' + (Number(r.count || 0) / maximum * 100).toFixed(2) + '%"></i></span></a>').join('') + '</div>';
}

function managementPeople(rows) {
  if (!rows || !rows.length) return managementEmpty('暂无已确认记录可汇总');
  return '<div class="manager-table-scroll"><table class="manager-table"><thead><tr><th>质量工程师 / 班组</th><th>已确认</th><th>合格</th><th>不合格</th><th>表单通过率</th></tr></thead><tbody>' + rows.map((r) => '<tr><td><a href="#/management-records" data-manager-person="' + r.owner_id + '" data-manager-person-dept="' + esc(r.dept || '') + '">' + esc(r.name) + '</a><small>' + esc(r.dept || '未记录班组') + '</small></td><td>' + managementCount(r.total) + '</td><td>' + managementCount(r.pass) + '</td><td>' + managementCount(r.fail) + '</td><td>' + managementPercent(Number(r.total) ? Number(r.pass) / Number(r.total) * 100 : null) + '</td></tr>').join('') + '</tbody></table></div>';
}

function managementPanel(title, body, extra) {
  return '<section class="manager-panel"><div class="manager-panel-head"><h2>' + esc(title) + '</h2>' + (extra || '') + '</div>' + body + '</section>';
}

function managementPager(result, cases) {
  return '<div class="manager-pagination"><span>共 ' + managementCount(result.total) + ' 条 · 第 ' + managementCount(result.page) + ' / ' + managementCount(result.pages || 1) + ' 页</span><div><button type="button" class="btn ghost" data-manager-page="' + (Number(result.page)-1) + '" data-manager-list="' + (cases ? 'cases' : 'records') + '"' + (result.page <= 1 ? ' disabled' : '') + '>上一页</button><button type="button" class="btn ghost" data-manager-page="' + (Number(result.page)+1) + '" data-manager-list="' + (cases ? 'cases' : 'records') + '"' + (result.page >= (result.pages || 1) ? ' disabled' : '') + '>下一页</button></div></div>';
}

function managementRecordsTable(items) {
  if (!items.length) return managementEmpty('没有符合筛选条件的检验记录');
  return '<div class="manager-table-scroll"><table class="manager-table manager-record-table"><thead><tr><th>检验记录</th><th>材料 / 产品</th><th>质量工程师</th><th>检验日期</th><th>结果</th><th>检验轮次</th><th></th></tr></thead><tbody>' + items.map((r) => '<tr><td><a class="manager-record-title" href="' + managementRecordLink(r) + '">' + esc(managementTitle(r)) + '</a><small>' + esc(r.title || (r.kind === 'steel' ? '钢板进货检验' : '工序检验')) + ' · #' + r.id + '</small></td><td>' + esc(r.product_name || '未记录') + '<small>' + esc([r.specification,r.grade].filter(Boolean).join(' · ') || '未记录规格') + '</small></td><td>' + esc((r.owner || {}).name || '未记录') + '<small>' + esc((r.owner || {}).dept || '') + '</small></td><td>' + esc(r.inspection_date || '未记录') + '</td><td>' + managementState(r.state) + '</td><td>' + (r.parent_id ? '<span class="manager-round">复检</span>' : '首检') + '</td><td><a class="manager-row-link" href="' + managementRecordLink(r) + '">查看追溯 →</a></td></tr>').join('') + '</tbody></table></div>';
}

function managementReadingStatus(status) {
  return {ok:'pass',over:'fail' }[status] || status;
}

function managementReadings(rows) {
  if (!rows || !rows.length) return managementEmpty('本表尚未填写检测值');
  return '<p class="manager-panel-note">本表记录的读数与判定。系统未单独记录样件、测点或量具编号的项目显示为表单级记录。</p><div class="manager-table-scroll"><table class="manager-table"><thead><tr><th>检验项目</th><th>标准要求</th><th>本次填写</th><th>结果</th><th>录入与核对</th></tr></thead><tbody>' + rows.map((r) => '<tr><td>' + esc(r.label || r.key) + '</td><td>' + esc(managementText(r.requirement || r.spec)) + '</td><td><strong>' + esc(r.value_text || managementText(r.value)) + '</strong>' + (r.unit ? ' ' + esc(r.unit) : '') + '</td><td>' + managementState(managementReadingStatus(r.status)) + '</td><td>' + esc(r.source === 'asr' ? '语音' : r.source === 'manual' ? '手动填写' : r.source === 'derived' ? '系统计算' : '未记录') + '<small>' + (r.source === 'asr' ? (r.confirmed ? '已人工核对' : '待人工核对') : '') + '</small></td></tr>').join('') + '</tbody></table></div>';
}

function managementTimeline(items) {
  if (!items || !items.length) return managementEmpty('暂无该记录的操作历史');
  return '<ol class="manager-timeline">' + items.map((r) => '<li><span class="manager-timeline-dot" aria-hidden="true"></span><div><strong>' + esc(r.label || r.action || '记录更新') + '</strong><small>' + esc(r.actor_name || '系统') + ' · ' + esc(r.at || '') + '</small>' + (r.summary ? '<p>' + esc(r.summary) + '</p>' : '') + '</div></li>').join('') + '</ol>';
}

function managementChain(items) {
  if (!items || !items.length) return managementEmpty('暂无关联记录');
  return '<div class="manager-chain">' + items.map((r,index) => '<a class="manager-chain-item' + (r.current ? ' current' : '') + '" href="' + managementRecordLink(r) + '"><span>' + (r.parent_id || index ? '复检' : '首检') + ' · #' + r.id + '</span><strong>' + esc(r.document_no || r.title || '检验记录') + '</strong>' + managementState(r.state) + '<small>' + esc((r.owner || {}).name || '') + ' · ' + esc(r.inspection_date || '') + '</small>' + (r.current ? '<b class="manager-current">当前查看</b>' : '') + '</a>').join('') + '</div>';
}

function managementDetailHTML(data) {
  const r = data.record;
  const standard = r.standard || {};
  const facts = [
    ['检验类型',r.title],['单据号',r.document_no],['产品 / 材料',r.product_name],['规格',r.specification],['牌号',r.grade],
    ['生产单位',r.manufacturer],['炉批号',r.heat_no],['检验日期',r.inspection_date],['质量工程师',(r.owner || {}).name],['所属班组',(r.owner || {}).dept],
    ['标准版本',[standard.title,standard.code,standard.version].filter(Boolean).join(' · ')],['记录版本',r.version]
  ];
  const artifacts = data.artifacts || [];
  const actions = '<a class="btn ghost" href="#/management-records">← 返回检验台账</a>' + (r.source_url ? '<a class="btn ghost" href="' + esc(r.source_url) + '">打开员工原单</a>' : '');
  const documents = artifacts.length ? '<div class="manager-document-links">' + artifacts.map((a) => {
    let link;
    if (a.kind === 'xlsx' && typeof steelFileActions === 'function') link = steelFileActions(r.id,a.label || '下载定稿 Excel',r.document_no);
    else link = '<a class="btn ghost" href="' + (a.kind === 'preview' ? '#/management-preview/' + r.kind + ':' + r.id : esc(a.url)) + '">' + esc(a.label || a.kind) + '</a>';
    return link + (a.sha256 ? '<small>文件校验值 <code>' + esc(a.sha256) + '</code></small>' : '');
  }).join('') + '</div>' : managementEmpty('本表暂未生成正式归档文件');
  return '<div class="manager-detail-actions">' + actions + '</div>' +
    managementPanel('记录信息','<div class="manager-detail-status">' + managementState(r.state) + '<span>记录 #' + r.id + '</span><span>' + esc(r.parent_id ? '关联原检 #' + r.parent_id : '首次检验记录') + '</span></div><dl class="manager-facts">' + facts.map(([label,value]) => '<div><dt>' + esc(label) + '</dt><dd>' + esc(managementText(value)) + '</dd></div>').join('') + '</dl>') +
    managementPanel('原检与复检', managementChain(data.chain)) + managementStandardHTML(r) + managementPanel('本次检测值',managementReadings(data.readings)) +
    '<div class="manager-grid-two">' + managementPanel('操作时间线',managementTimeline(data.timeline)) + managementPanel('表单与归档原件',documents) + '</div>';
}

function managementStandardHTML(record) {
  if (record.kind !== 'steel') return '';
  const standard = record.standard || {};
  const criteria = standard.criteria || {};
  const approved = record.approved_deviation;
  const request = record.deviation;
  const fields = [['specification','规格型号'],['thickness_nominal','公称厚度'],['thickness_lower','厚度下偏差'],['thickness_upper','厚度上偏差'],['width_nominal','公称宽度'],['width_lower','宽度下偏差'],['width_upper','宽度上偏差']];
  let body = Object.keys(criteria).length ? '<div class="manager-table-scroll"><table class="manager-table"><thead><tr><th>标准项目</th><th>发布版本要求</th><th>本单批准偏差</th></tr></thead><tbody>' + fields.map(([key,label]) => '<tr><td>' + label + '</td><td>' + esc(managementText(criteria[key])) + '</td><td>' + esc(approved && approved.limits && approved.limits[key] != null ? managementText(approved.limits[key]) : '按发布标准') + '</td></tr>').join('') + '</tbody></table></div>' : '<p class="manager-panel-note">历史记录未单独保存发布标准，本次实际判定要求见下方检测项。</p>';
  if (request) {
    const status = {pending:'待审批',approved:'已批准',rejected:'已驳回'}[request.status] || request.status;
    body += '<div class="manager-deviation"><strong>临时偏差 · ' + esc(status) + '</strong><dl class="manager-facts">' +
      [['申请人',request.applicant],['申请时间',request.created_at],['申请依据',request.reason],['审批人',request.reviewer],['审批时间',request.reviewed_at],['审批意见',request.decision]].map(([label,value]) => '<div><dt>' + label + '</dt><dd>' + esc(managementText(value)) + '</dd></div>').join('') + '</dl></div>';
  }
  return managementPanel('标准与临时偏差',body);
}

function managementCasesTable(items) {
  if (!items.length) return managementEmpty('当前范围内没有符合条件的异常记录');
  return '<div class="manager-table-scroll"><table class="manager-table"><thead><tr><th>不合格记录</th><th>质量工程师 / 班组</th><th>责任人</th><th>处理期限</th><th>跟进状态</th><th>后续检验</th><th></th></tr></thead><tbody>' + items.map((r) => {
    const c = r.case || {};
    return '<tr><td><a href="' + managementRecordLink(r) + '">' + esc(managementTitle(r)) + '</a><small>' + esc(r.title || '') + '</small></td><td>' + esc((r.owner || {}).name || '') + '<small>' + esc((r.owner || {}).dept || r.dept || '') + '</small></td><td>' + esc(c.assignee_name || '待分派') + '</td><td>' + esc(c.due_date || '未设置') + (c.overdue ? '<small class="manager-overdue">已逾期</small>' : '') + '</td><td>' + managementState(c.status || 'open') + '</td><td>' + (r.has_passed_followup ? '<span class="manager-inline-good">复检已合格</span>' : r.followup_record_id ? '复检待完成' : '尚无合格复检') + '</td><td><button type="button" class="btn ghost sm" data-manager-case="' + esc(r.key || r.kind + ':' + r.id) + '">查看与跟进</button></td></tr>';
  }).join('') + '</tbody></table></div>';
}

function managementCaseEditor() {
  const editor = Management.editor;
  if (!editor) return '';
  const r = editor.item, c = r.case || {}, f = editor.form;
  const people = (editor.assignees || []).filter((p) => !r.dept || p.dept === r.dept || p.dept === (r.owner || {}).dept);
  const statusOptions = [{value:'open',label:'待跟进'}, {value:'following',label:'跟进中'}, {value:'closed',label:'已关闭'}];
  return '<section class="manager-panel manager-case-editor" aria-label="异常跟进编辑"><div class="manager-panel-head"><div><h2>跟进 ' + esc(managementTitle(r)) + '</h2><p class="manager-panel-note">' + esc(r.title || '') + ' · ' + esc((r.owner || {}).dept || r.dept || '') + '</p></div><button type="button" class="btn ghost sm" data-manager-case-cancel>收起</button></div>' +
    '<form data-manager-case-form><div class="manager-case-fields">' +
    managementSelect('assignee_id','跟进责任人',[{value:'',label:'待分派'}].concat(people.map((p) => ({value:p.id,label:p.name + ' · ' + p.dept}))),f.assignee_id) +
    '<label class="manager-field"><span>处理期限</span><input name="due_date" type="date" value="' + esc(f.due_date) + '"></label>' +
    managementSelect('status','跟进状态',statusOptions,f.status) +
    '<label class="manager-field manager-note-field"><span>本次跟进说明</span><textarea name="note" rows="3" required maxlength="2000" placeholder="记录本次处置、分派或关闭的依据">' + esc(f.note) + '</textarea></label></div>' +
    '<p class="manager-panel-note">' + esc(r.closure_reason || '关闭异常须有后续已确认合格复检，或符合原单已确认的处置条件，并填写本次说明。') + '</p>' +
    (editor.error ? '<p class="manager-case-error" role="alert">' + esc(editor.error) + '</p>' : '') +
    '<div class="manager-case-actions"><a class="btn ghost" href="' + managementRecordLink(r) + '">核对原检与复检</a><button type="submit" class="btn"' + (editor.saving ? ' disabled' : '') + '>' + (editor.saving ? '正在保存…' : '保存跟进') + '</button></div></form>' +
    '<details class="manager-case-history"' + ((editor.history || []).length ? ' open' : '') + '><summary>跟进历史 · ' + (editor.history || []).length + ' 条</summary>' +
    ((editor.history || []).length ? '<ol class="manager-timeline">' + editor.history.map((h) => '<li><span class="manager-timeline-dot" aria-hidden="true"></span><div><strong>' + esc(h.note || '跟进更新') + '</strong><small>' + esc(h.actor_name || '') + ' · ' + esc(h.at || '') + '</small><p>' + managementState(h.status) + ' ' + esc(h.assignee_name || '未分派') + (h.due_date ? ' · 期限 ' + esc(h.due_date) : '') + '</p></div></li>').join('') + '</ol>' : '<p class="manager-panel-note">尚未写入跟进记录</p>') + '</details></section>';
}

async function managementLoadOptions() {
  if (Management.options) return;
  const owner = Management.owner;
  const options = await api('/api/management/options');
  if (Management.owner === owner && S.me && S.me.id === owner) Management.options = options;
}

async function managementView(route, id) {
  if (!S.me) return VIEWS.login();
  if (!managementCanRead()) return {html:'<div class="wrap"><section class="card"><h2>此页面仅向管理人员开放</h2><p>请使用部长或室主任账号访问。</p><a class="btn" href="#/home">返回我的任务</a></section></div>'};
  managementResetForUser();
  const owner = S.me.id;
  await managementLoadOptions();
  if (!S.me || S.me.id !== owner || Management.owner !== owner) return {html:''};
  Management.error = '';
  let body, title, subtitle;
  try {
    if (route === 'management-record' || route === 'management-preview') {
      if (!/^(steel|task):\d+$/.test(id || '')) throw new Error('记录编号无效');
      const [kind,rid] = id.split(':');
      const data = await api('/api/management/records/' + kind + '/' + rid);
      if (!S.me || S.me.id !== owner || Management.owner !== owner) return {html:''};
      title = managementTitle(data.record);
      subtitle = '从本次读数追溯标准版本、检验轮次和归档原件';
      body = route === 'management-preview' ? '<section class="manager-panel manager-preview-panel"><div class="manager-panel-head"><a class="btn ghost" href="' + managementRecordLink(data.record) + '">← 返回记录追溯</a></div><iframe title="管理端表单预览" src="' + esc(data.record.preview_url || data.record.download_url || '') + '"></iframe></section>' : managementDetailHTML(data);
    } else if (route === 'management-records') {
      const data = await api('/api/management/records?' + managementQuery({page:Management.page,page_size:20}));
      if (!S.me || S.me.id !== owner || Management.owner !== owner) return {html:''};
      Management.page = data.page;
      title = '检验台账'; subtitle = '统一查询员工检验记录，保留每次检验及其关联关系';
      const download = '<div class="manager-export"><a class="btn ghost sm" data-manager-export href="' + appURL('/api/management/export.csv?' + managementQuery()) + '">导出筛选结果 CSV</a><span role="status" data-manager-export-status></span></div>';
      body = managementPanel('检验记录', managementRecordsTable(data.items || []) + managementPager(data,false), download);
    } else if (route === 'management-cases') {
      const query = managementQuery({page:Management.casePage,page_size:20,status:Management.caseStatus,overdue:Management.overdueOnly ? 1 : ''},['state','defect']);
      const data = await api('/api/management/cases?' + query);
      if (!S.me || S.me.id !== owner || Management.owner !== owner) return {html:''};
      Management.casePage = data.page;
      title = '异常跟进'; subtitle = '分派责任、明确期限，跟踪每一份不合格记录';
      const m = data.summary || {};
      body = '<section class="manager-case-metrics">' + [['待跟进',m.open,'open'],['跟进中',m.following,'following'],['已逾期',m.overdue,'overdue'],['已关闭',m.closed,'closed']].map(([label,value,state]) => '<button type="button" data-manager-case-filter="' + state + '"><span>' + label + '</span><strong>' + managementCount(value || 0) + '</strong></button>').join('') + '</section>' +
        '<div data-manager-case-editor>' + managementCaseEditor() + '</div>' + managementPanel('不合格记录与处置', managementCasesTable(data.items || []) + managementPager(data,true));
    } else {
      const data = await api('/api/management/overview?' + managementQuery({},['state','defect']));
      if (!S.me || S.me.id !== owner || Management.owner !== owner) return {html:''};
      const m = data.metrics || {};
      title = route === 'management-analysis' ? '质量分析' : '工作总览';
      subtitle = route === 'management-analysis' ? '按当前筛选范围汇总已确认检验，查看质量分布与变化' : '员工检验数据、未完成记录与异常跟进';
      body = managementMetrics(data);
      if (route === 'management') {
        body += '<a class="manager-attention" href="#/management-cases"><div><strong>异常跟进</strong><span>集中查看不合格记录和后续处理</span></div><p><b>' + managementCount(m.open_issues == null ? m.open_cases : m.open_issues) + '</b> 待处理 <em>' + managementCount(m.overdue_issues == null ? m.overdue_count : m.overdue_issues) + ' 项逾期</em></p><span aria-hidden="true">→</span></a>';
      } else {
        body += '<section class="manager-analysis-summary"><div><span>表单通过率</span><strong>' + managementPercent(m.pass_rate) + '</strong><small>合格表单 ÷ 已确认表单</small></div><div><span>复检表单</span><strong>' + managementCount(m.retest_count) + '</strong><small>不覆盖首检结果</small></div><p>统计按检验日期筛选，基于全部有权限记录汇总。草稿、待核对与示例不计为合格；首检合格率按每组记录的首次有效确认计算。</p></section>';
      }
      body += '<div class="manager-grid-two manager-charts">' + managementPanel('检验数量趋势',managementTrend(data.trend,route === 'management'),'<span class="manager-panel-note">已确认表单</span>') + managementPanel('不合格项目分布',managementDefects(data.defects)) + '</div>' + managementPanel('人员检验汇总',managementPeople(data.people),'<span class="manager-panel-note">点击人员查看记录</span>');
      if (route === 'management') body += '<p class="manager-footnote">首检合格率按首次有效确认计算，复检合格保留原首检结果。自动保存次数不计为检验次数。</p>';
    }
    const now = new Date();
    Management.updatedAt = now.toLocaleTimeString('zh-CN',{hour12:false});
    return {html:managementShell(route,title,subtitle,body,{noFilters:['management-record','management-preview'].includes(route)})};
  } catch (error) {
    return {html:managementShell(route,'管理数据暂不可用','请重试读取或调整查询条件',managementError(error.message),{noFilters:['management-record','management-preview'].includes(route)})};
  }
}

['management','management-records','management-record','management-cases','management-analysis','management-preview'].forEach((route) => {
  VIEWS[route] = (id) => managementView(route,id);
});

function managementAfterRender() {
  clearInterval(Management.timer);
  const route = parseRoute();
  if (!route.name.startsWith('management') || !managementCanRead() || route.name === 'management-api') return;
  Management.timer = setInterval(() => {
    const active = document.activeElement;
    const editing = Management.editor || (active && active.closest && active.closest('.manager-filter')) ||
      document.querySelector('.insights-teams details[open], .insights-dimension-evidence[open]');
    if (!Management.auto || document.hidden || Management.refreshing || editing || Management.filterDirty || parseRoute().name === 'management-preview') return;
    if (!parseRoute().name.startsWith('management')) return;
    managementRefresh();
  },30000);
}

async function managementRefresh() {
  if (Management.refreshing || !managementCanRead() || !parseRoute().name.startsWith('management')) return;
  Management.refreshing = true;
  try { await render(); }
  finally { Management.refreshing = false; }
}

function managementApplyFilter(form) {
  const f = Management.filters;
  Object.keys(f).forEach((key) => { if (form.elements[key]) f[key] = form.elements[key].value.trim(); });
  if (form.elements.case_status) Management.caseStatus = form.elements.case_status.value;
  if (form.elements.overdue) Management.overdueOnly = form.elements.overdue.checked;
  Management.page = 1;
  Management.casePage = 1;
  Management.filterDirty = false;
}

function managementSetPeriod(value, form) {
  const beijing = new Date(Date.now() + 8*60*60*1000);
  const end = beijing.toISOString().slice(0,10);
  if (value === 'all') {
    form.elements.date_from.value = '';
    form.elements.date_to.value = '';
  } else {
    const days = value === 'today' ? 1 : Number(value);
    beijing.setUTCDate(beijing.getUTCDate() - days + 1);
    form.elements.date_from.value = beijing.toISOString().slice(0,10);
    form.elements.date_to.value = end;
  }
  managementApplyFilter(form);
}

function managementUpdateEditor(data) {
  Management.editor = {item:data.item, history:data.history || [], assignees:data.assignees || [], saving:false, error:'', dirty:false,
    form:{assignee_id:(data.item.case || {}).assignee_id || '', due_date:(data.item.case || {}).due_date || '', status:(data.item.case || {}).status || 'open', note:''}};
}

function managementPaintEditor() {
  const box = document.querySelector('[data-manager-case-editor]');
  if (!box) return;
  box.innerHTML = appHTML(managementCaseEditor());
}

async function managementExportCSV(link) {
  if (link.hasAttribute('aria-busy')) return;
  const status = link.closest('.manager-export').querySelector('[data-manager-export-status]');
  const original = link.href;
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  const timer = setTimeout(() => { if (controller) controller.abort(); },30000);
  link.setAttribute('aria-busy','true');
  status.textContent = '正在导出…';
  try {
    const response = await fetch(original,{credentials:'same-origin',cache:'no-store',signal:controller ? controller.signal : undefined});
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error || '导出未完成，请重试。');
    }
    if (!(response.headers.get('Content-Type') || '').includes('text/csv')) throw new Error('未收到 CSV 文件，请重新登录后重试。');
    const bytes = await response.arrayBuffer();
    if (!link.isConnected) return;
    const local = URL.createObjectURL(new Blob([bytes],{type:'text/csv;charset=utf-8'}));
    link.href = local;
    link.download = '检验台账_' + new Date(Date.now()+8*60*60*1000).toISOString().slice(0,10) + '.csv';
    status.textContent = 'CSV 已就绪，可再次点击保存。';
    link.click();
    setTimeout(() => {
      URL.revokeObjectURL(local);
      if (link.href === local) { link.href = original; link.removeAttribute('download'); status.textContent = ''; }
    },5*60*1000);
  } catch (error) {
    status.textContent = error.name === 'AbortError' ? '导出超时，请稍后重试。' : error.message;
  } finally { clearTimeout(timer); link.removeAttribute('aria-busy'); }
}

document.addEventListener('submit', async (event) => {
  const form = event.target;
  if (form.matches('[data-manager-filter]')) {
    event.preventDefault();
    managementApplyFilter(form);
    await managementRefresh();
  } else if (form.matches('[data-manager-case-form]')) {
    event.preventDefault();
    const editor = Management.editor;
    if (!editor || editor.saving) return;
    const {item} = editor;
    const owner = S.me.id;
    editor.form = {assignee_id:form.elements.assignee_id.value, due_date:form.elements.due_date.value, status:form.elements.status.value, note:form.elements.note.value.trim()};
    editor.saving = true; editor.error = '';
    managementPaintEditor();
    try {
      const body = Object.assign({version:(item.case || {}).version || 0},editor.form);
      body.assignee_id = body.assignee_id ? Number(body.assignee_id) : null;
      const data = await api('/api/management/cases/' + item.kind + '/' + item.id,{method:'PUT',body});
      if (Management.editor !== editor || !S.me || S.me.id !== owner) return;
      managementUpdateEditor(data);
      toast('异常跟进已保存');
      if (parseRoute().name === 'management-cases') await managementRefresh();
    } catch (error) {
      if (Management.editor !== editor || !S.me || S.me.id !== owner) return;
      editor.error = error.message;
      editor.saving = false;
      managementPaintEditor();
    }
  }
});

document.addEventListener('input', (event) => {
  if (event.target.closest('[data-manager-filter]')) Management.filterDirty = true;
  const form = event.target.closest('[data-manager-case-form]');
  if (form && Management.editor) {
    ['assignee_id','due_date','status','note'].forEach((key) => { Management.editor.form[key] = form.elements[key].value; });
    Management.editor.dirty = true;
  }
});

document.addEventListener('change', (event) => {
  if (event.target.matches('[data-manager-auto]')) Management.auto = event.target.checked;
  if (event.target.closest('[data-manager-filter]')) Management.filterDirty = true;
  const form = event.target.closest('[data-manager-case-form]');
  if (form && Management.editor) {
    ['assignee_id','due_date','status','note'].forEach((key) => { Management.editor.form[key] = form.elements[key].value; });
    Management.editor.dirty = true;
  }
});

document.addEventListener('click', async (event) => {
  const csv = event.target.closest('[data-manager-export]');
  if (csv) {
    if (csv.protocol !== 'blob:') { event.preventDefault(); await managementExportCSV(csv); }
    return;
  }
  const button = event.target.closest('[data-manager-refresh],[data-manager-clear],[data-manager-period],button[data-manager-page],[data-manager-metric],[data-manager-person],[data-manager-defect],[data-manager-case],[data-manager-case-cancel],[data-manager-case-filter],[data-manager-clear-defect]');
  if (!button || !button.closest('.manager-layout') || button.disabled) return;
  try {
    if (button.hasAttribute('data-manager-refresh')) {
      await managementRefresh();
    } else if (button.hasAttribute('data-manager-clear-defect')) {
      Management.filters.defect = ''; Management.page = 1; await managementRefresh();
    } else if (button.hasAttribute('data-manager-clear')) {
      Object.keys(Management.filters).forEach((key) => { Management.filters[key] = ''; });
      Management.caseStatus = ''; Management.overdueOnly = false; Management.page = 1; Management.casePage = 1; Management.filterDirty = false;
      await managementRefresh();
    } else if (button.hasAttribute('data-manager-period')) {
      managementSetPeriod(button.dataset.managerPeriod,button.closest('form'));
      await managementRefresh();
    } else if (button.hasAttribute('data-manager-page')) {
      const page = Number(button.dataset.managerPage);
      if (!Number.isSafeInteger(page) || page < 1) return;
      if (button.dataset.managerList === 'cases') Management.casePage = page;
      else Management.page = page;
      await managementRefresh();
    } else if (button.hasAttribute('data-manager-metric')) {
      event.preventDefault();
      const action = button.dataset.managerMetric;
      Management.filters.state = ['pass','fail','draft','pending','final','unfinished','first','retest'].includes(action) ? action : '';
      Management.page = 1;
      go('#/management-records');
    } else if (button.hasAttribute('data-manager-person')) {
      event.preventDefault(); Management.filters.owner_id = button.dataset.managerPerson;
      if (S.me.role === 'admin') Management.filters.dept = button.dataset.managerPersonDept || '';
      Management.page = 1; go('#/management-records');
    } else if (button.hasAttribute('data-manager-defect')) {
      event.preventDefault(); Management.filters.state = 'fail'; Management.filters.defect = button.dataset.managerDefect; Management.page = 1; go('#/management-records');
    } else if (button.hasAttribute('data-manager-case-filter')) {
      const status = button.dataset.managerCaseFilter;
      Management.overdueOnly = status === 'overdue'; Management.caseStatus = status === 'overdue' ? '' : status; Management.casePage = 1;
      await managementRefresh();
    } else if (button.hasAttribute('data-manager-case-cancel')) {
      Management.editor = null; managementPaintEditor();
    } else if (button.hasAttribute('data-manager-case')) {
      const key = button.dataset.managerCase;
      if (!/^(steel|task):\d+$/.test(key)) return;
      button.disabled = true;
      const [kind,rid] = key.split(':');
      const owner = S.me.id;
      const request = ++Management.caseRequest;
      const data = await api('/api/management/cases/' + kind + '/' + rid);
      if (parseRoute().name !== 'management-cases' || !S.me || S.me.id !== owner || Management.caseRequest !== request) return;
      managementUpdateEditor(data); managementPaintEditor();
      document.querySelector('.manager-case-editor').scrollIntoView({block:'start',behavior:'smooth'});
    }
  } catch (error) { toast(error.message,true); }
  finally { if (button.isConnected) button.disabled = false; }
});

window.addEventListener('hashchange', () => {
  clearInterval(Management.timer);
  Management.filterDirty = false;
});

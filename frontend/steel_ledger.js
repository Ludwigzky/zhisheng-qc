/* The server searches every record the employee is allowed to view. */
'use strict';
let steelLedgerOwner;
let steelLedgerFilter;

async function steelLedgerHTML() {
  if (steelLedgerOwner !== S.me.id) {
    steelLedgerOwner = S.me.id;
    steelLedgerFilter = {q:'', status:'', from:'', to:'', page:1};
  }
  const filter = steelLedgerFilter;
  const query = new URLSearchParams(filter);
  const result = await api('/api/steel/records?' + query.toString());
  const options = [['','全部状态'],['draft','草稿'],['pass','合格'],['fail','不合格']];
  const cards = result.records.map((record) => {
    const data = record.data;
    const final = record.status === 'final';
    const passed = final && record.evaluation.overall === 'pass';
    const label = final ? (passed ? '合格' : '不合格') : '草稿';
    const caption = [data.specification, data.manufacturer, data.grade].filter(Boolean);
    if (data.heat_no) caption.push('炉批号 '+data.heat_no);
    const title = (data.document_no ? '单据号 '+data.document_no : '未填写单据号')+' · '+data.product_name;
    return '<a class="card steel-record-link" href="#/steel/'+record.id+'"><div class="row between"><b>'+esc(title)+
      '</b><span class="pill '+(passed ? 'ok' : final ? 'bad' : 'warn')+'">'+label+'</span></div><p>'+esc(caption.join(' · '))+
      '</p><div class="hint">记录 #'+record.id+' · '+esc(data.inspection_date || '')+
      (record.parent_id ? ' · 复检自 #'+record.parent_id : '')+
      (record.archive_origin === 'legacy' ? ' · 历史补档' : record.archive_origin === 'signed' ? ' · 已归档' : '')+'</div></a>';
  }).join('');
  return '<section class="steel-ledger"><h3>检验记录</h3><form class="card steel-ledger-search">'+
    '<label class="steel-input">单据号、炉批号、厂家或牌号<input type="search" name="q" value="'+esc(filter.q)+'"></label>'+
    '<div class="row wrap"><label class="steel-input">状态<select name="status">'+options.map(([value,label])=>
      '<option value="'+value+'"'+(filter.status===value ? ' selected' : '')+'>'+label+'</option>').join('')+'</select></label>'+
    '<label class="steel-input">检验日期起<input type="date" name="from" value="'+esc(filter.from)+'"></label>'+
    '<label class="steel-input">检验日期止<input type="date" name="to" value="'+esc(filter.to)+'"></label></div>'+
    '<div class="row wrap"><button type="submit" class="btn">查询</button><button type="button" class="btn ghost" data-ledger-clear>清空条件</button></div></form>'+
    '<p class="hint">共 '+result.total+' 条 · 第 '+result.page+' / '+result.pages+' 页</p>'+
    (cards || '<div class="card hint">没有符合当前条件的记录。</div>')+
    '<div class="row wrap"><button class="btn ghost" data-ledger-page="'+(result.page-1)+'"'+(result.page<=1 ? ' disabled' : '')+
    '>上一页</button><button class="btn ghost" data-ledger-page="'+(result.page+1)+'"'+(result.page>=result.pages ? ' disabled' : '')+
    '>下一页</button></div></section>';
}

async function steelRefreshLedger(element, filter) {
  const box = element.closest('.steel-ledger');
  if (!box || box.hasAttribute('aria-busy')) return;
  const previous = steelLedgerFilter;
  steelLedgerFilter = filter;
  box.setAttribute('aria-busy','true');
  try {
    const html = await steelLedgerHTML();
    if (!box.isConnected) return;
    box.outerHTML = appHTML(html);
    const ledger = document.querySelector('.steel-ledger');
    if (ledger) ledger.scrollIntoView({block:'start'});
  } catch (error) {
    steelLedgerFilter = previous;
    toast(error.message, true);
  } finally { box.removeAttribute('aria-busy'); }
}

document.addEventListener('submit', (event) => {
  const form = event.target;
  if (!form.matches('.steel-ledger-search')) return;
  event.preventDefault();
  steelRefreshLedger(form, {q:form.elements.q.value.trim(), status:form.elements.status.value,
    from:form.elements.from.value, to:form.elements.to.value, page:1});
});

document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-ledger-clear], [data-ledger-page]');
  if (!button || button.disabled) return;
  const filter = button.hasAttribute('data-ledger-clear') ? {q:'', status:'', from:'', to:'', page:1} :
    Object.assign({}, steelLedgerFilter, {page:Number(button.dataset.ledgerPage)});
  steelRefreshLedger(button, filter);
});

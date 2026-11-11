/* Versioned specifications and per-inspection approvals. */
'use strict';
const steelLimitNames = {
  thickness_nominal:'厚度公称', thickness_lower:'厚度下偏差', thickness_upper:'厚度上偏差',
  width_nominal:'宽度公称', width_lower:'宽度下偏差', width_upper:'宽度上偏差'
};

function steelStandardSelect(id, list) {
  return '<select id="'+id+'">'+list.map(s=>'<option value="'+s.id+'">'+
    esc(s.code+' · v'+s.revision+' · '+s.criteria.specification)+'</option>').join('')+'</select>';
}
function steelCriteriaInputs(criteria, onlyDeviations) {
  return '<div class="steel-header-grid">'+Object.entries(steelLimitNames)
    .filter(([key])=>!onlyDeviations || !key.endsWith('nominal'))
    .map(([key,label])=>'<label class="steel-input">'+label+'（mm）<input type="number" step="any" required name="'+
       key+'" value="'+esc(criteria[key] ?? '')+'"></label>').join('')+'</div>';
}
function steelStandardBox(record,readonly) {
  const std=record.standard;
  if(!std) {
    if(readonly)return '<section class="card"><strong>历史记录标准</strong><p>原定稿保留当时的规格与公差。</p></section>';
    return '<section class="card"><h3>为历史草稿绑定工艺标准</h3>'+steelStandardSelect('steel-bind-standard',Steel.standards || [])+
      '<button type="button" class="btn" data-steel-bind>绑定并重新判定</button></section>';
  }
  const req=record.deviation;
  let content='';
  if(req) {
    const result={pending:'待工艺审核', approved:'工艺已批准', rejected:'工艺已驳回'}[req.status];
    content='<p><strong>本单偏差 · '+esc(result)+'</strong></p><p>申请依据：'+esc(req.reason)+'</p>'+
      (req.reviewer ? '<p>审核：'+esc(req.reviewer)+' · '+esc(req.reviewed_at)+' · '+esc(req.decision)+'</p>' : '')+
      (req.status==='pending' && !readonly ? '<button class="btn ghost sm" type="button" data-steel-reload>更新审核状态</button>' : '');
  }
  if(!readonly && (!req || req.status==='rejected')) {
    content+='<details><summary>申请本单临时偏差</summary><form data-standard-action="request">'+
      steelCriteriaInputs(std.criteria,true)+'<label class="steel-input">申请依据<input name="reason" maxlength="300" required></label>'+
      '<button class="btn ghost" type="submit">提交工艺审核</button><p role="status"></p></form></details>';
  }
  return '<section class="card"><strong>工艺标准 '+esc(std.code)+' · v'+std.revision+'</strong><p>'+
    esc(std.criteria.specification)+' mm · '+esc(std.source)+'</p>'+content+'</section>';
}
function steelCriteriaSummary(criteria) {
  return ['thickness','width'].map((key,i)=>(i ? '宽度 ' : '厚度 ')+criteria[key+'_nominal']+
    '（'+criteria[key+'_lower']+' / '+criteria[key+'_upper']+'）').join('，');
}
VIEWS['steel-standards']=async function(){
  if(!S.me)return VIEWS.login();
  if(S.me.role!=='process')return {html:'<div class="wrap"><p>仅工艺人员可发布规格。</p><a href="#/steel">返回检验记录</a></div>'};
  const [catalog,queue]=await Promise.all([api('/api/steel/standards'),api('/api/steel/deviations')]);
  Steel.standards=catalog.standards;
  const listing=catalog.standards.map(s=>'<section class="card"><b>'+esc(s.code)+' · v'+s.revision+
    '</b><p>'+esc(steelCriteriaSummary(s.criteria))+' mm</p><p>'+esc(s.source)+'</p>'+
    '<button class="btn ghost sm" type="button" data-standard-copy="'+s.id+'">以此版本修订</button></section>').join('');
  const queueHtml=queue.requests.map(r=>'<form class="card" data-standard-action="decide" data-request="'+r.id+'">'+
    '<strong>检验记录 #'+r.record_id+' · '+esc(r.applicant)+'</strong><p>原标准 '+esc(r.standard.code)+
    ' v'+r.standard.revision+'：'+esc(steelCriteriaSummary(r.standard.criteria))+'</p><p>申请值：'+
    esc(steelCriteriaSummary(Object.assign({},r.standard.criteria,r.limits)))+'</p><p>申请依据：'+esc(r.reason)+
    '</p><label class="steel-input">审核依据<input required name="note" maxlength="300"></label>'+
    '<label class="steel-input">审核决定<select required name="decision"><option value="">请选择</option>'+
    '<option value="approved">批准，仅限本单</option><option value="rejected">驳回</option></select></label>'+
    '<button class="btn" type="submit">确认审核决定</button><p role="status"></p></form>').join('');
  const form='<form id="publish-steel-standard" class="card" data-standard-action="publish">'+
    '<h3>发布规格版本</h3><label class="steel-input">标准编号<input required name="code" maxlength="64"></label>'+
    '<input type="hidden" name="previous_revision" value="0">'+steelCriteriaInputs({},false)+
    '<details><summary>补充尺寸（最多两项）</summary>'+[0,1].map(i=>'<label class="steel-input">尺寸名称<input name="extra_name_'+i+
      '" maxlength="80"></label><label class="steel-input">标准要求<input name="extra_requirement_'+i+'" maxlength="80"></label>').join('')+'</details>'+
    '<label class="steel-input">发布依据（图纸、技术协议或标准号）<input required name="source" maxlength="160"></label>'+
    '<button class="btn" type="submit">发布</button><p role="status"></p></form>';
  return {html:'<main class="wrap"><div class="page-title">钢板工艺标准 <a href="#/home">返回任务</a></div>'+
    '<h3>待审核的本单偏差</h3>'+(queueHtml || '<p>暂无待审核申请</p>')+'<h3>现行规格</h3>'+listing+form+'</main>'};
};

async function steelRequestRecordUpdate(requestCallback) {
  const rid=Steel.record.id;
  if(steelBusy())throw new Error('请等待录入完成');
  steelCollectForm();
  if(Steel.dirty && !(await steelSave(false,true)))throw new Error('请先保存草稿');
  if(!Steel.record || Steel.record.id!==rid || Steel.dirty || parseRoute().name!=='steel')return;
  const revision=Steel.revision;
  Steel.saving=true;
  Steel.finalizing=true;
  document.querySelectorAll('.steel-form input, .steel-form select, .steel-form button').forEach(el=>{el.disabled=true;});
  try {
    const response=await requestCallback(rid,Steel.record.version);
    if(Steel.record && Steel.record.id===rid)steelAcceptSaved(response.record,revision,true);
  } finally {
    Steel.saving=false;
    Steel.finalizing=false;
    await render();
  }
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-standard-copy],[data-steel-bind],[data-steel-reload]');
  if(!button)return;
  try {
    if(button.hasAttribute('data-standard-copy')){
      const std=Steel.standards.find(s=>s.id===Number(button.dataset.standardCopy));
      const form=document.getElementById('publish-steel-standard');
      form.reset();
      Object.entries({code:std.code,previous_revision:std.revision,source:std.source,...std.criteria}).forEach(([k,v])=>{
        if(form.elements.namedItem(k))form.elements.namedItem(k).value=v;
      });
      std.criteria.extra_rows.forEach((row,i)=>{for(const k of ['name','requirement'])form.elements.namedItem('extra_'+k+'_'+i).value=row[k];});
      form.scrollIntoView({block:'start'});
    } else if(button.hasAttribute('data-steel-bind')){
      const sid=Number(document.getElementById('steel-bind-standard').value);
      await steelRequestRecordUpdate((rid,version)=>api('/api/steel/records/'+rid+'/bind-standard',
        {method:'POST',body:{standard_id:sid,version}}));
    } else if(!steelBusy()) {
      steelCollectForm();
      const previous=Steel.record;
      const {record}=await api('/api/steel/records/'+previous.id);
      if(Steel.record!==previous || parseRoute().name!=='steel')return;
      if(!Steel.dirty) {
        Steel.record=null;await render();
      } else {
        // Approval advances the version while the inspector may be typing.
        // Rebase only if the server changed the four tolerances alone.
        const keys=['thickness_lower','thickness_upper','width_lower','width_upper'];
        const measurements=d=>Object.fromEntries(Object.entries(d).filter(([k])=>!keys.includes(k)));
        if(record.status!=='draft' || record.owner_id!==S.me.id ||
           JSON.stringify(measurements(record.data))!==JSON.stringify(measurements(previous.data))){
          throw new Error('另一页面也已修改测量数据，请先核对两份草稿。');
        }
        Steel.record=record;
        keys.forEach(k=>{Steel.data[k]=record.data[k];});
        steelChanged();await steelEvaluate();await render();
      }
    } else toast('请等待当前录入或保存完成。',true);
  } catch(err){toast(err.message,true);}
});
document.addEventListener('submit',async event=>{
  const form=event.target;
  const action=form.dataset.standardAction;
  if(!action)return;
  event.preventDefault();
  const data=Object.fromEntries(new FormData(form));
  const button=form.querySelector('button[type="submit"]');
  button.disabled=true;
  try{
    if(action==='publish'){
      data.previous_revision=Number(data.previous_revision);
      data.extra_rows=[];
      for(let i=0;i<2;i++){
        const name=data['extra_name_'+i].trim(), requirement=data['extra_requirement_'+i].trim();
        if(name || requirement)data.extra_rows.push({name,requirement});
      }
      await api('/api/steel/standards',{method:'POST',body:data});await render();
      toast('标准已发布，新建记录可选择该版本');
    } else if(action==='decide'){
      await api('/api/steel/deviations/'+form.dataset.request+'/decision',{method:'POST',body:data});
      await render();toast('已记录本单审核决定');
    } else if(action==='request'){
      await steelRequestRecordUpdate((rid,version)=>api('/api/steel/records/'+rid+'/deviation',
        {method:'POST',body:{...data,version}}));toast('偏差申请已提交');
    }
  }catch(error){form.querySelector('[role="status"]').textContent=error.message;toast(error.message,true);}
  finally{button.disabled=false;}
});

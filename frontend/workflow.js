'use strict';

function taskAssignmentHTML(t) {
  if(t.blocked_by.length)return '<section class="card">等待前置工序 '+esc(t.blocked_by.join('、'))+' 检验合格。</section>';
  if(t.status==='pass')return '';
  if(!t.assignee_id)return S.me.role==='inspector' ? '<section class="card"><button class="btn" data-work-claim>领取此任务</button></section>' : '<section class="card">等待质量工程师领取。</section>';
  const people=S.users.filter(u=>u.id!==t.assignee_id);
  const canHandoff=S.me.role==='supervisor' || (S.me.role==='inspector' && S.me.id===t.assignee_id);
  return '<section class="card">领取人 '+esc(t.assignee)+(canHandoff && people.length ?
    '<div class="row wrap"><label>接收质量工程师 <select data-work-recipient><option value="">请选择</option>'+
    people.map(u=>'<option value="'+u.id+'">'+esc(u.name)+'</option>').join('')+
    '</select></label><button class="btn ghost" data-work-handoff>交接任务</button></div>' : '')+'</section>';
}

VIEWS['work-admin']=async function(id){
  if(!S.me)return VIEWS.login();
  if(S.me.role!=='admin')return {html:'<div class="wrap">需要部长权限。</div>'};
  const editing=/^\d+$/.test(id);
  const plan=editing ? await api('/api/plans/'+id) : null;
  const steps=plan ? plan.nodes : [{seq:1,name:'来料检验',predecessors:[]},{seq:2,name:'过程检验',predecessors:[1]},{seq:3,name:'出厂检验',predecessors:[2]}];
  const unavailable=plan && (plan.plan.status==='closed' || steps.some(s=>s.task_status!=='open'));
  const fields=steps.filter(s=>s.seq>1).map(step=>'<fieldset><legend>'+esc(step.name)+'的前置工序</legend>'+
    steps.filter(s=>s.seq<step.seq).map(s=>'<label><input type="checkbox" data-step="'+step.seq+'" value="'+s.seq+'" '+
      (step.predecessors.includes(s.seq)?'checked':'')+'> '+esc(s.name)+'</label> ').join('')+'</fieldset>').join('');
  return {html:'<main class="wrap"><a href="'+(plan?'#/plan/'+id:'#/home')+'">← 返回</a><h2>'+
    (plan?'配置检验计划':'新建检验计划')+'</h2><form class="card" data-work-plan="'+(plan?id:'')+'"><fieldset class="work-fields" '+(unavailable?'disabled':'')+'>'+
    (plan ? '<p>'+esc(plan.product.name)+' · '+esc(plan.product.code)+'</p>' :
    '<label>产品工号<input name="code" required maxlength="60" pattern="[a-zA-Z0-9_\\-]+"></label><label>产品名称<input name="name" required maxlength="80"></label>')+
    '<label>负责班组<input name="workgroup" required maxlength="80" value="'+esc(plan ? plan.plan.workgroup : S.me.dept)+'"></label>'+
    '<p class="hint">使用邀请账号时填写的班组名。不勾选前置工序即可并行。</p>'+fields+
    '<button class="btn" type="submit">保存计划</button></fieldset><p class="hint" role="status">'+(unavailable?'计划已领用，当前配置已锁定。':'')+'</p></form></main>'};
};
document.addEventListener('submit',async event=>{
  const form=event.target.closest('form[data-work-plan]');if(!form)return;
  event.preventDefault();
  const predecessors={'1':[],'2':[],'3':[]};
  for(const el of form.querySelectorAll('input[data-step]:checked'))predecessors[el.dataset.step].push(Number(el.value));
  const values=Object.fromEntries(new FormData(form));
  const id=form.dataset.workPlan;
  const button=form.querySelector('button');button.disabled=true;
  try{
    const result=await api(id?'/api/plans/'+id+'/workflow':'/api/work/plans',{method:'POST',body:{...values,predecessors}});
    go('#/plan/'+(id||result.plan_id));
  }catch(e){form.querySelector('[role="status"]').textContent=e.message;}
  finally{button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-work-claim],[data-work-handoff]');
  if(!button || !S.task)return;
  const task=S.task,claim=button.hasAttribute('data-work-claim');
  const recipient=claim ? null : Number(document.querySelector('[data-work-recipient]').value);
  if(!claim && !recipient){toast('请选择接收人',true);return;}
  button.disabled=true;
  try{
    await api('/api/tasks/'+task.id+(claim?'/claim':'/handoff'),{method:'POST',body:{version:task.version,recipient}});
    S.task=null;await render();toast(claim?'领取成功':'任务已交接');
  }catch(e){toast(e.message,true);}
  finally{button.disabled=false;}
});

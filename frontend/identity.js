/* In-person enrollment: the administrator assigns the employee identity and
   role, then the employee sets their own password on their phone. */
'use strict';
const accountRoles = {admin:'部长', inspector:'质量工程师', supervisor:'室主任', process:'工艺人员'};
let identityMode = 'login';
let issuedInvitation = null;

function identityField(label, name, attributes) {
  return '<label class="identity-field">' + esc(label) + '<input name="'+name+'" required '+attributes+'></label>';
}
function identityPassword(newPassword) {
  return identityField(newPassword ? '设置密码（至少8个字符）' : '密码', 'password',
    'type="password" maxlength="128" autocomplete="'+(newPassword ? 'new-password' : 'current-password')+'"'+(newPassword ? ' minlength="8"' : ''));
}
function identityEmployee() {
  return identityField('账号', 'emp_no', 'maxlength="32" autocomplete="username" placeholder="工号或个人账号" autocapitalize="none" spellcheck="false"');
}
function identityError(form, message) {
  const note = form.querySelector('[data-identity-error]');
  if (note) note.textContent = message;
}
function identityForm(action, contents, button) {
  return '<form class="identity-form" data-identity-form="'+action+'">'+contents+
    '<p class="identity-error" data-identity-error role="alert"></p><button type="submit" class="btn">'+esc(button)+'</button></form>';
}

VIEWS.login = async function() {
  if (S.me) {
    go(S.me.is_admin ? '#/accounts' : '#/home');
    return {html:''};
  }
  const state = await api('/api/auth/setup-state');
  const prefix='<div class="identity-page"><main class="card identity-card"><p class="identity-brand">卫华 · 质检采集</p>';
  let content;
  if (state.setup_required) {
    if (state.local_setup_allowed) {
      content='<h1>创建部长</h1><p>部长负责邀请员工、分配角色和管理账号。</p>'+
        identityForm('setup', identityEmployee()+
          identityField('部长姓名','name','maxlength="48" autocomplete="name"')+
          identityField('班组','dept','maxlength="48" value="现场演示组"')+
          identityPassword(true), '创建部长');
    } else {
      content='<h1>等待部长开通</h1><p>请在运行服务的电脑上打开启动窗口中的“本机访问”地址并创建部长。收到邀请码后，刷新本页设置密码。</p>'+
        '<button type="button" class="btn ghost" data-identity-refresh>刷新</button>';
    }
  } else if (identityMode==='register') {
    content='<h1>凭邀请码设置密码</h1><p>输入部长发给你的一次性邀请码，完成后即可使用分配的账号登录。</p>'+
      identityForm('register', identityField('邀请码','code','maxlength="64" autocomplete="off" autocapitalize="characters" spellcheck="false"')+
        identityPassword(true), '设置密码并登录')+
      '<button class="btn ghost identity-mode" type="button" data-identity-mode="login">返回登录</button>';
  } else {
    content='<h1>员工登录</h1>'+identityForm('login', identityEmployee()+identityPassword(false),'登录')+
      '<button class="btn ghost identity-mode" type="button" data-identity-mode="register">使用邀请码设置密码</button>';
  }
  return {html:prefix+content+'</main></div>'};
};

VIEWS.accounts = async function() {
  if (!S.me) return VIEWS.login();
  if (!S.me.is_admin) return {html:'<div class="wrap"><p>此页面仅对部长开放。</p><a href="#/home">返回首页</a></div>'};
  const data = await api('/api/admin/accounts');
  const fields=identityEmployee()+identityField('员工姓名','name','maxlength="48" autocomplete="off"')+
    identityField('班组','dept','maxlength="48" value="'+esc(S.me.dept)+'"')+
    '<label class="identity-field">角色<select name="role"><option value="inspector">质量工程师</option>'+
    '<option value="supervisor">室主任</option><option value="process">工艺人员</option></select></label>';
  const invitation=issuedInvitation && issuedInvitation.actorId===S.me.id ? issuedInvitation : null;
  const receipt=invitation ? '<section class="card identity-invite-receipt" role="status"><h2>账号 '+esc(invitation.emp_no)+' 的邀请码</h2>'+
    '<p>把账号和邀请码发给本人。对方连接同一热点后，打开“手机访问”地址，点“使用邀请码设置密码”。邀请码24小时内有效。</p>'+
    '<div class="identity-code"><code>'+esc(invitation.code)+'</code><button class="btn ghost sm" type="button" data-identity-copy>复制邀请码</button></div></section>' : '';
  const toolbar= (id, action, label) => '<button class="btn ghost sm" type="button" data-identity-account="'+action+'" data-id="'+id+'">'+label+'</button>';
  const rows=data.users.map((u)=>'<li class="identity-row"><div><strong>'+esc(u.name)+'</strong> · '+esc(u.emp_no)+
    '<div class="hint">'+esc(u.dept)+' · '+esc(accountRoles[u.role] || u.role)+(u.enabled ? '' : ' · 已停用')+'</div></div>'+
    (!u.is_admin ? '<div class="identity-actions">'+toolbar(u.id,u.enabled ? 'disable':'enable',u.enabled ? '停用':'启用')+
      toolbar(u.id,'reset','重设密码')+'</div>' : '')+'</li>').join('');
  const pending=data.invites.map((i)=>'<li class="identity-row"><div><strong>'+esc(i.name)+'</strong> · '+esc(i.emp_no)+
    '<div class="hint">'+esc(i.dept)+' · '+esc(accountRoles[i.role])+' · '+(i.reset_user_id?'待重设密码':'待激活')+'</div></div>'+
    '<button class="btn ghost sm" data-identity-revoke="'+i.id+'">撤销邀请码</button></li>').join('');
  return {html:'<main class="wrap identity-manage"><div class="page-title">账号管理 <a class="btn ghost sm" href="#/home">返回首页</a></div>'+
    receipt+'<section class="card"><h2>邀请员工</h2>'+identityForm('invite',fields,'生成邀请码')+'</section>'+
    (pending?'<section class="card"><h2>待使用的邀请码</h2><ul>'+pending+'</ul></section>':'')+
    '<section class="card"><h2>员工账号</h2><ul>'+rows+'</ul></section></main>'};
};

document.addEventListener('submit', async event=>{
  const form=event.target.closest('[data-identity-form]');
  if (!form) return;
  event.preventDefault();
  const button=form.querySelector('[type=submit]');
  if (button.disabled) return;
  button.disabled=true;
  identityError(form,'');
  const action=form.dataset.identityForm;
  try {
    const body=Object.fromEntries(new FormData(form).entries());
    const path={login:'/api/login',setup:'/api/auth/setup',register:'/api/auth/register',invite:'/api/admin/invites'}[action];
    const result=await api(path,{method:'POST',body});
    if (action==='invite') {
      issuedInvitation=Object.assign({actorId:S.me.id},result);
      await render();
      const receipt=$('.identity-invite-receipt');
      if (receipt) receipt.scrollIntoView({block:'center'});
      return;
    }
    const route=parseRoute();
    // Preserve protected record links across authentication. Reload clears
    // any form values or old-session data remaining in the previous view.
    const returnToRecord=['steel','steel-preview'].includes(route.name) && (!route.id || /^\d+$/.test(route.id));
    const managementPages=['management','management-records','management-record','management-preview','management-cases','management-analysis'];
    const returnToManagement=managementPages.includes(route.name) &&
      (!route.id || (['management-record','management-preview'].includes(route.name) && /^(steel|task):\d+$/.test(route.id)));
    const next=(returnToRecord || returnToManagement) ? location.hash : result.user.is_admin ? '#/accounts' : '#/home';
    history.replaceState(null,'',next);
    location.reload();
  } catch(error) { identityError(form,error.message); }
  finally { button.disabled=false; }
});

document.addEventListener('click', async event=>{
  const button=event.target.closest('[data-identity-mode],[data-identity-refresh],[data-identity-copy],[data-identity-account],[data-identity-revoke]');
  if (!button || button.disabled) return;
  const data=button.dataset;
  try {
    if (data.identityMode || button.hasAttribute('data-identity-refresh')) {
      identityMode=data.identityMode || 'login';
      await render();
      return;
    }
    if (button.hasAttribute('data-identity-copy')) {
      if (!issuedInvitation) return;
      try { await navigator.clipboard.writeText(issuedInvitation.code); toast('邀请码已复制'); }
      catch (_) { toast('请长按并复制上方邀请码'); }
      return;
    }
    const revoke=data.identityRevoke;
    const action=data.identityAccount;
    const label=revoke ? '撤销这个邀请码' : {disable:'停用此账号并结束其登录',enable:'重新启用此账号',reset:'结束此账号的登录，并为本人生成重设密码的邀请码'}[action];
    if (!window.confirm('确认'+label+'？')) return;
    button.disabled=true;
    const path=revoke ? '/api/admin/invites/'+revoke+'/revoke' : '/api/admin/accounts/'+data.id+'/'+action;
    const result=await api(path,{method:'POST',body:{}});
    issuedInvitation=result.code ? Object.assign({actorId:S.me.id},result) : null;
    await render();
    if (result.code) $('.identity-invite-receipt').scrollIntoView({block:'center'});
  } catch(error) { toast(error.message,true); }
  finally { button.disabled=false; }
});
window.addEventListener('hashchange',()=>{
  if (parseRoute().name!=='accounts') issuedInvitation=null;
});

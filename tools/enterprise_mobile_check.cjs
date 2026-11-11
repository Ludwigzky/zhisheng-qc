/* Invoked only by enterprise_mobile_check.py, with its guarded disposable service. */
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const crypto = require('node:crypto');
const modules = process.env.CODEX_BUNDLED_NODE_MODULES ||
  'C:\\Program Files\\WindowsApps\\OpenAI.CodexPrimaryRuntime.v26-1105-1297-0_26.1105.1297.0_x64__3k8sg7r9htsxt\\dependencies\\node\\node_modules';
const {chromium} = require(path.join(modules,'playwright'));
const chromePath = process.env.QC_TEST_CHROME ||
  'C:\\Users\\98060\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe';

async function main() {
  const base=process.env.QC_ENTERPRISE_BASE;
  assert.equal(new URL(base).hostname,'127.0.0.1');
  const check=await fetch(base+'/_enterprise_ui_fixture');
  assert.ok(process.env.QC_ENTERPRISE_NONCE);
  assert.equal((await check.json()).nonce,process.env.QC_ENTERPRISE_NONCE);
  const browser=await chromium.launch({executablePath:chromePath,headless:true});
  const errors=[];
  const password='Ui-'+crypto.randomBytes(16).toString('hex');
  async function pageFor(context) {
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    page.setDefaultTimeout(8000);
    return page;
  }
  async function fillProfile(form, profile) {
    for (const [name,value] of Object.entries(profile)) {
      const input=form.locator('[name='+name+']');
      if (name==='role') await input.selectOption(value);
      else await input.fill(value);
    }
  }
  async function mobile() {
    return browser.newContext({baseURL:base, isMobile:true, hasTouch:true,viewport:{width:393,height:852},
      userAgent:'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36'});
  }
  async function assertFits(page) {
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
  }
  try {
    const desktop=await browser.newContext({baseURL:base});
    const admin=await pageFor(desktop);
    await admin.goto('/#/login');
    const setup=admin.locator('[data-identity-form=setup]');
    await fillProfile(setup,{emp_no:'OWNER-UI',name:'首位管理员',dept:'管理组',password});
    await setup.locator('[type=submit]').click();
    await admin.waitForURL('**/#/accounts');
    async function issue(profile) {
      const form=admin.locator('[data-identity-form=invite]');
      await fillProfile(form,profile);
      await Promise.all([admin.waitForResponse(r=>r.url().endsWith('/api/admin/invites') && r.status()===201),
        form.locator('[type=submit]').click()]);
      const receipt=admin.locator('.identity-invite-receipt').filter({hasText:profile.emp_no});
      await receipt.waitFor();
      return receipt.locator('code').innerText();
    }
    const worker=await issue({emp_no:'INSPECT-UI',name:'真实检验员',dept:'甲班组',role:'inspector'});
    const processCode=await issue({emp_no:'TECH-UI',name:'真实工艺员',dept:'乙班组',role:'process'});
    const nextShift=await issue({emp_no:'SHIFT-UI',name:'接班检验员',dept:'甲班组',role:'inspector'});
    const workerContext=await mobile(), processContext=await mobile();
    const employee=await pageFor(workerContext), engineer=await pageFor(processContext), recipient=await pageFor(await mobile());
    async function enroll(page,code) {
      await page.goto('/#/steel');
      const login=page.locator('[data-identity-form=login]');
      await login.waitFor();
      await assertFits(page);
      await page.getByRole('button',{name:'使用邀请码设置密码'}).click();
      const form=page.locator('[data-identity-form=register]');
      await fillProfile(form,{code,password});
      await form.locator('[type=submit]').click();
      await page.locator('#topbar .userchip').waitFor();
      const u=await (await page.request.get('/api/me')).json();
      assert.ok(u.user && !u.user.is_admin);
    }
    await enroll(employee,worker); await enroll(engineer,processCode); await enroll(recipient,nextShift);
    await engineer.locator('#publish-steel-standard').waitFor();
    assert.ok(await employee.locator('#steel-new-standard').isVisible());
    await employee.locator('[data-steel-act=new]').click();
    await employee.locator('.steel-form').waitFor();
    const id=(await employee.locator('.steel-workspace').getAttribute('data-steel-record-id'));
    assert.ok(/^\d+$/.test(id));
    const header=k=>employee.locator('[data-steel-header="'+k+'"]');
    const value=k=>employee.locator('[data-steel-value="'+k+'"]');
    for(const [field,input] of Object.entries({document_no:'E2E-117',manufacturer:'测试生产厂',grade:'Q235B'})) {
      await header(field).fill(input);
    }
    assert.equal(await header('inspection_mode').inputValue(),'sampling');
    assert.equal(await header('supply_quantity').inputValue(),'1');
    assert.equal(await header('inspected_quantity').inputValue(),'1');
    for(const field of ['cert_provided','cert_performance','identity_match','cert_seal','surface','shape','coil']) {
      await value(field).selectOption('合格');
    }
    await value('thickness').fill('5.71');
    await value('width').fill('1806');
    const finish=employee.locator('.steel-readonly-label [data-steel-act=finalize]');
    const [final]=await Promise.all([employee.waitForResponse(r=>r.url().endsWith('/records/'+id+'/finalize')),finish.tap()]);
    assert.equal(final.status(),200,await final.text());
    const card=employee.locator('.steel-download-card');
    await card.waitFor(); await assertFits(employee);
    const [download]=await Promise.all([employee.waitForEvent('download'),card.locator('[data-steel-download]').tap()]);
    assert.ok(download.suggestedFilename().includes('E2E-117'));
    await download.saveAs(process.env.QC_ENTERPRISE_DOWNLOAD);
    for(const suffix of ['', '/preview','/xlsx','/voice-history']){
      const response=await engineer.request.get('/api/steel/records/'+id+suffix);
      assert.ok([403,404,409].includes(response.status()),'cross-group '+suffix+' '+response.status());
    }
    const workerRoute='/#/steel/'+id;
    await employee.locator('#topbar .userchip').tap();
    await Promise.all([employee.waitForEvent('load'),
      employee.waitForResponse(r=>r.url().endsWith('/api/logout') && r.status()===200),
      employee.locator('[data-act=logout]').click()]);
    await employee.locator('[data-identity-form=login]').waitFor();
    await employee.goto(workerRoute);
    const login=employee.locator('[data-identity-form=login]');
    await fillProfile(login,{emp_no:'INSPECT-UI',password});
    await login.locator('[type=submit]').tap();
    await employee.locator('.steel-download-card').waitFor();
    assert.equal(new URL(employee.url()).hash,'#/steel/'+id);
    assert.equal((await employee.request.post('/api/admin/invites',{data:{}})).status(),403);

    await admin.getByRole('link',{name:'返回首页'}).click();
    await admin.getByRole('link',{name:'新建检验计划'}).click();
    const plan=admin.locator('[data-work-plan]');
    await fillProfile(plan,{code:'UI-WORK-013',name:'现场工序交接验收',workgroup:'甲班组'});
    const [created]=await Promise.all([admin.waitForResponse(r=>r.url().endsWith('/api/work/plans')),
      plan.locator('[type=submit]').click()]);
    assert.equal(created.status(),201,await created.text());
    const planId=(await created.json()).plan_id;
    await employee.goto('/#/home');
    await employee.locator('[data-act=openplan][data-id="'+planId+'"]').tap();
    const node=employee.locator('[data-act=opentask]').first();
    await node.waitFor();
    const taskId=await node.getAttribute('data-id');
    await node.tap();
    const take=employee.locator('[data-work-claim]');
    await take.waitFor();
    const recordPath='/api/tasks/'+taskId;
    const state=async()=> (await (await employee.request.get(recordPath)).json()).task;
    assert.equal((await state()).assignee_id,null);
    const [claimed]=await Promise.all([employee.waitForResponse(r=>r.url().endsWith(recordPath+'/claim')),take.tap()]);
    assert.equal(claimed.status(),200,await claimed.text());
    const receiver=employee.locator('[data-work-recipient]');
    await receiver.selectOption({label:'接班检验员'});
    const [handoff]=await Promise.all([employee.waitForResponse(r=>r.url().endsWith(recordPath+'/handoff')),
      employee.locator('[data-work-handoff]').tap()]);
    assert.equal(handoff.status(),200,await handoff.text());
    const assigned=await state();
    const peerUser=(await (await recipient.request.get('/api/me')).json()).user;
    assert.equal(assigned.assignee_id,peerUser.id);
    await recipient.goto('/#/task/'+taskId);
    await recipient.locator('[data-work-handoff]').waitFor();
    await assertFits(recipient);
    assert.ok([403,404].includes((await engineer.request.get(recordPath)).status()));
    assert.deepEqual(errors,[]);
    console.log('PASS: administrator setup, three mobile enrollments, role UIs, signing, protected download, cross-group denial, login return and 393px layout.');
    console.log('PASS: administrator created a plan through the homepage; inspector viewed the unclaimed stage, claimed it, then handed it to another inspector in the same team.');
  } finally {await browser.close();}
}
main().catch(err=>{console.error(err);process.exitCode=1;});

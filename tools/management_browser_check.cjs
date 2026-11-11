/* Only run with management_browser_check.py's nonce-guarded temporary service. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const modules = process.env.CODEX_BUNDLED_NODE_MODULES ||
  'C:\\Program Files\\WindowsApps\\OpenAI.CodexPrimaryRuntime.v26-1105-1297-0_26.1105.1297.0_x64__3k8sg7r9htsxt\\dependencies\\node\\node_modules';
const {chromium} = require(path.join(modules, 'playwright'));
const output = path.resolve(__dirname, '../_probe/management');
const failures = [];
let checks = 0;
function check(name, condition) {
  checks++;
  if (!condition) failures.push(name);
  assert.ok(condition, name);
  console.log('PASS: ' + name);
}

async function main() {
  const base = process.env.QC_MANAGER_BASE;
  assert.equal(new URL(base).hostname, '127.0.0.1');
  const fixture = await (await fetch(base+'/_management_ui_fixture')).json();
  assert.equal(fixture.nonce, process.env.QC_MANAGER_NONCE);
  assert.ok(fixture.nonce);
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({
    executablePath: process.env.QC_TEST_CHROME || 'C:\\Users\\98060\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe',
    headless:true, args:['--no-proxy-server'],
  });
  const errors = [];
  const contexts = [];
  async function account(name, width=1365) {
    const context = await browser.newContext({baseURL:base, viewport:{width,height:900}});
    contexts.push(context);
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/#/management');
    const form = page.locator('[data-identity-form=login]');
    await form.waitFor();
    await form.locator('[name=emp_no]').fill(name);
    await form.locator('[name=password]').fill(process.env.QC_MANAGER_PASSWORD);
    await form.locator('[type=submit]').click();
    await page.locator('#topbar .userchip').waitFor();
    return {page,context};
  }
  async function managerPage(page, route) {
    await page.goto('/#/'+route);
    await page.locator('.manager-layout[data-manager-page="'+route.split('/')[0]+'"]').waitFor();
    if (route.startsWith('management-record/')) {
      await page.locator('.manager-chain-item.current[href="#/'+route+'"]').waitFor();
    }
    if (route === 'management-api') await page.locator('[data-ai-config]').waitFor();
    else if (['management-reports','management-dimensions'].includes(route)) await page.locator('[data-insights-form]').waitFor();
    else if (!route.startsWith('management-record/')) await page.locator('[data-manager-filter]').waitFor();
    check('management view has no page-level overflow '+route,
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1));
  }
  try {
    const chief = await account('CHIEF-A');
    const admin = await account('ADMIN');
    const employee = await account('WORKER-A',393);
    check('management deep link survives login', new URL(chief.page.url()).hash === '#/management');
    check('employee cannot call management API',
      (await employee.context.request.get('/api/management/overview')).status() === 403);
    const initial = await (await chief.context.request.get('/api/management/overview')).json();
    check('supervisor initially sees only three finalized records from its team', initial.metrics.final_count === 3);
    check('a passed followup does not overwrite the first failed inspection', initial.metrics.first_pass_rate === 50);
    await chief.page.screenshot({path:path.join(output,'overview-desktop.png'),fullPage:true});
    await employee.page.goto('/#/steel');
    await employee.page.locator('[data-steel-act=new]').click();
    await employee.page.locator('.steel-form').waitFor();
    const currentId = await employee.page.locator('.steel-workspace').getAttribute('data-steel-record-id');
    for (const [key,value] of Object.entries({
      document_no:'LIVE-EMPLOYEE-909',manufacturer:'员工实时录入验证厂',grade:'Q355B',
    })) await employee.page.locator('[data-steel-header="'+key+'"]').fill(value);
    for (const key of ['cert_provided','cert_performance','identity_match','cert_seal','surface','shape','coil'])
      await employee.page.locator('[data-steel-value="'+key+'"]').selectOption('合格');
    await employee.page.locator('[data-steel-value=thickness]').fill('5.72');
    await employee.page.locator('[data-steel-value=width]').fill('1808');
    const [finalized] = await Promise.all([
      employee.page.waitForResponse(r => r.url().endsWith('/records/'+currentId+'/finalize')),
      employee.page.locator('.steel-readonly-label [data-steel-act=finalize]').click(),
    ]);
    check('employee normal form finalizes successfully', finalized.status() === 200);
    const [updated] = await Promise.all([
      chief.page.waitForResponse(r => new URL(r.url()).pathname === '/api/management/overview'),
      chief.page.locator('[data-manager-refresh]').click(),
    ]);
    const overview = await updated.json();
    check('employee finalization is visible to a separate manager browser', overview.metrics.final_count === 4);
    await chief.page.locator('[data-manager-page=management]').waitFor();
    await managerPage(chief.page,'management-records');
    let filters = chief.page.locator('[data-manager-filter]');
    await filters.locator('[name=q]').fill('LIVE-EMPLOYEE-909');
    await filters.locator('[type=submit]').click();
    const recordLink = chief.page.locator('a[href="#/management-record/steel:'+currentId+'"]').first();
    await recordLink.waitFor();
    check('ledger finds a newly signed employee record by document number', await recordLink.isVisible());
    await recordLink.click();
    await chief.page.locator('[data-manager-page=management-record]').waitFor();
    const text = await chief.page.locator('.manager-main').innerText();
    check('trace detail shows actual measurements, operator and approved standard',
      text.includes('5.72') && text.includes('1808') && text.includes('甲组检验员') && text.includes('标准'));
    await chief.page.screenshot({path:path.join(output,'trace-desktop.png'),fullPage:true});
    const artifactLink = chief.page.locator('a[href="/api/steel/records/'+currentId+'/xlsx"]');
    await artifactLink.waitFor();
    const file = await chief.context.request.get(await artifactLink.getAttribute('href'));
    check('trace detail links to the actual archived Excel', file.status() === 200 &&
      (await file.body()).readUInt32LE(0) === 0x04034b50);
    await chief.page.locator('a[href="#/management-preview/steel:'+currentId+'"]').click();
    await chief.page.locator('.manager-preview-panel iframe').waitFor();
    await chief.page.frameLocator('.manager-preview-panel iframe').getByText('LIVE-EMPLOYEE-909',{exact:true}).waitFor();
    await chief.page.locator('.manager-preview-panel a[href="#/management-record/steel:'+currentId+'"]').click();
    await chief.page.locator('[data-manager-page=management-record]').waitFor();
    check('archive preview returns directly to the management trace', new URL(chief.page.url()).hash === '#/management-record/steel:'+currentId);
    await managerPage(chief.page,'management-record/steel:'+fixture.failed);
    check('trace links the original failed inspection to its passing followup',
      await chief.page.locator('a[href="#/management-record/steel:'+fixture.followup+'"]').count() > 0);
    await managerPage(chief.page,'management-cases');
    await chief.page.locator('[data-manager-clear]').click();
    await chief.page.locator('[data-manager-page=management-cases]').waitFor();
    await chief.page.locator('[data-manager-case="steel:'+fixture.failed+'"]').click();
    const editor = chief.page.locator('[data-manager-case-form]');
    await editor.waitFor();
    await editor.locator('[name=assignee_id]').selectOption(String(fixture.ids['WORKER-A']));
    await editor.locator('[name=status]').selectOption('following');
    await editor.locator('[name=note]').fill('已联系本班检验员核查表面缺陷与复检结果');
    const [caseResponse] = await Promise.all([
      chief.page.waitForResponse(r => new URL(r.url()).pathname === '/api/management/cases/steel/'+fixture.failed &&
        ['PATCH','PUT'].includes(r.request().method())),
      editor.locator('[type=submit]').click(),
    ]);
    check('manager assigns followup with an auditable note', caseResponse.status() < 300);
    await chief.page.locator('[data-manager-case-form] [name=note]').waitFor();
    const saved = await (await chief.context.request.get('/api/management/cases/steel/'+fixture.failed)).json();
    check('case state is persisted', JSON.stringify(saved).includes('following') &&
      JSON.stringify(saved).includes('已联系本班检验员'));
    const closing = chief.page.locator('[data-manager-case-form]');
    await closing.locator('[name=status]').selectOption('closed');
    await closing.locator('[name=note]').fill('已核对后续正式复检合格记录，完成本项跟进');
    const [closed] = await Promise.all([
      chief.page.waitForResponse(r => new URL(r.url()).pathname === '/api/management/cases/steel/'+fixture.failed &&
        ['PATCH','PUT'].includes(r.request().method())),
      closing.locator('[type=submit]').click(),
    ]);
    check('manager closes the issue after the verified passing followup', closed.status() === 200);
    await chief.page.screenshot({path:path.join(output,'cases-desktop.png'),fullPage:true});
    await managerPage(chief.page,'management-analysis');
    check('analysis shows an explicit first-inspection metric',
      (await chief.page.locator('.manager-main').innerText()).includes('首检'));
    await chief.page.screenshot({path:path.join(output,'analysis-desktop.png'),fullPage:true});
    const exportResponse = await chief.context.request.get('/api/management/export.csv');
    const csv = (await exportResponse.body()).toString('utf8');
    check('CSV contains current team records and excludes other teams',
      exportResponse.status() === 200 && csv.includes('LIVE-EMPLOYEE-909') && !csv.includes('B-SECRET-303'));
    await managerPage(chief.page,'management-records');
    const [exported] = await Promise.all([
      chief.page.waitForEvent('download'),
      chief.page.locator('[data-manager-export]').click(),
    ]);
    const exportedCsv = fs.readFileSync(await exported.path()).toString('utf8');
    check('visible export control downloads its authenticated current-team CSV',
      exported.url().startsWith('blob:') && exportedCsv.includes('LIVE-EMPLOYEE-909') && !exportedCsv.includes('B-SECRET-303'));
    const all = await (await admin.context.request.get('/api/management/overview')).json();
    check('administrator overview includes all authorized teams', all.metrics.final_count === 5);
    const mobile = await account('CHIEF-A',393);
    await mobile.page.locator('.manager-layout').waitFor();
    check('mobile dashboard has no page-level overflow',
      await mobile.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1));
    await mobile.page.screenshot({path:path.join(output,'overview-mobile.png'),fullPage:true});
    await managerPage(mobile.page,'management-records');
    await managerPage(mobile.page,'management-record/steel:'+fixture.failed);
    await managerPage(mobile.page,'management-cases');
    await managerPage(chief.page,'management-reports');
    const daily = await (await chief.context.request.get('/api/management/insights/report')).json();
    check('daily interpretation agrees with finalized source records', daily.metrics.final_count === 4 && daily.metrics.retest_count === 1);
    check('daily report excludes another department and states its local generation mode', !JSON.stringify(daily).includes('B-SECRET') && daily.mode === 'local_rules');
    await chief.page.locator('.insights-teams summary').click();
    await chief.page.locator('a[href="#/management-record/steel:'+currentId+'"]').first().click();
    await chief.page.locator('[data-manager-page=management-record]').waitFor();
    check('report evidence opens the actual record', (await chief.page.locator('.manager-main').innerText()).includes('LIVE-EMPLOYEE-909'));
    await chief.page.goBack();
    await chief.page.locator('[data-insights-form=report]').waitFor();
    await chief.page.locator('[data-insights-form=report] [name=period]').selectOption('week');
    const [weekResponse] = await Promise.all([
      chief.page.waitForResponse(r => r.url().includes('/insights/report?') && r.url().includes('period=week')),
      chief.page.locator('[data-insights-form=report] [type=submit]').click(),
    ]);
    check('weekly selector requests a calendar week report', (await weekResponse.json()).period === 'week');
    await chief.page.locator('[data-manager-page=management-reports]').waitFor();
    await chief.page.screenshot({path:path.join(output,'reports-desktop.png'),fullPage:true});
    await managerPage(admin.page,'management-reports');
    check('minister report includes both departments', await admin.page.locator('.insights-teams > details').count() === 2);
    await admin.page.locator('[data-insights-form=report] [name=dept]').selectOption('乙班组');
    const [teamResponse] = await Promise.all([
      admin.page.waitForResponse(r => r.url().includes('/insights/report?') && r.url().includes('dept=')),
      admin.page.locator('[data-insights-form=report] [type=submit]').click(),
    ]);
    check('minister can select one team without changing report denominators', (await teamResponse.json()).metrics.final_count === 1);
    await managerPage(mobile.page,'management-reports');
    check('mobile report has no page overflow', await mobile.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1));
    await mobile.page.screenshot({path:path.join(output,'reports-mobile.png'),fullPage:true});
    await managerPage(mobile.page,'management-dimensions');
    check('sparse data is explicitly marked insufficient', await mobile.page.locator('[data-dimension-status=insufficient]').count() > 0);
    const standard = (await (await employee.context.request.get('/api/steel/standards')).json()).standards[0];
    for (const [i,thickness] of [5.8,5.86,5.91,5.96,6.0].entries()) {
      const created = await employee.context.request.post('/api/steel/records',{data:{standard_id:standard.id}});
      assert.equal(created.status(),201);
      const record = (await created.json()).record;
      const day = new Date(Date.now()+8*60*60*1000);
      day.setUTCDate(day.getUTCDate()-4+i);
      Object.assign(record.data,{document_no:'DIMENSION-'+i,manufacturer:'趋势验收厂',grade:'Q355B',
        inspection_date:day.toISOString().slice(0,10),supply_quantity:20,inspected_quantity:5,
        qualified_quantity:5,rejected_quantity:0,inspection_mode:'sampling'});
      for (const key of ['cert_provided','cert_performance','identity_match','cert_seal','surface','shape','coil'])
        record.data.values[key] = {value:'合格',source:'manual',confirmed:true};
      record.data.values.thickness = {value:[thickness],source:'manual',confirmed:true};
      record.data.values.width = {value:[1811],source:'manual',confirmed:true};
      const signed = await employee.context.request.post('/api/steel/records/'+record.id+'/finalize',{data:{version:record.version,data:record.data}});
      assert.equal(signed.status(),200,await signed.text());
    }
    await managerPage(chief.page,'management-dimensions');
    const alert = chief.page.locator('[data-dimension-status=approaching]');
    await alert.waitFor();
    check('dimension alert shows thickness, direction and supporting chart', (await alert.innerText()).includes('厚度') && (await alert.innerText()).includes('持续接近上限') && await alert.locator('svg').count() === 1);
    await alert.locator('summary').click();
    check('dimension alert exposes five original records', await alert.locator('.insights-source').count() === 5);
    await chief.page.screenshot({path:path.join(output,'dimensions-desktop.png'),fullPage:true});
    await managerPage(mobile.page,'management-reports');
    await managerPage(mobile.page,'management-dimensions');
    await mobile.page.locator('[data-dimension-status=approaching] summary').click();
    check('mobile dimensions and expanded evidence have no page overflow', await mobile.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1));
    await mobile.page.screenshot({path:path.join(output,'dimensions-mobile.png'),fullPage:true});
    check('director has no API configuration navigation', await chief.page.locator('.manager-sidebar a[href="#/management-api"]').count() === 0);
    await managerPage(admin.page,'management-api');
    let config = admin.page.locator('[data-ai-config]');
    check('API configuration defaults to DeepSeek', await config.locator('[name=base_url]').inputValue() === 'https://api.deepseek.com');
    await config.locator('[name=api_key]').fill('isolated-fake-browser-api-key');
    await config.locator('[name=enabled]').check();
    const [savedConfig] = await Promise.all([
      admin.page.waitForResponse(r => new URL(r.url()).pathname === '/api/admin/ai-config' && r.request().method() === 'PUT'),
      config.locator('[type=submit]').click(),
    ]);
    check('API key saves without being returned to browser', savedConfig.status() === 200 && !JSON.stringify(await savedConfig.json()).includes('isolated-fake-browser-api-key'));
    await admin.page.locator('[data-ai-test]:enabled').waitFor();
    check('saved key input is blank', await admin.page.locator('[data-ai-config] [name=api_key]').inputValue() === '');
    await admin.page.locator('[data-ai-test]').click();
    await admin.page.getByText('连接成功，模型可返回结构化结果。',{exact:false}).waitFor();
    check('connection test gives a concrete result', (await admin.page.locator('[data-ai-config-status]').innerText()).includes('连接成功'));
    await admin.page.screenshot({path:path.join(output,'api-config-desktop.png'),fullPage:true});
    await managerPage(chief.page,'management-reports');
    check('report function is named AI interpretation', await chief.page.locator('h1').innerText() === 'AI解读');
    const [generated] = await Promise.all([
      chief.page.waitForResponse(r => new URL(r.url()).pathname === '/api/management/insights/generate'),
      chief.page.locator('[data-insights-form=report] [type=submit]').click(),
    ]);
    const aiReport = await generated.json();
    check('explicit generation calls provider and keeps server metrics', aiReport.mode === 'ai_api' && aiReport.ai.status === 'generated' && aiReport.metrics.final_count > 0);
    await chief.page.locator('[data-ai-status=cached]').waitFor();
    check('AI result has verified original-record links', await chief.page.locator('.insights-ai .insights-source').count() > 0);
    const callsBefore = (await (await fetch(base+'/_management_ui_fixture')).json()).ai_calls;
    const [readCache] = await Promise.all([
      chief.page.waitForResponse(r => new URL(r.url()).pathname === '/api/management/insights/report'),
      chief.page.locator('[data-manager-refresh]').click(),
    ]);
    check('ordinary refresh reads cached AI without provider call', (await readCache.json()).ai.status === 'cached' && (await (await fetch(base+'/_management_ui_fixture')).json()).ai_calls === callsBefore);
    await chief.page.locator('[data-ai-status=cached]').waitFor();
    await chief.page.screenshot({path:path.join(output,'ai-report-desktop.png'),fullPage:true});
    const ministerMobile = await account('ADMIN',393);
    await managerPage(ministerMobile.page,'management-api');
    check('mobile API config masks saved key', await ministerMobile.page.locator('[data-ai-config] [name=api_key]').inputValue() === '');
    await ministerMobile.page.screenshot({path:path.join(output,'api-config-mobile.png'),fullPage:true});
    await managerPage(mobile.page,'management-reports');
    await mobile.page.locator('[data-insights-form=report] [name=period]').selectOption('week');
    await mobile.page.locator('[data-insights-form=report] [type=submit]').click();
    await mobile.page.locator('[data-ai-status=cached]').waitFor();
    check('mobile AI interpretation has no overflow', await mobile.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1));
    await mobile.page.screenshot({path:path.join(output,'ai-report-mobile.png'),fullPage:true});
    await managerPage(admin.page,'management-api');
    await admin.page.locator('[data-ai-config] [name=model]').fill('simulate-error');
    await admin.page.locator('[data-ai-config] [type=submit]').click();
    await admin.page.locator('[data-ai-config] [name=model][value=simulate-error]').waitFor();
    await managerPage(chief.page,'management-reports');
    await chief.page.locator('[data-insights-form=report] [type=submit]').click();
    await chief.page.locator('[data-ai-status=fallback]').waitFor();
    check('API failure is visible with local statistics preserved', (await chief.page.locator('.insights-ai').innerText()).includes('模拟 API 超时') && await chief.page.locator('.insights-summary').count() > 0);
    await admin.page.locator('[data-ai-remove]').click();
    await admin.page.locator('[data-ai-config] [name=api_key][placeholder="填写平台提供的 API Key"]').waitFor();
    check('key can be removed and AI disabled from configuration', !(await (await admin.context.request.get('/api/admin/ai-config')).json()).enabled);
    check('no uncaught browser errors', errors.length === 0);
    console.log('PASS: '+checks+' management browser checks, temporary database.');
  } finally {
    for (const context of contexts) await context.close();
    await browser.close();
    fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({checks,failures,errors},null,2));
  }
}
main().catch(error => {console.error(error);process.exitCode=1;});

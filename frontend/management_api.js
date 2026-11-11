'use strict';

VIEWS['management-api'] = async function() {
  if (!S.me) return VIEWS.login();
  if (S.me.role !== 'admin') return {html:'<div class="wrap"><section class="card"><h2>API配置仅向部长开放</h2><a class="btn" href="#/management-reports">返回AI解读</a></section></div>'};
  managementResetForUser();
  const owner = S.me.id;
  try {
    const config = await api('/api/admin/ai-config');
    if (!S.me || S.me.id !== owner) return {html:''};
    const body = '<section class="manager-panel insights-api"><div class="manager-panel-head"><h2>AI API配置</h2><span class="manager-state ' + (config.enabled && config.configured ? 'good' : 'muted') + '">' + (config.enabled && config.configured ? '已启用' : '未启用') + '</span></div>' +
      '<form data-ai-config data-version="' + config.version + '"><p>默认接入 DeepSeek，也支持兼容 Chat Completions 和 JSON 输出的 HTTPS 服务。</p>' +
      '<div class="insights-api-fields"><label class="manager-field"><span>API基础地址</span><input name="base_url" type="url" required maxlength="240" value="' + esc(config.base_url) + '" placeholder="https://api.deepseek.com"></label>' +
      '<label class="manager-field"><span>模型名称</span><input name="model" required maxlength="100" value="' + esc(config.model) + '" placeholder="deepseek-flash"></label>' +
      '<label class="manager-field"><span>API Key</span><input name="api_key" type="password" maxlength="400" autocomplete="new-password" spellcheck="false" placeholder="' + (config.key_saved ? '已保存，留空保留原密钥' : '填写平台提供的 API Key') + '"><small>' + (config.key_saved ? '密钥已加密保存在服务器，页面不回显。' : '密钥只用于服务端调用。') + '</small></label>' +
      '<label class="manager-field"><span>请求超时（秒）</span><input name="timeout" type="number" min="5" max="45" required value="' + config.timeout + '"></label></div>' +
      '<label class="manager-check insights-api-enabled"><input name="enabled" type="checkbox"' + (config.enabled ? ' checked' : '') + '> 启用AI解读</label>' +
      '<p class="manager-panel-note">生成时会将汇总指标、班组代号及匿名原单编号发送给所配置的平台，不发送员工姓名、实际单据号或录音。点击生成和测试连接可能产生平台费用；自动刷新不会调用模型。</p>' +
      '<p class="manager-panel-note">先保存配置，再测试连接。测试仅发送一条不含业务数据的请求。</p>' +
      (config.problem ? '<p class="manager-error">' + esc(config.problem) + '</p>' : '') +
      '<p role="status" data-ai-config-status></p><div class="insights-api-actions"><button type="submit" class="btn">保存配置</button><button type="button" class="btn ghost" data-ai-test' + (!config.configured ? ' disabled' : '') + '>测试连接</button>' +
      (config.key_saved ? '<button type="button" class="btn ghost" data-ai-remove>删除密钥并停用</button>' : '') + '<a href="#/management-reports" class="btn ghost">返回AI解读</a></div></form></section>';
    return {html:managementShell('management-api','API配置','由部长统一配置，室主任按班组权限使用AI解读',body,{noFilters:true,configuration:true,scopeLabel:'服务器 API 配置'})};
  } catch (error) {
    return {html:managementShell('management-api','API配置','配置暂不可用',managementError(error.message),{noFilters:true,configuration:true})};
  }
};

async function aiConfigAction(form, action) {
  if (form.dataset.busy) return;
  form.dataset.busy = '1';
  const owner = S.me.id;
  const buttons = Array.from(form.querySelectorAll('button'));
  const wasDisabled = buttons.map((b) => b.disabled);
  buttons.forEach((b) => { b.disabled = true; });
  const status = form.querySelector('[data-ai-config-status]');
  status.textContent = action === 'test' ? '正在测试连接…' : '正在保存…';
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(),65000);
  try {
    if (action === 'test') {
      const result = await api('/api/admin/ai-config/test',{method:'POST',body:{},signal:controller.signal});
      if (form.isConnected && S.me && S.me.id === owner) status.textContent = result.message + ' 模型：' + result.model;
    } else {
      const fields = form.elements;
      await api('/api/admin/ai-config',{method:'PUT',body:{version:Number(form.dataset.version),base_url:fields.base_url.value.trim(),model:fields.model.value.trim(),api_key:fields.api_key.value.trim(),timeout:Number(fields.timeout.value),enabled:fields.enabled.checked,remove_key:action === 'remove'},signal:controller.signal});
      fields.api_key.value = '';
      if (S.me && S.me.id === owner && parseRoute().name === 'management-api') { toast('API配置已保存'); await render(); }
    }
  } catch (error) {
    if (form.isConnected && S.me && S.me.id === owner) status.textContent = error.name === 'AbortError' ? '请求超时，请稍后重试。' : error.message;
  } finally {
    clearTimeout(timer);
    delete form.dataset.busy;
    if (form.isConnected) buttons.forEach((b,i) => { b.disabled = wasDisabled[i]; });
  }
}

document.addEventListener('submit', (event) => {
  if (!event.target.matches('[data-ai-config]')) return;
  event.preventDefault();
  aiConfigAction(event.target,'save');
});
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-ai-test],[data-ai-remove]');
  if (!button || button.disabled) return;
  aiConfigAction(button.closest('form'), button.hasAttribute('data-ai-test') ? 'test' : 'remove');
});

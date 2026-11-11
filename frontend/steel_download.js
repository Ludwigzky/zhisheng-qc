/* Keep the authenticated request inside the page even when an Android browser
   delegates a normal link to a download manager with a different cookie jar. */
'use strict';
const steelWorkbookMime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

function steelDownloadName(header, fallback) {
  let name = fallback;
  const match = /filename\*=UTF-8''([^;]+)/i.exec(header || '');
  if (match) {
    try { name = decodeURIComponent(match[1]); } catch (_) { /* Use the record label. */ }
  }
  return name.replace(/[<>:"/\\|?*\x00-\x1f\x7f]/g, '_').replace(/\.xlsx$/i, '').slice(0, 160) + '.xlsx';
}

async function steelReadWorkbook(url) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(url, {credentials:'same-origin', cache:'no-store', signal:controller.signal});
    if (response.status === 401) throw new Error('登录已过期，请在当前浏览器重新登录后下载。');
    if (!response.ok) {
      const reason = await response.json().catch(() => ({}));
      throw new Error(reason.error || '下载失败，请重新打开记录后再试。');
    }
    const bytes = await response.arrayBuffer();
    const view = new DataView(bytes);
    // Our XLSX writer produces ZIP files with no archive comment.
    if (view.byteLength < 22 || view.getUint32(0, true) !== 0x04034b50 ||
        view.getUint32(view.byteLength - 22, true) !== 0x06054b50) {
      throw new Error('收到的文件不完整，请重新下载。');
    }
    return {blob:new Blob([bytes], {type:steelWorkbookMime}), header:response.headers.get('Content-Disposition')};
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('下载超时，请确认手机仍连接电脑所在的 Wi-Fi，然后重试。');
    throw error;
  } finally { clearTimeout(timeout); }
}

async function steelStartFileDownload(box, link) {
  const status = box.querySelector('.steel-file-feedback');
  const options = box.querySelector('.steel-file-options');
  const share = box.querySelector('[data-steel-file-share]');
  link.setAttribute('aria-busy', 'true');
  status.textContent = '正在获取本单 Excel…';
  const original = link.href;
  try {
    const {blob, header} = await steelReadWorkbook(original);
    if (!box.isConnected) return;
    const filename = steelDownloadName(header, '进货检验记录_' + (box.dataset.fileDoc || box.dataset.steelFile));
    const localUrl = URL.createObjectURL(blob);
    link.href = localUrl;
    link.download = filename;
    if (typeof File === 'function' && navigator.share && navigator.canShare) {
      const file = new File([blob], filename, {type:steelWorkbookMime});
      if (navigator.canShare({files:[file]})) {
        box.steelShareFile = file;
        share.hidden = false;
      }
    }
    // A real link stays usable for a second trusted tap if the browser
    // blocks the synthetic one. Keep its bytes alive throughout handoff.
    options.hidden = false;
    status.textContent = '文件已就绪。如果下载列表没有出现文件，可再次点下载或使用下方按钮。';
    link.click();
    setTimeout(() => {
      URL.revokeObjectURL(localUrl);
      if (link.href === localUrl) {
        link.href = original;
        link.removeAttribute('download');
        box.steelShareFile = null;
        share.hidden = true;
      }
    }, 5 * 60 * 1000);
  } catch (error) {
    options.hidden = false;
    status.textContent = error.message || '未能读取 Excel，请重试。';
  } finally {
    link.removeAttribute('aria-busy');
  }
}

async function steelPrepareSystemDownload(box, button) {
  const options = box.querySelector('.steel-file-options');
  const status = box.querySelector('.steel-file-feedback');
  const id = box.dataset.steelFile;
  button.disabled = true;
  try {
    const data = await api('/api/steel/records/'+id+'/download-link', {method:'POST', body:{}});
    if (!box.isConnected) return;
    const url = new URL(appURL(data.url), location.href);
    if (url.origin !== location.origin || url.pathname !== appURL('/api/steel/records/'+id+'/download/steel-'+id+'.xlsx')) {
      throw new Error('下载地址无效，请重新打开记录。');
    }
    const link = document.createElement('a');
    link.className = 'btn';
    link.setAttribute('data-steel-file-ticket','');
    link.referrerPolicy = 'no-referrer';
    link.href = url.href;
    link.download = data.filename;
    link.textContent = '保存到手机';
    options.appendChild(link);
    button.hidden = true;
    status.textContent = '点“保存到手机”使用系统下载器，链接 5 分钟内有效。';
    setTimeout(() => {
      link.remove();
      button.hidden = false;
      status.textContent = '系统下载链接已到期，可重新获取。';
    }, data.expires_in * 1000);
  } catch (error) {
    status.textContent = error.status === 404 ? '请在电脑端重新启动 Demo，再使用系统下载。' :
      error.status === 401 ? '请先重新登录，再使用系统下载。' : error.message;
  } finally { button.disabled = false; }
}

document.addEventListener('click', async (event) => {
  const box = event.target.closest('[data-steel-file]');
  if (!box) return;
  const link = event.target.closest('[data-steel-download]');
  if (link) {
    if (link.protocol === 'blob:') return; // let the browser save the already-fetched file
    event.preventDefault();
    if (!link.hasAttribute('aria-busy')) await steelStartFileDownload(box, link);
  } else if (event.target.closest('[data-steel-file-share]') && box.steelShareFile) {
    try { await navigator.share({files:[box.steelShareFile]}); }
    catch (error) {
      if (error.name !== 'AbortError') box.querySelector('.steel-file-feedback').textContent = '无法分享文件，请点“改用系统下载”。';
    }
  } else {
    const button = event.target.closest('[data-steel-file-system]');
    if (button && !button.disabled) await steelPrepareSystemDownload(box, button);
  }
});

'use strict';
const $=id=>document.getElementById(id);
chrome.storage.local.get(['url','token','enabled','queue','error','lastDelivery','overflow']).then(v=>{if(v.url)$('url').value=v.url;if(v.token)$('token').value=v.token;$('enabled').checked=v.enabled!==false;$('status').textContent=`待发送 ${(v.queue||[]).length} 条。${v.error||''}${v.overflow?' 先前离线队列曾满，部分消息可能未保存。':''}\n最近发送：${v.lastDelivery||'尚无'}`;});
async function values(){const url=new URL($('url').value);if(url.protocol!=='http:'||!['127.0.0.1','localhost'].includes(url.hostname))throw Error('仅允许本机 HTTP 地址');return {url:url.origin,token:$('token').value.trim(),enabled:$('enabled').checked};}
$('save').onclick=async()=>{try{await chrome.storage.local.set(await values());$('status').textContent='已保存。';}catch(e){$('status').textContent=e.message;}};
$('test').onclick=async()=>{try{const v=await values();const r=await fetch(v.url+'/api/status',{headers:{Authorization:'Bearer '+v.token}});if(!r.ok)throw Error('连接返回 '+r.status);$('status').textContent='本机服务连接成功。';}catch(e){$('status').textContent=e.message;}};

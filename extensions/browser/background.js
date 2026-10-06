'use strict';
// A serialized durable queue prevents concurrent tabs from overwriting one another.
let chain=Promise.resolve();
const HOSTS=new Set(['chatgpt.com','claude.ai','gemini.google.com']);
function serialize(fn){chain=chain.then(fn,fn);return chain;}
async function flush(){
 const state=await chrome.storage.local.get(['url','token','enabled','queue']);
 if(state.enabled===false||!state.url||!state.token)return;
 let url;try{url=new URL(state.url);}catch{return;}
 if(url.protocol!=='http:'||!['127.0.0.1','localhost'].includes(url.hostname))return;
 const queue=state.queue||[];if(!queue.length)return;
 // The server accepts at most 2 MiB: character counts are insufficient for CJK text.
 const batch=[];let size=32;const encoder=new TextEncoder();
 for(const event of queue){const bytes=encoder.encode(JSON.stringify(event)).length+1;if(batch.length>=100||size+bytes>1500000)break;batch.push(event);size+=bytes;}
 if(!batch.length){await chrome.storage.local.set({error:'首条消息超过发送上限，请在扩展设置中检查。'});return;}
 try{
  const response=await fetch(url.origin+'/api/events',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+state.token},body:JSON.stringify({events:batch}),signal:AbortSignal.timeout(8000)});
  if(response.ok)await chrome.storage.local.set({queue:queue.slice(batch.length),lastDelivery:new Date().toISOString(),error:''});
  else await chrome.storage.local.set({error:'本机服务返回 '+response.status});
 }catch(e){await chrome.storage.local.set({error:'本机服务暂不可达；消息已排队，稍后重试。'});}
}
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if(message.type!=='events')return;
 let hostname;try{hostname=new URL(sender.url).hostname;}catch{return;}
 if(!HOSTS.has(hostname)||!Array.isArray(message.events))return;
 serialize(async()=>{
  const state=await chrome.storage.local.get(['queue','enabled']);
  if(state.enabled===false)return;
  const keyed=new Map((state.queue||[]).map(e=>[e.source+'/'+e.id,e]));
  for(const e of message.events.slice(0,100)){if(typeof e.text==='string'&&e.text.length<=24000)keyed.set(e.source+'/'+e.id,e);}
  const values=[...keyed.values()];
  // Respect the browser's storage quota, counting UTF-8 bytes, not characters.
  const kept=[];let bytes=0;const encoder=new TextEncoder();
  for(const value of values){bytes+=encoder.encode(JSON.stringify(value)).length;if(kept.length>=2000||bytes>7*1024*1024)break;kept.push(value);}
  const overflow=kept.length<values.length;
  await chrome.storage.local.set({queue:kept,...(overflow?{overflow:true,error:'离线队列已满，部分新消息未保存。请恢复本机服务。'}:{})});
  await flush();
 }).then(()=>respond({queued:true}),e=>respond({error:String(e)}));
 return true;
});
chrome.alarms.create('deliver',{periodInMinutes:1});
chrome.alarms.onAlarm.addListener(a=>{if(a.name==='deliver')serialize(flush);});
chrome.runtime.onStartup.addListener(()=>serialize(flush));

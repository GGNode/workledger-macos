'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function harness(events,status=200){
 const state={url:'http://127.0.0.1:8765',token:'fixture-only',queue:events,enabled:true,overflow:true};const sent=[];
 const noop={addListener:()=>{}};
 const context=vm.createContext({URL,TextEncoder,AbortSignal,Promise,Date,Set,Map,
   fetch:async(url,options)=>{sent.push(options.body);return {ok:status===200,status};},
   chrome:{storage:{local:{get:async()=>({...state}),set:async(value)=>Object.assign(state,value)}},
     runtime:{onMessage:noop,onStartup:noop},alarms:{create:()=>{},onAlarm:noop}}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../extensions/browser/background.js'),'utf8'),context);
 return {state,sent,flush:()=>context.flush()};
}
test('queue batches Chinese text by UTF-8 bytes below HTTP limit',async()=>{
 const entries=Array.from({length:100},(_,i)=>({id:String(i),source:'chatgpt',text:'汉'.repeat(24000)}));
 const h=harness(entries);await h.flush();const batch=JSON.parse(h.sent[0]).events;
 assert(Buffer.byteLength(h.sent[0])<1500000);assert(batch.length>0&&batch.length<100);
 assert.equal(h.state.queue.length,100-batch.length);assert.equal(h.state.queue[0].id,String(batch.length));
});
test('failed delivery preserves original queue and original message times',async()=>{
 const entries=[{id:'1',occurred_at:'2026-10-05T12:00:00Z',text:'stored'}];const h=harness(entries,503);
 await h.flush();assert.deepEqual(h.state.queue,entries);assert(h.state.error.includes('503'));
});
test('successful retry does not hide earlier overflow flag',async()=>{
 const h=harness([{id:'1',text:'x'}]);await h.flush();assert.equal(h.state.queue.length,0);assert.equal(h.state.overflow,true);
});

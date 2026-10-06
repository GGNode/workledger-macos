'use strict';
(()=>{
 const tracker=new WorkLedgerTracker();
 const host=location.hostname;
 const source=host==='chatgpt.com'?'chatgpt':host==='claude.ai'?'claude-web':'gemini-web';
 const stable=new WeakMap();let n=0;
 function rows(){
  let elements=[];
  if(source==='chatgpt')elements=[...document.querySelectorAll('[data-message-author-role="user"],[data-message-author-role="assistant"]')];
  else if(source==='claude-web')elements=[...document.querySelectorAll('[data-testid="user-message"], [data-is-streaming], .font-claude-response')].filter((el,i,all)=>!all.some((other,j)=>i!==j&&other.contains(el)));
  else elements=[...document.querySelectorAll('user-query,model-response')];
  return elements.map(el=>{
   let role=el.getAttribute('data-message-author-role');
   if(!role)role=(el.matches('[data-testid="user-message"],user-query'))?'user':'assistant';
   let id=el.getAttribute('data-message-id')||el.closest('[data-message-id]')?.getAttribute('data-message-id');
   if(!id){if(!stable.has(el))stable.set(el,'dom-'+crypto.randomUUID());id=stable.get(el);}
   return {id,role,text:(el.innerText||'').slice(0,24000)};
  });
 }
 function promptText(){
  const el=document.querySelector('#prompt-textarea,[contenteditable="true"][role="textbox"],.ProseMirror[contenteditable="true"],textarea');
  return el?(el.value??el.innerText??''):'';
 }
 let lastSubmit='',lastSubmitAt=0;
 function submit(e){
  if(!e.isTrusted)return;
  const text=promptText();if(!text.trim())return;
  if(text===lastSubmit&&Date.now()-lastSubmitAt<1500)return;
  lastSubmit=text;lastSubmitAt=Date.now();tracker.submit(text,new Date().toISOString(),true);
 }
 document.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.target.closest('textarea,[contenteditable="true"]'))submit(e);
 },true);
 document.addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;
  const label=(b.getAttribute('aria-label')||b.getAttribute('data-testid')||'').toLowerCase();
  if(/send|发送|提交/.test(label))submit(e);
 },true);
 let timer;
 function scan(){
  let scope=location.pathname.replace(/\/$/,'')||'/';
  if(source==='chatgpt'){const match=scope.match(/\/c\/([^/]+)/);if(!match)return;scope=match[1];}
  else if(source==='claude-web'){const match=scope.match(/\/chat\/([^/]+)/);if(!match)return;scope=match[1];}
  else if(scope==='/app'||scope==='/')return;
  const out=tracker.scan(scope,rows(),new Date().toISOString());
  if(out.length)chrome.runtime.sendMessage({type:'events',events:out.map(r=>({
    schema:'workledger.event.v1',source,id:scope+'/'+r.id,kind:r.role==='user'?'user_message':'agent_message',
    session_id:scope,session_title:document.title,actor:r.role==='assistant'?'agent':'unknown',
    occurred_at:r.occurred_at,chronology:r.chronology,text:r.text,evidence:'trusted_submit_then_new_dom',
    metadata:{role:r.role,url:location.origin+scope,creation_time:'live observation, not a server timestamp',physical_typing_verified:false}
  }))}).catch(()=>{});
 }
 new MutationObserver(()=>{clearTimeout(timer);timer=setTimeout(scan,900);}).observe(document.documentElement,{childList:true,subtree:true,characterData:true});
 scan();setInterval(scan,5000);
})();

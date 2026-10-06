'use strict';
// Optional DOM regression: NODE_PATH=<jsdom install>/node_modules node --test tests/content-dom.test.js
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{}
for(const modern of [false,true])test(`content script ${modern?'current':'legacy'} ChatGPT DOM excludes history and captures one new turn`,{skip:!JSDOM},()=>{
 const dom=new JSDOM('<div id="messages"></div><div contenteditable="true" role="textbox">Today question</div><button aria-label="发送">Send</button>',{url:'https://chatgpt.com/c/test-conversation',runScripts:'outside-only'});
 const w=dom.window, sent=[], listeners={};let scan;
 Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});
 function row(id,role,text){
  if(!modern)return `<div data-message-author-role="${role}" data-message-id="${id}">${text}</div>`;
  const body=role==='user'?'data-user-message-bubble="true"':'data-markdown-text-style="assistant-message"';
  // Duplicate assistant IDs occur on the observed production search-unit wrapper.
  return `<div data-chatgpt-search-unit-key="turn:${role}" data-chatgpt-search-message-ids="${id} ${id}"><h4>${role} says:</h4><div ${body}>${text}</div><button>Copy</button></div>`;
 }
 w.document.querySelector('#messages').innerHTML=row('old','assistant','Yesterday answer');
 w.chrome={runtime:{sendMessage(message){sent.push(...message.events);return Promise.resolve();}}};
 w.MutationObserver=class{observe(){}};w.setInterval=(fn)=>{scan=fn;};
 w.document.addEventListener=(type,fn)=>{listeners[type]=fn;};
 const root=path.resolve(__dirname,'../extensions/browser');
 w.eval(fs.readFileSync(path.join(root,'tracker.js'),'utf8'));
 w.eval(fs.readFileSync(path.join(root,'content.js'),'utf8'));
 assert.equal(sent.length,0);
 w.document.querySelector('#messages').insertAdjacentHTML('afterbegin',row('older','user','Old prompt'));
 scan();assert.equal(sent.length,0);
 // Synthetic trusted-submit boundary, not a claim of physical human typing.
 listeners.click({isTrusted:true,target:w.document.querySelector('button[aria-label="发送"]')});
 w.document.querySelector('#messages').insertAdjacentHTML('beforeend',row('new-user','user','Today question')+row('new-agent','assistant','Today answer'));
 scan();assert.equal(sent.length,2);
 assert.deepEqual(sent.map(e=>e.id),['test-conversation/new-user','test-conversation/new-agent']);
 assert.equal(sent[0].actor,'unknown');assert.equal(sent[1].actor,'agent');
 assert.equal(sent[1].text,'Today answer');
 assert.equal(sent[1].metadata.url,'https://chatgpt.com/c/test-conversation');
 scan();assert.equal(sent.length,2);
 dom.window.close();
});

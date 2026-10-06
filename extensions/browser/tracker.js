/* Pure state machine. DOM presence != message creation. Tested with node --test. */
'use strict';
class WorkLedgerTracker {
  constructor(){this.scope=null;this.seen=new Map();this.pending=null;}
  submit(text, at, trusted){
    text=String(text||'').trim();
    if(trusted && text)this.pending={text:this.normalize(text),at,matched:null,assistant:null};
  }
  normalize(s){return String(s||'').replace(/\s+/g,' ').trim();}
  scan(scope, rows, at){
    if(this.pending && Date.parse(at)-Date.parse(this.pending.at)>30*60*1000)this.pending=null;
    if(scope!==this.scope){this.scope=scope;this.seen=new Map();if(!this.pending){for(const r of rows)this.seen.set(r.id,{text:r.text,captured:false});return [];}}
    const output=[];let promptPosition=-1;
    if(this.pending?.matched)promptPosition=rows.findIndex(r=>r.id===this.pending.matched);
    for(let i=0;i<rows.length;i++){
      const r=rows[i];if(!r.id||!r.text?.trim())continue;
      const previous=this.seen.get(r.id);
      let creation=previous?.captured?previous.at:null;
      if(!creation && !previous && this.pending && r.role==='user' && this.normalize(r.text)===this.pending.text && !this.pending.matched){
        this.pending.matched=r.id;promptPosition=i;creation=this.pending.at;
      } else if(!creation && !previous && this.pending?.matched && i>promptPosition && promptPosition>=0 && r.role==='assistant' && (!this.pending.assistant||this.pending.assistant===r.id)){
        this.pending.assistant=r.id;creation=at;
      }
      const changed = !!previous && previous.text!==r.text;
      if(creation && (!previous || (r.role==='assistant' && changed)))output.push({...r,occurred_at:creation,chronology:'live_observed'});
      this.seen.set(r.id,{text:r.text,captured:!!creation,at:creation});
    }
    return output;
  }
}
if(typeof module!=='undefined')module.exports={WorkLedgerTracker};
else globalThis.WorkLedgerTracker=WorkLedgerTracker;

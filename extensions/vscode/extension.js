'use strict';
const vscode=require('vscode');const fs=require('fs');const fsp=fs.promises;const path=require('path');const os=require('os');const crypto=require('crypto');
const baselines=new Map();const changed=new Set();
function sha(s){return crypto.createHash('sha256').update(s).digest('hex');}
function home(){const c=vscode.workspace.getConfiguration('workledger');return c.get('dataHome')||path.join(os.homedir(),'Library','Application Support','WorkLedger');}
function permitted(document){
 if(!vscode.workspace.getConfiguration('workledger').get('enabled')||document.uri.scheme!=='file')return false;
 try{const config=JSON.parse(fs.readFileSync(path.join(home(),'config.json'),'utf8'));if(config.capture_paused)return false;const file=fs.realpathSync(document.uri.fsPath);const name=path.basename(file);if(/^\.env|\.pem$|\.key$|^auth\.json$|^credentials/i.test(name))return false;
 return config.projects.some(p=>p.paths.some(r=>{const root=fs.realpathSync(r.replace(/^~(?=\/|$)/,os.homedir()));const rel=path.relative(root,file);return !!rel&&!rel.startsWith('..'+path.sep)&&!path.isAbsolute(rel)&&!rel.split(path.sep).some(x=>['node_modules','.git','.venv'].includes(x));}));}catch{return false;}
}
function activate(context){
 for(const doc of vscode.workspace.textDocuments)if(permitted(doc))baselines.set(doc.uri.toString(),doc.getText());
 context.subscriptions.push(vscode.workspace.onDidOpenTextDocument(doc=>{if(permitted(doc))baselines.set(doc.uri.toString(),doc.getText());}));
 context.subscriptions.push(vscode.workspace.onDidChangeTextDocument(event=>{if(event.contentChanges.length&&permitted(event.document))changed.add(event.document.uri.toString());}));
 context.subscriptions.push(vscode.workspace.onDidSaveTextDocument(async doc=>{
  if(!permitted(doc))return;
  const key=doc.uri.toString(),after=doc.getText(),before=baselines.get(key);baselines.set(key,after);
  if(before===undefined||before===after||!changed.has(key))return;changed.delete(key);
  if(Buffer.byteLength(after)>2*1024*1024||Buffer.byteLength(before)>2*1024*1024)return;
  const stamp=new Date().toISOString();const id=sha(key+stamp+sha(after));
  const event={schema:'workledger.event.v1',source:'vscode',id,kind:'document_change',actor:'unknown',occurred_at:stamp,chronology:'live_observed',artifact:doc.uri.fsPath,text:path.basename(doc.uri.fsPath)+'：编辑器保存后的文本变化',evidence:'vscode_document_change_and_save',metadata:{before_hash:sha(before),after_hash:sha(after),physical_typing_verified:false,changes:[{section:'编辑器文本',diff:'保存前：\n'+before.slice(0,1600)+'\n\n保存后：\n'+after.slice(0,1600)}]}};
  try{await fsp.mkdir(path.join(home(),'inbox'),{recursive:true,mode:0o700});const dest=path.join(home(),'inbox','vscode-'+id+'.jsonl');await fsp.writeFile(dest+'.tmp',JSON.stringify(event)+'\n',{mode:0o600});await fsp.rename(dest+'.tmp',dest);}catch(e){console.error('WorkLedger spool failed',e.message);}
 }));
 context.subscriptions.push(vscode.workspace.onDidCloseTextDocument(doc=>{baselines.delete(doc.uri.toString());changed.delete(doc.uri.toString());}));
 context.subscriptions.push(vscode.commands.registerCommand('workledger.openPanel',async()=>{try{const cfg=JSON.parse(await fsp.readFile(path.join(home(),'config.json'),'utf8'));const token=(await fsp.readFile(path.join(home(),'token'),'utf8')).trim();vscode.env.openExternal(vscode.Uri.parse(`http://127.0.0.1:${cfg.port}/#token=${encodeURIComponent(token)}`));}catch{vscode.window.showErrorMessage('请先安装并启动 WorkLedger。');}}));
}
exports.activate=activate;exports.deactivate=()=>{};

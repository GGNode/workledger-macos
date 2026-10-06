'use strict';
const $ = id => document.getElementById(id);
let config = null;
let token = sessionStorage.getItem('workledger-token') || '';
const params = new URLSearchParams(location.hash.slice(1));
if (params.get('token')) {token=params.get('token'); sessionStorage.setItem('workledger-token',token); history.replaceState(null,'',location.pathname);}
function message(text, error=false) { const el=$('message'); el.textContent=text; el.className='status'+(error?' error':''); el.style.display='block'; }
async function api(path, data) {
  const response = await fetch('/api/'+path,{method:data?'POST':'GET',headers:{Authorization:'Bearer '+token,...(data?{'Content-Type':'application/json'}:{})},body:data?JSON.stringify(data):undefined});
  const value=await response.json();
  if(!response.ok) throw new Error(value.error || response.status);
  return value;
}
function node(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
function on(id, fn) { $(id).addEventListener('click',async()=>{const b=$(id);b.disabled=true;try{await fn();}catch(e){message(e.message,true);}finally{b.disabled=false;}}); }
async function save(data){await api('config',data);message('已保存，本机采集服务将读取新设置。');await load();}
function projectRow(value={name:'',paths:[]}) {
 const row=node('div',undefined,'source'); const name=document.createElement('input');name.type='text';name.placeholder='项目名称';name.value=value.name;name.className='project-name';
 const paths=document.createElement('textarea');paths.rows=2;paths.placeholder='项目文件夹绝对路径，每行一个';paths.value=value.paths.join('\n');paths.className='project-paths';
 const remove=node('button','移除此项目');remove.onclick=()=>row.remove();row.append(name,paths,remove);$('projects').append(row);
}
async function load(){
 const status=await api('status');config=status.config;
 $('pairing').hidden=true;$('workspace').hidden=false;$('connection').textContent=config.capture_paused?'采集已暂停':'本机服务已连接';
 $('event-count').textContent=status.events;$('session-count').textContent=status.sessions;$('issue-count').textContent=status.issues.length;
 $('last-capture').textContent='最近采集：'+(status.last_capture ? new Date(status.last_capture).toLocaleString(): '尚未运行');
 if(!$('day').value) $('day').value=new Intl.DateTimeFormat('en-CA',{timeZone:config.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 $('schedule-enabled').checked=config.schedule.enabled;$('schedule-time').value=config.schedule.time;$('weekdays').checked=config.schedule.weekdays_only;$('auto-open').checked=config.schedule.open;$('paused').checked=config.capture_paused;$('timezone').value=config.timezone;
 $('projects').replaceChildren();config.projects.forEach(projectRow);
 $('sources').replaceChildren();for(const [name,s] of Object.entries(config.sources)) {if(name==='activitywatch')continue;const row=node('div',undefined,'source');row.dataset.source=name;const label=node('label');const check=document.createElement('input');check.type='checkbox';check.checked=s.enabled;check.className='source-enabled';label.append(check,node('b',' '+name));const paths=node('textarea');paths.rows=2;paths.value=(s.paths||[]).join('\n');paths.className='source-paths';paths.placeholder='完整目录或 glob，每行一个';row.append(label,paths);$('sources').append(row);}
 $('llm-mode').value=config.llm.mode;$('llm-url').value=config.llm.url;$('llm-model').value=config.llm.model;$('llm-remote').checked=config.llm.allow_remote;
 $('diagnostics').replaceChildren();for(const s of status.doctor.sources) $('diagnostics').append(node('p',s.source+' · '+s.status+' · '+s.files_found+' 个文件'));
 if(!status.doctor.zstd_decoder)$('diagnostics').append(node('p','Zstd 解码器未安装：压缩 dsh 日志需要按交接文档安装可选依赖。'));
 for(const i of status.issues)$('diagnostics').append(node('p',i.source+'：'+i.detail));
 $('data-home').textContent=status.home;
 const reports=await api('reports');$('reports').replaceChildren();for(const day of reports.dates.slice(0,30)){const b=node('button',day);b.onclick=async()=>{try{await api('open',{date:day});}catch(e){message(e.message,true);}};$('reports').append(b);}
}
async function loadEvents(){
 const {events}=await api('events?date='+encodeURIComponent($('day').value));$('events').replaceChildren();
 const changes=events.filter(e=>e.kind==='document_change'||e.kind==='review');
 if(!changes.length)$('events').append(node('p','此日期没有待核实的文档变化。','muted'));
 for(const e of changes){const div=node('div',undefined,'event');const label=node('label');const check=document.createElement('input');check.type='checkbox';check.value=e.id;check.className='event-check';label.append(check,node('span',(e.actor==='human'?'本人已确认 · ':e.actor==='agent'?'已归为 Agent · ':'待确认 · ')+e.text));div.append(label,node('small',e.artifact+' · '+new Date(e.occurred_at).toLocaleString()));
  for(const d of e.metadata.changes||[]){const details=node('details');details.append(node('summary',d.section),node('pre',d.diff||''));div.append(details);}$('events').append(div);
 }
}
async function confirm(actor){const ids=[...document.querySelectorAll('.event-check:checked')].map(e=>e.value);if(!ids.length)throw new Error('请先选择需要确认的记录。');const reason=actor==='human'?'本人在控制面板确认这些修改由自己完成':actor==='agent'?'本人在控制面板确认这些修改由 Agent 完成':'本人撤回先前确认，归属恢复为待确认';await api('annotate',{ids,actor,reason});message('归属已记录。重新生成报告后生效。');await loadEvents();}
on('pair',async()=>{token=$('token').value.trim();sessionStorage.setItem('workledger-token',token);await load();});
on('generate',async()=>{message('正在采集并生成报告…');const r=await api('report',{date:$('day').value,open:true});message('报告已生成：'+r.path);await load();});
on('capture',async()=>{await api('collect',{});message('本轮采集完成。');await load();});
on('load-events',loadEvents);on('confirm-human',()=>confirm('human'));on('confirm-agent',()=>confirm('agent'));on('confirm-unknown',()=>confirm('unknown'));
on('save-note',async()=>{await api('note',{text:$('note').value});$('note').value='';message('已记录本人补充。');});
on('save-runtime',()=>save({timezone:$('timezone').value.trim(),capture_paused:$('paused').checked,schedule:{enabled:$('schedule-enabled').checked,time:$('schedule-time').value,weekdays_only:$('weekdays').checked,open:$('auto-open').checked}}));
on('add-project',()=>projectRow());on('save-projects',()=>save({projects:[...document.querySelectorAll('#projects .source')].map(r=>({name:r.querySelector('.project-name').value.trim(),paths:r.querySelector('.project-paths').value.split('\n').map(v=>v.trim()).filter(Boolean)}))}));
on('save-sources',()=>{const sources={};for(const r of document.querySelectorAll('[data-source]'))sources[r.dataset.source]={enabled:r.querySelector('.source-enabled').checked,paths:r.querySelector('.source-paths').value.split('\n').map(v=>v.trim()).filter(Boolean)};return save({sources});});
on('save-llm',()=>save({llm:{mode:$('llm-mode').value,url:$('llm-url').value.trim(),model:$('llm-model').value.trim(),allow_remote:$('llm-remote').checked}}));
load().catch(e=>{$('pairing').hidden=false;$('connection').textContent='等待配对';if(token)message(e.message,true);});

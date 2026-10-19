const $ = s => document.querySelector(s);
const statuses = ['TODO','In Progress','Blocked','In Review','Done','Cancelled'];
const tones = ['#97a39a','#629bd0','#d79d67','#a88fc7','#70a587','#a49191'];
let metricDays=30, showDeletedUsers=false;
let lastControlSignal=null;
const taskSortFields=['updated','code','title','status','priority','owner','context','batch','model'];
const taskGroupFields=['none','status','priority','owner','context','batch','category'];
function taskView(){
  let saved={};try{saved=JSON.parse(localStorage.getItem('scrum-task-view:'+project)||'{}')||{};}catch{}
  return {mode:saved.mode==='list'?'list':'board',sort:taskSortFields.includes(saved.sort)?saved.sort:'updated',direction:saved.direction==='asc'?'asc':'desc',group:taskGroupFields.includes(saved.group)?saved.group:'none'};
}
function setTaskView(change){localStorage.setItem('scrum-task-view:'+project,JSON.stringify({...taskView(),...change}));render();}
let users=[], actor=Number(localStorage.getItem('scrum-user'))||null;
const claims=JSON.parse(sessionStorage.getItem('scrum-folder-claims')||'{}');
function credentials(id){return {actor, ...(claims[`${project}:${id}`]?.actor===actor?{token:claims[`${project}:${id}`].token}:{})};}
function saveClaims(){sessionStorage.setItem('scrum-folder-claims',JSON.stringify(claims));}
async function loadUsers(){
  users=await api('users');
  const humans=users.filter(u=>u.kind==='human'&&u.active!==0);
  if(!humans.some(u=>u.id===actor))actor=null;
  $('#current-user').innerHTML='<option value="">Choose user</option>'+humans.map(u=>`<option value="${u.id}">${esc(u.name)}</option>`).join('');
  $('#current-user').value=actor||'';
}
async function moveTask(id,status,fields={}){
  if(status==='In Progress'){
    const claim=await api('task_update',{project,id,...credentials(id),...fields,status,claim:true});
    if(claim.claimed===false)throw Error('Files reserved by another task: '+claim.conflicts.map(c=>c.path).join(', '));
    claims[`${project}:${id}`]={actor,token:claim.token};saveClaims();
  }else await api('task_update',{project,id,...credentials(id),...fields,status});
}
let project = Number(localStorage.getItem('scrum-project')) || null, page = 'Board', state = {tasks:[],notes:[]}, projects = [], editing;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(action, args={}) {
  const r = await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args)});
  const data = await r.json(); if(!r.ok) throw Error(data.error); return data;
}
function error(e){ $('#error').textContent=e.message; }
async function loadProjects(){
  await loadUsers();
  projects=await api('projects');
  if(!projects.some(p=>p.id===project&&p.available!==false)) project=projects.find(p=>p.available!==false)?.id ?? null;
  $('#project').innerHTML=projects.length ? projects.map(p=>`<option value="${p.id}" ${p.available===false?'disabled':''}>${esc(p.name)}${p.available===false?' (unavailable)':''}</option>`).join('') : '<option>Create your first project</option>';
  $('#project').value=project; await refresh();
}
let refreshVersions={},refreshing=false,refreshSerial=0;
async function refresh(conditional=false){
  if(refreshing){if(conditional)return;refreshVersions={};}
  refreshing=true;
  const serial=++refreshSerial;
  const selected=project,selectedPage=page;
  try{
  $('#error').textContent='';
  if(project&&!['Projects','Settings','Chat'].includes(page)){
    const key=`${project}:${page}:${metricDays}`;
    const result=await api('web_refresh',{project,page,days:metricDays,...(conditional&&refreshVersions[key]?{version_seen:refreshVersions[key]}:{})});
    if(selected!==project||selectedPage!==page||serial!==refreshSerial)return;
    refreshVersions[key]=result.version;
    if(!result.changed)return;
    state={tasks:[],notes:[],...result.data};users=state.users||users;
    localStorage.setItem('scrum-project',project);render();return;
  }
  state=project&&page!=='Projects' ? await api('snapshot',{project}) : {tasks:[],notes:[]};
  users=state.users||users;
  if(project&&page==='File locks')state.fileLocks=await api('file_locks',{project});
  if(project&&page==='Agents')state.managedAgents=await api('agent_manage',{project,action:'list'});
  if(project&&page==='Chat')state.chat=await api('project_chat',{project});
  if(project&&page==='Metrics'){
    const [flow,coordination]=await Promise.all([api('project_metrics',{project,days:metricDays,include_series:true}),api('coordination_metrics',{project})]);
    state.metrics=flow;state.coordination=coordination;
  }
  localStorage.setItem('scrum-project',project ?? ''); render();
  }finally{if(serial===refreshSerial)refreshing=false;}
}
function render(){
  renderAgentControls();
  const name=projects.find(p=>p.id===project)?.name || 'Your workspace';
  $('#breadcrumb').textContent=`${name} / ${page}`;
  $('#heading').textContent=page==='Board' ? name : page;
  $('#subtitle').textContent=({Board:'A clear view of what’s next, what’s moving, and what’s done.',Notes:'Human notes and explicit task reminders. Only pending AI reminders enter agent context.','AI workflow':'A compact workflow that keeps your agent and board in sync.',Users:'Manage local user profiles while preserving authored history.',Agents:'Activity, real provider usage and project control signals.',Projects:'Open project folders, export portable data and manage this local panel.',Chat:'Project coordination for humans and agents. Refresh only when needed.',Metrics:'Understand flow, waiting time and completion trends.',Settings:'Project policies, workspace and completion requirements.','File locks':'Coordinate file edits before work starts. File reservations are isolated to this project checkout.'})[page];
  const view=taskView();
  $('#view-label').textContent=page==='Board'?(view.mode==='list'?'Task list':'Kanban board'):page==='AI workflow'?'Agent workspace':page==='Projects'?'Project directory':page==='Agents'?'Agent activity':page==='Notes'?'Project notes':`${page} notes`;
  $('#board-views').hidden=page!=='Board';$('#list-options').hidden=page!=='Board'||view.mode!=='list';
  document.querySelectorAll('[data-board-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.boardView===view.mode)));
  $('#task-group').value=view.group;$('#task-sort').value=view.sort;
  $('#task-sort-direction').textContent=view.direction==='asc'?'↑ Ascending':'↓ Descending';
  $('#add').textContent=page==='Board'?'＋ Create task':page==='Notes'?'＋ Add note':`＋ Add ${page.toLowerCase()}`;
  $('#add').hidden=['AI workflow','Settings','Metrics','Chat','Projects','Agents','Users'].includes(page); if(page==='File locks')$('#add').textContent='＋ Reserve files'; $('#search').hidden=page!=='Board'; $('#category-filter').hidden=page!=='Board';
  if(!['Projects','Agents'].includes(page))$('#review-count').textContent=state.notes.filter(n=>n.kind==='Review'&&!n.done).length;
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
  const selectedCategory=$('#category-filter').value;
  const categories=[...new Set(state.tasks.map(t=>t.category))].sort();
  $('#category-filter').innerHTML='<option value="">All categories</option>'+categories.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('');
  $('#category-filter').value=categories.includes(selectedCategory)?selectedCategory:'';
  const tasks=state.tasks.filter(t=>(!$('#category-filter').value||t.category===$('#category-filter').value)&&(t.code+' '+t.title+' '+t.description+' '+t.category).toLowerCase().includes($('#search').value.toLowerCase()));
  $('#task-count').textContent=page==='Board'?`${tasks.length} tasks`:'';
  if(page==='AI workflow'){
    $('#content').innerHTML=`<article class="workflow"><div class="eyebrow">LESS CONTEXT. MORE PROGRESS.</div><h2>Your AI, on the same board.</h2><p>The local MCP shares this SQLite database with the app. It works even when this browser is closed.</p><ol><li>Plan the full authorized scope before claiming. Prefer Small tasks with one independently verifiable outcome; split independent deliverables, keep cohesive fixes together.</li><li>Check actual 5h/weekly account usage and respect Stop before work; unknown quota pauses guarded agents.</li><li>Read task state, continuity and recorded verification.</li><li>Resume In Progress, check actionable Blocked tasks, then pick TODO.</li><li>Create justified work in TODO when needed. Move it as work happens.</li><li>After each completion, call <code>next_work</code> and reassess blockers.</li><li>Keep one task handoff for unfinished work; human notes are optional.</li></ol><p><b>Connect:</b> use the command and arguments in <code>mcp-config.example.json</code>. Use the repository skills <code>scrum-work</code> and <code>scrum-close</code>.</p><p><b>Planning:</b> Split “Improve checkout” into concrete address validation and receipt download outcomes when each can ship separately. Keep “Prevent duplicate receipt on retry” together across API, storage and tests. The MCP <code>task_plan</code> call batches up to ten outcomes, refines unlocked tasks without losing history, and leaves new work in TODO without claims. Independent work remains available to other authorized agents; no automatic workers or extra model calls are started. Use cached policy, not per-task ceremonies. Bytes and server time do not prove zero provider quota cost.</p><p><b>Token efficiency:</b> one candidate per response; human notes and historical retrospectives are excluded. Read pending task-linked AI reminders only when indicated. No automatic polling or model API calls.</p></article>`;return;
  }
  if(page==='Users'){renderUsers();return;}
  if(page==='Projects'){renderProjects();return;}
  if(!project){$('#content').innerHTML='<div class="empty"><div class="eyebrow">A FRESH START</div><h2>Good work starts with a project.</h2><p>Create your first project to bring your tasks and team rituals together.</p><button class="primary" id="empty-project">＋ New project</button></div>';$('#empty-project').onclick=()=>openProject();return;}
  if(page==='Agents'){renderAgents();return;}
  if(page==='Chat'){renderChat();return;}
  if(page==='Settings'){renderSettings();return;}
  if(page==='Metrics'){renderMetrics();return;}
  if(page==='File locks'){renderFileLocks();return;}
  if(page==='Board'){
    if(view.mode==='list'){renderTaskList(tasks,view);return;}
    $('#content').innerHTML='<div class="board">'+statuses.map((s,i)=>`<section class="column" data-status="${s}" style="--tone:${tones[i]}"><div class="column-head"><span class="status-dot"></span>${s==='TODO'?'To do':s}<i>${tasks.filter(t=>t.status===s).length}</i><button data-create="${s}" aria-label="Add task">＋</button></div>${tasks.filter(t=>t.status===s).map(t=>`<button draggable="true" class="card" data-task="${t.id}"><span class="card-id">${state.runtime?.active_task_ids.includes(t.id)?'<span class="active-task-dot" role="img" aria-label="Active on this task" title="Recent agent heartbeat and live task claim"></span>':''}${esc(t.code)}</span><span class="category-tag">${esc(t.category)}</span><h3>${esc(t.title)}</h3>${t.description?`<p>${esc(t.description.slice(0,130))}</p>`:''}${t.dependencies.length?`<p>Blocked by: ${t.dependencies.map(id=>esc(state.tasks.find(d=>d.id===id)?.code||id)).join(', ')}</p>`:''}${t.status==='Cancelled'?`<p>Cancelled: ${esc(t.cancel_reason.slice(0,130))}</p>`:''}${t.blocked_reason?`<p>⚑ ${esc(t.blocked_reason)}</p>`:''}${t.checklist.length?`<p class="checklist-progress">☑ ${t.checklist.filter(i=>i.done).length}/${t.checklist.length} completed</p>`:''}<div class="card-footer"><span class="priority p${t.priority}">${t.priority===1?'↑ High':t.priority===3?'↓ Low':'＝ Normal'}</span><span class="task-owner">${t.locked?'🔒 ':''}${esc(users.find(u=>u.id===t.owner_id)?.name||'Unassigned')}</span></div></button>`).join('')||'<div class="empty-column">No tasks here yet</div>'}</section>`).join('')+'</div>';
    document.querySelectorAll('[data-task]').forEach(b=>{b.onclick=()=>openTask(state.tasks.find(t=>t.id===+b.dataset.task));b.ondragstart=e=>e.dataTransfer.setData('text/plain',b.dataset.task);});
    document.querySelectorAll('[data-create]').forEach(b=>b.onclick=()=>openTask());
    document.querySelectorAll('.column').forEach(c=>{c.ondragover=e=>e.preventDefault();c.ondrop=async e=>{e.preventDefault();const id=Number(e.dataTransfer.getData('text/plain'));const t=state.tasks.find(t=>t.id===id);if(!t)return;if(c.dataset.status==='Blocked'||(['Done','Cancelled'].includes(c.dataset.status)&&t.status!==c.dataset.status)){openTask({...t,status:c.dataset.status});return;}try{await moveTask(id,c.dataset.status);await refresh();}catch(e){error(e);}};});
  }else{
    const notes=state.notes;
    $('#content').innerHTML=notes.length?'<div class="notes">'+notes.map(n=>`<article class="note ${n.done?'done':''}"><small>${esc(new Date(n.created).toLocaleString())} · ${n.audience==='agent'?'AI reminder':'Human only'} · ${esc(n.kind)}</small><h3>${esc(n.title)}</h3>${n.task?`<p>Task: <button data-note-task="${n.task}">${esc(state.tasks.find(t=>t.id===n.task)?.code||n.task)}</button></p>`:''}<ul>${n.items.map(i=>`<li>${esc(i)}</li>`).join('')}</ul>${n.kind==='Review'?`<label><input type="checkbox" data-note="${n.id}" ${n.done?'checked':''}> ${n.done?'Done':'Not done'}</label>`:''}</article>`).join('')+'</div>':`<div class="empty"><h2>No notes yet.</h2><p>Notes are optional. Task state and continuity are the agent session reminder.</p></div>`;
    document.querySelectorAll('[data-note-task]').forEach(b=>b.onclick=()=>{const task=state.tasks.find(t=>t.id===Number(b.dataset.noteTask));if(task)openTask(task);});
    document.querySelectorAll('[data-note]').forEach(c=>c.onchange=async()=>{try{await api('note_check',{project,id:+c.dataset.note,done:c.checked});await refresh();}catch(e){error(e);}});
  }
}
function renderTaskList(tasks,view){
  const owner=t=>users.find(u=>u.id===t.owner_id)?.name||'Unassigned';
  const groups=new Map(),batches=new Map();
  for(const group of [...(state.taskGroups||[])].sort((a,b)=>a.created-b.created))for(const id of group.tasks)batches.set(id,group);
  const batchTitle=group=>group?`Batch ${group.id.slice(0,6)} · ${group.name}`:'No recorded batch';
  const model=t=>t.model_selection?.model||t.model||'Automatic · not recorded';
  const value=(t,key)=>({code:t.number||t.id,title:t.title,status:statuses.indexOf(t.status),priority:t.priority,owner:owner(t),context:t.context_group||'',batch:batchTitle(batches.get(t.id)),model:model(t),updated:Date.parse(t.updated)||0})[key];
  const compare=(a,b)=>{
    const av=value(a,view.sort),bv=value(b,view.sort);
    const result=typeof av==='number'?av-bv:String(av).localeCompare(String(bv),undefined,{numeric:true,sensitivity:'base'});
    return result*(view.direction==='asc'?1:-1)||a.id-b.id;
  };
  const groupFor=t=>{
    const batch=batches.get(t.id);
    if(view.group==='status')return {key:t.status,label:t.status==='TODO'?'To do':t.status,rank:statuses.indexOf(t.status)};
    if(view.group==='priority')return {key:String(t.priority),label:['','High','Normal','Low'][t.priority],rank:t.priority};
    if(view.group==='owner')return {key:String(t.owner_id||0),label:owner(t),rank:t.owner_id?0:1};
    if(view.group==='context')return {key:t.context_group||'none',label:t.context_group||'No planned context group',rank:t.context_group?0:1};
    if(view.group==='category')return {key:t.category,label:t.category,rank:0};
    if(view.group==='batch')return {key:batch?.id||'none',label:batchTitle(batch)+(batch?.active?' · active':''),rank:batch?-(batch.created||Number.MAX_SAFE_INTEGER):1};
    return {key:'all',label:'',rank:0};
  };
  for(const task of [...tasks].sort(compare)){
    const group=groupFor(task);if(!groups.has(group.key))groups.set(group.key,{...group,tasks:[]});groups.get(group.key).tasks.push(task);
  }
  const columns=[['code','Code'],['title','Task'],['status','Status'],['priority','Priority'],['owner','Owner'],['batch','Agent batch'],['model','Selected model'],['updated','Updated']];
  const row=t=>{
    const batch=batches.get(t.id),reserved=batch?.reserved_tasks?.includes(t.id);
    return `<tr><td><button class="task-row-code" data-list-task="${t.id}">${esc(t.code)}</button></td><td class="task-row-title"><button data-list-task="${t.id}">${state.runtime?.active_task_ids.includes(t.id)?'<span class="active-task-dot" aria-label="Active on this task" role="img"></span>':''}${esc(t.title)}</button><small>${esc(t.category)}${t.context_group?' · Context: '+esc(t.context_group):''}</small></td><td><span class="list-status"><i style="background:${tones[statuses.indexOf(t.status)]}"></i>${esc(t.status==='TODO'?'To do':t.status)}</span></td><td><span class="priority p${t.priority}">${['','↑ High','＝ Normal','↓ Low'][t.priority]}</span></td><td>${t.locked?'🔒 ':''}${esc(owner(t))}</td><td title="${esc(batch?batchTitle(batch)+(batch.created?' · '+new Date(batch.created*1000).toLocaleString():''):'Batch history is recorded locally for new grouped runs.')}">${batch?`<span class="batch-label">Batch ${esc(batch.id.slice(0,6))}</span><small>${esc(batch.name)} · ${reserved?'reserved':batch.active?'active':'recorded'}</small>`:'<span class="list-muted">—</span>'}</td><td title="${esc(t.model_selection?.reason||'The model requested by Scrum; provider execution may not be reported.')}">${esc(model(t))}</td><td><time datetime="${esc(t.updated)}">${esc(new Date(t.updated).toLocaleString(undefined,{dateStyle:'short',timeStyle:'short'}))}</time></td></tr>`;
  };
  $('#content').innerHTML=`<section class="task-list"><p class="task-list-hint">Context group shows planned shared context, without reservations. Agent batch shows execution batches. Sort using a column heading. Batch grouping uses each task's latest recorded batch; older runs without saved membership appear ungrouped.</p><div class="task-list-scroll" tabindex="0" aria-label="Scrollable task list"><table class="task-table"><caption class="sr">Project tasks</caption><thead><tr>${columns.map(([key,label])=>`<th scope="col" aria-sort="${view.sort===key?(view.direction==='asc'?'ascending':'descending'):'none'}"><button data-task-sort="${key}">${label}${view.sort===key?(view.direction==='asc'?' ↑':' ↓'):''}</button></th>`).join('')}</tr></thead><tbody>${[...groups.values()].sort((a,b)=>a.rank-b.rank||a.label.localeCompare(b.label)).map(group=>(view.group!=='none'?`<tr class="task-group-row"><th scope="rowgroup" colspan="8">${esc(group.label)} <span>${group.tasks.length} task${group.tasks.length===1?'':'s'}</span></th></tr>`:'')+group.tasks.map(row).join('')).join('')||'<tr><td colspan="8" class="task-list-empty">No tasks match these filters.</td></tr>'}</tbody></table></div></section>`;
  document.querySelectorAll('[data-list-task]').forEach(b=>b.onclick=()=>openTask(state.tasks.find(t=>t.id===Number(b.dataset.listTask))));
  document.querySelectorAll('[data-task-sort]').forEach(b=>b.onclick=()=>setTaskView({sort:b.dataset.taskSort,direction:view.sort===b.dataset.taskSort?(view.direction==='asc'?'desc':'asc'):b.dataset.taskSort==='updated'?'desc':'asc'}));
}
function field(label,name,value='',type='text'){return `<label class="field">${label}${type==='textarea'?`<textarea name="${name}">${esc(value)}</textarea>`:`<input name="${name}" value="${esc(value)}" ${type==='required'?'required':''}>`}</label>`;}
function duration(seconds){if(seconds===null||seconds===undefined)return '—';if(seconds<60)return `${Math.round(seconds)}s`;if(seconds<3600)return `${Math.round(seconds/60)}m`;if(seconds<86400)return `${(seconds/3600).toFixed(1)}h`;return `${(seconds/86400).toFixed(1)}d`;}
function metricBars(entries,format=String){
  const max=Math.max(1,...entries.map(e=>e.value||0));
  return entries.map(e=>`<div class="metric-bar-row"><span>${esc(e.label)}</span><div class="metric-bar-track"><i style="width:${100*(e.value||0)/max}%;background:${e.color||'#6ca48a'}"></i></div><b>${esc(format(e.value))}</b></div>`).join('');
}
function renderMetrics(){
  const m=state.metrics;
  const series=m.daily||[];
  const max=Math.max(1,...series.map(d=>Math.max(d.done,d.cancelled)));
  const line=key=>series.map((d,i)=>`${24+i*552/Math.max(1,series.length-1)},${154-130*d[key]/max}`).join(' ');
  const states=statuses.filter(s=>!['Done','Cancelled'].includes(s));
  $('#content').innerHTML=`<section class="metrics"><div class="metrics-range"><label>Period <select id="metric-days"><option value="7">Last 7 UTC dates</option><option value="30">Last 30 UTC dates</option><option value="90">Last 90 UTC dates</option></select></label><span>Calendar time, including waiting. Updated ${esc(new Date(m.as_of_utc).toLocaleString())}</span></div><div class="metric-kpis"><article><span>Open tasks · now</span><strong>${m.open_tasks}</strong><small>${m.total_tasks} total tasks</small></article><article><span>Tasks completed · period</span><strong>${m.completed_tasks}</strong><small>${m.done_transitions} Done transitions, including re-completions</small></article><article><span>Average cycle time</span><strong>${duration(m.average_cycle_seconds)}</strong><small>${m.cycle_samples} complete tracked samples</small></article><article><span>Time blocked · period</span><strong>${m.blocked_time_percent}%</strong><small>Share of observed nonterminal state time</small></article></div><div class="metric-panels"><article class="metric-panel"><h2>Tasks by status</h2><p>Current distribution</p>${metricBars(statuses.map((s,i)=>({label:s,value:m.distribution[s],color:tones[i]})))}</article><article class="metric-panel"><h2>Average observed time per state</h2><p>Per task observed in the state during this period; partial visits included.</p>${metricBars(states.map(s=>({label:s,value:m.average_observed_state_seconds[s],color:tones[statuses.indexOf(s)]})),duration)}<p>Average lead time: <b>${duration(m.average_lead_seconds)}</b> (${m.lead_samples} samples).</p></article><article class="metric-panel flow-panel"><h2>Completion & cancellation flow</h2><p>State transitions per UTC date; a reopened task can finish more than once.</p><svg class="flow-chart" role="img" aria-label="Daily Done and Cancelled transitions" viewBox="0 0 600 190"><line x1="24" x2="576" y1="154" y2="154" stroke="currentColor" opacity=".2"/><text x="2" y="22" fill="currentColor" font-size="11">${max}</text><polyline points="${line('done')}" fill="none" stroke="#65a887" stroke-width="3"/><polyline points="${line('cancelled')}" fill="none" stroke="#ba8c8c" stroke-width="2"/><text x="24" y="181" fill="currentColor" font-size="11">${esc(series[0]?.date||'')}</text><text x="576" y="181" text-anchor="end" fill="currentColor" font-size="11">${esc(series.at(-1)?.date||'')}</text></svg><div class="chart-legend"><span>● Done</span><span>● Cancelled</span></div><details><summary>View daily data</summary><table class="lock-table"><thead><tr><th>UTC date</th><th>Done</th><th>Cancelled</th></tr></thead><tbody>${series.map(d=>`<tr><td>${esc(d.date)}</td><td>${d.done}</td><td>${d.cancelled}</td></tr>`).join('')}</tbody></table></details></article></div><p class="metrics-note">${m.legacy_tasks} tasks have history from before time tracking. Their earlier time is unknown; they are excluded from lead/cycle averages. Cycle time runs from first In Progress to latest Done; lead time runs from creation. These measure calendar flow, not agent CPU time or token usage.</p></section>`;
  $('#metric-days').value=metricDays;
  const c=state.coordination;
  if(c){
    const panel=document.createElement('article');panel.className='metric-panel';
    panel.innerHTML=`<h2>MCP coordination overhead · local lifetime</h2><p>${esc(c.scope)}. ${esc(c.note)}</p><div class="metric-kpis"><article><span>MCP calls</span><strong>${c.totals.calls}</strong></article><article><span>Response bytes</span><strong>${c.totals.response_bytes}</strong></article><article><span>Server time · ms</span><strong>${Math.round(c.totals.elapsed_ms)}</strong></article><article><span>Failed calls</span><strong>${c.totals.errors}</strong></article></div><details><summary>Per-tool measurements</summary><table class="lock-table"><thead><tr><th>Tool</th><th>Calls</th><th>Request bytes</th><th>Response bytes</th><th>Server ms</th></tr></thead><tbody>${c.tools.map(t=>`<tr><td>${esc(t.tool)}</td><td>${t.calls}</td><td>${t.request_bytes}</td><td>${t.response_bytes}</td><td>${Math.round(t.elapsed_ms)}</td></tr>`).join('')}</tbody></table></details>`;
    const scopes=document.createElement('details');
    scopes.innerHTML=`<summary>Session and task measurements</summary><p>Last ${c.session_limit||20} sessions and top ${c.task_limit||50} tasks. Start mode is initial eligibility. Untagged calls cannot be assigned to a session.</p><h3>Sessions</h3><table class="lock-table"><thead><tr><th>Started</th><th>Start mode</th><th>Calls</th><th>Response bytes</th></tr></thead><tbody>${(c.sessions||[]).map(s=>`<tr><td>${esc(new Date(s.started*1000).toLocaleString())}</td><td>${esc(s.start_mode)}</td><td>${s.calls}</td><td>${s.response_bytes}</td></tr>`).join('')}</tbody></table><h3>Tasks · local IDs</h3><table class="lock-table"><thead><tr><th>Task</th><th>Calls</th><th>Response bytes</th><th>Server ms</th></tr></thead><tbody>${(c.tasks||[]).map(t=>`<tr><td>${t.task}</td><td>${t.calls}</td><td>${t.response_bytes}</td><td>${Math.round(t.elapsed_ms)}</td></tr>`).join('')}</tbody></table>`;
    panel.append(scopes);
    $('#content .metrics').append(panel);
  }
  $('#metric-days').onchange=()=>{metricDays=Number($('#metric-days').value);refresh().catch(error);};
}
function mountTiming(t){
  const section=document.createElement('section');section.className='task-history task-timing';
  section.innerHTML='<h3>Time in each state</h3><div class="timing-content">Loading recorded times…</div>';
  $('#fields').append(section);
  let history=[];
  async function load(before){
    const result=await api('task_timing',{project,id:t.id,history:true,...(before?{before}:{})});
    if(!section.isConnected)return;
    history=before?[...history,...result.intervals]:result.intervals;
    section.querySelector('.timing-content').innerHTML=`<p>Calendar time, including waiting. ${result.partial_history?'Partial history: tracking began after this task existed. Earlier time is unknown.':'Tracked from task creation.'}</p><table class="lock-table"><tbody>${statuses.filter(s=>!['Done','Cancelled'].includes(s)).map(s=>`<tr><td>${esc(s)}</td><td>${duration(result.seconds_by_state[s])}</td></tr>`).join('')}</tbody></table><details><summary>State visits</summary>${history.map(v=>`<article class="activity-entry"><small>${esc(v.status)} · ${esc(users.find(u=>u.id===v.actor)?.name||'System')} · ${esc(new Date(v.entered*1000).toLocaleString())}</small>${['Done','Cancelled'].includes(v.status)?'':`<p>${duration(v.seconds)} ${v.exited===null?'· ongoing':''} ${v.source==='baseline'?'· partial baseline':''}</p>`}</article>`).join('')}<button type="button" class="older-states" ${result.has_more?'':'hidden'}>Load older visits</button></details>`;
    section.querySelector('.older-states').onclick=()=>load(result.oldest).catch(e=>error(e));
  }
  load().catch(e=>{if(section.isConnected)section.querySelector('.timing-content').textContent=e.message;});
}
function renderSettings(){
  const s=state.settings;
  $('#content').innerHTML=`<form id="settings-form" class="workflow settings-form"><h2>Project settings</h2><label class="field">Workflow mode<select name="workflow_mode"><option>Auto</option><option>Light</option><option>Standard</option></select></label><p>Auto prefers Light for bounded Small tasks with one agent. Dependencies, blockers, parallel work or larger scope use Standard. Light never bypasses ownership, usage limits or verification.</p><label class="field">Task sizing policy<select name="task_sizing"><option>Small</option><option>Balanced</option><option>Unrestricted</option></select></label><p>Small: one concrete, independently verifiable outcome per task. Split independent outcomes; avoid trivial fragments and oversized speculative backlogs.</p><fieldset><legend>Usage stop policy</legend><label><input type="checkbox" name="auto_resume_session_quota" ${s.auto_resume_session_quota?'checked':''}> Automatically resume supervised agents after a verified 5-hour quota reset</label><p>Off by default. Requires a connected authorized CLI supervisor and fresh provider readings below both quota limits. Manual Stop/pause cancels pending resume. Desktop chats are not reopened.</p><label><input type="checkbox" name="usage_guard" ${s.usage_guard?'checked':''}> Require recent provider usage before agent work</label><label class="field">5-hour usage stop (%)<input type="number" min="1" max="100" required name="session_stop_percent" value="${s.session_stop_percent}"></label><label class="field">Weekly usage stop (%)<input type="number" min="1" max="100" required name="weekly_stop_percent" value="${s.weekly_stop_percent}"></label><label class="field">Usage check interval (seconds)<input type="number" min="1" max="300" required name="usage_check_seconds" value="${s.usage_check_seconds}"></label><p>These are consumed account quota percentages, not context-window usage. Unknown/stale data pauses guarded agents. Provider refresh delay and safe stopping can allow overshoot; this is not a provider-enforced hard cap.</p></fieldset><label class="field">Maximum parallel agents<input type="number" name="max_parallel_agents" min="1" max="20" required value="${s.max_parallel_agents}"></label><p>Default one. Limits simultaneous agent claims and supervised model starts across this project. Extra workers defer without inference; lowering the limit does not interrupt existing work.</p><label class="field">Maximum active tasks per agent<input type="number" name="max_active_per_agent" min="1" max="20" required value="${s.max_active_per_agent}"></label><p>Counts active In Progress claims with an unexpired lease, per agent in this project. Humans are exempt. Release excess work before lowering the limit.</p>${field('Workspace root (absolute directory path)','workspace_root',s.workspace_root)}<p>Required for file locks. Relative file paths resolve here. Each checkout has its own file reservations. Move the folder and reopen it to change its location.</p><label><input type="checkbox" name="require_evidence" ${s.require_evidence?'checked':''}> Require verification evidence and completed checklist before Done</label><p>Evidence records what was checked and its result. It is not an automatic test runner.</p><button class="primary" type="submit">Save settings</button><p id="settings-result" role="status"></p></form>`;
  $('#settings-form [name=task_sizing]').value=s.task_sizing;
  $('#settings-form [name=workflow_mode]').value=s.workflow_mode;
  if(projects.find(p=>p.id===project)?.uuid)$('#settings-form [name=workspace_root]').readOnly=true;
  $('#settings-form').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{const f=new FormData(e.target);state.settings=await api('project_settings',{project,actor,workflow_mode:f.get('workflow_mode'),task_sizing:f.get('task_sizing'),usage_guard:f.has('usage_guard'),auto_resume_session_quota:f.has('auto_resume_session_quota'),session_stop_percent:Number(f.get('session_stop_percent')),weekly_stop_percent:Number(f.get('weekly_stop_percent')),usage_check_seconds:Number(f.get('usage_check_seconds')),max_parallel_agents:Number(f.get('max_parallel_agents')),max_active_per_agent:Number(f.get('max_active_per_agent')),workspace_root:f.get('workspace_root'),require_evidence:f.has('require_evidence')});state.runtime=await api('agent_status',{project});renderAgentControls();$('#settings-result').textContent='Settings saved.';}catch(e){$('#settings-result').textContent=e.message;}finally{button.disabled=false;}};
}
function renderFileLocks(){
  const data=state.fileLocks||{items:[],has_more:false};
  $('#content').innerHTML=`<section class="file-locks"><div class="lock-toolbar"><p>Reservations are advisory and expire with the task lease. Release files only after editing stops.</p><button id="release-all-files" class="danger">Release all</button></div><div class="lock-table-wrap"><table class="lock-table"><thead><tr><th>File</th><th>Task</th><th>Owner</th><th>Expires</th><th>Action</th></tr></thead><tbody>${data.items.map(l=>`<tr><td>${esc(l.path)}</td><td>${esc(state.tasks.find(t=>t.id===l.task)?.code||'#'+l.task)}</td><td>${esc(l.owner_name)}</td><td>${esc(new Date(l.lock_until*1000).toLocaleString())}</td><td><button data-release-file="${l.id}" data-owner="${l.owner_id}">Release</button></td></tr>`).join('')||'<tr><td colspan="5">No active file locks in this project.</td></tr>'}</tbody></table></div><button id="locks-more" ${data.has_more?'':'hidden'}>Load more</button><p id="locks-result" role="status"></p></section>`;
  document.querySelectorAll('[data-release-file]').forEach(b=>b.onclick=async()=>{try{const force=+b.dataset.owner!==actor;if(force&&!confirm('Release another worker’s file reservation? Coordinate with them before editing.'))return;await api('file_locks',{project,action:'release',actor,ids:[+b.dataset.releaseFile],force});await refresh();}catch(e){$('#locks-result').textContent=e.message;}});
  $('#release-all-files').onclick=()=>modal('Release file locks',`<p>Only the selected project is affected. This does not change task status or undo file edits.</p><label class="field">Scope<select name="scope"><option value="own">My files</option><option value="project">All project files (human only)</option></select></label><p>Coordinate with active workers before releasing their reservations.</p>`,async f=>{await api('file_locks',{project,action:'release',actor,all:true,force:f.get('scope')==='project'});await refresh();},'Release files');
  $('#locks-more').onclick=async()=>{try{const next=await api('file_locks',{project,after:data.next_cursor});state.fileLocks={...next,items:[...data.items,...next.items]};renderFileLocks();}catch(e){$('#locks-result').textContent=e.message;}};
}
function openFileReservation(){
  if(!project)return openProject();
  const tasks=state.tasks.filter(t=>!['Done','In Review','Cancelled'].includes(t.status));
  modal('Claim task & reserve files',`<label class="field">Task<select name="task" required><option value="">Choose task</option>${tasks.map(t=>`<option value="${t.id}">${esc(t.code)} · ${esc(t.title)}</option>`).join('')}</select></label>${field('Files (one relative or absolute path per line)','paths','','textarea')}<p>The task and files are claimed together. A conflict leaves the task unchanged.</p>`,async f=>{const id=Number(f.get('task'));const paths=f.get('paths').split('\n').map(p=>p.trim()).filter(Boolean);if(!paths.length)throw Error('Enter at least one file path.');const r=await api('task_claim',{project,id,...credentials(id),files:paths});if(r.claimed===false)throw Error('File conflict: '+r.conflicts.map(c=>c.path).join(', '));claims[`${project}:${id}`]={actor,token:r.token};saveClaims();await refresh();});
}
function modal(title,fields,save,saveLabel='Save changes'){$('#editor').classList.remove('task-editor');$('#editor-form button[type=submit]').textContent=saveLabel;$('#dialog-title').textContent=title;$('#fields').innerHTML=fields;$('#form-error').textContent='';editing=save;$('#editor').showModal();}
function openProject(){modal('Create project',field('Existing project folder (absolute path)','path','','required')+field('Project name','name','','required')+field('Description','description','','textarea'),async f=>{await api('project_create',{path:f.get('path'),name:f.get('name'),description:f.get('description')}).then(r=>project=r.id);await loadProjects();});}
function readChecklist(){
  return [...document.querySelectorAll('.checklist-row')].map(row=>({text:row.querySelector('[data-item-text]').value.trim(),done:row.querySelector('[data-item-done]').checked}));
}
function mountChecklist(items){
  const section=document.createElement('section');
  section.className='task-checklist';
  section.innerHTML='<div class="checklist-heading"><b>Checklist</b><span id="checklist-count"></span></div><div id="checklist-rows"></div><button type="button" id="add-checklist">＋ Add item</button><p class="checklist-hint">Changes are applied when you save the task.</p>';
  $('#fields').append(section);
  function count(){const rows=readChecklist();$('#checklist-count').textContent=`${rows.filter(i=>i.done).length}/${rows.length} completed`;$('#add-checklist').disabled=rows.length>=100;}
  function add(item={text:'',done:false},focus=false){
    const row=document.createElement('div');row.className='checklist-row';
    row.innerHTML=`<input type="checkbox" data-item-done aria-label="Item completed" ${item.done?'checked':''}><input data-item-text aria-label="Checklist item" placeholder="What needs to be done?" required maxlength="500" value="${esc(item.text)}"><button type="button" aria-label="Remove checklist item">✕</button>`;
    row.querySelector('button').onclick=()=>{row.remove();count();};row.querySelector('[data-item-done]').onchange=count;
    $('#checklist-rows').append(row);count();if(focus)row.querySelector('[data-item-text]').focus();
  }
  items.forEach(item=>add(item));$('#add-checklist').onclick=()=>add(undefined,true);count();
}
function blockedByField(t){
  return `<fieldset class="blocked-by"><legend>Blocked by</legend><p>Select tasks that must finish first.</p>${state.tasks.filter(d=>d.id!==t?.id&& (!['Done','Cancelled'].includes(d.status)||t?.dependencies.includes(d.id))).map(d=>`<label><input type="checkbox" name="dependencies" value="${d.id}" ${t?.dependencies.includes(d.id)?'checked':''}> <b>${esc(d.code)}</b> ${esc(d.title)}</label>`).join('')||'<p>No other tasks available.</p>'}</fieldset>`;
}
function ownerField(t){return `<label class="field">Owner<select name="owner_id"><option value="0">Unassigned</option>${users.filter(u=>u.active!==0||u.id===t?.owner_id).map(u=>`<option value="${u.id}" ${u.id===t?.owner_id?'selected':''}>${esc(u.name)} (${esc(u.kind)})${u.active===0?' · deleted':''}</option>`).join('')}</select></label>`;}
async function openTask(t){
  if(!project)return openProject();
  if(t){const selected=project;try{t=await api('task_get',{project,id:t.id,include_cost:true,full:true});if(project!==selected)return;}catch(e){error(e);return;}}
  const continuityField=t?`<label class="field">Continuity summary<textarea name="continuity" maxlength="2000">${esc(t.continuity||'')}</textarea></label><p>${t.continuity_stale?'Summary may be outdated: task context changed.':'Optional: current result, relevant files, verification and next step.'}</p>`:'';
  const verificationFields='<label class="field">Verification intent<select name="verification_level">' + ['auto','minimal','targeted','full'].map(v=>'<option '+(v===(t?.verification_level||'auto')?'selected':'')+'>'+v+'</option>').join('') + '</select></label>'+field('Verification rationale','verification_reason',t?.verification_reason||'')+'<p>Intent guides check selection. Acceptance evidence and mandatory checks still apply.</p>';
  const scopeField=`<label class="field">Estimated scope<select name="scope_size">${['Unknown','Small','Large'].map(v=>`<option ${v===(t?.scope_size||'Unknown')?'selected':''}>${v}</option>`).join('')}</select></label><label class="field">Scope reason<input name="scope_reason" maxlength="500" value="${esc(t?.scope_reason||'')}"></label><p>Small means one bounded, verifiable outcome; explain why. Auto can use Light despite file count. Dependencies and concurrency still require Standard.</p>`;
  const cost=t?.cost_data||{};const reported=t?.cost_summary?.reported_tokens;
  const costFields=`<fieldset><legend>Estimated vs reported cost</legend><label class="field">Estimated tokens (optional)<input name="estimated_tokens" type="number" min="0" step="1" value="${cost.estimated_tokens??''}"></label><label class="field">Estimated 5-hour account quota (%) (optional)<input name="estimated_quota_percent" type="number" min="0" max="100" step="any" value="${cost.estimated_quota_percent??''}"></label><p>Reported task tokens: <strong>${reported==null?'Not available':reported.toLocaleString()}</strong>${t?.cost_summary?.token_variance==null?'':` · Difference from estimate: ${t.cost_summary.token_variance>0?'+':''}${t.cost_summary.token_variance.toLocaleString()}`}</p><p>Client-reported runs only; coverage may be incomplete. Account quota is shared across agents and cannot be assigned to this task. Estimates are optional judgments, not provider measurements.</p></fieldset>`;
  const mainFields=field('Title','title',t?.title,'required')+`<label class="field">Description<textarea name="description" required maxlength="12000">${esc(t?.description||'')}</textarea></label><label class="field">Acceptance criteria (one per line)<textarea name="acceptance_criteria" required maxlength="12000">${esc((t?.acceptance_criteria||[]).join('\n'))}</textarea></label>`+continuityField+`<p>Define scope and acceptance before creating the task. Post progress in Updates & comments.</p>`+
    field('Planned files (one path per line, relative to workspace)','files',(t?.files||[]).join('\n'),'textarea')+blockedByField(t)+
    (t?field('Verification evidence (required when moving to Done)','evidence',t.evidence,'textarea')+field('Cancellation reason (required for Cancelled)','cancel_reason',t.cancel_reason,'textarea')+field('Other blocker (clear when resolved)','blocked_reason',t.blocked_reason):'');
  const modelFields=field('Model override (blank = automatic)','model',t?.model||'')+`<label class="field">Automatic model tier<select name="model_tier">${['auto','economy','standard','advanced'].map(v=>`<option ${v===(t?.model_tier||'auto')?'selected':''}>${v}</option>`).join('')}</select></label>`+field('Model choice reason','model_reason',t?.model_reason||'')+`<p>Priority: human task override, human agent profile, then automatic tier. AI may choose a tier with a reason. Changes apply on the next task launch.</p><p>Last selection: ${esc(t?.model_selection?.model||'Not selected / provider default')} · ${esc(t?.model_selection?.reason||'')}</p>`;
  const metaFields=field('Category','category',t?.category||'General','required')+field('Planned context group (no reservation)','context_group',t?.context_group||'')+
    (t?`<label class="field">Status<select name="status">${statuses.map(s=>`<option ${t.status===s?'selected':''}>${s}</option>`).join('')}</select></label>`:'<p>New tasks start in TODO.</p>')+
    `<label class="field">Priority<select name="priority"><option value="1" ${t?.priority===1?'selected':''}>High</option><option value="2" ${!t||t.priority===2?'selected':''}>Normal</option><option value="3" ${t?.priority===3?'selected':''}>Low</option></select></label>`+(t?ownerField(t):'')+scopeField+verificationFields+modelFields+costFields;
  const fields=`<div class="task-editor-layout"><div class="task-editor-main">${mainFields}</div><section class="task-editor-meta" aria-label="Task properties">${metaFields}</section></div>`;
  modal(t?`Edit ${t.code}`:'Create task',fields,async f=>{
    const a={...(t&&f.get('continuity')!==(t.continuity||'')?{continuity:f.get('continuity')}:{}),...(f.get('model')!==(t?.model||'')?{model:f.get('model')}:{}),verification_level:f.get('verification_level'),verification_reason:f.get('verification_reason'),model_tier:f.get('model_tier'),model_reason:f.get('model_reason'),scope_size:f.get('scope_size'),scope_reason:f.get('scope_reason'),title:f.get('title'),description:f.get('description'),acceptance_criteria:f.get('acceptance_criteria').split('\n').map(c=>c.trim()).filter(Boolean),category:f.get('category'),context_group:f.get('context_group'),priority:+f.get('priority'),checklist:readChecklist(),dependencies:f.getAll('dependencies').map(Number),files:f.get('files').split('\n').map(p=>p.trim()).filter(Boolean)};
    for(const k of ['estimated_tokens','estimated_quota_percent']){const v=f.get(k);a[k]=v===''?null:Number(v);}
    if(t){
      a.blocked_reason=f.get('blocked_reason');
      a.evidence=f.get('evidence');
      a.cancel_reason=f.get('cancel_reason');
      if(Number(f.get('owner_id'))!==(t.owner_id||0))a.owner_id=Number(f.get('owner_id'));
      if(f.get('status')==='In Progress' && 'owner_id' in a && a.owner_id!==actor)throw Error('Choose yourself as owner when starting work, or assign the task in TODO.');
      await moveTask(t.id,f.get('status'),a);
    }else await api('task_create',{project,...a,...(actor?{actor}:{})});
    await refresh();
  });
  $('#editor').classList.add('task-editor');
  mountChecklist(t?.checklist || []);
  if(t){
    const section=document.createElement('section');
    if(t.context_batch){
      const group=document.createElement('section');group.className='task-history';
      group.innerHTML=`<h3>Grouped tasks</h3><p>Reserved for ${esc(t.context_batch.name)} until ${esc(new Date(t.context_batch.expires*1000).toLocaleString())}. Tasks run sequentially; only the current task is In Progress. Reservations expire if the worker stops responding.</p><ul>${t.context_batch.tasks.map(item=>`<li><button type="button" data-group-task="${item.id}">${esc(item.code)} · ${esc(item.title)}</button> · ${esc(item.status)}${item.id===t.context_batch.current?' · current':''}</li>`).join('')}</ul>`;
      group.querySelectorAll('[data-group-task]').forEach(button=>button.onclick=()=>{$('#editor').close();openTask({id:Number(button.dataset.groupTask)});});
      $('#fields').append(group);
    }
    section.innerHTML=`<p>${t.locked?'🔒 Locked until '+esc(new Date(t.lock_until*1000).toLocaleString()):'No active lock'}</p><button type="button" id="claim-task">Claim / renew lock</button> <button type="button" id="release-task">Release lock</button> <button type="button" id="force-release">Force release (human)</button>`;
    $('#fields').append(section);
    const reopen=async()=>{$('#editor').close();await refresh();openTask(state.tasks.find(x=>x.id===t.id));};
    $('#claim-task').onclick=async()=>{try{const r=await api('task_claim',{project,id:t.id,...credentials(t.id)});if(r.claimed===false)throw Error('File conflict: '+r.conflicts.map(c=>c.path).join(', '));claims[`${project}:${t.id}`]={actor,token:r.token};saveClaims();await reopen();}catch(e){$('#form-error').textContent=e.message;}};
    const release=async force=>{try{await api('task_release',{project,id:t.id,...credentials(t.id),force});delete claims[`${project}:${t.id}`];saveClaims();await reopen();}catch(e){$('#form-error').textContent=e.message;}};
    $('#release-task').onclick=()=>release(false);
    $('#force-release').onclick=()=>{if(confirm('Release this lock? The current worker may still be running. Coordinate with them before taking over.'))release(true);};
    const modelButton=document.createElement('button');modelButton.type='button';modelButton.textContent='Apply model override only';modelButton.onclick=async()=>{try{await api('task_update',{project,id:t.id,actor,model:$('#fields [name=model]').value});await reopen();}catch(e){$('#form-error').textContent=e.message;}};$('#fields').append(modelButton);
    mountTiming(t);
    mountActivity(t);
    mountTaskNotes(t);
    mountAttachments(t);
  }
  for(const section of [...$('#fields').children]){if(!section.classList.contains('task-editor-layout'))$('.task-editor-main').append(section);}
}
function mountTaskNotes(t){
  const selected=project,section=document.createElement('section');section.className='task-history';
  section.innerHTML='<h3>Related notes</h3><p>Human notes and explicit AI reminders linked to this task. Load only when needed.</p><button type="button">Load related notes</button><div aria-live="polite"></div>';
  $('#fields').append(section);
  let after=0;
  const button=section.querySelector('button'),list=section.querySelector('div');
  button.onclick=async()=>{button.disabled=true;try{
    const result=await api('task_get',{project:selected,id:t.id,include_notes:true,notes_content:true,notes_audience:'all',notes_after:after});
    if(!section.isConnected)return;
    const notes=result.notes;after=notes.next_cursor;
    list.insertAdjacentHTML('beforeend',notes.items.map(n=>`<article class="activity-entry"><b>${n.audience==='agent'?'AI reminder':'Human only'} · ${esc(n.title)}</b><ul>${n.items.map(item=>`<li>${esc(item)}</li>`).join('')}</ul></article>`).join('')||'<p>No related notes.</p>');
    button.hidden=!notes.has_more;button.textContent='Load more related notes';
  }catch(e){list.textContent=e.message;}finally{button.disabled=false;}};
}
function mountAttachments(t){
  const selected=project,section=document.createElement('section');section.className='task-history';
  section.innerHTML='<h3>Reference attachments</h3><p>Up to ten files, 5 MiB each. Images preview only when requested; downloads load content on demand. JSON exports preserve metadata only. Save local attachment blobs separately for transfer.</p><label class="field">Reference file<input type="file" id="reference-file" accept=".png,.jpg,.jpeg,.gif,.webp,.pdf,.txt,.md,.csv,.json,.zip,.docx,.xlsx,.pptx"></label><button type="button" id="reference-add">Attach file</button><div id="reference-list"></div><p id="reference-result" role="status"></p>';
  $('#fields').append(section);
  const urls=[];const clear=()=>{urls.forEach(url=>URL.revokeObjectURL(url));urls.length=0;};
  $('#editor').addEventListener('close',clear,{once:true});
  async function load(){
    const result=await api('task_attachment',{project:selected,id:t.id,action:'list'});
    if(!section.isConnected)return;clear();
    $('#reference-list').innerHTML=result.items.map(item=>`<article class="activity-entry"><b>${esc(item.name)}</b><p>${esc(item.mime)} · ${item.size.toLocaleString()} bytes${item.available?'':' · Content missing; copy local attachment blobs from the source checkout.'}</p>${item.previewable&&item.available?`<button type="button" data-reference-preview="${item.id}">Preview image</button> `:''}<button type="button" data-reference-download="${item.id}" ${item.available?'':'disabled'}>Download</button> <button type="button" data-reference-remove="${item.id}">Remove reference</button><div data-reference-image="${item.id}"></div></article>`).join('')||'<p>No reference files attached.</p>';
    async function content(key){
      const item=await api('task_attachment',{project:selected,id:t.id,action:'get',attachment_id:key,include_content:true});
      const bytes=Uint8Array.from(atob(item.content_base64),c=>c.charCodeAt(0));
      const url=URL.createObjectURL(new Blob([bytes],{type:item.mime}));urls.push(url);return {item,url};
    }
    section.querySelectorAll('[data-reference-preview]').forEach(button=>button.onclick=async()=>{try{const {item,url}=await content(button.dataset.referencePreview);const image=document.createElement('img');image.src=url;image.alt=item.name;image.style.maxWidth='100%';image.style.maxHeight='360px';section.querySelector(`[data-reference-image="${item.id}"]`).replaceChildren(image);}catch(e){$('#reference-result').textContent=e.message;}});
    section.querySelectorAll('[data-reference-download]').forEach(button=>button.onclick=async()=>{try{const {item,url}=await content(button.dataset.referenceDownload);const link=document.createElement('a');link.href=url;link.download=item.name;link.click();}catch(e){$('#reference-result').textContent=e.message;}});
    section.querySelectorAll('[data-reference-remove]').forEach(button=>button.onclick=async()=>{if(!confirm('Remove this reference and its local file content?'))return;try{const result=await api('task_attachment',{project:selected,id:t.id,action:'remove',attachment_id:button.dataset.referenceRemove,...credentials(t.id)});await load();$('#reference-result').textContent=result.warning||'Reference removed.';}catch(e){$('#reference-result').textContent=e.message;}});
  }
  $('#reference-add').onclick=async()=>{
    const file=$('#reference-file').files[0];if(!file)return;
    if(file.size>5*1024*1024){$('#reference-result').textContent='File exceeds 5 MiB.';return;}
    const button=$('#reference-add');button.disabled=true;
    try{const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('Could not read selected reference'));reader.readAsDataURL(file);});await api('task_attachment',{project:selected,id:t.id,action:'add',name:file.name,content_base64:encoded,...credentials(t.id)});await load();$('#reference-result').textContent='Reference attached.';$('#reference-file').value='';}catch(e){$('#reference-result').textContent=e.message;}finally{button.disabled=false;}
  };
  load().catch(e=>$('#reference-result').textContent=e.message);
}
function mountActivity(t){
  const section=document.createElement('section');section.className='task-history';
  section.innerHTML='<h3>Updates & comments</h3><p>Record progress, findings and next steps here. Change the task specification only when its scope changes.</p><div id="activity-list" aria-live="polite"></div><button type="button" id="activity-older" hidden>Load older entries</button><label class="field">Entry type<select id="activity-kind"><option value="work">Work log</option><option value="comment">Comment</option></select></label><label class="field">Update<textarea id="activity-text" maxlength="2000" placeholder="What changed? Include results or the next step."></textarea></label><button type="button" id="activity-add">Post update</button><p id="activity-error" role="alert"></p>';
  $('#fields').append(section);
  let oldest=null;
  async function load(older=false){
    const result=await api('task_activity',{project,id:t.id,...(older&&oldest?{before:oldest}:{})});
    if(!section.isConnected)return;
    const html=result.items.map(e=>`<article class="activity-entry"><small>${esc(e.kind)} · ${esc(users.find(u=>u.id===e.actor)?.name||'System')} · ${esc(new Date(e.created).toLocaleString())}</small><p>${esc(e.text)}</p></article>`).join('');
    if(older)$('#activity-list').insertAdjacentHTML('afterbegin',html);else $('#activity-list').innerHTML=html||'<p>No activity yet.</p>';
    oldest=result.oldest;$('#activity-older').hidden=!result.has_more;
  }
  $('#activity-older').onclick=()=>load(true).catch(e=>$('#activity-error').textContent=e.message);
  $('#activity-add').onclick=async()=>{
    const button=$('#activity-add');button.disabled=true;
    try{await api('task_activity',{project,id:t.id,...credentials(t.id),text:$('#activity-text').value,kind:$('#activity-kind').value});$('#activity-text').value='';$('#activity-error').textContent='';await load();}
    catch(e){$('#activity-error').textContent=e.message;}finally{button.disabled=false;}
  };
  load().catch(e=>{if(section.isConnected)$('#activity-error').textContent=e.message;});
}
function openNote(){
  if(!project)return openProject();
  const taskField=`<label class="field">Related task (optional)<select name="task"><option value="">Project-wide</option>${state.tasks.map(t=>`<option value="${t.id}">${esc(t.code)} · ${esc(t.title)}</option>`).join('')}</select></label>`;
  const audienceField='<label class="field">Audience<select name="audience"><option value="human">Human only</option><option value="agent">AI reminder (pending work, task required)</option></select></label>';
  modal('Add note',field('Title','title','','required')+audienceField+taskField+field('Items (one per line)','items','','textarea'),async f=>{
    await api('note_add',{project,kind:'Review',audience:f.get('audience'),title:f.get('title'),items:f.get('items').split('\n').map(s=>s.trim()).filter(Boolean),...(f.get('task')?{task:Number(f.get('task'))}:{})});await refresh();
  });
}
$('#new-user').onclick=()=>modal('Create human user',field('Name','name','','required'),async f=>{const r=await api('user_register',{identity:'human:'+crypto.randomUUID(),name:f.get('name'),kind:'human'});actor=r.id;localStorage.setItem('scrum-user',actor);await loadUsers();render();});
$('#current-user').onchange=()=>{actor=Number($('#current-user').value)||null;localStorage.setItem('scrum-user',actor||'');};
$('#new-project').onclick=openProject;
$('#project').onchange=()=>{project=+$('#project').value;refresh().catch(error);};
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>{page=b.dataset.page;refresh().catch(error);});
document.querySelectorAll('[data-board-view]').forEach(b=>b.onclick=()=>setTaskView({mode:b.dataset.boardView}));
$('#task-group').onchange=e=>setTaskView({group:e.target.value});
$('#task-sort').onchange=e=>setTaskView({sort:e.target.value,direction:e.target.value==='updated'?'desc':'asc'});
$('#task-sort-direction').onclick=()=>setTaskView({direction:taskView().direction==='asc'?'desc':'asc'});
$('#search').oninput=render;
$('#category-filter').onchange=render;
function applyTheme(theme){document.documentElement.dataset.theme=theme;$('#theme-toggle').textContent=theme==='dark'?'☀ Light':'☾ Dark';$('#theme-toggle').setAttribute('aria-pressed',theme==='dark');}
applyTheme(localStorage.getItem('scrum-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'));
$('#theme-toggle').onclick=()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';localStorage.setItem('scrum-theme',theme);applyTheme(theme);};
$('#add').onclick=()=>page==='Board'?openTask():page==='File locks'?openFileReservation():openNote();
$('#close').onclick=$('#cancel').onclick=()=>$('#editor').close();
$('#editor-form').onsubmit=async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{await editing(new FormData(e.target));$('#editor').close();}catch(e){$('#form-error').textContent=e.message;}finally{b.disabled=false;}};
window.addEventListener('focus',()=>{if(!$('#editor').open&&!['Settings','Chat','Projects'].includes(page))refresh(true).catch(error);});
setInterval(()=>{if(!document.hidden&&!$('#editor').open&&!['Settings','Chat','Projects'].includes(page))refresh(true).catch(error);},15000);
loadProjects().catch(error);

function renderChat(){
  const data=state.chat;
  $('#content').innerHTML=`<section class="workflow"><div id="chat-messages"></div><button id="chat-older" ${data.has_more?'':'hidden'}>Load older</button> <button id="chat-refresh">Check new messages</button><form id="chat-form"><label class="field">Recipient (visible to everyone in this project)<select name="recipient"><option value="">Everyone</option>${users.filter(u=>u.active!==0).map(u=>`<option value="${u.id}">${esc(u.name)}</option>`).join('')}</select></label><label class="field">Task (optional)<select name="task"><option value="">No task</option>${state.tasks.map(t=>`<option value="${t.id}">${esc(t.code)} · ${esc(t.title)}</option>`).join('')}</select></label><label class="field">Message<textarea name="text" required maxlength="2000"></textarea></label><button type="submit" class="primary">Send message</button></form><p id="chat-error" role="alert"></p></section>`;
  let oldest=data.oldest,latest=data.latest;
  const html=items=>items.map(m=>`<article class="activity-entry"><small>${esc(m.author_name)} · ${esc(new Date(m.created).toLocaleString())}${m.recipient?' → '+esc(users.find(u=>u.id===m.recipient)?.name||m.recipient):''}${m.task?' · '+esc(state.tasks.find(t=>t.id===m.task)?.code||m.task):''}</small><p style="white-space:pre-wrap">${esc(m.text)}</p></article>`).join('');
  $('#chat-messages').innerHTML=html(data.items);
  async function read(older=false){
    const result=await api('project_chat',{project,...(older?{before:oldest}:{after:latest})});
    $('#chat-messages').insertAdjacentHTML(older?'afterbegin':'beforeend',html(result.items));
    if(older){oldest=result.oldest||oldest;$('#chat-older').hidden=!result.has_more;}
    else {latest=result.latest;$('#chat-refresh').textContent=result.has_more?'Load more new messages':'Check new messages';}
  }
  $('#chat-older').onclick=()=>read(true).catch(e=>$('#chat-error').textContent=e.message);
  $('#chat-refresh').onclick=()=>read().catch(e=>$('#chat-error').textContent=e.message);
  $('#chat-form').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{const f=new FormData(e.target);await api('project_chat',{project,actor,text:f.get('text'),...(f.get('recipient')?{recipient:Number(f.get('recipient'))}:{}),...(f.get('task')?{task:Number(f.get('task'))}:{})});e.target.elements.text.value='';await read();}catch(err){$('#chat-error').textContent=err.message;}finally{button.disabled=false;}};
}

function openFolder(){modal('Open / import project',field('Folder containing .scrum (absolute path)','path','','required')+'<p>Imports snapshot.json only when no local project data exists. Opening an existing database preserves local work.</p>',async f=>{const r=await api('project_open',{path:f.get('path')});project=r.id;await loadProjects();});}
function renderProjects(){
  $('#content').innerHTML=`<section class="workflow"><h2>Project folders</h2><p>One panel, separate local databases. Git carries project.json and snapshot.json; local databases and locks stay in each checkout.</p><button id="folder-create">Create in folder</button> <button id="folder-open">Open / import folder</button><div class="lock-table-wrap"><table class="lock-table"><thead><tr><th>Project</th><th>Folder & identity</th><th>Actions</th></tr></thead><tbody>${projects.map(p=>`<tr><td>${esc(p.name)}${p.available===false?' · unavailable':''}${p.legacy?' · legacy database':''}</td><td>${esc(p.path||'No workspace set')}<br><small>${esc(p.uuid||'Migration required')}${p.error?'<br>'+esc(p.error):''}</small></td><td>${p.legacy?`<button data-migrate="${p.id}">Migrate to folder</button>`:`${p.available!==false?`<button data-export="${p.id}">Export for Git</button> <button data-restore="${p.id}">Restore snapshot</button>`:''} <button data-forget="${p.id}">Remove from panel</button>`}</td></tr>`).join('')||'<tr><td colspan="3">No registered projects.</td></tr>'}</tbody></table></div><p id="folder-result" role="status"></p></section>`;
  $('#folder-create').onclick=openProject;$('#folder-open').onclick=openFolder;
  document.querySelectorAll('[data-export]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const r=await api('project_export',{project:+b.dataset.export});$('#folder-result').textContent='Exported: '+r.path+(r.attachments?.warning?' '+r.attachments.warning:'');}catch(e){error(e);}finally{b.disabled=false;}});
  document.querySelectorAll('[data-forget]').forEach(b=>b.onclick=async()=>{if(!confirm('Remove this project from the panel? Files remain on disk.'))return;try{await api('project_forget',{project:+b.dataset.forget});await loadProjects();}catch(e){error(e);}});
  document.querySelectorAll('[data-restore]').forEach(b=>b.onclick=async()=>{if(!confirm('Replace local tasks, notes and history with snapshot.json? A local backup will be created. Release active task claims first.'))return;try{const r=await api('project_restore',{project:+b.dataset.restore,confirm:true});await loadProjects();$('#folder-result').textContent='Snapshot restored. Backup: '+r.backup;}catch(e){error(e);}});
  document.querySelectorAll('[data-migrate]').forEach(b=>b.onclick=()=>{const p=projects.find(p=>p.id===+b.dataset.migrate);modal('Migrate legacy project',field('Existing project folder (absolute path)','path',p.path)+ '<p>Stop other old MCP/website processes first. Migration keeps an original database backup. Restart this website and MCP clients after success.</p>',async f=>{await api('project_migrate',{project:p.id,path:f.get('path')});project=p.id;await loadProjects();});});
}

function renderAgentControls(){
  const panel=$('#agent-controls');panel.hidden=!project||page==='Projects';
  const r=state.runtime;if(!r)return;
  $('#agent-counts').textContent=`${r.active_agents} active agents · ${r.active_tasks} active tasks · ${r.workflow?.effective||'Standard'} workflow (${r.workflow?.configured||'Auto'})`;
  $('#agent-counts').title=r.workflow?.reason||'';
  let readiness=r.wakeup;
  const recorded=lastControlSignal?.project===project&&lastControlSignal.generation===r.control.generation&&lastControlSignal.action==='wakeup';
  if(recorded&&lastControlSignal.report&&((lastControlSignal.report.status==='starting'&&!readiness?.connected_bridges&&Date.now()-lastControlSignal.at<20000)||['launch_failed','profiles_stopped'].includes(lastControlSignal.report.status)))readiness=lastControlSignal.report;
  $('#control-status').textContent=r.control.paused?'Stop requested — agents close at their next safe checkpoint.':readiness?recorded&&readiness.status==='ready'?`Wakeup recorded for ${readiness.connected_bridges} connected agent(s). Check Agents or Chat for the launch result.`:`${recorded?'Wakeup recorded. ':''}${readiness.message}`:'Open Agents to check worker connections before sending Wakeup.';
  $('#control-status').dataset.state=readiness?.status||'';
  $('#control-next-step').textContent=readiness?.next_step||'';
  $('#control-next-step').hidden=!readiness;
  $('#control-agents').hidden=page==='Agents';
}
async function signalProject(action){
  if(!actor){error(Error('Choose a human user first.'));return;}
  const button=action==='stop'?$('#project-stop'):$('#project-wakeup');button.disabled=true;
  try{const result=await api('project_control',{project,actor,action});lastControlSignal={project,action,generation:result.control.generation,report:result.wakeup,at:Date.now()};if(state.runtime){state.runtime.control=result.control;if(result.wakeup)state.runtime.wakeup=result.wakeup;renderAgentControls();}await refresh();}catch(e){error(e);}finally{button.disabled=false;}
}
$('#project-stop').onclick=()=>signalProject('stop');
$('#project-wakeup').onclick=()=>signalProject('wakeup');
$('#control-agents').onclick=async()=>{page='Agents';try{await refresh();$('#create-agent-profile')?.focus();}catch(e){error(e);}};
function renderAgents(){
  const r=state.runtime;
  const profiles=state.managedAgents?.agents||[];
  const profileCards=`<div class="managed-agent-toolbar"><p>Create a named CLI agent with a Markdown/TOML role, a text prompt, or no extra role. Creation starts it immediately by default; project Stop and usage policy can hold execution.</p><button class="primary" id="create-agent-profile">＋ Add agent</button></div><div class="managed-agent-grid">${profiles.filter(a=>!a.deleted).map(a=>`<article class="managed-agent-card"><h3>${esc(a.name)}</h3><p>Model: ${esc(a.model||'Provider default')}</p><p>${esc(a.client)} · ${esc(a.status)} · ${a.connected?'Bridge connected':'Bridge disconnected'}${!a.enabled?' · Stop requested':''}</p><p>${a.source?'Imported: '+esc(a.source):a.instruction_chars?'Custom prompt':'Default agent'} · ${a.instruction_chars} instruction characters · ${a.resume_on_wakeup?'Included in Wakeup':'Excluded from Wakeup'}</p>${a.error?`<p class="managed-agent-error">${esc(a.error)}</p>`:''}<button data-profile-action="model" data-profile-id="${a.id}">Edit model</button> <button data-profile-action="start" data-profile-id="${a.id}" ${['working','stopping','starting'].includes(a.status)?'disabled':''}>Start / continue</button> <button data-profile-action="stop" data-profile-id="${a.id}" ${!a.enabled&&!a.connected&&!a.resume_on_wakeup?'disabled':''}>Stop agent</button> <button class="danger" data-profile-action="delete" data-profile-id="${a.id}">Delete agent</button></article>`).join('')||'<p>No managed profiles yet. Existing manually connected agents appear below.</p>'}</div><p id="agent-profile-result" role="status"></p>`;
  const usage=(a,key)=>a[key]===null||a[key]===undefined?'Unknown':`${a[key]}% used${a.usage_fresh?'':' · stale'}`;
  $('#content').innerHTML=`<section class="workflow"><h2>Agents in this project</h2>${profileCards}<p>Quota readings belong to the provider account, may repeat across agents, and must not be summed or attributed to tasks. Activity means a recent cooperative heartbeat; green task dots also require a live task claim. It is not proof that an OS process is running. Stop signals need a cooperating agent or a connected CLI bridge.</p><div class="lock-table-wrap"><table class="lock-table"><thead><tr><th>Agent</th><th>Status</th><th>Account quota · 5h / weekly</th><th>Bridge</th><th>Last seen</th></tr></thead><tbody>${r.agents.map(a=>`<tr><td>${esc(a.name)}<br><small>${esc(a.client)}</small></td><td>${a.active?'<span class="active-task-dot"></span>':''}${esc(a.status)}${a.stop_pending?' · stop pending':''}${a.reason?'<br>'+esc(a.reason):''}${a.auto_resume_status?'<br>Auto resume: '+esc(a.auto_resume_status):''}</td><td>${esc(usage(a,'session_used_percent'))}<br>${esc(usage(a,'weekly_used_percent'))}</td><td>${a.bridge_connected?'Connected':'Disconnected'}</td><td>${esc(new Date(a.heartbeat*1000).toLocaleString())}</td></tr>`).join('')||'<tr><td colspan="5">No agents have reported a checkpoint yet.</td></tr>'}</tbody></table></div><h3>Wakeup conversation</h3><p>With a connected CLI bridge, Wakeup sends a continuation instruction to its Codex or Claude worker. Open Chat to see the exact instruction, final response (up to 1,500 characters), launch failures and interruptions. Raw tool logs stay local. This is the Scrum Studio project chat, not a message in an existing Desktop conversation. Each Wakeup starts a new CLI run that recovers context from Scrum tasks.</p><h3>Connect a CLI bridge</h3><p>From the Scrum Studio installation, explicitly start one bridge for each intended worker:</p><pre><code>python scripts/agent_bridge.py --project ${project} --client codex --worker worker1 --allow-run</code></pre><p>Use <code>--client claude --usage-file ABSOLUTE_USAGE_JSON</code> for Claude Code with its status-line telemetry feed. Setup supports <code>--client codex</code>, <code>claude</code> or <code>both</code>. Manual bridges wait for Wakeup. Managed profiles use their explicit Start immediately setting. Opening this page alone never starts a worker. CLI permissions still apply. External desktop chats cannot be woken by this page.</p></section>`;
  $('#create-agent-profile').onclick=()=>openAgentProfile();
  document.querySelectorAll('[data-profile-action]').forEach(button=>button.onclick=async()=>{
    const profile=profiles.find(a=>a.id===button.dataset.profileId);if(!profile)return;
    if(button.dataset.profileAction==='delete'){
      if(!actor){$('#agent-profile-result').textContent='Choose an active human user first.';return;}
      if(!confirm(`Delete agent ${profile.name} across registered projects? Unfinished work and reservations will be recovered; history is preserved. Running workers must stop first. You can restore the profile from Users.`))return;
      button.disabled=true;
      try{const result=await api('user_manage',{actor,id:profile.actor,action:'delete'});await loadProjects();$('#agent-profile-result').textContent=result.pending?result.next_step:`Agent deleted; ${result.tasks_recovered||0} unfinished task(s) recovered. History preserved. Restore from Users / Show deleted profiles.`;}
      catch(e){$('#agent-profile-result').textContent=e.message;button.disabled=false;}
      return;
    }
    if(button.dataset.profileAction==='model'){openAgentModel(profile);return;}
    if(button.dataset.profileAction==='start'&&profile.client==='claude'){openAgentAccountStart(profile);return;}
    button.disabled=true;try{const result=await api('agent_manage',{project,actor,action:button.dataset.profileAction,id:profile.id});await refresh();$('#agent-profile-result').textContent=result.request;}catch(e){$('#agent-profile-result').textContent=e.message;button.disabled=false;}
  });
}

function openAgentModel(profile){
  modal(`Model for ${profile.name}`,`<label class="field">Model (optional)<input name="model" maxlength="256" value="${esc(profile.model||'')}" placeholder="Blank uses provider default"></label><p>Enter an explicit ${esc(profile.client)} model ID or alias, or leave blank to preserve provider defaults. Saved changes apply to the next authorized run; the current run continues. No catalog lookup or account probe is performed. Provider rejection is shown in Agents and Chat; Scrum Studio never selects a replacement.</p>`,async f=>{
    await api('agent_manage',{project,actor,action:'update',id:profile.id,model:f.get('model')});await refresh();$('#agent-profile-result').textContent='Model saved for the next authorized run; active runs are unchanged.';
  },'Save model');
}
function openAgentAccountStart(profile){
  modal(`Start ${profile.name}`,`<p>This runs the configured Claude CLI account. Do not authorize a shared account without permission.</p><label><input type="checkbox" name="confirm_client_account" required> I authorize using the configured Claude account for this agent.</label>`,async f=>{
    await api('agent_manage',{project,actor,action:'start',id:profile.id,confirm_client_account:f.has('confirm_client_account')});await refresh();
  },'Start agent');
}
function openAgentProfile(){
  const fields=`<p>The agent will use this project's tasks, work limits and file locks. Starting a CLI agent consumes its provider allowance. Empty instructions use the default Scrum workflow.</p><label class="field">Agent name<input name="name" required maxlength="80" placeholder="e.g. Frontend reviewer"></label><label class="field">Client<select name="client"><option value="codex">Codex CLI</option><option value="claude">Claude Code CLI</option></select></label><label class="field">Model (optional)<input name="model" maxlength="256" placeholder="Blank uses provider default"></label><p>Enter an explicit provider model ID or alias. Availability is checked by the provider at the next authorized run; no account probe or automatic replacement is performed.</p><label class="field">Import agent file (.md or .toml)<input id="agent-markdown" type="file" accept=".md,.toml,text/markdown,application/toml,text/plain"></label><p id="agent-markdown-source">No file selected. You can write instructions below or leave them empty.</p><input type="hidden" name="source" value=""><label class="field">Agent instructions (optional)<textarea name="instructions" maxlength="8000" rows="7" placeholder="Describe the agent role, focus and working preferences. Leave empty for default behavior."></textarea></label><p>Markdown and TOML content is imported as editable role text, not as executable client configuration. It cannot disable ownership, quota, Stop or verification rules. Maximum 8,000 characters; profiles stay local to this checkout.</p><div id="claude-agent-settings" hidden><label class="field">Claude usage JSON path<input name="usage_file" placeholder="Absolute path to the configured usage feed"></label><label><input type="checkbox" name="confirm_client_account"> I authorize using the configured Claude account.</label><p>Required before starting Claude. This site does not install or sign in to another account.</p></div><label><input type="checkbox" name="start_immediately" checked> Start immediately after creation</label>`;
  modal('Add project agent',fields,async f=>{
    const result=await api('agent_manage',{project,actor,action:'create',name:f.get('name'),client:f.get('client'),model:f.get('model'),instructions:f.get('instructions'),source:f.get('source'),usage_file:f.get('client')==='claude'?f.get('usage_file'):'',start_immediately:f.has('start_immediately'),confirm_client_account:f.has('confirm_client_account')});
    await refresh();$('#agent-profile-result').textContent=result.error?`Profile created, but not started: ${result.error}`:`Agent created: ${result.request}. Open Chat for its instruction and result.`;
  },'Create agent');
  $('#fields [name=client]').onchange=e=>{$('#claude-agent-settings').hidden=e.target.value!=='claude';};
  $('#agent-markdown').onchange=async e=>{
    const file=e.target.files[0];if(!file)return;
    try{if(!/\.(md|toml)$/i.test(file.name)||file.size>32000)throw Error('Choose a UTF-8 .md or .toml file up to 32 KB.');const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());if(text.length>8000)throw Error('Agent instructions must be at most 8,000 characters.');$('#fields [name=instructions]').value=text;$('#fields [name=source]').value=file.name;$('#agent-markdown-source').textContent=`Imported ${file.name}; instructions below are editable.`;if(!$('#fields [name=name]').value)$('#fields [name=name]').value=file.name.replace(/\.(md|toml)$/i,'').slice(0,80);$('#form-error').textContent='';}catch(error){e.target.value='';$('#form-error').textContent=error.message;}
  };
}

function renderUsers(){
  $('#content').innerHTML=`<section class="workflow"><h2>Local users</h2><p>Profiles apply to all registered projects on this installation. Deleting disables a profile and removes it from new assignments; authored history keeps its original attribution. You can restore a deleted profile. Agent deletion recovers unfinished ownership and reservations across projects. Running managed workers receive Stop first; wait for exit and retry Delete. Blockers, review evidence and historical authorship are preserved. The last active human is protected.</p><button id="users-create">Add human user</button> <label><input type="checkbox" id="show-deleted-users" ${showDeletedUsers?'checked':''}> Show deleted profiles</label><div class="lock-table-wrap"><table class="lock-table"><thead><tr><th>Name</th><th>Kind</th><th>Status</th><th>Action</th></tr></thead><tbody>${users.filter(u=>showDeletedUsers||u.active!==0).map(u=>`<tr><td>${esc(u.name)}</td><td>${esc(u.kind)}</td><td>${u.active===0?'Deleted':'Active'}</td><td><button data-manage-user="${u.id}" data-action="${u.active===0?'restore':'delete'}">${u.active===0?'Restore':'Delete'}</button></td></tr>`).join('')||'<tr><td colspan="4">Create your first human profile.</td></tr>'}</tbody></table></div><p id="users-result" role="status"></p></section>`;
  $('#show-deleted-users').onchange=()=>{showDeletedUsers=$('#show-deleted-users').checked;renderUsers();};
  $('#users-create').onclick=()=>$('#new-user').click();
  document.querySelectorAll('[data-manage-user]').forEach(button=>button.onclick=async()=>{if(!actor){$('#users-result').textContent='Choose an active human user first.';return;}const action=button.dataset.action;if(action==='delete'&&!confirm('Delete this local profile across projects and recover its unfinished work? Running managed workers receive Stop first. Historical authorship is preserved and the profile can be restored.'))return;button.disabled=true;try{const result=await api('user_manage',{actor,id:Number(button.dataset.manageUser),action});await loadProjects();$('#users-result').textContent=result.pending?result.next_step:action==='delete'?'User deleted; '+(result.tasks_recovered||0)+' unfinished task(s) recovered; history preserved.':'User restored.';}catch(e){$('#users-result').textContent=e.message;button.disabled=false;}});
}


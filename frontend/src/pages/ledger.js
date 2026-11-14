import {ledger,jobs} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {LedgerBatchStatus,LedgerEntryStatus,LedgerEntryType} from '../constants/enums.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';

const isAdmin=()=>session.user.role==='admin';
const canWrite=()=>session.user.role!=='viewer';
const badge=(text,cls='')=>'<span class="status '+esc(cls)+'">'+esc(text)+'</span>';
const monthLabel=m=>m.month+(m.sealed?'（已封账）':'（未封）');

export async function ledgerPage(id){
 const [accounts,allJobs]=await Promise.all([ledger.accounts(),jobs.list()]);
 workspace.ledgerAccounts=accounts;
 const current=id?accounts.find(a=>String(a.id)===String(id)):accounts[0];
 const approved=allJobs.filter(j=>j.approved&&j.status==='completed');
 if(!current){
  return heading('05 / ASSESSMENT LEDGER','节能考核账本','开户时固定线体、批准的归因结论与逐日预测依据；按日期登记预计、实测电耗与差额，封账后只接受冲正。')+accountListPanel(accounts,approved,true);
 }
 const detail=await ledger.get(current.id);
 workspace.ledgerDetail=detail;
 return heading('05 / ASSESSMENT LEDGER','节能考核账本 · '+esc(current.line_name),esc(current.name)+'｜归因版本 '+esc(detail.basis_version)+'｜依据校验 '+esc(detail.basis_checksum.slice(0,18))+'…','<button class="button" id="refresh-ledger">刷新账本 ↻</button>')
 +accountListPanel(accounts,approved,false)
 +monthPanel(detail)
 +'<div class="report-grid"><section class="panel"><div class="section-title"><h2>登记实测批次</h2><span>待入账 → 确认入账</span></div>'
 +'<form id="batch-form"><label>批次幂等键（中断重试不重复）<input name="idem_key" required minlength="4" placeholder="例如 meter-20261114-01"></label><label>日账行（每行：日期,实测kWh）<textarea name="rows" rows="7" placeholder="2026-09-01,1102.5&#10;2026-09-02,1188" required></textarea></label><p class="hint">同日实测值相互冲突时整批拒绝；已存在的同值日期自动跳过、不重复累计；已封月份拒绝入账。</p><button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>登记为待入账</button></form>'
 +'<div class="section-title" style="margin-top:30px"><h2>迟到/更正冲正</h2></div><form id="reversal-form"><label>原始业务日期<input name="ref_day" required placeholder="YYYY-MM-DD"></label><label>更正后实测 kWh<input name="actual_kwh" type="number" step="any" min="0" required></label><label>冲正幂等键<input name="idem_key" required minlength="4" placeholder="例如 fix-20260901-01"></label><label>说明（可选）<input name="note" maxlength="300" placeholder="电表复核"></label><p class="hint">冲正只计入当前未封月份，原始日账、原月份快照与已发简报保持原值；每条原始日账限一次冲正。</p><button type="submit" '+(canWrite()?'':'disabled')+'>登记冲正</button></form></section>'
 +'<section><div class="section-title"><h2>入账批次</h2><span>'+detail.batches.length+' 批</span></div><div class="schedule-list">'+(detail.batches.map(batchCard(detail)).join('')||'<p class="empty">尚无批次。左侧登记后在此确认入账或丢弃。</p>')+'</div>'
 +'<section class="panel"><div class="section-title"><h2>日账明细</h2><span>'+detail.entries.length+' 行 · 可追溯原始日账与冲正</span></div><div class="table-wrap"><table><thead><tr><th>业务/登记日期</th><th>类型</th><th>归属月</th><th>状态</th><th>预计 kWh</th><th>实测 kWh</th><th>差额</th><th>关联</th></tr></thead><tbody>'
 +detail.entries.map(e=>'<tr><td>'+esc(e.day)+'</td><td>'+esc(LedgerEntryType[e.entry_type]||e.entry_type)+'</td><td>'+esc(e.book_month)+'</td><td>'+badge(LedgerEntryStatus[e.status]||e.status,e.status)+'</td><td>'+esc(fmt(e.predicted_kwh))+'</td><td>'+esc(fmt(e.actual_kwh))+'</td><td>'+esc(fmt(e.variance_kwh))+'</td><td>'+(e.ref_day?('冲正自 '+esc(e.ref_day)+' #'+esc(e.reversal_of_id)):'原始 #'+esc(e.id))+'</td></tr>').join('')
 +'</tbody></table></div></section></section></div>';
}

function accountListPanel(accounts,approved,emptyHint){
 return '<section class="panel"><div class="section-title"><h2>账本开户</h2><span>'+accounts.length+' 个账户</span></div><div class="batch-list">'
 +(accounts.map(a=>'<button class="batch-item" data-account="'+a.id+'"><strong>'+esc(a.name)+'</strong><span>'+esc(a.line_name)+' · 归因 '+esc(a.basis_version)+'</span><small>SHA256 '+esc(a.basis_checksum.slice(0,18))+'…</small></button>').join('')||'<p class="empty">'+(emptyHint?'尚未开立考核账本。':'')+'</p>')+'</div>'
 +(isAdmin()?'<form id="open-form"><div class="section-title"><h2>新开考核账本</h2></div><label>账本名称<input name="name" required minlength="2" placeholder="例如：冲压P01 · 11月考核账本"></label><label>线体编码<input name="line_code" required placeholder="例如 冲压P01"></label><label>线体名称<input name="line_name" placeholder="例如 冲压一线"></label><label>批准的归因结论<select name="job_id" required>'+approved.map(j=>'<option value="'+j.id+'">'+esc(j.name)+' · #'+j.id+'</option>').join('')+'</select></label><p class="hint">开户即冻结该批准结果的逐日预测、参数与指标；同一归因结论与线体不可重复开户。</p><button type="submit" class="primary" '+(approved.length?'':'disabled')+'>固定口径并开户</button></form>':'<p class="hint">仅业务负责人可开立账本与月度封账。</p>')+'</section>';
}

function monthPanel(detail){
 const sealedSet=new Set(detail.months.filter(m=>m.sealed).map(m=>m.month));
 return '<section class="panel"><div class="section-title"><h2>月度合计与封账</h2><span>已封月份回读快照</span></div>'
 +(detail.totals.length?'<div class="table-wrap"><table><thead><tr><th>月份</th><th>封账状态</th><th>原始日账</th><th>冲正</th><th>预计合计</th><th>实测合计</th><th>差额合计</th><th>归因版本</th><th>可追日账</th>'+(isAdmin()?'<th>操作</th>':'')+'</tr></thead><tbody>'
 +detail.totals.map(t=>'<tr><td>'+esc(t.month)+'</td><td>'+(t.sealed?badge('已封账','posted'):badge('未封账','pending'))+'</td><td>'+t.entry_count+'</td><td>'+t.reversal_count+'</td><td>'+esc(fmt(t.predicted_total))+'</td><td>'+esc(fmt(t.actual_total))+'</td><td>'+esc(fmt(t.variance_total))+'</td><td><small>'+esc(t.basis_version)+'</small></td><td>#'+esc((t.entry_ids||[]).join(', #'))+'</td>'+(isAdmin()?'<td>'+(t.sealed?'<small>已锁定</small>':'<button class="button small" data-seal="'+esc(t.month)+'">封账</button>')+'</td>':'')+'</tr>').join('')
 +'</tbody></table></div>':'<div class="empty">尚无已入账日账，封账前需先确认入账。</div>')
 +(isAdmin()?'<form id="seal-form" class="inline-form"><label>封账月份<input name="month" required placeholder="YYYY-MM" pattern="[0-9]{4}-[0-9]{2}"></label><button class="primary">封账生效</button></form><p class="hint">封账与入账并发以封账生效为界：生效后该月待入账批次只能拒绝，迟到与更正仅能在未封月份记冲正。</p>':'')+'</section>';
}

function batchCard(detail){
 return b=>{
  const cls=b.status==='posted'?'posted':(b.status==='rejected'?'failed':'pending');
  return '<article><div><strong>'+esc(b.kind==='reversal'?'冲正批次 · ':'入账批次 · ')+'#'+b.id+' '+badge(LedgerBatchStatus[b.status]||b.status,cls)+'</strong><p>'+esc(b.note||'')+(b.reject_reason?'<br><small class="warning-text">'+esc(b.reject_reason)+'</small>':'')+'</p><small>'+esc(b.idem_key)+' · '+date(b.created_at)+'</small></div>'
  +(b.status==='pending'&&canWrite()?'<div><button class="button small primary" data-confirm="'+b.id+'">确认入账</button><button class="button small" data-discard="'+b.id+'">丢弃</button></div>':'')+'</article>';
 };
}

function parseRows(text){
 const rows=[];
 for(const line of text.split(/\r?\n/)){
  const part=line.trim();
  if(!part)continue;
  const [day,kwh]=part.split(',').map(s=>s&&s.trim());
  if(!day||kwh===undefined||Number.isNaN(Number(kwh)))throw new Error('行格式错误：'+part+'（应为 日期,实测kWh）');
  rows.push({day,actual_kwh:Number(kwh)});
 }
 if(!rows.length)throw new Error('请至少填写一行日账');
 return rows;
}

export function bindLedger(refresh){
 const detail=workspace.ledgerDetail;
 document.querySelector('#refresh-ledger')?.addEventListener('click',refresh);
 document.querySelectorAll('[data-account]').forEach(btn=>btn.addEventListener('click',()=>{location.hash='#/ledger/'+btn.dataset.account;}));
 const openForm=document.querySelector('#open-form');
 if(openForm)submit(openForm,async f=>{
  await ledger.open({name:f.get('name'),line_code:f.get('line_code'),line_name:f.get('line_name')||f.get('line_code'),job_id:Number(f.get('job_id'))});
  notify('账本已开立，归因口径已冻结');await refresh();
 });
 if(!detail)return;
 const id=detail.id;
 submit(document.querySelector('#batch-form'),async f=>{
  const b=await ledger.submit(id,{idem_key:f.get('idem_key'),rows:parseRows(f.get('rows'))});
  notify('批次 #'+b.id+' 已登记为待入账，等待确认');await refresh();
 });
 submit(document.querySelector('#reversal-form'),async f=>{
  await ledger.reverse(id,{idem_key:f.get('idem_key'),ref_day:f.get('ref_day'),actual_kwh:Number(f.get('actual_kwh')),note:f.get('note')||''});
  notify('冲正已登记在当前未封月份，原始月份保持原值');await refresh();
 });
 const sealForm=document.querySelector('#seal-form');
 if(sealForm)submit(sealForm,async f=>{
  await ledger.seal(id,f.get('month'));notify('封账已生效，该月份快照已锁定');await refresh();
 });
 document.querySelectorAll('[data-seal]').forEach(btn=>btn.onclick=async()=>{try{await ledger.seal(id,btn.dataset.seal);notify(btn.dataset.seal+' 封账已生效');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-confirm]').forEach(btn=>btn.onclick=async()=>{try{const b=await ledger.confirm(id,btn.dataset.confirm);notify(b.status==='rejected'?'批次被拒绝：'+b.reject_reason:'批次已入账');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-discard]').forEach(btn=>btn.onclick=async()=>{try{await ledger.discard(id,btn.dataset.discard);notify('待入账批次已丢弃');await refresh();}catch(e){notify(e.message,true);}});
}

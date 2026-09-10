import { useEffect, useState } from 'react';
import { createInvestigationWorkspace } from '../../../src/services/investigationCreation';
import { getResourceEdit, listResourceEdits, listResources, openResourceEdit, readResource, saveResourceEdit, updateResourceEdit } from './resourceManagement';
import type { Change, Entry, Exemption, ResourceContent, ResourceEdit, ResourceKind, ResourceSummary, Rule } from './resourceManagement';
import './resources.css';

const stageNames: Record<string,string> = {image_evidence:'图片',video_frame_evidence:'视频 / OCR / ASR',comment_audit:'评论',fusion_audit:'融合研判'};
const same = (a:unknown,b:unknown) => JSON.stringify(a) === JSON.stringify(b);
const message = (e:unknown) => e instanceof Error ? e.message : '操作未完成，请重试。';

function changesBetween(edit:ResourceEdit, body:ResourceContent):Change[] {
  const old = edit.content;
  const changes:Change[] = [];
  const metadata:Record<string,unknown> = {};
  for (const key of edit.kind === 'lexicon' ? ['title','risk_label'] as const : ['name','domain','audit_goal'] as const) {
    if (body[key] !== old[key]) metadata[key] = body[key];
  }
  if (Object.keys(metadata).length) changes.push({operation:'set_metadata',values:metadata});
  if (edit.kind === 'lexicon') {
    for (const entry of old.entries || []) if (!(body.entries || []).some(e => e.id === entry.id)) {
      // Removing a main entry removes its variants on the server as well.
      if (entry.kind !== 'variant' || body.entries?.some(e => e.id === entry.parent_id)) changes.push({operation:'remove_entry',target_id:entry.id});
    }
    for (const entry of body.entries || []) {
      const previous = old.entries?.find(e => e.id === entry.id);
      if (!same(entry,previous)) changes.push({operation:'upsert_entry',target_id:previous?.id,values:{...entry}});
    }
  } else {
    if (!same(old.general_exemptions,body.general_exemptions)) changes.push({operation:'set_exemptions',values:{general_exemptions:body.general_exemptions}});
    for (const category of body.categories || []) for (const rule of category.rules) {
      const previous = old.categories?.flatMap(c => c.rules).find(r => r.rule_id === rule.rule_id);
      if (!same(rule,previous)) changes.push({operation:previous ? 'update_rule':'add_rule',target_id:previous ? rule.rule_id:category.category_id,values:{...rule}});
    }
    for (const rule of old.categories?.flatMap(c => c.rules) || []) if (!body.categories?.some(c => c.rules.some(r => r.rule_id === rule.rule_id))) changes.push({operation:'remove_rule',target_id:rule.rule_id});
  }
  return changes;
}

function Exemptions({items,onChange}:{items:Exemption[];onChange?:(items:Exemption[])=>void}) {
  return <div className="resource-exemptions">{items.map((item,index)=><div key={item.exemption_id}>
    <label>豁免名称<input value={item.name} readOnly={!onChange} onChange={e=>onChange?.(items.map((v,i)=>i===index?{...v,name:e.target.value}:v))}/></label>
    <label>豁免条件<textarea value={item.condition} readOnly={!onChange} onChange={e=>onChange?.(items.map((v,i)=>i===index?{...v,condition:e.target.value}:v))}/></label>
    {onChange && <button onClick={()=>onChange(items.filter((_,i)=>i!==index))}>删除此豁免</button>}
  </div>)}{onChange && <button onClick={()=>onChange([...items,{exemption_id:'ex.'+crypto.randomUUID(),name:'新豁免',condition:''}])}>添加豁免</button>}</div>;
}

export function ResourceContentView({content,onChange}:{content:ResourceContent;onChange?:(body:ResourceContent)=>void}) {
  const set = (values:Partial<ResourceContent>) => onChange?.({...content,...values});
  const updateEntry = (id:string, values:Partial<Entry>) => set({entries:content.entries?.map(e=>e.id===id?{...e,...values}:e)});
  const addEntry = (kind:Entry['kind'], parent_id='') => set({entries:[...(content.entries||[]),{id:'entry:'+crypto.randomUUID(),term:'',kind,parent_id,enabled:true,platform:'全平台',match_type:kind==='tag'?'平台标签':'黑话词',risk_level:'中',note:''}]});
  const updateRule = (id:string, values:Partial<Rule>) => set({categories:content.categories?.map(c=>({...c,rules:c.rules.map(r=>r.rule_id===id?{...r,...values}:r)}))});
  return <div className="resource-content">
    <label>名称<input value={content.title ?? content.name ?? ''} readOnly={!onChange} onChange={e=>set(content.entries?{title:e.target.value}:{name:e.target.value})}/></label>
    {content.entries ? <>
      <label>词库说明<input value={content.risk_label||''} readOnly={!onChange} onChange={e=>set({risk_label:e.target.value})}/></label>
      <p className="resource-hint">任务只搜索启用的主词；变体和标签保留在词库中。</p>
      {content.entries.filter(e=>e.kind!=='variant').map(entry=><div className="resource-entry" key={entry.id}>
        <label>{entry.kind==='tag'?'标签':'主词'}<input value={entry.term} readOnly={!onChange} onChange={e=>updateEntry(entry.id,{term:e.target.value})}/></label>
        <label className="resource-check"><input type="checkbox" checked={entry.enabled} disabled={!onChange} onChange={e=>updateEntry(entry.id,{enabled:e.target.checked})}/>启用</label>
        {content.entries?.filter(e=>e.parent_id===entry.id).map(v=><div key={v.id} className="resource-variant"><label>变体<input value={v.term} readOnly={!onChange} onChange={e=>updateEntry(v.id,{term:e.target.value})}/></label>{onChange&&<button onClick={()=>set({entries:content.entries?.filter(e=>e.id!==v.id)})}>删除变体</button>}</div>)}
        {onChange&&<div className="resource-actions">{entry.kind==='main'&&<button onClick={()=>addEntry('variant',entry.id)}>添加变体</button>}<button onClick={()=>set({entries:content.entries?.filter(e=>e.id!==entry.id&&e.parent_id!==entry.id)})}>删除{entry.kind==='tag'?'标签':'主词及变体'}</button></div>}
      </div>)}
      {onChange&&<div className="resource-actions"><button onClick={()=>addEntry('main')}>添加主词</button><button onClick={()=>addEntry('tag')}>添加标签</button></div>}
    </> : <>
      <label>领域<input value={content.domain||''} readOnly={!onChange} onChange={e=>set({domain:e.target.value})}/></label>
      <label>研判目标<textarea value={content.audit_goal||''} readOnly={!onChange} onChange={e=>set({audit_goal:e.target.value})}/></label>
      <h4>通用豁免</h4><Exemptions items={content.general_exemptions||[]} onChange={onChange?items=>set({general_exemptions:items}):undefined}/>
      {content.categories?.map(category=><section key={category.category_id}><h4>{category.name}</h4>{category.rules.map(rule=><details key={rule.rule_id} className="resource-rule"><summary>{rule.name}{!rule.enabled?'（停用）':''}</summary>
        <label>规则名称<input value={rule.name} readOnly={!onChange} onChange={e=>updateRule(rule.rule_id,{name:e.target.value})}/></label>
        <label>命中条件<textarea value={rule.hit_condition} readOnly={!onChange} onChange={e=>updateRule(rule.rule_id,{hit_condition:e.target.value})}/></label>
        <label>风险等级<select value={rule.suggested_risk_level} disabled={!onChange} onChange={e=>updateRule(rule.rule_id,{suggested_risk_level:e.target.value})}><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label>
        <div className="resource-actions">{Object.entries(stageNames).map(([stage,label])=><label className="resource-check" key={stage}><input type="checkbox" checked={rule.application_stages.includes(stage)} disabled={!onChange} onChange={e=>updateRule(rule.rule_id,{application_stages:e.target.checked?[...rule.application_stages,stage]:rule.application_stages.filter(s=>s!==stage)})}/>{label}</label>)}</div>
        <label>研判说明<textarea value={rule.adjudication_notes} readOnly={!onChange} onChange={e=>updateRule(rule.rule_id,{adjudication_notes:e.target.value})}/></label>
        <label className="resource-check"><input type="checkbox" checked={rule.enabled} disabled={!onChange} onChange={e=>updateRule(rule.rule_id,{enabled:e.target.checked})}/>启用规则</label>
        <Exemptions items={rule.rule_exemptions} onChange={onChange?items=>updateRule(rule.rule_id,{rule_exemptions:items}):undefined}/>
        {onChange&&<button onClick={()=>set({categories:content.categories?.map(c=>({...c,rules:c.rules.filter(r=>r.rule_id!==rule.rule_id)})).filter(c=>c.rules.length)})}>删除规则</button>}
      </details>)}</section>)}
    </>}
  </div>;
}

export function ResourceEditCard({session,edit,onRefresh,onUse}:{session:string;edit:ResourceEdit;onRefresh:()=>void;onUse?:(edit:ResourceEdit)=>void}) {
  const [working,setWorking]=useState(edit);
  const [body,setBody]=useState(edit.content);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  useEffect(()=>{
    if(edit.edit_id===working.edit_id&&!same(body,working.content)&&edit.version!==working.version) {
      setError('内容已在其他地方更新。你的修改仍保留，请先重新读取并核对差异。');
      return;
    }
    setWorking(edit);setBody(edit.content);
  },[edit.edit_id,edit.version]);
  const changed=!same(body,working.content);
  const apply = async (mode?:'new'|'update'|'copy') => {
    setBusy(true);setError('');setNotice('');
    try {
      const current=changed?await updateResourceEdit(session,working,changesBetween(working,body)):working;
      setWorking(current);setBody(current.content);
      if(mode) { const receipt=await saveResourceEdit(session,current,mode);setNotice(`已保存，正式版本 ${receipt.version}`); }
      else setNotice('已保留本次修改，尚未正式保存。');
      onRefresh();
    } catch(e) {setError(message(e));} finally {setBusy(false);}
  };
  return <details className="resource-card"><summary><strong>{edit.content.title||edit.content.name}</strong><span>{changed?'有未保留的修改':edit.saved?'已保存':'仅本次使用'}</span></summary>
    <fieldset disabled={busy}><ResourceContentView content={body} onChange={setBody}/></fieldset>
    {changed&&<p className="resource-hint">本次更改 {changesBetween(working,body).length} 项；保存前请检查上方内容。</p>}
    {error&&<p role="alert">{error} <button disabled={busy} onClick={()=>{setWorking(edit);setBody(edit.content);setError('');onRefresh();}}>丢弃本地修改并重新读取</button></p>}
    {notice&&<p role="status">{notice}</p>}
    <div className="resource-actions">
      <button disabled={busy||!changed} onClick={()=>void apply()}>保留本次修改</button>
      <button disabled={busy} onClick={()=>void apply(edit.source.id&&edit.source.editable?'update':edit.source.id?'copy':'new')}>{busy?'处理中…':'保存到资源库'}</button>
      <button disabled={busy} onClick={()=>void apply('copy')}>另存一份</button>
      {onUse&&<button disabled={busy||changed} onClick={()=>onUse(edit)}>用于任务</button>}
    </div>
    {edit.search_terms&&<p className="resource-hint">实际搜索主词：{edit.search_terms.join('、')||'暂无启用主词'}</p>}
  </details>;
}

export function SessionResources({session,refreshKey,onUse}:{session:string;refreshKey:string;onUse:(edit:ResourceEdit)=>void}) {
  const [items,setItems]=useState<ResourceEdit[]>([]);
  const [error,setError]=useState('');
  const [revision,setRevision]=useState(0);
  useEffect(()=>{let live=true;listResourceEdits(session).then(r=>{if(live){setItems(r.items);setError('');}}).catch(e=>{if(live)setError(message(e));});return()=>{live=false;};},[session,refreshKey,revision]);
  if(!items.length&&!error)return null;
  return <section className="session-resources" aria-label="本次规则与词库"><h3>本次规则与词库</h3>{error&&<p role="alert">{error}<button onClick={()=>setRevision(v=>v+1)}>重试读取</button></p>}{items.map(edit=><ResourceEditCard key={edit.edit_id} session={session} edit={edit} onRefresh={()=>setRevision(v=>v+1)} onUse={onUse}/>)}</section>;
}

export function ResourceLibrary({kind,sessionId,onDiscuss}:{kind:ResourceKind;sessionId?:string;onDiscuss:(text:string)=>void}) {
  const [items,setItems]=useState<ResourceSummary[]>([]);
  const [selected,setSelected]=useState<ResourceSummary>();
  const [content,setContent]=useState<ResourceContent>();
  const [edit,setEdit]=useState<ResourceEdit>();
  const [session,setSession]=useState(sessionId||'');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [revision,setRevision]=useState(0);
  useEffect(()=>{let live=true;(async()=>{const all:ResourceSummary[]=[];let more=true;while(more){const r=await listResources(kind,all.length);all.push(...r.items);more=r.has_more&&r.items.length>0;}if(live)setItems(all);})().catch(e=>{if(live)setError(message(e));});return()=>{live=false;};},[kind,revision]);
  const select = async(item:ResourceSummary)=>{setBusy(true);setError('');setSelected(item);setEdit(undefined);setContent(undefined);try{setContent((await readResource(kind,item.id)).content);}catch(e){setError(message(e));}finally{setBusy(false);}};
  const open = async()=>{if(!selected)return;setBusy(true);setError('');try{let id=session;if(!id){id=(await createInvestigationWorkspace('resource-manager:'+crypto.randomUUID())).workspace_session_id;setSession(id);}setEdit(await openResourceEdit(id,kind,selected.id));}catch(e){setError(message(e));}finally{setBusy(false);}};
  const refresh = ()=>{setRevision(v=>v+1);if(edit)void getResourceEdit(session,edit.edit_id).then(setEdit).catch(e=>setError(message(e)));};
  return <section className="resource-library"><div className="resource-actions"><h2>{kind==='ruleset'?'审核规则':'词库'}</h2><button onClick={()=>onDiscuss(kind==='ruleset'?'帮我生成一套新的审核规则，先展示给我。':'帮我生成一套完整词库，先展示给我。')}>在对话中创建</button></div>
    {error&&<p role="alert">{error}<button onClick={refresh}>重新读取</button></p>}
    <div className="resource-library-layout"><nav aria-label="正式资源列表">{items.map(item=><button key={item.id} disabled={busy} aria-pressed={selected?.id===item.id} onClick={()=>void select(item)}>{item.title}{!item.editable?'（只读）':''}</button>)}{!items.length&&<p>暂无资源</p>}</nav>
      <div>{busy&&<p role="status">读取中…</p>}{edit?<ResourceEditCard key={edit.edit_id} session={session} edit={edit} onRefresh={refresh}/>:content&&<><ResourceContentView content={content}/><div className="resource-actions"><button disabled={busy} onClick={()=>void open()}>{selected?.editable?'编辑此资源':'基于此资源另存'}</button><button onClick={()=>onDiscuss(`请读取${kind==='ruleset'?'规则':'词库'}“${selected?.title}”（${selected?.id}），我想继续修改它。`)}>在对话中修改</button></div></>}</div>
    </div>
  </section>;
}

import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';
import { join } from 'node:path';

const probe=(await import('node:net')).createServer();
await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
const freePort=probe.address().port;
await new Promise(resolve=>probe.close(resolve));
const server=await createServer({server:{host:'127.0.0.1',port:freePort,strictPort:true}});
await server.listen();
const base=`http://127.0.0.1:${server.httpServer.address().port}`;
const api=async(path,method='GET',body)=>{
  const response=await fetch(process.env.VITE_API_PROXY_TARGET+path,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  assert.equal(response.ok,true,await response.clone().text());return response.json();
};
const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined});
try {
  const workspace=await api('/api/investigation-workspaces','POST',{workspace_key:'browser-resource'});
  const root=`/api/investigation-workspaces/${encodeURIComponent(workspace.workspace_session_id)}`;
  const edit=await api(root+'/resource-edits/lexicon','POST',{content:{title:'民族讨论浏览器验收',entries:[{id:'main',term:'民族文化',kind:'main'},{id:'variant',term:'文化变体',kind:'variant',parent_id:'main'}]}});
  const saved=await api(root+`/resource-edits/${encodeURIComponent(edit.edit_id)}/save`,'POST',{expected_version:1,mode:'new',operation_id:'browser-seed'});
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  const errors=[];page.on('pageerror',e=>{errors.push(String(e));console.error(e);});
  const selectAndEdit=async()=>{
    await page.goto(base+'/tests/resourceLifecycle.html');
    await page.getByRole('button',{name:'民族讨论浏览器验收',exact:true}).click();
    await page.getByRole('button',{name:'编辑此资源',exact:true}).click();
    await page.locator('.resource-card > summary').click();
  };
  await selectAndEdit();
  await page.getByLabel('主词',{exact:true}).fill('民族语言文化');
  await page.getByRole('button',{name:'保存到资源库',exact:true}).click();
  await page.getByRole('status').filter({hasText:'已保存'}).waitFor();
  let formal=await api('/api/resource-library/lexicon/'+saved.resource_id);
  assert.deepEqual(formal.search_terms,['民族语言文化']);
  assert.equal(formal.content.entries.find(e=>e.id==='variant').parent_id,'main');
  await selectAndEdit();
  assert.equal(await page.getByLabel('主词',{exact:true}).inputValue(),'民族语言文化');
  assert.equal(await page.getByLabel('变体',{exact:true}).inputValue(),'文化变体');
  // Another session writes after this browser has loaded its base version.
  const external=await api(root+'/resource-edits/open','POST',{kind:'lexicon',resource_id:saved.resource_id});
  const updated=await api(root+`/resource-edits/${encodeURIComponent(external.edit_id)}`,'PATCH',{expected_version:1,changes:[{operation:'set_metadata',values:{risk_label:'另一个编辑器的更改'}}]});
  await api(root+`/resource-edits/${encodeURIComponent(external.edit_id)}/save`,'POST',{expected_version:updated.version,mode:'update',operation_id:'external-save'});
  await page.getByLabel('主词',{exact:true}).fill('冲突后仍保留的本地词');
  await page.getByRole('button',{name:'保存到资源库',exact:true}).click();
  await page.getByRole('alert').waitFor();
  assert.equal(await page.getByLabel('主词',{exact:true}).inputValue(),'冲突后仍保留的本地词');
  formal=await api('/api/resource-library/lexicon/'+saved.resource_id);
  assert.deepEqual(formal.search_terms,['民族语言文化']);
  await page.screenshot({path:join(process.env.RESOURCE_EVIDENCE,'browser-conflict.png'),fullPage:true});
  // A committed save whose HTTP response was lost is recovered through its receipt.
  await selectAndEdit();
  await page.getByLabel('主词',{exact:true}).fill('保存回执恢复测试');
  let dropped=false;
  await page.route('**/resource-edits/*/save',async route=>{
    if(!dropped){dropped=true;await route.fetch();await route.abort('failed');}else await route.continue();
  });
  await page.getByRole('button',{name:'保存到资源库',exact:true}).click();
  await page.getByRole('alert').waitFor();
  const beforeRetry=await api('/api/resource-library/lexicon/'+saved.resource_id);
  await page.getByRole('button',{name:'保存到资源库',exact:true}).click();
  await page.getByRole('status').filter({hasText:'已保存'}).waitFor();
  formal=await api('/api/resource-library/lexicon/'+saved.resource_id);
  assert.equal(formal.version,beforeRetry.version);
  assert.deepEqual(formal.search_terms,['保存回执恢复测试']);
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:join(process.env.RESOURCE_EVIDENCE,'browser-mobile.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  assert.deepEqual(errors,[]);
  console.log('Browser passed: real API save/reload, variant identity, conflict retention, lost-response receipt recovery, mobile layout; zero task starts.');
} finally {await browser.close();await server.close();}

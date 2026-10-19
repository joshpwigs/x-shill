// Optional browser integration test. Run against a disposable DEMO instance only.
// Requires Playwright; BROWSER_PATH can point at a system Chromium installation.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
async function main(){
  const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const base=process.env.SHILL_TEST_URL||'http://127.0.0.1:8787';
  const h=await (await page.request.get(base+'/api/health')).json();
  assert.equal(h.mode,'demo','Browser test only runs against a demo instance');
  await page.goto(base);
  await page.getByRole('heading',{name:'Find your next good conversation.'}).waitFor();
  assert.equal(await page.locator('.opportunity').count(),2);
  await page.getByRole('button',{name:'All imported',exact:true}).click();
  assert.equal(await page.locator('.opportunity').count(),4);
  await page.getByRole('button',{name:'Relevant',exact:true}).click();
  fs.mkdirSync('/tmp/signal-garden-screenshots',{recursive:true});
  await page.screenshot({path:'/tmp/signal-garden-screenshots/opportunities.png',fullPage:true});
  await page.getByRole('link',{name:'Content studio'}).click();
  await page.getByLabel('Format',{exact:true}).selectOption('question');
  await page.getByLabel('One clear idea').fill('welcoming curious newcomers '+Date.now());
  await page.getByRole('button',{name:'Generate 3 options'}).click();
  await page.locator('.draft-card').first().waitFor();
  assert.equal(await page.locator('.draft-card').count()>=3,true);
  await page.getByRole('button',{name:'Open draft →'}).first().click();
  await page.locator('#editor[open]').waitFor();
  await page.getByLabel('I checked every factual claim',{exact:false}).check();
  await page.getByLabel('I reviewed the conversation context',{exact:false}).check();
  await page.getByRole('button',{name:'Approve exact content',exact:true}).click();
  await page.getByRole('button',{name:'Export',exact:true}).waitFor();
  await page.getByRole('button',{name:'Export',exact:true}).click();
  await page.getByRole('heading',{name:'Your reviewed manual export'}).waitFor();
  assert.match(await page.locator('#utility-content').innerText(),/FICTIONAL DEMO/);
  await page.locator('#utility [data-action="close-utility"]').click();
  await page.getByRole('button',{name:'Schedule original',exact:true}).click();
  await page.getByRole('button',{name:'Schedule this approved original'}).click();
  await page.waitForFunction(()=>document.querySelector('#editor-content')?.textContent.includes('Job: pending'));
  // An edit must revoke both approval and schedule from the real browser workflow.
  await page.getByLabel('Content',{exact:true}).fill('What makes a first contribution feel manageable?\n\nFictional demo · project team');
  await page.getByRole('button',{name:'Save changes',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#editor-content')?.textContent.includes('VERSION 2'));
  assert.equal(await page.getByRole('button',{name:'Export',exact:true}).isDisabled(),true);
  await page.locator('#editor [data-action="close-editor"]').click();
  for(const [name,heading] of [['Review queue','Good judgment is part of the process.'],['Calendar','A little consistency goes a long way.'],['Project knowledge','Keep the facts close.'],['Browser campaign','Run a focused browser campaign.'],['Analytics','Learn from the conversation.'],['System & audit log','A clear view behind the scenes.']]){
    await page.getByRole('link',{name,exact:false}).click();
    await page.getByRole('heading',{name:heading}).waitFor();
  }
  await page.getByRole('button',{name:'Load fictional analytics examples'}).click();
  await page.getByRole('link',{name:'Analytics',exact:false}).click();
  await page.getByRole('heading',{name:'Learn from the conversation.'}).waitFor();
  await page.waitForFunction(()=>document.querySelector('#content')?.textContent.includes('Fictional simulation'));
  await page.screenshot({path:'/tmp/signal-garden-screenshots/analytics.png',fullPage:true});
  await page.getByRole('button',{name:'Emergency stop',exact:false}).click();
  await page.waitForFunction(()=>document.querySelector('#mode-banner').textContent.includes('Emergency stop is active'));
  await page.getByRole('button',{name:'Resume queue',exact:false}).click();
  await page.waitForFunction(()=>!document.querySelector('#mode-banner').textContent.includes('Emergency stop is active'));
  // Untrusted HTML must display as text; no DOM nodes or executable instructions.
  await page.request.post(base+'/api/opportunities/import',{data:{opportunities:[{id:'browser-xss',author:'SecurityReader',text:'<img src=x onerror="window.XSS=true"> How can community onboarding improve?',url:'https://x.com/SecurityReader/status/123456789',posted_at:1700000000,thread_id:'123456789',fictional:true}]}});
  await page.getByRole('link',{name:'Opportunities',exact:false}).click();
  await page.getByRole('button',{name:'Refresh',exact:false}).click();
  await page.getByRole('button',{name:'All imported',exact:true}).click();
  assert.equal(await page.locator('.opportunity img').count(),0);
  assert.equal(await page.evaluate(()=>window.XSS),undefined);
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'Relevant',exact:true}).click();
  await page.screenshot({path:'/tmp/signal-garden-screenshots/mobile.png',fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'No page-level mobile overflow');
  assert.deepEqual(errors,[]);
  await browser.close();
  console.log('Browser smoke passed: discovery, generation, approval, export, scheduling, invalidation, all views, analytics, stop/resume, XSS, mobile layout.');
}
main().catch(e=>{console.error(e);process.exit(1);});

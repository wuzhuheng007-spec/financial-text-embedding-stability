const {JSDOM}=require('jsdom');const fs=require('fs');const path=require('path');const assert=require('assert');
const root=path.resolve(__dirname,'../..'); const out=path.resolve(process.argv[2]);
(async()=>{
 const results=[];
 for(const language of ['zh','en']){
  const dom=new JSDOM(fs.readFileSync(path.join(out,`${language}.html`),'utf8'),{url:'http://localhost/',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window;w.HTMLElement.prototype.scrollIntoView=function(){};w.setInterval=()=>0;
  const now=new Date().toISOString();
  const item={item_key:'test-1',version:1,title:'合成样例：产品演示。非真实新闻。',content:'仅用于软件检查。',interest:true,focus_eligible:true,analysis_status:'REVIEW_REJECTED',source_url:'https://example.test/source',published_at_utc:now,received_at:now,event_labels:{entities:[],categories:[]}};
  const feed={states:[{provider:'moomoo',state:'DISCONNECTED',worker_alive:1,heartbeat_at:now,last_push_at:now},{provider:'jin10',state:'DISCONNECTED',worker_alive:0}],quotes:[],selection:[],symbols:[],news:[item],top10:[{...item,scope:'DIRECT_SUBJECT',evidence_phrase:'NVDA',tone:'UNKNOWN',claim_stage:'GENERAL',rank:{score:83}}],sentiment_observation:[]};
  w.fetch=async(url)=>({ok:true,json:async()=>String(url).includes('research')?{state:'READY',article_versions:0,chinese:{state:'READY',article_versions:3,scored_segments:7,rows:[],v43:{}}}:feed});
  for(const name of ['workspace_v15.js','personal_feed.js','research_sentiment_v4.js','i18n_v15.js'])w.eval(fs.readFileSync(path.join(root,'apps/quant_portal/static/core',name),'utf8'));
  await new Promise(r=>setTimeout(r,80));
  const doc=w.document;
  assert(doc.getElementById('panel-diagnostics').hidden);
  assert(doc.getElementById('panel-research-sentiment').hidden);
  assert(!doc.getElementById('panel-quotes').hidden);
  assert(doc.getElementById('feed-health-summary').textContent.includes(language==='zh'?'正在接收推送':'receiving push'));
  assert(!doc.querySelector('.top10-row').textContent.includes('GENERAL'));
  assert(!doc.querySelector('.top10-row').textContent.includes('83'));
  doc.querySelector('.top10-row').click();
  assert(!doc.getElementById('panel-news').hidden);
  assert(doc.getElementById('panel-news').open);
  assert(doc.querySelector('.analysis-unavailable'));
  assert(!doc.querySelector('.analysis-unavailable').open);
  assert.equal(doc.querySelectorAll('.analysis-card').length,0);
  doc.querySelector('[data-view="research"]').click();
  assert(!doc.getElementById('panel-research-sentiment').hidden);
  assert(doc.querySelector('.research-summary'));
  assert(!doc.querySelector('.research-audit').open);
  w.quantOpenPanel('chart');assert(!doc.getElementById('panel-chart').hidden);assert(doc.getElementById('panel-chart').open);
  results.push({language,check:'navigation_top10_missing_analysis_research_chart',result:'PASS',fixture:'SYNTHETIC',visual_layout:'NOT_RUN'});
  w.close();
 }
 fs.writeFileSync(path.join(out,'dom_results.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results));
})().catch(e=>{console.error(e);process.exit(1)});

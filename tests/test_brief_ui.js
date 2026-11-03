// Run with node tests/test_brief_ui.js. No browser or external data is needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('web/dashboard.js', 'utf8');
const dailyCode = source.slice(0, source.indexOf("document.addEventListener('click'"));
const stockCode = source.slice(source.indexOf('function stockHTML('));
const nodes = new Map();
const element = selector => {
  if (!nodes.has(selector)) nodes.set(selector, {innerHTML:'', textContent:'', value:'intraday', open:true, disabled:false,addEventListener:()=>{}});
  return nodes.get(selector);
};
const context = vm.createContext({
  assert, Date, Intl, Number, Set, Map, URLSearchParams,
  $:element, stored:(_,fallback)=>fallback,
  document:{querySelectorAll:()=>[]},
  setTimeout:callback=>callback(),setInterval:()=>{},
  esc:value=>String(value??'').replaceAll('<','&lt;').replaceAll('>','&gt;'),
  money:value=>value==null?'—':'₹'+value,
  stamp:value=>value||'Unavailable',
  list:items=>items?.length?'<ul>'+items.map(x=>'<li>'+x+'</li>').join('')+'</ul>':'',
  safeLink:()=>'', identity:()=>'',renderHistory:()=>{}, reports:[],
});
vm.runInContext(dailyCode+stockCode,context);

async function run(){
  const yesterday=new Date(Date.now()-86400000).toISOString();
  context.previous={id:'old',created_at:yesterday,mode:'intraday',symbols:['OLD'],recommendations:[],gaps:[]};
  vm.runInContext("dailyBriefs.intraday=previous;renderBrief('intraday');",context);
  const oldHTML=element('#brief-intraday').innerHTML;
  assert.match(oldHTML,/Previous brief/);
  let count=0;
  context.api=async()=>{
    count++;
    // Even while a scan progresses, the old cards stay in the same content node.
    assert.equal(element('#brief-intraday').innerHTML,oldHTML);
    return count===1?{status:'running',trace:[{detail:'New scan'}]}:{status:'error',error:'fixture failure'};
  };
  await vm.runInContext("pollBrief('failed','intraday')",context);
  assert.equal(element('#brief-intraday').innerHTML,oldHTML);
  assert.match(element('#brief-status-intraday').textContent,/Refresh failed/);
  assert.equal(element('#run-daily').disabled,false);

  context.next={...context.previous,id:'new',created_at:new Date().toISOString(),symbols:['NEW']};
  context.api=async path=>path.startsWith('/api/jobs/')?{status:'complete',report:context.next}:{reports:[]};
  await vm.runInContext("pollBrief('completed','intraday')",context);
  assert.doesNotMatch(element('#brief-intraday').innerHTML,/Previous brief/);
  assert.equal(vm.runInContext('dailyBriefs.intraday.id',context),'new');

  // A slow GET returning an older saved brief cannot replace the newer one.
  vm.runInContext("acceptBrief('intraday',previous)",context);
  assert.equal(vm.runInContext('dailyBriefs.intraday.id',context),'new');

  context.stock={symbol:'FIXTURE',name:'<script>bad</script>',sector:'Tech',status:'RESEARCH ONLY',score:42,
    metrics:{available:true,price:100,trend:'Bearish',rsi14:38.3,relative_volume:1.31},
    quote:{price:100,label:'PUBLIC · DELAY UNVERIFIED',as_of:yesterday},
    chart_structure:{levels:{support:90,resistance:110},patterns:[]},
    scenario:{entry:100,invalidation:90,target:105,risk_reward:.5},
    risks:['Price trend disagrees with a bullish investment thesis.'],news:[],gaps:['Broker missing']};
  const html=vm.runInContext("stockHTML(stock,0,'longterm')",context);
  assert.match(html,/DELAY UNVERIFIED/);
  assert.match(html,/Financials and valuation missing/);
  assert.match(html,/Reward \/ risk below 1/);
  assert.match(html,/Falling price trend/);
  assert.doesNotMatch(html,/<script>/);
  context.stock.status='WATCH · CONFIRM AT OPEN';
  context.stock.reasons=['Fixture catalyst'];
  context.stock.scenario.expires_at=yesterday;
  const oldCard=vm.runInContext('briefCard(stock,previous)',context);
  assert.match(oldCard,/PAST LEVELS/);assert.doesNotMatch(oldCard,/data-watch-symbol/);
  assert.doesNotMatch(vm.runInContext("stockHTML(stock,0,'intraday')",context),/data-watch-symbol/);
  context.health={...context.next,sector:'healthcare',recommendations:[context.stock]};
  context.energy={...context.next,sector:'energy',created_at:new Date(Date.now()+1000).toISOString(),recommendations:[]};
  vm.runInContext("activeSector='healthcare';dailyBriefs=sectorCache.healthcare={intraday:null,longterm:null};acceptBrief('intraday',health,'healthcare')",context);
  const sectorHTML=element('#brief-intraday').innerHTML;
  vm.runInContext("acceptBrief('intraday',energy,'energy')",context);
  assert.equal(element('#brief-intraday').innerHTML,sectorHTML,'A late response from another sector must not replace selected-sector cards');
  assert.equal(vm.runInContext('dailyBriefs.intraday.sector',context),'healthcare');
  console.log('Passed: retain during refresh/error, replace after success, reject older responses, preserve critical stock context.');
}
run().catch(e=>{console.error(e);process.exitCode=1;});

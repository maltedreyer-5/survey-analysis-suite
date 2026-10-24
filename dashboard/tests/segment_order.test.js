// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 Malte Dreyer
// Segments in small multiples follow the configured order of their dimension (export metadata),
// for every dimension; there is no fixed order for one dimension id.
// Run: node dashboard/tests/segment_order.test.js
const fs=require('fs'),vm=require('vm'),assert=require('assert');const dir=require('path').resolve(__dirname,'..');
const noop=new Proxy(function(){},{get:(t,k)=>k===Symbol.toPrimitive?()=>'':noop,apply:()=>noop,construct:()=>noop});
const ctx={console,addEventListener(){},document:noop,Plotly:noop,d3:noop,Alpine:noop,location:{hash:'',search:''},history:noop,navigator:{},setTimeout,URLSearchParams};
ctx.window=ctx;vm.createContext(ctx);
for(const f of ['i18n.js','charts.js']) vm.runInContext(fs.readFileSync(dir+'/js/'+f,'utf8'),ctx);
const r=vm.runInContext(`(()=>{
  const segments={age:{values:[{key:'u25'},{key:'25_39'},{key:'40plus'}]}, size:{values:['large','small']}};
  const vals=[{key:'40plus'},{key:'u25'},{key:'other'},{key:'25_39'}], labels=['40+','<25','?','25-39'], ns=[50,5,7,30];
  const a=sortSegmentData(vals,labels,ns,'age',segments);
  const s=sortSegmentData([{key:'small'},{key:'large'}],['S','L'],[1,2],'size',segments);
  const n=sortSegmentData(vals,labels,ns,'unknown_dim',segments);
  return {age:a.sortedLabels, size:s.sortedLabels, byN:n.sortedLabels, order:configuredSegmentOrder(segments,'size')};})()`,ctx);
// Plotly draws the last element on top: the configured first value is last, unknown keys first (bottom)
assert.strictEqual(JSON.stringify(r.age),JSON.stringify(['?','40+','25-39','<25']));
assert.strictEqual(JSON.stringify(r.size),JSON.stringify(['S','L']));            // configured: large, small -> large on top
assert.strictEqual(JSON.stringify(r.byN),JSON.stringify(['<25','?','25-39','40+']));   // no configured order: by n, largest on top
assert.strictEqual(JSON.stringify(r.order),JSON.stringify(['large','small']));
const src=fs.readFileSync(dir+'/js/charts.js','utf8');
assert(!/SIZE_ORDER|segmentKey === 'size'|sizeOrder/.test(src),'fixed order for one dimension id');
console.log('segment order: 5 checks ok');

// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 Malte Dreyer
// The group comparison names the size of each group, not the base of one free-text question.
// Run: node dashboard/tests/group_size.test.js
const fs=require('fs'),vm=require('vm'),assert=require('assert');const dir=require('path').resolve(__dirname,'..');
const noop=new Proxy(function(){},{get:(t,k)=>k===Symbol.toPrimitive?()=>'':noop,apply:()=>noop,construct:()=>noop});
const ctx={console,addEventListener(){},document:noop,Plotly:noop,d3:noop,Alpine:noop,location:{hash:'',search:''},history:noop,navigator:{},setTimeout,URLSearchParams};
ctx.window=ctx;vm.createContext(ctx);
for(const f of ['i18n.js','charts.js','app.js']) vm.runInContext(fs.readFileSync(dir+'/js/'+f,'utf8'),ctx);
const r=vm.runInContext(`(()=>{const d=dashboard(); d.lang='en';
  const cl=(n)=>({results:{by_role:{student:{count:5,n:n,percentage:10}}}});
  d.data={metadata:{min_n:5},segments:{role:{label:{en:'Role'},values:[{key:'student',label:{en:'Student'}}]}},
    questions:{other:{type:'freetext',clusters:[cl(17)],results:{by_role:{student:{n:17}}}},
               role:{type:'single_choice',results:{by_role:{student:{n:144}}}},
               improve:{type:'freetext',clusters:[cl(117)],results:{by_role:{student:{n:117}}}}}};
  d.activeComparisonDimension=()=>'role';
  return {size:d.groupSize('by_role','student'), listed:d.getSegmentValuesWithClusterData().map(v=>[v.key,v.totalN])};})()`,ctx);
assert.strictEqual(r.size,144);
assert.deepStrictEqual(JSON.parse(JSON.stringify(r.listed)),[['student',144]]);
console.log('group size: 2 checks ok');

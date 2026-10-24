// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 Malte Dreyer
// Start page and summary from the export metadata; filters of any dimensions in the cluster comparison.
// Run: node dashboard/tests/start_page.test.js
const fs=require('fs'),vm=require('vm'),assert=require('assert');const dir=require('path').resolve(__dirname,'..');
const noop=new Proxy(function(){},{get:(t,k)=>k===Symbol.toPrimitive?()=>'':noop,apply:()=>noop,construct:()=>noop});
const ctx={console,addEventListener(){},document:noop,Plotly:noop,d3:noop,Alpine:noop,location:{hash:'',search:''},history:noop,navigator:{},setTimeout,URLSearchParams};
ctx.window=ctx;vm.createContext(ctx);
for(const f of ['i18n.js','charts.js','app.js']) vm.runInContext(fs.readFileSync(dir+'/js/'+f,'utf8'),ctx);
const r=vm.runInContext(`(()=>{const d=dashboard(); d.lang='it';
  d.data={metadata:{about:{en:'About text',it:'Testo'},survey_period:{en:'May 2026'},contact:{name:'Survey team',url:'https://example.org'},
          summary:[{title:{en:'Finding 1',it:'Risultato 1'},text:{en:'Text 1'}},{title:{}}]},
          segments:{region:{label:{en:'Region',it:'Regione'},values:['n','s','e']}}};
  const empty=dashboard(); empty.lang='de'; empty.data={metadata:{}};
  return {about:d.metaText('about'), period:d.metaText('survey_period'), dim:d.firstDimension(), sum:d.summarySections(),
          none:empty.summarySections().length, noneAbout:empty.metaText('about'),
          title:t('title','de')};})()`,ctx);
assert.strictEqual(r.about,'Testo'); assert.strictEqual(r.period,'May 2026');
assert.strictEqual(JSON.stringify(r.dim),JSON.stringify({label:'Regione',count:3}));
assert.strictEqual(r.sum.length,1); assert.strictEqual(r.sum[0].title,'Risultato 1'); assert.strictEqual(r.sum[0].text,'Text 1');
assert.strictEqual(r.none,0); assert.strictEqual(r.noneAbout,'');
assert.strictEqual(r.title,'Umfrage');
// cluster comparison with filters of other dimensions than country/type/size must not throw
const src=fs.readFileSync(dir+'/js/charts.js','utf8');
assert(!src.includes('filters.country.length'),'no fixed filter dimension in the code');
console.log('start page: 10 checks ok');

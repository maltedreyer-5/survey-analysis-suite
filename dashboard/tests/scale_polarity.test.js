// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 Malte Dreyer
// Colours from the polarity of a named scale (export metadata), for every language;
// the trend keywords are only the fallback for scales without polarity.
// Run: node dashboard/tests/scale_polarity.test.js
const fs=require('fs'),vm=require('vm');const dir=require('path').resolve(__dirname,'..');const assert=require('assert');
let plotted=null;
const el={innerHTML:'',style:{},clientWidth:600,appendChild(){},querySelector(){return null},addEventListener(){},getBoundingClientRect(){return{width:600}}};
const ctx={console,addEventListener(){},window:{},document:{getElementById:()=>el,createElement:()=>({...el}),addEventListener(){}},
  Plotly:{newPlot:(c,data)=>{plotted=data;},react:(c,data)=>{plotted=data;},purge(){}},setTimeout,URLSearchParams};
ctx.window=ctx;vm.createContext(ctx);
for(const f of ['i18n.js','charts.js']) vm.runInContext(fs.readFileSync(dir+'/js/'+f,'utf8'),ctx);
const opts=[{key:'gut',label:{de:'Gut',it:'Buono'},polarity:'positive'},{key:'mittel',label:{de:'Mittel',it:'Medio'},polarity:'neutral'},{key:'schlecht',label:{de:'Schlecht',it:'Cattivo'},polarity:'negative'}];
ctx.el=el; ctx.q={id:'q',type:'single_choice',ordinal:true,options:opts};
vm.runInContext("renderBarChart(el,q,{n:10,values:{gut:5,mittel:3,schlecht:2},percentages:{gut:50,mittel:30,schlecht:20}},'it')",ctx);
const t=plotted[0]; const col={}; (t.y||[]).forEach((y,i)=>{col[String(y).replace(/<[^>]+>/g,'').trim()]=t.marker.color[i];});
assert.strictEqual(col['Buono'],'#22c55e'); assert.strictEqual(col['Medio'],'#9ca3af'); assert.strictEqual(col['Cattivo'],'#ef4444');
assert.strictEqual(vm.runInContext("configuredPolarity(q,'mittel')",ctx),'neutral');
assert.strictEqual(vm.runInContext("configuredPolarity(q,'unknown')",ctx),null);
// matrix (stacked bars): the polarity orders and colours the scale points
ctx.m={id:'m',type:'matrix_likert',scale:opts,rows:[{key:'r1',label:{it:'Biblioteca'}}]};
vm.runInContext("renderStackedBarChart(el,m,{n:10,by_row:{r1:{percentages:{schlecht:20,gut:50,mittel:30}}}},'it')",ctx);
const traces=plotted.map(tr=>[tr.name,tr.marker.color]);
assert.deepStrictEqual(JSON.parse(JSON.stringify(traces)),[['Buono','#22c55e'],['Medio','#9ca3af'],['Cattivo','#ef4444']]);
console.log('scale polarity: 6 checks ok');

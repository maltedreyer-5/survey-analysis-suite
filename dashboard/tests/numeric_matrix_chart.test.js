// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 Malte Dreyer
// numeric_matrix renderer: bars of the means, table with all fields, constant-sum note, Italian texts.
// Run: node dashboard/tests/numeric_matrix_chart.test.js
const fs=require('fs'),vm=require('vm');const dir=require('path').resolve(__dirname,'..');const assert=require('assert');
let plotted=null;const made=[];
const mk=()=>{const e={innerHTML:'',textContent:'',style:{},className:'',children:[],appendChild(c){this.children.push(c);}};made.push(e);return e;};
const root=mk();
const ctx={console,addEventListener(){},window:{},document:{getElementById:()=>root,createElement:()=>mk(),addEventListener(){}},
  Plotly:{newPlot:(c,data)=>{plotted=data;},purge(){}},setTimeout,URLSearchParams};
ctx.window=ctx;vm.createContext(ctx);
for(const f of ['i18n.js','charts.js']) vm.runInContext(fs.readFileSync(dir+'/js/'+f,'utf8'),ctx);
ctx.q={id:'budget',type:'numeric_matrix',decimals:1,unit:'%',constant_sum:100,
  items:[{key:'f0',label:{de:'Personal',it:'Personale'}},{key:'f1',label:{de:'Sachmittel'}},{key:'f2',label:{de:'Leer'}}]};
ctx.r={n:3,fields:{f0:{n:3,mean:46.7,median:50,std:15.3,min:30,max:60},f1:{n:3,mean:53.3,median:50,std:15.3,min:40,max:70}},
  constant_sum:{total:100,n_respondents:3,n_matching:3,share_matching:100,mean_share:{f0:46.7,f1:53.3}}};
ctx.root=root;
vm.runInContext("renderNumericMatrixChart(root,q,r,'it')",ctx);
assert.deepStrictEqual(plotted[0].y,['Sachmittel','Personale']);           // field without data left out; Italian label
assert.deepStrictEqual(plotted[0].x,[53.3,46.7]);
const html=made.map(e=>e.innerHTML).join(' ');const note=made.map(e=>e.textContent).join(' ');
assert(html.includes('Media')&&html.includes('Quota del totale')&&html.includes('Personale'));
assert(note.includes('100')&&note.includes('n = 3'));
vm.runInContext("renderNumericMatrixChart(root,q,{n:0,fields:{}},'de')",ctx);
assert(root.innerHTML.includes('Daten')||root.innerHTML.length>0);
console.log('numeric matrix chart: 4 checks ok');

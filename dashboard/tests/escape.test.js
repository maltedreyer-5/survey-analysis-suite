// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 Malte Dreyer
// Texts from the data are escaped before they go into HTML (labels, explanations, messages):
// open answers come from respondents and must never be interpreted as markup.
// Run: node dashboard/tests/escape.test.js
const fs=require('fs'),vm=require('vm'),assert=require('assert');const dir=require('path').resolve(__dirname,'..');
const made=[];const mk=()=>{const e={innerHTML:'',textContent:'',style:{},className:'',children:[],id:'',appendChild(c){this.children.push(c);},
  insertAdjacentHTML(_,h){this.innerHTML+=h;},querySelector(){return null},addEventListener(){},classList:{add(){},remove(){},contains(){return false}}};made.push(e);return e;};
const ctx={console,addEventListener(){},window:{},document:{getElementById:()=>mk(),createElement:()=>mk(),addEventListener(){}},Plotly:{newPlot(){},purge(){}},setTimeout};
ctx.window=ctx;vm.createContext(ctx);
for(const f of ['i18n.js','charts.js']) vm.runInContext(fs.readFileSync(dir+'/js/'+f,'utf8'),ctx);
const evil='<img src=x onerror=alert(1)>';
ctx.q={id:'q',type:'numeric_matrix',decimals:1,items:[{key:'f0',label:{de:evil}}]};
ctx.r={n:1,fields:{f0:{n:1,mean:1,median:1,std:0,min:1,max:1}}};
ctx.c=mk();
vm.runInContext("renderNumericMatrixChart(c,q,r,'de'); renderScaleMetrics(c,[{label:q.items[0].label.de,metrics:{n:1,n_excluded:0,mean:1,median:1,top_box:1,bottom_box:1,box:1,min_value:1,max_value:2,top_labels:[q.items[0].label.de],bottom_labels:[]}}],'de')",ctx);
const html=made.map(e=>e.innerHTML).join(' ');
assert(!html.includes('<img'),'raw HTML from the data');
assert(html.includes('&lt;img'),'escaped text expected');
assert.strictEqual(vm.runInContext("safeId(\"a'b<c\")",ctx),'a_b_c');
console.log('escape: 3 checks ok');

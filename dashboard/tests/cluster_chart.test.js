// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 Malte Dreyer
// Free-text cluster chart: overall, global clusters within a segment, segment-specific clusters.
// Run: node dashboard/tests/cluster_chart.test.js
const fs=require('fs'),vm=require('vm');const dir=require('path').resolve(__dirname,'..');const assert=require('assert');
let plotted=null;
const el={innerHTML:'',style:{},clientWidth:600,appendChild(){},querySelector(){return null},addEventListener(){},getBoundingClientRect(){return{width:600}}};
const ctx={console,addEventListener(){},window:{},document:{getElementById:()=>el,createElement:()=>({...el}),addEventListener(){}},
  Plotly:{newPlot:(c,data)=>{plotted=data;},react:(c,data)=>{plotted=data;},purge(){}},setTimeout,URLSearchParams};
ctx.window=ctx;vm.createContext(ctx);
for(const f of ['i18n.js','charts.js']) vm.runInContext(fs.readFileSync(dir+'/js/'+f,'utf8'),ctx);
const q={id:'q',type:'freetext',clusters:[{id:'kosten',label:{de:'Kosten'},results:{all:{percentage:50,count:5,n:10}}},{id:'zeit',label:{de:'Zeit'},results:{all:{percentage:60,count:6,n:10}}}]};
const pick=()=>{const t=plotted&&plotted[0];return t?Object.fromEntries((t.y||t.labels||[]).map((l,i)=>[String(l).replace(/<[^>]+>/g,''),(t.x||t.values)[i]])):null};
ctx.q=q;ctx.el=el;
vm.runInContext("renderClusterChart(el,q,{n:10},'de')",ctx);assert.deepStrictEqual(pick(),{Zeit:60,Kosten:50});
vm.runInContext("renderClusterChart(el,q,{n:6,clustering:'global',cluster_percentages_by_id:{kosten:50,zeit:100},cluster_counts_by_id:{kosten:3,zeit:6}},'de')",ctx);assert.deepStrictEqual(pick(),{Zeit:100,Kosten:50});
vm.runInContext("renderClusterChart(el,q,{n:6,clustering:'segment',cluster_percentages:{'Eigenes Thema':80},cluster_counts:{'Eigenes Thema':5}},'de')",ctx);assert.deepStrictEqual(pick(),{'Eigenes Thema':80});console.log('cluster chart: 3 checks ok');

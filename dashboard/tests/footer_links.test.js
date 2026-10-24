// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 Malte Dreyer
// Footer links come from settings.survey_metadata.links; a link that is not configured is not shown.
// Run: node dashboard/tests/footer_links.test.js
const fs=require('fs'),vm=require('vm'),assert=require('assert');const dir=require('path').resolve(__dirname,'..');
const noop=new Proxy(function(){},{get:(t,k)=>k===Symbol.toPrimitive?()=>'':noop,apply:()=>noop,construct:()=>noop});
const ctx={console,addEventListener(){},document:noop,Plotly:noop,d3:noop,Alpine:noop,location:{hash:'',search:''},history:noop,navigator:{},setTimeout,URLSearchParams};
ctx.window=ctx;vm.createContext(ctx);
for(const f of ['i18n.js','charts.js','app.js']) vm.runInContext(fs.readFileSync(dir+'/js/'+f,'utf8'),ctx);
const r=vm.runInContext(`(()=>{const d=dashboard(); d.lang='de';
  d.data={metadata:{source_language:'en',links:{imprint:'https://example.org/imprint',privacy:{en:'https://example.org/privacy',de:'https://example.org/datenschutz'},methodology:{en:'https://example.org/method'}}}};
  const out={imprint:d.footerLink('imprint'),privacy:d.footerLink('privacy'),methodology:d.footerLink('methodology'),contact:d.footerLink('contact')};
  d.data={metadata:{}}; out.none=d.footerLink('imprint'); return out;})()`,ctx);
assert.strictEqual(r.imprint,'https://example.org/imprint');
assert.strictEqual(r.privacy,'https://example.org/datenschutz');
assert.strictEqual(r.methodology,'https://example.org/method');      // falls back to the source language
assert.strictEqual(r.contact,''); assert.strictEqual(r.none,'');
const html=fs.readFileSync(dir+'/index.html','utf8'); const footer=html.slice(html.indexOf('<footer'), html.indexOf('</footer>'));
assert(!footer.includes('href="#"'),'no footer link to nowhere');
console.log('footer links: 6 checks ok');

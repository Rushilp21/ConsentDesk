const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('web/desk.js','utf8');
const code=source.slice(source.indexOf('function formData('),source.indexOf('const deskPost='))+
  source.slice(source.indexOf('function csvCell('),source.indexOf("$('#export-paper')",source.indexOf('function csvCell(')));
const context=vm.createContext({FormData:class{constructor(form){return form.entries;}},Number,Object,String});
vm.runInContext(code,context);
context.form={entries:[['symbol',' infy '],['entry','100'],['quantity',''],['report_id','']]};
const data=vm.runInContext("formData(form,['entry','quantity','capital'])",context);
assert.equal(data.symbol,'INFY');assert.equal(data.entry,100);assert.equal(data.quantity,null);assert.equal(data.report_id,null);
assert.equal('capital'in data,false,'Disabled starting capital must not become NaN/null in a settings update');
context.value='=HYPERLINK("https://example.com")';
assert.match(vm.runInContext('csvCell(value)',context),/^"'/);
context.value=-20;assert.equal(vm.runInContext('csvCell(value)',context),'"-20"');
context.value='a,"b"\nc';assert.equal(vm.runInContext('csvCell(value)',context),'"a,""b""\nc"');
console.log('Passed: paper form parsing, optional quantity/report, locked-capital omission, safe CSV export.');

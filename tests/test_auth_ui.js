// Deployment errors must remain understandable when the proxy returns plain text.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('web/auth.js','utf8');
const context=vm.createContext({document:{querySelector:()=>({addEventListener:()=>{}})}});
vm.runInContext(source,context);
(async()=>{
  const read=context.readAuthResponse;
  await assert.rejects(read({json:async()=>{throw new SyntaxError('Unexpected token A');}}),/Backend unavailable/);
  await assert.rejects(read({ok:false,status:401,json:async()=>({error:'Invalid email or password'})}),/Invalid email or password/);
  await assert.rejects(read({ok:false,status:503,json:async()=>({})}),/503/);
  const payload={public_mode:true,user:null};
  assert.equal(await read({ok:true,json:async()=>payload}),payload);
  console.log('Authentication deployment-error checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

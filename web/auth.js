let authUser=null,authPublic=false,authResolve=null;
async function ensureAuth(){
  const r=await fetch('/api/auth/me');const d=await r.json();authPublic=d.public_mode;authUser=d.user;
  document.querySelector('#logout').hidden=!authPublic||!authUser;
  if(!authPublic||authUser)return;
  document.querySelector('#register-account').hidden=!d.registration_enabled;
  document.querySelector('#auth-dialog').showModal();
  await new Promise(resolve=>authResolve=resolve);
}
document.querySelector('#auth-dialog').addEventListener('cancel',e=>e.preventDefault());
document.querySelector('#auth-form').addEventListener('submit',async e=>{
  e.preventDefault();const form=e.target;const button=e.submitter;button.disabled=true;
  const status=document.querySelector('#auth-status');status.textContent='Signing in…';
  try{
    const response=await fetch(button.dataset.auth==='register'?'/api/auth/register':'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(form)))});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'Sign-in failed');
    authUser=data.user;form.reset();document.querySelector('#auth-dialog').close();document.querySelector('#logout').hidden=false;
    document.querySelector('#workspace-label').textContent='Private account';authResolve?.();
  }catch(x){status.textContent=x.message;}finally{button.disabled=false;}
});
document.querySelector('#logout').addEventListener('click',async()=>{try{await fetch('/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':authUser.csrf},body:'{}'});location.reload();}catch{document.querySelector('#connection').textContent='Sign-out failed; retry';}});

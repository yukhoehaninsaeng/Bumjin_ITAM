(() => {
  'use strict';
  const requestForm=document.getElementById('recoveryRequest'),completeForm=document.getElementById('recoveryComplete'),status=document.getElementById('recoveryStatus');
  const link=new URLSearchParams(location.hash.slice(1));
  let id=link.get('id'),token=link.get('token');
  if(location.hash)history.replaceState(null,'',location.pathname);
  if(id&&token){requestForm.hidden=true;completeForm.hidden=false;document.getElementById('recoveryTitle').textContent='NAS 비밀번호 재설정';document.getElementById('recoveryHelp').textContent='새 비밀번호를 입력하세요. 링크는 30분 동안 한 번만 사용할 수 있습니다.';}
  async function send(form,values){const button=form.querySelector('button');button.disabled=true;status.textContent='처리 중입니다.';try{const response=await fetch('/password-recovery/submit',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]').content},body:JSON.stringify(values)});const result=await response.json().catch(()=>({error:'페이지를 새로고침하고 다시 시도하세요.'}));if(!response.ok)throw Error(result.error||'처리하지 못했습니다.');status.textContent=result.message;return true;}catch(error){status.textContent=error.message;return false;}finally{button.disabled=false;}}
  requestForm.onsubmit=async event=>{event.preventDefault();await send(requestForm,{action:'request',...Object.fromEntries(new FormData(requestForm))});};
  completeForm.onsubmit=async event=>{event.preventDefault();const values=Object.fromEntries(new FormData(completeForm));const ok=await send(completeForm,{action:'complete',id,token,...values});completeForm.reset();if(ok){id=null;token=null;completeForm.hidden=true;}};
})();

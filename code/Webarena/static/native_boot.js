(async()=>{
 const status=document.getElementById('status');
 try {
  const args=new URLSearchParams(location.hash.slice(1));
  const token=args.get('token');
  const site=location.pathname.split('/').pop();
  if(!token)throw Error('Please open this website from your study tab.');
  sessionStorage.setItem('wa101-native',JSON.stringify({token,site,returnUrl:args.get('return')||''}));
  history.replaceState(null,'',location.pathname);
  const response=await fetch('/__study__/bootstrap/'+site,{method:'POST',headers:{Authorization:'Bearer '+token}});
  const data=await response.json();
  if(!response.ok)throw Error(data.detail||'Unable to connect your study session.');
  location.replace(data.path);
 }catch(e){status.textContent=e.message+' Return to the study tab and try again.';}
})();

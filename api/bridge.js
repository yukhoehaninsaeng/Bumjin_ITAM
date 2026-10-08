// Vercel server-side bridge; persistent XLSX writes occur only on the backend.
export function targetPath(query, method) {
  const route=query.route;
  if (method==='GET' && ['data','export'].includes(route)) return '/api/'+route;
  if (method==='POST' && route==='assets') return '/api/assets';
  if (method==='GET' && route==='qr' && /^[a-f0-9-]{36}$/.test(query.id||'')) return '/qr/'+query.id;
  return null;
}
export default async function handler(req,res) {
  res.setHeader('Cache-Control','no-store');
  const path=targetPath(req.query||{},req.method);
  if (!path) return res.status(404).json({error:'지원하지 않는 요청입니다.'});
  const base=process.env.BACKEND_URL;
  if (!base) return res.status(503).json({error:'사내 API 연결이 필요합니다. Vercel 환경변수 BACKEND_URL을 설정하세요.'});
  let target;
  try {
    const parsed=new URL(base);
    if(parsed.protocol!=='https:' || parsed.username || parsed.password || parsed.search || parsed.hash) throw Error();
    target=new URL(path,parsed);
  } catch {return res.status(503).json({error:'BACKEND_URL은 인증정보 없는 HTTPS 주소여야 합니다.'});}
  const auth=req.headers.authorization;
  if (!auth || !auth.startsWith('Basic ')) {
    res.setHeader('WWW-Authenticate','Basic realm="Bumjin Assets"');
    return res.status(401).json({error:'관리자 로그인이 필요합니다.'});
  }
  const headers={'Authorization':auth};
  if (process.env.BACKEND_GATEWAY_TOKEN) headers['X-Gateway-Token']=process.env.BACKEND_GATEWAY_TOKEN;
  if(req.headers['x-csrf-token']) headers['X-CSRF-Token']=req.headers['x-csrf-token'];
  let body;
  if(req.method==='POST') {
    body=typeof req.body==='string'?req.body:JSON.stringify(req.body||{});
    if(Buffer.byteLength(body)>20000) return res.status(413).json({error:'요청이 너무 큽니다.'});
    headers['Content-Type']='application/json';
  }
  try {
    const upstream=await fetch(target,{method:req.method,headers,body,redirect:'error',signal:AbortSignal.timeout(25000)});
    for(const name of ['content-type','content-disposition','www-authenticate']) {
      const value=upstream.headers.get(name);if(value)res.setHeader(name,value);
    }
    return res.status(upstream.status).send(Buffer.from(await upstream.arrayBuffer()));
  } catch {return res.status(502).json({error:'사내 API에 연결하지 못했습니다. 서버와 HTTPS 접근 경로를 확인하세요.'});}
}

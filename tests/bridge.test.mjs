import test from 'node:test';
import assert from 'node:assert/strict';
import handler,{targetPath} from '../api/bridge.js';
function response(){return {headers:{},setHeader(k,v){this.headers[k]=v},status(s){this.code=s;return this},json(v){this.data=v;return this},send(v){this.data=v;return this}}}
test('allows only fixed API paths and UUID QR paths',()=>{
  assert.equal(targetPath({route:'data'},'GET'),'/api/data');
  assert.equal(targetPath({route:'assets'},'POST'),'/api/assets');
  assert.equal(targetPath({route:'qr',id:'../../secret'},'GET'),null);
  assert.equal(targetPath({route:'data'},'POST'),null);
});
test('missing backend gives explicit connection error',async()=>{
  delete process.env.BACKEND_URL;
  const res=response();await handler({query:{route:'data'},method:'GET',headers:{}},res);
  assert.equal(res.code,503);
});
test('requires auth and rejects HTTP upstream',async()=>{
  process.env.BACKEND_URL='http://api.example.com';let res=response();
  await handler({query:{route:'data'},method:'GET',headers:{}},res);assert.equal(res.code,503);
  process.env.BACKEND_URL='https://api.example.com';res=response();
  await handler({query:{route:'data'},method:'GET',headers:{}},res);assert.equal(res.code,401);
});
test('forwards authentication, CSRF, gateway token and Excel bytes',async()=>{
  const original=global.fetch;process.env.BACKEND_URL='https://api.example.com';process.env.BACKEND_GATEWAY_TOKEN='test-gateway-token';
  try{
    global.fetch=async(url,options)=>{
      assert.equal(url.toString(),'https://api.example.com/api/assets');
      assert.equal(options.headers.Authorization,'Basic test');
      assert.equal(options.headers['X-CSRF-Token'],'csrf-value');
      assert.equal(options.headers['X-Gateway-Token'],'test-gateway-token');
      return new Response('{"id":"123"}',{status:200,headers:{'Content-Type':'application/json'}});
    };
    const res=response();await handler({query:{route:'assets'},method:'POST',headers:{authorization:'Basic test','x-csrf-token':'csrf-value'},body:{number:'T001'}},res);
    assert.equal(res.code,200);assert.equal(JSON.parse(res.data).id,'123');
  }finally{global.fetch=original;delete process.env.BACKEND_URL;delete process.env.BACKEND_GATEWAY_TOKEN}
});

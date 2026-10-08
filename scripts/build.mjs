import { mkdir, copyFile, writeFile, access, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const output = join(root, '.vercel/output');
const fn = join(output, 'functions/bridge.func');
await access(join(root, 'public/index.html'));
await rm(output, {recursive:true, force:true});
await mkdir(join(output, 'static'), {recursive:true});
await mkdir(fn, {recursive:true});
await copyFile(join(root, 'public/index.html'), join(output, 'static/index.html'));
await copyFile(join(root, 'api/bridge.js'), join(fn, 'bridge.mjs'));
await writeFile(join(fn, '.vc-config.json'), JSON.stringify({
  runtime: 'nodejs22.x',
  handler: 'bridge.mjs',
  launcherType: 'Nodejs',
  shouldAddHelpers: true,
  maxDuration: 30
}, null, 2));
await writeFile(join(output, 'config.json'), JSON.stringify({
  version: 3,
  routes: [
    {src:'/(.*)', headers:{'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'same-origin'}, continue:true},
    {src:'/api/data', dest:'/bridge?route=data'},
    {src:'/api/assets', dest:'/bridge?route=assets'},
    {src:'/api/export', dest:'/bridge?route=export'},
    {src:'/qr/([a-f0-9-]{36})', dest:'/bridge?route=qr&id=$1'},
    {src:'/a/([a-f0-9-]{36})', dest:'/index.html'},
    {handle:'filesystem'},
    {src:'/', dest:'/index.html'}
  ]
}, null, 2));
console.log('Vercel Build Output API v3 generated: ' + output);

import { mkdir, copyFile } from 'node:fs/promises';
await mkdir('dist', {recursive:true});
await copyFile('public/index.html', 'dist/index.html');
console.log('Static frontend built: dist/index.html');

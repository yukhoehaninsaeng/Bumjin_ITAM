import { access, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const output = fileURLToPath(new URL('../public/index.html', import.meta.url));
await access(output);
const html = await readFile(output, 'utf8');
if (!html.includes('<html')) throw new Error('public/index.html is not a valid HTML entrypoint');
console.log('Static frontend verified: ' + output);

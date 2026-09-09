import { cp, mkdir, rm } from 'node:fs/promises';
const root = new URL('../', import.meta.url);
const dist = new URL('../dist/', import.meta.url);
await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
for (const name of ['index.html','styles-20260909a.css','script.js']) await cp(new URL(name, root), new URL(name, dist));
await cp(new URL('public/assets/', root), new URL('assets/', dist), { recursive: true });
console.log('Built static production site -> dist/');

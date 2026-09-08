import { readFile, stat } from 'node:fs/promises';
const required = ['index.html','styles.css','script.js','public/assets/baseline-strength-brand.webp','public/assets/baseline-strength-film.mp4','public/assets/baseline-strength-film-poster.jpg','public/assets/coach-portrait.jpg'];
for (const file of required) { const s = await stat(new URL(`../${file}`, import.meta.url)); if (!s.size) throw new Error(`${file} is empty`); }
const html = await readFile(new URL('../index.html', import.meta.url), 'utf8');
const requiredText = ['FORGE YOUR','10,000+ Rehab Hours','BUILD A BODY THAT HOLDS UP.','YOUR BASELINE IS THE SYSTEM.','$100','$180','$250','BaselineStrengthPersonalTraining@gmail.com','THE FULL VISUAL STORY.','baseline-strength-film.mp4','baseline-strength-brand.webp'];
for (const text of requiredText) if (!html.includes(text)) throw new Error(`Missing required content: ${text}`);
for (const href of html.matchAll(/href="([^"]+)"/g)) if (href[1].startsWith('javascript:')) throw new Error('Unsafe javascript: href');
console.log('VERIFY PASS — required content, assets and basic link safety checks passed.');

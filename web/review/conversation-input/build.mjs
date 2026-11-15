import { build } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const sourceRoot = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(sourceRoot,'../..');
const repositoryRoot = resolve(webRoot,'..');
const output = resolve(process.argv[2] ?? resolve(webRoot,'review-dist/conversation-input.html'));
const revision = execFileSync('git',['rev-parse','HEAD'],{cwd:repositoryRoot,encoding:'utf8'}).trim();
const bundledSources = new Set();
const hash = value => createHash('sha256').update(value).digest('hex');
const result = await build({
  configFile:false, root:webRoot, base:'./', plugins:[svelte(),{
    name:'local-conversation-preview', enforce:'pre',
    transform(_code,id) {
      const path = id.split('?')[0];
      if (path.startsWith(repositoryRoot+'/') && !path.includes('/node_modules/')) bundledSources.add(path.slice(repositoryRoot.length+1));
    },
    resolveId(id, importer) {
      if (!importer) return;
      if (id.endsWith('/markdown/workerClient') || id.endsWith('/markdown/workerClient.ts')) return resolve(sourceRoot,'parser.ts');
      if (id.endsWith('/ManagedHtmlDocument.svelte')) return resolve(sourceRoot,'LocalArtifactDocument.svelte');
    }
  }],
  build:{write:false,emptyOutDir:false,minify:true,cssCodeSplit:false,assetsInlineLimit:Infinity,
    lib:{entry:resolve(sourceRoot,'main.ts'),name:'PanelsConversationInputReview',formats:['iife']},
    rollupOptions:{output:{inlineDynamicImports:true}}}
});
const files = (Array.isArray(result) ? result : [result]).flatMap(item => item.output);
const script = files.filter(item => item.type === 'chunk').map(item=>item.code).join('\n').replaceAll('__PREVIEW_REVISION__',revision.slice(0,12));
const css = files.filter(item => item.type === 'asset' && item.fileName.endsWith('.css')).map(item=>String(item.source)).join('\n');
const extra = files.filter(item => item.type === 'asset' && !item.fileName.endsWith('.css'));
if (extra.length) throw new Error('Unexpected external preview assets: '+extra.map(item=>item.fileName).join(', '));
const inputs = [...bundledSources, 'assets/tokens.css','assets/app.css','web/package-lock.json','web/review/conversation-input/Preview.svelte','web/review/conversation-input/main.ts','web/review/conversation-input/parser.ts','web/review/conversation-input/LocalArtifactDocument.svelte','web/review/conversation-input/build.mjs','web/review/conversation-input/tsconfig.json','web/review/conversation-input/README.md','scripts/conversation_input_review/verify_preview.py'];
const inputHashes = Object.fromEntries(await Promise.all(inputs.map(async path=>[path,hash(await readFile(resolve(repositoryRoot,path)))])));
const sourceDirty = execFileSync('git',['status','--porcelain','--','assets','web','scripts/conversation_input_review'],{cwd:repositoryRoot,encoding:'utf8'}).trim() !== '';
const metadata = {revision,sourceDirty,previewLayoutRevision:'ec39ed4aaf05b8ad59d7b2e57ed3d1afaf0fc980',adapters:['Production Markdown parser inline, without module-worker isolation','Synthetic sample artifact srcdoc, without live managed-file navigation','Finite synthetic conversation transport, without live endpoints'],inputHashes,cssSha256:hash(css),scriptSha256:hash(script)};
const inner = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Conversation input preview</title><style>'+css.replaceAll('</style','<\\/style')+'</style></head><body><div id="app"></div><script type="application/json" id="preview-build">'+JSON.stringify(metadata).replaceAll('<','\\u003c')+'</script><script>'+script.replaceAll('</script','<\\/script')+'</script></body></html>';
// The selected size must be the real CSS viewport, not just a narrow container.
// This inline child remains opaque and can run without modules or network reads.
const frameSource = JSON.stringify(inner).replaceAll('<','\\u003c');
const wrapper = `const frame=document.getElementById('review-viewport');window.addEventListener('message',event=>{if(event.source!==frame.contentWindow||event.data?.type!=='panels-conversation-review-width'||![736,1250,390].includes(event.data.width))return;frame.style.width=event.data.width+'px';});frame.srcdoc=${frameSource};`;
const html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Conversation input preview</title><style>html,body{height:100%;margin:0;background:#0d0d0d}body{display:flex;justify-content:center}iframe{display:block;box-sizing:border-box;border:0;height:100%;max-width:100%;width:736px}</style></head><body><iframe id="review-viewport" title="Conversation input viewport" sandbox="allow-scripts"></iframe><script type="application/json" id="preview-build">'+JSON.stringify(metadata).replaceAll('<','\\u003c')+'</script><script>'+wrapper+'</script></body></html>';
await mkdir(dirname(output),{recursive:true});
await writeFile(output,html);
await writeFile(output.replace(/\.html$/,'')+'.build.json',JSON.stringify({...metadata,htmlSha256:hash(html)},null,2)+'\n');
console.log(JSON.stringify({output,bytes:Buffer.byteLength(html),revision,htmlSha256:hash(html)},null,2));

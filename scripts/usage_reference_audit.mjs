/** Independent real-history aggregation using the unmodified T3 reference parsers.
 * This is a verification script, never an application service or Node sidecar.
 * It emits aggregates only. No transcript line or credential file leaves this process.
 */
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import readline from 'node:readline';
import { pathToFileURL } from 'node:url';

const input = JSON.parse(await fsp.readFile(process.argv[2], 'utf8'));
const source = input.referenceRoot;
const parser = await import(pathToFileURL(path.join(source, 'apps/server/src/usage/usageTranscripts.ts')));
const pricing = await import(pathToFileURL(path.join(source, 'apps/server/src/usage/usagePricing.ts')));
const ratesDocument = JSON.parse(await fsp.readFile(input.ratesCache, 'utf8')).document;
const rates = pricing.parseRateTable(ratesDocument);
const since = Date.parse(input.window.sinceTime);
const until = Date.parse(input.window.untilTime);
const mtimeCutoff = since - 36 * 60 * 60 * 1000;
const started = performance.now();
const seen = new Set();
const buckets = new Map();
const stats = [];
const dayFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: input.window.timeZone, year:'numeric', month:'2-digit', day:'2-digit'
});
const categories = ['uncachedInputTokens', 'cachedInputTokens', 'cacheCreationTokens', 'outputTokens', 'reasoningTokens'];

async function inventory(root) {
  const found = [];
  async function visit(dir) {
    let entries;
    try { entries = await fsp.readdir(dir, {withFileTypes:true}); }
    catch { return; }
    for (const entry of entries) {
      const filename = path.join(dir, entry.name);
      if (entry.isDirectory()) await visit(filename);
      else if (entry.name.endsWith('.jsonl')) {
        const stat = await fsp.stat(filename);
        if (stat.mtimeMs >= mtimeCutoff) found.push({filename, size:stat.size});
      }
    }
  }
  await visit(root);
  return found;
}

for (const [provider, root] of input.roots) {
  const files = await inventory(root);
  const sourceStats = {provider, files:files.length, corpusBytes:files.reduce((s,f)=>s+f.size,0),
    records:0, duplicates:0, outOfWindow:0, sessions:new Set()};
  for (const file of files) {
    const state = parser.initialCodexScanState();
    const stream = fs.createReadStream(file.filename);
    const lines = readline.createInterface({input:stream, crlfDelay:Infinity});
    for await (const line of lines) {
      let records;
      if (provider === 'codex') records = [parser.parseCodexLine(line, state)];
      else if (provider === 'claude') records = [parser.parseClaudeLine(line)];
      else records = parser.parseGrokLine(line);
      for (const record of records) {
        if (record === null) continue;
        if (record.dedupeKey !== null) {
          if (seen.has(record.dedupeKey)) { sourceStats.duplicates++; continue; }
          seen.add(record.dedupeKey);
        }
        if (record.timestampMs < since || record.timestampMs >= until) {
          sourceStats.outOfWindow++; continue;
        }
        const day = dayFormatter.format(new Date(record.timestampMs));
        const hourStart = new Date(since + Math.floor((record.timestampMs-since)/3600000)*3600000).toISOString();
        const key = JSON.stringify([day,hourStart,record.provider,record.model]);
        if (!buckets.has(key)) buckets.set(key, {day,hourStart,provider:record.provider,model:record.model,
          totals:Object.fromEntries(categories.map(k=>[k,0])),costUsd:0,cacheSavingsUsd:0,records:0,
          unpricedRecords:0,providerReportedRecords:0,sessions:new Set()});
        const bucket = buckets.get(key);
        for (const category of categories) bucket.totals[category] += record.totals[category];
        const priced = pricing.priceUsage(rates, record.model, record.totals, record.reportedCostUsd);
        bucket.costUsd += priced.costUsd;
        bucket.cacheSavingsUsd += pricing.cacheSavingsUsd(rates, record.model, record.totals);
        bucket.records++;
        if (priced.costSource==='unpriced') bucket.unpricedRecords++;
        if (priced.costSource==='providerReported') bucket.providerReportedRecords++;
        if (record.sessionId) { bucket.sessions.add(record.sessionId); sourceStats.sessions.add(record.sessionId); }
        sourceStats.records++;
      }
    }
  }
  stats.push({...sourceStats,sessions:sourceStats.sessions.size});
}
const output = [...buckets.values()].map(b=>{
  const {providerReportedRecords,...value}=b;
  return {...value, sessions:b.sessions.size,
    costSource:b.unpricedRecords===b.records?'unpriced':providerReportedRecords===b.records?'providerReported':'modelPriced'};
});
output.sort((a,b)=>a.day.localeCompare(b.day)||a.hourStart.localeCompare(b.hourStart)||a.provider.localeCompare(b.provider)||a.model.localeCompare(b.model));
const result = {method:'Unmodified reference TypeScript parsers and price functions; independent inventory, deduplication, window filtering, bucket accumulation, session counting and JSON output. No translated Python reducer import.',
  referenceRevision:'75e4ceb96489a2fe92c710c79dbea59778cccfd1',window:input.window,sources:stats,buckets:output,durationMs:performance.now()-started};
await fsp.writeFile(process.argv[3],JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify({sources:stats,buckets:output.length,durationMs:result.durationMs}));

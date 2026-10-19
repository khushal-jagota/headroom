import assert from "node:assert/strict";

export function selectedEntries(catalog, selections) {
  assert.equal(catalog.schema, 1, "Unsupported browser catalog");
  assert.ok(Array.isArray(catalog.entries) && catalog.entries.length, "Empty browser catalog");
  const identities = new Set();
  for (const entry of catalog.entries) {
    assert.ok(entry.id && entry.path && Array.isArray(entry.cases) && entry.cases.length, "Invalid browser entry");
    assert.ok(Array.isArray(entry.companions || []) && (entry.companions || []).every(file => typeof file === "string" && file.startsWith("web/tests/") && file.endsWith(".test.ts")), "Invalid companion declaration");
    for (const identity of [entry.id, entry.path, ...entry.cases]) {
      assert.ok(typeof identity === "string" && identity.length > 0 && !identities.has(identity), `Duplicate browser identity: ${identity}`);
      identities.add(identity);
    }
  }
  const requested = selections.length ? selections : catalog.entries.map(entry => entry.id);
  assert.equal(new Set(requested).size, requested.length, "Duplicate browser selection");
  const entries = requested.map(selection => {
    const entry = catalog.entries.find(item => item.id === selection || item.path === selection || item.path.replace(/^web\//, "") === selection);
    assert.ok(entry, `Unknown browser entry: ${selection}`);
    return entry;
  });
  assert.equal(new Set(entries.map(entry => entry.id)).size, entries.length, "Duplicate browser entry");
  return entries;
}

export function validateCompanion(value, invocationId, revision, requiredFiles) {
  assert.equal(value.schema, 1, "Invalid companion report");
  assert.equal(value.invocationId, invocationId, "Companion belongs to another invocation");
  assert.equal(value.revision, revision, "Companion belongs to another revision");
  assert.equal(value.outcome, "passed", "Companion failed");
  assert.equal(value.compiler, "passed", "Companion compiler did not pass");
  for (const file of requiredFiles) {
    const requested = value.runtime.filter(result => result.path === file);
    assert.equal(requested.length, 1, "Companion runtime file missing or duplicated");
    assert.ok(requested[0].cases.length, "Companion runtime collection is empty");
  }
  const identities = new Set();
  for (const result of value.runtime) {
    assert.ok(result.cases.length, "Empty companion runtime module");
    for (const test of result.cases) {
      assert.equal(test.status, "passed", "Companion runtime case failed or skipped");
      const identity = `${result.path}:${test.name}`;
      assert.ok(!identities.has(identity), "Duplicate companion case");
      identities.add(identity);
    }
  }
  return value;
}

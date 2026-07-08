const ENTITY_PREFIXES = new Set(["t", "i", "s", "day", "idea"]);

/**
 * @typedef {{id: number, entity_id: string, kind: string, payload: Record<string, unknown>, created_at: number}} PlannerEvent
 * @typedef {{todayId?: string | null, includeTodayAlias?: boolean}} MappingOptions
 */

/**
 * @param {string[]} keys
 * @returns {string[]}
 */
function unique(keys) {
  return Array.from(new Set(keys.filter(Boolean)));
}

/**
 * @param {string} entityId
 * @param {string} prefix
 * @returns {string}
 */
function suffixAfter(entityId, prefix) {
  return entityId.slice(prefix.length + 1);
}

/**
 * @param {string} entityId
 * @param {MappingOptions} [options]
 * @returns {string[]}
 */
function keysForEntity(entityId, options = {}) {
  if (typeof entityId !== "string" || !entityId.includes("_")) {
    throw new Error(`unknown entity_id prefix: ${String(entityId)}`);
  }
  const prefix = entityId.split("_", 1)[0];
  if (!ENTITY_PREFIXES.has(prefix)) {
    throw new Error(`unknown entity_id prefix: ${entityId}`);
  }
  if (prefix === "t") {
    return [`ticket:${entityId}`, "board", "queues", "sprint:current"];
  }
  if (prefix === "i") {
    return [`item:${entityId}`, "items:backlog", "sprint:current", "board", "queues"];
  }
  if (prefix === "s") {
    return [`sprint:${entityId}`, "sprint:current", "sprints"];
  }
  if (prefix === "day") {
    const date = suffixAfter(entityId, "day");
    const keys = [`day:${date}`];
    if (options.todayId === entityId || options.includeTodayAlias) {
      keys.push("day:today");
    }
    return keys;
  }
  return ["ideas"];
}

/**
 * @param {string} entityId
 * @returns {string[]}
 */
function endpointEntityKeys(entityId) {
  const prefix = String(entityId).split("_", 1)[0];
  if (prefix === "t") return [`ticket:${entityId}`];
  if (prefix === "i") return [`item:${entityId}`];
  if (prefix === "s") return [`sprint:${entityId}`];
  if (prefix === "day") return [`day:${suffixAfter(String(entityId), "day")}`];
  if (prefix === "idea") return ["ideas"];
  throw new Error(`unknown entity_id prefix: ${String(entityId)}`);
}

/**
 * @param {PlannerEvent} event
 * @param {MappingOptions} [options]
 * @returns {string[]}
 */
export function keysForEvent(event, options = {}) {
  if (!event || typeof event !== "object") {
    throw new Error("event object is required");
  }
  const entityId = String(event.entity_id || "");
  const kind = String(event.kind || "");
  const payload = event.payload && typeof event.payload === "object" ? event.payload : {};
  const keys = keysForEntity(entityId, options);

  if (kind === "link_added" || kind === "link_removed") {
    if (payload.from_id) keys.push(...endpointEntityKeys(String(payload.from_id)));
    if (payload.to_id) keys.push(...endpointEntityKeys(String(payload.to_id)));
    keys.push("board", "queues", "sprint:current");
  }

  if (kind === "chat_session_created") {
    keys.push(...endpointEntityKeys(entityId));
  }

  if (kind.startsWith("day_") && payload.ticket_id) {
    keys.push(`ticket:${payload.ticket_id}`, "board");
  }

  return unique(keys);
}

export function knownEntityPrefixes() {
  return Array.from(ENTITY_PREFIXES);
}

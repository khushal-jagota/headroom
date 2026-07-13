const ENTITY_PREFIXES = new Set(["t", "si", "sp", "day", "idea", "project", "agent"]);

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
    return [`ticket:${entityId}`, `chat:${entityId}`, "board", "queues", "sprint:current"];
  }
  if (prefix === "si") {
    return [`item:${entityId}`, "items:backlog", "sprint:current", "board", "queues"];
  }
  if (prefix === "sp") {
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
  if (prefix === "agent") return [`chat:${entityId}`];
  if (prefix === "idea") return ["ideas"];
  return ["projects"];
}

/**
 * @param {string} entityId
 * @returns {string[]}
 */
function endpointEntityKeys(entityId) {
  const prefix = String(entityId).split("_", 1)[0];
  if (prefix === "t") return [`ticket:${entityId}`];
  if (prefix === "si") return [`item:${entityId}`];
  if (prefix === "sp") return [`sprint:${entityId}`];
  if (prefix === "day") return [`day:${suffixAfter(String(entityId), "day")}`];
  if (prefix === "agent") return [`chat:${entityId}`];
  if (prefix === "idea") return ["ideas"];
  if (prefix === "project") return ["projects"];
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

  if (Array.isArray(payload.affected_blocked_target_ids)) {
    for (const targetId of payload.affected_blocked_target_ids) {
      keys.push(...endpointEntityKeys(String(targetId)));
    }
    keys.push("board", "queues", "sprint:current");
  }

  if (
    kind === "chat_session_created" ||
    kind === "chat_message_recorded" ||
    kind === "chat_turn_started" ||
    kind === "chat_turn_updated" ||
    kind === "chat_turn_finished"
  ) {
    keys.push(...endpointEntityKeys(entityId));
  }

  if (kind.startsWith("day_") && payload.ticket_id) {
    keys.push(`ticket:${payload.ticket_id}`, "board");
    if (kind === "day_ticket_added" || kind === "day_ticket_removed") {
      keys.push("queues");
    }
  }

  return unique(keys);
}

export function knownEntityPrefixes() {
  return Array.from(ENTITY_PREFIXES);
}

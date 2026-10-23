import { DiagnosticExpiredError, isDiagnosticExpiry } from '../diagnosticExpiry';
/** One selected view: bounded pages, one stream, and cancellable demand reads. */
import { SvelteSet } from 'svelte/reactivity';
import type { ConversationViewItem, ConversationViewPage, ConversationViewSnapshot, ConversationViewStatus, ConversationViewLens, ViewPageCursor, ViewHistoryScope, ViewTailChanges, ViewBodyReference, ViewMessageReceipts } from './contracts';
import { VIEW_PROJECTION_VERSION, VIEW_PROTOCOL_VERSION } from './contracts';
export const READER_ITEMS = 300;
export const READER_BODY_BYTES = 512 * 1024;
const timeline: ViewHistoryScope = { kind: 'timeline', key: null };
const encoder = new TextEncoder();
const byteSizes = new WeakMap<ConversationViewItem, number>();
function scopeKey(scope: ViewHistoryScope): string { return `${scope.kind}:${scope.key ?? ''}`; }
function sameScope(a: ViewHistoryScope, b: ViewHistoryScope): boolean { return a.kind === b.kind && a.key === b.key; }
function sameCursor(a: ViewPageCursor | null, b: ViewPageCursor | null): boolean {
    return a === null || b === null ? a === b : a.projection_version === b.projection_version
        && a.lens === b.lens && sameScope(a.scope, b.scope)
        && a.order.sequence === b.order.sequence && a.order.slot === b.order.slot;
}
export function compareItems(a: ConversationViewItem, b: ConversationViewItem): number { return a.order.sequence - b.order.sequence || a.order.slot - b.order.slot; }
function itemBytes(item: ConversationViewItem): number { const cached = byteSizes.get(item); if (cached !== undefined)
    return cached; const count = item.kind === 'row' && item.body.kind === 'inline' ? encoder.encode(JSON.stringify(item.body.value)).byteLength : 0; byteSizes.set(item, count); return count; }
export type ViewReaderAnchor = {
    key: string;
    order: {
        sequence: number;
        slot: number;
    };
    offset: number;
    following: boolean;
};
export type HeldPage = {
    scope: ViewHistoryScope;
    items: ConversationViewItem[];
    older: ViewPageCursor | null;
    newer: ViewPageCursor | null;
};
export class ConversationViewReader {
    pages = $state.raw<readonly HeldPage[]>([]);
    status = $state.raw<ConversationViewStatus | null>(null);
    coverage = $state(0);
    trouble = $state(false);
    loading = $state(false);
    restoring = $state(false);
    entryPhase = $state<'idle' | 'loading' | 'placing' | 'error' | 'complete'>('idle');
    entryTarget = $state.raw<ViewReaderAnchor | null>(null);
    entryTargetReady = $state(false);
    entryHeld = $state(false);
    private entryOperation = 0;
    private entryStarted = false;
    private receivedSnapshot = false;
    private initialLiveOutput: string | null = null;
    private initialLiveOutputTurn: string | null = null;
    /** An existing snapshot body is layout restoration, not newly arriving text. */
    liveOutputIsArrival(reference: ViewBodyReference, replacingBody: boolean): boolean {
        const output = this.status?.live_output;
        const turn = output ? JSON.stringify([output.runtime_epoch, output.turn_key]) : null;
        return this.initialLiveOutput === null || turn !== this.initialLiveOutputTurn || replacingBody && this.initialLiveOutput !== JSON.stringify(reference);
    }
    private rangeTimer: ReturnType<typeof setTimeout> | null = null;
    revision = $state(0);
    private source: EventSource | null = null;
    private releaseSource: () => void = () => { };
    private abort = new AbortController();
    private generation = 0;
    /** Everything held is suspect, so read it all again - but keep showing it.
     *
     * What the reader is looking at stays true until something truer arrives. Taking it
     * away first leaves them watching an empty conversation for as long as the re-read
     * takes, and if the re-read never lands they are left with nothing at all. The
     * replacement happens in one write, when the replacement exists.
     *
     * An ordinary reconnect does not even come here. The stream is torn down once per
     * page of scroll-back by design, so treating its snapshot as a new conversation
     * threw the reader's history away every time they scrolled back. */
    private rereadHistory(): void {
        this.discardedBodies++;
        this.cancelPages();
        this.pageQueue = Promise.resolve();
        this.coverage = 0;
        this.wholeHistoryIsSuspect = true;
        this.revision++;
    }
    private wholeHistoryIsSuspect = false;
    /** Counts the times the bodies drawn in the page stopped being true.
     *
     * Diagnostic output expires, and a runtime that goes away takes its output with
     * it. What a row has already read is then wrong and has to be read again. This
     * says so without anything being unmounted: a cache is not what a reader is
     * looking at, and dropping one lets the row show the truth rather than a blank. */
    discardedBodies = $state(0);
    private retry: ReturnType<typeof setTimeout> | null = null;
    private disposed = false;
    private reconnecting: Promise<void> | null = null;
    private requests = new Map<string, { operation: Promise<boolean>; abort: AbortController; scope: ViewHistoryScope; cursor: ViewPageCursor | null; direction: 'older' | 'newer'; replace: boolean }>();
    private pageQueue: Promise<void> = Promise.resolve();
    /** Close the live stream so a history read is not racing a writer.
     *
     * Every page read calls this and then schedules a reconnect in its `finally`, so the
     * stream is deliberately torn down and re-opened once per page of scroll-back. That
     * churn is by design and is not a fault: it is what stops a live writer starving
     * history reads. It was measured and found not to be what makes scrolling back feel
     * stuck, so if you are chasing that, this is not it - the reader's retention slides
     * its window correctly at the ceiling, with or without a writer.
     *
     * One thing to know before believing a scroll-back measurement. A gesture that fires
     * faster than a page completes can call this again while the previous re-open is
     * still in flight, tearing the stream down mid-re-open. Repeated demand for one scope
     * otherwise coalesces, because `load` returns the in-flight promise for that scope. A
     * hand cannot scroll fast enough to reach that state; a test driving `scrollTop`
     * directly can, and will then be measuring the probe rather than the product.
     */
    private pauseTail(): void {
        if (this.rangeTimer !== null) clearTimeout(this.rangeTimer);
        this.rangeTimer = null;
        this.generation++;
        this.releaseSource();
        this.source = null;
        this.reconnecting = null;
        if (this.retry !== null) clearTimeout(this.retry);
        this.retry = null;
    }
    private cancelPages(): void {
        for (const request of this.requests.values()) request.abort.abort();
        this.requests.clear();
    }
    constructor(readonly id: string, readonly lens: ConversationViewLens, readonly transcript: boolean, private changed: () => void = () => { }, private initialAnchor: ViewReaderAnchor | null = null, private expanded = new Set<string>(), readonly openTools = new SvelteSet<string>(), private entryReadThroughSequence: number | null = null) {
        this.restoring = initialAnchor !== null && !initialAnchor.following;
        if (transcript && entryReadThroughSequence !== null && initialAnchor === null) this.entryPhase = 'loading';
    }
    /** Entry is an operation, not a moving copy of the durable read watermark. */
    private async prepareEntry(): Promise<void> {
        const operation = ++this.entryOperation;
        const active = () => !this.disposed && operation === this.entryOperation;
        this.entryStarted = true;
        this.entryPhase = 'loading';
        this.entryTarget = null;
        this.entryTargetReady = false;
        let cursor: ViewPageCursor = { projection_version: VIEW_PROJECTION_VERSION, lens: this.lens, scope: timeline,
            order: { sequence: this.entryReadThroughSequence ?? 0, slot: Number.MAX_SAFE_INTEGER } };
        while (active()) {
            const admitted = await this.load(timeline, cursor, 'newer', true);
            if (!active()) return;
            if (!admitted) { this.failEntry(); return; }
            const page = this.pages.find(page => page.scope.kind === 'timeline');
            const target = page?.items.find(item => item.kind === 'row' && [
                'prompt', 'agent_message', 'prompt_refused', 'prompt_uncertain', 'prompt_discarded', 'permission_ask', 'user_input'
            ].includes(item.row_kind));
            if (target) {
                this.entryTarget = { key: target.key, order: target.order, offset: 32, following: false };
                this.entryTargetReady = target.kind === 'row' && target.body.kind === 'inline';
                this.entryPhase = 'placing';
                this.changed();
                return;
            }
            const next = page?.newer;
            if (!next) {
                const latest = await this.load(timeline, null, 'older', true);
                if (!active()) return;
                if (!latest) { this.failEntry(); return; }
                this.entryTargetReady = true;
                this.entryPhase = 'placing';
                this.changed();
                return;
            }
            if (next.order.sequence < cursor.order.sequence || (next.order.sequence === cursor.order.sequence && next.order.slot <= cursor.order.slot)) {
                this.failEntry(); return;
            }
            cursor = next;
        }
    }
    entryBodyReady(key: string): void {
        if (this.entryPhase === 'placing' && this.entryTarget?.key === key) this.entryTargetReady = true;
    }
    failEntry(): void {
        if (this.disposed || this.entryPhase === 'complete' || this.entryPhase === 'idle') return;
        this.entryOperation++;
        this.entryPhase = 'error';
        this.loading = false;
        this.changed();
    }
    retryEntry(): void {
        if (this.entryPhase === 'error') void this.prepareEntry();
    }
    finishEntry(): void {
        if (this.entryPhase !== 'placing' || !this.entryTargetReady) return;
        this.entryHeld = this.entryTarget !== null;
        this.entryPhase = 'complete';
        this.changed();
        if (this.source === null) void this.connect().catch(() => undefined);
    }
    releaseEntry(): void { this.entryHeld = false; }
    private url(suffix: string, query: Record<string, string> = {}): string {
        const search = new URLSearchParams({ lens: this.lens, ...query });
        return `/api/conversation/conversations/${encodeURIComponent(this.id)}/view${suffix}?${search}`;
    }
    private async get<T>(suffix: string, query: Record<string, string> = {}, signal = this.abort.signal): Promise<T> {
        const response = await fetch(this.url(suffix, query), { signal });
        if (!response.ok)
            throw new Error(`Conversation view: ${response.status}`);
        return await response.json() as T;
    }
    private checkPage(page: ConversationViewPage): void {
        if (page.conversation_id !== this.id || page.lens !== this.lens || page.protocol_version !== VIEW_PROTOCOL_VERSION || page.projection_version !== VIEW_PROJECTION_VERSION || page.items.length > 100 || page.body_bytes > 128 * 1024)
            throw new Error('Conversation view version or bounds changed');
    }
    /** Take a snapshot as the truth about the range it covers, and nothing beyond it.
     *
     * One arrives on every reconnect, and the stream reconnects once per page of
     * scroll-back, so reading it as the truth about the whole timeline threw away
     * everything the reader had scrolled back to admit. Items inside its range that it
     * does not carry are gone. Items outside it are untouched. */
    private admitSnapshot(page: ConversationViewPage): void {
        // A page that names nothing older and nothing newer is the whole scope, so it
        // is the truth about all of it, including what it leaves out.
        if (page.older === null && page.newer === null) {
            this.accept(page, true);
            return;
        }
        const first = page.items[0]?.order;
        const last = page.items.at(-1)?.order;
        if (first !== undefined && last !== undefined) {
            const key = scopeKey(page.scope);
            const covered = (order: { sequence: number; slot: number }): boolean =>
                (order.sequence > first.sequence || (order.sequence === first.sequence && order.slot >= first.slot))
                && (order.sequence < last.sequence || (order.sequence === last.sequence && order.slot <= last.slot));
            this.pages = this.pages.map(held => scopeKey(held.scope) === key
                ? { ...held, items: held.items.filter(item => !covered(item.order)) }
                : held);
        }
        this.accept(page, false);
    }
    /** Diagnostics that expired take every body read from them with them. */
    private noteStatus(status: ConversationViewStatus): void {
        if (status.diagnostics_expired && this.status?.diagnostics_expired !== true) this.discardedBodies++;
        this.status = status;
    }
    private accept(page: ConversationViewPage, replace: boolean, direction: 'older' | 'newer' = 'older'): void {
        this.checkPage(page);
        const key = scopeKey(page.scope);
        const previous = this.pages.find(p => scopeKey(p.scope) === key);
        const map = new Map<string, ConversationViewItem>((replace ? [] : previous?.items ?? []).map(item => [item.key, item]));
        for (const item of page.items)
            map.set(item.key, item);
        const held: HeldPage = { scope: page.scope, items: [...map.values()].sort(compareItems), older: replace || direction === 'older' ? page.older : previous?.older ?? null, newer: replace || direction === 'newer' ? page.newer : previous?.newer ?? null };
        this.pages = [...this.pages.filter(p => scopeKey(p.scope) !== key), held];
        if (this.pages.length > 20) {
            const remove = this.pages.find(p => p.scope.kind !== "timeline" && scopeKey(p.scope) !== key);
            if (remove)
                this.pages = this.pages.filter(p => p !== remove);
        }
        this.trim(key, direction);
        this.revision++;
    }
    private trim(preferred: string, direction: 'older' | 'newer'): void {
        let pages = this.pages.map(p => ({ ...p, items: [...p.items] }));
        let count = pages.reduce((sum, p) => sum + p.items.length, 0);
        let bytes = pages.reduce((sum, p) => sum + p.items.reduce((n, item) => n + itemBytes(item), 0), 0);
        while (count > READER_ITEMS || bytes > READER_BODY_BYTES) {
            const removable = pages.filter(p => p.items.length > 0 && !(this.entryHeld && p.items.length === 1 && p.items[0].key === this.entryTarget?.key));
            const page = removable.find(p => scopeKey(p.scope) !== preferred) ?? removable[0];
            if (!page)
                break;
            const targetIndex = this.entryHeld ? page.items.findIndex(item => item.key === this.entryTarget?.key) : -1;
            const removeEnd = targetIndex >= 0 ? targetIndex < page.items.length / 2 : scopeKey(page.scope) === preferred && direction === 'older';
            const removed = removeEnd ? page.items.pop() : page.items.shift();
            if (!removed)
                break;
            count--;
            bytes -= itemBytes(removed);
            const edge = removeEnd ? page.items.at(-1) : page.items[0];
            if (edge) {
                const cursor: ViewPageCursor = { projection_version: VIEW_PROJECTION_VERSION, lens: this.lens, scope: page.scope, order: edge.order };
                if (removeEnd)
                    page.newer = cursor;
                else
                    page.older = cursor;
            }
        }
        this.pages = pages.filter(p => p.items.length > 0 || scopeKey(p.scope) === preferred);
    }
    async connect(): Promise<void> {
        if (this.disposed || this.requests.size > 0 || this.entryStarted && ['loading', 'placing', 'error'].includes(this.entryPhase))
            return;
        if (this.reconnecting !== null)
            return this.reconnecting;
        const operation = this.open();
        this.reconnecting = operation;
        try {
            await operation;
        }
        finally {
            if (this.reconnecting === operation)
                this.reconnecting = null;
        }
    }
    private async open(): Promise<void> {
        this.releaseSource();
        this.source = null;
        if (this.retry !== null) {
            clearTimeout(this.retry);
            this.retry = null;
        }
        const generation = ++this.generation;
        this.loading = true;
        const query: Record<string, string> = { transcript: String(this.transcript) };
        if (this.coverage > 0) {
            query.after = String(this.coverage);
            query.ranges = JSON.stringify(this.pages.filter(p => p.items.length).map(p => ({ scope: p.scope, first: p.items[0]!.order, last: p.items.at(-1)!.order })));
        }
        await new Promise<void>((resolve, reject) => {
            let settled = false;
            const source = new EventSource(this.url('/tail', query));
            this.source = source;
            const active = () => !this.disposed && generation === this.generation;
            const ready = () => { this.loading = false; this.trouble = false; this.changed(); if (!settled) {
                settled = true;
                resolve();
            } };
            const fail = (error: unknown) => { if (!active())
                return; source.close(); this.source = null; this.loading = false; this.trouble = true;
                if (this.entryPhase === 'loading' && !this.entryStarted) this.failEntry();
                if (this.status) this.status = { ...this.status, authority: 'unknown', turn_running: null, pending_permission: null, pending_user_input: null, live_output: null, live_tool_key: null, live_tool_progress: null };
                this.changed();
                if (!settled) {
                settled = true;
                reject(error);
            } this.retry = setTimeout(() => { this.retry = null; void this.connect().catch(() => undefined); }, 1500); };
            source.addEventListener('snapshot', async (event) => {
                if (!active())
                    return;
                try {
                    const value = JSON.parse((event as MessageEvent<string>).data) as ConversationViewSnapshot;
                    this.checkPage(value.page);
                    // A reconnect's snapshot is the newest page of a conversation already
                    // on screen. Merge it. Nothing about identity changed.
                    if (this.wholeHistoryIsSuspect) {
                        this.wholeHistoryIsSuspect = false;
                        this.pages = [];
                    }
                    this.admitSnapshot(value.page);
                    if (!this.receivedSnapshot) {
                        this.receivedSnapshot = true;
                        this.initialLiveOutput = value.status.live_output ? JSON.stringify(value.status.live_output.reference) : null;
                        this.initialLiveOutputTurn = value.status.live_output ? JSON.stringify([value.status.live_output.runtime_epoch, value.status.live_output.turn_key]) : null;
                    }
                    this.noteStatus(value.status);
                    this.coverage = value.coverage.through_sequence;
                    if (this.restoring && this.initialAnchor) {
                        const cursor: ViewPageCursor = { projection_version: VIEW_PROJECTION_VERSION, lens: this.lens, scope: timeline, order: { ...this.initialAnchor.order, slot: this.initialAnchor.order.slot + 1 } };
                        await this.load(timeline, cursor, 'older', true);
                        this.restoring = false;
                    }
                    if (this.entryPhase === 'loading' && !this.entryStarted) {
                        if (value.coverage.through_sequence <= (this.entryReadThroughSequence ?? 0)) {
                            // The admitted snapshot proves that there is no unread range.
                            this.entryStarted = true;
                            this.entryTarget = null;
                            this.entryTargetReady = true;
                            this.entryPhase = 'placing';
                        } else void this.prepareEntry();
                    }
                    ready();
                }
                catch (error) {
                    fail(error);
                }
            });
            source.addEventListener('changes', (event) => {
                if (!active())
                    return;
                try {
                    const value = JSON.parse((event as MessageEvent<string>).data) as ViewTailChanges;
                    if (value.conversation_id !== this.id || value.lens !== this.lens || value.from_sequence !== this.coverage || value.upserts.length > 128)
                        throw new Error('Conversation view gap');
                    const epochChanged = this.status !== null && this.status.runtime_epoch !== value.status.runtime_epoch;
                    if (epochChanged) {
                        this.rereadHistory();
                        source.close();
                        this.source = null;
                        this.retry = setTimeout(() => { this.retry = null; void this.connect().catch(() => undefined); }, 0);
                        if (!settled) {
                            settled = true;
                            resolve();
                        }
                        return;
                    }
                    const stoppedChanged = this.status?.stopped_without_ending !== value.status.stopped_without_ending;
                    this.noteStatus(value.status);
                    this.coverage = value.coverage.through_sequence;
                    if (this.transcript && (value.upserts.length > 0 || value.removed_keys.length > 0 || value.scope_changes.some(change => change.removed_keys.length > 0) || stoppedChanged)) {
                        const updates = new Map(value.upserts.map(item => [item.key, item]));
                        this.pages = this.pages.map(page => {
                            const membership = value.scope_changes.find(change => scopeKey(change.scope) === scopeKey(page.scope));
                            const removed = new Set([...value.removed_keys, ...membership?.removed_keys ?? []]);
                            const map = new Map(page.items.filter(item => !removed.has(item.key)).map(item => [item.key, item]));
                            for (const key of membership?.item_keys ?? []) {
                                const item = updates.get(key);
                                if (!item) continue;
                                // A historical window must not jump to the newest timeline.
                                if (map.has(key) || page.newer === null || (item.order.sequence >= (page.items[0]?.order.sequence ?? 0) && item.order.sequence <= (page.items.at(-1)?.order.sequence ?? 0))) map.set(key, item);
                            }
                            return { ...page, items: [...map.values()].sort(compareItems) };
                        });
                        if (stoppedChanged && value.status.rest.latest_turn) {
                            const latest = value.status.rest.latest_turn;
                            this.pages = this.pages.map(page => ({ ...page, items: page.items.map(item => item.key === latest.key ? latest : item) }));
                        }
                        this.trim(scopeKey(timeline), 'newer');
                        if (stoppedChanged) {
                            // Runtime stop has no durable row to change pending ask bodies.
                            // Re-read only held windows under the normal serialized demand bound.
                            for (const page of this.pages) {
                                const last = page.items.at(-1);
                                const cursor: ViewPageCursor | null = page.newer && last ? { projection_version: VIEW_PROJECTION_VERSION, lens: this.lens, scope: page.scope, order: { sequence: last.order.sequence, slot: last.order.slot + 1 } } : null;
                                void this.load(page.scope, cursor, 'older', true);
                            }
                        }
                        for (const scope of value.invalidated_scopes) {
                            if (this.pages.some(p => scopeKey(p.scope) === scopeKey(scope)))
                                void this.load(scope, null, 'older', true);
                        }
                    }
                    this.revision++;
                    ready();
                }
                catch (error) {
                    this.rereadHistory();
                    fail(error);
                }
            });
            source.addEventListener('reset', () => { if (!active()) return; this.rereadHistory(); fail(new Error('Conversation view reset')); });
            source.onerror = () => fail(new Error('Conversation disconnected'));
            const abort = () => { source.close(); if (!settled) {
                settled = true;
                resolve();
            } };
            this.abort.signal.addEventListener('abort', abort, { once: true });
            this.releaseSource = () => { this.abort.signal.removeEventListener('abort', abort); abort(); };
        });
    }
    async load(scope: ViewHistoryScope = timeline, cursor: ViewPageCursor | null = null, direction: 'older' | 'newer' = 'older', replace = false): Promise<boolean> {
        if (this.disposed) return false;
        const key = scopeKey(scope);
        const pending = this.requests.get(key);
        if (pending) {
            if (sameScope(pending.scope, scope) && sameCursor(pending.cursor, cursor) && pending.direction === direction && pending.replace === replace)
                return pending.operation;
            if (!replace) return false;
            pending.abort.abort();
        }
        // Replacement reserves the existing scope slot synchronously, even at capacity.
        // The same bound applies to held scopes and queued demand. No response bodies queue.
        if (!pending && this.requests.size >= 20) return false;
        const controller = new AbortController();
        const request = { operation: Promise.resolve(false), abort: controller, scope, cursor, direction, replace };
        const active = () => !this.disposed && !controller.signal.aborted && this.requests.get(key) === request;
        const operation = this.pageQueue.then(async () => {
            if (!active()) return false;
            // Stop source admission before reading a page. Coverage stays unchanged. The
            // reconnect replays writes during this read for every retained scope, including
            // the newly admitted page. A live writer therefore cannot starve history reads.
            this.pauseTail();
            try {
                const page = await this.get<ConversationViewPage>('/pages', { scope: JSON.stringify(scope), ...(cursor ? { cursor: JSON.stringify(cursor) } : {}), direction }, controller.signal);
                if (!active()) return false;
                if (!sameScope(page.scope, scope)) throw new Error('Conversation view scope changed');
                this.accept(page, replace || cursor === null, direction);
                this.changed();
                return true;
            }
            catch {
                if (active()) this.trouble = true;
                return false;
            }
            finally {
                if (this.requests.get(key) === request) this.requests.delete(key);
                if (!this.disposed) {
                    if (this.rangeTimer !== null) clearTimeout(this.rangeTimer);
                    this.rangeTimer = setTimeout(() => { this.rangeTimer = null; void this.connect().catch(() => undefined); }, 0);
                }
            }
        });
        request.operation = operation;
        this.requests.set(key, request);
        this.pageQueue = operation.then(() => undefined, () => undefined);
        return operation;
    }
    async expand(scope: ViewHistoryScope, open: boolean): Promise<void> {
        const key = scopeKey(scope);
        if (open) {
            this.expanded.add(key);
            while (this.expanded.size > 300)
                this.expanded.delete(this.expanded.values().next().value!);
            await this.load(scope);
        }
        else {
            this.expanded.delete(key);
            this.requests.get(key)?.abort.abort();
            this.requests.delete(key);
            this.pages = this.pages.filter(p => scopeKey(p.scope) !== key);
            this.revision++;
        }
    }
    isExpanded(scope: ViewHistoryScope): boolean { void this.revision; return this.expanded.has(scopeKey(scope)); }
    async body(reference: ViewBodyReference, signal: AbortSignal): Promise<string> {
        const response = await fetch(this.url('/body/content', { reference: JSON.stringify(reference) }), { signal });
        if (!response.ok) {
            if (isDiagnosticExpiry(await response.text())) throw new DiagnosticExpiredError();
            throw new Error(`Conversation body: ${response.status}`);
        }
        if (!response.body) throw new Error(`Conversation body: ${response.status}`);
        const stream = response.body.getReader();
        const decoder = new TextDecoder('utf-8', { fatal: true });
        const chunks: string[] = [];
        let bytes = 0;
        try {
            while (true) {
                const next = await stream.read();
                if (next.done)
                    break;
                bytes += next.value.byteLength;
                if (bytes > reference.byte_count)
                    throw new Error('Conversation body length changed');
                chunks.push(decoder.decode(next.value, { stream: true }));
            }
            chunks.push(decoder.decode());
            if (bytes !== reference.byte_count)
                throw new Error('Conversation body is incomplete');
            return chunks.join('');
        }
        finally {
            await stream.cancel().catch(() => undefined);
            stream.releaseLock();
        }
    }
    async receipts(ids: readonly string[]): Promise<ViewMessageReceipts> { return this.get('/receipts', { message_ids: JSON.stringify(ids.slice(0, 64)) }); }
    close(): void { this.entryOperation++; this.disposed = true; this.generation++; this.releaseSource(); this.source = null; this.abort.abort(); this.cancelPages(); if (this.retry !== null)
        clearTimeout(this.retry); this.retry = null; this.pages = []; this.status = null; this.requests.clear(); if (this.rangeTimer !== null)
        clearTimeout(this.rangeTimer); this.rangeTimer = null; }
}

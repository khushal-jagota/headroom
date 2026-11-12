import Foundation
import Observation

/// Domain-owned read declaration. Query parameters are part of the server key.
struct ResourceRegistration<Value: Decodable & Sendable> {
    let key: String
    var versioned = true
    var nextRefreshAt: (@Sendable (Value) -> Double?)? = nil
    var decode: @Sendable (Data) throws -> Value = { try APIClient.decoder().decode(Value.self, from: $0) }
}

enum ResourceLimits {
    static let probeBatch = 64
    static let concurrentReads = 3
    static let inactiveEntries = 64
    static let inactiveBytes = 4 * 1024 * 1024
    static let inactiveSeconds: TimeInterval = 300
    static let coalescingSeconds = 1.5
}

@MainActor @Observable final class ResourceEntry {
    let key: String
    let server: String
    let versioned: Bool
    var publication = 0
    var data: Data?
    var error: String?
    var failure: ServerReadFailure?
    var isStale = true
    var demands = 0
    var lastUsed = Date()
    var epoch: String?
    var revision: String?
    var generation = 0
    var nextRefreshAt: Double?
    @ObservationIgnored var deadlineForData: ((Data) throws -> Double?)?
    @ObservationIgnored var validate: ((Data) throws -> Void)?
    @ObservationIgnored var decoded: [String: Any] = [:]
    @ObservationIgnored var task: Task<Void, Never>?
    @ObservationIgnored var deadline: Task<Void, Never>?
    init(key: String, server: String, versioned: Bool) { self.key = key; self.server = server; self.versioned = versioned }
}

/// A handle reads shared accepted state. Each mounted scope acquires and releases demand.
@MainActor @Observable final class ResourceHandle<Value: Decodable & Sendable> {
    private(set) var entry: ResourceEntry
    private let owner: ResourceOwner
    private let registration: ResourceRegistration<Value>
    init(entry: ResourceEntry, owner: ResourceOwner, registration: ResourceRegistration<Value>) {
        self.entry = entry; self.owner = owner; self.registration = registration
    }
    var value: Value? {
        _ = entry.publication
        let type = String(reflecting: Value.self)
        if let value = entry.decoded[type] as? Value { return value }
        guard let data = entry.data else { return nil }
        guard let value = try? registration.decode(data) else { return nil }
        entry.decoded[type] = value
        return value
    }
    var error: String? {
        if let error = entry.error { return error }
        if entry.decoded[String(reflecting: Value.self)] != nil { return nil }
        guard let data = entry.data else { return nil }
        do { _ = try registration.decode(data); return nil } catch { return error.localizedDescription }
    }
    var failure: ServerReadFailure? { entry.failure }
    var isStale: Bool { entry.isStale }
    var key: String { entry.key }
    func acquire() {
        entry = owner.canonical(entry)
        entry.demands += 1; entry.lastUsed = Date()
        if entry.demands == 1 { owner.mounted(entry) }
    }
    func release() { owner.release(entry) }
    func refresh() async {
        await owner.refresh(entry)
        if let value, let deadline = registration.nextRefreshAt?(value) { owner.schedule(entry, at: deadline) }
    }
    /// Used only for accepted mutation results. It never attaches a probed revision.
    func accept(_ value: Value) where Value: Encodable {
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        entry.data = try? encoder.encode(value)
        entry.epoch = nil; entry.revision = nil
        entry.decoded = [String(reflecting: Value.self): value]
        entry.publication += 1
        entry.isStale = true
    }
}

/// Transport adapter also permits deterministic admission races without a server host.
struct ResourceRead: Sendable {
    let data: Data
    var epoch: String? = nil
    var revision: String? = nil
}
struct ResourceTransport: Sendable {
    var versions: @Sendable (APIClient, [String]) async throws -> ResourceVersions
    var read: @Sendable (APIClient, String, Bool) async throws -> ResourceRead
    static let live = ResourceTransport(versions: { try await $0.resourceVersions($1) }, read: { api, key, versioned in
        if versioned {
            do {
                let (wire, _) = try await api.responseData("/api/resource-body", query: [.init(name: "key", value: key)])
                let envelope = try JSONSerialization.jsonObject(with: wire) as? [String: Any]
                guard envelope?["key"] as? String == key,
                      let epoch = envelope?["epoch"] as? String, let revision = envelope?["revision"] as? String,
                      let body = envelope?["body"] else { throw URLError(.badServerResponse) }
                return ResourceRead(data: try JSONSerialization.data(withJSONObject: body, options: .fragmentsAllowed), epoch: epoch, revision: revision)
            } catch { try Task.checkCancellation() }
        }
        guard let parts = URLComponents(string: key) else { throw URLError(.badURL) }
        return ResourceRead(data: try await api.responseData(parts.path, query: parts.queryItems ?? []).0)
    })
}

/// Only admitted display data enters this schema. Restoration never grants freshness.
struct ResourceCacheEnvelope: Codable {
    static let schema = 1
    let schemaIdentity: Int
    let server: String
    let key: String
    let data: Data
    let epoch: String?
    let revision: String?
    static func identity(_ key: String) -> String { "resource-body:v1:" + key }
}

/// One raw body per normalized server and exact key. Navigation and tails remain separate.
@MainActor final class ResourceOwner {
    private var api: APIClient
    private let cacheStore: LocalStore
    private let transport: ResourceTransport
    private var generation = 0
    private var entries: [String: ResourceEntry] = [:]
    private var activeReads = 0
    private var running = false
    private var pending = false
    private var reconciliation: Task<Void, Never>?
    private var wake: Task<Void, Never>?
    private var changes: Task<Void, Never>?
    private var expiry: Task<Void, Never>?
    init(api: APIClient, transport: ResourceTransport = .live, cacheStore: LocalStore = .shared) {
        self.api = api; self.transport = transport; self.cacheStore = cacheStore
    }
    var server: String { LocalStore.server(api.baseURL) }
    var admissionGeneration: Int { generation }
    func handle<T>(_ registration: ResourceRegistration<T>) -> ResourceHandle<T> {
        evict()
        let identity = server + ":" + registration.key
        let entry: ResourceEntry
        if let existing = entries[identity] { entry = existing }
        else {
            entry = ResourceEntry(key: registration.key, server: server, versioned: registration.versioned)
            if let payload = try? cacheStore.record(server: server, kind: "cache", id: ResourceCacheEnvelope.identity(entry.key))?.payload,
               let saved = try? JSONDecoder().decode(ResourceCacheEnvelope.self, from: payload),
               saved.schemaIdentity == ResourceCacheEnvelope.schema, saved.server == server, saved.key == entry.key,
               (saved.epoch == nil) == (saved.revision == nil), (try? registration.decode(saved.data)) != nil {
                entry.data = saved.data; entry.epoch = saved.epoch; entry.revision = saved.revision
                // A saved revision is display metadata, never admission for this run.
                entry.isStale = true
            }
            entries[identity] = entry
        }
        entry.validate = { _ = try registration.decode($0) }
        if let next = registration.nextRefreshAt { entry.deadlineForData = { next(try registration.decode($0)) } }
        return ResourceHandle(entry: entry, owner: self, registration: registration)
    }
    func configure(api: APIClient) {
        stop()
        self.api = api
        // Even an A → B → A switch receives a new generation.
        for entry in entries.values { entry.isStale = true }
    }
    func start() {
        guard !running else { return }
        running = true
        evict()
        let expected = generation, api = api
        changes = Task { [weak self] in
            guard let self else { return }
            await reconcile()
            while !Task.isCancelled, generation == expected {
                do {
                    for try await _ in api.changes() {
                        guard generation == expected, !Task.isCancelled else { return }
                        signal()
                    }
                } catch { if Task.isCancelled { return } }
                guard generation == expected else { return }
                await reconcile()
                do { try await Task.sleep(for: .seconds(3)) } catch { return }
            }
        }
    }
    func stop() {
        generation += 1; running = false; pending = false
        changes?.cancel(); changes = nil; wake?.cancel(); wake = nil; expiry?.cancel(); expiry = nil
        reconciliation?.cancel(); reconciliation = nil
        for entry in entries.values {
            entry.generation += 1; entry.task?.cancel(); entry.task = nil
            entry.deadline?.cancel(); entry.deadline = nil; entry.nextRefreshAt = nil; entry.isStale = true
        }
    }
    func signal() {
        guard wake == nil else { return }
        let expected = generation
        wake = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(ResourceLimits.coalescingSeconds)) } catch { return }
            guard let self, generation == expected else { return }
            wake = nil
            await reconcile()
        }
    }
    func invalidate(where affected: (String) -> Bool) {
        invalidate(entries.values.filter { $0.server == server && affected($0.key) }.map(\.key))
    }
    func invalidate(_ keys: [String]) {
        for entry in entries.values where entry.server == server && keys.contains(entry.key) {
            entry.isStale = true; supersede(entry)
            if entry.demands > 0 { Task { await self.refresh(entry) } }
        }
    }
    func reconcile() async {
        pending = true
        if let reconciliation { await reconciliation.value; return }
        let expected = generation
        let task = Task { [weak self] in
            await Task.yield()
            guard let self else { return }
            while pending, generation == expected, !Task.isCancelled {
                pending = false
                await probe(expected: expected)
            }
        }
        reconciliation = task
        await task.value
        if generation == expected { reconciliation = nil }
    }
    private func probe(expected: Int) async {
        let active = entries.values.filter { $0.server == server && $0.demands > 0 }
        let keys = active.filter(\.versioned).map(\.key)
        let api = api
        var versions: [String: (String, String?)] = [:]
        var failed = false
        do {
            for offset in stride(from: 0, to: keys.count, by: ResourceLimits.probeBatch) {
                let batch = Array(keys[offset..<min(offset + ResourceLimits.probeBatch, keys.count)])
                let result = try await transport.versions(api, batch)
                guard expected == generation, !Task.isCancelled else { return }
                for resource in result.resources where batch.contains(resource.key) { versions[resource.key] = (result.epoch, resource.revision) }
            }
        } catch { failed = true }
        guard expected == generation, !Task.isCancelled else { return }
        await withTaskGroup(of: Void.self) { group in
            for entry in active where entry.demands > 0 {
                let stamp = versions[entry.key]
                if failed || !entry.versioned || stamp == nil || stamp?.1 == nil || entry.data == nil ||
                    entry.epoch != stamp?.0 || entry.revision != stamp?.1 || entry.isStale {
                    // A newer probe revokes an earlier body, even if cancellation arrives late.
                    supersede(entry); entry.isStale = true
                    group.addTask { await self.refresh(entry) }
                }
            }
        }
        evict()
    }
    private func supersede(_ entry: ResourceEntry) {
        entry.generation += 1; entry.task?.cancel(); entry.task = nil
    }
    fileprivate func canonical(_ entry: ResourceEntry) -> ResourceEntry {
        let key = entry.server + ":" + entry.key
        if let current = entries[key] { return current }
        entries[key] = entry
        return entry
    }
    fileprivate func mounted(_ entry: ResourceEntry) {
        guard entry.server == server else { return }
        Task { await self.refresh(entry) }
    }
    fileprivate func release(_ entry: ResourceEntry) {
        entry.demands = max(0, entry.demands - 1); entry.lastUsed = Date()
        if entry.demands == 0 { supersede(entry); entry.deadline?.cancel(); entry.deadline = nil; entry.nextRefreshAt = nil }
        evict()
    }
    fileprivate func refresh(_ entry: ResourceEntry) async {
        guard entry.server == server else { return }
        if let task = entry.task { await task.value; return }
        let expected = generation, readGeneration = entry.generation, api = api
        let task = Task { [weak self] in
            guard let self else { return }
            while activeReads >= ResourceLimits.concurrentReads {
                do { try await Task.sleep(for: .milliseconds(10)) } catch { return }
                guard expected == generation, readGeneration == entry.generation else { return }
            }
            guard expected == generation, readGeneration == entry.generation, !Task.isCancelled else { return }
            activeReads += 1
            defer { activeReads -= 1 }
            do {
                let result = try await transport.read(api, entry.key, entry.versioned)
                let data = result.data, epoch = result.epoch, revision = result.revision
                guard expected == generation, readGeneration == entry.generation, entry.server == server, !Task.isCancelled else { return }
                try entry.validate?(data)
                entry.data = data; entry.publication += 1; entry.decoded = [:]; entry.epoch = epoch; entry.revision = revision
                entry.isStale = revision == nil; entry.error = nil; entry.failure = nil; entry.lastUsed = Date()
                let saved = ResourceCacheEnvelope(schemaIdentity: ResourceCacheEnvelope.schema, server: entry.server,
                                                  key: entry.key, data: data, epoch: epoch, revision: revision)
                if let payload = try? JSONEncoder().encode(saved) {
                    cacheStore.enqueueCacheResponse(server: entry.server, id: ResourceCacheEnvelope.identity(entry.key), data: payload)
                }
                if let deadline = try entry.deadlineForData?(data) { schedule(entry, at: deadline) }
            } catch {
                guard expected == generation, readGeneration == entry.generation, !Task.isCancelled else { return }
                entry.error = error.localizedDescription; entry.failure = ServerReadFailure(error); entry.isStale = true
            }
        }
        entry.task = task
        await task.value
        if expected == generation, readGeneration == entry.generation { entry.task = nil }
    }
    fileprivate func schedule(_ entry: ResourceEntry, at timestamp: Double) {
        guard entry.demands > 0, timestamp > Date().timeIntervalSince1970, entry.nextRefreshAt != timestamp else { return }
        entry.nextRefreshAt = timestamp; entry.deadline?.cancel()
        let expected = generation
        entry.deadline = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(max(0, timestamp - Date().timeIntervalSince1970))) } catch { return }
            guard let self, expected == generation, entry.demands > 0 else { return }
            entry.nextRefreshAt = nil; entry.isStale = true; await refresh(entry)
        }
    }
    private func evict() {
        let inactive = entries.values.filter { $0.demands == 0 }.sorted { $0.lastUsed > $1.lastUsed }
        var bytes = 0
        for (index, entry) in inactive.enumerated() {
            bytes += entry.data?.count ?? 0
            if index >= ResourceLimits.inactiveEntries || bytes > ResourceLimits.inactiveBytes || Date().timeIntervalSince(entry.lastUsed) > ResourceLimits.inactiveSeconds {
                supersede(entry); entry.data = nil; entry.publication += 1; entry.decoded = [:]; entry.isStale = true
                entries[entry.server + ":" + entry.key] = nil
            }
        }
        expiry?.cancel(); expiry = nil
        if let oldest = entries.values.filter({ $0.demands == 0 }).map(\.lastUsed).min() {
            let delay = max(0.01, ResourceLimits.inactiveSeconds - Date().timeIntervalSince(oldest))
            expiry = Task { [weak self] in
                do { try await Task.sleep(for: .seconds(delay)) } catch { return }
                self?.evict()
            }
        }
    }
}

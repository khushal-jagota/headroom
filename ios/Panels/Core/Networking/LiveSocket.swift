import Foundation

// One WebSocket carries every live feed of the phone: the change signal and each
// subscription (navigation view, conversation view tail). The contract is
// `server/live_updates/contracts.ts`; `docs/live-updates.md` describes it in prose.
//
// This type only moves frames. A consumer keeps its own recovery: when the socket ends,
// each open stream ends with an error, and its owner reopens it with its own backoff and
// cursors, as it did with an event stream. The server replays nothing.

/// One server frame. `data` holds the payload bytes exactly as the server wrote them.
struct LiveEnvelope: Sendable {
    let seq: Int
    let sub: String?
    let kind: String
    let data: Data?

    private struct Header: Decodable { let seq: Int; let sub: String?; let kind: String }
    private static let marker = Data(#","data":"#.utf8)

    /// The server writes `seq`, `sub` and `kind` first and `data` last, so the payload is
    /// everything after the first `,"data":`. It is not parsed here: its consumer decodes it once.
    static func parse(_ text: String) throws -> LiveEnvelope {
        let bytes = Data(text.utf8)
        guard let found = bytes.range(of: marker) else {
            let header = try JSONDecoder().decode(Header.self, from: bytes)
            return LiveEnvelope(seq: header.seq, sub: header.sub, kind: header.kind, data: nil)
        }
        let header = try JSONDecoder().decode(Header.self, from: bytes[..<found.lowerBound] + Data("}".utf8))
        guard bytes.last == UInt8(ascii: "}") else { throw LiveSocketError.malformed }
        return LiveEnvelope(seq: header.seq, sub: header.sub, kind: header.kind, data: bytes[found.upperBound..<(bytes.count - 1)])
    }
}

/// One payload of a subscription: `kind` is the old stream's event name.
struct LiveEvent: Sendable {
    let kind: String
    let data: Data
}

enum LiveSocketError: Error, Equatable {
    /// A frame that is not an envelope.
    case malformed
    /// A skipped sequence number: the socket lost a frame.
    case sequenceGap
    /// The server closed the socket. 4001 is "update needed"; 4002 and 4003 are named too.
    case closed(code: Int)
}

/// What the socket needs of a transport, so a test can drive frames without a network.
protocol LiveSocketConnection: Sendable {
    func send(_ text: String) async throws
    /// The next text frame. Throws `LiveSocketError.closed` when the server closes with a code.
    func receive() async throws -> String
    func close()
}
protocol LiveSocketTransport: Sendable {
    func connect(_ request: URLRequest) -> any LiveSocketConnection
}

/// A native `URLSessionWebSocketTask`: its own HTTP/1.1 connection, so a network change that
/// closes every HTTP/2 stream on the same host does not end it.
struct URLSessionLiveTransport: LiveSocketTransport {
    let session: URLSession
    func connect(_ request: URLRequest) -> any LiveSocketConnection {
        let task = session.webSocketTask(with: request)
        // A navigation snapshot can pass the 1 MiB default.
        task.maximumMessageSize = 32 * 1024 * 1024
        task.resume()
        return Connection(task: task)
    }
    private struct Connection: LiveSocketConnection, @unchecked Sendable {
        let task: URLSessionWebSocketTask
        func send(_ text: String) async throws { try await task.send(.string(text)) }
        func receive() async throws -> String {
            do {
                switch try await task.receive() {
                case .string(let text): return text
                case .data(let data): return String(decoding: data, as: UTF8.self)
                @unknown default: throw LiveSocketError.malformed
                }
            } catch {
                // A close frame ends `receive`; the code is on the task.
                let code = task.closeCode.rawValue
                if code >= 4000 { throw LiveSocketError.closed(code: code) }
                throw error
            }
        }
        func close() { task.cancel(with: .normalClosure, reason: nil) }
    }
}

actor LiveSocket {
    /// Two missed heartbeats and a margin: a socket that sends nothing for this long is replaced.
    static let silence: Duration = .seconds(35)
    static let path = "/api/live"

    private struct Subscription {
        let kind: String
        let conversationID: String?
        let params: [String: String]
        let continuation: AsyncThrowingStream<LiveEvent, Error>.Continuation
        var sent = false
    }

    /// The same server, session and identity as the client that made it. It carries no socket.
    private let api: APIClient
    private let transport: any LiveSocketTransport
    private var connection: (any LiveSocketConnection)?
    private var generation = 0
    private var ready = false
    private var lastSequence = 0
    private var subscriptions: [String: Subscription] = [:]
    private var watchers: [Int: AsyncThrowingStream<Void, Error>.Continuation] = [:]
    private var nextID = 0
    private var silenceTask: Task<Void, Never>?

    init(api: APIClient, transport: (any LiveSocketTransport)? = nil) {
        var plain = api; plain.live = nil
        self.api = plain
        self.transport = transport ?? URLSessionLiveTransport(session: api.session)
    }

    // MARK: Feeds

    /// One value when the socket is ready, so a consumer resyncs after every connect, then one
    /// for each change ping. It buffers one: a refresh covers every commit before it.
    func changes() -> AsyncThrowingStream<Void, Error> {
        let (stream, continuation) = AsyncThrowingStream<Void, Error>.makeStream(bufferingPolicy: .bufferingNewest(1))
        nextID += 1
        let id = nextID
        watchers[id] = continuation
        continuation.onTermination = { [weak self] _ in Task { await self?.stopWatching(id) } }
        if ready { continuation.yield(()) } else { open() }
        return stream
    }

    /// One subscription. Its stream ends normally when the server ends the subscription, and
    /// with an error when the subscribe fails or the socket ends. Cancelling it unsubscribes.
    /// No frame is dropped: a missing frame is a gap.
    func events(kind: String, conversationID: String? = nil, params: [String: String] = [:]) -> AsyncThrowingStream<LiveEvent, Error> {
        let (stream, continuation) = AsyncThrowingStream<LiveEvent, Error>.makeStream()
        nextID += 1
        let id = "s\(nextID)"
        subscriptions[id] = Subscription(kind: kind, conversationID: conversationID, params: params, continuation: continuation)
        continuation.onTermination = { [weak self] _ in Task { await self?.unsubscribe(id) } }
        if ready { sendSubscribe(id) } else { open() }
        return stream
    }

    // MARK: Connection

    private func open() {
        guard connection == nil else { return }
        generation += 1
        let generation = generation
        ready = false
        lastSequence = 0
        let request: URLRequest
        do {
            request = try api.request(try api.url(Self.path))
        } catch { return end(generation, error) }
        let connection = transport.connect(request)
        self.connection = connection
        armSilence(generation)
        Task { await self.run(connection, generation) }
    }

    private func run(_ connection: any LiveSocketConnection, _ generation: Int) async {
        let attempt = await api.serverConnection?.attempt(server: api.baseURL)
        do {
            try await connection.send(try api.liveHello())
            while !Task.isCancelled, generation == self.generation {
                let text = try await connection.receive()
                guard generation == self.generation else { return }
                // Every frame is contact with the server, as a stream line was.
                await api.serverConnection?.answered(attempt)
                try handle(try LiveEnvelope.parse(text), generation)
            }
        } catch {
            end(generation, error)
        }
    }

    private func handle(_ envelope: LiveEnvelope, _ generation: Int) throws {
        // Each frame is the next number. A skipped number means that a frame was lost.
        guard envelope.seq == lastSequence + 1 else { throw LiveSocketError.sequenceGap }
        lastSequence = envelope.seq
        armSilence(generation)
        guard let id = envelope.sub else {
            switch envelope.kind {
            case "ready":
                ready = true
                for id in subscriptions.keys where subscriptions[id]?.sent == false { sendSubscribe(id) }
                for watcher in watchers.values { watcher.yield(()) }
            case "change":
                for watcher in watchers.values { watcher.yield(()) }
            default: break
            }
            return
        }
        guard let subscription = subscriptions[id] else { return }
        if envelope.kind == "ended" {
            subscriptions[id] = nil
            struct Ended: Decodable { let status: Int?; let detail: AnyDetail? }
            let ended = envelope.data.flatMap { try? JSONDecoder().decode(Ended.self, from: $0) }
            if let status = ended?.status {
                subscription.continuation.finish(throwing: APIError(status: status, message: ended?.detail?.message ?? "Server returned HTTP \(status)."))
            } else { subscription.continuation.finish() }
            return
        }
        if let data = envelope.data { subscription.continuation.yield(LiveEvent(kind: envelope.kind, data: data)) }
    }

    /// The one place a socket ends. Every open stream is told, and each owner recovers itself.
    private func end(_ generation: Int, _ error: Error) {
        guard generation == self.generation else { return }
        let failure = Self.failure(error, server: api.baseURL)
        stop()
        let open = subscriptions.values, watching = watchers.values
        subscriptions = [:]; watchers = [:]
        for subscription in open { subscription.continuation.finish(throwing: failure) }
        for watcher in watching { watcher.finish(throwing: failure) }
    }

    /// Close without telling anyone: the caller already decided.
    private func stop() {
        generation += 1
        ready = false
        silenceTask?.cancel(); silenceTask = nil
        connection?.close(); connection = nil
    }

    /// "Update needed" uses the existing update flow; every other close is a drop.
    private static func failure(_ error: Error, server: URL) -> Error {
        if case LiveSocketError.closed(let code) = error, code == 4001 {
            NativeBuildGate.requireUpdate(server)
            return APIError(status: 426, message: NativeBuildGate.message)
        }
        return error
    }

    private func armSilence(_ generation: Int) {
        silenceTask?.cancel()
        silenceTask = Task { [weak self] in
            do { try await Task.sleep(for: Self.silence) } catch { return }
            await self?.end(generation, URLError(.timedOut))
        }
    }

    // MARK: Subscriptions

    private func sendSubscribe(_ id: String) {
        guard let subscription = subscriptions[id], let connection else { return }
        subscriptions[id]?.sent = true
        var frame: [String: Any] = ["op": "subscribe", "id": id, "kind": subscription.kind, "params": subscription.params]
        if let conversationID = subscription.conversationID { frame["conversation_id"] = conversationID }
        send(frame, on: connection)
    }

    private func unsubscribe(_ id: String) {
        guard let subscription = subscriptions.removeValue(forKey: id) else { return }
        if subscription.sent, let connection { send(["op": "unsubscribe", "id": id], on: connection) }
        closeIfIdle()
    }

    private func stopWatching(_ id: Int) {
        watchers[id] = nil
        closeIfIdle()
    }

    /// No stream left, as when the app goes to the background: the socket closes.
    private func closeIfIdle() {
        if subscriptions.isEmpty, watchers.isEmpty { stop() }
    }

    private func send(_ frame: [String: Any], on connection: any LiveSocketConnection) {
        guard let data = try? JSONSerialization.data(withJSONObject: frame, options: [.sortedKeys, .withoutEscapingSlashes]) else { return }
        let text = String(decoding: data, as: UTF8.self), generation = generation
        Task { do { try await connection.send(text) } catch { await self.end(generation, error) } }
    }
}

/// A subscribe failure's detail is a message or an error object that holds one.
private struct AnyDetail: Decodable {
    let message: String?
    init(from decoder: Decoder) throws {
        struct Object: Decodable { let message: String? }
        if let text = try? decoder.singleValueContainer().decode(String.self) { message = text }
        else { message = (try? Object(from: decoder))?.message }
    }
}

extension APIClient {
    /// The first frame: who this phone is, in the shape of the `X-Panels-Client` header.
    func liveHello() throws -> String {
        let identity = try JSONSerialization.jsonObject(with: Data(try NativeClientIdentity.header().utf8))
        let data = try JSONSerialization.data(withJSONObject: ["op": "hello", "client": identity], options: [.sortedKeys])
        return String(decoding: data, as: UTF8.self)
    }

    /// The server's payload-free change signal over the socket.
    func changes() -> AsyncThrowingStream<Void, Error> {
        guard let live else { return Self.withoutSocket() }
        return AsyncThrowingStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
            let task = Task {
                do {
                    for try await _ in await live.changes() { continuation.yield(()) }
                    continuation.finish()
                } catch { continuation.finish(throwing: error) }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// One subscription's frames, mapped off the main actor by `transform`. A mapped nil is skipped.
    func liveEvents<T: Sendable>(
        kind: String, conversationID: String? = nil, params: [String: String] = [:],
        transform: @escaping @Sendable (LiveEvent) throws -> T?
    ) -> AsyncThrowingStream<T, Error> {
        guard let live else { return Self.withoutSocket() }
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await event in await live.events(kind: kind, conversationID: conversationID, params: params) {
                        try Task.checkCancellation()
                        if let value = try transform(event) { continuation.yield(value) }
                    }
                    continuation.finish()
                } catch { continuation.finish(throwing: error) }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private static func withoutSocket<T>() -> AsyncThrowingStream<T, Error> {
        AsyncThrowingStream { $0.finish(throwing: URLError(.cannotConnectToHost)) }
    }
}

import XCTest
import os
@testable import Panels

/// The socket owner's own behavior: envelopes, routing, and what ends each stream. The refresh
/// tests cover the owners above it, and the real transport is proved against a server.
final class LiveSocketTests: XCTestCase {
    private func envelope(_ seq: Int, sub: String? = nil, _ kind: String, data: String? = nil) -> String {
        #"{"seq":\#(seq),"sub":\#(sub.map { "\"\($0)\"" } ?? "null"),"kind":"\#(kind)"\#(data.map { #","data":\#($0)"# } ?? "")}"#
    }

    func testEnvelopeKeepsItsPayloadBytesWhateverTheyHold() throws {
        // The payload is the text after the first `,"data":`, so a payload that holds the marker is safe.
        let payload = #"{"note":",\"data\":","text":"line "}"#
        let parsed = try LiveEnvelope.parse(envelope(3, sub: "s1", "snapshot", data: payload))
        XCTAssertEqual(parsed.seq, 3)
        XCTAssertEqual(parsed.sub, "s1")
        XCTAssertEqual(String(decoding: try XCTUnwrap(parsed.data), as: UTF8.self), payload)
        XCTAssertNil(try LiveEnvelope.parse(envelope(1, "change")).data)
    }

    func testRoutesFramesToTheirStreamsAndClosesWhenTheLastOneEnds() async throws {
        let transport = FakeLiveTransport()
        let socket = LiveSocket(api: APIClient(baseURL: URL(string: "https://routing.invalid")!), transport: transport)
        let received = OSAllocatedUnfairLock(initialState: (changes: 0, events: [String](), ended: false))
        let followChanges = Task { for try await _ in await socket.changes() { received.withLock { $0.changes += 1 } } }
        let followView = Task {
            for try await event in await socket.events(kind: "navigation", params: ["view": "{}"]) { received.withLock { $0.events.append(event.kind) } }
            received.withLock { $0.ended = true }
        }
        let connection = try await transport.opened()
        try await connection.waitForSent(1)
        XCTAssertTrue(connection.sent.value[0].contains(#""op":"hello""#))
        // Nothing is subscribed before the socket is ready.
        XCTAssertEqual(connection.sent.value.count, 1)
        connection.deliver(envelope(1, "ready", data: #"{"heartbeat_ms":15000}"#))
        let subscribe = try await connection.sentFrame(at: 1)
        XCTAssertEqual(subscribe["op"] as? String, "subscribe")
        XCTAssertEqual(subscribe["kind"] as? String, "navigation")
        XCTAssertEqual((subscribe["params"] as? [String: String])?["view"], "{}")
        let id = try XCTUnwrap(subscribe["id"] as? String)
        connection.deliver(envelope(2, sub: id, "snapshot", data: #"{"event":"snapshot"}"#))
        connection.deliver(envelope(3, "change"))
        try await until { received.value.events == ["snapshot"] && received.value.changes == 2 }
        connection.deliver(envelope(4, sub: id, "ended", data: #"{"reason":"closed"}"#))
        try await until { received.value.ended }
        XCTAssertFalse(connection.isClosed, "The change stream still follows")
        // Cancelling the last stream, as the background does, closes the socket.
        followChanges.cancel()
        try await until { connection.isClosed }
    }

    private func until(_ condition: () -> Bool) async throws {
        for _ in 0..<500 {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTFail("Timed out")
    }

    func testRefusedSubscriptionFailsAloneAndUnsubscribeIsSent() async throws {
        let transport = FakeLiveTransport()
        let socket = LiveSocket(api: APIClient(baseURL: URL(string: "https://refused.invalid")!), transport: transport)
        var keep = await socket.changes().makeAsyncIterator()
        let refused = await socket.events(kind: "conversation_view", conversationID: "c1")
        let connection = try await transport.opened()
        connection.deliver(envelope(1, "ready", data: "{}"))
        _ = try await keep.next()
        let subscribed = try await connection.sentFrame(at: 1)
        let id = try XCTUnwrap(subscribed["id"] as? String)
        connection.deliver(envelope(2, sub: id, "ended", data: #"{"status":404,"detail":"no such conversation"}"#))
        do { for try await _ in refused {}; XCTFail("A refusal must fail its stream") }
        catch { XCTAssertEqual((error as? APIError)?.status, 404) }
        XCTAssertFalse(connection.isClosed)
        let open = Task { for try await _ in await socket.events(kind: "navigation") {} }
        try await connection.waitForSent(3)
        open.cancel()
        _ = await open.result
        try await connection.waitForSent(4)
        let last = try await connection.sentFrame(at: 3)
        XCTAssertEqual(last["op"] as? String, "unsubscribe")
    }

    func testEveryDropEndsEveryOpenStreamWithItsOwnError() async throws {
        for (name, drop) in [
            ("gap", { (c: FakeLiveConnection) in c.deliver(self.envelope(3, "change")) }),
            ("update needed", { $0.close(code: 4001) }),
            ("restarting", { $0.close(code: 4002) }),
            ("network", { $0.fail(URLError(.networkConnectionLost)) }),
        ] as [(String, (FakeLiveConnection) -> Void)] {
            let server = URL(string: "https://drop-\(UUID().uuidString.lowercased()).invalid")!
            let transport = FakeLiveTransport()
            let socket = LiveSocket(api: APIClient(baseURL: server), transport: transport)
            let changes = await socket.changes()
            let view = await socket.events(kind: "navigation")
            let connection = try await transport.opened()
            connection.deliver(envelope(1, "ready", data: "{}"))
            try await connection.waitForSent(2)
            drop(connection)
            var errors: [Error] = []
            do { for try await _ in changes {} } catch { errors.append(error) }
            do { for try await _ in view {} } catch { errors.append(error) }
            XCTAssertEqual(errors.count, 2, name)
            switch name {
            case "update needed":
                XCTAssertTrue((errors.first as? APIError)?.isUpdateRequired == true)
                XCTAssertTrue(NativeBuildGate.requiresUpdate(server), "The close uses the existing update latch")
            case "gap": XCTAssertEqual(errors.first as? LiveSocketError, .sequenceGap)
            case "restarting": XCTAssertEqual(errors.first as? LiveSocketError, .closed(code: 4002))
            default: XCTAssertEqual((errors.first as? URLError)?.code, .networkConnectionLost)
            }
            let again = await socket.changes()
            if name == "update needed" {
                // The latch stops every later request, so no new socket opens.
                do { for try await _ in again {}; XCTFail("A latched server must not reconnect") }
                catch { XCTAssertTrue((error as? APIError)?.isUpdateRequired == true) }
                continue
            }
            // Otherwise the next stream opens a new socket.
            let second = try await transport.opened(after: 1)
            XCTAssertFalse(second === connection)
            withExtendedLifetime(again) {}
        }
    }
}

private final class FakeLiveTransport: LiveSocketTransport, @unchecked Sendable {
    private let made = OSAllocatedUnfairLock(initialState: [FakeLiveConnection]())
    func connect(_ request: URLRequest) -> any LiveSocketConnection {
        let connection = FakeLiveConnection()
        made.withLock { $0.append(connection) }
        return connection
    }
    /// The connection at `index`, once the socket opened it.
    func opened(after index: Int = 0) async throws -> FakeLiveConnection {
        for _ in 0..<500 {
            if let connection = made.withLock({ $0.count > index ? $0[index] : nil }) { return connection }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTFail("The socket never opened")
        throw URLError(.cannotConnectToHost)
    }
}

private final class FakeLiveConnection: LiveSocketConnection, @unchecked Sendable {
    let sent = OSAllocatedUnfairLock(initialState: [String]())
    private let closed = OSAllocatedUnfairLock(initialState: false)
    private let incoming = AsyncThrowingStream<String, Error>.makeStream()
    private var iterator: AsyncThrowingStream<String, Error>.Iterator
    init() { iterator = incoming.stream.makeAsyncIterator() }
    var isClosed: Bool { closed.withLock { $0 } }
    func send(_ text: String) async throws { sent.withLock { $0.append(text) } }
    func receive() async throws -> String {
        guard let text = try await iterator.next() else { throw URLError(.cancelled) }
        return text
    }
    func close() { closed.withLock { $0 = true }; incoming.continuation.finish() }
    func deliver(_ text: String) { incoming.continuation.yield(text) }
    func close(code: Int) { incoming.continuation.finish(throwing: LiveSocketError.closed(code: code)) }
    func fail(_ error: Error) { incoming.continuation.finish(throwing: error) }
    func waitForSent(_ count: Int) async throws {
        for _ in 0..<500 {
            if sent.value.count >= count { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTFail("The socket sent \(sent.value.count) frames, not \(count)")
    }
    func sentFrame(at index: Int) async throws -> [String: Any] {
        try await waitForSent(index + 1)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: Data(sent.value[index].utf8)) as? [String: Any])
    }
}

private extension OSAllocatedUnfairLock where State: Sendable {
    var value: State { withLock { $0 } }
}

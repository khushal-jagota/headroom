import Foundation

extension APIClient {
    /// The server's payload-free change stream. Buffer one refresh, not every commit.
    /// Terminating the consumer cancels the underlying URLSession request.
    func changes() -> AsyncThrowingStream<Void, Error> {
        AsyncThrowingStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
            let task = Task {
                do {
                    var request = try self.request(try url("/api/changes"))
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    request.cachePolicy = .reloadIgnoringLocalCacheData
                    request.timeoutInterval = 120
                    let (bytes, response) = try await session.bytes(for: request)
                    try Self.validate(response)
                    // Resync after the connection is established, including reconnects.
                    // This closes the gap between the initial GET and opening the stream.
                    continuation.yield(())
                    var parser = ChangeStreamParser()
                    for try await line in ServerSentLines(bytes) {
                        try Task.checkCancellation()
                        if parser.consume(line) { continuation.yield(()) }
                    }
                    continuation.finish()
                } catch { continuation.finish(throwing: error) }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

struct ChangeStreamParser {
    // ServerSentLines omits empty lines. Each server change is one data line.
    mutating func consume(_ line: String) -> Bool {
        line == "data: change" || line == "data:change"
    }
}

/// The lines of an event stream, split only at CR, LF or CRLF, as EventSource splits
/// them. Foundation's `lines` also splits at U+0085, U+2028 and U+2029, which the
/// server leaves unescaped inside JSON text, and so would cut a data line in two.
/// Empty lines are omitted: each data line here is a whole frame.
struct ServerSentLines<Base: AsyncSequence>: AsyncSequence where Base.Element == UInt8 {
    typealias Element = String
    let base: Base
    init(_ base: Base) { self.base = base }
    struct AsyncIterator: AsyncIteratorProtocol {
        var bytes: Base.AsyncIterator
        var line: [UInt8] = []
        var afterCR = false
        var done = false
        mutating func next() async throws -> String? {
            while !done {
                guard let byte = try await bytes.next() else {
                    done = true
                    break
                }
                if byte == 0x0A && afterCR { afterCR = false; continue }
                afterCR = byte == 0x0D
                if byte == 0x0A || byte == 0x0D {
                    if line.isEmpty { continue }
                    defer { line.removeAll(keepingCapacity: true) }
                    return String(decoding: line, as: UTF8.self)
                }
                line.append(byte)
            }
            guard !line.isEmpty else { return nil }
            defer { line.removeAll() }
            return String(decoding: line, as: UTF8.self)
        }
    }
    func makeAsyncIterator() -> AsyncIterator { AsyncIterator(bytes: base.makeAsyncIterator()) }
}

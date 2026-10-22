import Foundation

extension APIClient {
    /// The server's payload-free change stream. Buffer one refresh, not every commit.
    /// Terminating the consumer cancels the underlying URLSession request.
    func changes() -> AsyncThrowingStream<Void, Error> {
        AsyncThrowingStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
            let task = Task {
                do {
                    var request = URLRequest(url: try url("/api/changes"))
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    request.cachePolicy = .reloadIgnoringLocalCacheData
                    request.timeoutInterval = 120
                    let (bytes, response) = try await session.bytes(for: request)
                    try Self.validate(response)
                    // Resync after the connection is established, including reconnects.
                    // This closes the gap between the initial GET and opening the stream.
                    continuation.yield(())
                    var parser = ChangeStreamParser()
                    for try await line in bytes.lines {
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
    // URLSession.AsyncLineSequence omits empty lines. Each server change is one data line.
    mutating func consume(_ line: String) -> Bool {
        line == "data: change" || line == "data:change"
    }
}

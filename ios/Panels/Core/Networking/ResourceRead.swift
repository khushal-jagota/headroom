import Foundation

extension ResourceOwner {
    /// Short-lived presentation lookups share admission and transport with mounted reads.
    @MainActor func read<Value: Decodable & Sendable>(_ registration: ResourceRegistration<Value>) async throws -> Value {
        let handle = handle(registration)
        handle.acquire(); defer { handle.release() }
        let generation = admissionGeneration, resourceGeneration = handle.entry.generation
        await handle.refresh()
        try Task.checkCancellation()
        guard generation == admissionGeneration, resourceGeneration == handle.entry.generation,
              handle.entry.server == server, handle.error == nil, let value = handle.value else {
            throw APIError(status: 0, message: handle.error ?? "This response is unavailable.")
        }
        return value
    }
}

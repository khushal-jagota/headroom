import SwiftUI

extension View {
    /// The SwiftUI task owns demand. Cancellation releases it when the route leaves.
    func resourceDemand<Value: Decodable & Sendable>(_ handle: ResourceHandle<Value>, active: Bool = true) -> some View {
        task(id: handle.entry.server + "|" + handle.key + "|" + String(active)) {
            guard active else { return }
            handle.acquire()
            defer { handle.release() }
            await handle.refresh()
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(3600)) } catch { return }
            }
        }
    }
}

extension View {
    func resourceDemand<Value: Decodable & Sendable>(_ handles: [ResourceHandle<Value>]) -> some View {
        task(id: handles.map { $0.entry.server + "|" + $0.key }.joined(separator: "\u{1f}")) {
            for handle in handles { handle.acquire() }
            defer { for handle in handles { handle.release() } }
            await withTaskGroup(of: Void.self) { group in
                for handle in handles { group.addTask { await handle.refresh() } }
            }
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(3600)) } catch { return }
            }
        }
    }
}

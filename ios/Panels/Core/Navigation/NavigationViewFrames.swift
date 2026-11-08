import Foundation

/// Mirrors web/src/lib/navigation/views/applyFrame.ts. Pure: a refusal means the
/// caller must read a fresh snapshot for its scope; nothing is guessed across a gap.
enum NavigationViewFrames {
    enum Refusal: Error, Equatable {
        case reset(String)
        case obsolete
        case checkpointChangedContent
        case contentGap
    }

    static func apply(_ previous: ViewSnapshot?, _ frame: ViewFrame) -> Result<ViewSnapshot, Refusal> {
        switch frame {
        case .reset(let reason): return .failure(.reset(reason))
        case .snapshot(let snapshot): return .success(snapshot)
        case .checkpoint(let checkpoint):
            guard let previous, follows(previous, viewId: checkpoint.viewId, scopeGeneration: checkpoint.scopeGeneration,
                                        orderingGeneration: checkpoint.orderingGeneration, authority: checkpoint.authority,
                                        coverage: checkpoint.coverage, previousCoverage: checkpoint.previousCoverage)
            else { return .failure(.obsolete) }
            guard checkpoint.contentVersion == previous.contentVersion else { return .failure(.checkpointChangedContent) }
            var next = previous
            next.coverage = checkpoint.coverage
            return .success(next)
        case .patch(let patch):
            guard let previous, follows(previous, viewId: patch.viewId, scopeGeneration: patch.scopeGeneration,
                                        orderingGeneration: patch.orderingGeneration, authority: patch.authority,
                                        coverage: patch.coverage, previousCoverage: patch.previousCoverage)
            else { return .failure(.obsolete) }
            guard patch.previousContentVersion == previous.contentVersion, patch.contentVersion != previous.contentVersion
            else { return .failure(.contentGap) }
            // Eviction is absence from this scope. An upsert replaces its key in place.
            let evicted = Set(patch.evictions)
            var records = previous.content.records.filter { !evicted.contains($0.key) }
            var position = Dictionary(records.enumerated().map { ($1.key, $0) }, uniquingKeysWith: { first, _ in first })
            for row in patch.upserts {
                if let index = position[row.key] { records[index] = row }
                else { position[row.key] = records.count; records.append(row) }
            }
            var next = previous
            next.coverage = patch.coverage
            next.contentVersion = patch.contentVersion
            next.content = ViewContent(records: records, windows: patch.windows, counts: patch.counts)
            return .success(next)
        }
    }

    private static func follows(_ previous: ViewSnapshot, viewId: String, scopeGeneration: Int, orderingGeneration: String,
                                authority: ViewAuthority, coverage: ViewContinuity, previousCoverage: ViewContinuity) -> Bool {
        previous.mode == "current" && previous.authority == authority && previous.viewId == viewId
            && previous.scopeGeneration == scopeGeneration && previous.orderingGeneration == orderingGeneration
            && previous.coverage.sameDomain(coverage) && previous.coverage.revision == previousCoverage.revision
    }
}

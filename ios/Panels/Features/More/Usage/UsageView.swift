import SwiftUI
import Charts

struct UsageView: View {
    @Environment(AppStore.self) private var store
    @State private var metric = "Limits"
    @State private var days = 1
    @State private var provider = "All"
    private var backendResource: ResourceHandle<UsageBackends> { store.resources.handle(UsageResources.backends) }
    private var summaryResource: ResourceHandle<RecordedUsageSummary> { store.resources.handle(UsageResources.summary(days: days)) }
    private var summary: UsageSummary? { summaryResource.value.map { UsageSummary($0, days: days) } }
    private var backends: [UsageBackend] { (backendResource.value?.backends ?? []).filter { ["codex", "claude"].contains($0.backendKey) } }
    @State private var refreshError: String?
    private var error: String? { refreshError ?? (metric == "Limits" ? backendResource.error : summaryResource.error) }
    private var loading: Bool { metric == "Limits" ? backendResource.value == nil : summary == nil }
    @State private var refreshing = false
    private let metrics = ["Limits", "Tokens", "Cost"]
    private var selectedMetrics: UsageMetrics? {
        provider == "All" ? summary?.totals : summary?.providers.first { $0.provider == provider.lowercased() }
    }

    var body: some View {
        List {
            Section {
                Picker("Usage", selection: $metric) { ForEach(metrics, id: \.self) { Text($0) } }.accessibleSegmentedPicker().disabled(refreshing)
                Picker("Provider", selection: $provider) { ForEach(["All", "Codex", "Claude"], id: \.self) { Text($0) } }
            }.listRowBackground(PanelsTheme.raised)
            if loading && summary == nil && backends.isEmpty { RingSpinner("Reading usage…") }
            if metric == "Limits" { limits } else { recorded }
            if let error { Section { LoadErrorView(message: error) { await refresh() } } }
        }
        .panelsNavigationTitle("Usage", role: .usage).panelsScreen()
        .resourceDemand(backendResource, active: metric == "Limits")
        .resourceDemand(summaryResource, active: metric != "Limits")
        .panelsRefreshable { await refresh() }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { Task { await refresh() } } label: {
                    Image(systemName: "arrow.clockwise")
                        .opacity(refreshing ? 0 : 1)
                        .overlay { if refreshing { RingSpinner() } }
                }
                .accessibilityLabel(refreshing ? "Refreshing usage and limits" : "Refresh usage and limits")
                .accessibilityIdentifier("usage.refresh")
                .disabled(refreshing)
            }
        }
        .onChange(of: metric) { _, _ in Haptics.selection() }
        .onChange(of: days) { _, _ in Haptics.selection() }
        .onChange(of: provider) { _, _ in Haptics.selection() }
    }

    private var limits: some View {
        ForEach(backends.filter { provider == "All" || $0.backendKey == provider.lowercased() }) { backend in
            Section {
                if let usage = backend.cachedUsage, !usage.windows.isEmpty {
                    ForEach(Array(usage.windows.enumerated()), id: \.offset) { _, window in
                        VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                            HStack {
                                Text(window.kind == "five_hour" ? "5 hours" : "7 days").font(PanelsTheme.TypeScale.allowanceLabel)
                                Spacer()
                                Text("\(window.usedPercent.formatted(.number.precision(.fractionLength(0))))% used").font(PanelsTheme.TypeScale.allowanceValue).monospacedDigit()
                            }
                            ProgressView(value: UsagePresentation.fraction(window.usedPercent)).tint(PanelsTheme.accent)
                            if let model = window.modelId { Text(model).font(PanelsTheme.TypeScale.small) }
                            if let reset = UsagePresentation.date(window.resetsAt) {
                                Text("Resets \(reset.formatted(date: .abbreviated, time: .shortened))")
                                    .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                            }
                        }.padding(.vertical, PanelsTheme.Space.xs)
                    }
                    if let date = UsagePresentation.date(usage.observedAt) {
                        Text("Last read \(date.formatted(date: .abbreviated, time: .shortened))")
                            .font(PanelsTheme.TypeScale.caption).foregroundStyle(PanelsTheme.faint)
                    }
                } else {
                    Text(backend.installed ? "No recorded allowance" : "Not installed").foregroundStyle(PanelsTheme.muted)
                }
                ForEach(backend.diagnoses, id: \.self) { Text($0).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted) }
            } header: { Text(backend.backendKey.capitalized).panelsSectionHeading(.usage) }
            .listRowBackground(PanelsTheme.raised)
        }
    }

    private var recorded: some View {
        Group {
            Section {
                Picker("Period", selection: $days) {
                    Text("Today").tag(1); Text("7 days").tag(7); Text("30 days").tag(30); Text("90 days").tag(90)
                }.accessibleSegmentedPicker().disabled(refreshing)
                VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                    Text(metric == "Cost" ? UsagePresentation.cost(selectedMetrics?.costUsd) : UsagePresentation.number(selectedMetrics?.totalTokens))
                        .font(PanelsTheme.TypeScale.title).monospacedDigit().contentTransition(.numericText())
                    Text(metric == "Cost" ? "Recorded cost · USD" : "Recorded tokens")
                        .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                }.padding(.vertical, PanelsTheme.Space.sm)
                if let summary, !summary.periods.isEmpty {
                    Chart(Array(summary.periods.enumerated()), id: \.offset) { index, period in
                        if let value = periodValue(period) {
                            BarMark(x: .value("Period", index), y: .value(metric, value))
                                .foregroundStyle(PanelsTheme.accent).cornerRadius(PanelsTheme.Radius.sm)
                                .accessibilityLabel(period.label)
                                .accessibilityValue(metric == "Cost" ? UsagePresentation.cost(value) : UsagePresentation.number(value))
                        }
                    }.chartXAxis(.hidden).chartYAxis { AxisMarks(position: .leading) }
                        .frame(height: 140).accessibilityLabel("Recorded \(metric.lowercased()) by period")
                    Text("\(summary.periods.first?.label ?? "") – \(summary.periods.last?.label ?? "") · \(summary.timezone)")
                        .font(PanelsTheme.TypeScale.caption).foregroundStyle(PanelsTheme.faint)
                }
            }.listRowBackground(PanelsTheme.raised)
            if let totals = selectedMetrics {
                Section {
                    if metric == "Cost" {
                        LabeledContent("Estimated", value: UsagePresentation.cost(totals.estimatedCostUsd))
                        LabeledContent("Provider reported", value: UsagePresentation.cost(totals.providerReportedCostUsd))
                        if totals.unknownCostRecords > 0 { Text("\(totals.unknownCostRecords) records have unknown cost").foregroundStyle(PanelsTheme.muted) }
                        if let note = summary?.pricingNote { Text(note).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint) }
                        Text("Recorded server CLI use. Estimates are not a bill.").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    } else {
                        LabeledContent("Input", value: UsagePresentation.number(totals.inputTokens))
                        LabeledContent("Cache read", value: UsagePresentation.number(totals.cachedInputTokens))
                        LabeledContent("Cache write", value: UsagePresentation.number(totals.cacheCreationTokens))
                        LabeledContent("Output", value: UsagePresentation.number(totals.outputTokens))
                    }
                } header: { Text(metric == "Cost" ? "Cost Detail" : "Token Detail").panelsSectionHeading(.usage) }.listRowBackground(PanelsTheme.raised)
            }
            if let summary {
                Section {
                    ForEach(summary.sources.filter { provider == "All" || $0.provider == provider.lowercased() }, id: \.id) { source in
                        VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                            LabeledContent("\(source.provider.capitalized) · \(source.host)", value: source.state.capitalized)
                            ForEach(source.issues, id: \.self) { Text($0).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted) }
                        }
                    }
                    if let observed = summary.observedAt, let date = UsagePresentation.date(observed) {
                        Text("Last read \(date.formatted(date: .abbreviated, time: .shortened))").font(PanelsTheme.TypeScale.caption).foregroundStyle(PanelsTheme.faint)
                    }
                } header: { Text("Sources").panelsSectionHeading(.usage) }.listRowBackground(PanelsTheme.raised)
            }
        }
    }
    private func periodValue(_ period: UsagePeriod) -> Double? {
        if provider == "All" { return metric == "Cost" ? period.costUsd : period.totalTokens }
        let value = period.providers.first { $0.provider == provider.lowercased() }
        return metric == "Cost" ? value?.costUsd : value?.totalTokens
    }
    @MainActor private func refresh() async {
        guard !refreshing else { return }
        refreshing = true
        Haptics.light()
        defer { refreshing = false }
        let client = store.client
        async let allowanceRead = client.refreshUsageBackends()
        async let recordedRead: Void = client.refreshRecordedUsage()
        var failures: [String] = []
        do {
            let result = try await allowanceRead
            guard store.baseURL == client.baseURL else { return }
            if let failure = result.failure { failures.append(failure) }
        } catch { failures.append(error.localizedDescription) }
        do {
            try await recordedRead
        } catch { failures.append(error.localizedDescription) }
        guard store.baseURL == client.baseURL else { return }
        store.resources.invalidate([backendResource.key, summaryResource.key])
        async let backends: Void = backendResource.refresh()
        async let summary: Void = summaryResource.refresh()
        _ = await (backends, summary)
        if let failure = backendResource.error ?? summaryResource.error { failures.append(failure) }
        guard store.baseURL == client.baseURL, !Task.isCancelled else { return }
        refreshError = failures.isEmpty ? nil : failures.joined(separator: " ")
        if failures.isEmpty { Haptics.success() } else { Haptics.error() }
    }
    @MainActor private func load() async {
        if metric == "Limits" { await backendResource.refresh() }
        else { await summaryResource.refresh() }
        refreshError = backendResource.error ?? summaryResource.error
    }
}

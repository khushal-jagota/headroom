import SwiftUI
import Charts

struct UsageView: View {
    @Environment(AppStore.self) private var store
    @State private var metric = "Limits"
    @State private var days = 1
    @State private var provider = "All"
    @State private var summary: UsageSummary?
    @State private var backends: [UsageBackend] = []
    @State private var error: String?
    @State private var loading = false
    private let metrics = ["Limits", "Tokens", "Cost"]
    private var selectedMetrics: UsageMetrics? {
        provider == "All" ? summary?.totals : summary?.providers.first { $0.provider == provider.lowercased() }
    }

    var body: some View {
        List {
            Section {
                Picker("Usage", selection: $metric) { ForEach(metrics, id: \.self) { Text($0) } }.pickerStyle(.segmented)
                Picker("Provider", selection: $provider) { ForEach(["All", "Codex", "Claude"], id: \.self) { Text($0) } }
            }.listRowBackground(PanelsTheme.raised)
            if let error { Section { LoadErrorView(message: error) { await load() } } }
            if loading && summary == nil && backends.isEmpty { ProgressView("Reading usage…") }
            if metric == "Limits" { limits } else { recorded }
        }
        .navigationTitle("Usage").panelsScreen()
        .task(id: "\(store.baseURL):\(metric):\(days):\(store.revision)") { await load() }
        .refreshable { Haptics.light(); await load() }
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
                                Text(window.kind == "five_hour" ? "5 hours" : "7 days")
                                Spacer()
                                Text("\(window.usedPercent.formatted(.number.precision(.fractionLength(0))))% used").monospacedDigit()
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
            } header: { Text(backend.backendKey.capitalized) }
            .listRowBackground(PanelsTheme.raised)
        }
    }

    private var recorded: some View {
        Group {
            Section {
                Picker("Period", selection: $days) {
                    Text("Today").tag(1); Text("7 days").tag(7); Text("30 days").tag(30); Text("90 days").tag(90)
                }.pickerStyle(.segmented)
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
                Section(metric == "Cost" ? "Cost detail" : "Token detail") {
                    if metric == "Cost" {
                        LabeledContent("Estimated", value: UsagePresentation.cost(totals.estimatedCostUsd))
                        LabeledContent("Provider reported", value: UsagePresentation.cost(totals.providerReportedCostUsd))
                        if totals.unknownCostRecords > 0 { Text("\(totals.unknownCostRecords) records have unknown cost").foregroundStyle(PanelsTheme.muted) }
                        Text("Recorded local CLI use. Estimates are not a bill.").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    } else {
                        LabeledContent("Input", value: UsagePresentation.number(totals.inputTokens))
                        LabeledContent("Cache read", value: UsagePresentation.number(totals.cachedInputTokens))
                        LabeledContent("Cache write", value: UsagePresentation.number(totals.cacheCreationTokens))
                        LabeledContent("Output", value: UsagePresentation.number(totals.outputTokens))
                    }
                }.listRowBackground(PanelsTheme.raised)
            }
            if let summary {
                Section("Sources") {
                    ForEach(summary.sources.filter { provider == "All" || $0.provider == provider.lowercased() }, id: \.provider) { source in
                        VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                            LabeledContent(source.provider.capitalized, value: source.state.capitalized)
                            ForEach(source.issues, id: \.self) { Text($0).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted) }
                        }
                    }
                    if let observed = summary.observedAt, let date = UsagePresentation.date(observed) {
                        Text("Last read \(date.formatted(date: .abbreviated, time: .shortened))").font(PanelsTheme.TypeScale.caption).foregroundStyle(PanelsTheme.faint)
                    }
                }.listRowBackground(PanelsTheme.raised)
            }
        }
    }
    private func periodValue(_ period: UsagePeriod) -> Double? {
        if provider == "All" { return metric == "Cost" ? period.costUsd : period.totalTokens }
        let value = period.providers.first { $0.provider == provider.lowercased() }
        return metric == "Cost" ? value?.costUsd : value?.totalTokens
    }
    @MainActor private func load() async {
        loading = true
        if metric != "Limits", summary?.days != days { summary = nil }
        defer { loading = false }
        do {
            if metric == "Limits" {
                let result = try await store.client.usageBackends()
                try Task.checkCancellation()
                backends = result.backends.filter { ["codex", "claude"].contains($0.backendKey) }
            } else {
                let result = try await store.client.usage(days: days)
                try Task.checkCancellation()
                summary = result
            }
            error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}

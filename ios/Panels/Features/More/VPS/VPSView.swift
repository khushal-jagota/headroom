import SwiftUI
import Charts

struct VPSView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @State private var latest: VPSLatest?
    @State private var processes: [VPSProcess] = []
    @State private var nextOffset: Int?
    @State private var processSequence: Int?
    @State private var totalProcesses = 0
    @State private var history: VPSHistory?
    @State private var window: VPSWindow = .minutes
    @State private var error: String?
    @State private var processError: String?
    @State private var historyError: String?
    @State private var loadingProcesses = false

    var body: some View {
        List {
            if let error { Section { LoadErrorView(message: error) { await load() } } }
            if let latest {
                if let sample = latest.sample {
                    Section {
                        TimelineView(.periodic(from: .now, by: 5)) { context in
                            HStack {
                                Text("\(sample.coreCount) cores · \(VPSPresentation.bytes(sample.value("memory_total_bytes"))) memory")
                                Spacer()
                                let stale = error != nil || VPSPresentation.stale(latest, now: context.date.timeIntervalSince1970)
                                Label(stale ? "No feed" : "Current", systemImage: stale ? "clock" : "circle.fill")
                                    .foregroundStyle(stale ? PanelsTheme.muted : PanelsTheme.success)
                            }.font(PanelsTheme.TypeScale.small)
                        }
                        Text("Sampled \(Date(timeIntervalSince1970: sample.timestamp).formatted(date: .abbreviated, time: .shortened))")
                            .font(PanelsTheme.TypeScale.caption).foregroundStyle(PanelsTheme.faint)
                    }.listRowBackground(PanelsTheme.raised)
                    instruments(sample)
                    Section {
                        Picker("Window", selection: $window) { ForEach(VPSWindow.allCases, id: \.self) { Text($0.rawValue) } }.accessibleSegmentedPicker()
                        if let historyError { LoadErrorView(message: historyError) { await loadHistory() } }
                        if let history, !history.points.isEmpty {
                            Chart(Array(history.points.enumerated()), id: \.offset) { _, point in
                                // Points remain separate so collection gaps are never drawn as measured activity.
                                if let value = point.value {
                                    PointMark(x: .value("Time", Date(timeIntervalSince1970: point.timestamp)), y: .value("CPU", value))
                                        .foregroundStyle(PanelsTheme.accent).symbolSize(8)
                                }
                            }.chartYScale(domain: 0...100).frame(height: 120).accessibilityLabel("Machine CPU history, percent of total capacity")
                        } else if historyError == nil { Text(history == nil ? "Reading history…" : "No samples in this window").foregroundStyle(PanelsTheme.muted) }
                    } header: { Text("CPU History").panelsSectionHeading(.monitor) }.listRowBackground(PanelsTheme.raised)
                    Section {
                        ForEach(sample.services) { service in
                            workload(name: service.name, detail: "\(service.processCount) processes · \(service.state ?? "Unknown")", cpu: service.metrics["cpu_percent"]?.value, memory: service.metrics["memory_bytes"]?.value, cores: sample.coreCount)
                        }
                        if sample.services.isEmpty { Text("No service readings").foregroundStyle(PanelsTheme.muted) }
                    } header: { Text("Services").panelsSectionHeading(.monitor) }.listRowBackground(PanelsTheme.raised)
                    Section {
                        if let processError { LoadErrorView(message: processError) { await loadProcesses(reset: true) } }
                        ForEach(processes) { process in
                            workload(name: process.name, detail: "PID \(process.identity.pid)", cpu: process.metrics["cpu_percent"]?.value, memory: process.metrics["rss_bytes"]?.value, cores: sample.coreCount)
                        }
                        if nextOffset != nil {
                            Button { Task { await loadProcesses(reset: false) } } label: {
                                if loadingProcesses { RingSpinner() } else { Text("Load more processes") }
                            }.disabled(loadingProcesses)
                        }
                    } header: { Text("Processes · \(totalProcesses)").panelsSectionHeading(.monitor) }.listRowBackground(PanelsTheme.raised)
                } else {
                    ContentUnavailableView("No machine readings", systemImage: "server.rack", description: Text(latest.collection.reason ?? "The server has not collected a sample."))
                }
            } else if error == nil { RingSpinner("Reading the machine…") }
        }
        .panelsNavigationTitle("VPS").panelsScreen()
        .task(id: "\(store.baseURL):\(scenePhase == .active)") {
            guard scenePhase == .active else { return }
            await load()
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                await load()
                if window == .minutes { await loadHistory() }
            }
        }
        .task(id: window) { history = nil; await loadHistory() }
        .onChange(of: window) { _, _ in Haptics.selection() }
        .panelsRefreshable { await load(); await loadHistory() }
    }
    private func instruments(_ sample: VPSSample) -> some View {
        Section {
            LabeledContent("CPU", value: VPSPresentation.percent(sample.value("cpu_percent")))
            LabeledContent("Memory", value: VPSPresentation.bytes(VPSPresentation.used(total: sample.value("memory_total_bytes"), available: sample.value("memory_available_bytes"))))
            LabeledContent("Available memory", value: VPSPresentation.bytes(sample.value("memory_available_bytes")))
            LabeledContent("Load · 1 minute", value: sample.value("load_1")?.formatted(.number.precision(.fractionLength(2))) ?? "—")
            LabeledContent("Disk read", value: rate(sample.value("disk.read_bytes_per_second")))
            LabeledContent("Disk write", value: rate(sample.value("disk.write_bytes_per_second")))
            LabeledContent("Network in", value: rate(sample.value("network.receive_bytes_per_second")))
            LabeledContent("Network out", value: rate(sample.value("network.transmit_bytes_per_second")))
            LabeledContent("Disk available", value: VPSPresentation.bytes(sample.value("filesystem.available_bytes")))
        } header: { Text("Machine").panelsSectionHeading(.monitor) }.monospacedDigit().listRowBackground(PanelsTheme.raised)
    }
    private func rate(_ value: Double?) -> String { value == nil ? "—" : VPSPresentation.bytes(value) + "/s" }
    private func workload(name: String, detail: String, cpu: Double?, memory: Double?, cores: Int) -> some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
            HStack { Text(name).lineLimit(2); Spacer(); Text(VPSPresentation.percent(VPSPresentation.cpuShare(cpu, cores: cores))).monospacedDigit() }
            HStack { Text(detail); Spacer(); Text(VPSPresentation.bytes(memory)) }.font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
        }.padding(.vertical, PanelsTheme.Space.xs)
    }
    @MainActor private func load() async {
        do {
            let result = try await store.client.vpsLatest()
            try Task.checkCancellation()
            latest = result; error = nil
            await loadProcesses(reset: true)
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
    @MainActor private func loadProcesses(reset: Bool) async {
        guard !loadingProcesses else { return }
        loadingProcesses = true
        defer { loadingProcesses = false }
        do {
            let loadedCount = processes.count
            let sequence = reset ? latest?.sequence : processSequence
            var page = try await store.client.vpsProcesses(sequence: sequence, offset: reset ? 0 : nextOffset ?? 0)
            var rows = page.processes
            // Keep the user's loaded window when the next machine sample arrives.
            while reset, let offset = page.nextOffset, rows.count < loadedCount {
                page = try await store.client.vpsProcesses(sequence: sequence, offset: offset)
                rows += page.processes
            }
            try Task.checkCancellation()
            if reset { processes = rows; processSequence = sequence } else {
                let existing = Set(processes.map(\.id)); processes += page.processes.filter { !existing.contains($0.id) }
            }
            nextOffset = page.nextOffset; totalProcesses = page.total
            processError = page.availability == "unavailable" ? page.reason ?? "Processes unavailable" : nil
        } catch { if !Task.isCancelled { processError = error.localizedDescription } }
    }
    @MainActor private func loadHistory() async {
        do {
            let result = try await store.client.vpsHistory(window: window)
            try Task.checkCancellation()
            history = result; historyError = nil
        } catch { if !Task.isCancelled { historyError = error.localizedDescription } }
    }
}

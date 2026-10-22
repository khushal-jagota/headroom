import SwiftUI

struct SettingsView: View {
    @Environment(AppStore.self) private var store
    @State private var section = "Config"
    @State private var workers: SettingsWorkers?
    @State private var skills: [ManagedSkill] = []
    @State private var schedules: [SettingsSchedule]?
    @State private var notifications: SettingsNotifications?
    @State private var error: String?
    @State private var loading = false
    @State private var showingServer = false

    var body: some View {
        List {
            Section {
                Picker("Settings", selection: $section) {
                    Text("Config").tag("Config")
                    Text("Scheduled").tag("Scheduled")
                    Text("Notifications").tag("Notifications")
                }.pickerStyle(.segmented)
            }.listRowBackground(PanelsTheme.raised)
            if let error { Section { LoadErrorView(message: error) { await load() } } }
            if loading && !hasData { ProgressView("Reading settings…") }
            switch section {
            case "Config": configuration
            case "Scheduled": scheduled
            default: notificationPreferences
            }
            Section {
                Button { showingServer = true } label: {
                    LabeledContent("Server", value: store.baseURL.host ?? store.baseURL.absoluteString)
                }.foregroundStyle(PanelsTheme.text)
            }.listRowBackground(PanelsTheme.raised)
        }
        .navigationTitle("Settings").panelsScreen()
        .task(id: "\(store.baseURL):\(section):\(store.revision)") { await load() }
        .refreshable { await load() }
        .onChange(of: section) { _, _ in Haptics.selection() }
        .sheet(isPresented: $showingServer) {
            ServerAddressSheet(initialAddress: store.baseURL.absoluteString) { store.configure(baseURL: $0) }
        }
    }
    private var hasData: Bool {
        switch section { case "Config": workers != nil; case "Scheduled": schedules != nil; default: notifications != nil }
    }
    private var configuration: some View {
        Group {
            if let workers {
                Section("Agents") {
                    NavigationLink {
                        SettingsAgentDetail(title: workers.chiefOfStaff.label, configuration: workers.chiefOfStaff.launchDefaults, skill: workers.chiefOfStaff.skill)
                    } label: { Text(workers.chiefOfStaff.label) }
                    ForEach(skills.filter { ["panels-manager", "panels-worker"].contains($0.name) }, id: \.name) { skill in
                        NavigationLink(skill.name == "panels-manager" ? "Manager" : "Worker skill") { SettingsSkillDetail(skill: skill) }
                    }
                }.listRowBackground(PanelsTheme.raised)
                Section("Workers") {
                    ForEach(workers.workers.sorted { $0.label < $1.label }) { worker in
                        NavigationLink {
                            SettingsAgentDetail(title: worker.label, configuration: worker.launchDefaults, skill: skills.first { $0.name == worker.specialistSkillName })
                        } label: {
                            VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                                Text(worker.label)
                                Text(worker.launchDefaults.employeeLaunchModel ?? worker.launchDefaults.employeeBackend)
                                    .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                            }
                        }
                    }
                }.listRowBackground(PanelsTheme.raised)
            }
        }
    }
    private var scheduled: some View {
        Group {
            if let schedules {
                Section {
                    if schedules.isEmpty { ContentUnavailableView("No scheduled tasks", systemImage: "calendar") }
                    ForEach(schedules) { schedule in
                        NavigationLink {
                            List {
                                Section {
                                    LabeledContent("Status", value: schedule.enabled ? "Enabled" : "Disabled")
                                    LabeledContent("Time", value: schedule.localTime)
                                    LabeledContent("Cadence", value: schedule.cadence.replacingOccurrences(of: "_", with: " ").capitalized)
                                    LabeledContent("Worker", value: schedule.workerType.replacingOccurrences(of: "_", with: " ").capitalized)
                                }.listRowBackground(PanelsTheme.raised)
                                if !schedule.kickoffNote.isEmpty { Section("Kickoff Note") { Text(schedule.kickoffNote).textSelection(.enabled) }.listRowBackground(PanelsTheme.raised) }
                            }.navigationTitle(schedule.title).navigationBarTitleDisplayMode(.inline).panelsScreen()
                        } label: {
                            VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                                Text(schedule.title)
                                Text("\(schedule.localTime) · \(schedule.enabled ? "Enabled" : "Disabled")")
                                    .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                            }
                        }
                    }
                } header: { Text("Scheduled Tasks") } footer: { Text("Schedules follow the server’s local time. Manage schedules on the web.") }
                .listRowBackground(PanelsTheme.raised)
            }
        }
    }
    private var notificationPreferences: some View {
        Group {
            if let notifications {
                ForEach(notifications.subjects, id: \.key) { subject in
                    Section(subject.label) {
                        ForEach(subject.types) { preference in
                            HStack(alignment: .top, spacing: PanelsTheme.Space.md) {
                                VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                                    Text(preference.label)
                                    Text(preference.description).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                                }
                                Spacer(minLength: 0)
                                Text(preference.enabled ? "On" : "Off")
                                    .foregroundStyle(preference.enabled ? PanelsTheme.accent : PanelsTheme.faint)
                            }.accessibilityElement(children: .combine)
                        }
                    }.listRowBackground(PanelsTheme.raised)
                }
                Section("This Device") {
                    Text("Push notifications are not connected in this iOS preview.").foregroundStyle(PanelsTheme.muted)
                    Text("These are the server’s notification preferences. Change them on the web.").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                }.listRowBackground(PanelsTheme.raised)
            }
        }
    }
    @MainActor private func load() async {
        loading = true
        defer { loading = false }
        do {
            switch section {
            case "Config":
                async let workerRead = store.client.settingsWorkers()
                async let skillRead = store.client.settingsSkills()
                let result = try await (workerRead, skillRead)
                try Task.checkCancellation()
                workers = result.0; skills = result.1.skills
            case "Scheduled":
                let result = try await store.client.settingsSchedules()
                try Task.checkCancellation(); schedules = result.schedules
            default:
                let result = try await store.client.settingsNotifications()
                try Task.checkCancellation(); notifications = result
            }
            error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}

private struct SettingsAgentDetail: View {
    let title: String
    let configuration: EmployeeConfiguration
    let skill: ManagedSkill?
    var body: some View {
        List {
            Section("Launch Defaults") {
                LabeledContent("Backend", value: configuration.employeeBackend.capitalized)
                LabeledContent("Model", value: configuration.employeeLaunchModel ?? "Backend default")
                LabeledContent("Reasoning", value: configuration.employeeLaunchReasoningEffort?.capitalized ?? "Backend default")
            }.listRowBackground(PanelsTheme.raised)
            if let skill {
                Section { NavigationLink("Role skill") { SettingsSkillDetail(skill: skill) } }
                    .listRowBackground(PanelsTheme.raised)
            }
            Section { Text("Manage configuration on the web.").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint) }
                .listRowBackground(PanelsTheme.raised)
        }.navigationTitle(title).panelsScreen()
    }
}
private struct SettingsSkillDetail: View {
    let skill: ManagedSkill
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
                Text(skill.description).font(PanelsTheme.TypeScale.heading)
                Text(skill.markdownBody).font(PanelsTheme.TypeScale.body).textSelection(.enabled)
            }.frame(maxWidth: .infinity, alignment: .leading).padding(PanelsTheme.Space.lg)
        }.navigationTitle(skill.name).navigationBarTitleDisplayMode(.inline).panelsScreen()
    }
}

struct ServerAddressSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var address: String
    @State private var error: String?
    let save: (URL) -> Void
    init(initialAddress: String, save: @escaping (URL) -> Void) {
        _address = State(initialValue: initialAddress); self.save = save
    }
    var body: some View {
        NavigationStack {
            Form {
                Section("HTTPS Address") {
                    TextField("https://panels.example.com", text: $address)
                        .textContentType(.URL).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    if let error { Text(error).foregroundStyle(PanelsTheme.error) }
                }.listRowBackground(PanelsTheme.raised)
            }.navigationTitle("Server").navigationBarTitleDisplayMode(.inline).panelsScreen()
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Save") {
                            guard let url = SettingsServerAddress.validated(address) else {
                                error = "Enter an HTTPS server address without a path."; return
                            }
                            save(url); Haptics.success(); dismiss()
                        }
                    }
                }
        }.presentationDragIndicator(.visible)
    }
}

import SwiftUI

struct SettingsView: View {
    @Environment(AppStore.self) private var store
    @State private var section = "Config"
    @State private var notificationEditor = NotificationPreferenceEditor()
    private var workerResource: ResourceHandle<SettingsWorkers> { store.resources.handle(SettingsResources.workers) }
    private var skillResource: ResourceHandle<SettingsSkills> { store.resources.handle(SettingsResources.skills) }
    private var scheduleResource: ResourceHandle<SettingsSchedules> { store.resources.handle(SettingsResources.schedules) }
    private var notificationResource: ResourceHandle<SettingsNotifications> { store.resources.handle(SettingsResources.notifications) }
    private var workers: SettingsWorkers? { workerResource.value }
    private var skills: [ManagedSkill] { skillResource.value?.skills ?? [] }
    private var schedules: [SettingsSchedule]? { scheduleResource.value?.schedules }
    private var notifications: SettingsNotifications? { notificationResource.value }
    private var error: String? {
        switch section {
        case "Config": return workerResource.error ?? skillResource.error
        case "Scheduled": return scheduleResource.error
        default: return notificationResource.error
        }
    }
    private var loading: Bool { !hasData && error == nil }
    @State private var showingServer = false

    var body: some View {
        List {
            SettingsPlaces()
            Section {
                Picker("Settings", selection: $section) {
                    Text("Config").tag("Config")
                    Text("Scheduled").tag("Scheduled")
                    Text("Notifications").tag("Notifications")
                }.accessibleSegmentedPicker()
            }.listRowBackground(PanelsTheme.raised)
            if let error { Section { LoadErrorView(message: error) { await load() } } }
            if loading && !hasData { RingSpinner("Reading settings…") }
            switch section {
            case "Config": configuration
            case "Scheduled": scheduled
            default: notificationPreferences
            }
            Section {
                Button { Haptics.light(); showingServer = true } label: {
                    LabeledContent("Server", value: store.baseURL.host ?? store.baseURL.absoluteString)
                }.foregroundStyle(PanelsTheme.text).systemCopyText(store.baseURL.absoluteString)
            }.listRowBackground(PanelsTheme.raised)
        }
        .panelsNavigationTitle("Settings").panelsScreen()
        .resourceDemand(workerResource, active: section == "Config")
        .resourceDemand(skillResource, active: section == "Config")
        .resourceDemand(scheduleResource, active: section == "Scheduled")
        .resourceDemand(notificationResource, active: section == "Notifications")
        .panelsRefreshable { await load() }
        .onChange(of: section) { _, _ in Haptics.selection() }
        .onChange(of: store.baseURL) { _, _ in notificationEditor.resetError() }
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
                Section {
                    MoreSelectableValue("Legacy executor", value: store.executorHealth?.activityKnown == true ? "Connected" : "Activity unknown")
                    MoreSelectableText(store.executorHealth?.contactSentence ?? "Last contact: unknown.", style: .footnote, color: PanelsTheme.muted)
                }.listRowBackground(PanelsTheme.raised)
                Section {
                    NavigationLink {
                        SettingsAgentDetail(title: workers.chiefOfStaff.label, configuration: workers.chiefOfStaff.launchDefaults, skill: workers.chiefOfStaff.skill)
                    } label: { Text(workers.chiefOfStaff.label) }.systemCopyText(workers.chiefOfStaff.label)
                    ForEach(skills.filter { ["panels-manager", "panels-worker"].contains($0.name) }, id: \.name) { skill in
                        NavigationLink(skill.name == "panels-manager" ? "Manager" : "Worker skill") { SettingsSkillDetail(skill: skill) }.systemCopyText(skill.name)
                    }
                } header: { MoreSelectableText("Agents", style: .footnote, weight: .medium, color: Color(uiColor: .secondaryLabel)) }.listRowBackground(PanelsTheme.raised)
                Section {
                    ForEach(workers.workers.sorted { $0.label < $1.label }) { worker in
                        NavigationLink {
                            SettingsAgentDetail(title: worker.label, configuration: worker.launchDefaults, skill: skills.first { $0.name == worker.specialistSkillName })
                        } label: {
                            VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                                Text(worker.label)
                                Text(worker.launchDefaults.employeeLaunchModel ?? store.backendLabel(worker.launchDefaults.employeeBackend))
                                    .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                            }
                        }.systemCopyText(worker.label + "\n" + (worker.launchDefaults.employeeLaunchModel ?? store.backendLabel(worker.launchDefaults.employeeBackend)))
                    }
                } header: { MoreSelectableText("Workers", style: .footnote, weight: .medium, color: Color(uiColor: .secondaryLabel)) }.listRowBackground(PanelsTheme.raised)
            }
        }
    }
    private var scheduled: some View {
        Group {
            if let schedules {
                Section {
                    if schedules.isEmpty {
                        ContentUnavailableView {
                            Label { MoreUnavailableText(text: "No scheduled tasks") } icon: { Image(systemName: "calendar") }
                        }
                    }
                    ForEach(schedules) { schedule in
                        NavigationLink {
                            List {
                                Section {
                                    TicketMachineLabel(locale: schedule.locale, selectable: true)
                                    MoreSelectableValue("Status", value: schedule.enabled ? "Enabled" : "Disabled")
                                    MoreSelectableValue("Time", value: schedule.localTime)
                                    MoreSelectableValue("Cadence", value: schedule.cadence.replacingOccurrences(of: "_", with: " ").capitalized)
                                    MoreSelectableValue("Worker", value: schedule.workerType.replacingOccurrences(of: "_", with: " ").capitalized)
                                }.listRowBackground(PanelsTheme.raised)
                                if !schedule.kickoffNote.isEmpty { Section { MoreSelectableText(schedule.kickoffNote) } header: { MoreSelectableText("Kickoff Note", style: .footnote, color: Color(uiColor: .secondaryLabel)) }.listRowBackground(PanelsTheme.raised) }
                            }.panelsNavigationTitle(schedule.title).navigationBarTitleDisplayMode(.inline).panelsScreen().onAppear { Haptics.light() }
                        } label: {
                            VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                                Text(schedule.title).font(PanelsTheme.TypeScale.itemTitle)
                                Text("\(schedule.localTime) · \(schedule.enabled ? "Enabled" : "Disabled")")
                                    .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                            }
                        }.systemCopyText(schedule.title + "\n" + schedule.localTime)
                    }
                } header: { MoreSelectableText("Scheduled Tasks", style: .footnote, color: Color(uiColor: .secondaryLabel)) } footer: { MoreSelectableText("Schedules follow the server’s local time. Manage schedules on the web.", style: .footnote, color: Color(uiColor: .secondaryLabel)) }
                .listRowBackground(PanelsTheme.raised)
            }
        }
    }
    private var notificationPreferences: some View {
        Group {
            NotificationDeviceSettings()
            if let error = notificationEditor.error {
                Section {
                    LoadErrorView(message: error) {
                        await notificationEditor.retry(using: store.client, resources: store.resources)
                    }
                }
            }
            if let notifications {
                ForEach(notifications.subjects, id: \.key) { subject in
                    Section {
                        ForEach(subject.types) { preference in
                            Toggle(isOn: Binding(get: { preference.enabled }, set: { enabled in
                                Task {
                                    await notificationEditor.save(subjectKey: subject.key, type: preference.id, enabled: enabled,
                                                                  using: store.client, resources: store.resources)
                                }
                            })) {
                                VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                                    Text(preference.label).font(PanelsTheme.TypeScale.itemTitle)
                                    Text(preference.description).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                                }
                            }
                            .tint(PanelsTheme.accent)
                            .systemCopyText(preference.label + "\n" + preference.description)
                            .disabled(notificationEditor.isSaving)
                            .accessibilityIdentifier("notification-preference-\(subject.key)-\(preference.id)")
                        }
                    } header: { MoreSelectableText(subject.label, style: .footnote, weight: .medium, color: Color(uiColor: .secondaryLabel)) }.listRowBackground(PanelsTheme.raised)
                }
            }
        }
    }
    @MainActor private func load() async {
        switch section {
        case "Config":
            async let workers: Void = workerResource.refresh()
            async let skills: Void = skillResource.refresh()
            _ = await (workers, skills)
        case "Scheduled": await scheduleResource.refresh()
        default: await notificationResource.refresh()
        }
    }

}

private struct SettingsAgentDetail: View {
    @Environment(AppStore.self) private var store
    let title: String
    let configuration: EmployeeConfiguration
    let skill: ManagedSkill?
    var body: some View {
        List {
            Section {
                MoreSelectableValue("Backend", value: store.backendLabel(configuration.employeeBackend))
                MoreSelectableValue("Model", value: configuration.employeeLaunchModel ?? "Backend default")
                MoreSelectableValue("Reasoning", value: configuration.employeeLaunchReasoningEffort?.capitalized ?? "Backend default")
            } header: { MoreSelectableText("Launch Defaults", style: .footnote, weight: .medium, color: Color(uiColor: .secondaryLabel)) }.listRowBackground(PanelsTheme.raised)
            if let skill {
                Section { NavigationLink("Role skill") { SettingsSkillDetail(skill: skill) } }
                    .listRowBackground(PanelsTheme.raised)
            }
            Section { MoreSelectableText("Manage configuration on the web.", style: .footnote, color: PanelsTheme.faint) }
                .listRowBackground(PanelsTheme.raised)
        }.panelsNavigationTitle(title).panelsScreen().onAppear { Haptics.light() }
    }
}
private struct SettingsSkillDetail: View {
    let skill: ManagedSkill
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
                MoreSelectableText(skill.description, style: .title3, weight: .medium)
                MoreSelectableText(skill.markdownBody)
            }.frame(maxWidth: .infinity, alignment: .leading).padding(PanelsTheme.Space.lg)
        }.panelsNavigationTitle(skill.name).navigationBarTitleDisplayMode(.inline).panelsScreen().onAppear { Haptics.light() }
    }
}

struct ServerAddressSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var address: String
    @State private var error: String?
    let isFirstLaunch: Bool
    let save: (URL) -> Void

    init(initialAddress: String, isFirstLaunch: Bool = false, save: @escaping (URL) -> Void) {
        _address = State(initialValue: initialAddress)
        self.isFirstLaunch = isFirstLaunch
        self.save = save
    }
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("https://machine.tailnet.ts.net", text: $address)
                        .textContentType(.URL).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .accessibilityLabel("HTTPS address")
                    if let error { MoreSelectableText(error, color: PanelsTheme.error) }
                } header: {
                    MoreSelectableText("HTTPS Address", style: .footnote, color: Color(uiColor: .secondaryLabel))
                } footer: {
                    if isFirstLaunch {
                        MoreSelectableText("Enter your Panels home's Tailscale Serve address. Connect this device to the same tailnet.", style: .footnote, color: Color(uiColor: .secondaryLabel))
                    }
                }.listRowBackground(PanelsTheme.raised)
            }.panelsNavigationTitle(isFirstLaunch ? "Connect to Panels" : "Server").navigationBarTitleDisplayMode(.inline).panelsScreen()
                .toolbar {
                    if !isFirstLaunch {
                        ToolbarItem(placement: .cancellationAction) { Button("Cancel") { Haptics.light(); dismiss() } }
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button(isFirstLaunch ? "Continue" : "Save") {
                            guard let url = SettingsServerAddress.validated(address) else {
                                error = "Enter an HTTPS server address without a path."; return
                            }
                            save(url)
                            Haptics.success()
                            if !isFirstLaunch { dismiss() }
                        }
                    }
                }
        }.presentationDragIndicator(.visible)
    }
}

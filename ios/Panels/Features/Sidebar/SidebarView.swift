import SwiftUI

/// The slide-over Sidebar: Chief, the active Managers, Settled, and at the bottom
/// Review and the Settings gear. Cards carry no ticket rows.
struct SidebarView: View {
    let current: AppDestination
    let reviewBadge: Int
    let select: (AppDestination) -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var goalsHandle: ResourceHandle<GoalsResponse>?
    @State private var settledOpen = false
    @State private var settledBusy: String?
    @State private var settledError: String?
    private var goals: [GoalRecord] { goalsHandle?.value?.goals ?? [] }
    private var managers: [ManagerSummary] {
        store.managers.sorted { $0.createdAt == $1.createdAt ? $0.id < $1.id : $0.createdAt < $1.createdAt }
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(spacing: PanelsTheme.Web.Rail.gap) {
                    SidebarCard(identifier: "sidebar-chief", eyebrow: "All projects", title: store.chief?.label ?? "Chief of Staff", signal: store.chiefSignal,
                                tag: SidebarCard.worstGoal(.chief, goals: goals), selected: current == .chief) { select(.chief) }
                    ForEach(managers) { manager in
                        SidebarCard(identifier: "sidebar-manager-\(manager.id)", eyebrow: manager.project, title: manager.title, signal: manager.conversationSignal,
                                    tag: SidebarCard.worstGoal(.manager(manager.id), goals: goals), selected: current == .manager(manager.id)) {
                            select(.manager(manager.id))
                        }
                    }
                }.padding(.horizontal, PanelsTheme.Web.Rail.inset).padding(.vertical, PanelsTheme.Web.Rail.gap)
            }
            .scrollIndicators(.hidden)
            VStack(spacing: PanelsTheme.Web.Rail.gap) {
                settledGroup
                bottomBar
            }
            .padding(.horizontal, PanelsTheme.Web.Rail.inset).padding(.top, PanelsTheme.Web.Rail.gap)
            .overlay(alignment: .top) { Divider().overlay(PanelsTheme.Web.border) }
        }
        .background(PanelsTheme.Web.surface)
        .foregroundStyle(PanelsTheme.Web.text)
        .task(id: store.baseURL) {
            let goals = store.resources.handle(GoalResources.list)
            goalsHandle = goals
            goals.acquire()
            defer { goals.release() }
            await goals.refresh()
            do { try await Task.sleep(for: .seconds(365 * 24 * 60 * 60)) } catch { }
        }
        .onChange(of: settledOpen, initial: true) { _, open in
            store.setNavigationDemand("sidebar.settled", open ? [ViewWindowRequest(section: "settled", limit: NavigationScope.maximumWindowRows)] : nil)
        }
        .onDisappear { store.setNavigationDemand("sidebar.settled", nil) }
        .onChange(of: store.baseURL) { _, _ in settledOpen = false; settledError = nil }
    }

    private var settledGroup: some View {
        VStack(spacing: 0) {
            Button {
                Haptics.selection()
                withAnimation(reduceMotion ? nil : .timingCurve(0.4, 0.02, 0.2, 1, duration: PanelsTheme.Web.Rail.Motion.space)) { settledOpen.toggle() }
            } label: {
                HStack(spacing: 8) {
                    Text("Settled")
                    if let count = store.counts?.settled {
                        Text(String(count)).panelsScaledFont(size: PanelsTheme.Web.smallSize).foregroundStyle(PanelsTheme.Web.tertiary).monospacedDigit()
                    }
                    Spacer()
                    Image(systemName: "chevron.right").font(.system(size: 14))
                        .foregroundStyle(PanelsTheme.Web.tertiary).rotationEffect(.degrees(settledOpen ? 90 : 0))
                }.panelsScaledFont(size: PanelsTheme.Web.rowSize)
                    .padding(.horizontal, PanelsTheme.Web.Rail.textInset)
                    .frame(minHeight: 44).contentShape(Rectangle())
            }.buttonStyle(SidebarButtonStyle(resting: PanelsTheme.Web.secondary))
                .accessibilityIdentifier("sidebar-settled")
                .accessibilityValue(settledOpen ? "Expanded" : "Collapsed")
            if settledOpen {
                ScrollView {
                    VStack(spacing: 0) {
                        ForEach(store.workspaceSettled) { settledRow($0) }
                        if let settledError {
                            Text(settledError).panelsScaledFont(size: PanelsTheme.Web.smallSize).foregroundStyle(PanelsTheme.Web.tertiary)
                                .padding(.horizontal, PanelsTheme.Web.Rail.textInset)
                        }
                    }
                }.frame(maxHeight: 240).fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(PanelsTheme.Web.Rail.padding)
        .background(PanelsTheme.Web.rail, in: RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius))
    }

    private func settledRow(_ manager: ManagerSummary) -> some View {
        HStack(spacing: 8) {
            Button { select(.manager(manager.id)) } label: {
                HStack {
                    Text(manager.title).lineLimit(typeSize.isAccessibilitySize ? nil : 1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    ConversationSignalMark(signal: manager.conversationSignal)
                }.contentShape(Rectangle())
            }.buttonStyle(SidebarButtonStyle(resting: manager.conversationSignal?.messageForOwner == true ? PanelsTheme.Web.strong : PanelsTheme.Web.railRow))
                .conversationAccessibility(label: manager.title, signal: manager.conversationSignal)
            Button("Reopen") { reopen(manager.id) }
                .panelsScaledFont(size: PanelsTheme.Web.smallSize).foregroundStyle(PanelsTheme.Web.tertiary)
                .disabled(settledBusy != nil).accessibilityLabel("Reopen \(manager.title)")
        }.panelsScaledFont(size: PanelsTheme.Web.rowSize)
            .padding(.horizontal, PanelsTheme.Web.Rail.textInset)
            .frame(minHeight: 44)
    }

    private var bottomBar: some View {
        HStack {
            Button { select(.review) } label: {
                HStack(spacing: 8) {
                    Image(systemName: "checkmark.square").foregroundStyle(PanelsTheme.Web.accent)
                    Text("Review")
                    if reviewBadge > 0 {
                        Text(String(reviewBadge)).panelsScaledFont(size: PanelsTheme.Web.smallSize, weight: .semibold).monospacedDigit()
                            .foregroundStyle(PanelsTheme.ink)
                            .padding(.horizontal, 7).frame(minHeight: 20)
                            .background(PanelsTheme.Web.accent, in: Capsule())
                    }
                }.panelsScaledFont(size: PanelsTheme.Web.bodySize, weight: .semibold)
                    .padding(.horizontal, PanelsTheme.Web.Rail.textInset).frame(minHeight: 44).contentShape(Rectangle())
            }.buttonStyle(SidebarButtonStyle(resting: current == .review ? PanelsTheme.Web.strong : PanelsTheme.Web.railTitle))
                .accessibilityLabel(reviewBadge > 0 ? "Review, \(reviewBadge) to review" : "Review")
                .accessibilityIdentifier("sidebar-review")
            Spacer()
            Button { select(.settings) } label: {
                Image(systemName: "gearshape").font(.system(size: 20))
                    .frame(width: 44, height: 44).contentShape(Rectangle())
            }.buttonStyle(SidebarButtonStyle(resting: current == .settings ? PanelsTheme.Web.strong : PanelsTheme.Web.railRow))
                .accessibilityLabel("Settings")
                .accessibilityIdentifier("sidebar-settings")
        }
    }

    private func reopen(_ id: String) {
        guard settledBusy == nil else { return }
        settledBusy = id; settledError = nil
        Task {
            defer { settledBusy = nil }
            do {
                try await store.client.setManagerSettled(id, settled: false)
                Haptics.success(); await store.refresh()
            } catch { Haptics.error(); settledError = error.localizedDescription }
        }
    }
}

/// One rail card: the project eyebrow with the worst goal status, then the title and its signal.
private struct SidebarCard: View {
    struct Tag { let status: String }
    let identifier: String
    let eyebrow: String
    let title: String
    let signal: ConversationSignal?
    let tag: Tag?
    let selected: Bool
    let action: () -> Void

    static func worstGoal(_ holder: GoalHolder, goals: [GoalRecord]) -> Tag? {
        let ranks = ["bad": 0, "attention": 1, "good": 2]
        return holder.goals(in: goals).filter { $0.active && ranks[$0.status] != nil }
            .min { ranks[$0.status, default: 3] < ranks[$1.status, default: 3] }
            .map { Tag(status: $0.status) }
    }

    private var tagLabel: String? {
        switch tag?.status { case "bad": "Off track"; case "attention": "At risk"; case "good": "On track"; default: nil }
    }

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(eyebrow).foregroundStyle(PanelsTheme.Web.tertiary).lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if let tag, let tagLabel {
                        Text(tagLabel).foregroundStyle(PanelsTheme.Web.goalTone(tag.status)).fixedSize()
                    }
                }.panelsScaledFont(size: PanelsTheme.Web.smallSize, relativeTo: .caption, weight: .medium)
                HStack(alignment: .top, spacing: 8) {
                    Text(title).panelsScaledFont(size: PanelsTheme.Web.Rail.titleSize, relativeTo: .title3, weight: .semibold)
                        .tracking(-0.014 * PanelsTheme.Web.Rail.titleSize)
                        .foregroundStyle(selected || signal?.messageForOwner == true ? PanelsTheme.Web.strong : PanelsTheme.Web.railTitle)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, minHeight: PanelsTheme.Web.Rail.titleLine, alignment: .leading)
                    ConversationSignalMark(signal: signal).frame(height: PanelsTheme.Web.Rail.titleLine)
                }
            }
            .padding(.horizontal, 14).padding(.top, 11).padding(.bottom, 13)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(alignment: .top) {
                if let tag, !selected {
                    LinearGradient(colors: [PanelsTheme.Web.goalTone(tag.status).opacity(tag.status == "good" ? 0.07 : 0.09), .clear], startPoint: .top, endPoint: .bottom)
                        .frame(height: PanelsTheme.Web.Rail.hazeHeight)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(SidebarCardStyle())
        // One button element carries the whole card: its label, signal and identifier.
        .accessibilityLabel([eyebrow, title, tagLabel.map { "Goals: " + $0 }].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: ", "))
        .accessibilityValue(signal?.messageForOwner == true ? "Unread message" : signal?.working == true ? "Working" : "")
        .accessibilityIdentifier(identifier)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

private struct SidebarCardStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(configuration.isPressed ? PanelsTheme.Web.hover : PanelsTheme.Web.rail)
            .clipShape(RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius))
    }
}

private struct SidebarButtonStyle: ButtonStyle {
    var resting = PanelsTheme.Web.railRow
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(configuration.isPressed ? PanelsTheme.Web.strong : resting)
            .background(configuration.isPressed ? PanelsTheme.Web.hover : .clear, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
    }
}

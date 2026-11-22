import SwiftUI
import UIKit

struct TicketView: View {
    let ticketID: String
    var openConversationID: String?
    @Environment(AppStore.self) private var store
    private var resource: ResourceHandle<TicketRead> { store.resources.handle(TicketResources.detail(ticketID)) }
    private var detail: TicketDetail? { resource.value?.detail }
    private var manifestResource: ResourceHandle<TicketWorkerTypes>? {
        guard let detail, detail.workflow == nil, let type = detail.workerType else { return nil }
        return store.resources.handle(TicketResources.manifest(type))
    }
    private var manifest: TicketWorkerType? { detail?.workflow ?? manifestResource?.value?.workerTypes.first }
    @State private var artifacts: ArtifactPage?
    private var error: String? { resource.error ?? manifestResource?.error }
    @State private var artifactError: String?
    @State private var actionNote: String?
    @State private var busy = false
    @State private var scopeOpen = false
    @State private var stepsOpen = false
    @State private var recapEditorOpen = false
    @State private var configurationOpen = false
    @State private var decision = TicketDecisionState()
    @State private var linkedArtifact: AppRoute?
    private var owner: ArtifactOwner { ArtifactOwner(kind: .ticket, id: ticketID) }
    private var presentation: TicketPresentation? {
        guard let detail, let manifest else { return nil }
        return TicketPresentation(ticket: detail, manifest: manifest)
    }

    var body: some View {
        ScreenTextEntryScope { textEntry in ticketContent(textEntry: textEntry) }
    }

    private func ticketContent(textEntry: ScreenTextEntryContext) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                AgentPageControls { ticketMenu }
                if let detail {
                    masthead(detail)
                    if let presentation {
                        stageCard(presentation)
                        steps(presentation)
                        artifactRow
                    }
                }
            }.frame(maxWidth: 720, alignment: .leading)
                .padding(.horizontal, AgentPageLayout.gutter).padding(.bottom, PanelsTheme.Space.lg)
                .frame(maxWidth: .infinity)
        }
        .toolbar(.hidden, for: .navigationBar)
        .scrollContentBackground(.hidden).textCase(nil)
        .background(PanelsTheme.Web.surface).foregroundStyle(PanelsTheme.Web.text)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if let presentation, let proposal = detail?.pendingProposal, proposal.field == presentation.currentField {
                TicketDecisionDock(presentation: presentation, proposal: proposal, decision: decision)
            }
        }
        .conversationNavigation(owner: .ticket(ticketID), conversationID: detail?.conversationId, title: detail?.title ?? "Ticket", openConversationID: openConversationID, refreshError: error, retry: { await load() }, textEntry: textEntry)
        .panelsRefreshable { await load(); await loadArtifacts() }
        .screenLoadState("ticket", hasContent: detail != nil, error: error) { await load() }
        .resourceDemand(resource)
        .task(id: store.baseURL.absoluteString + "|" + (detail?.workerType ?? "") + "|" + String(detail?.workflow == nil)) {
            guard detail?.workflow == nil, let handle = manifestResource else { return }
            handle.acquire(); defer { handle.release() }
            await handle.refresh()
            while !Task.isCancelled { do { try await Task.sleep(for: .seconds(3600)) } catch { return } }
        }
        .navigationDemand("ticket:\(ticketID)", selected: ViewOwner(kind: "ticket", id: ticketID))
        .task(id: "\(store.baseURL)|\(artifacts == nil ? store.connectionSession : 0)") { await loadArtifacts() }
        .sheet(isPresented: $scopeOpen) {
            if let presentation {
                TicketApprovalLimitSheet(presentation: presentation) { ceiling, holder in
                    try await store.client.setTicketApprovalLimit(ticketID, ceiling: ceiling, holder: holder)
                    await load(); await store.refresh()
                }
            }
        }
        .sheet(isPresented: $recapEditorOpen) {
            TicketTextEditor(title: "Recap", save: { text in
                try await store.client.editTicketRecap(ticketID, recap: text)
                await load(); await store.refresh()
            }, text: detail?.recap ?? "")
        }
        .sheet(isPresented: $configurationOpen) {
            if let detail { TicketWorkerSetup(ticket: detail) { await load() } }
        }
        .navigationDestination(item: $linkedArtifact) { RouteDestination(route: $0) }
        .environment(\.openURL, OpenURLAction { url in
            if let route = TicketArtifactRoute.linkedDestination(url, baseURL: store.baseURL, title: url.lastPathComponent) { textEntry.endEditingBeforeNavigation { linkedArtifact = route }; return .handled }
            return ArtifactTarget.isRetiredGoalURL(url, baseURL: store.baseURL) ? .discarded : .systemAction
        })
    }

    private var ticketMenu: some View {
        Menu {
            if let detail, presentation?.terminal == false {
                Button("Ask for a recap", systemImage: "arrow.clockwise") { requestRecap() }
                Button(detail.isBacklogged ? "Resume ticket" : "Backlog ticket", systemImage: detail.isBacklogged ? "play" : "pause") { changeBacklog(detail) }
            }
            Button("Copy link", systemImage: "doc.on.doc") {
                var components = URLComponents(url: store.baseURL, resolvingAgainstBaseURL: false)
                components?.fragment = "/workspace/\(ticketID)"
                UIPasteboard.general.url = components?.url
                Haptics.success()
            }
        } label: { Image(systemName: "ellipsis").frame(minWidth: 44, minHeight: 44) }
            .disabled(busy).accessibilityLabel("Ticket actions").accessibilityIdentifier("ticket-actions")
    }

    private func masthead(_ detail: TicketDetail) -> some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
            Text(detail.managerTitle ?? "Chief of Staff")
                .panelsScaledFont(size: PanelsTheme.Web.smallSize)
                .foregroundStyle(PanelsTheme.Web.tertiary)
            Text(detail.title)
                .panelsScaledFont(size: PanelsTheme.Web.pageTitleSize, relativeTo: .title, weight: .semibold)
                .tracking(-0.26).lineSpacing(1)
                .foregroundStyle(PanelsTheme.Web.strong)
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
            TicketMachineLabel(locale: detail.locale)
            Group {
                if detail.recap.isEmpty {
                    Button("+ add orientation") { recapEditorOpen = true }.foregroundStyle(PanelsTheme.faint)
                } else { TicketRecapText(text: detail.recap) }
            }
                .contextMenu {
                    Button("Copy", systemImage: "doc.on.doc") { UIPasteboard.general.string = detail.recap; Haptics.success() }
                    Button("Edit", systemImage: "pencil") { recapEditorOpen = true }
                    Button("Ask for a fresh recap", systemImage: "arrow.clockwise") { requestRecap() }
                }
            if let blockers = detail.blockerSummary?.blockedBy, !blockers.isEmpty {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                    Text("Blocked by").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    ForEach(blockers, id: \.ticketId) { blocker in
                        NavigationLink(value: AppRoute.ticket(blocker.ticketId)) { Text(blocker.title).multilineTextAlignment(.leading) }
                            .font(PanelsTheme.TypeScale.small).padding(.vertical, PanelsTheme.Space.xs)
                            .contextMenu {
                                Button("Remove blocker", systemImage: "minus.circle", role: .destructive) {
                                    Task { await perform { try await store.client.removeTicketBlocker(ticketID, blockerID: blocker.ticketId); return nil } }
                                }
                            }
                    }
                }
            }
            if detail.isBacklogged {
                HStack {
                    Text("Backlogged. Automatic work is paused.").foregroundStyle(PanelsTheme.faint)
                    Button("Resume") { changeBacklog(detail) }.font(PanelsTheme.TypeScale.sectionLabel).disabled(busy)
                }.font(PanelsTheme.TypeScale.small)
            }
            if let actionNote {
                Text(actionNote).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error)
                    .accessibilityIdentifier("ticket-action-note")
            }
        }
    }

    @ViewBuilder private func stageCard(_ presentation: TicketPresentation) -> some View {
        if let proposal = detail?.pendingProposal, proposal.field == presentation.currentField {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                TicketProposalCard(presentation: presentation, proposal: proposal, decision: decision) { await load(); await store.refresh() }
                    .id("\(proposal.field):\(proposal.revision)")
                workerSetup(presentation)
            }
        } else {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                Text(presentation.terminal ? "Done" : presentation.currentStage?.label ?? presentation.heading)
                    .panelsScaledFont(size: 15, weight: .semibold)
                    .foregroundStyle(presentation.ticket.agentState == "errored" ? PanelsTheme.Web.error : PanelsTheme.Web.strong)
                    .accessibilityAddTraits(.isHeader)
                if !presentation.terminal {
                    Button { scopeOpen = true } label: {
                        HStack(alignment: .firstTextBaseline, spacing: PanelsTheme.Space.sm) {
                            Text(presentation.ticket.ceiling == "done" ? "Approved until **\(presentation.ceilingLabel)**" : "Approved until **\(presentation.ceilingLabel)**, then **\(presentation.holderLabel(presentation.ticket.ceilingHolder))** reviews")
                                .multilineTextAlignment(.leading)
                            Image(systemName: "chevron.down").font(PanelsTheme.TypeScale.caption)
                        }.panelsScaledFont(size: PanelsTheme.Web.bodySize)
                            .foregroundStyle(PanelsTheme.Web.secondary)
                            .frame(minHeight: 44, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
                    }.buttonStyle(.plain).disabled(busy).accessibilityIdentifier("ticket-approval-limit")
                }
            if !presentation.terminal, presentation.ticket.assigned == true || presentation.currentStage?.ownershipMode == "user", let field = presentation.currentField,
               presentation.ticket.pendingProposal?.field != field {
                TicketExpandableText(text: presentation.ticket.fieldValues[field] ?? "", emptyText: "Not written yet.")
            }
            workerSetup(presentation)
        }.padding(20).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.white.opacity(0.045), in: RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius))
        }
    }

    @ViewBuilder private func workerSetup(_ presentation: TicketPresentation) -> some View {
        if !presentation.terminal, presentation.ticket.employeeConfigurationEditable {
            Button { configurationOpen = true } label: {
                Text([store.backendLabel(presentation.ticket.employeeBackend), presentation.ticket.employeeLaunchModel, presentation.ticket.employeeLaunchReasoningEffort?.capitalized].compactMap { $0 }.joined(separator: " · "))
                    .font(PanelsTheme.TypeScale.small).frame(minHeight: 44)
            }.buttonStyle(.plain)
        }
    }

    private func steps(_ presentation: TicketPresentation) -> some View {
        VStack(spacing: 0) {
            Rectangle().fill(PanelsTheme.Web.border).frame(height: 1)
            Button { stepsOpen.toggle() } label: {
                HStack {
                    Text("Steps").fontWeight(.semibold)
                    Spacer()
                    Text("\(presentation.completedCount) of \(presentation.manifest.fields.count) done").foregroundStyle(PanelsTheme.Web.tertiary)
                }.panelsScaledFont(size: PanelsTheme.Web.smallSize)
                    .foregroundStyle(PanelsTheme.Web.secondary).frame(minHeight: 44).padding(.horizontal, 8).contentShape(Rectangle())
            }.buttonStyle(.plain)
                .accessibilityLabel("Steps")
                .accessibilityValue("\(presentation.manifest.fields.count) steps, \(stepsOpen ? "expanded" : "collapsed")")
            if stepsOpen {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(presentation.manifest.fields) { field in TicketStepRow(field: field, presentation: presentation) }
                }
            }
            Rectangle().fill(PanelsTheme.Web.border).frame(height: 1)
        }
    }

    @ViewBuilder private var artifactRow: some View {
        if let artifactError { LoadErrorView(message: artifactError) { await loadArtifacts() } }
        if let artifacts, artifacts.total > 0 {
            ArtifactsRow(owner: owner, count: artifacts.total)
                .accessibilityIdentifier("ticket-artifacts")
        }
    }

    private func requestRecap() { Task { await perform { try await store.client.requestTicketRecap(ticketID).note } } }
    private func changeBacklog(_ ticket: TicketDetail) {
        Task { await perform { try await store.client.setTicketBacklogged(ticket, backlogged: !ticket.isBacklogged); return nil } }
    }
    private func load() async {
        store.resources.invalidate([resource.key])
        await resource.refresh()
        if detail?.workflow == nil { await manifestResource?.refresh() }
    }
    private func loadArtifacts() async {
        let client = store.client
        do {
            let result = try await client.artifacts(owner: owner)
            try Task.checkCancellation()
            guard client.baseURL == store.baseURL else { return }
            artifacts = result; artifactError = nil
        } catch { if !Task.isCancelled { artifactError = ServerReadFailure.note(error) } }
    }
    private func perform(_ action: () async throws -> String?) async {
        guard !busy else { return }
        busy = true; actionNote = nil
        defer { busy = false }
        do {
            actionNote = try await action()
            if actionNote == nil { Haptics.success() } else { Haptics.error() }
            await load(); await store.refresh()
        } catch { actionNote = error.localizedDescription; Haptics.error() }
    }
}

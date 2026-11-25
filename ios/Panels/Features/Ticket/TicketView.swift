import SwiftUI
import UIKit

struct TicketView: View {
    let ticketID: String
    @Environment(AppStore.self) private var store
    private var resource: ResourceHandle<TicketRead> { store.resources.handle(TicketResources.detail(ticketID)) }
    private var detail: TicketDetail? { resource.value?.detail }
    private var manifestResource: ResourceHandle<TicketWorkerTypes>? {
        guard let detail, detail.workflow == nil, let type = detail.workerType else { return nil }
        return store.resources.handle(TicketResources.manifest(type))
    }
    private var manifest: TicketWorkerType? { detail?.workflow ?? manifestResource?.value?.workerTypes.first }
    @ScaledMetric(relativeTo: .title) private var titleSize = PanelsTheme.Web.pageTitleSize
    @State private var artifacts: ArtifactPage?
    private var error: String? { resource.error ?? manifestResource?.error }
    @State private var artifactError: String?
    @State private var scopeOpen = false
    @State private var actionsOpen = false
    @State private var stepsOpen = false
    @State private var configurationOpen = false
    @State private var decision = TicketDecisionState()
    @Environment(\.openRoute) private var openRoute
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
        .navigationTitle("").navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { ticketMenu } }
        .scrollContentBackground(.hidden).textCase(nil)
        .background(PanelsTheme.Web.surface).foregroundStyle(PanelsTheme.Web.text)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if let presentation, let proposal = detail?.pendingProposal, proposal.field == presentation.currentField {
                TicketDecisionDock(presentation: presentation, proposal: proposal, decision: decision)
            }
        }
        .conversationNavigation(owner: .ticket(ticketID), conversationID: detail?.conversationId, refreshError: error, retry: { await load() }, textEntry: textEntry)
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
        .sheet(isPresented: $actionsOpen) {
            if let detail {
                TicketActionsSheet(ticket: detail, canChange: presentation?.terminal == false, baseURL: store.baseURL,
                    askForRecap: {
                        let delivery = try await store.client.requestTicketRecap(ticketID)
                        if let note = delivery.note { throw APIError(status: 0, message: note) }
                        await load(); await store.refresh()
                    },
                    setBacklog: {
                        try await store.client.setTicketBacklogged(detail, backlogged: !detail.isBacklogged)
                        await load(); await store.refresh()
                    })
            }
        }
        .sheet(isPresented: $configurationOpen) {
            if let detail { TicketWorkerSetup(ticket: detail) { await load() } }
        }
        .environment(\.openURL, OpenURLAction { url in
            if let route = TicketArtifactRoute.linkedDestination(url, baseURL: store.baseURL, title: url.lastPathComponent, conversationOwner: .ticket(ticketID)) { textEntry.endEditingBeforeNavigation { openRoute(route) }; return .handled }
            return ArtifactTarget.isRetiredGoalURL(url, baseURL: store.baseURL) ? .discarded : .systemAction
        })
    }

    private var ticketMenu: some View {
        Button { actionsOpen = true } label: { Image(systemName: "ellipsis") }
            .accessibilityLabel("Ticket actions").accessibilityIdentifier("ticket-actions")
    }

    private func masthead(_ detail: TicketDetail) -> some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
            Text(detail.managerTitle ?? "Chief of Staff").textSelection(.enabled)
                .panelsScaledFont(size: PanelsTheme.Web.smallSize)
                .foregroundStyle(PanelsTheme.Web.tertiary)
            PanelsSelectableText(detail.title, font: .systemFont(ofSize: titleSize, weight: .semibold),
                color: UIColor(PanelsTheme.Web.strong), lineSpacing: 1, tracking: -0.26)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("ticket-title")
            TicketMachineLabel(locale: detail.locale, selectable: true)
            if !detail.recap.isEmpty { TicketRecapText(text: detail.recap) }
            if let blockers = detail.blockerSummary?.blockedBy, !blockers.isEmpty {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                    Text("Blocked by").textSelection(.enabled).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    ForEach(blockers, id: \.ticketId) { blocker in
                        NavigationLink(value: AppRoute.ticket(blocker.ticketId)) { Text(blocker.title).multilineTextAlignment(.leading) }
                            .font(PanelsTheme.TypeScale.small).padding(.vertical, PanelsTheme.Space.xs)
                            .systemCopyText(blocker.title)
                    }
                }
            }
            if detail.isBacklogged {
                Text("Backlogged. Automatic work is paused.").textSelection(.enabled).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
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
                    .accessibilityAddTraits(.isHeader).textSelection(.enabled)
                if !presentation.terminal {
                    Button { scopeOpen = true } label: {
                        HStack(alignment: .firstTextBaseline, spacing: PanelsTheme.Space.sm) {
                            Text(presentation.ticket.ceiling == "done" ? "Approved until **\(presentation.ceilingLabel)**" : "Approved until **\(presentation.ceilingLabel)**, then **\(presentation.holderLabel(presentation.ticket.ceilingHolder))** reviews")
                                .multilineTextAlignment(.leading)
                            Image(systemName: "chevron.down").font(PanelsTheme.TypeScale.caption)
                        }.panelsScaledFont(size: PanelsTheme.Web.bodySize)
                            .foregroundStyle(PanelsTheme.Web.secondary)
                            .frame(minHeight: 44, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
                    }.buttonStyle(.plain).accessibilityIdentifier("ticket-approval-limit")
                        .systemCopyText("Approved until \(presentation.ceilingLabel), then \(presentation.holderLabel(presentation.ticket.ceilingHolder)) reviews")
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
                .systemCopyText([store.backendLabel(presentation.ticket.employeeBackend), presentation.ticket.employeeLaunchModel,
                    presentation.ticket.employeeLaunchReasoningEffort?.capitalized].compactMap { $0 }.joined(separator: " · "))
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
        // Until the server's list arrives, the list this phone read last, so saved artifacts
        // open offline. It is read here, where the row draws, not handed over by a task.
        let shown = artifacts ?? store.client.cachedArtifacts(owner: owner)
        if let artifactError, shown == nil { LoadErrorView(message: artifactError) { await loadArtifacts() } }
        if let shown, shown.total > 0 {
            ArtifactsRow(owner: owner, count: shown.total)
                .accessibilityIdentifier("ticket-artifacts")
        }
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
}

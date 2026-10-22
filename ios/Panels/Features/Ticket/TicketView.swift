import SwiftUI

struct TicketView: View {
    let ticketID: String
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var detail: TicketDetail?
    @State private var manifest: TicketWorkerType?
    @State private var artifacts: ArtifactPage?
    @State private var error: String?
    @State private var artifactError: String?
    @State private var actionNote: String?
    @State private var busy = false
    @State private var stepsOpen = false
    @State private var artifactsOpen = false
    @State private var scopeOpen = false
    @State private var loadGeneration = 0

    private var owner: ArtifactOwner { ArtifactOwner(kind: .ticket, id: ticketID) }
    private var presentation: TicketPresentation? {
        guard let detail, let manifest else { return nil }
        return TicketPresentation(ticket: detail, manifest: manifest)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                if let error { LoadErrorView(message: error) { await load() } }
                if let detail {
                    masthead(detail)
                    if let presentation {
                        stageCard(presentation)
                        bands(presentation)
                    }
                    ConversationPlaceholder()
                } else if error == nil {
                    ProgressView("Loading ticket…").frame(maxWidth: .infinity).padding(.top, PanelsTheme.Space.xxl)
                }
            }
            .frame(maxWidth: 720, alignment: .leading)
            .padding(.horizontal, PanelsTheme.Space.xl)
            .padding(.vertical, PanelsTheme.Space.xl)
            .frame(maxWidth: .infinity)
        }
        .navigationTitle("Ticket")
        .navigationBarTitleDisplayMode(.inline)
        .panelsScreen()
        .refreshable { Haptics.light(); await load(); await loadArtifacts() }
        .task(id: "\(store.baseURL.absoluteString):\(store.revision)") { await load() }
        .task(id: store.baseURL.absoluteString) { await loadArtifacts() }
        .sheet(isPresented: $scopeOpen) {
            if let presentation {
                TicketApprovalLimitSheet(presentation: presentation) { ceiling, holder in
                    await perform {
                        try await store.client.setTicketApprovalLimit(ticketID, ceiling: ceiling, holder: holder)
                        return nil
                    }
                }
            }
        }
    }

    private func masthead(_ detail: TicketDetail) -> some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
            Text("\(detail.project ?? "No project")  ·  \(manifest?.label ?? detail.workerType.replacingOccurrences(of: "_", with: " ").capitalized)")
                .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
            HStack(alignment: .firstTextBaseline, spacing: PanelsTheme.Space.md) {
                Text(detail.title).font(PanelsTheme.TypeScale.title).foregroundStyle(PanelsTheme.strong)
                    .fixedSize(horizontal: false, vertical: true).accessibilityAddTraits(.isHeader)
                if detail.conversationSignal?.working == true {
                    ProgressView().controlSize(.small).tint(PanelsTheme.accent).accessibilityLabel("Working")
                }
            }
            TicketExpandableText(text: detail.recap, emptyText: "No recap yet.")
            if let presentation, !presentation.terminal {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: PanelsTheme.Space.md) { lifecycleActions(detail) }
                    VStack(alignment: .leading, spacing: 0) { lifecycleActions(detail) }
                }
                .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                .buttonStyle(.plain)
                .disabled(busy)
                if detail.isBacklogged {
                    Text("Backlogged. Automatic work is paused. Messages and approvals remain available.")
                        .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                }
            }
            if let actionNote {
                Text(actionNote).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                    .accessibilityIdentifier("ticket-action-note")
            }
            if let blockers = detail.blockerSummary?.blockedBy, !blockers.isEmpty {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                    Text("Blocked by").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    ForEach(blockers, id: \.ticketId) { blocker in
                        NavigationLink(value: AppRoute.ticket(blocker.ticketId)) {
                            Label(blocker.title, systemImage: "arrow.up.right")
                        }.font(PanelsTheme.TypeScale.small).padding(.vertical, PanelsTheme.Space.xs)
                    }
                }
            }
        }
    }

    @ViewBuilder private func lifecycleActions(_ detail: TicketDetail) -> some View {
        Button("Ask for a recap") {
            Haptics.light()
            Task { await perform { try await store.client.requestTicketRecap(ticketID).note } }
        }.frame(minHeight: 44).accessibilityIdentifier("ticket-request-recap")
        Button(detail.isBacklogged ? "Resume ticket" : "Backlog ticket") {
            Haptics.medium()
            Task {
                await perform {
                    try await store.client.setTicketBacklogged(detail, backlogged: !detail.isBacklogged)
                    return nil
                }
            }
        }.frame(minHeight: 44).accessibilityIdentifier("ticket-backlog")
    }

    private func stageCard(_ presentation: TicketPresentation) -> some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
            Text(presentation.heading).font(PanelsTheme.TypeScale.heading).foregroundStyle(PanelsTheme.strong)
                .accessibilityAddTraits(.isHeader)
            if let proposal = detail?.pendingProposal, proposal.field == presentation.currentField {
                Text(detail?.awaitingApproval == true ? "Awaiting your approval" : "Awaiting an agent’s approval")
                    .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.accent)
                TicketExpandableText(text: proposal.body, emptyText: "")
            }
            if !presentation.terminal && presentation.ticket.stage != "needs_brief" {
                Button {
                    Haptics.selection(); scopeOpen = true
                } label: {
                    HStack(alignment: .firstTextBaseline, spacing: PanelsTheme.Space.sm) {
                        Text("Approved until **\(presentation.ceilingLabel)**, then **\(presentation.holderLabel(presentation.ticket.ceilingHolder))** reviews")
                            .multilineTextAlignment(.leading)
                        Image(systemName: "chevron.down").font(PanelsTheme.TypeScale.caption)
                    }.frame(minHeight: 44, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
                }.buttonStyle(.plain).disabled(busy).accessibilityIdentifier("ticket-approval-limit")
            }
        }
        .padding(PanelsTheme.Space.xl)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
    }

    private func bands(_ presentation: TicketPresentation) -> some View {
        VStack(spacing: 0) {
            Divider().overlay(PanelsTheme.border)
            disclosure("Steps", count: "\(presentation.completedCount) of \(presentation.manifest.fields.count) done", open: $stepsOpen)
            if stepsOpen {
                VStack(spacing: 0) {
                    ForEach(presentation.manifest.fields) { field in
                        TicketStepRow(field: field, presentation: presentation)
                    }
                }.padding(.bottom, PanelsTheme.Space.md)
            }
            Divider().overlay(PanelsTheme.border)
            disclosure("Artifacts", count: artifacts.map { String($0.total) } ?? "…", open: $artifactsOpen)
            if artifactsOpen {
                artifactRows.padding(.bottom, PanelsTheme.Space.md)
            }
            Divider().overlay(PanelsTheme.border)
        }
        .onChange(of: artifactsOpen) { _, open in if open { Task { await loadArtifacts() } } }
    }

    private func disclosure(_ title: String, count: String, open: Binding<Bool>) -> some View {
        Button {
            Haptics.selection()
            withAnimation(reduceMotion ? nil : .easeInOut(duration: PanelsTheme.Motion.base)) { open.wrappedValue.toggle() }
        } label: {
            HStack {
                Text(title).fontWeight(.semibold)
                Spacer()
                Text(count).foregroundStyle(PanelsTheme.faint)
                Image(systemName: "chevron.right").font(PanelsTheme.TypeScale.caption)
                    .rotationEffect(.degrees(open.wrappedValue ? 90 : 0))
            }.font(PanelsTheme.TypeScale.small).frame(minHeight: 52).contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityValue(open.wrappedValue ? "Expanded" : "Collapsed")
            .accessibilityIdentifier("ticket-\(title.lowercased())")
    }

    @ViewBuilder private var artifactRows: some View {
        if let artifactError { LoadErrorView(message: artifactError) { await loadArtifacts() } }
        if let artifacts {
            if artifacts.entries.isEmpty {
                Text("No artifacts yet.").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, PanelsTheme.Space.sm)
            }
            ForEach(artifacts.entries) { entry in
                NavigationLink(value: TicketArtifactRoute.destination(entry, owner: owner)) {
                    HStack(spacing: PanelsTheme.Space.md) {
                        Image(systemName: entry.directory == nil ? "doc.text" : "folder").foregroundStyle(PanelsTheme.faint)
                        Text(entry.label).lineLimit(2)
                        Spacer(minLength: 0)
                        Image(systemName: "chevron.right").font(PanelsTheme.TypeScale.caption).foregroundStyle(PanelsTheme.faint)
                    }.font(PanelsTheme.TypeScale.small).frame(minHeight: 48).contentShape(Rectangle())
                }.buttonStyle(.plain)
            }
            if artifacts.total > artifacts.entries.count {
                NavigationLink("View all \(artifacts.total) artifacts", value: AppRoute.artifacts(owner: owner, directory: ""))
                    .font(PanelsTheme.TypeScale.small).frame(minHeight: 44)
            }
        } else if artifactError == nil { ProgressView().padding(PanelsTheme.Space.md) }
    }

    private func load() async {
        loadGeneration += 1
        let generation = loadGeneration
        let client = store.client
        do {
            let result = try await client.ticket(ticketID)
            let type = try await client.ticketWorkerType(result.workerType)
            try Task.checkCancellation()
            guard generation == loadGeneration, client.baseURL == store.baseURL else { return }
            detail = result; manifest = type; error = nil
        } catch {
            if !Task.isCancelled && generation == loadGeneration { self.error = error.localizedDescription }
        }
    }

    private func loadArtifacts() async {
        let client = store.client
        do {
            let result = try await client.artifacts(owner: owner)
            try Task.checkCancellation()
            guard client.baseURL == store.baseURL else { return }
            artifacts = result; artifactError = nil
        } catch { if !Task.isCancelled { artifactError = error.localizedDescription } }
    }

    private func perform(_ action: () async throws -> String?) async {
        guard !busy else { return }
        busy = true; actionNote = nil
        defer { busy = false }
        do {
            actionNote = try await action()
            if actionNote == nil { Haptics.success() }
            await load()
            await store.refresh()
        } catch { actionNote = error.localizedDescription }
    }
}

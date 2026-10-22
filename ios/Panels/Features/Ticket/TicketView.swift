import SwiftUI
import UIKit

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
    @State private var scopeOpen = false
    @State private var recapEditorOpen = false
    @State private var configurationOpen = false
    @State private var renaming = false
    @State private var titleDraft = ""
    @State private var completionDraft = ""
    @State private var titleVisible = true
    @State private var loadGeneration = 0
    @State private var linkedArtifact: AppRoute?
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
                        steps(presentation)
                        artifactRow
                    }
                } else if error == nil {
                    ProgressView("Loading ticket…").frame(maxWidth: .infinity).padding(.top, PanelsTheme.Space.xxl)
                }
            }.frame(maxWidth: 720, alignment: .leading)
                .padding(.horizontal, PanelsTheme.Space.xl).padding(.vertical, PanelsTheme.Space.lg)
                .frame(maxWidth: .infinity)
        }
        .navigationTitle(titleVisible ? "" : detail?.title ?? "Ticket")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .tabBar)
        .toolbar { ToolbarItem(placement: .primaryAction) { ticketMenu } }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            ConversationPlaceholder().padding(.horizontal, PanelsTheme.Space.lg).padding(.vertical, PanelsTheme.Space.sm)
                .background(PanelsTheme.surface)
        }
        .panelsScreen()
        .refreshable { await load(); await loadArtifacts() }
        .task(id: "\(store.baseURL.absoluteString):\(store.revision)") { await load() }
        .task(id: store.baseURL.absoluteString) { await loadArtifacts() }
        .task(id: detail?.stage) {
            if let presentation, let field = presentation.currentField { completionDraft = detail?.fieldValues[field] ?? "" }
        }
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
            if let route = TicketArtifactRoute.linkedDestination(url, title: url.lastPathComponent) { linkedArtifact = route; return .handled }
            return .systemAction
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
            HStack(spacing: PanelsTheme.Space.sm) {
                Text("\(detail.project ?? "No project")  ·  \(manifest?.label ?? detail.workerType.replacingOccurrences(of: "_", with: " ").capitalized)")
                    .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                Spacer(minLength: PanelsTheme.Space.sm)
                if detail.conversationSignal?.messageForOwner == true {
                    Circle().fill(PanelsTheme.accent).frame(width: 8, height: 8).accessibilityLabel("Unread message")
                }
                if detail.conversationSignal?.working == true {
                    ProgressView().controlSize(.mini).tint(PanelsTheme.faint).frame(width: 12, height: 12).accessibilityLabel("Working")
                }
            }
            if renaming {
                TextField("Title", text: $titleDraft, axis: .vertical).font(.title.weight(.semibold))
                Button("Save title") {
                    Task { await perform { try await store.client.renameTicket(ticketID, title: titleDraft); renaming = false; return nil } }
                }.disabled(busy || titleDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Button("Cancel") { renaming = false }
            } else {
                Text(detail.title).font(.title.weight(.semibold)).foregroundStyle(PanelsTheme.strong)
                    .fixedSize(horizontal: false, vertical: true).accessibilityAddTraits(.isHeader)
                    .onScrollVisibilityChange(threshold: 0.1) { visible in
                        withAnimation(reduceMotion ? nil : .easeOut(duration: PanelsTheme.Motion.base)) { titleVisible = visible }
                    }
                    .contextMenu { Button("Rename", systemImage: "pencil") { titleDraft = detail.title; renaming = true } }
            }
            Group {
                if detail.recap.isEmpty {
                    Button("+ add orientation") { recapEditorOpen = true }.foregroundStyle(PanelsTheme.faint)
                } else { TicketExpandableText(text: detail.recap, emptyText: "") }
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
                        NavigationLink(value: AppRoute.ticket(blocker.ticketId)) { Text(blocker.title) }
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
                    Button("Resume") { changeBacklog(detail) }.fontWeight(.semibold).disabled(busy)
                }.font(PanelsTheme.TypeScale.small)
            }
            if let actionNote {
                Text(actionNote).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error)
                    .accessibilityIdentifier("ticket-action-note")
            }
        }
    }

    private func stageCard(_ presentation: TicketPresentation) -> some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
            if let proposal = detail?.pendingProposal, proposal.field == presentation.currentField {
                TicketProposalCard(presentation: presentation, proposal: proposal) { await load(); await store.refresh() }
                    .id("\(proposal.field):\(proposal.revision)")
            } else {
                Text(presentation.heading).font(PanelsTheme.TypeScale.heading)
                    .foregroundStyle(presentation.ticket.agentState == "errored" ? PanelsTheme.error : PanelsTheme.strong)
                    .accessibilityAddTraits(.isHeader)
                if presentation.ticket.assigned == true || presentation.currentStage?.ownershipMode == "user", let field = presentation.currentField {
                    TextEditor(text: $completionDraft).frame(minHeight: 120).scrollContentBackground(.hidden)
                        .padding(PanelsTheme.Space.sm).background(PanelsTheme.recessed, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
                        .accessibilityLabel("Value")
                    Button("Complete") {
                        Haptics.light()
                        Task { await perform { try await store.client.completeTicketField(ticketID, field: field, body: completionDraft); return nil } }
                    }.buttonStyle(.borderedProminent).buttonBorderShape(.capsule).disabled(busy || completionDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                } else if !presentation.terminal && presentation.ticket.stage != "needs_brief" {
                    Button { scopeOpen = true } label: {
                        HStack(alignment: .firstTextBaseline, spacing: PanelsTheme.Space.sm) {
                            Text("Approved until **\(presentation.ceilingLabel)**, then **\(presentation.holderLabel(presentation.ticket.ceilingHolder))** reviews")
                                .multilineTextAlignment(.leading)
                            Image(systemName: "chevron.down").font(PanelsTheme.TypeScale.caption)
                        }.font(PanelsTheme.TypeScale.small).frame(minHeight: 44, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
                    }.buttonStyle(.plain).disabled(busy).accessibilityIdentifier("ticket-approval-limit")
                }
            }
            if presentation.ticket.employeeConfigurationEditable {
                Button { configurationOpen = true } label: {
                    Text([presentation.ticket.employeeBackend.capitalized, presentation.ticket.employeeLaunchModel, presentation.ticket.employeeLaunchReasoningEffort?.capitalized].compactMap { $0 }.joined(separator: " · "))
                        .font(PanelsTheme.TypeScale.small).frame(minHeight: 44)
                }.buttonStyle(.plain)
            }
        }.padding(PanelsTheme.Space.xl).frame(maxWidth: .infinity, alignment: .leading)
            .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
    }

    private func steps(_ presentation: TicketPresentation) -> some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
            HStack {
                Text("STEPS").fontWeight(.semibold).tracking(1)
                Spacer()
                Text("\(presentation.completedCount) of \(presentation.manifest.fields.count) done")
            }.font(PanelsTheme.TypeScale.caption).foregroundStyle(PanelsTheme.faint)
            ForEach(presentation.manifest.fields) { field in TicketStepRow(field: field, presentation: presentation) }
        }.accessibilityIdentifier("ticket-steps")
    }

    @ViewBuilder private var artifactRow: some View {
        if let artifactError { LoadErrorView(message: artifactError) { await loadArtifacts() } }
        if let artifacts, artifacts.total > 0 {
            VStack(spacing: 0) {
                Divider().overlay(PanelsTheme.border)
                NavigationLink(value: AppRoute.artifacts(owner: owner, directory: "")) {
                    HStack { Text("Artifacts"); Spacer(); Text(String(artifacts.total)).foregroundStyle(PanelsTheme.faint) }
                        .font(PanelsTheme.TypeScale.body).frame(minHeight: 50).contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityIdentifier("ticket-artifacts")
            }
        }
    }

    private func requestRecap() { Task { await perform { try await store.client.requestTicketRecap(ticketID).note } } }
    private func changeBacklog(_ ticket: TicketDetail) {
        Task { await perform { try await store.client.setTicketBacklogged(ticket, backlogged: !ticket.isBacklogged); return nil } }
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
        } catch { if !Task.isCancelled && generation == loadGeneration { self.error = error.localizedDescription } }
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
            if actionNote == nil { Haptics.success() } else { Haptics.error() }
            await load(); await store.refresh()
        } catch { actionNote = error.localizedDescription; Haptics.error() }
    }
}

import SwiftUI

struct FeedbackView: View {
    @Environment(AppStore.self) private var store
    @State private var model = FeedbackModel()
    @State private var handled = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                Picker("Feedback view", selection: $handled) {
                    Text(tabTitle("Open", count: model.response?.openCount)).tag(false)
                    Text(tabTitle("Handled", count: model.response?.handledCount)).tag(true)
                }
                .pickerStyle(.segmented)
                .onChange(of: handled) { _, _ in Haptics.selection() }

                if let error = model.error {
                    VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                        Text(error).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error)
                        Button("Retry") { Haptics.light(); Task { await model.load(using: store.client) } }
                    }
                }
                if let response = model.response {
                    if handled { handledNotes(response) } else { openNotes(response) }
                } else if model.loading {
                    ProgressView("Loading feedback…").frame(maxWidth: .infinity).padding(.top, PanelsTheme.Space.hero)
                }
            }
            .padding(PanelsTheme.Space.lg)
        }
        .panelsScreen()
        .navigationTitle("Feedback")
        .navigationBarTitleDisplayMode(.large)
        .refreshable { Haptics.light(); await model.load(using: store.client) }
        .task(id: store.revision) { await model.load(using: store.client) }
    }

    private func tabTitle(_ title: String, count: Int?) -> String {
        count.map { "\(title) \($0)" } ?? title
    }

    @ViewBuilder private func openNotes(_ response: FeedbackResponse) -> some View {
        if response.open.isEmpty {
            emptyState("Nothing waiting.")
        } else {
            Text("The feedback agent reads these, groups them into tickets and does the work.")
                .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
            LazyVStack(spacing: 0) {
                ForEach(response.open) { note in noteRow(note, action: .dismiss) }
            }
        }
    }

    @ViewBuilder private func handledNotes(_ response: FeedbackResponse) -> some View {
        if response.handledGroups.isEmpty {
            emptyState("Nothing handled yet.")
        } else {
            LazyVStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                ForEach(Array(response.orderedHandledGroups.enumerated()), id: \.offset) { _, group in
                    VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                        if let ticket = group.ticket {
                            NavigationLink(value: AppRoute.ticket(ticket.id)) {
                                VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                                    HStack(alignment: .firstTextBaseline, spacing: PanelsTheme.Space.sm) {
                                        if ticket.conversationSignal?.messageForOwner == true {
                                            Circle().fill(PanelsTheme.accent).frame(width: 6, height: 6)
                                                .accessibilityLabel("Unread message")
                                        }
                                        Text(ticket.title).font(PanelsTheme.TypeScale.heading)
                                        Spacer(minLength: 0)
                                        Image(systemName: "chevron.right").font(PanelsTheme.TypeScale.small)
                                    }
                                    Text("\(ticket.stateLabel) · \(group.notes.count) \(group.notes.count == 1 ? "note" : "notes")")
                                        .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                                }
                            }.buttonStyle(.plain)
                        } else {
                            Text("Dismissed").font(PanelsTheme.TypeScale.heading)
                        }
                        ForEach(group.notes) { note in noteRow(note, action: .reopen) }
                    }
                }
            }
        }
    }

    private func emptyState(_ title: String) -> some View {
        Text(title).font(PanelsTheme.TypeScale.body).foregroundStyle(PanelsTheme.muted)
            .frame(maxWidth: .infinity, alignment: .leading).padding(.top, PanelsTheme.Space.lg)
    }

    private func noteRow(_ note: FeedbackNote, action: FeedbackAction) -> some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
            Text(note.text).font(PanelsTheme.TypeScale.body).textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .center, spacing: PanelsTheme.Space.md) {
                    noteMeta(note)
                    Spacer(minLength: PanelsTheme.Space.sm)
                    actionButton(note, action: action)
                }
                VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                    noteMeta(note)
                    actionButton(note, action: action)
                }
            }
            Rectangle().fill(PanelsTheme.border).frame(height: 1)
        }
        .padding(.vertical, PanelsTheme.Space.md)
    }

    private func noteMeta(_ note: FeedbackNote) -> some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
            if let label = note.pageLabel, !label.isEmpty {
                if let destination = FeedbackPageDestination.parse(note.pageAddress) {
                    NavigationLink { pageDestination(destination) } label: { Text(label) }
                        .simultaneousGesture(TapGesture().onEnded { Haptics.light() })
                } else { Text(label) }
            }
            Text(Date(timeIntervalSince1970: note.createdAt), style: .relative)
                .foregroundStyle(PanelsTheme.faint)
                .accessibilityLabel(Date(timeIntervalSince1970: note.createdAt).formatted(date: .abbreviated, time: .shortened))
        }.font(PanelsTheme.TypeScale.small)
    }

    private func actionButton(_ note: FeedbackNote, action: FeedbackAction) -> some View {
        Button {
            Haptics.medium()
            Task {
                if await model.update(note, action: action, using: store.client) { Haptics.success() }
            }
        } label: {
            HStack(spacing: PanelsTheme.Space.sm) {
                if model.pendingNoteID == note.id { ProgressView().controlSize(.small) }
                Text(action == .dismiss ? "Dismiss" : "Reopen")
            }.frame(minHeight: 32)
        }
        .buttonStyle(.bordered).buttonBorderShape(.capsule)
        .disabled(model.pendingNoteID != nil)
        .accessibilityLabel("\(action == .dismiss ? "Dismiss" : "Reopen") feedback: \(note.text)")
    }

    @ViewBuilder private func pageDestination(_ destination: FeedbackPageDestination) -> some View {
        switch destination {
        case .workspace: WorkspaceView()
        case .review: ReviewView()
        case .route(let route): RouteDestination(route: route)
        }
    }
}

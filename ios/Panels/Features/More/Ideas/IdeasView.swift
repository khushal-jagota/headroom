import SwiftUI

struct IdeasView: View {
    @Environment(AppStore.self) private var store
    @State private var model = IdeasModel()
    @FocusState private var titleFocused: Bool

    var body: some View {
        List {
            Section {
                captureForm
                    .listRowInsets(EdgeInsets(top: PanelsTheme.Space.md, leading: 0, bottom: PanelsTheme.Space.xl, trailing: 0))
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
            }
            Section("Captured") {
                if let error = model.readError {
                    VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                        Text(error).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error)
                        Button("Try again") { Task { await model.refresh(using: store.client) } }
                    }
                }
                if !model.hasLoaded && model.readError == nil {
                    RingSpinner("Loading ideas…")
                } else if model.hasLoaded && model.ideas.isEmpty {
                    Text("No ideas yet.").foregroundStyle(PanelsTheme.muted)
                }
                ForEach(model.ideas) { idea in IdeaRow(idea: idea) }
            }
            .listRowBackground(Color.clear)
        }
        .listStyle(.plain)
        .contentMargins(.horizontal, PanelsTheme.Space.xl)
        .scrollDismissesKeyboard(.interactively)
        .panelsNavigationTitle("Ideas")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                if model.hasLoaded {
                    Text(model.ideas.count, format: .number)
                        .font(PanelsTheme.TypeScale.small.monospacedDigit())
                        .padding(.horizontal, PanelsTheme.Space.md).padding(.vertical, PanelsTheme.Space.xs)
                        .background(PanelsTheme.raised, in: Capsule())
                        .accessibilityLabel("\(model.ideas.count) ideas")
                }
            }
        }
        .panelsScreen()
        .task(id: "\(store.baseURL.absoluteString):\(store.revision)") { await model.refresh(using: store.client) }
        .panelsRefreshable { await model.refresh(using: store.client) }
    }

    private var captureForm: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
            TextField("Capture an idea…", text: $model.title)
                .font(PanelsTheme.TypeScale.captureTitle)
                .focused($titleFocused)
                .submitLabel(.send)
                .onSubmit(capture)
                .accessibilityIdentifier("idea-title")
            TextField("Add detail — optional", text: $model.detail, axis: .vertical)
                .lineLimit(2...5)
                .font(PanelsTheme.TypeScale.body)
                .accessibilityIdentifier("idea-detail")
            ScrollView(.horizontal) {
                HStack(spacing: PanelsTheme.Space.sm) {
                    projectChip("None", id: nil)
                    ForEach(model.projects) { project in projectChip(project.name, id: project.id) }
                }
            }
            .scrollIndicators(.hidden)
            .accessibilityLabel("Project for new idea")
            HStack {
                Label("to capture", systemImage: "return")
                    .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                Spacer()
                Button(action: capture) {
                    HStack(spacing: PanelsTheme.Space.sm) {
                        if model.isCapturing { RingSpinner(size: .small) }
                        Text(model.isCapturing ? "Capturing…" : "Capture")
                    }
                    .font(PanelsTheme.TypeScale.primaryAction)
                    .padding(.horizontal, PanelsTheme.Space.lg).padding(.vertical, PanelsTheme.Space.md)
                }
                .buttonStyle(.borderedProminent)
                .disabled(!model.canCapture)
                .accessibilityIdentifier("idea-capture")
            }
            if let error = model.captureError {
                Text(error).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error)
            }
        }
        .disabled(model.isCapturing)
    }

    private func projectChip(_ name: String, id: String?) -> some View {
        Button {
            if model.projectID != id { Haptics.selection() }
            model.projectID = id
        } label: {
            Text(name)
                .font(PanelsTheme.TypeScale.small)
                .padding(.horizontal, PanelsTheme.Space.md)
                .frame(minHeight: 44)
                .foregroundStyle(model.projectID == id ? PanelsTheme.accentText : PanelsTheme.muted)
                .background(model.projectID == id ? PanelsTheme.accentSurface : PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(model.projectID == id ? .isSelected : [])
        .accessibilityLabel("Project: \(name)")
    }

    private func capture() {
        guard model.canCapture else { return }
        Haptics.light()
        Task {
            if await model.capture(using: store.client) {
                Haptics.success()
                titleFocused = false
            } else if model.captureError != nil { Haptics.error() }
        }
    }
}

private struct IdeaRow: View {
    let idea: Idea
    @State private var expanded = false

    var body: some View {
        Group {
            if idea.body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                heading
            } else {
                DisclosureGroup(isExpanded: $expanded) {
                    Text(.init(idea.body))
                        .font(PanelsTheme.TypeScale.body)
                        .foregroundStyle(PanelsTheme.muted)
                        .textSelection(.enabled)
                        .padding(.vertical, PanelsTheme.Space.md)
                } label: { heading }
                .clipped()
            }
        }
        .padding(.vertical, PanelsTheme.Space.sm)
        .listRowInsets(EdgeInsets(top: PanelsTheme.Space.sm, leading: 0, bottom: PanelsTheme.Space.sm, trailing: 0))
        .listRowSeparatorTint(PanelsTheme.border)
    }

    private var heading: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
            Text(idea.title).font(PanelsTheme.TypeScale.itemTitle).foregroundStyle(PanelsTheme.text)
                .fixedSize(horizontal: false, vertical: true)
            if let project = idea.project {
                Text(project).font(PanelsTheme.TypeScale.caption)
                    .foregroundStyle(PanelsTheme.muted)
                    .padding(.horizontal, PanelsTheme.Space.sm).padding(.vertical, PanelsTheme.Space.xs)
                    .overlay(Capsule().strokeBorder(PanelsTheme.border))
            }
        }
    }
}

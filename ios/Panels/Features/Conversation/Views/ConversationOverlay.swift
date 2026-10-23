import SwiftUI

private enum ConversationPresentation { case rest, peeked, opened }
private enum ConversationLayout {
    static let gutter: CGFloat = 20
    static let edgeGap: CGFloat = 12
    static let restingClearance: CGFloat = 170
    static let peekFraction = 0.60
}

struct ConversationOverlay: ViewModifier {
    let owner: ConversationOwner
    let conversationID: String?
    var preview = false
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var model = ConversationModel()
    @State private var speech = ConversationSpeech()
    @State private var presentation: ConversationPresentation = .rest
    @State private var keyboard = false
    @State private var latestRequest = 0
    @State private var composerHeight: CGFloat = 64
    @State private var retainedClearance = ConversationLayout.restingClearance
    @FocusState private var focused: Bool

    func body(content: Content) -> some View {
        content
            .contentMargins(.bottom, retainedClearance, for: .scrollContent)
            .overlay {
                GeometryReader { geometry in
                    let available = max(0, geometry.size.height - ConversationLayout.edgeGap * 2)
                    let expandedHeight = presentation == .opened ? available : min(available, available * ConversationLayout.peekFraction + max(0, composerHeight - 64))
                    VStack(spacing: 0) {
                        Spacer(minLength: 0)
                        card
                            .frame(height: presentation == .rest ? nil : expandedHeight)
                            .background(PanelsTheme.surface, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
                            .overlay(RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg).stroke(PanelsTheme.border))
                            .shadow(color: .black.opacity(0.45), radius: 12, y: -4)
                            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height in
                                if presentation == .rest { retainedClearance = height + ConversationLayout.edgeGap * 2 }
                            }
                    }
                    .padding(.horizontal, ConversationLayout.gutter)
                    .padding(.vertical, ConversationLayout.edgeGap)
                    .animation(reduceMotion ? nil : .easeOut(duration: PanelsTheme.Motion.base), value: presentation)
                }
            }
            .task(id: "\(store.baseURL)|\(conversationID ?? "new")|\(scenePhase == .active)") {
                guard !preview else {
                    model.status = try? APIClient.decoder().decode(ConversationStatus.self, from: Data(#"{"authority":"current","turn_running":true,"rest":{"latest_happened":null,"latest_tool":{"kind":"tool_call","title":"Reading the implementation"},"permission_title_excerpt":null,"plan_progress":{"current_text_excerpt":null}}}"#.utf8))
                    model.items = (try? APIClient.decoder().decode(ConversationPage.self, from: Data(#"{"items":[{"kind":"row","key":"preview-reply","body":{"kind":"inline","value":{"kind":"agent_message","content":[{"piece":"text","text":"The card stays in the content column. The page remains visible behind it, and your draft has room to grow."}]}}}],"older":null}"#.utf8)))?.items ?? []
                    model.draft = "Could you walk me through the next step?"
                    model.outgoing = [.init(id: "preview-owner", text: "Keep the conversation over the page, with room around it.", note: nil)]
                    presentation = .peeked
                    return
                }
                guard scenePhase == .active else { return }
                model.conversationID = conversationID ?? model.conversationID
                if owner == .chief, model.conversationID == nil {
                    model.conversationID = try? await store.client.chiefConversation().conversationId
                }
                while !Task.isCancelled {
                    await model.refresh(using: store.client, transcript: presentation != .rest)
                    do { try await Task.sleep(for: .seconds(2)) } catch { return }
                }
            }
            .onChange(of: presentation) { _, value in
                if value != .rest && !preview { Task { await model.refresh(using: store.client, transcript: true) } }
            }
            .onChange(of: model.lens) { _, _ in guard !preview else { return }; Task { await model.refresh(using: store.client, transcript: true) } }
            .onChange(of: scenePhase) { _, phase in if phase != .active { speech.stop() } }
            .onDisappear { speech.stop() }
            .onChange(of: store.baseURL) { _, _ in speech.stop(); model = ConversationModel() }
    }

    private var card: some View {
        VStack(spacing: 0) {
            if presentation != .rest {
                HStack {
                    Picker("Conversation lens", selection: $model.lens) {
                        ForEach(ConversationLens.allCases, id: \.self) { Text($0.rawValue.capitalized).tag($0) }
                    }.pickerStyle(.segmented).frame(maxWidth: 180)
                    Spacer()
                    Button {
                        Task {
                            await model.refresh(using: store.client, transcript: true)
                            if model.error == nil { latestRequest += 1 }
                        }
                    } label: {
                        Image(systemName: "arrow.down").frame(width: 44, height: 44)
                    }.accessibilityLabel("Latest messages")
                    Button { Haptics.selection(); presentation = presentation == .opened ? .peeked : .opened } label: {
                        Image(systemName: presentation == .opened ? "arrow.down.right.and.arrow.up.left" : "arrow.up.left.and.arrow.down.right").frame(width: 44, height: 44)
                    }.accessibilityLabel(presentation == .opened ? "Peek conversation" : "Expand conversation")
                    Button { focused = false; presentation = .rest } label: { Image(systemName: "chevron.down").frame(width: 44, height: 44) }.accessibilityLabel("Collapse conversation")
                }.padding(.horizontal, PanelsTheme.Space.md)
                thread
            }
            Button {
                Haptics.selection()
                if presentation == .rest { presentation = .peeked }
            } label: {
                HStack(spacing: PanelsTheme.Space.sm) {
                    if model.status?.turnRunning == true { ProgressView().controlSize(.mini) }
                    Text(model.status?.line ?? "Conversation").lineLimit(1).font(PanelsTheme.TypeScale.small)
                    Spacer(minLength: 0)
                    if presentation == .rest { Image(systemName: "chevron.up").font(.caption) }
                }.foregroundStyle(PanelsTheme.faint).frame(minHeight: 44).contentShape(Rectangle())
            }.buttonStyle(.plain).padding(.horizontal, PanelsTheme.Space.lg)
            if let error = model.error ?? speech.error {
                Text(error).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error).padding(.horizontal, PanelsTheme.Space.lg)
            }
            composer.disabled(preview)
        }.foregroundStyle(PanelsTheme.text)
    }

    private var thread: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
                    if model.older != nil {
                        Button("Earlier messages") { Task { await model.loadOlder(using: store.client) } }
                            .disabled(model.loadingOlder).frame(maxWidth: .infinity, minHeight: 44)
                    }
                    ForEach(model.items) { item in
                        if let body = model.expandedBodies[item.key] ?? item.body?.value, !body.text.isEmpty {
                            message(body.text, own: body.isPrompt, note: note(for: body)).id(item.id)
                        } else if item.body?.reference != nil {
                            Button("Read message") { Task { await model.loadBody(item, using: store.client) } }.frame(minHeight: 44)
                        }
                    }
                    ForEach(model.outgoing) { item in message(item.text, own: true, note: item.note).id(item.id) }
                    Color.clear.frame(height: 1).id("latest")
                }.padding(.horizontal, PanelsTheme.Space.lg).padding(.vertical, ConversationLayout.edgeGap)
            }
            .defaultScrollAnchor(.bottom)
            .onChange(of: latestRequest) { _, _ in
                withAnimation(reduceMotion ? nil : .linear(duration: PanelsTheme.Motion.slow)) { proxy.scrollTo("latest", anchor: .bottom) }
            }
            .onChange(of: model.outgoing.count) { old, new in
                if new > old, let item = model.outgoing.last {
                    withAnimation(reduceMotion ? nil : .linear(duration: PanelsTheme.Motion.slow)) { proxy.scrollTo(item.id, anchor: .top) }
                }
            }
        }
    }
    private func note(for body: ConversationBody) -> String? {
        switch body.kind {
        case "prompt_refused": return "Not delivered · \(body.refusalDetail ?? "refused")"
        case "prompt_uncertain": return "Delivery uncertain · do not resend"
        case "permission_ask", "user_input": return "Respond on the web"
        default: return nil
        }
    }
    private func message(_ text: String, own: Bool, note: String?) -> some View {
        HStack {
            if own { Spacer(minLength: 28) }
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                if let note { Text(note).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint) }
                Text(.init(text)).font(PanelsTheme.TypeScale.body).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }.padding(own ? PanelsTheme.Space.md : 0)
                .background(own ? PanelsTheme.raised : .clear, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
            if !own { Spacer(minLength: 0) }
        }
    }
    private var composer: some View {
        VStack(spacing: PanelsTheme.Space.sm) {
            if keyboard || !model.draft.isEmpty {
                TextField("Message", text: $model.draft, axis: .vertical)
                    .lineLimit(1...6).focused($focused).textFieldStyle(.plain)
                    .padding(.horizontal, PanelsTheme.Space.lg).padding(.top, PanelsTheme.Space.md)
            }
            HStack {
                Button {
                    if speech.recording { speech.stop() }
                    else {
                        focused = false
                        let prefix = model.draft
                        Task { await speech.start { text in model.draft = prefix + (prefix.isEmpty ? "" : " ") + text } }
                    }
                } label: { Label(speech.recording ? "Stop recording" : "Tap to speak", systemImage: speech.recording ? "stop.circle.fill" : "mic").frame(minHeight: 44) }
                Spacer(minLength: 4)
                Button { speech.stop(); keyboard = true; focused = true; Haptics.selection() } label: { Image(systemName: "keyboard").frame(width: 44, height: 44) }.accessibilityLabel("Use keyboard")
                if !model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    Button {
                        speech.stop(); presentation = .peeked
                        Task { if await model.send(to: owner, using: store.client) { Haptics.success() } }
                    } label: { Image(systemName: "arrow.up.circle.fill").font(.title2).frame(width: 44, height: 44) }
                        .disabled(model.sending).accessibilityLabel("Send message")
                }
            }.padding(.horizontal, PanelsTheme.Space.lg)
        }
        .padding(.bottom, PanelsTheme.Space.sm)
        .fixedSize(horizontal: false, vertical: true)
        .background(PanelsTheme.recessed)
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { composerHeight = $0 }
    }
}

extension View {
    func conversationOverlay(owner: ConversationOwner, conversationID: String?) -> some View {
        modifier(ConversationOverlay(owner: owner, conversationID: conversationID))
    }
}

#if DEBUG
/// Deterministic, read-only smoke surface. It never creates an AppShell or network poll.
struct ConversationSmokeView: View {
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                    Text("Chief of Staff").font(.title.weight(.semibold))
                    Text("The conversation stays above the page.").foregroundStyle(PanelsTheme.faint)
                    ForEach(0..<12) { index in
                        Text("Worker \(index + 1)").frame(maxWidth: .infinity, minHeight: 50, alignment: .leading)
                        Divider()
                    }
                }.padding(.horizontal, 20)
            }.panelsScreen()
                .modifier(ConversationOverlay(owner: .chief, conversationID: nil, preview: true))
        }
    }
}
#endif

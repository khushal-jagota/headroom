import AVFoundation
import Foundation
import Observation
import SwiftUI

@MainActor @Observable final class ReviewVoiceCapture {
    /// `saved`: the recording is on disk and waits for its words.
    enum Phase { case idle, recording, saved, failed }
    var phase = Phase.idle
    /// A saved recording is transcribing only while the outbox's request for it is in flight.
    var transcribing: Bool { phase == .saved && work.map { Outbox.shared.transcriptionsInFlight.contains($0.id) } == true }
    var startedAt = Date()
    var error: String?
    private var recorder: AVAudioRecorder?
    /// The recording this app is making now. The card and an artifact above it each have a
    /// note on the same draft, so the other note must not take it for an interrupted one.
    private static var recordingID: String?
    private var work: PendingWork?
    private var api: APIClient?
    @ObservationIgnored private let awake: KeepAwake

    init(awake: KeepAwake = .shared) { self.awake = awake }

    func restore(api: APIClient, draftID: String?, savedWork: [PendingWork]) {
        guard recorder == nil, let draftID else { return }
        self.api = api
        work = savedWork.last { $0.kind == .voice && $0.draftID == draftID && $0.state != .sent }
        guard var saved = work else { phase = .idle; error = nil; return }
        if saved.state == .recording, saved.id != Self.recordingID {
            // An interrupted recording is kept and transcribed like any other.
            saved.state = .waiting
            do { try Outbox.shared.save(saved, server: LocalStore.server(api.baseURL)); work = saved }
            catch { self.error = error.localizedDescription }
            Task { await Outbox.shared.drain(api: api) }
        }
        // A saved recording waits for its words. Only a refusal offers Retry.
        phase = saved.state == .failed ? .failed : .saved
        error = saved.state == .failed ? saved.note : nil
    }
    func start(api: APIClient, draftID: String?) async {
        guard phase == .idle, let draftID else { return }
        guard await AVAudioApplication.requestRecordPermission() else {
            error = "Microphone access is needed to record a revision."; return
        }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .default); try session.setActive(true)
            let id = UUID().uuidString
            let file = "review-\(id).m4a"
            let url = LocalStore.directory.appendingPathComponent(file)
            try FileManager.default.createDirectory(at: LocalStore.directory, withIntermediateDirectories: true)
            var saved = PendingWork(id: id, created: .now, kind: .voice, audioFile: file, draftID: draftID)
            saved.state = .recording
            try Outbox.shared.save(saved, server: LocalStore.server(api.baseURL))
            self.work = saved; self.api = api
            let recorder = try AVAudioRecorder(url: url, settings: [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 44100, AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 64000])
            guard recorder.record() else { throw APIError(status: 0, message: "Recording could not start.") }
            self.recorder = recorder; Self.recordingID = id; startedAt = Date(); phase = .recording; error = nil
            awake.hold(self)
            Haptics.light()
        } catch { self.error = error.localizedDescription; phase = .failed }
    }
    func preserve() {
        awake.release(self)
        guard recorder != nil else { return }
        recorder?.stop(); recorder = nil; Self.recordingID = nil
        try? AVAudioSession.sharedInstance().setActive(false)
        guard var work, let api else { return }
        work.state = .waiting
        do { try Outbox.shared.save(work, server: LocalStore.server(api.baseURL)); self.work = work }
        catch { self.error = error.localizedDescription }
        phase = .saved
        Task { await Outbox.shared.drain(api: api) }
    }
    func stop(api: APIClient) async -> String? {
        preserve()
        return await transcribe(api: api)
    }
    func transcribe(api: APIClient) async -> String? {
        guard let work else { return nil }
        phase = .saved; error = nil
        if Outbox.shared.work(LocalStore.server(api.baseURL), id: work.id)?.state == .failed { await Outbox.shared.retry(work.id, api: api) }
        await Outbox.shared.drain(api: api)
        let current = Outbox.shared.work(LocalStore.server(api.baseURL), id: work.id)
        if current == nil || current?.state == .sent { phase = .idle; self.work = nil; return "" }
        // Still waiting: the outbox transcribes it when the server answers.
        if current?.state == .failed { phase = .failed; error = current?.note }
        return nil
    }
    /// Only an explicit discard removes audio. Leaving the composer always preserves it.
    func cancel() {
        awake.release(self)
        if recorder != nil { recorder?.stop(); recorder = nil; Self.recordingID = nil }
        try? AVAudioSession.sharedInstance().setActive(false)
        guard let work, let api else { phase = .idle; return }
        do {
            try LocalStore.shared.remove(server: LocalStore.server(api.baseURL), kind: "outbox", id: work.id)
            if let file = work.audioFile, (try? LocalStore.shared.outgoing.audioIsUnowned(file)) == true { try? FileManager.default.removeItem(at: LocalStore.directory.appendingPathComponent(file)) }
            self.work = nil; phase = .idle; error = nil
        } catch { self.error = error.localizedDescription }
    }
    static func append(_ transcript: String, to draft: String) -> String {
        guard !transcript.isEmpty else { return draft }
        let existing = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        return existing.isEmpty ? transcript : existing + "\n\n" + transcript
    }
}

struct DecisionVoiceParts {
    let accessory: AnyView
    let noteEnabled: Bool
    let error: String?
    let focusRequest: Int
    let blurRequest: Int
}

/// The note's voice recording, shared by the Review card and the Ticket page card. It owns the
/// recorder, its mic button and its recording bar, and hands the composer what it must show.
struct DecisionVoiceNote<Key: Equatable, Content: View>: View {
    let isActive: Bool
    let draftID: String?
    /// The mic can start: the decision is live and nothing else is happening to it.
    let canRecord: Bool
    /// Changes whenever saved work changes, so a finished transcript reaches the note.
    let savedKey: Key
    let savedVoice: () -> [PendingWork]
    /// Reads the saved work again before the recording is restored.
    let refreshSaved: () -> Void
    /// Reads the note's saved words again into the composer.
    let reloadDraft: () -> Void
    @ViewBuilder let content: (DecisionVoiceParts) -> Content
    @Environment(AppStore.self) private var store
    @State private var voice = ReviewVoiceCapture()
    @State private var focusRequest = 0
    @State private var blurRequest = 0
    @ScaledMetric(relativeTo: .body) private var microphoneSize: CGFloat = 17
    private var api: APIClient { store.client }

    var body: some View {
        VStack(spacing: 0) {
            if voice.phase != .idle { voiceStatus.padding(.horizontal, 20).padding(.top, 8).background(PanelsTheme.Web.surface) }
            content(DecisionVoiceParts(accessory: AnyView(micButton), noteEnabled: voice.phase == .idle, error: voice.error,
                          focusRequest: focusRequest, blurRequest: blurRequest))
        }
        .onChange(of: isActive, initial: true) { _, active in
            guard active else { voice.preserve(); return }
            refreshSaved(); reloadDraft(); restoreVoice()
        }
        .onChange(of: savedKey) { _, _ in
            guard isActive else { return }
            refreshSaved(); reloadDraft(); restoreVoice()
        }
        .onChange(of: draftID) { _, _ in if isActive { restoreVoice() } }
        .onDisappear { voice.preserve() }
    }

    private var micButton: some View {
        Button {
            Haptics.light(); blurRequest += 1
            Task { await voice.start(api: api, draftID: draftID) }
        } label: {
            Image(systemName: "mic").resizable().scaledToFit()
                .frame(width: min(microphoneSize, 24), height: min(microphoneSize, 24))
                .frame(width: 40, height: 40).contentShape(Rectangle())
        }
        .buttonStyle(.plain).foregroundStyle(PanelsTheme.Web.secondary)
        .accessibilityLabel("Record a note").accessibilityIdentifier("review.mic")
        .disabled(!canRecord || draftID == nil || voice.phase != .idle)
    }
    private func restoreVoice() { voice.restore(api: api, draftID: draftID, savedWork: savedVoice()) }
    private func append(_ transcript: String?) {
        if transcript != nil { reloadDraft() }
        focusRequest += 1
    }
    @ViewBuilder private var voiceStatus: some View {
        HStack(spacing: PanelsTheme.Space.md) {
            Button { Haptics.selection(); voice.cancel(); focusRequest += 1 } label: { Image(systemName: "xmark").frame(width: 44, height: 44) }.accessibilityLabel("Cancel recording")
            Spacer()
            switch voice.phase {
            case .recording:
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    let elapsed = max(0, Int(context.date.timeIntervalSince(voice.startedAt)))
                    Text("recording \(elapsed / 60):\(String(format: "%02d", elapsed % 60))").monospacedDigit()
                }
                Spacer()
                Button { Haptics.light(); Task { append(await voice.stop(api: api)) } } label: { Image(systemName: "stop.fill").frame(width: 44, height: 44) }.background(PanelsTheme.accent, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg)).foregroundStyle(PanelsTheme.ink).accessibilityLabel("Stop and transcribe")
            case .saved:
                Text(voice.transcribing ? "transcribing" : "saved"); Spacer()
                if voice.transcribing { RingSpinner() }
            case .failed:
                Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.red).accessibilityLabel("Not transcribed")
                Spacer()
                Button("Retry") { Task { append(await voice.transcribe(api: api)) } }
            case .idle: EmptyView()
            }
        }.foregroundStyle(PanelsTheme.muted)
    }
}

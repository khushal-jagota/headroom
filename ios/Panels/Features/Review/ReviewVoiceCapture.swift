import AVFoundation
import Foundation
import Observation

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

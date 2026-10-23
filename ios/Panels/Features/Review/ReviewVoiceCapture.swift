import AVFoundation
import Foundation
import Observation

@MainActor @Observable final class ReviewVoiceCapture {
    enum Phase { case idle, recording, transcribing, failed }
    var phase = Phase.idle
    var startedAt = Date()
    var error: String?
    private var recorder: AVAudioRecorder?
    private var work: PendingWork?
    private var api: APIClient?

    func restore(api: APIClient, draftID: String?) {
        guard recorder == nil, let draftID else { return }
        self.api = api
        work = Outbox.shared.entries(LocalStore.server(api.baseURL)).last { $0.kind == .voice && $0.draftID == draftID && $0.state != .sent }
        guard var saved = work else { phase = .idle; return }
        if saved.state == .recording {
            saved.state = .waiting; saved.note = "Recording recovered · waiting for connection"
            do { try Outbox.shared.save(saved, server: LocalStore.server(api.baseURL)); work = saved }
            catch { self.error = error.localizedDescription }
        }
        phase = .failed
        error = saved.note
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
            saved.state = .recording; saved.note = "Recording saved on this phone"
            try Outbox.shared.save(saved, server: LocalStore.server(api.baseURL))
            self.work = saved; self.api = api
            let recorder = try AVAudioRecorder(url: url, settings: [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 44100, AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 64000])
            guard recorder.record() else { throw APIError(status: 0, message: "Recording could not start.") }
            self.recorder = recorder; startedAt = Date(); phase = .recording; error = nil
            Haptics.light()
        } catch { self.error = error.localizedDescription; phase = .failed }
    }
    func preserve() {
        guard recorder != nil else { return }
        recorder?.stop(); recorder = nil
        try? AVAudioSession.sharedInstance().setActive(false)
        guard var work, let api else { return }
        work.state = .waiting; work.note = "Recording saved · waiting for connection"
        do { try Outbox.shared.save(work, server: LocalStore.server(api.baseURL)); self.work = work }
        catch { self.error = error.localizedDescription }
        phase = .failed
    }
    func stop(api: APIClient) async -> String? {
        preserve()
        return await transcribe(api: api)
    }
    func transcribe(api: APIClient) async -> String? {
        guard let work else { return nil }
        phase = .transcribing
        if work.state == .failed { Outbox.shared.retry(work.id, api: api) }
        await Outbox.shared.drain(api: api)
        let current = Outbox.shared.entries(LocalStore.server(api.baseURL)).first { $0.id == work.id }
        if current?.state == .sent { phase = .idle; self.work = nil; error = nil; return "" }
        phase = .failed; error = current?.note ?? "Recording saved on this phone"
        return nil
    }
    /// Only an explicit discard removes audio. Leaving the composer always preserves it.
    func cancel() {
        recorder?.stop(); recorder = nil
        try? AVAudioSession.sharedInstance().setActive(false)
        guard let work, let api else { phase = .idle; return }
        do {
            try LocalStore.shared.remove(server: LocalStore.server(api.baseURL), kind: "outbox", id: work.id)
            if let file = work.audioFile { try? FileManager.default.removeItem(at: LocalStore.directory.appendingPathComponent(file)) }
            self.work = nil; phase = .idle; error = nil
        } catch { self.error = error.localizedDescription }
    }
    static func append(_ transcript: String, to draft: String) -> String {
        guard !transcript.isEmpty else { return draft }
        let existing = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        return existing.isEmpty ? transcript : existing + "\n\n" + transcript
    }
}

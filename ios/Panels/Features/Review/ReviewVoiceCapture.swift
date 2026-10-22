import AVFoundation
import Foundation
import Observation

@MainActor @Observable final class ReviewVoiceCapture {
    enum Phase { case idle, recording, transcribing, failed }
    var phase = Phase.idle
    var startedAt = Date()
    var error: String?
    private var recorder: AVAudioRecorder?
    private var file: URL?
    private var audio: Data?
    private var generation = UUID()
    private var transcriptionTask: Task<String, Error>?

    func start() async {
        guard phase == .idle else { return }
        let request = UUID(); generation = request
        guard await AVAudioApplication.requestRecordPermission(), generation == request else {
            error = "Microphone access is needed to record a revision."; return
        }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .default)
            try session.setActive(true)
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("review-\(UUID()).m4a")
            file = url
            let recorder = try AVAudioRecorder(url: url, settings: [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 44100, AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 64000])
            guard recorder.record() else { throw APIError(status: 0, message: "Recording could not start.") }
            self.recorder = recorder; startedAt = Date(); phase = .recording; error = nil
            Haptics.light()
        } catch { self.error = error.localizedDescription; cancel() }
    }

    func stop(api: APIClient) async -> String? {
        guard phase == .recording else { return nil }
        recorder?.stop(); recorder = nil
        try? AVAudioSession.sharedInstance().setActive(false)
        Haptics.selection()
        do {
            guard let file else { return nil }
            audio = try Data(contentsOf: file)
            try? FileManager.default.removeItem(at: file); self.file = nil
            return await transcribe(api: api)
        } catch { self.error = error.localizedDescription; phase = .failed; return nil }
    }

    func transcribe(api: APIClient) async -> String? {
        guard let audio else { return nil }
        let request = generation
        phase = .transcribing; error = nil
        do {
            let task = Task { try await api.transcribeReviewAudio(audio) }
            transcriptionTask = task
            let text = try await task.value
            guard generation == request else { return nil }
            transcriptionTask = nil
            self.audio = nil; phase = .idle
            return text
        } catch {
            guard generation == request else { return nil }
            transcriptionTask = nil
            self.error = error.localizedDescription; phase = .failed; ReviewStyle.warning()
            return nil
        }
    }

    func cancel() {
        generation = UUID(); transcriptionTask?.cancel(); transcriptionTask = nil
        recorder?.stop(); recorder = nil
        if let file { try? FileManager.default.removeItem(at: file) }
        file = nil; audio = nil; phase = .idle
        try? AVAudioSession.sharedInstance().setActive(false)
    }

    static func append(_ transcript: String, to draft: String) -> String {
        guard !transcript.isEmpty else { return draft }
        let existing = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        return existing.isEmpty ? transcript : existing + "\n\n" + transcript
    }
}

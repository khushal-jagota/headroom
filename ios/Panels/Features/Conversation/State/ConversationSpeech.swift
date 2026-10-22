import AVFoundation
// Speech predates Sendable annotations; its audio request accepts buffers from the audio tap.
@preconcurrency import Speech
import Observation

@MainActor @Observable final class ConversationSpeech {
    var recording = false
    var starting = false
    private var generation = 0
    var error: String?
    private var engine: AVAudioEngine?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var recognition: SFSpeechRecognitionTask?

    func start(update: @escaping @MainActor @Sendable (String) -> Void) async {
        guard !recording, !starting else { return }
        starting = true
        let currentGeneration = generation
        defer { starting = false }
        let speech = await withCheckedContinuation { continuation in
            // Speech invokes this callback off the main actor; only resume the continuation here.
            SFSpeechRecognizer.requestAuthorization { @Sendable status in continuation.resume(returning: status == .authorized) }
        }
        let microphone = await AVAudioApplication.requestRecordPermission()
        guard currentGeneration == generation else { return }
        guard speech, microphone else { error = "Speech access is off. Use the keyboard or enable access in Settings."; return }
        guard let recognizer = SFSpeechRecognizer(), recognizer.isAvailable else {
            error = "Speech is unavailable. Use the keyboard."; return
        }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement, options: .duckOthers)
            try session.setActive(true)
            let audio = AVAudioEngine()
            let buffer = SFSpeechAudioBufferRecognitionRequest()
            buffer.shouldReportPartialResults = true
            let input = audio.inputNode
            input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { @Sendable samples, _ in buffer.append(samples) }
            engine = audio; request = buffer
            recognition = recognizer.recognitionTask(with: buffer) { @Sendable [weak self] result, failure in
                let text = result?.bestTranscription.formattedString
                let final = result?.isFinal == true
                let failed = failure != nil
                Task { @MainActor [weak self] in
                    guard self?.generation == currentGeneration else { return }
                    if let text { update(text) }
                    if final || failed { self?.stop() }
                }
            }
            audio.prepare(); try audio.start()
            recording = true; error = nil
            Haptics.medium()
        } catch { stop(); self.error = "Speech could not start. Use the keyboard." }
    }
    func stop() {
        generation += 1
        engine?.stop(); engine?.inputNode.removeTap(onBus: 0)
        request?.endAudio(); recognition?.cancel()
        engine = nil; request = nil; recognition = nil
        if recording { Haptics.light() }
        recording = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}

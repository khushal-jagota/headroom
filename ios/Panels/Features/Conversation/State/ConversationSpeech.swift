import AVFoundation
// Speech predates Sendable annotations; its audio request accepts buffers from the audio tap.
@preconcurrency import Speech
import Observation

@MainActor @Observable final class ConversationSpeech {
    var recording = false
    var starting = false
    var transcribing = false
    var level: Float = 0
    var startedAt: Date?
    private var capture = ConversationSpeechDraft()
    private var insert: (@MainActor @Sendable (String) -> Void)?
    private var finishTask: Task<Void, Never>?
    var error: String?
    private var engine: AVAudioEngine?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var recognition: SFSpeechRecognitionTask?

    func start(update: @escaping @MainActor @Sendable (String) -> Void) async {
        guard !recording, !starting, !transcribing else { return }
        starting = true
        insert = update
        let currentGeneration = capture.begin()
        defer { if capture.generation == currentGeneration { starting = false } }
#if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--conversation-fixture-speech"), ProcessInfo.processInfo.arguments.contains("--conversation-stub") {
            capture.receive("Keep the newest message in view.\n", generation: currentGeneration)
            recording = true; startedAt = Date(); level = 0.4; error = nil
            return
        }
#endif
        let speech = await withCheckedContinuation { continuation in
            // Speech invokes this callback off the main actor; only resume the continuation here.
            SFSpeechRecognizer.requestAuthorization { @Sendable status in continuation.resume(returning: status == .authorized) }
        }
        let microphone = await AVAudioApplication.requestRecordPermission()
        guard currentGeneration == capture.generation else { return }
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
            input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { @Sendable [weak self] samples, _ in
                buffer.append(samples)
                guard let channel = samples.floatChannelData?[0], samples.frameLength > 0 else { return }
                var sum: Float = 0
                for index in 0..<Int(samples.frameLength) { sum += channel[index] * channel[index] }
                let level = min(1, sqrt(sum / Float(samples.frameLength)) * 8)
                Task { @MainActor [weak self] in
                    guard self?.capture.generation == currentGeneration, self?.recording == true else { return }
                    self?.level = level
                }
            }
            engine = audio; request = buffer
            recognition = recognizer.recognitionTask(with: buffer) { @Sendable [weak self] result, failure in
                let text = result?.bestTranscription.formattedString
                let final = result?.isFinal == true
                let failed = failure != nil
                Task { @MainActor [weak self] in
                    guard self?.capture.generation == currentGeneration else { return }
                    if let text { self?.capture.receive(text, generation: currentGeneration) }
                    if final || failed {
                        if self?.recording == true || self?.transcribing == true { self?.complete() }
                    }
                }
            }
            audio.prepare(); try audio.start()
            recording = true; startedAt = Date(); error = nil
            Haptics.light()
        } catch { cancel(); self.error = "Speech could not start. Use the keyboard." }
    }
    func stop() {
        guard recording else { return }
        recording = false; transcribing = true
        engine?.stop(); engine?.inputNode.removeTap(onBus: 0); engine = nil
        request?.endAudio()
        finishTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            self?.complete()
        }
    }
    private func complete() {
        let words = capture.finish(generation: capture.generation) ?? ""
        let callback = insert
        cancel()
        if words.isEmpty { error = "No speech heard. Try again." }
        else { callback?(words) }
    }
    func cancel() {
        let hadSession = request != nil || engine != nil
        capture.cancel()
        finishTask?.cancel(); finishTask = nil
        engine?.stop(); engine?.inputNode.removeTap(onBus: 0)
        request?.endAudio(); recognition?.cancel()
        engine = nil; request = nil; recognition = nil; insert = nil
        recording = false; starting = false; transcribing = false; level = 0; startedAt = nil
        if hadSession { try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
    }
}

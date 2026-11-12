import XCTest
@testable import Panels

/// The screen stays awake only while a recorder holds and the app is in the foreground.
@MainActor final class KeepAwakeTests: XCTestCase {
    private var applied: [Bool] = []

    private func makeAwake() -> KeepAwake {
        applied = []
        return KeepAwake { [weak self] in self?.applied.append($0) }
    }

    func testHoldsUntilEveryRecorderReleases() {
        let awake = makeAwake()
        let first = NSObject(), second = NSObject()
        awake.hold(first)
        XCTAssertTrue(awake.isAwake)
        awake.hold(second)
        awake.release(first)
        XCTAssertTrue(awake.isAwake, "One recorder must not release another recorder's hold")
        awake.release(second)
        XCTAssertFalse(awake.isAwake)
        XCTAssertEqual(applied, [true, false])
    }

    func testRepeatedHoldAndUnknownReleaseAreHarmless() {
        let awake = makeAwake()
        let recorder = NSObject()
        awake.release(recorder)
        XCTAssertFalse(awake.isAwake)
        awake.hold(recorder)
        awake.hold(recorder)
        awake.release(recorder)
        XCTAssertFalse(awake.isAwake, "A single release ends a repeated hold from one recorder")
        XCTAssertEqual(applied, [false, true, false])
    }

    func testLeavingForegroundLetsThePhoneSleepAndReturningRestoresHold() {
        let awake = makeAwake()
        let recorder = NSObject()
        awake.hold(recorder)
        awake.foreground = false
        XCTAssertFalse(awake.isAwake)
        awake.foreground = true
        XCTAssertTrue(awake.isAwake)
        awake.release(recorder)
        awake.foreground = false
        awake.foreground = true
        XCTAssertFalse(awake.isAwake, "Returning to the foreground without a recording keeps normal sleep")
        XCTAssertEqual(applied, [true, false, true, false])
    }

    func testConversationSpeechCancelReleasesItsHold() {
        let awake = makeAwake()
        let speech = ConversationSpeech(awake: awake)
        awake.hold(speech)
        speech.cancel()
        XCTAssertFalse(awake.isAwake)
    }

    func testReviewVoiceCancelAndPreserveReleaseTheirHold() {
        let awake = makeAwake()
        let cancelled = ReviewVoiceCapture(awake: awake)
        awake.hold(cancelled)
        cancelled.cancel()
        XCTAssertFalse(awake.isAwake)
        let preserved = ReviewVoiceCapture(awake: awake)
        awake.hold(preserved)
        preserved.preserve()
        XCTAssertFalse(awake.isAwake)
    }
}

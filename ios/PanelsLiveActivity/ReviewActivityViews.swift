import SwiftUI

/// The colours of the approved design: the web tones, and the accent as the summary uses it.
enum ReviewActivityTone {
    static let strong = Color(red: 0xec / 255, green: 0xec / 255, blue: 0xec / 255)
    static let secondary = Color(red: 0xa7 / 255, green: 0xa7 / 255, blue: 0xa7 / 255)
    static let tertiary = Color(red: 0x8c / 255, green: 0x8c / 255, blue: 0x8c / 255)
    static let accentBright = Color(red: 0x9a / 255, green: 0xad / 255, blue: 0xd2 / 255)
    /// The Lock Screen card's dark tint.
    static let card = Color(red: 16 / 255, green: 16 / 255, blue: 16 / 255).opacity(0.86)
}

/// The count rolls when it changes. Nothing else on the card moves.
private struct RollingCount: View {
    let count: Int
    var body: some View {
        Text("\(count)")
            .contentTransition(.numericText(value: Double(count)))
            .animation(.spring(duration: 0.42, bounce: 0.3), value: count)
    }
}

/// Two lines, as the Review summary writes them: the lead, then the detail beneath it.
struct ReviewHero: View {
    let state: ReviewActivityAttributes.ContentState
    let size: CGFloat
    /// The expanded Island may carry the card that just arrived. The Lock Screen never does.
    var showsArrival = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Group {
                if state.isClear {
                    Text("All clear.")
                } else {
                    HStack(spacing: 0) {
                        RollingCount(count: state.count)
                        Text(" new.")
                    }
                }
            }
            .font(.system(size: size, weight: .bold))
            .foregroundStyle(ReviewActivityTone.strong)
            Text(showsArrival ? state.arrivedTitle ?? state.detail : state.detail)
                .font(.system(size: size, weight: .semibold))
                .foregroundStyle(ReviewActivityTone.tertiary)
                .lineLimit(1)
        }
        .tracking(-0.025 * size)
        .monospacedDigit()
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// The Lock Screen card: two lines at about 31pt on a dark tint, with no app icon.
struct ReviewLockScreenView: View {
    let state: ReviewActivityAttributes.ContentState
    var body: some View {
        ReviewHero(state: state, size: 31)
            .padding(.horizontal, 22)
            .padding(.vertical, 20)
    }
}

/// What sits at the trailing edge of the compact Island, and alone in the minimal one.
struct ReviewIslandCount: View {
    let state: ReviewActivityAttributes.ContentState
    var body: some View {
        if state.isClear {
            Image(systemName: "checkmark")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(ReviewActivityTone.tertiary)
        } else {
            RollingCount(count: state.count)
                .font(.system(size: 17, weight: .semibold))
                .monospacedDigit()
                .foregroundStyle(ReviewActivityTone.accentBright)
        }
    }
}

struct ReviewIslandLabel: View {
    var body: some View {
        Text("Review")
            .font(.system(size: 14, weight: .semibold))
            .tracking(-0.14)
            .foregroundStyle(ReviewActivityTone.secondary)
    }
}

/// The expanded Island, at 26pt. A new card puts its title on the second line.
struct ReviewIslandExpanded: View {
    let state: ReviewActivityAttributes.ContentState
    var body: some View {
        ReviewHero(state: state, size: 26, showsArrival: true)
            .padding(.horizontal, 6)
            .padding(.top, 4)
    }
}

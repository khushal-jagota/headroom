import SwiftUI
import UIKit

/// The server row and the places that live behind the Settings gear, above Settings itself.
struct SettingsPlaces: View {
    @Environment(AppStore.self) private var store
    var body: some View {
        Section {
            TimelineView(.periodic(from: .now, by: 1)) { context in
                let state = DeploymentStatusPresentation.resolve(store.deployment, connected: store.connection == "Following changes", readFailed: store.errors["Deployment"] != nil, now: context.date)
                HStack(spacing: PanelsTheme.Space.md) {
                    Image(systemName: state == .problem ? "exclamationmark.triangle" : "server.rack")
                        .foregroundStyle(state == .problem ? PanelsTheme.error : PanelsTheme.accent)
                    VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                        MoreSelectableText("Panels server", color: PanelsTheme.strong)
                        MoreSelectableText([state?.rawValue ?? "Up to date", store.deployment?.detail, store.deployment?.deployedSha.map { "Running \(String($0.prefix(7)))" }].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "), style: .footnote, color: state == .problem ? PanelsTheme.error : PanelsTheme.faint)
                    }
                }.padding(.vertical, PanelsTheme.Space.xs)
                .accessibilityIdentifier("settings.deployment")
                .accessibilityElement(children: .contain)
            }
            updatesRow
        }.listRowBackground(PanelsTheme.raised)
        Section {
            place("All goals", symbol: "target", route: .goals)
            place("Usage", symbol: "chart.bar", route: .usage)
            place("Diagnostics", symbol: "server.rack", route: .vps)
        }
    }

    /// The one place that tells of a server update: a short line while one is offered or running.
    private var updatesRow: some View {
        let status = store.updateStatus
        let detail = status.map(\.title).flatMap { ["Up to date", "Update complete"].contains($0) ? nil : $0 }
        return NavigationLink(value: AppRoute.updates) {
            HStack(spacing: PanelsTheme.Space.md) {
                Label("Updates", systemImage: "arrow.triangle.2.circlepath")
                Spacer(minLength: PanelsTheme.Space.sm)
                if let detail {
                    Text(detail).font(PanelsTheme.TypeScale.small)
                        .foregroundStyle(status?.needsAttention == true ? PanelsTheme.error : PanelsTheme.faint)
                }
            }.padding(.vertical, PanelsTheme.Space.xs)
        }
        .accessibilityIdentifier("settings.updates")
    }

    private func place(_ title: String, symbol: String, route: AppRoute) -> some View {
        NavigationLink(value: route) {
            Label(title, systemImage: symbol)
                .padding(.vertical, PanelsTheme.Space.xs)
        }
        .listRowBackground(PanelsTheme.raised)
    }
}

/// Native selection for static List/Form text; explicit roles retain their current type scale.
struct MoreSelectableText: View {
    let text: String
    let weight: UIFont.Weight
    let color: Color
    let monospacedDigits: Bool
    let lineLimit: Int?
    let nativeFont: UIFont?
    @ScaledMetric private var pointSize: CGFloat

    init(_ text: String, style: UIFont.TextStyle = .body, weight: UIFont.Weight = .regular,
         color: Color = PanelsTheme.text, monospacedDigits: Bool = false, lineLimit: Int? = nil, nativeFont: UIFont? = nil) {
        self.text = text; self.weight = weight; self.color = color
        self.monospacedDigits = monospacedDigits; self.lineLimit = lineLimit; self.nativeFont = nativeFont
        let size: CGFloat
        let relativeTo: Font.TextStyle
        switch style {
        case .footnote: size = 13; relativeTo = .footnote
        case .caption2: size = 11; relativeTo = .caption2
        case .title3: size = 20; relativeTo = .title3
        case .largeTitle: size = 34; relativeTo = .largeTitle
        default: size = 17; relativeTo = .body
        }
        _pointSize = ScaledMetric(wrappedValue: size, relativeTo: relativeTo)
    }

    var body: some View {
        let font = nativeFont ?? (monospacedDigits
            ? UIFont.monospacedDigitSystemFont(ofSize: pointSize, weight: weight)
            : UIFont.systemFont(ofSize: pointSize, weight: weight))
        PanelsSelectableText(text, font: font, color: UIColor(color), lineLimit: lineLimit)
            .alignmentGuide(.firstTextBaseline) { _ in font.ascender }
    }
}

/// Keep the system's labeled-value row layout while making both leaves selectable.
struct MoreSelectableValue: View {
    let title: String
    let value: String
    var monospacedDigits = false

    init(_ title: String, value: String, monospacedDigits: Bool = false) {
        self.title = title; self.value = value; self.monospacedDigits = monospacedDigits
    }

    var body: some View {
        LabeledContent {
            MoreSelectableText(value, color: Color(uiColor: .secondaryLabel), monospacedDigits: monospacedDigits)
        } label: {
            MoreSelectableText(title, monospacedDigits: monospacedDigits)
        }
    }
}

/// Keep the system empty-state composition and use its native unavailable-content typography.
/// UIKit exposes these defaults; SwiftUI does not expose its resolved empty-state text font.
struct MoreUnavailableText: View {
    let text: String
    var secondary = false
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        let defaults = UIContentUnavailableConfiguration.empty()
        let properties = secondary ? defaults.secondaryTextProperties : defaults.textProperties
        MoreSelectableText(text, color: Color(uiColor: properties.color), nativeFont: properties.font)
    }
}

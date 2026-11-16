import SwiftUI
import UIKit

struct ChiefSwipeSettingsSection: View {
    @State private var log = ChiefSwipeLog.shared
    @State private var export: Export?
    @State private var error: String?

    var body: some View {
        Section {
            Toggle("Chief swipe diagnostics", isOn: $log.isEnabled)
                .tint(PanelsTheme.accent)
                .accessibilityIdentifier("chief-swipe-enabled")
            LabeledContent("Log entries", value: "\(log.recordCount)")
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Log entries")
                .accessibilityValue("\(log.recordCount)")
                .accessibilityIdentifier("chief-swipe-count")
            Button("Share Log", systemImage: "square.and.arrow.up") {
                do { export = Export(url: try log.export()); error = nil }
                catch { self.error = "Could not prepare the diagnostic log." }
            }.disabled(log.recordCount == 0)
                .accessibilityIdentifier("chief-swipe-share")
            Button("Clear Log", systemImage: "trash", role: .destructive) {
                do { try log.clear(); error = nil }
                catch { self.error = "Could not clear the diagnostic log." }
            }.accessibilityIdentifier("chief-swipe-clear")
            if let error = error ?? log.error {
                Text(error).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error)
            }
        } header: {
            Text("Diagnostics").panelsSectionHeading(.configuration)
        } footer: {
            Text("Chief swipes only. No page text. Keeps up to 1 MiB for 24 hours. Share only when you choose.")
        }.listRowBackground(PanelsTheme.raised)
            .onAppear { log.refresh() }
            .sheet(item: $export) { ChiefSwipeShareSheet(url: $0.url) }
    }
    private struct Export: Identifiable { let id = UUID(); let url: URL }
}

private struct ChiefSwipeShareSheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

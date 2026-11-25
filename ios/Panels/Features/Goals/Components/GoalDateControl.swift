import SwiftUI

struct GoalDateControl: View {
    let value: String?
    let label: String
    let tone: String
    let revision: Int
    let save: (String?, Int) async -> Void
    @State private var opened = false
    @State private var date = Date()
    @State private var capturedRevision = 0
    var body: some View {
        Button {
            date = GoalPresentation.calendarDate(value ?? "") ?? Date()
            capturedRevision = revision; opened = true
        } label: {
            Text(value == nil || value == "" ? "No deadline" : GoalPresentation.date(value)).foregroundStyle(PanelsTheme.Goal.tone(tone))
                .frame(minHeight: 32)
        }.buttonStyle(.plain).systemCopyText(value == nil || value == "" ? "No deadline" : GoalPresentation.date(value)).accessibilityLabel(label + ", " + GoalPresentation.date(value))
            .sheet(isPresented: $opened) {
                NavigationStack {
                    DatePicker(label, selection: $date, displayedComponents: .date).datePickerStyle(.graphical).environment(\.timeZone, TimeZone(secondsFromGMT: 0)!).padding()
                        .toolbar {
                            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { opened = false } }
                            ToolbarItem(placement: .confirmationAction) { Button("Done") { let chosen = GoalPresentation.day(date); opened = false; Task { await save(chosen, capturedRevision) } } }
                            ToolbarItem(placement: .bottomBar) { Button("No deadline") { opened = false; Task { await save(nil, capturedRevision) } } }
                        }.panelsNavigationTitle(label).navigationBarTitleDisplayMode(.inline)
                }.presentationDetents([.medium, .large]).preferredColorScheme(.dark)
            }
    }
}

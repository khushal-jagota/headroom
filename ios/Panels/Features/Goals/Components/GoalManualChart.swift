import SwiftUI

private struct GoalManualCounter: View {
    let block: GoalJSON
    let value: Double?
    var size: CGFloat = 32
    /// The folded summary keeps the unit in the counter; the inline value shows it beside the field.
    var unit = false
    private var displayBlock: GoalJSON {
        // The web's inline counter carries no unit; a reading's unit sits beside it (`.unit`).
        unit ? block : block.setting("unit", .string("")).setting("format", (block["format"].isNull ? .object([:]) : block["format"]).setting("suffix", .string("")))
    }
    var body: some View {
        if let literal = GoalChartPresentation.manualLiteral(displayBlock, value: value) {
            Text(literal).font(.system(size: size, weight: .medium)).monospacedDigit()
        } else { GoalCounter(block: GoalChartPresentation.manualDisplayBlock(displayBlock, value: value), value: value, size: size) }
    }
}

/// The web's inline value (manual-measurements.tsx): a native field over the shared
/// rolling counter. Focus selects the accepted value; Done or focus loss commits a
/// changed draft; Cancel restores the accepted value. No edit menu covers the number.
struct GoalManualValue: View {
    @Bindable var editor: GoalManualEditor
    let block: GoalJSON
    var centered = false
    var size: CGFloat = 40
    @State private var editing = false
    @State private var submitting = false
    private var current: Double? { editor.state?.value(cumulative: editor.cumulative) }
    var body: some View {
        // The counter stays mounted under the field, as on the web, so an accepted edit
        // rolls from the old reading to the new one.
        let editingValue = editing || editor.draft != nil
        ZStack(alignment: centered ? .center : .leading) {
            Button { guard !editor.busy, editor.pending == nil else { return }; editor.beginEditing(); editing = true } label: {
                GoalManualCounter(block: block, value: current, size: size).frame(minHeight: size * 1.2)
            }.buttonStyle(.plain).contentShape(Rectangle())
                .opacity(editingValue ? 0 : 1).allowsHitTesting(!editingValue)
                .accessibilityHidden(editingValue)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(GoalChartPresentation.manualLiteral(block, value: current) ?? GoalChartPresentation.format(GoalChartPresentation.manualDisplayBlock(block, value: current), current))
                .accessibilityIdentifier("goal-manual-value-" + block.id).accessibilityHint("Edit reading")
            // The field stays mounted too, so accepting an edit swaps opacity instead of
            // tearing down a first responder in the frame the counter starts turning.
            GoalNumberField(text: Binding(get: { editor.draft ?? current.map(GoalChartPresentation.plainNumber) ?? "" }, set: { editor.draft = $0 }),
                            focused: $editing, size: size, centered: centered, label: block["title"].string ?? "Reading",
                            commit: save, cancel: cancel)
                .frame(height: size * 1.2).fixedSize(horizontal: true, vertical: false)
                .opacity(editingValue ? 1 : 0).allowsHitTesting(editingValue).accessibilityHidden(!editingValue)
        }
        .overlay(alignment: .trailing) {
            if block["manual"]["model"].text == "individual_readings", !GoalChartPresentation.suffix(block).isEmpty {
                Text(GoalChartPresentation.suffix(block)).font(.system(size: 20)).foregroundStyle(PanelsTheme.Goal.faint).fixedSize()
                    .alignmentGuide(.trailing) { $0[.leading] }.offset(y: size * 0.12)
            }
        }
        .onChange(of: editing) { old, focused in
            if old && !focused && !submitting && editor.draft != nil && editor.pending == nil { Task { _ = await editor.prepare() } }
        }.onDisappear {
            if editor.draft != nil, !submitting { Task { _ = await editor.prepare() } }
        }
    }
    /// Done writes only a changed number, as the web's commit does; an unreadable one keeps the keyboard up.
    private func save() {
        guard let value = Double(editor.draft?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""), value.isFinite else { editor.error = "Enter a number."; return }
        submitting = true
        editing = false
        Task { _ = await editor.prepare(); submitting = false }
    }
    /// Cancel drops the draft at once, so nothing typed while the accepted reading reloads can be saved.
    private func cancel() {
        editor.draft = nil
        editing = false
        Task { await editor.discard() }
    }
}

/// A plain UIKit number field: it selects its text on focus without showing the
/// copy and paste menu, and carries Cancel and Done above the keyboard.
private struct GoalNumberField: UIViewRepresentable {
    @Binding var text: String
    @Binding var focused: Bool
    let size: CGFloat
    let centered: Bool
    let label: String
    let commit: () -> Void
    let cancel: () -> Void
    final class Field: UITextField {
        override func canPerformAction(_ action: Selector, withSender sender: Any?) -> Bool { false }
        override func buildMenu(with builder: any UIMenuBuilder) { builder.remove(menu: .standardEdit); super.buildMenu(with: builder) }
    }
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeUIView(context: Context) -> Field {
        let field = Field()
        field.font = UIFontMetrics(forTextStyle: .largeTitle).scaledFont(for: .monospacedDigitSystemFont(ofSize: size, weight: .medium))
        field.adjustsFontForContentSizeCategory = true
        field.textColor = UIColor(PanelsTheme.strong)
        field.tintColor = UIColor(PanelsTheme.accent)
        field.textAlignment = centered ? .center : .natural
        field.keyboardType = .decimalPad
        field.autocorrectionType = .no; field.spellCheckingType = .no
        field.accessibilityLabel = label
        field.delegate = context.coordinator
        field.addTarget(context.coordinator, action: #selector(Coordinator.changed(_:)), for: .editingChanged)
        if #available(iOS 16.0, *) { field.interactions.removeAll { $0 is UIEditMenuInteraction } }
        let bar = UIToolbar(frame: CGRect(x: 0, y: 0, width: 320, height: 44))
        bar.items = [UIBarButtonItem(title: "Cancel", style: .plain, target: context.coordinator, action: #selector(Coordinator.cancel)),
                     UIBarButtonItem(systemItem: .flexibleSpace),
                     UIBarButtonItem(title: "Done", style: .done, target: context.coordinator, action: #selector(Coordinator.done))]
        bar.sizeToFit()
        field.inputAccessoryView = bar
        field.setContentHuggingPriority(.defaultLow, for: .horizontal)
        field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return field
    }
    func updateUIView(_ field: Field, context: Context) {
        context.coordinator.owner = self
        if field.text != text { field.text = text }
        if focused, !field.isFirstResponder { DispatchQueue.main.async { field.becomeFirstResponder() } }
        else if !focused, field.isFirstResponder { DispatchQueue.main.async { field.resignFirstResponder() } }
    }
    final class Coordinator: NSObject, UITextFieldDelegate {
        var owner: GoalNumberField
        init(_ owner: GoalNumberField) { self.owner = owner }
        @objc func changed(_ field: UITextField) {
            // A comma-decimal keypad types ","; readings are parsed with ".".
            let text = (field.text ?? "").replacingOccurrences(of: ",", with: ".")
            if field.text != text { field.text = text }
            owner.text = text
        }
        @objc func done() { owner.commit() }
        @objc func cancel() { owner.cancel() }
        func textFieldDidBeginEditing(_ field: UITextField) {
            owner.focused = true
            // The web selects the accepted value on focus so typing replaces it.
            DispatchQueue.main.async { field.selectedTextRange = field.textRange(from: field.beginningOfDocument, to: field.endOfDocument) }
        }
        func textFieldDidEndEditing(_ field: UITextField) { if owner.focused { owner.focused = false } }
        func textFieldShouldReturn(_ field: UITextField) -> Bool { owner.commit(); return false }
        // A long press would otherwise still raise the system edit menu over the number.
        func textField(_ field: UITextField, editMenuForCharactersIn range: NSRange, suggestedActions: [UIMenuElement]) -> UIMenu? { UIMenu(children: []) }
    }
}

struct GoalManualChart: View {
    let block: GoalJSON
    let session: GoalSession
    var integrated = false
    var preview = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var active: String?
    @State private var bodyVisible = false
    private var editor: GoalManualEditor { session.manual(block) }
    private var preference: GoalChartPreference { session.preference(block.id) }
    private var supporting: Bool { block["placement"].text == "supporting" }
    private var current: Double? { editor.state?.value(cumulative: editor.cumulative) }
    private var target: Double? {
        block["reference"].array.isEmpty ? GoalChartPresentation.target(block, range: preference.range) : GoalChartPresentation.reference(block, date: editor.cumulative ? editor.state?.latestObservation?["date"].string ?? editor.state?.date ?? "" : editor.state?.date ?? "")
    }
    private var history: [GoalJSON] {
        let original = (session.readings?.values ?? []).filter { $0["measure_id"].text != editor.measureID || editor.history[$0["date"].text] == nil }
        return original + Array(editor.history.values)
    }
    private var dates: [String] {
        if !block["ranges"].array.isEmpty { return GoalChartPresentation.data(block, values: history, range: preference.range).map(\.id) }
        guard let day = editor.state?.currentDate, let today = GoalPresentation.calendarDate(day) else { return [] }
        return (0..<7).map { GoalPresentation.day(today.addingTimeInterval(Double($0 - 6) * 86400)) }
    }
    private var data: [GoalChartDatum] {
        dates.map { day in
            let observation = history.first { $0["measure_id"].text == editor.measureID && $0["date"].text == day }
            var point = GoalChartDatum(id: day, label: GoalManualBars.dayMonth(day))
            point.values["actual"] = observation?["value"].number
            point.values["target"] = block["reference"].array.isEmpty ? target : GoalChartPresentation.reference(block, date: day)
            return point
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            if supporting {
                Button {
                    Task {
                        guard await editor.prepare() else { return }
                        withAnimation(reduceMotion ? nil : PanelsTheme.Goal.disclosure) { var next = preference; next.expanded.toggle(); session.remember(block.id, next) }
                    }
                } label: {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack { title; Spacer(); Image(systemName: "chevron.down").font(.system(size: 12)).rotationEffect(.degrees(preference.expanded ? 180 : 0)).animation(reduceMotion ? nil : PanelsTheme.Goal.standard, value: preference.expanded) }
                        GoalManualCounter(block: block, value: current, size: 28, unit: true)
                    }.contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityValue(preference.expanded ? "Expanded" : "Collapsed")
            } else { title }
            if !supporting || preference.expanded {
                VStack(alignment: .leading, spacing: 16) {
                    if editor.cumulative {
                        GoalManualGauge(block: block, editor: editor, current: current, target: target, preview: preview)
                        HStack(spacing: 12) {
                            Text(completion).font(.system(size: 11)).foregroundStyle(GoalManualBars.toneColor(block, current, target))
                            Spacer(minLength: 0)
                            increments
                        }.frame(minHeight: 40)
                    } else {
                        HStack(alignment: .center, spacing: 8) {
                            if preview { GoalManualCounter(block: block, value: current, size: 40) }
                            else { GoalManualValue(editor: editor, block: block) }
                            Spacer(minLength: 8)
                            if block["manual"]["model"].text == "daily_quota" { increments }
                        }.frame(minHeight: 48).padding(.bottom, 8)
                        let pick: ((String) -> Void)? = preview ? nil : { date in Task { guard await editor.prepare() else { return }; await editor.refresh(date: date) } }
                        if block["manual"]["model"].text == "daily_quota" {
                            GoalManualBars(block: block, data: data, today: editor.state?.currentDate, selected: editor.state?.date, active: $active, select: pick)
                        } else {
                            GoalPlot(block: block.setting("kind", .string("line")), data: data, series: [], range: preference.range, active: $active, manual: true, select: pick)
                        }
                    }
                    if !block["ranges"].array.isEmpty {
                        GoalRangeSwitch(choices: block["ranges"].array.map { ($0.id, $0["label"].text) }, selection: Binding(get: { preference.range }, set: { range in
                            Task {
                                guard await editor.prepare() else { return }
                                var next = preference; next.range = range; session.remember(block.id, next)
                                if !editor.cumulative, let date = GoalChartPresentation.data(block, values: history, range: range).last?.id { await editor.refresh(date: date) }
                            }
                        }))
                    }
                }.opacity(!supporting || bodyVisible || reduceMotion ? 1 : 0).transition(supporting && !reduceMotion ? .goalDisclosure : .opacity)
            }
            GoalManualFailure(editor: editor)
        }.modifier(GoalChartContainer(framed: !integrated || supporting))
            .task(id: preference.expanded) {
                let open = preference.expanded
                if open { try? await Task.sleep(for: .seconds(reduceMotion ? 0 : PanelsTheme.Goal.delay)) }
                guard !Task.isCancelled else { return }
                withAnimation(reduceMotion ? nil : PanelsTheme.Goal.easeInOut(open ? PanelsTheme.Goal.standardDuration : PanelsTheme.Goal.instantDuration)) { bodyVisible = open }
            }
            .task(id: session.readings?.revision) { await editor.refresh() }
    }
    /// The web's completion line under a cumulative gauge.
    private var completion: String {
        guard let current, let target, GoalChartPresentation.tone(block, headline: .init(value: current, target: target)) == "good" else { return "" }
        if current == target { return "Target reached" }
        if GoalChartPresentation.direction(block) == "atmost" { return GoalChartPresentation.comparison(block, headline: .init(value: current, target: target)) }
        return GoalChartPresentation.number(current - target, decimals: 20, trim: true) + " beyond target"
    }
    private var title: some View { Text(block["title"].string ?? "Reading").panelsScaledFont(size: supporting ? 14 : 17, weight: .medium).foregroundStyle(supporting ? PanelsTheme.Goal.faint : PanelsTheme.strong) }
    @ViewBuilder private var increments: some View {
        if !preview {
            HStack(spacing: 4) {
                ForEach(Array(block["manual"]["increments"].array.enumerated()), id: \.offset) { index, entry in
                    if let delta = entry.number {
                        Button((delta > 0 ? "+" : "") + GoalChartPresentation.number(delta, decimals: 6, trim: true)) {
                            Task { guard await editor.prepare() else { return }; await editor.write(delta: delta); await session.refresh() }
                        }.buttonStyle(GoalAddButtonStyle(primary: index == 0)).disabled(editor.state == nil)
                    }
                }
            }
        }
    }
}

/// The web's add buttons: the first one inverted, each pressing to 97% at once.
private struct GoalAddButtonStyle: ButtonStyle {
    let primary: Bool
    @Environment(\.isEnabled) private var enabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 15)).monospacedDigit()
            .foregroundStyle(primary ? PanelsTheme.ink : PanelsTheme.text)
            .padding(8).frame(minWidth: 40, minHeight: 40)
            .background(primary ? PanelsTheme.strong : PanelsTheme.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(PanelsTheme.border))
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.97 : 1)
            .opacity(enabled ? 1 : 0.35)
            .transaction { $0.animation = nil }
    }
}

struct GoalManualFailure: View {
    let editor: GoalManualEditor
    var body: some View {
        if let error = editor.error {
                VStack(alignment: .leading, spacing: 8) {
                    Text(error).foregroundStyle(PanelsTheme.error)
                    HStack {
                        if editor.pending != nil, !editor.conflicted { Button("Retry save") { Task { await editor.retry() } } }
                        else if editor.pending == nil { Button("Retry") { Task { await editor.refresh() } } }
                        Button("Discard edit") { Task { await editor.discard() } }
                    }.disabled(editor.busy)
                }.font(.footnote)
            }
    }
}

/// The web's manual daily bars (manual-measurements.tsx): bars against a scale from
/// zero to the largest reading or target, each changing height and colour over 0.24 s.
struct GoalManualBars: View {
    let block: GoalJSON
    let data: [GoalChartDatum]
    let today: String?
    let selected: String?
    @Binding var active: String?
    var select: ((String) -> Void)? = nil
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private var title: String { block["title"].string ?? "Reading" }
    private var minimum: Double { min(0, data.compactMap { $0.values["target"] }.min() ?? 0, data.compactMap { $0.values["actual"] }.min() ?? 0) }
    private var maximum: Double { max(0, data.compactMap { $0.values["target"] }.max() ?? 0, data.compactMap { $0.values["actual"] }.max() ?? 0) }
    private var span: Double { maximum - minimum == 0 ? 1 : maximum - minimum }
    private var target: Double? { block["reference"].array.isEmpty ? data.compactMap({ $0.values["target"] }).first : nil }
    static func compact(_ number: Double) -> String {
        let magnitude = abs(number)
        for (limit, suffix) in [(1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")] where magnitude >= limit {
            let scaled = number / limit
            return GoalChartPresentation.number(scaled, decimals: abs(scaled) < 10 ? 1 : 0, trim: true) + suffix
        }
        return GoalChartPresentation.number(number, decimals: magnitude < 10 ? 1 : 0, trim: true)
    }
    /// The web's manual dates: "29 Oct".
    static func dayMonth(_ day: String) -> String {
        let parts = GoalPresentation.date(day).split(separator: " ")
        return parts.count == 2 ? "\(parts[1]) \(parts[0])" : day
    }
    static func toneColor(_ block: GoalJSON, _ value: Double?, _ target: Double?) -> Color {
        switch GoalChartPresentation.tone(block, headline: .init(value: value, target: target)) {
        case "good": PanelsTheme.success
        case "attention": PanelsTheme.Goal.attention
        case "bad": PanelsTheme.error
        default: PanelsTheme.accent
        }
    }
    var body: some View {
        GeometryReader { geometry in
            let plot = geometry.size.height - 24, width = geometry.size.width - 32
            ZStack(alignment: .topLeading) {
                grid(plot: plot, width: width)
                HStack(spacing: 4) {
                    ForEach(data) { datum in bar(datum, plot: plot) }
                }.frame(width: width)
                if let index = data.firstIndex(where: { $0.id == active }) {
                    let share = (Double(index) + 0.5) / Double(max(1, data.count))
                    inspection(data[index])
                        .alignmentGuide(.leading) { $0.width * share }
                        .offset(x: width * share, y: 8).allowsHitTesting(false)
                }
            }
        }
        .frame(height: 220)
        .background { if active != nil { GoalInspectionDismissal { active = nil } } }
        .accessibilityElement(children: .contain).accessibilityLabel("\(title) by date")
    }
    private func y(_ value: Double, plot: CGFloat) -> CGFloat { plot * (1 - (value - minimum) / span) }
    private func grid(plot: CGFloat, width: CGFloat) -> some View {
        var lines: [(String, Double, String, Bool)] = []
        if !block["reference"].array.isEmpty || target == nil || (maximum - (target ?? 0)) / span > 0.09 { lines.append(("max", maximum, Self.compact(maximum), false)) }
        if let target { lines.append(("target", target, (target - minimum) / span > 0.08 ? Self.compact(target) : "", true)) }
        lines.append(("zero", 0, "0", false))
        if minimum < 0 { lines.append(("min", minimum, Self.compact(minimum), false)) }
        return Canvas { context, _ in
            for (_, value, label, dashed) in lines {
                let at = y(value, plot: plot) + 8
                context.stroke(Path { $0.move(to: .init(x: 0, y: at)); $0.addLine(to: .init(x: width, y: at)) }, with: .color(dashed ? PanelsTheme.faint : PanelsTheme.border), style: StrokeStyle(lineWidth: 1, dash: dashed ? [3, 3] : []))
                context.draw(Text(label).font(.system(size: 11)).monospacedDigit().foregroundStyle(PanelsTheme.faint), at: CGPoint(x: width + 8, y: at), anchor: .leading)
            }
        }.frame(height: plot + 16).offset(y: -8).allowsHitTesting(false)
    }
    private func bar(_ datum: GoalChartDatum, plot: CGFloat) -> some View {
        let value = datum.values["actual"], picked = selected == datum.id
        let height = value == nil || value == 0 ? 2 : abs(value!) / span * plot
        return Button {
            active = datum.id
            select?(datum.id)
        } label: {
            VStack(spacing: 0) {
                ZStack(alignment: .bottom) {
                    Color.clear
                    Group {
                        if value == nil {
                            Path { $0.move(to: .zero); $0.addLine(to: .init(x: 1000, y: 0)) }.stroke(PanelsTheme.faint, style: StrokeStyle(lineWidth: 1, dash: [3, 3])).frame(height: 1).clipped()
                        } else {
                            UnevenRoundedRectangle(topLeadingRadius: 4, topTrailingRadius: 4).fill(Self.toneColor(block, value, datum.values["target"]))
                        }
                    }
                    .frame(maxWidth: 32).frame(height: height)
                    .opacity(picked ? 1 : 0.65)
                    .overlay { if picked { UnevenRoundedRectangle(topLeadingRadius: 6, bottomLeadingRadius: 2, bottomTrailingRadius: 2, topTrailingRadius: 6).stroke(PanelsTheme.text, lineWidth: 2).padding(-4) } }
                    .offset(y: -(min(0, value ?? 0) - minimum) / span * plot)
                    .animation(reduceMotion ? nil : PanelsTheme.Goal.easeInOut(PanelsTheme.Goal.standardDuration), value: value)
                    .animation(reduceMotion ? nil : PanelsTheme.Goal.easeInOut(PanelsTheme.Goal.standardDuration), value: span)
                }.frame(height: plot).padding(.horizontal, 4)
                Text(datum.id == today ? "Today" : GoalPresentation.date(datum.id).split(separator: " ").last.map(String.init) ?? datum.label)
                    .font(.system(size: 11)).foregroundStyle(picked ? PanelsTheme.strong : PanelsTheme.faint).frame(height: 24)
            }.contentShape(Rectangle())
        }.buttonStyle(.plain)
            .accessibilityLabel("\(GoalPresentation.date(datum.id)), \(value.map { GoalChartPresentation.format(block, $0) + " " + title.lowercased() } ?? "no reading")")
            .accessibilityAddTraits(picked ? .isSelected : [])
    }
    private func inspection(_ datum: GoalChartDatum) -> some View {
        let value = datum.values["actual"], target = datum.values["target"]
        return VStack(alignment: .leading, spacing: 4) {
            Text(Self.dayMonth(datum.id)).font(.system(size: 11)).foregroundStyle(PanelsTheme.text).padding(.bottom, 2)
            row(title, value.map { GoalChartPresentation.format(block, $0) } ?? "No reading", Self.toneColor(block, value, target), dashed: false)
            if let target { row("Target", GoalChartPresentation.format(block, target), PanelsTheme.faint, dashed: true) }
        }
        .padding(.vertical, 10).padding(.horizontal, 12)
        .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(PanelsTheme.border))
        .shadow(color: Color(red: 10 / 255, green: 9 / 255, blue: 8 / 255).opacity(0.45), radius: 10, y: 8)
        .fixedSize()
    }
    private func row(_ label: String, _ value: String, _ color: Color, dashed: Bool) -> some View {
        HStack(spacing: 8) {
            RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 10, height: 2.5).opacity(dashed ? 0.8 : 1)
            Text(label).foregroundStyle(PanelsTheme.text).lineLimit(1)
            Spacer(minLength: 8)
            Text(value).fontWeight(.medium).foregroundStyle(PanelsTheme.strong).monospacedDigit()
        }.font(.system(size: 13))
    }
}

/// The web's cumulative ring (GaugeVisual, `.radial`): the fill runs along the dial on
/// the smooth spring, changing colour with it, around the inline value.
private struct GoalManualGauge: View {
    let block: GoalJSON
    let editor: GoalManualEditor
    let current: Double?
    let target: Double?
    let preview: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private var fraction: Double {
        guard let target else { return 0 }
        return target == 0 ? (current == 0 ? 1 : 0) : (current ?? 0) / target
    }
    var body: some View {
        GeometryReader { geometry in
            let scale = geometry.size.width / 100
            ZStack(alignment: .top) {
                GoalGaugeArcShape().stroke(PanelsTheme.accent.opacity(0.13), style: StrokeStyle(lineWidth: 9 * scale, lineCap: .round))
                GoalGaugeArcShape().trim(from: 0, to: max(0, min(1, fraction)))
                    .stroke(GoalManualBars.toneColor(block, current, target), style: StrokeStyle(lineWidth: 9 * scale, lineCap: .round))
                    .opacity(current == nil || target == nil || fraction <= 0 ? 0 : 1)
                    .animation(reduceMotion ? nil : PanelsTheme.Goal.smooth, value: fraction)
                    .animation(reduceMotion ? nil : PanelsTheme.Goal.smooth, value: GoalChartPresentation.tone(block, headline: .init(value: current, target: target)))
                VStack(spacing: 4) {
                    Group {
                        if preview { GoalManualCounter(block: block, value: current, size: 40) }
                        else { GoalManualValue(editor: editor, block: block, centered: true) }
                    }.frame(maxWidth: geometry.size.width * 0.65)
                    if let target { Text("of " + GoalChartPresentation.number(target, decimals: 20, trim: true)).font(.system(size: 13)).foregroundStyle(PanelsTheme.faint) }
                }.frame(width: geometry.size.width, height: geometry.size.width)
            }
        }
        .aspectRatio(100 / 90, contentMode: .fit)
        .frame(maxWidth: 232)
        .padding(.vertical, 24)
        .frame(maxWidth: .infinity)
    }
}

private struct GoalGaugeArcShape: Shape {
    func path(in rect: CGRect) -> Path { GoalGaugeGeometry.arc(1, scale: rect.width / 100) }
}

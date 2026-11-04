import Foundation

struct GoalChartDatum: Equatable, Identifiable, Sendable {
    let id: String
    var label: String
    var values: [String: Double] = [:]
    var position: Double? = nil
    var breakBefore = false
    var comparison: Bool? = nil
    var observation: GoalJSON = .null
    var evidence: GoalJSON = .null
}
struct GoalChartHeadline: Equatable, Sendable {
    var value: Double?
    var target: Double?
    var label: String?
    var missingCount = 0
    var comparison: Bool? = nil
    var difference: Double? = nil
    var evidence: [GoalJSON] = []
}

/// Port of charts/presentation.ts. No device calendar, view state or network.
enum GoalChartPresentation {
    static func suffix(_ block: GoalJSON) -> String {
        if let explicit = block["format"]["suffix"].string { return explicit }
        let unit = block["unit"].text
        return unit.first?.isLetter == true ? " " + unit : unit
    }
    static func number(_ value: Double, decimals: Int = 0, trim: Bool = false) -> String {
        let f = NumberFormatter(); f.locale = Locale(identifier: "en_US"); f.numberStyle = .decimal
        f.maximumFractionDigits = decimals; f.minimumFractionDigits = trim ? 0 : decimals
        return f.string(from: NSNumber(value: value)) ?? "—"
    }
    static func format(_ block: GoalJSON, _ value: Double?, trim: Bool = false) -> String {
        guard let value, value.isFinite else { return "—" }
        return block["format"]["prefix"].text + number(value, decimals: Int(block["format"]["decimals"].number ?? 0), trim: trim) + suffix(block)
    }
    private static func manualPrecision(_ block: GoalJSON, value: Double) -> Int {
        let parts = String(value).lowercased().split(separator: "e")
        let fraction = value.rounded(.towardZero) == value ? 0 : parts[0].split(separator: ".").dropFirst().first?.count ?? 0
        return max(Int(block["format"]["decimals"].number ?? 0), fraction - (parts.count > 1 ? Int(parts[1]) ?? 0 : 0))
    }
    static func manualLiteral(_ block: GoalJSON, value: Double?) -> String? {
        guard let value, manualPrecision(block, value: value) > 20 else { return nil }
        return String(value) + suffix(block)
    }
    static func manualDisplayBlock(_ block: GoalJSON, value: Double?) -> GoalJSON {
        guard let value else { return block }
        let decimals = min(20, manualPrecision(block, value: value))
        let format = (block["format"].isNull ? GoalJSON.object([:]) : block["format"]).setting("decimals", .number(Double(decimals)))
        return block.setting("format", format)
    }
    static func categoryRange(_ projection: GoalJSON, _ range: String) -> GoalJSON {
        let ranges = projection["ranges"].array
        return ranges.first { $0.id == range } ?? ranges.first { $0.id == "all" } ?? .null
    }
    static func hasCountAxis(_ block: GoalJSON, projection: GoalJSON, range: String) -> Bool {
        let binding = block["binding"]
        guard binding.kind == "dataset", let field = binding["metric"]["field"].string, projection.kind == "category" else { return false }
        let selected = categoryRange(projection, range)
        let results = projection["dataset_results"].array.filter { $0["dataset_id"] == binding["dataset_id"] && $0["date"].text >= selected["first_date"].text && $0["date"].text <= selected["last_date"].text }
        return !results.isEmpty && results.allSatisfy { $0["output"]["fields"].array.first { $0["name"].text == field }?["kind"].text == "count" }
    }
    static func validNumber(_ value: GoalJSON) -> Double? { value["status"].text == "value" ? value["value"].number : nil }
    static func selectedSeries(_ block: GoalJSON, range: String, projection: GoalJSON = .null) -> [GoalJSON] {
        if !block["binding"].isNull {
            return categoryRange(projection, range)["series"].array.map { series in
                .object(["key": series["key"], "name": series["label"], "points": .array(series["points"].array.map { .object(["label": $0["label"], "value": validNumber($0["actual"]).map(GoalJSON.number) ?? .null]) })])
            }
        }
        let selected = block["ranges"].array.first { $0.id == range }
        let series = selected?["series"].array ?? (block["series"].isNull ? block["ranges"].array.first?["series"].array ?? [] : block["series"].array)
        return block["measure_id"].isNull ? series : Array(series.prefix(1))
    }
    static func seriesKey(_ series: GoalJSON, _ index: Int) -> String { series["key"].string ?? (index == 0 ? "actual" : series["name"].text) }
    static func target(_ block: GoalJSON, range: String) -> Double? {
        let selected = block["ranges"].array.first { $0.id == range } ?? (block["series"].isNull ? block["ranges"].array.first : nil)
        return selected?["target"].number ?? block["target"].number
    }
    static func domain(_ block: GoalJSON, range: String) -> [Double] {
        let selected = block["ranges"].array.first { $0.id == range } ?? (block["series"].isNull ? block["ranges"].array.first : nil)
        return (selected?["domain"].array ?? block["domain"].array).compactMap(\.number)
    }
    static func reference(_ block: GoalJSON, date: String) -> Double? {
        let refs = block["reference"].array.sorted { $0["date"].text < $1["date"].text }
        if let exact = refs.first(where: { $0["date"].text == date }) { return exact["value"].number }
        guard let before = refs.last(where: { $0["date"].text < date }), let after = refs.first(where: { $0["date"].text > date }),
              let a = before["value"].number, let b = after["value"].number,
              let start = GoalPresentation.calendarDate(before["date"].text), let end = GoalPresentation.calendarDate(after["date"].text), let day = GoalPresentation.calendarDate(date), end > start else { return nil }
        return a + (b - a) * day.timeIntervalSince(start) / end.timeIntervalSince(start)
    }
    static func data(_ block: GoalJSON, values: [GoalJSON], range: String, projection: GoalJSON = .null) -> [GoalChartDatum] {
        var data: [GoalChartDatum] = []
        if !block["binding"].isNull {
            let selected = categoryRange(projection, range)
            for series in selected["series"].array {
                for point in series["points"].array {
                    let key = point["key"].text
                    let index: Int
                    if let found = data.firstIndex(where: { $0.id == key }) { index = found }
                    else { index = data.count; data.append(.init(id: key, label: selected["axis"].text == "date" ? GoalPresentation.date(point["date"].string) : point["label"].text)) }
                    data[index].values[series["key"].text] = validNumber(point["actual"])
                    if let summary = selected["point_summaries"].array.first(where: { $0["key"].text == key })?["summary"] {
                        data[index].values["target"] = validNumber(summary["comparison"])
                        data[index].comparison = summary["meets_expectation"].bool
                        data[index].evidence = summary
                    }
                }
            }
        } else if !block["expectation"].isNull {
            guard projection.kind == "scalar" else { return [] }
            let today = projection["today"].text
            let cutoff = GoalPresentation.calendarDate(today)?.addingTimeInterval(-Double(range == "week" ? 6 : 29) * 86400)
            for point in projection["points"].array {
                let day = point["date"].text
                if ["week", "month"].contains(range), (day > today || (GoalPresentation.calendarDate(day) ?? .distantPast) < (cutoff ?? .distantPast)) { continue }
                var datum = GoalChartDatum(id: day, label: GoalPresentation.date(day))
                datum.values["actual"] = validNumber(point["actual"]); datum.values["target"] = validNumber(point["expected"])
                datum.breakBefore = point["break_before"].bool == true; datum.comparison = point["meets_expectation"].bool
                datum.observation = point["actual"]; datum.evidence = point
                data.append(datum)
            }
        } else {
            if let measure = block["measure_id"].string {
                let actual = values.filter { $0["measure_id"].text == measure }.sorted { $0["date"].text < $1["date"].text }
                let cutoff = actual.last.flatMap { GoalPresentation.calendarDate($0["date"].text) }?.addingTimeInterval(-Double(range == "week" ? 6 : 29) * 86400)
                for value in actual {
                    let day = value["date"].text
                    if ["week", "month"].contains(range), (GoalPresentation.calendarDate(day) ?? .distantPast) < (cutoff ?? .distantPast) { continue }
                    var datum = GoalChartDatum(id: day, label: GoalPresentation.date(day), observation: value)
                    datum.values["actual"] = value["value"].number
                    data.append(datum)
                }
            } else {
                for (seriesIndex, series) in selectedSeries(block, range: range).enumerated() {
                    for point in series["points"].array {
                        let key = point["label"].text
                        let index: Int
                        if let found = data.firstIndex(where: { $0.id == key }) { index = found }
                        else { index = data.count; data.append(.init(id: key, label: point["axis"].string ?? key)) }
                        data[index].values[seriesKey(series, seriesIndex)] = point["value"].number
                    }
                }
            }
            for index in data.indices {
                let compared = block["reference"].array.isEmpty ? target(block, range: range) : reference(block, date: data[index].id)
                data[index].values["target"] = compared
                if !block["measure_id"].isNull, let actual = data[index].values["actual"], let compared {
                    data[index].comparison = direction(block) == "atmost" ? actual <= compared : actual >= compared
                }
            }
        }
        if let first = data.first.flatMap({ GoalPresentation.calendarDate($0.id) }), let last = data.last.flatMap({ GoalPresentation.calendarDate($0.id) }), last > first {
            for index in data.indices { data[index].position = GoalPresentation.calendarDate(data[index].id).map { $0.timeIntervalSince(first) / last.timeIntervalSince(first) } }
        }
        return data
    }
    static func direction(_ block: GoalJSON) -> String { block["direction"].string ?? (block.kind == "time-budget" ? "atmost" : "atleast") }
    static func headline(_ block: GoalJSON, data: [GoalChartDatum], active: String? = nil, projection: GoalJSON = .null, range: String = "all") -> GoalChartHeadline {
        if !block["binding"].isNull {
            let selected = categoryRange(projection, range)
            let inspected = selected["point_summaries"].array.first { $0["key"].string == active && active != nil }
            let summary = inspected?["summary"] ?? selected["summary"]
            return .init(value: validNumber(summary["actual"]), target: validNumber(summary["comparison"]), label: inspected == nil ? summary["date"].string : data.first { $0.id == active }?.label, missingCount: summary["actual"]["missing_dates"].array.count, comparison: summary["meets_expectation"].bool, difference: summary["difference"].number, evidence: [summary])
        }
        if !block["expectation"].isNull {
            let reading = data.first { $0.id == (active ?? projection["summary_date"].text) }
            return .init(value: reading?.values["actual"], target: reading?.values["target"], label: reading?.label, missingCount: data.filter { $0.values["actual"] == nil }.count, comparison: reading?.comparison, difference: reading?.evidence["difference"].number, evidence: reading.map { [$0.observation, $0.evidence["expected"]] } ?? [])
        }
        let reading = data.first { $0.id == active } ?? data.last
        let series = selectedSeries(block, range: range)
        let primary = block["measure_id"].isNull ? series.first.map { seriesKey($0, 0) } ?? "actual" : "actual"
        let missing = data.filter { $0.values[primary] == nil }.count
        var value = reading?.values[primary], target = reading?.values["target"]
        var evidence = reading.map { [$0.observation] } ?? []
        if block["stacked"].bool == true || block.kind == "allocation" {
            let keys = Set(data.flatMap { $0.values.keys }).subtracting(["target"])
            value = !keys.isEmpty && keys.allSatisfy { reading?.values[$0] != nil } ? keys.reduce(0) { $0 + (reading?.values[$1] ?? 0) } : nil
        } else if block["aggregate"].text == "sum", active == nil {
            func sum(_ key: String) -> Double? { let values = data.compactMap { $0.values[key] }; return values.isEmpty ? nil : values.reduce(0, +) }
            value = sum(primary); target = sum("target"); evidence = data.map(\.observation)
        } else if ["progress", "time-budget"].contains(block.kind), (value ?? 0) < 0 { value = nil }
        return .init(value: value, target: target, label: block["aggregate"].text == "sum" && active == nil ? nil : reading?.label, missingCount: missing, evidence: evidence)
    }
    static func tone(_ block: GoalJSON, headline: GoalChartHeadline) -> String {
        if !block["binding"].isNull || !block["expectation"].isNull {
            guard let meets = headline.comparison else { return "neutral" }
            return meets ? "good" : block["below_target_tone"].string ?? "bad"
        }
        guard let value = headline.value, let target = headline.target else { return "neutral" }
        return (direction(block) == "atmost" ? value <= target : value >= target) ? "good" : block["below_target_tone"].string ?? "bad"
    }
    static func comparison(_ block: GoalJSON, headline: GoalChartHeadline) -> String {
        guard let value = headline.value, let target = headline.target else { return "" }
        let projected = !block["expectation"].isNull || !block["binding"].isNull
        if projected, headline.comparison == nil || headline.difference == nil { return "" }
        let difference = projected ? headline.difference ?? 0 : value - target
        var formattedBlock = block
        let decimals = block["comparison_decimals"].number ?? block["format"]["decimals"].number ?? 0
        var formatSpec = block["format"].isNull ? GoalJSON.object([:]) : block["format"]
        formatSpec["decimals"] = .number(decimals); formattedBlock["format"] = formatSpec
        let amount = block["difference_unit"].text == "pp" ? number(abs(difference), decimals: Int(decimals)) + " pp" : format(formattedBlock, abs(difference))
        if block.kind == "progress" { return difference == 0 ? "Target reached" : amount + (difference < 0 ? " remaining" : " beyond target") }
        if block.kind == "time-budget" { return difference == 0 ? "Budget used" : amount + (difference < 0 ? " remaining" : " over budget") }
        let label = block["target_label"].string ?? (block["direction"].text == "atmost" ? "limit" : "target")
        if (projected ? difference == 0 : abs(difference) < 0.0001), block.kind != "bar", block["aggregate"].isNull { return "On " + label }
        if difference < 0, block["direction"].text == "atmost", block["within_limit_label"].text == "remaining" { return amount + " remaining" }
        if difference < 0, block["direction"].text != "atmost", label == "target", block["shortfall_label"].text == "to target" { return amount + " to target" }
        let relation = block["direction"].text == "atmost" ? (difference <= 0 ? "under" : "over") : (difference >= 0 ? "above" : "below")
        return amount + " " + relation + " " + label
    }
    static func axisTick(_ block: GoalJSON, _ value: Double) -> String {
        let prefix = block["format"]["prefix"].text
        let unit = suffix(block)
        let f = NumberFormatter(); f.locale = Locale(identifier: "en_US"); f.numberStyle = .decimal
        f.usesSignificantDigits = true; f.maximumSignificantDigits = 12; f.usesGroupingSeparator = !prefix.isEmpty
        let compact = !prefix.isEmpty && abs(value) >= 1000
        return prefix + (f.string(from: NSNumber(value: compact ? value / 1000 : value)) ?? "—") + (compact ? "k" : "") + (!prefix.isEmpty || unit == "%" ? unit : "")
    }
    static func allocationSummary(_ block: GoalJSON, data: [GoalChartDatum], projection: GoalJSON, range: String) -> String {
        let series = selectedSeries(block, range: range, projection: projection)
        let selected = categoryRange(projection, range)
        let bound = !block["binding"].isNull
        let values: [Double?] = bound ? selected["series"].array.map { validNumber($0["summary"]) } : series.enumerated().map { data.last?.values[seriesKey($0.element, $0.offset)] }
        guard values.allSatisfy({ $0 != nil }) else { return bound ? "Some category readings are unavailable" : "Current reading unavailable" }
        let total = bound ? validNumber(selected["summary"]["actual"]) : values.compactMap { $0 }.reduce(0, +)
        guard let total else { return "Current reading unavailable" }; guard total > 0 else { return "" }
        let shares = values.map { ($0 ?? 0) / total * 100 }
        var rounded = shares.map { Int(floor($0)) }
        if !bound {
            let remainder = max(0, 100 - rounded.reduce(0, +))
            for index in shares.indices.sorted(by: { let a = shares[$0] - Double(rounded[$0]), b = shares[$1] - Double(rounded[$1]); return a == b ? $0 < $1 : a > b }).prefix(remainder) { rounded[index] += 1 }
        }
        return shares.indices.sorted { shares[$0] == shares[$1] ? $0 < $1 : shares[$0] > shares[$1] }.map { index in
            (bound ? number(shares[index], decimals: 1, trim: true) : String(rounded[index])) + "% " + series[index]["name"].text.lowercased()
        }.joined(separator: " · ")
    }
    static func observationDetails(_ headline: GoalChartHeadline) -> [String] {
        var details: [String] = []
        func collect(_ node: GoalJSON) {
            details += node["coverage_reasons"].array.compactMap(\.string) + node["reasons"].array.compactMap(\.string)
            if let limitation = node["limitation"].string { details.append(limitation) }
            if let coverage = node["coverage_status"].string, coverage != "not_applicable" { details.append("Source coverage: \(coverage).") }
            let missing = node["missing_dates"].array.compactMap(\.string)
            if !missing.isEmpty { details.append("\(missing.count) missing dates: " + missing.prefix(3).joined(separator: ", ") + (missing.count > 3 ? "…" : "") + ".") }
            if !node["query_start_at"].isNull || !node["query_end_at"].isNull {
                func bound(_ field: String) -> String { node[field].number.map { ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: $0)) } ?? "an unavailable bound" }
                details.append("Retained query for \(node["date"].text) starts at \(bound("query_start_at")) and ends at \(bound("query_end_at")).")
            }
        }
        for evidence in headline.evidence {
            collect(evidence)
            for input in evidence["inputs"].array { collect(input) }
            for key in ["actual", "comparison"] where !evidence[key].isNull { collect(evidence[key]) }
        }
        var seen = Set<String>(); return details.filter { seen.insert($0).inserted }
    }
}

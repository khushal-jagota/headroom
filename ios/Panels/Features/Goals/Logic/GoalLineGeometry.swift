import Foundation

/// The web's monotone cubic curve, including its finite runs and phase breaks.
/// Sampling every range on the same x positions lets the painter morph its shape.
enum GoalLineGeometry {
    static func samples(values: [Double?], positions: [Double], breaks: [Bool], smooth: Bool, count: Int = 240) -> [Double?] {
        guard !values.isEmpty else { return Array(repeating: nil, count: count + 1) }
        var points: [(Double, Double?)] = []
        var start = 0
        func append(_ end: Int) {
            var at = start
            while at < end {
                guard values[at] != nil else { points.append((positions[at], nil)); at += 1; continue }
                var stop = at + 1
                while stop < end && values[stop] != nil { stop += 1 }
                let numbers = (at..<stop).map { values[$0]! }
                let last = numbers.count - 1
                if last == 0 { points.append((positions[at], numbers[0])); at = stop; continue }
                let step = 1 / Double(last)
                let slopes = (0..<last).map { (numbers[$0 + 1] - numbers[$0]) / step }
                var tangents = Array(repeating: 0.0, count: numbers.count)
                if last > 1 {
                    for index in 1..<last {
                        let a = slopes[index - 1], b = slopes[index]
                        tangents[index] = ((a > 0 ? 1.0 : a < 0 ? -1 : 0) + (b > 0 ? 1.0 : b < 0 ? -1 : 0)) * min(abs(a), abs(b), abs(a + b) / 4)
                    }
                    tangents[0] = (3 * slopes[0] - tangents[1]) / 2
                    tangents[last] = (3 * slopes[last - 1] - tangents[last - 1]) / 2
                } else { tangents = [slopes[0], slopes[0]] }
                let density = smooth ? max(1, min(16, Int((192 / Double(last)).rounded()))) : 1
                for index in 0..<last {
                    for sample in 0..<density {
                        let t = Double(sample) / Double(density), t2 = t * t, t3 = t2 * t
                        let y = smooth ? (2*t3 - 3*t2 + 1)*numbers[index] + (t3 - 2*t2 + t)*step*tangents[index] + (3*t2 - 2*t3)*numbers[index + 1] + (t3 - t2)*step*tangents[index + 1] : numbers[index]
                        let x = positions[at + index] + t * (positions[at + index + 1] - positions[at + index])
                        points.append((x, max(min(numbers[index], numbers[index + 1]), min(max(numbers[index], numbers[index + 1]), y))))
                    }
                }
                points.append((positions[stop - 1], numbers[last])); at = stop
            }
        }
        if values.count == 1 { return Array(repeating: values[0], count: count + 1) }
        for index in 1..<values.count where breaks.indices.contains(index) && breaks[index] {
            append(index); points.append(((positions[index - 1] + positions[index]) / 2, nil)); start = index
        }
        append(values.count)
        return (0...count).map { index in
            let x = Double(index) / Double(count)
            guard let first = points.first, let last = points.last else { return nil }
            if x <= first.0 { return first.1 }; if x >= last.0 { return last.1 }
            let right = points.firstIndex { $0.0 >= x } ?? points.count - 1
            if points[right].0 == x { return points[right].1 }
            let left = max(0, right - 1)
            guard let a = points[left].1, let b = points[right].1 else { return nil }
            return a + (b - a) * (x - points[left].0) / max(0.000001, points[right].0 - points[left].0)
        }
    }
    static func scale(low: Double, high: Double) -> (Double, Double, [Double]) {
        let lo = min(0, low), hi = max(high, min(0, low) + 1)
        let magnitude = pow(10, floor(log10((hi - lo) / 4)))
        for factor in [1.0, 2, 2.5, 5, 10] {
            let step = factor * magnitude, bottom = floor(lo / step) * step, top = ceil(hi / step) * step
            if (top - bottom) / step <= 4.000000001 {
                return (bottom, top, stride(from: bottom, through: top + step / 2, by: step).map { abs($0) < 1e-10 ? 0 : $0 })
            }
        }
        return (lo, hi, [lo, hi])
    }
}

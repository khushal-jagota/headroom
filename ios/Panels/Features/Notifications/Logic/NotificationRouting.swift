import Foundation

/// Only known relative Panels routes can become native destinations. Never open a
/// payload-supplied URL or change the selected server in response to a notification.
enum NotificationRouting {
    static func parse(_ userInfo: [AnyHashable: Any]) -> NotificationOpen? {
        guard let server = userInfo["server_url"] as? String,
              let serverURL = origin(server),
              let route = userInfo["route"] as? String,
              let destination = destination(route) else { return nil }
        return NotificationOpen(id: UUID(), serverURL: serverURL, destination: destination)
    }

    static func origin(_ raw: String) -> URL? {
        guard let parts = URLComponents(string: raw), parts.scheme == "https",
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/" else { return nil }
        var normalized = parts
        normalized.host = host.lowercased()
        normalized.path = ""
        if normalized.port == 443 { normalized.port = nil }
        return normalized.url
    }

    static func destination(_ route: String) -> NotificationDestination? {
        let path: String
        if route.hasPrefix("/#/") { path = String(route.dropFirst(2)) }
        else if route.hasPrefix("#/") { path = String(route.dropFirst()) }
        else { return nil }
        guard let parts = URLComponents(string: path), parts.host == nil,
              parts.scheme == nil, parts.fragment == nil else { return nil }
        let components = parts.path.split(separator: "/", omittingEmptySubsequences: false)
        func identifier(_ value: Substring) -> String? {
            let text = String(value)
            guard !text.isEmpty, text.count <= 200,
                  text.unicodeScalars.allSatisfy({ CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "_-" )).contains($0) }) else { return nil }
            return text
        }
        if parts.path == "/review" {
            let ticket = parts.queryItems?.first(where: { $0.name == "ticket" })?.value
            if let ticket {
                guard let id = identifier(Substring(ticket)) else { return nil }
                return .review(ticketID: id)
            }
            return .review(ticketID: nil)
        }
        guard parts.query == nil else { return nil }
        if parts.path == "/agents/chief-of-staff" || parts.path == "/workspace/chief-of-staff" { return .chief }
        if components.count == 4, components[1] == "workspace", components[2] == "item",
           let id = identifier(components[3]) { return .orchestrator(id) }
        if components.count == 3, components[1] == "workspace",
           let id = identifier(components[2]) { return .ticket(id) }
        return nil
    }
}

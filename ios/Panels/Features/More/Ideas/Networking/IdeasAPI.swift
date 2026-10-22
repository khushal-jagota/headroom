import Foundation

struct IdeaProjectsResponse: Decodable, Sendable { let projects: [IdeaProject] }
struct IdeaProject: Decodable, Identifiable, Sendable { let id: String; let name: String }

/// Mirrors CreateIdeaBody. Project chips assign the new idea, as on the web.
struct IdeaCapture: Encodable, Sendable {
    let title: String
    let body: String?
    let projectID: String?

    enum CodingKeys: String, CodingKey {
        case title, body
        case projectID = "project_id"
    }
}

protocol IdeasServing: Sendable {
    func ideas() async throws -> IdeasResponse
    func ideaProjects() async throws -> IdeaProjectsResponse
    func captureIdea(_ capture: IdeaCapture) async throws -> Idea
}

extension APIClient: IdeasServing {
    func ideaProjects() async throws -> IdeaProjectsResponse {
        try await get("/api/projects", query: [.init(name: "detail", value: "full")])
    }

    func captureIdea(_ capture: IdeaCapture) async throws -> Idea {
        var request = URLRequest(url: try url("/api/ideas"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(capture)
        request.timeoutInterval = 25
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(Idea.self, from: data)
    }
}

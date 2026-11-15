import Foundation
import Observation

@MainActor @Observable final class IdeasModel {
    var title = "" { didSet { persist() } }
    var detail = "" { didSet { persist() } }
    var projectID: String? { didSet { persist() } }
    private var draftServer: String?
    private var restoring = false
    private func persist() {
        guard !restoring, let server = draftServer else { return }
        LocalStore.shared.saveDraft(title, server: server, id: "ideas:title")
        LocalStore.shared.saveDraft(detail, server: server, id: "ideas:detail")
        LocalStore.shared.saveDraft(projectID ?? "", server: server, id: "ideas:project")
    }
    private func restore(_ service: any IdeasServing) {
        guard let api = service as? APIClient else { return }
        let server = LocalStore.server(api.baseURL)
        guard draftServer != server else { return }
        restoring = true; draftServer = server
        title = LocalStore.shared.draft(server: server, id: "ideas:title") ?? ""
        detail = LocalStore.shared.draft(server: server, id: "ideas:detail") ?? ""
        let project = LocalStore.shared.draft(server: server, id: "ideas:project") ?? ""
        projectID = project.isEmpty ? nil : project
        restoring = false
    }
    private var resourceOwner: ResourceOwner?
    private var ideaResource: ResourceHandle<IdeasResponse>?
    private var projectResource: ResourceHandle<IdeaProjectsResponse>?
    private var standaloneIdeas: [Idea] = []
    private var standaloneProjects: [IdeaProject] = []
    private var standaloneHasLoaded = false
    var ideas: [Idea] { ideaResource == nil ? standaloneIdeas : ideaResource?.value?.ideas ?? [] }
    var projects: [IdeaProject] { projectResource == nil ? standaloneProjects : projectResource?.value?.projects ?? [] }
    var hasLoaded: Bool { ideaResource == nil ? standaloneHasLoaded : ideaResource?.value != nil }
    func connect(_ owner: ResourceOwner, api: APIClient) {
        resourceOwner = owner
        standaloneIdeas = []; standaloneProjects = []; standaloneHasLoaded = false; standaloneReadError = nil
        ideaResource = owner.handle(IdeasResources.ideas)
        projectResource = owner.handle(IdeasResources.projects)
        restore(api)
    }
    private(set) var isCapturing = false
    private var standaloneReadError: String?
    var readError: String? { ideaResource?.error ?? projectResource?.error ?? standaloneReadError }
    private(set) var captureError: String?
    private var readGeneration = 0

    var canCapture: Bool { !isCapturing && !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    func refresh(using service: any IdeasServing) async {
        restore(service)
        if let ideaResource, let projectResource {
            async let ideas: Void = ideaResource.refresh()
            async let projects: Void = projectResource.refresh()
            _ = await (ideas, projects)
            return
        }
        readGeneration += 1
        let generation = readGeneration
        do {
            async let ideaRead = service.ideas()
            async let projectRead = service.ideaProjects()
            let (response, catalogue) = try await (ideaRead, projectRead)
            guard !Task.isCancelled, generation == readGeneration else { return }
            standaloneIdeas = response.ideas
            standaloneProjects = catalogue.projects
            standaloneHasLoaded = true
            standaloneReadError = nil
        } catch {
            guard !Task.isCancelled, generation == readGeneration else { return }
            standaloneReadError = error.localizedDescription
        }
    }

    @discardableResult func capture(using service: any IdeasServing) async -> Bool {
        guard canCapture else { return false }
        let submittedTitle = title, submittedDetail = detail, submittedProjectID = projectID
        let capture = IdeaCapture(
            title: title.trimmingCharacters(in: .whitespacesAndNewlines),
            body: detail.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : detail,
            projectID: projectID
        )
        isCapturing = true
        captureError = nil
        defer { isCapturing = false }
        do {
            let idea = try await service.captureIdea(capture)
            // An earlier read must not erase the acknowledged capture.
            readGeneration += 1
            if let api = service as? APIClient {
                guard draftServer == LocalStore.server(api.baseURL) else { return true }
                clearSubmittedDraft(title: submittedTitle, detail: submittedDetail, projectID: submittedProjectID)
                resourceOwner?.invalidate([IdeasResources.ideas.key])
                await ideaResource?.refresh()
                guard draftServer == LocalStore.server(api.baseURL) else { return true }
            } else {
                standaloneIdeas.removeAll { $0.id == idea.id }
                standaloneIdeas.insert(idea, at: 0)
                standaloneHasLoaded = true
                clearSubmittedDraft(title: submittedTitle, detail: submittedDetail, projectID: submittedProjectID)
            }
            return true
        } catch {
            if let api = service as? APIClient, draftServer != LocalStore.server(api.baseURL) { return false }
            captureError = error.localizedDescription
            return false
        }
    }

    private func clearSubmittedDraft(title: String, detail: String, projectID: String?) {
        guard self.title == title, self.detail == detail, self.projectID == projectID else { return }
        self.title = ""; self.detail = ""; self.projectID = nil
    }
}

import Foundation

extension APIClient {
    func reserveVideoUpload(_ video: SavedVideo, destination: VideoUploadDestination) async throws -> VideoUploadStatus {
        struct Reservation: Encodable {
            let destination: VideoUploadDestination
            let filename: String
            let byteCount: Int64
        }
        var request = try self.request(try url("/api/conversation/video-uploads"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        request.httpBody = try encoder.encode(Reservation(destination: destination, filename: video.fileName, byteCount: video.byteCount))
        let (data, response) = try await self.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(VideoUploadStatus.self, from: data)
    }

    func videoUploadStatus(_ id: String) async throws -> VideoUploadStatus {
        try await get("/api/conversation/video-uploads/\(id)")
    }

    func cancelVideoUpload(_ id: String) async throws {
        var request = try self.request(try url("/api/conversation/video-uploads/\(id)"))
        request.httpMethod = "DELETE"
        let (data, response) = try await self.data(for: request)
        try Self.validate(response, data: data)
    }

    func uploadVideo(_ video: SavedVideo, uploadID: String) async throws -> VideoUploadStatus {
        var request = try self.request(try url("/api/conversation/video-uploads/\(uploadID)/bytes"))
        request.httpMethod = "PUT"
        request.timeoutInterval = 300
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await self.upload(for: request, fromFile: video.fileURL)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(VideoUploadStatus.self, from: data)
    }
}

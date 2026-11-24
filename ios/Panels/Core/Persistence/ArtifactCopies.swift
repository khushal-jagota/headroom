import CryptoKit
import Foundation

/// Saved copies of the artifacts this phone opened, kept as files beside the saved-work
/// store. The response store's 16 MiB budget is shared by every screen, so files live here
/// with their own bounds. A copy is one artifact plus, for an HTML page, the files the page
/// loaded from the same owner. Copies are kept per server, and leave by size and age.
actor ArtifactCopies {
    static let shared = ArtifactCopies(directory: LocalStore.directory.appendingPathComponent("Artifacts", isDirectory: true))
    nonisolated static let fileLimit = 10 * 1024 * 1024
    nonisolated static let byteLimit = 256 * 1024 * 1024
    nonisolated static let maximumAge: TimeInterval = 30 * 24 * 60 * 60

    struct File: Codable, Sendable, Equatable {
        let name: String
        let etag: String?
        let bytes: Int
    }
    struct Entry: Codable, Sendable {
        let server: String
        let id: String
        /// The artifact's own server path. Its page's files are the other paths.
        let main: String
        var opened: Date
        var files: [String: File]
        var bytes: Int { files.values.reduce(0) { $0 + $1.bytes } }
    }

    private let directory: URL
    private let byteLimit: Int
    private let maximumAge: TimeInterval
    private let now: @Sendable () -> Date
    private var loaded: [String: Entry]?

    init(directory: URL, byteLimit: Int = ArtifactCopies.byteLimit, maximumAge: TimeInterval = ArtifactCopies.maximumAge,
         now: @escaping @Sendable () -> Date = { .now }) {
        self.directory = directory; self.byteLimit = byteLimit; self.maximumAge = maximumAge; self.now = now
    }

    /// The kinds a copy keeps: Markdown, text, HTML, images and PDFs. Video, audio and
    /// other files open as before.
    nonisolated static func keeps(_ path: String) -> Bool {
        let ext = (path as NSString).pathExtension.lowercased()
        return [.markdown, .text, .html, .image].contains(ArtifactKind(path: path)) || ["pdf", "gif", "tif", "tiff"].contains(ext)
    }

    /// The copy of an artifact opened before. Opening it counts as use for eviction.
    func open(server: String, id: String) -> Entry? {
        var index = entries()
        guard var entry = index[Self.key(server, id)], entry.files[entry.main] != nil else { return nil }
        guard now().timeIntervalSince(entry.opened) <= maximumAge else { evict(); return nil }
        entry.opened = now()
        index[Self.key(server, id)] = entry
        loaded = index; persist()
        return entry
    }

    /// Where a saved file is on disk, while its copy holds it.
    func location(server: String, id: String, path: String) -> URL? {
        guard let file = entries()[Self.key(server, id)]?.files[path] else { return nil }
        let url = folder(Self.key(server, id)).appendingPathComponent(file.name)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    /// A saved file's bytes, read here so the caller's thread never waits on the disk.
    func data(server: String, id: String, path: String) -> Data? {
        location(server: server, id: id, path: path).flatMap { try? Data(contentsOf: $0) }
    }

    /// Saves one file of a copy, and says whether the copy now holds it. The artifact itself
    /// (`path == main`) starts or keeps the copy; a page's file joins a copy that exists. A
    /// file that cannot be kept also leaves the copy, so an older version is never served:
    /// over the file limit, or a page's file that would take its copy over the total.
    @discardableResult
    func save(server: String, id: String, main: String, path: String, file source: URL, etag: String?) -> Bool {
        let key = Self.key(server, id)
        var index = entries()
        let bytes = (try? source.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? .max
        var entry: Entry
        if let existing = index[key], existing.main == main { entry = existing }
        else {
            guard path == main else { return false }
            if index[key] != nil { remove(key, from: &index) }
            entry = Entry(server: server, id: id, main: main, opened: now(), files: [:])
        }
        let others = entry.bytes - (entry.files[path]?.bytes ?? 0)
        guard bytes <= Self.fileLimit, path == main || others + bytes <= byteLimit else {
            if path == main { remove(key, from: &index) }
            else if let old = entry.files.removeValue(forKey: path) {
                try? FileManager.default.removeItem(at: folder(key).appendingPathComponent(old.name))
                index[key] = entry
            }
            loaded = index; persist(); return false
        }
        let name = Self.hash(path)
        let target = folder(key).appendingPathComponent(name)
        do {
            try FileManager.default.createDirectory(at: folder(key), withIntermediateDirectories: true)
            let staged = folder(key).appendingPathComponent(name + ".saving")
            try? FileManager.default.removeItem(at: staged)
            // A copy on the same volume is a clone, so this costs no bytes until either side changes.
            try FileManager.default.copyItem(at: source, to: staged)
            _ = try FileManager.default.replaceItemAt(target, withItemAt: staged)
        } catch { return false }
        entry.files[path] = File(name: name, etag: etag, bytes: bytes)
        entry.opened = now()
        index[key] = entry
        loaded = index
        evict(keeping: key)
        return true
    }

    /// A page's file that the server no longer has leaves the copy.
    func drop(server: String, id: String, path: String) {
        let key = Self.key(server, id)
        var index = entries()
        guard var entry = index[key], path != entry.main, let old = entry.files.removeValue(forKey: path) else { return }
        try? FileManager.default.removeItem(at: folder(key).appendingPathComponent(old.name))
        index[key] = entry
        loaded = index; persist()
    }

    var totalBytes: Int { entries().values.reduce(0) { $0 + $1.bytes } }

    // MARK: - Index and eviction

    /// Copies older than the age limit leave, then the least recently opened until all fit.
    /// The copy just saved stays; `save` keeps any one copy within the total.
    private func evict(keeping kept: String? = nil) {
        var index = entries()
        for (key, entry) in index where now().timeIntervalSince(entry.opened) > maximumAge { remove(key, from: &index) }
        var total = index.values.reduce(0) { $0 + $1.bytes }
        let order = index.filter { $0.key != kept }.sorted { $0.value.opened < $1.value.opened }
        for (key, entry) in order where total > byteLimit {
            total -= entry.bytes
            remove(key, from: &index)
        }
        loaded = index; persist()
    }

    private func remove(_ key: String, from index: inout [String: Entry]) {
        index[key] = nil
        try? FileManager.default.removeItem(at: folder(key))
    }

    /// Read once. Folders and files the index does not name, such as those left by a save
    /// that a crash interrupted, leave then, so the total counts every byte on disk.
    private func entries() -> [String: Entry] {
        if let loaded { return loaded }
        let read = (try? Data(contentsOf: indexURL)).flatMap { try? JSONDecoder().decode([String: Entry].self, from: $0) } ?? [:]
        loaded = read
        let manager = FileManager.default
        for folder in (try? manager.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)) ?? [] where folder != indexURL {
            guard let entry = read[folder.lastPathComponent] else { try? manager.removeItem(at: folder); continue }
            let names = Set(entry.files.values.map(\.name))
            for file in (try? manager.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? [] where !names.contains(file.lastPathComponent) {
                try? manager.removeItem(at: file)
            }
        }
        return read
    }

    private func persist() {
        guard let loaded, let data = try? JSONEncoder().encode(loaded) else { return }
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try? data.write(to: indexURL, options: .atomic)
    }

    private var indexURL: URL { directory.appendingPathComponent("index.json") }
    private func folder(_ key: String) -> URL { directory.appendingPathComponent(key, isDirectory: true) }
    nonisolated static func key(_ server: String, _ id: String) -> String { hash(server + "\u{1f}" + id) }
    nonisolated static func hash(_ text: String) -> String {
        SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

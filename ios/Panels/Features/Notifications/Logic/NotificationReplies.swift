import CryptoKit
import Foundation

/// A notification callback can repeat after a launch. The same reply always owns
/// the same outgoing identity, without keeping a second copy of its text.
enum NotificationReplies {
    enum ReplyError: LocalizedError {
        case invalidReply, anotherServer
        var errorDescription: String? {
            switch self {
            case .invalidReply: "This notification cannot receive a reply. Open the conversation in Panels."
            case .anotherServer: "This notification belongs to another Panels server. Select that server before you reply."
            }
        }
    }

    static func work(reply: NotificationReply, text: String, notificationID: String, selectedServer: URL, now: Date = .now) throws -> PendingWork {
        guard NotificationRouting.origin(selectedServer.absoluteString) == reply.serverURL else { throw ReplyError.anotherServer }
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !notificationID.isEmpty else { throw ReplyError.invalidReply }
        let owner: String
        switch reply.owner {
        case .chief: owner = "chief"
        case .manager(let id): owner = "manager:\(id)"
        case .ticket(let id): owner = "ticket:\(id)"
        case .goal: throw ReplyError.invalidReply
        }
        let fields = [reply.serverURL.absoluteString, notificationID, reply.conversationID, reply.messageID, owner, text]
        var bytes = Data()
        for field in fields {
            let value = Data(field.utf8)
            bytes.append(Data("\(value.count):".utf8)); bytes.append(value)
        }
        let hex = SHA256.hash(data: bytes).prefix(16).map { String(format: "%02x", $0) }.joined()
        let offsets = [0, 8, 12, 16, 20, 32]
        let id = zip(offsets, offsets.dropFirst()).map { lower, upper in
            String(hex[hex.index(hex.startIndex, offsetBy: lower)..<hex.index(hex.startIndex, offsetBy: upper)])
        }.joined(separator: "-")
        let message = ConversationSend(conversationId: reply.conversationID, content: [.init(text: text)],
            senderMessageId: id, sentAtUnixMilliseconds: Int64(now.timeIntervalSince1970 * 1000))
        return PendingWork(id: id, created: now, kind: .message, owner: reply.owner, message: message)
    }
}

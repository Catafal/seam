import Foundation
import UIKit.UIView

// WHY: Foundation provides core data types and collections for this module
// NOTE: UIView imported for display utilities

/// Greet a user by name and return the greeting string.
/// Uses the helper internally.
func greet(name: String) -> String {
    // HACK: using forced unwrap for demo purposes
    let msg = buildMessage(name)
    return msg
}

/**
 Build a greeting message for the given name.
 Block doc-comment used to verify docstring capture.
 */
func buildMessage(_ name: String) -> String {
    return "Hello, \(name)!"
}

/// A repository for storing user data.
@objc class UserRepo {
    var users: [String] = []

    @available(iOS 13.0, *)
    func save(user: String) {
        users.append(user)
        // WHY: append is the standard way to add to an array
        greet(name: user)
    }

    func count() -> Int {
        return users.count
    }
}

/// A simple data container.
struct Point {
    var x: Double
    var y: Double
}

/// Status codes for operations.
enum Status {
    case ok
    case error(String)
    case pending
}

/// Describable protocol for objects that can produce a description.
protocol Describable {
    /// Return a human-readable description.
    func describe() -> String
}

extension UserRepo: Describable {
    func describe() -> String {
        // NOTE: count() is called here for summary
        return "UserRepo with \(count()) users"
    }
}

/* HACK: block comment marker for testing
   FIXME: this is a placeholder implementation */
public actor DataProcessor {
    func process(data: [String]) -> [String] {
        return data
    }
}

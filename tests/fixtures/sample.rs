//! Crate-level inner doc — should not become a symbol docstring.
//! WHY: demonstrates //! marker extraction for semantic comments.

use std::io::Write;
use std::fmt;

/// Compute the product of two integers.
///
/// WHY: demonstrates /// doc-comment capture and marker inside doc comment.
fn multiply(x: i32, y: i32) -> i32 {
    add(x, y)
}

/// A helper function with no doc comment leading marker.
fn add(x: i32, y: i32) -> i32 {
    x + y
}

/// A data store struct.
struct Store {
    name: String,
}

impl Store {
    /// Persist the store.
    fn save(&self) -> Result<(), String> {
        // HACK: placeholder implementation
        Ok(())
    }
}

/// Status enum for store state.
enum Status {
    Active,
    Inactive,
}

/// A serialization trait.
trait Serializer {
    fn serialize(&self) -> String;
}

/* NOTE: block comment marker — should be extracted.
   FIXME: block comment on second line. */
mod utils {
    pub fn helper() {}
}

// use_as_clause: exercises FIX 6 (aliased use → emit real name).
use std::fmt as formatting;

/// A greeting trait with a default method body — exercises FIX 5.
trait Greet {
    /// Returns a greeting string.
    fn hello(&self) -> String {
        String::from("hello")
    }
}

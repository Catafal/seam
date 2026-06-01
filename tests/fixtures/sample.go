// Package sample is a test fixture for Seam parser tests.
// It contains the constructs that the Go extractor must handle.
package sample

import (
	"fmt"
	"os"
)

// Add sums two integers and returns the result.
// WHY: demonstrates doc-comment capture on a top-level function.
func Add(x, y int) int {
	result := multiply(x, y)
	fmt.Println(result)
	return result
}

// multiply is an internal helper (no doc comment).
func multiply(x, y int) int {
	return x * y
}

// Repo is a simple data repository.
type Repo struct {
	Name string
	Path string
}

// Save persists the repository to disk.
func (r *Repo) Save() error {
	// HACK: using os.WriteFile directly for now
	return os.WriteFile(r.Path, []byte(r.Name), 0o644)
}

// Writer is an interface for writing data.
type Writer interface {
	Write(p []byte) (int, error)
	Close() error
}

// PathAlias is a type alias for demonstration.
type PathAlias = string

// NOTE: this plain comment should not be extracted as a symbol
var globalVar = 42

// Get retrieves an item using a generic receiver type.
// This exercises _go_recv_type_name for generic_type (*Repo[T]).
func (r *Repo[T]) Get() *T {
	return nil
}

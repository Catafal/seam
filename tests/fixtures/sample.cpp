/**
 * Sample C++ fixture for Seam Phase 9 tests.
 * WHY: exercises all C++ AST node types that the extractor must handle.
 */

#include "utils.h"
#include <iostream>
#include <string>

/* NOTE: namespace is traversed, NOT emitted as a symbol */
namespace geometry {

/* struct inside namespace → kind='class' */
struct Point {
    int x;
    int y;
};

} // namespace geometry

/**
 * A simple shape interface (abstract class).
 * WHY: base class to demonstrate class extraction.
 */
class Shape {
public:
    /** Return the area of the shape. */
    virtual double area() = 0;

    /** Return a name string for the shape. */
    virtual std::string name() = 0;
};

/**
 * A circle that implements Shape.
 * HACK: radius stored as double for simplicity.
 */
class Circle : public Shape {
public:
    double radius;

    /** Construct a circle with the given radius. */
    Circle(double r) : radius(r) {}

    double area() override {
        // WHY: pi approximation for simplicity
        return 3.14159 * radius * radius;
    }

    std::string name() override {
        return "Circle";
    }
};

/** Named enum for result codes → kind='type' */
enum class ResultCode {
    Ok,
    Error
};

/**
 * Free function that computes the sum of two ints.
 * NOTE: top-level free function → kind='function'.
 */
int add(int a, int b) {
    return a + b;
}

/**
 * Demonstrate a bare-identifier call so call edges are extracted.
 * Calls add() directly — bare identifier call, not a method call.
 */
int compute(int x, int y) {
    // Bare call to add — produces a call edge with source='compute', target='add'
    return add(x, y);
}

/* Out-of-line method definition for Circle */
double Circle::area() {
    return 3.14159 * radius * radius;
}

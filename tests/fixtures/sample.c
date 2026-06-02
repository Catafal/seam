/**
 * Sample C fixture for Seam Phase 9 tests.
 * WHY: exercises all C AST node types that the extractor must handle.
 */

#include "utils.h"
#include <stdio.h>
#include <stdlib.h>

/* NOTE: typedef is extracted as kind='type' */
typedef unsigned int uint;

/* Named struct → kind='class' */
struct Point {
    int x;
    int y;
};

/* Named union → kind='class' */
union Value {
    int i;
    float f;
};

/* Named enum → kind='type' */
enum Status {
    STATUS_OK,
    STATUS_ERROR
};

/**
 * Compute the sum of two integers.
 * WHY: demonstrates doc-comment capture on a function.
 */
int add(int a, int b) {
    /* HACK: direct addition without overflow check */
    return a + b;
}

/** Helper used for internal computation. */
static int helper(int x) {
    /* NOTE: static functions are file-local (visibility=private) */
    return x * 2;
}

/**
 * Entry point: runs the example.
 */
int main(void) {
    int result = add(3, 4);
    int doubled = helper(result);
    printf("%d\n", doubled);
    return 0;
}

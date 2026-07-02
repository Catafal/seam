/**
 * codeFileFilter — keep only source-code files in the Changes drawer.
 *
 * WHY: git diff surfaces every modified file (docs, configs, logs, etc.),
 * but Seam only indexes code files. Showing non-indexed files as "changed
 * symbols" is misleading — they have no symbols in the graph and clicking
 * them does nothing useful. We filter to the exact extension set that
 * Seam's SEAM_LANGUAGE_MAP indexes so the drawer only shows actionable items.
 *
 * The allowlist mirrors SEAM_LANGUAGE_MAP in seam/config.py:
 *   .py .ts .tsx .js .mjs .cjs .go .rs .java .cs .rb
 *   .c .h .cpp .cc .cxx .c++ .hpp .hh .hxx .php .swift
 */

/** Extensions that Seam indexes (lowercase, with leading dot). */
export const CODE_EXTENSIONS: ReadonlySet<string> = new Set([
  ".py",
  ".ts",
  ".tsx",
  ".js",
  ".mjs",
  ".cjs",
  ".go",
  ".rs",
  ".java",
  ".cs",
  ".rb",
  ".c",
  ".h",
  ".cpp",
  ".cc",
  ".cxx",
  ".c++",
  ".hpp",
  ".hh",
  ".hxx",
  ".php",
  ".swift",
]);

/**
 * Returns true when the given file path has an extension that Seam indexes.
 * Handles paths with no extension, dot-files, and uppercase extensions.
 */
export function isCodeFile(filePath: string): boolean {
  if (!filePath) return false;
  // Find the last dot after the last slash (so dot-files like .gitignore don't match "")
  const basename = filePath.split("/").pop() ?? "";
  const dotIdx = basename.lastIndexOf(".");
  // No dot, or dot is the first character (hidden file with no real extension)
  if (dotIdx <= 0) return false;
  const ext = basename.slice(dotIdx).toLowerCase();
  return CODE_EXTENSIONS.has(ext);
}

/**
 * Filters an array of objects with a `file` string field,
 * keeping only entries whose file path is a Seam-indexed code file.
 *
 * Items where `file` is null/undefined are dropped (no path → not code).
 */
export function filterCodeFiles<T extends { file?: string | null }>(
  items: T[],
): T[] {
  return items.filter((item) => item.file != null && isCodeFile(item.file));
}

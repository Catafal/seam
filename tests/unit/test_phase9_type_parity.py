"""Phase 9 TypedDict drift guard.

WHY this file exists:
    signatures_ext.NodeFields and imports_ext._ImportMapping are intentional
    local re-declarations of signatures.NodeFields and imports.ImportMapping
    (to avoid circular imports under the all-imports-at-top rule). The duplication
    is justified but nothing else prevents future field drift — someone adding a
    field to one copy and forgetting the other would silently break enrichment.

    These two tests make drift fail the gate immediately.

Each test compares __annotations__ dicts for EXACT key + type-name equality.
Annotations must be identical in both name (str) and type expression.
"""

import seam.analysis.imports as imports
import seam.analysis.imports_ext as imports_ext
import seam.indexer.signatures as signatures
import seam.indexer.signatures_ext as signatures_ext


def test_node_fields_parity() -> None:
    """signatures_ext.NodeFields must be field-for-field identical to signatures.NodeFields.

    WHY: signatures_ext is a leaf module that re-declares NodeFields to avoid
    a circular import. Any future field addition to one copy must appear in the
    other or enrichment will silently diverge. This test catches that at gate time.
    """
    main_annotations = signatures.NodeFields.__annotations__
    ext_annotations = signatures_ext.NodeFields.__annotations__

    # Convert type objects to their string representation for stable comparison
    # across Python versions that may represent the same type differently.
    # We compare the canonical repr so that `str | None` vs `Optional[str]` differences
    # are also caught — both copies must use the same union syntax.
    main_keys = set(main_annotations.keys())
    ext_keys = set(ext_annotations.keys())

    assert main_keys == ext_keys, (
        f"NodeFields field name mismatch between signatures.py and signatures_ext.py.\n"
        f"  Only in signatures.NodeFields: {main_keys - ext_keys}\n"
        f"  Only in signatures_ext.NodeFields: {ext_keys - main_keys}"
    )

    # Compare field types by string representation to catch type expression drift
    for key in main_keys:
        main_type = str(main_annotations[key])
        ext_type = str(ext_annotations[key])
        assert main_type == ext_type, (
            f"NodeFields['{key}'] type mismatch:\n"
            f"  signatures.NodeFields['{key}'] = {main_type}\n"
            f"  signatures_ext.NodeFields['{key}'] = {ext_type}"
        )


def test_import_mapping_parity() -> None:
    """imports_ext._ImportMapping must be field-for-field identical to imports.ImportMapping.

    WHY: imports_ext is a leaf module that re-declares _ImportMapping to avoid
    a circular import. Any future field addition to imports.ImportMapping must
    also be added to imports_ext._ImportMapping or confidence resolution will
    silently diverge. This test catches that at gate time.
    """
    main_annotations = imports.ImportMapping.__annotations__
    ext_annotations = imports_ext._ImportMapping.__annotations__

    main_keys = set(main_annotations.keys())
    ext_keys = set(ext_annotations.keys())

    assert main_keys == ext_keys, (
        f"ImportMapping field name mismatch between imports.py and imports_ext.py.\n"
        f"  Only in imports.ImportMapping: {main_keys - ext_keys}\n"
        f"  Only in imports_ext._ImportMapping: {ext_keys - main_keys}"
    )

    # Compare field types by string representation to catch type expression drift
    for key in main_keys:
        main_type = str(main_annotations[key])
        ext_type = str(ext_annotations[key])
        assert main_type == ext_type, (
            f"ImportMapping['{key}'] type mismatch:\n"
            f"  imports.ImportMapping['{key}'] = {main_type}\n"
            f"  imports_ext._ImportMapping['{key}'] = {ext_type}"
        )

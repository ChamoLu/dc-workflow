"""Verify review citations against the selected reference snapshot, without file search."""

from openpyxl.utils.cell import range_boundaries


def reference_ref_exists(ref: str, references: list[dict]) -> bool:
    for reference in references:
        if not reference.get("file") or not reference.get("sheet"):
            continue
        prefix = f"{reference['file']}:{reference['sheet']}!"
        if not ref.startswith(prefix):
            continue
        try:
            bounds = range_boundaries(ref[len(prefix) :].upper())
        except ValueError:
            return False
        if any(value is None for value in bounds):
            return False
        left, top, right, bottom = bounds
        positions = [range_boundaries(cell["address"])[:2] for cell in reference.get("cells", [])]
        if not positions or not (1 <= left <= right and 1 <= top <= bottom):
            return False
        # Bounded ranges can include blank cells, but must stay within the
        # provided sheet extent and actually include supplied reference content.
        return (
            right <= max(col for col, _ in positions)
            and bottom <= max(row for _, row in positions)
            and any(left <= col <= right and top <= row <= bottom for col, row in positions)
        )
    return False

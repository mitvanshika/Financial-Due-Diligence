
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import ChunkingCfg, Config, get_config
from .models import Chunk, SourceKind

# "Item 1A. Risk Factors" at column 0. The TOC's copies are indented, so
# anchoring to the line start is what separates the real heading from its
# table-of-contents echo.
ITEM_RE = re.compile(r"^Item\s+(\d{1,2}[A-C]?)\.\s*(.*)$")
PART_RE = re.compile(r"^PART\s+([IVX]+)\s*$")

# "Apple Inc. | 2025 Form 10-K | 5" - a page footer, not content.
FOOTER_RE = re.compile(r"^.{0,40}\|\s*\d{4}\s+Form\s+10-[KQ]\s*\|\s*\d+\s*$")

# Box-drawing rules and 3+ space columns are how the text dump renders tables.
# The range is the Unicode box-drawing block (U+2500-U+257F), built with chr()
# so the pattern cannot be mangled by an encoding round-trip of this file.
_BOX_RANGE = chr(0x2500) + "-" + chr(0x257F)
TABLE_RULE_RE = re.compile("[" + _BOX_RANGE + "]{3,}|_{6,}")
COLUMN_GAP_RE = re.compile(r"\S {3,}\S")

# "(In millions, except number of shares, which are reflected in thousands...)"
# under a statement's title: the unit of every number in the table below.
UNITS_RE = re.compile(r"^\((?:dollars |amounts )?in (?:millions|thousands|billions)\b.*\)$", re.I)

# A table cell that is a number: 35,934  (14,264)  $7.46  —  12%
NUMERIC_CELL_RE = re.compile(r"^[$()\-—–\d,.%]+$")

# A table's column header is its leading rows with no row label and number:
# "Years ended" / "September 27, | September 28," / "2025 | 2024". Capped so a
# table made only of words doesn't turn wholly into header.
MAX_HEADER_ROWS = 5

# The sentence leading into a note table ("...were as follows (in millions):")
# is what says what its numbers are. Longer than this, it isn't a lead-in.
MAX_LEAD_IN_CHARS = 300

# Sentence boundary for splitting paragraphs that exceed the target size on
# their own. Avoids breaking on "U.S." or "Inc." by requiring a following
# capital or quote and a preceding lowercase/digit/closing punctuation.
SENTENCE_RE = re.compile(r'(?<=[a-z0-9\)\]"”])\.\s+(?=[A-Z"“])')

MAX_HEADING_CHARS = 130


@dataclass
class Block:
    """One structural unit of the filing: a paragraph, a table, or the units
    line under a statement's title."""

    text: str
    kind: str  # "prose" | "table" | "units"
    part: str
    item: str
    section: str
    subsection: str
    char_start: int
    char_end: int


def _is_table_line(line: str) -> bool:
    """A table line either draws a rule or has multiple wide column gaps."""
    if TABLE_RULE_RE.search(line):
        return True
    gaps = len(COLUMN_GAP_RE.findall(line))
    if gaps >= 2:
        return True
    # A single wide gap still reads as tabular when the tail is numeric,
    # e.g. "  Total operating expenses      $  62,151    8%".
    return gaps == 1 and bool(re.search(r"[\d$%]", line.split("   ")[-1]))


def _is_heading(line: str) -> bool:
    """Short, title-ish, no terminal period - a subsection heading.

    Risk Factors also uses full-sentence headings ("The Company's business can
    be impacted by political events...") which end in a period and are treated
    as prose. That is deliberate: they read as the opening sentence of their own
    chunk, which is exactly where a retriever wants them.
    """
    s = line.strip()
    if not s or len(s) > MAX_HEADING_CHARS:
        return False
    if s.endswith((".", ";", ":", ",")):
        return False
    if _is_table_line(line):
        return False
    # Headings are not indented in this dump; table fragments usually are.
    if line[:1].isspace():
        return False
    words = s.split()
    if len(words) > 18:
        return False
    # Require at least one capitalised word and no sentence-like verb pileup.
    return any(w[:1].isupper() for w in words)


def find_body_start(lines: list[str]) -> int:
    """Index of the first real "PART I" line, skipping the cover page and TOC.

    The TOC lists "Part I" indented; the body's is at column 0. Falling back to
    0 would silently index the whole cover page, so the fallback is loud.
    """
    for i, line in enumerate(lines):
        if PART_RE.match(line):
            return i
    raise ValueError(
        "no 'PART I' line at column 0 - the text dump's shape changed, "
        "re-check the Layer 1 extraction before indexing"
    )


def parse_blocks(text: str) -> list[Block]:
    """Walk the filing top to bottom, emitting structure-tagged blocks."""
    lines = text.split("\n")
    start = find_body_start(lines)

    # Byte offsets into the original text, so a citation can point back at the
    # source span even after footers and blank lines are stripped.
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    blocks: list[Block] = []
    part = item = section = subsection = ""
    buf: list[str] = []
    buf_kind = "prose"
    buf_start = 0

    def flush() -> None:
        nonlocal buf, buf_kind, buf_start
        if not buf:
            return
        body = "\n".join(buf).strip()
        if body:
            blocks.append(
                Block(
                    text=body,
                    kind=buf_kind,
                    part=part,
                    item=item,
                    section=section,
                    subsection=subsection,
                    char_start=buf_start,
                    char_end=buf_start + len("\n".join(buf)),
                )
            )
        buf = []
        buf_kind = "prose"

    def table_follows(i: int) -> bool:
        """Does a table line come next, past blanks and other indented lines?
        A column heading can take several lines ("Number of / RSUs / (in
        thousands)"); only its last one is directly above the table."""
        for j in range(i + 1, min(i + 6, len(lines))):
            if _is_table_line(lines[j]):
                return True
            if lines[j].strip() and not lines[j][:1].isspace():
                return False
        return False

    for i in range(start, len(lines)):
        line = lines[i]
        stripped = line.strip()

        # A blank line inside a table is layout, not the table's end: the
        # statements put blank lines between their date and year rows, and
        # ending the table there left its rows without their years.
        if not stripped and buf and buf_kind == "table":
            continue
        if not stripped or FOOTER_RE.match(stripped):
            flush()
            continue

        if m := PART_RE.match(line):
            flush()
            part = f"Part {m.group(1)}"
            item = section = subsection = ""
            continue

        if m := ITEM_RE.match(line):
            flush()
            item = f"Item {m.group(1)}"
            section = m.group(2).strip() or item
            subsection = ""
            continue

        if UNITS_RE.match(stripped) and not line[:1].isspace():
            flush()
            blocks.append(Block(text=stripped, kind="units", part=part, item=item, section=section,
                                subsection=subsection, char_start=offsets[i],
                                char_end=offsets[i] + len(line)))
            continue

        # Indented lines in or just before a table are its labels and column
        # headings ("Current assets:", "Years ended"), not prose: as prose they
        # cut the balance sheet into five pieces, none with its title or years.
        part_of_table = line[:1].isspace() and ((buf and buf_kind == "table") or table_follows(i))
        kind = "table" if _is_table_line(line) or part_of_table else "prose"

        if kind == "prose" and _is_heading(line):
            flush()
            subsection = stripped
            continue

        # A table run and a prose run are never merged into one block.
        if buf and kind != buf_kind:
            flush()
        if not buf:
            buf_start = offsets[i]
            buf_kind = kind
        # Prose keeps no layout either: a two-column passage in the auditor's
        # report reads "How We Addressed the      We tested controls...".
        buf.append(line if kind == "table" else re.sub(r" {2,}", " ", stripped))

    flush()
    return blocks


def _split_long(text: str, target: int, overlap: int) -> list[str]:
    """Split one oversized paragraph on sentence boundaries, with overlap.

    Overlap exists so a claim that straddles a boundary survives in at least
    one chunk intact.
    """
    sentences = SENTENCE_RE.split(text)
    if len(sentences) == 1:
        # No sentence boundary to use (a long single sentence or a URL blob):
        # fall back to hard character slicing rather than emitting a chunk the
        # embedding model will silently truncate.
        step = max(target - overlap, 1)
        return [text[i : i + target] for i in range(0, len(text), step)]

    out: list[str] = []
    cur: list[str] = []
    size = 0
    for sent in sentences:
        s = sent if sent.endswith(".") else sent + "."
        if size + len(s) > target and cur:
            out.append(" ".join(cur))
            # Carry the tail of the previous chunk forward as overlap.
            carry: list[str] = []
            carried = 0
            for prev in reversed(cur):
                if carried + len(prev) > overlap:
                    break
                carry.insert(0, prev)
                carried += len(prev)
            cur = carry
            size = carried
        cur.append(s)
        size += len(s)
    if cur:
        out.append(" ".join(cur))
    return out


def _clean_row(line: str) -> str:
    """One table row as text: rules dropped, "$" joined to its number, the
    run of spaces between columns cut to " | ". The filing pads columns with
    long runs of spaces - 18% of all indexed text was padding, sent to Groq
    in every prompt and blurring the table's match in search."""
    line = TABLE_RULE_RE.sub(" ", line).strip()
    line = re.sub(r"\$\s+", "$", line)
    return " | ".join(cell for cell in re.split(r"\s{3,}", line) if cell)


def _cells(row: str) -> list[str]:
    return row.split(" | ")


def _has_number(row: str) -> bool:
    return any(NUMERIC_CELL_RE.match(c) for c in _cells(row)[1:])


def _is_body_row(row: str) -> bool:
    """A labelled row ("Total assets | $359,241 | $364,980") or a section
    label ("Current assets:"), as opposed to column headings."""
    return bool(re.search(r"[A-Za-z]", _cells(row)[0])) and (row.endswith(":") or _has_number(row))


def _starts_section(rows: list[str], i: int) -> bool:
    """rows[i] is a label with no numbers: "Investing activities:", "ASSETS:"."""
    return not _has_number(rows[i]) and re.search(r"[A-Za-z]", rows[i]) is not None


def _closes_section(rows: list[str], i: int) -> bool:
    """A cut before rows[i] falls between two of the statement's own parts:
    after a total ("Total assets", "Cash generated by operating activities")
    or at an all-capitals label ("LIABILITIES AND SHAREHOLDERS' EQUITY:")."""
    before = _cells(rows[i - 1])[0].lower()
    return rows[i].isupper() or before.startswith("total") or "activities" in before


def _split_table(header: list[str], rows: list[str], budget: int) -> list[str]:
    """Rows into pieces of at most `budget` characters, each starting with
    the header, so every piece says which statement, unit and year its
    numbers belong to - without that, a piece reads "Diluted | 15,004,697 |
    15,408,095 | 15,812,547" and an answer took FY2023's figure as FY2025's.

    Cut where the table's own parts meet when one fits (after "Total
    liabilities", before "Financing activities:"), else at any section label,
    else between any two rows: never inside a row.
    """
    room = max(budget - sum(len(h) + 1 for h in header), budget // 2)

    def size(a: int, b: int) -> int:
        return sum(len(r) + 1 for r in rows[a:b])

    pieces: list[str] = []
    start = 0
    while start < len(rows):
        if size(start, len(rows)) <= room:
            end = len(rows)
        else:
            fits = [e for e in range(start + 1, len(rows)) if size(start, e) <= room] or [start + 1]
            sections = [e for e in fits if _starts_section(rows, e)]
            parts = [e for e in sections if _closes_section(rows, e)]
            end = (parts or sections or fits)[-1]
        pieces.append("\n".join(header + rows[start:end]))
        start = end
    return pieces


def _table_context(table: Block, before: Block | None, units: Block | None) -> list[str]:
    """What a table is, from the lines around it: its heading ("CONSOLIDATED
    BALANCE SHEETS"), the units line under that heading, and the sentence
    leading into it ("...were as follows (in millions):")."""
    lines = [table.subsection or table.section]
    here = (table.item, table.subsection)
    if units is not None and (units.item, units.subsection) == here:
        lines.append(units.text)
    if before is not None and before.kind == "prose" and (before.item, before.subsection) == here:
        lead = SENTENCE_RE.split(before.text)[-1].strip()
        if lead.endswith(":") and len(lead) <= MAX_LEAD_IN_CHARS:
            lines.append(lead)
    return [line for line in lines if line]


def _table_pieces(table: Block, context: list[str], budget: int) -> list[str]:
    rows = [r for r in (_clean_row(line) for line in table.text.split("\n")) if r]
    n_head = 0
    while n_head < min(len(rows), MAX_HEADER_ROWS) and not _is_body_row(rows[n_head]):
        n_head += 1
    if n_head == len(rows):  # a table of words: nothing to repeat but its context
        n_head = 0
    return _split_table(context + rows[:n_head], rows[n_head:], budget)


def chunk_blocks(blocks: list[Block], cfg: ChunkingCfg) -> list[tuple[str, Block]]:
    """Pack blocks into ~target_chars chunks without crossing a heading.

    Chunks never span two subsections. That costs a little packing efficiency
    and buys a citation that is actually true.
    """
    out: list[tuple[str, Block]] = []
    cur: list[Block] = []
    size = 0
    units: Block | None = None   # the last units line, for the table below it
    before: Block | None = None  # the block just before this one

    def flush_group() -> None:
        nonlocal cur, size
        if not cur:
            return
        head = cur[0]
        merged = "\n\n".join(b.text for b in cur)
        out.append((merged, head))
        cur = []
        size = 0

    for block in blocks:
        if block.item in cfg.exclude_items:
            flush_group()
            continue

        if block.kind == "units":
            units = block
            continue

        # Tables are never packed with prose. A table is one piece if it fits
        # the models' input with room for the question, otherwise split at
        # its own section breaks - every piece carrying the table's context.
        if block.kind == "table":
            flush_group()
            context = _table_context(block, before, units)
            for piece in _table_pieces(block, context, cfg.table_max_chars):
                out.append((piece, block))
            units, before = None, block
            continue
        before = block

        same_section = bool(cur) and (
            cur[0].item == block.item and cur[0].subsection == block.subsection
        )
        if cur and not same_section:
            flush_group()

        if len(block.text) > cfg.target_chars:
            flush_group()
            for piece in _split_long(block.text, cfg.target_chars, cfg.overlap_chars):
                out.append((piece, block))
            continue

        if size + len(block.text) > cfg.target_chars and cur:
            flush_group()
        cur.append(block)
        size += len(block.text)

    flush_group()
    return out


def build_chunks(text: str, cfg: Config | None = None) -> list[Chunk]:
    """Text in, citation-carrying Chunks out. The whole of Layer 2a."""
    conf = cfg or get_config()
    blocks = parse_blocks(text)
    packed = chunk_blocks(blocks, conf.chunking)

    fiscal_year = _infer_fiscal_year(text, conf)
    chunks: list[Chunk] = []
    for idx, (body, head) in enumerate(packed):
        if len(body.strip()) < conf.chunking.min_chars:
            continue  # stray heading fragments and one-line table remnants
        chunks.append(
            Chunk(
                chunk_id=f"{conf.company.ticker}-{conf.company.form}-{fiscal_year}-{idx:04d}",
                text=body,
                kind=SourceKind.NARRATIVE,
                company=conf.company.name,
                ticker=conf.company.ticker,
                form=conf.company.form,
                fiscal_year=fiscal_year,
                item=head.item,
                section=head.section,
                subsection=head.subsection,
                char_start=head.char_start,
                char_end=head.char_end,
            )
        )
    return chunks


def _infer_fiscal_year(text: str, cfg: Config) -> str:
    """Read the fiscal year off the cover page, falling back to config."""
    if str(cfg.company.year) != "latest":
        return f"FY{cfg.company.year}"
    m = re.search(r"fiscal year ended\s+\w+\s+\d{1,2},\s*(\d{4})", text[:4000], re.I)
    return f"FY{m.group(1)}" if m else "FY"


def write_chunks(chunks: list[Chunk], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(c.model_dump_json() + "\n")


def read_chunks(path: Path) -> list[Chunk]:
    if not path.exists():
        raise FileNotFoundError(f"no chunks at {path} - run `dd chunk` first")
    with path.open(encoding="utf-8") as fh:
        return [Chunk.model_validate(json.loads(line)) for line in fh if line.strip()]

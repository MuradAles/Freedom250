"""Curated federal regulation sources used by the legal RAG corpus."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegulationSource:
    """One extractable eCFR source and its relevance to SBA workflows."""

    slug: str
    title: int
    label: str
    cfr_scope: str
    rationale: str
    path: tuple[tuple[str, str], ...]
    version_parts: tuple[str, ...] = ()
    version_part_range: tuple[int, int] | None = None
    section_prefix: str | None = None


CORE_REGULATION_SOURCES = (
    RegulationSource(
        slug="13-cfr-chapter-i-sba",
        title=13,
        label="Small Business Administration Regulations",
        cfr_scope="13 CFR Chapter I",
        rationale=(
            "Primary SBA rules for lending, eligibility, size standards, "
            "contracting programs, disaster assistance, and appeals."
        ),
        path=(("chapter", "I"),),
        version_part_range=(1, 199),
    ),
    RegulationSource(
        slug="2-cfr-part-200-uniform-guidance",
        title=2,
        label="Uniform Federal Award Requirements",
        cfr_scope="2 CFR Part 200",
        rationale=(
            "Government-wide administrative, cost, and audit requirements "
            "for federal financial assistance."
        ),
        path=(("chapter", "II"), ("part", "200")),
        version_parts=("200",),
    ),
    RegulationSource(
        slug="2-cfr-chapter-xxvii-sba-awards",
        title=2,
        label="SBA Federal Award and Debarment Requirements",
        cfr_scope="2 CFR Chapter XXVII",
        rationale=(
            "SBA-specific nonprocurement debarment and federal award rules."
        ),
        path=(("chapter", "XXVII"),),
        version_part_range=(2700, 2799),
    ),
    RegulationSource(
        slug="48-cfr-part-19-small-business",
        title=48,
        label="FAR Small Business Programs",
        cfr_scope="48 CFR Part 19",
        rationale=(
            "Federal acquisition policy for small-business set-asides, "
            "subcontracting, representations, and socioeconomic programs."
        ),
        path=(("chapter", "1"), ("part", "19")),
        version_parts=("19",),
    ),
    RegulationSource(
        slug="48-cfr-52-219-small-business-clauses",
        title=48,
        label="FAR Small Business Contract Clauses",
        cfr_scope="48 CFR 52.219",
        rationale=(
            "Solicitation provisions and contract clauses implementing FAR "
            "small-business programs."
        ),
        path=(("chapter", "1"), ("part", "52")),
        version_parts=("52",),
        section_prefix="52.219-",
    ),
)


def source_by_slug(slug: str) -> RegulationSource:
    """Return a configured regulation source by stable slug."""
    for source in CORE_REGULATION_SOURCES:
        if source.slug == slug:
            return source
    raise KeyError(f"Unknown regulation source: {slug}")

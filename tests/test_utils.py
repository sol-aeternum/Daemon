from __future__ import annotations

from orchestrator.utils import slugify_filename


def test_slugify_filename_basic():
    assert slugify_filename("Jane Smith's Resume!!") == "jane-smiths-resume"


def test_slugify_filename_empty_input():
    assert slugify_filename("") == ""


def test_slugify_filename_all_invalid_input():
    assert slugify_filename("../../\\//***") == ""


def test_slugify_filename_max_length_cap():
    assert slugify_filename("a" * 100) == "a" * 60


def test_slugify_filename_word_boundary_truncation():
    value = "quarterly-status-report-for-the-international-product-expansion-q4"
    slug = slugify_filename(value, max_length=40)
    assert len(slug) <= 40
    assert slug == "quarterly-status-report-for-the"


def test_slugify_filename_unicode_strips_non_ascii_chars():
    assert slugify_filename("Résumé São Paulo") == "rsum-so-paulo"

from nexus.core.utils.message_formatting import (
    clip_message_text,
    flatten_markdown_table,
    normalize_markdown_headers,
)


def test_clip_message_text_no_truncation():
    text = "Short message"
    assert clip_message_text(text, limit=100) == text


def test_clip_message_text_with_truncation():
    text = "A very long message that needs clipping"
    limit = 20
    clipped = clip_message_text(text, limit=limit)
    assert len(clipped) <= limit
    assert clipped.endswith("[truncated]")


def test_flatten_markdown_table():
    table = (
        "| Header 1 | Header 2 |\n"
        "|---|---|\n"
        "| Val 1 | Val 2 |\n"
        "| Val 3 | Val 4 |"
    )
    flattened = flatten_markdown_table(table)
    assert "- Header 1: Val 1 | Header 2: Val 2" in flattened
    assert "- Header 1: Val 3 | Header 2: Val 4" in flattened


def test_normalize_markdown_headers():
    text = "# Main Title\n## Secondary\nNormal text"
    normalized = normalize_markdown_headers(text)
    assert normalized == "*Main Title*\n*Secondary*\nNormal text"

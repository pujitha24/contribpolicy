from contribpolicy.markdown import clean_inline, parse, split_sentences


def kinds(units):
    return [(u.kind, u.text) for u in units]


def test_headings_and_paragraph_lines():
    units = parse("# Title\n\nFirst line\ncontinues here. Second sentence.\n\n## Sub\nText.\n")
    assert units[0].kind == "heading" and units[0].text == "Title" and units[0].line == 1
    assert units[1].text == "First line continues here." and units[1].line == 3
    assert units[2].text == "Second sentence." and units[2].line == 4
    assert units[-1].headings == ("Title", "Sub")


def test_setext_heading():
    units = parse("Policy\n======\n\nRules\n-----\nBe nice.\n")
    assert [u.text for u in units if u.kind == "heading"] == ["Policy", "Rules"]
    assert units[-1].headings == ("Policy", "Rules")


def test_list_items_get_lead_sentence():
    units = parse("Contributors must:\n\n- review the code\n- explain it\n\nAfter.\n")
    items = [u for u in units if u.kind == "item"]
    assert [i.lead for i in items] == ["Contributors must:", "Contributors must:"]
    assert units[-1].kind == "prose" and units[-1].lead == ""


def test_list_continuation_and_line_numbers():
    units = parse("- first item\n  continues\n- second\n")
    assert units[0].text == "first item continues" and units[0].line == 1
    assert units[1].line == 3


def test_checkbox_state():
    units = parse("- [ ] open\n- [x] done\n* [X] also done\n")
    assert [(u.kind, u.checked) for u in units] == [("checkbox", False), ("checkbox", True), ("checkbox", True)]
    assert units[0].text == "open"


def test_code_fence_lines_keep_lead():
    units = parse("**Preferred: trailer**\n\n```\nSubject\n\nAssisted-by: Tool 1\n```\n")
    code = [u for u in units if u.kind == "code"]
    assert [c.text for c in code] == ["Subject", "Assisted-by: Tool 1"]
    assert code[1].line == 6
    assert code[1].lead == "Preferred: trailer"


def test_fence_content_is_not_parsed_as_markdown():
    units = parse("```\n# not a heading\n- not an item\n```\n")
    assert all(u.kind == "code" for u in units)


def test_tables_rows_and_separator():
    units = parse("| Doc | Purpose |\n|---|---|\n| AI | rules |\n")
    assert kinds(units) == [("table", "Doc | Purpose"), ("table", "AI | rules")]


def test_front_matter_skipped():
    units = parse("---\ntitle: x\n---\n\n# Real\n")
    assert kinds(units) == [("heading", "Real")]
    assert units[0].line == 5


def test_blockquote_and_github_alert():
    units = parse("> [!CAUTION]\n> Do not do this.\n>\n> - item one\n")
    assert ("prose", "Do not do this.") in kinds(units)
    assert ("item", "item one") in kinds(units)


def test_html_comments_become_text():
    units = parse("<!--\nTick exactly one option.\n-->\n")
    assert [u.text for u in units] == ["Tick exactly one option."]
    assert units[0].line == 2  # the sentence itself starts on line 2


def test_multiline_bold_and_links_keep_line_numbers():
    text = "Intro sentence here.\n**Do not copy responses\nfrom the AI.** Then [a\nlink](x.md) follows. Last one.\n"
    units = parse(text)
    assert [u.text for u in units] == [
        "Intro sentence here.",
        "Do not copy responses from the AI.",
        "Then a link follows.",
        "Last one.",
    ]
    assert [u.line for u in units] == [1, 2, 3, 4]


def test_clean_inline():
    assert clean_inline("see [the guide](a/b.md) and ![img](x.png)") == "see the guide and img"
    assert clean_inline("**bold** and *em* and _em2_ keep `code`") == "bold and em and em2 keep `code`"
    assert clean_inline("AI_POLICY.md stays") == "AI_POLICY.md stays"
    assert clean_inline("<https://x.org> <br/>") == "https://x.org  "


def test_split_sentences_abbreviations():
    s = "Use tools (e.g. Copilot) carefully. Next one! And i.e. this stays. Done"
    assert [t for _, t in split_sentences(s)] == [
        "Use tools (e.g. Copilot) carefully.",
        "Next one!",
        "And i.e. this stays.",
        "Done",
    ]


def test_empty_document():
    assert parse("") == []
    assert parse("\n\n   \n") == []


def test_hr_breaks_paragraph():
    units = parse("one\n\n---\n\ntwo\n")
    assert [u.text for u in units] == ["one", "two"]


def test_fence_inside_list_item():
    units = parse("- Commit with sign-off:\n    ```bash\n    git commit -s\n    ```\n- Next item\n")
    assert [(u.kind, u.text) for u in units] == [
        ("item", "Commit with sign-off:"),
        ("code", "git commit -s"),
        ("item", "Next item"),
    ]

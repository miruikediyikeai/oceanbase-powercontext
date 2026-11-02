from notes import split_note, stable_card_id


def test_split_note_respects_headings_and_merges_lines():
    note = """# 事务

原子性确保事务整体提交或回滚，不会出现只完成一半的状态。

一致性确保数据从一种合法状态转换到另一种合法状态。

## 隔离级别

可串行化是最严格的隔离级别，能够避免幻读等并发异常现象。
"""
    segments = split_note(note)
    assert [segment.heading for segment in segments] == ["事务", "隔离级别"]
    assert "原子性" in segments[0].text
    assert "可串行化" in segments[1].text


def test_split_note_skips_short_fragments():
    note = "太短。"
    assert split_note(note) == []


def test_stable_id_is_content_derived_and_order_insensitive_to_whitespace():
    first = stable_card_id("数据库", "什么是事务？", "一组操作的集合。", "笔记")
    again = stable_card_id("数据库", "什么是事务？", "一组操作的集合。", "笔记")
    assert first == again
    assert first.startswith("note-")
    assert len(first) == len("note-") + 16
    different = stable_card_id("数据库", "什么是索引？", "加速查询的结构。", "笔记")
    assert first != different

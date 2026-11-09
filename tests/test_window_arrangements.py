from vith.window_arrangement import EditorBuffer, VSplit


def test_initial(window, tab_page):
    assert isinstance(tab_page.root, VSplit)
    assert tab_page.root == [window]


def test_vsplit(editor, tab_page):
    # Create new buffer.
    eb = EditorBuffer(editor)

    # Insert in tab, by splitting.
    tab_page.vsplit(eb)

    assert isinstance(tab_page.root, VSplit)
    assert len(tab_page.root) == 2


def test_new_buffers_are_numbered_right_after_the_active_one(editor):
    # docs/bugs/013.md: `_add_editor_buffer` inserts new buffers right
    # AFTER the active one (matching the docstring and vim `:buffers`
    # growth), not before it.
    arrangement = editor.window_arrangement
    eb_a = EditorBuffer(editor)
    arrangement._add_editor_buffer(eb_a)
    assert arrangement.editor_buffers == [eb_a]
    assert arrangement.active_editor_buffer is eb_a

    eb_b = EditorBuffer(editor)
    arrangement._add_editor_buffer(eb_b)
    assert arrangement.editor_buffers == [eb_a, eb_b]
    # Without `show_in_current_window`, focus stays on the active buffer:
    assert arrangement.active_editor_buffer is eb_a

    # A new split targeting eb_b makes it active; the next insertion again
    # lands right after it.
    arrangement.active_tab.vsplit(eb_b)
    assert arrangement.active_editor_buffer is eb_b
    eb_c = EditorBuffer(editor)
    arrangement._add_editor_buffer(eb_c)
    assert arrangement.editor_buffers == [eb_a, eb_b, eb_c]
    assert arrangement.active_editor_buffer is eb_b


def test_close_editor_buffer_closes_target_window(editor):
    editor.load_initial_files([None])
    wa = editor.window_arrangement
    eb1 = wa.active_editor_buffer
    eb2 = EditorBuffer(editor)
    wa._add_editor_buffer(eb2)
    wa.active_tab.vsplit(eb2)
    assert wa.active_tab.window_count() == 2

    wa.active_tab.show_editor_buffer(eb1)
    assert wa.active_editor_buffer is eb1
    wa.active_tab.close_editor_buffer(eb2)
    assert eb2 not in [w.editor_buffer for w in wa.active_tab.windows()]
    assert eb1 in [w.editor_buffer for w in wa.active_tab.windows()]


def test_close_window_on_last_window_does_not_crash(editor):
    editor.load_initial_files([None])
    wa = editor.window_arrangement
    assert wa.active_tab.window_count() == 1
    assert len(wa.tab_pages) == 1
    wa.close_window()
    assert wa.active_tab.window_count() == 1
    assert editor.message is not None


def test_close_active_window_reports_empty_and_cycle_focus_no_ops(editor_buffer):
    # 066: `close_active_window` reports when the tab emptied, and
    # `cycle_focus` no-ops on an empty tab (previously `ValueError` from
    # `windows.index(None)`).
    from vith.window_arrangement import TabPage, Window

    tab = TabPage(Window(editor_buffer))
    assert tab.close_active_window() is True
    assert tab.active_window is None
    tab.cycle_focus()  # Must not raise.


def test_close_active_window_not_empty_returns_false(editor):
    # 066: with two windows the close only removes the focused one.
    from vith.window_arrangement import TabPage, Window

    wa = editor.window_arrangement
    eb1 = EditorBuffer(editor)
    wa._add_editor_buffer(eb1)
    tab = TabPage(Window(eb1))
    eb2 = EditorBuffer(editor)
    wa._add_editor_buffer(eb2)
    tab.vsplit(eb2)
    assert tab.close_active_window() is False
    assert tab.active_window is not None


def test_close_buffer_keeps_active_index_valid_across_tabs(editor):
    # 066: with tabs [T0 shared] [T1 other] [T2 shared-active], wiping the
    # shared buffer removes T0 and T2; the old snapshot-index bookkeeping
    # left active_tab_index pointing past the end (IndexError on next
    # `active_tab` access). The repair must land on the surviving tab T1.
    from vith.window_arrangement import TabPage, Window

    wa = editor.window_arrangement
    eb_shared = EditorBuffer(editor)
    wa._add_editor_buffer(eb_shared)
    base_tab = wa.tab_pages[0]
    eb_other = EditorBuffer(editor)
    wa._add_editor_buffer(eb_other)

    t_other = TabPage(Window(eb_other))
    t_shared2 = TabPage(Window(eb_shared))
    wa.tab_pages = [base_tab, t_other, t_shared2]
    wa.active_tab_index = 2

    wa.close_buffer()  # Must not raise.

    assert eb_shared not in wa.editor_buffers
    assert wa.tab_pages == [t_other]
    assert wa.active_tab_index == 0
    assert wa.active_tab is t_other


def test_close_buffer_repairs_when_active_tab_itself_is_removed(editor):
    # 066: tabs [T0 other] [T1 shared, active] [T2 shared] — the active tab
    # itself is among the removed ones; the index must clamp to a survivor.
    from vith.window_arrangement import TabPage, Window

    wa = editor.window_arrangement
    eb_shared = EditorBuffer(editor)
    wa._add_editor_buffer(eb_shared)
    t_other = TabPage(Window(EditorBuffer(editor)))
    t_shared = TabPage(Window(eb_shared))
    wa.tab_pages = [t_other, t_shared, TabPage(Window(eb_shared))]
    wa.active_tab_index = 1

    wa.close_buffer()

    assert wa.tab_pages != []
    assert wa.active_tab is wa.tab_pages[wa.active_tab_index]


def test_go_to_buffer_resolution_order(editor, monkeypatch):
    # 067: ':b' resolves exact location -> unique substring (ambiguous
    # rejected) -> 1-based display number; previously it required an exact
    # match or a 0-based index, and a completed substring did nothing.
    wa = editor.window_arrangement
    eb_alpha = EditorBuffer(editor, "alpha.txt")
    wa._add_editor_buffer(eb_alpha)
    eb_beta = EditorBuffer(editor, "beta.txt")
    wa._add_editor_buffer(eb_beta)
    eb_gamma = EditorBuffer(editor, "gamma.txt")
    wa._add_editor_buffer(eb_gamma)

    # Substring completion now jumps to the unique match:
    wa.go_to_buffer("gam")
    assert wa.active_editor_buffer is eb_gamma

    # Exact match still wins:
    wa.go_to_buffer("beta.txt")
    assert wa.active_editor_buffer is eb_beta

    # 1-based number: ':b 1' is the FIRST buffer (previously off by one):
    wa.go_to_buffer("1")
    assert wa.active_editor_buffer is wa.editor_buffers[0]

    # Ambiguous substring (after adding 'alphabet.txt') is rejected:
    wa.go_to_buffer("bet")  # unique still
    eb_alphabet = EditorBuffer(editor, "alphabet.txt")
    wa._add_editor_buffer(eb_alphabet)
    wa.go_to_buffer("bet")
    assert editor.message and editor.message.startswith("E93")
    assert editor.message.endswith("bet")

    # No match at all is rejected:
    wa.go_to_buffer("zzz-nomatch")
    assert editor.message and editor.message.startswith("E94")


def test_tab_page_windows_cache_invalidated_on_split_and_close(editor):
    # 114 item 8: `TabPage._get_windows()` caches the flattened window list and
    # invalidates it on `hsplit`, `vsplit`, and `close_active_window`.
    editor.load_initial_files([None])
    wa = editor.window_arrangement
    tab = wa.active_tab
    assert len(tab.windows()) == 1
    cached_initial = tab._windows_cache
    assert cached_initial is not None

    wa.hsplit()
    assert len(tab.windows()) == 2
    wa.vsplit()
    assert len(tab.windows()) == 3
    assert tab.close_active_window() is False
    assert len(tab.windows()) == 2


def test_window_arrangement_empty_state_guards(editor):
    # 111 items 1-5 & 113 item 2: calling window/tab navigation and mutation
    # methods when `tab_pages` or `editor_buffers` is empty does not crash.
    wa = editor.window_arrangement
    assert wa.active_tab is None
    assert wa.active_editor_buffer is None
    assert wa.active_pt_window is None

    # All must be safe no-ops on an empty arrangement:
    wa.close_tab()
    wa.keep_only_current_window()
    wa.go_to_next_buffer()
    wa.go_to_previous_buffer()
    wa.go_to_next_tab()
    wa.go_to_previous_tab()

    eb = EditorBuffer(editor, "solo.txt")
    wa._add_editor_buffer(eb)
    assert wa.editor_buffers == [eb]
    wa.show_editor_buffer(eb)  # no active tab yet -> safe no-op

    # Single-tab cycling stays at index 0:
    wa.editor_buffers.clear()
    editor.load_initial_files(["solo.txt"])
    assert len(wa.tab_pages) == 1
    wa.go_to_next_tab()
    assert wa.active_tab_index == 0
    wa.go_to_previous_tab()
    assert wa.active_tab_index == 0


def test_close_buffer_tolerates_window_buffer_desync(editor):
    # 111 items 2/5 follow-up: active window shows a buffer missing from
    # `editor_buffers` (desync); `close_buffer` must not raise ValueError.
    from vith.window_arrangement import EditorBuffer

    editor.load_initial_files([None])
    wa = editor.window_arrangement
    stray = EditorBuffer(editor)
    wa.active_tab.active_window.editor_buffer = stray
    assert stray not in wa.editor_buffers
    wa.close_buffer()  # Must not raise.
    assert stray not in wa.editor_buffers

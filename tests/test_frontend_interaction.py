import pathlib
import unittest


APP_JS = pathlib.Path(__file__).parents[1] / "app" / "static" / "app.js"
STYLES_CSS = pathlib.Path(__file__).parents[1] / "app" / "static" / "styles.css"
INDEX_HTML = pathlib.Path(__file__).parents[1] / "app" / "static" / "index.html"


class FrontendInteractionTests(unittest.TestCase):
    def test_item_selection_updates_without_replacing_the_list(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("function selectItem(id)", source)

    def test_item_actions_render_inside_meta_after_timestamp(self):
        source = APP_JS.read_text(encoding="utf-8")
        meta_start = source.index('<div class="clip-meta">')
        content_start = source.index('${item.kind === "image"', meta_start)
        meta_markup = source[meta_start:content_start]

        self.assertIn('<span class="clip-time">${escapeHtml(item.created_at)}</span>', meta_markup)
        self.assertIn("${renderActions(item)}", meta_markup)
        self.assertIn('<div class="clip-actions">', source)
        self.assertLess(
            meta_markup.index('<span class="clip-time">${escapeHtml(item.created_at)}</span>'),
            meta_markup.index("${renderActions(item)}"),
        )
        self.assertNotIn('</div>\n    <div class="clip-actions">', source)

    def test_item_actions_are_right_aligned_in_meta_row(self):
        source = STYLES_CSS.read_text(encoding="utf-8")
        actions_start = source.index(".clip-actions {")
        actions_end = source.index("}", actions_start)
        actions_rule = source[actions_start:actions_end]

        self.assertIn("margin-left: auto;", actions_rule)

    def test_history_uses_paging_envelope_and_loads_more_on_scroll(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("payload.items", source)
        self.assertIn("payload.total", source)
        self.assertIn("payload.has_more", source)
        self.assertIn('list.addEventListener("scroll"', source)
        self.assertIn("load(true)", source)

    def test_list_groups_dates_and_image_filter_uses_grid_mode(self):
        source = APP_JS.read_text(encoding="utf-8")
        styles = STYLES_CSS.read_text(encoding="utf-8")
        self.assertIn("function dateGroupLabel", source)
        self.assertIn('list.classList.toggle("image-grid"', source)
        self.assertIn(".clip-list.image-grid", styles)

    def test_search_fills_header_and_actions_reveal_on_selection(self):
        styles = STYLES_CSS.read_text(encoding="utf-8")
        search_start = styles.index(".search-box input {")
        search_rule = styles[search_start:styles.index("}", search_start)]
        self.assertIn("width: 100%;", search_rule)
        self.assertIn(".clip-item.selected .clip-actions", styles)
        self.assertIn("opacity: 0;", styles)

    def test_typing_from_list_focus_starts_search(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("isPrintableKey(event)", source)
        self.assertIn("searchInput.focus()", source)

    def test_focus_selection_resets_to_first_visible_item(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("function selectFirstItem()", source)
        focus_start = source.index("window.focusSelection = function focusSelection()")
        focus_end = source.index("};", focus_start)
        focus_body = source[focus_start:focus_end]
        self.assertIn("selectFirstItem()", focus_body)
        self.assertNotIn("ensureSelection()", focus_body)

    def test_settings_include_privacy_storage_and_hotkey_status(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        source = APP_JS.read_text(encoding="utf-8")
        for element_id in (
            "mainHotkeyStatus",
            "obsidianHotkeyStatus",
            "monitorPaused",
            "sensitiveFilter",
            "excludedApps",
            "retentionDays",
            "cleanupDuplicates",
            "autostartStatus",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertNotIn('id="clearAll"', html)
        self.assertIn("renderHotkeyStatus", source)

    def test_polling_skips_unchanged_payload(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("function itemsFingerprint", source)
        self.assertIn("if (nextFingerprint === state.fingerprint) return", source)

    def test_smart_categories_are_filterable_and_render_as_metadata(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        source = APP_JS.read_text(encoding="utf-8")
        styles = STYLES_CSS.read_text(encoding="utf-8")

        self.assertIn('id="smartChips"', html)
        for category in ("code", "todo", "prompt", "contact", "path", "sensitive"):
            self.assertIn(f'data-smart="{category}"', html)
        self.assertIn('params.set("smart_category", state.smartCategory)', source)
        self.assertIn("smartCategoryLabels", source)
        self.assertIn('class="smart-badge', source)
        self.assertIn(".smart-filters", styles)
        self.assertIn("overflow-x: auto;", styles)


if __name__ == "__main__":
    unittest.main()

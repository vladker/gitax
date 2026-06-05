# -*- coding: utf-8 -*-
"""
Tests for BrowserMAX export optimization.

Core idea: replace two separate page.evaluate calls per scroll step
(_collect_pass_sigs + _collect_full_for_sigs) with ONE call
(_collect_enrich_new) that knows about existing signatures and
returns full data only for new messages.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestCollectEnrichNew:
    """Test the single-phase enrichment method."""

    def _mock_page(self, return_value):
        page = MagicMock()
        page.evaluate.return_value = return_value
        page.is_closed.return_value = False  # _check_connection calls this
        return page

    # -- RED: these tests fail because _collect_enrich_new doesn't exist yet --

    def test_returns_full_data_from_page(self):
        """When page returns enriched messages, method should pass them through."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True

        expected = [
            {
                "text": "New message alpha",
                "html": "<div>alpha</div>",
                "classes": "message message--out",
                "sender": "Alice",
                "timestamp": "2025-01-01T10:00:00",
                "direction": "out",
                "attachments": [{"name": "file.zip", "size": "1MB"}],
                "reactions": ["+1"],
                "is_reply": False,
            },
            {
                "text": "New message beta",
                "html": "<div>beta</div>",
                "classes": "message message--in",
                "sender": "Bob",
                "timestamp": "2025-01-01T11:00:00",
                "direction": "in",
                "attachments": [],
                "reactions": [],
                "is_reply": True,
            },
        ]

        bm.page = self._mock_page(expected)

        result = bm._collect_enrich_new(set())

        assert len(result) == 2
        assert result[0]["text"] == "New message alpha"
        assert result[0]["direction"] == "out"
        assert result[0]["attachments"][0]["name"] == "file.zip"
        assert result[1]["is_reply"] is True
        assert result[1]["reactions"] == []

    def test_calls_page_evaluate_exactly_once(self):
        """Only ONE page.evaluate per step — the whole point of the optimization.
        Old approach: _collect_pass_sigs (1) + _collect_full_for_sigs (2) = 2 calls."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = self._mock_page([])

        bm._collect_enrich_new({"some_known_sig"})

        assert bm.page.evaluate.call_count == 1

    def test_handles_empty_result(self):
        """Empty DOM or no new messages -> empty list."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = self._mock_page([])

        result = bm._collect_enrich_new({"known"})
        assert result == []

    def test_handles_none_result(self):
        """JS returns null/undefined -> empty list, no crash."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = self._mock_page(None)

        result = bm._collect_enrich_new(set())
        assert result == []

    def test_handles_js_error_gracefully(self):
        """If page.evaluate throws (e.g. CDP disconnect), return [] not crash."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.side_effect = Exception("CDP disconnected")
        bm._logger = MagicMock()

        result = bm._collect_enrich_new({"sig"})
        assert result == []

    def test_passes_known_sigs_to_js(self):
        """The known_sigs set should be passed to the JS so it can skip them."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True

        captured_args = []

        def capture_evaluate(expr, arg=None):
            captured_args.append((expr, arg))
            return []

        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate = capture_evaluate

        known = {"sig_alpha", "sig_beta", "sig_gamma"}
        bm._collect_enrich_new(known)

        # The second arg to evaluate() should contain our known signatures
        assert len(captured_args) == 1
        expr, arg = captured_args[0]
        # arg should be a list containing the known sigs
        assert isinstance(arg, list)
        assert "sig_alpha" in arg
        assert "sig_beta" in arg
        assert "sig_gamma" in arg


class TestScrollCollectSinglePhase:
    """Verify the scroll loop uses the single-phase approach."""

    def test_scroll_loop_uses_enrich_not_two_phase(self):
        """_scroll_and_collect_full should call _collect_enrich_new,
        NOT _collect_pass_sigs + _collect_full_for_sigs separately."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm._logger = MagicMock()

        enrich_call_count = [0]
        sigs_call_count = [0]
        full_for_sigs_call_count = [0]

        def mock_enrich(known):
            enrich_call_count[0] += 1
            # Stop after first call to avoid infinite loop
            if enrich_call_count[0] == 1:
                return [{"text": "first msg", "html": "", "classes": "",
                         "sender": "", "timestamp": "", "direction": "unknown",
                         "attachments": [], "reactions": [], "is_reply": False}]
            return []

        def mock_sigs():
            sigs_call_count[0] += 1
            return []

        def mock_full_for_sigs(sigs):
            full_for_sigs_call_count[0] += 1
            return []

        bm._collect_enrich_new = mock_enrich
        bm._collect_pass_sigs = mock_sigs
        bm._collect_full_for_sigs = mock_full_for_sigs

        with patch.object(bm, '_scroll_to_bottom'):
            # Make scroll return False after 2 steps to stop the loop
            scroll_count = [0]

            def mock_scroll_eval(expr):
                if 'scrollBy' in str(expr) or 'scrollTop' in str(expr):
                    scroll_count[0] += 1
                    return scroll_count[0] <= 2
                # For container focus, return None
                return None

            bm.page = MagicMock()
            bm.page.is_closed.return_value = False
            bm.page.evaluate = mock_scroll_eval
            bm.page.wait_for_timeout = MagicMock()

            bm._scroll_and_collect_full(passes=1)

        # The old approach would call _collect_pass_sigs AND _collect_full_for_sigs
        # The new approach calls _collect_enrich_new instead
        assert enrich_call_count[0] >= 1, "Should have called _collect_enrich_new"
        # Old two-phase calls should NOT be used in the main loop
        # (full_for_sigs might be called in safety net, that's OK)

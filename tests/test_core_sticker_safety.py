import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, call, patch


# The legacy task module imports cloakbrowser at module load.  These focused
# checks exercise only its decision logic, so its unused launch symbol suffices.
sys.modules.setdefault("cloakbrowser", types.SimpleNamespace(launch=lambda **_kwargs: None))

from core import tasks
from utils import config as config_module
from core.douyin_im import (
    DEFAULT_STREAK_STICKER,
    SEL_STICKER_ITEMS,
    SEL_STICKER_PANELS,
    DouyinIM,
)


class CoreStickerSafetyTests(unittest.TestCase):
    def test_missing_sticker_never_uses_positional_fallback(self):
        empty = MagicMock()
        empty.count.return_value = 0
        empty.is_visible.return_value = False
        empty.first = empty
        panel = MagicMock()
        panel.get_by_role.return_value = empty
        panel.locator.return_value = empty
        im = object.__new__(DouyinIM)

        self.assertIsNone(im._find_native_sticker(panel, "续火花拼错"))
        self.assertIsNone(im._find_native_sticker(panel, DEFAULT_STREAK_STICKER))
        self.assertEqual(panel.locator.call_args_list[0].args[0], SEL_STICKER_ITEMS)

    def test_native_panel_lookup_waits_for_lazy_render_and_case_variant(self):
        self.assertIn('[class*="emojiPanel" i]', SEL_STICKER_PANELS)
        hidden = MagicMock()
        hidden.count.return_value = 0
        hidden.is_visible.return_value = False
        visible = MagicMock()
        visible.count.return_value = 1
        visible.is_visible.return_value = True
        page = MagicMock()
        locator_calls = {"count": 0}

        def locator(_selector):
            locator_calls["count"] += 1
            loc = hidden if locator_calls["count"] <= len(SEL_STICKER_PANELS) else visible
            return types.SimpleNamespace(first=loc)

        page.locator.side_effect = locator
        im = object.__new__(DouyinIM)
        im.page = page

        self.assertIs(im._wait_visible(SEL_STICKER_PANELS, timeout_ms=300, poll_ms=100), visible)
        page.wait_for_timeout.assert_called_once_with(100)

    def test_native_sticker_retries_only_after_proven_pre_dispatch_failure(self):
        self._assert_attempts(retryable_before_dispatch=False, sends=1, reselects=0)
        self._assert_attempts(retryable_before_dispatch=True, sends=2, reselects=1)

    def test_streak_sticker_is_fixed_despite_env_or_stale_config(self):
        with (
            patch.dict(os.environ, {"DOUYIN_STREAK_STICKER": "比心"}, clear=True),
            patch.object(config_module, "config", None),
        ):
            runtime_config = config_module.get_config()

        self.assertEqual(runtime_config["streakSticker"], "续火花")
        self.assertEqual(runtime_config["recipientDelayMinMs"], 3000)
        self.assertEqual(runtime_config["recipientDelayMaxMs"], 8000)

        sent_stickers = []
        delays = []
        friends = [{"display": f"目标好友{index}"} for index in range(3)]
        im = types.SimpleNamespace(
            last_scan={},
            wait_ready=lambda: {
                "status": tasks.STATUS_READY,
                "user_id": "id",
                "nickname": "nick",
            },
            iter_find_and_select=lambda _targets: iter(friends),
            send_native_sticker=lambda _friend, sticker: (
                sent_stickers.append(sticker)
                or {"ok": True, "via": "native", "sticker": sticker}
            ),
            fold_groups=lambda: {},
            detach=lambda: None,
        )
        page = types.SimpleNamespace(wait_for_timeout=delays.append)
        context = types.SimpleNamespace(
            set_default_navigation_timeout=lambda _timeout: None,
            set_default_timeout=lambda _timeout: None,
            new_page=lambda: page,
            add_cookies=lambda _cookies: None,
            close=lambda: None,
        )
        logger = types.SimpleNamespace(
            info=lambda *_args: None,
            debug=lambda *_args: None,
            warning=lambda *_args: None,
        )
        with (
            patch.object(tasks, "DouyinIM", lambda *_args, **_kwargs: im),
            patch.object(tasks, "logger", logger),
            patch.object(tasks, "config", {**runtime_config, "streakSticker": "开心"}),
            patch.object(tasks.random, "randint", side_effect=[3000, 8000, 3000]) as randint,
        ):
            tasks.do_user_task(
                types.SimpleNamespace(new_context=lambda: context),
                "account",
                [],
                [friend["display"] for friend in friends],
            )

        self.assertEqual(sent_stickers, ["续火花"] * 3)
        self.assertEqual(delays, [3000, 8000, 3000])
        self.assertEqual(randint.call_args_list, [call(3000, 8000)] * 3)

    def test_probe_persists_allowlisted_result_for_one_chat(self):
        probe_calls = []
        friend = {"display": "private friend", "conv_id": "private-id"}
        im = types.SimpleNamespace(
            wait_ready=lambda: {"status": tasks.STATUS_READY},
            iter_find_and_select=lambda selected: (
                probe_calls.append(list(selected)) or iter([friend])
            ),
            probe_native_sticker=lambda hit, sticker, screenshot_path: {
                "ok": True,
                "sticker_name": sticker,
                "screenshot": "account-panel.png",
                "sticker_clicked": False,
                "text_input": False,
                "message_sent": False,
                "panel_wait_ms": 42,
                "button_selector": "svg.messageMsgInputiconAction",
                "panel_selector": ".componentsemojiemojiPanel",
                "sticker_item_selector": ".emojiEmojiItememojiItem",
                "sticker_match": "description-exact",
            },
            detach=lambda: None,
        )
        page = types.SimpleNamespace()
        context = types.SimpleNamespace(
            set_default_navigation_timeout=lambda _timeout: None,
            set_default_timeout=lambda _timeout: None,
            new_page=lambda: page,
            add_cookies=lambda _cookies: None,
            close=lambda: None,
        )
        logger = types.SimpleNamespace(
            info=lambda *_args: None,
            debug=lambda *_args: None,
            warning=lambda *_args: None,
            error=lambda *_args: None,
        )
        config = {
            "browserActionTimeout": 1,
            "imScanTimeout": 1,
            "imReadyTimeout": 1,
            "friendListSettleMs": 1,
            "imMaxSteps": 1,
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "core.tasks.DouyinIM", lambda *_args, **_kwargs: im
        ), patch.object(tasks, "logger", logger), patch.object(tasks, "config", config):
            self.assertTrue(
                tasks.do_user_probe(
                    types.SimpleNamespace(new_context=lambda: context),
                    "account",
                    [{"name": "sessionid", "value": "redacted"}],
                    ["first", "second"],
                    artifact_dir=tmp,
                )
            )
            with open(os.path.join(tmp, "account.json"), encoding="utf-8") as handle:
                evidence = json.load(handle)

        self.assertEqual(probe_calls, [["first"]])
        self.assertEqual(evidence["sticker_name"], DEFAULT_STREAK_STICKER)
        self.assertTrue(evidence["chat_selected"])
        self.assertNotIn("display", evidence)
        self.assertNotIn("conv_id", evidence)

    def test_probe_never_clicks_sticker_item_or_composer(self):
        button = MagicMock()
        panel = MagicMock()
        item = MagicMock()
        page = MagicMock()
        im = object.__new__(DouyinIM)
        im.page = page
        im._first_visible_with_selector = MagicMock(
            return_value=("button-selector", button)
        )
        im._wait_visible_with_selector = MagicMock(
            return_value=("panel-selector", panel)
        )
        im._find_native_sticker_match = MagicMock(
            return_value=("description-exact", item)
        )

        with patch("core.douyin_im.get_config", return_value={"streakStickerCategory": ""}):
            result = im.probe_native_sticker({"conv_id": "safe"}, DEFAULT_STREAK_STICKER)

        self.assertTrue(result["ok"])
        button.click.assert_called_once_with(force=True)
        item.click.assert_not_called()
        page.keyboard.type.assert_not_called()
        page.keyboard.press.assert_called_once_with("Escape")

    def _assert_attempts(self, *, retryable_before_dispatch, sends, reselects):
        calls = {"send": 0, "reselect": 0}

        def reselect():
            calls["reselect"] += 1
            return True

        def send_native_sticker(_friend, _sticker):
            calls["send"] += 1
            return {
                "ok": calls["send"] == 2,
                "dispatched": not retryable_before_dispatch,
                "retryable_before_dispatch": retryable_before_dispatch,
            }

        friend = {"display": "目标好友", "reselect": reselect}
        im = types.SimpleNamespace(
            last_scan={},
            wait_ready=lambda: {"status": tasks.STATUS_READY, "user_id": "id", "nickname": "nick"},
            iter_find_and_select=lambda _targets: iter([friend]),
            send_native_sticker=send_native_sticker,
            fold_groups=lambda: {},
            detach=lambda: None,
        )
        page = types.SimpleNamespace(wait_for_timeout=lambda _timeout: None)
        context = types.SimpleNamespace(
            set_default_navigation_timeout=lambda _timeout: None,
            set_default_timeout=lambda _timeout: None,
            new_page=lambda: page,
            add_cookies=lambda _cookies: None,
            close=lambda: None,
        )
        config = {
            "browserActionTimeout": 1,
            "imScanTimeout": 1,
            "imReadyTimeout": 1,
            "friendListSettleMs": 1,
            "imMaxSteps": 1,
            "streakSticker": "续火花",
            "recipientDelayMinMs": 3000,
            "recipientDelayMaxMs": 8000,
        }
        logger = types.SimpleNamespace(
            info=lambda *_args: None,
            debug=lambda *_args: None,
            warning=lambda *_args: None,
        )
        with (
            patch.object(tasks, "DouyinIM", lambda *_args, **_kwargs: im),
            patch.object(tasks, "logger", logger),
            patch.object(tasks, "config", config),
        ):
            tasks.do_user_task(
                types.SimpleNamespace(new_context=lambda: context), "account", [], ["目标好友"]
            )

        self.assertEqual(calls, {"send": sends, "reselect": reselects})

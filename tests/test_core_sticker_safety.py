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
    SEL_STICKER_ACTIONS,
    SEL_STICKER_BUTTONS,
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

    def test_generic_composer_actions_are_not_direct_sticker_selectors(self):
        self.assertNotIn(SEL_STICKER_ACTIONS, SEL_STICKER_BUTTONS)

    def test_generic_trigger_is_accepted_only_after_native_panel_opens(self):
        im = object.__new__(DouyinIM)
        im.page = MagicMock()
        rejected = MagicMock()
        accepted = MagicMock()
        panel = MagicMock()
        im._native_sticker_trigger_candidates = MagicMock(
            return_value=[
                ("generic-action-0", rejected, False),
                ("generic-action-1", accepted, False),
            ]
        )
        im._first_visible_with_selector = MagicMock(return_value=(None, None))
        im._wait_visible_with_selector = MagicMock(
            side_effect=[(None, None), (None, None), (".componentsemojiemojiPanel", panel)]
        )

        opened = im._open_native_sticker_panel()

        self.assertIs(opened["panel"], panel)
        self.assertEqual(opened["button_selector"], "generic-action-1")
        self.assertEqual(opened["panel_selector"], ".componentsemojiemojiPanel")
        self.assertEqual(
            opened["trigger_attempts"],
            [
                {"selector": "generic-action-0", "opened": False},
                {"selector": "generic-action-1", "opened": True},
            ],
        )
        rejected.click.assert_called_once()
        accepted.click.assert_called_once()

    def test_panel_control_retries_synthetic_click_before_giving_up(self):
        im = object.__new__(DouyinIM)
        im.page = MagicMock()
        button, panel = MagicMock(), MagicMock()
        im._first_visible_with_selector = MagicMock(return_value=(None, None))
        im._native_sticker_trigger_candidates = MagicMock(return_value=[("emoji-control", button, True)])
        im._wait_visible_with_selector = MagicMock(side_effect=[(None, None), (".emojiPanel", panel)])
        opened = im._open_native_sticker_panel()
        self.assertIs(opened["panel"], panel)
        button.click.assert_called_once()
        self.assertEqual(button.dispatch_event.call_args.args, ("click",))

    def test_diagnostic_screenshot_runs_after_panel_interaction(self):
        im = object.__new__(DouyinIM)
        im.page = MagicMock()
        button, panel = MagicMock(), MagicMock()
        im._first_visible_with_selector = MagicMock(return_value=(None, None))
        im._native_sticker_trigger_candidates = MagicMock(return_value=[("emoji-control", button, True)])
        im._wait_visible_with_selector = MagicMock(return_value=(".emojiPanel", panel))
        def screenshot(*args):
            button.click.assert_called_once()
            im._wait_visible_with_selector.assert_called_once()
            return "post.png"
        im._screenshot_trigger = MagicMock(side_effect=screenshot)
        opened = im._open_native_sticker_panel(diagnostic_screenshot_path="panel.png")
        self.assertIs(opened["panel"], panel)
        self.assertEqual(opened["diagnostic_screenshot_post_click"], "post.png")

    def test_empty_trigger_discovery_polls_until_late_button_appears(self):
        im = object.__new__(DouyinIM)
        im.page = MagicMock()
        button, panel = MagicMock(), MagicMock()
        im._first_visible_with_selector = MagicMock(return_value=(None, None))
        im._native_sticker_trigger_candidates = MagicMock(
            side_effect=[[], [("late-action", button, False)]]
        )
        im._wait_visible_with_selector = MagicMock(return_value=(".emojiPanel", panel))
        opened = im._open_native_sticker_panel()
        self.assertIs(opened["panel"], panel)
        im.page.wait_for_timeout.assert_called_once()
        button.click.assert_called_once()
        self.assertEqual(SEL_STICKER_ACTIONS, "svg.messageMsgInputiconAction")

    def test_empty_trigger_discovery_stops_at_shared_deadline(self):
        im = object.__new__(DouyinIM)
        im.page = MagicMock()
        im._first_visible_with_selector = MagicMock(return_value=(None, None))
        im._native_sticker_trigger_candidates = MagicMock(return_value=[])
        clock = [0.0]
        im.page.wait_for_timeout.side_effect = lambda ms: clock.__setitem__(0, clock[0] + ms / 1000)
        with patch("core.douyin_im.time.monotonic", side_effect=lambda: clock[0]):
            opened = im._open_native_sticker_panel()
        self.assertIsNone(opened["panel"])
        self.assertGreater(im._native_sticker_trigger_candidates.call_count, 1)
        self.assertGreaterEqual(opened["panel_wait_ms"], 2999)
        self.assertLessEqual(opened["panel_wait_ms"], 3000)

    def test_visible_wait_honors_elapsed_deadline(self):
        im = object.__new__(DouyinIM)
        im.page = MagicMock()
        im._first_visible_with_selector = MagicMock(return_value=(None, None))

        with patch("core.douyin_im.time.monotonic", side_effect=[0.0, 0.0, 0.101, 0.301]):
            selector, panel = im._wait_visible_with_selector(
                (".never-appears",), timeout_ms=200, poll_ms=100
            )

        self.assertIsNone(selector)
        self.assertIsNone(panel)
        self.assertEqual(im.page.wait_for_timeout.call_args_list, [call(100), call(99)])

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

    def test_task_failure_makes_workflow_fail_and_closes_browser(self):
        browser = MagicMock()
        with patch.object(tasks, "userData", [{"cookies": [], "targets": ["target"]}]), \
             patch.object(tasks, "get_browser", return_value=browser), \
             patch.object(tasks, "do_user_task", return_value=False):
            with self.assertRaises(RuntimeError):
                tasks.runTasks()
        browser.close.assert_called_once()

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
                "screenshot_scope": "sticker-panel",
                "diagnostic_screenshot_pre_click": "account-panel-trigger-pre-click.png",
                "diagnostic_screenshot_post_click": "account-panel-trigger-post-click.png",
                "trigger_attempts": [
                    {
                        "selector": '.messageMsgInput [data-e2e*="emoji" i]',
                        "opened": True,
                    }
                ],
                "pre_click_panel_selector": None,
                "post_click_panel_selector": ".componentsemojiemojiPanel",
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
        self.assertEqual(
            evidence["trigger_attempts"],
            [
                {
                    "selector": '.messageMsgInput [data-e2e*="emoji" i]',
                    "opened": True,
                }
            ],
        )
        self.assertEqual(
            evidence["diagnostic_screenshot_pre_click"],
            "account-panel-trigger-pre-click.png",
        )
        self.assertNotIn("display", evidence)
        self.assertNotIn("conv_id", evidence)

    def test_sticker_dispatches_loaded_image_once(self):
        for direct_image in (False, True):
            with self.subTest(direct_image=direct_image):
                im = object.__new__(DouyinIM)
                im.page = MagicMock()
                im.mon = types.SimpleNamespace(sends=[])
                panel, item = MagicMock(), MagicMock()
                panel.get_by_text.return_value.first.count.return_value = 0
                im._open_native_sticker_panel = MagicMock(return_value={"panel": panel})
                im._find_native_sticker_match = MagicMock(return_value=("description-exact", item))
                im._sticker_resource_key = MagicMock(return_value="sticker")
                im._native_sticker_state = MagicMock(return_value={})
                im._wait_native_sticker = MagicMock(return_value={"count": 1})
                item.evaluate.return_value = direct_image
                image = item if direct_image else item.locator.return_value.first
                result = im.send_native_sticker({"display": "target"})
                self.assertTrue(result["ok"])
                im.page.wait_for_timeout.assert_called_once_with(3000)
                image.dispatch_event.assert_called_once_with("click")
                im.page.wait_for_function.assert_called_once_with(
                    "img => img.complete && img.naturalWidth > 0",
                    arg=image.element_handle.return_value, timeout=10000,
                )
                if not direct_image:
                    item.dispatch_event.assert_not_called()
                im.page.mouse.click.assert_not_called()
                item.click.assert_not_called()

                image.dispatch_event.reset_mock()
                im.page.wait_for_function.side_effect = TimeoutError("image not loaded")
                with self.assertRaises(TimeoutError):
                    im.send_native_sticker({"display": "target"})
                image.dispatch_event.assert_not_called()

    def test_local_sticker_bubble_requires_server_receipt(self):
        im = object.__new__(DouyinIM)
        im.page = MagicMock()
        im._native_sticker_state = MagicMock(return_value={
            "count": 1, "outgoingCount": 1, "lastHasImage": True, "lastResource": "sticker",
        })
        for accepted in (False, True):
            im.mon = types.SimpleNamespace(sends=[{"ok": accepted}])
            self.assertEqual(bool(im._wait_native_sticker({}, "sticker", 1, 0)), accepted)
        im.mon.sends = []
        with patch("core.douyin_im.time.monotonic", side_effect=[0, 0, 2]):
            self.assertIsNone(im._wait_native_sticker({}, "sticker", 1, 0))

    def test_probe_never_clicks_sticker_item_or_composer(self):
        button = MagicMock()
        panel = MagicMock()
        item = MagicMock()
        page = MagicMock()
        im = object.__new__(DouyinIM)
        im.page = page
        im._open_native_sticker_panel = MagicMock(
            return_value={
                "button_selector": "button-selector",
                "panel_selector": "panel-selector",
                "panel": panel,
                "panel_wait_ms": 1,
                "panel_wait_limit_ms": 3000,
                "diagnostic_screenshot_pre_click": None,
                "diagnostic_screenshot_post_click": None,
                "trigger_attempts": [
                    {"selector": "button-selector", "opened": True}
                ],
                "pre_click_panel_selector": None,
                "post_click_panel_selector": "panel-selector",
            }
        )
        im._find_native_sticker_match = MagicMock(
            return_value=("description-exact", item)
        )

        with patch("core.douyin_im.get_config", return_value={"streakStickerCategory": ""}):
            result = im.probe_native_sticker({"conv_id": "safe"}, DEFAULT_STREAK_STICKER)

        self.assertTrue(result["ok"])
        self.assertTrue(result["sticker_image_loaded"])
        page.wait_for_timeout.assert_called_once_with(3000)
        page.wait_for_function.assert_called_once()
        button.click.assert_not_called()
        item.click.assert_not_called()
        page.keyboard.type.assert_not_called()
        page.keyboard.press.assert_called_once_with("Escape")

    def test_sticker_items_can_appear_after_panel_and_settle_delay(self):
        im = object.__new__(DouyinIM)
        im.page = MagicMock()
        panel, item = MagicMock(), MagicMock()
        im._sticker_resource_key = MagicMock(return_value="fire-resource")
        im._find_native_sticker_match = MagicMock(side_effect=[(None, None), ("description-exact", item)])
        with patch("core.douyin_im.get_config", return_value={"streakStickerCategory": ""}):
            _, selected, _ = im._prepare_native_sticker(panel, "续火花")
        self.assertIs(selected, item)
        self.assertEqual(im.page.wait_for_timeout.call_args_list, [call(3000), call(100)])
        item.click.assert_not_called()

    def test_probe_rejects_unloaded_sticker_instead_of_reporting_success(self):
        im = object.__new__(DouyinIM)
        im.page = MagicMock()
        panel, item = MagicMock(), MagicMock()
        im._open_native_sticker_panel = MagicMock(return_value={"panel": panel})
        im._find_native_sticker_match = MagicMock(return_value=("description-exact", item))
        im.page.wait_for_function.side_effect = TimeoutError("image not loaded")
        with patch("core.douyin_im.get_config", return_value={"streakStickerCategory": ""}):
            result = im.probe_native_sticker({"conv_id": "safe"})
        self.assertFalse(result["ok"])
        self.assertFalse(result["sticker_image_loaded"])
        self.assertFalse(result["sticker_clicked"])

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

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.modules.setdefault('cloakbrowser', types.SimpleNamespace(launch=lambda **kwargs: None))

from core import password_login as login
from core import tasks
from utils import export_github_env


class PasswordLoginTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'DOUYIN_PHONE': '13800000000',
                                        'DOUYIN_PASSWORD': 'fake $value\\n"quote'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def page(self, outcome='positive'):
        page = MagicMock()
        page.url = 'https://www.douyin.com/chat/'
        state = {'submitted': False}

        def loc(visible=False):
            item = MagicMock()
            item.count.return_value = 1 if visible else 0
            item.nth.return_value = item
            item.get_attribute.return_value = None
            item.is_visible.side_effect = visible if callable(visible) else lambda: bool(visible)
            return item

        phone, password = loc(True), loc(True)
        phone.is_visible.side_effect = lambda: not state['submitted']
        submit = loc(True)
        submit.click.side_effect = lambda **kwargs: state.update(submitted=True)
        switch = loc(True)
        switch.is_visible.side_effect = lambda: not state['submitted']
        country = loc(True)
        country.input_value.return_value = '+86'
        hidden = loc(False)
        login_box = loc(True)
        login_box.is_visible.side_effect = lambda: not state['submitted']
        challenge = loc(True)
        challenge.is_visible.side_effect = lambda: state['submitted'] and outcome == 'challenge'
        rejected = loc(True)
        rejected.is_visible.side_effect = lambda: state['submitted'] and outcome == 'rejected'
        positive = loc(True)
        positive.is_visible.side_effect = lambda: state['submitted'] and outcome == 'positive'

        def locate(selector):
            return {login.PHONE_SELECTOR: phone, login.PASSWORD_SELECTOR: password,
                    login.SUBMIT_SELECTOR: submit, login.LOGIN_SELECTOR: login_box,
                    login.CHALLENGE_SELECTOR: challenge,
                    '[data-e2e="user-avatar-card"], [data-e2e="conversation-item"]': positive
                    }.get(selector, hidden)

        page.locator.side_effect = locate
        page.get_by_role.return_value = country
        page.get_by_text.side_effect = lambda text, **kwargs: (
            switch if text == login.PASSWORD_METHOD else rejected if text == login.REJECTION_TEXT else hidden)
        return page, phone, password, submit

    def test_scoped_credentials_cannot_mix_with_global_pair(self):
        with patch.dict(os.environ, {'DOUYIN_PHONE_ACCOUNT': '13900000000'}):
            self.assertEqual(login.credentials_for('account', allow_global=True),
                             (None, None, 'credentials_missing'))
        self.assertEqual(login.credentials_for('account', allow_global=False),
                         (None, None, 'credentials_missing'))

    def test_password_is_not_stripped_or_decoded(self):
        with patch.dict(os.environ, {'DOUYIN_PASSWORD': '  fake\\n"$text  '}):
            self.assertEqual(login.credentials_for('account', allow_global=True)[1],
                             '  fake\\n"$text  ')

    def test_phone_accepts_common_display_format_without_changing_digits(self):
        for formatted in ('+86 138-0000-0000', '0086 13800000000', '8613800000000',
                          '(+86) 13800000000', '１３８００００００００', '"13800000000"'):
            with self.subTest(formatted=formatted), patch.dict(os.environ, {'DOUYIN_PHONE': formatted}):
                self.assertEqual(login.credentials_for('account', allow_global=True)[0], '13800000000')
        with patch.dict(os.environ, {'DOUYIN_PHONE': '手机号:13800000000'}):
            self.assertEqual(login.credentials_for('account', allow_global=True)[2], 'phone_format_invalid')

    def test_official_english_login_copy_is_supported(self):
        self.assertIsNotNone(login.PASSWORD_METHOD.fullmatch('Use Password'))
        self.assertIsNotNone(login.CHALLENGE_TEXT.search('Drag the slider to complete security verification'))
        self.assertIsNotNone(login.REJECTION_TEXT.search('Incorrect password'))

    def test_refuses_non_official_origin_without_filling(self):
        page, phone, password, submit = self.page()
        for url in ('http://www.douyin.com/chat/', 'https://www.douyin.com.evil.test/',
                    'https://www.douyin.com:8443/', 'https://user@www.douyin.com/'):
            page.url = url
            result = login.attempt_password_login(page, 'account', allow_global=True)
            self.assertEqual(result['reason'], 'unexpected_origin')
        phone.fill.assert_not_called()
        password.fill.assert_not_called()
        submit.click.assert_not_called()

    def test_challenge_after_submission_stops_with_one_attempt(self):
        page, phone, password, submit = self.page('challenge')
        result = login.attempt_password_login(page, 'account', allow_global=True)
        self.assertEqual(result['reason'], 'manual_verification_required')
        self.assertFalse(result['ok'])
        submit.click.assert_called_once()

    def test_rejection_never_retries_and_never_verifies(self):
        page, phone, password, submit = self.page('rejected')
        result = login.attempt_password_login(page, 'account', allow_global=True)
        self.assertEqual(result['reason'], 'login_rejected')
        self.assertFalse(result['ok'])
        submit.click.assert_called_once()

    def test_positive_ui_still_needs_fresh_chat_preflight(self):
        page, phone, password, submit = self.page()
        result = login.attempt_password_login(page, 'account', allow_global=True)
        self.assertEqual(result['reason'], 'awaiting_chat_preflight')
        self.assertFalse(result['ok'])

    def test_mode_tab_retry_never_repeats_credential_submission(self):
        page, phone, password, submit = self.page()
        password.wait_for.side_effect = [TimeoutError(), None]
        result = login.attempt_password_login(page, 'account', allow_global=True)
        self.assertEqual(result['reason'], 'awaiting_chat_preflight')
        page.get_by_text(login.PASSWORD_METHOD).dispatch_event.assert_called_once_with('click', timeout=5000)
        submit.click.assert_called_once()

    def test_exception_never_serializes_fill_arguments(self):
        page, phone, password, submit = self.page()
        password.fill.side_effect = RuntimeError('fake secret embedded in Playwright error')
        result = login.attempt_password_login(page, 'account', allow_global=True)
        self.assertEqual(result['reason'], 'login_operation_failed')
        self.assertNotIn('fake secret', json.dumps(result))
        submit.click.assert_not_called()

    def test_list_timeout_without_login_form_does_not_try_password(self):
        im = MagicMock()
        im.wait_ready.return_value = {'status': 'TIMEOUT'}
        with patch.object(tasks, 'DouyinIM', return_value=im), \
             patch.object(tasks, 'login_visible', return_value=False), \
             patch.object(tasks, 'attempt_password_login') as attempt:
            result_im, ready = tasks._open_ready_im(MagicMock())
        attempt.assert_not_called()
        self.assertEqual(ready['status'], 'TIMEOUT')

    def test_failed_fresh_preflight_cannot_be_reported_as_login_success(self):
        first, fresh = MagicMock(), MagicMock()
        first.wait_ready.return_value = {'status': 'EXPIRED'}
        fresh.wait_ready.return_value = {'status': 'TIMEOUT'}
        result = {'attempted': True, 'submitted': True, 'ok': False,
                  'reason': 'awaiting_chat_preflight'}
        with patch.object(tasks, 'DouyinIM', side_effect=[first, fresh]), \
             patch.object(tasks, 'login_visible', return_value=True), \
             patch.object(tasks, 'attempt_password_login', return_value=result), \
             patch.object(tasks, 'save_diagnostic') as diagnostic:
            result_im, ready = tasks._open_ready_im(MagicMock())
        self.assertEqual(ready['status'], 'TIMEOUT')
        self.assertFalse(diagnostic.call_args.args[2]['ok'])
        first.detach.assert_called_once()

    def test_diagnostic_masks_credentials_and_omits_extra_fields(self):
        page = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            login.save_diagnostic(page, Path(tmp) / 'result', {
                'ok': False, 'reason': 'manual_verification_required', 'password': 'do-not-store'})
            stored = (Path(tmp) / 'result.json').read_text()
        self.assertNotIn('do-not-store', stored)
        masks = page.screenshot.call_args.kwargs['mask']
        self.assertGreaterEqual(len(masks), 5)
        page.locator.assert_any_call('input, textarea, [contenteditable="true"]')

    def test_export_credentials_only_to_runner_environment(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'VARS_JSON': '{}', 'SECRETS_JSON': json.dumps({
                'DOUYIN_PHONE': '13800000000', 'DOUYIN_PASSWORD': 'fake\\n$quote',
                'COOKIES_ACCOUNT': '[]'}), 'GITHUB_ENV': str(Path(tmp) / 'env')
        }):
            old = os.getcwd()
            try:
                os.chdir(tmp)
                export_github_env.main()
                dotenv = Path('.env').read_text()
                envfile = Path('env').read_text()
            finally:
                os.chdir(old)
        self.assertNotIn('DOUYIN_PHONE', dotenv)
        self.assertNotIn('DOUYIN_PASSWORD', dotenv)
        self.assertIn('DOUYIN_PASSWORD<<', envfile)


if __name__ == '__main__':
    unittest.main()

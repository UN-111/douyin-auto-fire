import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import password_login as login
from core.sms_login import CODE_SELECTOR, SEND_CODE, SMS_METHOD, attempt_sms_login


class SmsHandoffTests(unittest.TestCase):
    def run_handoff(self, response_code=0, supplied='123456', sms_visible=True,
                    country_count=1, code_count=1, phone_count=1, native=False,
                    target_hit=True):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ, {'DOUYIN_OTP_DIR': directory}):
            page = MagicMock()
            page._original = None
            page.url = 'https://www.douyin.com/chat/'
            page.evaluate.side_effect = lambda expression: (
                [{'type': 'click', 'x': 900, 'y': 375, 'trusted': False,
                  'tag': 'span', 'target': True}]
                if expression == 'window.__douyinSmsPointerEvents' else False)
            hidden = MagicMock()
            hidden.count.return_value = 0
            fields = {}
            for selector in (CODE_SELECTOR, login.PHONE_SELECTOR, login.COUNTRY_SELECTOR,
                             login.SUBMIT_SELECTOR):
                field = MagicMock()
                field.count.return_value = 1
                field.is_visible.return_value = True
                field.is_enabled.return_value = True
                field.evaluate.return_value = '+86'
                field.evaluate_all.return_value = []
                fields[selector] = field
            fields[login.COUNTRY_SELECTOR].count.return_value = country_count
            fields[CODE_SELECTOR].count.return_value = code_count
            fields[login.PHONE_SELECTOR].count.return_value = phone_count
            hidden.evaluate_all.return_value = []
            page.locator.side_effect = lambda selector: fields.get(selector, hidden)
            send, switch = MagicMock(), MagicMock()
            for item in (send, switch):
                item.count.return_value = 1
                item.is_visible.return_value = True
                item.is_enabled.return_value = True
            fields[CODE_SELECTOR].nth.return_value = fields[CODE_SELECTOR]
            fields[CODE_SELECTOR].is_visible.return_value = sms_visible
            switch.dispatch_event.side_effect = lambda *a, **kw: setattr(
                fields[CODE_SELECTOR].is_visible, 'return_value', True)
            page.get_by_text.side_effect = lambda pattern: (
                send if pattern == SEND_CODE else switch if pattern == SMS_METHOD else hidden)
            listeners = {}
            password_listener = lambda: None
            registered = {password_listener}

            def register(event, callback):
                listeners[event] = callback
                registered.add(callback)

            page.on.side_effect = register
            # pyee raises KeyError when another response listener exists but
            # the requested callback was never registered.
            page.remove_listener.side_effect = lambda event, callback: registered.remove(callback)
            response = MagicMock()
            response.url = 'https://sso.douyin.com/passport/web/send_code/'
            response.request.method = 'POST'
            response.status = 200
            response.json.return_value = {'data': {'error_code': response_code}}
            send.inner_text.return_value = 'Send code'
            send.evaluate.return_value = {'x': 900, 'y': 375, 'hit': target_hit}
            click = send.dispatch_event
            if native:
                page._original = MagicMock()
            click.side_effect = lambda *args, **kwargs: listeners['response'](response)

            def wait(_):
                ready = Path(directory) / 'ready'
                if ready.exists():
                    (Path(directory) / ready.read_text()).write_text(supplied)

            page.wait_for_timeout.side_effect = wait
            result = {'login_response_signal': 'verification_required'}
            with patch.object(login, 'save_diagnostic') as diagnostic:
                attempt_sms_login(page, '13800000000', result)
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertEqual(registered, {password_listener})
            if sms_visible:
                switch.dispatch_event.assert_not_called()
            else:
                switch.dispatch_event.assert_called_once()
            if result.get('sms_click_completed'):
                click.assert_called_once_with('click', {'button': 0, 'buttons': 0,
                    'detail': 1, 'clientX': 900, 'clientY': 375}, timeout=10000)
            else:
                click.assert_not_called()
            if native:
                page._original.click.assert_not_called()
                page._original.mouse_click.assert_not_called()
                page.mouse.click.assert_not_called()
            send.click.assert_not_called()
            return result, fields, send, diagnostic

    def test_code_enters_same_page_once_and_old_rejection_is_cleared(self):
        result, fields, send, diagnostic = self.run_handoff()
        self.assertEqual(result['reason'], 'sms_submitted')
        self.assertTrue(result['sms_requested'])
        self.assertNotIn('login_response_signal', result)
        fields[CODE_SELECTOR].fill.assert_called_once_with('123456', timeout=10000)
        fields[CODE_SELECTOR].focus.assert_called_once_with(timeout=10000)
        fields[login.SUBMIT_SELECTOR].dispatch_event.assert_called_once()
        self.assertEqual(diagnostic.call_count, 3)

    def test_rejected_sms_request_never_waits_or_submits(self):
        result, fields, send, diagnostic = self.run_handoff(response_code=1009)
        self.assertEqual(result['reason'], 'sms_request_rejected')
        fields[CODE_SELECTOR].fill.assert_not_called()
        fields[login.SUBMIT_SELECTOR].dispatch_event.assert_not_called()
        self.assertEqual(diagnostic.call_count, 2)

    def test_switches_to_sms_only_when_code_field_is_hidden(self):
        result, fields, send, _ = self.run_handoff(sms_visible=False)
        self.assertEqual(result['reason'], 'sms_submitted')
        fields[CODE_SELECTOR].fill.assert_called_once_with('123456', timeout=10000)

    def test_invalid_code_is_erased_without_submission(self):
        result, fields, _, _ = self.run_handoff(supplied='bad\n1234')
        self.assertEqual(result['reason'], 'sms_code_invalid')
        fields[CODE_SELECTOR].fill.assert_not_called()
        fields[login.SUBMIT_SELECTOR].dispatch_event.assert_not_called()

    def test_ambiguous_country_never_requests_sms_or_reads_value(self):
        for count in (0, 2):
            result, fields, send, diagnostic = self.run_handoff(country_count=count)
            self.assertEqual(result['reason'], 'sms_country_selector_missing')
            self.assertEqual(result['sms_country_selector_count'], count)
            fields[login.COUNTRY_SELECTOR].evaluate.assert_not_called()
            send.click.assert_not_called()
            diagnostic.assert_not_called()

    def test_ambiguous_code_preserves_reason_and_existing_listener(self):
        result, fields, send, diagnostic = self.run_handoff(code_count=2)
        self.assertEqual(result['reason'], 'ambiguous_sms_form')
        self.assertEqual(result['sms_form_counts'], {'code': 2, 'phone': 1})
        fields[CODE_SELECTOR].fill.assert_not_called()
        send.click.assert_not_called()
        diagnostic.assert_not_called()

    def test_ambiguous_phone_never_fills_or_requests_sms(self):
        result, fields, send, diagnostic = self.run_handoff(phone_count=2)
        self.assertEqual(result['reason'], 'ambiguous_sms_form')
        fields[login.PHONE_SELECTOR].fill.assert_not_called()
        fields[CODE_SELECTOR].fill.assert_not_called()
        send.click.assert_not_called()
        diagnostic.assert_not_called()

    def test_dom_send_click_is_used_once_with_safe_response_status(self):
        result, _, _, _ = self.run_handoff(native=True)
        self.assertEqual(result['reason'], 'sms_submitted')
        self.assertTrue(result['sms_response_seen'])
        self.assertEqual(result['sms_http_status'], 200)
        self.assertFalse(result['sms_click_event_trusted'])
        self.assertEqual(result['sms_pointer_events'][0]['target'], True)

    def test_obstructed_send_control_never_clicks_or_requests_code(self):
        result, fields, _, diagnostic = self.run_handoff(native=True, target_hit=False)
        self.assertEqual(result['reason'], 'sms_send_control_obstructed')
        self.assertNotIn('sms_click_completed', result)
        self.assertNotIn('sms_requested', result)
        fields[CODE_SELECTOR].fill.assert_not_called()
        self.assertEqual(diagnostic.call_count, 1)

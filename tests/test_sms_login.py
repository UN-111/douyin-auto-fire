import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import password_login as login
from core.sms_login import CODE_SELECTOR, SEND_CODE, SMS_METHOD, attempt_sms_login


class SmsHandoffTests(unittest.TestCase):
    def run_handoff(self, response_code=0, supplied='123456', sms_visible=True,
                    country_count=1):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ, {'DOUYIN_OTP_DIR': directory}):
            page = MagicMock()
            page._original = None
            page.url = 'https://www.douyin.com/chat/'
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
                fields[selector] = field
            fields[login.COUNTRY_SELECTOR].count.return_value = country_count
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
            page.on.side_effect = lambda event, callback: listeners.update({event: callback})
            response = MagicMock()
            response.url = 'https://sso.douyin.com/passport/web/send_code/'
            response.request.method = 'POST'
            response.json.return_value = {'data': {'error_code': response_code}}
            send.dispatch_event.side_effect = lambda *args, **kwargs: listeners['response'](response)

            def wait(_):
                ready = Path(directory) / 'ready'
                if ready.exists():
                    (Path(directory) / ready.read_text()).write_text(supplied)

            page.wait_for_timeout.side_effect = wait
            result = {'login_response_signal': 'verification_required'}
            with patch.object(login, 'save_diagnostic') as diagnostic:
                attempt_sms_login(page, '13800000000', result)
            self.assertEqual(list(Path(directory).iterdir()), [])
            if sms_visible:
                switch.dispatch_event.assert_not_called()
            else:
                switch.dispatch_event.assert_called_once()
            return result, fields, send, diagnostic

    def test_code_enters_same_page_once_and_old_rejection_is_cleared(self):
        result, fields, send, diagnostic = self.run_handoff()
        self.assertEqual(result['reason'], 'sms_submitted')
        self.assertTrue(result['sms_requested'])
        self.assertNotIn('login_response_signal', result)
        fields[CODE_SELECTOR].fill.assert_called_once_with('123456', timeout=10000)
        fields[login.SUBMIT_SELECTOR].dispatch_event.assert_called_once()
        send.dispatch_event.assert_called_once()
        diagnostic.assert_called_once()

    def test_rejected_sms_request_never_waits_or_submits(self):
        result, fields, send, diagnostic = self.run_handoff(response_code=1009)
        self.assertEqual(result['reason'], 'sms_request_rejected')
        fields[CODE_SELECTOR].fill.assert_not_called()
        fields[login.SUBMIT_SELECTOR].dispatch_event.assert_not_called()
        diagnostic.assert_not_called()
        send.dispatch_event.assert_called_once()

    def test_switches_to_sms_only_when_code_field_is_hidden(self):
        result, fields, send, _ = self.run_handoff(sms_visible=False)
        self.assertEqual(result['reason'], 'sms_submitted')
        fields[CODE_SELECTOR].fill.assert_called_once_with('123456', timeout=10000)
        send.dispatch_event.assert_called_once()

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
            send.dispatch_event.assert_not_called()
            diagnostic.assert_not_called()

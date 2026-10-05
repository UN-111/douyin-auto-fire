"""Opt-in SMS login on the existing page; only Actions operates the browser."""

import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit


SMS_METHOD = re.compile(r'^(手机登录|短信登录|验证码登录|Use Phone)$', re.I)
SEND_CODE = re.compile(r'^(获取验证码|发送验证码|Get code|Send code|Get verification code)$', re.I)
CODE_SELECTOR = ('input[autocomplete="one-time-code"], input[placeholder*="验证码"], '
                 'input[placeholder*="code" i]:not([name="web-login-area-code-input"])')


def attempt_sms_login(page, phone, result):
    from core.password_login import (PHONE_SELECTOR, COUNTRY_SELECTOR, SUBMIT_SELECTOR,
                                     CHALLENGE_SELECTOR, any_visible, country_code,
                                     official_origin, fill_login_field, save_diagnostic)
    root = Path(os.environ['DOUYIN_OTP_DIR'])
    challenge = uuid.uuid4().hex
    code_path = root / challenge
    ready_path = root / 'ready'
    response_codes = []
    listening = False

    def on_response(response):
        parsed = urlsplit(response.url)
        if (response.request.method != 'POST' or parsed.scheme != 'https'
                or not (parsed.hostname or '').endswith('.douyin.com')):
            return
        if not re.search(r'send_code|send_sms|sendsms|sms/send', parsed.path, re.I):
            if 'passport' in parsed.path.lower():
                result['sms_other_passport_response_count'] = result.get(
                    'sms_other_passport_response_count', 0) + 1
            return
        result['sms_response_seen'] = True
        if type(response.status) is int and 100 <= response.status <= 599:
            result['sms_http_status'] = response.status
        try:
            payload = response.json()
            for item in (payload, payload.get('data', {})):
                if isinstance(item, dict) and type(item.get('error_code')) is int:
                    response_codes.append(item['error_code'])
        except Exception:
            pass

    def finish(reason):
        result['reason'] = reason
        return result

    try:
        ready_path.unlink(missing_ok=True)
        if not official_origin(page):
            return finish('unexpected_origin')
        if any_visible(page.locator(CHALLENGE_SELECTOR)):
            return finish('manual_verification_required')
        code_input = page.locator(CODE_SELECTOR)
        if not any_visible(code_input):
            result['stage'] = 'select_sms_method'
            switch = page.get_by_text(SMS_METHOD)
            if switch.count() != 1 or not switch.is_visible():
                return finish('sms_method_unavailable')
            switch.dispatch_event('click', timeout=10000)
        result['stage'] = 'wait_sms_fields'
        code_input.first.wait_for(state='visible', timeout=10000)
        result['stage'] = 'confirm_sms_form'
        phone_input = page.locator(PHONE_SELECTOR)
        result['sms_form_counts'] = {'code': code_input.count(), 'phone': phone_input.count()}
        if result['sms_form_counts'] != {'code': 1, 'phone': 1}:
            result['sms_code_controls'] = code_input.evaluate_all(
                'elements => elements.slice(0, 8).map(e => ({name: e.name, '
                'type: e.type, autocomplete: e.autocomplete, '
                'visible: Boolean(e.getClientRects().length)}))')
            return finish('ambiguous_sms_form')
        result['stage'] = 'confirm_sms_country'
        country = page.locator(COUNTRY_SELECTOR)
        result['sms_country_selector_count'] = country.count()
        if country.count() != 1:
            # Public control structure only; never inspect phone/password values.
            result['sms_country_controls'] = page.locator(
                'input[name*="area-code"], input[role="combobox"]'
            ).evaluate_all('elements => elements.slice(0, 8).map(e => ({'
                           'name: e.name, role: e.getAttribute("role"), '
                           'visible: Boolean(e.getClientRects().length)}))')
            return finish('sms_country_selector_missing')
        if country_code(country) != '+86':
            return finish('sms_country_not_confirmed')
        result['stage'] = 'fill_sms_phone'
        fill_login_field(page, phone_input, PHONE_SELECTOR, phone)
        result['stage'] = 'find_sms_send_control'
        send = page.get_by_text(SEND_CODE)
        if send.count() != 1 or not send.is_visible() or not send.is_enabled():
            return finish('sms_send_control_missing')
        if not official_origin(page):
            return finish('unexpected_origin')
        page.on('response', on_response)
        listening = True
        result['stage'] = 'request_sms_once'
        # A native click supplies focus/blur and trusted pointer events. Never
        # retry an SMS request whose outcome is unknown.
        original = getattr(page, '_original', None)
        if original is not None:
            label = send.inner_text().strip()
            if not SEND_CODE.fullmatch(label):
                return finish('sms_send_control_missing')
            original.click('text="' + label + '"', timeout=10000)
        else:
            send.click(timeout=10000)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if not official_origin(page):
                return finish('unexpected_origin')
            if any_visible(page.locator(CHALLENGE_SELECTOR)):
                return finish('sms_request_challenge')
            if response_codes:
                result['sms_business_code'] = next((c for c in response_codes if c), 0)
                if result['sms_business_code']:
                    return finish('sms_request_rejected')
                break
            # The native countdown also confirms an accepted SMS request.
            if any_visible(page.get_by_text(re.compile(
                    r'^\d+\s*(s|秒|秒后.*)|^.*(重新获取|Resend).*\d+', re.I))):
                break
            page.wait_for_timeout(500)
        else:
            return finish('sms_request_unconfirmed')
        result['stage'] = 'waiting_for_sms_code'
        result['sms_requested'] = True
        result['reason'] = 'waiting_for_sms_code'
        save_diagnostic(page, Path('artifacts/otp-waiting/login'), result)
        ready_path.write_text(challenge)
        print('SMS_REQUESTED: waiting for code over restricted SSH', flush=True)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline and not code_path.exists():
            if not official_origin(page):
                return finish('unexpected_origin')
            page.wait_for_timeout(500)
        if not code_path.exists():
            return finish('sms_code_timeout')
        code = code_path.read_text()
        code_path.unlink()
        if not re.fullmatch(r'[0-9]{4,8}', code):
            return finish('sms_code_invalid')
        if not official_origin(page) or any_visible(page.locator(CHALLENGE_SELECTOR)):
            return finish('manual_verification_required')
        result['stage'] = 'submit_sms_once'
        fill_login_field(page, code_input, CODE_SELECTOR, code)
        del code
        submit = page.locator(SUBMIT_SELECTOR)
        if submit.count() != 1 or not submit.is_visible() or not submit.is_enabled():
            return finish('sms_submit_unavailable')
        # Clear the password rejection before observing this distinct login request.
        for key in ('login_response_signal', 'login_rejection_kind', 'login_business_codes'):
            result.pop(key, None)
        result['sms_submitted'] = True
        submit.dispatch_event('click', timeout=10000)
        return finish('sms_submitted')
    finally:
        if listening:
            page.remove_listener('response', on_response)
        ready_path.unlink(missing_ok=True)
        code_path.unlink(missing_ok=True)

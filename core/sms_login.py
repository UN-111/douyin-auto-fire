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
                                     official_origin, fill_login_field, save_diagnostic,
                                     login_response_diagnostic)
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
                # Static endpoint names only; no URL queries, identifiers or bodies.
                if re.fullmatch(r'/passport/web/(?:[a-z_]+/){1,3}', parsed.path):
                    responses = result.setdefault('sms_other_passport_responses', [])
                    if len(responses) < 12:
                        item = {'path': parsed.path, 'status': response.status}
                        try:
                            item.update(login_response_diagnostic(response.json()))
                        except Exception:
                            pass
                        responses.append(item)
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
        # Finish editing the phone before the DOM button action, which does not
        # move focus itself. This delivers the form's normal change/blur events.
        code_input.focus(timeout=10000)
        result['sms_phone_state'] = phone_input.evaluate('''el => ({
            digit_count: el.value.replace(/[^0-9]/g, '').length,
            valid: el.checkValidity(), focused: document.activeElement === el
        })''')
        result['stage'] = 'find_sms_send_control'
        send = page.get_by_text(SEND_CODE)
        if send.count() != 1 or not send.is_visible() or not send.is_enabled():
            return finish('sms_send_control_missing')
        if not official_origin(page):
            return finish('unexpected_origin')
        page.on('response', on_response)
        listening = True
        save_diagnostic(page, Path('artifacts/password-login/sms-before-click'), result)
        send.scroll_into_view_if_needed(timeout=10000)
        target = send.evaluate('''el => {
            window.__douyinSmsClickTrusted = null;
            window.__douyinSmsPointerEvents = [];
            for (const scope of [window, document]) {
              for (const type of ['mousedown', 'mouseup', 'click']) {
                scope.addEventListener(type, e => {
                    if (window.__douyinSmsPointerEvents.length < 6) {
                        window.__douyinSmsPointerEvents.push({type: e.type,
                            scope: scope === window ? 'window' : 'document',
                            x: e.clientX, y: e.clientY, trusted: e.isTrusted,
                            tag: e.target.tagName?.toLowerCase(),
                            target: e.composedPath().includes(el)});
                    }
                    if (e.type === 'mousedown' && e.composedPath().includes(el)) {
                        window.__douyinSmsClickTrusted = e.isTrusted;
                    }
                }, {once: true, capture: true});
              }
            }
            const r = el.getBoundingClientRect();
            const x = r.x + r.width / 2, y = r.y + r.height / 2;
            const hit = document.elementFromPoint(x, y);
            return {x, y, hit: r.width > 0 && r.height > 0 &&
                (hit === el || el.contains(hit))};
        }''')
        result['sms_send_controls'] = send.evaluate('''el => {
            const controls = [];
            for (let node = el; node && controls.length < 4; node = node.parentElement) {
                const key = Object.keys(node).find(k => k.startsWith('__reactProps$'));
                const props = key ? node[key] : {};
                controls.push({tag: node.tagName.toLowerCase(),
                    disabled: Boolean(node.disabled), aria_disabled: node.getAttribute('aria-disabled'),
                    handlers: Object.keys(props || {}).filter(k => /^on[A-Z]/.test(k) && typeof props[k] === 'function')});
            }
            return controls;
        }''')
        result['sms_click_target'] = target
        if not target['hit']:
            return finish('sms_send_control_obstructed')
        result['stage'] = 'request_sms_once'
        # Send code binds onMouseDown, unlike the other login controls.
        # Dispatch its actual mouse action once; click alone has no handler.
        send.dispatch_event('mousedown', {'button': 0, 'buttons': 1, 'detail': 1,
                                     'clientX': target['x'], 'clientY': target['y']},
                            timeout=10000)
        result['sms_click_completed'] = True
        result['sms_click_event_trusted'] = page.evaluate(
            'window.__douyinSmsClickTrusted')
        page.wait_for_timeout(1000)
        result['sms_pointer_events'] = page.evaluate('window.__douyinSmsPointerEvents')
        save_diagnostic(page, Path('artifacts/password-login/sms-after-click'), result)
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

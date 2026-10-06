"""One password-login attempt on the official page; never solve challenges.

Credentials remain in runner environment/memory. Public screenshots mask inputs;
an optional encrypted copy shows the phone to the owner for input verification.
"""

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit


# The SMS code control also uses type=tel; its public name is button-input.
PHONE_SELECTOR = 'input[type="tel"]:not([name="button-input"])'
PASSWORD_SELECTOR = 'input[type="password"]'
COUNTRY_SELECTOR = 'input[name="web-login-area-code-input"][role="combobox"]'
COUNTRY_OPTION_SELECTOR = '[id^="areacode_item_"]'
CHINA_CODE = re.compile(r'\+86(?:\D|$)')
PASSWORD_METHOD = re.compile(r'^(密码登录|Use Password)$')
LOGIN_HEADING = re.compile(r'^(Log in to Douyin|登录后免费畅享高清视频)$')
SUBMIT_SELECTOR = '#douyin_login_comp_btn_id'
LOGIN_SELECTOR = '[data-e2e="login-container"], #douyin_login_comp_btn_id'
CHALLENGE_SELECTOR = (
    'iframe[src*="captcha" i], iframe[src*="verify" i], '
    '[id*="captcha" i], [class*="captcha" i]'
)
CHALLENGE_TEXT = re.compile(
    r'拖动滑块|拖动下方滑块|完成安全验证|请进行验证|请验证身份|'
    r'输入短信验证码|请输入验证码|短信验证|扫码确认|手机确认|安全校验|'
    r'drag.*slider|security verification|verify your identity|enter.*verification code|'
    r'log\s*in.*using.*verification code|'
    r'confirm.*phone|verify.*phone', re.I
)
REJECTION_TEXT = re.compile(
    r'密码错误|密码不正确|账号或密码错误|帐号或密码错误|登录失败|'
    r'操作频繁|请求频繁|账号不存在|帐号不存在|参数错误|系统繁忙|网络异常|'
    r'incorrect password|wrong password|invalid password|login failed|log in failed|'
    r'too many|too frequent|account.*not exist|network error|try again later|'
    r'username or password doesn.t match', re.I
)


def official_origin(page):
    parsed = urlsplit(page.url)
    return (parsed.scheme == 'https' and parsed.hostname == 'www.douyin.com'
            and parsed.port in (None, 443) and not parsed.username and not parsed.password)


def any_visible(locator):
    return any(locator.nth(i).is_visible() for i in range(locator.count()))


def login_visible(page):
    return any_visible(page.locator(LOGIN_SELECTOR)) or any_visible(
        page.get_by_text(PASSWORD_METHOD)
    )


def challenge_visible(page):
    return any_visible(page.locator(CHALLENGE_SELECTOR)) or any_visible(
        page.get_by_text(CHALLENGE_TEXT)
    )


def credentials_for(unique_id, *, allow_global=False):
    """Use an account-specific pair, or a global pair for a single-account batch."""
    suffix = str(unique_id or '').upper()
    phone = os.getenv(f'DOUYIN_PHONE_{suffix}', '')
    password = os.getenv(f'DOUYIN_PASSWORD_{suffix}', '')
    # Never combine a scoped phone with a global password (or vice versa).
    if not phone and not password and allow_global:
        phone = os.getenv('DOUYIN_PHONE', '')
        password = os.getenv('DOUYIN_PASSWORD', '')
    phone = normalize_phone(phone)
    if not phone or not password:
        return None, None, 'credentials_missing'
    if not re.fullmatch(r'1[3-9]\d{9}', phone):
        return None, None, 'phone_format_invalid'
    return phone, password, None


def normalize_phone(value):
    phone = unicodedata.normalize('NFKC', value).strip()
    # Accept formatting only; never remove arbitrary letters or descriptions.
    if len(phone) >= 2 and phone[0] == phone[-1] and phone[0] in ('"', "'"):
        phone = phone[1:-1].strip()
    phone = re.sub(r'[\s\-()]', '', phone)
    for prefix in ('+86', '0086', '86'):
        if phone.startswith(prefix) and len(phone) == len(prefix) + 11:
            phone = phone[len(prefix):]
            break
    return phone


def country_code(country):
    # React may set the input's live value without retaining a value attribute.
    # This locator identifies only the public country-code control, never the
    # phone or password fields. Do not serialize the raw DOM value.
    value = country.evaluate('(element) => element.value')
    code = unicodedata.normalize('NFKC', str(value or '')).strip()
    return '+' + code if re.fullmatch(r'\d{1,4}', code) else code


def fill_login_field(page, locator, selector, value):
    # Authentication inputs may be covered by the still-open country popup.
    # Native fill focuses the editable field and dispatches its input events;
    # it never clicks the submit button. Credentials stay in runner memory.
    original = getattr(page, '_original', None)
    if original is not None:
        original.fill(selector, value, timeout=10000)
    else:
        locator.fill(value, timeout=10000)


def login_request(request):
    # Inspect URL/method only, never headers or POST data.
    parsed = urlsplit(request.url)
    return (request.method == 'POST' and parsed.scheme == 'https'
            and (parsed.hostname or '').endswith('.douyin.com')
            and 'login' in parsed.path.lower() and 'passport' in parsed.path.lower())


def login_response_diagnostic(payload):
    """Keep numeric business codes and fixed classifications, never raw data."""
    if not isinstance(payload, dict):
        return {}
    containers = [payload]
    if isinstance(payload.get('data'), dict):
        containers.append(payload['data'])
    codes = {}
    signal = None
    rejection_kind = None
    for index, item in enumerate(containers):
        for key in ('error_code', 'status_code', 'code'):
            value = item.get(key)
            if type(value) is int and -1 <= value <= 1000000:
                codes[('data.' if index else '') + key] = value
        for key in ('message', 'description', 'error_msg', 'error_message'):
            value = item.get(key)
            if isinstance(value, str):
                if CHALLENGE_TEXT.search(value):
                    signal = 'verification_required'
                elif REJECTION_TEXT.search(value) and signal is None:
                    signal = 'rejected'
                if re.search(r'密码错误|密码不正确|账号或密码错误|帐号或密码错误|'
                             r'incorrect password|wrong password|invalid password|'
                             r'username or password doesn.t match', value, re.I):
                    rejection_kind = 'incorrect_password'
    safe = {'login_business_codes': codes} if codes else {}
    if signal:
        safe['login_response_signal'] = signal
    if rejection_kind:
        safe['login_rejection_kind'] = rejection_kind
    return safe


def save_diagnostic(page, path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = {k: result[k] for k in ('attempted', 'submitted', 'ok', 'reason', 'stage', 'operation_error', 'initial_country_code', 'country_code', 'country_option_shape', 'submit_shape', 'post_request_seen', 'login_request_seen', 'login_http_status', 'login_business_codes', 'login_response_signal', 'login_rejection_kind', 'sms_requested', 'sms_submitted', 'sms_business_code', 'sms_response_seen', 'sms_http_status', 'sms_other_passport_response_count', 'sms_other_passport_responses', 'sms_click_target', 'sms_click_completed', 'sms_click_event_trusted', 'sms_pointer_events', 'sms_country_selector_count', 'sms_country_controls', 'sms_form_counts', 'sms_code_controls') if k in result}
    try:
        page.screenshot(
            path=str(path.with_suffix('.png')), full_page=False, timeout=15000,
            animations='disabled',
            mask=[page.locator('input, textarea, [contenteditable="true"]'),
                  page.locator('[data-e2e="conversation-item"], [data-e2e="msg-item-content"]'),
                  page.locator('[id*="qrcode" i], [class*="qrcode" i]'),
                  page.get_by_role('img', name='二维码', exact=True),
                  page.get_by_text(re.compile(r'1[3-9]\d{9}'))],
        )
        safe['screenshot'] = path.with_suffix('.png').name
    except Exception as exc:
        safe['screenshot'] = None
        safe['screenshot_error'] = type(exc).__name__
    if os.getenv('DOUYIN_DIAGNOSTIC_KEY'):
        try:
            from cryptography.fernet import Fernet
            cipher = Fernet(os.environ['DOUYIN_DIAGNOSTIC_KEY'].encode('ascii'))
            # Keep the clear image in memory only. Password, OTP, QR codes and
            # conversations remain masked in this owner-only phone view.
            phone_view = page.screenshot(
                type='png', full_page=False, timeout=15000, animations='disabled',
                mask=[page.locator('input:not(' + PHONE_SELECTOR + '):not(' +
                                   COUNTRY_SELECTOR + '), textarea, [contenteditable="true"]'),
                      page.locator('[data-e2e="conversation-item"], [data-e2e="msg-item-content"]'),
                      page.locator('[id*="qrcode" i], [class*="qrcode" i]'),
                      page.get_by_role('img', name='二维码', exact=True)],
            )
            encrypted = path.with_suffix('.png.fernet')
            encrypted.write_bytes(cipher.encrypt(phone_view))
            safe['encrypted_phone_screenshot'] = encrypted.name
        except Exception as exc:
            safe['encrypted_phone_screenshot_error'] = type(exc).__name__
    path.with_suffix('.json').write_text(
        json.dumps(safe, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )


def attempt_password_login(page, unique_id, *, allow_global=False, timeout_seconds=45):
    result = {'attempted': False, 'submitted': False, 'ok': False, 'reason': 'credentials_missing'}
    listeners = []
    phone, password, error = credentials_for(unique_id, allow_global=allow_global)
    if error:
        result['reason'] = error
        return result
    try:
        if not official_origin(page):
            result['reason'] = 'unexpected_origin'
            return result
        if challenge_visible(page):
            result['reason'] = 'manual_verification_required'
            return result
        if not login_visible(page):
            result['reason'] = 'login_form_missing'
            return result
        switch = page.get_by_text(PASSWORD_METHOD)
        if switch.count() != 1 or not switch.is_visible():
            result['reason'] = 'password_method_unavailable'
            return result
        result['stage'] = 'select_password_method'
        switch.click(timeout=10000)
        phone_input = page.locator(PHONE_SELECTOR)
        password_input = page.locator(PASSWORD_SELECTOR)
        result['stage'] = 'wait_password_fields'
        try:
            password_input.wait_for(state='visible', timeout=2000)
        except Exception:
            # CloakBrowser's pointer click can miss this tab, as with the
            # existing conversation/sticker controls. Retry only the mode tab,
            # never the credential submission or an interactive challenge.
            if (not official_origin(page) or challenge_visible(page)
                    or not switch.is_enabled()
                    or switch.get_attribute('aria-disabled') == 'true'
                    or switch.get_attribute('disabled') is not None):
                result['reason'] = 'password_method_unavailable'
                return result
            switch.dispatch_event('click', timeout=5000)
        password_input.wait_for(state='visible', timeout=10000)
        phone_input.wait_for(state='visible', timeout=10000)
        if phone_input.count() != 1 or password_input.count() != 1:
            result['reason'] = 'ambiguous_login_form'
            return result
        result['stage'] = 'confirm_country'
        country = page.locator(COUNTRY_SELECTOR)
        if country.count() != 1:
            result['reason'] = 'country_selector_missing'
            return result
        code = country_code(country)
        result['country_code'] = code if re.fullmatch(r'\+\d{1,4}', code) else 'unknown'
        result['initial_country_code'] = result['country_code']
        if code != '+86':
            result['stage'] = 'open_country_menu'
            country.click(timeout=5000)
            # The editable combobox filters the long country list by code.
            # Filtering brings China into view without relying on menu scroll
            # offsets, and the subsequent selection/blur must retain +86.
            result['stage'] = 'filter_country_options'
            original = getattr(page, '_original', None)
            if original is not None:
                original.fill(COUNTRY_SELECTOR, '+86', timeout=5000)
            else:
                country.fill('+86', timeout=5000)
            result['stage'] = 'find_country_option'
            china = page.locator(COUNTRY_OPTION_SELECTOR).filter(has_text=CHINA_CODE)
            try:
                china.wait_for(state='visible', timeout=3000)
            except Exception:
                if (not official_origin(page) or challenge_visible(page)
                        or not country.is_enabled()):
                    result['reason'] = 'country_option_missing'
                    return result
                # Retry opening this public menu only, never the login submit.
                country.dispatch_event('click', timeout=5000)
                try:
                    china.wait_for(state='visible', timeout=5000)
                except Exception:
                    result['reason'] = 'country_option_missing'
                    return result
            if china.count() != 1:
                result['reason'] = 'country_option_missing'
                return result
            result['stage'] = 'select_country_option'
            option_id = china.get_attribute('id')
            if not re.fullmatch(r'areacode_item_\d+', option_id or ''):
                result['reason'] = 'country_option_missing'
                return result
            # Resolve the public option by code, then use its plain CSS id for
            # the humanized click (regex-filter selectors are not portable).
            option = page.locator('#' + option_id)
            try:
                if original is not None:
                    # The country option sits inside a scrollable menu. The
                    # native action scrolls that container before clicking.
                    # Only this public menu action uses the original API.
                    original.click('#' + option_id, timeout=5000)
                else:
                    option.click(timeout=5000)
            except Exception:
                pass
            page.wait_for_timeout(300)
            if country_code(country) != '+86' or any_visible(page.locator(COUNTRY_OPTION_SELECTOR)):
                if (not official_origin(page) or challenge_visible(page)
                        or not option.is_enabled()):
                    result['reason'] = 'country_not_confirmed'
                    return result
                # Typing +86 filters the list but does not select the country.
                # Some menu widgets select on mousedown to avoid losing focus.
                # These events target only the resolved public country option.
                if option.count() == 1 and option.is_visible():
                    option.dispatch_event('mousedown', timeout=5000)
                    page.wait_for_timeout(100)
                    # The verified widget removes its option on mousedown.
                    # Do not wait for or click an option that has disappeared.
                    if option.count() == 1 and option.is_visible():
                        option.dispatch_event('click', timeout=5000)
                        page.wait_for_timeout(300)
            if original is not None:
                original.keyboard_press('Tab')
            else:
                country.press('Tab', timeout=5000)
            page.wait_for_timeout(300)
        result['stage'] = 'verify_country'
        code = country_code(country)
        result['country_code'] = code if re.fullmatch(r'\+\d{1,4}', code) else 'unknown'
        if code != '+86':
            result['reason'] = 'country_not_confirmed'
            return result
        result['stage'] = 'close_country_menu'
        if any_visible(page.locator(COUNTRY_OPTION_SELECTOR)):
            heading = page.get_by_text(LOGIN_HEADING)
            if heading.count() != 1 or not heading.is_visible():
                result['reason'] = 'country_menu_still_open'
                return result
            title = heading.inner_text().strip()
            if not LOGIN_HEADING.fullmatch(title):
                result['reason'] = 'country_menu_still_open'
                return result
            original = getattr(page, '_original', None)
            if original is not None:
                original.click('text="' + title + '"', timeout=5000)
            else:
                heading.click(timeout=5000)
            page.wait_for_timeout(300)
            if any_visible(page.locator(COUNTRY_OPTION_SELECTOR)) or country_code(country) != '+86':
                # Record structure only for this public option, never the login
                # form's HTML, inputs, text, application state or credentials.
                option = page.locator(COUNTRY_OPTION_SELECTOR).filter(has_text=CHINA_CODE)
                if option.count() == 1:
                    result['country_option_shape'] = option.evaluate('''(root) => {
                        const shape = (el, depth) => ({
                            tag: el.tagName.toLowerCase(),
                            role: el.getAttribute('role'),
                            pointer: getComputedStyle(el).pointerEvents,
                            children: depth < 2 ? [...el.children].slice(0, 5).map(
                                child => shape(child, depth + 1)) : []
                        });
                        return shape(root, 0);
                    }''')
                result['reason'] = 'country_menu_still_open'
                return result
        submit = page.locator(SUBMIT_SELECTOR)
        if submit.count() != 1 or not submit.is_visible():
            result['reason'] = 'submit_missing'
            return result
        # Origin checks also apply to redirects during method/country selection.
        if not official_origin(page):
            result['reason'] = 'unexpected_origin'
            return result
        result['attempted'] = True
        result['stage'] = 'fill_phone'
        fill_login_field(page, phone_input, PHONE_SELECTOR, phone)
        if not official_origin(page):
            result['reason'] = 'unexpected_origin'
            return result
        if not os.getenv('DOUYIN_OTP_DIR'):
            result['stage'] = 'fill_password'
            fill_login_field(page, password_input, PASSWORD_SELECTOR, password)
        if challenge_visible(page):
            result['reason'] = 'manual_verification_required'
            return result
        if not official_origin(page):
            result['reason'] = 'unexpected_origin'
            return result
        result['login_request_seen'] = False
        result['post_request_seen'] = False
        if (not submit.is_enabled() or submit.get_attribute('aria-disabled') == 'true'
                or submit.get_attribute('disabled') is not None):
            result['reason'] = 'submit_disabled'
            return result
        result['submit_shape'] = submit.evaluate('''(element) => ({
            tag: element.tagName.toLowerCase(),
            pointer: getComputedStyle(element).pointerEvents,
            children: [...element.children].slice(0, 5).map(child => ({
                tag: child.tagName.toLowerCase(), role: child.getAttribute('role')
            }))
        })''')

        def on_request(request):
            try:
                if request.method == 'POST' and urlsplit(request.url).scheme == 'https':
                    result['post_request_seen'] = True
                if login_request(request):
                    result['login_request_seen'] = True
            except Exception:
                pass

        def on_response(response):
            try:
                if login_request(response.request) and 100 <= response.status <= 599:
                    result['login_http_status'] = response.status
                    # Parse the matched authentication response in memory only.
                    # Discard everything except bounded numeric codes and fixed
                    # signals; never retain tokens, cookies, accounts or text.
                    result.update(login_response_diagnostic(response.json()))
            except Exception:
                pass

        for event, callback in (('request', on_request), ('response', on_response)):
            page.on(event, callback)
            listeners.append((event, callback))
        if os.getenv('DOUYIN_OTP_DIR'):
            # Interactive runs request SMS directly, without a rejected password
            # attempt or its stale error overlay before the Send code control.
            from core.sms_login import attempt_sms_login
            attempt_sms_login(page, phone, result)
            if result['reason'] != 'sms_submitted':
                return result
            result['submitted'] = True
        else:
            result['stage'] = 'submit_once'
            result['submitted'] = True
            submit.dispatch_event('click', timeout=10000)
        result['stage'] = 'verify_result'
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not official_origin(page):
                result['reason'] = 'unexpected_origin'
                return result
            if (os.getenv('DOUYIN_OTP_DIR') and not result.get('sms_submitted')
                    and (result.get('login_response_signal') == 'verification_required'
                         or 1039 in result.get('login_business_codes', {}).values())):
                from core.sms_login import attempt_sms_login
                attempt_sms_login(page, phone, result)
                if result['reason'] != 'sms_submitted':
                    return result
                deadline = time.monotonic() + timeout_seconds
                page.wait_for_timeout(500)
                continue
            if (any_visible(page.locator(CHALLENGE_SELECTOR))
                    or (not result.get('sms_submitted') and challenge_visible(page))):
                result['reason'] = 'manual_verification_required'
                return result
            if result.get('login_response_signal') == 'verification_required':
                result['reason'] = 'manual_verification_required'
                return result
            if result.get('login_response_signal') == 'rejected':
                result['reason'] = 'login_rejected'
                return result
            if any_visible(page.get_by_text(REJECTION_TEXT)):
                result['reason'] = 'login_rejected'
                return result
            positive = any_visible(page.locator(
                '[data-e2e="user-avatar-card"], [data-e2e="conversation-item"]'
            ))
            if positive and not login_visible(page) and not phone_input.is_visible():
                # Only the fresh IM preflight may declare verified success.
                result['reason'] = 'awaiting_chat_preflight'
                return result
            page.wait_for_timeout(500)
        result['reason'] = 'login_result_unconfirmed'
    except Exception as exc:
        # Playwright errors can embed fill arguments. Never log str(exc)/tracebacks.
        result['reason'] = 'login_operation_failed'
        result['operation_error'] = ('timeout' if type(exc).__name__ == 'TimeoutError'
                                     else 'attribute' if isinstance(exc, AttributeError)
                                     else 'other')
    finally:
        for event, callback in listeners:
            try:
                page.remove_listener(event, callback)
            except Exception:
                pass
    return result

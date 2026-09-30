"""One password-login attempt on the official page; never solve challenges.

Credentials remain in runner environment/memory. Diagnostics contain only
fixed status labels, and screenshots mask inputs, QR codes and conversations.
"""

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit


PHONE_SELECTOR = 'input[name="normal-input"][type="tel"]'
PASSWORD_SELECTOR = 'input[name="normal-input"][type="password"]'
PASSWORD_METHOD = re.compile(r'^(密码登录|Use Password)$')
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
    r'confirm.*phone|verify.*phone', re.I
)
REJECTION_TEXT = re.compile(
    r'密码错误|密码不正确|账号或密码错误|帐号或密码错误|登录失败|'
    r'操作频繁|请求频繁|账号不存在|帐号不存在|参数错误|系统繁忙|网络异常|'
    r'incorrect password|wrong password|invalid password|login failed|log in failed|'
    r'too many|too frequent|account.*not exist|network error|try again later', re.I
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


def save_diagnostic(page, path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = {k: result[k] for k in ('attempted', 'submitted', 'ok', 'reason', 'stage') if k in result}
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
    path.with_suffix('.json').write_text(
        json.dumps(safe, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )


def attempt_password_login(page, unique_id, *, allow_global=False, timeout_seconds=45):
    result = {'attempted': False, 'submitted': False, 'ok': False, 'reason': 'credentials_missing'}
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
        phone_input.wait_for(state='visible', timeout=10000)
        password_input.wait_for(state='visible', timeout=10000)
        if phone_input.count() != 1 or password_input.count() != 1:
            result['reason'] = 'ambiguous_login_form'
            return result
        result['stage'] = 'confirm_country'
        country = page.get_by_role('combobox')
        if country.count() != 1:
            result['reason'] = 'country_selector_missing'
            return result
        if country.input_value() != '+86':
            country.click(timeout=5000)
            china = page.locator('#areacode_item_0')
            if china.count() != 1 or '+86' not in china.inner_text(timeout=5000):
                result['reason'] = 'country_option_missing'
                return result
            china.click(timeout=5000)
        if country.input_value() != '+86':
            result['reason'] = 'country_not_confirmed'
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
        phone_input.fill(phone, timeout=10000)
        if not official_origin(page):
            result['reason'] = 'unexpected_origin'
            return result
        result['stage'] = 'fill_password'
        password_input.fill(password, timeout=10000)
        if challenge_visible(page):
            result['reason'] = 'manual_verification_required'
            return result
        if not official_origin(page):
            result['reason'] = 'unexpected_origin'
            return result
        # Exactly one submit. A challenge or ambiguous response never triggers a retry.
        result['stage'] = 'submit_once'
        result['submitted'] = True
        submit.click(timeout=10000)
        result['stage'] = 'verify_result'
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not official_origin(page):
                result['reason'] = 'unexpected_origin'
                return result
            if challenge_visible(page):
                result['reason'] = 'manual_verification_required'
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
    except Exception:
        # Playwright errors can embed fill arguments. Never log str(exc)/tracebacks.
        result['reason'] = 'login_operation_failed'
    return result

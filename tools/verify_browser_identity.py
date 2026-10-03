"""Verify real browser output across fresh processes; never load account data."""
import hashlib
import json
from pathlib import Path
from core.browser import get_browser

JS_IDENTITY = r"""() => {
  const c = document.createElement('canvas'); c.width = 320; c.height = 160;
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#ef9800'; ctx.fillRect(5, 8, 97, 53);
  ctx.font = '18px Arial'; ctx.fillStyle = '#135ac9';
  ctx.fillText('Douyin fixed browser 123', 12, 70);
  const gl = document.createElement('canvas').getContext('webgl');
  const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
  return {
    canvas: c.toDataURL(),
    gpuVendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
    gpuRenderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
    userAgent: navigator.userAgent, platform: navigator.platform,
    hardwareConcurrency: navigator.hardwareConcurrency, deviceMemory: navigator.deviceMemory,
    languages: navigator.languages,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    screen: {width: screen.width, height: screen.height, colorDepth: screen.colorDepth},
    viewport: {width: innerWidth, height: innerHeight, outerWidth, outerHeight},
    webdriver: navigator.webdriver,
  };
}"""


def sample(seed):
    browser = get_browser(seed)
    try:
        context = browser.new_context()
        try:
            page = context.new_page()
            # A new page is already about:blank. Avoid waiting for load events
            # on set_content, which can stall in the pinned headless build.
            value = page.evaluate(JS_IDENTITY)
            value['canvas_sha256'] = hashlib.sha256(value.pop('canvas').encode()).hexdigest()
            return value
        finally:
            context.close()
    finally:
        browser.close()


def main():
    tasks = json.loads(Path('config/github-actions.tasks.json').read_text())
    seed = str(tasks[0]['fingerprint'])
    first, second = sample(seed), sample(seed)
    control = sample('17022' if seed != '17022' else '17023')
    changed = [key for key in first if first[key] != second[key]]
    seed_changes_output = any(first[key] != control[key] for key in (
        'canvas_sha256', 'gpuVendor', 'gpuRenderer', 'hardwareConcurrency', 'deviceMemory'
    ))
    result = {
        'fixed_seed': seed, 'same_seed_stable': not changed,
        'changed_fields': changed, 'different_seed_changes_output': seed_changes_output,
        'samples': [first, second], 'control_sample': control,
    }
    path = Path('artifacts/browser-identity.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if 'sample' not in key}))
    if changed or not seed_changes_output:
        raise SystemExit('Fixed browser identity check failed; see browser-identity.json')


if __name__ == '__main__':
    main()

"""Receive one code through the key-restricted SSH terminal, without echo."""

import getpass
import os
import re
import time
from pathlib import Path


def main():
    root = Path(os.environ['DOUYIN_OTP_DIR'])
    deadline = time.monotonic() + 900
    print('Waiting for Actions to request SMS. No browser commands are accepted.', flush=True)
    while time.monotonic() < deadline and not (root / 'ready').exists():
        time.sleep(1)
    if not (root / 'ready').exists():
        raise SystemExit('No SMS request arrived.')
    challenge = (root / 'ready').read_text().strip()
    if not re.fullmatch(r'[a-f0-9]{32}', challenge):
        raise SystemExit('Invalid challenge.')
    code = getpass.getpass('SMS code (hidden): ')
    if not re.fullmatch(r'[0-9]{4,8}', code):
        raise SystemExit('Invalid code format; nothing submitted.')
    # Exclusive create: only one answer to this run-specific challenge.
    incoming = root / (challenge + '.incoming')
    fd = os.open(incoming, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(code)
    os.replace(incoming, root / challenge)
    del code
    print('Code delivered to Actions. This channel is now closed.', flush=True)
    # Do not drop into a shell when the receiver exits.
    while time.monotonic() < deadline:
        time.sleep(1)


if __name__ == '__main__':
    main()

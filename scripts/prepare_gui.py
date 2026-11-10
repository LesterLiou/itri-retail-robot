#!/usr/bin/env python3
"""Copy only the current local display authorization for Docker GUI clients."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def main():
    display = os.environ.get('DISPLAY', '')
    if not re.fullmatch(r'(?:unix/?)?:\d+(?:\.\d+)?', display):
        raise SystemExit('Run this command in your local Ubuntu desktop terminal with DISPLAY set (X11 or XWayland).')
    if not shutil.which('xauth'):
        raise SystemExit('The host desktop needs xauth. Install the Ubuntu xauth package and retry.')
    source = Path(os.environ.get('XAUTHORITY') or Path.home()/'.Xauthority')
    if not source.is_file():
        raise SystemExit('XAUTHORITY is missing. Run this command from the logged-in desktop terminal.')
    # -i avoids attempting to lock/write the host session's authority database.
    result = subprocess.run(['xauth', '-i', '-f', str(source), 'nlist', display],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    records = result.stdout.splitlines()
    if result.returncode or not records:
        raise SystemExit('No authorization for the current DISPLAY. Check DISPLAY and XAUTHORITY in your desktop terminal.')
    # FamilyWild permits a different container hostname; the display and cookie
    # still come only from the selected desktop session.
    payload = b'\n'.join(b'ffff'+line[4:] for line in records)+b'\n'
    destination = Path(__file__).resolve().parents[1]/'config/local/gui.Xauthority'
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.gui-auth-', delete=False) as f:
        temporary = Path(f.name)
    try:
        merged = subprocess.run(['xauth', '-f', str(temporary), 'nmerge', '-'],
                                input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if merged.returncode:
            raise SystemExit('Could not prepare Docker display authorization.')
        temporary.chmod(0o600)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'Prepared native display {display}; authorization saved privately under config/local.')


if __name__ == '__main__':
    main()

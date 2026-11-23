"""Run SPIder in demo mode: a made-up home network and a stand-in Pi-hole.

    python scripts/demo/run.py

Then open http://127.0.0.1:8090 and sign in as  demo / demo-password
(or  guest / guest-password  to see the view-only side).

Nothing here touches your real SPIder: the demo keeps its own data in
data-demo/ (wiped and re-made each run) and never scans your network.
Press Ctrl+C to stop.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data-demo"
PORT, PIHOLE_PORT = 8090, 8099


def main() -> None:
    py = sys.executable
    shutil.rmtree(DATA, ignore_errors=True)
    subprocess.run([py, str(ROOT / "scripts" / "demo" / "seed.py"), str(DATA)], check=True, stdout=subprocess.DEVNULL)

    env = os.environ | {"SPIDER_DATA_DIR": str(DATA), "SPIDER_DEMO": "true",
                        "SPIDER_HOST": "127.0.0.1", "SPIDER_PORT": str(PORT)}
    pihole = subprocess.Popen([py, str(ROOT / "scripts" / "demo" / "fake_pihole.py"), str(PIHOLE_PORT)],
                              stdout=subprocess.DEVNULL)
    app = subprocess.Popen([py, "-m", "spider"], cwd=ROOT, env=env)

    time.sleep(2)
    print(f"\n  SPIder demo:  http://127.0.0.1:{PORT}")
    print("  Admin:        demo / demo-password")
    print("  Viewer:       guest / guest-password")
    print("  Ctrl+C to stop.\n")
    try:
        app.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for proc in (app, pihole):
            proc.terminate()


if __name__ == "__main__":
    main()

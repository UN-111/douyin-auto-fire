"""Run one Douyin Spark Flow task batch.

The repository uses the upstream task runtime directly.  The only supported
entrypoint is a local or GitHub Actions task run; no cloud-function server is
included in this migration.
"""

import os
import sys


if os.path.exists(".env"):
    from dotenv import load_dotenv

    load_dotenv(".env")


MODE = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("RUN_MODE", "task")).strip().lower()


def main() -> None:
    if MODE in {"task", "run", "cli", ""}:
        from core.tasks import runTasks

        runTasks()
        return

    print(f"未知启动模式: {MODE}（可选：task）", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()

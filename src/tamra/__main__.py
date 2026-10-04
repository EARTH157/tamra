import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tamra")
    parser.add_argument("--dev", action="store_true", help="API only on :8765, token 'dev'")
    sub = parser.add_subparsers(dest="command")
    sc = sub.add_parser("selfcheck", help="verify bundled dependencies on this machine")
    sc.add_argument("--embed-model-dir", type=Path)
    sc.add_argument("--llm-model", type=Path)
    sc.add_argument("--report", type=Path, help="also write the JSON report here")
    args = parser.parse_args(argv)

    if args.command == "selfcheck":
        from tamra.paths import resource_dir
        from tamra.selfcheck import run_selfcheck

        report = run_selfcheck(
            args.embed_model_dir,
            args.llm_model,
            resource_dir() / "vendor" / "llama" / "llama-server.exe",
        )
        # Write report file first (with full Unicode, before stdout encoding can fail)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(report, ensure_ascii=False, indent=2)
            args.report.write_text(text, encoding="utf-8")
        text = json.dumps(report, indent=2)  # default ensure_ascii=True
        print(text)  # no-op in the windowed exe (stdout is None); use --report there
        return 0 if report["ok"] else 1

    from tamra.app import run

    try:
        run(dev=args.dev)
    except Exception:
        if args.dev:
            raise  # the developer wants the traceback
        return 1  # already logged and shown in a message box; no second dialog
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
        from tamra.paths import data_dir, resource_dir
        from tamra.selfcheck import run_selfcheck

        report = run_selfcheck(
            args.embed_model_dir,
            args.llm_model,
            resource_dir() / "vendor" / "llama" / "llama-server.exe",
            data_dir() / "logs",
        )
        # Write report file first (with full Unicode, before stdout encoding can fail)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(report, ensure_ascii=False, indent=2)
            args.report.write_text(text, encoding="utf-8")
        # Print to stdout with safe encoding (no-op in windowed exe where stdout is None)
        if sys.stdout is not None:
            text = json.dumps(report, indent=2)  # default ensure_ascii=True
            print(text)
        return 0 if report["ok"] else 1

    from tamra.app import run

    run(dev=args.dev)
    return 0


if __name__ == "__main__":
    sys.exit(main())

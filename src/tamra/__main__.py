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
        text = json.dumps(report, ensure_ascii=False, indent=2)
        print(text)  # no-op in the windowed exe (stdout is None); use --report there
        if args.report:
            args.report.write_text(text, encoding="utf-8")
        return 0 if report["ok"] else 1

    from tamra.app import run

    run(dev=args.dev)
    return 0


if __name__ == "__main__":
    sys.exit(main())

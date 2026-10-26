import argparse
import json
import os
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
    sub.add_parser("mcp", help="MCP server over stdio for AI apps (Tamra must be running)")
    args = parser.parse_args(argv)

    dev_token = os.environ.get("TAMRA_DEV_TOKEN", "").strip() or None
    if args.dev and getattr(sys, "frozen", False) and (dev_token is None or len(dev_token) < 24):
        # the packaged exe has no dev mode unless the caller brings its own secret token
        return 2

    if args.command == "selfcheck":
        from tamra.paths import llama_server
        from tamra.selfcheck import run_selfcheck

        report = run_selfcheck(args.embed_model_dir, args.llm_model, llama_server())
        # Write report file first (with full Unicode, before stdout encoding can fail)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(report, ensure_ascii=False, indent=2)
            args.report.write_text(text, encoding="utf-8")
        text = json.dumps(report, indent=2)  # default ensure_ascii=True
        print(text)  # no-op in the windowed exe (stdout is None); use --report there
        return 0 if report["ok"] else 1

    if args.command == "mcp":
        from tamra.mcp_server import main as mcp_main  # light: no core, no window

        return mcp_main()

    from tamra.app import run

    try:
        run(dev=args.dev, token=dev_token if args.dev else None)
    except Exception:
        if args.dev:
            raise  # the developer wants the traceback
        return 1  # already logged and shown in a message box; no second dialog
    return 0


if __name__ == "__main__":
    sys.exit(main())

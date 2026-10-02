import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tamra")
    parser.add_argument("--dev", action="store_true", help="API only on :8765, token 'dev'")
    args = parser.parse_args(argv)

    from tamra.app import run

    run(dev=args.dev)
    return 0


if __name__ == "__main__":
    sys.exit(main())

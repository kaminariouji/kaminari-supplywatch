"""SupplyWatch CLI.

Commands:
    python -m agent.cli scan   --watchlist watchlist.demo.json [--state state.json]
                               [--out state.json] [--llm-base-url URL --llm-model M --llm-api-key K]
    python -m agent.cli show   --state state.json
    python -m agent.cli export --state state.json --out demo/data/state.json
"""

from __future__ import annotations

import argparse
import json
import sys

from .llm import LLMConfig
from .watch import load_json, save_json, scan_watchlist


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent.cli", description="SupplyWatch monitor")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="scan the watchlist and detect changes")
    scan.add_argument("--watchlist", required=True)
    scan.add_argument("--state", default=None, help="previous state file (for diffs)")
    scan.add_argument("--out", default="state.json", help="where to write the new state")
    scan.add_argument("--llm-base-url", default=None)
    scan.add_argument("--llm-api-key", default=None)
    scan.add_argument("--llm-model", default=None)

    show = sub.add_parser("show", help="print the alert + changes from a state file")
    show.add_argument("--state", required=True)

    export = sub.add_parser("export", help="copy a state file into the demo folder")
    export.add_argument("--state", required=True)
    export.add_argument("--out", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "scan":
        watchlist = load_json(args.watchlist)
        previous = load_json(args.state) if args.state else None
        llm = None
        if args.llm_base_url and args.llm_model:
            llm = LLMConfig(
                base_url=args.llm_base_url,
                api_key=args.llm_api_key or "",
                model=args.llm_model,
            )
        state = scan_watchlist(watchlist, previous, llm)
        save_json(args.out, state.__dict__)
        print(json.dumps(
            {
                "generated_at": state.generated_at,
                "items": len(state.items),
                "changes": len(state.changes),
                "llm_used": state.llm_used,
                "alert_text": state.alert_text,
            },
            indent=2,
        ))
        return 0

    if args.command == "show":
        state = load_json(args.state)
        print(state.get("alert_text") or "No alert (no changes detected).")
        for change in state.get("changes", []):
            print(
                f"  [{change['severity']}] {change['item_id']} {change['field']}: "
                f"{change['old']} -> {change['new']}  (quote: {change['quote']!r})"
            )
        return 0

    if args.command == "export":
        save_json(args.out, load_json(args.state))
        print(f"exported -> {args.out}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

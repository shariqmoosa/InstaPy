"""CLI entry point for the shopping agent.

Single store:
    python -m shopping_agent.main --url "https://..." --zip "90210"

Compare multiple stores (same item):
    python -m shopping_agent.main compare \\
        --stores ubereats:https://... doordash:https://... instacart:https://... \\
        --zip "90210"

Record a store session (opens visible browser):
    python -m shopping_agent.main record --url "https://www.ubereats.com/store/7-eleven/..."

Replay a recorded playbook across multiple stores:
    python -m shopping_agent.main replay \\
        --stores "https://ubereats.com/..." "https://doordash.com/..." \\
        --playbook ubereats.com
"""
import argparse
import json
import sys

from .agent import ShoppingAgent
from .recorder import SiteRecorder
from .playbook import PlaybookRunner


def _print_result(result, label=""):
    header = f"{'='*50}\n{label or result.store_url}\n{'='*50}"
    print(header)
    print(result)
    print()


def _print_comparison(results):
    print("\n" + "="*60)
    print("FEE COMPARISON SUMMARY")
    print("="*60)
    print(f"{'Store':<20} {'Item':>8} {'Delivery':>10} {'Service':>9} {'Tax':>8} {'TOTAL':>10}")
    print("-"*60)
    for r in results:
        res = r["result"]
        def fmt(v):
            return f"${v:.2f}" if v is not None else "  N/A "
        print(
            f"{r['store'][:19]:<20} "
            f"{fmt(res.base_price):>8} "
            f"{fmt(res.shipping):>10} "
            f"{fmt(res.service_fee):>9} "
            f"{fmt(res.tax):>8} "
            f"{fmt(res.total):>10}"
        )
    print("="*60)


def cmd_single(args):
    agent = ShoppingAgent(
        provider=args.provider,
        api_key=args.api_key,
        model=args.model,
        headless=not args.no_headless,
    )
    result = agent.get_pricing(args.url, zip_code=args.zip, address=args.address)
    if args.json:
        print(result.to_json())
    else:
        _print_result(result)
    return 0 if result.total is not None else 1


def cmd_compare(args):
    # Parse "label:url" or plain "url" pairs
    stores = {}
    for s in args.stores:
        if ":" in s and not s.startswith("http"):
            label, url = s.split(":", 1)
        else:
            from .browser import _domain_from_url
            label = _domain_from_url(s)
            url = s
        stores[label] = url

    agent = ShoppingAgent(
        provider=args.provider,
        api_key=args.api_key,
        model=args.model,
        headless=not args.no_headless,
    )
    results = agent.compare(stores, zip_code=args.zip, address=args.address)

    if args.json:
        print(json.dumps(
            [{"store": r["store"], "url": r["url"], "pricing": r["result"].to_dict()}
             for r in results],
            indent=2,
        ))
    else:
        for r in results:
            _print_result(r["result"], label=r["store"])
        _print_comparison(results)
    return 0


def cmd_record(args):
    with SiteRecorder() as rec:
        steps = rec.record(args.url, description=args.description or "")
    print(f"\nRecorded {len(steps)} steps.")
    return 0


def cmd_replay(args):
    runner = PlaybookRunner(headless=not args.no_headless)
    results = runner.run_many(args.stores, playbook_domain=args.playbook)
    for url, result in results:
        _print_result(result, label=url)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Shopping agent — extract and compare fees across stores",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd")

    # Shared args
    def add_common(p):
        p.add_argument("--provider", default="auto",
                       choices=["auto", "claude", "openai", "gemini"],
                       help="AI provider (default: auto-detect from env vars)")
        p.add_argument("--api-key", default=None, help="API key for the provider")
        p.add_argument("--model", default=None, help="Model name override")
        p.add_argument("--zip", default=None, help="ZIP/postal code for delivery calculation")
        p.add_argument("--address", default=None, help="Full delivery address")
        p.add_argument("--no-headless", action="store_true", help="Show browser window")
        p.add_argument("--json", action="store_true", help="Output raw JSON")

    # Single store
    p_single = sub.add_parser("get", help="Get pricing for a single product URL")
    add_common(p_single)
    p_single.add_argument("--url", required=True)

    # Compare
    p_compare = sub.add_parser("compare", help="Compare fees across multiple stores")
    add_common(p_compare)
    p_compare.add_argument(
        "--stores", nargs="+", required=True,
        metavar="LABEL:URL",
        help="Store URLs, optionally prefixed with label: e.g. ubereats:https://...",
    )

    # Record
    p_record = sub.add_parser("record", help="Record a browser session as a playbook")
    p_record.add_argument("--url", required=True)
    p_record.add_argument("--description", default="")

    # Replay
    p_replay = sub.add_parser("replay", help="Replay a recorded playbook across stores")
    p_replay.add_argument("--stores", nargs="+", required=True)
    p_replay.add_argument("--playbook", default=None, help="Domain of the playbook to use")
    p_replay.add_argument("--no-headless", action="store_true")

    # Default: no subcommand → treat as single-store for backwards compat
    parser.add_argument("--url", default=None)
    add_common(parser)

    args = parser.parse_args()

    if args.cmd == "get" or (args.cmd is None and args.url):
        return cmd_single(args)
    elif args.cmd == "compare":
        return cmd_compare(args)
    elif args.cmd == "record":
        return cmd_record(args)
    elif args.cmd == "replay":
        return cmd_replay(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())

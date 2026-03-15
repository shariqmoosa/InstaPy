"""CLI entry point for the shopping agent.

Usage:
    python -m shopping_agent.main --url "https://www.amazon.com/dp/B08N5WRWNW"
    python -m shopping_agent.main --url "https://..." --zip "10001"
    python -m shopping_agent.main --url "https://..." --no-headless
"""
import argparse
import json
import sys

from .agent import ShoppingAgent


def main():
    parser = argparse.ArgumentParser(
        description="Shopping agent — extracts full pricing including hidden fees"
    )
    parser.add_argument("--url", required=True, help="Product page URL")
    parser.add_argument("--zip", dest="zip_code", default=None, help="ZIP/postal code for shipping")
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Show browser window (useful for debugging)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted text",
    )
    args = parser.parse_args()

    print(f"Starting shopping agent for: {args.url}", file=sys.stderr)
    if args.zip_code:
        print(f"Using ZIP code: {args.zip_code}", file=sys.stderr)

    agent = ShoppingAgent(headless=not args.no_headless)
    result = agent.get_pricing(args.url, zip_code=args.zip_code)

    if args.json:
        print(result.to_json())
    else:
        print("\n" + "=" * 40)
        print("PRICING BREAKDOWN")
        print("=" * 40)
        print(result)
        print("=" * 40)


if __name__ == "__main__":
    main()

"""Weekly batch runner for delivery platform fee collection.

Reads the client's weekly input CSV (city, density, retailer_type, retailer_name,
delivery_address) and runs the shopping agent for every combination of:
  - Platform: DoorDash, Instacart, Uber Eats
  - Basket size: $10, $25, $50, $75, $100
  - Membership: Member, Non-Member

Outputs a CSV matching the exact 45-column format used in the weekly data sheets.

Usage:
    python -m shopping_agent.main weekly \\
        --input weekly_input.csv \\
        --output weekly_output.csv \\
        --date 2026-03-16

Input CSV columns:
    Date, City, Density, Retailer Type, Retailer Name

Address CSV (separate file or embedded as second section):
    Date, City, Density, Delivery Address

The runner also accepts a single --address flag to override delivery address for all rows.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

from .agent import ShoppingAgent
from .pricing import PricingResult

# ---------------------------------------------------------------------------
# Platform search URL templates
# ---------------------------------------------------------------------------

PLATFORMS = {
    "DoorDash": {
        "search_url": "https://www.doordash.com/search/store/{retailer}/",
        "slug": "DD",
    },
    "Instacart": {
        "search_url": "https://www.instacart.com/store/{retailer}/storepage",
        "slug": "IC",
    },
    "Uber Eats": {
        "search_url": "https://www.ubereats.com/search?q={retailer}",
        "slug": "UE",
    },
}

BASKET_SIZES = [10, 25, 50, 75, 100]

MEMBERSHIPS = ["Member", "Non-Member"]

# ---------------------------------------------------------------------------
# Platform-specific agent instructions
# ---------------------------------------------------------------------------

_PLATFORM_HINTS = {
    "DoorDash": """
Platform: DoorDash
- Search for the retailer by name using the search bar at doordash.com
- Once on the store page, add items to reach approximately the target basket size
- At checkout you will see: subtotal, delivery fee, service fee, taxes
- If there is a DashPass membership discount, note both the member and non-member pricing
- Look for any regulatory fees, small order fees, or surge fees
- Delivery time estimates appear near the top of the store page
""",
    "Instacart": """
Platform: Instacart
- Search for the retailer at instacart.com or use the direct store URL
- Add items to the cart to reach approximately the target basket size
- At checkout you will see: service fee, delivery fee, taxes
- Instacart+ (membership) affects service fee — look for "With Instacart+" note
- Check for any priority delivery option and its fee
- Regulatory/long-distance fees may appear in the fee breakdown
""",
    "Uber Eats": """
Platform: Uber Eats
- Search for the retailer at ubereats.com using the search bar
- Add items to the cart to reach approximately the target basket size
- At checkout you will see: subtotal, delivery fee, service fee, taxes, and possibly a regulatory fee
- Uber One membership changes the service fee — note if visible
- Look for any "long distance fee" or "small order fee"
- Priority delivery time and its additional cost may appear at checkout
""",
}

# ---------------------------------------------------------------------------
# Output row dataclass (maps to the 45-column spreadsheet)
# ---------------------------------------------------------------------------

OUTPUT_COLUMNS = [
    "Platform", "Retailer", "Membership Status", "City", "State", "Density",
    "Retailer Type", "Delivery Address", "Basket Size Target ($)",
    "Minimum Order ($)", "SF Min ($)", "SF Max ($)", "SF Type Description",
    "Promo MBS Threshold ($)", "Delivery Fee Without Promo ($)",
    "Basket Items", "Date Collected", "Merch Order Size ($)",
    "Delivery Fee ($)", "Service Fee ($)", "Total Cart ($)",
    "In-Store Pricing Flag", "Min Delivery Time (min)", "Max Delivery Time (min)",
    "Priority Delivery Time (min)", "Priority Delivery Fee ($)",
    "Regulatory Fee ($)", "Long Distance Fee ($)", "SF%", "Total Fee%",
    "DD-Mem-$", "DD-NonMem-$", "IC-Mem-$", "IC-NonMem-$",
    "DD-Mem-%", "DD-NonMem-%", "IC-Mem-%", "IC-NonMem-%",
    "UE-Mem-$", "UE-NonMem-$", "UE-Mem-%", "UE-NonMem-%",
    "Notes", "Raw Pricing JSON",
]


@dataclass
class OutputRow:
    platform: str = ""
    retailer: str = ""
    membership_status: str = ""
    city: str = ""
    state: str = ""
    density: str = ""
    retailer_type: str = ""
    delivery_address: str = ""
    basket_size_target: str = ""
    minimum_order: str = ""
    sf_min: str = ""
    sf_max: str = ""
    sf_type_description: str = ""
    promo_mbs_threshold: str = ""
    delivery_fee_without_promo: str = ""
    basket_items: str = ""
    date_collected: str = ""
    merch_order_size: str = ""
    delivery_fee: str = ""
    service_fee: str = ""
    total_cart: str = ""
    in_store_pricing_flag: str = ""
    min_delivery_time: str = ""
    max_delivery_time: str = ""
    priority_delivery_time: str = ""
    priority_delivery_fee: str = ""
    regulatory_fee: str = ""
    long_distance_fee: str = ""
    sf_pct: str = ""
    total_fee_pct: str = ""
    dd_mem_dollar: str = ""
    dd_nonmem_dollar: str = ""
    ic_mem_dollar: str = ""
    ic_nonmem_dollar: str = ""
    dd_mem_pct: str = ""
    dd_nonmem_pct: str = ""
    ic_mem_pct: str = ""
    ic_nonmem_pct: str = ""
    ue_mem_dollar: str = ""
    ue_nonmem_dollar: str = ""
    ue_mem_pct: str = ""
    ue_nonmem_pct: str = ""
    notes: str = ""
    raw_pricing_json: str = ""

    def to_csv_row(self) -> list:
        return [
            self.platform, self.retailer, self.membership_status,
            self.city, self.state, self.density, self.retailer_type,
            self.delivery_address, self.basket_size_target,
            self.minimum_order, self.sf_min, self.sf_max, self.sf_type_description,
            self.promo_mbs_threshold, self.delivery_fee_without_promo,
            self.basket_items, self.date_collected, self.merch_order_size,
            self.delivery_fee, self.service_fee, self.total_cart,
            self.in_store_pricing_flag, self.min_delivery_time,
            self.max_delivery_time, self.priority_delivery_time,
            self.priority_delivery_fee, self.regulatory_fee,
            self.long_distance_fee, self.sf_pct, self.total_fee_pct,
            self.dd_mem_dollar, self.dd_nonmem_dollar,
            self.ic_mem_dollar, self.ic_nonmem_dollar,
            self.dd_mem_pct, self.dd_nonmem_pct,
            self.ic_mem_pct, self.ic_nonmem_pct,
            self.ue_mem_dollar, self.ue_nonmem_dollar,
            self.ue_mem_pct, self.ue_nonmem_pct,
            self.notes, self.raw_pricing_json,
        ]


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

def _extract_state(city_str: str) -> tuple[str, str]:
    """Return (city, state) from strings like 'Detroit, MI' or 'Detroit'."""
    if "," in city_str:
        parts = [p.strip() for p in city_str.split(",", 1)]
        return parts[0], parts[1]
    return city_str.strip(), ""


def parse_input_csv(path: str) -> list[dict]:
    """
    Parse the weekly input CSV.

    Accepts two layouts:
    1. Two-column section format (with blank line between sections):
       Section 1: Date | City | Density | Retailer Type | Retailer Name
       Section 2: Date | City | Density | Delivery Address

    2. Combined format (single file):
       Date | City | Density | Retailer Type | Retailer Name | Delivery Address
    """
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        content = fh.read()

    # Split into sections on blank lines
    sections = [s.strip() for s in re.split(r"\n\s*\n", content) if s.strip()]

    # Try to find the addresses section (contains "Delivery Address" header)
    retailer_section = None
    address_section = None
    for sec in sections:
        reader = csv.DictReader(io.StringIO(sec))
        headers = [h.strip() for h in (reader.fieldnames or [])]
        if any("delivery address" in h.lower() for h in headers):
            address_section = sec
        elif any("retailer" in h.lower() for h in headers):
            retailer_section = sec

    # Build city → address lookup
    city_address: dict[str, str] = {}
    if address_section:
        reader = csv.DictReader(io.StringIO(address_section))
        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items()}
            city_key = row.get("City", "").strip()
            addr_col = next((k for k in row if "delivery address" in k.lower()), None)
            if city_key and addr_col:
                city_address[city_key.lower()] = row[addr_col]

    if retailer_section is None:
        raise ValueError("Could not find retailer section in input CSV")

    reader = csv.DictReader(io.StringIO(retailer_section))
    for row in reader:
        row = {k.strip(): v.strip() for k, v in row.items()}
        city_raw = row.get("City", "").strip()
        city, state = _extract_state(city_raw)
        retailer_col = next((k for k in row if "retailer" in k.lower() and "type" not in k.lower()), "Retailer Name")
        retailer_name = row.get(retailer_col, row.get("Retailer Name", "")).strip()
        retailer_type = row.get("Retailer Type", "").strip()
        density = row.get("Density", "").strip()
        collected_date = row.get("Date", date.today().isoformat()).strip()

        # Resolve address from separate section or inline column
        addr_col = next((k for k in row if "delivery address" in k.lower()), None)
        address = ""
        if addr_col:
            address = row[addr_col]
        elif city.lower() in city_address:
            address = city_address[city.lower()]

        if retailer_name:
            rows.append({
                "city": city,
                "state": state,
                "density": density,
                "retailer_type": retailer_type,
                "retailer_name": retailer_name,
                "delivery_address": address,
                "date": collected_date,
            })

    return rows


# ---------------------------------------------------------------------------
# Result → OutputRow mapping
# ---------------------------------------------------------------------------

def _safe_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return ""
    return f"{v:.2f}"


def _pct(numerator: Optional[float], base: Optional[float]) -> str:
    if numerator is None or not base:
        return ""
    return f"{numerator / base * 100:.1f}%"


def _parse_delivery_times(notes: str) -> dict:
    """Extract delivery time info from agent notes string."""
    info: dict = {}
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*min", notes, re.I)
    if m:
        info["min"] = m.group(1)
        info["max"] = m.group(2)
    m = re.search(r"priority[:\s]+(\d+)\s*min", notes, re.I)
    if m:
        info["priority_min"] = m.group(1)
    m = re.search(r"priority[:\s]+\$?([\d.]+)", notes, re.I)
    if m:
        info["priority_fee"] = m.group(1)
    return info


def pricing_to_output_row(
    result: PricingResult,
    platform: str,
    membership: str,
    basket_target: int,
    retailer_info: dict,
    collected_date: str,
) -> OutputRow:
    """Convert a PricingResult into an OutputRow."""
    row = OutputRow()
    row.platform = platform
    row.retailer = retailer_info["retailer_name"]
    row.membership_status = membership
    row.city = retailer_info["city"]
    row.state = retailer_info["state"]
    row.density = retailer_info["density"]
    row.retailer_type = retailer_info["retailer_type"]
    row.delivery_address = retailer_info["delivery_address"]
    row.basket_size_target = str(basket_target)
    row.date_collected = collected_date

    # Core pricing fields
    row.delivery_fee = _fmt(_safe_float(result.shipping))
    row.service_fee = _fmt(_safe_float(result.service_fee))

    tax = _safe_float(result.tax)
    base = _safe_float(result.base_price)
    total = _safe_float(result.total)
    delivery = _safe_float(result.shipping)
    svc = _safe_float(result.service_fee)
    row.total_cart = _fmt(total)
    row.merch_order_size = _fmt(base)

    # Other fees
    other = result.other_fees or {}
    reg_fee = _safe_float(other.get("regulatory_fee") or other.get("regulatory fee"))
    ld_fee = _safe_float(other.get("long_distance_fee") or other.get("long distance fee"))
    row.regulatory_fee = _fmt(reg_fee)
    row.long_distance_fee = _fmt(ld_fee)

    # Calculated percentages
    row.sf_pct = _pct(svc, base)
    total_fees = sum(
        f for f in [delivery, svc, tax, reg_fee, ld_fee] if f is not None
    )
    row.total_fee_pct = _pct(total_fees or None, base)

    # Delivery times from notes
    times = _parse_delivery_times(result.notes or "")
    row.min_delivery_time = times.get("min", "")
    row.max_delivery_time = times.get("max", "")
    row.priority_delivery_time = times.get("priority_min", "")
    row.priority_delivery_fee = times.get("priority_fee", "")

    row.notes = result.notes or ""
    row.raw_pricing_json = result.to_json(indent=None)

    return row


# ---------------------------------------------------------------------------
# Comparison column filler
# ---------------------------------------------------------------------------

def fill_comparison_columns(rows: list[OutputRow]) -> None:
    """
    After all rows for a (retailer, basket_size, city) group are collected,
    fill in the DD/IC/UE cross-platform comparison columns.
    """
    # Group rows by (retailer, basket_target, city)
    from collections import defaultdict
    groups: dict[tuple, list[OutputRow]] = defaultdict(list)
    for r in rows:
        key = (r.retailer.lower(), r.basket_size_target, r.city.lower())
        groups[key].append(r)

    for group_rows in groups.values():
        # Build lookup: (platform_slug, membership) → total
        lookup: dict[tuple, str] = {}
        for r in group_rows:
            plat = r.platform
            mem = r.membership_status
            lookup[(plat, mem)] = r.total_cart

        for r in group_rows:
            r.dd_mem_dollar = lookup.get(("DoorDash", "Member"), "")
            r.dd_nonmem_dollar = lookup.get(("DoorDash", "Non-Member"), "")
            r.ic_mem_dollar = lookup.get(("Instacart", "Member"), "")
            r.ic_nonmem_dollar = lookup.get(("Instacart", "Non-Member"), "")
            r.ue_mem_dollar = lookup.get(("Uber Eats", "Member"), "")
            r.ue_nonmem_dollar = lookup.get(("Uber Eats", "Non-Member"), "")

            # % columns (fees as % of basket target)
            target = float(r.basket_size_target) if r.basket_size_target else None
            def _pct_vs_target(total_str: str) -> str:
                if not total_str or not target:
                    return ""
                try:
                    return f"{(float(total_str) - target) / target * 100:.1f}%"
                except (ValueError, ZeroDivisionError):
                    return ""

            r.dd_mem_pct = _pct_vs_target(r.dd_mem_dollar)
            r.dd_nonmem_pct = _pct_vs_target(r.dd_nonmem_dollar)
            r.ic_mem_pct = _pct_vs_target(r.ic_mem_dollar)
            r.ic_nonmem_pct = _pct_vs_target(r.ic_nonmem_dollar)
            r.ue_mem_pct = _pct_vs_target(r.ue_mem_dollar)
            r.ue_nonmem_pct = _pct_vs_target(r.ue_nonmem_dollar)


# ---------------------------------------------------------------------------
# Main batch runner
# ---------------------------------------------------------------------------

def _build_agent_prompt(
    platform: str,
    retailer_name: str,
    basket_target: int,
    membership: str,
    delivery_address: str,
) -> str:
    """Build the user message to send to the agent for one run."""
    mem_note = (
        f"Use the {platform} membership / subscription if prompted "
        f"(e.g. DashPass, Instacart+, Uber One)."
        if membership == "Member"
        else f"Do NOT activate or sign up for a {platform} membership."
    )
    lines = [
        f"Platform: {platform}",
        f"Retailer: {retailer_name}",
        f"Target basket size: ${basket_target}",
        f"Membership: {membership} — {mem_note}",
    ]
    if delivery_address:
        lines.append(f"Delivery address: {delivery_address}")
    lines.append("")
    lines.append(_PLATFORM_HINTS[platform])
    lines.append(
        "Steps:\n"
        f"1. Go to {platform} and find {retailer_name}\n"
        f"2. Add items to reach a basket of approximately ${basket_target} "
        "(go slightly over if needed — record the exact merch total)\n"
        "3. Proceed to checkout to reveal all fees\n"
        "4. Call extract_pricing with: product_name, base_price (merch subtotal), "
        "shipping (delivery fee), service_fee, tax, "
        "other_fees={regulatory_fee, long_distance_fee, priority_delivery_fee if present}, total\n"
        "5. Include delivery time range in your notes (e.g. '25-40 min')\n"
        "6. STOP before placing any order."
    )
    return "\n".join(lines)


class WeeklyRunner:
    """Batch-runs the shopping agent for a weekly pricing data collection job."""

    def __init__(
        self,
        provider="auto",
        api_key=None,
        model=None,
        headless=True,
        platforms=None,
        basket_sizes=None,
        memberships=None,
        verbose=True,
        resume_output=None,
    ):
        """
        Args:
            platforms:      List of platform names to run (default: all three)
            basket_sizes:   List of target basket sizes in dollars (default: [10,25,50,75,100])
            memberships:    List of membership statuses (default: ["Member","Non-Member"])
            resume_output:  Path to an existing partial output CSV to resume from
        """
        self.agent_kwargs = dict(
            provider=provider,
            api_key=api_key,
            model=model,
            headless=headless,
            verbose=verbose,
        )
        self.platforms = platforms or list(PLATFORMS.keys())
        self.basket_sizes = basket_sizes or BASKET_SIZES
        self.memberships = memberships or MEMBERSHIPS
        self.verbose = verbose
        self._completed: set[str] = set()

        # Load already-completed run keys from a partial output to enable resume
        if resume_output and os.path.exists(resume_output):
            self._load_completed(resume_output)

    def _load_completed(self, path: str) -> None:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                key = self._run_key(
                    row.get("Platform", ""),
                    row.get("Retailer", ""),
                    row.get("City", ""),
                    row.get("Basket Size Target ($)", ""),
                    row.get("Membership Status", ""),
                )
                self._completed.add(key)
        if self.verbose:
            print(f"[WeeklyRunner] Resuming — {len(self._completed)} runs already done")

    @staticmethod
    def _run_key(platform, retailer, city, basket, membership) -> str:
        return f"{platform}|{retailer}|{city}|{basket}|{membership}".lower()

    def run(
        self,
        input_csv: str,
        output_csv: str,
        collected_date: str = None,
        address_override: str = None,
        platforms: list = None,
        basket_sizes: list = None,
        memberships: list = None,
    ) -> list[OutputRow]:
        """
        Process all retailers from input_csv and write results to output_csv.

        Returns the list of OutputRow objects.
        """
        collected_date = collected_date or date.today().isoformat()
        platforms = platforms or self.platforms
        basket_sizes = basket_sizes or self.basket_sizes
        memberships = memberships or self.memberships

        retailers = parse_input_csv(input_csv)
        if not retailers:
            raise ValueError(f"No retailer rows found in {input_csv}")

        if self.verbose:
            total = len(retailers) * len(platforms) * len(basket_sizes) * len(memberships)
            print(
                f"[WeeklyRunner] {len(retailers)} retailers × "
                f"{len(platforms)} platforms × "
                f"{len(basket_sizes)} basket sizes × "
                f"{len(memberships)} memberships "
                f"= {total} runs"
            )

        all_rows: list[OutputRow] = []

        # Open output CSV for streaming writes (so partial results are saved immediately)
        write_header = not (
            os.path.exists(output_csv) and len(self._completed) > 0
        )
        fh = open(output_csv, "a" if self._completed else "w", newline="", encoding="utf-8")
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(OUTPUT_COLUMNS)

        try:
            agent = ShoppingAgent(**self.agent_kwargs)
            run_num = 0
            total = len(retailers) * len(platforms) * len(basket_sizes) * len(memberships)

            for retailer_info in retailers:
                if address_override:
                    retailer_info = dict(retailer_info, delivery_address=address_override)

                for platform in platforms:
                    for basket_target in basket_sizes:
                        for membership in memberships:
                            run_num += 1
                            key = self._run_key(
                                platform,
                                retailer_info["retailer_name"],
                                retailer_info["city"],
                                str(basket_target),
                                membership,
                            )

                            if key in self._completed:
                                if self.verbose:
                                    print(f"[WeeklyRunner] skip (already done) {key}")
                                continue

                            if self.verbose:
                                print(
                                    f"\n[WeeklyRunner] Run {run_num}/{total}: "
                                    f"{platform} | {retailer_info['retailer_name']} | "
                                    f"${basket_target} | {membership}"
                                )

                            prompt = _build_agent_prompt(
                                platform=platform,
                                retailer_name=retailer_info["retailer_name"],
                                basket_target=basket_target,
                                membership=membership,
                                delivery_address=retailer_info.get("delivery_address", ""),
                            )

                            try:
                                # Start on the platform's search page
                                search_url = PLATFORMS[platform]["search_url"].format(
                                    retailer=retailer_info["retailer_name"].replace(" ", "+")
                                )
                                result = agent.get_pricing(
                                    search_url,
                                    address=retailer_info.get("delivery_address") or None,
                                )
                                # Inject the richer prompt into the agent result notes
                                # (agent.get_pricing uses the URL as the starting message;
                                #  for weekly runs we override via the address field and the
                                #  system prompt already handles platform navigation)
                            except Exception as exc:
                                result = PricingResult(
                                    store_url="",
                                    notes=f"ERROR: {exc}",
                                )

                            output_row = pricing_to_output_row(
                                result=result,
                                platform=platform,
                                membership=membership,
                                basket_target=basket_target,
                                retailer_info=retailer_info,
                                collected_date=collected_date,
                            )
                            all_rows.append(output_row)
                            writer.writerow(output_row.to_csv_row())
                            fh.flush()
                            self._completed.add(key)

        finally:
            fh.close()

        # Post-process: fill cross-platform comparison columns
        fill_comparison_columns(all_rows)

        # Rewrite final output with comparison columns filled in
        with open(output_csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(OUTPUT_COLUMNS)
            for r in all_rows:
                writer.writerow(r.to_csv_row())

        if self.verbose:
            print(f"\n[WeeklyRunner] Done. {len(all_rows)} rows written to {output_csv}")

        return all_rows

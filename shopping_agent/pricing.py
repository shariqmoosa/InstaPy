from dataclasses import dataclass, field, asdict
import json


@dataclass
class PricingResult:
    product_name: str = ""
    base_price: float = None
    shipping: float = None
    tax: float = None
    service_fee: float = None
    other_fees: dict = field(default_factory=dict)
    total: float = None
    currency: str = "USD"
    store_url: str = ""
    notes: str = ""

    def to_dict(self):
        return asdict(self)

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent)

    def __str__(self):
        lines = [f"Product : {self.product_name}"]
        if self.base_price is not None:
            lines.append(f"Price   : {self.currency} {self.base_price:.2f}")
        if self.shipping is not None:
            lines.append(f"Shipping: {self.currency} {self.shipping:.2f}")
        if self.tax is not None:
            lines.append(f"Tax     : {self.currency} {self.tax:.2f}")
        if self.service_fee is not None:
            lines.append(f"Service : {self.currency} {self.service_fee:.2f}")
        for name, amount in (self.other_fees or {}).items():
            lines.append(f"{name:<8}: {self.currency} {amount:.2f}")
        if self.total is not None:
            lines.append("-" * 30)
            lines.append(f"Total   : {self.currency} {self.total:.2f}")
        if self.notes:
            lines.append(f"Notes   : {self.notes}")
        return "\n".join(lines)

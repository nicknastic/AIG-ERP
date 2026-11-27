"""Expense categories mapped to CRA form T2125, Part 5 (Business expenses).

A general partnership whose partners are individuals does not file its own
income tax return. Each partner reports their share of the partnership's
income and expenses on their own T1 using a T2125, so the expense lines here
follow the T2125 line numbers.

`deductible_pct` is the share of the amount that is deductible before any
business-use percentage is applied. Meals and entertainment are limited to
50% by section 67.1 of the Income Tax Act.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    line: str
    name: str
    hint: str
    deductible_pct: int = 100


CATEGORIES: list[Category] = [
    Category("8521", "Advertising", "ads, promotion, website ads, business cards"),
    Category("8523", "Meals and entertainment", "restaurant meals, client entertainment", 50),
    Category("8590", "Bad debts", "uncollectible amounts already included in income"),
    Category("8690", "Insurance", "business insurance (not life insurance)"),
    Category("8710", "Interest and bank charges", "business loan interest, bank fees, card fees"),
    Category("8760", "Business taxes, licences and memberships", "business licence, professional dues, subscriptions to trade bodies"),
    Category("8810", "Office expenses", "small office items: pens, paper, postage, toner"),
    Category("8811", "Office stationery and supplies", "supplies used directly in the business"),
    Category("8860", "Professional fees", "accounting, legal, bookkeeping, consulting"),
    Category("8871", "Management and administration fees", "management or admin services bought in"),
    Category("8910", "Rent", "rent for business premises or equipment"),
    Category("8960", "Repairs and maintenance", "repairs to business property or equipment"),
    Category("9060", "Salaries, wages and benefits", "payments to employees, employer CPP/EI"),
    Category("9180", "Property taxes", "property tax on business property"),
    Category("9200", "Travel expenses", "airfare, hotels, taxis, transit, parking on trips"),
    Category("9220", "Telephone and utilities", "phone, internet, hydro, gas for business premises"),
    Category("9224", "Fuel costs (except for motor vehicles)", "heating fuel, generator fuel"),
    Category("9275", "Delivery, freight and express", "courier, shipping, postage for deliveries"),
    Category("9281", "Motor vehicle expenses", "gas, insurance, repairs, licence for a business vehicle"),
    Category("9270", "Other expenses", "software, cloud services, small tools, anything else"),
    Category("9936", "Capital cost allowance (CCA)", "computers, furniture, equipment, vehicles: depreciated, not expensed"),
    Category("9945", "Business-use-of-home expenses", "share of home rent, utilities, insurance"),
]

BY_LINE: dict[str, Category] = {c.line: c for c in CATEGORIES}
DEFAULT_LINE = "9270"


def category_for(line: str | None) -> Category:
    return BY_LINE.get(line or "", BY_LINE[DEFAULT_LINE])

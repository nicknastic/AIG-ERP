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
    name: str      # CRA name of the T2125 line
    hint: str
    deductible_pct: int = 100
    label: str = ""  # everyday expense type shown in the app and the Excel file

    @property
    def type(self) -> str:
        return self.label or self.name


CATEGORIES: list[Category] = [
    Category("8521", "Advertising", "ads, promotion, website ads, business cards", label="Advertising and marketing"),
    Category("8523", "Meals and entertainment", "restaurant meals, client entertainment", 50, label="Entertainment / business development"),
    Category("8590", "Bad debts", "uncollectible amounts already included in income", label="Bad debts"),
    Category("8690", "Insurance", "business insurance (not life insurance)", label="Insurance"),
    Category("8710", "Interest and bank charges", "business loan interest, bank fees, card fees", label="Bank fees and interest"),
    Category("8760", "Business taxes, licences and memberships", "business licence, professional dues, subscriptions to trade bodies", label="Licences and memberships"),
    Category("8810", "Office expenses", "small office items: pens, paper, postage, toner", label="Office expenses"),
    Category("8811", "Office stationery and supplies", "supplies used directly in the business", label="Supplies"),
    Category("8860", "Professional fees", "accounting, legal, bookkeeping, consulting", label="Accounting and legal"),
    Category("8871", "Management and administration fees", "management or admin services bought in", label="Management and admin fees"),
    Category("8910", "Rent", "rent for business premises or equipment", label="Rent"),
    Category("8960", "Repairs and maintenance", "repairs to business property or equipment", label="Repairs and maintenance"),
    Category("9060", "Salaries, wages and benefits", "payments to employees, employer CPP/EI", label="Salaries and wages"),
    Category("9180", "Property taxes", "property tax on business property", label="Property taxes"),
    Category("9200", "Travel expenses", "airfare, hotels, taxis, transit, parking on trips", label="Travel"),
    Category("9220", "Telephone and utilities", "phone, internet, hydro, gas for business premises", label="Phone and internet"),
    Category("9224", "Fuel costs (except for motor vehicles)", "heating fuel, generator fuel", label="Fuel (not vehicle)"),
    Category("9275", "Delivery, freight and express", "courier, shipping, postage for deliveries", label="Shipping and courier"),
    Category("9281", "Motor vehicle expenses", "gas, insurance, repairs, licence for a business vehicle", label="Vehicle"),
    Category("9270", "Other expenses", "software, cloud services, small tools, anything else", label="Software and other"),
    Category("9936", "Capital cost allowance (CCA)", "computers, furniture, equipment, vehicles: depreciated, not expensed", label="Equipment"),
    Category("9945", "Business-use-of-home expenses", "share of home rent, utilities, insurance", label="Home office"),
]

BY_LINE: dict[str, Category] = {c.line: c for c in CATEGORIES}
DEFAULT_LINE = "9270"


def category_for(line: str | None) -> Category:
    return BY_LINE.get(line or "", BY_LINE[DEFAULT_LINE])

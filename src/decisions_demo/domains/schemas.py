"""Investing-domain output schemas, used by every backend.

The taxonomy is an Enum because member docstrings are what Jev reads as the
option descriptions: this is where a fund puts its own definitions. These nine
start from YC's industries so YC's label can serve as the reference. YC files
Infrastructure, Engineering, Product and Design, and Marketing under B2B, so the
B2B definition says it is the fallback for business software.
"""

from enum import Enum

from pydantic_ai import UseEnumMemberDocstrings


class Domain(UseEnumMemberDocstrings, str, Enum):
    b2b = 'B2B'
    """Software or services businesses use to run their operations, such as finance, HR, legal, sales, security or supply chain, when none of Infrastructure, Engineering, Product and Design, or Marketing fits better."""
    consumer = 'Consumer'
    """Products individuals buy or use for themselves: apps, games, content, social, food, apparel and other consumer goods."""
    fintech = 'Fintech'
    """Financial services or financial infrastructure: payments, banking, lending, insurance, asset management or consumer finance."""
    healthcare = 'Healthcare'
    """Medicine and health: care delivery, healthcare IT, diagnostics, medical devices, therapeutics, drug discovery or industrial biotech."""
    industrials = 'Industrials'
    """Physical industry: manufacturing, robotics, energy, climate, aerospace, defense, drones, automotive or agriculture."""
    infrastructure = 'Infrastructure'
    """The technical foundation other software runs on: cloud, compute, databases, data pipelines, networking and AI infrastructure."""
    engineering_product_design = 'Engineering, Product and Design'
    """Tools engineering, product and design teams use to build software: developer tools, testing, code review, design and product management."""
    marketing = 'Marketing'
    """Tools and services that help businesses attract customers: advertising, growth, SEO, content marketing and customer engagement."""
    real_estate_construction = 'Real Estate and Construction'
    """Property and the built environment: housing, buying, selling or managing real estate, and construction."""
    other = 'Other'
    """None of the domains above fit, for example education, government or nonprofits."""

"""Deterministic seeding for the insurance demo database (SPEC §4.2).

All randomness flows through a single random.Random(42) instance consumed in
a fixed table order (products -> agents -> customers -> policies -> claims ->
payments), so repeated runs produce identical data. uuid4()/now()/
date.today() are never used: the "current time" is the fixed AS_OF constant.

The script only ever touches ADMIN_DATABASE_URL (seed flow); the application
itself connects as the read-only role created by 02_roles.sql.
"""

import os
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import NamedTuple

import psycopg
from dotenv import load_dotenv

DB_DIR = Path(__file__).resolve().parent

SEED = 42
WINDOW_START = date(2023, 1, 1)
WINDOW_END = date(2025, 12, 31)
AS_OF = date(2025, 12, 31)  # fixed "today"; date.today() is forbidden (SPEC §4.2)
DEFAULT_ADMIN_URL = "postgresql://postgres:postgres@localhost:5432/insurance"

EXPECTED_ROWS = {
    "products": 12,
    "agents": 40,
    "customers": 500,
    "policies": 2_000,
    "claims": 600,
    "payments": 8_000,
}
PAYMENTS_PER_POLICY = 4  # 2,000 policies x 4 periods = 8,000 payments

CITIES = [
    "北京", "上海", "广州", "深圳", "杭州", "成都",
    "武汉", "南京", "重庆", "西安", "苏州", "天津",
]
SURNAMES = [
    "王", "李", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴",
    "徐", "孙", "胡", "朱", "高", "林", "何", "郭", "马", "罗",
]
GIVEN_NAMES = [
    "伟", "芳", "娜", "敏", "静", "磊", "军", "洋", "勇", "艳",
    "杰", "娟", "涛", "明", "超", "秀英", "国栋", "欣怡", "子涵", "志强",
]

CATEGORY_LABEL = {
    "life": "寿险",
    "critical_illness": "重疾险",
    "medical": "医疗险",
    "accident": "意外险",
    "annuity": "年金险",
}
TERM_CHOICES = {
    "life": (0, 20, 30),  # 0 = whole life
    "critical_illness": (20, 30),
    "medical": (1,),
    "accident": (1,),
    "annuity": (10, 15),
}
SUM_ASSURED_RANGE = {
    "life": (300_000, 2_000_000),
    "critical_illness": (200_000, 1_000_000),
    "medical": (50_000, 300_000),
    "accident": (100_000, 500_000),
    "annuity": (100_000, 800_000),
}
PREMIUM_RATE = {  # annual premium as a fraction of sum assured
    "life": (0.015, 0.04),
    "critical_illness": (0.03, 0.08),
    "accident": (0.005, 0.02),
    "annuity": (0.08, 0.15),
    "medical": None,  # flat band below
}

# Exact counts, not weights: ~75% in_force (SPEC §4.2) with a fixed split.
POLICY_STATUS_COUNTS = {
    "in_force": 1_500,
    "lapsed": 200,
    "surrendered": 200,
    "expired": 100,
}


class ProductRow(NamedTuple):
    code: str
    name: str
    category: str
    term_years: int
    launched_date: date
    is_active: bool


class AgentRow(NamedTuple):
    code: str
    name: str
    branch_city: str
    hire_date: date


class CustomerRow(NamedTuple):
    name: str
    gender: str
    birth_date: date
    city: str
    risk_level: str
    created_at: datetime


class PolicyRow(NamedTuple):
    no: str
    customer_id: int
    product_id: int
    agent_id: int
    status: str
    effective_date: date
    expiry_date: date
    sum_assured: int
    annual_premium: int


class ClaimRow(NamedTuple):
    no: str
    policy_id: int
    filed_date: date
    status: str
    claimed_amount: int
    approved_amount: int | None
    closed_date: date | None


class PaymentRow(NamedTuple):
    policy_id: int
    period_no: int
    due_date: date
    paid_date: date | None
    amount: int
    method: str
    status: str


def rand_date(rng: random.Random, start: date, end: date) -> date:
    """Uniform date in [start, end]; day capped at 28 so add_years stays valid."""
    offset = rng.randrange((end - start).days + 1)
    picked = start + timedelta(days=offset)
    return picked.replace(day=min(picked.day, 28))


def add_years(d: date, years: int) -> date:
    """Shift by whole years; safe because every seeded day is <= 28."""
    return date(d.year + years, d.month, d.day)


def gen_products(rng: random.Random) -> list[ProductRow]:
    categories = list(CATEGORY_LABEL)
    rows: list[ProductRow] = []
    for i in range(EXPECTED_ROWS["products"]):
        category = categories[i % len(categories)]
        series = chr(ord("A") + i // len(categories))
        rows.append(
            ProductRow(
                code=f"PRD-{i + 1:03d}",
                name=f"安泰{CATEGORY_LABEL[category]}{series}款",
                category=category,
                term_years=rng.choice(TERM_CHOICES[category]),
                launched_date=rand_date(rng, WINDOW_START, WINDOW_END),
                is_active=rng.random() < 0.85,
            )
        )
    return rows


def gen_agents(rng: random.Random) -> list[AgentRow]:
    rows: list[AgentRow] = []
    for i in range(EXPECTED_ROWS["agents"]):
        rows.append(
            AgentRow(
                code=f"AGT-{i + 1:03d}",
                name=rng.choice(SURNAMES) + rng.choice(GIVEN_NAMES),
                branch_city=rng.choice(CITIES),
                hire_date=rand_date(rng, date(2015, 1, 1), date(2024, 12, 31)),
            )
        )
    return rows


def gen_customers(rng: random.Random) -> list[CustomerRow]:
    rows: list[CustomerRow] = []
    for _ in range(EXPECTED_ROWS["customers"]):
        birth_year = rng.randrange(1955, 2008)
        risk = rng.random()
        created_day = rand_date(rng, WINDOW_START, WINDOW_END)
        rows.append(
            CustomerRow(
                name=rng.choice(SURNAMES) + rng.choice(GIVEN_NAMES),
                gender="M" if rng.random() < 0.5 else "F",
                birth_date=rand_date(rng, date(birth_year, 1, 1), date(birth_year, 12, 31)),
                city=rng.choice(CITIES),
                risk_level="low" if risk < 0.5 else "medium" if risk < 0.85 else "high",
                created_at=datetime.combine(
                    created_day, time(rng.randrange(8, 20), rng.randrange(60), rng.randrange(60))
                ),
            )
        )
    return rows


def gen_policies(rng: random.Random, products: list[ProductRow]) -> list[PolicyRow]:
    statuses = [s for s, n in POLICY_STATUS_COUNTS.items() for _ in range(n)]
    rng.shuffle(statuses)
    rows: list[PolicyRow] = []
    for i, status in enumerate(statuses):
        if status == "in_force":
            effective = rand_date(rng, WINDOW_START, WINDOW_END)
            term = rng.choice((5, 10, 15, 20, 30))  # expiry always > AS_OF
        elif status == "lapsed":
            effective = rand_date(rng, WINDOW_START, date(2024, 6, 30))
            term = rng.choice((5, 10, 15, 20))
        elif status == "surrendered":
            effective = rand_date(rng, WINDOW_START, WINDOW_END)
            term = rng.choice((5, 10, 15, 20))
        else:  # expired: expiry must land inside the window
            effective = rand_date(rng, WINDOW_START, date(2024, 12, 31))
            term = rng.randint(1, WINDOW_END.year - effective.year)

        product_id = rng.randrange(1, len(products) + 1)
        category = products[product_id - 1].category
        sum_assured = rng.randrange(*SUM_ASSURED_RANGE[category])
        if PREMIUM_RATE[category] is None:
            premium = rng.randrange(500, 3_000)
        else:
            lo, hi = PREMIUM_RATE[category]
            premium = max(100, int(sum_assured * rng.uniform(lo, hi)))

        rows.append(
            PolicyRow(
                no=f"POL-{effective.year}-{i + 1:05d}",
                customer_id=rng.randrange(1, EXPECTED_ROWS["customers"] + 1),
                product_id=product_id,
                agent_id=rng.randrange(1, EXPECTED_ROWS["agents"] + 1),
                status=status,
                effective_date=effective,
                expiry_date=add_years(effective, term),
                sum_assured=sum_assured,
                annual_premium=premium,
            )
        )
    return rows


def gen_claims(rng: random.Random, policies: list[PolicyRow]) -> list[ClaimRow]:
    rows: list[ClaimRow] = []
    # Sample positional indices: index + 1 is the policy_id (fresh sequences).
    for i, policy_idx in enumerate(rng.sample(range(len(policies)), EXPECTED_ROWS["claims"])):
        policy = policies[policy_idx]
        # filed_date >= effective_date and inside the window (SPEC §4.2).
        filed = rand_date(rng, policy.effective_date, WINDOW_END)
        roll = rng.random()
        if roll < 0.40:
            status = "paid"
        elif roll < 0.70:
            status = "approved"
        elif roll < 0.85:
            status = "rejected"
        else:
            status = "pending"

        # 5%..60% of sum assured -> claimed_amount <= sum_assured guaranteed.
        claimed = policy.sum_assured * rng.randrange(5, 61) // 100
        approved = None
        closed = None
        if status in ("approved", "paid"):
            approved = claimed * rng.randrange(60, 101) // 100  # <= claimed
        if status == "rejected":
            closed = min(filed + timedelta(days=rng.randrange(10, 61)), AS_OF)
        elif status == "paid":
            closed = min(filed + timedelta(days=rng.randrange(30, 91)), AS_OF)

        rows.append(
            ClaimRow(
                no=f"CLM-{filed.year}-{i + 1:05d}",
                policy_id=policy_idx + 1,
                filed_date=filed,
                status=status,
                claimed_amount=claimed,
                approved_amount=approved,
                closed_date=closed,
            )
        )
    return rows


def gen_payments(rng: random.Random, policies: list[PolicyRow]) -> list[PaymentRow]:
    rows: list[PaymentRow] = []
    for policy_id, policy in enumerate(policies, start=1):
        for period in range(1, PAYMENTS_PER_POLICY + 1):
            due = add_years(policy.effective_date, period - 1)
            if due > AS_OF:
                paid_date, status = None, "pending"
            elif rng.random() < 0.92:
                paid_date = min(due + timedelta(days=rng.randrange(-10, 11)), AS_OF)
                if paid_date < policy.effective_date:
                    paid_date = policy.effective_date
                status = "paid"
            else:
                paid_date, status = None, "overdue"

            roll = rng.random()
            method = (
                "bank_transfer" if roll < 0.45
                else "alipay" if roll < 0.70
                else "wechat" if roll < 0.95
                else "cash"
            )
            rows.append(
                PaymentRow(
                    policy_id=policy_id,
                    period_no=period,
                    due_date=due,
                    paid_date=paid_date,
                    amount=policy.annual_premium,
                    method=method,
                    status=status,
                )
            )
    return rows


def rebuild_schema(conn: psycopg.Connection) -> None:
    """Recreate public from scratch: the SQL files stay the single source of truth."""
    conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
    conn.execute("CREATE SCHEMA public")


def apply_sql_file(conn: psycopg.Connection, path: Path) -> None:
    """Apply a whole .sql file in one execute (dollar-quoting handled by the server)."""
    conn.execute(path.read_text(encoding="utf-8"))


def truncate_all(conn: psycopg.Connection) -> None:
    conn.execute(
        "TRUNCATE payments, claims, policies, customers, agents, products "
        "RESTART IDENTITY CASCADE"
    )


def insert_all(
    conn: psycopg.Connection,
    products: list[ProductRow],
    agents: list[AgentRow],
    customers: list[CustomerRow],
    policies: list[PolicyRow],
    claims: list[ClaimRow],
    payments: list[PaymentRow],
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO products (product_code, product_name, category, term_years,"
            " launched_date, is_active) VALUES (%s, %s, %s, %s, %s, %s)",
            products,
        )
        cur.executemany(
            "INSERT INTO agents (agent_code, name, branch_city, hire_date)"
            " VALUES (%s, %s, %s, %s)",
            agents,
        )
        cur.executemany(
            "INSERT INTO customers (name, gender, birth_date, city, risk_level, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            customers,
        )
        cur.executemany(
            "INSERT INTO policies (policy_no, customer_id, product_id, agent_id, status,"
            " effective_date, expiry_date, sum_assured, annual_premium)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            policies,
        )
        cur.executemany(
            "INSERT INTO claims (claim_no, policy_id, filed_date, status, claimed_amount,"
            " approved_amount, closed_date) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            claims,
        )
        cur.executemany(
            "INSERT INTO payments (policy_id, period_no, due_date, paid_date, amount,"
            " method, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            payments,
        )


def verify_counts(conn: psycopg.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in EXPECTED_ROWS:
            cur.execute(f"SELECT count(*) FROM {table}")  # tables fixed above
            counts[table] = cur.fetchone()[0]
    return counts


def main() -> None:
    load_dotenv()
    admin_url = os.environ.get("ADMIN_DATABASE_URL", DEFAULT_ADMIN_URL)

    rng = random.Random(SEED)
    products = gen_products(rng)
    agents = gen_agents(rng)
    customers = gen_customers(rng)
    policies = gen_policies(rng, products)
    claims = gen_claims(rng, policies)
    payments = gen_payments(rng, policies)

    with psycopg.connect(admin_url) as conn:
        rebuild_schema(conn)
        apply_sql_file(conn, DB_DIR / "01_schema.sql")
        apply_sql_file(conn, DB_DIR / "02_roles.sql")
        truncate_all(conn)  # SPEC §4.2; a no-op safeguard on the fresh schema
        insert_all(conn, products, agents, customers, policies, claims, payments)
        counts = verify_counts(conn)
        if counts != EXPECTED_ROWS:
            raise SystemExit(f"row-count mismatch, transaction rolled back: {counts}")
        conn.commit()

    in_force = sum(p.status == "in_force" for p in policies) / len(policies)
    overdue = sum(p.status == "overdue" for p in payments) / len(payments)
    print("Seed complete (seed=42):")
    for table, n in counts.items():
        print(f"  {table:<10} {n:>5}")
    print(f"  policies in_force: {in_force:.1%} (target ~75%)")
    print(f"  payments overdue:  {overdue:.1%} (target ~5%)")


if __name__ == "__main__":
    main()

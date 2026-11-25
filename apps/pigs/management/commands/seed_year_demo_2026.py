"""Add two isolated, deterministic 365-day PIGS test companies.

This command never clears, edits or copies pre-existing business records.
The deliberately known test passwords are entered interactively at run time.
"""

from __future__ import annotations

import getpass
import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Max, Min, Sum
from django.utils import timezone

from apps.companies.constants import ROLE_MANAGER, ROLE_OWNER, ROLE_VIEWER
from apps.companies.models import Company, CompanyMembership
from apps.companies.scope import company_scope
from apps.companies.services import create_company, ensure_default_roles
from apps.pigs.models import (
    BatchProfitReport, DailySale, DailySaleReport, Expense, FoodItem,
    FoodSaleRecord, MeatStock, Pig, PigSaleRecord, Purchase, SlaughterBatch,
)


START = date(2025, 10, 2)
END = date(2026, 10, 1)
DAYS = 365
MARKER = "[BIZMANAGER_YEAR_DEMO_2026_V1]"
ZERO = Decimal("0.00")
User = get_user_model()
BUSINESS_MODELS = (
    Purchase, Pig, SlaughterBatch, MeatStock, DailySale, PigSaleRecord,
    FoodItem, FoodSaleRecord, BatchProfitReport, DailySaleReport, Expense,
)


@dataclass(frozen=True)
class Spec:
    suffix: str
    code: str
    name: str
    location: str
    seed: int
    price: Decimal
    meals: tuple[tuple[str, int], ...]


SPECS = (
    Spec("A", "test-year-2026-njombe-a", "TEST Njombe Pork A",
         "Njombe", 20261001, Decimal("13500.00"),
         (("Ugali A", 2500), ("Chips A", 3500), ("Supu A", 4000))),
    Spec("B", "test-year-2026-dodoma-b", "TEST Dodoma Pork B",
         "Dodoma", 20261119, Decimal("14500.00"),
         (("Pilau B", 4500), ("Ndizi B", 3200), ("Chai B", 1800))),
)


def _username(role, spec):
    return f"{role}{spec.suffix}"


def _email(username):
    return f"{username.lower()}@bizmanager-year-demo.invalid"


def _save(instance):
    instance.full_clean()
    instance.save()
    return instance


def _all(model, company):
    return model.all_objects.filter(company=company)


def _assert_no_collisions(admin_username):
    if Company.objects.filter(code__in=[s.code for s in SPECS]).exists():
        raise CommandError(
            "A demo company already exists but its fixture is incomplete. "
            "No changes made; inspect it before attempting a repair."
        )
    usernames = [_username(role, spec) for spec in SPECS
                 for role in ("Owner", "Manager", "Viewer")]
    for username in usernames:
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f"Username already exists: {username}. No changes made.")
        if User.objects.filter(email__iexact=_email(username)).exists():
            raise CommandError(f"Demo email already exists for {username}. No changes made.")
    admin = User.objects.filter(username__iexact=admin_username).first()
    if admin and (admin.username != admin_username or not admin.is_active
                  or not admin.is_platform_admin):
        raise CommandError(
            f"Existing {admin_username} is not an active Platform Admin. "
            "No account was modified."
        )
    return admin


def _complete(company, spec):
    if (company.name != spec.name or company.email != _email(spec.code)
            or company.address != MARKER + " " + spec.location
            or company.status != Company.Status.ACTIVE):
        return False
    memberships = list(CompanyMembership.objects.filter(
        company=company, status=CompanyMembership.Status.ACTIVE
    ).select_related("user", "role"))
    if {(m.user.username, m.role.code) for m in memberships} != {
        (_username("Owner", spec), ROLE_OWNER),
        (_username("Manager", spec), ROLE_MANAGER),
        (_username("Viewer", spec), ROLE_VIEWER),
    } or len(memberships) != 3:
        return False
    sales = _all(DailySale, company)
    span = sales.aggregate(n=Count("pk"), lo=Min("sale_date"), hi=Max("sale_date"))
    if span != {"n": DAYS, "lo": START, "hi": END}:
        return False
    if len(set(sales.values_list("sale_date", flat=True))) != DAYS:
        return False
    if _all(DailySaleReport, company).count() != DAYS:
        return False
    batches = _all(SlaughterBatch, company).count()
    if not batches or _all(MeatStock, company).filter(is_finished=True).count() != batches:
        return False
    if _all(BatchProfitReport, company).filter(total_profit__gt=0).count() != batches:
        return False
    if _all(PigSaleRecord, company).count() != DAYS:
        return False
    return True


def _create_user(username, password, first, last):
    user = User(
        username=username, email=_email(username), first_name=first,
        last_name=last, is_active=True, must_change_password=False,
        password_changed_at=timezone.now(),
    )
    user.set_password(password)
    return _save(user)


def _membership(company, user, role, admin):
    return _save(CompanyMembership(
        company=company, user=user, role=role,
        status=CompanyMembership.Status.ACTIVE,
        requested_language="sw", approved_language="sw",
        created_by=admin, approved_by=admin, approved_at=timezone.now(),
    ))


def _create_company(spec, admin, test_password):
    owner = _create_user(_username("Owner", spec), test_password,
                         "Test", f"Owner {spec.suffix}")
    company = create_company(
        actor=admin, name=spec.name, code=spec.code, owner=owner,
        allowed_languages=["sw", "en"], default_language="sw",
        step_up_verified=True, email=_email(spec.code),
        address=MARKER + " " + spec.location,
    )
    roles = ensure_default_roles(company)
    for kind, role in (("Manager", ROLE_MANAGER), ("Viewer", ROLE_VIEWER)):
        user = _create_user(_username(kind, spec), test_password,
                            "Test", f"{kind} {spec.suffix}")
        _membership(company, user, roles[role], admin)
    return company


def _seed_one(company, spec, progress=None):
    rng = random.Random(spec.seed)
    with company_scope(company):
        meals = [
            _save(FoodItem(company=company, name=name,
                           selling_price=Decimal(price), is_active=True))
            for name, price in spec.meals
        ]
        current = START
        batch_no = 0
        reported_month = None
        while current <= END:
            month = (current.year, current.month)
            if month != reported_month:
                if progress:
                    progress(f"{spec.suffix}: creating {current:%Y-%m} ...")
                reported_month = month
            batch_no += 1
            remaining = (END - current).days + 1
            length = min(2 + (rng.randrange(3) == 0), remaining)
            pig_count = 1 if length == 1 else 2
            pig_cost = (400000 + rng.randrange(0, 13) * 10000
                        + (25000 if spec.suffix == "B" else 0))
            purchase = _save(Purchase(
                company=company,
                supplier_name=f"{spec.location} Test Farmer {batch_no:03d}",
                supplier_location=spec.location, purchase_date=current,
                number_of_pigs=pig_count,
                total_cost=Decimal(pig_cost * pig_count),
                pigs_created=False, notes=f"{MARKER} Test purchase.",
            ))
            pigs = []
            for index in range(pig_count):
                pigs.append(_save(Pig(
                    company=company, purchase=purchase,
                    gender=Pig.MALE if index == 0 else Pig.FEMALE,
                    purchase_price=Decimal(pig_cost), status=Pig.AVAILABLE,
                    notes=f"{MARKER} Test pig.",
                )))
            purchase.pigs_created = True
            purchase.save(update_fields=["pigs_created", "updated_at"])
            batch = _save(SlaughterBatch(
                company=company, slaughter_date=current,
                notes=f"{MARKER} Test slaughter batch.",
            ))
            batch.add_pigs(pigs)
            stock = _save(MeatStock(company=company, slaughter_batch=batch))
            for offset in range(length):
                day = current + timedelta(days=offset)
                # Two pigs sold over two or three days; a final one-day batch
                # contains a single pig. Both sale patterns remain profitable.
                meat = Decimal(
                    (650000 if length == 2 else 460000) if length > 1 else 760000
                ) + Decimal(rng.randrange(0, 11) * 10000)
                meal = meals[(day.toordinal() + spec.seed) % len(meals)]
                quantity = 1 + rng.randrange(1, 7)
                food = meal.selling_price * quantity
                sale = _save(DailySale(
                    company=company, sale_date=day, slaughter_batch=batch,
                    total_money_received=meat + food,
                    meat_price_per_kg=spec.price,
                    notes=f"{MARKER} Test daily sale.",
                ))
                _save(FoodSaleRecord(
                    company=company, daily_sale=sale, food_item=meal,
                    quantity=quantity, unit_price=meal.selling_price,
                ))
                _save(PigSaleRecord(
                    company=company, daily_sale=sale, slaughter_batch=batch,
                    price_per_kg=spec.price, total_amount=meat,
                    notes=f"{MARKER} Test meat sale.",
                ))
                sale.refresh_totals()
                sale.create_report()
                if day.toordinal() % (4 if spec.suffix == "A" else 5) == 0:
                    _save(Expense(
                        company=company, expense_date=day,
                        title="Mkaa" if spec.suffix == "A" else "Usafiri",
                        amount=Decimal(15000 + rng.randrange(0, 6) * 5000),
                        description=f"{MARKER} Test operating expense.",
                    ))
            stock.is_finished = True
            stock.finished_date = current + timedelta(days=length - 1)
            stock.needs_confirmation = False
            stock.save(update_fields=["is_finished", "finished_date",
                                      "needs_confirmation", "updated_at"])
            batch.pigs.update(status=Pig.FINISHED)
            report = batch.create_profit_report()
            if report.total_profit <= ZERO:
                raise CommandError(f"Batch profit is not positive: {spec.code}/{batch_no}")
            current += timedelta(days=length)


def seed_year_demo(*, admin_password, test_password, admin_username="Eliud",
                   progress=None):
    """Seed atomically; reject unrelated/partial rows, never overwrite them."""
    if not admin_password or not test_password:
        raise CommandError("Both passwords are required.")
    with transaction.atomic():
        companies = [Company.objects.filter(code=spec.code).first() for spec in SPECS]
        if any(companies):
            if all(companies) and all(_complete(c, s) for c, s in zip(companies, SPECS)):
                admin = User.objects.filter(username=admin_username,
                    is_platform_admin=True, is_active=True).first()
                if not admin or not admin.check_password(admin_password):
                    raise CommandError("Demo admin password did not match.")
                if not all(User.objects.get(username=_username(role, spec))
                           .check_password(test_password) for spec in SPECS
                           for role in ("Owner", "Manager", "Viewer")):
                    raise CommandError("Demo account passwords did not match.")
                return False
            raise CommandError("Partial demo state found. No rows were changed.")
        admin = _assert_no_collisions(admin_username)
        if admin is None:
            admin = User(
                username=admin_username, email=_email(admin_username),
                first_name="Eliud", is_active=True, is_staff=True,
                is_platform_admin=True, is_superuser=False,
                # The temporary credential is known from the test brief. The
                # first admin login must rotate it before normal use.
                must_change_password=True, password_changed_at=timezone.now(),
            )
            if User.objects.filter(email__iexact=admin.email).exists():
                raise CommandError("Admin test email already exists. No changes made.")
            admin.set_password(admin_password)
            _save(admin)
        elif not admin.check_password(admin_password):
            raise CommandError(
                "Existing Platform Admin password differs. Refusing to reset it."
            )
        for spec in SPECS:
            company = _create_company(spec, admin, test_password)
            _seed_one(company, spec, progress=progress)
            if not _complete(company, spec):
                raise CommandError(f"Post-seed verification failed: {spec.code}")
        return True


class Command(BaseCommand):
    help = "Preview or add two isolated year-long test companies (2025-10-02 to 2026-10-01)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Create the fixture.")
        parser.add_argument("--admin-username", default="Eliud")

    def handle(self, *args, **options):
        admin_username = options["admin_username"]
        self.stdout.write(f"Test range: {START} through {END} ({DAYS} days/company)")
        self.stdout.write("Company A: TEST Njombe Pork A (OwnerA, ManagerA, ViewerA)")
        self.stdout.write("Company B: TEST Dodoma Pork B (OwnerB, ManagerB, ViewerB)")
        if not options["apply"]:
            if Company.objects.filter(code__in=[s.code for s in SPECS]).exists():
                self.stdout.write("Existing demo company detected; --apply will verify it.")
            else:
                _assert_no_collisions(admin_username)
                self.stdout.write("No naming collisions. Existing business data is untouched.")
            self.stdout.write("Preview only. Use --apply after a validated backup.")
            return
        confirmation = input("Type SEED TEST DATA to add these accounts and records: ").strip()
        if confirmation != "SEED TEST DATA":
            raise CommandError("Cancelled. No records were changed.")
        admin_password = getpass.getpass(f"Password for {admin_username}: ")
        test_password = getpass.getpass("Password for Owner/Manager/Viewer test accounts: ")
        created = seed_year_demo(admin_username=admin_username,
                                 admin_password=admin_password,
                                 test_password=test_password,
                                 progress=self.stdout.write)
        self.stdout.write(self.style.SUCCESS(
            "Fixture created and verified." if created else
            "Fixture already complete; no changes made."
        ))

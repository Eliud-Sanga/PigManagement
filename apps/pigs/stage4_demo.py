from __future__ import annotations

import getpass
import secrets
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.db import router, transaction
from django.db.models.query import QuerySet
from django.utils import timezone

from apps.companies.constants import (
    ROLE_MANAGER,
    ROLE_OWNER,
    ROLE_STAFF,
)
from apps.companies.models import (
    Company,
    CompanyMembership,
)
from apps.companies.scope import company_scope
from apps.companies.services import (
    create_company,
    ensure_default_roles,
    set_company_status,
)
from apps.pigs.models import (
    BatchProfitReport,
    DailySale,
    DailySaleReport,
    Expense,
    FoodItem,
    FoodSaleRecord,
    MeatPartSale,
    MeatStock,
    Pig,
    PigSaleRecord,
    Purchase,
    SlaughterBatch,
)


User = get_user_model()

DEMO_MARKER = "PIGS_STAGE4_DEMO_V1"
DEMO_DOMAIN = "stage4-demo.invalid"
DEFAULT_CREDENTIALS_FILE = (
    Path.home() / "PigManagement_STAGE4_DEMO_CREDENTIALS.txt"
)

DEMO_MODEL_COUNTS = {
    Purchase: 1,
    Pig: 4,
    SlaughterBatch: 2,
    MeatStock: 2,
    DailySale: 3,
    PigSaleRecord: 3,
    MeatPartSale: 1,
    FoodItem: 3,
    FoodSaleRecord: 3,
    BatchProfitReport: 1,
    DailySaleReport: 3,
    Expense: 3,
}


@dataclass(frozen=True)
class DemoCompanySpec:
    key: str
    code: str
    name: str
    location: str
    default_language: str
    phone: str


DEMO_COMPANIES = (
    DemoCompanySpec(
        key="njombe",
        code="demo-stage4-njombe",
        name="DEMO Stage 4 - Njombe Pork House",
        location="Njombe, Tanzania",
        default_language="sw",
        phone="+255700004001",
    ),
    DemoCompanySpec(
        key="dodoma",
        code="demo-stage4-dodoma",
        name="DEMO Stage 4 - Dodoma Pork House",
        location="Dodoma, Tanzania",
        default_language="en",
        phone="+255700004002",
    ),
)


def demo_email(spec: DemoCompanySpec) -> str:
    return f"{spec.key}@{DEMO_DOMAIN}"


def demo_address(spec: DemoCompanySpec) -> str:
    return f"[{DEMO_MARKER}] {spec.location}"


def username_for(spec: DemoCompanySpec, role: str) -> str:
    return f"demo4_{spec.key}_{role.lower()}"


def user_email_for(spec: DemoCompanySpec, role: str) -> str:
    return f"{spec.key}.{role.lower()}@{DEMO_DOMAIN}"


def _strong_demo_password() -> str:
    return "Demo!" + secrets.token_urlsafe(18) + "9aA"


def verify_platform_admin(admin_username: str | None = None):
    queryset = User.objects.filter(
        is_platform_admin=True,
        is_active=True,
    )

    if admin_username:
        admin = queryset.filter(
            username=admin_username.strip()
        ).first()
        if admin is None:
            raise CommandError(
                "Active Platform Administrator not found."
            )
    else:
        admins = list(queryset.order_by("pk")[:2])
        if len(admins) != 1:
            raise CommandError(
                "Specify --admin-username because there is not "
                "exactly one active Platform Administrator."
            )
        admin = admins[0]

    password = getpass.getpass(
        f"Current password for Platform Admin '{admin.username}': "
    )

    if not admin.check_password(password):
        raise CommandError(
            "Platform Administrator password confirmation failed."
        )

    return admin


def _is_exact_demo_company(
    company: Company,
    spec: DemoCompanySpec,
) -> bool:
    return (
        company.code == spec.code
        and company.name == spec.name
        and company.email == demo_email(spec)
        and company.address == demo_address(spec)
        and company.phone == spec.phone
    )


def _get_company(spec: DemoCompanySpec):
    return Company.objects.filter(code=spec.code).first()


def _business_queryset(model, company):
    manager = getattr(model, "all_objects", model._base_manager)
    return manager.filter(company=company)


def _hard_delete_company_rows(model, company):
    source = _business_queryset(model, company)
    ids = list(source.values_list("pk", flat=True))
    if not ids:
        return 0

    using = router.db_for_write(model)
    queryset = QuerySet(
        model=model,
        using=using,
    ).filter(pk__in=ids)

    deleted, _ = queryset.delete()
    return deleted


def _demo_users_for_company(spec: DemoCompanySpec):
    return User.objects.filter(
        username__in=[
            username_for(spec, "owner"),
            username_for(spec, "manager"),
            username_for(spec, "staff"),
        ]
    ).order_by("username")


def _user_collision_exists():
    usernames = []
    emails = []

    for spec in DEMO_COMPANIES:
        for role in ("owner", "manager", "staff"):
            usernames.append(username_for(spec, role))
            emails.append(user_email_for(spec, role))

    return (
        User.objects.filter(username__in=usernames).exists()
        or User.objects.filter(email__in=emails).exists()
    )


def _company_complete(company, spec):
    if not _is_exact_demo_company(company, spec):
        return False

    if company.status != Company.Status.ACTIVE:
        return False

    if company.owner_user_management_enabled:
        return False

    expected_users = {
        username_for(spec, "owner"): ROLE_OWNER,
        username_for(spec, "manager"): ROLE_MANAGER,
        username_for(spec, "staff"): ROLE_STAFF,
    }

    memberships = (
        CompanyMembership.objects
        .filter(
            company=company,
            status=CompanyMembership.Status.ACTIVE,
        )
        .select_related("user", "role")
    )

    actual = {
        membership.user.username: membership.role.code
        for membership in memberships
    }

    if actual != expected_users:
        return False

    for model, expected in DEMO_MODEL_COUNTS.items():
        if _business_queryset(model, company).count() != expected:
            return False

    with company_scope(company):
        finished = MeatStock.objects.filter(
            is_finished=True
        ).count()
        open_stock = MeatStock.objects.filter(
            is_finished=False
        ).count()

        if finished != 1 or open_stock != 1:
            return False

        if Pig.objects.filter(status=Pig.FINISHED).count() != 2:
            return False

        if Pig.objects.filter(status=Pig.SLAUGHTERED).count() != 1:
            return False

        if Pig.objects.filter(status=Pig.AVAILABLE).count() != 1:
            return False

    return True


def _archived_clean_shell(company, spec):
    if not _is_exact_demo_company(company, spec):
        return False

    if company.status != Company.Status.ARCHIVED:
        return False

    if CompanyMembership.objects.filter(company=company).exists():
        return False

    for model in DEMO_MODEL_COUNTS:
        if _business_queryset(model, company).exists():
            return False

    return True


def _save_clean(instance):
    instance.full_clean()
    instance.save()
    return instance


def _create_user(
    *,
    spec,
    role,
    first_name,
    last_name,
    phone,
):
    username = username_for(spec, role)
    email = user_email_for(spec, role)

    if User.objects.filter(
        username__iexact=username
    ).exists():
        raise CommandError(
            f"Demo username collision: {username}"
        )

    if User.objects.filter(
        email__iexact=email
    ).exists():
        raise CommandError(
            f"Demo email collision: {email}"
        )

    password = _strong_demo_password()

    user = User(
        username=username,
        email=email,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        preferred_language=spec.default_language,
        is_active=True,
        must_change_password=False,
        password_changed_at=timezone.now(),
    )
    user.set_password(password)
    user.full_clean()
    user.save()

    return user, password


def _active_membership(
    *,
    company,
    user,
    role,
    admin,
    language,
):
    membership = CompanyMembership(
        company=company,
        user=user,
        role=role,
        status=CompanyMembership.Status.ACTIVE,
        requested_language=language,
        approved_language=language,
        created_by=admin,
        approved_by=admin,
        approved_at=timezone.now(),
    )
    membership.full_clean()
    membership.save()
    return membership


def _prepare_company(
    *,
    spec,
    admin,
    owner,
):
    company = _get_company(spec)

    if company is None:
        company = create_company(
            actor=admin,
            name=spec.name,
            code=spec.code,
            owner=owner,
            allowed_languages=["sw", "en"],
            default_language=spec.default_language,
            step_up_verified=True,
            email=demo_email(spec),
            phone=spec.phone,
            address=demo_address(spec),
        )

        if company.owner_user_management_enabled:
            company.owner_user_management_enabled = False
            company.save(
                update_fields=[
                    "owner_user_management_enabled",
                    "updated_at",
                ]
            )

        return company

    if not _archived_clean_shell(company, spec):
        raise CommandError(
            f"Existing {spec.code} is not a clean archived "
            "Stage 4 demo shell."
        )

    set_company_status(
        actor=admin,
        company=company,
        status=Company.Status.ACTIVE,
        step_up_verified=True,
    )
    company.refresh_from_db()

    if company.owner_user_management_enabled:
        company.owner_user_management_enabled = False
        company.save(
            update_fields=[
                "owner_user_management_enabled",
                "updated_at",
            ]
        )

    roles = ensure_default_roles(company)
    _active_membership(
        company=company,
        user=owner,
        role=roles[ROLE_OWNER],
        admin=admin,
        language=spec.default_language,
    )

    return company


def _seed_business(company, spec):
    marker = f"[{DEMO_MARKER}]"

    with company_scope(company):
        purchase = _save_clean(
            Purchase(
                company=company,
                supplier_name=(
                    "Mkulima Demo Njombe"
                    if spec.key == "njombe"
                    else "Mkulima Demo Dodoma"
                ),
                supplier_phone="+255700004100",
                supplier_location=spec.location,
                purchase_date=date(2026, 9, 1),
                number_of_pigs=4,
                total_cost=Decimal("720000.00"),
                notes=f"{marker} Commercial demo purchase.",
            )
        )

        pigs = []
        genders = (
            Pig.MALE,
            Pig.FEMALE,
            Pig.MALE,
            Pig.FEMALE,
        )

        for index, gender in enumerate(genders, start=1):
            pigs.append(
                _save_clean(
                    Pig(
                        company=company,
                        tag_number=f"P{index:03d}",
                        gender=gender,
                        purchase_price=Decimal("180000.00"),
                        status=Pig.AVAILABLE,
                        notes=f"{marker} Demo pig {index}.",
                        purchase=purchase,
                    )
                )
            )

        purchase.pigs_created = True
        purchase.save(
            update_fields=[
                "pigs_created",
                "updated_at",
            ]
        )

        batch_finished = _save_clean(
            SlaughterBatch(
                company=company,
                batch_number="B001",
                slaughter_date=date(2026, 9, 3),
                notes=f"{marker} Finished demo batch.",
            )
        )
        batch_finished.add_pigs(pigs[:2])

        stock_finished = _save_clean(
            MeatStock(
                company=company,
                slaughter_batch=batch_finished,
                is_finished=False,
                needs_confirmation=False,
            )
        )

        batch_active = _save_clean(
            SlaughterBatch(
                company=company,
                batch_number="B002",
                slaughter_date=date(2026, 9, 10),
                notes=f"{marker} Active demo batch.",
            )
        )
        batch_active.add_pigs([pigs[2]])

        _save_clean(
            MeatStock(
                company=company,
                slaughter_batch=batch_active,
                is_finished=False,
                needs_confirmation=False,
            )
        )

        chips = _save_clean(
            FoodItem(
                company=company,
                name="Chipsi",
                selling_price=Decimal("3000.00"),
                is_active=True,
            )
        )
        ugali = _save_clean(
            FoodItem(
                company=company,
                name="Ugali",
                selling_price=Decimal("2500.00"),
                is_active=True,
            )
        )
        soda = _save_clean(
            FoodItem(
                company=company,
                name="Soda",
                selling_price=Decimal("1500.00"),
                is_active=True,
            )
        )

        sale_specs = (
            {
                "sale_date": date(2026, 9, 3),
                "batch": batch_finished,
                "received": Decimal("306000.00"),
                "price_per_kg": Decimal("12000.00"),
                "food": chips,
                "quantity": 2,
                "meat_total": Decimal("300000.00"),
                "parts": True,
            },
            {
                "sale_date": date(2026, 9, 4),
                "batch": batch_finished,
                "received": Decimal("287500.00"),
                "price_per_kg": Decimal("14000.00"),
                "food": ugali,
                "quantity": 3,
                "meat_total": Decimal("280000.00"),
                "parts": False,
            },
            {
                "sale_date": date(2026, 9, 10),
                "batch": batch_active,
                "received": Decimal("166000.00"),
                "price_per_kg": Decimal("10000.00"),
                "food": soda,
                "quantity": 4,
                "meat_total": Decimal("160000.00"),
                "parts": False,
            },
        )

        for sale_spec in sale_specs:
            daily_sale = _save_clean(
                DailySale(
                    company=company,
                    sale_date=sale_spec["sale_date"],
                    slaughter_batch=sale_spec["batch"],
                    total_money_received=sale_spec["received"],
                    meat_price_per_kg=sale_spec["price_per_kg"],
                    notes=f"{marker} Demo daily sale.",
                )
            )

            food_sale = FoodSaleRecord(
                company=company,
                quantity=sale_spec["quantity"],
                unit_price=sale_spec["food"].selling_price,
                daily_sale=daily_sale,
                food_item=sale_spec["food"],
            )
            food_sale.full_clean()
            food_sale.save()

            pig_sale = PigSaleRecord(
                company=company,
                price_per_kg=sale_spec["price_per_kg"],
                total_amount=sale_spec["meat_total"],
                notes=f"{marker} Demo meat sale.",
                daily_sale=daily_sale,
                slaughter_batch=sale_spec["batch"],
            )
            pig_sale.full_clean()
            pig_sale.save()

            if sale_spec["parts"]:
                _save_clean(
                    MeatPartSale(
                        company=company,
                        ribs=2,
                        thighs=2,
                        head_sold=True,
                        internal_organs_sold=True,
                        pig_sale_record=pig_sale,
                    )
                )

            daily_sale.refresh_totals()
            daily_sale.create_report()

        _save_clean(
            Expense(
                company=company,
                expense_date=date(2026, 9, 2),
                title="Mkaa",
                amount=Decimal("25000.00"),
                description=f"{marker} Demo charcoal expense.",
            )
        )
        _save_clean(
            Expense(
                company=company,
                expense_date=date(2026, 9, 5),
                title="Usafiri",
                amount=Decimal("40000.00"),
                description=f"{marker} Demo transport expense.",
            )
        )
        _save_clean(
            Expense(
                company=company,
                expense_date=date(2026, 9, 11),
                title="Viungo",
                amount=Decimal("18000.00"),
                description=f"{marker} Demo seasoning expense.",
            )
        )

        stock_finished.mark_finished()

        report = BatchProfitReport.objects.get(
            slaughter_batch=batch_finished
        )
        expected_profit = Decimal("220000.00")

        if report.total_profit != expected_profit:
            raise CommandError(
                "Demo profit lifecycle mismatch: expected "
                f"{expected_profit}, got {report.total_profit}."
            )


def _write_credentials(path, credentials):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "============================================================",
        " PIG MANAGEMENT - STAGE 4 DEMO CREDENTIALS",
        "============================================================",
        "LOCAL DEMO ACCOUNTS ONLY",
        "Do not reuse these passwords for real accounts.",
        "",
    ]

    for entry in credentials:
        lines.extend(
            [
                f"Company: {entry['company_name']}",
                f"Company code: {entry['company_code']}",
                f"Role: {entry['role']}",
                f"Username: {entry['username']}",
                f"Password: {entry['password']}",
                "",
            ]
        )

    path.write_text("\n".join(lines))
    path.chmod(0o600)
    return path


@transaction.atomic
def seed_stage4_demo(
    *,
    admin,
    credentials_file=DEFAULT_CREDENTIALS_FILE,
):
    existing = [
        _get_company(spec)
        for spec in DEMO_COMPANIES
    ]

    if all(existing):
        if all(
            _company_complete(company, spec)
            for company, spec in zip(
                existing,
                DEMO_COMPANIES,
            )
        ):
            return {
                "created": False,
                "companies": existing,
                "credentials_file": (
                    Path(credentials_file).expanduser()
                    if credentials_file
                    else None
                ),
            }

        if not all(
            _archived_clean_shell(company, spec)
            for company, spec in zip(
                existing,
                DEMO_COMPANIES,
            )
        ):
            raise CommandError(
                "Partial or modified Stage 4 demo state detected. "
                "Run cleanup_stage4_demo first."
            )

    elif any(existing):
        raise CommandError(
            "Only one Stage 4 demo company exists. "
            "Run cleanup or repair the partial demo state first."
        )
    elif _user_collision_exists():
        raise CommandError(
            "Stage 4 demo usernames/emails already exist without "
            "the expected demo companies."
        )

    credentials = []
    companies = []

    for index, spec in enumerate(DEMO_COMPANIES, start=1):
        owner, owner_password = _create_user(
            spec=spec,
            role="owner",
            first_name="Demo",
            last_name=f"{spec.key.title()} Owner",
            phone=f"+2557000041{index:02d}",
        )

        company = _prepare_company(
            spec=spec,
            admin=admin,
            owner=owner,
        )

        roles = ensure_default_roles(company)

        manager, manager_password = _create_user(
            spec=spec,
            role="manager",
            first_name="Demo",
            last_name=f"{spec.key.title()} Manager",
            phone=f"+2557000042{index:02d}",
        )
        staff, staff_password = _create_user(
            spec=spec,
            role="staff",
            first_name="Demo",
            last_name=f"{spec.key.title()} Staff",
            phone=f"+2557000043{index:02d}",
        )

        _active_membership(
            company=company,
            user=manager,
            role=roles[ROLE_MANAGER],
            admin=admin,
            language=spec.default_language,
        )
        _active_membership(
            company=company,
            user=staff,
            role=roles[ROLE_STAFF],
            admin=admin,
            language=spec.default_language,
        )

        if company.owner_user_management_enabled:
            raise CommandError(
                "Demo provisioning must not enable Owner user management."
            )

        _seed_business(company, spec)

        companies.append(company)

        for role_name, user, password in (
            ("OWNER", owner, owner_password),
            ("MANAGER", manager, manager_password),
            ("STAFF", staff, staff_password),
        ):
            credentials.append(
                {
                    "company_name": company.name,
                    "company_code": company.code,
                    "role": role_name,
                    "username": user.username,
                    "password": password,
                }
            )

    if not all(
        _company_complete(company, spec)
        for company, spec in zip(
            companies,
            DEMO_COMPANIES,
        )
    ):
        raise CommandError(
            "Stage 4 demo verification failed inside transaction."
        )

    transaction.on_commit(
        lambda: _write_credentials(
            credentials_file,
            credentials,
        )
    )

    return {
        "created": True,
        "companies": companies,
        "credentials_file": Path(
            credentials_file
        ).expanduser(),
    }


@transaction.atomic
def cleanup_stage4_demo(
    *,
    admin,
    credentials_file=DEFAULT_CREDENTIALS_FILE,
):
    companies = []

    for spec in DEMO_COMPANIES:
        company = _get_company(spec)
        if company is None:
            continue

        if not _is_exact_demo_company(company, spec):
            raise CommandError(
                f"Refusing cleanup: {spec.code} does not match "
                "the exact Stage 4 demo marker."
            )

        companies.append((company, spec))

    # No demo companies means cleanup is safely idempotent.
    if not companies:
        return {
            "cleaned": False,
            "companies": [],
        }

    delete_order = (
        DailySaleReport,
        MeatPartSale,
        FoodSaleRecord,
        PigSaleRecord,
        BatchProfitReport,
        MeatStock,
        DailySale,
        SlaughterBatch,
        Pig,
        Purchase,
        FoodItem,
        Expense,
    )

    for company, spec in companies:
        for model in delete_order:
            _hard_delete_company_rows(
                model,
                company,
            )

        demo_user_ids = list(
            CompanyMembership.objects
            .filter(company=company)
            .values_list("user_id", flat=True)
        )

        CompanyMembership.objects.filter(
            company=company
        ).delete()

        company.owner_user_management_enabled = False
        company.save(
            update_fields=[
                "owner_user_management_enabled",
                "updated_at",
            ]
        )

        if company.status != Company.Status.ARCHIVED:
            set_company_status(
                actor=admin,
                company=company,
                status=Company.Status.ARCHIVED,
                step_up_verified=True,
            )

        for user_id in demo_user_ids:
            user = User.objects.filter(pk=user_id).first()
            if user is None:
                continue

            expected_names = {
                username_for(spec, "owner"),
                username_for(spec, "manager"),
                username_for(spec, "staff"),
            }

            if user.username not in expected_names:
                # A real/global identity may have been added to a demo company
                # during browser testing. Its demo membership is removed, but
                # the global user identity is deliberately preserved.
                continue

            if user.is_platform_admin:
                raise CommandError(
                    "Refusing cleanup of Platform Administrator identity."
                )

            if user.company_memberships.exists():
                continue

            user.delete()

        if not _archived_clean_shell(
            Company.objects.get(pk=company.pk),
            spec,
        ):
            raise CommandError(
                f"Cleanup verification failed for {spec.code}."
            )

    credential_path = Path(
        credentials_file
    ).expanduser()

    transaction.on_commit(
        lambda: (
            credential_path.unlink()
            if credential_path.exists()
            else None
        )
    )

    return {
        "cleaned": True,
        "companies": [company for company, _ in companies],
    }

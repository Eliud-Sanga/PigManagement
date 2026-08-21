from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone


# ============================================================
# CONSTANTS
# ============================================================

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def money(value):
    """
    Convert value to Decimal money format.
    """
    return Decimal(value).quantize(
        CENT,
        rounding=ROUND_HALF_UP
    )


# ============================================================
# BASE MODEL
# ============================================================

class BaseModel(models.Model):

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        abstract = True


# ============================================================
# PURCHASE
# ============================================================

class Purchase(BaseModel):

    supplier_name = models.CharField(
        max_length=100
    )

    supplier_phone = models.CharField(
        max_length=20,
        blank=True,
        null=True
    )

    supplier_location = models.CharField(
        max_length=100,
        blank=True,
        null=True
    )

    purchase_date = models.DateField()

    number_of_pigs = models.PositiveIntegerField(
        validators=[
            MinValueValidator(1)
        ]
    )

    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[
            MinValueValidator(CENT)
        ]
    )

    notes = models.TextField(
        blank=True,
        null=True
    )

    pigs_created = models.BooleanField(
        default=False
    )

    def clean(self):

        if self.number_of_pigs < 1:
            raise ValidationError(
                "Lazima kuwe na angalau nguruwe mmoja."
            )

        if self.total_cost <= ZERO:
            raise ValidationError(
                "Gharama ya ununuzi lazima iwe zaidi ya sifuri."
            )

    def get_default_pig_cost(self):

        if self.number_of_pigs <= 0:
            return ZERO

        return (
            self.total_cost /
            Decimal(self.number_of_pigs)
        ).quantize(
            CENT,
            rounding=ROUND_HALF_UP
        )

    def get_assigned_pig_cost(self):

        return sum(
            (
                pig.purchase_price
                for pig in self.pigs.all()
                if pig.purchase_price is not None
            ),
            ZERO
        )

    def get_remaining_pig_cost(self):

        return (
            self.total_cost -
            self.get_assigned_pig_cost()
        )

    def __str__(self):

        return (
            f"{self.supplier_name} - "
            f"{self.number_of_pigs} pigs - "
            f"{self.purchase_date}"
        )

    class Meta:

        ordering = [
            "-purchase_date",
            "-created_at"
        ]

        permissions = [
            (
                "manage_data_management",
                "Can manage data management"
            ),
        ]


# ============================================================
# PIG
# ============================================================

class Pig(BaseModel):

    MALE = "MALE"
    FEMALE = "FEMALE"

    GENDER_CHOICES = [
        (MALE, "Dume"),
        (FEMALE, "Jike"),
    ]

    AVAILABLE = "AVAILABLE"
    SLAUGHTERED = "SLAUGHTERED"
    FINISHED = "FINISHED"

    STATUS_CHOICES = [
        (AVAILABLE, "Hajachinjwa"),
        (SLAUGHTERED, "Amechinjwa"),
        (FINISHED, "Nyama imeisha"),
    ]

    tag_number = models.CharField(
        max_length=10,
        unique=True,
        blank=True
    )

    gender = models.CharField(
        max_length=10,
        choices=GENDER_CHOICES,
        blank=True,
        null=True
    )

    purchase_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[
            MinValueValidator(CENT)
        ]
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=AVAILABLE
    )

    notes = models.TextField(
        blank=True,
        null=True
    )

    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.CASCADE,
        related_name="pigs"
    )

    def clean(self):

        if self.purchase_price is not None:

            if self.purchase_price <= ZERO:
                raise ValidationError(
                    "Bei ya nguruwe lazima iwe zaidi ya sifuri."
                )

            other_pigs_total = (
                self.purchase.pigs
                .exclude(pk=self.pk)
                .aggregate(
                    total=Sum("purchase_price")
                )["total"]
                or ZERO
            )

            if (
                other_pigs_total +
                self.purchase_price
                > self.purchase.total_cost
            ):
                raise ValidationError(
                    "Jumla ya bei za nguruwe "
                    "haiwezi kuzidi gharama ya ununuzi."
                )

    def save(self, *args, **kwargs):

        if not self.tag_number:

            last_pig = (
                Pig.objects
                .filter(
                    tag_number__startswith="P"
                )
                .order_by("-id")
                .first()
            )

            if last_pig and last_pig.tag_number:

                try:
                    last_number = int(
                        last_pig.tag_number[1:]
                    )
                except (ValueError, TypeError):
                    last_number = 0

            else:
                last_number = 0

            self.tag_number = (
                f"P{last_number + 1:03d}"
            )

        super().save(*args, **kwargs)

    def get_purchase_cost(self):
        """
        Pata gharama ya nguruwe hii.

        ============================================================
        LOGIC:
        ============================================================

        1. Kama pig ina purchase_price yake mwenyewe,
           tumia hiyo.

        2. Kama haina, gawanya total_cost ya purchase
           kwa number_of_pigs.

        Hii ndiyo gharama halisi ya nguruwe hii.
        """

        if self.purchase_price is not None:
            return self.purchase_price

        return self.purchase.get_default_pig_cost()

    def __str__(self):

        return (
            self.tag_number
            or f"Pig #{self.pk}"
        )

    class Meta:

        ordering = [
            "tag_number"
        ]

# ============================================================
# SLAUGHTER BATCH
# ============================================================

class SlaughterBatch(BaseModel):

    batch_number = models.CharField(
        max_length=20,
        unique=True,
        blank=True
    )

    slaughter_date = models.DateField()

    notes = models.TextField(
        blank=True,
        null=True
    )

    pigs = models.ManyToManyField(
        Pig,
        related_name="slaughter_batches"
    )

    # ========================================================
    # SAVE
    # ========================================================

    def save(self, *args, **kwargs):
        """
        Tengeneza batch number automatically.

        Mfano:
            B001
            B002
            B003
        """

        if not self.batch_number:

            last_batch = (
                SlaughterBatch.objects
                .filter(
                    batch_number__startswith="B"
                )
                .order_by("-id")
                .first()
            )

            if last_batch and last_batch.batch_number:

                try:
                    last_number = int(
                        last_batch.batch_number[1:]
                    )

                except (
                    ValueError,
                    TypeError
                ):
                    last_number = 0

            else:
                last_number = 0

            self.batch_number = (
                f"B{last_number + 1:03d}"
            )

        super().save(*args, **kwargs)

    # ========================================================
    # TOTAL PURCHASE COST
    # ========================================================

    def calculate_total_purchase_cost(self):
        """
        ========================================================
        GHARAMA YA NGURUWE WOTE KWENYE BATCH
        ========================================================

        Kila pig:

            Pig.get_purchase_cost()

        Kama pig ana purchase_price yake:
            tumia hiyo.

        Kama hana:
            tumia default cost ya purchase.

        Return:
            Decimal
        """

        return sum(
            (
                pig.get_purchase_cost()
                for pig in self.pigs.select_related(
                    "purchase"
                ).all()
            ),
            ZERO
        )

    # ========================================================
    # TOTAL MEAT SALES
    # ========================================================

    def get_total_meat_sales(self):
        """
        ========================================================
        MAUZO YOTE YA NYAMA YA BATCH HII
        ========================================================

        SOURCE OF TRUTH:
            PigSaleRecord.total_amount

        MUHIMU:

        Hii inahesabu mauzo yote yaliyorekodiwa
        kwa batch hii tangu siku ya kwanza mpaka
        siku ambayo nyama imeisha.

        Mfano:

            Day 1 = 300,000
            Day 2 = 400,000
            Day 3 = 250,000

        Total:
            950,000

        HATUTUMII:

            DailySale.total_meat_sales

        kwa sababu DailySale ni ya siku nzima,
        wakati PigSaleRecord inajua batch halisi.
        """

        return (
            self.sales.aggregate(
                total=Sum(
                    "total_amount"
                )
            )["total"]
            or ZERO
        )

    # ========================================================
    # TOTAL MEAT WEIGHT
    # ========================================================

    def get_total_meat_weight(self):
        """
        ========================================================
        JUMLA YA KG ZILIZOUZWA KWA BATCH
        ========================================================

        Hii ni kwa reports tu.

        HAIAMUI:
            - stock imeisha
            - batch imeisha
            - profit

        Ni taarifa ya ziada tu.
        """

        return (
            self.sales.aggregate(
                total=Sum(
                    "meat_weight_sold"
                )
            )["total"]
            or ZERO
        )

    # ========================================================
    # TOTAL NUMBER OF SALES
    # ========================================================

    def get_total_sales_count(self):
        """
        Idadi ya PigSaleRecord zote
        za batch hii.
        """

        return self.sales.count()

    # ========================================================
    # IS FINISHED
    # ========================================================

    def is_finished(self):
        """
        Angalia kama stock ya batch imefungwa.
        """

        try:

            return self.stock.is_finished

        except MeatStock.DoesNotExist:

            return False

    # ========================================================
    # FINISH BATCH
    # ========================================================

    @transaction.atomic
    def finish_batch(self):
        """
        ========================================================
        FUNGA BATCH
        ========================================================

        Hii inatumika pale mama anapothibitisha:

            "NYAMA IMEISHA"

        Mfumo:

            1. Pata / create MeatStock
            2. Funga stock
            3. Weka finished_date
            4. Pig wote -> FINISHED
            5. Tengeneza final profit report

        MUHIMU:

        Hatutumii kilo kuamua kama nyama imeisha.

        Mama ndiye anayethibitisha.
        """

        stock, _ = (
            MeatStock.objects.get_or_create(
                slaughter_batch=self
            )
        )

        # ----------------------------------------------------
        # KAMA BATCH TAYARI IMEFUNGWA
        # ----------------------------------------------------

        if stock.is_finished:

            return self.create_profit_report()

        # ----------------------------------------------------
        # FUNGIA STOCK
        # ----------------------------------------------------

        stock.is_finished = True

        stock.needs_confirmation = False

        stock.finished_date = (
            timezone.localdate()
        )

        stock.save(
            update_fields=[
                "is_finished",
                "needs_confirmation",
                "finished_date",
                "updated_at",
            ]
        )

        # ----------------------------------------------------
        # BADILISHA STATUS ZA PIGS
        # ----------------------------------------------------

        self.pigs.update(
            status=Pig.FINISHED
        )

        # ----------------------------------------------------
        # FINAL PROFIT
        # ----------------------------------------------------

        return self.create_profit_report()

    # ========================================================
    # CREATE PROFIT REPORT
    # ========================================================

    def create_profit_report(self):
        """
        ========================================================
        FINAL PROFIT REPORT YA BATCH
        ========================================================

        FORMULA:

            TOTAL MEAT SALES
            -
            TOTAL PURCHASE COST

        ambapo:

            TOTAL MEAT SALES
            =
            Sum ya PigSaleRecord.total_amount

        na:

            TOTAL PURCHASE COST
            =
            Sum ya Pig.get_purchase_cost()

        Profit inafinalize ONLY baada ya
        batch kufungwa.
        """

        # ----------------------------------------------------
        # PATA STOCK
        # ----------------------------------------------------

        try:

            stock = self.stock

        except MeatStock.DoesNotExist:

            raise ValueError(
                "Batch hii haina MeatStock."
            )

        # ----------------------------------------------------
        # HAKIKISHA IMEFUNGWA
        # ----------------------------------------------------

        if not stock.is_finished:

            raise ValueError(
                "Profit haiwezi kufungwa kabla "
                "nyama ya batch haijaisha."
            )

        # ----------------------------------------------------
        # PURCHASE COST
        # ----------------------------------------------------

        total_purchase_cost = (
            self.calculate_total_purchase_cost()
        )

        # ----------------------------------------------------
        # MEAT SALES
        # ----------------------------------------------------

        total_meat_sales = (
            self.get_total_meat_sales()
        )

        # ----------------------------------------------------
        # NORMALIZE MONEY
        # ----------------------------------------------------

        total_purchase_cost = (
            money(total_purchase_cost)
        )

        total_meat_sales = (
            money(total_meat_sales)
        )

        # ----------------------------------------------------
        # PROFIT
        # ----------------------------------------------------

        total_profit = (
            total_meat_sales
            - total_purchase_cost
        )

        total_profit = money(
            total_profit
        )

        # ----------------------------------------------------
        # FINALIZED DATE
        # ----------------------------------------------------

        finalized_date = (
            stock.finished_date
            or timezone.localdate()
        )

        # ----------------------------------------------------
        # CREATE / UPDATE REPORT
        # ----------------------------------------------------

        report, _ = (
            BatchProfitReport.objects
            .update_or_create(
                slaughter_batch=self,
                defaults={
                    "total_pigs": self.pigs.count(),

                    "total_purchase_cost": (
                        total_purchase_cost
                    ),

                    "total_meat_sales": (
                        total_meat_sales
                    ),

                    "total_profit": (
                        total_profit
                    ),

                    "finalized_date": (
                        finalized_date
                    ),
                }
            )
        )

        return report

    # ========================================================
    # ADD PIGS
    # ========================================================

    def add_pigs(self, pigs):
        """
        ========================================================
        ONGEZA NGURUWE KWENYE BATCH
        ========================================================

        Ni nguruwe wenye status AVAILABLE tu
        wanaoweza kuingia kwenye batch.

        Baada ya kuongezwa:

            AVAILABLE -> SLAUGHTERED
        """

        for pig in pigs:

            if pig.status != Pig.AVAILABLE:
                continue

            self.pigs.add(pig)

            pig.status = Pig.SLAUGHTERED

            pig.save(
                update_fields=[
                    "status",
                    "updated_at",
                ]
            )

    # ========================================================
    # STRING
    # ========================================================

    def __str__(self):

        return self.batch_number

    # ========================================================
    # META
    # ========================================================

    class Meta:

        ordering = [
            "-slaughter_date",
            "-created_at",
        ]


# ============================================================
# MEAT STOCK / BATCH STATUS
# ============================================================

class MeatStock(BaseModel):

    slaughter_batch = models.OneToOneField(
        SlaughterBatch,
        on_delete=models.CASCADE,
        related_name="stock"
    )

    is_finished = models.BooleanField(
        default=False
    )

    needs_confirmation = models.BooleanField(
        default=False
    )

    finished_date = models.DateField(
        blank=True,
        null=True
    )

    def mark_pending_confirmation(self):

        if self.is_finished:
            return

        self.needs_confirmation = True

        self.save(
            update_fields=[
                "needs_confirmation",
                "updated_at",
            ]
        )

    @transaction.atomic
    def mark_finished(self):

        if self.is_finished:
            return

        self.is_finished = True
        self.needs_confirmation = False
        self.finished_date = timezone.localdate()

        self.save(
            update_fields=[
                "is_finished",
                "needs_confirmation",
                "finished_date",
                "updated_at",
            ]
        )

        self.slaughter_batch.pigs.update(
            status=Pig.FINISHED
        )

        self.slaughter_batch.create_profit_report()

    def __str__(self):

        status = (
            "Nyama imeisha"
            if self.is_finished
            else "Nyama bado ipo"
        )

        return (
            f"{self.slaughter_batch.batch_number} - "
            f"{status}"
        )

    class Meta:

        ordering = [
            "-created_at"
        ]


# ============================================================
# DAILY SALE
# ============================================================

class DailySale(BaseModel):

    sale_date = models.DateField(
        unique=True
    )

    slaughter_batch = models.ForeignKey(
        SlaughterBatch,
        on_delete=models.PROTECT,
        related_name="daily_sales",
        blank=True,
        null=True,
    )

    total_money_received = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        validators=[
            MinValueValidator(ZERO)
        ]
    )

    total_food_sales = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        validators=[
            MinValueValidator(ZERO)
        ]
    )

    total_meat_sales = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        validators=[
            MinValueValidator(ZERO)
        ]
    )

    # Bei ya kilo moja ya nyama
    meat_price_per_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=ZERO,
        validators=[
            MinValueValidator(ZERO)
        ]
    )

    # Kilo za nyama zilizouzwa
    total_meat_weight_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=ZERO,
        validators=[
            MinValueValidator(ZERO)
        ]
    )

    notes = models.TextField(
        blank=True,
        null=True
    )

    # ========================================================
    # CALCULATE MEAT WEIGHT
    # ========================================================

    def calculate_meat_weight(self):
        """
        Calculate kilograms of meat sold.

        Formula:
            Meat Sales ÷ Price per KG = Meat Weight
        """

        if (
            self.meat_price_per_kg
            and self.meat_price_per_kg > ZERO
            and self.total_meat_sales
            and self.total_meat_sales > ZERO
        ):
            meat_weight = (
                self.total_meat_sales /
                self.meat_price_per_kg
            )

            self.total_meat_weight_kg = (
                meat_weight.quantize(
                    Decimal("0.01")
                )
            )

        else:
            self.total_meat_weight_kg = ZERO

        return self.total_meat_weight_kg

    # ========================================================
    # CALCULATE TOTALS
    # ========================================================

    def calculate_totals(self):

        food_total = (
            self.food_records.aggregate(
                total=Sum("total_price")
            )["total"]
            or ZERO
        )

        meat_total = (
            self.total_money_received
            - food_total
        )

        if meat_total < ZERO:
            raise ValueError(
                "Jumla ya chakula haiwezi kuzidi "
                "jumla ya mauzo ya siku."
            )

        self.total_food_sales = money(food_total)

        self.total_meat_sales = money(
            meat_total
        )

        # ----------------------------------------------------
        # CALCULATE MEAT KG
        # ----------------------------------------------------

        self.calculate_meat_weight()

        return {
            "food": self.total_food_sales,
            "meat": self.total_meat_sales,
            "meat_weight": self.total_meat_weight_kg,
            "total": self.total_money_received,
        }

    # ========================================================
    # REFRESH TOTALS
    # ========================================================

    def refresh_totals(self):

        self.calculate_totals()

        self.save(
            update_fields=[
                "total_food_sales",
                "total_meat_sales",
                "total_meat_weight_kg",
                "meat_price_per_kg",
                "updated_at",
            ]
        )

    # ========================================================
    # CREATE / UPDATE DAILY REPORT
    # ========================================================

    def create_report(self):

        totals = self.calculate_totals()

        report, _ = (
            DailySaleReport.objects.update_or_create(
                daily_sale=self,
                defaults={
                    "total_pig_income": (
                        totals["meat"]
                    ),

                    "total_food_income": (
                        totals["food"]
                    ),

                    "total_meat_weight_kg": (
                        totals["meat_weight"]
                    ),

                    "total_income": (
                        totals["total"]
                    ),

                    "notes": self.notes,
                }
            )
        )

        return report

    # ========================================================
    # STRING
    # ========================================================

    def __str__(self):
        return str(self.sale_date)

    # ========================================================
    # META
    # ========================================================

    class Meta:

        ordering = [
            "-sale_date",
            "-created_at"
        ]


# ============================================================
# PIG / MEAT SALE RECORD
# ============================================================

class PigSaleRecord(BaseModel):

    meat_weight_sold = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True
    )

    price_per_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[
            MinValueValidator(CENT)
        ]
    )

    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        validators=[
            MinValueValidator(ZERO)
        ]
    )

    notes = models.TextField(
        blank=True,
        null=True
    )

    daily_sale = models.ForeignKey(
        DailySale,
        on_delete=models.CASCADE,
        related_name="pig_records"
    )

    slaughter_batch = models.ForeignKey(
        SlaughterBatch,
        on_delete=models.CASCADE,
        related_name="sales"
    )

    def calculate_weight(self):

        if (
            self.total_amount is None
            or self.price_per_kg is None
            or self.price_per_kg <= ZERO
        ):
            return ZERO

        return (
            self.total_amount /
            self.price_per_kg
        ).quantize(
            CENT,
            rounding=ROUND_HALF_UP
        )

    def save(self, *args, **kwargs):

        self.meat_weight_sold = (
            self.calculate_weight()
        )

        super().save(*args, **kwargs)

    def clean(self):

        if self.total_amount < ZERO:

            raise ValidationError(
                "Kiasi cha mauzo hakiwezi kuwa hasi."
            )

        if self.price_per_kg <= ZERO:

            raise ValidationError(
                "Bei kwa kilo lazima iwe zaidi ya sifuri."
            )

        if self.daily_sale_id and self.slaughter_batch_id:

            batch_stock = getattr(
                self.slaughter_batch,
                "stock",
                None
            )

            if (
                batch_stock
                and batch_stock.is_finished
                and not self.pk
            ):
                raise ValidationError(
                    "Huwezi kuongeza mauzo kwenye "
                    "batch ambayo nyama imekwisha."
                )

    def __str__(self):

        return (
            f"{self.slaughter_batch.batch_number} - "
            f"{self.total_amount}"
        )

    class Meta:

        ordering = [
            "-created_at"
        ]


# ============================================================
# MEAT PART SALE
# ============================================================

class MeatPartSale(BaseModel):

    ribs = models.PositiveIntegerField(
        default=0
    )

    thighs = models.PositiveIntegerField(
        default=0
    )

    head_sold = models.BooleanField(
        default=False
    )

    internal_organs_sold = models.BooleanField(
        default=False
    )

    pig_sale_record = models.OneToOneField(
        PigSaleRecord,
        on_delete=models.CASCADE,
        related_name="meat_parts"
    )

    def __str__(self):

        return (
            f"Parts - "
            f"{self.pig_sale_record}"
        )


# ============================================================
# FOOD ITEM
# ============================================================

class FoodItem(BaseModel):

    name = models.CharField(
        max_length=100,
        unique=True
    )

    selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[
            MinValueValidator(CENT)
        ]
    )

    is_active = models.BooleanField(
        default=True
    )

    def __str__(self):

        return self.name

    class Meta:

        ordering = [
            "name"
        ]


# ============================================================
# FOOD SALE
# ============================================================

class FoodSaleRecord(BaseModel):

    quantity = models.PositiveIntegerField(
        validators=[
            MinValueValidator(1)
        ]
    )

    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[
            MinValueValidator(CENT)
        ]
    )

    total_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO
    )

    daily_sale = models.ForeignKey(
        DailySale,
        on_delete=models.CASCADE,
        related_name="food_records"
    )

    food_item = models.ForeignKey(
        FoodItem,
        on_delete=models.PROTECT
    )

    def calculate_total(self):

        self.total_price = money(
            self.quantity *
            self.unit_price
        )

        return self.total_price

    def save(self, *args, **kwargs):

        self.calculate_total()

        super().save(*args, **kwargs)

    def __str__(self):

        return (
            f"{self.food_item.name} "
            f"x {self.quantity}"
        )

    class Meta:

        ordering = [
            "-created_at"
        ]


# ============================================================
# BATCH PROFIT REPORT
# ============================================================

class BatchProfitReport(BaseModel):
    """
    ============================================================
    RIPOTI YA FAIDA KWA KILA BATCH
    ============================================================

    PROFIT CALCULATION:
        = TOTAL MEAT SALES - TOTAL PURCHASE COST

    TOTAL MEAT SALES:
        = Jumla ya DailySale.total_meat_sales
          kwa batch hii (tangu siku ya kwanza hadi mwisho)
          ✅ Sio PigSaleRecord!

    TOTAL PURCHASE COST:
        = Sum of all Pig.get_purchase_cost()
          for all pigs in this batch
    """

    slaughter_batch = models.OneToOneField(
        SlaughterBatch,
        on_delete=models.CASCADE,
        related_name="profit_report"
    )

    total_pigs = models.PositiveIntegerField(
        default=0
    )

    total_purchase_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        validators=[
            MinValueValidator(ZERO)
        ]
    )

    total_meat_sales = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        validators=[
            MinValueValidator(ZERO)
        ]
    )

    total_profit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO
    )

    finalized_date = models.DateField()

    def calculate_profit(self):
        """
        ============================================================
        HESABU FAIDA
        ============================================================

        FORMULA:
            = total_meat_sales - total_purchase_cost
        """

        self.total_profit = (
            self.total_meat_sales -
            self.total_purchase_cost
        )

        return self.total_profit

    def save(self, *args, **kwargs):

        self.calculate_profit()

        super().save(*args, **kwargs)

    def get_profit_display(self):

        if self.total_profit >= ZERO:
            return (
                f"+TSh {self.total_profit:,.0f}"
            )

        return (
            f"-TSh {abs(self.total_profit):,.0f}"
        )

    def is_profitable(self):

        return self.total_profit >= ZERO

    def __str__(self):

        return (
            f"Profit - "
            f"{self.slaughter_batch.batch_number}"
        )

    class Meta:

        ordering = [
            "-finalized_date",
            "-created_at"
        ]


# ============================================================
# DAILY SALE REPORT
# ============================================================

class DailySaleReport(BaseModel):

    total_pig_income = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO
    )

    total_food_income = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO
    )

    total_meat_weight_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=ZERO
    )

    total_income = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO
    )

    notes = models.TextField(
        blank=True,
        null=True
    )

    daily_sale = models.OneToOneField(
        DailySale,
        on_delete=models.CASCADE,
        related_name="report"
    )

    def calculate_total(self):

        self.total_income = (
            self.total_pig_income +
            self.total_food_income
        )

        return self.total_income

    def save(self, *args, **kwargs):

        self.calculate_total()

        super().save(*args, **kwargs)

    def __str__(self):

        return (
            f"Report - "
            f"{self.daily_sale.sale_date}"
        )

    class Meta:

        ordering = [
            "-created_at"
        ]


# ============================================================
# EXPENSE / MATUMIZI
# ============================================================

class Expense(BaseModel):

    expense_date = models.DateField()

    title = models.CharField(
        max_length=200
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[
            MinValueValidator(ZERO)
        ]
    )

    description = models.TextField(
        blank=True,
        null=True
    )

    def __str__(self):

        return self.title

    class Meta:

        ordering = [
            "-expense_date",
            "-created_at"
        ]
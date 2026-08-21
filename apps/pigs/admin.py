from django.contrib import admin

from .models import (
    Purchase,
    Pig,
    SlaughterBatch,
    MeatStock,
    DailySale,
    PigSaleRecord,
    MeatPartSale,
    FoodItem,
    FoodSaleRecord,
    DailySaleReport,
)


# ============================================================
# PURCHASE
# ============================================================

@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "supplier_name",
        "purchase_date",
        "number_of_pigs",
        "total_cost",
        "pigs_created",
    )

    search_fields = (
        "supplier_name",
        "supplier_phone",
        "supplier_location",
    )

    list_filter = (
        "purchase_date",
        "pigs_created",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    ordering = (
        "-purchase_date",
        "-id",
    )


# ============================================================
# PIG
# ============================================================

@admin.register(Pig)
class PigAdmin(admin.ModelAdmin):

    list_display = (
        "tag_number",
        "gender",
        "purchase_price",
        "status",
        "purchase",
    )

    search_fields = (
        "tag_number",
        "purchase__supplier_name",
    )

    list_filter = (
        "status",
        "gender",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    ordering = (
        "tag_number",
    )


# ============================================================
# SLAUGHTER BATCH
# ============================================================

@admin.register(SlaughterBatch)
class SlaughterBatchAdmin(admin.ModelAdmin):

    list_display = (
        "batch_number",
        "slaughter_date",
        "number_of_pigs",
    )

    search_fields = (
        "batch_number",
        "pigs__tag_number",
    )

    list_filter = (
        "slaughter_date",
    )

    filter_horizontal = (
        "pigs",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    ordering = (
        "-slaughter_date",
        "-id",
    )

    def number_of_pigs(self, obj):
        return obj.pigs.count()

    number_of_pigs.short_description = "Number of pigs"

    def formfield_for_manytomany(
        self,
        db_field,
        request,
        **kwargs
    ):

        if db_field.name == "pigs":

            kwargs["queryset"] = Pig.objects.filter(
                status=Pig.AVAILABLE
            )

        return super().formfield_for_manytomany(
            db_field,
            request,
            **kwargs
        )


# ============================================================
# MEAT STOCK
# ============================================================

@admin.register(MeatStock)
class MeatStockAdmin(admin.ModelAdmin):

    list_display = (
        "slaughter_batch",
        "status_display",
        "needs_confirmation",
        "finished_date",
    )

    list_filter = (
        "is_finished",
        "needs_confirmation",
        "finished_date",
    )

    search_fields = (
        "slaughter_batch__batch_number",
    )

    readonly_fields = (
        "finished_date",
        "created_at",
        "updated_at",
    )

    def status_display(self, obj):

        if obj.is_finished:
            return "Nyama imeisha"

        return "Nyama bado ipo"

    status_display.short_description = "Hali ya Nyama"

# ============================================================
# DAILY SALE
# ============================================================

@admin.register(DailySale)
class DailySaleAdmin(admin.ModelAdmin):

    list_display = (
        "sale_date",
        "total_money_received",
        "total_food_sales",
        "total_meat_sales",
    )

    list_filter = (
        "sale_date",
    )

    search_fields = (
        "sale_date",
    )


# ============================================================
# PIG / MEAT SALE RECORD
# ============================================================

@admin.register(PigSaleRecord)
class PigSaleRecordAdmin(admin.ModelAdmin):

    list_display = (
        "daily_sale",
        "slaughter_batch",
        "meat_weight_sold",
        "price_per_kg",
        "total_amount",
    )

    search_fields = (
        "slaughter_batch__batch_number",
    )

    list_filter = (
        "daily_sale",
        "slaughter_batch",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "total_amount",
    )

    ordering = (
        "-created_at",
    )


# ============================================================
# MEAT PART SALE
# ============================================================

@admin.register(MeatPartSale)
class MeatPartSaleAdmin(admin.ModelAdmin):

    list_display = (
        "pig_sale_record",
        "ribs",
        "thighs",
        "head_sold",
        "internal_organs_sold",
    )

    list_filter = (
        "head_sold",
        "internal_organs_sold",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )


# ============================================================
# FOOD ITEM
# ============================================================

@admin.register(FoodItem)
class FoodItemAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "selling_price",
        "is_active",
    )

    search_fields = (
        "name",
    )

    list_filter = (
        "is_active",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    ordering = (
        "name",
    )


# ============================================================
# FOOD SALE RECORD
# ============================================================

@admin.register(FoodSaleRecord)
class FoodSaleRecordAdmin(admin.ModelAdmin):

    list_display = (
        "daily_sale",
        "food_item",
        "quantity",
        "unit_price",
        "total_price",
    )

    search_fields = (
        "food_item__name",
    )

    list_filter = (
        "daily_sale",
        "food_item",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    ordering = (
        "-created_at",
    )


# ============================================================
# DAILY SALE REPORT
# ============================================================

@admin.register(DailySaleReport)
class DailySaleReportAdmin(admin.ModelAdmin):

    list_display = (
        "daily_sale",
        "total_pig_income",
        "total_food_income",
        "total_income",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "total_income",
    )

    ordering = (
        "-created_at",
    )
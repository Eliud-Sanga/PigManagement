"""
URL configuration for the Pig Management application.

All user-facing URLs use simple Kiswahili paths
to make the system easier for the business user.
"""

from django.urls import path
from . import views
from .views import (
    dashboard,

    # Purchase
    purchase_create,
    purchase_list,

    # Pigs
    pig_list,
    pig_detail,
    pig_bulk_edit,
    pig_delete,
    purchase_delete,

    # Slaughter
    slaughter_create,
    slaughter_list,

    # Meat stock
    meat_list,

    # Daily sales
    daily_sale_create,
    daily_sale_list,
    daily_sale_detail,
    meat_finished_confirm,

    # Food
    food_item_create,
    food_sale_create,
    food_sale_edit,
    pig_sale_create,
    meat_sale_edit,

    # Reports
    daily_report,
    monthly_report,

    # Batch Profit Reports
    batch_profit_report_list,
    batch_profit_report_detail,

    # Meat Status Popup
    meat_status,
)

# ============================================================
# APP NAMESPACE
# ============================================================
app_name = 'pigs'

urlpatterns = [

    # =========================================================
    # DASHBOARD
    # =========================================================

    path(
        "",
        dashboard,
        name="dashboard",
    ),


    # =========================================================
    # UNUNUZI WA NGURUWE
    # =========================================================

    path(
        "ununuzi/",
        purchase_create,
        name="purchase_create",
    ),

    path(
        "ununuzi/historia/",
        purchase_list,
        name="purchase_list",
    ),


    # =========================================================
    # NGURUWE
    # =========================================================

    path(
        "nguruwe/",
        pig_list,
        name="pig_list",
    ),

    path(
        "nguruwe/<int:pig_id>/",
        pig_detail,
        name="pig_detail",
    ),

    path(
        "nguruwe/bulk-edit/<int:purchase_id>/",
        pig_bulk_edit,
        name="pig_bulk_edit",
    ),


    # =========================================================
    # MACHINJIO
    # =========================================================

    path(
        "machinjio/sajili/",
        slaughter_create,
        name="slaughter_create",
    ),

    path(
        "machinjio/",
        slaughter_list,
        name="slaughter_list",
    ),


    # =========================================================
    # STOCK YA NYAMA
    # =========================================================

    path(
        "nyama/",
        meat_list,
        name="meat_list",
    ),


    # =========================================================
    # MAUZO YA KILA SIKU
    # =========================================================

    path(
        "mauzo/siku/",
        daily_sale_create,
        name="daily_sale_create",
    ),

    path(
        "mauzo/",
        daily_sale_list,
        name="daily_sale_list",
    ),

    path(
        "mauzo/siku/<int:sale_id>/",
        daily_sale_detail,
        name="daily_sale_detail",
    ),

    path(
        "mauzo/siku/<int:sale_id>/nyama-imeisha/",
        meat_finished_confirm,
        name="meat_finished_confirm",
    ),


    # =========================================================
    # CHAKULA
    # =========================================================

    path(
        "chakula/bidhaa/sajili/",
        food_item_create,
        name="food_item_create",
    ),

    path(
        "chakula/mauzo/<int:sale_id>/",
        food_sale_create,
        name="food_sale_create",
    ),

    path(
        "chakula/mauzo/<int:record_id>/edit/",
        food_sale_edit,
        name="food_sale_edit",
    ),

    # =========================================================
    # MAUZO YA NYAMA
    # =========================================================

    path(
        "nyama/mauzo/<int:sale_id>/",
        pig_sale_create,
        name="pig_sale_create",
    ),

    path(
        "nyama/mauzo/<int:record_id>/edit/",
        meat_sale_edit,
        name="meat_sale_edit",
    ),

    # =========================================================
    # RIPOTI
    # =========================================================

    path(
        "mauzo/report/<int:sale_id>/",
        daily_report,
        name="daily_report",
    ),

    path(
        "mauzo/mwezi/",
        monthly_report,
        name="monthly_report",
    ),


    # =========================================================
    # RIPOTI ZA FAIDA ZA BATCH
    # =========================================================

    path(
        "ripoti/faida/batch/",
        batch_profit_report_list,
        name="batch_profit_report_list",
    ),

    path(
        "ripoti/faida/batch/<int:batch_id>/",
        batch_profit_report_detail,
        name="batch_profit_report_detail",
    ),


    # =========================================================
    # DELETE
    # =========================================================

    path(
        "nguruwe/delete/<int:pig_id>/",
        pig_delete,
        name="pig_delete",
    ),

    path(
        "purchase/delete/<int:purchase_id>/",
        purchase_delete,
        name="purchase_delete",
    ),


    # =========================================================
    # MEAT STATUS POPUP
    # =========================================================
    path(
        "mauzo/nyama-status/",
        meat_status,
        name="meat_status",
    ),

    # ============================================================
    # EXPENSES / MATUMIZI
    # ============================================================

    path(
        "matumizi/",
        views.expense_list,
        name="expense_list"
    ),

    path(
        "matumizi/ongeza/",
        views.expense_create,
        name="expense_create"
    ),

    path(
        "matumizi/<int:expense_id>/",
        views.expense_detail,
        name="expense_detail"
    ),

    path(
        "matumizi/<int:expense_id>/futa/",
        views.expense_delete,
        name="expense_delete"
    ),
]
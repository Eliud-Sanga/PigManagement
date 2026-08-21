from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import (
    DailySale,
    PigSaleRecord,
    MeatPartSale,
    FoodSaleRecord,
    FoodItem,
    Purchase,
    Pig,
    SlaughterBatch,
)


# ============================================================
# DAILY SALE FORM
# ============================================================

class DailySaleForm(forms.ModelForm):

    class Meta:

        model = DailySale

        fields = [
            "sale_date",
            "total_money_received",
            "notes",
        ]

        labels = {
            "sale_date": "Tarehe ya Mauzo",
            "total_money_received": "Jumla ya Mauzo (TSh)",
            "notes": "Maelezo",
        }

        widgets = {

            "sale_date": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": "form-control",
                }
            ),

            "total_money_received": forms.NumberInput(
                attrs={
                    "step": "1",
                    "min": "1",
                    "placeholder": "Mfano: 700000",
                    "class": "form-control",
                }
            ),

            "notes": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Maelezo ya ziada...",
                    "class": "form-control",
                }
            ),
        }

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        if not self.instance.pk:

            self.fields[
                "sale_date"
            ].initial = timezone.localdate()

            self.fields[
                "total_money_received"
            ].initial = None

    def clean_sale_date(self):

        sale_date = self.cleaned_data.get(
            "sale_date"
        )

        if not sale_date:
            return sale_date

        queryset = DailySale.objects.filter(
            sale_date=sale_date
        )

        if self.instance.pk:

            queryset = queryset.exclude(
                pk=self.instance.pk
            )

        if queryset.exists():

            raise forms.ValidationError(
                "Tayari kuna mauzo ya tarehe hii."
            )

        return sale_date

    def clean_total_money_received(self):

        amount = self.cleaned_data.get(
            "total_money_received"
        )

        if amount is None:
            return amount

        if amount <= Decimal("0.00"):

            raise forms.ValidationError(
                "Jumla ya mauzo lazima iwe zaidi ya sifuri."
            )

        return amount


# ============================================================
# PIG / MEAT SALE FORM
# ============================================================

class PigSaleRecordForm(forms.ModelForm):

    class Meta:

        model = PigSaleRecord

        fields = [
            "slaughter_batch",
            "price_per_kg",
            "total_amount",
            "notes",
        ]

        labels = {
            "slaughter_batch": "Batch ya Machinjio",
            "price_per_kg": "Bei kwa Kilo (TSh)",
            "total_amount": "Jumla ya Mauzo (TSh)",
            "notes": "Maelezo",
        }

        widgets = {

            "slaughter_batch": forms.Select(
                attrs={
                    "class": "form-control",
                }
            ),

            "price_per_kg": forms.NumberInput(
                attrs={
                    "step": "0.01",
                    "min": "0.01",
                    "placeholder": "Mfano: 12000",
                    "class": "form-control",
                }
            ),

            "total_amount": forms.NumberInput(
                attrs={
                    "step": "0.01",
                    "min": "0.01",
                    "placeholder": "Mfano: 60000",
                    "class": "form-control",
                }
            ),

            "notes": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Maelezo ya ziada...",
                    "class": "form-control",
                }
            ),
        }

    def __init__(self, *args, **kwargs):

        super().__init__(
            *args,
            **kwargs
        )

        # ====================================================
        # BATCHES ZINAZORUHUSIWA KUUZA
        # ====================================================

        self.fields[
            "slaughter_batch"
        ].queryset = (
            SlaughterBatch.objects
            .filter(
                stock__is_finished=False
            )
            .select_related(
                "stock"
            )
            .prefetch_related(
                "pigs"
            )
            .order_by(
                "-slaughter_date",
                "-id"
            )
        )

        self.fields[
            "slaughter_batch"
        ].label_from_instance = (
            lambda batch:
            f"{batch.batch_number} - "
            f"{batch.pigs.count()} nguruwe - "
            f"Nyama bado ipo"
        )

    # ========================================================
    # PRICE VALIDATION
    # ========================================================

    def clean_price_per_kg(self):

        price = self.cleaned_data.get(
            "price_per_kg"
        )

        if price is None:
            return price

        if price <= Decimal("0.00"):

            raise forms.ValidationError(
                "Bei kwa kilo lazima iwe zaidi ya sifuri."
            )

        return price

    # ========================================================
    # TOTAL AMOUNT VALIDATION
    # ========================================================

    def clean_total_amount(self):

        total = self.cleaned_data.get(
            "total_amount"
        )

        if total is None:
            return total

        if total <= Decimal("0.00"):

            raise forms.ValidationError(
                "Jumla ya mauzo lazima iwe zaidi ya sifuri."
            )

        return total

    # ========================================================
    # FORM-LEVEL VALIDATION
    # ========================================================

    def clean(self):

        cleaned_data = super().clean()

        batch = cleaned_data.get(
            "slaughter_batch"
        )

        price_per_kg = cleaned_data.get(
            "price_per_kg"
        )

        total_amount = cleaned_data.get(
            "total_amount"
        )

        if not batch:
            return cleaned_data

        # ----------------------------------------------------
        # BATCH MUST STILL BE OPEN
        # ----------------------------------------------------

        try:

            stock = batch.stock

        except Exception:

            self.add_error(
                "slaughter_batch",
                "Batch hii haina taarifa ya MeatStock."
            )

            return cleaned_data

        if stock.is_finished:

            self.add_error(
                "slaughter_batch",
                "Nyama ya batch hii imekwisha tayari."
            )

            return cleaned_data

        # ----------------------------------------------------
        # PRICE + MONEY MUST EXIST TOGETHER
        # ----------------------------------------------------

        if (
            price_per_kg is not None
            and total_amount is not None
        ):

            calculated_weight = (
                total_amount /
                price_per_kg
            )

            # Tunahifadhi kilo kama taarifa
            # lakini hatulinganishi na remaining stock.
            cleaned_data[
                "_calculated_weight"
            ] = calculated_weight

        return cleaned_data


# ============================================================
# MEAT PART SALE FORM
# ============================================================

class MeatPartSaleForm(forms.ModelForm):

    class Meta:

        model = MeatPartSale

        fields = [
            "ribs",
            "thighs",
            "head_sold",
            "internal_organs_sold",
        ]

        labels = {
            "ribs": "Idadi ya Mbavu",
            "thighs": "Idadi ya Vipaja",
            "head_sold": "Kichwa Kimeuzwa?",
            "internal_organs_sold": "Vya Ndani Vimeuzwa?",
        }

        widgets = {

            "ribs": forms.NumberInput(
                attrs={
                    "step": "1",
                    "min": "0",
                    "placeholder": "Mfano: 5",
                }
            ),

            "thighs": forms.NumberInput(
                attrs={
                    "step": "1",
                    "min": "0",
                    "placeholder": "Mfano: 4",
                }
            ),

            "head_sold": forms.CheckboxInput(),

            "internal_organs_sold": forms.CheckboxInput(),
        }


# ============================================================
# FOOD ITEM FORM
# ============================================================

class FoodItemForm(forms.ModelForm):

    class Meta:

        model = FoodItem

        fields = [
            "name",
            "selling_price",
            "is_active",
        ]

        labels = {
            "name": "Aina ya Chakula",
            "selling_price": "Bei ya Sahani Moja",
            "is_active": "Chakula Kinapatikana",
        }

        widgets = {

            "name": forms.TextInput(
                attrs={
                    "placeholder": "Mfano: Ugali",
                }
            ),

            "selling_price": forms.NumberInput(
                attrs={
                    "step": "0.01",
                    "min": "0.01",
                    "placeholder": "Mfano: 2000",
                }
            ),
        }

    def clean_name(self):

        name = self.cleaned_data.get(
            "name"
        )

        if not name:

            raise forms.ValidationError(
                "Weka jina la chakula."
            )

        name = name.strip()

        if not name:

            raise forms.ValidationError(
                "Weka jina la chakula."
            )

        return name


# ============================================================
# FOOD SALE FORM
# ============================================================

class FoodSaleRecordForm(forms.ModelForm):

    food_name = forms.CharField(
        label="Aina ya Chakula",
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Mfano: Ugali",
            }
        ),
    )

    unit_price_input = forms.DecimalField(
        label="Bei ya Sahani Moja",
        min_value=Decimal("0.01"),
        decimal_places=2,
        max_digits=10,
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "min": "0.01",
                "placeholder": "Mfano: 2000",
            }
        ),
    )

    class Meta:

        model = FoodSaleRecord

        fields = [
            "food_name",
            "unit_price_input",
            "quantity",
        ]

        labels = {
            "quantity": "Idadi ya Sahani Zilizouzwa",
        }

        widgets = {

            "quantity": forms.NumberInput(
                attrs={
                    "min": "1",
                    "step": "1",
                    "placeholder": "Mfano: 20",
                }
            ),
        }

    def clean_food_name(self):

        food_name = self.cleaned_data.get(
            "food_name"
        )

        if not food_name:

            raise forms.ValidationError(
                "Weka aina ya chakula."
            )

        food_name = food_name.strip()

        if not food_name:

            raise forms.ValidationError(
                "Weka aina ya chakula."
            )

        return food_name

    def clean_unit_price_input(self):

        price = self.cleaned_data.get(
            "unit_price_input"
        )

        if price is None:
            return price

        if price <= Decimal("0.00"):

            raise forms.ValidationError(
                "Bei ya sahani lazima iwe zaidi ya sifuri."
            )

        return price

    def clean_quantity(self):

        quantity = self.cleaned_data.get(
            "quantity"
        )

        if quantity is None:
            return quantity

        if quantity < 1:

            raise forms.ValidationError(
                "Idadi ya sahani lazima iwe angalau moja."
            )

        return quantity


# ============================================================
# PURCHASE FORM
# ============================================================

class PurchaseForm(forms.ModelForm):

    class Meta:

        model = Purchase

        fields = [
            "supplier_name",
            "supplier_phone",
            "supplier_location",
            "purchase_date",
            "number_of_pigs",
            "total_cost",
            "notes",
        ]

        labels = {
            "supplier_name": "Jina la Muuzaji",
            "supplier_phone": "Namba ya Simu",
            "supplier_location": "Mahali",
            "purchase_date": "Tarehe ya Ununuzi",
            "number_of_pigs": "Idadi ya Nguruwe",
            "total_cost": "Gharama Jumla",
            "notes": "Maelezo",
        }

        widgets = {

            "supplier_name": forms.TextInput(
                attrs={
                    "placeholder": "Mfano: Juma",
                }
            ),

            "supplier_phone": forms.TextInput(
                attrs={
                    "placeholder": "Mfano: 07XXXXXXXX",
                }
            ),

            "supplier_location": forms.TextInput(
                attrs={
                    "placeholder": "Mfano: Makete",
                }
            ),

            "purchase_date": forms.DateInput(
                attrs={
                    "type": "date",
                }
            ),

            "number_of_pigs": forms.NumberInput(
                attrs={
                    "min": "1",
                    "step": "1",
                    "placeholder": "Mfano: 5",
                }
            ),

            "total_cost": forms.NumberInput(
                attrs={
                    "min": "0.01",
                    "step": "0.01",
                    "placeholder": "Mfano: 1250000",
                }
            ),

            "notes": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Maelezo ya ziada...",
                }
            ),
        }

    def __init__(self, *args, **kwargs):

        super().__init__(
            *args,
            **kwargs
        )

        if not self.instance.pk:

            self.fields[
                "purchase_date"
            ].initial = timezone.localdate()

    def clean_supplier_name(self):

        name = self.cleaned_data.get(
            "supplier_name"
        )

        if not name:

            raise forms.ValidationError(
                "Weka jina la muuzaji."
            )

        name = name.strip()

        if not name:

            raise forms.ValidationError(
                "Weka jina la muuzaji."
            )

        return name

    def clean_number_of_pigs(self):

        number = self.cleaned_data.get(
            "number_of_pigs"
        )

        if number is None:
            return number

        if number < 1:

            raise forms.ValidationError(
                "Idadi ya nguruwe lazima iwe angalau mmoja."
            )

        return number

    def clean_total_cost(self):

        cost = self.cleaned_data.get(
            "total_cost"
        )

        if cost is None:
            return cost

        if cost <= Decimal("0.00"):

            raise forms.ValidationError(
                "Gharama lazima iwe zaidi ya sifuri."
            )

        return cost


# ============================================================
# PIG FORM
# ============================================================

class PigForm(forms.ModelForm):

    class Meta:

        model = Pig

        fields = [
            "gender",
            "purchase_price",
            "notes",
        ]

        labels = {
            "gender": "Jinsia",
            "purchase_price": "Bei ya Nguruwe",
            "notes": "Maelezo",
        }

        widgets = {

            "gender": forms.Select(),

            "purchase_price": forms.NumberInput(
                attrs={
                    "step": "0.01",
                    "min": "0.01",
                    "placeholder": "Mfano: 250000",
                }
            ),

            "notes": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Maelezo ya ziada...",
                }
            ),
        }

    def clean_purchase_price(self):

        price = self.cleaned_data.get(
            "purchase_price"
        )

        if price is not None:

            if price <= Decimal("0.00"):

                raise forms.ValidationError(
                    "Bei ya nguruwe lazima iwe zaidi ya sifuri."
                )

        return price


# ============================================================
# SLAUGHTER BATCH FORM
# ============================================================

class SlaughterBatchForm(forms.ModelForm):

    class Meta:

        model = SlaughterBatch

        fields = [
            "slaughter_date",
            "pigs",
            "notes",
        ]

        labels = {
            "slaughter_date": "Tarehe ya Kuchinja",
            "pigs": "Nguruwe wa Kuchinja",
            "notes": "Maelezo",
        }

        widgets = {

            "slaughter_date": forms.DateInput(
                attrs={
                    "type": "date",
                }
            ),

            "pigs": forms.SelectMultiple(
                attrs={
                    "size": "8",
                }
            ),

            "notes": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Maelezo ya ziada...",
                }
            ),
        }

    def __init__(self, *args, **kwargs):

        super().__init__(
            *args,
            **kwargs
        )

        if not self.instance.pk:

            self.fields[
                "slaughter_date"
            ].initial = timezone.localdate()

        self.fields[
            "pigs"
        ].queryset = (
            Pig.objects
            .filter(
                status=Pig.AVAILABLE
            )
            .select_related(
                "purchase"
            )
            .order_by(
                "tag_number",
                "id",
            )
        )

        self.fields[
            "pigs"
        ].label_from_instance = (
            lambda pig:
            f"{pig.tag_number or f'Pig #{pig.pk}'} - "
            f"{pig.get_gender_display() or 'Jinsia haijawekwa'}"
        )

    def clean_pigs(self):

        pigs = self.cleaned_data.get(
            "pigs"
        )

        if not pigs:

            raise forms.ValidationError(
                "Chagua angalau nguruwe mmoja."
            )

        unavailable_pigs = [
            pig
            for pig in pigs
            if pig.status != Pig.AVAILABLE
        ]

        if unavailable_pigs:

            raise forms.ValidationError(
                "Baadhi ya nguruwe waliochaguliwa "
                "hawapatikani tena kwa ajili ya kuchinjwa."
            )

        return pigs
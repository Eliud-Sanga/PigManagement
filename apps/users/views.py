from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User, Group, Permission
from django.db import transaction
from django.forms import modelform_factory
from django.core import serializers
from django.utils import timezone
from pathlib import Path
import json
import re


# ============================================================
# DEFAULT VIEWER PERMISSIONS
# ============================================================

DEFAULT_VIEWER_PERMISSION_CODENAMES = [

    "view_purchase",

    "view_pig",

    "view_slaughterbatch",

    "view_meatstock",

    "view_dailysale",

    "view_pigsalerecord",

    "view_meatpartsale",

    "view_fooditem",

    "view_foodsalerecord",

    "view_batchprofitreport",

    "view_dailysalereport",

    "view_expense",

]


def assign_default_viewer_permissions(user):
    """
    Mpe user mpya permissions za msingi za Viewer.

    Viewer:
        - anaweza KUONA taarifa
        - hawezi KUONGEZA
        - hawezi KUHARIRI
        - hawezi KUFUTA

    Permissions zinawekwa moja kwa moja kwa user,
    sio kwenye Group.
    """

    permissions = Permission.objects.filter(
        content_type__app_label="pigs",
        codename__in=DEFAULT_VIEWER_PERMISSION_CODENAMES,
    )

    user.user_permissions.add(
        *permissions
    )


# ============================================================
# SECURITY HELPERS
# ============================================================

def _super_admin_verification_key(user_id):
    """
    Session key inayotumika kuthibitisha kuwa Super Admin
    ameweka password yake kabla ya kusimamia account nyeti
    ya Super Admin mwingine.
    """

    return f"super_admin_verified_{user_id}"


def _target_super_admin_requires_verification(
    request,
    target_user
):
    """
    Angalia kama target user ni Super Admin na
    verification ya Super Admin imekamilika.

    Return:
        True  -> tayari verified
        False -> bado hajaverify
    """

    if not target_user.is_superuser:
        return True

    return request.session.get(
        _super_admin_verification_key(
            target_user.id
        ),
        False
    )


def _require_super_admin_target_verification(
    request,
    target_user
):
    """
    Kama target ni Super Admin na hajaverify,
    mpeleke kwenye verification page.

    Return:
        None -> verification tayari ipo
        HttpResponse -> redirect kwenda verification
    """

    if not target_user.is_superuser:
        return None

    if _target_super_admin_requires_verification(
        request,
        target_user
    ):
        return None

    messages.warning(
        request,
        "🔐 Kwa sababu huyu ni Super Admin, "
        "thibitisha password ya Super Admin kwanza."
    )

    return redirect(
        "users:verify_permission_access",
        user_id=target_user.id
    )


def _clear_super_admin_verification(
    request,
    target_user
):
    """
    Ondoa verification baada ya action nyeti kukamilika.
    Hii inamaanisha action nyingine nyeti itahitaji
    verification tena.
    """

    request.session.pop(
        _super_admin_verification_key(
            target_user.id
        ),
        None
    )

    request.session.modified = True


# ============================================================
# LOGIN VIEW
# ============================================================

def login_view(request):
    """Ukurasa wa kuingia kwenye mfumo."""

    if request.user.is_authenticated:
        return redirect('pigs:dashboard')

    if request.method == 'POST':

        username = request.POST.get(
            'username',
            ''
        ).strip()

        password = request.POST.get(
            'password',
            ''
        )

        remember_me = request.POST.get(
            'remember_me',
            False
        )

        # ----------------------------------------------------
        # CHECK 1: USERNAME IPO?
        # ----------------------------------------------------

        try:

            target_user = User.objects.get(
                username=username
            )

        except User.DoesNotExist:

            messages.error(
                request,
                "❌ Akaunti hii haipatikani tena kwenye mfumo."
            )

            return render(
                request,
                'users/login.html'
            )

        # ----------------------------------------------------
        # CHECK 2: ACCOUNT IMESIMAMISHWA?
        # ----------------------------------------------------

        if not target_user.is_active:

            messages.error(
                request,
                "❌ Akaunti yako imesimamishwa. "
                "Huwezi kuingia kwenye mfumo. "
                "Wasiliana na Super Admin."
            )

            return render(
                request,
                'users/login.html'
            )

        # ----------------------------------------------------
        # CHECK 3: AUTHENTICATE PASSWORD
        # ----------------------------------------------------

        user = authenticate(
            request,
            username=username,
            password=password
        )

        if user is not None:

            login(
                request,
                user
            )

            if remember_me:

                request.session.set_expiry(
                    1209600
                )

            else:

                request.session.set_expiry(
                    0
                )

            messages.success(
                request,
                f"✅ Karibu {user.username}!"
            )

            return redirect(
                'pigs:dashboard'
            )

        else:

            messages.error(
                request,
                "❌ Nenosiri si sahihi."
            )

    return render(
        request,
        'users/login.html'
    )


# ============================================================
# LOGOUT VIEW
# ============================================================

def logout_view(request):
    """Kutoka kwenye mfumo."""

    if request.method == "POST":

        logout(request)

        messages.info(
            request,
            "✅ Umefanikiwa kutoka kwenye mfumo."
        )

        response = redirect(
            "users:login"
        )

        # Zuia browser kutumia cached pages baada ya logout
        response["Cache-Control"] = (
            "no-cache, no-store, must-revalidate"
        )

        response["Pragma"] = "no-cache"

        response["Expires"] = "0"

        return response

    return redirect(
        "pigs:dashboard"
    )


# ============================================================
# USER MANAGEMENT - SUPER ADMIN ONLY
# ============================================================

@login_required
def user_list(request):
    """Orodha ya watumiaji wote - Super Admin only"""

    if not request.user.is_superuser:

        messages.error(
            request,
            "❌ Huna ruhusa ya kuona ukurasa huu."
        )

        return redirect(
            'pigs:dashboard'
        )

    users = (
        User.objects
        .all()
        .order_by('id')
    )

    user_data = []

    for user in users:

        groups = [
            g.name
            for g in user.groups.all()
        ]

        user_data.append({

            'user': user,

            'groups': groups,

            'is_superuser': user.is_superuser,

            'is_active': user.is_active,

        })

    return render(
        request,
        'users/user_list.html',
        {
            'user_data': user_data
        }
    )


@login_required
def user_detail(request, user_id):
    """
    Maelezo ya mtumiaji mmoja.

    Super Admin pekee ndiye anayeweza kufungua
    User Management.

    Kama target ni Super Admin mwingine,
    password verification inahitajika kwanza.
    """

    # ========================================================
    # REQUESTER MUST BE SUPER ADMIN
    # ========================================================

    if not request.user.is_superuser:

        messages.error(
            request,
            "❌ Huna ruhusa ya kuona ukurasa huu."
        )

        return redirect(
            'pigs:dashboard'
        )

    user = get_object_or_404(
        User,
        id=user_id
    )

    # ========================================================
    # SUPER ADMIN TARGET PROTECTION
    # ========================================================

    verification_redirect = (
        _require_super_admin_target_verification(
            request,
            user
        )
    )

    if verification_redirect:

        return verification_redirect

    groups = (
        user.groups.all()
    )

    permissions = (
        user.user_permissions.all()
    )

    all_permissions = (
        Permission.objects
        .all()
        .order_by(
            'content_type__app_label',
            'codename'
        )
    )

    perm_categories = {}

    for perm in all_permissions:

        category = (

            perm.codename.split('_')[0]

            if '_' in perm.codename

            else 'other'

        )

        if category not in perm_categories:

            perm_categories[category] = []

        perm_categories[category].append({

            'permission': perm,

            'has_perm': user.has_perm(
                f"{perm.content_type.app_label}."
                f"{perm.codename}"
            ),

        })

    return render(
        request,
        'users/user_detail.html',
        {
            'user': user,

            'groups': groups,

            'permissions': permissions,

            'perm_categories': perm_categories,

            'all_permissions': all_permissions,

            'all_groups': Group.objects.all(),

        }
    )


@login_required
def user_create(request):
    """
    Kuongeza mtumiaji mpya - Super Admin only.

    DEFAULT ROLE:
        Viewer

    DEFAULT VIEWER PERMISSIONS:
        Viewer anapopewa kama role ya mwanzo,
        anaongezewa direct permissions za kuona
        taarifa za mfumo.

    ADMIN / MANAGER:
        Hawapati permissions automatically.
        Super Admin ndiye atakayeongeza permissions
        kupitia User Permissions Management.
    """

    if not request.user.is_superuser:

        messages.error(
            request,
            "❌ Huna ruhusa ya kuona ukurasa huu."
        )

        return redirect(
            'pigs:dashboard'
        )

    # ========================================================
    # POST
    # ========================================================

    if request.method == 'POST':

        username = request.POST.get(
            'username',
            ''
        ).strip()

        email = request.POST.get(
            'email',
            ''
        ).strip()

        password = request.POST.get(
            'password',
            ''
        )

        password_confirm = request.POST.get(
            'password_confirm',
            ''
        )

        # ----------------------------------------------------
        # ROLE
        #
        # Template inatuma GROUP ID.
        #
        # Empty value = Viewer by default.
        # ----------------------------------------------------

        group_id = request.POST.get(
            'group',
            ''
        ).strip()

        # ====================================================
        # USERNAME
        # ====================================================

        if not username:

            messages.error(
                request,
                "❌ Jina la mtumiaji linahitajika."
            )

            return render(
                request,
                'users/user_create.html',
                {
                    'groups': Group.objects.all()
                }
            )

        # ====================================================
        # DUPLICATE USERNAME
        # ====================================================

        if User.objects.filter(
            username=username
        ).exists():

            messages.error(
                request,
                f"❌ Jina '{username}' tayari linatumika."
            )

            return render(
                request,
                'users/user_create.html',
                {
                    'groups': Group.objects.all()
                }
            )

        # ====================================================
        # PASSWORD CONFIRMATION
        # ====================================================

        if password != password_confirm:

            messages.error(
                request,
                "❌ Nenosiri na uthibitisho havilingani."
            )

            return render(
                request,
                'users/user_create.html',
                {
                    'groups': Group.objects.all()
                }
            )

        # ====================================================
        # PASSWORD LENGTH
        # ====================================================

        if len(password) < 6:

            messages.error(
                request,
                "❌ Nenosiri lazima liwe na "
                "herufi 6 au zaidi."
            )

            return render(
                request,
                'users/user_create.html',
                {
                    'groups': Group.objects.all()
                }
            )

        # ====================================================
        # RESOLVE ROLE
        # ====================================================

        group = None

        # ----------------------------------------------------
        # EMPTY ROLE = VIEWER
        # ----------------------------------------------------

        if not group_id:

            group = Group.objects.filter(
                name='Viewer'
            ).first()

        # ----------------------------------------------------
        # ROLE SELECTED
        # ----------------------------------------------------

        else:

            try:

                group = Group.objects.get(
                    id=group_id
                )

            except (
                Group.DoesNotExist,
                ValueError,
                TypeError,
            ):

                group = Group.objects.filter(
                    name='Viewer'
                ).first()

                messages.warning(
                    request,
                    "⚠️ Role iliyochaguliwa haikupatikana. "
                    "Mtumiaji amepewa Viewer kama default."
                )

        # ====================================================
        # CREATE USER
        # ====================================================

        with transaction.atomic():

            user = User.objects.create_user(

                username=username,

                email=email,

                password=password,

                is_active=True,

            )

            # =================================================
            # ASSIGN GROUP / ROLE
            # =================================================

            if group:

                user.groups.add(
                    group
                )

            # =================================================
            # DEFAULT VIEWER PERMISSIONS
            # =================================================

            if group and group.name == 'Viewer':

                assign_default_viewer_permissions(
                    user
                )

        # ====================================================
        # SUCCESS MESSAGE
        # ====================================================

        role_name = (
            group.name
            if group
            else 'bila role'
        )

        if role_name == 'Viewer':

            message_text = (
                f"✅ Mtumiaji '{username}' "
                f"ameundwa kikamilifu kama Viewer. "
                f"Permissions za kuona zimewekwa automatically."
            )

        else:

            message_text = (
                f"✅ Mtumiaji '{username}' "
                f"ameundwa kikamilifu kama "
                f"'{role_name}'. "
                f"Permissions bado zitasimamiwa na Super Admin."
            )

        messages.success(
            request,
            message_text
        )

        return redirect(
            'users:user_list'
        )

    # ========================================================
    # GET
    # ========================================================

    return render(
        request,
        'users/user_create.html',
        {
            'groups': Group.objects.all()
        }
    )


@login_required
def user_delete(request, user_id):
    """
    Kufuta mtumiaji - Super Admin only.

    MUHIMU:
        Super Admin HAWEZI kufutwa kabisa.
    """

    if not request.user.is_superuser:

        messages.error(
            request,
            "❌ Huna ruhusa ya kufanya kitendo hiki."
        )

        return redirect(
            'pigs:dashboard'
        )

    user = get_object_or_404(
        User,
        id=user_id
    )

    # ========================================================
    # ABSOLUTE SUPER ADMIN PROTECTION
    # ========================================================

    if user.is_superuser:

        messages.error(
            request,
            "🔒 Super Admin hawezi kufutwa."
        )

        return redirect(
            'users:user_list'
        )

    # ========================================================
    # PREVENT SELF DELETE
    # ========================================================

    if user == request.user:

        messages.error(
            request,
            "❌ Huwezi kujifuta mwenyewe!"
        )

        return redirect(
            'users:user_list'
        )

    if request.method == 'POST':

        username = user.username

        user.delete()

        messages.success(
            request,
            f"✅ Mtumiaji '{username}' "
            f"amefutwa kikamilifu!"
        )

        return redirect(
            'users:user_list'
        )

    return render(
        request,
        'users/user_confirm_delete.html',
        {
            'user': user
        }
    )


@login_required
def user_toggle_active(request, user_id):
    """
    Kuactivate au deactivate mtumiaji.

    Super Admin HAWEZI kusimamishwa.
    """

    if not request.user.is_superuser:

        messages.error(
            request,
            "❌ Huna ruhusa ya kufanya kitendo hiki."
        )

        return redirect(
            'pigs:dashboard'
        )

    user = get_object_or_404(
        User,
        id=user_id
    )

    # ========================================================
    # ABSOLUTE SUPER ADMIN PROTECTION
    # ========================================================

    if user.is_superuser:

        messages.error(
            request,
            "🔒 Super Admin hawezi kusimamishwa."
        )

        return redirect(
            'users:user_list'
        )

    # ========================================================
    # PREVENT SELF DEACTIVATION
    # ========================================================

    if user == request.user:

        messages.error(
            request,
            "❌ Huwezi kujideactivate mwenyewe!"
        )

        return redirect(
            'users:user_list'
        )

    # ========================================================
    # TOGGLE
    # ========================================================

    user.is_active = not user.is_active

    user.save(
        update_fields=[
            "is_active",
        ]
    )

    status = (

        "activated"

        if user.is_active

        else "deactivated"

    )

    messages.success(
        request,
        f"✅ Mtumiaji '{user.username}' "
        f"ame{status}!"
    )

    return redirect(
        'users:user_list'
    )


@login_required
def user_change_password(request, user_id):
    """
    Kubadilisha nenosiri la mtumiaji.

    Kwa target Super Admin:
        lazima Super Admin password verification
        iwe imekamilika kwanza.
    """

    if not request.user.is_superuser:

        messages.error(
            request,
            "❌ Huna ruhusa ya kuona ukurasa huu."
        )

        return redirect(
            'pigs:dashboard'
        )

    user = get_object_or_404(
        User,
        id=user_id
    )

    # ========================================================
    # SUPER ADMIN VERIFICATION
    # ========================================================

    verification_redirect = (
        _require_super_admin_target_verification(
            request,
            user
        )
    )

    if verification_redirect:

        return verification_redirect

    if request.method == 'POST':

        # ----------------------------------------------------
        # DIRECT POST PROTECTION
        # ----------------------------------------------------

        if user.is_superuser:

            if not _target_super_admin_requires_verification(
                request,
                user
            ):

                messages.error(
                    request,
                    "🔒 Password ya Super Admin "
                    "inathibitishwa kwanza."
                )

                return redirect(
                    'users:verify_permission_access',
                    user_id=user.id
                )

        new_password = request.POST.get(
            'new_password',
            ''
        )

        confirm_password = request.POST.get(
            'confirm_password',
            ''
        )

        if new_password != confirm_password:

            messages.error(
                request,
                "❌ Nenosiri na uthibitisho "
                "havilingani."
            )

            return redirect(
                'users:user_change_password',
                user_id=user_id
            )

        if not new_password or len(new_password) < 6:

            messages.error(
                request,
                "❌ Nenosiri lazima liwe na "
                "herufi 6 au zaidi."
            )

            return redirect(
                'users:user_change_password',
                user_id=user_id
            )

        user.set_password(
            new_password
        )

        user.save()

        if user.is_superuser:

            _clear_super_admin_verification(
                request,
                user
            )

        messages.success(
            request,
            f"✅ Nenosiri la '{user.username}' "
            f"limebadilishwa!"
        )

        return redirect(
            'users:user_detail',
            user_id=user_id
        )

    return render(
        request,
        'users/user_change_password.html',
        {
            'user': user
        }
    )


@login_required
def user_grant_permission(request, user_id):
    """Kumpa mtumiaji permission - Super Admin only"""

    if not request.user.is_superuser:

        messages.error(
            request,
            "❌ Huna ruhusa ya kuona ukurasa huu."
        )

        return redirect(
            'pigs:dashboard'
        )

    user = get_object_or_404(
        User,
        id=user_id
    )

    # ========================================================
    # SUPER ADMIN PERMISSIONS ARE LOCKED
    # ========================================================

    if user.is_superuser:

        messages.error(
            request,
            "🔒 Permissions za Super Admin "
            "zimefungwa na haziwezi kubadilishwa."
        )

        return redirect(
            'users:user_detail',
            user_id=user_id
        )

    if request.method == 'POST':

        perm_codename = request.POST.get(
            'permission'
        )

        if perm_codename:

            try:

                perm = Permission.objects.get(
                    codename=perm_codename
                )

                user.user_permissions.add(
                    perm
                )

                messages.success(
                    request,
                    f"✅ Permission "
                    f"'{perm_codename}' "
                    f"imeongezwa kwa "
                    f"{user.username}"
                )

            except Permission.DoesNotExist:

                messages.error(
                    request,
                    f"❌ Permission "
                    f"'{perm_codename}' haipo"
                )

    return redirect(
        'users:user_detail',
        user_id=user_id
    )


@login_required
def user_revoke_permission(
    request,
    user_id,
    perm_id
):
    """
    Kumwondoa mtumiaji permission.

    Super Admin permissions haziwezi kuondolewa.
    """

    if not request.user.is_superuser:

        messages.error(
            request,
            "❌ Huna ruhusa ya kuona ukurasa huu."
        )

        return redirect(
            'pigs:dashboard'
        )

    user = get_object_or_404(
        User,
        id=user_id
    )

    # ========================================================
    # ABSOLUTE SUPER ADMIN PROTECTION
    # ========================================================

    if user.is_superuser:

        messages.error(
            request,
            "🔒 Permissions za Super Admin "
            "haziwezi kuondolewa."
        )

        return redirect(
            'users:user_detail',
            user_id=user_id
        )

    perm = get_object_or_404(
        Permission,
        id=perm_id
    )

    if request.method == 'POST':

        user.user_permissions.remove(
            perm
        )

        messages.success(
            request,
            f"✅ Permission "
            f"'{perm.codename}' "
            f"imeondolewa kwa "
            f"{user.username}"
        )

    return redirect(
        'users:user_detail',
        user_id=user_id
    )


@login_required
def user_add_to_group(request, user_id):
    """
    Kumweka mtumiaji kwenye group.

    Super Admin hawezi kubadilishiwa groups.
    """

    if not request.user.is_superuser:

        messages.error(
            request,
            "❌ Huna ruhusa ya kuona ukurasa huu."
        )

        return redirect(
            'pigs:dashboard'
        )

    user = get_object_or_404(
        User,
        id=user_id
    )

    # ========================================================
    # PROTECT SUPER ADMIN GROUPS
    # ========================================================

    if user.is_superuser:

        messages.error(
            request,
            "🔒 Groups za Super Admin "
            "haziwezi kubadilishwa."
        )

        return redirect(
            'users:user_detail',
            user_id=user_id
        )

    if request.method == 'POST':

        group_id = request.POST.get(
            'group'
        )

        if group_id:

            try:

                group = Group.objects.get(
                    id=group_id
                )

                user.groups.add(
                    group
                )

                messages.success(
                    request,
                    f"✅ {user.username} "
                    f"ameongezwa kwenye group "
                    f"'{group.name}'"
                )

            except Group.DoesNotExist:

                messages.error(
                    request,
                    "❌ Group haipo"
                )

    return redirect(
        'users:user_detail',
        user_id=user_id
    )


@login_required
def user_remove_from_group(
    request,
    user_id,
    group_id
):
    """
    Kumwondoa mtumiaji kwenye group.

    Super Admin hawezi kuondolewa group.
    """

    if not request.user.is_superuser:

        messages.error(
            request,
            "❌ Huna ruhusa ya kuona ukurasa huu."
        )

        return redirect(
            'pigs:dashboard'
        )

    user = get_object_or_404(
        User,
        id=user_id
    )

    # ========================================================
    # PROTECT SUPER ADMIN GROUPS
    # ========================================================

    if user.is_superuser:

        messages.error(
            request,
            "🔒 Groups za Super Admin "
            "haziwezi kubadilishwa."
        )

        return redirect(
            'users:user_detail',
            user_id=user_id
        )

    group = get_object_or_404(
        Group,
        id=group_id
    )

    if request.method == 'POST':

        user.groups.remove(
            group
        )

        messages.success(
            request,
            f"✅ {user.username} "
            f"ameondolewa kwenye group "
            f"'{group.name}'"
        )

    return redirect(
        'users:user_detail',
        user_id=user_id
    )


@login_required
def user_edit(request, user_id):
    """
    Kubadilisha taarifa za mtumiaji.

    Target Super Admin:
        - verification inahitajika
        - status haiwezi kubadilishwa
        - account haiwezi kuharibiwa
    """

    if not request.user.is_superuser:

        messages.error(
            request,
            "❌ Huna ruhusa ya kuona ukurasa huu."
        )

        return redirect(
            'pigs:dashboard'
        )

    user = get_object_or_404(
        User,
        id=user_id
    )

    # ========================================================
    # SUPER ADMIN VERIFICATION
    # ========================================================

    verification_redirect = (
        _require_super_admin_target_verification(
            request,
            user
        )
    )

    if verification_redirect:

        return verification_redirect

    if request.method == 'POST':

        # ====================================================
        # DIRECT POST PROTECTION
        # ====================================================

        if user.is_superuser:

            if not _target_super_admin_requires_verification(
                request,
                user
            ):

                messages.error(
                    request,
                    "🔒 Verification ya Super Admin "
                    "inahitajika kwanza."
                )

                return redirect(
                    'users:verify_permission_access',
                    user_id=user.id
                )

        username = request.POST.get(
            'username'
        )

        email = request.POST.get(
            'email'
        )

        is_active = (
            request.POST.get(
                'is_active'
            )
            == 'on'
        )

        new_password = request.POST.get(
            'new_password'
        )

        # ====================================================
        # SUPER ADMIN ACCOUNT PROTECTION
        # ====================================================

        if user.is_superuser:

            # ------------------------------------------------
            # SUPER ADMIN HAIWEZI KUSIMAMISHWA
            # ------------------------------------------------

            if not user.is_active:

                user.is_active = True

            # ------------------------------------------------
            # HELD ACTIVE REGARDLESS OF FORM
            # ------------------------------------------------

            is_active = True

        # ====================================================
        # BADILISHA USERNAME
        # ====================================================

        if username and username != user.username:

            if User.objects.filter(
                username=username
            ).exclude(
                id=user.id
            ).exists():

                messages.error(
                    request,
                    f"❌ Jina '{username}' "
                    f"tayari linatumika."
                )

                return redirect(
                    'users:user_edit',
                    user_id=user.id
                )

            user.username = username

        # ====================================================
        # BADILISHA EMAIL
        # ====================================================

        if email is not None:

            user.email = email

        # ====================================================
        # BADILISHA STATUS
        # ====================================================

        if user.is_superuser:

            user.is_active = True

        else:

            user.is_active = is_active

        # ====================================================
        # BADILISHA PASSWORD
        # ====================================================

        if new_password:

            if len(new_password) < 6:

                messages.error(
                    request,
                    "❌ Nenosiri lazima liwe na "
                    "herufi 6 au zaidi."
                )

                return redirect(
                    'users:user_edit',
                    user_id=user.id
                )

            user.set_password(
                new_password
            )

        # ====================================================
        # SAVE
        # ====================================================

        user.save()

        if user.is_superuser:

            _clear_super_admin_verification(
                request,
                user
            )

        messages.success(
            request,
            f"✅ Taarifa za '{user.username}' "
            f"zimebadilishwa!"
        )

        return redirect(
            'users:user_detail',
            user_id=user.id
        )

    return render(
        request,
        'users/user_edit.html',
        {
            'user': user
        }
    )


# ============================================================
# USER PERMISSIONS MANAGEMENT
# ============================================================

@login_required
def user_permissions(request, user_id):
    """
    GUI ya kusimamia permissions za user mmoja.

    SUPER ADMIN:
        - Anaona permissions zote ON.
        - Permissions zake haziwezi kubadilishwa.
        - POST ya kubadilisha permissions zake inakataliwa.

    NORMAL USER:
        - Lazima awe na MASTER ACCESS:
              pigs.manage_data_management

        - Kila model ina action permissions zake tofauti:

              View
              Add
              Edit
              Delete

        - Kila action inaweza kuwashwa/kuzimwa
          independently.
    """

    # ========================================================
    # REQUESTER MUST BE SUPER ADMIN
    # ========================================================

    if not request.user.is_superuser:

        messages.error(
            request,
            "❌ Ni Super Admin pekee anayeruhusiwa "
            "kusimamia permissions."
        )

        return redirect(
            "pigs:dashboard"
        )

    # ========================================================
    # TARGET USER
    # ========================================================

    user = get_object_or_404(
        User,
        id=user_id
    )

    # ========================================================
    # SUPER ADMIN VERIFICATION
    # ========================================================

    verification_redirect = (
        _require_super_admin_target_verification(
            request,
            user
        )
    )

    if verification_redirect:

        return verification_redirect

    # ========================================================
    # DATA MANAGEMENT MODELS
    #
    # KILA MODEL INA:
    #
    #   View
    #   Add
    #   Edit
    #   Delete
    #
    # ========================================================

    data_management_models = [

        {
            "name": "Manunuzi",

            "permissions": [

                ("view_purchase", "View"),

                ("add_purchase", "Add"),

                ("change_purchase", "Edit"),

                ("delete_purchase", "Delete"),

            ],
        },

        {
            "name": "Nguruwe",

            "permissions": [

                ("view_pig", "View"),

                ("add_pig", "Add"),

                ("change_pig", "Edit"),

                ("delete_pig", "Delete"),

            ],
        },

        {
            "name": "Machinjio",

            "permissions": [

                ("view_slaughterbatch", "View"),

                ("add_slaughterbatch", "Add"),

                ("change_slaughterbatch", "Edit"),

                ("delete_slaughterbatch", "Delete"),

            ],
        },

        {
            "name": "Meat Stock",

            "permissions": [

                ("view_meatstock", "View"),

                ("add_meatstock", "Add"),

                ("change_meatstock", "Edit"),

                ("delete_meatstock", "Delete"),

            ],
        },

        {
            "name": "Mauzo ya Siku",

            "permissions": [

                ("view_dailysale", "View"),

                ("add_dailysale", "Add"),

                ("change_dailysale", "Edit"),

                ("delete_dailysale", "Delete"),

            ],
        },

        {
            "name": "Mauzo ya Nyama",

            "permissions": [

                ("view_pigsalerecord", "View"),

                ("add_pigsalerecord", "Add"),

                ("change_pigsalerecord", "Edit"),

                ("delete_pigsalerecord", "Delete"),

            ],
        },

        {
            "name": "Sehemu za Nyama",

            "permissions": [

                ("view_meatpartsale", "View"),

                ("add_meatpartsale", "Add"),

                ("change_meatpartsale", "Edit"),

                ("delete_meatpartsale", "Delete"),

            ],
        },

        {
            "name": "Chakula",

            "permissions": [

                ("view_fooditem", "View"),

                ("add_fooditem", "Add"),

                ("change_fooditem", "Edit"),

                ("delete_fooditem", "Delete"),

            ],
        },

        {
            "name": "Mauzo ya Chakula",

            "permissions": [

                ("view_foodsalerecord", "View"),

                ("add_foodsalerecord", "Add"),

                ("change_foodsalerecord", "Edit"),

                ("delete_foodsalerecord", "Delete"),

            ],
        },

        {
            "name": "Profit Reports",

            "permissions": [

                ("view_batchprofitreport", "View"),

                ("add_batchprofitreport", "Add"),

                ("change_batchprofitreport", "Edit"),

                ("delete_batchprofitreport", "Delete"),

            ],
        },

        {
            "name": "Daily Reports",

            "permissions": [

                ("view_dailysalereport", "View"),

                ("add_dailysalereport", "Add"),

                ("change_dailysalereport", "Edit"),

                ("delete_dailysalereport", "Delete"),

            ],
        },

        {
            "name": "Matumizi",

            "permissions": [

                ("view_expense", "View"),

                ("add_expense", "Add"),

                ("change_expense", "Edit"),

                ("delete_expense", "Delete"),

            ],
        },

    ]

    # ========================================================
    # BUILD GUI DATA
    # ========================================================

    permission_groups = []

    # ========================================================
    # MASTER ACCESS
    # ========================================================

    master_permission = None

    try:

        master_permission = Permission.objects.get(

            codename="manage_data_management",

            content_type__app_label="pigs"

        )

    except Permission.DoesNotExist:

        master_permission = None

    if master_permission:

        if user.is_superuser:

            master_checked = True

        else:

            master_checked = (
                user.user_permissions
                .filter(
                    id=master_permission.id
                )
                .exists()
            )

        permission_groups.append({

            "category": "Data Management",

            "is_master": True,

            "permissions": [{

                "permission": master_permission,

                "codename":
                    "manage_data_management",

                "label":
                    "Kusimamia Data Management",

                "action":
                    "master",

                "checked":
                    master_checked,

            }],

        })

    # ========================================================
    # DATA MANAGEMENT MODEL PERMISSIONS
    # ========================================================

    for model in data_management_models:

        category_permissions = []

        for codename, action in model["permissions"]:

            try:

                permission = Permission.objects.get(

                    codename=codename,

                    content_type__app_label="pigs"

                )

            except Permission.DoesNotExist:

                continue

            # ------------------------------------------------
            # SUPER ADMIN
            # ------------------------------------------------

            if user.is_superuser:

                checked = True

            # ------------------------------------------------
            # NORMAL USER
            # ------------------------------------------------

            else:

                checked = (
                    user.user_permissions
                    .filter(
                        id=permission.id
                    )
                    .exists()
                )

            category_permissions.append({

                "permission":
                    permission,

                "codename":
                    codename,

                "label":
                    action,

                "action":
                    action.lower(),

                "checked":
                    checked,

            })

        # ----------------------------------------------------
        # ADD CATEGORY
        # ----------------------------------------------------

        if category_permissions:

            permission_groups.append({

                "category":
                    model["name"],

                "is_master":
                    False,

                "permissions":
                    category_permissions,

            })

    # ========================================================
    # SAVE PERMISSIONS
    # ========================================================

    if request.method == "POST":

        # ====================================================
        # ABSOLUTE SUPER ADMIN PROTECTION
        # ====================================================

        if user.is_superuser:

            messages.error(
                request,
                "🔒 Permissions za Super Admin "
                "zimefungwa na haziwezi kubadilishwa."
            )

            return redirect(
                "users:user_permissions",
                user_id=user.id
            )

        # ====================================================
        # SELECTED PERMISSIONS
        # ====================================================

        selected_permissions = set(

            request.POST.getlist(
                "permissions"
            )

        )

        # ====================================================
        # BUILD PERMISSIONS TO ASSIGN
        # ====================================================

        permissions_to_assign = []

        for group in permission_groups:

            for item in group["permissions"]:

                codename = item["codename"]

                if codename in selected_permissions:

                    permissions_to_assign.append(
                        item["permission"]
                    )

        # ====================================================
        # SAVE DIRECT USER PERMISSIONS
        #
        # IMPORTANT:
        # set() inaondoa permissions za zamani
        # ambazo hazijachaguliwa.
        #
        # Hii ndiyo inayotuwezesha:
        #
        # View ON
        # Add OFF
        # Edit ON
        # Delete OFF
        #
        # independently.
        # ====================================================

        user.user_permissions.set(
            permissions_to_assign
        )

        messages.success(

            request,

            f"✅ Permissions za "
            f"'{user.username}' "
            f"zimehifadhiwa kikamilifu."

        )

        return redirect(

            "users:user_permissions",

            user_id=user.id

        )

    # ========================================================
    # RENDER
    # ========================================================

    return render(

        request,

        "users/user_permissions.html",

        {

            "user":
                user,

            "permission_groups":
                permission_groups,

        }

    )


# ============================================================
# VERIFY SUPER ADMIN ACCESS
# ============================================================

@login_required
def verify_permission_access(request, user_id):
    """
    Inathibitisha password ya Super Admin kabla ya kufungua
    taarifa nyeti za Super Admin.

    Hii verification inatumika kwa:
        - user_detail
        - user_edit
        - user_change_password
        - user_permissions

    Kwa normal user:
        verification haihitajiki.
    """

    # ========================================================
    # REQUESTER MUST BE SUPER ADMIN
    # ========================================================

    if not request.user.is_superuser:

        messages.error(
            request,
            "❌ Ni Super Admin pekee "
            "anayeruhusiwa kufanya verification."
        )

        return redirect(
            "pigs:dashboard"
        )

    # ========================================================
    # TARGET USER
    # ========================================================

    target_user = get_object_or_404(
        User,
        id=user_id
    )

    # ========================================================
    # IF TARGET IS NOT SUPER ADMIN
    # ========================================================

    if not target_user.is_superuser:

        return redirect(
            "users:user_detail",
            user_id=target_user.id
        )

    # ========================================================
    # POST
    # ========================================================

    if request.method == "POST":

        password = request.POST.get(
            "password",
            ""
        )

        # ====================================================
        # VERIFY REQUESTER PASSWORD
        # ====================================================

        if request.user.check_password(
            password
        ):

            # -----------------------------------------------
            # IMPORTANT:
            #
            # Tunatumia key ileile inayotumiwa na
            # _super_admin_verification_key()
            # -----------------------------------------------

            request.session[
                _super_admin_verification_key(
                    target_user.id
                )
            ] = True

            request.session.modified = True

            messages.success(
                request,
                f"✅ Uthibitisho umekamilika. "
                f"Sasa unaweza kusimamia taarifa "
                f"za {target_user.username}."
            )

            return redirect(
                "users:user_detail",
                user_id=target_user.id
            )

        # ====================================================
        # WRONG PASSWORD
        # ====================================================

        messages.error(
            request,
            "❌ Password ya Super Admin si sahihi."
        )

    # ========================================================
    # RENDER VERIFICATION PAGE
    # ========================================================

    return render(
        request,
        "users/verify_permission_access.html",
        {
            "target_user": target_user,
        }
    )


# ============================================================
# DATA MANAGEMENT - BACKEND
# ============================================================

from django.db.models import Q

from apps.pigs.models import (
    Purchase,
    Pig,
    SlaughterBatch,
    MeatStock,
    DailySale,
    PigSaleRecord,
    MeatPartSale,
    FoodItem,
    FoodSaleRecord,
    BatchProfitReport,
    DailySaleReport,
    Expense,
)


# ============================================================
# DATA MANAGEMENT MODEL MAP
#
# KILA MODEL INA PERMISSIONS NNE:
#
#   1. View
#   2. Add
#   3. Edit
#   4. Delete
#
# NA:
#
#   manage_data_management
#       = MASTER ACCESS YA DATA MANAGEMENT
#
# User lazima awe na MASTER ACCESS kwanza,
# ndipo action permissions ziweze kutumika.
# ============================================================

DATA_MANAGEMENT_MODELS = {

    # --------------------------------------------------------
    # PURCHASE
    # --------------------------------------------------------

    "purchase": {

        "model": Purchase,

        "label": "Manunuzi",

        "view_permission":
            "pigs.view_purchase",

        "add_permission":
            "pigs.add_purchase",

        "change_permission":
            "pigs.change_purchase",

        "delete_permission":
            "pigs.delete_purchase",
    },


    # --------------------------------------------------------
    # PIG
    # --------------------------------------------------------

    "pig": {

        "model": Pig,

        "label": "Nguruwe",

        "view_permission":
            "pigs.view_pig",

        "add_permission":
            "pigs.add_pig",

        "change_permission":
            "pigs.change_pig",

        "delete_permission":
            "pigs.delete_pig",
    },


    # --------------------------------------------------------
    # SLAUGHTER BATCH
    # --------------------------------------------------------

    "slaughterbatch": {

        "model": SlaughterBatch,

        "label": "Machinjio",

        "view_permission":
            "pigs.view_slaughterbatch",

        "add_permission":
            "pigs.add_slaughterbatch",

        "change_permission":
            "pigs.change_slaughterbatch",

        "delete_permission":
            "pigs.delete_slaughterbatch",
    },


    # --------------------------------------------------------
    # MEAT STOCK
    # --------------------------------------------------------

    "meatstock": {

        "model": MeatStock,

        "label": "Meat Stock",

        "view_permission":
            "pigs.view_meatstock",

        "add_permission":
            "pigs.add_meatstock",

        "change_permission":
            "pigs.change_meatstock",

        "delete_permission":
            "pigs.delete_meatstock",
    },


    # --------------------------------------------------------
    # DAILY SALE
    # --------------------------------------------------------

    "dailysale": {

        "model": DailySale,

        "label": "Mauzo ya Siku",

        "view_permission":
            "pigs.view_dailysale",

        "add_permission":
            "pigs.add_dailysale",

        "change_permission":
            "pigs.change_dailysale",

        "delete_permission":
            "pigs.delete_dailysale",
    },


    # --------------------------------------------------------
    # PIG / MEAT SALE
    # --------------------------------------------------------

    "pigsalerecord": {

        "model": PigSaleRecord,

        "label": "Mauzo ya Nyama",

        "view_permission":
            "pigs.view_pigsalerecord",

        "add_permission":
            "pigs.add_pigsalerecord",

        "change_permission":
            "pigs.change_pigsalerecord",

        "delete_permission":
            "pigs.delete_pigsalerecord",
    },


    # --------------------------------------------------------
    # MEAT PART SALE
    # --------------------------------------------------------

    "meatpartsale": {

        "model": MeatPartSale,

        "label": "Sehemu za Nyama",

        "view_permission":
            "pigs.view_meatpartsale",

        "add_permission":
            "pigs.add_meatpartsale",

        "change_permission":
            "pigs.change_meatpartsale",

        "delete_permission":
            "pigs.delete_meatpartsale",
    },


    # --------------------------------------------------------
    # FOOD ITEM
    # --------------------------------------------------------

    "fooditem": {

        "model": FoodItem,

        "label": "Chakula",

        "view_permission":
            "pigs.view_fooditem",

        "add_permission":
            "pigs.add_fooditem",

        "change_permission":
            "pigs.change_fooditem",

        "delete_permission":
            "pigs.delete_fooditem",
    },


    # --------------------------------------------------------
    # FOOD SALES
    # --------------------------------------------------------

    "foodsalerecord": {

        "model": FoodSaleRecord,

        "label": "Mauzo ya Chakula",

        "view_permission":
            "pigs.view_foodsalerecord",

        "add_permission":
            "pigs.add_foodsalerecord",

        "change_permission":
            "pigs.change_foodsalerecord",

        "delete_permission":
            "pigs.delete_foodsalerecord",
    },


    # --------------------------------------------------------
    # BATCH PROFIT REPORT
    # --------------------------------------------------------

    "batchprofitreport": {

        "model": BatchProfitReport,

        "label": "Profit Reports",

        "view_permission":
            "pigs.view_batchprofitreport",

        "add_permission":
            "pigs.add_batchprofitreport",

        "change_permission":
            "pigs.change_batchprofitreport",

        "delete_permission":
            "pigs.delete_batchprofitreport",
    },


    # --------------------------------------------------------
    # DAILY REPORT
    # --------------------------------------------------------

    "dailysalereport": {

        "model": DailySaleReport,

        "label": "Daily Reports",

        "view_permission":
            "pigs.view_dailysalereport",

        "add_permission":
            "pigs.add_dailysalereport",

        "change_permission":
            "pigs.change_dailysalereport",

        "delete_permission":
            "pigs.delete_dailysalereport",
    },


    # --------------------------------------------------------
    # EXPENSE
    # --------------------------------------------------------

    "expense": {

        "model": Expense,

        "label": "Matumizi",

        "view_permission":
            "pigs.view_expense",

        "add_permission":
            "pigs.add_expense",

        "change_permission":
            "pigs.change_expense",

        "delete_permission":
            "pigs.delete_expense",
    },

}


# ============================================================
# DATA MANAGEMENT PERMISSION ENFORCEMENT
# ============================================================

def _require_data_management_access(
    request,
    config,
    action=None
):
    """
    Enforce Data Management permissions.

    SUPER ADMIN:
        Ana access kamili.

    NORMAL USER:
        Lazima awe na:
            1. manage_data_management
            2. permission ya action husika

    action:
        view
        add
        change
        delete
    """

    # ========================================================
    # SUPER ADMIN
    # ========================================================

    if request.user.is_superuser:
        return True

    # ========================================================
    # MASTER ACCESS
    # ========================================================

    if not request.user.has_perm(
        "pigs.manage_data_management"
    ):
        return False

    # ========================================================
    # ACTION PERMISSION
    # ========================================================

    if action is None:
        return True

    permission_key = {
        "view": "view_permission",
        "add": "add_permission",
        "change": "change_permission",
        "delete": "delete_permission",
    }.get(action)

    if not permission_key:
        return False

    permission = config.get(
        permission_key
    )

    if not permission:
        return False

    return request.user.has_perm(
        permission
    )


# ============================================================
# DATA MANAGEMENT HOME
# ============================================================

@login_required
def data_management(request):
    """
    Data Management home.

    SUPER ADMIN:
        Anaona categories zote.

    NORMAL USER:
        Lazima awe na:

            manage_data_management

        ndipo aweze kutumia Data Management.

        Baada ya hapo category inaonekana ikiwa ana
        angalau moja kati ya:

            - view
            - add
            - change
            - delete
    """

    # ========================================================
    # DATA MANAGEMENT MASTER ACCESS
    # ========================================================

    if not request.user.is_superuser:

        if not request.user.has_perm(
            "pigs.manage_data_management"
        ):

            messages.error(
                request,
                "❌ Huna ruhusa ya kufungua Data Management."
            )

            return redirect(
                "pigs:dashboard"
            )

    # ========================================================
    # AVAILABLE DATA
    # ========================================================

    available_data = []

    for key, config in DATA_MANAGEMENT_MODELS.items():

        model = config["model"]

        # ----------------------------------------------------
        # PERMISSION NAMES
        # ----------------------------------------------------

        view_permission = (
            config["view_permission"]
        )

        add_permission = (
            config["add_permission"]
        )

        change_permission = (
            config["change_permission"]
        )

        delete_permission = (
            config["delete_permission"]
        )

        # ====================================================
        # ACCESS
        # ====================================================

        if request.user.is_superuser:

            allowed = True

        else:

            allowed = (

                request.user.has_perm(
                    view_permission
                )

                or

                request.user.has_perm(
                    add_permission
                )

                or

                request.user.has_perm(
                    change_permission
                )

                or

                request.user.has_perm(
                    delete_permission
                )
            )

        if not allowed:

            continue

        # ====================================================
        # ACTION PERMISSIONS
        # ====================================================

        if request.user.is_superuser:

            can_view = True
            can_add = True
            can_edit = True
            can_delete = True

        else:

            can_view = (
                request.user.has_perm(
                    view_permission
                )
            )

            can_add = (
                request.user.has_perm(
                    add_permission
                )
            )

            can_edit = (
                request.user.has_perm(
                    change_permission
                )
            )

            can_delete = (
                request.user.has_perm(
                    delete_permission
                )
            )

        # ====================================================
        # APPEND
        # ====================================================

        available_data.append({

            "key": key,

            "label": config["label"],

            "count": model.objects.count(),

            "can_view": can_view,

            "can_add": can_add,

            "can_edit": can_edit,

            "can_delete": can_delete,

            "view_permission": (
                view_permission
            ),

            "add_permission": (
                add_permission
            ),

            "change_permission": (
                change_permission
            ),

            "delete_permission": (
                delete_permission
            ),

        })

    # ========================================================
    # RENDER
    # ========================================================

    return render(
        request,
        "users/data_management.html",
        {
            "available_data": available_data,

            "is_super_admin": (
                request.user.is_superuser
            ),
        }
    )


# ============================================================
# DATA MANAGEMENT MODEL LIST
# ============================================================

@login_required
def data_management_model(
    request,
    model_key
):
    """
    Inaonyesha records za model moja.

    SUPER ADMIN:
        - Access full.
        - View full.
        - Add full.
        - Edit full.
        - Delete full.

    NORMAL USER:
        Lazima awe na:

            manage_data_management

        pamoja na angalau moja kati ya:

            - view
            - add
            - change
            - delete

        Edit inaonekana kama ana change permission.

        Delete inaonekana kama ana delete permission.

        Add inaonekana kama ana add permission.

        View inaonekana kama ana view permission.
    """

    # ========================================================
    # GET MODEL CONFIG
    # ========================================================

    config = DATA_MANAGEMENT_MODELS.get(
        model_key
    )

    if not config:

        messages.error(
            request,
            "❌ Aina ya data haijatambuliwa."
        )

        return redirect(
            "users:data_management"
        )

    # ========================================================
    # MASTER DATA MANAGEMENT ACCESS
    # ========================================================

    if not request.user.is_superuser:

        if not request.user.has_perm(
            "pigs.manage_data_management"
        ):

            messages.error(
                request,
                "❌ Huna ruhusa ya kufungua Data Management."
            )

            return redirect(
                "users:data_management"
            )

    # ========================================================
    # PERMISSION NAMES
    # ========================================================

    view_permission = (
        config["view_permission"]
    )

    add_permission = (
        config["add_permission"]
    )

    change_permission = (
        config["change_permission"]
    )

    delete_permission = (
        config["delete_permission"]
    )

    # ========================================================
    # ACCESS CHECK
    # ========================================================

    allowed = (
        request.user.is_superuser
        or
        _require_data_management_access(
            request,
            config
        )
    )

    if not allowed:

        messages.error(
            request,
            "❌ Huna ruhusa ya kusimamia data hii."
        )

        return redirect(
            "users:data_management"
        )

    # ========================================================
    # MODEL
    # ========================================================
    
    model = config["model"]

    # ========================================================
    # ACTION PERMISSIONS
    # ========================================================

    if request.user.is_superuser:

        can_view = True
        can_add = True
        can_edit = True
        can_delete = True

    else:

        can_view = (
            request.user.has_perm(
                view_permission
            )
        )

        can_add = (
            request.user.has_perm(
                add_permission
            )
        )

        can_edit = (
            request.user.has_perm(
                change_permission
            )
        )

        can_delete = (
            request.user.has_perm(
                delete_permission
            )
        )


    # ========================================================
    # VIEW PERMISSION ENFORCEMENT
    # ========================================================

    if not can_view:

        messages.error(
            request,
            "❌ Huna ruhusa ya kuona taarifa za data hii."
        )

        return redirect(
            "users:data_management"
        )

    # ========================================================
    # SEARCH
    # ========================================================

    search_query = (
        request.GET.get(
            "q",
            ""
        )
        .strip()
    )

    # ========================================================
    # BASE QUERYSET
    # ========================================================

    queryset = (
        model.objects
        .all()
    )

    # ========================================================
    # SEARCHABLE FIELDS
    # ========================================================

    searchable_fields = []

    for field in model._meta.fields:

        field_type = (
            field.get_internal_type()
        )

        if field_type in [
            "CharField",
            "TextField",
            "EmailField",
            "SlugField",
        ]:

            searchable_fields.append(
                field.name
            )

    # ========================================================
    # APPLY SEARCH
    # ========================================================

    if search_query and searchable_fields:

        search_filter = Q()

        for field_name in searchable_fields:

            search_filter |= Q(
                **{
                    f"{field_name}__icontains": (
                        search_query
                    )
                }
            )

        queryset = queryset.filter(
            search_filter
        )

    # ========================================================
    # ORDER
    # ========================================================

    queryset = (
        queryset
        .order_by("-id")
    )

    # ========================================================
    # MODEL FIELDS
    # ========================================================

    model_fields = []

    for field in model._meta.fields:

        model_fields.append({

            "name": field.name,

            "label": (
                field.verbose_name.title()
            ),

            "type": (
                field.get_internal_type()
            ),

        })

    # ========================================================
    # DISPLAY RECORDS
    # ========================================================

    display_records = []

    for record in queryset:

        row_values = []

        for field in model._meta.fields:

            value = getattr(
                record,
                field.name,
                None
            )

            # ------------------------------------------------
            # FOREIGN KEY / RELATION
            # ------------------------------------------------

            if field.is_relation:

                if value is None:

                    display_value = "-"

                else:

                    display_value = str(
                        value
                    )

            # ------------------------------------------------
            # NORMAL FIELD
            # ------------------------------------------------

            else:

                if value is None:

                    display_value = "-"

                else:

                    display_value = value

            row_values.append({

                "field_name": (
                    field.name
                ),

                "label": (
                    field.verbose_name.title()
                ),

                "value": display_value,

            })

        display_records.append({

            "id": record.pk,

            "object": record,

            "values": row_values,

            "created_at": getattr(
                record,
                "created_at",
                None
            ),

            "updated_at": getattr(
                record,
                "updated_at",
                None
            ),

        })

    # ========================================================
    # CONTEXT
    # ========================================================

    context = {

        # ----------------------------------------------------
        # MODEL
        # ----------------------------------------------------

        "model_key": model_key,

        "model_label": config["label"],

        "model": model,

        # ----------------------------------------------------
        # RECORDS
        # ----------------------------------------------------

        "records": queryset,

        "display_records": (
            display_records
        ),

        "record_count": (
            queryset.count()
        ),

        # ----------------------------------------------------
        # FIELDS
        # ----------------------------------------------------

        "model_fields": model_fields,

        # ----------------------------------------------------
        # SEARCH
        # ----------------------------------------------------

        "search_query": search_query,

        # ----------------------------------------------------
        # PERMISSIONS
        # ----------------------------------------------------

        "view_permission": (
            view_permission
        ),

        "add_permission": (
            add_permission
        ),

        "change_permission": (
            change_permission
        ),

        "delete_permission": (
            delete_permission
        ),

        "can_view": can_view,

        "can_add": can_add,

        "can_edit": can_edit,

        "can_delete": can_delete,

        # ----------------------------------------------------
        # USER TYPE
        # ----------------------------------------------------

        "is_super_admin": (
            request.user.is_superuser
        ),

    }

    return render(
        request,
        "users/data_management_model.html",
        context
    )


# ============================================================
# DATA MANAGEMENT PERMISSION HELPER
# ============================================================

def _data_management_permissions(
    request,
    config
):
    """
    Return action permissions za user.
    """

    if request.user.is_superuser:

        return {

            "can_view": True,

            "can_add": True,

            "can_edit": True,

            "can_delete": True,

        }

    return {

        "can_view": request.user.has_perm(
            config["view_permission"]
        ),

        "can_add": request.user.has_perm(
            config["add_permission"]
        ),

        "can_edit": request.user.has_perm(
            config["change_permission"]
        ),

        "can_delete": request.user.has_perm(
            config["delete_permission"]
        ),

    }


# ============================================================
# BACKUP DELETED RECORD
# ============================================================

def _backup_deleted_record(
    record,
    model_key,
    model_label
):
    """
    Hifadhi snapshot ya record kabla ya deletion.

    Super Admin haitumii backup hii.
    """

    backup_root = (
        Path(__file__).resolve().parents[2]
        / "data_backups"
    )

    backup_root.mkdir(
        parents=True,
        exist_ok=True
    )

    timestamp = timezone.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    model_name = (
        model_key.lower()
    )

    filename = (
        f"{model_name}_"
        f"{record.pk}_"
        f"{timestamp}.json"
    )

    backup_path = (
        backup_root / filename
    )

    serialized = serializers.serialize(
        "json",
        [record]
    )

    backup_data = {

        "model_key": model_key,

        "model_label": model_label,

        "record_id": record.pk,

        "deleted_at":
            timezone.now().isoformat(),

        "record":
            json.loads(serialized),

    }

    backup_path.write_text(
        json.dumps(
            backup_data,
            indent=4,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    return backup_path


# ============================================================
# DATA MANAGEMENT - EDIT RECORD
# ============================================================

@login_required
def data_management_edit(
    request,
    model_key,
    record_id
):
    """
    Edit record kupitia Data Management.

    Super Admin:
        Full access.

    Normal User:
        Lazima awe na:

            1. manage_data_management
            2. change permission ya model hiyo
    """

    # ========================================================
    # MODEL CONFIG
    # ========================================================

    config = DATA_MANAGEMENT_MODELS.get(
        model_key
    )

    if not config:

        messages.error(
            request,
            "❌ Aina ya data haijatambuliwa."
        )

        return redirect(
            "users:data_management"
        )

    # ========================================================
    # MASTER DATA MANAGEMENT ACCESS
    # ========================================================

    if not request.user.is_superuser:

        if not request.user.has_perm(
            "pigs.manage_data_management"
        ):

            messages.error(
                request,
                "❌ Huna ruhusa ya kufungua Data Management."
            )

            return redirect(
                "pigs:dashboard"
            )

    # ========================================================
    # EDIT PERMISSION
    # ========================================================

    if not request.user.is_superuser:

        if not request.user.has_perm(
            config["change_permission"]
        ):

            messages.error(
                request,
                "❌ Huna ruhusa ya kuhariri "
                "data hii."
            )

            return redirect(
                "users:data_management_model",
                model_key=model_key
            )

    # ========================================================
    # GET RECORD
    # ========================================================

    model = config["model"]

    record = get_object_or_404(
        model,
        pk=record_id
    )

    # ========================================================
    # BUILD FORM
    # ========================================================

    editable_fields = []

    for field in model._meta.fields:

        if not field.editable:

            continue

        if field.name in [
            "created_at",
            "updated_at",
        ]:

            continue

        editable_fields.append(
            field.name
        )

    DataForm = modelform_factory(
        model,
        fields=editable_fields
    )

    # ========================================================
    # POST
    # ========================================================

    if request.method == "POST":

        form = DataForm(
            request.POST,
            instance=record
        )

        if form.is_valid():

            form.save()

            messages.success(
                request,
                f"✅ {config['label']} "
                f"#{record.pk} imehaririwa."
            )

            return redirect(
                "users:data_management_model",
                model_key=model_key
            )

    else:

        form = DataForm(
            instance=record
        )

    # ========================================================
    # RENDER
    # ========================================================

    return render(
        request,
        "users/data_management_edit.html",
        {
            "form": form,

            "record": record,

            "model_key": model_key,

            "model_label": config["label"],

            "is_super_admin": (
                request.user.is_superuser
            ),
        }
    )


# ============================================================
# DATA MANAGEMENT - DELETE RECORD
# ============================================================

@login_required
def data_management_delete(
    request,
    model_key,
    record_id
):
    """
    Delete record kupitia Data Management.

    SUPER ADMIN:
        Delete moja kwa moja.
        Hakuna backup requirement.

    NORMAL USER:
        Lazima awe na:

            1. manage_data_management
            2. delete permission ya model hiyo

        Backup record kwanza,
        kisha delete.
    """

    # ========================================================
    # MODEL CONFIG
    # ========================================================

    config = DATA_MANAGEMENT_MODELS.get(
        model_key
    )

    if not config:

        messages.error(
            request,
            "❌ Aina ya data haijatambuliwa."
        )

        return redirect(
            "users:data_management"
        )

    # ========================================================
    # MASTER DATA MANAGEMENT ACCESS
    # ========================================================

    if not request.user.is_superuser:

        if not request.user.has_perm(
            "pigs.manage_data_management"
        ):

            messages.error(
                request,
                "❌ Huna ruhusa ya kufungua Data Management."
            )

            return redirect(
                "pigs:dashboard"
            )

    # ========================================================
    # DELETE PERMISSION
    # ========================================================

    if not request.user.is_superuser:

        if not request.user.has_perm(
            config["delete_permission"]
        ):

            messages.error(
                request,
                "❌ Huna ruhusa ya kufuta "
                "data hii."
            )

            return redirect(
                "users:data_management_model",
                model_key=model_key
            )

    # ========================================================
    # RECORD
    # ========================================================

    model = config["model"]

    record = get_object_or_404(
        model,
        pk=record_id
    )

    # ========================================================
    # GET = SHOW CONFIRMATION
    # ========================================================

    if request.method != "POST":

        return render(
            request,
            "users/data_management_delete_confirm.html",
            {
                "record": record,

                "model_key": model_key,

                "model_label": config["label"],

                "is_super_admin": (
                    request.user.is_superuser
                ),
            }
        )

    # ========================================================
    # POST = DELETE
    # ========================================================

    record_id_display = record.pk

    try:

        with transaction.atomic():

            # ------------------------------------------------
            # NORMAL USER BACKUP
            # ------------------------------------------------

            if not request.user.is_superuser:

                _backup_deleted_record(
                    record=record,

                    model_key=model_key,

                    model_label=config["label"],
                )

            # ------------------------------------------------
            # DELETE
            # ------------------------------------------------

            record.delete()

        messages.success(
            request,
            f"✅ {config['label']} "
            f"#{record_id_display} imefutwa."
        )

    except Exception as error:

        messages.error(
            request,
            f"❌ Data haikuweza kufutwa: {error}"
        )

    return redirect(
        "users:data_management_model",
        model_key=model_key
    )
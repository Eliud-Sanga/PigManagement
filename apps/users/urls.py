from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    # Login/Logout
    path(
        'login/', 
         views.
         login_view, 
         name='login'
         ),

    path(
        'logout/', 
         views.logout_view, 
         name='logout'
    ),
    
    path(
        'users/', 
        views.user_list, 
        name='user_list'
    ),

    path(
        'users/create/', 
        views.user_create, 
        name='user_create'
    ),

    path(
        'users/<int:user_id>/', 
        views.user_detail, 
        name='user_detail'
    ),

    path(
        'users/<int:user_id>/edit/', 
        views.user_edit, 
        name='user_edit'
    ),

    path(
        'users/<int:user_id>/delete/', 
        views.user_delete, 
        name='user_delete'
    ),

    path(
        'users/<int:user_id>/toggle-active/', 
        views.user_toggle_active, 
        name='user_toggle_active'
    ),

    path(
        'users/<int:user_id>/change-password/', 
        views.user_change_password, 
        name='user_change_password'
    ),

    path(
        'users/<int:user_id>/grant-permission/', 
        views.user_grant_permission, 
        name='user_grant_permission'
    ),

    path(
        'users/<int:user_id>/revoke-permission/<int:perm_id>/', 
        views.user_revoke_permission, 
        name='user_revoke_permission'
    ),

    path(
        'users/<int:user_id>/add-to-group/', 
        views.user_add_to_group, 
        name='user_add_to_group'
    ),

    path(
        'users/<int:user_id>/remove-from-group/<int:group_id>/', 
        views.user_remove_from_group, 
        name='user_remove_from_group'
    ),

    path(
        'users/<int:user_id>/permissions/',
        views.user_permissions,
        name='user_permissions'
    ),

    path(
        'users/<int:user_id>/verify-permissions/',
        views.verify_permission_access,
        name='verify_permission_access'
    ),
    
    path(
        'data-management/',
        views.data_management,
        name='data_management'
    ),

    path(
        'data-management/<str:model_key>/',
        views.data_management_model,
        name='data_management_model'
    ),

    path(
        'data-management/<str:model_key>/<int:record_id>/edit/',
        views.data_management_edit,
        name='data_management_edit'
    ),

    path(
        'data-management/<str:model_key>/<int:record_id>/delete/',
        views.data_management_delete,
        name='data_management_delete'
    ),

    ]


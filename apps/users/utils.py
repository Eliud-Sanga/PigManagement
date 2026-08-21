from .models import AuditLog


def create_audit_log(
    request,
    action,
    model_name="",
    object_id="",
    description="",
):
    ip_address = request.META.get(
        "HTTP_X_FORWARDED_FOR"
    )

    if ip_address:
        ip_address = ip_address.split(",")[0].strip()
    else:
        ip_address = request.META.get(
            "REMOTE_ADDR"
        )

    AuditLog.objects.create(
        user=request.user
        if request.user.is_authenticated
        else None,
        action=action,
        model_name=model_name,
        object_id=str(object_id)
        if object_id
        else "",
        description=description,
        ip_address=ip_address,
    )

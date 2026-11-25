from .models import AuditLog


def _client_ip(request):
    if request is None:
        return None

    # Do not record a client-supplied forwarding header as trusted evidence.
    return request.META.get("REMOTE_ADDR")


def create_audit_log(
    request,
    action,
    model_name="",
    object_id="",
    description="",
    *,
    result="SUCCESS",
    metadata=None,
    company=None,
):
    """
    Backward-compatible audit helper.
    """

    user = None

    if (
        request is not None
        and getattr(request, "user", None) is not None
        and request.user.is_authenticated
    ):
        user = request.user

    if company is None and request is not None:
        company = getattr(request, "company", None)

    return AuditLog.objects.create(
        user=user,
        action=action,
        result=result,
        model_name=model_name or "",
        object_id=str(object_id or ""),
        company_public_id=(
            getattr(company, "public_id", None)
            if company is not None
            else None
        ),
        description=description or "",
        metadata=metadata or {},
        ip_address=_client_ip(request),
        user_agent=(
            request.META.get(
                "HTTP_USER_AGENT",
                "",
            )[:500]
            if request is not None
            else ""
        ),
    )

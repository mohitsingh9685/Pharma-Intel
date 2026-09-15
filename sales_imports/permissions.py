from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from accounts.models import User


def administrator_required(view_function):
    """Require an active Pharma Intel Administrator application role."""

    @login_required
    @wraps(view_function)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_active or request.user.role != User.Role.ADMIN:
            raise PermissionDenied
        return view_function(request, *args, **kwargs)

    return wrapped

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.models import User


@login_required
def home(request):
    if request.user.role == User.Role.ADMIN:
        return redirect("sales_imports:list")
    return render(request, "home.html")

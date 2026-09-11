from django.contrib.auth.base_user import BaseUserManager


class UserManager(BaseUserManager):
    """Create users whose email address is their login identity."""

    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        if not email:
            return ""
        normalized_email = super().normalize_email(email)
        return normalized_email.strip().casefold()

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")

        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.full_clean()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("role", self.model.Role.ADMIN)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_superuser=True.")
        if extra_fields.get("role") != self.model.Role.ADMIN:
            raise ValueError("A superuser must have the administrator role.")

        return self.create_user(email, password, **extra_fields)


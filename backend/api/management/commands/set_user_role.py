from django.core.management.base import BaseCommand, CommandError

from api.models import User


class Command(BaseCommand):
    help = "Set an account's application role."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument(
            "role",
            choices=[role for role, _label in User.Role.choices],
        )

    def handle(self, *args, **options):
        email = options["email"].strip()
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist as error:
            raise CommandError(f"No account found for {email!r}.") from error

        role = options["role"]
        if user.role == role:
            self.stdout.write(f"{user.email} already has role {role}.")
            return

        user.role = role
        user.save(update_fields=("role",))
        self.stdout.write(
            self.style.SUCCESS(f"Set role {role} for {user.email}."),
        )

from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from rest_framework import serializers

from .models import (
    Client,
    Department,
    AttendanceCorrectionRequest,
    Employee,
    Evaluation,
    OfficeLocation,
    Prediction,
    User,
)

EMPLOYEE_ROLE_ALIASES = {
    User.Role.hr_manager: Employee.Role.management,
    User.Role.employee: Employee.Role.staff,
}


def canonical_employee_role(role):
    return EMPLOYEE_ROLE_ALIASES.get(role, role)


def create_employee_for_account(user):
    role = canonical_employee_role(user.role)
    if role not in (Employee.Role.staff, Employee.Role.team_leader):
        return None
    department, _ = Department.objects.get_or_create(name="Recruitment")
    return Employee.objects.create(
        user=user,
        name=user.name,
        role=role,
        department=department,
        position=user.get_role_display(),
        date_hired=timezone.localdate(),
    )


DEFAULT_NOTIFICATION_PREFERENCES = {
    "email_evaluations": True,
    "email_predictions": True,
    "weekly_summary": False,
}


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "name", "email", "role", "created_at")
        read_only_fields = ("id", "created_at")


class UserAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "name", "email", "role", "is_active", "created_at")
        read_only_fields = ("id", "created_at")


class UserAccountCreateSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=1, max_length=160)
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, max_length=128, write_only=True)
    role = serializers.ChoiceField(choices=())

    def __init__(self, *args, allowed_roles, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = allowed_roles

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists")
        return value

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User.objects.create_user(
            password=password,
            is_active=True,
            is_staff=False,
            is_superuser=False,
            **validated_data,
        )
        create_employee_for_account(user)
        return user


class UserAccountUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("name", "email", "role", "is_active")

    def __init__(self, *args, allowed_roles, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = allowed_roles

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email__iexact=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("An account with this email already exists")
        return value

    def update(self, instance, validated_data):
        previous_role = instance.role
        account = super().update(instance, validated_data)
        employee = Employee.objects.filter(user=account).first()
        if employee is None:
            create_employee_for_account(account)
            return account
        if employee:
            employee.name = account.name
            update_fields = ["name"]
            if previous_role != account.role:
                employee.role = canonical_employee_role(account.role)
                update_fields.append("role")
            if employee.role != Employee.Role.staff and employee.team_leader_id:
                employee.team_leader = None
                update_fields.append("team_leader")
            employee.save(update_fields=update_fields)
        return account


class AttendanceCorrectionRequestSerializer(serializers.ModelSerializer):
    record_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = AttendanceCorrectionRequest
        fields = (
            "id",
            "record_id",
            "requested_status",
            "requested_clock_in_at",
            "requested_clock_out_at",
            "change_status",
            "change_clock_in",
            "change_clock_out",
            "reason",
            "status",
            "review_note",
            "created_at",
            "reviewed_at",
            "requested_by",
            "reviewed_by",
        )
        read_only_fields = (
            "id",
            "status",
            "review_note",
            "created_at",
            "reviewed_at",
            "requested_by",
            "reviewed_by",
        )

    def validate_reason(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A correction reason is required.")
        return value

    def validate(self, attrs):
        if not (
            attrs.get("change_status")
            or attrs.get("change_clock_in")
            or attrs.get("change_clock_out")
        ):
            raise serializers.ValidationError(
                "Choose at least one attendance detail to correct."
            )
        for field, selected in (
            ("requested_clock_in_at", attrs.get("change_clock_in")),
            ("requested_clock_out_at", attrs.get("change_clock_out")),
        ):
            if selected and attrs.get(field) is None:
                raise serializers.ValidationError(
                    {field: "Enter the corrected time or clear the change checkbox."}
                )
            if not selected and field in attrs:
                attrs.pop(field)
        return attrs


class SubmissionReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=(("reviewed", "Approved"), ("rejected", "Rejected"))
    )
    reviewer_comment = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=5000,
    )


class UserProfileSerializer(serializers.ModelSerializer):
    notification_preferences = serializers.DictField()
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "name", "email", "role", "notification_preferences", "photo_url")
        read_only_fields = ("id", "role")

    def to_representation(self, instance):
        result = super().to_representation(instance)
        result["notification_preferences"] = {
            **DEFAULT_NOTIFICATION_PREFERENCES,
            **(instance.notification_preferences or {}),
        }
        return result

    def get_photo_url(self, instance):
        if instance.photo_blob_path or instance.photo:
            return "/api/users/me/photo"
        return ""

    def validate_email(self, value):
        return value.lower()


class UserCreateSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=1, max_length=160)
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, max_length=128, write_only=True)
    role = serializers.ChoiceField(
        choices=(
            User.Role.admin,
            User.Role.management,
            User.Role.team_leader,
            User.Role.staff,
            User.Role.employee,
            User.Role.client,
        )
    )
    company_name = serializers.CharField(max_length=180, required=False, allow_null=True, allow_blank=True)
    services_used = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists")
        return value

    def create(self, validated_data):
        company_name = validated_data.pop("company_name", None)
        services_used = validated_data.pop("services_used", None)
        password = validated_data.pop("password")
        user = User.objects.create_user(password=password, **validated_data)
        create_employee_for_account(user)
        if user.role == User.Role.client:
            Client.objects.create(
                client_code=f"CL-{user.pk:03d}",
                user=user,
                company_name=company_name or user.name,
                services_used=services_used or "Not specified",
            )
        return user


class NotificationPreferencesSerializer(serializers.Serializer):
    email_evaluations = serializers.BooleanField(default=True)
    email_predictions = serializers.BooleanField(default=True)
    weekly_summary = serializers.BooleanField(default=False)


class OfficeLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = OfficeLocation
        fields = (
            "id",
            "name",
            "latitude",
            "longitude",
            "allowed_radius_meters",
            "outside_time_in_policy",
            "is_active",
        )
        read_only_fields = ("id",)

    def validate_latitude(self, value):
        if not -90 <= value <= 90:
            raise serializers.ValidationError("Latitude must be between -90 and 90.")
        return value

    def validate_longitude(self, value):
        if not -180 <= value <= 180:
            raise serializers.ValidationError("Longitude must be between -180 and 180.")
        return value


class PasswordUpdateSerializer(serializers.Serializer):
    current_password = serializers.CharField(min_length=1, max_length=128, write_only=True)
    new_password = serializers.CharField(min_length=8, max_length=128, write_only=True)

    def validate_new_password(self, value):
        validate_password(value, user=self.context["request"].user)
        return value


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ("id", "name", "description", "is_active")


class EmployeeSerializer(serializers.ModelSerializer):
    department = serializers.CharField(max_length=100)
    position = serializers.CharField(max_length=120, required=False, allow_blank=True)
    user_id = serializers.IntegerField(required=False, allow_null=True)
    team_leader_id = serializers.IntegerField(required=False, allow_null=True)
    role = serializers.ChoiceField(choices=Employee.Role.choices, required=False)

    class Meta:
        model = Employee
        fields = (
            "id", "employee_code", "user_id", "team_leader_id", "name", "role", "department", "position",
            "date_hired", "status", "attendance_rate", "tenure_months", "training_hours",
        )
        read_only_fields = ("id",)
        extra_kwargs = {"employee_code": {"required": False, "allow_null": True}}

    def validate_status(self, value):
        if value not in {"active", "inactive"}:
            raise serializers.ValidationError("Status must be active or inactive")
        return value

    def validate_attendance_rate(self, value):
        if not 0 <= value <= 100:
            raise serializers.ValidationError("Attendance rate must be between 0 and 100")
        return value

    def validate_tenure_months(self, value):
        if value < 0:
            raise serializers.ValidationError("Tenure months cannot be negative")
        return value

    def validate_training_hours(self, value):
        if value < 0:
            raise serializers.ValidationError("Training hours cannot be negative")
        return value

    def validate(self, attrs):
        user_id = attrs.get("user_id", getattr(self.instance, "user_id", None))
        if user_id is not None and ("user_id" in attrs or self.instance is None):
            user = User.objects.filter(pk=user_id).first()
            if user is None or user.role not in {
                User.Role.staff,
                User.Role.employee,
                User.Role.team_leader,
            }:
                raise serializers.ValidationError({"user_id": "user_id must reference an existing Staff or Team Leader account"})
        leader_id = attrs.get("team_leader_id", getattr(self.instance, "team_leader_id", None))
        if leader_id is not None:
            leader = User.objects.filter(pk=leader_id, role=User.Role.team_leader).first()
            if leader is None:
                raise serializers.ValidationError({"team_leader_id": "team_leader_id must reference a Team Leader account"})
        role = attrs.get("role", getattr(self.instance, "role", Employee.Role.staff))
        if not self.instance and not attrs.get("position"):
            attrs["position"] = dict(Employee.Role.choices)[role]
        effective_leader_id = attrs.get(
            "team_leader_id",
            getattr(self.instance, "team_leader_id", None),
        )
        if role != Employee.Role.staff and effective_leader_id is not None:
            raise serializers.ValidationError(
                {"team_leader_id": "Only Staff employees can be assigned to a Team Leader."}
            )
        if user_id is not None and ("user_id" in attrs or self.instance is None):
            linked_user = User.objects.get(pk=user_id)
            if canonical_employee_role(linked_user.role) != role:
                raise serializers.ValidationError(
                    {"role": "The employee role must match the linked account role."}
                )
        if self.instance and self.instance.user_id and role != canonical_employee_role(self.instance.user.role):
            request = self.context.get("request")
            actor = request.user if request else None
            permitted = (
                {Employee.Role.management, Employee.Role.team_leader, Employee.Role.staff}
                if actor and actor.role == User.Role.admin
                else {Employee.Role.team_leader, Employee.Role.staff}
            )
            if not actor or actor.role not in (
                User.Role.admin,
                User.Role.management,
                User.Role.hr_manager,
            ):
                raise serializers.ValidationError(
                    {"role": "You do not have permission to change this account role."}
                )
            if role not in permitted:
                raise serializers.ValidationError(
                    {"role": "The selected role cannot be assigned to this linked employee."}
                )
        if not self.partial or "department" in attrs:
            department_name = attrs.get("department")
            if department_name:
                department, _ = Department.objects.get_or_create(name=department_name.strip())
                attrs["department"] = department
        return attrs

    def create(self, validated_data):
        user_id = validated_data.pop("user_id", None)
        team_leader_id = validated_data.pop("team_leader_id", None)
        if "role" in validated_data and "position" not in validated_data:
            validated_data["position"] = dict(Employee.Role.choices)[validated_data["role"]]
        if not validated_data.get("employee_code"):
            validated_data["employee_code"] = "pending"
            employee = Employee.objects.create(**validated_data, user_id=user_id, team_leader_id=team_leader_id)
            employee.employee_code = f"EMP-{1000 + employee.pk}"
            employee.save(update_fields=("employee_code",))
            return employee
        validated_data["user_id"] = user_id
        validated_data["team_leader_id"] = team_leader_id
        return Employee.objects.create(**validated_data)

    def update(self, instance, validated_data):
        if "user_id" in validated_data:
            instance.user_id = validated_data.pop("user_id")
        if "team_leader_id" in validated_data:
            instance.team_leader_id = validated_data.pop("team_leader_id")
        role = validated_data.get("role")
        if role and role != Employee.Role.staff:
            instance.team_leader = None
        if instance.user_id:
            if role and role != canonical_employee_role(instance.user.role):
                instance.user.role = role
            if "name" in validated_data:
                instance.user.name = validated_data["name"]
            instance.user.save(update_fields=("role", "name"))
        return super().update(instance, validated_data)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.user_id:
            data["role"] = canonical_employee_role(instance.user.role)
        return data


class EvaluationSerializer(serializers.ModelSerializer):
    employee_id = serializers.IntegerField()
    evaluator_id = serializers.IntegerField(read_only=True)
    overall_score = serializers.FloatField(read_only=True)
    assigned_to_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = Evaluation
        fields = (
            "id", "employee_id", "evaluator_id", "date", "productivity_score",
            "attendance_score", "quality_score", "teamwork_score",
            "communication_score", "comments", "created_at", "overall_score",
            "status", "phase", "cycle_month", "team_leader_rating",
            "management_rating", "hybrid_score", "auto_score", "assigned_to_id",
        )
        read_only_fields = (
            "id", "evaluator_id", "created_at", "overall_score", "status",
            "phase", "cycle_month", "team_leader_rating", "management_rating",
            "hybrid_score", "auto_score", "assigned_to_id",
        )

    def validate_employee_id(self, value):
        if not Employee.objects.filter(pk=value).exists():
            raise serializers.ValidationError("Employee not found")
        return value

    def validate(self, attrs):
        for field in ("productivity_score", "attendance_score", "quality_score", "teamwork_score", "communication_score"):
            score = attrs.get(field)
            if score is not None and not 0 <= score <= 100:
                raise serializers.ValidationError({field: "Score must be between 0 and 100"})
        return attrs

    def create(self, validated_data):
        return Evaluation.objects.create(evaluator=self.context["request"].user, **validated_data)

    def update(self, instance, validated_data):
        score_fields = (
            "productivity_score",
            "attendance_score",
            "quality_score",
            "teamwork_score",
            "communication_score",
        )
        scores_changed = any(field in validated_data for field in score_fields)
        instance = super().update(instance, validated_data)
        if scores_changed:
            instance.auto_score = None
            instance.status = (
                Evaluation.Status.completed
                if all(getattr(instance, field) is not None for field in score_fields)
                else Evaluation.Status.pending
            )
            instance.save(update_fields=("auto_score", "status"))
        return instance


class PredictionSerializer(serializers.ModelSerializer):
    employee_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Prediction
        fields = (
            "id", "employee_id", "predicted_category", "confidence_score",
            "feature_importances", "model_source", "model_version", "created_at",
        )
        read_only_fields = fields


class ClientSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Client
        fields = ("id", "client_code", "user_id", "company_name", "services_used", "account_status", "next_renewal_date")
        read_only_fields = fields

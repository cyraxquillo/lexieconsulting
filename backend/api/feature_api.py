import csv
import hmac
import logging
import mimetypes
import os
from datetime import date

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .access import (
    MANAGEMENT_ROLES,
    TEAM_ROLES,
    VIEWER_ROLES,
    can_manage_device,
    employee_queryset_for,
    evaluation_queryset_for,
    is_management,
    managed_user_queryset,
)
from .automation import create_notification
from .blob_storage import get_submission_blob
from .hybrid import update_hybrid_evaluation_score
from .models import (
    AutomationSettings,
    Department,
    Evaluation,
    EvaluationCriteriaSettings,
    Notification,
    Prediction,
    TrustedDevice,
    User,
)
from .serializers import EvaluationSerializer, PredictionSerializer


logger = logging.getLogger(__name__)
CRITERIA_DEFAULTS = {
    "productivity_score": "Productivity",
    "attendance_score": "Attendance",
    "quality_score": "Quality of work",
    "teamwork_score": "Teamwork",
    "communication_score": "Communication",
}


class AutomationSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationSettings
        exclude = ("updated_by", "updated_at")

    def validate_score_drop_threshold(self, value):
        if not 0 < value <= 100:
            raise serializers.ValidationError(
                "The score-drop threshold must be greater than 0 and at most 100."
            )
        return value

    def validate_attendance_working_days(self, value):
        days = [item.strip() for item in value.split(",") if item.strip()]
        if not days or any(not item.isdigit() or not 1 <= int(item) <= 7 for item in days):
            raise serializers.ValidationError(
                "Use comma-separated ISO weekday numbers from 1 through 7."
            )
        if len(set(days)) != len(days):
            raise serializers.ValidationError("Working days must not be repeated.")
        return ",".join(days)

    def validate_attendance_grace_minutes(self, value):
        if value < 0:
            raise serializers.ValidationError("Grace period cannot be negative.")
        return value

    def validate_attendance_holidays(self, value):
        holidays = [item.strip() for item in value.split(",") if item.strip()]
        try:
            for item in holidays:
                date.fromisoformat(item)
        except ValueError as error:
            raise serializers.ValidationError(
                "Use comma-separated YYYY-MM-DD dates."
            ) from error
        return ",".join(holidays)

    def validate(self, attrs):
        point_fields = (
            "attendance_on_time_points",
            "attendance_late_points",
            "attendance_time_out_points",
            "attendance_no_time_out_points",
            "attendance_excused_points",
            "attendance_absent_points",
        )
        for field in point_fields:
            value = attrs.get(field, getattr(self.instance, field))
            if not 0 <= value <= 1:
                raise serializers.ValidationError({field: "Points must be between 0 and 1."})
        open_time = attrs.get("attendance_time_in_open_time", self.instance.attendance_time_in_open_time)
        cutoff = attrs.get("attendance_cutoff_time", self.instance.attendance_cutoff_time)
        expected_out = attrs.get("attendance_expected_time_out", self.instance.attendance_expected_time_out)
        break_start = attrs.get("attendance_break_start_time", self.instance.attendance_break_start_time)
        break_end = attrs.get("attendance_break_end_time", self.instance.attendance_break_end_time)
        if open_time > cutoff:
            raise serializers.ValidationError({"attendance_cutoff_time": "The on-time cutoff cannot precede Time In opens."})
        if expected_out <= open_time:
            raise serializers.ValidationError({"attendance_expected_time_out": "Expected Time Out must follow Time In opens."})
        if break_start and break_end and break_end <= break_start:
            raise serializers.ValidationError({"attendance_break_end_time": "Break end time must follow break start time."})
        return attrs


class HybridReviewSerializer(serializers.Serializer):
    team_leader_rating = serializers.FloatField(required=False, allow_null=True)
    management_rating = serializers.FloatField(required=False, allow_null=True)
    comments = serializers.CharField(required=False, allow_blank=True, max_length=10000)

    def validate(self, attrs):
        for field in ("team_leader_rating", "management_rating"):
            value = attrs.get(field)
            if value is not None and not 0 <= value <= 100:
                raise serializers.ValidationError(
                    {field: "Rating must be between 0 and 100."}
                )
        return attrs


class ProfilePhotoUploadSerializer(serializers.Serializer):
    photo = serializers.ImageField()


def _report_queryset(request):
    queryset = evaluation_queryset_for(request.user).select_related(
        "employee__department", "evaluator"
    )
    department_id = request.query_params.get("department", "").strip()
    start_date = request.query_params.get("start_date", "").strip()
    end_date = request.query_params.get("end_date", "").strip()
    if department_id:
        department = (
            Department.objects.filter(pk=int(department_id)).first()
            if department_id.isdigit()
            else Department.objects.filter(name__iexact=department_id).first()
        )
        if department is None:
            return None, Response({"department": ["Select a valid department."]}, status=422)
        queryset = queryset.filter(employee__department=department)
    parsed_dates = {}
    for key, value in (("start_date", start_date), ("end_date", end_date)):
        if value:
            try:
                parsed_dates[key] = date.fromisoformat(value)
            except ValueError:
                return None, Response({key: ["Use YYYY-MM-DD format."]}, status=422)
    if parsed_dates.get("start_date") and parsed_dates.get("end_date"):
        if parsed_dates["start_date"] > parsed_dates["end_date"]:
            return None, Response({"detail": "start_date must be on or before end_date."}, status=422)
    if "start_date" in parsed_dates:
        queryset = queryset.filter(date__gte=parsed_dates["start_date"])
    if "end_date" in parsed_dates:
        queryset = queryset.filter(date__lte=parsed_dates["end_date"])
    return queryset.order_by("date", "employee__name"), None


def _report_rows(queryset):
    return [
        {
            "id": item.pk,
            "employee_code": item.employee.employee_code,
            "employee": item.employee.name,
            "department": item.employee.department.name,
            "date": item.date.isoformat(),
            "score": item.overall_score,
            "evaluator": item.evaluator.name,
        }
        for item in queryset
    ]


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def report_records(request):
    if request.user.role not in TEAM_ROLES:
        return Response({"detail": "You do not have permission to view reports."}, status=403)
    queryset, error = _report_queryset(request)
    if error:
        return error
    rows = _report_rows(queryset)
    scores = [row["score"] for row in rows if row["score"] is not None]
    return Response({
        "rows": rows,
        "record_count": len(rows),
        "average_score": round(sum(scores) / len(scores), 2) if scores else 0,
        "departments": list(
            employee_queryset_for(request.user)
            .values_list("department__name", flat=True)
            .distinct()
            .order_by("department__name")
        ),
    })


def _csv_safe(value):
    if value is None:
        return ""
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def report_export_csv(request):
    if request.user.role not in TEAM_ROLES:
        return Response({"detail": "You do not have permission to export reports."}, status=403)
    queryset, error = _report_queryset(request)
    if error:
        return error
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="lexie-performance-report.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(("Employee ID", "Employee", "Department", "Date", "Overall score", "Evaluator"))
    for row in _report_rows(queryset):
        writer.writerow(_csv_safe(row[key]) for key in ("employee_code", "employee", "department", "date", "score", "evaluator"))
    return response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def report_export_pdf(request):
    if request.user.role not in TEAM_ROLES:
        return Response({"detail": "You do not have permission to export reports."}, status=403)
    queryset, error = _report_queryset(request)
    if error:
        return error
    lines = ["Lexie Performance Report", f"Generated: {date.today().isoformat()}"]
    lines.extend(["", "Employee ID | Employee | Department | Date | Score | Evaluator", "-" * 85])
    for row in _report_rows(queryset):
        score = f"{row['score']:.2f}" if row["score"] is not None else "N/A"
        lines.append(
            f"{row['employee_code']} | {row['employee']} | {row['department']} | "
            f"{row['date']} | {score} | {row['evaluator']}"
        )
    from .web_views import build_pdf

    response = HttpResponse(build_pdf(lines), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="lexie-performance-report.pdf"'
    return response


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def automation_settings(request):
    if not is_management(request.user):
        return Response({"detail": "Management access is required."}, status=403)
    instance = AutomationSettings.objects.get_or_create_default()
    if request.method == "GET":
        return Response(AutomationSettingsSerializer(instance).data)
    serializer = AutomationSettingsSerializer(instance, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    settings_row = serializer.save(updated_by=request.user)
    return Response(AutomationSettingsSerializer(settings_row).data)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def notification_composer(request):
    if request.user.role not in TEAM_ROLES:
        return Response({"detail": "You do not have permission to send notifications."}, status=403)
    if request.method == "GET":
        recipients = managed_user_queryset(request.user).filter(is_active=True).order_by("name")
        return Response([{"id": user.pk, "name": user.name, "email": user.email} for user in recipients])
    recipient_id = request.data.get("recipient_id")
    title = str(request.data.get("title", "")).strip()
    message = str(request.data.get("message", "")).strip()
    if not str(recipient_id or "").isdigit():
        return Response({"recipient_id": ["Select a recipient."]}, status=422)
    if not title or len(title) > 160 or not message:
        return Response({"detail": "A title (up to 160 characters) and message are required."}, status=422)
    recipient = managed_user_queryset(request.user).filter(
        pk=int(recipient_id), is_active=True
    ).first()
    if recipient is None:
        return Response({"detail": "Recipient not found or outside your team."}, status=404)
    notification = create_notification(
        recipient,
        title,
        message,
        Notification.Category.manual,
        sender=request.user,
    )
    return Response({"id": notification.pk}, status=201)


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def evaluation_criteria(request):
    if request.user.role not in VIEWER_ROLES:
        return Response({"detail": "You do not have permission to view evaluation criteria."}, status=403)
    settings_row, _ = EvaluationCriteriaSettings.objects.get_or_create(
        pk=1, defaults={"criteria": CRITERIA_DEFAULTS}
    )
    if request.method == "GET":
        return Response(settings_row.criteria)
    if not is_management(request.user):
        return Response({"detail": "Management access is required to edit criteria."}, status=403)
    criteria = request.data
    if not isinstance(criteria, dict) or set(criteria) != set(CRITERIA_DEFAULTS):
        return Response({"detail": "All five evaluation criteria are required."}, status=422)
    cleaned = {}
    for key in CRITERIA_DEFAULTS:
        value = criteria.get(key)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
            return Response({key: ["Enter a label up to 80 characters."]}, status=422)
        cleaned[key] = value.strip()
    if len({value.casefold() for value in cleaned.values()}) != len(cleaned):
        return Response({"detail": "Criterion labels must be unique."}, status=422)
    settings_row.criteria = cleaned
    settings_row.updated_by = request.user
    settings_row.save(update_fields=("criteria", "updated_by", "updated_at"))
    return Response(settings_row.criteria)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def hybrid_review(request, evaluation_id):
    evaluation = evaluation_queryset_for(request.user).filter(pk=evaluation_id).first()
    if evaluation is None:
        return Response({"detail": "Evaluation not found or outside your team."}, status=404)
    if request.user.role not in TEAM_ROLES:
        return Response({"detail": "You do not have permission to review evaluations."}, status=403)
    serializer = HybridReviewSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    values = serializer.validated_data
    if request.user.role == User.Role.team_leader:
        if "team_leader_rating" in values:
            evaluation.team_leader_rating = values["team_leader_rating"]
    elif is_management(request.user):
        if "management_rating" in values:
            evaluation.management_rating = values["management_rating"]
    if "comments" in values:
        evaluation.comments = values["comments"]
    evaluation.save(update_fields=("team_leader_rating", "management_rating", "comments"))
    score = update_hybrid_evaluation_score(evaluation)
    return Response({
        "id": evaluation.pk,
        "team_leader_rating": evaluation.team_leader_rating,
        "management_rating": evaluation.management_rating,
        "hybrid_score": evaluation.hybrid_score,
        "phase": evaluation.phase,
        "score_details": score,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def rerun_predictions(request):
    if request.user.role not in TEAM_ROLES:
        return Response({"detail": "Management or Team Leader access is required."}, status=403)
    employees = employee_queryset_for(request.user).filter(status="active").select_related(
        "department"
    )
    if employees.count() > 200:
        return Response({"detail": "Batch rerun is limited to 200 employees. Narrow the scope first."}, status=413)
    from .ml.predict import ModelUnavailableError, UnknownDepartmentError, predict_performance

    completed = []
    errors = []
    for employee in employees:
        evaluation = employee.evaluations.order_by("-date", "-id").first()
        if evaluation is None:
            errors.append({"employee_id": employee.pk, "detail": "No evaluation is available."})
            continue
        try:
            prediction = Prediction.objects.create(
                employee=employee,
                **predict_performance(employee, evaluation),
            )
        except (ModelUnavailableError, UnknownDepartmentError, ValueError) as error:
            errors.append({"employee_id": employee.pk, "detail": str(error)})
            continue
        except Exception:
            logger.exception("Batch prediction failed for employee_id=%s", employee.pk)
            errors.append({"employee_id": employee.pk, "detail": "Prediction failed unexpectedly."})
            continue
        completed.append(prediction)
    return Response({
        "completed": len(completed),
        "failed": len(errors),
        "errors": errors,
        "predictions": PredictionSerializer(completed, many=True).data,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def trusted_devices(request):
    devices = TrustedDevice.objects.select_related("user", "approved_by")
    if request.user.role in MANAGEMENT_ROLES or request.user.role == User.Role.team_leader:
        manageable = [
            item.pk for item in devices if can_manage_device(request.user, item)
        ]
        devices = devices.filter(user=request.user) | devices.filter(pk__in=manageable)
    else:
        devices = devices.filter(user=request.user)
    return Response([
        {
            "id": device.pk,
            "label": device.label,
            "status": device.status,
            "created_at": device.created_at,
            "last_used_at": device.last_used_at,
            "user_id": device.user_id,
            "user_name": device.user.name,
            "can_manage": device.user_id == request.user.pk or can_manage_device(request.user, device),
        }
        for device in devices.distinct()
    ])


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def trusted_device_action(request, device_id):
    device = TrustedDevice.objects.select_related("user").filter(pk=device_id).first()
    if device is None:
        return Response({"detail": "Device not found."}, status=404)
    own = device.user_id == request.user.pk
    if not own and not can_manage_device(request.user, device):
        return Response({"detail": "You do not have permission to manage this device."}, status=403)
    action = request.data.get("action")
    if own and action != "remove":
        return Response({"detail": "You may only revoke your own trusted device."}, status=403)
    if action not in {"approve", "reject", "remove"}:
        return Response({"action": ["Choose approve, reject, or remove."]}, status=422)
    if action == "approve" and device.user_id == request.user.pk:
        return Response({"detail": "You cannot approve your own device."}, status=403)
    from .web_views import _current_device_hash, _update_device_status

    if action == "remove" and device.token_hash == _current_device_hash(request):
        return Response({"detail": "You cannot revoke the device you are currently using."}, status=409)
    if not _update_device_status(device, action, request.user):
        return Response({"detail": "This device cannot be changed in its current state."}, status=409)
    return Response({"id": device.pk, "status": device.status})


@api_view(["GET", "POST", "DELETE"])
@permission_classes([IsAuthenticated])
def profile_photo(request):
    user = request.user
    if request.method == "POST":
        uploaded_file = request.FILES.get("photo")
        if uploaded_file is None:
            return Response({"photo": ["Choose an image to upload."]}, status=422)
        extension = "." + uploaded_file.name.rsplit(".", 1)[-1].lower() if "." in uploaded_file.name else ""
        if extension not in {".jpg", ".jpeg", ".png"}:
            return Response({"photo": ["Only JPG and PNG images are supported."]}, status=422)
        if uploaded_file.size > 2_000_000:
            return Response({"photo": ["Profile images must be 2 MB or smaller."]}, status=413)
        serializer = ProfilePhotoUploadSerializer(data={"photo": uploaded_file})
        if not serializer.is_valid():
            return Response(serializer.errors, status=422)
        uploaded_file = serializer.validated_data["photo"]
        try:
            from .blob_storage import (
                delete_profile_photo_blob,
                upload_profile_photo_blob,
            )

            new_path = upload_profile_photo_blob(user.pk, uploaded_file)
        except RuntimeError as error:
            logger.error("Profile photo storage is not configured: %s", error)
            return Response({"detail": "Profile photo storage is not configured."}, status=503)
        except Exception:
            logger.exception("Unable to upload profile photo for user_id=%s", user.pk)
            return Response({"detail": "Unable to store profile photo."}, status=502)
        if user.photo_blob_path:
            try:
                delete_profile_photo_blob(user.photo_blob_path)
            except Exception:
                logger.exception("Unable to replace prior profile photo for user_id=%s", user.pk)
                try:
                    delete_profile_photo_blob(new_path)
                except Exception:
                    logger.exception("Unable to remove unreferenced profile photo blob for user_id=%s", user.pk)
                return Response({"detail": "Unable to replace the existing profile photo."}, status=502)
        if user.photo:
            user.photo.delete(save=False)
        user.photo_blob_path = new_path
        user.save(update_fields=("photo_blob_path",))
        return Response({"photo_url": "/api/users/me/photo"})
    if request.method == "DELETE":
        if user.photo_blob_path:
            try:
                from .blob_storage import delete_profile_photo_blob

                delete_profile_photo_blob(user.photo_blob_path)
            except Exception:
                logger.exception("Unable to delete profile photo for user_id=%s", user.pk)
                return Response({"detail": "Unable to delete the stored profile photo."}, status=502)
        if user.photo:
            user.photo.delete(save=False)
        user.photo_blob_path = ""
        user.photo = ""
        user.save(update_fields=("photo_blob_path", "photo"))
        return Response(status=204)
    if user.photo_blob_path:
        try:
            result = get_submission_blob(user.photo_blob_path)
        except Exception:
            logger.exception("Unable to retrieve profile photo for user_id=%s", user.pk)
            return Response({"detail": "Unable to retrieve profile photo."}, status=502)
        response = HttpResponse(result.body, content_type=result.metadata.content_type or "image/jpeg")
    elif user.photo:
        filename = user.photo.name
        response = HttpResponse(
            user.photo.read(),
            content_type=mimetypes.guess_type(filename)[0] or "image/jpeg",
        )
    else:
        return Response(status=404)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@api_view(["GET"])
@permission_classes([AllowAny])
def monthly_evaluations_cron(request):
    configured_secret = os.environ.get("CRON_SECRET", "")
    authorization = request.headers.get("Authorization", "")
    supplied_secret = authorization.removeprefix("Bearer ").strip()
    if not configured_secret:
        return Response({"detail": "Cron authentication is not configured."}, status=503)
    if not supplied_secret or not hmac.compare_digest(supplied_secret, configured_secret):
        return Response({"detail": "Invalid cron authorization."}, status=403)
    from .automation import create_monthly_evaluations

    try:
        created = create_monthly_evaluations()
    except ValueError as error:
        logger.error("Monthly evaluation cron could not complete: %s", error)
        return Response({"detail": str(error)}, status=409)
    return Response({
        "created": len(created),
        "cycle_month": timezone.localdate().replace(day=1).isoformat(),
    })

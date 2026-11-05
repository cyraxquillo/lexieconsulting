from collections import defaultdict
from datetime import date
import logging
import mimetypes

from django.contrib.auth import authenticate
from django.http import Http404, HttpResponse
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils.http import content_disposition_header
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from .access import (
    MANAGEMENT_ROLES,
    TEAM_ROLES,
    VIEWER_ROLES,
    can_review_submission,
    employee_queryset_for,
    evaluation_queryset_for,
    is_management,
    is_staff_user,
    managed_user_queryset,
    prediction_queryset_for,
    submission_queryset_for,
)
from .blob_storage import (
    ALLOWED_SUBMISSION_EXTENSIONS,
    MAX_SUBMISSION_FILE_SIZE,
    get_submission_blob,
    sanitize_filename,
    upload_submission_blob,
)
from .models import Client, Department, Employee, Evaluation, Prediction, Submission, User
from .login_throttling import LoginRateThrottle
from .permissions import IsAdmin, IsAdminOrManager, IsClient, IsManagementOrTeamLeader
from .serializers import (
    ClientSerializer,
    DepartmentSerializer,
    EmployeeSerializer,
    EvaluationSerializer,
    NotificationPreferencesSerializer,
    PasswordUpdateSerializer,
    PredictionSerializer,
    SubmissionReviewSerializer,
    UserCreateSerializer,
    UserAccountCreateSerializer,
    UserAccountSerializer,
    UserAccountUpdateSerializer,
    UserProfileSerializer,
    UserSerializer,
)

logger = logging.getLogger(__name__)


def submission_data(submission):
    return {
        "id": submission.pk,
        "employee_id": submission.employee_id,
        "employee_name": submission.employee.name,
        "title": submission.title,
        "description": submission.description,
        "file_name": submission.file_name or submission.file.name.rsplit("/", 1)[-1],
        "file_content_type": submission.file_content_type,
        "download_url": f"/api/submissions/{submission.pk}/file",
        "uploaded_at": submission.uploaded_at.isoformat(),
        "status": submission.status,
        "reviewer_comment": submission.reviewer_comment,
        "is_late": submission.is_late,
    }


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def submissions(request):
    if request.method == "GET":
        if request.user.role not in VIEWER_ROLES:
            return role_denied()
        queryset = submission_queryset_for(request.user)
        return Response([
            submission_data(item)
            for item in queryset.order_by("-uploaded_at", "-pk")[:200]
        ])

    if not is_staff_user(request.user):
        return role_denied()
    try:
        employee = request.user.employee
    except Employee.DoesNotExist:
        return Response(
            {"detail": "Your account is not linked to an employee profile."},
            status=status.HTTP_409_CONFLICT,
        )
    uploaded_file = request.FILES.get("file")
    title = str(request.data.get("title", "")).strip()
    description = str(request.data.get("description", "")).strip()
    if not title or len(title) > 160:
        return Response({"title": ["Enter a title of 1 to 160 characters."]}, status=422)
    if uploaded_file is None:
        return Response({"file": ["Please select a file to upload."]}, status=422)
    extension = "." + uploaded_file.name.rsplit(".", 1)[-1].lower() if "." in uploaded_file.name else ""
    if extension not in ALLOWED_SUBMISSION_EXTENSIONS:
        return Response(
            {"file": ["Supported file types: PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, TXT, CSV, PNG, JPG."]},
            status=422,
        )
    if uploaded_file.size > MAX_SUBMISSION_FILE_SIZE:
        return Response({"file": ["The file must be 4 MB or smaller for Vercel serverless upload."]}, status=413)
    try:
        blob_path = upload_submission_blob(employee.pk, uploaded_file)
    except RuntimeError as error:
        logger.error("Submission storage is not configured: %s", error)
        return Response({"detail": "Submission file storage is not configured."}, status=503)
    except Exception:
        logger.exception("Unable to upload work submission to Vercel Blob.")
        return Response({"detail": "Unable to store the uploaded file. Please try again."}, status=502)

    submission = Submission.objects.create(
        employee=employee,
        title=title,
        description=description,
        file="",
        blob_path=blob_path,
        file_name=sanitize_filename(uploaded_file.name),
        file_content_type=uploaded_file.content_type or "application/octet-stream",
        status=Submission.Status.pending,
    )
    return Response(submission_data(submission), status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def submission_review(request, submission_id):
    submission = submission_queryset_for(request.user).filter(pk=submission_id).first()
    if submission is None or not can_review_submission(request.user, submission):
        return Response({"detail": "Submission not found or you do not have permission to review it."}, status=404)
    serializer = SubmissionReviewSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    submission.status = serializer.validated_data["status"]
    submission.reviewer_comment = serializer.validated_data.get("reviewer_comment", "").strip()
    submission.save(update_fields=("status", "reviewer_comment"))
    return Response(submission_data(submission))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def submission_file(request, submission_id):
    submission = submission_queryset_for(request.user).filter(pk=submission_id).first()
    if submission is None:
        raise Http404
    if submission.blob_path:
        try:
            result = get_submission_blob(submission.blob_path)
        except Exception:
            logger.exception("Unable to retrieve work submission %s from Vercel Blob.", submission.pk)
            return Response({"detail": "Unable to retrieve the submission file."}, status=502)
        content = result.body
        content_type = submission.file_content_type or result.metadata.content_type or "application/octet-stream"
        filename = submission.file_name or "submission"
    elif submission.file:
        content = submission.file.read()
        filename = submission.file.name.rsplit("/", 1)[-1]
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    else:
        raise Http404
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = content_disposition_header(True, filename)
    response["X-Content-Type-Options"] = "nosniff"
    return response


def parse_pagination(request):
    try:
        skip = int(request.query_params.get("skip", "0"))
        limit = int(request.query_params.get("limit", "50"))
    except ValueError:
        return None, None, Response({"detail": "skip and limit must be integers"}, status=422)
    if skip < 0 or not 1 <= limit <= 200:
        return None, None, Response({"detail": "skip must be non-negative and limit must be between 1 and 200"}, status=422)
    return skip, limit, None


def role_denied():
    return Response(
        {"detail": "You do not have permission to perform this action"},
        status=status.HTTP_403_FORBIDDEN,
    )


def user_has_role(user, *roles):
    return user.is_authenticated and user.role in roles


def find_employee(employee_id):
    queryset = Employee.objects.select_related("department", "user")
    if employee_id.isdigit():
        return queryset.filter(pk=int(employee_id)).first()
    return queryset.filter(employee_code=employee_id).first()


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def login_view(request):
    username = request.data.get("username", "")
    password = request.data.get("password", "")
    if not username or not password:
        return Response(
            {"detail": "Both username and password are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    user = authenticate(request, email=username.lower(), password=password)
    if user is None:
        return Response(
            {"detail": "Incorrect email or password"},
            status=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )
    refresh = RefreshToken.for_user(user)
    return Response({
        "access_token": str(refresh.access_token),
        "token_type": "bearer",
        "role": user.role,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def auth_me(request):
    return Response(UserSerializer(request.user).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def register_user(request):
    if request.user.role != User.Role.admin:
        return role_denied()
    serializer = UserCreateSerializer(data=request.data)
    if not serializer.is_valid():
        if "email" in serializer.errors and "An account with this email already exists" in str(serializer.errors["email"]):
            return Response({"detail": "An account with this email already exists"}, status=409)
        return Response(serializer.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    try:
        with transaction.atomic():
            user = serializer.save()
    except IntegrityError:
        return Response({"detail": "An account with this email already exists"}, status=409)
    return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


def account_queryset_for(actor):
    return managed_user_queryset(actor)


def account_roles_for(actor):
    if actor.role == User.Role.admin:
        return (
            User.Role.management,
            User.Role.team_leader,
            User.Role.staff,
        )
    return (User.Role.team_leader, User.Role.staff)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def user_accounts(request):
    if not is_management(request.user):
        return role_denied()
    if request.method == "GET":
        accounts = account_queryset_for(request.user).order_by("name", "pk")
        return Response(UserAccountSerializer(accounts, many=True).data)

    requested_role = request.data.get("role")
    if requested_role and requested_role not in account_roles_for(request.user):
        return role_denied()
    serializer = UserAccountCreateSerializer(
        data=request.data,
        allowed_roles=account_roles_for(request.user),
    )
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    try:
        with transaction.atomic():
            account = serializer.save()
    except IntegrityError:
        return Response(
            {"detail": "An account with this email already exists"},
            status=status.HTTP_409_CONFLICT,
        )
    return Response(
        UserAccountSerializer(account).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticated])
def user_account_detail(request, account_id):
    if not is_management(request.user):
        return role_denied()
    if account_id == request.user.pk:
        return role_denied()
    account = account_queryset_for(request.user).filter(pk=account_id).first()
    if account is None:
        if User.objects.filter(pk=account_id).exists():
            return role_denied()
        return Response({"detail": "Account not found"}, status=status.HTTP_404_NOT_FOUND)

    requested_role = request.data.get("role")
    if requested_role and requested_role not in account_roles_for(request.user):
        return role_denied()
    if request.data.get("is_active") is False and account_id == request.user.pk:
        return role_denied()
    serializer = UserAccountUpdateSerializer(
        account,
        data=request.data,
        partial=request.method == "PATCH",
        allowed_roles=account_roles_for(request.user),
    )
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    try:
        serializer.save()
    except IntegrityError:
        return Response(
            {"detail": "An account with this email already exists"},
            status=status.HTTP_409_CONFLICT,
        )
    return Response(UserAccountSerializer(account).data)


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def user_profile(request):
    user = request.user
    if request.method == "GET":
        return Response(UserProfileSerializer(user).data)
    serializer = UserProfileSerializer(user, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    try:
        serializer.save()
    except IntegrityError:
        return Response({"detail": "An account with this email already exists"}, status=409)
    return Response(serializer.data)


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def update_password(request):
    serializer = PasswordUpdateSerializer(data=request.data, context={"request": request})
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    if not request.user.check_password(serializer.validated_data["current_password"]):
        return Response({"detail": "Current password is incorrect"}, status=400)
    request.user.set_password(serializer.validated_data["new_password"])
    request.user.save(update_fields=("password",))
    return Response({"message": "Password updated successfully"})


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def notification_preferences(request):
    defaults = {"email_evaluations": True, "email_predictions": True, "weekly_summary": False}
    if request.method == "GET":
        return Response({**defaults, **(request.user.notification_preferences or {})})
    serializer = NotificationPreferencesSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    request.user.notification_preferences = serializer.validated_data
    request.user.save(update_fields=("notification_preferences",))
    return Response(serializer.validated_data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def departments(request):
    if not is_management(request.user):
        return role_denied()
    return Response(DepartmentSerializer(Department.objects.filter(is_active=True), many=True).data)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def employees(request):
    user = request.user
    if request.method == "GET":
        if user.role not in VIEWER_ROLES:
            return role_denied()
        queryset = employee_queryset_for(user)
        search = request.query_params.get("search")
        department = request.query_params.get("department")
        status_filter = request.query_params.get("status")
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search.strip())
                | Q(employee_code__icontains=search.strip())
                | Q(position__icontains=search.strip())
            )
        if department:
            queryset = queryset.filter(department__name__iexact=department.strip())
        if status_filter in {"active", "inactive"}:
            queryset = queryset.filter(status=status_filter)
        skip, limit, error = parse_pagination(request)
        if error:
            return error
        if status_filter is not None and status_filter not in {"active", "inactive"}:
            return Response({"detail": "status must be active or inactive"}, status=422)
        return Response(EmployeeSerializer(queryset.order_by("name")[skip:skip + limit], many=True).data)
    if not is_management(user):
        return role_denied()
    serializer = EmployeeSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    try:
        employee = serializer.save()
    except IntegrityError:
        return Response({"detail": "Employee code or linked staff account is already in use"}, status=409)
    return Response(EmployeeSerializer(employee).data, status=201)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def employee_detail(request, employee_id):
    queryset = employee_queryset_for(request.user)
    employee = (
        queryset.filter(pk=int(employee_id)).first()
        if employee_id.isdigit()
        else queryset.filter(employee_code=employee_id).first()
    )
    if employee is None:
        if employee_id.isdigit():
            exists = Employee.objects.filter(pk=int(employee_id)).exists()
        else:
            exists = Employee.objects.filter(employee_code=employee_id).exists()
        if exists:
            return Response({"detail": "You do not have permission to access this employee"}, status=403)
        return Response({"detail": "Employee not found"}, status=404)
    if request.method == "GET":
        if request.user.role not in VIEWER_ROLES:
            return role_denied()
        return Response(EmployeeSerializer(employee).data)
    if request.method == "DELETE" and not is_management(request.user):
        return role_denied()
    if request.method == "PUT" and request.user.role not in TEAM_ROLES:
        return role_denied()
    if request.method == "DELETE":
        employee.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    data = request.data
    if request.user.role == User.Role.team_leader:
        data = request.data.copy()
        data.pop("user_id", None)
        data.pop("team_leader_id", None)
        data.pop("department", None)
    serializer = EmployeeSerializer(employee, data=data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    try:
        employee = serializer.save()
    except IntegrityError:
        return Response({"detail": "Linked staff account is already assigned to another employee"}, status=409)
    return Response(EmployeeSerializer(employee).data)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def evaluations(request):
    user = request.user
    if request.method == "GET":
        if user.role not in VIEWER_ROLES:
            return role_denied()
        queryset = evaluation_queryset_for(user)
        employee_id = request.query_params.get("employee_id")
        if employee_id is not None:
            try:
                queryset = queryset.filter(employee_id=int(employee_id))
            except ValueError:
                return Response({"detail": "employee_id must be an integer"}, status=422)
        department_id = request.query_params.get("department")
        if department_id:
            if department_id.isdigit():
                queryset = queryset.filter(employee__department_id=int(department_id))
            else:
                queryset = queryset.filter(employee__department__name__iexact=department_id.strip())
        for field, lookup in (("start_date", "date__gte"), ("end_date", "date__lte")):
            value = request.query_params.get(field)
            if value:
                try:
                    parsed = date.fromisoformat(value)
                except ValueError:
                    return Response({field: ["Use YYYY-MM-DD format."]}, status=422)
                queryset = queryset.filter(**{lookup: parsed})
        if request.query_params.get("status"):
            queryset = queryset.filter(status=request.query_params["status"])
        if request.query_params.get("phase"):
            queryset = queryset.filter(phase=request.query_params["phase"])
        skip, limit, error = parse_pagination(request)
        if error:
            return error
        return Response(EvaluationSerializer(queryset.order_by("-date", "-id")[skip:skip + limit], many=True).data)
    if user.role not in TEAM_ROLES:
        return role_denied()
    employee_id = request.data.get("employee_id")
    if not str(employee_id or "").isdigit() or not employee_queryset_for(user).filter(pk=int(employee_id)).exists():
        return Response({"detail": "Employee not found"}, status=404)
    serializer = EvaluationSerializer(data=request.data, context={"request": request})
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    evaluation = serializer.save()
    return Response(EvaluationSerializer(evaluation).data, status=201)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def evaluation_detail(request, evaluation_id):
    evaluation = evaluation_queryset_for(request.user).filter(pk=evaluation_id).first()
    if evaluation is None:
        if Evaluation.objects.filter(pk=evaluation_id).exists():
            return Response({"detail": "You do not have permission to access this evaluation"}, status=403)
        return Response({"detail": "Evaluation not found"}, status=404)
    if request.method == "GET":
        if request.user.role not in VIEWER_ROLES:
            return role_denied()
        return Response(EvaluationSerializer(evaluation).data)
    if request.method == "DELETE" and not is_management(request.user):
        return role_denied()
    if request.method == "PUT" and request.user.role not in TEAM_ROLES:
        return role_denied()
    if request.method == "DELETE":
        evaluation.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = EvaluationSerializer(evaluation, data=request.data, partial=True, context={"request": request})
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    evaluation = serializer.save()
    return Response(EvaluationSerializer(evaluation).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def predictions(request):
    user = request.user
    if user.role not in VIEWER_ROLES:
        return role_denied()
    queryset = prediction_queryset_for(user)
    employee_id = request.query_params.get("employee_id")
    if employee_id is not None:
        try:
            queryset = queryset.filter(employee_id=int(employee_id))
        except ValueError:
            return Response({"detail": "employee_id must be an integer"}, status=422)
    department_id = request.query_params.get("department")
    if department_id:
        if department_id.isdigit():
            queryset = queryset.filter(employee__department_id=int(department_id))
        else:
            queryset = queryset.filter(employee__department__name__iexact=department_id.strip())
    for field, lookup in (("start_date", "created_at__date__gte"), ("end_date", "created_at__date__lte")):
        value = request.query_params.get(field)
        if value:
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                return Response({field: ["Use YYYY-MM-DD format."]}, status=422)
            queryset = queryset.filter(**{lookup: parsed})
    skip, limit, error = parse_pagination(request)
    if error:
        return error
    return Response(PredictionSerializer(queryset.order_by("-created_at", "-id")[skip:skip + limit], many=True).data)


@api_view(["POST"])
@permission_classes([IsManagementOrTeamLeader])
def create_prediction(request, employee_id):
    queryset = employee_queryset_for(request.user)
    employee = (
        queryset.filter(pk=int(employee_id)).first()
        if employee_id.isdigit()
        else queryset.filter(employee_code=employee_id).first()
    )
    if employee is None:
        return Response({"detail": "Employee not found"}, status=404)
    evaluation = employee.evaluations.order_by("-date", "-id").first()
    if evaluation is None:
        return Response({"detail": "Cannot predict performance without an evaluation"}, status=422)
    try:
        from .ml import ModelUnavailableError, UnknownDepartmentError, predict_performance

        result = predict_performance(employee, evaluation)
    except ModelUnavailableError as error:
        return Response({"detail": str(error)}, status=503)
    except UnknownDepartmentError as error:
        return Response({"detail": str(error)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    except ValueError as error:
        return Response({"detail": str(error)}, status=422)
    prediction = Prediction.objects.create(employee=employee, **result)
    logger.info(
        "Created prediction for employee_id=%s model_source=%s model_version=%s category=%s confidence=%s",
        employee.pk,
        prediction.model_source,
        prediction.model_version,
        prediction.predicted_category,
        prediction.confidence_score,
    )
    return Response(PredictionSerializer(prediction).data, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def report_summary(request):
    if request.user.role not in TEAM_ROLES:
        return role_denied()
    active_employees = employee_queryset_for(request.user).filter(status="active")
    evaluations = evaluation_queryset_for(request.user).order_by("-date", "-id")
    latest_by_employee = {}
    for evaluation in evaluations:
        latest_by_employee.setdefault(evaluation.employee_id, evaluation)
    scores_by_department = defaultdict(list)
    latest_scores = []
    at_risk_count = 0
    for employee in active_employees:
        evaluation = latest_by_employee.get(employee.id)
        if evaluation is None:
            continue
        score = evaluation.overall_score
        latest_scores.append(score)
        scores_by_department[employee.department.name].append(score)
        at_risk_count += score < 70
    department_breakdown = [
        {"name": name, "score": round(sum(scores) / len(scores), 2), "employee_count": len(scores)}
        for name, scores in sorted(scores_by_department.items())
    ]
    return Response({
        "average_score": round(sum(latest_scores) / len(latest_scores), 2) if latest_scores else 0,
        "at_risk_count": at_risk_count,
        "active_employee_count": active_employees.count(),
        "department_breakdown": department_breakdown,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def performance_trend(request):
    if request.user.role not in TEAM_ROLES:
        return role_denied()
    month_scores = defaultdict(list)
    for evaluation in evaluation_queryset_for(request.user).order_by("date"):
        month_scores[evaluation.date.strftime("%Y-%m")].append(evaluation.overall_score)
    return Response([
        {"month": month, "score": round(sum(scores) / len(scores), 2)}
        for month, scores in sorted(month_scores.items())
    ])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def clients(request):
    if not user_has_role(request.user, User.Role.admin):
        return role_denied()
    return Response(ClientSerializer(Client.objects.select_related("user").all(), many=True).data)


@api_view(["GET"])
@permission_classes([IsClient])
def my_client(request):
    client = Client.objects.filter(user=request.user).first()
    if client is None:
        return Response({"detail": "Client account record not found"}, status=404)
    return Response(ClientSerializer(client).data)

from django.core.exceptions import ValidationError as DjangoValidationError
from datetime import time
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .access import (
    ATTENDANCE_ROLES,
    MANAGEMENT_ROLES,
    STAFF_ROLES,
    attendance_visible_users,
    get_managed_users,
    is_management,
)
from .attendance_locations import (
    OutsidePremisesBlocked,
    apply_attendance_location,
    create_outside_attendance_notifications,
)
from .breaks import (
    auto_end_overdue_breaks,
    break_auto_end_at,
    break_seconds_for_attendance,
    end_active_break_for_attendance,
    manila_localtime,
    net_attendance_seconds,
)
from .hybrid import update_employee_attendance_rate
from .models import (
    AttendanceBreak,
    AttendanceCorrection,
    AttendanceCorrectionRequest,
    AttendanceRecord,
    AutomationSettings,
    Employee,
    OfficeLocation,
    User,
)
from .serializers import AttendanceCorrectionRequestSerializer, OfficeLocationSerializer
from .views_hybrid import _record_status


def _attendance_record_data(record):
    user = record.account
    employee = record.employee
    team_leader = employee.team_leader if employee else None
    now = timezone.now()
    breaks = list(record.breaks.all())
    active_break = next((item for item in breaks if item.ended_at is None), None)
    break_seconds = break_seconds_for_attendance(record, now)
    return {
        "id": record.pk,
        "user_id": user.pk if user else None,
        "user_name": record.display_name,
        "role": user.get_role_display() if user else "",
        "team": team_leader.name if team_leader else "",
        "work_date": record.work_date.isoformat(),
        "clock_in_at": record.clock_in_at.isoformat() if record.clock_in_at else None,
        "clock_out_at": record.clock_out_at.isoformat() if record.clock_out_at else None,
        "status": record.attendance_status,
        "status_code": record.status,
        "time_in_status": (
            "Late"
            if record.clock_in_at and manila_localtime(record.clock_in_at).time().replace(tzinfo=None) > time(8, 0)
            else "On time" if record.clock_in_at else "—"
        ),
        "time_out_status": (
            dict(AttendanceRecord.TimeOutStatus.choices).get(record.time_out_status, "—")
        ),
        "break_minutes": int(break_seconds // 60),
        "break_seconds": int(break_seconds),
        "active_break": (
            {
                "id": active_break.pk,
                "started_at": active_break.started_at.isoformat(),
                "auto_end_at": active_break.auto_end_at.isoformat(),
                "auto_ended": active_break.auto_ended,
            }
            if active_break else None
        ),
        "breaks": [
            {
                "started_at": item.started_at.isoformat(),
                "ended_at": item.ended_at.isoformat() if item.ended_at else None,
                "auto_end_at": item.auto_end_at.isoformat(),
                "auto_ended": item.auto_ended,
            }
            for item in breaks
        ],
        "latitude": record.latitude,
        "longitude": record.longitude,
        "accuracy": record.accuracy,
        "distance_from_office": record.distance_from_office,
        "is_outside_premises": record.is_outside_premises,
        "outside_premises_reason": record.outside_premises_reason,
    }


def _attendance_record_for_user(user, work_date, *, lock=False):
    queryset = AttendanceRecord.objects.filter(
        Q(user=user) | Q(employee__user=user),
        work_date=work_date,
    )
    if lock:
        queryset = queryset.select_for_update()
    return queryset.select_related("employee", "employee__team_leader", "user").first()


def _employee_for_user(user):
    if user.role in STAFF_ROLES:
        return get_object_or_404(Employee.objects.select_related("team_leader"), user=user)
    return None


def _time_error(error):
    message = error.messages[0] if hasattr(error, "messages") else str(error)
    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def attendance_records(request):
    if request.user.role not in (*ATTENDANCE_ROLES, User.Role.admin):
        return Response({"detail": "You do not have permission"}, status=403)
    if request.user.role in STAFF_ROLES:
        auto_end_overdue_breaks(
            now=timezone.now(),
            employee_user=request.user,
        )
    records = AttendanceRecord.objects.all()
    if request.user.role != User.Role.admin:
        visible_users = attendance_visible_users(request.user)
        records = records.filter(
            Q(user__in=visible_users) | Q(employee__user__in=visible_users)
        )
    records = (
        records.select_related("employee", "employee__user", "employee__team_leader", "user")
        .prefetch_related("breaks")
        .distinct()
        .order_by("-work_date", "-pk")[:200]
    )
    return Response([_attendance_record_data(record) for record in records])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def attendance_roster(request):
    actor = request.user
    if actor.role not in (*ATTENDANCE_ROLES, User.Role.admin):
        return Response({"detail": "You do not have permission"}, status=403)

    now = timezone.now()
    auto_end_overdue_breaks(now=now)
    local_now = manila_localtime(now)
    work_date = local_now.date()
    settings = AutomationSettings.objects.get_or_create_default()
    if actor.role == User.Role.admin:
        roster = User.objects.filter(role__in=ATTENDANCE_ROLES, is_active=True)
    elif actor.role in MANAGEMENT_ROLES:
        roster = get_managed_users(actor).filter(is_active=True)
    elif actor.role == User.Role.team_leader:
        roster = get_managed_users(actor).filter(is_active=True)
    else:
        roster = User.objects.filter(pk=actor.pk, is_active=True)

    records = AttendanceRecord.objects.filter(
        work_date=work_date,
    ).filter(Q(user__in=roster) | Q(employee__user__in=roster)).select_related(
        "user", "employee", "employee__user", "employee__team_leader"
    ).prefetch_related("breaks")
    record_by_user = {}
    for record in records.distinct():
        account = record.account
        if account:
            record_by_user[account.pk] = record

    results = []
    for account in roster.select_related("employee").order_by("name"):
        try:
            employee = account.employee
        except Employee.DoesNotExist:
            employee = None
        if employee and (
            employee.status != Employee.Status.active or employee.date_hired > work_date
        ):
            continue
        record = record_by_user.get(account.pk)
        if record:
            data = _attendance_record_data(record)
            data["status"] = (
                "Late"
                if record.clock_in_at
                and manila_localtime(record.clock_in_at).time().replace(tzinfo=None)
                > time(8, 0)
                else record.attendance_status if record.status in (
                    AttendanceRecord.Status.absent,
                    AttendanceRecord.Status.excused,
                ) else "On time"
            )
            data["time_in_status"] = (
                "Late" if data["status"] == "Late"
                else "On time" if record.clock_in_at else "—"
            )
        else:
            data = {
                "id": None,
                "user_id": account.pk,
                "user_name": account.name,
                "role": account.get_role_display(),
                "team": employee.team_leader.name if employee and employee.team_leader_id else "",
                "work_date": work_date.isoformat(),
                "clock_in_at": None,
                "clock_out_at": None,
                "status": "Absent" if local_now.time().replace(tzinfo=None) >= settings.attendance_time_in_open_time else "Not started",
                "status_code": (
                    AttendanceRecord.Status.absent
                    if local_now.time().replace(tzinfo=None) >= settings.attendance_time_in_open_time
                    else ""
                ),
                "time_in_status": "—",
                "time_out_status": "—",
                "break_minutes": 0,
                "break_seconds": 0,
                "active_break": None,
                "breaks": [],
                "latitude": None,
                "longitude": None,
                "accuracy": None,
                "distance_from_office": None,
                "is_outside_premises": None,
                "outside_premises_reason": "",
            }
        results.append(data)
    return Response(results)


def _correction_request_data(item):
    record = item.record
    return {
        "id": item.pk,
        "record_id": record.pk,
        "user_id": record.account.pk if record.account else None,
        "user_name": record.display_name,
        "work_date": record.work_date.isoformat(),
        "requested_by": item.requested_by.name,
        "requested_status": item.requested_status,
        "change_status": item.change_status,
        "requested_clock_in_at": item.requested_clock_in_at.isoformat() if item.requested_clock_in_at else None,
        "requested_clock_out_at": item.requested_clock_out_at.isoformat() if item.requested_clock_out_at else None,
        "change_clock_in": item.change_clock_in,
        "change_clock_out": item.change_clock_out,
        "reason": item.reason,
        "status": item.status,
        "review_note": item.review_note,
        "created_at": item.created_at.isoformat(),
        "reviewed_by": item.reviewed_by.name if item.reviewed_by_id else None,
        "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
    }


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def attendance_correction_requests(request):
    actor = request.user
    if request.method == "GET":
        if actor.role in MANAGEMENT_ROLES:
            records = AttendanceCorrectionRequest.objects.filter(
                record__employee__user__in=get_managed_users(actor)
            )
        elif actor.role == User.Role.team_leader:
            records = AttendanceCorrectionRequest.objects.filter(
                record__employee__team_leader=actor
            )
        elif actor.role in STAFF_ROLES:
            records = AttendanceCorrectionRequest.objects.filter(
                requested_by=actor
            )
        else:
            return Response({"detail": "You do not have permission"}, status=403)
        return Response([
            _correction_request_data(item)
            for item in records.select_related(
                "record", "record__employee", "record__user", "requested_by", "reviewed_by"
            )[:200]
        ])

    if actor.role not in STAFF_ROLES:
        return Response({"detail": "Only employees can request an attendance correction."}, status=403)
    serializer = AttendanceCorrectionRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    payload = dict(serializer.validated_data)
    payload.pop("record_id")
    with transaction.atomic():
        record = AttendanceRecord.objects.select_for_update().select_related(
            "employee", "user"
        ).filter(
            pk=serializer.validated_data["record_id"],
            work_date__lte=manila_localtime().date(),
        ).filter(Q(user=actor) | Q(employee__user=actor)).first()
        if record is None:
            return Response({"detail": "Attendance record not found."}, status=404)
        if AttendanceCorrectionRequest.objects.filter(
            record=record, status=AttendanceCorrectionRequest.Status.pending
        ).exists():
            return Response({"detail": "A correction request is already pending for this record."}, status=409)
        item = AttendanceCorrectionRequest.objects.create(
            record=record,
            requested_by=actor,
            **payload,
        )
    return Response(_correction_request_data(item), status=201)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def attendance_correction_review(request, request_id):
    actor = request.user
    if not (is_management(actor) or actor.role == User.Role.team_leader):
        return Response({"detail": "Only Management or the assigned Team Leader can review requests."}, status=403)
    decision = request.data.get("status")
    if decision not in (AttendanceCorrectionRequest.Status.approved, AttendanceCorrectionRequest.Status.rejected):
        return Response({"detail": "Status must be approved or rejected."}, status=422)
    note = str(request.data.get("review_note", "")).strip()
    if decision == AttendanceCorrectionRequest.Status.rejected and not note:
        return Response({"detail": "A reason is required when rejecting a request."}, status=422)

    with transaction.atomic():
        item = AttendanceCorrectionRequest.objects.select_for_update().select_related(
            "record", "record__employee", "record__user"
        ).filter(pk=request_id).first()
        if item is None:
            return Response({"detail": "Correction request not found."}, status=404)
        record = item.record
        managed = (
            actor.role == User.Role.admin
            or actor.role in (User.Role.management, User.Role.hr_manager)
            and get_managed_users(actor).filter(pk=item.requested_by_id).exists()
            or actor.role == User.Role.team_leader
            and record.employee_id is not None
            and record.employee.team_leader_id == actor.pk
        )
        if not managed:
            return Response({"detail": "You do not have permission to review this request."}, status=403)
        if item.status != AttendanceCorrectionRequest.Status.pending:
            return Response({"detail": "This request has already been reviewed."}, status=409)
        if decision == AttendanceCorrectionRequest.Status.approved:
            corrected_clock_in = (
                item.requested_clock_in_at
                if item.change_clock_in else record.clock_in_at
            )
            corrected_clock_out = (
                item.requested_clock_out_at
                if item.change_clock_out else record.clock_out_at
            )
            if (
                corrected_clock_in
                and corrected_clock_out
                and corrected_clock_out <= corrected_clock_in
            ):
                return Response(
                    {"detail": "Time Out must be later than Time In."},
                    status=422,
                )
            old_status = record.status
            old_clock_in_at = record.clock_in_at
            old_clock_out_at = record.clock_out_at
            old_time_out_status = record.time_out_status
            if item.change_status:
                record.status = item.requested_status
            if item.change_clock_in:
                record.clock_in_at = item.requested_clock_in_at
            if item.change_clock_out:
                record.clock_out_at = item.requested_clock_out_at
            if item.change_status and record.status in (AttendanceRecord.Status.present, AttendanceRecord.Status.late):
                record.time_in_status = "late" if record.status == AttendanceRecord.Status.late else "on_time"
            elif item.change_status and record.status == AttendanceRecord.Status.early_out:
                record.time_out_status = AttendanceRecord.TimeOutStatus.early_out
            elif item.change_status and record.status == AttendanceRecord.Status.no_time_out:
                record.time_out_status = AttendanceRecord.TimeOutStatus.no_time_out
            elif item.change_status and record.status == AttendanceRecord.Status.absent:
                record.time_in_status = ""
                record.time_out_status = AttendanceRecord.TimeOutStatus.no_time_out
            if record.clock_in_at and record.clock_out_at and record.clock_out_at > record.clock_in_at:
                record.hours = round(net_attendance_seconds(record, record.clock_out_at) / 3600, 2)
            else:
                record.hours = None
            record.correction_reason = item.reason
            record.corrected_at = timezone.now()
            record.corrected_by = actor
            record.save()
            AttendanceCorrection.objects.create(
                record=record,
                changed_by=actor,
                old_status=old_status,
                new_status=record.status,
                old_clock_in_at=old_clock_in_at,
                new_clock_in_at=record.clock_in_at,
                old_clock_out_at=old_clock_out_at,
                new_clock_out_at=record.clock_out_at,
                old_time_out_status=old_time_out_status,
                new_time_out_status=record.time_out_status,
                reason=item.reason,
            )
            if record.employee_id:
                settings = AutomationSettings.objects.get_or_create_default()
                update_employee_attendance_rate(
                    record.employee,
                    settings,
                    record.work_date.replace(day=1),
                )
        item.status = decision
        item.review_note = note
        item.reviewed_by = actor
        item.reviewed_at = timezone.now()
        item.save(update_fields=("status", "review_note", "reviewed_by", "reviewed_at"))
    return Response(_correction_request_data(item))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def attendance_break_start(request):
    actor = request.user
    if actor.role not in STAFF_ROLES:
        return Response({"detail": "Only employees can start a break."}, status=403)
    now = timezone.now()
    auto_end_overdue_breaks(now=now, employee_user=actor)
    work_date = manila_localtime(now).date()
    settings = AutomationSettings.objects.get_or_create_default()
    if not settings.attendance_break_start_time <= manila_localtime(now).time().replace(tzinfo=None) < settings.attendance_break_end_time:
        return Response({"detail": "Break is not available at this time."}, status=400)
    with transaction.atomic():
        record = _attendance_record_for_user(actor, work_date, lock=True)
        if record is None or record.clock_in_at is None:
            return Response({"detail": "Time In before starting a break."}, status=400)
        if record.clock_out_at is not None:
            return Response({"detail": "A break cannot be started after Time Out."}, status=400)
        if AttendanceBreak.objects.filter(attendance=record, ended_at__isnull=True).exists():
            return Response({"detail": "You already have an active break."}, status=409)
        attendance_break = AttendanceBreak.objects.create(
            attendance=record,
            started_at=now,
            auto_end_at=break_auto_end_at(work_date, settings.attendance_break_end_time),
        )
    return Response({
        "id": attendance_break.pk,
        "started_at": attendance_break.started_at.isoformat(),
        "auto_end_at": attendance_break.auto_end_at.isoformat(),
        "auto_ended": attendance_break.auto_ended,
    }, status=201)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def attendance_break_end(request):
    actor = request.user
    if actor.role not in STAFF_ROLES:
        return Response({"detail": "Only employees can end a break."}, status=403)
    now = timezone.now()
    auto_end_overdue_breaks(now=now, employee_user=actor)
    work_date = manila_localtime(now).date()
    with transaction.atomic():
        record = _attendance_record_for_user(actor, work_date, lock=True)
        if record is None:
            return Response({"detail": "There is no active break to end."}, status=404)
        attendance_break = AttendanceBreak.objects.select_for_update().filter(
            attendance=record, ended_at__isnull=True
        ).first()
        if attendance_break is None:
            return Response({"detail": "There is no active break to end."}, status=404)
        attendance_break.ended_at = now
        attendance_break.save(update_fields=("ended_at",))
    return Response({
        "id": attendance_break.pk,
        "started_at": attendance_break.started_at.isoformat(),
        "ended_at": attendance_break.ended_at.isoformat(),
        "auto_end_at": attendance_break.auto_end_at.isoformat(),
        "auto_ended": attendance_break.auto_ended,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def attendance_time_in(request):
    user = request.user
    if user.role not in ATTENDANCE_ROLES:
        return Response({"detail": "You do not have permission"}, status=403)
    employee = _employee_for_user(user)
    now = timezone.now()
    local_now = manila_localtime(now)
    work_date = local_now.date()
    settings = AutomationSettings.objects.get_or_create_default()
    if local_now.time().replace(tzinfo=None) < settings.attendance_time_in_open_time:
        return Response(
            {"detail": "Time In is not open yet."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        with transaction.atomic():
            record = _attendance_record_for_user(user, work_date, lock=True)
            if record and record.clock_in_at:
                return Response(
                    {"detail": "You have already timed in today."},
                    status=status.HTTP_409_CONFLICT,
                )
            if record is None:
                record = AttendanceRecord(
                    employee=employee,
                    user=user,
                    work_date=work_date,
                )
            else:
                record.user = user
            outside = apply_attendance_location(
                record,
                user,
                request.data,
                require_reason=True,
            )
            record.clock_in_at = now
            record.status, record.minutes_late = _record_status(
                record.clock_in_at,
                settings,
            )
            record.time_in_status = (
                "late" if record.status == AttendanceRecord.Status.late else "on_time"
            )
            record.time_out_status = ""
            record.save()
            if employee is not None:
                update_employee_attendance_rate(
                    employee,
                    settings,
                    work_date.replace(day=1),
                )
            if outside:
                create_outside_attendance_notifications(user, record)
    except OutsidePremisesBlocked as error:
        create_outside_attendance_notifications(
            user,
            error.record,
            attempt_at=now,
            blocked=True,
        )
        return Response(
            {
                "detail": error.messages[0],
                "is_outside_premises": True,
                "distance_from_office": error.record.distance_from_office,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    except DjangoValidationError as error:
        return _time_error(error)
    return Response(_attendance_record_data(record), status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def attendance_time_out(request):
    user = request.user
    if user.role not in ATTENDANCE_ROLES:
        return Response({"detail": "You do not have permission"}, status=403)
    employee = _employee_for_user(user)
    now = timezone.now()
    auto_end_overdue_breaks(now=now, employee_user=user)
    work_date = manila_localtime(now).date()
    settings = AutomationSettings.objects.get_or_create_default()
    try:
        with transaction.atomic():
            record = _attendance_record_for_user(user, work_date, lock=True)
            if record is None or record.clock_in_at is None:
                return Response(
                    {"detail": "Time In before recording Time Out."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if record.clock_out_at is not None:
                return Response(
                    {"detail": "You have already timed out today."},
                    status=status.HTTP_409_CONFLICT,
                )
            apply_attendance_location(
                record,
                user,
                request.data,
                require_reason=False,
            )
            record.user = user
            end_active_break_for_attendance(record, now)
            record.clock_out_at = now
            if now > record.clock_in_at:
                record.hours = round(net_attendance_seconds(record, now) / 3600, 2)
            local_clock_out = manila_localtime(now)
            if local_clock_out.time().replace(tzinfo=None) < settings.attendance_expected_time_out:
                record.status = AttendanceRecord.Status.early_out
                record.time_out_status = AttendanceRecord.TimeOutStatus.early_out
            else:
                if record.status == AttendanceRecord.Status.early_out:
                    record.status = (
                        AttendanceRecord.Status.late
                        if record.time_in_status == "late"
                        else AttendanceRecord.Status.present
                    )
                record.time_out_status = AttendanceRecord.TimeOutStatus.on_time
            record.save()
            if employee is not None:
                update_employee_attendance_rate(
                    employee,
                    settings,
                    work_date.replace(day=1),
                )
    except DjangoValidationError as error:
        return _time_error(error)
    return Response(_attendance_record_data(record))


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def office_locations(request):
    if request.user.role != User.Role.admin:
        return Response({"detail": "Admin access is required"}, status=403)
    if request.method == "GET":
        rows = OfficeLocation.objects.all()
        return Response(OfficeLocationSerializer(rows, many=True).data)
    serializer = OfficeLocationSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    office = serializer.save()
    return Response(OfficeLocationSerializer(office).data, status=status.HTTP_201_CREATED)


@api_view(["PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def office_location_detail(request, pk):
    if request.user.role != User.Role.admin:
        return Response({"detail": "Admin access is required"}, status=403)
    office = get_object_or_404(OfficeLocation, pk=pk)
    if request.method == "DELETE":
        office.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = OfficeLocationSerializer(
        office,
        data=request.data,
        partial=request.method == "PATCH",
    )
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    serializer.save()
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notifications(request):
    queryset = request.user.notifications.all()
    return Response(
        {
            "unread_count": queryset.filter(read_at__isnull=True).count(),
            "results": [
                {
                    "id": item.pk,
                    "title": item.title,
                    "message": item.message,
                    "created_at": item.created_at.isoformat(),
                    "read_at": item.read_at.isoformat() if item.read_at else None,
                }
                for item in queryset[:100]
            ],
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notification_mark_read(request, pk):
    notification = get_object_or_404(
        request.user.notifications.all(),
        pk=pk,
    )
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=("read_at",))
    return Response({"id": notification.pk, "read": True})

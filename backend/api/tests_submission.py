from datetime import date
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .hybrid import calculate_work_score
from .models import Department, Employee, Submission, User


@override_settings(MEDIA_ROOT="/tmp/lexie-submission-tests")
class SubmissionFeatureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="admin@lexie.ph",
            password="Admin123!",
            name="Admin User",
            role=User.Role.admin,
            is_staff=True,
            is_superuser=True,
        )
        cls.employee = User.objects.create_user(
            email="employee@lexie.ph",
            password="Employee123!",
            name="Employee User",
            role=User.Role.employee,
        )
        cls.other_employee = User.objects.create_user(
            email="other@lexie.ph",
            password="Employee123!",
            name="Other Employee",
            role=User.Role.employee,
        )
        department = Department.objects.create(name="Operations")
        cls.employee_profile = Employee.objects.create(
            employee_code="EMP-1001",
            user=cls.employee,
            name="Employee User",
            department=department,
            position="Analyst",
            date_hired=date(2024, 1, 1),
        )
        cls.other_profile = Employee.objects.create(
            employee_code="EMP-1002",
            user=cls.other_employee,
            name="Other Employee",
            department=department,
            position="Coordinator",
            date_hired=date(2024, 2, 1),
        )

    def client_for(self, user):
        client = self.client
        client.force_login(user=user)
        return client

    def test_employee_can_upload_and_only_see_own_submission(self):
        client = self.client_for(self.employee)
        with patch("api.blob_storage.upload_submission_blob", return_value="submissions/1/report.pdf"):
            response = client.post(
                reverse("submission-create"),
                {
                    "title": "Monthly report",
                    "description": "Report for this month",
                    "file": SimpleUploadedFile(
                        "report.pdf",
                        b"pdf-data",
                        content_type="application/pdf",
                    ),
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(Submission.objects.get().employee, self.employee_profile)

        response = client.get(reverse("submission-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Monthly report")
        self.assertNotContains(response, "Other Employee")

    def test_manager_can_review_submission_and_comment(self):
        submission = Submission.objects.create(
            employee=self.employee_profile,
            title="Draft",
            description="Needs review",
            file=SimpleUploadedFile("draft.pdf", b"pdf-data"),
        )
        client = self.client_for(self.admin)
        response = client.post(
            reverse("submission-review", args=[submission.pk]),
            {"status": "reviewed", "reviewer_comment": "Approved"},
        )
        self.assertEqual(response.status_code, 302)
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.reviewed)
        self.assertEqual(submission.reviewer_comment, "Approved")

        response = client.get(reverse("submission-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft")

    def test_manager_submission_page_hides_upload_action_and_uses_admin_heading(self):
        client = self.client_for(self.admin)
        response = client.get(reverse("submission-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review queue")
        self.assertNotContains(response, "Upload work")

    def test_non_employee_redirects_with_styled_authorization_message(self):
        client = self.client_for(self.admin)
        response = client.get(reverse("submission-create"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse("submission-list"))
        self.assertContains(response, "Only employees can upload work submissions.")
        self.assertContains(response, "message-error")

    def test_invalid_file_type_and_size_are_rejected(self):
        client = self.client_for(self.employee)
        response = client.post(
            reverse("submission-create"),
            {
                "title": "Invalid file",
                "description": "Not allowed",
                "file": SimpleUploadedFile(
                    "script.exe",
                    b"not-a-supported-file",
                    content_type="application/x-msdownload",
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PDF, DOC/DOCX, XLS/XLSX")
        self.assertFalse(Submission.objects.exists())

        oversized = SimpleUploadedFile(
            "large.pdf",
            b"x" * 4_000_001,
            content_type="application/pdf",
        )
        response = client.post(
            reverse("submission-create"),
            {
                "title": "Oversized file",
                "description": "Too large",
                "file": oversized,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "4 MB")
        self.assertFalse(Submission.objects.exists())


class SubmissionApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        department = Department.objects.create(name="Submission API")
        cls.employee_user = User.objects.create_user(
            email="submission-staff@example.test",
            password="StaffPass123",
            name="Submission Staff",
            role=User.Role.staff,
        )
        cls.employee = Employee.objects.create(
            employee_code="SUB-001",
            user=cls.employee_user,
            name="Submission Staff",
            department=department,
            position="Analyst",
            date_hired=date(2024, 1, 1),
        )
        cls.team_leader = User.objects.create_user(
            email="submission-leader@example.test",
            password="LeaderPass123",
            name="Submission Team Leader",
            role=User.Role.team_leader,
        )
        cls.employee.team_leader = cls.team_leader
        cls.employee.save(update_fields=("team_leader",))
        cls.management = User.objects.create_user(
            email="submission-manager@example.test",
            password="ManagerPass123",
            name="Submission Manager",
            role=User.Role.management,
        )

    def test_staff_upload_is_scoped_and_approved_work_counts_toward_points(self):
        self.client.force_authenticate(self.employee_user)
        with patch(
            "api.views.upload_submission_blob",
            return_value="submissions/1/work.pdf",
        ):
            response = self.client.post(
                "/api/submissions",
                {
                    "title": "Monthly work",
                    "description": "Report",
                    "file": SimpleUploadedFile("work.pdf", b"pdf-data", content_type="application/pdf"),
                },
                format="multipart",
            )
        self.assertEqual(response.status_code, 201)
        submission = Submission.objects.get()
        self.assertEqual(submission.blob_path, "submissions/1/work.pdf")
        self.assertEqual(submission.status, Submission.Status.pending)

        rejected = self.client.patch(
            f"/api/submissions/{submission.pk}",
            {"status": "reviewed", "reviewer_comment": "Not authorized"},
            format="json",
        )
        self.assertEqual(rejected.status_code, 404)

        self.client.force_authenticate(self.team_leader)
        approved = self.client.patch(
            f"/api/submissions/{submission.pk}",
            {"status": "reviewed", "reviewer_comment": "Approved"},
            format="json",
        )
        self.assertEqual(approved.status_code, 200)
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.reviewed)
        self.assertEqual(submission.reviewer_comment, "Approved")
        self.assertEqual(calculate_work_score([submission], monthly_target=2)["earned_points"], 1.0)

    def test_team_leader_can_review_assigned_work_and_reject_with_comment(self):
        submission = Submission.objects.create(
            employee=self.employee,
            title="Work sample",
            file="legacy.pdf",
        )
        self.client.force_authenticate(self.team_leader)
        response = self.client.patch(
            f"/api/submissions/{submission.pk}",
            {"status": "rejected", "reviewer_comment": "Please revise the report."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.rejected)
        self.assertEqual(submission.reviewer_comment, "Please revise the report.")
        self.assertEqual(calculate_work_score([submission], monthly_target=2)["earned_points"], 0)

    def test_team_leader_cannot_review_another_team_submission(self):
        other_leader = User.objects.create_user(
            email="other-submission-leader@example.test",
            password="LeaderPass123",
            name="Other Leader",
            role=User.Role.team_leader,
        )
        self.employee.team_leader = other_leader
        self.employee.save(update_fields=("team_leader",))
        submission = Submission.objects.create(
            employee=self.employee,
            title="Team work",
            file="legacy.pdf",
        )
        self.client.force_authenticate(self.team_leader)
        response = self.client.patch(
            f"/api/submissions/{submission.pk}",
            {"status": "reviewed", "reviewer_comment": "Approved"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

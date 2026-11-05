from datetime import date
from io import BytesIO
import os
from urllib.parse import urlencode
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APITestCase

from api.forms import AutomationSettingsForm
from api.ml.predict import (
    MODEL_SOURCE,
    MODEL_VERSION,
    build_feature_vector,
    loaded_model_artifacts,
    predict_performance,
    UnknownDepartmentError,
)
from .models import (
    AutomationSettings,
    Department,
    Employee,
    Evaluation,
    EvaluationCriteriaSettings,
    Prediction,
    TrustedDevice,
    User,
)


@override_settings(ALLOWED_HOSTS=["127.0.0.1", "localhost", "testserver"])
class BackendApiTests(APITestCase):
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
        cls.hr = User.objects.create_user(
            email="hr@lexie.ph",
            password="Manager123!",
            name="HR Manager",
            role=User.Role.hr_manager,
        )
        cls.employee_user = User.objects.create_user(
            email="staff@lexie.ph",
            password="Staff123!",
            name="Staff User",
            role=User.Role.staff,
        )
        cls.other_employee_user = User.objects.create_user(
            email="other@lexie.ph",
            password="Staff123!",
            name="Other User",
            role=User.Role.employee,
        )
        cls.department = Department.objects.create(name="Recruitment")
        cls.employee = Employee.objects.create(
            employee_code="EMP-1001",
            user=cls.employee_user,
            name="Staff User",
            department=cls.department,
            position="Recruiter",
            date_hired=date(2024, 1, 1),
        )
        cls.other_employee = Employee.objects.create(
            employee_code="EMP-1002",
            user=cls.other_employee_user,
            name="Other User",
            department=cls.department,
            position="Coordinator",
            date_hired=date(2024, 2, 1),
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def setUp(self):
        cache.clear()

    def evaluation_payload(self, employee_id=None):
        return {
            "employee_id": employee_id or self.employee.id,
            "date": "2026-01-15",
            "productivity_score": 80,
            "attendance_score": 85,
            "quality_score": 90,
            "teamwork_score": 88,
            "communication_score": 92,
            "comments": "Consistent performance",
        }

    def test_login_accepts_frontend_form_and_returns_jwt_contract(self):
        response = self.client.post(
            "/auth/login",
            urlencode({"username": "admin@lexie.ph", "password": "Admin123!"}),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["access_token"])
        self.assertEqual(response.data["token_type"], "bearer")
        self.assertEqual(response.data["role"], "admin")
        profile = self.client.get(
            "/auth/me",
            HTTP_AUTHORIZATION=f"Bearer {response.data['access_token']}",
        )
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.data["email"], "admin@lexie.ph")

    def test_vercel_api_prefix_routes_to_auth_api(self):
        response = self.client.post(
            "/api/auth/login",
            urlencode({"username": "admin@lexie.ph", "password": "Admin123!"}),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 200)

    def test_invalid_login_is_401(self):
        response = self.client.post(
            "/auth/login",
            urlencode({"username": "admin@lexie.ph", "password": "wrong"}),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 401)

    def test_set_user_role_management_command_updates_application_role(self):
        call_command("set_user_role", self.employee_user.email, User.Role.admin)
        self.employee_user.refresh_from_db()
        self.assertEqual(self.employee_user.role, User.Role.admin)

    def test_api_login_is_throttled_after_five_failed_attempts(self):
        for _ in range(5):
            response = self.client.post(
                "/auth/login",
                {"username": "admin@lexie.ph", "password": "wrong"},
                format="json",
            )
            self.assertEqual(response.status_code, 401)

        response = self.client.post(
            "/auth/login",
            {"username": "admin@lexie.ph", "password": "wrong"},
            format="json",
        )

        self.assertEqual(response.status_code, 429)
        self.assertIn("Too many attempts. Try again later.", response.data["detail"])

    def test_staff_can_only_list_and_read_own_employee(self):
        self.authenticate(self.employee_user)
        response = self.client.get("/employees")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data], [self.employee.id])
        response = self.client.get(f"/employees/{self.other_employee.id}")
        self.assertEqual(response.status_code, 403)

    def test_staff_cannot_create_employee(self):
        self.authenticate(self.employee_user)
        response = self.client.post("/employees", {
            "name": "New Person",
            "department": "Finance",
            "position": "Analyst",
            "date_hired": "2026-01-01",
        }, format="json")
        self.assertEqual(response.status_code, 403)

    def test_hr_manager_can_manage_workforce_but_cannot_register_accounts(self):
        self.authenticate(self.hr)
        create_response = self.client.post("/employees", {
            "name": "New Person",
            "department": "Finance",
            "position": "Analyst",
            "date_hired": "2026-01-01",
        }, format="json")
        self.assertEqual(create_response.status_code, 201)
        register_response = self.client.post("/auth/register", {
            "name": "New User",
            "email": "new@lexie.ph",
            "password": "NewPassword123!",
            "role": "staff",
        }, format="json")
        self.assertEqual(register_response.status_code, 403)

    def test_management_can_manage_only_workforce_accounts(self):
        self.authenticate(self.hr)
        created = self.client.post("/api/accounts", {
            "name": "New Team Leader",
            "email": "leader@lexie.ph",
            "password": "StrongPass123",
            "role": User.Role.team_leader,
        }, format="json")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["role"], User.Role.team_leader)
        self.assertFalse(User.objects.get(email="leader@lexie.ph").is_superuser)
        leader_account_id = created.data["id"]
        self.assertIn(
            leader_account_id,
            {account["id"] for account in self.client.get("/api/accounts").data},
        )
        deactivated = self.client.patch(
            f"/api/accounts/{leader_account_id}",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(deactivated.status_code, 200)
        self.assertFalse(deactivated.data["is_active"])
        reactivated = self.client.patch(
            f"/api/accounts/{leader_account_id}",
            {"is_active": True},
            format="json",
        )
        self.assertEqual(reactivated.status_code, 200)
        self.assertTrue(reactivated.data["is_active"])
        role_updated = self.client.patch(
            f"/api/accounts/{leader_account_id}",
            {"role": User.Role.staff},
            format="json",
        )
        self.assertEqual(role_updated.status_code, 200)
        self.assertEqual(role_updated.data["role"], User.Role.staff)

        rejected = self.client.post("/api/accounts", {
            "name": "New Manager",
            "email": "manager@lexie.ph",
            "password": "StrongPass123",
            "role": User.Role.management,
        }, format="json")
        self.assertEqual(rejected.status_code, 403)
        rejected_admin = self.client.post("/api/accounts", {
            "name": "Forbidden Admin",
            "email": "forbidden-admin@lexie.ph",
            "password": "StrongPass123",
            "role": User.Role.admin,
        }, format="json")
        self.assertEqual(rejected_admin.status_code, 403)

        listed = self.client.get("/api/accounts")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            {account["role"] for account in listed.data},
            {User.Role.staff, User.Role.employee},
        )
        self.assertEqual(self.client.patch(f"/api/accounts/{self.admin.id}", {"is_active": False}).status_code, 403)

    def test_admin_can_create_update_and_deactivate_accounts(self):
        self.authenticate(self.admin)
        created = self.client.post("/api/accounts", {
            "name": "New Manager",
            "email": "manager@lexie.ph",
            "password": "StrongPass123",
            "role": User.Role.management,
        }, format="json")
        self.assertEqual(created.status_code, 201)
        account_id = created.data["id"]

        updated = self.client.patch(
            f"/api/accounts/{account_id}",
            {"role": User.Role.team_leader, "is_active": False},
            format="json",
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["role"], User.Role.team_leader)
        self.assertFalse(updated.data["is_active"])

    def test_admin_can_manage_each_assignable_account_role(self):
        self.authenticate(self.admin)
        for role in (User.Role.management, User.Role.team_leader, User.Role.staff):
            with self.subTest(role=role):
                created = self.client.post(
                    "/api/accounts",
                    {
                        "email": f"{role}@accounts.test",
                        "password": "AccountsPassword123!",
                        "name": f"{role} Account",
                        "role": role,
                    },
                    format="json",
                )
                self.assertEqual(created.status_code, 201)
                account_id = created.data["id"]
                response = self.client.patch(
                    f"/api/accounts/{account_id}",
                    {"is_active": False},
                    format="json",
                )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.data["is_active"])
                response = self.client.patch(
                    f"/api/accounts/{account_id}",
                    {"is_active": True},
                    format="json",
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.data["is_active"])

    def test_management_cannot_see_or_change_admin_or_management_accounts(self):
        other_manager = User.objects.create_user(
            email="another-manager@lexie.ph",
            password="ManagerPassword123!",
            name="Another Manager",
            role=User.Role.management,
        )
        self.authenticate(self.hr)
        listed = self.client.get("/api/accounts")
        self.assertEqual(listed.status_code, 200)
        visible_ids = {row["id"] for row in listed.data}
        self.assertNotIn(self.admin.pk, visible_ids)
        self.assertNotIn(self.hr.pk, visible_ids)
        self.assertNotIn(other_manager.pk, visible_ids)
        for changes in ({"role": User.Role.staff}, {"is_active": False}):
            self.assertEqual(
                self.client.patch(
                    f"/api/accounts/{self.hr.pk}",
                    changes,
                    format="json",
                ).status_code,
                403,
            )
        self.hr.refresh_from_db()
        self.assertEqual(self.hr.role, User.Role.hr_manager)
        self.assertTrue(self.hr.is_active)
        for account in (self.admin, other_manager):
            with self.subTest(account=account.email):
                response = self.client.patch(
                    f"/api/accounts/{account.pk}",
                    {"is_active": False},
                    format="json",
                )
                self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/accounts",
                {
                    "name": "Forbidden Manager",
                    "email": "forbidden-manager@lexie.ph",
                    "password": "StrongPass123",
                    "role": User.Role.management,
                },
                format="json",
            ).status_code,
            403,
        )

    def test_account_api_forbids_self_role_change_and_self_deactivation(self):
        self.authenticate(self.admin)
        for changes in (
            {"role": User.Role.staff},
            {"is_active": False},
            {"role": User.Role.management, "is_active": False},
        ):
            with self.subTest(changes=changes):
                response = self.client.patch(
                    f"/api/accounts/{self.admin.pk}",
                    changes,
                    format="json",
                )
                self.assertEqual(response.status_code, 403)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, User.Role.admin)
        self.assertTrue(self.admin.is_active)

    def test_team_leader_and_staff_cannot_access_account_management_or_change_roles(self):
        leader = User.objects.create_user(
            email="accounts-leader@lexie.ph",
            password="LeaderPassword123!",
            name="Accounts Leader",
            role=User.Role.team_leader,
        )
        self.employee.team_leader = leader
        self.employee.save(update_fields=("team_leader",))
        target = self.employee_user
        for actor in (leader, self.employee_user):
            self.authenticate(actor)
            self.assertEqual(self.client.get("/api/accounts").status_code, 403)
            self.assertEqual(
                self.client.post(
                    "/api/accounts",
                    {
                        "name": "Blocked Account",
                        "email": f"blocked-{actor.pk}@lexie.ph",
                        "password": "StrongPass123",
                        "role": User.Role.staff,
                    },
                    format="json",
                ).status_code,
                403,
            )
            self.assertEqual(
                self.client.patch(
                    f"/api/accounts/{target.pk}",
                    {"role": User.Role.team_leader},
                    format="json",
                ).status_code,
                403,
            )

    def test_staff_cannot_list_or_create_accounts(self):
        self.authenticate(self.employee_user)
        self.assertEqual(self.client.get("/api/accounts").status_code, 403)
        self.assertEqual(self.client.post("/api/accounts", {}, format="json").status_code, 403)

    def test_team_leader_employee_list_is_limited_to_assigned_team(self):
        leader = User.objects.create_user(
            email="leader@lexie.ph",
            password="LeaderPass123",
            name="Team Leader",
            role=User.Role.team_leader,
        )
        self.employee.team_leader = leader
        self.employee.save(update_fields=["team_leader"])
        self.authenticate(leader)

        response = self.client.get("/api/employees")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data], [self.employee.id])

    def test_admin_employee_crud(self):
        self.authenticate(self.admin)
        created = self.client.post("/employees", {
            "name": "New Person",
            "department": "Finance",
            "position": "Analyst",
            "date_hired": "2026-01-01",
        }, format="json")
        self.assertEqual(created.status_code, 201)
        employee_id = created.data["id"]
        self.assertEqual(created.data["department"], "Finance")

        updated = self.client.put(f"/employees/{employee_id}", {
            "name": "Updated Person",
            "department": "Finance",
        }, format="json")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["name"], "Updated Person")

        deleted = self.client.delete(f"/employees/{employee_id}")
        self.assertEqual(deleted.status_code, 204)

    def test_admin_evaluation_crud_and_read_format(self):
        self.authenticate(self.admin)
        created = self.client.post("/evaluations", self.evaluation_payload(), format="json")
        self.assertEqual(created.status_code, 201)
        evaluation_id = created.data["id"]
        self.assertEqual(created.data["overall_score"], 87.0)
        self.assertEqual(created.data["evaluator_id"], self.admin.id)

        updated = self.client.put(f"/evaluations/{evaluation_id}", {"comments": "Updated"}, format="json")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["comments"], "Updated")
        self.assertEqual(self.client.get("/evaluations").status_code, 200)
        self.assertEqual(self.client.delete(f"/evaluations/{evaluation_id}").status_code, 204)

    def test_employee_evaluations_are_scoped_to_owner(self):
        evaluation = Evaluation.objects.create(
            employee=self.employee,
            evaluator=self.admin,
            date=date(2026, 1, 1),
            productivity_score=80,
            attendance_score=80,
            quality_score=80,
            teamwork_score=80,
            communication_score=80,
        )
        self.authenticate(self.other_employee_user)
        self.assertEqual(self.client.get(f"/evaluations/{evaluation.id}").status_code, 403)

    def test_client_cannot_access_admin_reports(self):
        client_user = User.objects.create_user(
            email="client@lexie.ph",
            password="Client123!",
            name="Client User",
            role=User.Role.client,
        )
        self.authenticate(client_user)
        self.assertEqual(self.client.get("/reports/summary").status_code, 403)

    def test_departments_api_returns_seedable_catalog(self):
        self.authenticate(self.hr)
        response = self.client.get("/departments")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["name"], "Recruitment")

    def test_loaded_random_forest_prediction_matches_prediction_api(self):
        self.authenticate(self.admin)
        evaluation = Evaluation.objects.create(
            employee=self.employee,
            evaluator=self.admin,
            date=date(2026, 1, 15),
            productivity_score=80,
            attendance_score=85,
            quality_score=90,
            teamwork_score=88,
            communication_score=92,
        )
        features = build_feature_vector(self.employee, evaluation)
        model, _, target_encoder, _ = loaded_model_artifacts()
        predicted_label = model.predict(features)[0]
        probabilities = model.predict_proba(features)[0]
        expected_category = str(target_encoder.inverse_transform([predicted_label])[0])
        expected_confidence = round(float(max(probabilities)), 4)
        expected = predict_performance(self.employee, evaluation)

        response = self.client.post(f"/predictions/{self.employee.id}")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["predicted_category"], expected_category)
        self.assertEqual(response.data["predicted_category"], expected["predicted_category"])
        self.assertEqual(response.data["confidence_score"], expected_confidence)
        self.assertEqual(response.data["feature_importances"], expected["feature_importances"])
        self.assertEqual(response.data["model_source"], MODEL_SOURCE)
        self.assertEqual(response.data["model_version"], MODEL_VERSION)

    def test_prediction_api_returns_422_for_unknown_department(self):
        self.authenticate(self.admin)
        unsupported = Department.objects.create(name="Teacher")
        self.other_employee.department = unsupported
        self.other_employee.save(update_fields=["department"])
        evaluation = Evaluation.objects.create(
            employee=self.other_employee,
            evaluator=self.admin,
            date=date(2026, 1, 15),
            productivity_score=80,
            attendance_score=85,
            quality_score=90,
            teamwork_score=88,
            communication_score=92,
        )
        with self.assertRaises(UnknownDepartmentError):
            build_feature_vector(self.other_employee, evaluation)

        response = self.client.post(f"/predictions/{self.other_employee.id}")

        self.assertEqual(response.status_code, 422)
        self.assertIn("Teacher", response.data["detail"])
        self.assertIn("Recruitment", response.data["detail"])


@override_settings(ALLOWED_HOSTS=["127.0.0.1", "localhost", "testserver"])
class WebViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="web-admin@lexie.ph",
            password="WebTest123!",
            name="Web Admin",
            role=User.Role.admin,
            is_staff=True,
            is_superuser=True,
        )
        cls.hr_user = User.objects.create_user(
            email="web-hr@lexie.ph",
            password="WebTest123!",
            name="Web HR",
            role=User.Role.hr_manager,
        )
        cls.management_user = User.objects.create_user(
            email="web-management@lexie.ph",
            password="WebTest123!",
            name="Web Management",
            role=User.Role.management,
        )
        cls.employee_user = User.objects.create_user(
            email="web-employee@lexie.ph",
            password="WebTest123!",
            name="Web Employee",
            role=User.Role.employee,
        )
        cls.other_user = User.objects.create_user(
            email="web-other@lexie.ph",
            password="WebTest123!",
            name="Other Employee",
            role=User.Role.staff,
        )
        cls.client_user = User.objects.create_user(
            email="web-client@lexie.ph",
            password="WebTest123!",
            name="Web Client",
            role=User.Role.client,
        )
        cls.department = Department.objects.create(name="Recruitment")
        cls.employee = Employee.objects.create(
            employee_code="WEB-1001",
            user=cls.employee_user,
            name="Web Employee",
            department=cls.department,
            position="Analyst",
            date_hired=date(2025, 1, 1),
        )
        cls.other_employee = Employee.objects.create(
            employee_code="WEB-1002",
            user=cls.other_user,
            name="Other Employee",
            department=cls.department,
            position="Coordinator",
            date_hired=date(2025, 1, 1),
        )
        Evaluation.objects.create(
            employee=cls.employee,
            evaluator=cls.admin,
            date=date(2026, 1, 15),
            productivity_score=80,
            attendance_score=85,
            quality_score=90,
            teamwork_score=88,
            communication_score=92,
        )

    def test_dashboard_redirects_anonymous_users_to_login(self):
        response = self.client.get("/dashboard/")

        self.assertRedirects(response, "/login/?next=/dashboard/")

    def test_dashboard_and_reports_pages_render_for_authenticated_admin(self):
        self.client.force_login(self.admin)

        dashboard_response = self.client.get("/dashboard/")
        reports_response = self.client.get("/reports/")

        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, "Performance trend")
        self.assertEqual(reports_response.status_code, 200)
        self.assertContains(reports_response, "Export results")
        self.assertContains(reports_response, "WEB-1001")

    def test_login_uses_session_and_logout_ends_session(self):
        response = self.client.post(
            "/login/",
            {"email": "WEB-ADMIN@LEXIE.PH", "password": "WebTest123!"},
        )

        self.assertRedirects(response, "/dashboard/")
        self.assertTrue(self.client.session.get("_auth_user_id"))
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)

        response = self.client.post("/logout/")

        self.assertRedirects(response, "/login/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_web_login_is_throttled_after_five_failed_attempts(self):
        for _ in range(5):
            response = self.client.post(
                "/login/",
                {"email": "WEB-ADMIN@LEXIE.PH", "password": "wrong"},
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/login/",
            {"email": "WEB-ADMIN@LEXIE.PH", "password": "wrong"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too many attempts. Try again later.")

    def test_employee_dashboard_contains_only_own_profile(self):
        self.client.force_login(self.employee_user)

        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Web Employee")
        self.assertContains(response, "87.0")
        self.assertNotContains(response, "Other Employee")
        self.assertNotContains(response, "WEB-1002")

    def test_unsupported_role_is_denied_dashboard_access(self):
        self.client.force_login(self.client_user)

        self.assertEqual(self.client.get("/dashboard/").status_code, 403)

    def test_employee_views_are_scoped_and_write_views_require_workforce_role(self):
        self.client.force_login(self.employee_user)

        response = self.client.get("/employees/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Web Employee")
        self.assertNotContains(response, "Other Employee")
        self.assertEqual(self.client.get(f"/employees/{self.other_employee.pk}/").status_code, 404)
        self.assertEqual(self.client.get("/employees/add/").status_code, 403)
        self.assertEqual(self.client.get("/departments/").status_code, 403)
        self.assertEqual(self.client.get("/reports/").status_code, 403)
        self.assertEqual(self.client.get("/accounts/").status_code, 403)

    def test_hr_can_create_department_and_employee_with_derived_fields(self):
        self.client.force_login(self.hr_user)

        department_response = self.client.post(
            "/departments/add/",
            {"name": "Client Services", "description": "Client support"},
        )
        self.assertRedirects(department_response, "/departments/")
        department = Department.objects.get(name="Client Services")
        self.assertTrue(department.is_active)

        add_response = self.client.get("/employees/add/")
        form = add_response.context["form"]
        self.assertEqual(
            set(form.fields),
            {"user", "team_leader", "name", "department", "position", "date_hired"},
        )

        employee_response = self.client.post(
            "/employees/add/",
            {
                "name": "Created Employee",
                "department": department.pk,
                "position": "Coordinator",
                "date_hired": "2025-02-01",
            },
        )
        self.assertRedirects(employee_response, "/employees/")
        employee = Employee.objects.get(name="Created Employee")
        self.assertTrue(employee.employee_code.startswith("EMP-"))
        self.assertEqual(employee.department, department)
        self.assertEqual(employee.status, Employee.Status.active)
        self.assertIsNone(employee.attendance_rate)
        self.assertIsNone(employee.training_hours)
        self.assertGreaterEqual(employee.tenure_months, 0)

    def test_department_status_is_not_exposed_when_creating_and_cannot_deactivate_with_active_employee(self):
        self.client.force_login(self.hr_user)
        add_response = self.client.get("/departments/add/")
        self.assertNotIn("is_active", add_response.context["form"].fields)

        edit_response = self.client.post(
            f"/departments/{self.department.pk}/edit/",
            {
                "name": self.department.name,
                "description": self.department.description,
                "is_active": "inactive",
            },
        )
        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(
            edit_response,
            "Cannot deactivate this department while active employees remain.",
        )
        self.department.refresh_from_db()
        self.assertTrue(self.department.is_active)

    def test_management_can_change_employee_status_from_profile(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/employees/{self.employee.pk}/status/", {"status": "on_leave"}
        )
        self.assertRedirects(response, f"/employees/{self.employee.pk}/")
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.status, Employee.Status.on_leave)

    def test_monthly_evaluation_starts_after_new_employee_hire_month(self):
        employee = Employee.objects.create(
            employee_code="WEB-NEW-1",
            name="New Employee",
            department=self.department,
            position="Analyst",
            date_hired=date(2026, 1, 15),
            status=Employee.Status.active,
        )
        from api.automation import create_monthly_evaluations

        created = create_monthly_evaluations(date(2026, 1, 15))
        self.assertFalse(employee.evaluations.exists())
        self.assertFalse(any(item.employee == employee for item in created))

        created = create_monthly_evaluations(date(2026, 2, 1))
        new_employee_evaluations = [
            item for item in created if item.employee == employee
        ]
        self.assertEqual(len(new_employee_evaluations), 1)
        self.assertEqual(new_employee_evaluations[0].cycle_month, date(2026, 2, 1))

    def test_prediction_run_shows_unknown_department_error(self):
        unsupported = Department.objects.create(name="Teacher")
        self.employee.department = unsupported
        self.employee.save(update_fields=["department"])
        self.client.force_login(self.admin)

        response = self.client.post(
            "/predictions/run/",
            {"action": "single", "employee_id": self.employee.pk},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Teacher")
        self.assertContains(response, "not supported by the trained model")

    def test_department_with_employees_cannot_be_deleted(self):
        self.client.force_login(self.admin)

        response = self.client.post(f"/departments/{self.department.pk}/delete/")

        self.assertRedirects(response, "/departments/")
        self.assertTrue(Department.objects.filter(pk=self.department.pk).exists())

    def test_performance_records_are_filtered_by_owner_and_can_be_created_by_hr(self):
        self.client.force_login(self.employee_user)
        response = self.client.get("/performance/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Web Employee")
        self.assertNotContains(response, "Other Employee")
        self.assertEqual(self.client.get("/performance/add/").status_code, 403)

        self.client.force_login(self.hr_user)
        response = self.client.post(
            "/performance/add/",
            {
                "employee": self.employee.pk,
                "date": "2026-02-01",
                "productivity_score": "90",
                "attendance_score": "90",
                "quality_score": "90",
                "teamwork_score": "90",
                "communication_score": "90",
            },
        )
        self.assertRedirects(response, "/performance/")
        self.assertTrue(Evaluation.objects.filter(employee=self.employee, date=date(2026, 2, 1)).exists())

        record = Evaluation.objects.get(employee=self.employee, date=date(2026, 2, 1))
        response = self.client.post(
            f"/performance/{record.pk}/edit/",
            {
                "employee": self.employee.pk,
                "date": "2026-02-01",
                "productivity_score": "91",
                "attendance_score": "90",
                "quality_score": "90",
                "teamwork_score": "90",
                "communication_score": "90",
            },
        )
        self.assertRedirects(response, "/performance/")
        record.refresh_from_db()
        self.assertEqual(record.productivity_score, 91)

    def test_evaluation_views_calculate_scores_and_protect_other_employees(self):
        self.client.force_login(self.employee_user)
        self.assertEqual(self.client.get("/evaluations/criteria/").status_code, 403)
        response = self.client.get("/evaluations/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "87.0")

        self.client.force_login(self.hr_user)
        response = self.client.post(
            "/evaluations/add/",
            {
                "employee": self.other_employee.pk,
                "date": "2026-03-01",
                "productivity_score": "80",
                "attendance_score": "85",
                "quality_score": "90",
                "teamwork_score": "88",
                "communication_score": "92",
                "comments": "Quarterly review",
            },
        )
        evaluation = Evaluation.objects.get(employee=self.other_employee, date=date(2026, 3, 1))
        self.assertRedirects(response, "/evaluations/")
        self.assertEqual(evaluation.overall_score, 87)

        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(f"/evaluations/{evaluation.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/evaluations/{Evaluation.objects.filter(employee=self.employee).first().pk}/").status_code, 404)

    def test_admin_can_manage_criteria_and_employees_cannot_run_predictions(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/evaluations/criteria/").status_code, 200)

        self.client.force_login(self.employee_user)
        response = self.client.post(
            "/predictions/run/",
            {"action": "single", "employee_id": self.employee.pk},
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.get("/predictions/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Other Employee")

    def test_prediction_page_shows_latest_prediction_for_each_evaluation(self):
        for evaluation_date in (date(2026, 2, 15), date(2026, 3, 15)):
            Evaluation.objects.create(
                employee=self.employee,
                evaluator=self.admin,
                date=evaluation_date,
                productivity_score=80,
                attendance_score=85,
                quality_score=90,
                teamwork_score=88,
                communication_score=92,
            )

        self.client.force_login(self.admin)
        response = self.client.get("/predictions/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "latest prediction for each evaluation")
        self.assertContains(response, "Feb 15, 2026")
        self.assertContains(response, "Mar 15, 2026")
        displayed_evaluation_ids = {
            prediction.evaluation_id for prediction in response.context["predictions"]
        }
        expected_evaluation_ids = set(
            Evaluation.objects.filter(employee=self.employee).values_list("pk", flat=True)
        )
        self.assertEqual(displayed_evaluation_ids, expected_evaluation_ids)
        self.assertEqual(len(response.context["predictions"]), len(expected_evaluation_ids))

    def test_score_drop_threshold_must_be_greater_than_zero_and_at_most_100(self):
        valid_form = AutomationSettingsForm(data={"score_drop_threshold": "10"})
        valid_form.is_valid()
        self.assertNotIn("score_drop_threshold", valid_form.errors)
        for invalid_value in ("0", "-1", "100.01"):
            with self.subTest(value=invalid_value):
                form = AutomationSettingsForm(
                    data={"score_drop_threshold": invalid_value}
                )
                form.is_valid()
                self.assertIn("score_drop_threshold", form.errors)
                with self.assertRaises(ValidationError):
                    AutomationSettings(
                        score_drop_threshold=float(invalid_value)
                    ).full_clean()

    def test_admin_can_run_model_prediction_and_view_feature_importance(self):
        trained_department, _ = Department.objects.get_or_create(name="Recruitment")
        self.employee.department = trained_department
        self.employee.save(update_fields=("department",))
        self.client.force_login(self.admin)

        response = self.client.post(
            "/predictions/run/",
            {"action": "single", "employee_id": self.employee.pk},
        )

        self.assertRedirects(response, "/predictions/")
        prediction = Prediction.objects.filter(employee=self.employee).first()
        self.assertIsNotNone(prediction)
        self.assertTrue(0 <= prediction.confidence_score <= 1)
        self.assertTrue(prediction.feature_importances)
        self.assertContains(self.client.get("/predictions/"), prediction.predicted_category)

    def test_batch_prediction_saves_successes_and_reports_missing_evaluations(self):
        trained_department, _ = Department.objects.get_or_create(name="Recruitment")
        self.employee.department = trained_department
        self.employee.save(update_fields=("department",))
        self.client.force_login(self.admin)

        response = self.client.post("/predictions/run/", {"action": "batch"})

        self.assertRedirects(response, "/predictions/")
        self.assertTrue(Prediction.objects.filter(employee=self.employee).exists())
        self.assertContains(self.client.get("/predictions/"), self.employee.name)

    def test_reports_export_filtered_csv_and_pdf(self):
        self.client.force_login(self.hr_user)

        self.client.force_login(self.employee_user)
        self.assertEqual(self.client.get("/reports/export.csv").status_code, 403)
        self.assertEqual(self.client.get("/reports/export.pdf").status_code, 403)

        self.client.force_login(self.hr_user)

        csv_response = self.client.get(
            "/reports/export.csv",
            {"department": self.department.pk, "start_date": "2026-01-01", "end_date": "2026-01-31"},
        )
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("text/csv", csv_response["Content-Type"])
        self.assertIn("lexie-performance-report-", csv_response["Content-Disposition"])
        self.assertTrue(csv_response["Content-Disposition"].endswith(".csv\""))
        self.assertIn(b"WEB-1001", csv_response.content)
        self.assertNotIn(b"WEB-1002", csv_response.content)

        pdf_response = self.client.get("/reports/export.pdf")
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertIn("lexie-performance-report-", pdf_response["Content-Disposition"])
        self.assertTrue(pdf_response["Content-Disposition"].endswith(".pdf\""))
        self.assertTrue(pdf_response.content.startswith(b"%PDF-1.4"))

    def test_accounts_and_settings_are_role_scoped_and_forms_work(self):
        self.client.force_login(self.hr_user)
        self.assertEqual(self.client.get("/accounts/").status_code, 403)

        self.client.force_login(self.management_user)
        response = self.client.get("/accounts/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "User accounts")
        self.assertEqual(self.client.get("/system-tools/").status_code, 403)

        unlinked_employee = Employee.objects.create(
            employee_code="WEB-1003-MGMT",
            name="Unlinked Employee",
            department=self.department,
            position="Analyst",
            date_hired=date(2025, 3, 1),
        )
        response = self.client.post(
            "/accounts/add/",
            {
                "name": "New Team Leader",
                "email": "new-team-leader@lexie.ph",
                "role": User.Role.team_leader,
                "password1": "Strong-Test-Password-123!",
                "password2": "Strong-Test-Password-123!",
            },
        )
        self.assertRedirects(response, "/accounts/")
        created_user = User.objects.get(email="new-team-leader@lexie.ph")
        self.assertEqual(created_user.role, User.Role.team_leader)

        response = self.client.post(
            "/accounts/add/",
            {
                "name": "New Staff Account",
                "email": "new-staff-account@lexie.ph",
                "role": User.Role.staff,
                "employee": unlinked_employee.pk,
                "password1": "Strong-Test-Password-123!",
                "password2": "Strong-Test-Password-123!",
            },
        )
        self.assertRedirects(response, "/accounts/")
        staff_user = User.objects.get(email="new-staff-account@lexie.ph")
        self.assertEqual(staff_user.role, User.Role.staff)
        unlinked_employee.refresh_from_db()
        self.assertEqual(unlinked_employee.user, staff_user)

        self.client.force_login(self.admin)
        unlinked_employee = Employee.objects.create(
            employee_code="WEB-1003",
            name="Unlinked Employee",
            department=self.department,
            position="Analyst",
            date_hired=date(2025, 3, 1),
        )
        response = self.client.post(
            "/accounts/add/",
            {
                "name": "New Account",
                "email": "new-account@lexie.ph",
                "role": User.Role.employee,
                "employee": unlinked_employee.pk,
                "password1": "Strong-Test-Password-123!",
                "password2": "Strong-Test-Password-123!",
            },
        )
        self.assertRedirects(response, "/accounts/")
        created_user = User.objects.get(email="new-account@lexie.ph")
        unlinked_employee.refresh_from_db()
        self.assertEqual(created_user.role, User.Role.employee)
        self.assertEqual(unlinked_employee.user, created_user)

        self.client.force_login(self.employee_user)
        response = self.client.post(
            "/settings/",
            {"form_action": "profile", "name": "Updated Web Employee", "email": self.employee_user.email},
        )
        self.assertRedirects(response, "/settings/")
        self.employee_user.refresh_from_db()
        self.assertEqual(self.employee_user.name, "Updated Web Employee")
        self.assertEqual(self.client.get("/settings/").status_code, 200)

    def test_notification_preferences_load_defaults_save_and_are_user_specific(self):
        self.client.force_login(self.employee_user)

        response = self.client.get("/settings/?tab=notifications")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="panel-notifications"')
        self.assertContains(response, 'name="email_evaluations" role="switch" aria-label="Evaluation updates" checked')
        self.assertContains(response, 'name="email_predictions" role="switch" aria-label="Prediction updates" checked')
        self.assertNotContains(response, 'name="weekly_summary" role="switch" aria-label="Weekly summary" checked')

        response = self.client.post(
            "/settings/?tab=notifications",
            {
                "form_action": "notifications",
                "email_predictions": "on",
                "weekly_summary": "on",
            },
        )

        self.assertRedirects(response, "/settings/?tab=notifications")
        self.employee_user.refresh_from_db()
        self.other_user.refresh_from_db()
        self.assertEqual(
            self.employee_user.notification_preferences,
            {
                "email_evaluations": False,
                "email_predictions": True,
                "weekly_summary": True,
            },
        )
        self.assertEqual(self.other_user.notification_preferences, {})

        response = self.client.get("/settings/?tab=notifications")
        self.assertNotContains(response, 'name="email_evaluations" role="switch" aria-label="Evaluation updates" checked')
        self.assertContains(response, 'name="weekly_summary" role="switch" aria-label="Weekly summary" checked')

    def test_staff_role_is_treated_as_employee_for_private_pages(self):
        self.client.force_login(self.other_user)
        response = self.client.get("/performance/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Other Employee")
        self.assertNotContains(response, "Web Employee")

    def test_admin_can_open_all_server_rendered_pages(self):
        self.client.force_login(self.admin)
        evaluation = Evaluation.objects.filter(employee=self.employee).first()
        paths = (
            "/dashboard/",
            "/employees/",
            f"/employees/{self.employee.pk}/",
            f"/employees/{self.employee.pk}/edit/",
            f"/employees/{self.employee.pk}/delete/",
            "/employees/add/",
            "/departments/",
            "/departments/add/",
            f"/departments/{self.department.pk}/edit/",
            f"/departments/{self.department.pk}/delete/",
            "/performance/",
            "/performance/add/",
            f"/performance/{evaluation.pk}/edit/",
            "/evaluations/",
            "/evaluations/add/",
            "/evaluations/criteria/",
            f"/evaluations/{evaluation.pk}/",
            f"/evaluations/{evaluation.pk}/edit/",
            f"/evaluations/{evaluation.pk}/delete/",
            "/predictions/",
            "/reports/",
            "/reports/export.csv",
            "/reports/export.pdf",
            "/accounts/",
            "/accounts/add/",
            "/settings/",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_admin_can_update_criteria_in_the_database(self):
        self.client.force_login(self.admin)
        fields = {
            "productivity_score": "Output",
            "attendance_score": "Reliability",
            "quality_score": "Quality",
            "teamwork_score": "Team contribution",
            "communication_score": "Communication",
        }
        response = self.client.post("/evaluations/criteria/", fields)
        self.assertRedirects(response, "/evaluations/criteria/")
        self.assertEqual(
            EvaluationCriteriaSettings.objects.get(pk=1).criteria,
            fields,
        )

    def test_password_can_be_changed_without_losing_the_session(self):
        self.client.force_login(self.employee_user)

        response = self.client.post(
            "/settings/",
            {
                "form_action": "password",
                "old_password": "WebTest123!",
                "new_password1": "New-WebPass-123!",
                "new_password2": "New-WebPass-123!",
            },
        )

        self.assertRedirects(response, "/settings/")
        self.employee_user.refresh_from_db()
        self.assertTrue(self.employee_user.check_password("New-WebPass-123!"))
        self.assertEqual(self.client.get("/settings/").status_code, 200)


class ReactFeaturesApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="feature-admin@lexie.ph",
            password="AdminPassword123!",
            name="Feature Admin",
            role=User.Role.admin,
            is_staff=True,
            is_superuser=True,
        )
        cls.management = User.objects.create_user(
            email="feature-manager@lexie.ph",
            password="ManagerPassword123!",
            name="Feature Manager",
            role=User.Role.management,
        )
        cls.leader = User.objects.create_user(
            email="feature-leader@lexie.ph",
            password="LeaderPassword123!",
            name="Feature Leader",
            role=User.Role.team_leader,
        )
        cls.staff = User.objects.create_user(
            email="feature-staff@lexie.ph",
            password="StaffPassword123!",
            name="Feature Staff",
            role=User.Role.staff,
        )
        cls.department = Department.objects.create(name="Feature Department")
        cls.employee = Employee.objects.create(
            employee_code="EMP-FEATURE",
            user=cls.staff,
            team_leader=cls.leader,
            name=cls.staff.name,
            department=cls.department,
            position="Analyst",
            date_hired=date(2024, 1, 1),
        )
        cls.evaluation = Evaluation.objects.create(
            employee=cls.employee,
            evaluator=cls.leader,
            date=date(2026, 1, 15),
            productivity_score=80,
            attendance_score=80,
            quality_score=80,
            teamwork_score=80,
            communication_score=80,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def test_reports_filter_and_export_are_scoped_to_team_leader(self):
        self.authenticate(self.leader)
        response = self.client.get(
            "/api/reports/records",
            {"department": self.department.name, "start_date": "2026-01-01", "end_date": "2026-01-31"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["record_count"], 1)
        self.assertEqual(self.client.get("/api/reports/export.csv").status_code, 200)
        pdf = self.client.get("/api/reports/export.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")

    def test_management_settings_criteria_and_notification_permissions(self):
        self.authenticate(self.management)
        settings_response = self.client.get("/api/automation/settings")
        self.assertEqual(settings_response.status_code, 200)
        update = self.client.patch("/api/automation/settings", {"scoring_mode": "automatic"}, format="json")
        self.assertEqual(update.status_code, 200)
        criteria = self.client.get("/api/evaluation-criteria")
        self.assertEqual(criteria.status_code, 200)
        updated_criteria = {**criteria.data, "productivity_score": "Output"}
        self.assertEqual(
            self.client.put("/api/evaluation-criteria", updated_criteria, format="json").status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/notifications/compose",
                {"recipient_id": self.leader.pk, "title": "Review", "message": "Please review."},
                format="json",
            ).status_code,
            201,
        )
        self.authenticate(self.staff)
        self.assertEqual(self.client.get("/api/automation/settings").status_code, 403)
        self.assertEqual(self.client.post("/api/notifications/compose", {}, format="json").status_code, 403)

    def test_hybrid_review_and_prediction_batch_use_existing_employee_scope(self):
        self.authenticate(self.leader)
        review = self.client.post(
            f"/api/evaluations/{self.evaluation.pk}/hybrid-review",
            {"team_leader_rating": 91},
            format="json",
        )
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.data["team_leader_rating"], 91)
        with patch(
            "api.ml.predict.predict_performance",
            return_value={
                "predicted_category": "Average",
                "confidence_score": 0.8,
                "feature_importances": {},
                "model_source": "test",
                "model_version": "test",
            },
        ):
            batch = self.client.post("/api/predictions/rerun", {}, format="json")
        self.assertEqual(batch.status_code, 200)
        self.assertEqual(batch.data["completed"], 1)

    def test_profile_photo_trusted_device_and_cron_endpoints_are_protected(self):
        self.authenticate(self.staff)
        image_buffer = BytesIO()
        Image.new("RGB", (1, 1)).save(image_buffer, format="PNG")
        photo = SimpleUploadedFile(
            "profile.png",
            image_buffer.getvalue(),
            content_type="image/png",
        )
        with patch("api.blob_storage.upload_profile_photo_blob", return_value="profiles/test/photo.png"):
            uploaded = self.client.post("/api/users/me/photo", {"photo": photo}, format="multipart")
        self.assertEqual(uploaded.status_code, 200)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.photo_blob_path, "profiles/test/photo.png")
        with patch(
            "api.feature_api.get_submission_blob",
            return_value=SimpleNamespace(body=b"image-data", metadata=SimpleNamespace(content_type="image/png")),
        ):
            image = self.client.get("/api/users/me/photo")
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image["Content-Type"], "image/png")

        device = TrustedDevice.objects.create(
            user=self.staff,
            token_hash="a" * 64,
            label="Test browser",
            status=TrustedDevice.Status.approved,
        )
        self.authenticate(self.admin)
        self.assertEqual(self.client.get("/api/devices").status_code, 200)
        self.assertEqual(
            self.client.patch(f"/api/devices/{device.pk}", {"action": "remove"}, format="json").status_code,
            200,
        )
        device.refresh_from_db()
        self.assertEqual(device.status, TrustedDevice.Status.revoked)

        with patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}), patch(
            "api.automation.create_monthly_evaluations", return_value=[]
        ):
            denied = self.client.get("/api/automation/monthly-evaluations")
            allowed = self.client.get(
                "/api/automation/monthly-evaluations",
                HTTP_AUTHORIZATION="Bearer cron-test-secret",
            )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)

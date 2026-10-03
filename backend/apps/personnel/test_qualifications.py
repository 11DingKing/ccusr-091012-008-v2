"""资质登记、续证、撤销、临时豁免与核验服务测试。"""
from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from apps.personnel.models import (
    PersonnelQualification,
    QualificationChangeLog,
    QualificationCheck,
    QualificationExemption,
    QualificationType,
    StockOutPerson,
)
from apps.personnel.qualification import (
    QualificationInvalidError,
    evaluate,
    verify_qualification,
)
from django.test import TestCase


class QualificationFixture(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.manager = User.objects.create_user("manager", "pass123456", role="admin")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(self.manager)}")
        self.today = timezone.localdate()

    def create_person(self, name="值班员甲", username="duty-user"):
        user = User.objects.create_user(username, "pass123456", role="admin")
        return StockOutPerson.objects.create(
            police_no=f"P{name}", name=name, phone=f"13{abs(hash(name)) % 10**9:09d}",
            binduser=user,
        ), user

    def create_type(self, code="RELEASE", name="夜间放行授权", business=("release",)):
        return QualificationType.objects.create(
            name=name, code=code, version="2024版",
            applicable_business=list(business), created_by=self.manager,
        )

    def grant(self, person, qtype, valid_until=None, valid_from=None, basis="年度培训合格"):
        return PersonnelQualification.objects.create(
            person=person, qualification_type=qtype, version=qtype.version,
            valid_from=valid_from or (self.today - timedelta(days=30)),
            valid_until=valid_until or (self.today + timedelta(days=30)),
            grant_basis=basis, granted_by=self.manager,
        )


class QualificationTypeAPITest(QualificationFixture):
    def test_create_type_with_version_and_business(self):
        resp = self.client.post("/api/qualification-types/", {
            "name": "放行授权证", "code": "REL", "version": "v2",
            "applicable_business": ["release", "receiving"],
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()["data"]
        self.assertEqual(data["business_labels"], ["放行", "收件"])

    def test_reject_unknown_business_and_duplicate_code(self):
        self.create_type(code="DUP")
        bad_business = self.client.post("/api/qualification-types/", {
            "name": "X", "code": "X1", "version": "v1",
            "applicable_business": ["night"],
        }, format="json")
        duplicate = self.client.post("/api/qualification-types/", {
            "name": "Y", "code": "DUP", "version": "v1",
            "applicable_business": ["release"],
        }, format="json")
        self.assertEqual(bad_business.status_code, 400)
        self.assertEqual(duplicate.status_code, 400)


class QualificationLifecycleTest(QualificationFixture):
    def test_grant_renew_and_revoke_leave_basis_logs(self):
        person, _ = self.create_person()
        qtype = self.create_type(business=("release", "approval"))

        # 首次登记
        granted = self.client.post("/api/qualifications/grant/", {
            "person": person.id, "qualification_type": qtype.id,
            "valid_from": self.today.isoformat(),
            "valid_until": (self.today + timedelta(days=10)).isoformat(),
            "grant_basis": "第1期培训合格证 文号PX-1",
        }, format="json")
        self.assertEqual(granted.status_code, 200, granted.content)
        first_id = granted.json()["data"]["id"]

        # 续证：旧记录保留，新记录生效
        renewed = self.client.post("/api/qualifications/grant/", {
            "person": person.id, "qualification_type": qtype.id,
            "valid_from": (self.today + timedelta(days=11)).isoformat(),
            "valid_until": (self.today + timedelta(days=400)).isoformat(),
            "grant_basis": "第2期培训合格证 文号PX-2",
        }, format="json")
        self.assertIn("续证", renewed.json()["message"])
        self.assertEqual(PersonnelQualification.objects.filter(person=person).count(), 2)
        old = PersonnelQualification.objects.get(pk=first_id)
        self.assertEqual(old.status, "valid")  # 旧证作为历史证据保留

        actions = set(
            QualificationChangeLog.objects.filter(person=person)
            .values_list("action", flat=True)
        )
        self.assertEqual(actions, {"grant", "renew"})

        # 撤销必须提供批准依据
        latest = PersonnelQualification.objects.filter(person=person).order_by("-id").first()
        no_basis = self.client.post(
            f"/api/qualifications/{latest.id}/revoke/", {}, format="json"
        )
        self.assertEqual(no_basis.status_code, 400)
        revoked = self.client.post(
            f"/api/qualifications/{latest.id}/revoke/",
            {"revoke_reason": "违规操作，依据监规〔2026〕3号撤销"},
            format="json",
        )
        self.assertEqual(revoked.status_code, 200, revoked.content)
        latest.refresh_from_db()
        self.assertEqual(latest.status, "revoked")
        self.assertEqual(latest.revoked_by, self.manager)
        self.assertTrue(
            QualificationChangeLog.objects.filter(
                person=person, action="revoke", basis__contains="监规"
            ).exists()
        )


class QualificationVerificationTest(QualificationFixture):
    def test_blocks_unregistered_account(self):
        lone_user = User.objects.create_user("lone", "pass123456", role="user")
        with self.assertRaises(QualificationInvalidError):
            verify_qualification(lone_user, "release")
        check = QualificationCheck.objects.get(user=lone_user, business="release")
        self.assertEqual(check.result, "blocked")
        self.assertIn("重新分配", check.detail)

    def test_valid_expired_revoked_and_wrong_business(self):
        person, user = self.create_person()
        release_type = self.create_type(code="REL", business=("release",))
        approval_type = self.create_type(code="APR", name="审批授权", business=("approval",))

        # 无证：阻断
        with self.assertRaises(QualificationInvalidError):
            verify_qualification(user, "release")

        # 有效：通过并固化快照
        qual = self.grant(person, release_type)
        check = verify_qualification(user, "release")
        self.assertEqual(check.result, "pass")
        self.assertEqual(check.grant_type, "qualification")
        self.assertTrue(check.snapshot["qualifications"][0]["effective_on_check_date"])

        # 业务不匹配：审批证不能放行
        self.grant(person, approval_type, valid_until=self.today + timedelta(days=5))
        with self.assertRaises(QualificationInvalidError):
            verify_qualification(user, "receiving")

        # 过期：阻断，且提示过期日期与重新分配
        qual.valid_until = self.today - timedelta(days=1)
        qual.save()
        with self.assertRaises(QualificationInvalidError) as ctx:
            verify_qualification(user, "release")
        self.assertIn("过期", ctx.exception.message)
        self.assertIn("重新分配", ctx.exception.message)

        # 撤销：阻断
        qual.valid_until = self.today + timedelta(days=10)
        qual.status = "revoked"
        qual.revoke_reason = "违规"
        qual.save()
        with self.assertRaises(QualificationInvalidError) as ctx:
            verify_qualification(user, "release")
        self.assertIn("撤销", ctx.exception.message)

    def test_historical_check_survives_later_revocation(self):
        """核验时固化的历史结论，不因子后续证/撤销而改变。"""
        person, user = self.create_person()
        qtype = self.create_type(business=("release",))
        self.grant(person, qtype)

        check = verify_qualification(user, "release")
        self.assertEqual(check.result, "pass")

        check.qualification.status = "revoked"
        check.qualification.save()
        check.refresh_from_db()
        self.assertEqual(check.result, "pass")  # 历史已完成操作按当时证据解释
        self.assertEqual(check.snapshot["qualifications"][0]["status"], "valid")

        # 当前评估则应判定无有效依据
        grant_type, grant, _ = evaluate(person, "release")
        self.assertEqual(grant_type, "")
        self.assertIsNone(grant)


class ExemptionTest(QualificationFixture):
    def test_exemption_requires_basis_and_window_enforced(self):
        person, user = self.create_person()
        qtype = self.create_type(business=("release",))

        # 无资质无豁免：阻断
        with self.assertRaises(QualificationInvalidError):
            verify_qualification(user, "release")

        # 批准豁免必须填写依据
        missing = self.client.post("/api/exemptions/", {
            "person": person.id, "business": "release",
            "reason": "新到岗", "basis": "",
            "valid_from": self.today.isoformat(),
            "valid_until": (self.today + timedelta(days=3)).isoformat(),
        }, format="json")
        self.assertEqual(missing.status_code, 400)

        exempt = self.client.post("/api/exemptions/", {
            "person": person.id, "business": "release",
            "reason": "新到岗待培训", "basis": "值班领导批准单 LN-7",
            "valid_from": self.today.isoformat(),
            "valid_until": (self.today + timedelta(days=3)).isoformat(),
        }, format="json")
        self.assertEqual(exempt.status_code, 200, exempt.content)
        exemption_id = exempt.json()["data"]["id"]
        self.assertTrue(
            QualificationChangeLog.objects.filter(
                person=person, action="exempt", basis="值班领导批准单 LN-7"
            ).exists()
        )

        # 豁免期内：依据豁免放行
        check = verify_qualification(user, "release")
        self.assertEqual(check.grant_type, "exemption")

        # 豁免到期：阻断
        QualificationExemption.objects.filter(pk=exemption_id).update(
            valid_until=self.today - timedelta(days=1)
        )
        with self.assertRaises(QualificationInvalidError):
            verify_qualification(user, "release")

    def test_exemption_revoke_logged(self):
        person, _ = self.create_person()
        exemption = QualificationExemption.objects.create(
            person=person, business="release", reason="r", basis="b",
            valid_from=self.today - timedelta(days=1),
            valid_until=self.today + timedelta(days=1),
            approved_by=self.manager,
        )
        resp = self.client.post(
            f"/api/exemptions/{exemption.id}/revoke/",
            {"revoke_reason": "培训完成，提前终止"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            QualificationChangeLog.objects.filter(
                person=person, action="exempt_revoke"
            ).exists()
        )

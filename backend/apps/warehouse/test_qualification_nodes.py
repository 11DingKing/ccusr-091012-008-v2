"""收件、审批、放行节点的资质核验集成测试。"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from apps.personnel.models import (
    PersonnelQualification,
    QualificationCheck,
    QualificationExemption,
    QualificationType,
    StockOutPerson,
)
from apps.warehouse.models import Approval, Goods, StockIn, StockOut
from django.test import TestCase


class DutyNodeFixture(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.goods = self._goods()

    def _goods(self, quantity=Decimal("10")):
        from apps.warehouse.models import Unit, Category, Variety
        unit = Unit.objects.create(name="件")
        category = Category.objects.create(name="受控器材", unit=unit)
        variety = Variety.objects.create(name="记录终端", category=category)
        return Goods.objects.create(
            variety=variety, name="执法终端", code="DEV-1", quantity=quantity,
            warning_threshold=Decimal("2"),
        )

    def duty_user(self, tag):
        user = User.objects.create_user(f"user_{tag}", "pass123456", role="admin")
        person = StockOutPerson.objects.create(
            police_no=f"POL_{tag}", name=f"值班员{tag}",
            phone=f"139{tag:0>8}", binduser=user,
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(user)}")
        return user, person, client

    def qualify(self, person, business, days=30, by=None):
        qtype, _ = QualificationType.objects.get_or_create(
            code=f"Q_{business}",
            defaults={
                "name": f"{business}资质", "version": "v1",
                "applicable_business": [business],
            },
        )
        return PersonnelQualification.objects.create(
            person=person, qualification_type=qtype, version="v1",
            valid_from=self.today - timedelta(days=1),
            valid_until=self.today + timedelta(days=days),
            grant_basis="培训合格证", granted_by=by,
        )

    def apply(self, client, quantity=Decimal("3")):
        resp = client.post("/api/stock-out/", {
            "goods": self.goods.id, "receiver": "办案单位",
            "receiver_dept": "一大队", "quantity": str(quantity),
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        return StockOut.objects.get(pk=resp.json()["data"]["id"])


class ReceivingNodeTest(DutyNodeFixture):
    def test_receiving_blocked_without_qualification(self):
        _, _, client = self.duty_user("A")
        resp = client.post("/api/stock-in/", {
            "goods": self.goods.id, "quantity": "5",
        }, format="json")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("重新分配", resp.json()["message"])
        self.assertEqual(StockIn.objects.count(), 0)
        self.goods.refresh_from_db()
        self.assertEqual(self.goods.quantity, Decimal("10"))
        blocked = QualificationCheck.objects.get(business="receiving", result="blocked")
        self.assertTrue(blocked.snapshot)

    def test_receiving_allowed_with_qualification(self):
        user, person, client = self.duty_user("B")
        self.qualify(person, "receiving", by=user)
        resp = client.post("/api/stock-in/", {
            "goods": self.goods.id, "quantity": "5", "supplier": "省厅",
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        record = StockIn.objects.get()
        self.assertEqual(record.qualification_check.result, "pass")
        self.goods.refresh_from_db()
        self.assertEqual(self.goods.quantity, Decimal("15"))


class ApprovalNodeTest(DutyNodeFixture):
    def test_approval_blocked_then_allowed(self):
        applicant, _, app_client = self.duty_user("C")
        approver, approver_person, approve_client = self.duty_user("D")
        stock_out = self.apply(app_client)

        # 审批人无资质：阻断，申请仍待审批
        blocked = approve_client.post(
            f"/api/stock-out/{stock_out.id}/approval/approve/",
            {"remark": "同意"}, format="json",
        )
        self.assertEqual(blocked.status_code, 403)
        stock_out.refresh_from_db()
        self.assertEqual(stock_out.status, "pending")
        self.assertEqual(Approval.objects.count(), 0)

        # 取得审批资质后通过
        self.qualify(approver_person, "approval", by=approver)
        ok = approve_client.post(
            f"/api/stock-out/{stock_out.id}/approval/approve/",
            {"remark": "同意"}, format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        stock_out.refresh_from_db()
        self.assertEqual(stock_out.status, "approved")
        approval = Approval.objects.get()
        self.assertEqual(approval.qualification_check.result, "pass")

    def test_reject_also_verifies_and_stays_blocked(self):
        _, _, app_client = self.duty_user("E")
        _, _, reject_client = self.duty_user("F")
        stock_out = self.apply(app_client)
        resp = reject_client.post(
            f"/api/stock-out/{stock_out.id}/approval/reject/", {}, format="json"
        )
        self.assertEqual(resp.status_code, 403)
        stock_out.refresh_from_db()
        self.assertEqual(stock_out.status, "pending")


class ReleaseNodeTest(DutyNodeFixture):
    def _approved(self, approver_person_user="G", approver_tag="H"):
        applicant, _, app_client = self.duty_user(approver_person_user)
        approver, approver_person, approve_client = self.duty_user(approver_tag)
        self.qualify(approver_person, "approval")
        stock_out = self.apply(app_client)
        resp = approve_client.post(
            f"/api/stock-out/{stock_out.id}/approval/approve/", {}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        return stock_out

    def test_release_requires_approved_state(self):
        _, _, client = self.duty_user("I")
        stock_out = self.apply(client)
        resp = client.post(f"/api/stock-out/{stock_out.id}/release/", {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_release_allowed_and_decrements_stock(self):
        stock_out = self._approved()
        _, release_person, release_client = self.duty_user("J")
        self.qualify(release_person, "release")
        resp = release_client.post(
            f"/api/stock-out/{stock_out.id}/release/", {}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        stock_out.refresh_from_db()
        self.assertEqual(stock_out.status, "completed")
        self.assertIsNotNone(stock_out.stock_out_time)
        self.assertEqual(stock_out.qualification_check.result, "pass")
        self.goods.refresh_from_db()
        self.assertEqual(self.goods.quantity, Decimal("7"))

    def test_mid_task_expiry_blocks_release_but_keeps_history(self):
        """审批时资质有效；放行前资质过期：放行阻断并提示重新分配，历史审批仍按当时证据解释。"""
        applicant, _, app_client = self.duty_user("K")
        approver, approver_person, approve_client = self.duty_user("L")
        approval_qual = self.qualify(approver_person, "approval")
        stock_out = self.apply(app_client)
        approve_resp = approve_client.post(
            f"/api/stock-out/{stock_out.id}/approval/approve/", {}, format="json"
        )
        self.assertEqual(approve_resp.status_code, 200)
        approval = Approval.objects.get(stock_out=stock_out)

        # 同一值班员持有的放行资质在任务办理期间过期
        guard, guard_person, guard_client = self.duty_user("M")
        release_qual = self.qualify(guard_person, "release", days=30)
        release_qual.valid_until = self.today - timedelta(days=1)
        release_qual.save()

        blocked = guard_client.post(
            f"/api/stock-out/{stock_out.id}/release/", {}, format="json"
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertIn("重新分配", blocked.json()["message"])
        stock_out.refresh_from_db()
        self.assertEqual(stock_out.status, "approved")  # 未完成动作被阻断
        self.assertTrue(stock_out.last_block_reason)
        self.assertIsNotNone(stock_out.last_block_at)
        self.goods.refresh_from_db()
        self.assertEqual(self.goods.quantity, Decimal("10"))

        # 历史审批结论不变
        approval.refresh_from_db()
        self.assertEqual(approval.qualification_check.result, "pass")
        self.assertEqual(
            approval.qualification_check.snapshot["qualifications"][0]["status"], "valid"
        )

        # 重新分配给具备有效资质的值班员后放行成功
        _, new_person, new_client = self.duty_user("N")
        self.qualify(new_person, "release")
        ok = new_client.post(
            f"/api/stock-out/{stock_out.id}/release/", {}, format="json"
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        stock_out.refresh_from_db()
        self.assertEqual(stock_out.status, "completed")
        self.goods.refresh_from_db()
        self.assertEqual(self.goods.quantity, Decimal("7"))

    def test_temporary_exemption_allows_release_and_is_logged(self):
        manager = User.objects.create_user("boss", "pass123456", role="admin")
        stock_out = self._approved()
        _, guard_person, guard_client = self.duty_user("O")
        QualificationExemption.objects.create(
            person=guard_person, business="release", reason="证件补办中",
            basis="带班领导批准 EX-9",
            valid_from=self.today - timedelta(days=1),
            valid_until=self.today + timedelta(days=2),
            approved_by=manager,
        )
        resp = guard_client.post(
            f"/api/stock-out/{stock_out.id}/release/", {}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        stock_out.refresh_from_db()
        self.assertEqual(stock_out.qualification_check.grant_type, "exemption")

    def test_completed_history_unchanged_after_later_revocation(self):
        stock_out = self._approved()
        _, release_person, release_client = self.duty_user("P")
        qual = self.qualify(release_person, "release")
        self.assertEqual(
            release_client.post(
                f"/api/stock-out/{stock_out.id}/release/", {}, format="json"
            ).status_code, 200
        )
        check_id = StockOut.objects.get(pk=stock_out.id).qualification_check_id

        # 事后撤销资质
        qual.status = "revoked"
        qual.revoke_reason = "事后调查"
        qual.save()

        stock_out.refresh_from_db()
        self.assertEqual(stock_out.status, "completed")
        check = QualificationCheck.objects.get(pk=check_id)
        self.assertEqual(check.result, "pass")
        self.assertEqual(check.snapshot["qualifications"][0]["status"], "valid")

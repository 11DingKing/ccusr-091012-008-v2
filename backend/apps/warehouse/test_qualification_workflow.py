"""
出库关键节点资质核验的端到端测试。

重点复现并固化需求：
- 审批、收件、放行三个节点都在“当时”核验资质并留证据；
- 资质过期/撤销时，尚未完成的动作阻断并提示重新分配；
- 历史已完成动作按当时证据解释，事后续证/撤销/过期不追溯既往；
- 临时豁免与重新分配路径可用。
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from apps.qualifications.models import (
    Qualification,
    QualificationCheck,
    QualificationType,
)
from apps.qualifications.services import grant_waiver, revoke_qualification

from .models import Approval, Category, Goods, StockOut, Unit, Variety


class WorkflowFixture(TransactionTestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin', 'pass123456', role='admin')
        self.applicant = User.objects.create_user('applicant', 'pass123456', role='user')
        self.approver = User.objects.create_user('approver', 'pass123456', role='user')
        self.receiver = User.objects.create_user('receiver', 'pass123456', role='user')
        self.guard = User.objects.create_user('guard', 'pass123456', role='user')       # 夜间值班员（将过期）
        self.backup = User.objects.create_user('backup', 'pass123456', role='user')     # 合格接替人

        self.today = timezone.localdate()
        self.approval_type = QualificationType.objects.create(
            code='AP', name='审批资格', version='v1', applicable_business=['approval'])
        self.receive_type = QualificationType.objects.create(
            code='RC', name='收件资格', version='v1', applicable_business=['receive'])
        self.release_type = QualificationType.objects.create(
            code='RL', name='放行资格', version='v1', applicable_business=['release'])

        self._give_qual(self.approver, self.approval_type, self.today + timedelta(days=30))
        self._give_qual(self.receiver, self.receive_type, self.today + timedelta(days=30))

        unit = Unit.objects.create(name='件', created_by=self.admin)
        category = Category.objects.create(name='器材', unit=unit, created_by=self.admin)
        variety = Variety.objects.create(name='终端', category=category, created_by=self.admin)
        self.goods = Goods.objects.create(
            variety=variety, name='执法终端', code='G-1', quantity=Decimal('10'))

    def _give_qual(self, holder, qtype, valid_until, status=Qualification.STATUS_ACTIVE):
        return Qualification.objects.create(
            holder=holder, qualification_type=qtype,
            certificate_no=f'C-{holder.id}-{qtype.id}', version=qtype.version,
            valid_from=self.today - timedelta(days=10), valid_until=valid_until,
            status=status,
        )

    def auth(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {generate_token(user)}')
        return client

    def create_order(self, user=None):
        user = user or self.applicant
        return StockOut.objects.create(
            goods=self.goods, operator=user, receiver='某民警',
            receiver_dept='一线大队', quantity=Decimal('2'),
        )


class FullHappyPathTest(WorkflowFixture):
    def test_three_nodes_pass_with_valid_qualifications_and_deduct_stock(self):
        self._give_qual(self.guard, self.release_type, self.today + timedelta(days=10))
        order = self.create_order()

        r1 = self.auth(self.approver).post(f'/api/stock-out/{order.id}/approval/',
                                           {'action': 'approve'}, format='json')
        self.assertEqual(r1.status_code, 200, r1.json())
        self.assertEqual(r1.json()['data']['status'], 'approved')

        r2 = self.auth(self.receiver).post(f'/api/stock-out/{order.id}/receive/',
                                           {}, format='json')
        self.assertEqual(r2.status_code, 200, r2.json())
        self.assertEqual(r2.json()['data']['status'], 'received')

        r3 = self.auth(self.guard).post(f'/api/stock-out/{order.id}/release/',
                                        {}, format='json')
        self.assertEqual(r3.status_code, 200, r3.json())
        self.assertEqual(r3.json()['data']['status'], 'completed')

        self.goods.refresh_from_db()
        self.assertEqual(self.goods.quantity, Decimal('8'))
        # 三个节点都固化了通过证据
        checks = QualificationCheck.objects.filter(stock_out=order)
        self.assertEqual(checks.count(), 3)
        self.assertTrue(all(c.is_passing for c in checks))


class ExpiredGuardBlocksReleaseTest(WorkflowFixture):
    """事故原型：资质已过期的值班员操作夜间放行，必须被阻断。"""

    def test_expired_qualification_blocks_release_and_flags_reassign(self):
        # 审批、收件已正常完成
        self._give_qual(self.guard, self.release_type,
                        self.today - timedelta(days=1))  # 已过期
        order = self.create_order()
        self.auth(self.approver).post(f'/api/stock-out/{order.id}/approval/',
                                      {'action': 'approve'}, format='json')
        self.auth(self.receiver).post(f'/api/stock-out/{order.id}/receive/',
                                      {}, format='json')

        resp = self.auth(self.guard).post(f'/api/stock-out/{order.id}/release/',
                                          {}, format='json')
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertFalse(body['success'])
        self.assertTrue(body['data']['need_reassign'])
        self.assertEqual(body['data']['business'], 'release')
        self.assertTrue(any('已过期' in r for r in body['data']['reasons']))

        # 状态未推进、库存未扣减
        order.refresh_from_db()
        self.assertEqual(order.status, 'received')
        self.goods.refresh_from_db()
        self.assertEqual(self.goods.quantity, Decimal('10'))

        # 阻断也固化了证据
        blocked = QualificationCheck.objects.filter(
            stock_out=order, business='release', result=QualificationCheck.RESULT_BLOCKED).first()
        self.assertIsNotNone(blocked)
        self.assertTrue(blocked.need_reassign)

    def test_expired_qualification_blocks_approval_too(self):
        """审批节点同样核验（无资质不得审批）。"""
        expired_approver = User.objects.create_user('exp_appr', 'pass123456', role='user')
        self._give_qual(expired_approver, self.approval_type,
                        self.today - timedelta(days=3))
        order = self.create_order()
        resp = self.auth(expired_approver).post(
            f'/api/stock-out/{order.id}/approval/', {'action': 'approve'}, format='json')
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.json()['data']['need_reassign'])
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')


class MidTaskInvalidationTest(WorkflowFixture):
    """任务办理过程中资质失效：尚未完成的动作阻断，可重新分配。"""

    def test_revocation_mid_flow_blocks_later_node(self):
        # 放行人最初有有效资质
        qual = self._give_qual(self.guard, self.release_type,
                               self.today + timedelta(days=20))
        order = self.create_order()
        self.auth(self.approver).post(f'/api/stock-out/{order.id}/approval/',
                                      {'action': 'approve'}, format='json')
        self.auth(self.receiver).post(f'/api/stock-out/{order.id}/receive/',
                                      {}, format='json')

        # 放行前资质被撤销
        revoke_qualification(qual, actor=self.admin, approval_basis='督察现场决定')

        resp = self.auth(self.guard).post(f'/api/stock-out/{order.id}/release/',
                                          {}, format='json')
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.json()['data']['need_reassign'])
        self.assertTrue(any('已撤销' in r for r in resp.json()['data']['reasons']))

        # 已完成的审批/收件不受影响
        order.refresh_from_db()
        self.assertEqual(order.status, 'received')
        self.assertTrue(
            QualificationCheck.objects.filter(
                stock_out=order, business='approval').first().is_passing
        )

    def test_reassign_to_qualified_officer_allows_release(self):
        self._give_qual(self.guard, self.release_type,
                        self.today - timedelta(days=1))  # 过期
        self._give_qual(self.backup, self.release_type,
                        self.today + timedelta(days=30))  # 合格接替
        order = self.create_order()
        self.auth(self.approver).post(f'/api/stock-out/{order.id}/approval/',
                                      {'action': 'approve'}, format='json')
        self.auth(self.receiver).post(f'/api/stock-out/{order.id}/receive/',
                                      {}, format='json')

        # 值班员被阻断
        blocked = self.auth(self.guard).post(f'/api/stock-out/{order.id}/release/',
                                             {}, format='json')
        self.assertEqual(blocked.status_code, 403)

        # 主管把放行重新分配给合格人员
        assign = self.auth(self.admin).post(
            f'/api/stock-out/{order.id}/assign/',
            {'release_operator': self.backup.id}, format='json')
        self.assertEqual(assign.status_code, 200, assign.json())

        released = self.auth(self.backup).post(f'/api/stock-out/{order.id}/release/',
                                               {}, format='json')
        self.assertEqual(released.status_code, 200, released.json())
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.release_operator_id, self.backup.id)

    def test_cannot_reassign_completed_node(self):
        self._give_qual(self.guard, self.release_type, self.today + timedelta(days=5))
        order = self.create_order()
        self.auth(self.approver).post(f'/api/stock-out/{order.id}/approval/',
                                      {'action': 'approve'}, format='json')
        # 审批已完成，不允许改派审批人
        resp = self.auth(self.admin).post(
            f'/api/stock-out/{order.id}/assign/',
            {'approver': self.backup.id}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_non_admin_cannot_reassign(self):
        order = self.create_order()
        resp = self.auth(self.guard).post(
            f'/api/stock-out/{order.id}/assign/',
            {'release_operator': self.backup.id}, format='json')
        self.assertEqual(resp.status_code, 403)


class HistoricalEvidenceTest(WorkflowFixture):
    """历史已完成操作按当时证据解释，事后变化不追溯既往。"""

    def test_completed_release_not_retroactively_invalidated_by_revocation(self):
        qual = self._give_qual(self.guard, self.release_type,
                               self.today + timedelta(days=20))
        order = self.create_order()
        self.auth(self.approver).post(f'/api/stock-out/{order.id}/approval/',
                                      {'action': 'approve'}, format='json')
        self.auth(self.receiver).post(f'/api/stock-out/{order.id}/receive/',
                                      {}, format='json')
        release_resp = self.auth(self.guard).post(
            f'/api/stock-out/{order.id}/release/', {}, format='json')
        self.assertEqual(release_resp.status_code, 200)
        release_check = QualificationCheck.objects.filter(
            stock_out=order, business='release').first()
        self.assertTrue(release_check.is_passing)

        # 事后撤销资质
        revoke_qualification(qual, actor=self.admin, approval_basis='事后处理决定')

        # 出库单仍是已完成；当时的放行证据仍为“核验通过”
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')
        release_check.refresh_from_db()
        self.assertEqual(release_check.result, QualificationCheck.RESULT_PASSED)
        self.assertEqual(release_check.qualification_id, qual.id)


class WaiverPathTest(WorkflowFixture):
    def test_waiver_allows_release_within_window_and_records_evidence(self):
        # 值班员无放行资质，但在豁免窗口内
        order = self.create_order()
        self.auth(self.approver).post(f'/api/stock-out/{order.id}/approval/',
                                      {'action': 'approve'}, format='json')
        self.auth(self.receiver).post(f'/api/stock-out/{order.id}/receive/',
                                      {}, format='json')

        now = timezone.now()
        grant_waiver(
            holder=self.guard, business='release', reason='夜间突发警情',
            actor=self.admin, approval_basis='值班领导 00:20 口头授权',
            valid_from=now - timedelta(minutes=10),
            valid_until=now + timedelta(hours=2),
            stock_out=order,
        )
        resp = self.auth(self.guard).post(f'/api/stock-out/{order.id}/release/',
                                          {}, format='json')
        self.assertEqual(resp.status_code, 200, resp.json())
        check = QualificationCheck.objects.filter(
            stock_out=order, business='release').first()
        self.assertEqual(check.result, QualificationCheck.RESULT_PASSED_WITH_WAIVER)
        self.assertEqual(check.basis, 'waiver')
        self.assertIsNotNone(check.waiver_id)

    def test_expired_waiver_does_not_allow_release(self):
        order = self.create_order()
        self.auth(self.approver).post(f'/api/stock-out/{order.id}/approval/',
                                      {'action': 'approve'}, format='json')
        self.auth(self.receiver).post(f'/api/stock-out/{order.id}/receive/',
                                      {}, format='json')
        now = timezone.now()
        grant_waiver(
            holder=self.guard, business='release', reason='警情',
            actor=self.admin, approval_basis='授权',
            valid_from=now - timedelta(hours=3),
            valid_until=now - timedelta(hours=1),
        )
        resp = self.auth(self.guard).post(f'/api/stock-out/{order.id}/release/',
                                          {}, format='json')
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.json()['data']['need_reassign'])


class SequenceAndAssignmentTest(WorkflowFixture):
    def test_cannot_skip_approval(self):
        order = self.create_order()
        # 未审批直接收件
        resp = self.auth(self.receiver).post(f'/api/stock-out/{order.id}/receive/',
                                             {}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_assigned_node_rejects_other_user(self):
        self._give_qual(self.guard, self.release_type, self.today + timedelta(days=5))
        order = self.create_order()
        # 将审批分配给 approver
        assign = self.auth(self.admin).post(
            f'/api/stock-out/{order.id}/assign/',
            {'approver': self.approver.id}, format='json')
        self.assertEqual(assign.status_code, 200)
        # backup 虽可能无资质，但首先因未被分配而被拒
        resp = self.auth(self.backup).post(
            f'/api/stock-out/{order.id}/approval/', {'action': 'approve'}, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_reject_has_no_qualification_gate_but_is_recorded(self):
        order = self.create_order()
        # approver 无审批资质仍可驳回（驳回不授权）
        resp = self.auth(self.admin).post(
            f'/api/stock-out/{order.id}/approval/',
            {'action': 'reject', 'remark': '单据不符'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.json())
        order.refresh_from_db()
        self.assertEqual(order.status, 'rejected')
        self.assertTrue(Approval.objects.filter(stock_out=order, status='rejected').exists())

    def test_insufficient_stock_blocks_release_after_qualification_passes(self):
        self._give_qual(self.guard, self.release_type, self.today + timedelta(days=5))
        order = self.create_order()
        order.quantity = Decimal('999')
        order.save()
        self.auth(self.approver).post(f'/api/stock-out/{order.id}/approval/',
                                      {'action': 'approve'}, format='json')
        self.auth(self.receiver).post(f'/api/stock-out/{order.id}/receive/',
                                      {}, format='json')
        resp = self.auth(self.guard).post(f'/api/stock-out/{order.id}/release/',
                                          {}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('库存不足', resp.json()['message'])

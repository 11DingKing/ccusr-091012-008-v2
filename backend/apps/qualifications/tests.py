"""
人员资质管理测试。

覆盖：资质类型/证件登记、有效期与时点状态、续证/撤销/豁免留批准依据、
临时豁免的时限与单据范围、以及资格预检接口。
"""
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User

from .models import (
    Qualification,
    QualificationEvent,
    QualificationType,
    QualificationWaiver,
)
from .services import (
    QualificationError,
    evaluate,
    grant_waiver,
    renew_qualification,
    revoke_qualification,
    revoke_waiver,
)


class QualificationFixture(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin', 'pass123456', role='admin')
        self.officer = User.objects.create_user('officer', 'pass123456', role='user')
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {generate_token(self.admin)}')

        self.today = timezone.localdate()
        # 放行授权资质：适用于“放行”业务
        self.release_type = QualificationType.objects.create(
            code='RELEASE-01', name='夜间放行授权', version='2024',
            applicable_business=['release'], created_by=self.admin,
        )
        # 审批资质
        self.approval_type = QualificationType.objects.create(
            code='APPROVE-01', name='出库审批资格', version='2024',
            applicable_business=['approval'], created_by=self.admin,
        )

    def grant_valid(self, user, qtype, valid_until=None):
        return Qualification.objects.create(
            holder=user,
            qualification_type=qtype,
            certificate_no=f'C-{user.id}-{qtype.id}',
            version=qtype.version,
            valid_from=self.today - timedelta(days=30),
            valid_until=valid_until or (self.today + timedelta(days=30)),
            status=Qualification.STATUS_ACTIVE,
        )


class QualificationModelServiceTest(QualificationFixture):
    def test_type_registers_version_and_businesses(self):
        self.assertEqual(self.release_type.version, '2024')
        self.assertEqual(self.release_type.business_labels, ['放行'])
        self.assertIn('夜间放行授权（2024）', str(self.release_type))

    def test_validity_boundary_expires_after_valid_until(self):
        qual = self.grant_valid(self.officer, self.release_type, valid_until=self.today)
        # 到期日当天仍有效，次日过期
        self.assertFalse(qual.is_expired_by_date)
        self.assertTrue(qual.is_currently_valid)
        qual.valid_until = self.today - timedelta(days=1)
        self.assertTrue(qual.is_expired_by_date)
        self.assertFalse(qual.is_currently_valid)

    def test_evaluate_passes_with_valid_qualification(self):
        self.grant_valid(self.officer, self.release_type)
        outcome = evaluate(self.officer, 'release')
        self.assertTrue(outcome['passed'])
        self.assertEqual(outcome['basis'], 'qualification')

    def test_evaluate_blocks_when_no_qualification(self):
        outcome = evaluate(self.officer, 'release')
        self.assertFalse(outcome['passed'])
        self.assertTrue(any('缺少资质' in r for r in outcome['reasons']))

    def test_evaluate_blocks_expired_qualification(self):
        self.grant_valid(self.officer, self.release_type,
                         valid_until=self.today - timedelta(days=1))
        outcome = evaluate(self.officer, 'release')
        self.assertFalse(outcome['passed'])
        self.assertTrue(any('已过期' in r for r in outcome['reasons']))

    def test_evaluate_blocks_revoked_qualification(self):
        qual = self.grant_valid(self.officer, self.release_type)
        revoke_qualification(qual, actor=self.admin, approval_basis='督察决定〔2026〕7号')
        outcome = evaluate(self.officer, 'release')
        self.assertFalse(outcome['passed'])
        self.assertTrue(any('已撤销' in r for r in outcome['reasons']))

    def test_point_in_time_revocation_does_not_change_past(self):
        """撤销发生在核验时点之后：该历史时点仍解释为有效。"""
        qual = self.grant_valid(self.officer, self.release_type)
        revoke_qualification(qual, actor=self.admin, approval_basis='后续撤销决定')
        past = self.today - timedelta(days=1)
        self.assertEqual(qual.status_at(past), Qualification.STATUS_ACTIVE)
        self.assertEqual(qual.status_at(self.today), Qualification.STATUS_REVOKED)

    def test_renew_extends_validity_and_records_basis(self):
        qual = self.grant_valid(
            self.officer, self.release_type,
            valid_until=self.today - timedelta(days=1),  # 已过期
        )
        event = renew_qualification(
            qual,
            new_valid_until=self.today + timedelta(days=180),
            actor=self.admin,
            approval_basis='复训合格证书 PX-2026-15',
            approved_by='张队长',
            new_version='2026',
        )
        qual.refresh_from_db()
        self.assertTrue(qual.is_currently_valid)
        self.assertEqual(qual.version, '2026')
        self.assertEqual(event.event_type, QualificationEvent.TYPE_RENEW)
        self.assertEqual(event.previous_valid_until, self.today - timedelta(days=1))
        self.assertEqual(event.approval_basis, '复训合格证书 PX-2026-15')

    def test_renew_requires_basis_and_later_date(self):
        qual = self.grant_valid(self.officer, self.release_type)
        with self.assertRaises(QualificationError):
            renew_qualification(
                qual, new_valid_until=self.today + timedelta(days=10),
                actor=self.admin, approval_basis='  ',
            )
        with self.assertRaises(QualificationError):
            renew_qualification(
                qual, new_valid_until=qual.valid_until,
                actor=self.admin, approval_basis='依据',
            )

    def test_revoke_requires_basis(self):
        qual = self.grant_valid(self.officer, self.release_type)
        with self.assertRaises(QualificationError):
            revoke_qualification(qual, actor=self.admin, approval_basis='')

    def test_waiver_grants_pass_within_window(self):
        now = timezone.now()
        grant_waiver(
            holder=self.officer, business='release', reason='夜间突发紧急任务',
            actor=self.admin, approval_basis='值班领导口头授权 00:12',
            approved_by='李教导员',
            valid_from=now - timedelta(hours=1),
            valid_until=now + timedelta(hours=2),
        )
        outcome = evaluate(self.officer, 'release')
        self.assertTrue(outcome['passed'])
        self.assertEqual(outcome['basis'], 'waiver')

    def test_waiver_requires_basis_and_reason(self):
        now = timezone.now()
        with self.assertRaises(QualificationError):
            grant_waiver(
                holder=self.officer, business='release', reason='事由',
                actor=self.admin, approval_basis='',
                valid_from=now, valid_until=now + timedelta(hours=1),
            )
        with self.assertRaises(QualificationError):
            grant_waiver(
                holder=self.officer, business='release', reason='',
                actor=self.admin, approval_basis='依据',
                valid_from=now, valid_until=now + timedelta(hours=1),
            )

    def test_waiver_window_expiry_blocks(self):
        now = timezone.now()
        waiver = grant_waiver(
            holder=self.officer, business='release', reason='紧急任务',
            actor=self.admin, approval_basis='授权',
            valid_from=now - timedelta(hours=3),
            valid_until=now - timedelta(hours=1),  # 已过期
        )
        self.assertFalse(waiver.covers_at(now, business='release'))
        self.assertFalse(evaluate(self.officer, 'release')['passed'])

    def test_waiver_scoped_to_stockout_does_not_leak(self,):
        from apps.warehouse.models import Goods, StockOut, Unit, Category, Variety
        unit = Unit.objects.create(name='件', created_by=self.admin)
        category = Category.objects.create(name='器材', unit=unit, created_by=self.admin)
        variety = Variety.objects.create(name='终端', category=category, created_by=self.admin)
        goods = Goods.objects.create(variety=variety, name='终端A', code='G1')
        order1 = StockOut.objects.create(goods=goods, operator=self.officer,
                                         receiver='领用', quantity=1)
        order2 = StockOut.objects.create(goods=goods, operator=self.officer,
                                         receiver='领用', quantity=1)
        now = timezone.now()
        waiver = grant_waiver(
            holder=self.officer, business='release', reason='单据专项豁免',
            actor=self.admin, approval_basis='审批单',
            valid_from=now - timedelta(hours=1),
            valid_until=now + timedelta(hours=1),
            stock_out=order1,
        )
        self.assertTrue(waiver.covers_at(now, business='release', stock_out=order1))
        self.assertFalse(waiver.covers_at(now, business='release', stock_out=order2))

    def test_revoke_waiver_strips_coverage_and_records_event(self):
        now = timezone.now()
        waiver = grant_waiver(
            holder=self.officer, business='release', reason='紧急',
            actor=self.admin, approval_basis='授权',
            valid_from=now - timedelta(hours=1),
            valid_until=now + timedelta(hours=2),
        )
        revoke_waiver(waiver, actor=self.admin, reason='紧急情况解除',
                      approval_basis='撤销决定 CF-3')
        waiver.refresh_from_db()
        self.assertEqual(waiver.status, QualificationWaiver.STATUS_REVOKED)
        self.assertFalse(waiver.covers_at(timezone.now(), business='release'))
        self.assertTrue(
            QualificationEvent.objects.filter(
                event_type=QualificationEvent.TYPE_WAIVER_REVOKE).exists()
        )


class QualificationAPITest(QualificationFixture):
    def test_non_admin_cannot_create_type(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {generate_token(self.officer)}')
        resp = client.post('/api/qualification-types/', {
            'code': 'X', 'name': 'X', 'version': '1',
            'applicable_business': ['release'],
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_admin_creates_type_and_validates_business(self):
        ok = self.client.post('/api/qualification-types/', {
            'code': 'REC-01', 'name': '收件授权', 'version': '2024',
            'applicable_business': ['receive'],
        }, format='json')
        self.assertEqual(ok.status_code, 200, ok.json())
        bad = self.client.post('/api/qualification-types/', {
            'code': 'BAD', 'name': '非法', 'version': '1',
            'applicable_business': ['nope'],
        }, format='json')
        self.assertEqual(bad.status_code, 400)

    def test_register_qualification_then_revoke_via_api(self):
        created = self.client.post('/api/qualifications/', {
            'holder': self.officer.id,
            'qualification_type': self.release_type.id,
            'certificate_no': 'CERT-9',
            'valid_from': str(self.today - timedelta(days=5)),
            'valid_until': str(self.today + timedelta(days=5)),
        }, format='json')
        self.assertEqual(created.status_code, 200, created.json())
        qual_id = created.json()['data']['id']

        # 撤销必须带依据
        no_basis = self.client.post(f'/api/qualifications/{qual_id}/revoke/',
                                    {}, format='json')
        self.assertEqual(no_basis.status_code, 400)

        revoked = self.client.post(f'/api/qualifications/{qual_id}/revoke/', {
            'approval_basis': '督察通报第3期', 'approved_by': '王政委',
        }, format='json')
        self.assertEqual(revoked.status_code, 200, revoked.json())
        self.assertEqual(revoked.json()['data']['qualification']['status'], 'revoked')

    def test_renew_via_api(self):
        qual = self.grant_valid(self.officer, self.release_type,
                                valid_until=self.today - timedelta(days=2))
        resp = self.client.post(f'/api/qualifications/{qual.id}/renew/', {
            'new_valid_until': str(self.today + timedelta(days=200)),
            'approval_basis': '年度复训合格',
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.json())
        self.assertTrue(resp.json()['data']['qualification']['is_currently_valid'])

    def test_grant_waiver_via_api_and_list_events(self):
        resp = self.client.post('/api/waivers/', {
            'holder': self.officer.id,
            'business': 'release',
            'reason': '夜间应急',
            'approval_basis': '值班领导授权',
            'valid_from': (timezone.now() - timedelta(hours=1)).isoformat(),
            'valid_until': (timezone.now() + timedelta(hours=3)).isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.json())

        events = self.client.get('/api/qualification-events/?event_type=waiver')
        self.assertEqual(events.status_code, 200)
        self.assertEqual(events.json()['data']['total'], 1)
        self.assertEqual(events.json()['data']['list'][0]['approval_basis'], '值班领导授权')

    def test_eligibility_endpoint(self):
        # 无资质：不合格
        resp = self.client.get(
            f'/api/eligibility/?user_id={self.officer.id}&business=release')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['data']['eligible'])

        # 登记有效资质后：合格
        self.grant_valid(self.officer, self.release_type)
        resp = self.client.get(
            f'/api/eligibility/?user_id={self.officer.id}&business=release')
        self.assertTrue(resp.json()['data']['eligible'])

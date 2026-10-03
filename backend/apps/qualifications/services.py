"""
资质核验与状态变更服务。

核心原则：
1. 关键节点（审批、收件、放行）在动作发生“当时”核验资质，并把结论与证据快照固化；
2. 资质在任务办理过程中失效时，尚未完成的动作一律阻断并提示重新分配；
3. 历史已完成操作只按当时固化的证据解释——事后续证、撤销、过期不追溯既往。
"""
from django.db import transaction
from django.utils import timezone

from .models import (
    Qualification,
    QualificationCheck,
    QualificationEvent,
    QualificationType,
    QualificationWaiver,
)


class QualificationError(Exception):
    """资质业务错误。"""


def required_types_for(business):
    """某业务节点要求的全部启用资质类型。"""
    return [
        qt for qt in QualificationType.objects.filter(is_active=True)
        if business in (qt.applicable_business or [])
    ]


def _find_waiver(holder, business, at, stock_out, qualification_type):
    """查找在 at 时点覆盖指定人员/业务/资质类型的有效豁免（含全局豁免）。"""
    waivers = holder.qualification_waivers.all()
    for waiver in waivers:
        if waiver.qualification_type_id is not None \
                and waiver.qualification_type_id != qualification_type.id:
            continue
        if waiver.covers_at(at, business=business, stock_out=stock_out):
            return waiver
    return None


def evaluate(holder, business, *, at=None, stock_out=None):
    """
    评估某人员在指定时点办理某业务是否具备资质（不写库）。

    返回 dict：passed / basis / qualification / waiver / reasons / snapshot。
    所有判定都基于 at 时点，at 之后的过期、撤销不影响结论。
    """
    at = at or timezone.now()
    at_date = timezone.localdate(at)

    snapshot = []
    reasons = []
    basis_qualification = None
    relied_waiver = None
    all_by_qualification = True

    for qt in required_types_for(business):
        entry = {
            'type_id': qt.id,
            'type_code': qt.code,
            'type_name': qt.name,
            'type_version': qt.version,
            'satisfied': False,
            'via': None,
            'qualification': None,
            'waiver': None,
        }

        valid_qual = None
        for qual in holder.qualifications.filter(qualification_type=qt):
            if qual.status_at(at_date) == Qualification.STATUS_ACTIVE:
                valid_qual = qual
                break

        if valid_qual is not None:
            entry['satisfied'] = True
            entry['via'] = 'qualification'
            entry['qualification'] = {
                'id': valid_qual.id,
                'certificate_no': valid_qual.certificate_no,
                'version': valid_qual.version or qt.version,
                'valid_from': str(valid_qual.valid_from),
                'valid_until': str(valid_qual.valid_until),
                'status_at_check': valid_qual.status_at(at_date),
            }
            if basis_qualification is None:
                basis_qualification = valid_qual
        else:
            all_by_qualification = False
            waiver = _find_waiver(holder, business, at, stock_out, qt)
            if waiver is not None:
                entry['satisfied'] = True
                entry['via'] = 'waiver'
                entry['waiver'] = {
                    'id': waiver.id,
                    'reason': waiver.reason,
                    'approved_by': waiver.approved_by_id,
                    'valid_from': timezone.localtime(waiver.valid_from).isoformat(),
                    'valid_until': timezone.localtime(waiver.valid_until).isoformat(),
                }
                if relied_waiver is None:
                    relied_waiver = waiver
            else:
                # 给出具体缺失原因（过期/撤销/未持证）
                current = holder.qualifications.filter(qualification_type=qt).first()
                if current is None:
                    reasons.append(f'缺少资质：{qt.name}（{qt.version}）')
                else:
                    status_label = dict(Qualification.STATUS_CHOICES).get(
                        current.status_at(at_date), current.status_at(at_date)
                    )
                    reasons.append(
                        f'资质 {qt.name}（{qt.version}）当前不可用：{status_label}'
                        f'，有效期至 {current.valid_until}'
                    )

        snapshot.append(entry)

    required_count = len(snapshot)
    satisfied_count = sum(1 for e in snapshot if e['satisfied'])
    passed = required_count > 0 and satisfied_count == required_count

    # 该业务未配置任何资质要求时不设卡（无要求即通过）
    if required_count == 0:
        return {
            'passed': True,
            'basis': 'qualification',
            'qualification': None,
            'waiver': None,
            'reasons': [],
            'snapshot': snapshot,
            'no_requirement': True,
        }

    return {
        'passed': passed,
        'basis': 'qualification' if (passed and all_by_qualification) else ('waiver' if passed else ''),
        'qualification': basis_qualification if passed and all_by_qualification else None,
        'waiver': relied_waiver if passed and not all_by_qualification else None,
        'reasons': reasons,
        'snapshot': snapshot,
        'no_requirement': False,
    }


@transaction.atomic
def verify_and_record(stock_out, business, assignee, *, at=None):
    """
    在关键节点执行核验并固化证据。

    无论通过与否都写入一条 QualificationCheck；返回该记录。
    调用方依据 check.is_passing 决定放行还是阻断。
    """
    at = at or timezone.now()
    outcome = evaluate(assignee, business, at=at, stock_out=stock_out)

    if outcome['passed']:
        if outcome['basis'] == 'waiver':
            result = QualificationCheck.RESULT_PASSED_WITH_WAIVER
            basis = 'waiver'
        else:
            result = QualificationCheck.RESULT_PASSED
            basis = 'qualification'
        need_reassign = False
    else:
        result = QualificationCheck.RESULT_BLOCKED
        basis = ''
        need_reassign = True

    check = QualificationCheck.objects.create(
        stock_out=stock_out,
        business=business,
        assignee=assignee,
        result=result,
        basis=basis,
        qualification=outcome.get('qualification'),
        waiver=outcome.get('waiver'),
        evidence_snapshot={
            'checked_at': timezone.localtime(at).isoformat(),
            'assignee_id': assignee.id,
            'assignee_name': assignee.username,
            'required': outcome['snapshot'],
            'no_requirement': outcome.get('no_requirement', False),
        },
        failure_reasons=outcome['reasons'],
        need_reassign=need_reassign,
        checked_at=at,
    )
    return check


def latest_passing_check(stock_out, business):
    """取该节点最近一次通过核验的证据（用于解释历史、防止越序办理）。"""
    return stock_out.qualification_checks.filter(
        business=business,
        result__in=[QualificationCheck.RESULT_PASSED, QualificationCheck.RESULT_PASSED_WITH_WAIVER],
    ).first()


# ==================== 续证 / 撤销 / 豁免（均留批准依据） ====================

@transaction.atomic
def renew_qualification(qualification, *, new_valid_until, actor, approval_basis,
                        approved_by='', new_version=None, new_certificate_no=None, detail=''):
    """续证：延长有效期并记录批准依据。"""
    if not approval_basis or not approval_basis.strip():
        raise QualificationError('续证必须填写批准依据')
    if qualification.status == Qualification.STATUS_REVOKED:
        raise QualificationError('该证件已撤销，不能在原记录上续证，请重新登记发证')
    if new_valid_until <= qualification.valid_until:
        raise QualificationError('续证后的有效期应晚于原有效期')

    previous = qualification.valid_until
    qualification.valid_until = new_valid_until
    qualification.status = Qualification.STATUS_ACTIVE
    if new_version:
        qualification.version = new_version
    if new_certificate_no:
        qualification.certificate_no = new_certificate_no
    qualification.save()

    return QualificationEvent.objects.create(
        qualification=qualification,
        event_type=QualificationEvent.TYPE_RENEW,
        actor=actor,
        approval_basis=approval_basis,
        approved_by=approved_by,
        effective_at=timezone.now(),
        previous_valid_until=previous,
        new_valid_until=new_valid_until,
        detail=detail,
    )


@transaction.atomic
def revoke_qualification(qualification, *, actor, approval_basis, approved_by='', detail=''):
    """撤销资质：置为已撤销并记录批准依据。"""
    if not approval_basis or not approval_basis.strip():
        raise QualificationError('撤销必须填写批准依据')

    now = timezone.now()
    qualification.status = Qualification.STATUS_REVOKED
    qualification.revoked_at = now
    qualification.save()

    return QualificationEvent.objects.create(
        qualification=qualification,
        event_type=QualificationEvent.TYPE_REVOKE,
        actor=actor,
        approval_basis=approval_basis,
        approved_by=approved_by,
        effective_at=now,
        previous_valid_until=qualification.valid_until,
        new_valid_until=None,
        detail=detail,
    )


@transaction.atomic
def grant_waiver(*, holder, business, reason, actor, approval_basis, valid_from, valid_until,
                 approved_by='', qualification_type=None, stock_out=None, detail=''):
    """批准临时豁免：创建豁免并记录批准依据。"""
    if not reason or not reason.strip():
        raise QualificationError('豁免必须填写事由')
    if not approval_basis or not approval_basis.strip():
        raise QualificationError('临时豁免必须填写批准依据')
    if valid_until <= valid_from:
        raise QualificationError('豁免截止时间应晚于开始时间')

    waiver = QualificationWaiver.objects.create(
        holder=holder,
        business=business,
        qualification_type=qualification_type,
        reason=reason,
        approved_by=actor,
        valid_from=valid_from,
        valid_until=valid_until,
        stock_out=stock_out,
    )
    QualificationEvent.objects.create(
        waiver=waiver,
        event_type=QualificationEvent.TYPE_WAIVER,
        business=business,
        actor=actor,
        approval_basis=approval_basis,
        approved_by=approved_by,
        effective_at=timezone.now(),
        detail=detail,
    )
    return waiver


@transaction.atomic
def revoke_waiver(waiver, *, actor, reason, approval_basis, detail=''):
    """撤销临时豁免并记录依据。"""
    if not reason or not reason.strip():
        raise QualificationError('撤销豁免必须填写原因')
    if not approval_basis or not approval_basis.strip():
        raise QualificationError('撤销豁免必须填写批准依据')

    now = timezone.now()
    waiver.status = QualificationWaiver.STATUS_REVOKED
    waiver.revoked_at = now
    waiver.revoked_by = actor
    waiver.revoke_reason = reason
    waiver.save()

    QualificationEvent.objects.create(
        waiver=waiver,
        event_type=QualificationEvent.TYPE_WAIVER_REVOKE,
        business=waiver.business,
        actor=actor,
        approval_basis=approval_basis,
        effective_at=now,
        detail=detail,
    )
    return waiver

"""
资质核验领域服务。

关键原则：
1. 审批、收件、放行三个节点执行前，按“当时”的资质状态核验；
2. 核验结果连同资质快照固化保存，事后续证、过期或撤销均不改变历史结论；
3. 核验不通过时抛出业务异常阻断当前动作，并提示重新分配具备有效资质的值班员。
"""
import logging

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import BusinessException

from .models import (
    BUSINESS_LABELS,
    PersonnelQualification,
    QualificationCheck,
    QualificationExemption,
    StockOutPerson,
)

logger = logging.getLogger('apps')


class QualificationInvalidError(BusinessException):
    """资质核验未通过，动作阻断"""

    def __init__(self, message):
        super().__init__(message, code=403)


def resolve_duty_person(user):
    """将登录账号解析为已登记、在职的值班（出库）人员。"""
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    return (
        StockOutPerson.objects
        .filter(binduser=user, is_active=True)
        .first()
    )


def _build_snapshot(person, business, on_date, qualifications, exemptions):
    """构造核验当时的资质状态快照。"""
    return {
        'checked_date': on_date.isoformat(),
        'business': business,
        'business_label': BUSINESS_LABELS.get(business, business),
        'person_id': person.id,
        'person_name': person.name,
        'police_no': person.police_no,
        'qualifications': [
            {
                'id': q.id,
                'qualification_type_id': q.qualification_type_id,
                'qualification_type': q.qualification_type.name,
                'code': q.qualification_type.code,
                'version': q.version,
                'certificate_no': q.certificate_no,
                'valid_from': q.valid_from.isoformat(),
                'valid_until': q.valid_until.isoformat(),
                'status': q.status,
                'applies_to_business': q.applies_to(business),
                'effective_on_check_date': q.is_effective_on(on_date),
            }
            for q in qualifications
        ],
        'exemptions': [
            {
                'id': e.id,
                'reason': e.reason,
                'basis': e.basis,
                'valid_from': e.valid_from.isoformat(),
                'valid_until': e.valid_until.isoformat(),
                'effective_on_check_date': e.is_effective_on(on_date),
            }
            for e in exemptions
        ],
    }


def evaluate(person, business, on_date=None):
    """
    评估人员在指定业务节点、指定日期的资质状态。

    返回 (grant_type, grant_obj, snapshot)：
      grant_type 为 'qualification' / 'exemption' / ''（无有效依据）。
    """
    if on_date is None:
        on_date = timezone.localdate()

    qualifications = list(
        PersonnelQualification.objects
        .filter(person=person)
        .select_related('qualification_type')
        .order_by('-valid_until')
    )
    exemptions = list(
        QualificationExemption.objects.filter(person=person, business=business)
    )

    effective_qualification = next(
        (q for q in qualifications
         if q.applies_to(business) and q.is_effective_on(on_date)),
        None,
    )
    effective_exemption = next(
        (e for e in exemptions if e.is_effective_on(on_date)),
        None,
    )

    snapshot = _build_snapshot(person, business, on_date, qualifications, exemptions)

    if effective_qualification:
        return 'qualification', effective_qualification, snapshot
    if effective_exemption:
        return 'exemption', effective_exemption, snapshot
    return '', None, snapshot


def _block_reason(person, business, on_date):
    """给出阻断的具体原因，便于值班台重新分配。"""
    business_label = BUSINESS_LABELS.get(business, business)
    held = list(
        PersonnelQualification.objects
        .filter(person=person)
        .select_related('qualification_type')
    )
    relevant = [q for q in held if q.applies_to(business)]
    if not relevant:
        return (
            f'值班员{person.name}未登记适用于“{business_label}”的资质，'
            f'该动作已阻断，请重新分配具备有效资质的值班员'
        )
    revoked = [q for q in relevant if q.status == 'revoked']
    expired = [q for q in relevant if q.status == 'valid' and q.valid_until < on_date]
    not_started = [
        q for q in relevant
        if q.status == 'valid' and q.valid_from > on_date
    ]
    parts = []
    if revoked:
        parts.append('已被撤销')
    if expired:
        until = min(q.valid_until for q in expired).isoformat()
        parts.append(f'已于 {until} 过期')
    if not_started:
        start = min(q.valid_from for q in not_started).isoformat()
        parts.append(f'新证 {start} 才生效')
    cause = '、'.join(parts) or '无有效资质'
    return (
        f'{person.name}的“{business_label}”资质{cause}，'
        f'该动作已阻断，请重新分配具备有效资质的值班员'
    )


def verify_qualification(user, business, on_date=None, save=True):
    """
    在关键节点执行资质核验。

    无论通过与否都固化一条 QualificationCheck（含当时状态快照）；
    不通过时抛出 QualificationInvalidError 阻断业务动作。
    返回 QualificationCheck。
    """
    if on_date is None:
        on_date = timezone.localdate()

    person = resolve_duty_person(user)
    if person is None:
        message = (
            '当前账号未登记为在职值班人员，无法核验资质，'
            '该动作已阻断，请重新分配具备有效资质的值班员'
        )
        check = QualificationCheck(
            person=None, user=user, business=business,
            result='blocked', detail=message, snapshot={
                'checked_date': on_date.isoformat(),
                'business': business,
                'business_label': BUSINESS_LABELS.get(business, business),
            },
        )
        if save:
            check.save()
        logger.warning('Qualification blocked: user=%s business=%s reason=unregistered',
                       getattr(user, 'username', None), business)
        raise QualificationInvalidError(message)

    grant_type, grant, snapshot = evaluate(person, business, on_date)

    if grant_type == 'qualification':
        result, detail = 'pass', (
            f'依据有效资质《{grant.qualification_type.name}》{grant.version} '
            f'（有效期至 {grant.valid_until.isoformat()}）放行'
        )
    elif grant_type == 'exemption':
        result, detail = 'pass', (
            f'依据临时豁免（{grant.basis}，有效期至 '
            f'{grant.valid_until.isoformat()}）放行'
        )
    else:
        result, detail = 'blocked', _block_reason(person, business, on_date)

    check = QualificationCheck(
        person=person,
        user=user,
        business=business,
        result=result,
        grant_type=grant_type,
        qualification=grant if grant_type == 'qualification' else None,
        exemption=grant if grant_type == 'exemption' else None,
        snapshot=snapshot,
        detail=detail,
    )
    if save:
        check.save()

    if result == 'blocked':
        logger.warning('Qualification blocked: person=%s business=%s', person.name, business)
        raise QualificationInvalidError(detail)

    logger.info('Qualification passed: person=%s business=%s grant=%s',
                person.name, business, grant_type)
    return check


def run_verified(business, user, func):
    """
    先核验资质再执行业业动作。

    核验（含阻断记录）独立落库：这样资质不通过抛出异常时，阻断证据不会被
    业务事务回滚；核验通过后业务动作在独立事务中执行。
    func 接收 QualificationCheck，返回业务对象。
    """
    check = verify_qualification(user, business)
    with transaction.atomic():
        return func(check)

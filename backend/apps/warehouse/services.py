"""
出库工作流服务：申请 → 审批 → 收件 → 放行。

在审批、收件、放行三个关键节点固定核验“当时”资质：
- 核验通过：固化证据并推进状态；
- 核验不通过：固化阻断证据，动作不生效，返回 need_reassign 提示重新分配；
- 已完成节点不因事后续证/撤销而改变，历史一律按当时 QualificationCheck 解释。
"""
from django.db import transaction
from django.utils import timezone

from apps.authentication.models import User
from apps.qualifications.services import evaluate, verify_and_record

from .models import Approval, StockOut


# 业务节点 -> (执行该节点所需的出库单状态, 成功后的出库单状态)
NODE_FLOW = {
    'approval': {'from': 'pending', 'to': 'approved'},
    'receive': {'from': 'approved', 'to': 'received'},
    'release': {'from': 'received', 'to': 'completed'},
}

NODE_ASSIGNEE_FIELDS = {
    'approval': 'approver',
    'receive': 'intake_operator',
    'release': 'release_operator',
}

NODE_DONE_FIELDS = {
    'approval': 'approved_at',
    'receive': 'received_at',
    'release': 'released_at',
}


class WorkflowError(Exception):
    def __init__(self, message, code=400, extra=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.extra = extra or {}


def _eligible_candidates(business, stock_out, limit=10):
    """当前时点可办理某节点的在职人员，供阻断后重新分配选择。"""
    candidates = []
    for user in User.objects.filter(is_active=True).order_by('id'):
        outcome = evaluate(user, business, stock_out=stock_out)
        if outcome['passed']:
            candidates.append({'id': user.id, 'username': user.username, 'basis': outcome['basis']})
            if len(candidates) >= limit:
                break
    return candidates


def assign_operators(stock_out, *, approver=None, intake_operator=None, release_operator=None):
    """
    分配/重新分配三个节点责任人。

    只允许调整“尚未完成”的节点；已完成节点的责任人作为历史证据的一部分，
    不得替换，以保证“当时是谁办理”可追溯。
    """
    changed = []
    for business, user_id in (
        ('approval', approver),
        ('receive', intake_operator),
        ('release', release_operator),
    ):
        if user_id is None:
            continue
        if getattr(stock_out, NODE_DONE_FIELDS[business]) is not None:
            raise WorkflowError(f'{dict(StockOut.STATUS_CHOICES).get(NODE_FLOW[business]["to"])}节点已完成，不得改派', code=400)
        user = User.objects.filter(pk=user_id).first()
        if not user:
            raise WorkflowError('指定人员不存在', code=404)
        if not user.is_active:
            raise WorkflowError(f'人员 {user.username} 账号已停用，无法分配', code=400)
        setattr(stock_out, NODE_ASSIGNEE_FIELDS[business], user)
        changed.append(business)
    stock_out.save()
    return changed


@transaction.atomic
def _apply_transition(stock_out, business, actor, check, *, now, remark=''):
    """资质通过后推进状态（独立事务，与取证分离）。"""
    flow = NODE_FLOW[business]
    # 行锁 + 状态复核，避免并发请求重复办理同一节点（生产库生效；SQLite 为空操作）
    locked = StockOut.objects.select_for_update().select_related('goods').get(pk=stock_out.pk)
    if locked.status != flow['from']:
        label = dict(StockOut.STATUS_CHOICES).get(locked.status, locked.status)
        raise WorkflowError(f'单据状态已变为「{label}」，请刷新后重试')

    if business == 'approval':
        locked.status = 'approved'
        locked.approver = actor
        locked.approved_at = now
        Approval.objects.create(stock_out=locked, approver=actor, status='approved', remark=remark)
    elif business == 'receive':
        locked.status = 'received'
        locked.intake_operator = actor
        locked.received_at = now
    elif business == 'release':
        goods = locked.goods
        if goods.quantity < locked.quantity:
            raise WorkflowError(
                f'库存不足：当前 {goods.quantity}，申请出库 {locked.quantity}', code=400,
            )
        locked.status = 'completed'
        locked.release_operator = actor
        locked.released_at = now
        locked.stock_out_time = now
        if remark:
            locked.remark = (locked.remark or '') + f'\n[放行备注] {remark}'

        # 库存扣减与状态推进同生共死
        goods.quantity = goods.quantity - locked.quantity
        goods.save(update_fields=['quantity'])

    locked.save()
    # 同步调用方持有的实例，便于直接序列化
    stock_out.status = locked.status
    stock_out.approved_at = locked.approved_at
    stock_out.received_at = locked.received_at
    stock_out.released_at = locked.released_at
    return locked


def perform_node(stock_out, business, actor, *, remark=''):
    """
    执行关键节点动作（审批通过 / 收件 / 放行）。

    返回 (stock_out, check)。资质不满足时抛出 WorkflowError（code=403，
    extra.need_reassign=True），出库单状态保持不变，但“阻断证据”已独立落库保留。
    """
    if business not in NODE_FLOW:
        raise WorkflowError('未知业务节点')

    flow = NODE_FLOW[business]

    # 1) 账号仍须启用（认证层已挡一层，这里再兜底）
    if not actor.is_active:
        raise WorkflowError('操作账号已停用，请重新分配', code=403,
                            extra={'need_reassign': True, 'reasons': ['账号已停用']})

    # 2) 节点归属：已分配他人则不得越权办理
    assigned = getattr(stock_out, NODE_ASSIGNEE_FIELDS[business])
    if assigned is not None and assigned.id != actor.id:
        raise WorkflowError(
            f'该节点已分配给 {assigned.username}，请由其办理或重新分配',
            code=403,
        )

    # 3) 流程顺序校验（未完成动作必须在前置节点完成后才能办理）
    if stock_out.status != flow['from']:
        label = dict(StockOut.STATUS_CHOICES).get(stock_out.status, stock_out.status)
        raise WorkflowError(f'当前状态为「{label}」，不能办理该节点')

    # 4) 业务前置条件（如库存）在取证之前校验，避免产生“通过却未成行”的噪声证据
    if business == 'release' and stock_out.goods.quantity < stock_out.quantity:
        raise WorkflowError(
            f'库存不足：当前 {stock_out.goods.quantity}，申请出库 {stock_out.quantity}', code=400,
        )

    # 5) 固定核验“当时”资质并固化证据（独立事务提交，不受后续阻断回滚影响）
    check = verify_and_record(stock_out, business, actor)
    if not check.is_passing:
        raise WorkflowError(
            '资质核验未通过，该动作已阻断，请重新分配给具备有效资质的人员',
            code=403,
            extra={
                'need_reassign': True,
                'business': business,
                'assignee': actor.id,
                'reasons': check.failure_reasons,
                'check_id': check.id,
                'eligible_candidates': _eligible_candidates(business, stock_out),
            },
        )

    # 6) 通过：推进状态、记录责任人与时间
    stock_out = _apply_transition(stock_out, business, actor, check,
                                  now=timezone.now(), remark=remark)
    return stock_out, check


@transaction.atomic
def reject_stock_out(stock_out, actor, *, remark=''):
    """
    审批驳回：不授予任何后续权限，因此不设资质门槛，
    但记录审批人与意见，节点责任同样可追溯。
    """
    assigned_id = stock_out.approver_id
    if assigned_id and assigned_id != actor.id:
        raise WorkflowError(
            f'该审批已分配给 {stock_out.approver.username}，请由其办理或重新分配', code=403,
        )
    if stock_out.status != 'pending':
        label = dict(StockOut.STATUS_CHOICES).get(stock_out.status, stock_out.status)
        raise WorkflowError(f'当前状态为「{label}」，不能驳回')

    stock_out.status = 'rejected'
    stock_out.approver = actor
    stock_out.approved_at = timezone.now()
    stock_out.save()
    Approval.objects.create(stock_out=stock_out, approver=actor, status='rejected', remark=remark)
    return stock_out

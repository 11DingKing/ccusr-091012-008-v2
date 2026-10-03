"""
人员资质管理模型。

本应用解决“只校验账号启用、不核对培训授权证件”的缺口：
- QualificationType：登记资质类型、版本及适用业务；
- Qualification：某人在某资质类型下的证件及其有效期；
- QualificationWaiver：针对特定业务/单据的临时豁免；
- QualificationEvent：续证、撤销、临时豁免的批准依据（不可物理删除）；
- QualificationCheck：审批、收件、放行关键节点的“当时”资质核验证据。

所有关键节点都只依据核验发生时点已固化的证据解释，事后续证或撤销不追溯既往。
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


# 资质适用的业务节点（与出库流程的关键节点对应）
BUSINESS_APPROVAL = 'approval'
BUSINESS_RECEIVE = 'receive'
BUSINESS_RELEASE = 'release'

BUSINESS_CHOICES = [
    (BUSINESS_APPROVAL, '审批'),
    (BUSINESS_RECEIVE, '收件'),
    (BUSINESS_RELEASE, '放行'),
]

# 需要核验资质的全部业务节点（顺序即办理顺序）
QUALIFIED_BUSINESSES = [BUSINESS_APPROVAL, BUSINESS_RECEIVE, BUSINESS_RELEASE]


class QualificationType(models.Model):
    """资质类型：登记类型、版本与适用业务。"""

    code = models.CharField('资质类型编码', max_length=50, unique=True)
    name = models.CharField('资质类型名称', max_length=100)
    version = models.CharField('版本', max_length=30)
    applicable_business = models.JSONField(
        '适用业务',
        default=list,
        help_text='该资质适用的业务节点，取值为 approval/receive/release',
    )
    description = models.TextField('说明', blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='created_qualification_types', verbose_name='创建人',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'qa_qualification_type'
        verbose_name = '资质类型'
        verbose_name_plural = verbose_name
        ordering = ['code']

    def __str__(self):
        return f"{self.name}（{self.version}）"

    @property
    def business_labels(self):
        labels = dict(BUSINESS_CHOICES)
        return [labels.get(b, b) for b in (self.applicable_business or [])]


class Qualification(models.Model):
    """人员资质证件：某人持有的某类型资质及其有效期。"""

    STATUS_ACTIVE = 'active'
    STATUS_EXPIRED = 'expired'
    STATUS_REVOKED = 'revoked'

    STATUS_CHOICES = [
        (STATUS_ACTIVE, '有效'),
        (STATUS_EXPIRED, '已过期'),
        (STATUS_REVOKED, '已撤销'),
    ]

    holder = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='qualifications', verbose_name='持证人员',
    )
    qualification_type = models.ForeignKey(
        QualificationType, on_delete=models.PROTECT,
        related_name='qualifications', verbose_name='资质类型',
    )
    certificate_no = models.CharField('证件编号', max_length=80, blank=True)
    version = models.CharField('证件版本', max_length=30, blank=True)
    valid_from = models.DateField('生效日期')
    valid_until = models.DateField('有效期至')
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    revoked_at = models.DateTimeField('撤销时间', null=True, blank=True, editable=False)
    issued_by = models.CharField('发证/授权机关', max_length=150, blank=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'qa_qualification'
        verbose_name = '人员资质'
        verbose_name_plural = verbose_name
        ordering = ['-valid_until']
        constraints = [
            models.UniqueConstraint(
                fields=['holder', 'qualification_type', 'certificate_no'],
                name='uniq_qualification_holder_type_cert',
            ),
        ]

    def __str__(self):
        return f"{self.holder} - {self.qualification_type.name}（至{self.valid_until}）"

    @property
    def is_expired_by_date(self):
        """按有效期判断是否过期（不含当天，到期日当天仍有效）。"""
        return self.valid_until < timezone.localdate()

    @property
    def is_currently_valid(self):
        """当前时点是否有效：状态正常且在有效期内。"""
        return self.status == self.STATUS_ACTIVE and not self.is_expired_by_date

    def status_at(self, at):
        """
        指定时点的资质状态（时点语义）。

        历史已完成动作按当时证据解释：在核验时点之后发生的过期或撤销，
        不改变该时点的结论。
        """
        if self.status == self.STATUS_REVOKED and self.revoked_at is not None:
            if timezone.localdate(self.revoked_at) <= at:
                return self.STATUS_REVOKED
        if self.valid_until < at:
            return self.STATUS_EXPIRED
        if self.valid_from > at:
            return 'not_yet_effective'
        if self.status == self.STATUS_REVOKED:
            # 撤销发生在该时点之后：时点上仍视为有效
            return self.STATUS_ACTIVE
        return self.status


class QualificationWaiver(models.Model):
    """临时豁免：在限定时间/单据范围内允许无资质办理，须留批准依据。"""

    STATUS_ACTIVE = 'active'
    STATUS_REVOKED = 'revoked'
    STATUS_EXPIRED = 'expired'

    holder = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='qualification_waivers', verbose_name='被豁免人员',
    )
    business = models.CharField('适用业务', max_length=20, choices=BUSINESS_CHOICES)
    qualification_type = models.ForeignKey(
        QualificationType, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='waivers', verbose_name='豁免的资质类型',
        help_text='为空表示豁免该业务要求的全部资质',
    )
    reason = models.TextField('豁免事由')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='approved_waivers', verbose_name='批准人',
    )
    approved_at = models.DateTimeField('批准时间', auto_now_add=True)
    valid_from = models.DateTimeField('豁免开始时间')
    valid_until = models.DateTimeField('豁免截止时间')
    stock_out = models.ForeignKey(
        'warehouse.StockOut', on_delete=models.CASCADE, null=True, blank=True,
        related_name='qualification_waivers', verbose_name='关联出库单',
        help_text='为空表示在时限内对该人员的同类业务普遍有效',
    )
    status = models.CharField('状态', max_length=20, default=STATUS_ACTIVE)
    revoked_at = models.DateTimeField('撤销时间', null=True, blank=True, editable=False)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='revoked_waivers', verbose_name='撤销人',
    )
    revoke_reason = models.TextField('撤销原因', blank=True)

    class Meta:
        db_table = 'qa_qualification_waiver'
        verbose_name = '资质临时豁免'
        verbose_name_plural = verbose_name
        ordering = ['-approved_at']

    def __str__(self):
        return f"{self.holder} - {self.get_business_display()}豁免"

    def covers_at(self, at, *, business, stock_out=None):
        """该豁免在指定时点是否覆盖给定业务（及可选单据）。"""
        at = timezone.localtime(at)
        # 撤销：撤销时点之后不再覆盖；撤销时点之前（历史）仍视为有效
        if self.status == self.STATUS_REVOKED and self.revoked_at is not None \
                and timezone.localtime(self.revoked_at) <= at:
            return False
        if self.business != business:
            return False
        if not (timezone.localtime(self.valid_from) <= at <= timezone.localtime(self.valid_until)):
            return False
        if self.stock_out_id is not None:
            if stock_out is None or self.stock_out_id != getattr(stock_out, 'id', stock_out):
                return False
        return True


class QualificationEvent(models.Model):
    """
    资质事件：续证、撤销、豁免、豁免撤销的批准依据。

    只追加（append-only），不提供物理删除，保证每一次状态变化都可追溯。
    """

    TYPE_RENEW = 'renew'
    TYPE_REVOKE = 'revoke'
    TYPE_WAIVER = 'waiver'
    TYPE_WAIVER_REVOKE = 'waiver_revoke'

    TYPE_CHOICES = [
        (TYPE_RENEW, '续证'),
        (TYPE_REVOKE, '资质撤销'),
        (TYPE_WAIVER, '临时豁免'),
        (TYPE_WAIVER_REVOKE, '豁免撤销'),
    ]

    qualification = models.ForeignKey(
        Qualification, on_delete=models.PROTECT, null=True, blank=True,
        related_name='events', verbose_name='相关资质',
    )
    waiver = models.ForeignKey(
        QualificationWaiver, on_delete=models.PROTECT, null=True, blank=True,
        related_name='events', verbose_name='相关豁免',
    )
    event_type = models.CharField('事件类型', max_length=20, choices=TYPE_CHOICES)
    business = models.CharField('涉及业务', max_length=20, choices=BUSINESS_CHOICES, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='qualification_events', verbose_name='操作人',
    )
    approval_basis = models.TextField(
        '批准依据',
        help_text='批准文件/决定编号、口头授权记录等据以批准的依据',
    )
    approved_by = models.CharField('批准人姓名/职务', max_length=100, blank=True)
    effective_at = models.DateTimeField('生效时间', default=timezone.now)
    previous_valid_until = models.DateField('原有效期至', null=True, blank=True)
    new_valid_until = models.DateField('新有效期至', null=True, blank=True)
    detail = models.TextField('说明', blank=True)
    created_at = models.DateTimeField('记录时间', auto_now_add=True)

    class Meta:
        db_table = 'qa_qualification_event'
        verbose_name = '资质事件'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_event_type_display()} - {self.qualification_id or self.waiver_id}"


class QualificationCheck(models.Model):
    """
    资质核验记录：审批、收件、放行关键节点固定下来的“当时”证据。

    记录一旦生成即不可修改；后续节点以此判断当时是否具备资质，
    历史已完成操作始终按此记录解释。
    """

    RESULT_PASSED = 'passed'
    RESULT_PASSED_WITH_WAIVER = 'waived'
    RESULT_FAILED = 'failed'
    RESULT_BLOCKED = 'blocked'

    RESULT_CHOICES = [
        (RESULT_PASSED, '核验通过'),
        (RESULT_PASSED_WITH_WAIVER, '豁免通过'),
        (RESULT_BLOCKED, '已阻断'),
        (RESULT_FAILED, '核验失败'),
    ]

    stock_out = models.ForeignKey(
        'warehouse.StockOut', on_delete=models.CASCADE,
        related_name='qualification_checks', verbose_name='出库记录',
    )
    business = models.CharField('业务节点', max_length=20, choices=BUSINESS_CHOICES)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='qualification_checks', verbose_name='被核验人',
    )
    result = models.CharField('核验结果', max_length=20, choices=RESULT_CHOICES)
    basis = models.CharField(
        '通过依据', max_length=20, blank=True,
        help_text='qualification=凭有效资质，waiver=凭临时豁免',
    )
    qualification = models.ForeignKey(
        Qualification, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='checks', verbose_name='据以通过的资质',
    )
    waiver = models.ForeignKey(
        QualificationWaiver, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='checks', verbose_name='据以通过的豁免',
    )
    evidence_snapshot = models.JSONField('当时资质状态快照', default=dict)
    failure_reasons = models.JSONField('不满足原因', default=list)
    need_reassign = models.BooleanField('需重新分配', default=False)
    checked_at = models.DateTimeField('核验时间', default=timezone.now)

    class Meta:
        db_table = 'qa_qualification_check'
        verbose_name = '资质核验记录'
        verbose_name_plural = verbose_name
        ordering = ['-checked_at']
        indexes = [
            models.Index(fields=['stock_out', 'business']),
        ]

    def __str__(self):
        return f"{self.stock_out_id} - {self.get_business_display()} - {self.get_result_display()}"

    @property
    def is_passing(self):
        return self.result in (self.RESULT_PASSED, self.RESULT_PASSED_WITH_WAIVER)

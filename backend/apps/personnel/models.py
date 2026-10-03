"""
人员管理模型
"""
from django.db import models
from PIL import Image
import os


def avatar_upload_path(instance, filename):
    """头像上传路径"""
    ext = filename.split('.')[-1]
    return f'avatars/{instance.police_no}.{ext}'


# 需要核验资质的关键业务节点
BUSINESS_CHOICES = [
    ('receiving', '收件'),
    ('approval', '审批'),
    ('release', '放行'),
]
BUSINESS_LABELS = dict(BUSINESS_CHOICES)


class StockOutPerson(models.Model):
    """出库人员模型"""
    police_no = models.CharField('警号', max_length=50, unique=True)
    name = models.CharField('姓名', max_length=50, unique=True)
    id_card = models.CharField('身份证号', max_length=18, blank=True)
    phone = models.CharField('手机号', max_length=20, unique=True)
    avatar = models.ImageField('蓝底照片', upload_to=avatar_upload_path, blank=True, null=True)
    binduser = models.ForeignKey(
        'authentication.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_out_persons',
        verbose_name='关联管理员',
        limit_choices_to={'role': 'admin'}
    )
    is_active = models.BooleanField('是否在职', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'pm_stock_out_person'
        verbose_name = '出库人员'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.police_no})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.avatar:
            self._process_avatar()

    def _process_avatar(self):
        """使用Pillow处理头像图片 - 压缩和调整尺寸"""
        try:
            img = Image.open(self.avatar.path)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            max_size = (200, 200)
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            img.save(self.avatar.path, 'JPEG', quality=85, optimize=True)
        except Exception:
            pass


class QualificationType(models.Model):
    """资质类型：登记证件类别、版本及其适用的业务节点"""
    name = models.CharField('资质类型', max_length=100, unique=True)
    code = models.CharField('类型编码', max_length=50, unique=True)
    version = models.CharField('版本', max_length=30)
    applicable_business = models.JSONField(
        '适用业务', default=list,
        help_text='该资质可覆盖的业务节点编码，如 ["approval", "release"]'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_by = models.ForeignKey(
        'authentication.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_qualification_types', verbose_name='创建人'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'pm_qualification_type'
        verbose_name = '资质类型'
        verbose_name_plural = verbose_name
        ordering = ['code']

    def __str__(self):
        return f"{self.name}（{self.version}）"

    @property
    def business_labels(self):
        return [BUSINESS_LABELS.get(code, code) for code in (self.applicable_business or [])]


class PersonnelQualification(models.Model):
    """人员持证记录：同一人员同一资质续证时新增记录，旧记录保留作为历史证据"""
    STATUS_CHOICES = [
        ('valid', '有效'),
        ('revoked', '已撤销'),
    ]

    person = models.ForeignKey(
        StockOutPerson, on_delete=models.CASCADE,
        related_name='qualifications', verbose_name='持证人员'
    )
    qualification_type = models.ForeignKey(
        QualificationType, on_delete=models.PROTECT,
        related_name='holdings', verbose_name='资质类型'
    )
    certificate_no = models.CharField('证件编号', max_length=80, blank=True)
    version = models.CharField('发证版本', max_length=30)
    valid_from = models.DateField('生效日期')
    valid_until = models.DateField('有效期至')
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='valid')
    grant_basis = models.CharField('取得/续证批准依据', max_length=200, blank=True)
    granted_by = models.ForeignKey(
        'authentication.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='granted_qualifications', verbose_name='登记批准人'
    )
    revoked_by = models.ForeignKey(
        'authentication.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='revoked_qualifications', verbose_name='撤销批准人'
    )
    revoked_at = models.DateTimeField('撤销时间', null=True, blank=True)
    revoke_reason = models.CharField('撤销原因/依据', max_length=200, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'pm_personnel_qualification'
        verbose_name = '人员资质'
        verbose_name_plural = verbose_name
        ordering = ['-valid_until', '-created_at']

    def __str__(self):
        return f"{self.person.name}-{self.qualification_type.name}-{self.version}"

    def is_effective_on(self, on_date):
        """该证在指定日期是否有效：未撤销且处于有效期内"""
        return (
            self.status == 'valid'
            and self.valid_from <= on_date <= self.valid_until
        )

    def applies_to(self, business):
        business_list = self.qualification_type.applicable_business or []
        return self.qualification_type.is_active and business in business_list


class QualificationExemption(models.Model):
    """临时豁免：资质缺失或失效时，经批准在限定业务和期限内放行"""
    person = models.ForeignKey(
        StockOutPerson, on_delete=models.CASCADE,
        related_name='exemptions', verbose_name='被豁免人员'
    )
    business = models.CharField('适用业务', max_length=20, choices=BUSINESS_CHOICES)
    reason = models.CharField('豁免事由', max_length=200)
    basis = models.CharField('批准依据', max_length=200)
    valid_from = models.DateField('生效日期')
    valid_until = models.DateField('有效期至')
    approved_by = models.ForeignKey(
        'authentication.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_exemptions', verbose_name='批准人'
    )
    is_revoked = models.BooleanField('是否已撤销', default=False)
    revoked_by = models.ForeignKey(
        'authentication.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='revoked_exemptions', verbose_name='豁免撤销人'
    )
    revoked_at = models.DateTimeField('撤销时间', null=True, blank=True)
    revoke_reason = models.CharField('撤销原因', max_length=200, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'pm_qualification_exemption'
        verbose_name = '临时豁免'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.person.name}-{self.get_business_display()}豁免"

    def is_effective_on(self, on_date):
        return (
            not self.is_revoked
            and self.valid_from <= on_date <= self.valid_until
        )


class QualificationChangeLog(models.Model):
    """资质变更台账：登记、续证、撤销、豁免及其撤销均须留下批准依据"""
    ACTION_CHOICES = [
        ('grant', '登记发证'),
        ('renew', '续证'),
        ('revoke', '撤销资质'),
        ('exempt', '批准豁免'),
        ('exempt_revoke', '撤销豁免'),
    ]

    person = models.ForeignKey(
        StockOutPerson, on_delete=models.CASCADE,
        related_name='qualification_logs', verbose_name='人员'
    )
    action = models.CharField('变更类型', max_length=20, choices=ACTION_CHOICES)
    business = models.CharField('涉及业务', max_length=20, choices=BUSINESS_CHOICES, blank=True)
    qualification = models.ForeignKey(
        PersonnelQualification, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='change_logs', verbose_name='相关资质'
    )
    exemption = models.ForeignKey(
        QualificationExemption, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='change_logs', verbose_name='相关豁免'
    )
    approver = models.ForeignKey(
        'authentication.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='qualification_approvals', verbose_name='批准人'
    )
    basis = models.CharField('批准依据', max_length=200)
    valid_from = models.DateField('生效日期', null=True, blank=True)
    valid_until = models.DateField('有效期至', null=True, blank=True)
    remark = models.CharField('备注', max_length=200, blank=True)
    created_at = models.DateTimeField('记录时间', auto_now_add=True)

    class Meta:
        db_table = 'pm_qualification_change_log'
        verbose_name = '资质变更记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.person.name}-{self.get_action_display()}"


class QualificationCheck(models.Model):
    """关键节点资质核验记录：固化核验当时的资质状态快照，事后资质失效不改变历史结论"""
    RESULT_CHOICES = [
        ('pass', '核验通过'),
        ('blocked', '已阻断'),
    ]
    GRANT_CHOICES = [
        ('qualification', '有效资质'),
        ('exemption', '临时豁免'),
    ]

    person = models.ForeignKey(
        StockOutPerson, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='qualification_checks', verbose_name='值班人员'
    )
    user = models.ForeignKey(
        'authentication.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='qualification_checks', verbose_name='操作账号'
    )
    business = models.CharField('业务节点', max_length=20, choices=BUSINESS_CHOICES)
    result = models.CharField('核验结果', max_length=20, choices=RESULT_CHOICES)
    grant_type = models.CharField('放行依据类型', max_length=20, choices=GRANT_CHOICES, blank=True)
    qualification = models.ForeignKey(
        PersonnelQualification, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='verifications', verbose_name='核验依据资质'
    )
    exemption = models.ForeignKey(
        QualificationExemption, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='verifications', verbose_name='核验依据豁免'
    )
    snapshot = models.JSONField('当时资质状态快照', default=dict)
    detail = models.CharField('核验说明', max_length=300, blank=True)
    checked_at = models.DateTimeField('核验时间', auto_now_add=True)

    class Meta:
        db_table = 'pm_qualification_check'
        verbose_name = '资质核验记录'
        verbose_name_plural = verbose_name
        ordering = ['-checked_at']

    def __str__(self):
        return f"{self.get_business_display()}-{self.get_result_display()}"

    @property
    def passed(self):
        return self.result == 'pass'

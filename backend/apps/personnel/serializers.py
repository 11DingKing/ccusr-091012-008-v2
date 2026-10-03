"""
人员管理序列化器
"""
from django.utils import timezone
from rest_framework import serializers
from .models import (
    BUSINESS_CHOICES,
    BUSINESS_LABELS,
    PersonnelQualification,
    QualificationChangeLog,
    QualificationCheck,
    QualificationExemption,
    QualificationType,
    StockOutPerson,
)
from apps.authentication.models import User

BUSINESS_CODES = [code for code, _ in BUSINESS_CHOICES]


class StockOutPersonSerializer(serializers.ModelSerializer):
    """出库人员序列化器"""
    binduser_name = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = StockOutPerson
        fields = [
            'id', 'police_no', 'name', 'id_card', 'phone',
            'avatar', 'avatar_url', 'binduser', 'binduser_name',
            'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_binduser_name(self, obj):
        """获取关联管理员用户名"""
        if obj.binduser:
            return obj.binduser.username
        return None

    def get_avatar_url(self, obj):
        """获取头像URL"""
        if obj.avatar:
            return obj.avatar.url
        return None


class StockOutPersonCreateSerializer(serializers.Serializer):
    """出库人员创建序列化器"""
    police_no = serializers.CharField(max_length=50, required=True, error_messages={
        'required': '请输入警号',
        'blank': '警号不能为空',
    })
    name = serializers.CharField(max_length=50, required=True, error_messages={
        'required': '请输入姓名',
        'blank': '姓名不能为空',
    })
    id_card = serializers.CharField(max_length=18, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=True, error_messages={
        'required': '请输入手机号',
        'blank': '手机号不能为空',
    })
    binduser = serializers.IntegerField(required=False, allow_null=True)

    def validate_police_no(self, value):
        instance = self.context.get('instance')
        if instance:
            if StockOutPerson.objects.filter(police_no=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('警号已存在')
        else:
            if StockOutPerson.objects.filter(police_no=value).exists():
                raise serializers.ValidationError('警号已存在')
        return value

    def validate_name(self, value):
        instance = self.context.get('instance')
        if instance:
            if StockOutPerson.objects.filter(name=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('姓名已存在')
        else:
            if StockOutPerson.objects.filter(name=value).exists():
                raise serializers.ValidationError('姓名已存在')
        return value

    def validate_phone(self, value):
        instance = self.context.get('instance')
        if instance:
            if StockOutPerson.objects.filter(phone=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('手机号已存在')
        else:
            if StockOutPerson.objects.filter(phone=value).exists():
                raise serializers.ValidationError('手机号已存在')
        return value

    def validate_binduser(self, value):
        if value:
            try:
                user = User.objects.get(pk=value)
                if user.role not in ['admin']:
                    raise serializers.ValidationError('只能绑定管理员用户')
            except User.DoesNotExist:
                raise serializers.ValidationError('用户不存在')
        return value


class AdminUserSerializer(serializers.ModelSerializer):
    """管理员用户序列化器（用于下拉选择）"""
    class Meta:
        model = User
        fields = ['id', 'username']


# ==================== 资质类型 ====================

class QualificationTypeSerializer(serializers.ModelSerializer):
    """资质类型序列化器"""
    business_labels = serializers.JSONField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = QualificationType
        fields = [
            'id', 'name', 'code', 'version', 'applicable_business',
            'business_labels', 'is_active', 'created_by', 'created_by_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class QualificationTypeCreateSerializer(serializers.Serializer):
    """资质类型创建/更新序列化器"""
    name = serializers.CharField(max_length=100, required=True, error_messages={
        'required': '请输入资质类型名称',
        'blank': '资质类型名称不能为空',
    })
    code = serializers.CharField(max_length=50, required=True, error_messages={
        'required': '请输入类型编码',
        'blank': '类型编码不能为空',
    })
    version = serializers.CharField(max_length=30, required=True, error_messages={
        'required': '请输入版本',
        'blank': '版本不能为空',
    })
    applicable_business = serializers.ListField(
        child=serializers.ChoiceField(choices=BUSINESS_CODES),
        required=True, allow_empty=False,
        error_messages={'required': '请选择适用业务', 'empty': '适用业务不能为空'},
    )

    def validate_name(self, value):
        instance = self.context.get('instance')
        queryset = QualificationType.objects.filter(name=value)
        if instance:
            queryset = queryset.exclude(pk=instance.pk)
        if queryset.exists():
            raise serializers.ValidationError('资质类型名称已存在')
        return value

    def validate_code(self, value):
        instance = self.context.get('instance')
        queryset = QualificationType.objects.filter(code=value)
        if instance:
            queryset = queryset.exclude(pk=instance.pk)
        if queryset.exists():
            raise serializers.ValidationError('类型编码已存在')
        return value


# ==================== 人员资质 ====================

class PersonnelQualificationSerializer(serializers.ModelSerializer):
    """人员资质序列化器"""
    qualification_name = serializers.CharField(
        source='qualification_type.name', read_only=True
    )
    qualification_code = serializers.CharField(
        source='qualification_type.code', read_only=True
    )
    applicable_business = serializers.JSONField(
        source='qualification_type.applicable_business', read_only=True
    )
    person_name = serializers.CharField(source='person.name', read_only=True)
    police_no = serializers.CharField(source='person.police_no', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    granted_by_name = serializers.CharField(source='granted_by.username', read_only=True)
    revoked_by_name = serializers.CharField(source='revoked_by.username', read_only=True)
    is_effective = serializers.SerializerMethodField()

    class Meta:
        model = PersonnelQualification
        fields = [
            'id', 'person', 'person_name', 'police_no',
            'qualification_type', 'qualification_name', 'qualification_code',
            'applicable_business', 'certificate_no', 'version',
            'valid_from', 'valid_until', 'status', 'status_display',
            'is_effective', 'grant_basis', 'granted_by', 'granted_by_name',
            'revoked_by', 'revoked_by_name', 'revoked_at', 'revoke_reason',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_is_effective(self, obj):
        return obj.is_effective_on(timezone.localdate())


class QualificationGrantSerializer(serializers.Serializer):
    """资质登记/续证序列化器"""
    person = serializers.IntegerField(required=True, error_messages={'required': '请选择人员'})
    qualification_type = serializers.IntegerField(
        required=True, error_messages={'required': '请选择资质类型'}
    )
    certificate_no = serializers.CharField(max_length=80, required=False, allow_blank=True)
    version = serializers.CharField(max_length=30, required=False, allow_blank=True)
    valid_from = serializers.DateField(required=True, error_messages={
        'required': '请选择生效日期',
        'invalid': '生效日期格式无效',
    })
    valid_until = serializers.DateField(required=True, error_messages={
        'required': '请选择有效期至',
        'invalid': '有效期格式无效',
    })
    grant_basis = serializers.CharField(max_length=200, required=True, error_messages={
        'required': '请填写批准依据（培训/授权文件）',
        'blank': '批准依据不能为空',
    })

    def validate_person(self, value):
        if not StockOutPerson.objects.filter(pk=value).exists():
            raise serializers.ValidationError('人员不存在')
        return value

    def validate_qualification_type(self, value):
        qtype = QualificationType.objects.filter(pk=value, is_active=True).first()
        if not qtype:
            raise serializers.ValidationError('资质类型不存在或已停用')
        return value

    def validate(self, data):
        if data['valid_until'] < data['valid_from']:
            raise serializers.ValidationError('有效期至不能早于生效日期')
        return data


class QualificationRevokeSerializer(serializers.Serializer):
    """资质撤销序列化器"""
    revoke_reason = serializers.CharField(max_length=200, required=True, error_messages={
        'required': '请填写撤销批准依据',
        'blank': '撤销批准依据不能为空',
    })


# ==================== 临时豁免 ====================

class QualificationExemptionSerializer(serializers.ModelSerializer):
    """临时豁免序列化器"""
    person_name = serializers.CharField(source='person.name', read_only=True)
    police_no = serializers.CharField(source='person.police_no', read_only=True)
    business_display = serializers.CharField(source='get_business_display', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.username', read_only=True)
    revoked_by_name = serializers.CharField(source='revoked_by.username', read_only=True)
    is_effective = serializers.SerializerMethodField()

    class Meta:
        model = QualificationExemption
        fields = [
            'id', 'person', 'person_name', 'police_no',
            'business', 'business_display', 'reason', 'basis',
            'valid_from', 'valid_until', 'approved_by', 'approved_by_name',
            'is_revoked', 'revoked_by', 'revoked_by_name',
            'revoked_at', 'revoke_reason', 'created_at', 'is_effective'
        ]
        read_only_fields = ['id', 'created_at']

    def get_is_effective(self, obj):
        return obj.is_effective_on(timezone.localdate())


class ExemptionCreateSerializer(serializers.Serializer):
    """临时豁免创建序列化器"""
    person = serializers.IntegerField(required=True, error_messages={'required': '请选择人员'})
    business = serializers.ChoiceField(
        choices=BUSINESS_CODES, required=True,
        error_messages={'required': '请选择适用业务'}
    )
    reason = serializers.CharField(max_length=200, required=True, error_messages={
        'required': '请填写豁免事由',
        'blank': '豁免事由不能为空',
    })
    basis = serializers.CharField(max_length=200, required=True, error_messages={
        'required': '请填写批准依据',
        'blank': '批准依据不能为空',
    })
    valid_from = serializers.DateField(required=True, error_messages={'required': '请选择生效日期'})
    valid_until = serializers.DateField(required=True, error_messages={'required': '请选择有效期至'})

    def validate_person(self, value):
        if not StockOutPerson.objects.filter(pk=value).exists():
            raise serializers.ValidationError('人员不存在')
        return value

    def validate(self, data):
        if data['valid_until'] < data['valid_from']:
            raise serializers.ValidationError('有效期至不能早于生效日期')
        return data


class ExemptionRevokeSerializer(serializers.Serializer):
    """豁免撤销序列化器"""
    revoke_reason = serializers.CharField(max_length=200, required=True, error_messages={
        'required': '请填写撤销原因',
        'blank': '撤销原因不能为空',
    })


# ==================== 变更台账与核验记录 ====================

class QualificationChangeLogSerializer(serializers.ModelSerializer):
    """资质变更记录序列化器"""
    person_name = serializers.CharField(source='person.name', read_only=True)
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    business_display = serializers.SerializerMethodField()
    approver_name = serializers.CharField(source='approver.username', read_only=True)

    class Meta:
        model = QualificationChangeLog
        fields = [
            'id', 'person', 'person_name', 'action', 'action_display',
            'business', 'business_display', 'qualification', 'exemption',
            'approver', 'approver_name', 'basis',
            'valid_from', 'valid_until', 'remark', 'created_at'
        ]

    def get_business_display(self, obj):
        return BUSINESS_LABELS.get(obj.business, obj.business) if obj.business else ''


class QualificationCheckSerializer(serializers.ModelSerializer):
    """资质核验记录序列化器"""
    person_name = serializers.CharField(source='person.name', read_only=True)
    police_no = serializers.CharField(source='person.police_no', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    business_display = serializers.CharField(source='get_business_display', read_only=True)
    result_display = serializers.CharField(source='get_result_display', read_only=True)
    grant_type_display = serializers.SerializerMethodField()
    qualification_summary = serializers.SerializerMethodField()

    class Meta:
        model = QualificationCheck
        fields = [
            'id', 'person', 'person_name', 'police_no', 'user', 'username',
            'business', 'business_display', 'result', 'result_display',
            'grant_type', 'grant_type_display',
            'qualification', 'exemption', 'qualification_summary',
            'snapshot', 'detail', 'checked_at'
        ]

    def get_grant_type_display(self, obj):
        return obj.get_grant_type_display() if obj.grant_type else '无'

    def get_qualification_summary(self, obj):
        if obj.qualification_id:
            q = obj.qualification
            return f"{q.qualification_type.name} {q.version}（至 {q.valid_until.isoformat()}）"
        if obj.exemption_id:
            e = obj.exemption
            return f"临时豁免（至 {e.valid_until.isoformat()}）"
        return ''

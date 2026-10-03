"""
人员资质管理序列化器
"""
from rest_framework import serializers

from .models import (
    Qualification,
    QualificationCheck,
    QualificationEvent,
    QualificationType,
    QualificationWaiver,
)


class QualificationTypeSerializer(serializers.ModelSerializer):
    business_labels = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = QualificationType
        fields = [
            'id', 'code', 'name', 'version', 'applicable_business',
            'business_labels', 'description', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def get_business_labels(self, obj):
        return obj.business_labels

    def validate_applicable_business(self, value):
        valid = {'approval', 'receive', 'release'}
        if not isinstance(value, list) or not value:
            raise serializers.ValidationError('适用业务至少选择一项')
        invalid = [b for b in value if b not in valid]
        if invalid:
            raise serializers.ValidationError(f'非法的业务节点：{invalid}')
        return value

    def validate_code(self, value):
        instance = self.context.get('instance')
        qs = QualificationType.objects.filter(code=value)
        if instance:
            qs = qs.exclude(pk=instance.pk)
        if qs.exists():
            raise serializers.ValidationError('资质类型编码已存在')
        return value


class QualificationSerializer(serializers.ModelSerializer):
    holder_name = serializers.CharField(source='holder.username', read_only=True)
    type_name = serializers.CharField(source='qualification_type.name', read_only=True)
    type_code = serializers.CharField(source='qualification_type.code', read_only=True)
    type_version = serializers.CharField(source='qualification_type.version', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_currently_valid = serializers.BooleanField(read_only=True)
    is_expired_by_date = serializers.BooleanField(read_only=True)

    class Meta:
        model = Qualification
        fields = [
            'id', 'holder', 'holder_name',
            'qualification_type', 'type_name', 'type_code', 'type_version',
            'certificate_no', 'version', 'valid_from', 'valid_until',
            'status', 'status_display', 'is_currently_valid', 'is_expired_by_date',
            'issued_by', 'remark', 'revoked_at', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'status', 'revoked_at', 'created_at', 'updated_at']

    def validate(self, attrs):
        valid_from = attrs.get('valid_from')
        valid_until = attrs.get('valid_until')
        if valid_from and valid_until and valid_until < valid_from:
            raise serializers.ValidationError('有效期至不能早于生效日期')
        return attrs


class QualificationRenewSerializer(serializers.Serializer):
    """续证入参。"""
    new_valid_until = serializers.DateField(required=True)
    new_version = serializers.CharField(required=False, allow_blank=True)
    new_certificate_no = serializers.CharField(required=False, allow_blank=True)
    approved_by = serializers.CharField(required=False, allow_blank=True)
    approval_basis = serializers.CharField(required=True, error_messages={
        'required': '续证必须填写批准依据',
        'blank': '续证必须填写批准依据',
    })
    detail = serializers.CharField(required=False, allow_blank=True)


class QualificationRevokeSerializer(serializers.Serializer):
    """撤销资质入参。"""
    approved_by = serializers.CharField(required=False, allow_blank=True)
    approval_basis = serializers.CharField(required=True, error_messages={
        'required': '撤销必须填写批准依据',
        'blank': '撤销必须填写批准依据',
    })
    detail = serializers.CharField(required=False, allow_blank=True)


class WaiverSerializer(serializers.ModelSerializer):
    holder_name = serializers.CharField(source='holder.username', read_only=True)
    business_display = serializers.CharField(source='get_business_display', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.username', read_only=True)
    type_name = serializers.CharField(source='qualification_type.name', read_only=True)

    class Meta:
        model = QualificationWaiver
        fields = [
            'id', 'holder', 'holder_name', 'business', 'business_display',
            'qualification_type', 'type_name', 'reason',
            'approved_by', 'approved_by_name', 'approved_at',
            'valid_from', 'valid_until', 'stock_out',
            'status', 'revoked_at', 'revoked_by', 'revoke_reason',
        ]
        read_only_fields = [
            'id', 'approved_by', 'approved_at', 'status',
            'revoked_at', 'revoked_by', 'revoke_reason',
        ]


class WaiverGrantSerializer(serializers.Serializer):
    """批准临时豁免入参。"""
    holder = serializers.IntegerField(required=True)
    business = serializers.ChoiceField(choices=[('approval', '审批'), ('receive', '收件'), ('release', '放行')])
    qualification_type = serializers.IntegerField(required=False, allow_null=True)
    stock_out = serializers.IntegerField(required=False, allow_null=True)
    reason = serializers.CharField(required=True, error_messages={'required': '请填写豁免事由', 'blank': '请填写豁免事由'})
    approved_by = serializers.CharField(required=False, allow_blank=True)
    approval_basis = serializers.CharField(required=True, error_messages={
        'required': '临时豁免必须填写批准依据',
        'blank': '临时豁免必须填写批准依据',
    })
    valid_from = serializers.DateTimeField(required=True)
    valid_until = serializers.DateTimeField(required=True)
    detail = serializers.CharField(required=False, allow_blank=True)


class WaiverRevokeSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, error_messages={'required': '请填写撤销原因', 'blank': '请填写撤销原因'})
    approval_basis = serializers.CharField(required=True, error_messages={
        'required': '撤销豁免必须填写批准依据',
        'blank': '撤销豁免必须填写批准依据',
    })
    detail = serializers.CharField(required=False, allow_blank=True)


class QualificationEventSerializer(serializers.ModelSerializer):
    event_type_display = serializers.CharField(source='get_event_type_display', read_only=True)
    actor_name = serializers.CharField(source='actor.username', read_only=True)
    business_display = serializers.SerializerMethodField()

    class Meta:
        model = QualificationEvent
        fields = [
            'id', 'qualification', 'waiver', 'event_type', 'event_type_display',
            'business', 'business_display', 'actor', 'actor_name',
            'approval_basis', 'approved_by', 'effective_at',
            'previous_valid_until', 'new_valid_until', 'detail', 'created_at',
        ]

    def get_business_display(self, obj):
        return obj.get_business_display() if obj.business else ''


class QualificationCheckSerializer(serializers.ModelSerializer):
    result_display = serializers.CharField(source='get_result_display', read_only=True)
    business_display = serializers.CharField(source='get_business_display', read_only=True)
    assignee_name = serializers.CharField(source='assignee.username', read_only=True)

    class Meta:
        model = QualificationCheck
        fields = [
            'id', 'stock_out', 'business', 'business_display',
            'assignee', 'assignee_name', 'result', 'result_display',
            'basis', 'qualification', 'waiver', 'evidence_snapshot',
            'failure_reasons', 'need_reassign', 'checked_at',
        ]

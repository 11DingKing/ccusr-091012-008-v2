"""
人员管理视图
"""
import logging
from django.db import transaction
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from apps.core.response import success_response, error_response
from apps.authentication.models import User
from .models import (
    PersonnelQualification,
    QualificationChangeLog,
    QualificationCheck,
    QualificationExemption,
    QualificationType,
    StockOutPerson,
)
from .serializers import (
    StockOutPersonSerializer, StockOutPersonCreateSerializer, AdminUserSerializer,
    QualificationTypeSerializer, QualificationTypeCreateSerializer,
    PersonnelQualificationSerializer, QualificationGrantSerializer,
    QualificationRevokeSerializer,
    QualificationExemptionSerializer, ExemptionCreateSerializer,
    ExemptionRevokeSerializer,
    QualificationChangeLogSerializer, QualificationCheckSerializer,
)

logger = logging.getLogger('apps')


def _first_error(serializer):
    errors = serializer.errors
    first_error = list(errors.values())[0]
    if isinstance(first_error, (list, tuple)):
        first_error = first_error[0]
    return str(first_error)


def _paginate(request, queryset):
    page = int(request.query_params.get('page', 1))
    page_size = int(request.query_params.get('page_size', 10))
    total = queryset.count()
    start = (page - 1) * page_size
    return queryset[start:start + page_size], total, page, page_size


class StockOutPersonListView(APIView):
    """出库人员列表视图"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        queryset = StockOutPerson.objects.all().order_by('-created_at')
        persons, total, page, page_size = _paginate(request, queryset)
        serializer = StockOutPersonSerializer(persons, many=True)

        return success_response(data={
            'list': serializer.data,
            'total': total,
            'page': page,
            'page_size': page_size
        })

    def post(self, request):
        """创建出库人员"""
        serializer = StockOutPersonCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))

        data = serializer.validated_data
        binduser = None
        if data.get('binduser'):
            try:
                binduser = User.objects.get(pk=data['binduser'])
            except User.DoesNotExist:
                pass

        person = StockOutPerson.objects.create(
            police_no=data['police_no'],
            name=data['name'],
            id_card=data.get('id_card', ''),
            phone=data['phone'],
            binduser=binduser
        )

        # 处理头像上传
        if 'avatar' in request.FILES:
            person.avatar = request.FILES['avatar']
            person.save()

        logger.info(f"User {request.user.username} created stock out person {person.name}")

        return success_response(data=StockOutPersonSerializer(person).data, message='创建成功')


class StockOutPersonDetailView(APIView):
    """出库人员详情视图"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request, pk):
        try:
            person = StockOutPerson.objects.get(pk=pk)
        except StockOutPerson.DoesNotExist:
            return error_response(message='出库人员不存在', code=404)

        serializer = StockOutPersonSerializer(person)
        return success_response(data=serializer.data)

    def put(self, request, pk):
        """更新出库人员"""
        try:
            person = StockOutPerson.objects.get(pk=pk)
        except StockOutPerson.DoesNotExist:
            return error_response(message='出库人员不存在', code=404)

        serializer = StockOutPersonCreateSerializer(
            data=request.data,
            context={'instance': person}
        )
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))

        data = serializer.validated_data
        person.police_no = data['police_no']
        person.name = data['name']
        person.id_card = data.get('id_card', '')
        person.phone = data['phone']

        if data.get('binduser'):
            try:
                person.binduser = User.objects.get(pk=data['binduser'])
            except User.DoesNotExist:
                person.binduser = None
        else:
            person.binduser = None

        # 处理头像上传
        if 'avatar' in request.FILES:
            person.avatar = request.FILES['avatar']

        person.save()

        logger.info(f"User {request.user.username} updated stock out person {person.name}")

        return success_response(data=StockOutPersonSerializer(person).data, message='更新成功')

    def delete(self, request, pk):
        """删除出库人员"""
        try:
            person = StockOutPerson.objects.get(pk=pk)
        except StockOutPerson.DoesNotExist:
            return error_response(message='出库人员不存在', code=404)

        name = person.name
        person.delete()

        logger.info(f"User {request.user.username} deleted stock out person {name}")

        return success_response(message='删除成功')


class AdminUserListView(APIView):
    """管理员用户列表（用于下拉选择）"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 只获取管理员用户（不包括超级管理员和普通用户）
        users = User.objects.filter(role='admin', is_active=True)
        serializer = AdminUserSerializer(users, many=True)
        return success_response(data=serializer.data)


# ==================== 资质类型管理 ====================

class QualificationTypeListView(APIView):
    """资质类型列表/创建"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = QualificationType.objects.all().order_by('code')
        is_active = request.query_params.get('is_active')
        if is_active in ('true', 'false'):
            queryset = queryset.filter(is_active=is_active == 'true')
        types, total, page, page_size = _paginate(request, queryset)
        serializer = QualificationTypeSerializer(types, many=True)
        return success_response(data={
            'list': serializer.data, 'total': total, 'page': page, 'page_size': page_size
        })

    def post(self, request):
        serializer = QualificationTypeCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        data = serializer.validated_data
        qtype = QualificationType.objects.create(
            name=data['name'],
            code=data['code'],
            version=data['version'],
            applicable_business=data['applicable_business'],
            created_by=request.user,
        )
        logger.info("User %s created qualification type %s", request.user.username, qtype.code)
        return success_response(data=QualificationTypeSerializer(qtype).data, message='创建成功')


class QualificationTypeAllView(APIView):
    """全部启用的资质类型（下拉选择）"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        types = QualificationType.objects.filter(is_active=True).order_by('code')
        return success_response(data=QualificationTypeSerializer(types, many=True).data)


class QualificationTypeDetailView(APIView):
    """资质类型详情/更新/删除"""
    permission_classes = [IsAuthenticated]

    def _get_object(self, pk):
        try:
            return QualificationType.objects.get(pk=pk)
        except QualificationType.DoesNotExist:
            return None

    def get(self, request, pk):
        qtype = self._get_object(pk)
        if not qtype:
            return error_response(message='资质类型不存在', code=404)
        return success_response(data=QualificationTypeSerializer(qtype).data)

    def put(self, request, pk):
        qtype = self._get_object(pk)
        if not qtype:
            return error_response(message='资质类型不存在', code=404)
        serializer = QualificationTypeCreateSerializer(
            data=request.data, context={'instance': qtype}
        )
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        data = serializer.validated_data
        qtype.name = data['name']
        qtype.code = data['code']
        qtype.version = data['version']
        qtype.applicable_business = data['applicable_business']
        qtype.save()
        return success_response(data=QualificationTypeSerializer(qtype).data, message='更新成功')

    def delete(self, request, pk):
        qtype = self._get_object(pk)
        if not qtype:
            return error_response(message='资质类型不存在', code=404)
        if qtype.holdings.exists():
            return error_response(message='该资质类型已有持证记录，无法删除，可停用')
        qtype.delete()
        return success_response(message='删除成功')


# ==================== 人员资质：登记/续证/撤销 ====================

class PersonnelQualificationListView(APIView):
    """人员持证列表（可按人员、资质状态筛选）"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = (
            PersonnelQualification.objects
            .select_related('person', 'qualification_type')
            .all().order_by('-valid_until', '-created_at')
        )
        person_id = request.query_params.get('person')
        status = request.query_params.get('status')
        if person_id:
            queryset = queryset.filter(person_id=person_id)
        if status in dict(PersonnelQualification.STATUS_CHOICES):
            queryset = queryset.filter(status=status)
        records, total, page, page_size = _paginate(request, queryset)
        serializer = PersonnelQualificationSerializer(records, many=True)
        return success_response(data={
            'list': serializer.data, 'total': total, 'page': page, 'page_size': page_size
        })


class QualificationGrantView(APIView):
    """资质登记发证/续证。

    同一人员、同一资质类型再次登记视为续证：旧记录保留作为历史证据，
    新记录自其生效日起作为核验依据，全过程写入变更台账。
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = QualificationGrantSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        data = serializer.validated_data

        person = StockOutPerson.objects.get(pk=data['person'])
        qtype = QualificationType.objects.get(pk=data['qualification_type'])
        version = data.get('version') or qtype.version

        with transaction.atomic():
            previous = (
                PersonnelQualification.objects
                .filter(person=person, qualification_type=qtype, status='valid')
                .order_by('-valid_until')
                .first()
            )
            is_renew = previous is not None
            qualification = PersonnelQualification.objects.create(
                person=person,
                qualification_type=qtype,
                certificate_no=data.get('certificate_no', ''),
                version=version,
                valid_from=data['valid_from'],
                valid_until=data['valid_until'],
                status='valid',
                grant_basis=data['grant_basis'],
                granted_by=request.user,
            )
            QualificationChangeLog.objects.create(
                person=person,
                action='renew' if is_renew else 'grant',
                qualification=qualification,
                approver=request.user,
                basis=data['grant_basis'],
                valid_from=data['valid_from'],
                valid_until=data['valid_until'],
                remark=(
                    f'续证，前证有效期至 {previous.valid_until.isoformat()}'
                    if is_renew else f'登记发证：{qtype.name} {version}'
                ),
            )

        logger.info(
            "User %s %s qualification for %s (%s)",
            request.user.username,
            'renewed' if is_renew else 'granted',
            person.name, qtype.code,
        )
        return success_response(
            data=PersonnelQualificationSerializer(qualification).data,
            message='续证成功' if is_renew else '登记成功',
        )


class QualificationRevokeView(APIView):
    """撤销人员资质：必须填写批准依据，撤销即时生效并写入台账。"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            qualification = PersonnelQualification.objects.select_related(
                'person', 'qualification_type'
            ).get(pk=pk)
        except PersonnelQualification.DoesNotExist:
            return error_response(message='资质记录不存在', code=404)

        if qualification.status == 'revoked':
            return error_response(message='该资质已处于撤销状态')

        serializer = QualificationRevokeSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        reason = serializer.validated_data['revoke_reason']

        with transaction.atomic():
            qualification.status = 'revoked'
            qualification.revoked_by = request.user
            qualification.revoked_at = timezone.now()
            qualification.revoke_reason = reason
            qualification.save(update_fields=[
                'status', 'revoked_by', 'revoked_at', 'revoke_reason', 'updated_at'
            ])
            for business in qualification.qualification_type.applicable_business or []:
                QualificationChangeLog.objects.create(
                    person=qualification.person,
                    action='revoke',
                    business=business,
                    qualification=qualification,
                    approver=request.user,
                    basis=reason,
                    valid_from=None,
                    valid_until=None,
                    remark=f'撤销《{qualification.qualification_type.name}》',
                )

        logger.info(
            "User %s revoked qualification %s: %s",
            request.user.username, qualification.id, reason,
        )
        return success_response(
            data=PersonnelQualificationSerializer(qualification).data, message='已撤销'
        )


# ==================== 临时豁免 ====================

class QualificationExemptionListView(APIView):
    """临时豁免列表/批准"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = QualificationExemption.objects.select_related('person').all()
        person_id = request.query_params.get('person')
        business = request.query_params.get('business')
        if person_id:
            queryset = queryset.filter(person_id=person_id)
        if business:
            queryset = queryset.filter(business=business)
        records, total, page, page_size = _paginate(request, queryset)
        serializer = QualificationExemptionSerializer(records, many=True)
        return success_response(data={
            'list': serializer.data, 'total': total, 'page': page, 'page_size': page_size
        })

    def post(self, request):
        """批准临时豁免：必须登记事由、批准依据和期限。"""
        serializer = ExemptionCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        data = serializer.validated_data

        person = StockOutPerson.objects.get(pk=data['person'])
        with transaction.atomic():
            exemption = QualificationExemption.objects.create(
                person=person,
                business=data['business'],
                reason=data['reason'],
                basis=data['basis'],
                valid_from=data['valid_from'],
                valid_until=data['valid_until'],
                approved_by=request.user,
            )
            QualificationChangeLog.objects.create(
                person=person,
                action='exempt',
                business=data['business'],
                exemption=exemption,
                approver=request.user,
                basis=data['basis'],
                valid_from=data['valid_from'],
                valid_until=data['valid_until'],
                remark=data['reason'],
            )

        logger.info(
            "User %s granted exemption for %s on %s",
            request.user.username, person.name, data['business'],
        )
        return success_response(
            data=QualificationExemptionSerializer(exemption).data, message='豁免已批准'
        )


class QualificationExemptionRevokeView(APIView):
    """撤销临时豁免"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            exemption = QualificationExemption.objects.select_related('person').get(pk=pk)
        except QualificationExemption.DoesNotExist:
            return error_response(message='豁免记录不存在', code=404)

        if exemption.is_revoked:
            return error_response(message='该豁免已被撤销')

        serializer = ExemptionRevokeSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        reason = serializer.validated_data['revoke_reason']

        with transaction.atomic():
            exemption.is_revoked = True
            exemption.revoked_by = request.user
            exemption.revoked_at = timezone.now()
            exemption.revoke_reason = reason
            exemption.save(update_fields=[
                'is_revoked', 'revoked_by', 'revoked_at', 'revoke_reason'
            ])
            QualificationChangeLog.objects.create(
                person=exemption.person,
                action='exempt_revoke',
                business=exemption.business,
                exemption=exemption,
                approver=request.user,
                basis=reason,
                remark='提前撤销临时豁免',
            )

        return success_response(
            data=QualificationExemptionSerializer(exemption).data, message='豁免已撤销'
        )


# ==================== 变更台账与核验记录 ====================

class QualificationChangeLogView(APIView):
    """资质变更台账"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = (
            QualificationChangeLog.objects
            .select_related('person', 'approver')
            .all().order_by('-created_at')
        )
        person_id = request.query_params.get('person')
        action = request.query_params.get('action')
        if person_id:
            queryset = queryset.filter(person_id=person_id)
        if action:
            queryset = queryset.filter(action=action)
        records, total, page, page_size = _paginate(request, queryset)
        serializer = QualificationChangeLogSerializer(records, many=True)
        return success_response(data={
            'list': serializer.data, 'total': total, 'page': page, 'page_size': page_size
        })


class QualificationCheckListView(APIView):
    """关键节点资质核验记录（当时状态证据）"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = (
            QualificationCheck.objects
            .select_related('person', 'user')
            .all().order_by('-checked_at')
        )
        person_id = request.query_params.get('person')
        business = request.query_params.get('business')
        result = request.query_params.get('result')
        if person_id:
            queryset = queryset.filter(person_id=person_id)
        if business:
            queryset = queryset.filter(business=business)
        if result:
            queryset = queryset.filter(result=result)
        records, total, page, page_size = _paginate(request, queryset)
        serializer = QualificationCheckSerializer(records, many=True)
        return success_response(data={
            'list': serializer.data, 'total': total, 'page': page, 'page_size': page_size
        })

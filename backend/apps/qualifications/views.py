"""
人员资质管理视图。

续证、撤销、临时豁免等改变资质状态的操作仅限管理员，
且统一通过 services 落库，确保每一次变化都写入带批准依据的 QualificationEvent。
"""
import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.authentication.models import User
from apps.core.response import success_response, error_response

from .models import (
    Qualification,
    QualificationCheck,
    QualificationEvent,
    QualificationType,
    QualificationWaiver,
)
from .serializers import (
    QualificationCheckSerializer,
    QualificationEventSerializer,
    QualificationRenewSerializer,
    QualificationRevokeSerializer,
    QualificationSerializer,
    QualificationTypeSerializer,
    WaiverGrantSerializer,
    WaiverRevokeSerializer,
    WaiverSerializer,
)
from .services import (
    QualificationError,
    evaluate,
    grant_waiver,
    renew_qualification,
    revoke_qualification,
    revoke_waiver,
)

logger = logging.getLogger('apps')


def _is_admin(user):
    return bool(user and user.is_authenticated and user.role in ('superadmin', 'admin'))


def _paginate(request, queryset):
    page = int(request.query_params.get('page', 1))
    page_size = int(request.query_params.get('page_size', 10))
    start = (page - 1) * page_size
    return queryset.count(), queryset[start:start + page_size], page, page_size


# ==================== 资质类型 ====================

class QualificationTypeListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = QualificationType.objects.select_related('created_by').all().order_by('code')
        keyword = request.query_params.get('keyword')
        if keyword:
            queryset = queryset.filter(name__icontains=keyword) | queryset.filter(code__icontains=keyword)
        total, items, page, page_size = _paginate(request, queryset)
        return success_response(data={
            'list': QualificationTypeSerializer(items, many=True).data,
            'total': total, 'page': page, 'page_size': page_size,
        })

    def post(self, request):
        if not _is_admin(request.user):
            return error_response(message='仅管理员可登记资质类型', code=403)
        serializer = QualificationTypeSerializer(data=request.data)
        if not serializer.is_valid():
            first = list(serializer.errors.values())[0]
            return error_response(message=str(first[0] if isinstance(first, list) else first))
        qt = QualificationType.objects.create(created_by=request.user, **serializer.validated_data)
        logger.info("用户 %s 登记资质类型 %s", request.user.username, qt.code)
        return success_response(data=QualificationTypeSerializer(qt).data, message='创建成功')


class QualificationTypeDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        qt = QualificationType.objects.filter(pk=pk).first()
        if not qt:
            return error_response(message='资质类型不存在', code=404)
        return success_response(data=QualificationTypeSerializer(qt).data)

    def put(self, request, pk):
        if not _is_admin(request.user):
            return error_response(message='仅管理员可修改资质类型', code=403)
        qt = QualificationType.objects.filter(pk=pk).first()
        if not qt:
            return error_response(message='资质类型不存在', code=404)
        serializer = QualificationTypeSerializer(qt, data=request.data, context={'instance': qt})
        if not serializer.is_valid():
            first = list(serializer.errors.values())[0]
            return error_response(message=str(first[0] if isinstance(first, list) else first))
        for field, value in serializer.validated_data.items():
            setattr(qt, field, value)
        qt.save()
        return success_response(data=QualificationTypeSerializer(qt).data, message='更新成功')


class QualificationTypeAllView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qts = QualificationType.objects.filter(is_active=True).order_by('code')
        business = request.query_params.get('business')
        if business:
            qts = [qt for qt in qts if business in (qt.applicable_business or [])]
        return success_response(data=QualificationTypeSerializer(qts, many=True).data)


# ==================== 人员资质证件 ====================

class QualificationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = Qualification.objects.select_related(
            'holder', 'qualification_type'
        ).all().order_by('-id')
        holder = request.query_params.get('holder')
        if holder:
            queryset = queryset.filter(holder_id=holder)
        qtype = request.query_params.get('qualification_type')
        if qtype:
            queryset = queryset.filter(qualification_type_id=qtype)
        status = request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)
        total, items, page, page_size = _paginate(request, queryset)
        return success_response(data={
            'list': QualificationSerializer(items, many=True).data,
            'total': total, 'page': page, 'page_size': page_size,
        })

    def post(self, request):
        if not _is_admin(request.user):
            return error_response(message='仅管理员可登记人员资质', code=403)
        serializer = QualificationSerializer(data=request.data)
        if not serializer.is_valid():
            first = list(serializer.errors.values())[0]
            return error_response(message=str(first[0] if isinstance(first, list) else first))
        qual = Qualification.objects.create(**serializer.validated_data)
        logger.info("用户 %s 为 %s 登记资质 %s",
                    request.user.username, qual.holder_id, qual.qualification_type_id)
        return success_response(data=QualificationSerializer(qual).data, message='创建成功')


class QualificationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        qual = Qualification.objects.select_related('holder', 'qualification_type').filter(pk=pk).first()
        if not qual:
            return error_response(message='资质不存在', code=404)
        return success_response(data=QualificationSerializer(qual).data)


class QualificationRenewView(APIView):
    """续证：延长有效期，留批准依据。"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not _is_admin(request.user):
            return error_response(message='仅管理员可办理续证', code=403)
        qual = Qualification.objects.select_related('qualification_type').filter(pk=pk).first()
        if not qual:
            return error_response(message='资质不存在', code=404)
        serializer = QualificationRenewSerializer(data=request.data)
        if not serializer.is_valid():
            first = list(serializer.errors.values())[0]
            return error_response(message=str(first[0] if isinstance(first, list) else first))
        data = serializer.validated_data
        try:
            event = renew_qualification(
                qual,
                new_valid_until=data['new_valid_until'],
                actor=request.user,
                approval_basis=data['approval_basis'],
                approved_by=data.get('approved_by', ''),
                new_version=data.get('new_version') or None,
                new_certificate_no=data.get('new_certificate_no') or None,
                detail=data.get('detail', ''),
            )
        except QualificationError as exc:
            return error_response(message=str(exc))
        qual.refresh_from_db()
        logger.info("用户 %s 为资质 %s 续证至 %s（事件 %s）",
                    request.user.username, pk, qual.valid_until, event.id)
        return success_response(
            data={'qualification': QualificationSerializer(qual).data, 'event_id': event.id},
            message='续证成功',
        )


class QualificationRevokeView(APIView):
    """撤销资质：留批准依据，立即对后续动作生效。"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not _is_admin(request.user):
            return error_response(message='仅管理员可撤销资质', code=403)
        qual = Qualification.objects.filter(pk=pk).first()
        if not qual:
            return error_response(message='资质不存在', code=404)
        if qual.status == Qualification.STATUS_REVOKED:
            return error_response(message='该资质已处于撤销状态')
        serializer = QualificationRevokeSerializer(data=request.data)
        if not serializer.is_valid():
            first = list(serializer.errors.values())[0]
            return error_response(message=str(first[0] if isinstance(first, list) else first))
        data = serializer.validated_data
        event = revoke_qualification(
            qual,
            actor=request.user,
            approval_basis=data['approval_basis'],
            approved_by=data.get('approved_by', ''),
            detail=data.get('detail', ''),
        )
        logger.info("用户 %s 撤销资质 %s（事件 %s）", request.user.username, pk, event.id)
        qual.refresh_from_db()
        return success_response(
            data={'qualification': QualificationSerializer(qual).data, 'event_id': event.id},
            message='资质已撤销',
        )


# ==================== 临时豁免 ====================

class WaiverListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = QualificationWaiver.objects.select_related(
            'holder', 'qualification_type', 'approved_by'
        ).all().order_by('-approved_at')
        holder = request.query_params.get('holder')
        if holder:
            queryset = queryset.filter(holder_id=holder)
        business = request.query_params.get('business')
        if business:
            queryset = queryset.filter(business=business)
        total, items, page, page_size = _paginate(request, queryset)
        return success_response(data={
            'list': WaiverSerializer(items, many=True).data,
            'total': total, 'page': page, 'page_size': page_size,
        })

    def post(self, request):
        if not _is_admin(request.user):
            return error_response(message='仅管理员可批准临时豁免', code=403)
        serializer = WaiverGrantSerializer(data=request.data)
        if not serializer.is_valid():
            first = list(serializer.errors.values())[0]
            return error_response(message=str(first[0] if isinstance(first, list) else first))
        data = serializer.validated_data

        holder = User.objects.filter(pk=data['holder']).first()
        if not holder:
            return error_response(message='被豁免人员不存在', code=404)
        qtype = None
        if data.get('qualification_type'):
            qtype = QualificationType.objects.filter(pk=data['qualification_type']).first()
            if not qtype:
                return error_response(message='资质类型不存在', code=404)
        stock_out = None
        if data.get('stock_out'):
            from apps.warehouse.models import StockOut
            stock_out = StockOut.objects.filter(pk=data['stock_out']).first()
            if not stock_out:
                return error_response(message='关联出库单不存在', code=404)

        try:
            waiver = grant_waiver(
                holder=holder,
                business=data['business'],
                reason=data['reason'],
                actor=request.user,
                approval_basis=data['approval_basis'],
                valid_from=data['valid_from'],
                valid_until=data['valid_until'],
                approved_by=data.get('approved_by', ''),
                qualification_type=qtype,
                stock_out=stock_out,
                detail=data.get('detail', ''),
            )
        except QualificationError as exc:
            return error_response(message=str(exc))
        logger.info("用户 %s 批准豁免 %s（人员 %s）", request.user.username, waiver.id, holder.id)
        return success_response(data=WaiverSerializer(waiver).data, message='临时豁免已批准')


class WaiverRevokeView(APIView):
    """撤销临时豁免：留批准依据。"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not _is_admin(request.user):
            return error_response(message='仅管理员可撤销豁免', code=403)
        waiver = QualificationWaiver.objects.filter(pk=pk).first()
        if not waiver:
            return error_response(message='豁免不存在', code=404)
        if waiver.status == QualificationWaiver.STATUS_REVOKED:
            return error_response(message='该豁免已被撤销')
        serializer = WaiverRevokeSerializer(data=request.data)
        if not serializer.is_valid():
            first = list(serializer.errors.values())[0]
            return error_response(message=str(first[0] if isinstance(first, list) else first))
        data = serializer.validated_data
        try:
            revoke_waiver(
                waiver,
                actor=request.user,
                reason=data['reason'],
                approval_basis=data['approval_basis'],
                detail=data.get('detail', ''),
            )
        except QualificationError as exc:
            return error_response(message=str(exc))
        logger.info("用户 %s 撤销豁免 %s", request.user.username, pk)
        waiver.refresh_from_db()
        return success_response(data=WaiverSerializer(waiver).data, message='豁免已撤销')


# ==================== 事件与核验记录（只读审计） ====================

class QualificationEventListView(APIView):
    """续证/撤销/豁免的批准依据流水（只追加，不可修改）。"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = QualificationEvent.objects.select_related('actor', 'qualification', 'waiver').all()
        event_type = request.query_params.get('event_type')
        if event_type:
            queryset = queryset.filter(event_type=event_type)
        qualification = request.query_params.get('qualification')
        if qualification:
            queryset = queryset.filter(qualification_id=qualification)
        waiver = request.query_params.get('waiver')
        if waiver:
            queryset = queryset.filter(waiver_id=waiver)
        total, items, page, page_size = _paginate(request, queryset)
        return success_response(data={
            'list': QualificationEventSerializer(items, many=True).data,
            'total': total, 'page': page, 'page_size': page_size,
        })


class QualificationCheckListView(APIView):
    """关键节点固化的当时资质证据。"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = QualificationCheck.objects.select_related('assignee').all()
        stock_out = request.query_params.get('stock_out')
        if stock_out:
            queryset = queryset.filter(stock_out_id=stock_out)
        business = request.query_params.get('business')
        if business:
            queryset = queryset.filter(business=business)
        total, items, page, page_size = _paginate(request, queryset)
        return success_response(data={
            'list': QualificationCheckSerializer(items, many=True).data,
            'total': total, 'page': page, 'page_size': page_size,
        })


class EligibilityView(APIView):
    """
    资格预检：在分配或重新分配前查询某人当前是否可办理指定业务。

    GET /api/eligibility/?user_id=&business=approval|receive|release[&stock_out=]
    不落库，仅返回当前时点的判定与不满足原因，便于选择合格的接替人员。
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user_id = request.query_params.get('user_id')
        business = request.query_params.get('business')
        if business not in ('approval', 'receive', 'release'):
            return error_response(message='business 必须为 approval/receive/release')
        user = User.objects.filter(pk=user_id).first()
        if not user:
            return error_response(message='人员不存在', code=404)
        if not user.is_active:
            return success_response(data={
                'eligible': False,
                'reasons': ['账号已停用，请重新分配给其他人员'],
                'snapshot': [],
            })

        stock_out = None
        stock_out_id = request.query_params.get('stock_out')
        if stock_out_id:
            from apps.warehouse.models import StockOut
            stock_out = StockOut.objects.filter(pk=stock_out_id).first()

        outcome = evaluate(user, business, stock_out=stock_out)
        return success_response(data={
            'eligible': outcome['passed'],
            'basis': outcome['basis'],
            'reasons': outcome['reasons'],
            'snapshot': outcome['snapshot'],
            'no_requirement': outcome.get('no_requirement', False),
        })

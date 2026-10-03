"""
人员管理URL配置
"""
from django.urls import path
from .views import (
    StockOutPersonListView, StockOutPersonDetailView, AdminUserListView,
    QualificationTypeListView, QualificationTypeAllView, QualificationTypeDetailView,
    PersonnelQualificationListView, QualificationGrantView, QualificationRevokeView,
    QualificationExemptionListView, QualificationExemptionRevokeView,
    QualificationChangeLogView, QualificationCheckListView,
)

urlpatterns = [
    path('stock-out-persons/', StockOutPersonListView.as_view(), name='stock-out-person-list'),
    path('stock-out-persons/<int:pk>/', StockOutPersonDetailView.as_view(), name='stock-out-person-detail'),
    path('admin-users/', AdminUserListView.as_view(), name='admin-user-list'),

    # 资质类型
    path('qualification-types/', QualificationTypeListView.as_view(), name='qualification-type-list'),
    path('qualification-types/all/', QualificationTypeAllView.as_view(), name='qualification-type-all'),
    path('qualification-types/<int:pk>/', QualificationTypeDetailView.as_view(), name='qualification-type-detail'),

    # 人员资质：登记/续证、撤销
    path('qualifications/', PersonnelQualificationListView.as_view(), name='qualification-list'),
    path('qualifications/grant/', QualificationGrantView.as_view(), name='qualification-grant'),
    path('qualifications/<int:pk>/revoke/', QualificationRevokeView.as_view(), name='qualification-revoke'),

    # 临时豁免
    path('exemptions/', QualificationExemptionListView.as_view(), name='exemption-list'),
    path('exemptions/<int:pk>/revoke/', QualificationExemptionRevokeView.as_view(), name='exemption-revoke'),

    # 变更台账与核验记录
    path('qualification-logs/', QualificationChangeLogView.as_view(), name='qualification-log'),
    path('qualification-checks/', QualificationCheckListView.as_view(), name='qualification-check'),
]

"""
人员资质管理URL配置
"""
from django.urls import path

from .views import (
    EligibilityView,
    QualificationCheckListView,
    QualificationDetailView,
    QualificationEventListView,
    QualificationListCreateView,
    QualificationRenewView,
    QualificationRevokeView,
    QualificationTypeAllView,
    QualificationTypeDetailView,
    QualificationTypeListCreateView,
    WaiverListCreateView,
    WaiverRevokeView,
)

urlpatterns = [
    # 资质类型
    path('qualification-types/', QualificationTypeListCreateView.as_view(), name='qualification-type-list'),
    path('qualification-types/all/', QualificationTypeAllView.as_view(), name='qualification-type-all'),
    path('qualification-types/<int:pk>/', QualificationTypeDetailView.as_view(), name='qualification-type-detail'),

    # 人员资质证件
    path('qualifications/', QualificationListCreateView.as_view(), name='qualification-list'),
    path('qualifications/<int:pk>/', QualificationDetailView.as_view(), name='qualification-detail'),
    path('qualifications/<int:pk>/renew/', QualificationRenewView.as_view(), name='qualification-renew'),
    path('qualifications/<int:pk>/revoke/', QualificationRevokeView.as_view(), name='qualification-revoke'),

    # 临时豁免
    path('waivers/', WaiverListCreateView.as_view(), name='waiver-list'),
    path('waivers/<int:pk>/revoke/', WaiverRevokeView.as_view(), name='waiver-revoke'),

    # 审计：事件流水、节点核验证据
    path('qualification-events/', QualificationEventListView.as_view(), name='qualification-event-list'),
    path('qualification-checks/', QualificationCheckListView.as_view(), name='qualification-check-list'),

    # 资格预检（分配/重新分配时使用）
    path('eligibility/', EligibilityView.as_view(), name='eligibility'),
]

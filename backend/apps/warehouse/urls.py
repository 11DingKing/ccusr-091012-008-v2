"""
仓库管理URL配置
"""
from django.urls import path
from .views import (
    UnitListView, UnitDetailView, UnitBatchDeleteView, UnitAllView,
    CategoryListView, CategoryDetailView, CategoryBatchDeleteView, CategoryAllView,
    VarietyListView, VarietyDetailView, VarietyBatchDeleteView,
    VarietyTemplateView, VarietyImportView,
    GoodsListView, StockInListView, StockOutListView,
    StockOutReleaseView, WarningListView,
    ApprovalListView, ApprovalDecisionView,
)

urlpatterns = [
    # 单位管理
    path('units/', UnitListView.as_view(), name='unit-list'),
    path('units/all/', UnitAllView.as_view(), name='unit-all'),
    path('units/batch-delete/', UnitBatchDeleteView.as_view(), name='unit-batch-delete'),
    path('units/<int:pk>/', UnitDetailView.as_view(), name='unit-detail'),

    # 品类管理
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('categories/all/', CategoryAllView.as_view(), name='category-all'),
    path('categories/batch-delete/', CategoryBatchDeleteView.as_view(), name='category-batch-delete'),
    path('categories/<int:pk>/', CategoryDetailView.as_view(), name='category-detail'),

    # 品种管理
    path('varieties/', VarietyListView.as_view(), name='variety-list'),
    path('varieties/batch-delete/', VarietyBatchDeleteView.as_view(), name='variety-batch-delete'),
    path('varieties/template/', VarietyTemplateView.as_view(), name='variety-template'),
    path('varieties/import/', VarietyImportView.as_view(), name='variety-import'),
    path('varieties/<int:pk>/', VarietyDetailView.as_view(), name='variety-detail'),

    # 货物管理
    path('goods/', GoodsListView.as_view(), name='goods-list'),

    # 收件（入库）：节点核验“收件”资质
    path('stock-in/', StockInListView.as_view(), name='stock-in-list'),

    # 出库申请
    path('stock-out/', StockOutListView.as_view(), name='stock-out-list'),

    # 放行：节点核验“放行”资质
    path('stock-out/<int:pk>/release/', StockOutReleaseView.as_view(), name='stock-out-release'),

    # 预警管理
    path('warnings/', WarningListView.as_view(), name='warning-list'),

    # 审批：节点核验“审批”资质
    path('approvals/', ApprovalListView.as_view(), name='approval-list'),
    path('stock-out/<int:pk>/approval/<str:decision>/', ApprovalDecisionView.as_view(),
         name='stock-out-approval'),
]

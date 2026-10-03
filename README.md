# 监管物资保管服务

该项目为监管仓、证物室和受控物资保管点提供服务端 API，覆盖人员授权、物资分类、批次登记、收发记录、审批、预警、审计日志与统计报表。数据保存在 SQLite，所有测试和接口验收均可在单个 Linux 应用容器内离线完成。

## 运行环境

- Python 3.11
- Django REST Framework
- SQLite

## 安装与初始化

```bash
python -m pip install -r backend/requirements.txt
cd backend
python manage.py migrate --run-syncdb
```

## 测试

```bash
cd backend
pytest -q
```

## 编译检查

```bash
python -m compileall -q backend
```

## API 验收

```bash
cd backend
python manage.py migrate --run-syncdb
python manage.py shell -c "from rest_framework.test import APIClient; from apps.authentication.models import User; u=User.objects.create_user('smoke','safe-pass',role='admin'); c=APIClient(); r=c.post('/api/auth/login/',{'username':'smoke','password':'safe-pass'},format='json'); print(r.status_code, bool(r.json()['data']['token']))"
```

## 人员资质与关键节点核验

针对“仅校验账号启用、不核对培训授权证件”的风险，系统在出库流程的
**审批、收件、放行**三个关键节点固定核验办理人“当时”的资质状态：

- `apps/qualifications` 登记**资质类型**（类型、版本、适用业务）、人员**资质证件**
  （有效期、状态），以及**临时豁免**。
- 续证、撤销、临时豁免/豁免撤销都写入只追加的**资质事件**（`QualificationEvent`），
  必须填写**批准依据**（批准文件/决定编号、口头授权记录等）。
- 每个关键节点在动作发生当时生成不可修改的**核验记录**（`QualificationCheck`），
  固化结论、依据与资质状态快照；阻断也会留证。
- 资质在任务中途失效（过期/撤销/豁免到期）时，**尚未完成的动作一律阻断**，
  返回 `need_reassign=true`、具体原因及当前可接替人员；管理员可通过
  `/api/stock-out/<id>/assign/` 重新分配。
- **历史已完成操作按当时证据解释**：事后续证、撤销或过期不追溯既往，
  已完成节点的责任人不得改派。

主要接口（均需登录，写操作限管理员）：

| 方法 & 路径 | 说明 |
| --- | --- |
| `GET/POST /api/qualification-types/` | 资质类型列表 / 登记（含版本、适用业务） |
| `GET/POST /api/qualifications/` | 人员资质列表 / 登记 |
| `POST /api/qualifications/<id>/renew/` | 续证（须批准依据） |
| `POST /api/qualifications/<id>/revoke/` | 撤销资质（须批准依据） |
| `GET/POST /api/waivers/` | 临时豁免列表 / 批准（须事由、批准依据、时限） |
| `POST /api/waivers/<id>/revoke/` | 撤销豁免（须原因、批准依据） |
| `GET /api/qualification-events/` | 续证/撤销/豁免的批准依据流水 |
| `GET /api/qualification-checks/` | 各节点固化的当时资质证据 |
| `GET /api/eligibility/?user_id=&business=` | 分配/重新分配前的资格预检 |
| `POST /api/stock-out/<id>/approval/` | 审批（`action=approve` 触发资质核验，`reject` 不授权） |
| `POST /api/stock-out/<id>/receive/` | 收件（触发资质核验） |
| `POST /api/stock-out/<id>/release/` | 放行（触发资质核验并扣减库存） |
| `POST /api/stock-out/<id>/assign/` | 分配/重新分配节点责任人（仅未完成节点） |

## 容器

```bash
docker build -t custody-service .
docker run --rm custody-service
```

# Citibank Backend API

基于 FastAPI 构建的 Citibank 后端服务 API。

## 功能特性

- ✅ RESTful API 设计
- ✅ 自动生成 API 文档 (Swagger UI / ReDoc)
- ✅ 健康检查端点 (ping/health)
- ✅ CORS 跨域支持
- ✅ 环境变量配置管理
- ✅ 模块化项目结构
- ✅ Docker 容器化部署
- ✅ 数据库支持 (MySQL, Redis)
- ✅ Kafka 消息队列集成

## 项目结构

```
Citibank-Backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # 应用入口
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       └── endpoints/
│   │           ├── __init__.py
│   │           └── health.py  # ping/健康检查端点
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py        # 配置管理
│   └── schemas/
│       └── __init__.py
├── tests/
│   └── __init__.py
├── .env.example             # 环境变量示例
├── .gitignore
├── requirements.txt         # 依赖列表
├── pyproject.toml          # 项目元数据
└── README.md               # 项目文档
```

## 技术栈

- **Python**: 3.8+
- **FastAPI**: 现代化的 Web 框架
- **Uvicorn**: ASGI 服务器
- **Pydantic**: 数据验证

## 服务组件

本项目集成了以下服务:

- **Redis**: 缓存和会话存储
- **MySQL**: 关系型数据库
- **Kafka**: 消息队列
- **Zookeeper**: Kafka 依赖服务

## 快速开始

### 方式一: 使用 Docker (推荐)

```bash
# 1. 启动所有服务(包括应用)
docker-compose up -d

# 2. 查看服务状态
docker-compose ps

# 3. 查看日志
docker-compose logs -f app
```

### 方式二: 本地开发

#### 1. 启动基础服务

```bash
# 启动 Redis, MySQL, PostgreSQL, Kafka
docker-compose -f docker-compose.dev.yml up -d
```

#### 2. 安装依赖

```bash
pip install -r requirements.txt
```

#### 3. 配置环境变量

```bash
# 复制环境变量示例文件
cp .env.example .env

# 根据需要修改 .env 文件
```

#### 4. 启动应用

```bash
# 使用 uvicorn 启动
uvicorn app.main:app --host 0.0.0.0 --port 8091 --reload
```

### 访问服务

服务启动后,访问以下地址:

- **API 文档 (Swagger)**: http://localhost:8091/docs
- **API 文档 (ReDoc)**: http://localhost:8091/redoc
- **Kafka UI**: http://localhost:8081 (仅 Docker 模式)
- **健康检查**: http://localhost:8091/api/v1/health
- **Ping**: http://localhost:8091/api/v1/ping

## API 端点

### 根路径
- `GET /` - 获取 API 基本信息

### 健康检查
- `GET /api/v1/ping` - Ping 检查,返回 "pong"
- `GET /api/v1/health` - 详细健康检查,返回服务状态和时间戳

## 示例请求

### Ping 检查

```bash
curl http://localhost:8091/api/v1/ping
```

响应:
```json
{
  "message": "pong"
}
```

### 健康检查

```bash
curl http://localhost:8091/api/v1/health
```

响应:
```json
{
  "status": "healthy",
  "timestamp": "2026-02-12T13:00:00.000000",
  "service": "Citibank Backend API",
  "version": "0.1.0"
}
```

## 开发指南

### 添加新的 API 端点

1. 在 `app/api/v1/endpoints/` 目录下创建新的端点文件
2. 在 `app/api/v1/__init__.py` 中注册路由
3. 如需数据模型,在 `app/schemas/` 中定义

### 配置管理

所有配置项都在 `app/core/config.py` 中定义,可通过环境变量覆盖。

### 运行测试

```bash
pytest
```

## Docker 部署

详细的 Docker 部署说明请参考 [README_DOCKER.md](README_DOCKER.md)

### 快速命令

```bash
# 启动所有服务
docker-compose up -d

# 仅启动基础服务(用于本地开发)
docker-compose -f docker-compose.dev.yml up -d

# 停止服务
docker-compose down

# 查看日志
docker-compose logs -f
```

## 数据库连接

### MySQL
```
Host: localhost:3306
Database: citibank_db
User: citibank
Password: citibank123
```

### Redis
```
Host: localhost:6379
Database: 0
```

### Kafka
```
Bootstrap Servers: localhost:9093
```

## 生产环境部署

```bash
# 使用 Docker Compose
docker-compose up -d

# 或使用多个 worker 进程
uvicorn app.main:app --host 0.0.0.0 --port 8091 --workers 4
```

### 安全建议

1. 修改所有默认密码
2. 使用环境变量管理敏感信息
3. 启用 HTTPS
4. 配置防火墙规则
5. 定期备份数据库

## 许可证

MIT License

## 联系方式

如有问题,请联系开发团队。
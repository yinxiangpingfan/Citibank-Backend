# Docker 部署指南

本文档介绍如何使用 Docker 和 Docker Compose 部署 Citibank Backend 项目。

## 前置要求

- Docker 20.10+
- Docker Compose 2.0+

## 服务说明

项目包含以下服务:

| 服务 | 端口 | 说明 |
|------|------|------|
| FastAPI 应用 | 8091 | 主应用服务 |
| Redis | 6379 | 缓存和会话存储 |
| MySQL | 3306 | 关系型数据库 |
| Kafka | 9092 (内部), 9093 (外部) | 消息队列 |
| Zookeeper | 2181 | Kafka 依赖 |
| Kafka UI | 8081 | Kafka 管理界面 |

## 快速开始

### 1. 仅启动基础服务(开发环境推荐)

如果你想在本地运行 FastAPI 应用,只需要启动基础服务:

```bash
# 启动 Redis, MySQL, Kafka 等基础服务
docker-compose -f docker-compose.dev.yml up -d

# 查看服务状态
docker-compose -f docker-compose.dev.yml ps

# 查看日志
docker-compose -f docker-compose.dev.yml logs -f

# 停止服务
docker-compose -f docker-compose.dev.yml down

# 停止服务并删除数据卷
docker-compose -f docker-compose.dev.yml down -v
```

然后在本地运行 FastAPI 应用:

```bash
# 复制环境变量文件
cp .env.example .env

# 启动应用
uvicorn app.main:app --host 0.0.0.0 --port 8091 --reload
```

### 2. 启动完整服务(包括应用)

```bash
# 启动所有服务(包括 FastAPI 应用)
docker-compose up -d

# 查看服务状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 查看特定服务日志
docker-compose logs -f app
docker-compose logs -f kafka

# 停止服务
docker-compose down

# 停止服务并删除数据卷
docker-compose down -v
```

## 服务访问

启动后可以访问以下服务:

- **API 文档**: http://localhost:8091/docs
- **API ReDoc**: http://localhost:8091/redoc
- **Kafka UI**: http://localhost:8081
- **Redis**: localhost:6379
- **MySQL**: localhost:3306
- **Kafka**: localhost:9093 (外部访问)

## 数据库连接信息

### MySQL

```
Host: localhost
Port: 3306
Database: citibank_db
User: citibank
Password: citibank123
Root Password: root123
```

连接字符串:
```
mysql+pymysql://citibank:citibank123@localhost:3306/citibank_db
```

### Redis

```
Host: localhost
Port: 6379
Database: 0
Password: (无)
```

连接字符串:
```
redis://localhost:6379/0
```

### Kafka

```
Bootstrap Servers: localhost:9093
```

## 常用命令

### 重启服务

```bash
# 重启所有服务
docker-compose restart

# 重启特定服务
docker-compose restart app
docker-compose restart kafka
```

### 查看服务状态

```bash
# 查看所有服务状态
docker-compose ps

# 查看服务资源使用情况
docker stats
```

### 进入容器

```bash
# 进入应用容器
docker-compose exec app bash

# 进入 MySQL 容器
docker-compose exec mysql bash

# 进入 Redis 容器
docker-compose exec redis sh

# 进入 Kafka 容器
docker-compose exec kafka bash
```

### 数据库操作

```bash
# 连接 MySQL
docker-compose exec mysql mysql -ucitibank -pcitibank123 citibank_db

# 连接 Redis
docker-compose exec redis redis-cli
```

### 清理数据

```bash
# 停止并删除所有容器
docker-compose down

# 停止并删除所有容器和数据卷
docker-compose down -v

# 删除所有未使用的镜像
docker image prune -a

# 完全清理(谨慎使用)
docker system prune -a --volumes
```

## 数据持久化

所有数据都存储在 Docker 卷中:

- `redis-data`: Redis 数据
- `mysql-data`: MySQL 数据
- `kafka-data`: Kafka 数据
- `zookeeper-data`: Zookeeper 数据
- `zookeeper-logs`: Zookeeper 日志

查看数据卷:

```bash
docker volume ls | grep citibank
```

## 初始化脚本

数据库初始化脚本位于:

- MySQL: `docker/mysql/init/01-init.sql`

这些脚本会在容器首次启动时自动执行。

## 健康检查

所有服务都配置了健康检查,可以通过以下命令查看:

```bash
docker-compose ps
```

健康状态说明:
- `healthy`: 服务正常运行
- `unhealthy`: 服务异常
- `starting`: 服务正在启动

## 故障排查

### 服务启动失败

```bash
# 查看详细日志
docker-compose logs -f [service_name]

# 检查容器状态
docker-compose ps
```

### 端口冲突

如果端口被占用,可以修改 `docker-compose.yml` 中的端口映射:

```yaml
ports:
  - "新端口:容器端口"
```

### 数据库连接失败

1. 确认服务已启动: `docker-compose ps`
2. 检查健康状态是否为 `healthy`
3. 查看服务日志: `docker-compose logs [service_name]`

### Kafka 连接问题

确保使用正确的地址:
- 容器内访问: `kafka:9092`
- 宿主机访问: `localhost:9093`

## 生产环境部署建议

1. **修改默认密码**: 更改所有服务的默认密码
2. **配置资源限制**: 在 docker-compose.yml 中添加 CPU 和内存限制
3. **启用日志轮转**: 配置 Docker 日志驱动
4. **使用外部卷**: 将数据卷映射到宿主机目录
5. **配置备份策略**: 定期备份数据库和 Kafka 数据
6. **监控和告警**: 集成 Prometheus、Grafana 等监控工具

## 参考资料

- [Docker 官方文档](https://docs.docker.com/)
- [Docker Compose 文档](https://docs.docker.com/compose/)
- [FastAPI 文档](https://fastapi.tiangolo.com/)
- [Kafka 文档](https://kafka.apache.org/documentation/)

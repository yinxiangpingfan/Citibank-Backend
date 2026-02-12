# Docker Compose 环境变量说明

本文档说明如何使用 `.env` 文件配置 Docker Compose 服务。

## 环境变量文件

Docker Compose 会自动读取项目根目录下的 `.env` 文件。

### 创建 .env 文件

```bash
# 复制示例文件
cp .env.example .env

# 根据需要修改配置
vim .env
```

## 环境变量说明

### 应用配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `HOST` | `0.0.0.0` | 应用监听地址 |
| `PORT` | `8091` | 应用端口 |
| `DEBUG` | `True` | 调试模式 |

### Redis 配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `REDIS_HOST` | `redis` | Redis 主机名 |
| `REDIS_PORT` | `6379` | Redis 端口 |
| `REDIS_DB` | `0` | Redis 数据库编号 |
| `REDIS_PASSWORD` | (空) | Redis 密码 |

### MySQL 配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `MYSQL_HOST` | `mysql` | MySQL 主机名 |
| `MYSQL_PORT` | `3306` | MySQL 端口 |
| `MYSQL_ROOT_PASSWORD` | `root123` | MySQL root 密码 |
| `MYSQL_USER` | `citibank` | MySQL 用户名 |
| `MYSQL_PASSWORD` | `citibank123` | MySQL 密码 |
| `MYSQL_DATABASE` | `citibank_db` | MySQL 数据库名 |

### Kafka 配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Kafka 服务器地址 |
| `KAFKA_BROKER_ID` | `1` | Kafka Broker ID |
| `KAFKA_GROUP_ID` | `citibank-backend` | Kafka 消费者组 ID |
| `ZOOKEEPER_CLIENT_PORT` | `2181` | Zookeeper 端口 |
| `ZOOKEEPER_TICK_TIME` | `2000` | Zookeeper Tick 时间 |

### 其他配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `TZ` | `Asia/Shanghai` | 时区 |

## 使用示例

### 修改数据库密码

编辑 `.env` 文件:

```bash
MYSQL_ROOT_PASSWORD=my_secure_password
MYSQL_PASSWORD=my_user_password
```

### 修改端口

```bash
PORT=8092
MYSQL_PORT=3307
REDIS_PORT=6380
```

### 设置 Redis 密码

```bash
REDIS_PASSWORD=my_redis_password
```

## 注意事项

1. **不要提交 .env 文件到版本控制系统**
   - `.env` 文件已在 `.gitignore` 中
   - 只提交 `.env.example` 作为模板

2. **生产环境安全**
   - 修改所有默认密码
   - 使用强密码
   - 限制网络访问

3. **环境变量优先级**
   - Docker Compose 会优先使用 `.env` 文件中的值
   - 如果 `.env` 中没有定义,使用 `docker-compose.yml` 中的默认值

4. **重启服务**
   - 修改 `.env` 后需要重启服务才能生效
   ```bash
   docker-compose down
   docker-compose up -d
   ```

## 验证配置

查看服务使用的环境变量:

```bash
# 查看所有服务的环境变量
docker-compose config

# 查看特定服务的环境变量
docker-compose exec mysql env | grep MYSQL
docker-compose exec postgres env | grep POSTGRES
```

## 故障排查

### 环境变量未生效

1. 确认 `.env` 文件在项目根目录
2. 检查变量名是否正确
3. 重启服务: `docker-compose restart`

### 密码错误

1. 如果已经创建了容器,修改密码需要删除数据卷:
   ```bash
   docker-compose down -v
   docker-compose up -d
   ```

2. 或者手动修改数据库密码后再修改 `.env`

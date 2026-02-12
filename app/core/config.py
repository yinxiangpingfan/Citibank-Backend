"""
应用配置管理
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """应用配置类"""
    
    # 应用基本信息
    APP_NAME: str = "Citibank Backend API"
    APP_VERSION: str = "0.1.0"
    APP_DESCRIPTION: str = "Citibank 后端服务 API"
    
    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8091
    
    # API 配置
    API_V1_PREFIX: str = "/api/v1"
    
    # CORS 配置
    CORS_ORIGINS: list = ["*"]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: list = ["*"]
    CORS_ALLOW_HEADERS: list = ["*"]
    
    # 调试模式
    DEBUG: bool = True
    
    class Config:
        env_file = ".env"
        case_sensitive = True


# 创建全局配置实例
settings = Settings()

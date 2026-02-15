-- MySQL 初始化脚本
-- 创建数据库时自动执行

-- 设置字符集
SET NAMES utf8mb4;

SET CHARACTER SET utf8mb4;

-- 创建用户表 (ZKP零知识登录)
CREATE TABLE IF NOT EXISTS `users` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `username` VARCHAR(50) NOT NULL UNIQUE,
    `public_key_y` TEXT NOT NULL,
    `salt` VARCHAR(255) NOT NULL,
    INDEX `idx_username` (`username`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

-- 创建系统配置表
CREATE TABLE IF NOT EXISTS `system_config` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `config_key` VARCHAR(100) NOT NULL UNIQUE,
    `config_value` TEXT,
    `description` VARCHAR(255),
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX `idx_config_key` (`config_key`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

-- 插入系统配置
INSERT INTO
    `system_config` (
        `config_key`,
        `config_value`,
        `description`
    )
VALUES (
        'app_name',
        'Citibank Backend',
        '应用名称'
    ),
    (
        'app_version',
        '0.1.0',
        '应用版本'
    )
ON DUPLICATE KEY UPDATE
    `config_value` = VALUES(`config_value`);
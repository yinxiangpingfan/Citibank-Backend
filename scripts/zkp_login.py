#!/usr/bin/env python3
"""
ZKP 零知识登录测试脚本

自动完成注册和登录流程，获取 JWT Token

流程:
1. 注册: 生成私钥x, 公钥Y=g^x mod p, 发送Y到服务器
2. 登录: 生成随机k, 计算R=g^k, 获取挑战c, 计算s=k+c*x, 验证获取token
"""
import httpx
import hashlib
import secrets

# Schnorr Group Parameters (与服务器一致)
P_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF"
)
P = int(P_HEX, 16)
Q = (P - 1) // 2
G = 2

# API 基础地址
BASE_URL = "http://localhost:8091/v1"


def generate_private_key() -> int:
    """生成私钥 x (随机数)"""
    return secrets.randbelow(Q)


def compute_public_key(x: int) -> int:
    """计算公钥 Y = g^x mod p"""
    return pow(G, x, P)


def int_to_hex(n: int) -> str:
    """整数转 hex 字符串"""
    return hex(n)[2:]


async def register(username: str, x: int) -> bool:
    """注册用户"""
    Y = compute_public_key(x)
    Y_hex = int_to_hex(Y)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/auth/register",
            json={
                "username": username,
                "publicKeyY": Y_hex,
                "salt": "test_salt_12345",
            },
        )

    if resp.status_code == 200:
        print(f"✅ 注册成功: {username}")
        return True
    else:
        print(f"⚠️ 注册响应: {resp.status_code} - {resp.text}")
        return False


async def login(username: str, x: int) -> str | None:
    """登录获取 Token"""
    # Step 1: 生成随机 k 和 R
    k = secrets.randbelow(Q)
    R = pow(G, k, P)
    R_hex = int_to_hex(R)

    # Step 2: 获取挑战
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/auth/challenge",
            json={
                "username": username,
                "clientR": R_hex,
            },
        )

    if resp.status_code != 200:
        print(f"❌ 获取挑战失败: {resp.status_code} - {resp.text}")
        return None

    challenge = resp.json()
    challenge_id = challenge["challengeId"]
    c_hex = challenge["c"]

    print(f"📋 获取挑战成功: challengeId={challenge_id}")

    # Step 3: 计算 s = k + c*x mod q
    c = int(c_hex, 16)
    s = (k + c * x) % Q
    s_hex = int_to_hex(s)

    # Step 4: 验证并获取 Token
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/auth/verify",
            json={
                "challengeId": challenge_id,
                "s": s_hex,
                "clientR": R_hex,
                "username": username,
            },
        )

    if resp.status_code != 200:
        print(f"❌ 验证失败: {resp.status_code} - {resp.text}")
        return None

    token_data = resp.json()
    token = token_data["token"]
    print(f"🎉 登录成功!")
    print(f"🎫 Token: {token}")
    print(f"⏰ 过期时间: {token_data['expiresIn']} 秒")
    return token


async def main():
    import sys

    username = sys.argv[1] if len(sys.argv) > 1 else "testuser"

    # 生成或使用固定私钥 (实际应用中应安全存储)
    # 这里用一个固定的私钥方便测试
    x = int(hashlib.sha256(f"private_key_{username}".encode()).hexdigest(), 16) % Q

    print(f"🔑 用户: {username}")
    print(f"🔐 私钥 x: {x}")
    print()

    # 注册
    await register(username, x)
    print()

    # 登录
    token = await login(username, x)

    if token:
        print()
        print("=" * 60)
        print("使用以下命令测试 API:")
        print(f'curl -H "Authorization: Bearer {token}" http://localhost:8091/v1/market/snapshot?market=WTI')
        print("=" * 60)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

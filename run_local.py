"""本地开发入口，绑定回环地址并关闭代理头信任。"""
import uvicorn


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, proxy_headers=False)

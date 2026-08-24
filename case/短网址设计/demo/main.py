"""
短网址服务 Demo (FastAPI) —— 教学版
====================================
对应 ../短网址设计.md，只有三个核心 API：

    POST /shorten        长链接 -> 短链接        （写路径，文档 5.1）
    GET  /{short_code}   短链接 -> 302 跳转      （读路径，文档 5.2）
    GET  /urls           看 txt 里存了什么 + 缓存命中率

编码、存储、缓存这些实现细节都在 helper.py 里，这个文件只管路由。

启动：  uvicorn main:app --reload
文档：  http://127.0.0.1:8000/docs
"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, HttpUrl

import helper

app = FastAPI(title="短网址服务 Demo", description="Base62 + txt 存储 + 内存缓存")

print(f"[启动] 从 {helper.DB_FILE} 载入 {helper.load_db()} 条映射")


class ShortenRequest(BaseModel):
    long_url: HttpUrl


@app.post("/shorten", tags=["核心"])
def shorten(req: ShortenRequest) -> dict:
    """
    【写路径】长链接 -> 短链接（文档 5.1）

        长链接已存在？ -> 复用旧短码
                      -> 发号器给短码 -> 写入 txt -> 回填缓存
    """
    long_url = str(req.long_url)

    # 同一长链接复用同一短码（幂等，省存储）
    code = helper.db_find_code(long_url)
    if code:
        return {"short_url": helper.short_url(code), "short_code": code, "created": False}

    code = helper.generate_code()
    helper.db_insert(code, long_url)
    
    helper.cache_set(code, long_url)   # 写完顺手回填，第一次点击就能命中缓存

    return {"short_url": helper.short_url(code), "short_code": code, "created": True}


@app.get("/urls", tags=["核心"])
def list_urls() -> dict:
    """看一眼 txt 里存了什么，以及缓存命中率"""
    return {
        "total": helper.db_count(),
        "urls": helper.db_all(),
        "cache": helper.cache_stats(),
    }


@app.get("/{short_code}", tags=["核心"])
def redirect(short_code: str):
    """
    【读路径】短链接 -> 302 跳转（文档 5.2）

        查缓存 -> 命中：直接跳（内存访问 <1ms）
              -> 未命中：回源查 txt -> 回填缓存 -> 跳

    用 302 而不是 301：301 会被浏览器缓存，后续点击不再回源，就统计不到点击量了（文档 5.3）。
    """
    long_url = helper.cache_get(short_code)

    if not long_url:
        long_url = helper.db_get(short_code)      # 回源查“数据库”
        if not long_url:
            raise HTTPException(404, "短码不存在")
        helper.cache_set(short_code, long_url)    # 回填缓存

    return RedirectResponse(long_url, status_code=302)

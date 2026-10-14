import io
import os
import re
import zipfile
from urllib.parse import urlparse

import instaloader
import requests
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, HttpUrl

app = FastAPI(title="Instagram Carousel Downloader API")

API_KEY = "a8f3c912e7d64b10f5a2c8e9d1b7036a4f8c2e6b9d0a1f5c7e3b6d8a2c4f9017"

loader = instaloader.Instaloader(
    download_pictures=False,
    download_videos=False,
    download_video_thumbnails=False,
    save_metadata=False,
    compress_json=False,
)

class PostRequest(BaseModel):
    url: HttpUrl

def authenticate(x_api_key: str | None):
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

def get_post(url: str):
    parsed = urlparse(url)

    if parsed.hostname not in ("instagram.com", "www.instagram.com"):
        raise HTTPException(status_code=400, detail="Instagram URL required")

    match = re.match(r"^/(p|reel|tv)/([^/]+)/?", parsed.path)
    if not match:
        raise HTTPException(status_code=400, detail="Invalid Instagram post URL")

    try:
        post = instaloader.Post.from_shortcode(
            loader.context, match.group(2)
        )

        if post.typename == "GraphSidecar":
            nodes = list(post.get_sidecar_nodes())
        else:
            nodes = [post]

        items = []
        for i, node in enumerate(nodes, start=1):
            is_video = node.is_video
            media_url = (
                node.video_url if is_video else node.display_url
            ) if post.typename == "GraphSidecar" else (
                post.video_url if is_video else post.url
            )

            items.append({
                "index": i,
                "type": "video" if is_video else "image",
                "url": media_url,
            })

        return items

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Instagram could not provide this post. "
                   "It may require login or be rate-limited."
        )

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/api/carousel")
def carousel(
    body: PostRequest,
    x_api_key: str | None = Header(default=None),
):
    authenticate(x_api_key)
    items = get_post(str(body.url))
    return {"count": len(items), "items": items}

@app.post("/api/carousel/download")
def download(
    body: PostRequest,
    x_api_key: str | None = Header(default=None),
):
    authenticate(x_api_key)
    items = get_post(str(body.url))

    buffer = io.BytesIO()
    saved = 0

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        with requests.Session() as session:
            for item in items:
                if not item["url"]:
                    continue

                try:
                    response = session.get(item["url"], timeout=30)
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "")

                    if not content_type.startswith("image/"):
                        continue

                    extension = {
                        "image/jpeg": ".jpg",
                        "image/png": ".png",
                        "image/webp": ".webp",
                        "image/gif": ".gif",
                    }.get(content_type.split(";")[0], ".jpg")

                    archive.writestr(
                        f"image_{item['index']}{extension}",
                        response.content,
                    )
                    saved += 1
                except requests.RequestException:
                    continue

    if saved == 0:
        raise HTTPException(status_code=502, detail="No images could be downloaded")

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="carousel.zip"'
        },
    )
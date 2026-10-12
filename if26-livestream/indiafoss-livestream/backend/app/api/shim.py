"""Lightweight compatibility shim for FastAPI when running in zero-dependency environments.

Re-exports real FastAPI when installed (in production Docker containers), or
provides minimal standard-library implementations for unit tests and local CLI tools.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Union

try:
    from fastapi import (
        APIRouter,
        Depends,
        FastAPI,
        Header,
        HTTPException,
        Request,
        Response,
        status,
    )
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles

    HAS_FASTAPI = True

except ImportError:
    HAS_FASTAPI = False

    class Status:
        HTTP_200_OK = 200
        HTTP_201_CREATED = 201
        HTTP_204_NO_CONTENT = 204
        HTTP_400_BAD_REQUEST = 400
        HTTP_401_UNAUTHORIZED = 401
        HTTP_403_FORBIDDEN = 403
        HTTP_404_NOT_FOUND = 404
        HTTP_405_METHOD_NOT_ALLOWED = 405
        HTTP_500_INTERNAL_SERVER_ERROR = 500

    status = Status()

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: Any = None, headers: Optional[Dict[str, str]] = None):
            self.status_code = status_code
            self.detail = detail
            self.headers = headers or {}
            super().__init__(str(detail))

    def Depends(dependency: Optional[Callable] = None):
        return dependency

    def Header(default: Any = None):
        return default

    class Request:
        def __init__(self, method: str = "GET", headers: Optional[Dict[str, str]] = None):
            self.method = method
            self.headers = headers or {}
            self._disconnected = False

        async def is_disconnected(self) -> bool:
            return self._disconnected

    class Response:
        def __init__(
            self,
            content: Any = None,
            status_code: int = 200,
            headers: Optional[Dict[str, str]] = None,
            media_type: Optional[str] = None,
        ):
            self.content = content
            self.status_code = status_code
            self.headers = headers or {}
            self.media_type = media_type

    class JSONResponse(Response):
        def __init__(
            self,
            content: Any = None,
            status_code: int = 200,
            headers: Optional[Dict[str, str]] = None,
        ):
            super().__init__(
                content=json.dumps(content).encode("utf-8"),
                status_code=status_code,
                headers=headers,
                media_type="application/json",
            )
            self.raw_content = content

    class StreamingResponse(Response):
        def __init__(
            self,
            content: Any,
            status_code: int = 200,
            headers: Optional[Dict[str, str]] = None,
            media_type: Optional[str] = None,
        ):
            super().__init__(content=content, status_code=status_code, headers=headers, media_type=media_type)

    class FileResponse(Response):
        def __init__(self, path: str, status_code: int = 200, media_type: Optional[str] = None):
            super().__init__(content=path, status_code=status_code, media_type=media_type)
            self.path = path

    class CORSMiddleware:
        def __init__(self, app: Any, **kwargs: Any):
            self.app = app

    class StaticFiles:
        def __init__(self, directory: str, html: bool = False):
            self.directory = directory
            self.html = html

    class RouteHandler:
        def __init__(
            self,
            path: str,
            methods: List[str],
            endpoint: Callable,
            dependencies: Optional[List[Any]] = None,
            response_model: Optional[Any] = None,
        ):
            self.path = path
            self.methods = [m.upper() for m in methods]
            self.endpoint = endpoint
            self.dependencies = dependencies or []
            self.response_model = response_model

    class APIRouter:
        def __init__(self, prefix: str = ""):
            self.prefix = prefix
            self.routes: List[RouteHandler] = []

        def api_route(self, path: str, methods: Optional[List[str]] = None, **kwargs: Any):
            methods = methods or ["GET"]

            def decorator(func: Callable):
                self.routes.append(
                    RouteHandler(
                        path=self.prefix + path,
                        methods=methods,
                        endpoint=func,
                        dependencies=kwargs.get("dependencies"),
                        response_model=kwargs.get("response_model"),
                    )
                )
                return func

            return decorator

        def get(self, path: str, **kwargs: Any):
            return self.api_route(path, methods=["GET"], **kwargs)

        def post(self, path: str, **kwargs: Any):
            return self.api_route(path, methods=["POST"], **kwargs)

        def put(self, path: str, **kwargs: Any):
            return self.api_route(path, methods=["PUT"], **kwargs)

        def delete(self, path: str, **kwargs: Any):
            return self.api_route(path, methods=["DELETE"], **kwargs)

    class FastAPI:
        def __init__(
            self,
            title: str = "FastAPI",
            version: str = "0.1.0",
            lifespan: Optional[Callable] = None,
            **kwargs: Any,
        ):
            self.title = title
            self.version = version
            self.lifespan = lifespan
            self.routes: List[RouteHandler] = []
            self.middlewares: List[Any] = []
            self.mounts: Dict[str, Any] = {}
            self.exception_handlers: Dict[Any, Callable] = {}

        def add_middleware(self, middleware_class: Any, **kwargs: Any):
            self.middlewares.append((middleware_class, kwargs))

        def include_router(self, router: APIRouter):
            self.routes.extend(router.routes)

        def mount(self, path: str, app: Any, name: Optional[str] = None):
            self.mounts[path] = app

        def exception_handler(self, exc_class: Any):
            def decorator(handler: Callable):
                self.exception_handlers[exc_class] = handler
                return handler

            return decorator

        def api_route(self, path: str, methods: Optional[List[str]] = None, **kwargs: Any):
            methods = methods or ["GET"]

            def decorator(func: Callable):
                self.routes.append(
                    RouteHandler(
                        path=path,
                        methods=methods,
                        endpoint=func,
                        dependencies=kwargs.get("dependencies"),
                        response_model=kwargs.get("response_model"),
                    )
                )
                return func

            return decorator

        def get(self, path: str, **kwargs: Any):
            return self.api_route(path, methods=["GET"], **kwargs)

        def post(self, path: str, **kwargs: Any):
            return self.api_route(path, methods=["POST"], **kwargs)

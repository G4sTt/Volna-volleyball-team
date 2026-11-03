"""Validate sessions through Accounts API; this service never reads account tables."""
import os
import httpx
from fastapi import Header, HTTPException, Request

ACCOUNTS_URL = os.getenv("ACCOUNTS_URL", "http://accounts:8000").rstrip("/")

def csrf_guard(x_requested_with: str = Header("")):
    if x_requested_with != "VolnaApp":
        raise HTTPException(403, "Обновите страницу и повторите запрос")

def current_user(request: Request):
    token = request.cookies.get("volna_session", "")
    if not token or len(token) > 128:
        raise HTTPException(401, "Войдите в аккаунт")
    try:
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            response = client.get(
                f"{ACCOUNTS_URL}/auth/me",
                cookies={"volna_session": token},
            )
    except httpx.RequestError as exc:
        raise HTTPException(503, "Сервис аккаунтов временно недоступен") from exc
    if response.status_code == 401:
        raise HTTPException(401, "Сессия истекла. Войдите снова")
    if response.status_code != 200:
        raise HTTPException(503, "Не удалось проверить авторизацию")
    try:
        user = response.json()
        if not isinstance(user, dict) or not isinstance(user.get("id"), int):
            raise ValueError("Invalid account response")
        return user
    except ValueError as exc:
        raise HTTPException(503, "Некорректный ответ сервиса аккаунтов") from exc

import io
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from psycopg import Error
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, Field

from .auth_client import csrf_guard, current_user
from .database import get_connection
from .schema import init_catalog


UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

Image.MAX_IMAGE_PIXELS = 20_000_000


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_catalog()
    yield


app = FastAPI(
    title="Team Info API",
    version="1.0.0",
    lifespan=lifespan,
)


app.mount(
    "/photos",
    StaticFiles(directory=UPLOAD_DIR),
    name="photos",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PlayerCreate(BaseModel):
    model_config = {"str_strip_whitespace": True}

    number: int = Field(ge=1, le=99)
    full_name: str = Field(min_length=2, max_length=120)
    position: str = Field(min_length=2, max_length=80)
    height_cm: int = Field(ge=150, le=230)
    country: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=2000)


class PlayerInfoUpdate(BaseModel):
    model_config = {"str_strip_whitespace": True}

    description: str = Field(max_length=2000)


@app.get("/health")
def health():
    return {"status": "ok", "service": "team_info"}


@app.get("/team")
def get_team():
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            "SELECT * FROM team ORDER BY id LIMIT 1"
        )

        team = cursor.fetchone()

    if not team:
        raise HTTPException(
            status_code=404,
            detail="Команда не найдена",
        )

    return team


@app.get("/players")
def get_players():
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            "SELECT * FROM players ORDER BY number"
        )

        players = cursor.fetchall()

    for player in players:
        photo_filename = player.pop("photo_filename", None)
        player["photo_url"] = (
            f"/api/photos/{photo_filename}"
            if photo_filename
            else None
        )

    return players


def save_player(
    player: PlayerCreate,
    image_bytes: bytes | None = None,
):
    """Сохраняет игрока и необязательную фотографию."""
    saved_path = None

    try:
        with get_connection() as connection:
            cursor = connection.cursor()

            cursor.execute(
                """
                INSERT INTO players (
                    number,
                    full_name,
                    position,
                    height_cm,
                    country,
                    description
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    player.number,
                    player.full_name,
                    player.position,
                    player.height_cm,
                    player.country,
                    player.description,
                ),
            )

            player_id = cursor.fetchone()["id"]

            if image_bytes is not None:
                filename = f"{player_id}-{uuid4().hex}.jpg"
                saved_path = UPLOAD_DIR / filename
                saved_path.write_bytes(image_bytes)
                cursor.execute(
                    "UPDATE players SET photo_filename = %s WHERE id = %s",
                    (filename, player_id),
                )

            connection.commit()

    except Exception as exc:
        # Если запись не сохранилась, убираем её фотографию.
        if saved_path is not None:
            saved_path.unlink(missing_ok=True)

        if isinstance(exc, UniqueViolation):
            raise HTTPException(
                status_code=409,
                detail="Игрок с таким номером уже существует",
            ) from exc

        if isinstance(exc, (Error, OSError)):
            raise HTTPException(
                status_code=500,
                detail="Не удалось сохранить игрока",
            ) from exc

        raise

    return {
        "id": player_id,
        **player.model_dump(),
        "photo_url": (
            f"/api/photos/{saved_path.name}"
            if saved_path
            else None
        ),
    }


@app.post(
    "/players",
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(current_user),
        Depends(csrf_guard),
    ],
)
def create_player(player: PlayerCreate):
    return save_player(player)


@app.post(
    "/players/with-photo",
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(current_user),
        Depends(csrf_guard),
    ],
)
async def create_player_with_photo(
    number: Annotated[int, Form(ge=1, le=99)],
    full_name: Annotated[str, Form(min_length=2, max_length=120)],
    position: Annotated[str, Form(min_length=2, max_length=80)],
    height_cm: Annotated[int, Form(ge=150, le=230)],
    country: Annotated[str, Form(min_length=2, max_length=80)],
    description: Annotated[str, Form(max_length=2000)] = "",
    photo: Annotated[UploadFile | None, File()] = None,
):
    full_name = full_name.strip()
    position = position.strip()
    country = country.strip()

    if any(
        len(value) < 2
        for value in (full_name, position, country)
    ):
        raise HTTPException(
            status_code=422,
            detail="Заполните имя, амплуа и страну: минимум 2 символа",
        )

    player = PlayerCreate(
        number=number,
        full_name=full_name,
        position=position,
        height_cm=height_cm,
        country=country,
        description=description,
    )

    image_bytes = None

    if photo is not None:
        data = await photo.read(5 * 1024 * 1024 + 1)
        await photo.close()

        if len(data) > 5 * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail="Фото должно быть не больше 5 МБ",
            )

        try:
            with Image.open(io.BytesIO(data)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Неподдерживаемый формат")

                image = ImageOps.exif_transpose(original).convert("RGB")
                image.thumbnail((1600, 1600))

                output = io.BytesIO()
                image.save(output, format="JPEG", quality=90)

                image_bytes = output.getvalue()

        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
        ) as exc:
            raise HTTPException(
                status_code=422,
                detail="Выберите корректное фото JPG, PNG или WebP",
            ) from exc

    return save_player(player, image_bytes)


@app.patch(
    "/players/{player_id}",
    dependencies=[Depends(current_user), Depends(csrf_guard)],
)
def update_player_info(player_id: int, update: PlayerInfoUpdate):
    with get_connection() as connection:
        player = connection.execute(
            "UPDATE players SET description = %s WHERE id = %s RETURNING id, description",
            (update.description, player_id),
        ).fetchone()
        if player is None:
            raise HTTPException(status_code=404, detail="Игрок не найден")
    return player


@app.delete(
    "/players/{player_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        Depends(current_user),
        Depends(csrf_guard),
    ],
)
def delete_player(player_id: int):
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            "DELETE FROM players WHERE id = %s",
            (player_id,),
        )

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Игрок не найден",
            )

        connection.commit()

    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )


@app.get("/ready")
def ready():
    with get_connection() as connection:
        connection.execute("SELECT 1")
    return {"status": "ready", "service": "team_info"}

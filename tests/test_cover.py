"""Cover download and torrent exclusion."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from deckdrop.core.cover import (
    COVER_FILENAMES,
    clear_covers,
    download_cover_from_url,
    download_steam_cover,
    has_local_cover,
)


def test_clear_covers(tmp_path):
    game = tmp_path / "Game"
    game.mkdir()
    (game / "deckdrop.png").write_bytes(b"old")
    (game / "deckdrop.jpg").write_bytes(b"old")
    clear_covers(game)
    assert not any((game / n).exists() for n in COVER_FILENAMES)


def test_download_steam_cover_success(tmp_path):
    game = tmp_path / "Game"
    game.mkdir()
    fake_jpeg = b"\xff\xd8\xff" + b"x" * 100

    response = MagicMock()
    response.status_code = 200
    response.content = fake_jpeg
    response.headers = {"content-type": "image/jpeg"}

    with patch("deckdrop.core.cover.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = response
        ok = download_steam_cover(game, 2073850)

    assert ok is True
    assert (game / "deckdrop.jpg").read_bytes() == fake_jpeg
    assert has_local_cover(game)


def test_download_steam_cover_http_404(tmp_path):
    game = tmp_path / "Game"
    game.mkdir()

    response = MagicMock()
    response.status_code = 404
    response.content = b""

    with patch("deckdrop.core.cover.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = response
        ok = download_steam_cover(game, 999999)

    assert ok is False
    assert not has_local_cover(game)


def test_download_steam_cover_replaces_previous(tmp_path):
    game = tmp_path / "Game"
    game.mkdir()
    (game / "deckdrop.png").write_bytes(b"old-png")
    fake_jpeg = b"\xff\xd8\xff" + b"y" * 50

    response = MagicMock()
    response.status_code = 200
    response.content = fake_jpeg
    response.headers = {"content-type": "image/jpeg"}

    with patch("deckdrop.core.cover.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = response
        download_steam_cover(game, 123)

    assert not (game / "deckdrop.png").exists()
    assert (game / "deckdrop.jpg").exists()


def test_download_cover_from_url_png_extension(tmp_path):
    game = tmp_path / "Game"
    game.mkdir()
    fake_png = b"\x89PNG\r\n\x1a\n" + b"x" * 100

    response = MagicMock()
    response.status_code = 200
    response.content = fake_png
    response.headers = {"content-type": "image/png"}

    with patch("deckdrop.core.cover.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = response
        ok = download_cover_from_url(game, "http://host:7373/api/games/abc/cover")

    assert ok is True
    assert (game / "deckdrop.png").read_bytes() == fake_png
    assert not (game / "deckdrop.jpg").exists()
    assert has_local_cover(game)


def test_download_cover_from_url_unknown_type_defaults_jpg(tmp_path):
    game = tmp_path / "Game"
    game.mkdir()
    fake_img = b"\xff\xd8\xff" + b"z" * 300

    response = MagicMock()
    response.status_code = 200
    response.content = fake_img
    response.headers = {"content-type": "application/octet-stream"}

    with patch("deckdrop.core.cover.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = response
        ok = download_cover_from_url(game, "http://host:7373/api/games/abc/cover")

    assert ok is True
    assert (game / "deckdrop.jpg").read_bytes() == fake_img


def test_download_cover_from_url_404(tmp_path):
    game = tmp_path / "Game"
    game.mkdir()

    response = MagicMock()
    response.status_code = 404
    response.content = b""

    with patch("deckdrop.core.cover.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = response
        ok = download_cover_from_url(game, "http://host:7373/api/games/abc/cover")

    assert ok is False
    assert not has_local_cover(game)

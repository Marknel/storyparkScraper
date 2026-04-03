from __future__ import annotations


def validate_google_photos_enabled(enabled: bool) -> None:
    if enabled:
        raise NotImplementedError(
            "Google Photos upload is intentionally deferred in this release. "
            "Keep google_photos.enabled=false until the uploader is implemented."
        )

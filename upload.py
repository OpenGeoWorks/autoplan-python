"""File upload helper backed by Cloudinary.

Requires the ``CLOUDINARY_URL`` environment variable, e.g.::

    CLOUDINARY_URL=cloudinary://<api_key>:<api_secret>@<cloud_name>
"""

import logging

import cloudinary
import cloudinary.uploader

logger = logging.getLogger(__name__)

# The Cloudinary SDK reads CLOUDINARY_URL from the environment.
cloudinary.config(secure=True)


def upload_file(file_path: str, folder: str = "uploads", file_name: str = None):
    """Upload a file to Cloudinary and return its public URL (None on failure)."""
    try:
        response = cloudinary.uploader.upload(
            file_path,
            folder=folder,
            public_id=file_name,
            overwrite=True,
            resource_type="auto",
        )
        return response.get("secure_url")
    except Exception:
        logger.exception("Upload to Cloudinary failed")
        return None

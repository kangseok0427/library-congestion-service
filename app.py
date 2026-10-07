"""Vercel entrypoint: all persistent state lives in Neon."""
from backend.app import create_app
from backend.neon_storage import Neon
from backend.cloud_versions import CloudVersions, CloudProvider
from backend.cloud_admin import CloudAdmin

storage = Neon()
versions = CloudVersions(storage)
app = create_app(provider=CloudProvider(versions), admin_backend=CloudAdmin(storage, versions))

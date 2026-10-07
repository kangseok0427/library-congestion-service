"""Vercel entrypoint: all persistent state lives in Supabase."""
from backend.app import create_app
from backend.cloud_storage import Supabase
from backend.cloud_versions import CloudVersions, CloudProvider
from backend.cloud_admin import CloudAdmin

storage = Supabase()
versions = CloudVersions(storage)
app = create_app(provider=CloudProvider(versions), admin_backend=CloudAdmin(storage, versions))

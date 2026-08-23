"""Moved to `forge.ollama`. This shim keeps the old imports working."""
from forge.ollama import *  # noqa: F401,F403
from forge.ollama import (CLOUD_HOST, LOCAL_DEFAULT_CTX, OllamaClient,  # noqa: F401
                          get_api_key, get_local_host, is_cloud_model,
                          load_settings, max_context, save_settings,
                          set_default_client, with_retry)

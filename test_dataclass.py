import json
from dataclasses import dataclass, asdict
from media_optimizer.config import get_default_config

conf = get_default_config()
d = asdict(conf)
# hardware has a class that we can't just json dump directly, let's remove it for user settings
user_keys = ["convert_heic_to_jpeg", "preserve_metadata", "jpeg_quality", "image_max_dimension", "video_max_height", "video_max_fps"]
user_settings = {k: d[k] for k in user_keys if k in d}
print(json.dumps(user_settings, indent=2))
